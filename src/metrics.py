"""Traffic metrics: density, speed, queue, flow and occupancy per zone.

Two measurement paths, because one metric cannot honestly cover both scene
types in this dataset:

1. Detection-based (PCU density, speed, queue, flow). Valid while individual
   vehicles remain separable — the junction cameras.

2. Structural occupancy. An index derived from image texture rather than from
   resolved objects, so it keeps working when detection saturates. In the
   worst gridlock in this dataset the detector returns *fewer* boxes than in
   free-flowing traffic, because a wall of overlapping vehicles and people
   stops being separable. Reporting detection counts as congestion there would
   invert the ranking and call the most jammed street in the city the
   emptiest. Occupancy does not have that failure mode.

Which path leads is decided per zone by a saturation check, and the active
mode is carried in the output so the dashboard can be explicit about it.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict

import cv2
import numpy as np

import config as cfg
import zones as Z
from detector import Detection, FrameResult


# --------------------------------------------------------------------------
# Saturation flow (for Webster's method downstream)
# --------------------------------------------------------------------------
# IRC:SP:41 puts saturation flow for urban arterials in the region of
# 1800 PCU/hour per lane. Used as the default denominator for flow ratios.
SATURATION_FLOW_PCU_PER_LANE_PER_HR = 1800.0

# A track is "stopped" below this speed. Chosen above zero to absorb the
# jitter in box centres rather than to describe real creeping motion.
STOPPED_SPEED_KMPH = 4.0

# Saturation needs both a high object density and genuine mutual occlusion.
# A busy arterial lane can carry ~15 vehicles per 100 m^2 while still being
# perfectly countable, so density alone would misfire on flowing traffic.
SATURATION_DET_PER_100M2 = 22.0
SATURATION_OVERLAP_RATIO = 0.35

# The "full scene, empty count" signature. When texture says a zone is packed
# but the detector's boxes cover very little of it, the detector is missing
# objects rather than the zone being clear. This is the case that matters most
# and the one an overlap test cannot catch: overlap is computed between
# detections, so when detection itself collapses there is nothing left to
# overlap, and the worst gridlock would silently look calm.
SATURATION_OCCUPANCY_MIN = 0.62
SATURATION_BOX_COVER_MAX = 0.22


@dataclass
class ZoneWindow:
    """Aggregated metrics for one zone over one time window."""

    camera_id: str
    zone_id: str
    zone_label: str
    zone_kind: str
    window_start_s: float
    window_end_s: float

    # counts
    n_detections_mean: float = 0.0
    counts_by_group: dict[str, float] = field(default_factory=dict)
    unique_tracks: int = 0

    # density
    pcu_mean: float = 0.0
    density_pcu_per_100m2: float | None = None
    density_pcu_per_km_lane: float | None = None  # the unit engineers quote
    real_area_m2: float | None = None

    # dynamics
    mean_speed_kmph: float | None = None
    stopped_fraction: float = 0.0
    queue_length_pcu: float = 0.0

    # flow
    arrivals: int = 0
    flow_pcu_per_hr: float = 0.0

    # occupancy
    # Detection-box area divided by zone area. This EXCEEDS 1 when boxes
    # overlap, which is not an error — it is the overlap itself, and it is a
    # useful occlusion signal. It is therefore never clamped, and must not be
    # read as a 0-1 fraction. `structural_occupancy` is the bounded one.
    box_occupancy: float = 0.0
    structural_occupancy: float = 0.0  # texture-based, saturation-proof

    # state
    saturated: bool = False
    measurement_mode: str = "detection"  # detection | occupancy
    scale_verified: bool = False         # are the metric figures trustworthy?
    los: str = "Free"
    congestion_index: float = 0.0        # 0-1, the single comparable number

    def to_dict(self) -> dict:
        return asdict(self)


class ZoneAccumulator:
    """Rolling per-zone state across the frames inside one window."""

    def __init__(self, zone: Z.Zone, real_area_m2: float | None):
        self.zone = zone
        self.real_area_m2 = real_area_m2
        self.reset()

    def reset(self) -> None:
        self.frames = 0
        self.det_count_sum = 0
        self.pcu_sum = 0.0
        self.group_sum: dict[str, int] = defaultdict(int)
        self.box_area_sum = 0.0
        self.structural_sum = 0.0
        self.structural_n = 0
        self.speeds: list[float] = []
        self.stopped = 0
        self.moving = 0
        self.stopped_pcu_sum = 0.0
        self.track_ids: set[int] = set()
        self.overlap_samples: list[float] = []

    def add_frame(self, dets: list[Detection], speeds: dict[int, float]) -> None:
        self.frames += 1
        self.det_count_sum += len(dets)
        for d in dets:
            self.pcu_sum += d.pcu
            self.group_sum[d.group] += 1
            self.box_area_sum += d.area
            if d.track_id is not None:
                self.track_ids.add(d.track_id)
                sp = speeds.get(d.track_id)
                if sp is not None:
                    self.speeds.append(sp)
                    if sp < STOPPED_SPEED_KMPH:
                        self.stopped += 1
                        self.stopped_pcu_sum += d.pcu
                    else:
                        self.moving += 1
        self.overlap_samples.append(_overlap_ratio(dets))


def _overlap_ratio(dets: list[Detection]) -> float:
    """How much of the total box area is double-covered.

    A cheap proxy for "are objects still separable here". Rises sharply once a
    scene packs in tightly, which is exactly when counting becomes unreliable.
    """
    if len(dets) < 2:
        return 0.0
    total = sum(d.area for d in dets)
    if total <= 0:
        return 0.0

    inter = 0.0
    for i in range(len(dets)):
        for j in range(i + 1, len(dets)):
            a, b = dets[i].xyxy, dets[j].xyxy
            w = min(a[2], b[2]) - max(a[0], b[0])
            h = min(a[3], b[3]) - max(a[1], b[1])
            if w > 0 and h > 0:
                inter += w * h
    return min(1.0, inter / total)


# --------------------------------------------------------------------------
# Speed tracking
# --------------------------------------------------------------------------

class SpeedTracker:
    """Estimates per-track ground speed from anchor-point displacement.

    Pixel displacement is converted to metres using the camera's per-row scale,
    so the same motion near the camera and far up the road resolves to a
    comparable speed. Without a metric scale we report nothing rather than
    reporting pixels/second dressed up as km/h.
    """

    # A track unseen for this long is gone. ByteTrack keeps a lost track
    # alive for ~30 frames (1.5 s at 20 fps) before retiring its ID, so this
    # is comfortably past any re-identification - and without it the history
    # grows by one entry per track ever seen, which on a continuous RTSP feed
    # is an unbounded leak.
    STALE_TRACK_S = 10.0

    def __init__(self, scale_model: dict | None, history: int = 5):
        self.scale_model = scale_model
        self.history = history
        self.tracks: dict[int, deque] = defaultdict(lambda: deque(maxlen=history))
        self._last_prune_t = 0.0

    def update(self, fr: FrameResult) -> dict[int, float]:
        if fr.timestamp_s - self._last_prune_t >= self.STALE_TRACK_S:
            self._prune(fr.timestamp_s)
        speeds: dict[int, float] = {}
        for d in fr.detections:
            if d.track_id is None:
                continue
            x, y = d.anchor
            hist = self.tracks[d.track_id]
            hist.append((fr.timestamp_s, x, y))
            if len(hist) < 2:
                continue

            t0, x0, y0 = hist[0]
            t1, x1, y1 = hist[-1]
            dt = t1 - t0
            if dt <= 0:
                continue

            # Anisotropic: sideways and up-the-road pixels are different
            # ground distances, and vehicles here move mostly up the road.
            dist_m = Z.ground_displacement_m(self.scale_model, x0, y0, x1, y1)
            if dist_m is None:
                continue
            speeds[d.track_id] = (dist_m / dt) * 3.6
        return speeds

    def forget(self, active_ids: set[int]) -> None:
        for tid in list(self.tracks):
            if tid not in active_ids:
                del self.tracks[tid]

    def _prune(self, now_s: float) -> None:
        """Drop tracks whose most recent sighting is older than STALE_TRACK_S."""
        cutoff = now_s - self.STALE_TRACK_S
        for tid in [t for t, h in self.tracks.items() if not h or h[-1][0] < cutoff]:
            del self.tracks[tid]
        self._last_prune_t = now_s


# --------------------------------------------------------------------------
# Structural occupancy
# --------------------------------------------------------------------------

class StructuralOccupancy:
    """Texture-based measure of how much of a zone is covered by 'stuff'.

    Empty carriageway is a smooth, low-gradient surface; vehicles, people and
    stalls introduce dense edges. Measuring edge density inside a zone
    therefore tracks how occupied it is without needing to resolve individual
    objects — the property that makes it survive gridlock.

    Reported as an index on a fixed 0-1 scale, normalised against absolute
    edge-density bounds shared by every camera (see `index`), not as a
    physical quantity.
    """

    def __init__(self, calib: Z.CameraCalibration, smooth_alpha: float = 0.3):
        self.calib = calib
        self.masks: dict[str, np.ndarray] = {}
        self.zone_px: dict[str, int] = {}
        self.smooth_alpha = smooth_alpha
        self._ema: dict[str, float] = {}
        # Edge map of the most recent frame. Every zone of a camera is measured
        # on the same frame, and the grey/blur/Canny pass is the expensive part
        # — at 1080p it costs more than the rest of the window bookkeeping put
        # together. Computing it once per frame instead of once per zone gives
        # identical numbers at a fraction of the cost. The source frame is held
        # by reference and compared by identity, so a recycled `id()` can never
        # hand one frame's edges to another.
        self._edge_src: np.ndarray | None = None
        self._edge_map: np.ndarray | None = None

    def _edges(self, frame: np.ndarray) -> np.ndarray:
        if frame is not self._edge_src or self._edge_map is None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (5, 5), 0)
            self._edge_map = cv2.Canny(gray, 60, 160)
            self._edge_src = frame
        return self._edge_map

    def _mask(self, zone: Z.Zone, shape) -> np.ndarray:
        if zone.id not in self.masks:
            m = np.zeros(shape[:2], np.uint8)
            cv2.fillPoly(m, [zone.np_polygon], 255)
            self.masks[zone.id] = m
            self.zone_px[zone.id] = int((m > 0).sum())
        return self.masks[zone.id]

    def raw(self, frame: np.ndarray, zone: Z.Zone) -> float:
        """Edge-pixel fraction inside the zone."""
        mask = self._mask(zone, frame.shape)
        n_px = self.zone_px[zone.id] or 1

        edges = cv2.bitwise_and(self._edges(frame), mask)
        return float(cv2.countNonZero(edges)) / n_px

    def index(self, frame: np.ndarray, zone: Z.Zone) -> float:
        """Occupancy on a fixed 0-1 scale, smoothed over time.

        The bounds are absolute and shared by every zone (see config), so an
        index of 0.8 means the same amount of stuff on the ground whichever
        camera it comes from. Normalising per zone would make each zone its own
        yardstick and quietly destroy any cross-site comparison.
        """
        r = self.raw(frame, zone)
        lo, hi = cfg.OCCUPANCY_EDGE_CLEAR, cfg.OCCUPANCY_EDGE_PACKED
        raw_idx = float(np.clip((r - lo) / max(1e-6, hi - lo), 0.0, 1.0))

        prev = self._ema.get(zone.id)
        val = raw_idx if prev is None else (
            self.smooth_alpha * raw_idx + (1 - self.smooth_alpha) * prev
        )
        self._ema[zone.id] = val
        return val


# --------------------------------------------------------------------------
# Level of service
# --------------------------------------------------------------------------

def classify_los(density_pcu_per_100m2: float | None,
                 occupancy_index: float,
                 mode: str,
                 scale_verified: bool = False) -> tuple[str, float]:
    """Return (LOS label, congestion index 0-1).

    Density leads only where the camera has a verified metric scale. Without
    one, a PCU-per-100 m^2 figure carries an unknown multiplier, and bucketing
    it into LOS bands would dress that uncertainty up as a precise engineering
    verdict. Occupancy is measured on an absolute, scale-free scale shared by
    every camera, so it stays honest and stays comparable between sites.
    """
    if mode == "detection" and density_pcu_per_100m2 is not None and scale_verified:
        d = density_pcu_per_100m2
        t = cfg.LOS_THRESHOLDS
        if d < t["Free"]:
            los = "Free"
        elif d < t["Moderate"]:
            los = "Moderate"
        elif d < t["Heavy"]:
            los = "Heavy"
        else:
            los = "Jam"
        # Indexed against jam density, so 1.0 means "at a standstill" rather
        # than an arbitrary ceiling.
        ci = float(np.clip(d / cfg.JAM_DENSITY_PCU_PER_100M2, 0.0, 1.0))
        return los, ci

    o = float(np.clip(occupancy_index, 0.0, 1.0))
    if o < 0.25:
        los = "Free"
    elif o < 0.5:
        los = "Moderate"
    elif o < 0.75:
        los = "Heavy"
    else:
        los = "Jam"
    return los, o


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

class MetricsEngine:
    """Consumes FrameResults, emits one ZoneWindow per zone per time window."""

    def __init__(
        self,
        camera_id: str,
        calib: Z.CameraCalibration,
        window_s: float = 2.0,
        compute_structural: bool = True,
    ):
        self.camera_id = camera_id
        self.calib = calib
        self.window_s = window_s
        self.compute_structural = compute_structural
        self.scale_verified = cfg.scale_is_verified(camera_id)

        self.areas = {
            z.id: Z.zone_real_area(z, calib.scale_model) for z in calib.zones
        }
        self.acc = {
            z.id: ZoneAccumulator(z, self.areas[z.id]) for z in calib.zones
        }
        self.speed = SpeedTracker(calib.scale_model)
        self.structural = StructuralOccupancy(calib) if compute_structural else None

        self._window_start: float | None = None
        self._seen_tracks: dict[str, set[int]] = defaultdict(set)

    def process(self, fr: FrameResult) -> list[ZoneWindow]:
        """Feed one frame. Returns finished windows (usually empty)."""
        if self._window_start is None:
            self._window_start = fr.timestamp_s

        out: list[ZoneWindow] = []
        if fr.timestamp_s - self._window_start >= self.window_s:
            out = self._close_window(fr.timestamp_s)

        speeds = self.speed.update(fr)

        by_zone: dict[str, list[Detection]] = defaultdict(list)
        for d in fr.detections:
            z = self.calib.assign(d.anchor)
            if z is not None:
                by_zone[z.id].append(d)

        for zid, acc in self.acc.items():
            acc.add_frame(by_zone.get(zid, []), speeds)
            if self.structural is not None and fr.frame is not None:
                acc.structural_sum += self.structural.index(fr.frame, acc.zone)
                acc.structural_n += 1

        return out

    # A window shorter than this carries no usable rate: dividing a couple of
    # detections by a few milliseconds produces a per-hour figure that is
    # arithmetically enormous and physically meaningless.
    MIN_WINDOW_S = 0.25

    def flush(self, end_t: float) -> list[ZoneWindow]:
        """Close the final partial window, unless there is nothing in it.

        `flush` is normally called with the timestamp of the last processed
        frame. If a window happened to close on that same frame, the remaining
        window has zero length, and emitting it produced rows with
        window_end_s == window_start_s — three of them in the shipped dataset.
        """
        start = self._window_start
        if start is not None and (end_t - start) < self.MIN_WINDOW_S:
            return []
        return self._close_window(end_t)

    # -- internals --------------------------------------------------------

    def _close_window(self, now: float) -> list[ZoneWindow]:
        start = self._window_start if self._window_start is not None else 0.0
        duration = max(1e-6, now - start)
        results: list[ZoneWindow] = []

        for zid, acc in self.acc.items():
            if acc.frames == 0:
                continue
            zone = acc.zone
            area = self.areas[zid]

            n_mean = acc.det_count_sum / acc.frames
            pcu_mean = acc.pcu_sum / acc.frames
            density = (pcu_mean / area * 100.0) if area else None

            structural = (acc.structural_sum / acc.structural_n) if acc.structural_n else 0.0
            box_occ = (acc.box_area_sum / acc.frames / zone.pixel_area) if zone.pixel_area else 0.0
            overlap = float(np.mean(acc.overlap_samples)) if acc.overlap_samples else 0.0

            saturated = self._is_saturated(n_mean, area, overlap,
                                           structural, box_occ)
            mode = "occupancy" if saturated or zone.kind in ("pedestrian", "platform") else "detection"

            speeds = acc.speeds
            mean_speed = float(np.mean(speeds)) if speeds else None
            tracked = acc.stopped + acc.moving
            stopped_frac = (acc.stopped / tracked) if tracked else 0.0
            queue_pcu = (acc.stopped_pcu_sum / acc.frames) if acc.frames else 0.0

            new_tracks = acc.track_ids - self._seen_tracks[zid]
            self._seen_tracks[zid] |= acc.track_ids
            arrivals = len(new_tracks)

            # Flow from the fundamental relation q = k * v rather than by
            # counting new track IDs. ByteTrack re-issues IDs whenever a track
            # is briefly lost — common in dense mixed traffic — and counting
            # those as fresh arrivals inflates flow to physically impossible
            # values (tens of thousands of PCU/hr against a real saturation
            # flow near 1800). Density and speed are both measured directly,
            # so their product is the more trustworthy estimate.
            flow = self._flow_from_density_speed(zone, area, pcu_mean, mean_speed)

            los, ci = classify_los(density, structural, mode,
                                   scale_verified=self.scale_verified)

            results.append(ZoneWindow(
                camera_id=self.camera_id,
                zone_id=zid,
                zone_label=zone.label,
                zone_kind=zone.kind,
                window_start_s=round(start, 2),
                window_end_s=round(now, 2),
                n_detections_mean=round(n_mean, 2),
                counts_by_group={k: round(v / acc.frames, 2)
                                 for k, v in acc.group_sum.items()},
                unique_tracks=len(acc.track_ids),
                pcu_mean=round(pcu_mean, 2),
                density_pcu_per_100m2=round(density, 3) if density is not None else None,
                density_pcu_per_km_lane=(
                    round(cfg.pcu_per_100m2_to_per_km_lane(density), 1)
                    if density is not None else None
                ),
                real_area_m2=round(area, 1) if area else None,
                mean_speed_kmph=round(mean_speed, 1) if mean_speed is not None else None,
                stopped_fraction=round(stopped_frac, 3),
                queue_length_pcu=round(queue_pcu, 2),
                arrivals=arrivals,
                flow_pcu_per_hr=round(flow, 1),
                box_occupancy=round(box_occ, 4),
                structural_occupancy=round(structural, 4),
                saturated=saturated,
                measurement_mode=mode,
                scale_verified=self.scale_verified,
                los=los,
                congestion_index=round(ci, 3),
            ))
            acc.reset()

        self._window_start = now
        return results

    def _flow_from_density_speed(
        self, zone: Z.Zone, area: float | None,
        pcu_mean: float, mean_speed_kmph: float | None,
    ) -> float:
        """q [PCU/hr] = k [PCU/km] * v [km/h].

        Linear density k comes from the PCU standing in the zone divided by the
        zone's length along the road, which we recover from its area and the
        assumed lane width.
        """
        if not area or mean_speed_kmph is None or pcu_mean <= 0:
            return 0.0
        lane_width_m = 3.5 * max(1.0, zone.lanes_equivalent)
        length_km = (area / lane_width_m) / 1000.0
        if length_km <= 0:
            return 0.0
        k = pcu_mean / length_km
        return k * mean_speed_kmph

    def _is_saturated(
        self, n_mean: float, area: float | None, overlap: float,
        structural: float, box_occ: float,
    ) -> bool:
        """Whether counting can still be trusted in this zone.

        Two independent signatures, either of which is enough:

        1. Crowded but still resolvable — high object density together with
           heavy mutual overlap. Requiring both keeps busy-but-flowing lanes,
           where counting is fine, from being flagged.

        2. Full scene, empty count — texture says the zone is packed while the
           detector's boxes cover almost none of it. This is the collapse case,
           and only this test catches it: once detection fails there are too
           few boxes left to overlap, so signature 1 goes quiet exactly when
           the problem is worst.
        """
        collapsed = (structural > SATURATION_OCCUPANCY_MIN
                     and box_occ < SATURATION_BOX_COVER_MAX)
        if collapsed:
            return True

        if area:
            dense = (n_mean / area * 100.0) > SATURATION_DET_PER_100M2
            return dense and overlap > SATURATION_OVERLAP_RATIO
        return overlap > SATURATION_OVERLAP_RATIO


def los_color(los: str) -> tuple[int, int, int]:
    return cfg.LOS_COLORS.get(los, (200, 200, 200))
