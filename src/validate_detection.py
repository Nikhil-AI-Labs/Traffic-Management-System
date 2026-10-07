"""Sanity-check the detector on a camera: prints class distribution and
writes annotated sample frames for visual inspection.

Usage:
    python src/validate_detection.py gauravpath --frames 40
"""

from __future__ import annotations

import argparse
import time
from collections import Counter

import cv2

import config as cfg
from detector import VehicleDetector, draw_detections, draw_hud


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", choices=list(cfg.CAMERAS.keys()))
    ap.add_argument("--frames", type=int, default=40, help="frames to process")
    ap.add_argument("--stride", type=int, default=cfg.DEFAULT_STRIDE)
    ap.add_argument("--start", type=int, default=0, help="start frame index")
    ap.add_argument("--save-every", type=int, default=10,
                    help="write an annotated frame every N processed frames")
    ap.add_argument("--model", default=str(cfg.DEFAULT_MODEL))
    ap.add_argument("--conf", type=float, default=cfg.DETECT_CONF)
    ap.add_argument("--imgsz", type=int, default=cfg.DETECT_IMGSZ)
    args = ap.parse_args()

    cam = cfg.CAMERAS[args.camera]
    video = cfg.camera_video_path(args.camera)
    print(f"Camera : {cam['name']}")
    print(f"Mode   : {cam['mode']}")
    print(f"Video  : {video.name}")
    print(f"Model  : {args.model}")
    print(f"imgsz={args.imgsz} conf={args.conf} stride={args.stride}\n")

    det = VehicleDetector(model_path=args.model, conf=args.conf, imgsz=args.imgsz)

    out_dir = cfg.FRAME_DIR / f"validate_{args.camera}"
    out_dir.mkdir(parents=True, exist_ok=True)

    cls_counter = Counter()
    group_counter = Counter()
    per_frame_counts = []
    per_frame_pcu = []
    track_ids = set()
    saved = 0

    t0 = time.time()
    n = 0
    for fr in det.stream(
        video,
        stride=args.stride,
        max_frames=args.frames,
        start_frame=args.start,
        keep_frame=True,
    ):
        n += 1
        per_frame_counts.append(len(fr.detections))
        per_frame_pcu.append(fr.total_pcu)
        for d in fr.detections:
            cls_counter[d.cls_name] += 1
            group_counter[d.group] += 1
            if d.track_id is not None:
                track_ids.add(d.track_id)

        if n % args.save_every == 0 and fr.frame is not None:
            vis = draw_detections(fr.frame, fr.detections)
            vis = draw_hud(vis, [
                f"{cam['name']}",
                f"t={fr.timestamp_s:6.2f}s  frame={fr.frame_idx}",
                f"objects={len(fr.detections)}  PCU={fr.total_pcu:.1f}",
            ])
            path = out_dir / f"frame_{fr.frame_idx:06d}.jpg"
            cv2.imwrite(str(path), vis, [cv2.IMWRITE_JPEG_QUALITY, 90])
            saved += 1

    elapsed = time.time() - t0
    if n == 0:
        raise SystemExit("No frames were processed — check --start against the "
                         "clip's length.")

    print(f"Processed {n} frames in {elapsed:.1f}s "
          f"({n / max(elapsed, 1e-6):.1f} FPS inference throughput)")
    print(f"Unique track IDs: {len(track_ids)}")
    if per_frame_counts:
        avg_n = sum(per_frame_counts) / len(per_frame_counts)
        avg_pcu = sum(per_frame_pcu) / len(per_frame_pcu)
        print(f"Objects/frame: avg={avg_n:.1f} min={min(per_frame_counts)} "
              f"max={max(per_frame_counts)}")
        print(f"PCU/frame    : avg={avg_pcu:.1f} min={min(per_frame_pcu):.1f} "
              f"max={max(per_frame_pcu):.1f}")

    total = sum(cls_counter.values()) or 1
    print("\nDetections by class:")
    for name, c in cls_counter.most_common():
        print(f"  {name:<18} {c:>6}  ({100 * c / total:5.1f}%)")

    print("\nDetections by group:")
    for g in cfg.GROUP_ORDER:
        c = group_counter.get(g, 0)
        if c:
            print(f"  {g:<18} {c:>6}  ({100 * c / total:5.1f}%)")

    print(f"\nSaved {saved} annotated frames to {out_dir}")


if __name__ == "__main__":
    main()
