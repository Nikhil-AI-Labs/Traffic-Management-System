"""Short-horizon congestion forecasting and trend analysis.

The dataset gives a few minutes per camera, not the weeks of history a learned
model would need. So this stays deliberately simple: a damped linear trend over
a recent window, with a prediction interval derived from the residuals.

The interval is the point of it. A single forecast number from two minutes of
footage would imply a confidence that this data cannot support; showing the
band makes the uncertainty visible instead of hiding it. Where the trend is
weaker than the noise, `reliable` comes back False and the caller should say
"insufficient history" rather than print a number.

Given continuous operation, the same interface would be backed by a seasonal
model over time-of-day and day-of-week - noted as the upgrade path rather than
pretended at here.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


# With 2 s windows this is ~40 s of history — enough to see a platoon build
# without chasing single-window noise.
DEFAULT_HISTORY_WINDOWS = 20
DEFAULT_HORIZON_S = 60.0

# Trend must beat this share of the series' own noise to count as real.
MIN_TREND_TO_NOISE = 0.8
# Real traffic trends flatten; unchecked extrapolation is how you forecast
# negative congestion or a jam that never arrives.
TREND_DAMPING = 0.6


@dataclass
class Forecast:
    camera_id: str
    zone_id: str
    zone_label: str
    metric: str
    current: float
    predicted: float
    lower: float
    upper: float
    horizon_s: float
    slope_per_s: float
    direction: str        # rising | falling | steady
    reliable: bool
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _fit(y: np.ndarray, t: np.ndarray) -> tuple[float, float, float]:
    """Least-squares line. Returns (slope, intercept, residual std)."""
    if len(y) < 3:
        return 0.0, float(y[-1]) if len(y) else 0.0, 0.0
    A = np.vstack([t, np.ones_like(t)]).T
    (slope, intercept), *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - (slope * t + intercept)
    return float(slope), float(intercept), float(np.std(resid))


def forecast_zone(
    z: pd.DataFrame,
    camera_id: str,
    metric: str = "congestion_index",
    horizon_s: float = DEFAULT_HORIZON_S,
    history: int = DEFAULT_HISTORY_WINDOWS,
) -> Forecast | None:
    """Project one zone's metric forward."""
    z = z.sort_values("window_start_s")
    series = z[metric].astype(float).dropna()
    if len(series) < 5:
        return None

    tail = z.tail(history)
    # .ffill(), not fillna(method="ffill") — the latter was removed in pandas 3.
    y = tail[metric].astype(float).ffill().to_numpy()
    t = tail["window_start_s"].astype(float).to_numpy()
    if len(y) < 5 or np.allclose(t, t[0]):
        return None

    t0 = t - t[0]
    slope, intercept, resid_std = _fit(y, t0)
    current = float(y[-1])

    # Damped extrapolation.
    raw_delta = slope * horizon_s
    predicted = current + raw_delta * TREND_DAMPING

    spread = float(np.std(y))
    trend_magnitude = abs(raw_delta)
    reliable = bool(
        len(y) >= 8
        and spread > 1e-6
        and trend_magnitude > MIN_TREND_TO_NOISE * max(resid_std, 1e-6)
    )

    # Interval widens with horizon: uncertainty compounds the further out we go.
    band = max(resid_std, spread * 0.5) * (1.0 + horizon_s / 60.0)
    lower, upper = predicted - band, predicted + band

    if metric in ("congestion_index", "structural_occupancy"):
        predicted = float(np.clip(predicted, 0.0, 1.0))
        lower = float(np.clip(lower, 0.0, 1.0))
        upper = float(np.clip(upper, 0.0, 1.0))
    else:
        predicted = max(0.0, predicted)
        lower = max(0.0, lower)
        upper = max(0.0, upper)

    if not reliable:
        direction = "steady"
    elif raw_delta > 0:
        direction = "rising"
    elif raw_delta < 0:
        direction = "falling"
    else:
        direction = "steady"

    note = "" if reliable else (
        "Trend is within the noise of this series - treat as steady. "
        "A few minutes of footage cannot support a directional call."
    )

    return Forecast(
        camera_id=camera_id,
        zone_id=str(z.zone_id.iloc[0]),
        zone_label=str(z.zone_label.iloc[0]),
        metric=metric,
        current=round(current, 3),
        predicted=round(predicted, 3),
        lower=round(lower, 3),
        upper=round(upper, 3),
        horizon_s=horizon_s,
        slope_per_s=round(slope, 5),
        direction=direction,
        reliable=reliable,
        note=note,
    )


def forecast_all(
    df: pd.DataFrame,
    metric: str = "congestion_index",
    horizon_s: float = DEFAULT_HORIZON_S,
) -> list[Forecast]:
    out: list[Forecast] = []
    for (cam, _zone), z in df.groupby(["camera_id", "zone_id"]):
        f = forecast_zone(z, cam, metric=metric, horizon_s=horizon_s)
        if f is not None:
            out.append(f)
    out.sort(key=lambda f: (-f.predicted, f.camera_id))
    return out


def rising_risks(forecasts: list[Forecast], threshold: float = 0.5) -> list[Forecast]:
    """Zones credibly trending towards congestion."""
    return [f for f in forecasts
            if f.reliable and f.direction == "rising" and f.predicted >= threshold]


def to_frame(forecasts: list[Forecast]) -> pd.DataFrame:
    return pd.DataFrame([f.to_dict() for f in forecasts]) if forecasts else pd.DataFrame()


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

def time_profile(df: pd.DataFrame, camera_id: str, bin_s: float = 10.0) -> pd.DataFrame:
    """Congestion profile over the clip, binned. Feeds the dashboard charts."""
    sub = df[df.camera_id == camera_id].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["bin"] = (sub.window_start_s // bin_s) * bin_s
    return sub.groupby(["bin", "zone_label"]).agg(
        congestion_index=("congestion_index", "mean"),
        density_pcu_per_km_lane=("density_pcu_per_km_lane", "mean"),
        speed_kmph=("mean_speed_kmph", "mean"),
        flow_pcu_per_hr=("flow_pcu_per_hr", "mean"),
        occupancy=("structural_occupancy", "mean"),
    ).reset_index()


def camera_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per camera: the network overview table."""
    rows = []
    for cam, sub in df.groupby("camera_id"):
        lanes = sub[sub.zone_kind == "lane"]
        worst = sub.loc[sub.congestion_index.idxmax()] if len(sub) else None
        rows.append({
            "camera_id": cam,
            "zones": sub.zone_id.nunique(),
            "mean_congestion": round(float(sub.congestion_index.mean()), 3),
            "peak_congestion": round(float(sub.congestion_index.max()), 3),
            "worst_zone": str(worst.zone_label) if worst is not None else "",
            "mean_speed_kmph": (round(float(lanes.mean_speed_kmph.mean()), 1)
                                if len(lanes) and lanes.mean_speed_kmph.notna().any()
                                else None),
            "saturated_windows": int(sub.saturated.sum()) if "saturated" in sub else 0,
            "total_windows": int(len(sub)),
        })
    return pd.DataFrame(rows).sort_values("mean_congestion", ascending=False)
