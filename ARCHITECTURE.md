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
├── API.md                       Python module API reference
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
├── configs/
│   ├── cameras.json             Camera registry: zones, polygons, scale models
│   └── calibration_state.json   Operator state (generated, not committed)
│
├── src/                         27 modules · ~13,700 lines
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
│   └── download_models.py       Fetch UVH-26 weights from HuggingFace
│
├── tests/
│   └── verify_system.py         18 end-to-end checks
│
├── outputs/                     Pipeline output
│   ├── logs/                    8 metrics CSVs — COMMITTED, 2,451 windows
│   ├── frames/reference/        7 clean frames — COMMITTED
│   ├── frames/heatmap/          Movement heatmaps (generated)
│   ├── annotated/               Annotated MP4s (generated)
│   └── fluid_cache/             FLUID derived tables (generated)
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
