"""JSON API behind the React dashboard.

    python src/api.py                 # http://127.0.0.1:8000
    uvicorn api:app --app-dir src     # the same, under any ASGI server

Every number on the dashboard is computed here, from the same modules the
Streamlit dashboard and the verification suite use, so the React front end
holds no traffic logic at all - it lays out what this returns. Charts travel as
Plotly figure JSON built by `charts.py` in the viewer's light or dark mode, so
the validated chart specs are not re-implemented in a second language.

Long work - re-measuring a camera, finding the road in an upload, analysing a
clip - runs in a background thread and is polled through `/api/jobs/{id}`.
The detector is a single shared model guarded by a lock: ByteTrack keeps its
state on the model, so two analyses may not interleave on it.

When `web/dist` exists (after `npm run build` in `web/`) the built front end is
served from `/` as well, so one process is the whole application.
"""

from __future__ import annotations

import json
import math
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable

import anyio
import numpy as np
import pandas as pd
from fastapi import (Body, Depends, FastAPI, File, HTTPException, Query, Request,
                     UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

import config as cfg

cfg.quiet_opencv()
import bottleneck_detect as bn
import charts as C
import forecast as fc
import incidents as INC
import security as SEC
import services as S
import signal_opt as so
import theme as T
import whatif as W
import zones as Z
from security import limit, operator

WEB_DIST = cfg.ROOT / "web" / "dist"
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024        # 1 GB, as the old uploader allowed


# --------------------------------------------------------------------------
# Warm-up
# --------------------------------------------------------------------------

_WARM = {"fluid_summary": False, "fluid_totals": False}


def _warm():
    """Pay the slow first computations before anyone is waiting on them."""
    try:
        S.load_metrics()
        import fluid as FL
        if FL.available():
            S.fluid_summary()
            _WARM["fluid_summary"] = True
            S.fluid_totals()
            _WARM["fluid_totals"] = True
    except Exception:
        traceback.print_exc()


@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=_warm, daemon=True, name="warm-up").start()
    yield


app = FastAPI(title="Surat Traffic Intelligence API", version="2.1.0",
              docs_url="/api/docs", openapi_url="/api/openapi.json",
              lifespan=lifespan)
# Streams must not be compressed: gzip holds output back until it has enough
# to emit, which turns a live video feed into a stutter. Starlette already
# skips event streams and images; the MJPEG multipart type is added here.
try:
    from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES as _NO_GZIP
    app.add_middleware(GZipMiddleware, minimum_size=1024,
                       exclude_content_types=(*_NO_GZIP, "multipart/x-mixed-replace"))
except ImportError:                      # Starlette < 0.40: no exclusion list
    app.add_middleware(GZipMiddleware, minimum_size=1024)
# The Vite dev server proxies /api, so this only matters when the front end is
# served from a different origin during development.
app.add_middleware(CORSMiddleware,
                   allow_origins=["http://localhost:5173",
                                  "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Vehicles", "X-Queued", "Retry-After"])


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

def clean(o: Any) -> Any:
    """Make anything the analysis modules return safe for strict JSON.

    NaN and infinity become null - Webster's delay is genuinely infinite above
    capacity, and the front end shows that as "—" with an explanation rather
    than as a number. numpy scalars become Python ones.
    """
    if o is None or isinstance(o, (bool, str)):
        return o
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, pd.DataFrame):
        return records(o)
    if isinstance(o, pd.Series):
        return clean(o.tolist())
    if is_dataclass(o):
        return clean(asdict(o))
    if isinstance(o, Path):
        return str(o)
    return o


def records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    return clean(df.astype(object).where(pd.notna(df), None).to_dict("records"))


def figure(fig) -> dict:
    """A Plotly figure as plain JSON, for react-plotly on the other side."""
    return json.loads(fig.to_json())


def ok(payload: dict) -> JSONResponse:
    return JSONResponse(clean(payload))


def jpeg(rgb: np.ndarray | None, max_width: int = 1600,
         headers: dict | None = None) -> Response:
    if rgb is None:
        raise HTTPException(404, "Image not available.")
    return Response(S.encode_jpeg(rgb, quality=84, max_width=max_width),
                    media_type="image/jpeg",
                    headers={"Cache-Control": "no-cache", **(headers or {})})


def short(cam: str) -> str:
    return C.short_name(cam)


def need_metrics() -> pd.DataFrame:
    df = S.load_metrics()
    if df.empty:
        raise HTTPException(
            409, "No metrics have been generated yet. Run "
                 "`python src/analyze.py --all`, or analyse a clip from the "
                 "Analyse footage view.")
    return df


def need_camera(cam: str) -> dict:
    if cam not in cfg.CAMERAS and cam not in cfg.SESSION_CAMERAS:
        raise HTTPException(404, f"Unknown camera '{cam}'.")
    return cfg.camera_meta(cam)


Theme = Query("light", pattern="^(light|dark)$")


# --------------------------------------------------------------------------
# Background jobs
# --------------------------------------------------------------------------

class Job:
    def __init__(self, kind: str, label: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.label = label
        self.status = "queued"        # queued | running | done | error
        self.progress = 0.0
        self.message = "Waiting to start…"
        self.result: Any = None
        self.error = ""
        self.created = time.time()
        self.finished: float | None = None
        self.detector = False

    def update(self, frac: float, msg: str) -> None:
        self.progress = float(max(0.0, min(1.0, frac)))
        self.message = msg

    def view(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "status": self.status, "progress": self.progress,
                "message": self.message, "error": self.error,
                "result": self.result,
                "elapsed_s": (self.finished or time.time()) - self.created}


JOBS: dict[str, Job] = {}
MAX_DETECTOR_QUEUE = 3      # detector jobs waiting or running at once


def start_job(kind: str, label: str,
              fn: Callable[[Job], Any], detector: bool = False) -> Job:
    if detector:
        # Detector jobs run one at a time behind a lock; past a short queue a
        # new one would only wait minutes for its turn, so say so now.
        pending = sum(1 for j in JOBS.values()
                      if j.detector and j.status in ("queued", "running"))
        if pending >= MAX_DETECTOR_QUEUE:
            raise HTTPException(429, f"The detector already has {pending} jobs "
                                     f"queued. Try again when one finishes.",
                                headers={"Retry-After": "30"})
    job = Job(kind, label)
    job.detector = detector
    JOBS[job.id] = job
    # Keep the table from growing for the life of the server.
    for old in sorted(JOBS.values(), key=lambda j: j.created)[:-50]:
        JOBS.pop(old.id, None)

    def run():
        try:
            if detector:
                job.update(0.0, "Waiting for the detector…")
                with S.DETECTOR_LOCK:
                    job.status = "running"
                    job.result = clean(fn(job))
            else:
                job.status = "running"
                job.result = clean(fn(job))
            job.status = "done"
            job.progress = 1.0
        except Exception as e:  # surfaced to the client, logged here
            traceback.print_exc()
            job.status = "error"
            job.error = f"{type(e).__name__}: {e}"
        finally:
            job.finished = time.time()

    threading.Thread(target=run, daemon=True, name=f"job-{kind}").start()
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job (it may have expired).")
    return ok(job.view())


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------

def _fluid_status() -> dict:
    try:
        import fluid as FL
        if not FL.available():
            return {"available": False, "root": str(FL.FLUID_ROOT),
                    "recordings": [], "totals": None}
        return {"available": True, "root": str(FL.FLUID_ROOT),
                "recordings": [{"id": r.id, "site": r.site, "name": r.name,
                                "site_label": FL.SITE_LABELS.get(r.site, r.site),
                                "has_video": r.video_path is not None}
                               for r in FL.recordings()],
                "totals": (S.fluid_totals() if _WARM["fluid_totals"] else None)}
    except Exception as e:
        return {"available": False, "error": str(e), "recordings": [],
                "totals": None}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/meta")
def meta():
    df = S.load_metrics()
    analysed = S.analysed_cameras(df)
    windows = (df.groupby("camera_id").size().to_dict() if not df.empty else {})
    minutes = (float(df.window_end_s.groupby(df.camera_id).max().sum() / 60)
               if not df.empty else 0.0)
    det_ok, det_path = S.detector_ready()

    cams = []
    for cid in list(cfg.CAMERAS) + [c for c in analysed if c not in cfg.CAMERAS]:
        m = cfg.camera_meta(cid)
        dataset = cid in cfg.CAMERAS
        cams.append({
            "id": cid, "name": m["name"], "short": short(cid),
            "mode": m.get("mode", "area"), "notes": m.get("notes", ""),
            "dataset": dataset,
            "analysed": cid in analysed,
            "windows": int(windows.get(cid, 0)),
            "scale_verified": cfg.scale_is_verified(cid),
            "has_video": dataset and cfg.camera_video_path(cid).exists(),
            "has_reference": dataset and S.reference_image(cid) is not None,
            "has_heatmap": (S.HEATMAP_DIR / f"{cid}_heatmap.jpg").exists(),
            "has_annotated": (cfg.ANNOTATED_DIR / f"{cid}_demo.mp4").exists(),
        })

    rec = None
    if not df.empty:
        try:
            js = S.junction_ids(df)
            adv = so.recommend_controller(df, js, downstream_observed=False,
                                          demands=S.junction_demands(df))
            rec = adv.to_dict()
        except Exception:
            rec = None

    try:
        inc = INC.counts(_incident_items(df))
    except Exception:
        traceback.print_exc()
        inc = None

    return ok({
        "cameras": cams,
        "metrics": {"windows": int(len(df)), "cameras": len(analysed),
                    "minutes": minutes},
        "calibrated": sum(1 for c in cfg.CAMERAS if cfg.scale_is_verified(c)),
        "detector": {"ready": det_ok, "path": det_path,
                     "fast": S.live_detector_label()},
        "fluid": _fluid_status(),
        "mode": cfg.load_optimization_mode(),
        "recommendation": rec,
        "auth": {"required": SEC.auth_required()},
        "incidents": inc,
    })


@app.post("/api/auth/check", dependencies=[Depends(limit("auth", 10, 5))])
def auth_check(name: str = Depends(operator)):
    """200 when the supplied operator token is valid (or none is required).
    Rate-limited, so the token cannot be guessed at speed."""
    return ok({"ok": True, "required": SEC.auth_required(), "name": name})


@app.post("/api/settings/mode", dependencies=[Depends(limit("settings", 30, 10))])
def set_mode(body: dict = Body(...)):
    """The control strategy is a standing operator decision, so it persists."""
    mode = body.get("mode")
    if mode not in cfg.OPTIMIZATION_MODES:
        raise HTTPException(422, f"mode must be one of {cfg.OPTIMIZATION_MODES}")
    cfg.save_optimization_mode(mode)
    return ok({"mode": mode})


# --------------------------------------------------------------------------
# Webster track — Network
# --------------------------------------------------------------------------

@app.get("/api/network")
def network(theme: str = Theme):
    df = need_metrics()
    summary = fc.camera_summary(df)
    order = list(summary.camera_id)
    sat = int(summary.saturated_windows.sum())
    worst = summary.iloc[0]

    rows = []
    for _, r in summary.iterrows():
        cid = r.camera_id
        rows.append({
            **{k: r[k] for k in summary.columns},
            "id": cid, "name": cfg.camera_name(cid), "short": short(cid),
            "mode": cfg.camera_meta(cid).get("mode", "area"),
            "los": T.los_of(float(r.mean_congestion)),
            "has_overlay": S.reference_image(cid) is not None,
        })

    with T.use_mode(theme):
        charts = {"los_timeline": figure(C.network_los_timeline(df, order)),
                  "ranking": figure(C.congestion_ranking(summary))}

    return ok({
        "kpi": {
            "over_capacity_pct": float(df.los.isin(["Heavy", "Jam"]).mean() * 100),
            "sites": len(summary),
            "junction_sites": len(cfg.junction_cameras()),
            "area_sites": len(cfg.area_cameras()),
            "zones": int(summary.zones.sum()),
            "windows": int(len(df)),
            "saturated": sat,
            "saturated_pct": sat / max(1, len(df)) * 100.0,
            "worst": {"id": worst.camera_id, "short": short(worst.camera_id),
                      "mean": float(worst.mean_congestion)},
        },
        "sites": rows,
        "scale_verified_any": bool(df.scale_verified.any())
        if "scale_verified" in df else False,
        "charts": charts,
    })


# --------------------------------------------------------------------------
# Webster track — Site detail
# --------------------------------------------------------------------------

TREND_METRICS = ("congestion_index", "occupancy", "density_pcu_per_km_lane",
                 "speed_kmph", "flow_pcu_per_hr")


def _alerts_payload(alerts, actionable: bool = True) -> dict:
    """Alerts as the views show them. Each carries its incident id and
    workflow status, so a card can be acknowledged where it is read.
    `actionable` is off for an upload that has not been kept: its alerts are
    not part of the network, and the incident log only tracks the network."""
    counts = {s: sum(a.severity == s for a in alerts)
              for s in ("critical", "warning", "info")}
    log = INC.load()["incidents"] if actionable else {}
    out = []
    for a in alerts:
        d = {**a.to_dict(), "camera_short": short(a.camera_id)}
        d["id"] = INC.alert_id(d)
        d["status"] = log.get(d["id"], {}).get("status", "open")
        d["actionable"] = actionable
        out.append(d)
    return {"alerts": out, "counts": counts}


@app.get("/api/sites/{cam}")
def site(cam: str, theme: str = Theme):
    meta_ = need_camera(cam)
    df = need_metrics()
    state = S.zone_state(df, cam)
    if state.empty:
        raise HTTPException(404, f"No metrics for '{cam}' yet. Run "
                                 f"`python src/analyze.py {cam}`.")
    sub = df[df.camera_id == cam]
    ci = float(sub.congestion_index.mean())
    verified = cfg.scale_is_verified(cam)

    zones = []
    for _, r in state.iterrows():
        zci = float(r.get("avg_congestion", 0) or 0)
        zones.append({
            "zone_id": r["zone_id"], "label": str(r["zone_label"]),
            "kind": r.get("zone_kind"),
            "congestion": zci, "los": T.los_of(zci),
            "occupancy": r.get("avg_occupancy"),
            "density": r.get("avg_density"), "speed": r.get("avg_speed"),
            "flow": r.get("avg_flow"), "peak": r.get("peak_congestion"),
            "saturated_windows": int(r.get("sat_windows", 0) or 0),
            "windows": int(r.get("windows", 0) or 0),
        })

    prof = fc.time_profile(df, cam, bin_s=10.0)
    metrics = [m for m in TREND_METRICS
               if not prof.empty and m in prof.columns and prof[m].notna().any()]

    with T.use_mode(theme):
        charts = {
            "zone_los": figure(C.zone_los_timeline(df, cam)),
            "count_vs_occupancy": figure(C.count_vs_occupancy(df, cam)),
        }
        if meta_.get("mode") == "junction":
            charts["fundamental"] = figure(C.fundamental_diagram(df, cam))

    return ok({
        "camera": {"id": cam, "name": meta_["name"], "short": short(cam),
                   "mode": meta_.get("mode", "area"),
                   "notes": meta_.get("notes", ""),
                   "scale_verified": verified},
        "mean_congestion": ci, "los": T.los_of(ci),
        "windows": int(len(sub)),
        "saturated_pct": (float(sub.saturated.mean() * 100)
                          if "saturated" in sub else 0.0),
        "zones": zones,
        "trend_metrics": [{"id": m, "label": C.METRIC_AXIS[m][0],
                           "estimate": bool(C.METRIC_AXIS[m][2]) and not verified}
                          for m in metrics],
        "assets": {
            "overlay": S.reference_image(cam) is not None,
            "heatmap": (S.HEATMAP_DIR / f"{cam}_heatmap.jpg").exists(),
            "annotated_video": (cfg.ANNOTATED_DIR / f"{cam}_demo.mp4").exists(),
        },
        "charts": charts,
        **_alerts_payload(bn.detect(df, cam)),
        "min_sustained_windows": bn.MIN_SUSTAINED_WINDOWS,
    })


def _trend(df: pd.DataFrame, cam: str, metric: str, theme: str) -> dict:
    if metric not in TREND_METRICS:
        raise HTTPException(422, f"metric must be one of {TREND_METRICS}")
    prof = fc.time_profile(df, cam, bin_s=10.0)
    if prof.empty or metric not in prof:
        return {"chart": None, "columns": [], "rows": []}
    with T.use_mode(theme):
        chart = figure(C.zone_trend(prof, metric))
    piv = (prof.pivot(index="bin", columns="zone_label", values=metric)
           .round(2).reset_index().rename(columns={"bin": "t (s)"}))
    piv.columns = [str(c) for c in piv.columns]
    return {"chart": chart, "columns": list(piv.columns), "rows": records(piv)}


@app.get("/api/sites/{cam}/trend")
def site_trend(cam: str, metric: str = "congestion_index", theme: str = Theme):
    need_camera(cam)
    return ok(_trend(need_metrics(), cam, metric, theme))


# --------------------------------------------------------------------------
# Webster track — Signal timing
# --------------------------------------------------------------------------

def _parse_widths(sat_source: str, widths: str) -> dict[str, float]:
    if sat_source not in S.SAT_SOURCES:
        raise HTTPException(422, f"sat_source must be one of {list(S.SAT_SOURCES)}")
    try:
        w = {str(k): float(v) for k, v in json.loads(widths or "{}").items()}
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(422, "widths must be a JSON object of numbers")
    for k, v in w.items():
        if not 2.0 <= v <= 30.0:
            raise HTTPException(422, f"width for {k} must be 2–30 m")
    return w


def _plan(plan) -> dict:
    d = plan.to_dict()
    for p, ph in zip(d["phases"], plan.phases):
        p["total_s"] = ph.total_s
        p["short"] = ph.label.split("-")[0].strip()
    return d


@app.get("/api/signal")
def signal(baseline_cycle: float = Query(120.0, ge=60, le=150),
           delay_model: str = Query("auto", pattern="^(auto|hcm|webster)$"),
           sat_source: str = Query("per_lane"),
           widths: str = Query("{}", description="JSON {approach_id: metres}"),
           theme: str = Theme):
    df = need_metrics()
    demands = S.junction_demands(df)
    if not demands:
        raise HTTPException(409, "No junction-mode cameras analysed yet.")
    w = _parse_widths(sat_source, widths)

    approaches = [{"id": d.approach_id, "label": d.label,
                   "short": short(d.camera_id), "lanes": d.lanes,
                   "default_width_m": float(d.lanes) * 3.5,
                   "flow_pcu_per_hr": d.flow_pcu_per_hr,
                   "critical_lane_flow_pcu_per_hr": d.critical_lane_flow_pcu_per_hr}
                  for d in demands]

    factor = S.saturation_factor(sat_source)
    demands = S.with_widths(demands, factor, w)
    width_notes = ([so.saturation_flow_note(d.approach_width_m) for d in demands
                    if d.approach_width_m] if factor else [])

    base = so.evaluate_fixed_plan(demands, cycle_s=float(baseline_cycle),
                                  delay_model=delay_model)
    opt = so.optimise(demands, junction_name="Adaptive plan (Webster)",
                      delay_model=delay_model)
    cmp = so.compare(base, opt)

    Y = sum(d.flow_ratio for d in demands)
    L = len(demands) * so.LOST_TIME_PER_PHASE_S
    x_common = so.common_degree_of_saturation(Y, opt.cycle_s, L)
    c_min = so.websters_cmin(L, Y, Xc=0.90)

    model_rows = []
    for m, label in (("webster", "Webster 1958 (3-term)"),
                     ("hcm", "HCM control delay")):
        b = so.evaluate_fixed_plan(demands, cycle_s=float(baseline_cycle),
                                   delay_model=m)
        o = so.optimise(demands, delay_model=m)
        c = so.compare(b, o)
        diverges = not math.isfinite(b.mean_delay_s_per_pcu)
        model_rows.append({
            "model": m, "label": label,
            "baseline": c["baseline_mean_delay_s"],
            "optimised": c["optimised_mean_delay_s"],
            # Webster's formula has no finite answer above capacity; a
            # reduction "from infinity" is not a percentage worth printing.
            "reduction_pct": None if diverges else c["delay_reduction_pct"],
            "diverges": diverges,
        })

    def _sweep_min(sw: pd.DataFrame) -> int | None:
        s = sw["Demand-proportional splits"].replace([np.inf, -np.inf], np.nan)
        return int(s.idxmin()) if s.notna().any() else None

    sweep = S.delay_sweep(demands, delay_model)
    best_c = _sweep_min(sweep)
    # The optimiser's self-audit is only meaningful under the delay model
    # Webster's cycle formula was derived from. Under HCM - which the default
    # "auto" model switches to above x = 0.85 - the minimum lands at a much
    # shorter cycle, because HCM's bounded overflow term penalises running
    # close to capacity less than Webster's divergent one. Both are reported,
    # so the UI never presents the HCM minimum as "agreeing" with Webster.
    best_c_webster = (best_c if delay_model == "webster"
                      else _sweep_min(S.delay_sweep(demands, "webster")))

    dmap = {d.approach_id: d for d in demands}
    table = []
    for tag, plan in (("Baseline (fixed timer)", base), ("Adaptive (Webster)", opt)):
        for p in plan.phases:
            d = dmap.get(p.approach_id)
            table.append({
                "Approach": p.label, "Plan": tag,
                "Lanes": d.lanes if d else None,
                "Sat. flow (PCU/hr/lane)": (round(d.saturation_flow_per_lane)
                                            if d else None),
                "~Critical lane flow (PCU/hr)": (round(d.critical_lane_flow_pcu_per_hr)
                                                 if d else None),
                "Flow ratio y": p.flow_ratio, "Cycle (s)": plan.cycle_s,
                "Green (s)": p.green_s, "Saturation x": p.degree_of_saturation,
                "Delay (s/PCU)": p.est_delay_s_per_pcu, "Delay model": p.delay_model,
            })

    with T.use_mode(theme):
        charts = {"cycle": figure(C.cycle_composition(base, opt)),
                  "dumbbell": figure(C.saturation_dumbbell(base, opt)),
                  "delay_curve": figure(C.delay_curve(sweep, opt.cycle_s))}

    return ok({
        "approaches": approaches,
        "sat_sources": [{"id": k, "label": v[0]} for k, v in S.SAT_SOURCES.items()],
        "factor": factor, "width_notes": [n for n in width_notes if n],
        "base": _plan(base), "opt": _plan(opt), "compare": cmp,
        "Y": Y, "L": L, "x_common": x_common,
        "c_min": c_min if math.isfinite(c_min) else None,
        "max_cycle": so.MAX_CYCLE_S, "amber_s": so.AMBER_S,
        "all_red_s": so.ALL_RED_S,
        "hcm_period_min": so.HCM_ANALYSIS_PERIOD_HR * 60,
        "worst_before": max(p.degree_of_saturation for p in base.phases),
        "worst_after": max(p.degree_of_saturation for p in opt.phases),
        "over_before": sum(p.degree_of_saturation >= 1.0 for p in base.phases),
        "delay_models": model_rows,
        "sweep": records(sweep.round(1).reset_index()),
        "best_cycle": best_c,
        "best_cycle_webster": best_c_webster,
        "delay_model": delay_model,
        "plan_table": table,
        "plans_text": so.format_plan(base) + "\n\n" + so.format_plan(opt),
        "charts": charts,
    })


# --------------------------------------------------------------------------
# Webster track — Bottlenecks, Outlook, Method
# --------------------------------------------------------------------------

@app.get("/api/bottlenecks")
def bottlenecks(theme: str = Theme):
    df = need_metrics()
    alerts = bn.detect_all(df)
    with T.use_mode(theme):
        charts = {"gantt": figure(C.alert_gantt(alerts)),
                  "mix": figure(C.alert_mix(alerts))}
    return ok({
        **_alerts_payload(alerts),
        "kinds": sorted({a.kind.replace("_", " ") for a in alerts}),
        "sites": len({a.camera_id for a in alerts}),
        "longest_s": max((a.duration_s for a in alerts), default=0.0),
        "min_sustained_windows": bn.MIN_SUSTAINED_WINDOWS,
        "charts": charts,
    })


@app.get("/api/outlook")
def outlook(horizon: float = Query(60.0, ge=15, le=300), theme: str = Theme):
    df = need_metrics()
    forecasts = fc.forecast_all(df, horizon_s=float(horizon))
    risks = fc.rising_risks(forecasts, threshold=0.45)
    with T.use_mode(theme):
        chart = figure(C.forecast_chart(forecasts, float(horizon)))

    def fmt(f):
        return {**f.to_dict(), "camera_short": short(f.camera_id),
                "camera_name": cfg.camera_name(f.camera_id)}

    return ok({
        "horizon_s": horizon,
        "forecasts": [fmt(f) for f in forecasts],
        "risks": [fmt(f) for f in risks],
        "reliable": sum(1 for f in forecasts if f.reliable),
        "threshold": 0.45,
        "chart": chart,
    })


@app.get("/api/method")
def method(theme: str = Theme):
    df = need_metrics()
    g = df.groupby("camera_id").agg(det=("n_detections_mean", "mean"),
                                    ci=("congestion_index", "mean"))
    worst_id = str(g.ci.idxmax())
    busiest_id = str(g.det.idxmax())
    mix = C.fleet_table(df)
    with T.use_mode(theme):
        charts = {"occlusion": figure(C.occlusion_paradox(df)),
                  "count_vs_occupancy": figure(C.count_vs_occupancy(df, worst_id)),
                  "fleet": figure(C.fleet_mix(mix))}
    groups = [gname for gname in cfg.GROUP_ORDER if gname in mix.columns]
    return ok({
        "worst": {"id": worst_id, "short": short(worst_id),
                  "detections": float(g.det[worst_id])},
        "busiest": {"id": busiest_id, "short": short(busiest_id),
                    "detections": float(g.det[busiest_id])},
        "fleet": {"groups": groups,
                  "rows": (records(mix.drop(columns=["camera_id"]).round(1))
                           if not mix.empty else [])},
        "junction_sites": [short(c) for c in cfg.junction_cameras()],
        "area_sites": [short(c) for c in cfg.area_cameras()],
        "charts": charts,
    })


# --------------------------------------------------------------------------
# Images and media
# --------------------------------------------------------------------------

@app.get("/api/images/overlay/{cam}")
def image_overlay(cam: str, w: int = Query(1600, ge=200, le=3840)):
    need_camera(cam)
    df = S.load_metrics()
    state = S.zone_state(df, cam) if not df.empty else pd.DataFrame()
    return jpeg(S.overlay(cam, state), max_width=w)


@app.get("/api/images/reference/{cam}")
def image_reference(cam: str, w: int = Query(1600, ge=200, le=3840)):
    need_camera(cam)
    return jpeg(S.reference_image(cam), max_width=w)


@app.get("/api/images/heatmap/{cam}")
def image_heatmap(cam: str, w: int = Query(1600, ge=200, le=3840)):
    need_camera(cam)
    return jpeg(S.heatmap_image(cam), max_width=w)


@app.get("/api/media/annotated/{cam}")
def media_annotated(cam: str):
    need_camera(cam)
    p = cfg.ANNOTATED_DIR / f"{cam}_demo.mp4"
    if not p.exists():
        raise HTTPException(404, f"Not rendered — `python src/render_demo.py {cam}`.")
    return FileResponse(p, media_type="video/mp4")


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------

def _calibratable() -> list[str]:
    return [c for c in cfg.CAMERAS if Z.load(c) is not None]


@app.get("/api/calibration")
def calibration_overview():
    import calibration as CAL
    state = CAL.load_state()
    rows = []
    for c in _calibratable():
        m = state["measurements"].get(c)
        cal = Z.load(c)
        rows.append({
            "id": c, "name": cfg.camera_name(c), "short": short(c),
            "mode": cfg.camera_meta(c)["mode"],
            "verified": cfg.scale_is_verified(c),
            "widths": [{"row": int(r), "metres": float(wd)}
                       for r, wd in (m or {}).get("rows", [])],
            "camera_height_m": (m or {}).get("camera_height_m"),
            "hfov_deg": (m or {}).get("hfov_deg"),
            "applied_at": (m or {}).get("applied_at"),
            "model": ("from measurement" if m else
                      "seeded assumption" if (cal and cal.scale_model) else None),
            "has_video": cfg.camera_video_path(c).exists(),
        })
    return ok({"cameras": rows,
               "verified": sum(1 for r in rows if r["verified"]),
               "detector_ready": S.detector_ready()[0]})


@app.get("/api/calibration/{cam}")
def calibration_camera(cam: str):
    import calibration as CAL
    if cam not in cfg.CAMERAS:
        raise HTTPException(404, f"Unknown camera '{cam}'.")
    calib = Z.load(cam)
    if calib is None:
        raise HTTPException(404, "No zones are traced on this camera.")
    record = CAL.load_state()["measurements"].get(cam)
    rows = CAL.suggested_rows(calib)
    prior = {int(r): float(wd) for r, wd in (record or {}).get("rows", [])}
    return ok({
        "id": cam, "name": cfg.camera_name(cam),
        "verified": cfg.scale_is_verified(cam),
        "record": ({k: v for k, v in record.items() if k != "previous_scale_model"}
                   if record else None),
        "rows": [{"row": int(y), "px": CAL.road_pixel_width(calib, y),
                  "prior_m": prior.get(int(y), 0.0)} for y in rows],
        "frame_width": int(calib.frame_size[0]),
        "camera_height_m": float((record or {}).get("camera_height_m", 7.0)),
        "hfov_deg": float((record or {}).get("hfov_deg", 60.0)),
        "has_reference": S.reference_image(cam) is not None,
    })


@app.get("/api/images/guide/{cam}")
def image_guide(cam: str, w: int = Query(1600, ge=200, le=3840)):
    import calibration as CAL
    calib = Z.load(cam)
    if calib is None:
        raise HTTPException(404, "No zones for this camera.")
    rows = CAL.suggested_rows(calib)
    return jpeg(CAL.guide_image(cam, rows, calib), max_width=w)


def _fit_args(body: dict) -> tuple[list[tuple[int, float]], float, float]:
    try:
        meas = [(int(r), float(wd)) for r, wd in body.get("measurements", [])
                if float(wd) > 0]
        height = float(body.get("camera_height_m", 7.0))
        hfov = float(body.get("hfov_deg", 60.0))
    except (TypeError, ValueError):
        raise HTTPException(422, "measurements must be [[row, metres], …]")
    if not meas:
        raise HTTPException(422, "Enter at least one measured width.")
    if not (2.0 <= height <= 25.0 and 10.0 <= hfov <= 140.0):
        raise HTTPException(422, "Camera height 2–25 m, field of view 10–140°.")
    return meas, height, hfov


@app.post("/api/calibration/{cam}/fit", dependencies=[Depends(limit("fit", 30, 10))])
def calibration_fit(cam: str, body: dict = Body(...)):
    import calibration as CAL
    if cam not in cfg.CAMERAS:
        raise HTTPException(404, f"Unknown camera '{cam}'.")
    meas, height, hfov = _fit_args(body)
    res = CAL.fit_scale(cam, meas, camera_height_m=height, hfov_deg=hfov)
    out = res.to_dict()
    out.pop("model", None)
    out["focal_px"] = CAL.focal_px_from_hfov(hfov, Z.load(cam).frame_size[0])
    return ok(out)


def _remeasure(cam: str) -> Callable[[Job], Any]:
    def run(job: Job):
        from analyze import analyze, rebuild_combined
        job.update(0.01, "Loading the detector…")
        det = S.get_detector()
        df = analyze(cam, det, stride=4, on_progress=job.update)
        rebuild_combined()
        job.update(1.0, f"Re-measured — {len(df)} windows written.")
        return {"windows": len(df)}
    return run


@app.post("/api/calibration/{cam}/apply",
          dependencies=[Depends(operator), Depends(limit("remeasure", 4, 2))])
def calibration_apply(cam: str, body: dict = Body(...)):
    import calibration as CAL
    if cam not in cfg.CAMERAS:
        raise HTTPException(404, f"Unknown camera '{cam}'.")
    ok_det, path = S.detector_ready()
    if not ok_det:
        raise HTTPException(409, f"Detection weights not found at {path}. "
                                 f"Run `python scripts/download_models.py`.")
    if not cfg.camera_video_path(cam).exists():
        raise HTTPException(409, "The source video for this camera is not on "
                                 "this machine, so it cannot be re-measured.")
    meas, height, hfov = _fit_args(body)
    res = CAL.fit_scale(cam, meas, camera_height_m=height, hfov_deg=hfov)
    if not res.ok:
        raise HTTPException(422, res.message)
    CAL.apply(cam, res, mark_verified=True, measurements=meas,
              camera_height_m=height, hfov_deg=hfov)
    job = start_job("remeasure", f"Re-measuring {cfg.camera_name(cam)}",
                    _remeasure(cam), detector=True)
    return ok({"job": job.view()})


@app.delete("/api/calibration/{cam}",
            dependencies=[Depends(operator), Depends(limit("remeasure", 4, 2))])
def calibration_clear(cam: str):
    import calibration as CAL
    if cam not in cfg.CAMERAS:
        raise HTTPException(404, f"Unknown camera '{cam}'.")
    CAL.clear(cam)
    job = None
    if S.detector_ready()[0] and cfg.camera_video_path(cam).exists():
        job = start_job("remeasure", f"Re-measuring {cfg.camera_name(cam)}",
                        _remeasure(cam), detector=True)
    return ok({"job": job.view() if job else None})


# --------------------------------------------------------------------------
# Analyse footage (uploads)
# --------------------------------------------------------------------------

class Upload:
    def __init__(self, path: Path, name: str, demo: bool = False):
        import ingest
        self.id = uuid.uuid4().hex[:12]
        self.path = path
        self.name = name
        self.demo = demo
        self.media = ingest.probe(path)
        self.session = ingest.session_id(path)
        self.roi = None
        self.calib = None
        self.n_lanes = 0
        self.result = None
        self.created = time.time()

    def view(self) -> dict:
        m = self.media
        size = self.path.stat().st_size / 1e6 if self.path.exists() else 0.0
        return {
            "id": self.id, "name": self.name, "demo": self.demo,
            "media": {"kind": m.kind, "ok": m.ok, "error": m.error,
                      "width": m.width, "height": m.height, "fps": m.fps,
                      "duration_s": m.duration_s,
                      "label": m.label if m.ok else "", "size_mb": size},
            "roi": (None if self.roi is None else {
                "method": self.roi.method, "note": self.roi.note,
                "coverage": self.roi.coverage, "anchors": self.roi.n_anchors,
                "zones": len(self.calib.zones) if self.calib else 0,
                "mode": self.calib.mode if self.calib else None,
                "n_lanes": self.n_lanes}),
            "analysed": self.result is not None,
        }


UPLOADS: dict[str, Upload] = {}


def need_upload(uid: str) -> Upload:
    u = UPLOADS.get(uid)
    if u is None:
        raise HTTPException(404, "Unknown upload (the server may have restarted).")
    return u


def _register_upload(u: Upload) -> dict:
    UPLOADS[u.id] = u
    for old in sorted(UPLOADS.values(), key=lambda x: x.created)[:-20]:
        UPLOADS.pop(old.id, None)
    return u.view()


@app.post("/api/uploads", dependencies=[Depends(operator), Depends(limit("upload", 6, 3))])
async def upload_file(file: UploadFile = File(...)):
    import ingest
    try:
        dest = ingest.new_upload_path(file.filename or "upload")
    except ValueError as e:
        raise HTTPException(415, str(e))
    written = 0
    too_big = False
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                too_big = True
                break
            f.write(chunk)
    if too_big:
        dest.unlink(missing_ok=True)
        raise HTTPException(413, "Uploads are limited to 1 GB.")
    u = Upload(dest, file.filename or dest.name)
    if not u.media.ok:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, u.media.error)
    return ok(_register_upload(u))


@app.post("/api/uploads/demo", dependencies=[Depends(operator), Depends(limit("upload", 6, 3))])
def upload_demo(body: dict = Body(...)):
    cam = body.get("camera")
    if cam not in cfg.CAMERAS:
        raise HTTPException(404, f"Unknown camera '{cam}'.")
    p = cfg.camera_video_path(cam)
    if not p.exists():
        raise HTTPException(409, "That camera's source video is not on this "
                                 "machine.")
    u = Upload(p, f"{cfg.camera_name(cam)} (dataset clip)", demo=True)
    return ok(_register_upload(u))


@app.get("/api/uploads/{uid}")
def upload_get(uid: str):
    return ok(need_upload(uid).view())


@app.delete("/api/uploads/{uid}", dependencies=[Depends(operator)])
def upload_delete(uid: str):
    u = UPLOADS.pop(uid, None)
    # Dataset clips are loaded in place and are never deleted.
    if u is not None and not u.demo:
        u.path.unlink(missing_ok=True)
    return ok({"deleted": u is not None})


def _whole_frame_roi(media):
    import ingest
    mask = np.full((media.height, media.width), 255, np.uint8)
    return ingest.RoiResult(
        mask, [(0, 0), (media.width - 1, 0), (media.width - 1, media.height - 1),
               (0, media.height - 1)], 1.0, 0, "whole-frame",
        note="Whole frame used as one zone, as requested. Occupancy will "
             "include everything in view, not just carriageway.")


@app.post("/api/uploads/{uid}/roi",
          dependencies=[Depends(operator), Depends(limit("analyse", 12, 4))])
def upload_roi(uid: str, body: dict = Body(...)):
    import ingest
    u = need_upload(uid)
    method = body.get("method", "traffic")
    try:
        n_lanes = int(body.get("n_lanes", 0) or 0)
    except (TypeError, ValueError):
        raise HTTPException(422, "n_lanes must be an integer")
    if not 0 <= n_lanes <= 8:
        raise HTTPException(422, "n_lanes must be 0–8")
    u.result = None

    def finish(roi):
        u.roi = roi
        u.n_lanes = n_lanes
        u.calib = ingest.build_calibration(u.session, roi,
                                           (u.media.width, u.media.height),
                                           n_lanes=n_lanes)
        return u.view()

    if method == "whole" or u.media.kind == "image":
        return ok({"upload": finish(_whole_frame_roi(u.media)), "job": None})

    ok_det, path = S.detector_ready()
    if not ok_det:
        raise HTTPException(409, f"Detection weights not found at {path}.")

    def run(job: Job):
        roi = ingest.auto_roi(u.media, S.get_detector(), samples=60,
                              on_progress=job.update)
        return finish(roi)

    job = start_job("roi", "Finding the road", run, detector=True)
    return ok({"upload": u.view(), "job": job.view()})


@app.get("/api/uploads/{uid}/zones.jpg")
def upload_zones_image(uid: str, w: int = Query(1600, ge=200, le=3840)):
    import ingest
    u = need_upload(uid)
    if u.calib is None:
        raise HTTPException(409, "Find the road first.")
    return jpeg(ingest.preview_zones(u.media, u.calib, u.roi), max_width=w)


@app.post("/api/uploads/{uid}/analyze",
          dependencies=[Depends(operator), Depends(limit("analyse", 12, 4))])
def upload_analyze(uid: str, body: dict = Body(default={})):
    import ingest
    u = need_upload(uid)
    if u.calib is None:
        raise HTTPException(409, "Find the road first.")
    ok_det, path = S.detector_ready()
    if not ok_det:
        raise HTTPException(409, f"Detection weights not found at {path}.")
    try:
        stride = int(body.get("stride", 3))
        seconds = float(body.get("seconds", 120))
    except (TypeError, ValueError):
        raise HTTPException(422, "stride and seconds must be numbers")
    if stride not in (2, 3, 4, 6, 8) or not 5 <= seconds <= 1800:
        raise HTTPException(422, "stride must be 2/3/4/6/8 and seconds 5–1800")

    def run(job: Job):
        det = S.get_detector()
        if u.media.kind == "image":
            job.update(0.3, "Detecting…")
            u.result = ingest.analyze_image(u.media, u.calib, det)
        else:
            u.result = ingest.analyze_video(u.media, u.calib, det, stride=stride,
                                            max_seconds=seconds,
                                            on_progress=job.update)
        return u.view()

    job = start_job("analyze", f"Analysing {u.name}", run, detector=True)
    return ok({"job": job.view()})


def _video_report(u: Upload, theme: str) -> dict:
    res = u.result
    df = res.metrics
    if df.empty:
        return {"kind": "video", "empty": True}
    verified = cfg.scale_is_verified(res.session)
    sat = int(df.saturated.sum()) if "saturated" in df else 0
    worst = df.loc[df.congestion_index.idxmax()]
    alerts = bn.detect(df, res.session)
    forecasts = fc.forecast_all(df, horizon_s=60.0)
    mean_ci = float(df.congestion_index.mean())
    prof = fc.time_profile(df, res.session, bin_s=10.0)
    metrics = [m for m in TREND_METRICS
               if not prof.empty and m in prof.columns and prof[m].notna().any()]
    with T.use_mode(theme):
        charts = {"zone_los": figure(C.zone_los_timeline(df, res.session)),
                  "count_vs_occupancy": figure(C.count_vs_occupancy(df, res.session)),
                  "forecast": (figure(C.forecast_chart(forecasts, 60.0))
                               if forecasts else None)}
    return {
        "kind": "video", "empty": False,
        "session": res.session, "scale_verified": verified,
        "windows": len(df), "seconds": res.seconds_covered,
        "frames": res.frames_processed, "elapsed_s": res.elapsed_s,
        "over_capacity_pct": float(df.los.isin(["Heavy", "Jam"]).mean() * 100),
        "worst": {"zone": str(worst.zone_label),
                  "ci": float(worst.congestion_index),
                  "t": float(worst.window_start_s)},
        "mean_congestion": mean_ci, "los": T.los_of(mean_ci),
        "zones": int(df.zone_id.nunique()), "mode": res.calib.mode,
        "vehicles": int(sum(res.group_counts.values())),
        "saturated": sat, "saturated_pct": sat / max(1, len(df)) * 100,
        "groups": [{"group": g, "detections": n} for g, n in
                   sorted(res.group_counts.items(), key=lambda kv: -kv[1])],
        "has_preview": res.preview is not None,
        "trend_metrics": [{"id": m, "label": C.METRIC_AXIS[m][0]} for m in metrics],
        "charts": charts,
        **_alerts_payload(alerts, actionable=False),
    }


def _image_report(u: Upload) -> dict:
    res = u.result
    zdf = pd.DataFrame(res.zones)
    worst = zdf.loc[zdf.congestion_index.idxmax()] if len(zdf) else None
    return {
        "kind": "image",
        "vehicles": int(zdf.n_detections.sum()) if len(zdf) else 0,
        "pcu": float(zdf.pcu.sum()) if len(zdf) else 0.0,
        "zones": records(zdf),
        "worst": (None if worst is None else
                  {"zone": str(worst.zone_label), "ci": float(worst.congestion_index),
                   "los": str(worst.los)}),
        "mode": res.calib.mode,
        "classes": [{"class": k, "count": v, "pcu_each": cfg.PCU_FACTORS.get(k, 1.0)}
                    for k, v in sorted(res.class_counts.items(),
                                       key=lambda kv: -kv[1])],
        "not_measurable": list(res.not_measurable),
    }


@app.get("/api/uploads/{uid}/report")
def upload_report(uid: str, theme: str = Theme):
    import ingest
    u = need_upload(uid)
    if u.result is None:
        raise HTTPException(409, "Not analysed yet.")
    if isinstance(u.result, ingest.ImageResult):
        return ok(_image_report(u))
    return ok(_video_report(u, theme))


@app.get("/api/uploads/{uid}/trend")
def upload_trend(uid: str, metric: str = "congestion_index", theme: str = Theme):
    import ingest
    u = need_upload(uid)
    if not isinstance(u.result, ingest.VideoResult):
        raise HTTPException(409, "No video analysis for this upload.")
    return ok(_trend(u.result.metrics, u.result.session, metric, theme))


@app.get("/api/uploads/{uid}/preview.jpg")
def upload_preview(uid: str, w: int = Query(1600, ge=200, le=3840)):
    import ingest
    u = need_upload(uid)
    res = u.result
    img = (res.annotated if isinstance(res, ingest.ImageResult)
           else getattr(res, "preview", None))
    return jpeg(img, max_width=w)


@app.get("/api/uploads/{uid}/metrics.csv")
def upload_csv(uid: str):
    import ingest
    u = need_upload(uid)
    if not isinstance(u.result, ingest.VideoResult):
        raise HTTPException(409, "No video analysis for this upload.")
    return Response(u.result.metrics.to_csv(index=False), media_type="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="{u.path.stem}_metrics.csv"'})


@app.get("/api/uploads/{uid}/report.html")
def upload_html(uid: str):
    import ingest
    import report_html
    u = need_upload(uid)
    res = u.result
    if not isinstance(res, ingest.VideoResult):
        raise HTTPException(409, "No video analysis for this upload.")
    alerts = bn.detect(res.metrics, res.session)
    forecasts = fc.forecast_all(res.metrics, horizon_s=60.0)
    html = report_html.build(res, alerts, forecasts,
                             cfg.scale_is_verified(res.session))
    return Response(html, media_type="text/html",
                    headers={"Content-Disposition":
                             f'attachment; filename="{u.path.stem}_report.html"'})


@app.post("/api/uploads/{uid}/persist",
          dependencies=[Depends(operator), Depends(limit("persist", 6, 3))])
def upload_persist(uid: str):
    import ingest
    u = need_upload(uid)
    if not isinstance(u.result, ingest.VideoResult):
        raise HTTPException(409, "Only an analysed video can be kept.")
    p = ingest.persist(u.result)
    return ok({"file": p.name})


# --------------------------------------------------------------------------
# Max-pressure track — FLUID
# --------------------------------------------------------------------------

def need_fluid():
    import fluid as FL
    if not FL.available():
        raise HTTPException(
            409, "The FLUID dataset is not on this machine. figshare DOI "
                 "10.6084/m9.figshare.29974954; set FLUID_ROOT to where it was "
                 "extracted.")
    return FL


def need_recording(rid: str):
    FL = need_fluid()
    rec = FL.find(rid)
    tables = S.fluid_tables(rid) if rec else None
    if rec is None or tables is None:
        raise HTTPException(404, f"Unknown recording '{rid}'.")
    return FL, rec, tables


@app.get("/api/fluid/summary")
def fluid_summary(theme: str = Theme):
    need_fluid()
    d = S.fluid_summary()
    if d.empty:
        raise HTTPException(404, "No FLUID recordings could be replayed.")
    ratio = d.press_mp / d.press_actual.clip(lower=1e-6)
    worst = d.loc[d.wasted_actual.idxmax()]
    with T.use_mode(theme):
        chart = figure(C.fluid_validation_chart(d))
    return ok({
        "rows": records(d),
        "wasted_actual": {"min": d.wasted_actual.min(), "max": d.wasted_actual.max()},
        "wasted_mp": {"min": d.wasted_mp.min(), "max": d.wasted_mp.max()},
        "relief_ratio": {"min": ratio.min(), "max": ratio.max()},
        "worst": {"site": worst.site, "label": worst.label,
                  "wasted": worst.wasted_actual},
        "genuine": bool((d["mode"] == "max-pressure").all()),
        "steps": int(d.steps.sum()),
        "chart": chart,
    })


@app.get("/api/fluid/replay")
def fluid_replay(rid: str, min_green: float = Query(15.0, ge=5, le=60),
                 downstream: str = Query("queue", pattern="^(queue|occupancy)$"),
                 theme: str = Theme):
    FL, rec, (queues, sig, turns) = need_recording(rid)
    res = S.fluid_replay(rid, float(min_green), downstream)
    cat = FL.phase_catalogue(sig)
    qi = queues.groupby("t").queue_in_pcu.sum()
    qo = queues.groupby("t").queue_out_pcu.max()
    keys = set(res.phase_share_actual) | set(res.phase_share_maxpressure)
    diffs = sorted(((k, res.phase_share_maxpressure.get(k, 0)
                     - res.phase_share_actual.get(k, 0)) for k in keys),
                   key=lambda kv: -abs(kv[1]))
    with T.use_mode(theme):
        charts = {"turns": figure(C.turn_ratio_chart(turns)),
                  "phase_share": figure(C.phase_share_chart(
                      res.phase_share_actual, res.phase_share_maxpressure)),
                  "timeline": figure(C.fluid_timeline(res.history))}
    return ok({
        "recording": {"id": rec.id, "site": rec.site, "name": rec.name,
                      "site_label": FL.SITE_LABELS.get(rec.site, rec.site)},
        "cycle_s": FL.cycle_length_s(sig), "phases": len(cat),
        "cycles": int(sig.cycle.nunique()),
        "movements": int(queues.movement.nunique()),
        "queue_in": {"mean": qi.mean(), "max": qi.max()},
        "queue_out": {"mean": qo.mean()},
        "turns": records(turns[["entry_direction", "exit_direction",
                                "vehicles", "turn_ratio"]].round(3)),
        "result": {k: v for k, v in res.to_dict().items() if k != "history"},
        "phase_share": [{"phase": k,
                         "actual": res.phase_share_actual.get(k, 0),
                         "maxpressure": res.phase_share_maxpressure.get(k, 0)}
                        for k, _ in diffs],
        "top_diff": ({"phase": diffs[0][0], "pp": diffs[0][1]} if diffs else None),
        "catalogue": [{"phase": p["phase_key"], "movements": list(p["movements"]),
                       "times_run": p["times_run"],
                       "mean_green_s": p["mean_green_s"],
                       "total_green_s": p["total_green_s"]} for p in cat],
        "charts": charts,
    })


@app.get("/api/fluid/replay.csv")
def fluid_replay_csv(rid: str, min_green: float = Query(15.0, ge=5, le=60),
                     downstream: str = Query("queue", pattern="^(queue|occupancy)$")):
    need_recording(rid)
    res = S.fluid_replay(rid, float(min_green), downstream)
    name = f"maxpressure_replay_{rid.replace('/', '_')}.csv"
    return Response(pd.DataFrame(res.history).to_csv(index=False),
                    media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _times(queues: pd.DataFrame) -> list[float]:
    return [float(t) for t in sorted(queues.t.unique())]


@app.get("/api/fluid/instant")
def fluid_instant(rid: str, t: float | None = None):
    import maxpressure as MP
    FL, rec, (queues, sig, turns) = need_recording(rid)
    times = _times(queues)
    if not times:
        raise HTTPException(404, "No queue measurements in this recording.")
    if t is None:
        t = times[min(len(times) - 1, len(times) // 3)]
    t = min(times, key=lambda x: abs(x - t))          # snap to the grid

    mv = FL.movements_at(queues, float(t), turns=turns)
    ph = FL.controller_phases(sig, queues, min_green_s=cfg.MP_MIN_GREEN_S,
                              max_green_s=cfg.MP_MAX_GREEN_S)
    dec = MP.decide(mv, ph)
    full = dec.mode == MP.MODE_FULL
    mark = dec.chosen_phase_id if full else dec.greedy_phase_id
    return ok({
        "t": t, "t_min": times[0], "t_max": times[-1],
        "observed": sum(1 for m in mv if m.downstream_observed),
        "movements_total": len(mv),
        "decision": {"mode": dec.mode, "usable": dec.usable,
                     "chosen_label": dec.chosen_label,
                     "chosen_pressure": dec.chosen_pressure,
                     "greedy_label": dec.greedy_label,
                     "all_non_positive": dec.all_non_positive,
                     "note": dec.note, "warnings": dec.warnings},
        "phases": [{"phase": p.label, "phase_id": p.phase_id,
                    "pressure": p.pressure,
                    "observed": p.observed_movements, "total": p.total_movements,
                    "serve": p.phase_id == mark, "note": p.note}
                   for p in dec.phases],
        "movements": [{"label": m.label, "queue_in": m.queue_in_pcu,
                       "queue_out": m.queue_out_pcu, "turn_ratio": m.turn_ratio,
                       "pressure": m.pressure} for m in mv],
        "actual_green": sorted(FL.green_at(sig, float(t))),
    })


@app.get("/api/fluid/scene")
def fluid_scene(rid: str, theme: str = Theme):
    FL, rec, (queues, sig, turns) = need_recording(rid)
    w2p = FL.world_to_pixel(rid)
    mix = S.fluid_mix(rid)
    times = _times(queues)
    with T.use_mode(theme):
        chart = figure(C.fluid_fleet_mix(mix))
    return ok({
        "px_per_m": w2p.px_per_m if w2p else None,
        "residual_px": w2p.residual_px if w2p else None,
        "cycle_s": FL.cycle_length_s(sig),
        "phases": len(FL.controller_phases(sig, queues)),
        "has_video": rec.video_path is not None,
        "video_mb": (rec.video_path.stat().st_size / 1e6) if rec.video_path else None,
        "queue_speed_mps": FL.QUEUE_SPEED_MPS,
        "t_min": times[0] if times else 0.0,
        "t_max": times[-1] if times else 0.0,
        "t_default": times[min(len(times) - 1, len(times) // 3)] if times else 0.0,
        "mix": records(mix.round(2)) if not mix.empty else [],
        "chart": chart,
    })


@app.get("/api/fluid/image/{kind}")
def fluid_image(kind: str, rid: str, t: float | None = None,
                w: int = Query(1600, ge=200, le=3840)):
    FL, rec, (queues, sig, turns) = need_recording(rid)
    if kind == "zones":
        return jpeg(FL.zone_overlay(rid, queues, t), max_width=w)
    if kind == "heatmap":
        return jpeg(FL.trajectory_heatmap(rid), max_width=w)
    if kind == "instant":
        if t is None:
            raise HTTPException(422, "t is required")
        img, n_all, n_q = FL.annotated_instant(rid, float(t))
        return jpeg(img, max_width=w,
                    headers={"X-Vehicles": str(n_all), "X-Queued": str(n_q)})
    raise HTTPException(404, "kind must be zones, heatmap or instant")


@app.get("/api/fluid/video")
def fluid_video(rid: str):
    FL, rec, _ = need_recording(rid)
    if rec.video_path is None:
        raise HTTPException(404, "The drone video for this recording was not "
                                 "downloaded.")
    return FileResponse(rec.video_path, media_type="video/mp4")


# --------------------------------------------------------------------------
# Max-pressure track — simulator and method
# --------------------------------------------------------------------------

@app.get("/api/sim/scenarios")
def sim_scenarios():
    import network_sim as NS
    return ok({"scenarios": [{"id": k, "label": v["label"], "blurb": v["blurb"]}
                             for k, v in NS.SCENARIOS.items()]})


@app.post("/api/sim/run", dependencies=[Depends(limit("sim", 30, 10))])
def sim_run(body: dict = Body(default={}), theme: str = Theme):
    import network_sim as NS
    scenario = body.get("scenario", "steady")
    if scenario not in NS.SCENARIOS:
        raise HTTPException(422, f"scenario must be one of {list(NS.SCENARIOS)}")
    # Defaults are the corridor's own (700 / 300 PCU/hr) - the demand the
    # README and REPORT head-to-head table was produced with - so pressing Run
    # without touching anything reproduces the published figures.
    try:
        main_flow = float(body.get("main", 700))
        cross_flow = float(body.get("cross", 300))
    except (TypeError, ValueError):
        raise HTTPException(422, "main and cross must be numbers")
    if not (400 <= main_flow <= 1400 and 150 <= cross_flow <= 700):
        raise HTTPException(422, "main 400–1400, cross 150–700 PCU/hr")
    import inspect
    cc = inspect.signature(NS.compare_controllers).parameters
    free_min_green = float(cc["min_green_s"].default)
    free_margin = float(cc["switch_margin"].default)

    kw = dict(main_arrival_pcu_per_hr=main_flow,
              cross_arrival_pcu_per_hr=cross_flow)
    net_a, js = NS.build(scenario, 1800.0, **kw)
    greens, cycle, Y = NS.webster_splits(net_a, js)
    r_fixed = net_a.run(NS.FixedTimeController(greens, cycle_s=cycle),
                        seconds=1800.0)
    net_b, _ = NS.build(scenario, 1800.0, **kw)
    r_cyc = net_b.run(NS.CyclicMaxPressureController(
        cycle_s=cycle, min_green_s=cfg.MP_MIN_GREEN_S), seconds=1800.0)
    net_c, _ = NS.build(scenario, 1800.0, **kw)
    # The free-running variant uses the documented experiment's settings
    # (network_sim.compare_controllers), so its row matches REPORT.md.
    r_free = net_c.run(NS.MaxPressureController(
        min_green_s=free_min_green, switch_margin=free_margin), seconds=1800.0)

    base = r_fixed.mean_delay_s_per_pcu

    def gain(r):
        return ((base - r.mean_delay_s_per_pcu) / base * 100.0) if base else 0.0

    def ctl(r):
        return {"name": r.controller, "delay": r.mean_delay_s_per_pcu,
                "switches": r.switches, "gain_pct": gain(r)}

    best = max((r_cyc, r_free), key=gain)
    with T.use_mode(theme):
        charts = {"comparison": figure(C.controller_comparison(
                      [r_fixed, r_cyc, r_free])),
                  "queues": figure(C.queue_history({
                      "Fixed-time": r_fixed.history, "MP — cyclic": r_cyc.history,
                      "MP — free-running": r_free.history}))}
    return ok({
        "scenario": {"id": scenario, "label": NS.SCENARIOS[scenario]["label"]},
        "Y": Y, "cycle_s": cycle,
        "fixed": ctl(r_fixed), "cyclic": ctl(r_cyc), "free": ctl(r_free),
        "best": ctl(best),
        "table": [r.to_row() for r in (r_fixed, r_cyc, r_free)],
        "charts": charts,
    })


@app.get("/api/mp/method")
def mp_method():
    import maxpressure as MP
    df = need_metrics()
    junctions = S.junction_ids(df)
    movements = S.mp_movements_from_metrics(df, junctions)
    phases = MP.one_movement_per_phase(movements, min_green_s=cfg.MP_MIN_GREEN_S,
                                       max_green_s=cfg.MP_MAX_GREEN_S)
    decision = MP.decide(movements, phases)
    return ok({
        "mode": decision.mode, "usable": decision.usable,
        "observed": sum(1 for m in movements if m.downstream_observed),
        "total": len(movements),
        "junctions": [short(c) for c in junctions],
        "warnings": decision.warnings,
        "chosen_phase_id": decision.chosen_phase_id,
        "movements": [{"label": m.label, "queue_in": m.queue_in_pcu,
                       "queue_in_source": m.queue_in_source,
                       "queue_out": m.queue_out_pcu,
                       "queue_out_source": m.queue_out_source,
                       "pressure": m.pressure} for m in movements],
    })


# --------------------------------------------------------------------------
# Signal timing — what-if
# --------------------------------------------------------------------------

@app.get("/api/signal/whatif")
def signal_whatif(growth: float = Query(0.0, ge=-50, le=100,
                                        description="demand change from today, %"),
                  loss_approach: str | None = Query(None),
                  loss_pct: float = Query(0.0, ge=0, le=60,
                                          description="saturation flow lost, %"),
                  growth_per_year: float = Query(5.0, ge=0.5, le=20),
                  hours_per_day: float = Query(6.0, ge=1, le=24),
                  baseline_cycle: float = Query(120.0, ge=60, le=150),
                  delay_model: str = Query("auto", pattern="^(auto|hcm|webster)$"),
                  sat_source: str = Query("per_lane"),
                  widths: str = Query("{}"),
                  theme: str = Theme):
    df = need_metrics()
    today = S.junction_demands(df)
    if not today:
        raise HTTPException(409, "No junction-mode cameras analysed yet.")
    today = S.with_widths(today, S.saturation_factor(sat_source),
                          _parse_widths(sat_source, widths))
    ids = [d.approach_id for d in today]
    if loss_approach and loss_approach not in ids:
        raise HTTPException(422, f"loss_approach must be one of {ids}")
    loss = {loss_approach: loss_pct / 100.0} if loss_approach and loss_pct else None
    scen = W.scenario(today, 1.0 + growth / 100.0, loss)

    def plans(dem):
        b = so.evaluate_fixed_plan(dem, cycle_s=baseline_cycle, delay_model=delay_model)
        o = so.optimise(dem, delay_model=delay_model)
        return b, o

    base_t, opt_t = plans(today)
    base_s, opt_s = plans(scen)
    curve = W.growth_curve(today, baseline_cycle, delay_model)
    shares = W.fleet_shares(df, ids)
    with T.use_mode(theme):
        chart = figure(C.growth_headroom(curve, W.PRACTICAL_X))

    def block(base, opt, dem):
        return {"headroom": W.headroom(dem, growth_per_year / 100.0),
                "base": _plan(base), "opt": _plan(opt), "compare": so.compare(base, opt),
                "worst_x_fixed": max(p.degree_of_saturation for p in base.phases),
                "worst_x_adaptive": max(p.degree_of_saturation for p in opt.phases)}

    return ok({
        "approaches": [{"id": d.approach_id, "short": short(d.camera_id),
                        "label": d.label} for d in today],
        "today": block(base_t, opt_t, today),
        "scenario": {**block(base_s, opt_s, scen), "growth_pct": growth,
                     "loss_approach": loss_approach, "loss_pct": loss_pct},
        "benefits": W.benefits(base_t, opt_t, today, shares, hours_per_day=hours_per_day),
        "curve": records(curve.round(4)),
        "practical_x": W.PRACTICAL_X,
        "chart": chart,
    })


# --------------------------------------------------------------------------
# Incidents — the alert workflow
# --------------------------------------------------------------------------

_SEV_ORDER = {"critical": 0, "warning": 1, "info": 2}
_STATUS_ORDER = {"open": 0, "acknowledged": 1, "resolved": 2, "cleared": 3}
_recorded_cache: tuple[int, list[dict]] = (0, [])


def _recorded_alerts(df: pd.DataFrame) -> list[dict]:
    """Alerts derived from the measured windows, with stable ids. Cached on
    the metrics frame itself, which is replaced whenever a CSV changes."""
    global _recorded_cache
    if _recorded_cache[0] == id(df) and _recorded_cache[1]:
        return _recorded_cache[1]
    out = []
    for a in bn.detect_all(df) if not df.empty else []:
        d = {**a.to_dict(), "camera_short": short(a.camera_id), "source": "recorded"}
        d["id"] = INC.alert_id(d)
        out.append(d)
    _recorded_cache = (id(df), out)
    return out


def _incident_items(df: pd.DataFrame | None = None) -> list[dict]:
    df = S.load_metrics() if df is None else df
    return INC.annotate(_recorded_alerts(df) + LIVE.alert_log())


@app.get("/api/incidents")
def incidents(status: str = Query("all", pattern="^(all|open|acknowledged|resolved|cleared)$"),
              source: str = Query("all", pattern="^(all|recorded|live)$")):
    items = _incident_items()
    counts = INC.counts(items)
    if status != "all":
        items = [i for i in items if i["status"] == status]
    if source == "recorded":
        items = [i for i in items if i.get("source", "recorded") == "recorded"]
    elif source == "live":
        items = [i for i in items if i.get("source") in ("replay", "detect")]
    # Open before handled. Within a status, live alerts first and newest
    # first; then recorded ones, most severe first, longest first.
    live = sorted((i for i in items if i.get("source") in ("replay", "detect")),
                  key=lambda i: str(i.get("raised_at", "")), reverse=True)
    rec = sorted((i for i in items if i.get("source") not in ("replay", "detect")),
                 key=lambda i: (_SEV_ORDER.get(i.get("severity"), 3),
                                -float(i.get("duration_s") or 0)))
    ordered = sorted(live + rec, key=lambda i: _STATUS_ORDER.get(i["status"], 3))
    return ok({"items": ordered, "counts": counts})


@app.post("/api/incidents/{iid}/action",
          dependencies=[Depends(limit("incident", 120, 30))])
def incident_action(iid: str, body: dict = Body(...), who: str = Depends(operator)):
    item = next((i for i in _incident_items() if i["id"] == iid), None)
    if item is None:
        raise HTTPException(404, "Unknown incident (a live alert from before a "
                                 "restart is only kept once acted on).")
    snapshot = {k: v for k, v in item.items()
                if k not in ("status", "history", "current", "active")}
    try:
        rec = INC.act(iid, str(body.get("action", "")), who,
                      str(body.get("note") or ""), snapshot=snapshot)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return ok({"id": iid, "status": rec["status"], "history": rec["history"]})


@app.get("/api/report/briefing.html", dependencies=[Depends(limit("report", 30, 10))])
def report_briefing(download: bool = False):
    """The printable network briefing (src/briefing.py)."""
    import briefing
    df = need_metrics()
    html = briefing.build(df, _incident_items(df))
    headers = {"Cache-Control": "no-cache"}
    if download:
        stamp = time.strftime("%Y%m%d")
        headers["Content-Disposition"] = f'attachment; filename="network_briefing_{stamp}.html"'
    return Response(html, media_type="text/html", headers=headers)


@app.get("/api/incidents.csv")
def incidents_csv():
    rows = INC.to_rows(_incident_items())
    return Response(pd.DataFrame(rows).to_csv(index=False), media_type="text/csv",
                    headers={"Content-Disposition":
                             'attachment; filename="incident_log.csv"'})


# --------------------------------------------------------------------------
# Camera locations — the network map
# --------------------------------------------------------------------------

LOCATIONS = cfg.CONFIG_DIR / "camera_locations.json"
SURAT_BOUNDS = (20.95, 21.35, 72.60, 73.00)       # lat_min, lat_max, lon_min, lon_max
_loc_lock = threading.Lock()


def _locations_raw() -> dict:
    try:
        with open(LOCATIONS, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


@app.get("/api/locations")
def locations():
    df = S.load_metrics()
    summary = fc.camera_summary(df) if not df.empty else pd.DataFrame()
    mean_ci = (dict(zip(summary.camera_id, summary.mean_congestion))
               if not summary.empty else {})
    raw = _locations_raw()
    cams = []
    for cid in cfg.CAMERAS:
        loc = raw.get(cid) if isinstance(raw.get(cid), dict) else None
        ci = mean_ci.get(cid)
        cams.append({
            "id": cid, "name": cfg.camera_name(cid), "short": short(cid),
            "mode": cfg.camera_meta(cid).get("mode", "area"),
            "lat": loc.get("lat") if loc else None,
            "lon": loc.get("lon") if loc else None,
            "verified": bool(loc and loc.get("verified")),
            "mean_congestion": ci, "los": T.los_of(ci) if ci is not None else None,
        })
    return ok({"cameras": cams, "note": raw.get("_note", ""),
               "bounds": {"lat": SURAT_BOUNDS[:2], "lon": SURAT_BOUNDS[2:]}})


@app.put("/api/locations/{cam}",
         dependencies=[Depends(operator), Depends(limit("locations", 60, 20))])
def set_location(cam: str, body: dict = Body(...)):
    if cam not in cfg.CAMERAS:
        raise HTTPException(404, f"Unknown camera '{cam}'.")
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "lat and lon must be numbers")
    if not (SURAT_BOUNDS[0] <= lat <= SURAT_BOUNDS[1]
            and SURAT_BOUNDS[2] <= lon <= SURAT_BOUNDS[3]):
        raise HTTPException(422, "That position is outside Surat.")
    with _loc_lock:
        raw = _locations_raw()
        raw[cam] = {"lat": round(lat, 6), "lon": round(lon, 6), "verified": True}
        cfg.write_json_atomic(LOCATIONS, raw)
    return ok({"id": cam, **raw[cam]})


# --------------------------------------------------------------------------
# Live monitoring
# --------------------------------------------------------------------------

import live as _live_mod  # noqa: E402

LIVE = _live_mod.HUB


@app.get("/api/live/state")
def live_state():
    LIVE.ensure_started()
    LIVE.touch()
    return ok(LIVE.snapshot())


@app.get("/api/live/events")
async def live_events(request: Request,
                      max_ticks: int = Query(0, ge=0, le=100000, alias="limit")):
    """Server-sent events: the whole live picture once a second, plus every
    alert raised or cleared since the last tick. Resumes from Last-Event-ID
    after a reconnect, so a dropped connection does not lose an alert."""
    LIVE.ensure_started()
    last = request.headers.get("last-event-id", "")
    start = int(last) if last.isdigit() else LIVE.seq

    async def gen():
        seq, n = start, 0
        yield "retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                break
            LIVE.touch()
            snap = await anyio.to_thread.run_sync(LIVE.snapshot)
            events, seq = LIVE.events_since(seq)
            data = json.dumps(clean({"state": snap, "events": events}),
                              separators=(",", ":"))
            yield f"id: {seq}\nevent: tick\ndata: {data}\n\n"
            n += 1
            if max_ticks and n >= max_ticks:
                break
            await anyio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _frame_source(cam: str):
    LIVE.ensure_started()
    try:
        return LIVE.frame_source(cam)
    except KeyError:
        raise HTTPException(404, f"No live feed for '{cam}'.")


@app.get("/api/live/stream.mjpg")
async def live_stream(request: Request, cam: str,
                      frames: int = Query(0, ge=0, le=100000)):
    """Motion-JPEG: an <img> plays it with no player and no codec questions."""
    src = _frame_source(cam)
    src.acquire()

    async def gen():
        last, n = 0, 0
        try:
            while True:
                if await request.is_disconnected():
                    break
                jpg, last = await anyio.to_thread.run_sync(src.wait, last, 2.0)
                if jpg is None:
                    continue
                LIVE.touch()
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                n += 1
                if frames and n >= frames:
                    break
        finally:
            src.release()

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/live/frame.jpg")
def live_frame(cam: str):
    src = _frame_source(cam)
    src.acquire()
    try:
        jpg, _ = src.wait(0, 8.0)
    finally:
        src.release()
    if jpg is None:
        raise HTTPException(503, "No frame yet — try again in a moment.")
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.post("/api/live/control",
          dependencies=[Depends(operator), Depends(limit("live", 60, 20))])
def live_control(body: dict = Body(...)):
    playing, speed = body.get("playing"), body.get("speed")
    if playing is not None and not isinstance(playing, bool):
        raise HTTPException(422, "playing must be true or false")
    try:
        state = LIVE.control(playing=playing,
                             speed=None if speed is None else float(speed),
                             restart=bool(body.get("restart")))
    except (TypeError, ValueError) as e:
        raise HTTPException(422, str(e))
    return ok(state)


@app.post("/api/live/detect",
          dependencies=[Depends(operator), Depends(limit("live-detect", 6, 3))])
def live_detect_start(body: dict = Body(...)):
    cam = body.get("camera")
    if cam not in cfg.CAMERAS:
        raise HTTPException(404, f"Unknown camera '{cam}'.")
    ok_det, path = S.detector_ready()
    if not ok_det:
        raise HTTPException(409, f"Detection weights not found at {path}. "
                                 f"Run `python scripts/download_models.py`.")
    if not cfg.camera_video_path(cam).exists():
        raise HTTPException(409, "That camera's footage is not on this machine.")
    if Z.load(cam) is None:
        raise HTTPException(409, "No zones are traced on this camera.")
    running = LIVE.detect
    if running is not None and running.running and running.cam != cam:
        raise HTTPException(409, f"Live detection is already running on "
                                 f"{running.info['short']}. Stop it first.")
    if running is not None and running.running:
        return ok({"session": running.view()})
    if not S.DETECTOR_LOCK.acquire(blocking=False):
        raise HTTPException(409, "The detector is busy with an analysis job. "
                                 "Try again when it finishes.")
    S.DETECTOR_LOCK.release()
    try:
        sess = LIVE.start_detect(cam)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return ok({"session": sess.view()})


@app.delete("/api/live/detect", dependencies=[Depends(operator)])
def live_detect_stop():
    sess = LIVE.stop_detect()
    return ok({"session": sess.view() if sess else None})


# --------------------------------------------------------------------------
# The built front end
# --------------------------------------------------------------------------

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"],
               include_in_schema=False)
def api_not_found(path: str):
    raise HTTPException(404, f"No API route /api/{path}")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Static assets by path; anything else is a client-side route."""
    if not WEB_DIST.exists():
        raise HTTPException(
            404, "The React front end has not been built. Run `npm run build` "
                 "in web/, or `npm run dev` there for development.")
    root = WEB_DIST.resolve()
    target = (root / path).resolve()
    if path and target.is_file() and root in target.parents:
        return FileResponse(target)
    return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})


def main():
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(description="Surat Traffic Intelligence API")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    uvicorn.run("api:app", host=a.host, port=a.port, reload=a.reload,
                app_dir=str(Path(__file__).resolve().parent))


if __name__ == "__main__":
    main()
