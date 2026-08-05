"""Lane / zone geometry: definition, persistence, point assignment, rendering.

A camera's calibration is a list of polygons drawn in image space. For junction
cameras these are traffic lanes; for area cameras they are functional zones
(carriageway, pedestrian area, parking encroachment, ...).

Optionally a polygon carries a `real_area_m2` so density can be reported in
proper engineering units (PCU per 100 m^2) instead of a pixel-relative number.
Where that is not known we fall back to pixel-area occupancy, and say so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

import cv2
import numpy as np

import config as cfg


@dataclass
class Zone:
    """One lane or zone polygon on a camera view."""

    id: str
    label: str
    polygon: list[tuple[int, int]]
    kind: str = "lane"          # lane | carriageway | pedestrian | parking | platform
    approach: str | None = None  # which junction approach this lane feeds
    real_area_m2: float | None = None
    lanes_equivalent: float = 1.0  # for area zones covering several lanes' worth
    notes: str = ""

    # -- geometry ---------------------------------------------------------

    @property
    def np_polygon(self) -> np.ndarray:
        return np.array(self.polygon, dtype=np.int32)

    @property
    def pixel_area(self) -> float:
        return float(cv2.contourArea(self.np_polygon))

    def contains(self, point: tuple[float, float]) -> bool:
        return cv2.pointPolygonTest(self.np_polygon, (float(point[0]), float(point[1])), False) >= 0

    def centroid(self) -> tuple[int, int]:
        m = cv2.moments(self.np_polygon)
        if m["m00"] == 0:
            pts = self.np_polygon
            return int(pts[:, 0].mean()), int(pts[:, 1].mean())
        return int(m["m10"] / m["m00"]), int(m["m01"] / m["m00"])

    # -- persistence ------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["polygon"] = [[int(x), int(y)] for x, y in self.polygon]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Zone":
        d = dict(d)
        d["polygon"] = [(int(x), int(y)) for x, y in d["polygon"]]
        return cls(**d)


@dataclass
class CameraCalibration:
    """All zones for one camera, plus optional scale information."""

    camera_id: str
    mode: str
    zones: list[Zone] = field(default_factory=list)
    frame_size: tuple[int, int] = (1920, 1080)
    # Optional metric scale: how many square metres one square pixel covers at
    # a given reference row. Perspective makes this vary with y, so we store a
    # simple two-point linear model instead of a single constant.
    scale_model: dict | None = None
    notes: str = ""

    def zone(self, zone_id: str) -> Zone | None:
        return next((z for z in self.zones if z.id == zone_id), None)

    def lanes(self) -> list[Zone]:
        return [z for z in self.zones if z.kind == "lane"]

    def assign(self, point: tuple[float, float]) -> Zone | None:
        """Which zone contains this point (first match wins)."""
        for z in self.zones:
            if z.contains(point):
                return z
        return None

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "mode": self.mode,
            "frame_size": list(self.frame_size),
            "scale_model": self.scale_model,
            "notes": self.notes,
            "zones": [z.to_dict() for z in self.zones],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CameraCalibration":
        return cls(
            camera_id=d["camera_id"],
            mode=d["mode"],
            frame_size=tuple(d.get("frame_size", (1920, 1080))),
            scale_model=d.get("scale_model"),
            notes=d.get("notes", ""),
            zones=[Zone.from_dict(z) for z in d.get("zones", [])],
        )


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

def load_all(path: Path = cfg.CAMERA_CONFIG) -> dict[str, CameraCalibration]:
    if not Path(path).exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {k: CameraCalibration.from_dict(v) for k, v in raw.items()}


def load(camera_id: str, path: Path = cfg.CAMERA_CONFIG) -> CameraCalibration | None:
    return load_all(path).get(camera_id)


def save(calib: CameraCalibration, path: Path = cfg.CAMERA_CONFIG) -> None:
    path = Path(path)
    all_c = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            all_c = json.load(f)
    all_c[calib.camera_id] = calib.to_dict()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(all_c, f, indent=2)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

_KIND_COLORS = {
    "lane": (0, 220, 120),
    "carriageway": (0, 200, 255),
    "pedestrian": (255, 160, 0),
    "parking": (200, 0, 255),
    "platform": (255, 100, 100),
}


def draw_zones(
    frame: np.ndarray,
    calib: CameraCalibration,
    alpha: float = 0.25,
    show_labels: bool = True,
    highlight: dict[str, tuple[int, int, int]] | None = None,
) -> np.ndarray:
    """Shade each zone. `highlight` overrides a zone's colour by id (used to
    paint live LOS state onto the lanes)."""
    out = frame.copy()
    overlay = frame.copy()

    for z in calib.zones:
        color = (highlight or {}).get(z.id) or _KIND_COLORS.get(z.kind, (200, 200, 200))
        cv2.fillPoly(overlay, [z.np_polygon], color)

    out = cv2.addWeighted(overlay, alpha, out, 1 - alpha, 0)

    for z in calib.zones:
        color = (highlight or {}).get(z.id) or _KIND_COLORS.get(z.kind, (200, 200, 200))
        cv2.polylines(out, [z.np_polygon], True, color, 2)
        if show_labels:
            cx, cy = z.centroid()
            (tw, th), _ = cv2.getTextSize(z.label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(out, (cx - tw // 2 - 5, cy - th - 6),
                          (cx + tw // 2 + 5, cy + 5), (0, 0, 0), -1)
            cv2.putText(out, z.label, (cx - tw // 2, cy),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    return out


# --------------------------------------------------------------------------
# Metric scale
# --------------------------------------------------------------------------

def build_scale_model(
    y_near: float, px_width_near: float,
    y_far: float, px_width_far: float,
    real_width_m: float,
    focal_px: float = 1663.0,
    camera_height_m: float = 7.0,
) -> dict:
    """Perspective scale model for a camera looking at a flat road.

    Ground scale is *anisotropic*: one pixel of sideways movement and one pixel
    of movement up the road cover very different real distances, and the gap
    widens towards the horizon. Treating them as equal is the classic way to
    get badly wrong speeds — vehicles approaching a traffic camera move almost
    entirely "up the frame", so an isotropic scale under-reads their speed by
    the ratio between the two, which here is a factor of two or more.

    For a pinhole camera at height h above a flat plane, ground distance Z maps
    to image row as Z = C / (y - y_horizon). Differentiating gives the two
    scales, in metres per pixel:

        lateral       s_h(y) = Z / f          proportional to Z
        longitudinal  s_v(y) = dZ/dy = Z^2 / (f * h)    proportional to Z^2

    so s_v = s_h^2 * (f / h). We fit s_h from the pixel width of a known
    real-world width (a lane, or the full carriageway) measured at two rows,
    which recovers the horizon row as a by-product, then derive s_v from it.

    focal_px / camera_height_m are the two assumptions carried by this model.
    Defaults suit a 1080p traffic camera with roughly a 60 degree horizontal
    field of view on a 7 m pole. Everything derived from them is reported as
    approximate.
    """
    s_near = real_width_m / float(px_width_near)   # m per px, laterally
    s_far = real_width_m / float(px_width_far)

    # Solve s = C / (y - y_h) through both calibration rows.
    if abs(s_near - s_far) < 1e-12:
        y_horizon = float(y_far) - 1e6
        c = s_near * (float(y_near) - y_horizon)
    else:
        y_horizon = (s_near * y_near - s_far * y_far) / (s_near - s_far)
        c = s_near * (float(y_near) - y_horizon)

    return {
        "type": "perspective_v1",
        "y_horizon": float(y_horizon),
        "c": float(c),
        "focal_px": float(focal_px),
        "camera_height_m": float(camera_height_m),
        "calibration": {
            "y_near": float(y_near), "px_width_near": float(px_width_near),
            "y_far": float(y_far), "px_width_far": float(px_width_far),
            "real_width_m": float(real_width_m),
        },
    }


def _lateral_m_per_px(model: dict, y: float) -> float | None:
    """Metres of ground per pixel, measured across the road."""
    if not model or model.get("type") != "perspective_v1":
        return None
    denom = y - model["y_horizon"]
    if denom <= 1e-6:
        return None
    return model["c"] / denom


def lateral_m_per_px(model: dict | None, y: float) -> float | None:
    return _lateral_m_per_px(model, y) if model else None


def longitudinal_m_per_px(model: dict | None, y: float) -> float | None:
    """Metres of ground per pixel, measured along the road (into the frame)."""
    s_h = _lateral_m_per_px(model, y) if model else None
    if s_h is None:
        return None
    return s_h * s_h * (model["focal_px"] / model["camera_height_m"])


def ground_displacement_m(
    model: dict | None, x0: float, y0: float, x1: float, y1: float
) -> float | None:
    """Real-world distance between two image points on the road plane."""
    if not model:
        return None
    y_mid = (y0 + y1) / 2.0
    s_h = lateral_m_per_px(model, y_mid)
    s_v = longitudinal_m_per_px(model, y_mid)
    if s_h is None or s_v is None:
        return None
    dx = (x1 - x0) * s_h
    dy = (y1 - y0) * s_v
    return float((dx * dx + dy * dy) ** 0.5)


def m2_per_pixel(scale_model: dict | None, y: float) -> float | None:
    """Ground area (m^2) covered by one pixel at image row y."""
    s_h = lateral_m_per_px(scale_model, y)
    s_v = longitudinal_m_per_px(scale_model, y)
    if s_h is None or s_v is None:
        return None
    return s_h * s_v


def zone_real_area(zone: Zone, scale_model: dict | None) -> float | None:
    """Approximate real-world area of a zone in m^2.

    Integrates the per-row scale over the polygon rather than using a single
    mid-point value, which matters for the long thin lane polygons that stretch
    a long way in depth.
    """
    if zone.real_area_m2 is not None:
        return zone.real_area_m2
    if not scale_model:
        return None

    pts = zone.np_polygon
    y0, y1 = int(pts[:, 1].min()), int(pts[:, 1].max())
    if y1 <= y0:
        return None

    total = 0.0
    step = max(1, (y1 - y0) // 200)
    for y in range(y0, y1, step):
        row_px = _row_width_in_polygon(zone, y)
        if row_px <= 0:
            continue
        a = m2_per_pixel(scale_model, y)
        if a is None:
            return None
        total += row_px * step * a
    return total if total > 0 else None


def _row_width_in_polygon(zone: Zone, y: int) -> float:
    """Pixel width of the polygon at image row y."""
    pts = zone.polygon
    xs = []
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if y1 == y2:
            continue
        if min(y1, y2) <= y < max(y1, y2):
            t = (y - y1) / (y2 - y1)
            xs.append(x1 + t * (x2 - x1))
    if len(xs) < 2:
        return 0.0
    return max(xs) - min(xs)
