"""Bottleneck detection and engineering recommendations.

Named `bottleneck_detect` rather than `bottleneck` on purpose: `bottleneck` is
a real PyPI package that pandas imports opportunistically to speed up its
nan-aware reductions. A local module of that name shadows it and makes pandas
fail on import for the whole project.


A bottleneck is not simply "a lot of vehicles" — a busy lane moving freely is
working as designed. What matters is the combination of high density with
*depressed speed*, sustained long enough to be a condition rather than a
passing platoon. Each detector below therefore looks for a pattern over a
window of time, not a single reading.

Recommendations are rule-based and templated, and each carries the evidence
that triggered it, so a reader can check the reasoning rather than trust it.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

import config as cfg


# Sustained conditions, in consecutive windows (2 s each by default).
MIN_SUSTAINED_WINDOWS = 3

SPEED_DROP_KMPH = 12.0          # below this counts as depressed on an arterial
HIGH_DENSITY_PCU_KM_LANE = 45.0  # ~75% of capacity density
STOPPED_FRACTION_ALERT = 0.35
OCCUPANCY_ALERT = 0.7
SPEED_DIFFERENTIAL_KMPH = 12.0   # between best and worst lane on an approach


@dataclass
class Alert:
    camera_id: str
    camera_name: str
    zone_id: str
    zone_label: str
    kind: str
    severity: str            # info | warning | critical
    start_s: float
    end_s: float
    duration_s: float
    headline: str
    evidence: str
    recommendation: str

    def to_dict(self) -> dict:
        return asdict(self)


def _est(rows: pd.DataFrame) -> str:
    """'~' unless every row behind a figure was measured on a verified scale.

    Speeds and PCU densities carry the camera's unknown pixel-to-metre factor
    until it is calibrated. Every other surface marks them '~'; alert evidence
    is quoted verbatim to operators, so it has to say the same thing.
    """
    if "scale_verified" in rows and len(rows) and bool(rows.scale_verified.all()):
        return ""
    return "~"


def _runs(mask: pd.Series, min_len: int) -> list[tuple[int, int]]:
    """Index ranges where `mask` is True for at least min_len in a row."""
    out, start = [], None
    vals = list(mask)
    for i, v in enumerate(vals):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i - 1))
            start = None
    if start is not None and len(vals) - start >= min_len:
        out.append((start, len(vals) - 1))
    return out


def detect(df: pd.DataFrame, camera_id: str) -> list[Alert]:
    """All alerts for one camera."""
    cam = cfg.camera_meta(camera_id)
    name = cam["name"]
    sub = df[df.camera_id == camera_id].copy()
    if sub.empty:
        return []

    alerts: list[Alert] = []
    for zone_id, z in sub.groupby("zone_id"):
        z = z.sort_values("window_start_s").reset_index(drop=True)
        label = z.zone_label.iloc[0]
        kind = z.zone_kind.iloc[0]
        mode = z.measurement_mode.iloc[-1] if "measurement_mode" in z else "detection"

        alerts += _congestion(z, camera_id, name, zone_id, label, mode)
        alerts += _queue(z, camera_id, name, zone_id, label)
        alerts += _saturation(z, camera_id, name, zone_id, label)
        if kind == "parking":
            alerts += _encroachment(z, camera_id, name, zone_id, label)

    alerts += _lane_imbalance(sub, camera_id, name)
    return alerts


def _congestion(z, camera_id, name, zone_id, label, mode) -> list[Alert]:
    """High density with suppressed speed, sustained."""
    if mode == "occupancy" or z.density_pcu_per_km_lane.isna().all():
        return []

    dense = z.density_pcu_per_km_lane.fillna(0) > HIGH_DENSITY_PCU_KM_LANE
    slow = z.mean_speed_kmph.fillna(99) < SPEED_DROP_KMPH
    mask = dense & slow

    out = []
    for i0, i1 in _runs(mask, MIN_SUSTAINED_WINDOWS):
        seg = z.iloc[i0:i1 + 1]
        dur = float(seg.window_end_s.iloc[-1] - seg.window_start_s.iloc[0])
        d = float(seg.density_pcu_per_km_lane.mean())
        v = float(seg.mean_speed_kmph.mean())
        est = _est(seg)
        sev = "critical" if dur > 20 and v < 8 else "warning"
        out.append(Alert(
            camera_id=camera_id, camera_name=name, zone_id=zone_id,
            zone_label=label, kind="congestion_bottleneck", severity=sev,
            start_s=float(seg.window_start_s.iloc[0]),
            end_s=float(seg.window_end_s.iloc[-1]), duration_s=dur,
            headline=f"Sustained bottleneck on {label}",
            evidence=(f"density {est}{d:.0f} PCU/km/lane with mean speed "
                      f"{est}{v:.1f} km/h held for {dur:.0f}s "
                      f"(threshold: >{HIGH_DENSITY_PCU_KM_LANE:.0f} and "
                      f"<{SPEED_DROP_KMPH:.0f} km/h)"),
            recommendation=(
                "Demand on this lane is exceeding what it can discharge. "
                "Extend green time for this approach in the adaptive plan; if "
                "the pattern repeats daily at the same clock time, it is a "
                "capacity problem rather than a timing one and warrants a lane "
                "or geometry review."),
        ))
    return out


def _queue(z, camera_id, name, zone_id, label) -> list[Alert]:
    mask = z.stopped_fraction.fillna(0) > STOPPED_FRACTION_ALERT
    out = []
    for i0, i1 in _runs(mask, MIN_SUSTAINED_WINDOWS):
        seg = z.iloc[i0:i1 + 1]
        dur = float(seg.window_end_s.iloc[-1] - seg.window_start_s.iloc[0])
        sf = float(seg.stopped_fraction.mean())
        q = float(seg.queue_length_pcu.mean())
        out.append(Alert(
            camera_id=camera_id, camera_name=name, zone_id=zone_id,
            zone_label=label, kind="standing_queue",
            severity="critical" if sf > 0.6 else "warning",
            start_s=float(seg.window_start_s.iloc[0]),
            end_s=float(seg.window_end_s.iloc[-1]), duration_s=dur,
            headline=f"Standing queue on {label}",
            evidence=(f"{sf * 100:.0f}% of tracked vehicles stationary, "
                      f"~{q:.1f} PCU queued, for {dur:.0f}s"),
            recommendation=(
                "Queue is not clearing within the cycle. Check for a "
                "downstream obstruction before retiming - if the blockage is "
                "downstream, adding green here just moves the queue."),
        ))
    return out


def _saturation(z, camera_id, name, zone_id, label) -> list[Alert]:
    """Scene so dense that counting stops being reliable."""
    if "saturated" not in z:
        return []
    mask = z.saturated.fillna(False).astype(bool)
    out = []
    for i0, i1 in _runs(mask, MIN_SUSTAINED_WINDOWS):
        seg = z.iloc[i0:i1 + 1]
        dur = float(seg.window_end_s.iloc[-1] - seg.window_start_s.iloc[0])
        occ = float(seg.structural_occupancy.mean())
        out.append(Alert(
            camera_id=camera_id, camera_name=name, zone_id=zone_id,
            zone_label=label, kind="saturated_gridlock",
            severity="critical" if occ > OCCUPANCY_ALERT else "warning",
            start_s=float(seg.window_start_s.iloc[0]),
            end_s=float(seg.window_end_s.iloc[-1]), duration_s=dur,
            headline=f"Saturated conditions in {label}",
            evidence=(f"occupancy index {occ:.2f} for {dur:.0f}s; vehicles no "
                      f"longer individually separable, so counts under-report "
                      f"and occupancy is the governing measure here"),
            recommendation=(
                "Movement space is effectively full. Signal timing has no "
                "leverage on this. Options are demand management (access "
                "restriction at peak), reclaiming carriageway width from "
                "encroachment, or providing off-street parking."),
        ))
    return out


def _encroachment(z, camera_id, name, zone_id, label) -> list[Alert]:
    """A parking zone persistently occupied is lost carriageway width."""
    occupied = (z.n_detections_mean.fillna(0) > 1.0)
    frac = float(occupied.mean())
    if frac < 0.5 or len(z) < MIN_SUSTAINED_WINDOWS:
        return []

    mean_n = float(z.n_detections_mean.mean())
    dur = float(z.window_end_s.iloc[-1] - z.window_start_s.iloc[0])
    return [Alert(
        camera_id=camera_id, camera_name=name, zone_id=zone_id,
        zone_label=label, kind="encroachment",
        severity="warning" if frac > 0.8 else "info",
        start_s=float(z.window_start_s.iloc[0]),
        end_s=float(z.window_end_s.iloc[-1]), duration_s=dur,
        headline=f"Persistent encroachment: {label}",
        evidence=(f"occupied in {frac * 100:.0f}% of observed windows, "
                  f"averaging {mean_n:.1f} stationary objects"),
        recommendation=(
            "Kerb-side space is being used as parking, narrowing the running "
            "carriageway and pushing traffic into the adjacent lane. Enforce "
            "the no-parking line or formalise the bay and re-mark the lane."),
    )]


def _lane_imbalance(sub, camera_id, name) -> list[Alert]:
    """Large speed spread between lanes on the same approach.

    Uneven lane use is a signature of a localised obstruction rather than
    genuine over-demand, and it points at a cheap fix.
    """
    lanes = sub[sub.zone_kind == "lane"]
    if lanes.zone_id.nunique() < 2:
        return []

    per = lanes.groupby(["zone_id", "zone_label"]).agg(
        speed=("mean_speed_kmph", "mean"),
        density=("density_pcu_per_km_lane", "mean"),
    ).reset_index().dropna(subset=["speed"])
    if len(per) < 2:
        return []

    fastest = per.loc[per.speed.idxmax()]
    slowest = per.loc[per.speed.idxmin()]
    diff = float(fastest.speed - slowest.speed)
    if diff < SPEED_DIFFERENTIAL_KMPH:
        return []

    t0 = float(lanes.window_start_s.min())
    t1 = float(lanes.window_end_s.max())
    est = _est(lanes)
    return [Alert(
        camera_id=camera_id, camera_name=name,
        zone_id=str(slowest.zone_id), zone_label=str(slowest.zone_label),
        kind="lane_imbalance", severity="info",
        start_s=t0, end_s=t1, duration_s=t1 - t0,
        headline=f"Uneven lane performance on {name}",
        evidence=(f"{slowest.zone_label} averages {est}{slowest.speed:.1f} km/h "
                  f"against {est}{fastest.speed:.1f} km/h on "
                  f"{fastest.zone_label} - a {est}{diff:.1f} km/h spread"),
        recommendation=(
            "The approach is not being used evenly, which usually means a "
            "local obstruction rather than a shortage of capacity. Clear the "
            "kerb lane and improve lane-discipline marking before considering "
            "any widening."),
    )]


def detect_all(df: pd.DataFrame) -> list[Alert]:
    out: list[Alert] = []
    for cam in df.camera_id.unique():
        out += detect(df, cam)
    order = {"critical": 0, "warning": 1, "info": 2}
    out.sort(key=lambda a: (order.get(a.severity, 3), -a.duration_s))
    return out


def to_frame(alerts: list[Alert]) -> pd.DataFrame:
    return pd.DataFrame([a.to_dict() for a in alerts]) if alerts else pd.DataFrame()
