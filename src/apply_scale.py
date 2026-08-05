"""Pin a camera's metric scale using one measured real-world width.

Give the real width of the carriageway at one image row (see
src/measurement_guide.py for where to measure) and this fits the perspective
model, writes it into the calibration, and marks the camera scale-verified so
its speed and PCU-density figures are promoted from estimates to primary.

Two rows are better than one: with a single row we must assume the horizon,
whereas two determine it, which is what actually sets the longitudinal scale
and therefore the speeds.

Usage:
    python src/apply_scale.py gauravpath --row 700 --width-m 7.5
    python src/apply_scale.py gauravpath --row 400 --width-m 7.5 --row2 1000 --width2-m 7.5
"""

from __future__ import annotations

import argparse

import config as cfg
import seed_calibration as SC
import zones as Z


def road_px_at(camera_id: str, row: int) -> int:
    d = SC.JUNCTION_DEFS[camera_id]
    lx = SC._interp_polyline(d["left_edge"], [row])[0]
    rx = SC._interp_polyline(d["right_edge"], [row])[0]
    return rx - lx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", choices=list(cfg.CAMERAS.keys()))
    ap.add_argument("--row", type=int, required=True,
                    help="image row where the width was measured")
    ap.add_argument("--width-m", type=float, required=True,
                    help="real carriageway width at that row, in metres")
    ap.add_argument("--row2", type=int, help="a second measured row (recommended)")
    ap.add_argument("--width2-m", type=float, help="real width at the second row")
    ap.add_argument("--camera-height-m", type=float, default=7.0,
                    help="camera mounting height, if known")
    ap.add_argument("--focal-px", type=float, default=1663.0,
                    help="focal length in pixels (1080p, ~60 deg HFOV by default)")
    args = ap.parse_args()

    cam = args.camera
    calib = Z.load(cam)
    if calib is None:
        raise SystemExit(f"No calibration for '{cam}'. Run seed_calibration.py first.")

    px1 = road_px_at(cam, args.row) if cam in SC.JUNCTION_DEFS else None
    if not px1:
        raise SystemExit(f"'{cam}' has no traced road edges to measure against.")

    if args.row2 and args.width2_m:
        px2 = road_px_at(cam, args.row2)
        model = Z.build_scale_model(
            y_near=max(args.row, args.row2),
            px_width_near=px1 if args.row >= args.row2 else px2,
            y_far=min(args.row, args.row2),
            px_width_far=px2 if args.row >= args.row2 else px1,
            real_width_m=args.width_m,
            focal_px=args.focal_px,
            camera_height_m=args.camera_height_m,
        )
        print(f"Fitted from two rows: y={args.row} ({px1} px) and "
              f"y={args.row2} ({px2} px)")
    else:
        # One row fixes the lateral scale there; the horizon has to come from
        # the traced edge convergence instead, which is weaker but workable.
        d = SC.JUNCTION_DEFS[cam]
        rows = [200, 400, 600, 800, 1000]
        widths = [road_px_at(cam, r) for r in rows]
        # width is linear in row for a flat road: width = m*(y - y_horizon)
        import numpy as np
        A = np.vstack([rows, np.ones(len(rows))]).T
        m, c = np.linalg.lstsq(A, np.array(widths, float), rcond=None)[0]
        y_h = -c / m
        far_row = 250
        far_px = max(1.0, m * (far_row - y_h))
        model = Z.build_scale_model(
            y_near=args.row, px_width_near=px1,
            y_far=far_row, px_width_far=far_px,
            real_width_m=args.width_m,
            focal_px=args.focal_px,
            camera_height_m=args.camera_height_m,
        )
        print(f"Fitted from one row (y={args.row}, {px1} px = {args.width_m} m); "
              f"horizon taken from edge convergence (y={y_h:.0f}).")
        print("Supplying --row2/--width2-m would determine the horizon directly "
              "and give more trustworthy speeds.")

    calib.scale_model = model
    Z.save(calib)

    print(f"\nApplied to {cam}:")
    print(f"  y_horizon = {model['y_horizon']:.0f}   C = {model['c']:.3f}")
    for y in (1000, 700, 400, 250):
        lat = Z.lateral_m_per_px(model, y)
        lon = Z.longitudinal_m_per_px(model, y)
        if lat:
            print(f"    row {y:>4}: lateral {lat * 100:6.2f} cm/px   "
                  f"longitudinal {lon * 100:6.2f} cm/px")

    print("\n  zone areas:")
    for z in calib.zones:
        a = Z.zone_real_area(z, model)
        if a:
            print(f"    {z.label:<26} {a:8.0f} m2  "
                  f"(~{a / cfg.LANE_WIDTH_M:.0f} m of road)")

    print(f"\nNow mark it verified so its metric figures become primary:")
    print(f"  in src/config.py, set  SCALE_VERIFIED = {{'{cam}': True}}")
    print(f"Then re-run:  python src/analyze.py {cam}")


if __name__ == "__main__":
    main()
