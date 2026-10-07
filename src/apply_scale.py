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

    # Same fit, checks and persistence as the dashboards' Calibration view, so
    # a CLI calibration marks the camera verified in calibration_state.json,
    # records what it replaced (so it can be undone), and honours a second
    # row's own width. This script used to write the model directly, ignore
    # --width2-m, and tell the user to edit a config.py setting that no longer
    # controls anything.
    import calibration as CAL

    cam = args.camera
    calib = Z.load(cam)
    if calib is None:
        raise SystemExit(f"No calibration for '{cam}'. Run seed_calibration.py first.")

    measurements = [(args.row, args.width_m)]
    if args.row2 and args.width2_m:
        measurements.append((args.row2, args.width2_m))
    hfov = CAL.hfov_from_focal_px(args.focal_px, int(calib.frame_size[0]))

    res = CAL.fit_scale(cam, measurements, camera_height_m=args.camera_height_m,
                        hfov_deg=hfov, calib=calib)
    if not res.ok:
        raise SystemExit(res.message)
    print(res.message)
    for w in res.warnings or []:
        print(f"  check: {w}")
    CAL.apply(cam, res, mark_verified=True, measurements=measurements,
              camera_height_m=args.camera_height_m, hfov_deg=hfov)
    model = res.model

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

    print(f"\nMarked {cam} scale-verified in {CAL.STATE_PATH.name}.")
    print(f"Re-measure so the new scale takes effect:  python src/analyze.py {cam}")
    print(f"Undo from the dashboard's Calibration view, or "
          f"`python -c \"import sys; sys.path.insert(0,'src'); "
          f"import calibration; calibration.clear('{cam}')\"`.")


if __name__ == "__main__":
    main()
