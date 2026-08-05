"""Extract a clean reference frame per camera, plus a grid-overlaid copy.

The reference frame is what lane/zone polygons are drawn against; the grid
copy makes it possible to read off pixel coordinates by eye when placing or
sanity-checking polygons.

Usage:
    python src/reference_frames.py            # all cameras
    python src/reference_frames.py gauravpath # one camera
"""

from __future__ import annotations

import argparse

import cv2

import config as cfg

REF_DIR = cfg.FRAME_DIR / "reference"


def extract(cam_id: str, at_fraction: float = 0.4) -> None:
    video = cfg.camera_video_path(cam_id)
    cap = cfg.open_video(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open {video}")

    total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * at_fraction))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"Could not read frame from {video}")

    REF_DIR.mkdir(parents=True, exist_ok=True)
    plain = REF_DIR / f"{cam_id}.jpg"
    cv2.imwrite(str(plain), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])

    cv2.imwrite(
        str(REF_DIR / f"{cam_id}_grid.jpg"),
        draw_grid(frame),
        [cv2.IMWRITE_JPEG_QUALITY, 92],
    )
    print(f"{cam_id:<20} -> {plain.name} ({frame.shape[1]}x{frame.shape[0]})")


def draw_grid(frame, step: int = 100, major: int = 500):
    """Overlay a labelled pixel grid."""
    out = frame.copy()
    h, w = out.shape[:2]

    for x in range(0, w, step):
        is_major = x % major == 0
        cv2.line(out, (x, 0), (x, h),
                 (0, 255, 255) if is_major else (90, 90, 90),
                 2 if is_major else 1)
        if is_major:
            cv2.putText(out, str(x), (x + 4, 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 255, 255), 2, cv2.LINE_AA)

    for y in range(0, h, step):
        is_major = y % major == 0
        cv2.line(out, (0, y), (w, y),
                 (0, 255, 255) if is_major else (90, 90, 90),
                 2 if is_major else 1)
        if is_major:
            cv2.putText(out, str(y), (6, y - 6), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 255, 255), 2, cv2.LINE_AA)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", nargs="?", choices=list(cfg.CAMERAS.keys()))
    ap.add_argument("--at", type=float, default=0.4,
                    help="fraction into the video to sample (0-1)")
    args = ap.parse_args()

    cams = [args.camera] if args.camera else list(cfg.CAMERAS.keys())
    for c in cams:
        extract(c, args.at)
    print(f"\nReference frames in {REF_DIR}")


if __name__ == "__main__":
    main()
