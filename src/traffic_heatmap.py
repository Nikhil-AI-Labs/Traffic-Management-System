"""Build an empirical map of where vehicles actually travel in each camera view.

Guessing lane polygons from a single still is error-prone: barricades, parked
vehicles and service roads all look like drivable road. Instead we accumulate
the road-contact point of every detection over a stretch of footage. The result
shows the corridors traffic really uses, which is what the lane/zone polygons
should follow.

Also emits a per-class heatmap so we can see, for example, that two-wheelers
filter along a kerb-side strip that cars never use — an India-specific
behaviour worth capturing in the zone layout.

Usage:
    python src/traffic_heatmap.py rokadiya_hanuman --frames 300
    python src/traffic_heatmap.py --all --frames 200
"""

from __future__ import annotations

import argparse
import json

import cv2
import numpy as np

import config as cfg
from detector import VehicleDetector
from reference_frames import REF_DIR

HEATMAP_DIR = cfg.FRAME_DIR / "heatmap"


def build(
    camera_id: str,
    detector: VehicleDetector,
    frames: int = 300,
    stride: int = 4,
    blur: int = 61,
) -> dict:
    video = cfg.camera_video_path(camera_id)
    cam = cfg.CAMERAS[camera_id]

    ref = cv2.imread(str(REF_DIR / f"{camera_id}.jpg"))
    h, w = ref.shape[:2]

    accum = np.zeros((h, w), np.float32)
    accum_by_group: dict[str, np.ndarray] = {}
    points: list[dict] = []

    detector.reset_tracker()
    n = 0
    for fr in detector.stream(video, stride=stride, max_frames=frames):
        n += 1
        for d in fr.detections:
            cx, cy = d.anchor
            xi, yi = int(round(cx)), int(round(cy))
            if not (0 <= xi < w and 0 <= yi < h):
                continue
            # weight by PCU so a bus counts more than a scooter
            accum[yi, xi] += d.pcu
            g = d.group
            if g not in accum_by_group:
                accum_by_group[g] = np.zeros((h, w), np.float32)
            accum_by_group[g][yi, xi] += 1.0
            points.append({
                "x": xi, "y": yi, "cls": d.cls_name, "group": g,
                "pcu": d.pcu, "t": round(fr.timestamp_s, 2),
                "track_id": d.track_id,
                "w": round(d.xyxy[2] - d.xyxy[0], 1),
                "h": round(d.xyxy[3] - d.xyxy[1], 1),
            })

    HEATMAP_DIR.mkdir(parents=True, exist_ok=True)

    _write_heatmap(ref, accum, blur, HEATMAP_DIR / f"{camera_id}_heatmap.jpg",
                   f"{cam['name']} - all traffic (PCU-weighted), {n} frames")

    for g, arr in accum_by_group.items():
        if arr.sum() < 50:
            continue
        safe = g.replace("/", "-").lower()
        _write_heatmap(ref, arr, blur,
                       HEATMAP_DIR / f"{camera_id}_heatmap_{safe}.jpg",
                       f"{cam['name']} - {g} only, {n} frames")

    # raw points, for fitting polygons programmatically later
    with open(HEATMAP_DIR / f"{camera_id}_points.json", "w", encoding="utf-8") as f:
        json.dump({"camera": camera_id, "frames": n, "points": points}, f)

    summary = {
        "camera": camera_id,
        "frames_processed": n,
        "detections": len(points),
        "groups": {g: int(a.sum()) for g, a in accum_by_group.items()},
    }
    print(f"  {camera_id:<20} {n:>4} frames, {len(points):>6} detections")
    return summary


def _write_heatmap(ref, accum, blur, path, title):
    k = blur if blur % 2 == 1 else blur + 1
    hm = cv2.GaussianBlur(accum, (k, k), 0)
    if hm.max() > 0:
        # sqrt compresses the dynamic range so light-traffic corridors stay
        # visible next to a heavily used one
        hm = np.sqrt(hm / hm.max())
    hm_u8 = np.clip(hm * 255, 0, 255).astype(np.uint8)
    colored = cv2.applyColorMap(hm_u8, cv2.COLORMAP_TURBO)

    mask = (hm_u8 > 12).astype(np.float32)[..., None]
    out = (ref * (1 - mask * 0.72) + colored * (mask * 0.72)).astype(np.uint8)

    cv2.putText(out, title, (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(out, title, (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), out, [cv2.IMWRITE_JPEG_QUALITY, 90])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", nargs="?", choices=list(cfg.CAMERAS.keys()))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--model", default=str(cfg.DEFAULT_MODEL))
    args = ap.parse_args()

    cams = list(cfg.CAMERAS.keys()) if args.all else [args.camera]
    if not cams or cams == [None]:
        ap.error("give a camera id or --all")

    det = VehicleDetector(model_path=args.model)
    print(f"Building traffic heatmaps ({args.frames} frames each)...")
    for c in cams:
        build(c, det, frames=args.frames, stride=args.stride)
    print(f"\nHeatmaps -> {HEATMAP_DIR}")


if __name__ == "__main__":
    main()
