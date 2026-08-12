"""YOLO11 + ByteTrack detection and tracking over a video source.

Wraps ultralytics so the rest of the pipeline sees a clean stream of tracked
detections rather than framework objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

import config as cfg


def _pick_device() -> str | int:
    """GPU if there is one, CPU otherwise.

    Hard-coding device 0 made the pipeline unusable on a machine without CUDA,
    which matters now that a dashboard user can start an analysis themselves —
    slow is a far better failure than a stack trace.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return 0
    except Exception:
        pass
    return "cpu"


@dataclass
class Detection:
    """One tracked object in one frame."""

    track_id: int | None
    cls_id: int
    cls_name: str
    conf: float
    xyxy: tuple[float, float, float, float]

    @property
    def anchor(self) -> tuple[float, float]:
        """Bottom-centre of the box — where the vehicle meets the road.

        Using the box centroid instead would place tall vehicles (buses, trucks)
        well above their actual road position and mis-assign them to the wrong
        lane on an angled camera view.
        """
        x1, y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2.0, y2)

    @property
    def pcu(self) -> float:
        return cfg.PCU_FACTORS.get(self.cls_name, 1.0)

    @property
    def group(self) -> str:
        return cfg.CLASS_GROUPS.get(self.cls_name, "Other")

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.xyxy
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)


@dataclass
class FrameResult:
    """All detections for a single processed frame."""

    frame_idx: int
    timestamp_s: float
    detections: list[Detection] = field(default_factory=list)
    frame: np.ndarray | None = None

    @property
    def total_pcu(self) -> float:
        return sum(d.pcu for d in self.detections)

    def counts_by_group(self) -> dict[str, int]:
        out = {g: 0 for g in cfg.GROUP_ORDER}
        for d in self.detections:
            out[d.group] = out.get(d.group, 0) + 1
        return out


class VehicleDetector:
    """YOLO11 detector with persistent ByteTrack IDs across frames."""

    def __init__(
        self,
        model_path: Path | str = cfg.DEFAULT_MODEL,
        conf: float = cfg.DETECT_CONF,
        iou: float = cfg.DETECT_IOU,
        imgsz: int = cfg.DETECT_IMGSZ,
        device: str | int | None = None,
        tracker: str = cfg.TRACKER,
    ):
        model_path = Path(model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Model weights not found at {model_path}. "
                "Run scripts/download_models.py first."
            )
        # Imported here rather than at module scope, because importing
        # ultralytics pulls in torch — around 2 GB installed and several hundred
        # MB resident. `Detection` and `FrameResult` below are plain dataclasses
        # that half the project depends on (metrics, ingest, and through them the
        # dashboard), so a module-level import made merely opening the dashboard
        # load a deep-learning stack it never calls. That is fine locally and
        # fatal on a memory-capped host: the Webster track reads committed CSVs
        # and needs no detector at all, so the deployed app carries neither
        # torch nor ultralytics and this line is simply never reached.
        from ultralytics import YOLO

        self.model = YOLO(str(model_path))
        self.conf = conf
        self.iou = iou
        self.imgsz = imgsz
        self.device = _pick_device() if device is None else device
        self.tracker = tracker

    def stream(
        self,
        video_path: Path | str,
        stride: int = cfg.DEFAULT_STRIDE,
        max_frames: int | None = None,
        start_frame: int = 0,
        keep_frame: bool = False,
        verbose: bool = False,
    ) -> Iterator[FrameResult]:
        """Yield a FrameResult for every processed frame.

        stride=N processes every Nth frame. Tracking still works across a
        stride because ByteTrack matches on IoU + motion, and at 10 fps
        effective the inter-frame displacement stays small.
        """
        video_path = str(video_path)
        cap = cfg.open_video(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
        if start_frame:
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        frame_idx = start_frame
        processed = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break

                if (frame_idx - start_frame) % stride != 0:
                    frame_idx += 1
                    continue

                results = self.model.track(
                    frame,
                    persist=True,
                    conf=self.conf,
                    iou=self.iou,
                    imgsz=self.imgsz,
                    device=self.device,
                    tracker=self.tracker,
                    verbose=verbose,
                )

                dets = self._parse(results)
                yield FrameResult(
                    frame_idx=frame_idx,
                    timestamp_s=frame_idx / fps,
                    detections=dets,
                    frame=frame if keep_frame else None,
                )

                processed += 1
                frame_idx += 1
                if max_frames is not None and processed >= max_frames:
                    break
        finally:
            cap.release()

    def _parse(self, results) -> list[Detection]:
        dets: list[Detection] = []
        if not results:
            return dets

        r = results[0]
        boxes = getattr(r, "boxes", None)
        if boxes is None or boxes.xyxy is None or len(boxes) == 0:
            return dets

        xyxy = boxes.xyxy.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        conf = boxes.conf.cpu().numpy()
        ids = (
            boxes.id.cpu().numpy().astype(int)
            if boxes.id is not None
            else [None] * len(cls)
        )

        for box, c, cf, tid in zip(xyxy, cls, conf, ids):
            dets.append(
                Detection(
                    track_id=int(tid) if tid is not None else None,
                    cls_id=int(c),
                    cls_name=cfg.CLASS_NAMES.get(int(c), str(c)),
                    conf=float(cf),
                    xyxy=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                )
            )
        return dets

    def reset_tracker(self) -> None:
        """Clear tracker state between videos so IDs don't leak across cameras."""
        if hasattr(self.model, "predictor") and self.model.predictor is not None:
            if hasattr(self.model.predictor, "trackers"):
                for t in self.model.predictor.trackers:
                    if hasattr(t, "reset"):
                        t.reset()


# --------------------------------------------------------------------------
# Annotation helpers
# --------------------------------------------------------------------------

_GROUP_COLORS = {
    "Two-wheeler": (255, 180, 0),
    "Auto": (0, 220, 255),
    "Car": (0, 255, 120),
    "LCV/Tempo": (200, 100, 255),
    "Bus/Truck": (0, 100, 255),
    "Other": (180, 180, 180),
}


def draw_detections(
    frame: np.ndarray,
    detections: list[Detection],
    show_id: bool = True,
    show_conf: bool = False,
) -> np.ndarray:
    """Draw boxes coloured by vehicle group."""
    out = frame.copy()
    for d in detections:
        x1, y1, x2, y2 = (int(v) for v in d.xyxy)
        color = _GROUP_COLORS.get(d.group, (200, 200, 200))
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)

        label = d.cls_name
        if show_id and d.track_id is not None:
            label = f"#{d.track_id} {label}"
        if show_conf:
            label = f"{label} {d.conf:.2f}"

        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.rectangle(out, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
        cv2.putText(
            out, label, (x1 + 2, y1 - 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA,
        )
    return out


def draw_hud(frame: np.ndarray, lines: list[str], origin=(12, 12)) -> np.ndarray:
    """Translucent info panel in the top-left corner."""
    out = frame
    x, y = origin
    pad = 8
    line_h = 26
    width = 10 + max((len(s) for s in lines), default=0) * 11
    height = pad * 2 + line_h * len(lines)

    overlay = out.copy()
    cv2.rectangle(overlay, (x, y), (x + width, y + height), (0, 0, 0), -1)
    out = cv2.addWeighted(overlay, 0.55, out, 0.45, 0)

    for i, s in enumerate(lines):
        cv2.putText(
            out, s, (x + pad, y + pad + line_h * (i + 1) - 7),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA,
        )
    return out
