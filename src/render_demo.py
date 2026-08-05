"""Render a presentation-grade annotated video for one camera.

Shows, live over the footage: lane/zone polygons shaded by level of service,
tracked detections coloured by vehicle class, a per-zone metrics panel, and a
running class tally. Figures whose values depend on the metric scale are
prefixed with '~' so nothing on screen overstates what has been measured.

Usage:
    python src/render_demo.py rokadiya_hanuman
    python src/render_demo.py gauravpath --seconds 45 --fps 12
"""

from __future__ import annotations

import argparse
import time
from collections import Counter

import cv2
import numpy as np

import config as cfg
import zones as Z
from detector import VehicleDetector, draw_detections
from metrics import MetricsEngine, los_color

PANEL_W = 470
# Near-opaque: at lower values the detection boxes drawn underneath show
# through the panel and make the readouts hard to read.
PANEL_ALPHA = 0.93

GROUP_SWATCH = {
    "Two-wheeler": (255, 180, 0),
    "Auto": (0, 220, 255),
    "Car": (0, 255, 120),
    "LCV/Tempo": (200, 100, 255),
    "Bus/Truck": (0, 100, 255),
    "Other": (180, 180, 180),
}


def _text(img, s, org, scale=0.6, color=(255, 255, 255), thick=1, shadow=True):
    if shadow:
        cv2.putText(img, s, (org[0] + 1, org[1] + 1), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color,
                thick, cv2.LINE_AA)


def draw_panel(frame, cam, calib, latest, tally, t, scale_verified):
    h, w = frame.shape[:2]
    x0 = w - PANEL_W

    overlay = frame.copy()
    cv2.rectangle(overlay, (x0, 0), (w, h), (14, 14, 18), -1)
    frame = cv2.addWeighted(overlay, PANEL_ALPHA, frame, 1 - PANEL_ALPHA, 0)
    cv2.line(frame, (x0, 0), (x0, h), (70, 70, 80), 2)

    x = x0 + 20
    y = 44
    _text(frame, "SURAT TRAFFIC INTELLIGENCE", (x, y), 0.62, (120, 200, 255), 2)
    y += 30
    _text(frame, cam["name"][:38], (x, y), 0.58, (235, 235, 235), 1)
    y += 26
    _text(frame, f"{cam['mode'].upper()} MODE   t = {t:6.1f} s",
          (x, y), 0.52, (150, 150, 160), 1)
    y += 30
    cv2.line(frame, (x, y), (w - 20, y), (70, 70, 80), 1)
    y += 34

    _text(frame, "ZONE STATUS", (x, y), 0.55, (150, 200, 255), 2)
    y += 30

    est = "" if scale_verified else "~"
    for z in calib.zones:
        wnd = latest.get(z.id)
        if wnd is None:
            continue
        col = los_color(wnd.los)

        cv2.rectangle(frame, (x, y - 13), (x + 13, y), col, -1)
        _text(frame, z.label[:26], (x + 22, y), 0.52, (240, 240, 240), 1)
        y += 22

        _text(frame, f"{wnd.los}", (x + 22, y), 0.5, col, 1)
        _text(frame, f"occupancy {wnd.structural_occupancy:.2f}",
              (x + 130, y), 0.46, (175, 175, 185), 1)
        y += 20

        bits = []
        if wnd.density_pcu_per_km_lane is not None:
            bits.append(f"{est}{wnd.density_pcu_per_km_lane:.0f} PCU/km/ln")
        if wnd.mean_speed_kmph is not None:
            bits.append(f"{est}{wnd.mean_speed_kmph:.0f} km/h")
        if bits:
            _text(frame, "  ".join(bits), (x + 22, y), 0.45, (150, 150, 160), 1)
            y += 20

        if wnd.saturated:
            _text(frame, "! saturated - counts under-report",
                  (x + 22, y), 0.44, (90, 140, 255), 1)
            y += 20
        y += 10

    y = max(y, h - 250)
    cv2.line(frame, (x, y), (w - 20, y), (70, 70, 80), 1)
    y += 30
    _text(frame, "DETECTED (cumulative)", (x, y), 0.55, (150, 200, 255), 2)
    y += 28

    total = sum(tally.values()) or 1
    for g in cfg.GROUP_ORDER:
        n = tally.get(g, 0)
        if not n:
            continue
        col = GROUP_SWATCH.get(g, (180, 180, 180))
        cv2.rectangle(frame, (x, y - 11), (x + 13, y), col, -1)
        _text(frame, f"{g:<13} {n:>6}", (x + 22, y), 0.5, (225, 225, 225), 1)
        bar_w = int(210 * n / total)
        cv2.rectangle(frame, (x + 205, y - 10), (x + 205 + bar_w, y - 2), col, -1)
        y += 24

    y = h - 46
    _text(frame, "YOLO11-S (UVH-26, Indian CCTV) + ByteTrack",
          (x, y), 0.44, (120, 120, 130), 1)
    y += 18
    note = ("PCU per IRC:106-1990" if scale_verified
            else "~ = depends on uncalibrated metric scale")
    _text(frame, note, (x, y), 0.44, (120, 120, 130), 1)
    return frame


def render(camera_id: str, seconds: float | None, fps: int, stride: int,
           model: str) -> None:
    cam = cfg.CAMERAS[camera_id]
    calib = Z.load(camera_id)
    if calib is None:
        raise SystemExit(f"No calibration for '{camera_id}'.")

    src_fps = 20.0
    max_frames = int(seconds * src_fps / stride) if seconds else None

    det = VehicleDetector(model_path=model)
    engine = MetricsEngine(camera_id, calib, window_s=2.0)
    scale_verified = cfg.scale_is_verified(camera_id)

    out_path = cfg.ANNOTATED_DIR / f"{camera_id}_demo.mp4"
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, tuple(calib.frame_size))

    latest: dict = {}
    tally: Counter = Counter()
    n = 0
    t0 = time.time()

    for fr in det.stream(cfg.camera_video_path(camera_id), stride=stride,
                         max_frames=max_frames, keep_frame=True):
        n += 1
        for wnd in engine.process(fr):
            latest[wnd.zone_id] = wnd
        for d in fr.detections:
            tally[d.group] += 1

        highlight = {zid: los_color(w.los) for zid, w in latest.items()}
        vis = Z.draw_zones(fr.frame, calib, alpha=0.28,
                           show_labels=False, highlight=highlight)
        vis = draw_detections(vis, fr.detections, show_id=False)
        vis = draw_panel(vis, cam, calib, latest, tally,
                         fr.timestamp_s, scale_verified)
        writer.write(vis)

        if n % 100 == 0:
            print(f"    {n} frames ({fr.timestamp_s:.0f}s of footage)")

    writer.release()
    size_mb = out_path.stat().st_size / 1e6
    print(f"  {cam['name'][:40]:<42} {n:>4} frames  "
          f"{time.time() - t0:>5.0f}s  {size_mb:>5.1f} MB")
    print(f"    -> {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", nargs="?", choices=list(cfg.CAMERAS.keys()))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--junctions", action="store_true",
                    help="just the two junction cameras")
    ap.add_argument("--seconds", type=float, default=None,
                    help="footage seconds to render (default: whole clip)")
    ap.add_argument("--fps", type=int, default=10, help="output frame rate")
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--model", default=str(cfg.DEFAULT_MODEL))
    args = ap.parse_args()

    if args.all:
        cams = list(cfg.CAMERAS.keys())
    elif args.junctions:
        cams = cfg.junction_cameras()
    elif args.camera:
        cams = [args.camera]
    else:
        ap.error("give a camera id, --junctions or --all")

    print("Rendering demo videos...")
    for c in cams:
        render(c, args.seconds, args.fps, args.stride, args.model)


if __name__ == "__main__":
    main()
