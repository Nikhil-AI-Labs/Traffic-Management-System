"""Run the full pipeline over a camera: detect -> track -> assign -> measure.

Writes a per-window metrics CSV and, optionally, an annotated video with lanes
shaded by live level of service.

Usage:
    python src/analyze.py gauravpath --video
    python src/analyze.py --all --frames 600
"""

from __future__ import annotations

import argparse
import time

import cv2
import pandas as pd

import config as cfg

cfg.quiet_opencv()
import zones as Z
from detector import VehicleDetector, draw_detections, draw_hud
from metrics import MetricsEngine, los_color


def analyze(
    camera_id: str,
    detector: VehicleDetector,
    frames: int | None = None,
    stride: int = cfg.DEFAULT_STRIDE,
    window_s: float = 2.0,
    write_video: bool = False,
    video_fps: int = 10,
) -> pd.DataFrame:
    cam = cfg.CAMERAS[camera_id]
    calib = Z.load(camera_id)
    if calib is None:
        raise RuntimeError(
            f"No calibration for '{camera_id}'. Run src/seed_calibration.py first."
        )

    video_path = cfg.camera_video_path(camera_id)
    engine = MetricsEngine(camera_id, calib, window_s=window_s)
    detector.reset_tracker()

    writer = None
    if write_video:
        out_path = cfg.ANNOTATED_DIR / f"{camera_id}_annotated.mp4"
        writer = cv2.VideoWriter(
            str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
            video_fps, tuple(calib.frame_size),
        )

    rows: list[dict] = []
    latest: dict[str, object] = {}
    n = 0
    t0 = time.time()
    last_t = 0.0

    for fr in detector.stream(
        video_path, stride=stride, max_frames=frames,
        keep_frame=True,  # structural occupancy needs pixels
    ):
        n += 1
        last_t = fr.timestamp_s

        for w in engine.process(fr):
            rows.append(w.to_dict())
            latest[w.zone_id] = w

        if writer is not None and fr.frame is not None:
            writer.write(_render(fr, calib, latest, cam))

    for w in engine.flush(last_t):
        rows.append(w.to_dict())

    if writer is not None:
        writer.release()

    elapsed = time.time() - t0
    df = pd.DataFrame(rows)

    out_csv = cfg.LOG_DIR / f"{camera_id}_metrics.csv"
    df.to_csv(out_csv, index=False)

    print(f"  {cam['name'][:44]:<45} {n:>5} frames  {elapsed:>5.1f}s  "
          f"{len(df):>4} windows -> {out_csv.name}")
    if write_video:
        print(f"      video -> {cfg.ANNOTATED_DIR / f'{camera_id}_annotated.mp4'}")
    return df


def _render(fr, calib, latest, cam):
    """Frame with LOS-shaded zones, boxes and a summary panel."""
    highlight = {
        zid: los_color(getattr(w, "los", "Free")) for zid, w in latest.items()
    }
    vis = Z.draw_zones(fr.frame, calib, alpha=0.3,
                       show_labels=False, highlight=highlight)
    vis = draw_detections(vis, fr.detections, show_id=False)

    lines = [f"{cam['name']}", f"t={fr.timestamp_s:6.1f}s"]
    for z in calib.zones:
        w = latest.get(z.id)
        if w is None:
            continue
        if getattr(w, "measurement_mode", "") == "occupancy":
            val = f"occ {w.structural_occupancy:.2f}"
        else:
            d = w.density_pcu_per_100m2
            val = f"{d:.1f} PCU/100m2" if d is not None else f"{w.pcu_mean:.1f} PCU"
        sat = " [SAT]" if getattr(w, "saturated", False) else ""
        lines.append(f"{z.label}: {w.los} | {val}{sat}")

    return draw_hud(vis, lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", nargs="?", choices=list(cfg.CAMERAS.keys()))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--frames", type=int, default=None,
                    help="limit processed frames (default: whole clip)")
    ap.add_argument("--stride", type=int, default=cfg.DEFAULT_STRIDE)
    ap.add_argument("--window", type=float, default=2.0, help="window seconds")
    ap.add_argument("--video", action="store_true", help="write annotated mp4")
    ap.add_argument("--model", default=str(cfg.DEFAULT_MODEL))
    args = ap.parse_args()

    cams = list(cfg.CAMERAS.keys()) if args.all else [args.camera]
    if not cams or cams == [None]:
        ap.error("give a camera id or --all")

    det = VehicleDetector(model_path=args.model)
    print("Analyzing...")
    frames_all = []
    for c in cams:
        df = analyze(c, det, frames=args.frames, stride=args.stride,
                     window_s=args.window, write_video=args.video)
        frames_all.append(df)

    if len(frames_all) > 1:
        combined = pd.concat(frames_all, ignore_index=True)
        out = cfg.LOG_DIR / "all_metrics.csv"
        combined.to_csv(out, index=False)
        print(f"\nCombined -> {out}")


if __name__ == "__main__":
    main()
