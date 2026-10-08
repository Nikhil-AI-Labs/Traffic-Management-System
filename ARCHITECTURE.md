# System Architecture

E-Rakshak Hackathon 2026 · PS-08 · *Surat Traffic Intelligence*

How the system is put together, and why it is put together that way. The full
engineering record — every decision, every bug, every derivation — is in
[`REPORT.md`](REPORT.md); this document is the structural view.

**Related:** [`API.md`](API.md) (programmatic interface) ·
[`DATA_SCHEMA.md`](DATA_SCHEMA.md) (data contract) ·
[`DEPLOYMENT.md`](DEPLOYMENT.md) (hosting)

---

## 1. Project structure

```
erakshak/
│
├── README.md                    Overview, quick start, results
├── REPORT.md                    Full engineering report
├── ARCHITECTURE.md              This document
├── API.md                       HTTP endpoints + Python module API reference
├── DATA_SCHEMA.md               Measurement + config data contract
├── DEPLOYMENT.md                Hosting instructions
├── ATTRIBUTION.md               Third-party components and licensing
├── LICENSE                      AGPL-3.0
├── requirements.txt             Slim set — dashboard and deployment
├── requirements-full.txt        Full set — detection pipeline
│
├── .streamlit/
│   └── config.toml              Theme, validated for both light and dark
│
├── .github/workflows/ci.yml     CI: verification, API tests, React lint + build
│
├── configs/
│   ├── cameras.json             Camera registry: zones, polygons, scale models
│   ├── camera_locations.json    Map positions (approximate until confirmed)
│   └── calibration_state.json   Operator state (generated, not committed)
│
├── src/                         34 modules · ~17,500 lines
│   │
│   │  ── Measurement ──────────────────────────────────────────────
│   ├── config.py                Camera registry, PCU factors, LOS bands, paths
│   ├── detector.py              YOLO11 + ByteTrack wrapper
│   ├── zones.py                 Zone geometry, perspective scale model
│   ├── metrics.py               The measurement engine — both paths
│   ├── analyze.py               Pipeline runner: detect → measure → CSV
│   │
│   │  ── Control ─────────────────────────────────────────────────
│   ├── signal_opt.py            Webster's cycle/splits, HCM delay, IRC standards
│   ├── maxpressure.py           Max-pressure + the observability gate
│   ├── network_sim.py           Point-queue simulator, two junctions in series
│   ├── fluid.py                 FLUID adapter: queues, turns, replay, scene
│   │
│   │  ── Analysis ────────────────────────────────────────────────
│   ├── bottleneck_detect.py     Five sustained-pattern detectors
│   ├── forecast.py              Damped trend with prediction intervals
│   │
│   │  ── Calibration ─────────────────────────────────────────────
│   ├── seed_calibration.py      First-pass zone polygons
│   ├── calibrate.py             Interactive polygon tracer (needs GUI OpenCV)
│   ├── calibration.py           Metric-scale calibration from a known width
│   ├── calibrate_scale.py       Scale recovery experiments
│   ├── calibrate_occupancy.py   Absolute occupancy bounds across all cameras
│   ├── apply_scale.py           Write a verified scale into calibration state
│   │
│   │  ── Service layer ───────────────────────────────────────────
│   ├── api.py                   FastAPI JSON API; serves the React build
│   ├── services.py              Data layer shared by every front end + tests
│   ├── live.py                  Live monitor: replay clock, real-time detection,
│   │                            stalled-vehicle watch, SSE + MJPEG streams
│   ├── incidents.py             Incident workflow log (acknowledge / resolve)
│   ├── security.py              Operator sign-in, rate limits
│   ├── whatif.py                Reserve capacity, scenarios, benefit estimate
│   ├── briefing.py              Printable network briefing
│   │
│   │  ── Presentation ────────────────────────────────────────────
│   ├── dashboard.py             Two-track Streamlit UI, 13 views
│   ├── charts.py                Every figure
│   ├── theme.py                 Colour system, validated for colour vision
│   ├── report_html.py           Downloadable HTML reports
│   ├── ingest.py                Uploaded-footage analysis
│   │
│   │  ── Utilities ───────────────────────────────────────────────
│   ├── reference_frames.py      One clean frame per camera
│   ├── traffic_heatmap.py       Movement density, validates traced polygons
│   ├── render_demo.py           Annotated MP4s
│   ├── validate_detection.py    Detector spot-checks
│   └── measurement_guide.py     In-dashboard measurement explainer
│
├── scripts/
│   ├── download_models.py       Fetch UVH-26 weights from HuggingFace
│   └── export_openvino.py       OpenVINO export + PyTorch/OpenVINO benchmark
│
├── tests/
│   ├── verify_system.py         21 end-to-end checks (--ci skips data-bound ones)
│   ├── test_api.py              50 API checks
│   └── e2e_pipeline.py          Detector flows end to end, incl. live detection
│
├── web/                         React 19 + TypeScript front end (Vite)
│   └── src/pages/               Live · Network · Site · Signal (+ WhatIf) ·
│                                Bottlenecks · Incidents · Outlook · Analyse ·
│                                Calibration · Method · fluid/ (5 views)
│
├── outputs/                     Pipeline output
│   ├── logs/                    8 metrics CSVs — COMMITTED, 2,451 windows
│   ├── frames/reference/        7 clean frames — COMMITTED
│   ├── frames/heatmap/          Movement heatmaps (generated)
│   ├── annotated/               Annotated MP4s (generated)
│   ├── fluid_cache/             FLUID derived tables (generated)
│   └── incidents.json           Incident log (generated, not committed)
│
├── docs/images/                 9 dashboard screenshots
│
├── submission/                  Stage-2 deliverables
│   ├── build_deck.py            Generates the 15-slide deck
│   ├── build_report.py          Generates the standalone report PDF
│   └── VIDEO_SCRIPT.md          2:45 demo script
│
└── models/weights/              UVH-26 weights (downloaded, not committed)
```

**What is committed and why.** The metrics CSVs and reference frames are in the
repository so the entire Webster track runs immediately — no video download, no
GPU, no model weights. Source video (31.5 min), FLUID (18 GB) and model weights
(55 MB) are not committed; see [`README.md`](README.md) for how to obtain each.

---

## 2. The pipeline

```
   video (.avi / RTSP)
            │
            ▼
   ┌──────────────────────┐
   │ 1. DETECT + TRACK    │   detector.py
   │                      │   YOLO11-S (UVH-26) + ByteTrack → 14 classes
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ 2. LOCATE            │   zones.py
   │                      │   lane / zone polygons, perspective scale model
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ 3. MEASURE           │   metrics.py
   └──────────┬───────────┘
              │
      ┌───────┴────────┐
      ▼                ▼
┌───────────┐   ┌──────────────┐
│ DETECTION │   │ OCCUPANCY    │   ← two parallel paths, §3
│ PATH      │   │ PATH         │
└─────┬─────┘   └──────┬───────┘
      └───────┬────────┘
              ▼
   ┌──────────────────────────────────────────────┐
   │  4. OPTIMISE      5. BOTTLENECKS   6. PREDICT│
   │  signal_opt.py    bottleneck_      forecast  │
   │  maxpressure.py   detect.py        .py       │
   └──────────────────────┬───────────────────────┘
                          ▼
              ┌───────────────────────┐
              │  7. PRESENT           │   dashboard.py
              │  two tracks, 13 views │
              └───────────────────────┘
```

Stages 1–3 write one `ZoneWindow` row per zone per 2-second window to
`outputs/logs/`. Stages 4–7 read those rows. That boundary is what makes the
dashboard deployable without a detector.

---

## 3. The two measurement paths

The central design decision. One metric cannot honestly cover both scene types
in this dataset.

| | Detection path | Occupancy path |
|---|---|---|
| **Produces** | PCU density, speed, queue, flow | Texture-based occupancy index |
| **Valid while** | individual vehicles remain separable | always |
| **Method** | per-object, PCU-weighted per IRC:106-1990 | Canny edge density inside the zone |
| **Fails when** | the scene saturates and recall collapses | — |
| **Scale-dependent** | yes (density, speed) | no |

Which path leads is decided **per zone, per window** by a saturation check, and
the active mode is carried in the output (`measurement_mode`) so the dashboard
can be explicit about it.

The saturation check uses two independent signatures, either sufficient:

1. **Crowded but resolvable** — high object density *and* heavy mutual overlap.
   Requiring both keeps busy-but-flowing lanes, where counting is fine, from
   being flagged.
2. **Full scene, empty count** — texture says the zone is packed while detection
   boxes cover almost none of it. **Only this test catches the collapse case**,
   because once detection fails there are too few boxes left to overlap, so
   signature 1 goes quiet exactly when the problem is worst.

Occupancy is normalised against **absolute** bounds shared by every camera, not
per-zone bounds. Per-zone normalisation would make each zone its own yardstick:
an empty carriageway would stretch its own noise across the full 0–1 scale, and
a permanently packed street would report "clear" at its own quietest moment.
Cross-site comparison — which is what the network ranking is — would become
meaningless.

---

## 4. The controller boundary

Two signal controllers, and the boundary between them is architectural rather
than a runtime preference.

| | Webster's | Max-pressure |
|---|---|---|
| Module | `signal_opt.py` | `maxpressure.py` |
| Observes | arrival flow per approach | approach queue **and** receiving-link queue |
| Dataset | the 7 Surat clips | FLUID drone recordings |
| On Surat | full plan | reports `greedy-queue`, refuses to name a phase |

**How the boundary is enforced.** `MaxPressureDecision` keeps the max-pressure
answer (`chosen_*`) and the greedy-queue answer (`greedy_*`) in *separate
fields*. When the downstream term is unobserved, `chosen_phase_id` is `None` and
the weaker answer is only reachable under a name that says what it is. A caller
cannot render greedy-queue output as max-pressure by accident — it has to ask
for it explicitly.

This is enforced by test, not convention. `tests/verify_system.py` asserts:

- the Surat signal plan is **byte-identical** under both controller settings;
- the string `maxpressure` does not appear in any of the nine measurement and
  analysis modules;
- max-pressure **refuses** on Surat and **runs** on FLUID.

---

## 5. Data flow and state

```
configs/cameras.json ──► zones.py ──┐
                                     ├──► metrics.py ──► outputs/logs/*.csv
video ──► detector.py ──────────────┘                          │
                                                                ▼
configs/calibration_state.json ◄──── dashboard.py ◄──── signal_opt / bottleneck
        (operator state)                                 detect / forecast
```

There is **no database and no server-side session store**. State is:

| State | Lives in | Lifetime |
|---|---|---|
| Measurements | `outputs/logs/*.csv` | permanent, committed |
| Zone geometry | `configs/cameras.json` | permanent, committed |
| Operator settings | `configs/calibration_state.json` | persists across restarts |
| Uploaded-clip cameras | `config.SESSION_CAMERAS` | process lifetime |
| Computed views | Streamlit `@st.cache_data` | process lifetime |

See [`DATA_SCHEMA.md`](DATA_SCHEMA.md) for the full contract.

---

## 6. Import graph, and why it matters

```
dashboard.py
  ├── bottleneck_detect, forecast, signal_opt, charts, theme, zones, calibration
  ├── ingest ──► metrics ──► detector ──► [ultralytics, torch]   ← LAZY
  └── fluid, maxpressure                                          ← lazy, per-view
```

`detector.py` imports `ultralytics` **inside** `VehicleDetector.__init__`, not at
module scope. `Detection` and `FrameResult` are plain dataclasses that `metrics`
and `ingest` depend on, and `dashboard` imports `ingest` — so a module-level
import would drag ~2 GB of deep-learning stack into every page load, for a
dashboard that reads committed CSVs and never constructs a detector.

That single placement is what allows `requirements.txt` to exclude torch, and
therefore what allows the app to run on a free tier. It is documented in
`DEPLOYMENT.md` §1 as a constraint, not an optimisation.

`fluid` and `maxpressure` are imported per-view so the Webster track has no
dependency on the FLUID dataset being present.

---

## 7. Presentation layer

The dashboard is split into two **tracks** selected by a sidebar control. The
selection changes which views exist — neither track can render the other's data,
because the two controllers have nothing to say about each other's datasets.

| Track 1 — Webster's · Surat CCTV | Track 2 — Max-pressure · FLUID |
|---|---|
| Network · Site detail · Signal timing · Bottlenecks · Outlook · Analyse footage · Calibration · How it works | Validation · Scene · Phase pressure · Head-to-head · Method |

Two rules hold throughout:

- **Every chart has a table beside it**, so no value is reachable only by
  hovering or only by colour.
- **Every figure depending on the uncalibrated pixel scale is prefixed `~`** and
  sits behind a persistent banner. The ranking, level of service and the signal
  comparison do not depend on it and are presented without the caveat.

---

## 8. Failure behaviour

Missing assets degrade to an explanation rather than a crash or a blank. This is
asserted by `tests/verify_system.py`, not left to chance.

| Missing | Behaviour |
|---|---|
| Model weights | `detector_ready()` returns False; the upload view shows an explanatory notice |
| FLUID dataset | Max-pressure views render a "Real-intersection data not found" callout |
| Heatmap images | Panel shows the command that generates them |
| Reference frames | Scene overlays are skipped; every other view is unaffected |
| Metrics CSVs | Dashboard reports an empty dataset rather than raising |

---

## 9. The React front end and the JSON API

```
web/ (React + TS)  ──HTTP /api──►  src/api.py (FastAPI)  ──►  src/services.py  ──►  analysis modules
   TanStack Query                     charts as Plotly JSON        data layer:            signal_opt, fluid,
   react-plotly                       background jobs               metrics, images,       bottleneck_detect,
   light / dark                       serves web/dist               FLUID tables           forecast, ...
                                            ▲
src/dashboard.py (Streamlit) ───────────────┘ same services.py, same charts.py
```

**One source of truth for every number.** The data helpers that used to live
inside `dashboard.py` moved to `services.py`, which imports no UI framework.
The API, the Streamlit dashboard and both test suites all call it, so they
cannot drift apart. Its caches are keyed on file modification times, so a
re-measured camera or a kept upload appears on the next request without a
restart.

**Charts are built once.** `charts.py` stays the only place a figure is
specified. The API builds each figure in the viewer's theme (`theme.use_mode`,
a context variable, so concurrent requests in different themes do not
interfere) and ships it as Plotly JSON; the React app draws it with
`react-plotly`. The colour-vision-checked palette is not re-implemented.

**Long work runs as jobs.** Re-measuring a camera, finding the road in an
upload and analysing a clip run in background threads and are polled through
`/api/jobs/{id}`. The detector is one shared model behind a lock, because
ByteTrack keeps its state on the model.

**Strict JSON.** Webster's delay is genuinely infinite above capacity; the API
converts NaN and infinity to `null`, and the UI shows "∞ (over capacity)" or
"—" with the reason rather than a number. `tests/test_api.py` asserts that no
payload contains a non-finite value.

**Deployment.** `npm run build` writes `web/dist/`, which `api.py` serves from
`/` with client-side routing, so one Python process is the whole application.
The Streamlit dashboard remains available for environments without Node.js.

---

## 10. Live monitoring

```
                    ┌──────────────── src/live.py ─────────────────┐
committed CSVs ───► │ ReplayFeed ×7    one shared clock (play/pause, │
                    │                  0.5–10×), alerts scheduled at │──► GET /api/live/events
                    │                  the end of their 3rd window   │    (SSE, 1 tick/s + events)
footage / render ─► │ ReplayFrames     decoded once per camera,      │
                    │                  shared by every viewer        │──► GET /api/live/stream.mjpg
detector ─────────► │ DetectSession    YOLO11 + ByteTrack + metrics  │    (MJPEG, <img> plays it)
                    │                  at wall-clock pace; stalled-  │
                    │                  vehicle watch per track       │──► incidents (acknowledge,
                    └────────────────────────────────────────────────┘     resolve, CSV log)
```

**Two sources, one picture.** Every camera's recorded windows replay on one
clock, as if arriving now. One camera at a time can instead run the real
pipeline: `DetectSession` reads the clip at wall-clock speed, processes the
newest frame it has time for and *skips* the rest — so the picture is always
current and the frame rate reported is the one achieved. While it runs, that
camera's tile, zones, alerts and video come from detection.

**Alerts that do not know the future.** A replayed alert fires when its
evidence completes — the end of its third sustained window — and quotes
`bottleneck_detect` run on the windows up to that instant, not on the whole
clip. `verify_system.py` asserts that one lap raises exactly the 31 recorded
alerts, none before its condition began. Live detection re-runs the same
detectors on its own rolling windows every time a window closes, and adds one
the zone averages cannot see: a track that has stood still for 20 s in a lane
whose traffic is flowing.

**Streams that cost nothing per viewer.** The SSE snapshot is computed at most
once per 0.4 s however many screens are open; each camera's frames are decoded
and encoded once and fanned out through a condition variable. A replay source
outlives its last viewer by eight seconds, and live detection stops itself when
nobody has watched for ninety, freeing the detector for analysis jobs. GZip is
switched off for both stream types — it would hold frames back until it had
enough to compress.

**Fallbacks.** Without footage on disk — the hosted case — the replay shows the
camera's reference frame with its zones shaded by the replayed level of
service. Without detector weights, the live-detection button is simply absent.

## 11. Incidents, sign-in, what-if

**Incidents** (`src/incidents.py`). Alerts are derived, so they carry no state;
the incident log is the state — an append-only history per alert id, written
atomically to `outputs/incidents.json`. Recorded alerts have stable ids
(`camera|zone|kind|start`), so an acknowledgement survives restarts; a live
alert's details are snapshotted into its entry when it is first acted on.

**Sign-in** (`src/security.py`). One shared operator token
(`STI_OPERATOR_TOKEN`), compared in constant time, guards every endpoint that
changes state or starts heavy work; reading is always open. Per-client token
buckets rate-limit uploads, analyses, simulator runs and sign-in attempts, and
the detector queue is capped at three jobs.

**What-if** (`src/whatif.py`). Reserve capacity straight from Webster's closed
form — at the longest acceptable cycle the junction stays workable while
Y ≤ x·(C−L)/C — using *uncapped* flow ratios, because the optimiser's
per-approach clip at 0.95 would otherwise report headroom that is not there.
Scenarios scale demand or cut one approach's saturation flow and re-run the
same optimiser; the benefit estimate turns the delay difference into
vehicle-hours, idling fuel, CO₂ and rupees with every assumption returned
alongside the result.
