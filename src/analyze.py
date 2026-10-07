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
    on_progress=None,
) -> pd.DataFrame:
    """Measure one camera's whole clip and write its metrics CSV.

    `on_progress(fraction, message)`, when given, is called every few frames
    so a caller running this in the background can show how far it has got.
    """
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
        writer, _codec = cfg.video_writer(out_path, video_fps,
                                          tuple(calib.frame_size))

    rows: list[dict] = []
    latest: dict[str, object] = {}
    n = 0
    t0 = time.time()
    last_t = 0.0

    expected = None
    if on_progress is not None:
        cap = cfg.open_video(str(video_path))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        expected = max(1, total // max(1, stride))
        if frames:
            expected = min(expected, frames)

    for fr in detector.stream(
        video_path, stride=stride, max_frames=frames,
        keep_frame=True,  # structural occupancy needs pixels
    ):
        n += 1
        last_t = fr.timestamp_s
        if on_progress is not None and (n == 1 or n % 10 == 0):
            on_progress(min(0.99, n / expected),
                        f"{fr.timestamp_s:.0f}s of footage measured, "
                        f"{len(rows)} windows")

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
    for c in cams:
        analyze(c, det, frames=args.frames, stride=args.stride,
                window_s=args.window, write_video=args.video)

    out = rebuild_combined()
    if out is not None:
        print(f"\nCombined -> {out}")


def rebuild_combined():
    """Regenerate all_metrics.csv from every per-camera CSV on disk.

    Rebuilt after *any* run, not only `--all`. Re-analysing one camera used to
    leave the combined file holding that camera's previous numbers, so the two
    files disagreed silently. The dashboard reads the per-camera files, but
    all_metrics.csv is what gets handed to anyone who wants the whole dataset
    in one file.
    """
    frames = [pd.read_csv(p) for c in cfg.CAMERAS
              if (p := cfg.LOG_DIR / f"{c}_metrics.csv").exists()]
    if not frames:
        return None
    out = cfg.LOG_DIR / "all_metrics.csv"
    pd.concat(frames, ignore_index=True).to_csv(out, index=False)
    return out


if __name__ == "__main__":
    main()
