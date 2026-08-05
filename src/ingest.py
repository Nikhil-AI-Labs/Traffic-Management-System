"""Analyse footage the system has never seen: an uploaded video or a still.

The seven dataset cameras have hand-traced zone polygons and a known scene.
An uploaded clip has neither, so this module has to answer three questions
before it can measure anything:

  1. **Where is the road?** Derived from where vehicles actually go — the
     detector is run over sampled frames and the ground-contact points are
     accumulated into a mask. That is strictly better than asking a stranger to
     trace polygons, and it is the same empirical check we used to validate the
     dataset cameras' hand-traced zones.
  2. **How many lanes?** Only the operator knows. Left at zero the corridor is
     treated as one shared space in area mode, which is the honest default for
     footage we know nothing about.
  3. **What is the scale?** Unknown unless the operator measures a width, so
     metric figures stay marked as estimates exactly as they do elsewhere.

A **single image** is handled separately and deliberately reports less. Speed,
flow, queue dynamics and any forecast need two moments in time; one frame
cannot supply them, and inventing them would undo the point of the project.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np
import pandas as pd

import config as cfg

cfg.quiet_opencv()
import zones as Z
from detector import Detection, FrameResult, VehicleDetector
from metrics import MetricsEngine

UPLOAD_DIR = cfg.OUTPUT_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".mpg", ".mpeg", ".webm"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

Progress = Callable[[float, str], None]


def _noop(_frac: float, _msg: str) -> None:
    pass


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------

@dataclass
class MediaInfo:
    path: Path
    kind: str                 # video | image
    ok: bool
    width: int = 0
    height: int = 0
    fps: float = 0.0
    n_frames: int = 0
    duration_s: float = 0.0
    error: str = ""

    @property
    def label(self) -> str:
        if self.kind == "image":
            return f"{self.width}×{self.height} still"
        return (f"{self.width}×{self.height} · {self.fps:.0f} fps · "
                f"{self.duration_s:.0f}s")


def probe(path: Path | str) -> MediaInfo:
    """What is this file, and can OpenCV actually read it?

    Checked up front rather than at frame 400: OpenCV's Windows build silently
    fails on some codecs, and finding that out halfway through a three-minute
    analysis is a bad experience.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in IMAGE_SUFFIXES:
        img = cv2.imread(str(path))
        if img is None:
            return MediaInfo(path, "image", False,
                             error="Could not decode this image.")
        h, w = img.shape[:2]
        return MediaInfo(path, "image", True, width=w, height=h, n_frames=1)

    if suffix not in VIDEO_SUFFIXES:
        return MediaInfo(path, "video", False,
                         error=f"Unsupported file type '{suffix}'.")

    cap = cfg.open_video(str(path))
    if not cap.isOpened():
        cap.release()
        return MediaInfo(path, "video", False,
                         error="OpenCV could not open this video. It may use a "
                               "codec this build does not ship — re-encoding to "
                               "H.264 MP4 will fix it.")
    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        return MediaInfo(path, "video", False,
                         error="The video opened but the first frame could not "
                               "be decoded.")

    h, w = frame.shape[:2]
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()

    if fps <= 0 or fps > 240:
        fps = 25.0
    duration = n / fps if n > 0 else 0.0
    return MediaInfo(path, "video", True, width=w, height=h, fps=fps,
                     n_frames=n, duration_s=duration)


def save_upload(data: bytes, filename: str) -> Path:
    """Write an uploaded file where OpenCV can reach it by path."""
    stem = Path(filename).stem[:48] or "upload"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)
    out = UPLOAD_DIR / f"{safe}_{uuid.uuid4().hex[:8]}{Path(filename).suffix.lower()}"
    out.write_bytes(data)
    return out


def session_id(path: Path) -> str:
    return f"upload::{path.stem}"


# --------------------------------------------------------------------------
# Finding the road
# --------------------------------------------------------------------------

@dataclass
class RoiResult:
    mask: np.ndarray
    polygon: list[tuple[int, int]] | None
    coverage: float              # fraction of frame the ROI covers
    n_anchors: int
    method: str                  # traffic | motion | whole-frame
    note: str = ""


def auto_roi(
    media: MediaInfo,
    detector: VehicleDetector,
    samples: int = 90,
    on_progress: Progress = _noop,
) -> RoiResult:
    """Find the drivable corridor from where vehicles actually travel.

    Two independent signals, because either alone fails on some footage:
    vehicle ground-contact points (precise, but sparse if the detector
    struggles) and frame-to-frame motion energy (dense, but includes
    pedestrians and swaying foliage). Whichever produces a usable region wins,
    with a whole-frame fallback so an upload always yields *something* rather
    than an error.
    """
    h, w = media.height, media.width
    accum = np.zeros((h, w), dtype=np.float32)
    motion = np.zeros((h, w), dtype=np.float32)

    cap = cfg.open_video(str(media.path))
    total = max(1, media.n_frames)
    step = max(1, total // max(1, samples))
    n_anchors = 0
    prev_gray = None
    taken = 0

    try:
        for i in range(0, total, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            taken += 1
            on_progress(min(0.99, taken / max(1, samples)),
                        f"Scanning the scene — frame {i:,} of {total:,}")

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if prev_gray is not None:
                motion += cv2.absdiff(gray, prev_gray).astype(np.float32)
            prev_gray = gray

            res = detector.model.predict(
                frame, conf=detector.conf, iou=detector.iou,
                imgsz=detector.imgsz, device=detector.device, verbose=False)
            if not res:
                continue
            boxes = getattr(res[0], "boxes", None)
            if boxes is None or boxes.xyxy is None or len(boxes) == 0:
                continue
            for x1, y1, x2, y2 in boxes.xyxy.cpu().numpy():
                cx, cy = int((x1 + x2) / 2), int(y2)
                if 0 <= cy < h and 0 <= cx < w:
                    # Weight by box size so a distant scooter does not carve
                    # the same footprint as a nearby bus.
                    r = max(6, int((x2 - x1) * 0.35))
                    cv2.circle(accum, (cx, cy), r, 1.0, -1)
                    n_anchors += 1
    finally:
        cap.release()

    def _to_region(field: np.ndarray, thresh_q: float,
                   method: str) -> RoiResult | None:
        if field.max() <= 0:
            return None
        norm = field / field.max()
        blur = cv2.GaussianBlur(norm, (0, 0), sigmaX=max(9, w / 90))
        t = np.quantile(blur[blur > 0], thresh_q) if (blur > 0).any() else 1.0
        mask = (blur >= max(t, 1e-6)).astype(np.uint8) * 255

        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)

        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None
        big = max(cnts, key=cv2.contourArea)
        area = cv2.contourArea(big)
        coverage = area / float(h * w)
        if coverage < 0.012:
            return None

        approx = cv2.approxPolyDP(big, 0.008 * cv2.arcLength(big, True), True)
        poly = [(int(p[0][0]), int(p[0][1])) for p in approx]
        if len(poly) < 3:
            return None

        clean = np.zeros((h, w), np.uint8)
        cv2.fillPoly(clean, [np.array(poly, np.int32)], 255)
        return RoiResult(clean, poly, coverage, n_anchors, method)

    r = _to_region(accum, 0.34, "traffic")
    if r is not None:
        r.note = (f"Traced from {n_anchors:,} vehicle ground-contact points "
                  f"across {taken} sampled frames.")
        return r

    r = _to_region(motion, 0.55, "motion")
    if r is not None:
        r.note = ("Too few vehicles were detected to trace the road from "
                  "traffic, so the region came from frame-to-frame motion "
                  "instead. It may include pedestrians and moving foliage.")
        return r

    mask = np.full((h, w), 255, np.uint8)
    poly = [(0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)]
    return RoiResult(mask, poly, 1.0, n_anchors, "whole-frame",
                     note="Neither traffic nor motion produced a usable region, "
                          "so the whole frame is treated as one zone. Occupancy "
                          "will include sky, buildings and pavement, so read it "
                          "as relative to itself only.")


def _mask_span(mask: np.ndarray, row: int) -> tuple[int, int] | None:
    line = mask[int(np.clip(row, 0, mask.shape[0] - 1))] > 0
    if not line.any():
        return None
    best_len = best_start = run = start = 0
    for i, v in enumerate(line):
        if v:
            if run == 0:
                start = i
            run += 1
            if run > best_len:
                best_len, best_start = run, start
        else:
            run = 0
    return (best_start, best_start + best_len - 1)


def build_calibration(
    session: str,
    roi: RoiResult,
    frame_size: tuple[int, int],
    n_lanes: int = 0,
    label_prefix: str = "Lane",
) -> Z.CameraCalibration:
    """Turn the ROI into zones.

    With `n_lanes = 0` the corridor becomes a single carriageway zone and the
    session runs in area mode — the right answer when nobody has told us the
    road is lane-marked. With a lane count the corridor is split into that many
    bands, following the corridor's own edges row by row so the bands converge
    with perspective instead of staying parallel in image space.
    """
    w, h = frame_size
    if n_lanes and n_lanes > 0 and roi.method != "whole-frame":
        rows = [r for r in range(0, h, max(8, h // 40))
                if _mask_span(roi.mask, r) is not None]
        if len(rows) >= 4:
            zones = []
            for i in range(n_lanes):
                f0, f1 = i / n_lanes, (i + 1) / n_lanes
                left, right = [], []
                for r in rows:
                    span = _mask_span(roi.mask, r)
                    if span is None:
                        continue
                    x0, x1 = span
                    left.append((int(x0 + (x1 - x0) * f0), r))
                    right.append((int(x0 + (x1 - x0) * f1), r))
                poly = left + right[::-1]
                if len(poly) >= 3:
                    zones.append(Z.Zone(
                        id=f"lane_{i + 1}",
                        label=f"{label_prefix} {i + 1}",
                        polygon=[(int(x), int(y)) for x, y in poly],
                        kind="lane",
                        approach=session,
                        lanes_equivalent=1.0,
                    ))
            if zones:
                return Z.CameraCalibration(
                    camera_id=session, mode="junction", zones=zones,
                    frame_size=(w, h),
                    notes=f"Auto-generated from uploaded footage. {roi.note}")

    return Z.CameraCalibration(
        camera_id=session, mode="area",
        zones=[Z.Zone(
            id="carriageway", label="Movement area",
            polygon=[(int(x), int(y)) for x, y in (roi.polygon or [])] or
                    [(0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)],
            kind="carriageway",
            lanes_equivalent=max(1.0, float(n_lanes or 1)),
        )],
        frame_size=(w, h),
        notes=f"Auto-generated from uploaded footage. {roi.note}",
    )


def preview_zones(media: MediaInfo, calib: Z.CameraCalibration,
                  roi: RoiResult | None = None) -> np.ndarray | None:
    """First frame with the derived zones drawn, RGB for st.image."""
    if media.kind == "image":
        frame = cv2.imread(str(media.path))
    else:
        cap = cfg.open_video(str(media.path))
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, media.n_frames // 4))
        ok, frame = cap.read()
        cap.release()
        if not ok:
            frame = None
    if frame is None:
        return None
    out = Z.draw_zones(frame, calib, alpha=0.30, show_labels=True)
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

@dataclass
class VideoResult:
    session: str
    media: MediaInfo
    calib: Z.CameraCalibration
    metrics: pd.DataFrame
    frames_processed: int
    seconds_covered: float
    elapsed_s: float
    class_counts: dict[str, int] = field(default_factory=dict)
    group_counts: dict[str, int] = field(default_factory=dict)
    preview: np.ndarray | None = None


def analyze_video(
    media: MediaInfo,
    calib: Z.CameraCalibration,
    detector: VehicleDetector,
    stride: int = 3,
    window_s: float = 2.0,
    max_seconds: float | None = None,
    on_progress: Progress = _noop,
) -> VideoResult:
    """Run the full measurement pipeline over an uploaded clip.

    Identical to what the dataset cameras go through — same detector, same
    zone assignment by ground-contact point, same PCU conversion, same
    saturation test, same occupancy fallback. Only the zones differ in where
    they came from.
    """
    session = calib.camera_id
    cfg.register_camera(session, {
        "name": media.path.stem.replace("_", " ")[:48] or "Uploaded footage",
        "mode": calib.mode,
        "video": str(media.path),
        "notes": "Uploaded through the dashboard.",
    })

    engine = MetricsEngine(session, calib, window_s=window_s)
    detector.reset_tracker()

    fps = media.fps or 25.0
    max_frames = None
    if max_seconds:
        max_frames = int(max_seconds * fps / stride)
    total_expected = max_frames or max(1, int(media.n_frames / stride))

    rows: list[dict] = []
    class_counts: dict[str, int] = {}
    group_counts: dict[str, int] = {}
    preview = None
    n = 0
    last_t = 0.0
    t0 = time.time()

    for fr in detector.stream(media.path, stride=stride,
                              max_frames=max_frames, keep_frame=True):
        n += 1
        last_t = fr.timestamp_s
        for w in engine.process(fr):
            rows.append(w.to_dict())
        for d in fr.detections:
            class_counts[d.cls_name] = class_counts.get(d.cls_name, 0) + 1
            group_counts[d.group] = group_counts.get(d.group, 0) + 1

        if preview is None and n == max(1, total_expected // 5) and fr.frame is not None:
            from detector import draw_detections
            vis = Z.draw_zones(fr.frame, calib, alpha=0.26, show_labels=False)
            vis = draw_detections(vis, fr.detections, show_id=False)
            preview = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)

        if n % 5 == 0 or n == 1:
            on_progress(min(0.99, n / total_expected),
                        f"Measuring — {fr.timestamp_s:.0f}s of footage, "
                        f"{len(rows)} windows")

    for w in engine.flush(last_t):
        rows.append(w.to_dict())

    on_progress(1.0, "Done")
    return VideoResult(
        session=session, media=media, calib=calib,
        metrics=pd.DataFrame(rows), frames_processed=n,
        seconds_covered=last_t, elapsed_s=time.time() - t0,
        class_counts=class_counts, group_counts=group_counts,
        preview=preview,
    )


@dataclass
class ImageResult:
    session: str
    media: MediaInfo
    calib: Z.CameraCalibration
    zones: list[dict]
    detections: list[Detection]
    class_counts: dict[str, int]
    group_counts: dict[str, int]
    annotated: np.ndarray | None = None
    # Stated explicitly so the UI can print it rather than quietly omitting it.
    not_measurable: tuple[str, ...] = (
        "speed", "flow", "queue length", "level-of-service over time",
        "bottleneck duration", "forecast", "signal timing",
    )


def analyze_image(
    media: MediaInfo,
    calib: Z.CameraCalibration,
    detector: VehicleDetector,
) -> ImageResult:
    """Everything a single frame can honestly support, and nothing more.

    One frame fixes an instant: how many vehicles, of what kinds, how much PCU,
    how much of the zone is covered. It cannot fix a rate. Speed, flow, queue
    dynamics and any forecast require two moments in time, so they are reported
    as unavailable rather than estimated — a plausible-looking speed derived
    from one frame would be pure fabrication.
    """
    frame = cv2.imread(str(media.path))
    session = calib.camera_id
    cfg.register_camera(session, {
        "name": media.path.stem.replace("_", " ")[:48] or "Uploaded still",
        "mode": calib.mode, "video": str(media.path),
    })

    res = detector.model.predict(
        frame, conf=detector.conf, iou=detector.iou, imgsz=detector.imgsz,
        device=detector.device, verbose=False)
    dets = detector._parse(res)

    # smooth_alpha=1.0: no temporal smoothing, because there is no time series
    # here — one frame is the whole observation.
    from metrics import StructuralOccupancy
    occ = StructuralOccupancy(calib, smooth_alpha=1.0)
    occ_by_zone = {z.id: occ.index(frame, z) for z in calib.zones}

    class_counts: dict[str, int] = {}
    group_counts: dict[str, int] = {}
    for d in dets:
        class_counts[d.cls_name] = class_counts.get(d.cls_name, 0) + 1
        group_counts[d.group] = group_counts.get(d.group, 0) + 1

    zone_rows = []
    for z in calib.zones:
        inside = [d for d in dets if z.contains(d.anchor)]
        pcu = sum(d.pcu for d in inside)
        area_m2 = Z.zone_real_area(z, calib.scale_model)
        box_cover = (sum(d.area for d in inside) / z.pixel_area
                     if z.pixel_area > 0 else 0.0)
        structural = float(occ_by_zone.get(z.id, 0.0))
        density = (pcu / area_m2 * 100.0) if area_m2 else None
        zone_rows.append({
            "zone_id": z.id, "zone_label": z.label, "zone_kind": z.kind,
            "n_detections": len(inside),
            "pcu": round(pcu, 2),
            "real_area_m2": round(area_m2) if area_m2 else None,
            "density_pcu_per_100m2": round(density, 3) if density else None,
            "density_pcu_per_km_lane": (round(density * 35.0, 1)
                                        if density else None),
            # Not clamped, matching the video path: values above 1 mean
            # the detection boxes overlap, which is information.
            "box_occupancy": round(box_cover, 4),
            "structural_occupancy": round(structural, 4),
            "congestion_index": round(structural, 3),
            "los": _los_for(structural),
        })

    from detector import draw_detections
    vis = Z.draw_zones(frame, calib, alpha=0.24, show_labels=False)
    vis = draw_detections(vis, dets, show_id=False)

    return ImageResult(
        session=session, media=media, calib=calib, zones=zone_rows,
        detections=dets, class_counts=class_counts, group_counts=group_counts,
        annotated=cv2.cvtColor(vis, cv2.COLOR_BGR2RGB),
    )


def _los_for(ci: float) -> str:
    return ("Jam" if ci >= 0.75 else "Heavy" if ci >= 0.5
            else "Moderate" if ci >= 0.25 else "Free")


def persist(result: VideoResult) -> Path:
    """Write an upload's metrics beside the dataset cameras' CSVs."""
    safe = result.session.replace("::", "_")
    out = cfg.LOG_DIR / f"{safe}_metrics.csv"
    result.metrics.to_csv(out, index=False)
    Z.save(result.calib, cfg.CONFIG_DIR / "uploaded_cameras.json")
    return out
