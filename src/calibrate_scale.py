"""Fit the perspective scale from vehicle widths instead of assumed road width.

Anchoring the scale on "the carriageway is N lanes x 3.5 m" requires two
guesses: how many lanes the traced polygon spans, and that they are standard
width. Both are easy to get wrong from a single oblique view, and the error
propagates straight into every speed and density figure. On this dataset that
assumption put a Maruti hatchback at 3.3 m wide.

Vehicles are a far better ruler. Their real widths are known to within a few
centimetres, the detector already classifies them, and they conveniently
distribute themselves across the whole depth of the view. Fitting

    s_h(y) = known_width_m / box_width_px

over thousands of detections recovers the lateral scale directly, and with it
the horizon row, with no road-geometry assumption at all.

Only near-frontal views are used: a vehicle crossing obliquely presents a box
wider than its body. Since these cameras look up the road, most traffic moves
almost directly toward or away from the lens, which is the good case.

Usage:
    python src/calibrate_scale.py gauravpath --frames 400
    python src/calibrate_scale.py --all --apply
"""

from __future__ import annotations

import argparse
import json

import numpy as np

import config as cfg
import zones as Z
from detector import VehicleDetector

# Typical body widths for the Indian fleet, in metres. Conservative central
# estimates; the fit uses the median across many detections so a few atypical
# vehicles do not move the result.
VEHICLE_WIDTH_M = {
    "Two-wheeler": 0.75,     # handlebar to handlebar
    "bicycle": 0.60,
    "Three-wheeler": 1.40,   # auto-rickshaw
    "Hatchback": 1.65,
    "Sedan": 1.75,
    "SUV": 1.85,
    "MUV": 1.85,
    "Van": 1.90,
    "LCV": 1.95,
    "tempo-traveller": 2.00,
    "Mini-bus": 2.30,
    "Bus": 2.55,
    "Truck": 2.50,
}

# Classes with tight, well-known widths carry more weight than the loose ones.
CLASS_WEIGHT = {
    "Sedan": 1.0, "Hatchback": 1.0, "SUV": 0.9, "MUV": 0.8, "Van": 0.8,
    "Three-wheeler": 1.0, "Bus": 0.9, "Truck": 0.7, "LCV": 0.6,
    "Mini-bus": 0.6, "tempo-traveller": 0.5,
    "Two-wheeler": 0.6, "bicycle": 0.3,
}

MIN_BOX_PX = 18          # ignore boxes too small to measure reliably
MAX_ASPECT = 3.0         # reject wildly elongated boxes (merged detections)


def collect(camera_id: str, detector: VehicleDetector,
            frames: int = 400, stride: int = 4) -> list[tuple[float, float, float, str]]:
    """Gather (y, implied_scale_m_per_px, weight, class) samples."""
    samples = []
    detector.reset_tracker()
    for fr in detector.stream(cfg.camera_video_path(camera_id),
                              stride=stride, max_frames=frames):
        for d in fr.detections:
            w_px = d.xyxy[2] - d.xyxy[0]
            h_px = d.xyxy[3] - d.xyxy[1]
            if w_px < MIN_BOX_PX or h_px <= 0:
                continue
            if max(w_px / h_px, h_px / w_px) > MAX_ASPECT:
                continue
            real_w = VEHICLE_WIDTH_M.get(d.cls_name)
            if real_w is None:
                continue

            # Associate the width with the box's mid-height row, not its ground
            # contact. A vehicle is widest around waist height, and on a steeply
            # angled near-field view its box spans a large range of rows — so
            # pinning that width to the bottom row makes near vehicles look far
            # wider than the ground scale there allows, and drags the fit.
            y = (d.xyxy[1] + d.xyxy[3]) / 2.0
            samples.append((float(y), real_w / float(w_px),
                            CLASS_WEIGHT.get(d.cls_name, 0.5) * d.conf,
                            d.cls_name))
    return samples


def fit(samples, y_lo: float = 150.0, y_hi: float = 1075.0) -> dict | None:
    """Fit s_h(y) = C / (y - y_h) by weighted least squares.

    Linearised as 1/s = (y - y_h)/C = y/C - y_h/C, i.e. a straight line in
    (y, 1/s), which is well conditioned and needs no starting guess.
    """
    if len(samples) < 50:
        return None

    arr = np.array([(y, s, w) for y, s, w, _ in samples
                    if y_lo <= y <= y_hi and s > 0])
    if len(arr) < 50:
        return None

    y, s, w = arr[:, 0], arr[:, 1], arr[:, 2]
    inv = 1.0 / s

    # Trim outliers in a few passes: merged boxes and misclassifications
    # otherwise drag the line.
    keep = np.ones(len(y), bool)
    slope = intercept = 0.0
    for _ in range(3):
        A = np.vstack([y[keep], np.ones(keep.sum())]).T
        sw = np.sqrt(w[keep])
        sol, *_ = np.linalg.lstsq(A * sw[:, None], inv[keep] * sw, rcond=None)
        slope, intercept = float(sol[0]), float(sol[1])
        pred = slope * y + intercept
        resid = np.abs(inv - pred)
        cutoff = np.percentile(resid[keep], 88)
        keep = resid <= max(cutoff, 1e-9)

    if slope <= 0:
        return None

    C = 1.0 / slope
    y_horizon = -intercept * C
    return {
        "type": "perspective_v1",
        "y_horizon": float(y_horizon),
        "c": float(C),
        "focal_px": 1663.0,
        "camera_height_m": 7.0,
        "fitted_from": {
            "method": "vehicle_width_regression",
            "n_samples": int(keep.sum()),
            "n_total": int(len(arr)),
        },
    }


def report(camera_id: str, model: dict, samples) -> None:
    print(f"\n{camera_id}")
    print(f"  y_horizon = {model['y_horizon']:.0f}   C = {model['c']:.2f}")
    print(f"  fitted on {model['fitted_from']['n_samples']} of "
          f"{model['fitted_from']['n_total']} samples")

    print(f"  {'row':>6} {'lateral':>12} {'longitudinal':>14} {'car width':>11}")
    for yy in (1000, 800, 600, 400, 250):
        lat = Z.lateral_m_per_px(model, yy)
        lon = Z.longitudinal_m_per_px(model, yy)
        if lat is None:
            continue
        print(f"  {yy:>6} {lat * 100:>9.2f} cm {lon * 100:>11.2f} cm "
              f"{1.75 / lat:>8.0f} px")

    # Per-class residual check: if the model is right, every class should
    # recover close to its own known width.
    print("  class check (implied width vs assumed):")
    by_cls: dict[str, list[float]] = {}
    for y, s, _w, cls in samples:
        lat = Z.lateral_m_per_px(model, y)
        if lat is None or s <= 0:
            continue
        # s = real_w / box_px  ->  box_px = real_w / s ; implied = box_px * lat
        by_cls.setdefault(cls, []).append(
            (VEHICLE_WIDTH_M[cls] / s) * lat)
    for cls, vals in sorted(by_cls.items(), key=lambda kv: -len(kv[1]))[:6]:
        if len(vals) < 20:
            continue
        print(f"    {cls:<16} n={len(vals):>5}  implied "
              f"{np.median(vals):.2f} m  (assumed {VEHICLE_WIDTH_M[cls]:.2f} m)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", nargs="?", choices=list(cfg.CAMERAS.keys()))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--frames", type=int, default=400)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--apply", action="store_true",
                    help="write the fitted model into configs/cameras.json")
    args = ap.parse_args()

    cams = (list(cfg.CAMERAS.keys()) if args.all
            else [args.camera] if args.camera else [])
    if not cams:
        ap.error("give a camera id or --all")

    det = VehicleDetector()
    for cam in cams:
        samples = collect(cam, det, frames=args.frames, stride=args.stride)
        model = fit(samples)
        if model is None:
            print(f"\n{cam}: too few usable samples ({len(samples)}) - skipped")
            continue
        report(cam, model, samples)

        if args.apply:
            calib = Z.load(cam)
            if calib is None:
                print(f"  ! no calibration for {cam}; run seed_calibration first")
                continue
            calib.scale_model = model
            Z.save(calib)
            areas = {z.label: Z.zone_real_area(z, model) for z in calib.zones}
            print("  applied. zone areas now:")
            for k, v in areas.items():
                if v:
                    print(f"    {k:<26} {v:7.0f} m2  "
                          f"(~{v / cfg.LANE_WIDTH_M:.0f} m long)")


if __name__ == "__main__":
    main()
