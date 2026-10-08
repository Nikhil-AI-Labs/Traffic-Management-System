"""Data layer shared by every front end: the React dashboard's JSON API, the
Streamlit dashboard, and the verification suite.

Everything here was previously defined inside `dashboard.py`, which meant the
only way to load the metrics, build a zone overlay or reach the FLUID tables
was to import Streamlit and run `st.set_page_config` as a side effect. The
tests did exactly that. Pulling the data functions out gives every caller the
same numbers from the same code, with no UI framework in the import graph.

Caching is keyed on the source files' modification times rather than held for
the life of the process, so a re-measured camera, a newly kept upload or an
applied calibration shows up on the next request without a restart and without
anyone having to remember to clear a cache.
"""

from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

import config as cfg

cfg.quiet_opencv()
import theme as T
import zones as Z
from reference_frames import REF_DIR

HEATMAP_DIR = cfg.FRAME_DIR / "heatmap"

_lock = threading.Lock()


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def _metric_paths(include_uploads: bool) -> list[Path]:
    paths = [cfg.LOG_DIR / f"{c}_metrics.csv" for c in cfg.CAMERAS]
    if include_uploads:
        import ingest
        paths += [ingest.persisted_metrics_path(s)
                  for s in ingest.persisted_sessions()]
    return [p for p in paths if p.exists()]


def _signature(paths: list[Path]) -> tuple:
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(out)


_metrics_cache: dict[bool, tuple[tuple, pd.DataFrame]] = {}


def load_metrics(include_uploads: bool = True) -> pd.DataFrame:
    """Every camera's measurement windows.

    Named rather than globbed on purpose: `outputs/logs/` also holds
    `all_metrics.csv`, the concatenation of the same rows, and a glob over
    `*_metrics.csv` silently loads every window twice — which inflates alert
    and forecast counts without producing a single obviously wrong number.

    `include_uploads` adds clips an operator chose to keep alongside the
    dataset. The verification suite turns it off: the figures it checks
    against REPORT.md are a property of the seven dataset cameras, and must
    not move because somebody kept a clip on their own machine.

    Windows are validated on the way in. `metrics.py` refuses to emit a
    zero-length window, but CSVs written before that guard existed still hold
    a few, and a window with `end == start` gives a per-hour rate with a zero
    denominator.

    The returned frame is shared between callers; treat it as read-only.
    """
    paths = _metric_paths(include_uploads)
    sig = _signature(paths)
    with _lock:
        hit = _metrics_cache.get(include_uploads)
        if hit is not None and hit[0] == sig:
            return hit[1]

    frames = [pd.read_csv(p) for p in paths]
    if frames:
        df = pd.concat(frames, ignore_index=True)
        if {"window_start_s", "window_end_s"} <= set(df.columns):
            df = df[df.window_end_s > df.window_start_s].reset_index(drop=True)
    else:
        df = pd.DataFrame()

    with _lock:
        _metrics_cache[include_uploads] = (sig, df)
    return df


def analysed_cameras(df: pd.DataFrame) -> list[str]:
    """Cameras with metrics, dataset cameras first in registry order."""
    if df.empty:
        return []
    present = list(dict.fromkeys(df.camera_id.astype(str)))
    ordered = [c for c in cfg.CAMERAS if c in present]
    return ordered + [c for c in present if c not in cfg.CAMERAS]


def junction_ids(df: pd.DataFrame) -> list[str]:
    """Junction-mode dataset cameras that have been analysed."""
    have = set(df.camera_id.unique()) if not df.empty else set()
    return [c for c in cfg.junction_cameras() if c in have]


def zone_state(df: pd.DataFrame, camera_id: str) -> pd.DataFrame:
    """Latest window per zone, joined to that zone's clip averages."""
    sub = df[df.camera_id == camera_id]
    if sub.empty:
        return pd.DataFrame()
    latest = sub.sort_values("window_start_s").groupby("zone_id").tail(1)
    means = sub.groupby("zone_id").agg(
        avg_congestion=("congestion_index", "mean"),
        avg_occupancy=("structural_occupancy", "mean"),
        avg_speed=("mean_speed_kmph", "mean"),
        avg_density=("density_pcu_per_km_lane", "mean"),
        avg_flow=("flow_pcu_per_hr", "mean"),
        peak_congestion=("congestion_index", "max"),
        sat_windows=("saturated", "sum"),
        windows=("congestion_index", "size"),
    )
    return latest.merge(means, on="zone_id", how="left")


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

@lru_cache(maxsize=32)
def _read_rgb(path_str: str, mtime_ns: int) -> np.ndarray | None:
    img = cv2.imread(path_str)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if img is not None else None


def load_image(path: Path | str) -> np.ndarray | None:
    """An RGB image from disk, cached until the file changes."""
    p = Path(path)
    try:
        mtime = p.stat().st_mtime_ns
    except OSError:
        return None
    return _read_rgb(str(p), mtime)


def reference_image(camera_id: str) -> np.ndarray | None:
    return load_image(REF_DIR / f"{camera_id}.jpg")


def heatmap_image(camera_id: str) -> np.ndarray | None:
    return load_image(HEATMAP_DIR / f"{camera_id}_heatmap.jpg")


def _bgr(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)


def overlay(camera_id: str, state: pd.DataFrame) -> np.ndarray | None:
    """Reference frame with zones shaded by their average level of service.

    Shaded from the same status palette the charts use, so a zone that reads
    'Heavy' on the image reads 'Heavy' in every band and legend beside it.
    """
    img = reference_image(camera_id)
    calib = Z.load(camera_id)
    if img is None or calib is None:
        return None
    highlight = {}
    if not state.empty:
        highlight = {r["zone_id"]: _bgr(T.los_color(T.los_of(
            float(r.get("avg_congestion", 0) or 0)))) for _, r in state.iterrows()}
    out = Z.draw_zones(cv2.cvtColor(img, cv2.COLOR_RGB2BGR), calib,
                       alpha=0.34, show_labels=True, highlight=highlight)
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)


def encode_jpeg(rgb: np.ndarray, quality: int = 85,
                max_width: int | None = None) -> bytes:
    """RGB array to JPEG bytes, optionally downscaled for the browser."""
    img = rgb
    if max_width and img.shape[1] > max_width:
        h = int(round(img.shape[0] * max_width / img.shape[1]))
        img = cv2.resize(img, (max_width, h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buf.tobytes()


# --------------------------------------------------------------------------
# Detector
# --------------------------------------------------------------------------

# One model for the whole process — loading it costs seconds — and one user of
# it at a time. ByteTrack's state lives on the model, so two analyses
# interleaving their frames on one instance would hand each other track IDs.
DETECTOR_LOCK = threading.RLock()
_detector = None


def detector_ready() -> tuple[bool, str]:
    """Are the model weights on disk? Returns (ok, expected path)."""
    p = Path(cfg.DEFAULT_MODEL)
    return (True, "") if p.exists() else (False, str(p))


def get_detector():
    global _detector
    with DETECTOR_LOCK:
        if _detector is None:
            from detector import VehicleDetector
            _detector = VehicleDetector()
        return _detector


# Live monitoring needs frames per second more than it needs bit-for-bit
# agreement with the committed batch run, so it prefers an OpenVINO export of
# the same weights when one has been made (scripts/export_openvino.py, which
# also benchmarks the two on the machine it runs on). Batch analysis,
# re-measuring and uploads keep the PyTorch weights, which produced every
# committed figure.
OPENVINO_MODEL = Path(cfg.DEFAULT_MODEL).with_name(
    Path(cfg.DEFAULT_MODEL).stem + "_openvino_model")
_live_detector = None


def _openvino_ready() -> bool:
    if not (OPENVINO_MODEL / "metadata.yaml").exists():
        return False
    try:
        import importlib.util
        return importlib.util.find_spec("openvino") is not None
    except Exception:
        return False


def openvino_imgsz():
    """The input shape the OpenVINO export was made for, [h, w]. A static
    export must be fed exactly that shape, not the square default."""
    try:
        import yaml
        with open(OPENVINO_MODEL / "metadata.yaml", "r", encoding="utf-8") as f:
            sz = yaml.safe_load(f).get("imgsz")
        if isinstance(sz, (list, tuple)) and len(sz) == 2:
            return [int(sz[0]), int(sz[1])]
    except Exception:
        pass
    return cfg.DETECT_IMGSZ


def openvino_device() -> str:
    """Where OpenVINO should run: the Intel integrated GPU when there is one.

    Measured on the development laptop (i7-12700H, Iris Xe; 40 frames of
    Rokadiya Hanuman via scripts/export_openvino.py): PyTorch on the CPU
    6.3 fps, OpenVINO on the CPU 5.0 fps, OpenVINO on the integrated GPU
    25.4 fps, with detection counts within 0.7 % of PyTorch's. The GPU is
    what makes live detection keep up with a 20 fps camera.
    STI_OPENVINO_DEVICE overrides (e.g. "cpu").
    """
    forced = os.environ.get("STI_OPENVINO_DEVICE", "").strip()
    if forced:
        return forced
    try:
        import openvino as ov
        if any(d.startswith("GPU") for d in ov.Core().available_devices):
            return "intel:gpu"
    except Exception:
        pass
    return "cpu"


def _ov_label(device: str) -> str:
    return f"YOLO11-S · OpenVINO {'GPU' if 'gpu' in device.lower() else 'CPU'}"


def live_detector_label() -> str | None:
    """What the live monitor will run, for display; None if it cannot run."""
    if _live_detector is not None:
        return getattr(_live_detector, "label", None)
    if _openvino_ready():
        return _ov_label(openvino_device())
    return "YOLO11-S · PyTorch" if detector_ready()[0] else None


def get_live_detector():
    global _live_detector
    with DETECTOR_LOCK:
        if _live_detector is None:
            if _openvino_ready():
                from detector import VehicleDetector
                # One inference now compiles the graph for the device, so a
                # missing GPU driver fails here - and falls back to the CPU -
                # rather than in the middle of a live session.
                blank = np.zeros((1080, 1920, 3), np.uint8)
                for device in dict.fromkeys([openvino_device(), "cpu"]):
                    try:
                        det = VehicleDetector(model_path=OPENVINO_MODEL, device=device,
                                              imgsz=openvino_imgsz())
                        det.track_frame(blank)
                        det.reset_tracker()
                        det.label = _ov_label(device)
                        _live_detector = det
                        break
                    except Exception:
                        import traceback
                        traceback.print_exc()
            if _live_detector is None:
                _live_detector = get_detector()
                _live_detector.label = "YOLO11-S · PyTorch"
        return _live_detector


# --------------------------------------------------------------------------
# Signal timing (Webster's, Surat)
# --------------------------------------------------------------------------

def junction_demands(df: pd.DataFrame):
    import signal_opt as so
    return [d for d in (so.demand_from_metrics(df, c) for c in junction_ids(df))
            if d is not None]


SAT_SOURCES = {
    "irc": ("IRC:SP:41 (525·W)", "IRC_SATURATION_FACTOR"),
    "field_low": ("Field low (636·W)", "FIELD_SATURATION_FACTOR_LOW"),
    "mid": ("Mid (900·W)", None),
    "field_high": ("Field high (1283·W)", "FIELD_SATURATION_FACTOR_HIGH"),
    "per_lane": ("Per-lane 1800 (default)", None),
}


def saturation_factor(source: str) -> float | None:
    """PCU/hr per metre for a width-based source; None for per-lane 1800."""
    import signal_opt as so
    if source == "mid":
        return 900.0
    attr = SAT_SOURCES.get(source, (None, None))[1]
    return float(getattr(so, attr)) if attr else None


def with_widths(demands, factor: float | None, widths: dict[str, float]):
    """Rebuild demands with operator-supplied approach widths."""
    import signal_opt as so
    if factor is None:
        return demands
    out = []
    for d in demands:
        base = {k: v for k, v in d.__dict__.items()
                if k not in ("approach_width_m", "saturation_factor",
                             "saturation_flow_per_lane")}
        w = float(widths.get(d.approach_id, float(d.lanes) * 3.5))
        out.append(so.ApproachDemand(**base, approach_width_m=w,
                                     saturation_factor=factor))
    return out


def delay_sweep(demands, delay_model: str = "auto") -> pd.DataFrame:
    """Mean delay at every cycle length 60-180 s, equal and proportional splits."""
    import signal_opt as so
    Y = sum(d.flow_ratio for d in demands)
    L = len(demands) * so.LOST_TIME_PER_PHASE_S
    rows = []
    for c in range(60, 185, 5):
        eff = c - L
        splits = ({d.approach_id: eff * d.flow_ratio / Y for d in demands}
                  if Y > 0 else None)
        rows.append({
            "Cycle (s)": c,
            "Equal splits": so.evaluate_fixed_plan(
                demands, cycle_s=float(c),
                delay_model=delay_model).mean_delay_s_per_pcu,
            "Demand-proportional splits": so.evaluate_fixed_plan(
                demands, cycle_s=float(c), green_splits=splits,
                delay_model=delay_model).mean_delay_s_per_pcu,
        })
    return pd.DataFrame(rows).set_index("Cycle (s)")


# --------------------------------------------------------------------------
# Max-pressure on Surat — the observability gate
# --------------------------------------------------------------------------

def mp_movements_from_metrics(df: pd.DataFrame, junctions: list[str]):
    """Build max-pressure movements from what the cameras actually measured.

    Each junction camera gives us the queue on its approach. None of them sees
    the link that approach discharges into, so `queue_out_pcu` stays None and
    the controller reports itself as unobservable rather than inventing a zero.
    """
    import maxpressure as MP
    rows = []
    for cam in junctions:
        sub = df[(df.camera_id == cam) & (df.zone_kind == "lane")]
        if sub.empty:
            continue
        latest_t = sub.window_start_s.max()
        recent = sub[sub.window_start_s >= latest_t - 20.0]
        q = float(recent.groupby("zone_id").queue_length_pcu.mean().sum())
        rows.append({
            "id": cam,
            "label": cfg.camera_name(cam),
            "from_link": f"{cam}_approach",
            "to_link": None,                     # no camera sees it
            "queue_in_pcu": q,
            "queue_out_pcu": None,               # deliberately absent
            "queue_in_source": f"{cfg.camera_name(cam)} — measured",
            "queue_out_source": "no downstream camera configured",
        })
    return MP.movements_from_zones(rows)


# --------------------------------------------------------------------------
# FLUID
# --------------------------------------------------------------------------

@lru_cache(maxsize=16)
def fluid_tables(recording_id: str):
    """Queues, signal and turn ratios for one FLUID recording, or None."""
    import fluid as FL
    rec = FL.find(recording_id)
    if rec is None:
        return None
    return (FL.build_queue_table(rec), FL.load_signal(rec), FL.turn_ratios(rec))


@lru_cache(maxsize=1)
def fluid_summary() -> pd.DataFrame:
    """Headline validation across every site: one row per intersection.

    Replays three full recordings second by second; deterministic, so it is
    computed once per process.
    """
    import fluid as FL
    rows = []
    for site in FL.SITES:
        recs = FL.recordings(site)
        if not recs:
            continue
        r = recs[0]
        q, sg, tr = fluid_tables(r.id)
        res = FL.replay(r, q, sg, tr)
        rows.append({
            "site": site,
            "label": FL.SITE_LABELS.get(site, site),
            "cycle_s": FL.cycle_length_s(sg),
            "phases": len(FL.controller_phases(sg, q)),
            "steps": res.steps,
            "mode": res.mode,
            "wasted_actual": res.wasted_green_pct_actual,
            "wasted_mp": res.wasted_green_pct_maxpressure,
            "agreement": res.agreement_pct,
            "press_actual": res.mean_pressure_actual,
            "press_mp": res.mean_pressure_maxpressure,
        })
    return pd.DataFrame(rows)


@lru_cache(maxsize=16)
def fluid_replay(recording_id: str, min_green_s: float, downstream: str):
    import fluid as FL
    rec = FL.find(recording_id)
    tables = fluid_tables(recording_id)
    if rec is None or tables is None:
        return None
    q, sg, tr = tables
    return FL.replay(rec, q, sg, tr, min_green_s=float(min_green_s),
                     downstream=downstream)


@lru_cache(maxsize=16)
def fluid_mix(recording_id: str) -> pd.DataFrame:
    """Vehicle-class share for one FLUID recording, by distinct vehicle."""
    import fluid as FL
    rec = FL.find(recording_id)
    if rec is None:
        return pd.DataFrame()
    d = pd.read_csv(rec.traj_path, usecols=["id", "type"]).dropna()
    d = d[d.type != "pedestrian"].drop_duplicates("id")
    if d.empty:
        return pd.DataFrame()
    c = d.type.value_counts()
    return pd.DataFrame({
        "class": c.index,
        "count": c.values,
        "share": c.values / c.values.sum() * 100.0,
        "pcu": [FL.FLUID_PCU.get(k, 1.0) for k in c.index],
    })


@lru_cache(maxsize=1)
def fluid_totals() -> dict:
    """Recording count, intersections, minutes and distinct vehicles.

    The Streamlit sidebar used to print these as constants; computed here so
    they cannot drift from the dataset actually on disk.
    """
    import fluid as FL
    if not FL.available():
        return {"recordings": 0, "intersections": 0, "minutes": 0.0,
                "vehicles": 0}
    recs = FL.recordings()
    minutes = 0.0
    vehicles = 0
    for r in recs:
        # Duration from the signal file (a few KB), not from the queue table:
        # building and caching queue tables for all fourteen recordings just
        # to print a sidebar total held hundreds of MB for the server's life.
        try:
            sg = pd.read_csv(r.signal_path, usecols=["begin_time", "end_time"])
            minutes += float(sg.end_time.max() - sg.begin_time.min()) / 60.0
        except Exception:
            pass
        # Distinct vehicles, read in chunks so a 145 MB trajectory file is
        # never fully in memory.
        try:
            ids: set = set()
            for chunk in pd.read_csv(r.traj_path, usecols=["id", "type"],
                                     chunksize=500_000):
                ids.update(chunk.loc[chunk.type != "pedestrian", "id"].unique())
            vehicles += len(ids)
        except Exception:
            pass
    return {"recordings": len(recs),
            "intersections": len({r.site for r in recs}),
            "minutes": round(minutes, 1),
            "vehicles": vehicles}
