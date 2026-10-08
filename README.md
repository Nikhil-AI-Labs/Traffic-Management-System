<div align="center">

# 🚦 Surat Traffic Intelligence

**Data-Driven Traffic Optimization & Adaptive Infrastructure Intelligence**
E-Rakshak Hackathon 2026 · Problem Statement **PS-08**

Real-time lane density from CCTV · adaptive signal optimisation · bottleneck detection · predictive recommendations
**— with two signal controllers, each validated on data that can actually support it.**

<br>

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![PyTorch](https://img.shields.io/badge/PyTorch-2.5-EE4C2C?logo=pytorch&logoColor=white)
![YOLO11](https://img.shields.io/badge/YOLO11--S-UVH--26%20fine--tune-00b4d8)
![React](https://img.shields.io/badge/React%2019-FastAPI-61DAFB?logo=react&logoColor=white)
![Verification](https://img.shields.io/badge/verification-21%2F21%20passing-2ea44f)
![API tests](https://img.shields.io/badge/API%20tests-50%20passing-2ea44f)
![Standards](https://img.shields.io/badge/IRC-106%20%C2%B7%20SP%3A41%20%C2%B7%20103-1c5cab)

</div>

---

## The one-line architecture

> **Surat's junctions run Webster's. Max-pressure is validated on FLUID. Neither borrows from the other.**

Two controllers, two datasets, one strictly enforced boundary — a test fails if that ever stops being true.

| | **Webster's** | **Max-pressure** (Varaiya 2013) |
|---|---|---|
| **Needs to observe** | arrival flow on each approach | approach queue **and** the queue on the link it discharges into |
| **Cameras that requires** | one per approach | one per approach **plus one per receiving link**, or one aerial view |
| **Surat CCTV provides** | ✅ exactly this | ❌ no camera watches a receiving link |
| **Runs on** | the 7 Surat clips — **every operational figure here** | FLUID: 14 drone recordings of 3 real signalised intersections |
| **On Surat it reports** | cycle, splits, delay, saturation | `greedy-queue` — and **refuses to name a phase** |

Dropping the downstream term does not give a degraded max-pressure. It gives **greedy queue control**, which **Varaiya's §3.1 Example 2** proves fails to stabilise demands max-pressure handles. So rather than fake it, a dataset containing the missing term was obtained, and the controller is validated there.

---

## The dashboard

The sidebar's **control-strategy toggle switches the entire dashboard between two tracks.** Selecting a strategy changes which views exist — neither track can render the other's data.

### The control room — live, on the Surat network

<div align="center">

![Live monitor](docs/images/live-monitor.png)

*<b>Live monitor</b> — every camera at once on a street map of Surat, the focused camera's video, live zone states, alerts as they fire, and what is rising in the next minute*

</div>

- **Live monitor.** All seven cameras on one clock: each camera's recorded measurement windows are replayed as if arriving now, with the footage in step, and alerts fire **the moment their evidence completes** — quoting only what was known at that moment. One click switches a camera to **real-time detection**: YOLO11 + ByteTrack + the metrics engine at wall-clock speed, dropping frames rather than falling behind, and watching individual tracks for a **vehicle standing still in a running lane** (a breakdown or an illegal stop). Desktop notifications and an audible chime for critical alerts.
- **Incidents.** Every alert — recorded and live — can be **acknowledged** and **resolved** with a note; the log keeps who did what and when, survives restarts, and exports to CSV for the shift handover.
- **What-if on the signal plan.** *How long does the retiming last?* Webster's closed form gives the **reserve capacity**: the measured junction has **+8.2% demand headroom** before no timing can cope — **1.6 years at 5% annual growth**, and it is already past the 0.90 practical limit. Grow the demand or take capacity away from one approach (kerb parking, a stalled bus) and watch the best plan respond; the delay it saves is turned into vehicle-hours, fuel, CO₂ and rupees, with every assumption on screen.
- **Network map** with each camera coloured by level of service, and a **printable network briefing** — sites, plan, headroom, bottlenecks and the control-room log on a few A4 pages.
- **Operator sign-in** (`STI_OPERATOR_TOKEN`) for anything that changes state, and rate limits on everything heavy. Viewing never needs it.

<table>
<tr>
<td width="50%"><img src="docs/images/live-detection.png" alt="Live detection"></td>
<td width="50%"><img src="docs/images/signal-whatif.png" alt="Signal what-if"></td>
</tr>
<tr>
<td><b>Live detection</b> — YOLO11 + ByteTrack + the metrics engine on one camera in real time, lanes shaded by live level of service; the other six keep replaying</td>
<td><b>How long the retiming lasts</b> — reserve capacity from Webster's closed form, the saturation curve as demand grows, and capacity-loss scenarios</td>
</tr>
<tr>
<td colspan="2"><img src="docs/images/incidents.png" alt="Incidents"><br><b>Incidents</b> — acknowledge and resolve with a note; who did what and when, exported for the shift handover</td>
</tr>
</table>

### Track 1 — Webster's, on the Surat CCTV network

<div align="center">

![Network view](docs/images/webster-network.png)

*<b>Network</b> — the headline figures and every camera on Surat's street map, coloured by average level of service; below it, LOS bands per site and the congestion ranking on an absolute scale*

</div>

<table>
<tr>
<td width="50%"><img src="docs/images/webster-signal.png" alt="Signal timing"></td>
<td width="50%"><img src="docs/images/webster-site.png" alt="Site detail"></td>
</tr>
<tr>
<td><b>Signal timing</b> — the saturation crossing 1.01 → 0.93 and both delay models side by side; further down, cycle composition, the swept delay-vs-cycle curve and the what-if</td>
<td><b>Site detail</b> — zone tiles, scene overlay, movement heatmap, annotated video, per-zone trends, and the speed–density diagram</td>
</tr>
<tr>
<td><img src="docs/images/webster-bottleneck.png" alt="Bottlenecks"></td>
<td><img src="docs/images/webster-method.png" alt="How it works"></td>
</tr>
<tr>
<td><b>Bottlenecks</b> — every alert on a common clip clock, each carrying the evidence that raised it, a specific recommendation, and acknowledge/resolve</td>
<td><b>How it works</b> — the occlusion finding as a chart, the fleet composition, and what this data cannot support</td>
</tr>
</table>

**Ten views:** Live monitor · Network · Site detail · Signal timing (with what-if) · Bottlenecks · Incidents · Outlook · Analyse footage · Calibration · How it works

### Track 2 — Max-pressure, on the FLUID drone data

<div align="center">

![Validation](docs/images/mp-validation.png)

*<b>Validation</b> — max-pressure replayed second by second against three real signals*

</div>

<table>
<tr>
<td width="50%"><img src="docs/images/mp-scene.png" alt="Scene"></td>
<td width="50%"><img src="docs/images/mp-pressure.png" alt="Phase pressure"></td>
</tr>
<tr>
<td><b>Scene</b> — the orthorectified aerial with approach zones shaded by live queue, receiving links in grey, junction box outlined. <b>This is the picture the Surat cameras structurally cannot produce.</b></td>
<td><b>Phase pressure</b> — the ranking at one instant, with every movement's approach queue, receiving-link queue, turn ratio and weight</td>
</tr>
</table>

<div align="center">

![Head to head](docs/images/mp-headtohead.png)

*<b>Head-to-head</b> — Webster against max-pressure across three demand regimes in a point-queue simulator*

</div>

**Five views:** Validation · Scene · Phase pressure · Head-to-head · Method

---

## Results

### Surat — the operational system (Webster's)

| | Baseline (fixed timer) | Webster-optimal | |
|---|---:|---:|---|
| Cycle length | 120.0 s | **135.6 s** | |
| **Worst-approach saturation *x*** | **1.01 — over capacity** | **0.929** | **crosses back under 1.0** |
| Mean delay (HCM control delay) | 53.5 s/PCU | 51.4 s/PCU | −3.8% |
| **Headroom before no timing can cope** | | **+8.2% demand** | **≈1.6 years at 5%/yr growth** |

The saturation crossing is the claim that survives any delay model: equal green splits drive the busier approach past capacity, where the queue never clears within the cycle. The headroom row is the follow-up question — how long that fix lasts. At the 150 s maximum cycle every approach reaches capacity once the total flow ratio passes 0.947; today it is 0.875, and already past the 0.90 practical limit, so beyond roughly a year and a half the remedy is physical capacity, not retiming (checked against the optimiser in `verify_system.py`). Two independent checks confirm the optimiser — sweeping cycle length under Webster's own delay model puts the empirical delay minimum at **130 s** against Webster's closed form of **135.6 s** (under the HCM model, which bounds the over-capacity term, the minimum moves to 90 s — the dashboard shows both), and the optimal plan equalises degree of saturation across approaches (**0.929 / 0.929**), which is the known optimality condition for Webster green allocation.

**Network ranking** — 7 cameras, 21 zones, 2,451 measurement windows:

| Rank | Site | Mode | Mean congestion | Saturated windows |
|---|---|---|---:|---:|
| 1 | Mahidharpura – Diamond Mkt | area | **0.939** | 452 / 453 |
| 2 | Gopi Talav Gate | area | 0.749 | 167 / 456 |
| 3 | Central Bus Depo | area | 0.483 | 213 / 450 |
| 4 | Rokadiya Hanuman | junction | 0.437 | 0 / 244 |
| 5 | Chauta Bazaar | area | 0.205 | 4 / 453 |
| 6 | Rly Station | area | 0.099 | 45 / 302 |
| 7 | GauravPath | junction | 0.073 | 0 / 93 |

Plus **31 evidence-backed bottleneck alerts** (10 critical / 20 warning / 1 info) and **21 zone forecasts** with prediction intervals.

### FLUID — the max-pressure validation

Replaying max-pressure against what each real signal actually did:

| Intersection | Cycle | Phases | Real signal: green with **nothing to relieve** | Max-pressure | Pressure relieved |
|---|---:|---:|---:|---:|---:|
| **FI** (4-way, 3-phase) | 96 s | 3 | **41.1%** | 1.5% | 1.31 → **5.28** |
| **FIDRT** (4-way, dedicated right turns) | 123 s | 4 | **66.3%** | 2.3% | 0.27 → **4.87** |
| **TI** (T-intersection) | 71 s | 2 | **46.6%** | 16.0% | 0.64 → **1.30** |

These real fixed-time plans spend **41–66% of their green on a phase whose queue had already cleared** — or whose receiving link was fuller than the approach feeding it. Max-pressure, on the same measured queues, does so 1.5–16% of the time and relieves **2–18× more queue** per second of green.

That is an observation about the recorded signals, made from the recorded queues. It is deliberately **not** a delay-reduction claim: had max-pressure actually run, the queues would have evolved differently. The counterfactual needs a model:

| Scenario | Fixed-time | Max-pressure | Throughput | Spillback | Verdict |
|---|---:|---:|---:|---:|---|
| Steady, balanced | **18.6 s/PCU** | 20.8 s/PCU | −0.3% | 0 → 0 s | **fixed-time wins by 12%** |
| Demand swings between approaches | 24.5 s/PCU | **18.5 s/PCU** | +1.2% | 0 → 0 s | max-pressure **−25%** |
| Downstream blockage | 150.0 s/PCU | **102.9 s/PCU** | **+8.1%** | **289 → 88 s** | max-pressure **−31%** |

**The operator's decision rule** — narrower than the literature's enthusiasm suggests. Max-pressure earns its place when the *split* of demand moves between approaches, and above all when something downstream backs up. On steady demand it is the **worse** choice, because every extra switch pays an inter-green for a benefit that is not there. A system offering it as a straight upgrade would be selling the wrong thing.

---

## The two discoveries that shaped the design

### 1. In the worst gridlock, detection counts *fewer* vehicles, not more

| Camera | Detections | Actual condition |
|---|---:|---|
| Rokadiya Hanuman | **7,664** | flowing arterial |
| Central Bus Depo | 2,905 | busy |
| GauravPath | 1,957 | free-flowing |
| … | … | … |
| **Mahidharpura** | **463** | **worst gridlock in the dataset** |

At extreme density a wall of overlapping vehicles stops being separable and the detector's recall collapses. **A naive "more detections = more congestion" system would rank Surat's worst street among its emptiest — confidently, and backwards. Every number would look plausible.**

The fix is a texture-based occupancy index (Canny edge density, normalised against absolute bounds measured across all seven cameras) which has no notion of an object and so cannot be defeated by occlusion, plus an explicit saturation flag. Mahidharpura now flags **452 of 453 windows** as saturated and ranks correctly as the most congested site.

### 2. Only two of seven cameras can support the requested measurement

Five cameras watch market streets, a station forecourt and a bus depot — no lane discipline, no signal to optimise. "Lane density" is not a measurable quantity there, so forcing it would mean inventing numbers. Those run in **area mode** with functional zones (movement channel, encroachment, pedestrian area) and report occupancy and crowding instead.

---

## How it works

```
                         ┌─────────────────────────┐
   video (.avi/RTSP) ──▶ │  1. DETECT + TRACK      │  YOLO11-S (UVH-26) + ByteTrack
                         │     detector.py         │  → 14 Indian vehicle classes
                         └───────────┬─────────────┘
                         ┌───────────▼─────────────┐
                         │  2. LOCATE              │  lane / zone polygons,
                         │     zones.py            │  perspective scale model
                         └───────────┬─────────────┘
                         ┌───────────▼─────────────┐
                         │  3. MEASURE             │  two parallel paths
                         │     metrics.py          │
                         └───────────┬─────────────┘
                    ┌────────────────┴────────────────┐
        ┌───────────▼──────────┐         ┌────────────▼───────────┐
        │ DETECTION PATH       │         │ OCCUPANCY PATH         │
        │ PCU density, speed,  │         │ texture-based index,   │
        │ queue, flow (q=k·v)  │         │ survives saturation    │
        └───────────┬──────────┘         └────────────┬───────────┘
                    └────────────────┬────────────────┘
       ┌─────────────────────────────┼─────────────────────────────┐
┌──────▼────────┐        ┌───────────▼──────────┐       ┌──────────▼────────┐
│ 4. OPTIMISE   │        │ 5. DETECT BOTTLENECK │       │ 6. PREDICT        │
│ signal_opt.py │        │ bottleneck_detect.py │       │ forecast.py       │
│ Webster's     │        │ 5 sustained patterns │       │ damped trend +    │
│ maxpressure.py│        │ + recommendations    │       │ prediction band   │
└──────┬────────┘        └───────────┬──────────┘       └──────────┬────────┘
       └─────────────────────────────┼─────────────────────────────┘
                        ┌────────────▼─────────────┐
                        │  7. PRESENT              │
                        │  api.py → React (web/)   │
                        │  live.py: replay + live  │
                        │  detection, SSE + MJPEG  │
                        └──────────────────────────┘
```

**Standards, not guesses.** Density in **PCU per IRC:106-1990** — twelve two-wheelers and twelve buses are not the same amount of traffic. Saturation flow from **IRC:SP:41-1994** (`S = 525·W`, width-based, because lane count is precisely what this data cannot pin down). Pedestrian minimum green from **IRC:103-2012**, computed from each approach's own crossing width at a conservative 0.8 m/s.

**The detection weights matter more than the architecture.** UVH-26 is YOLO11 fine-tuned on 26,646 annotated frames of Indian Safe-City CCTV — the same domain as this footage — giving 14 India-specific classes including the auto-rickshaw, which **COCO does not have at all** and which dominates most of these clips.

**Flow is never counted from tracker IDs.** ByteTrack reissues IDs whenever a track drops briefly, which is constant in dense mixed traffic, so counting them inflated flow tenfold. Flow comes from the fundamental relation **q = k·v** instead, on directly measured density and speed.

---

## Quick start

**React dashboard** (recommended) — a FastAPI server computes every figure and serves the React front end:

```bash
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
python src/api.py                    # → http://127.0.0.1:8000
```

For front-end development, run `python src/api.py` and `npm run dev` (in `web/`) side by side and open http://localhost:5173 — Vite proxies `/api` to the Python server and hot-reloads the UI.

The app opens on the **Live monitor**, which replays the recorded network on a shared clock straight away. Real-time detection on a camera needs the full install below; on an Intel machine, `pip install openvino` and `python scripts/export_openvino.py` once give it an OpenVINO build of the same weights. Measured on the development laptop (i7-12700H with Iris Xe graphics) by that script's benchmark: **25 fps on the integrated GPU against 6 fps for PyTorch on the CPU**, detection counts within 0.7% — faster than the cameras' own 20 fps. The live monitor picks the GPU automatically when OpenVINO can see one. For any instance other people can reach, set an operator token — viewing stays open, changing anything needs it:

```bash
STI_OPERATOR_TOKEN=choose-a-long-secret python src/api.py --host 0.0.0.0
```

**Streamlit dashboard** (still supported, no Node.js needed):

```bash
pip install -r requirements.txt
streamlit run src/dashboard.py
```

Either way, the metrics CSVs and reference frames are committed, so **the Webster track works immediately** — no video download, no GPU, no model weights, and no torch. `requirements.txt` is the slim dashboard set for exactly that reason; it is also what a deployment installs. Point `FLUID_ROOT` at the extracted FLUID dataset to enable the max-pressure track.

Both dashboards read the same numbers through the same code: `src/services.py` is the shared data layer, `src/api.py` exposes it as JSON (interactive docs at `/api/docs`), and every chart is built once, in `src/charts.py`, then drawn by Plotly in either front end.

<details>
<summary><b>Full pipeline, from raw video</b></summary>

Analysing new footage needs the detector, so install the full set instead — **not both**, as they pin different OpenCV builds (`requirements-full.txt` explains why).

```bash
pip install -r requirements-full.txt
python scripts/download_models.py         # UVH-26 YOLO11 weights, ~55 MB
python src/reference_frames.py            # clean frame per camera
python src/seed_calibration.py            # first-pass lane / zone polygons
python src/calibrate.py rokadiya_hanuman  # (optional) refine by hand
python src/analyze.py --all --stride 4    # detect → track → measure → CSV
python src/traffic_heatmap.py --all       # where traffic actually goes
python src/render_demo.py --junctions     # annotated MP4s
streamlit run src/dashboard.py
```

Throughput is ≈12 FPS at 960 px on 1080p input on an RTX 3050 6 GB — faster than real time at stride 2.
</details>

<details>
<summary><b>Datasets — not in this repository</b></summary>

Neither dataset is committed: the Surat exports are the problem statement's own data, and FLUID is 19 GB with its own DOI.

**Surat CCTV** — 7 clips, 1920×1080 @ 20 fps, ≈31.5 min total. Place the `.avi` files in the project root.

**FLUID** — *A Fine-grained Lightweight Urban signalized-Intersection Dataset with Dense conflict trajectories.* Xuancheng, Anhui, China; DJI Mini 3 at 100–120 m; metre-scale trajectories, per-second signal state, per-vehicle turn annotation.
figshare DOI **`10.6084/m9.figshare.29974954`** · CC BY 4.0 · ≈18 GB.
Extract anywhere and set `FLUID_ROOT`, or drop it in the project root as `29974954/`.
</details>

<details>
<summary><b>Troubleshooting</b></summary>

- **No scene images** → `python src/reference_frames.py` has not been run; the overlays are drawn onto those frames.
- **"Model weights not found"** → `python scripts/download_models.py`. Every other view works without them, because they read the committed CSVs.
- **Max-pressure track says "FLUID not found"** → set `FLUID_ROOT` to wherever you extracted it.
- **`No start code is found.` in the terminal** → already suppressed. It was FFmpeg noting that H.264-in-AVI carries no parameter sets at the container boundary, then decoding correctly anyway.
</details>

---

## Verification

```bash
python tests/verify_system.py        # 21 checks
python -m pytest tests/test_api.py   # 50 API checks: every view, both themes, strict JSON,
                                     # sign-in, rate limits, incidents, what-if, live streams
python tests/e2e_pipeline.py         # detector flows end to end: uploads, calibration,
                                     # live detection (needs weights + videos, ~4 min on CPU)
```

GitHub Actions runs the first two (`verify_system.py --ci` skips the checks that need the 18 GB FLUID download or the videos) plus a lint, type-check and production build of the React app on every push.

Every figure quoted in this README and in `REPORT.md` is **recomputed and compared**, so the documentation cannot drift from the code. The suite also asserts the things that matter most:

- **The separation** — the Surat plan is recomputed under both controller settings and must come out byte-identical, and the string `maxpressure` must not appear in any of the nine measurement and analysis modules.
- **The observability gate** — max-pressure must *refuse* on Surat and *run* on FLUID.
- **Scale invariance** — multiplying every queue by 7.3 must leave the phase choice unchanged (it scales the pressure by exactly 7.30).
- **Simulator fairness** — both controllers must be charged the same lost time, and all three demand scenarios must genuinely differ from one another.
- **Geometry** — the world-to-pixel fit on every FLUID site must land within 5 px; a per-axis fit missed by 8 m on the site rotated 157°.
- **Physical sanity** — no zero-length measurement window, and no speed, density or occupancy outside its physical range.
- **Live replay is honest** — over one lap it raises exactly the 31 recorded alerts, each no earlier than its condition began.
- **Headroom agrees with the optimiser** — scaling demand by the reported reserve puts the best plan at exactly x = 1.000.

---

## Honest limitations

**The metric scale is not calibrated.** Converting pixels to metres needs one true ground reference — a surveyed width, camera intrinsics, or a landmark of known size. This dataset ships none, and three independent recovery attempts disagreed with each other by a factor of two to four. So the system separates what it **measures** from what it **estimates**:

- **Scale-free and trustworthy** — congestion index, level of service, site ranking, occupancy, lane comparison, and the entire signal-timing before/after comparison.
- **Scale-dependent estimates** — anything in km/h, PCU/km/lane or PCU/hr. Displayed with a `~` prefix behind a persistent banner.

Every headline result above is in the first category. One measured road width promotes the rest, and the **Calibration** view does it in about two minutes without a site visit.

**Live means two things, and the UI always says which.** *Replay* plays the recorded July 2026 windows and footage on a clock — labelled REPLAY on every surface. *Live detection* runs the real pipeline in real time, but on the recorded clips: no camera's RTSP feed was available, and a stream reader is the one piece not built. Camera positions on the map are placed from the camera names and marked ≈ until an operator drags them to the true spot.

**Other limits, stated rather than hidden.** Lane polygons are traced and heatmap-validated, not surveyed. The two junction cameras face different corridors rather than opposing approaches of one intersection, so the combined signal plan demonstrates the method on real measured demand rather than being deployable at a specific junction. Forecasting has minutes of history, not weeks. FLUID is Chinese traffic — it validates that the *controller implementation* is correct on data containing the terms it needs, and no claim from it transfers to Surat's fleet.

---

## Documentation

| Document | What it covers |
|---|---|
| **[`REPORT.md`](REPORT.md)** | **The full engineering report — every design decision, every bug found, every number derived** |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System architecture, the complete folder tree, the two measurement paths, the controller boundary, and the import graph |
| [`API.md`](API.md) | Python module API reference — every public entry point, with the observability contract |
| [`DATA_SCHEMA.md`](DATA_SCHEMA.md) | Every column of the metrics CSV, the camera/zone config, and the FLUID inputs |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | How to deploy, and what a hosted instance can and cannot show |
| [`ATTRIBUTION.md`](ATTRIBUTION.md) | Third-party libraries, datasets, pre-trained models and standards — and what is our own work |
| [`LICENSE`](LICENSE) | AGPL-3.0 — inherited from Ultralytics; see `ATTRIBUTION.md` §6 |

---

## Repository layout

The complete annotated tree is in [`ARCHITECTURE.md` §1](ARCHITECTURE.md#1-project-structure).
The parts worth knowing about:

| Path | Contents |
|---|---|
| `src/detector.py` · `zones.py` · `metrics.py` · `analyze.py` | Detection, tracking, zone geometry, and the measurement engine |
| `src/signal_opt.py` | Webster's cycle and splits, HCM delay, IRC saturation flow, pedestrian minima, controller recommender |
| `src/maxpressure.py` | Max-pressure with the observability gate that refuses rather than guess a missing downstream queue |
| `src/fluid.py` | FLUID adapter — queues, turn ratios, phase recovery, replay, and the aerial scene layer |
| `src/network_sim.py` | Point-queue simulator over two junctions in series |
| `src/bottleneck_detect.py` · `forecast.py` | Five sustained-pattern detectors; damped trend with prediction intervals |
| `web/` | React + TypeScript dashboard (Vite, TanStack Query, Plotly, Leaflet) — both tracks, 15 views, light/dark, mobile |
| `src/api.py` · `services.py` | FastAPI JSON API and the data layer shared by every front end and the tests |
| `src/live.py` | Live monitor: replay clock, real-time detection session, stalled-vehicle watch, SSE + MJPEG |
| `src/incidents.py` · `security.py` | Incident workflow log; operator sign-in and rate limits |
| `src/whatif.py` · `briefing.py` | Reserve capacity, demand/capacity scenarios, benefit estimate; the printable network briefing |
| `src/dashboard.py` · `theme.py` · `charts.py` | The Streamlit UI, the validated visual system, and every figure |
| `src/calibration.py` · `ingest.py` · `report_html.py` | Metric-scale calibration, uploaded-footage analysis, downloadable reports |
| `configs/cameras.json` · `camera_locations.json` | Camera registry — zones, polygons, scale models; map positions |
| `outputs/logs/` | 8 metrics CSVs, 2,451 windows — **committed**, so results reproduce without the video |
| `tests/` | `verify_system.py` 21 checks · `test_api.py` 50 API checks · `e2e_pipeline.py` detector flows |
| `.github/workflows/ci.yml` | CI: verification, API tests, React lint + build |
| `scripts/` | Model download; OpenVINO export + benchmark |
| `submission/` | Stage-2 deliverables and the scripts that generate them |

---

<div align="center">

**[📄 Read the full engineering report →](REPORT.md)**

*Built for E-Rakshak Hackathon 2026 · Problem Statement PS-08*

</div>
