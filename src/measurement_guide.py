"""Render a marked-up frame showing exactly where a real-world width is needed.

A measurement is only useful if we know which image row it corresponds to, so
guessing "the road is about 7 m" does not help — we need a width taken at an
identifiable place in the view. This draws candidate measurement lines on the
reference frame, each anchored to landmarks visible both on the ground and in
satellite imagery, and prints the pixel length of each so the metres can be
dropped straight into the calibration.

Usage:
    python src/measurement_guide.py gauravpath
    python src/measurement_guide.py --all
"""

from __future__ import annotations

import argparse

import cv2

import config as cfg
import seed_calibration as SC
from reference_frames import REF_DIR

GUIDE_DIR = cfg.FRAME_DIR / "measurement_guide"

# Rows chosen where the carriageway edges are unambiguous in the image and the
# same edges are identifiable from above in satellite view.
GUIDE_ROWS = {
    "gauravpath": [400, 700, 1000],
    "rokadiya_hanuman": [300, 600, 900],
}

COLORS = [(0, 0, 255), (0, 200, 255), (255, 0, 255)]


def build(camera_id: str) -> None:
    if camera_id not in SC.JUNCTION_DEFS:
        return

    frame = cv2.imread(str(REF_DIR / f"{camera_id}.jpg"))
    if frame is None:
        print(f"{camera_id}: no reference frame")
        return

    d = SC.JUNCTION_DEFS[camera_id]
    rows = GUIDE_ROWS.get(camera_id, [400, 700, 1000])

    print(f"\n{cfg.CAMERAS[camera_id]['name']}")
    print("  Measure the carriageway width (median/kerb edge to opposite kerb)")
    print("  along each coloured line, and note the metres:")

    for i, y in enumerate(rows):
        lx = SC._interp_polyline(d["left_edge"], [y])[0]
        rx = SC._interp_polyline(d["right_edge"], [y])[0]
        color = COLORS[i % len(COLORS)]

        cv2.line(frame, (lx, y), (rx, y), color, 4)
        cv2.circle(frame, (lx, y), 9, color, -1)
        cv2.circle(frame, (rx, y), 9, color, -1)
        label = f"LINE {i + 1}: row y={y}, {rx - lx} px  ->  ? metres"
        cv2.putText(frame, label, (max(10, lx - 10), y - 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(frame, label, (max(10, lx - 10), y - 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, color, 2, cv2.LINE_AA)
        print(f"    LINE {i + 1}  row y={y:<5} spans {rx - lx:>4} px  -> ____ m")

    header = [
        f"{cfg.CAMERAS[camera_id]['name']}",
        "Measure each coloured line's real width in metres.",
        "Any ONE line is enough; two or three lets us cross-check.",
    ]
    yy = 42
    for line in header:
        cv2.putText(frame, line, (16, yy), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(frame, line, (16, yy), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (255, 255, 255), 2, cv2.LINE_AA)
        yy += 38

    GUIDE_DIR.mkdir(parents=True, exist_ok=True)
    out = GUIDE_DIR / f"{camera_id}_measure.jpg"
    cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"    -> {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", nargs="?", choices=list(SC.JUNCTION_DEFS.keys()))
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    cams = list(SC.JUNCTION_DEFS.keys()) if args.all or not args.camera else [args.camera]
    for c in cams:
        build(c)

    print("\nOnce you have the metres, apply with:")
    print("  python src/apply_scale.py gauravpath --row 700 --width-m <metres>")


if __name__ == "__main__":
    main()
