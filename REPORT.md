# Surat Traffic Intelligence — Full Project Report

**E-Rakshak Hackathon 2026 · Problem Statement PS-08**
**Data-Driven Traffic Optimization & Adaptive Infrastructure Intelligence**

---

## Table of contents

1. [What the problem statement asks for](#1-what-the-problem-statement-asks-for)
2. [What we were given, and what we went and got](#2-what-we-were-given-and-what-we-went-and-got)
3. [What we built — the solution](#3-what-we-built--the-solution)
4. [How it works, stage by stage](#4-how-it-works-stage-by-stage)
5. [The two discoveries that shaped the design](#5-the-two-discoveries-that-shaped-the-design)
6. [Results](#6-results)
   - [6A — Surat, the operational system (Webster's)](#6a-surat--the-operational-system-websters)
   - [6B — FLUID, the max-pressure validation](#6b-fluid--the-max-pressure-validation)
7. [Technology used and why](#7-technology-used-and-why)
8. [Bugs found and fixed during development](#8-bugs-found-and-fixed-during-development)
9. [Honest limitations](#9-honest-limitations)
10. [Every file, and what it does](#10-every-file-and-what-it-does)
11. [How to run it](#11-how-to-run-it)

---

## 1. What the problem statement asks for

The source document is `Existing Solutions.docx`. It defines PS-08 as:

> **Real-time lane density from CCTV, adaptive signal cycle optimization, bottleneck detection, and predictive engineering recommendations.**

That is four connected deliverables, each building on the one before:

| # | Requirement | In plain terms |
|---|---|---|
| 1 | **Real-time lane density from CCTV** | Watch a live camera feed and continuously measure how crowded each lane is |
| 2 | **Adaptive signal cycle optimization** | Use that measurement to compute better traffic-light timings than a fixed timer |
| 3 | **Bottleneck detection** | Automatically find *where* and *when* congestion is forming |
| 4 | **Predictive engineering recommendations** | Look ahead, and suggest fixes — retiming, lane changes, enforcement |

The document also surveys what already exists on the market (ATSAC in Los Angeles, "Passable" intelligent signals, various YOLOv8/v11 research systems) and ends with a **Gap Analysis**. That gap analysis is the most important part of the brief, because it tells us what would actually count as a contribution:

> - Retrofitting to existing fixed-timer infrastructure is challenging
> - Integration with ATCS/junction controllers requires proprietary APIs
> - **Surat-specific data not available in commercial platforms**

So the brief is not "reimplement ATSAC". It is: **build something that works on Surat's real traffic, which commercial systems have never been trained on or tuned for.** Indian traffic is heterogeneous and lane-indisciplined in a way Western systems do not model. That is the target.

---

## 2. What we were given, and what we went and got

Two datasets, used for two different and strictly separated purposes. Confusing them is the single easiest way to misread this project, so they are separated here first.

| | **Surat CCTV** — the problem statement's data | **FLUID** — obtained to validate the second controller |
|---|---|---|
| **Source** | Surat traffic camera network, VMS site `MSTSRV76` | figshare DOI `10.6084/m9.figshare.29974954`, CC BY 4.0 |
| **What it is** | 7 fixed CCTV clips, 1920×1080, 20 fps, H.264 | 14 drone recordings of 3 signalised intersections, DJI Mini 3 at 100–120 m |
| **Volume** | ≈31.5 min | 305 min, 20,860 vehicle trajectories |
| **Labels** | **none** — XML files are export manifests, not annotations | metre-scale trajectories, per-second signal state, per-vehicle entry/exit direction |
| **View** | one approach per camera | the whole intersection in one aerial frame |
| **Controller it supports** | **Webster's** | **max-pressure** |
| **What it produces here** | every operational figure in this report: lane density, congestion ranking, signal timing, bottlenecks, forecasts | one thing only: proof that the max-pressure implementation works on data containing what it requires |

**Why two.** PS-08 is a Surat problem and Surat is where the system runs. But a traffic-management platform that offered only one control strategy would be answering half the question: fixed-time optimisation is the right tool for an isolated approach and the wrong one for a congested network where links back up. So the system implements both — and because a controller you cannot demonstrate is a claim rather than a feature, a dataset was obtained specifically so the second one could be *validated* rather than merely asserted.

**The separation is enforced, not just intended.** No Surat number is produced by max-pressure; no FLUID number feeds a Surat result. `tests/verify_system.py` fails if either becomes untrue.

### 2.1 The Surat CCTV data

**Technical shape:** every clip is 1920×1080, 20 fps, H.264. Total ≈ 31.5 minutes.

**Critically: there are no labels.** The XML files are export manifests (camera name, timestamp, resource ID) — not annotations. There are no bounding boxes, no ground-truth counts, no lane definitions, no speeds. Everything the system knows, it derives from raw pixels.

The `Gauravpath/Review/` folder is a bundled third-party video player (DLLs, DevExpress components, VC++ redistributables) shipped alongside the export. It is **not** data and is ignored entirely.

#### The seven cameras split into two very different kinds of scene

This split turned out to drive the entire architecture.

| Camera | Duration | Scene | Mode |
|---|---|---|---|
| **GauravPath – Towards Kargil Chowk** | 1:02 | Divided arterial, clear lane markings, free-flowing | **junction** |
| **Rokadiya Hanuman – Towards Bhatena** | 2:01 | Divided arterial, 3 lanes, moderate–heavy mixed traffic, roadworks | **junction** |
| Chauta Bazaar | 5:01 | Dense market street, shared pedestrian/vehicle space | area |
| Mahidharpura – Diamond Mkt | 5:01 | Near-total gridlock, wall of people and parked two-wheelers | area |
| Gopi Talav Gate | 5:06 | Recreational gate, pedestrian-dominant, vendor stalls | area |
| Rly Station – Bismillah Rest | 5:01 | Station forecourt, auto-rickshaw pickup/drop | area |
| Central Bus Depo | 5:00 | GSRTC depot platform, bus manoeuvring plus crowds | area |

**Only two of seven cameras look at a structured, lane-marked road.** The other five are market streets, a station plaza and a bus depot. They have no lane discipline and no traffic signal to optimise.

This matters enormously. "Lane density" is not a measurable quantity in a market street where there are no lanes. Forcing that measurement onto those cameras would mean inventing numbers. Instead the system runs them in a separate **area mode** with functional zones (movement channel, encroachment, pedestrian area) and reports occupancy and crowding — measurements the scene can actually support.

**And the two junction cameras face different corridors**, not opposing approaches of one intersection. That single fact is why max-pressure cannot run on this data: there is no intersection to observe, no receiving link, and no measurable turn ratio. See §4b.

### 2.2 The FLUID drone data

Obtained after establishing that no Surat camera observes a receiving link, and therefore that max-pressure could be implemented but not demonstrated on the problem statement's own footage.

| Site | Geometry | Phases | Cycle | Recordings |
|---|---|---:|---:|---:|
| `FI` | 4-way intersection | 3 | 96 s | 8 |
| `FIDRT` | 4-way with dedicated right turns | 4 | 123–142 s | 3 |
| `TI` | T-intersection | 2 | 71 s | 3 |

Recorded in Xuancheng, Anhui, China. Each intersection sits whole in one aerial frame, which is exactly what makes the downstream queue observable. The dataset ships metre-scale world coordinates already rectified — so unlike the Surat footage it needs no scale calibration, and every distance in the max-pressure validation is a true metre.

**A note on transferability.** FLUID is Chinese traffic: right-hand drive, a fleet of cars, mopeds and tricycles, and lane discipline that Surat does not have. It is used here to validate that *the controller implementation is correct on data containing the terms it needs* — not to claim Surat's traffic behaves like Xuancheng's. The Indian-specific work (UVH-26 detection, IRC PCU factors, IRC:SP:41 saturation flow, IRC:103 pedestrian minima) all sits on the Surat side, where it belongs.

---

## 3. What we built — the solution

A complete, working pipeline that takes raw CCTV video and produces live traffic intelligence, plus an operator dashboard.

```
                         ┌─────────────────────────┐
   video (.avi/RTSP) ──▶ │  1. DETECT + TRACK      │  YOLO11-S (UVH-26) + ByteTrack
                         │     detector.py         │  → 14 Indian vehicle classes,
                         └───────────┬─────────────┘    stable IDs across frames
                                     │
                         ┌───────────▼─────────────┐
                         │  2. LOCATE              │  lane / zone polygons,
                         │     zones.py            │  perspective scale model
                         └───────────┬─────────────┘
                                     │
                         ┌───────────▼─────────────┐
                         │  3. MEASURE             │  two parallel measurement paths
                         │     metrics.py          │
                         └───────────┬─────────────┘
                    ┌────────────────┴────────────────┐
        ┌───────────▼──────────┐         ┌────────────▼───────────┐
        │ DETECTION PATH       │         │ OCCUPANCY PATH         │
        │ PCU density, speed,  │         │ texture-based index,   │
        │ queue, flow (q=k·v)  │         │ survives saturation    │
        │ used when countable  │         │ used when detection    │
        │                      │         │ collapses              │
        └───────────┬──────────┘         └────────────┬───────────┘
                    └────────────────┬────────────────┘
                                     │
       ┌─────────────────────────────┼─────────────────────────────┐
       │                             │                             │
┌──────▼────────┐        ┌───────────▼──────────┐       ┌──────────▼────────┐
│ 4. OPTIMISE   │        │ 5. DETECT BOTTLENECK │       │ 6. PREDICT        │
│ signal_opt.py │        │ bottleneck_detect.py │       │ forecast.py       │
│ Webster's     │        │ sustained patterns   │       │ damped trend +    │
│ cycle+splits  │        │ + recommendations    │       │ prediction band   │
└──────┬────────┘        └───────────┬──────────┘       └──────────┬────────┘
       └─────────────────────────────┼─────────────────────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │  7. PRESENT              │
                        │     dashboard.py         │  Streamlit operator UI
                        │     render_demo.py       │  annotated MP4 for demo
                        └──────────────────────────┘
```

### Mapping back to the four requirements

| PS-08 requirement | How it is delivered | Dataset | Where |
|---|---|---|---|
| Real-time lane density | Per-lane PCU density (IRC:106-1990) + occupancy index, per 2-second window | Surat | `metrics.py` |
| Adaptive signal optimization | **Webster's method** on measured approach demand, with before/after delay comparison | Surat | `signal_opt.py` |
| Bottleneck detection | Five detectors for sustained patterns: congestion, standing queue, saturation, encroachment, lane imbalance | Surat | `bottleneck_detect.py` |
| Predictive recommendations | Damped trend forecast with prediction interval + evidence-backed templated recommendations | Surat | `forecast.py`, `bottleneck_detect.py` |

All four PS-08 deliverables are met on the Surat data, by Webster's. The second controller is additional capability, validated separately:

| Beyond the brief | How it is delivered | Dataset | Where |
|---|---|---|---|
| Networked signal control | **Max-pressure** (Varaiya 2013) with an observability gate that refuses rather than guess a missing downstream queue | **FLUID** | `maxpressure.py`, `fluid.py` |
| Controller counterfactual | Point-queue simulator over two junctions in series, three demand regimes | Surat demand, constructed topology | `network_sim.py` |
| Which controller to run | Recommender driven by observability and measured demand-split variability | Surat | `signal_opt.recommend_controller()` |

---

## 4. How it works, stage by stage

### Stage 1 — Detection and tracking

Every processed frame goes through **YOLO11-S** to find vehicles, then **ByteTrack** to give each vehicle a stable ID across frames so it can be followed.

The model is not stock YOLO. It uses **UVH-26** weights — YOLO11 fine-tuned on 26,646 annotated 1080p images from Indian Safe-City traffic cameras. This matters because stock COCO-trained YOLO has **no auto-rickshaw class at all**, and the auto-rickshaw is one of the most common vehicles in this footage. UVH-26 gives 14 India-specific classes:

`Hatchback · Sedan · SUV · MUV · Bus · Truck · Three-wheeler · Two-wheeler · LCV · Mini-bus · Tempo-traveller · Bicycle · Van · Others`

Each detection's **road-contact point** is its bounding-box bottom-centre — not its centroid. Using the centroid would place tall vehicles (buses, trucks) well above their actual position on the road and assign them to the wrong lane on an angled camera view.

### Stage 2 — Locating vehicles in lanes and zones

Each camera has hand-traced polygons stored in `configs/cameras.json`:
- **Junction cameras** get lane polygons, generated by interpolating between two traced road-edge polylines. Interpolating (rather than clicking each lane corner) keeps lane widths proportionally correct all the way up the frame as perspective makes them converge.
- **Area cameras** get functional zones — movement channel, parking/encroachment bank, pedestrian area, platform.

The polygons were **cross-checked against empirical traffic heatmaps**. Rather than trusting a single still image, we ran the detector over each clip and accumulated where vehicles actually travel (`traffic_heatmap.py`). That reveals the real drivable corridors — and it exposed things a still frame hides, such as persistent stationary hotspots near the Rokadiya Hanuman median which turned out to be parked two-wheelers and a vendor stall encroaching on the kerb lane.

### Stage 3 — Measurement

Metrics are aggregated into **2-second windows** per zone. Two paths run in parallel:

**Detection path** (valid while vehicles remain individually separable):
- **PCU density** — vehicles converted to Passenger Car Units per **IRC:106-1990** before counting. This is essential: twelve two-wheelers and twelve buses are not the same amount of traffic. Factors used: two-wheeler/bicycle 0.5, car 1.0, auto-rickshaw 1.2, LCV 1.4, bus/truck 2.2.
- **Speed** — from tracked displacement, converted through a perspective model.
- **Queue** — fraction of tracked vehicles below 4 km/h, and queued PCU.
- **Flow** — from the fundamental traffic relation **q = k · v** (density × speed).

**Occupancy path** (survives when detection fails):
- **Structural occupancy** — an index derived from image *texture* rather than from resolved objects. Empty carriageway is smooth and low-gradient; vehicles, people and stalls introduce dense edges. Measuring edge density inside a zone tracks how occupied it is without needing to identify individual objects.
- Normalised on an **absolute scale shared by all cameras** (`OCCUPANCY_EDGE_CLEAR = 0.022`, `OCCUPANCY_EDGE_PACKED = 0.175`, measured across all seven cameras), so an occupancy of 0.8 means the same thing wherever it appears.

A **saturation test** decides which path leads, and the active mode is carried into the output so the dashboard can be explicit about it.

### Stage 4 — Adaptive signal optimisation

Uses **Webster's method**, the standard basis for fixed-time signal design:

```
optimal cycle    C_opt = (1.5·L + 5) / (1 − Y)
green split      g_i   = (C_opt − L) · y_i / Y
```

where `y_i = q_i / s_i` is the flow ratio on the **critical lane** of approach *i*, `Y` is the sum of critical flow ratios, and `L` is total lost time per cycle. Delay is estimated with Webster's two-term delay formula so a before/after comparison can be **quoted rather than asserted**.

Delay is reported under **two models**, because the answer depends on which one you believe. Webster's own three-term formula diverges as an approach approaches capacity — exactly where our busier approach sits — so above a degree of saturation of 0.85 the system switches to the **HCM control-delay** form, whose time-dependent term stays finite above capacity. The honest headline is therefore the **saturation crossing**, which no modelling choice can take away: the fixed timer drives Rokadiya Hanuman to x = 1.01 (over capacity, the queue never clears) and the adaptive plan brings both approaches back to 0.929.

That 0.929 is not a coincidence. Webster's proportional split gives approach *i* a green of `g_i = (y_i/Y)(C − L)`, so its degree of saturation is `x_i = y_i·C/g_i = Y·C/(C − L)` — which has no *i* in it. Equal saturation across approaches is what proportional allocation **is**. The closed form gives 0.929; the optimiser lands on 0.929/0.929. The two agreeing is the arithmetic check that the implementation matches the theory.

**Saturation flow** follows IRC:SP:41-1994's width-based form, `S = 525·W` PCU/hr, rather than a per-lane constant — because "number of lanes" is precisely the quantity this dataset cannot pin down. Field measurement at Vijay Cross-road, Ahmedabad found 636·W to 1283·W, so the dashboard runs the optimisation as a band across that range rather than pretending to one number.

**Minimum green is a pedestrian requirement, not a tuning parameter.** A flat 10 s previously sat in the code labelled "pedestrian crossing minimum". It is not one: the minimum depends on how far somebody has to walk. Both governing standards size it as **start-up plus traverse** — IRC:103-2012 (the Indian code that governs Surat) at the conservative 0.8 m/s where elderly pedestrians are common, MUTCD as an explicit 7 s WALK plus clearance plus buffer. At a 12 m crossing that is **18.0 s** (IRC) or 21.3 s (MUTCD); at 15 m, 21.8 s or 24.1 s. The floor is now computed from each approach's own width.

This one is worth flagging as a near-miss, because the first version of the fix was *less safe than the constant it replaced*. Sizing the green as the traverse alone — dropping the start-up interval — gives 8.8 s for a 7 m crossing, i.e. a change made explicitly in the name of pedestrian safety that quietly lowered the safety floor below the 10 s it condemned. It was caught by an audit checking the code against the arithmetic in its own comment, and the absolute floor is now enforced so it cannot recur.

### Stage 4b — Max-pressure: a second controller, validated on a second dataset

This is the part of the architecture most likely to be misread, so it is worth stating in one line before anything else:

> **Surat's junctions run Webster's. Max-pressure is validated on FLUID. Neither borrows from the other.**

Every Surat figure in this report and in the dashboard — the cycle length, the green splits, the delay comparison, the saturation crossing — is produced by Webster's method. No Surat number depends on the max-pressure code path, and `tests/verify_system.py` asserts exactly that: it recomputes the Surat plan under both settings of the controller toggle and fails if a single digit differs, and it fails if the string `maxpressure` appears anywhere in the nine measurement and analysis modules.

#### Why the two controllers cannot share a dataset

The difference is not preference. It is what each one is *defined* by.

| | Webster's | Max-pressure |
|---|---|---|
| **Needs to observe** | arrival flow on each approach | the queue on each approach **and** the queue on the link it discharges into |
| **Cameras that requires** | one per approach | one per approach **plus one per receiving link**, or one aerial view of the whole junction |
| **Surat has** | ✅ exactly this | ❌ no camera watches a receiving link |
| **Optimal when** | demand is steady enough to plan a cycle for | demand shifts between approaches, or a downstream link backs up |
| **Output** | cycle length, green splits, delay | a phase to serve, re-ranked continuously |

Max-pressure serves the phase with the greatest queue difference between an approach and the link it discharges into:

```
movement weight   w(l,m) = x_l − Σ_k r(m,k)·x_k
phase pressure    P(p)   = Σ over (l,m) in p of  s_lm · w(l,m)
control law       serve argmax_p P(p), subject to minimum green
```

It is **throughput-optimal** — it stabilises any demand the junction can physically clear, using only local queues and no demand forecast. The subtracted downstream term is the whole point: it is what stops the controller discharging into a link that is already full, which is the spillback that gridlocks a network.

**And no Surat camera observes that term.** So `src/maxpressure.py` tracks observability per movement and reports the mode it actually ran in — `max-pressure`, `partial`, or `greedy-queue`. On the Surat cameras it reports **0 of 2 movements observable**, and it **refuses to name a phase**: `chosen_phase_id` is `None`. What greedy queue control *would* have served is returned separately, under a `greedy_*` name that cannot be mistaken for a max-pressure recommendation.

That refusal is not caution, it is the source paper. Dropping the downstream term does not give a degraded max-pressure; it gives **greedy queue control**, and **Varaiya's §3.1 Example 2** proves that controller fails to stabilise demands max-pressure handles — on a junction with two entry links and two exit links, unstable for every δ below 0.0358. Gregoire et al. (arXiv:1309.6484, Thm 1) show even true back-pressure loses work conservation under finite queues.

**The argument is stronger than "we cannot see downstream."** At a genuinely *isolated* junction the downstream term is dropped legitimately — Gayah & Levin's capacity-aware formulation treats exit links as sinks. But that also means max-pressure at an isolated junction *is* greedy max-weight, with none of the network property that makes it worth having. Our real position: the two Surat junction cameras watch **different corridors**, so there is no intersection at all, and no observable turning ratios.

#### The dataset obtained specifically to validate it

Since Surat cannot exercise max-pressure, a dataset that can was obtained for that purpose alone:

> **FLUID — a Fine-grained Lightweight Urban signalized-Intersection Dataset with Dense conflict trajectories.**
> Xuancheng, Anhui, China. DJI Mini 3 at 100–120 m. figshare DOI `10.6084/m9.figshare.29974954`, CC BY 4.0, ≈18 GB.

| | |
|---|---|
| **Sites** | 3 — `FI` (4-way, 3-phase), `FIDRT` (4-way with dedicated right turns, 4-phase), `TI` (T-intersection, 2-phase) |
| **Recordings** | 14 |
| **Footage** | 305 minutes |
| **Trajectories** | 20,860 vehicles |
| **Classes** | car, van, moped, tricycle, bus, truck, pedestrian |
| **Coordinates** | metre-scale world coordinates, already rectified — no scale calibration needed |
| **Signal state** | per-second, per-movement, with cycle / phase / turn annotations |
| **Turn annotation** | `entry_direction` and `exit_direction` per vehicle |

It supplies precisely the three things the Surat cameras cannot:

1. **The downstream queue.** The whole intersection sits in one aerial frame, so every approach *and* every receiving link is measured at the same instant.
2. **Turn ratios r(m,k).** Measured per vehicle, not assumed. They sum to 1.000 per origin direction — verified.
3. **A real signal to compare against.** Per-second ground truth, so the comparison is against what a real controller actually did rather than against a fixed timer we invented.

`src/fluid.py` is the adapter. It converts trajectories to per-movement queues (stopped vehicles, upstream of the stop line, within 80 m of the junction box), attaches each movement's receiving-link queue by exit direction, measures turn ratios, and reconstructs the real phase structure from the recorded signal groups. Its arm-assignment geometry was validated against the dataset's own `entry_direction` / `exit_direction` labels: **96.8% and 95.6% agreement**, with every mismatch at the 45° boundary between arms, exactly as a dominant-axis classifier must produce.

Two modelling details are documented rather than hidden. **Right turns carry no signal group** — they are the free turn in right-hand traffic, permitted on red — so 4 of 16 movements (3 of 9 at the T-junction) belong to no phase. That is correct: a signal controller has no lever over them, and ranking them in a phase pressure would attribute to the signal an influence it does not have. Their queues still count toward the receiving-link term of the movements that *are* controlled. And because arms are not split into inbound and outbound carriageways, a **U-turn's receiving link is the arm it is queued on**, so those vehicles are counted in both terms, depressing U-turn pressure toward zero. It affects 0.7–3.8% of queued PCU across the three sites.

The controller reports mode **`max-pressure`** on this data — genuine, with every downstream term measured.

#### What the validation shows

Replaying max-pressure second by second against what each real signal actually did:

| Intersection | Cycle | Phases | Steps | Real signal: green on a phase with nothing to relieve | Max-pressure | Agreement |
|---|---:|---:|---:|---:|---:|---:|
| **FI** (4-way, 3-phase) | 96 s | 3 | 1,713 | **41.1%** | 1.5% | 17.3% |
| **FIDRT** (4-way, dedicated right turns) | 123 s | 4 | 1,272 | **66.3%** | 2.3% | 5.4% |
| **TI** (T-intersection) | 71 s | 2 | 1,428 | **46.6%** | 16.0% | 52.0% |

Mean pressure actually served — how much queue each controller's choice was relieving, averaged over the run:

| Intersection | Real signal | Max-pressure |
|---|---:|---:|
| FI | 1.31 | **5.28** |
| FIDRT | 0.27 | **4.87** |
| TI | 0.64 | **1.30** |

And where the green actually went, at FI:

| Phase | Real signal | Max-pressure |
|---|---:|---:|
| E-l + E-s + W-l + W-s | 31.3% | **58.2%** |
| N-s + S-s | 37.6% | 24.1% |
| N-l + S-l | 31.1% | 17.7% |

The fixed plan splits green roughly evenly across three phases; max-pressure concentrates it on the east–west movements, which is where the queues actually were.

**"Nothing to relieve"** means the served phase's pressure was zero or negative — the queue had already cleared, or the link it discharges into was fuller than the approach feeding it. Instants where the real signal's green maps to no vehicle phase (all-red transitions, pedestrian-only intervals) are excluded from the denominator: about 9% of steps, and charging the fixed plan for time it was correctly *not* serving traffic would be unfair.

**What this is, and is not.** The queues are the ones that really formed, under the real signal. So the replay establishes *by direct observation* that these fixed plans routinely held green on an approach with nothing to discharge, and which phase max-pressure would have served instead. It is **not** a claim that max-pressure would have cut delay by any particular amount: had it actually run, the queues would have evolved differently, and asserting a delay figure from a replay would be exactly the kind of unsupported number this project refuses to print.

#### Where the counterfactual needs a model

`src/network_sim.py` is a point-queue simulator over **two junctions in series** — the smallest network in which the downstream term carries any information at all. Arrival demand is measured from the Surat clips; the topology, the link between the junctions and its storage capacity are constructed, and the report says which is which.

| Scenario | Fixed-time | Max-pressure | Throughput | Spillback | Switches | Verdict |
|---|---:|---:|---:|---:|---:|---|
| Steady, balanced | **18.6 s/PCU** | 20.8 s/PCU | −0.3% | 0 → 0 s | 144 / 203 | **fixed-time wins, by 12%** |
| Demand swings between approaches | 24.5 s/PCU | **18.5 s/PCU** | +1.2% | 0 → 0 s | 144 / 188 | **max-pressure −25%** |
| **Downstream blockage** | 150.0 s/PCU | **102.9 s/PCU** | **+8.1%** | **289 → 88 s** | 144 / 142 | **max-pressure −31%** |

An earlier version of this report quoted −52% and −38% here. Those numbers were wrong, and the corrections are worth recording in full, because in every case it was the *experiment* distorting the result rather than either controller earning it. Four separate biases, all found by the pre-submission audit, all now fixed — and they did not all point the same way:

1. **The fixed-time plan was not paying its inter-greens.** The cycle walk folded each inter-green into the following phase's serving window, so the plan was served during time that should have been all-red and paid **one** lost time per cycle instead of one per phase. *Flattered fixed-time.*
2. **The delay metric billed a phantom queue.** Queue-seconds accumulated on the boundary **sink** link — an accounting bucket everything served eventually lands in — so a term growing with throughput dominated the mean delay it was supposed to measure. *Distorted both.*
3. **The swing scenario was not holding demand constant.** Its description claimed "total demand is unchanged, but which approach carries it flips"; the multipliers were applied to unequal bases and total demand actually fell **18.6%** at the switch. *Flattered max-pressure.* It now shifts 300 PCU/hr between approaches with the total fixed at 1,180 PCU/hr.
4. **Max-pressure was overcharged for switching.** Each phase change cost it `lost + dt` rather than `lost`, and the "first phase is free" guard tested a controller-wide counter, so the second junction was blacked out at t = 0 for an inter-green it never earned. *Penalised max-pressure* — correcting it moved the blockage case from −13% to −31%.

The lesson worth standing behind is the one about direction: **two of these four biases favoured the algorithm we were arguing for.** A comparison that only gets audited when the answer looks wrong is not a comparison.

A fifth issue was structural rather than numerical: the third scenario was labelled "demand swings, oversaturated" but the code throttled the boundary **sink**, which — once sinks correctly stopped exerting back-pressure — made it a **no-op producing results identical to the steady case**. A downstream blockage has to be modelled where the controller can see it, so it is now a capacity drop on the movement discharging the short mid-link. That link backs up, max-pressure sees it and stops feeding it, while a fixed-time plan keeps discharging into a link with no room. It is the one scenario that isolates the downstream term, and the only one where throughput improves and spillback collapses.

#### The finding that is unflattering to the fashionable algorithm

**Naive max-pressure loses once you charge it the real lost time of a signal.** On steady demand — the case a fixed-time plan is designed for — free-running max-pressure switched **203 times against the fixed plan's 144** and came out **12% worse on delay** (20.8 against 18.6 s/PCU), with slightly lower throughput. Every extra switch buys a better-targeted green and pays an inter-green for it, and on demand that is not moving there is nothing to target. That is the gap between Varaiya's slotted model, where switching is free, and a junction where it is not. The deployable form (`CyclicMaxPressureController`) keeps the cycle and lets pressure set only the splits, so it pays exactly what Webster's pays.

#### One genuinely good property for a project with no metric scale

Max-pressure's argmax is **scale-invariant**. Both the weight and the pressure are homogeneous of degree 1 in the queues (Varaiya eq. 21–22), so multiplying every queue by a common factor leaves the phase choice unchanged. Tested: ×7.3 leaves the argmax identical and scales the pressure by exactly 7.30. Our uncalibrated pixels-to-metres therefore does not threaten the decision.

What *does* threaten it is an **additive, state-dependent bias** — and our occlusion saturation is exactly that, growing with density and hitting the longest queue hardest, which is the very term the pressure is differencing. That is a bigger risk to max-pressure here than the missing metric scale, and it is why the input is `queue_length_pcu`, a count, rather than any metric queue length.

#### The operator's decision rule

`recommend_controller()` in `signal_opt.py` turns the toggle from a guess into a decision. It tests **observability first** — without a downstream view max-pressure is *unavailable*, not merely worse, and no amount of demand variability changes that — then the **variability of the demand split** between approaches, which is dimensionless and so unaffected by the uncalibrated scale. On the Surat network it returns `webster / decisive`, with the reason stated. The recommendation appears beside the sidebar toggle.

Summarised:

- **Steady demand, one camera per approach** → Webster's.
- **Demand shifting between approaches, full downstream visibility** → max-pressure.
- **Downstream link backing up** → max-pressure, decisively.
- **No downstream visibility at all** → Webster's, because max-pressure is not *available*, and pretending otherwise means shipping greedy queue control under a better name.

### Stage 5 — Bottleneck detection

A bottleneck is *not* simply "a lot of vehicles" — a busy lane moving freely is working as designed. What matters is high density combined with **depressed speed**, sustained long enough to be a condition rather than a passing platoon. Every detector looks for a pattern held over at least 3 consecutive windows.

Five detectors:

| Detector | Trigger | What it means |
|---|---|---|
| **Congestion bottleneck** | density > 45 PCU/km/lane **and** speed < 12 km/h, sustained | Demand exceeds what the lane can discharge |
| **Standing queue** | > 35% of tracked vehicles stationary, sustained | Queue not clearing within the cycle |
| **Saturated gridlock** | scene packed but detector cannot resolve objects | Movement space effectively full |
| **Encroachment** | a parking zone occupied in > 50% of windows | Kerb space lost to parking, narrowing the carriageway |
| **Lane imbalance** | > 12 km/h speed spread between lanes on one approach | Localised obstruction, not a capacity shortage |

Each alert carries **the evidence that triggered it** and a specific recommendation, so a reader can check the reasoning instead of trusting it.

### Stage 6 — Prediction

Deliberately simple: a **damped linear trend** over the recent window, with a prediction interval from the residuals.

The interval is the point. A single forecast number from two minutes of footage would imply confidence this data cannot support. Where the trend is weaker than the series' own noise, the forecast returns `reliable = False` and the system says *"insufficient history"* rather than printing a number. Damping (factor 0.6) prevents unchecked extrapolation from predicting negative congestion or a jam that never arrives.

Given continuous operation, this same interface would be backed by a seasonal time-of-day / day-of-week model. That is noted as the upgrade path rather than pretended at.

### Stage 7 — Presentation

**Streamlit dashboard** (`dashboard.py`, with `theme.py` and `charts.py`).

The sidebar's **control-strategy toggle switches the entire dashboard between two tracks**, because the two controllers have nothing to say about each other's data. It is not a filter on one navigation list — selecting a strategy changes which views exist. Keeping one shared list would put a FLUID result one click from a Surat result with nothing but a heading between them, which is exactly the confusion the split exists to prevent.

#### Track 1 — Webster's, on the Surat CCTV network (8 views)

| View | What it answers | Lead visualisation |
|---|---|---|
| **Network** | Which site is worst, and how is the network behaving? | Level-of-service bands per site across the whole clip, plus the congestion ranking with mean and peak |
| **Site detail** | What is happening at this camera? | Zone tiles with meters, the scene overlay, the movement heatmap, the annotated video, per-zone trends, counting-vs-occupancy, and the speed–density diagram on junction cameras |
| **Signal timing** | What should the signal do differently? | Cycle composition before and after on one time axis, degree of saturation as a before/after dumbbell, the swept delay-vs-cycle curve, and the feasible-cycle window |
| **Bottlenecks** | Where are the sustained problems? | Every alert on a common clip clock by site and severity, plus the filterable evidence-backed alert feed |
| **Outlook** | What is about to get worse? | Now-against-forecast per zone with the prediction interval drawn as a mark, not a footnote |
| **Analyse footage** | What does this system say about a clip it has never seen? | Upload a video or still; the road region is found from where vehicles actually travel, then the full measurement, alert and forecast stack runs on it, with a downloadable HTML report |
| **Calibration** | How do I promote the `~` estimates to measured values? | Guide image with candidate measurement lines, one real width typed in, the fitted perspective model with plausibility diagnostics, then an in-place re-measure |
| **How it works** | Why should I believe these numbers? | The pipeline, the occlusion finding as a chart, the fleet composition, and what the dataset cannot support |

#### Track 2 — Max-pressure, on the FLUID drone data (4 views)

| View | What it answers | Lead visualisation |
|---|---|---|
| **Validation** | Does max-pressure actually work? | The headline across all three intersections: wasted green real-vs-max-pressure as a grouped bar, mean pressure served, agreement — then a per-recording drill-down with measured turn ratios and the replay timeline |
| **Scene** | Where do these numbers come from? | The intersection itself — orthorectified aerial with approach and receiving zones shaded by live queue, every vehicle at a chosen instant coloured queued/moving, the movement heatmap over the whole recording, the 4K drone video, and the vehicle mix |
| **Phase pressure** | What does the ranking look like at one instant? | Phase pressure scores at a chosen second of a chosen recording, with every movement's approach queue, receiving-link queue, turn ratio and weight |
| **Head-to-head** | When is each controller the right choice? | Webster against max-pressure across three demand regimes in the point-queue simulator, with delay, throughput, spillback and switch counts |
| **Method** | Why is this validated here and not on Surat? | The observability gate, the movement table showing `not computable` on Surat, and the Varaiya argument for refusing rather than substituting zero |

**The Scene view is the max-pressure track's answer to Site detail**, and it exists because "show me the place these numbers came from" is the same question on both sides. FLUID ships an orthorectified aerial image and metre-scale trajectories, so world coordinates can be drawn straight onto the image. The mapping is fitted per recording as a **full 2-D affine** rather than two per-axis scales — the drone's heading is not aligned to the world axes, and the three sites are rotated by −0.2°, +2.3° and **+156.8°**. A per-axis fit cannot express rotation at all and was placing the zones roughly **8 m out** on the rotated site; the affine lands within 2.7 px (about 11 cm) on all three. `tests/verify_system.py` asserts that residual, because a picture that quietly disagrees with the numbers beside it is worse than no picture.

**Neither track can show the other's data.** The Webster track never renders a FLUID figure; the max-pressure track never renders a Surat junction result. `tests/verify_system.py` asserts the underlying separation, and the UI test sweep renders all 12 views in both tracks and fails if a Surat camera name appears in the FLUID track or vice versa.

Three rules govern the visual layer, and each one is there to stop a specific way of misleading a reader:

- **Colour does one job per chart.** Level of service and alert severity take a *reserved status palette* and always carry their name in text beside a coloured dot, so hue is never the only channel. Series identity takes a *fixed six-slot categorical order* that is never reassigned by rank — filtering a series out cannot repaint the survivors. Magnitude takes a *single-hue ramp*, never a rainbow.
- **No dual-axis charts.** Where two measures of different scale must be read together — detection counts against occupancy, the central claim of this project — they go on stacked panels sharing one time axis. Two arbitrary y-scales on one plot manufacture a correlation the data does not contain.
- **Nothing is reachable only by hovering.** Every chart has a table view beside it, and values are direct-labelled wherever the labels do not collide.

Both the light and dark palettes were checked for colour-vision separation against the surface they actually render on rather than picked by eye: worst adjacent pair 9.1 (light) / 8.4 (dark) in OKLab ΔE×100, against a target of 8. Dark mode is a selected palette — the same six hues re-stepped for a dark surface, with the magnitude ramp reversed so "near zero" recedes toward the background in both modes — not an inversion of the light one.

**Graceful degradation.** Any view that needs the detector checks for the weights first and shows a one-command fix rather than a traceback; any view that needs a reference frame or a rendered video says which script produces it. The dashboard opens and every Surat view works from the CSVs alone, with no model download at all.

**Annotated demo videos** (`render_demo.py`) — MP4 with live lane shading by level of service, tracked detections, a per-zone metrics panel and a running class tally.

---

## 5. The two discoveries that shaped the design

These are the findings that make this system different from a textbook YOLO-counting project.

### Discovery 1 — In the worst gridlock, detection counts *fewer* vehicles, not more

We ran the detector over 250 sampled frames from every camera and counted detections:

| Camera | Detections | Actual condition |
|---|---:|---|
| Rokadiya Hanuman | **7,664** | flowing arterial |
| Central Bus Depo | 2,905 | busy |
| GauravPath | 1,957 | free-flowing |
| Rly Station | 1,823 | moderate |
| Gopi Talav | 1,619 | crowded |
| Chauta Bazaar | 706 | dense market |
| **Mahidharpura** | **463** | **worst gridlock in the dataset** |

The most congested street in the city produced the **fewest** detections.

**Why:** at extreme density, a wall of overlapping vehicles and pedestrians stops being separable. The detector's recall collapses. This is not a model quality problem — it is a fundamental limit of object detection in saturated scenes.

**Why it matters:** a naive "more detections = more congestion" system would rank Mahidharpura as the *emptiest* road in Surat. It would be confidently, dangerously backwards — and it would look fine in testing, because every number would be plausible.

**Our fix:** the texture-based occupancy index, which does not depend on resolving individual objects, plus an explicit saturation flag. The saturation test itself needed care: the obvious approach (detect heavy overlap between boxes) fails precisely when it is needed, because once detection collapses there are too few boxes left to overlap. So we added a second signature — **"full scene, empty count"**: texture says the zone is packed while the detector's boxes cover almost none of it. That catches the collapse case.

Result: Mahidharpura now correctly flags **452 of 453 windows** as saturated, and ranks as the most congested site.

### Discovery 2 — Only two of seven cameras can support the requested measurement

As covered in §2. Rather than fabricate lane density for market streets, the system splits into junction mode and area mode. This is an honesty decision that also happens to produce a better product: the area cameras get metrics that genuinely suit them (crowding, encroachment, movement-channel occupancy) instead of a meaningless lane number.

---

## 6. Results

Results are reported in two parts, matching the two datasets. **§6A is the Surat system** — everything PS-08 asks for, produced by Webster's. **§6B is the max-pressure validation** on FLUID. No figure crosses between them.

---

## 6A. Surat — the operational system (Webster's)

### Scale of the analysis

- **7 cameras**, **21 zones**, **2,451 measurement windows**, ≈ **28 minutes** of footage analysed
- Throughput ≈ **12 FPS at 960 px on 1080p input** on an RTX 3050 6 GB — **faster than real time** at stride 2

### Network-wide congestion ranking

| Rank | Camera | Mode | Mean congestion | Peak | Worst zone | Saturated windows |
|---|---|---|---:|---:|---|---:|
| 1 | Mahidharpura – Diamond Mkt | area | **0.939** | 1.000 | Movement channel | 452 / 453 |
| 2 | Gopi Talav Gate | area | 0.749 | 1.000 | Vehicle channel | 167 / 456 |
| 3 | Central Bus Depo | area | 0.483 | 0.706 | Platform waiting area | 213 / 450 |
| 4 | Rokadiya Hanuman | junction | 0.437 | 0.872 | Kerb-side encroachment | 0 / 244 |
| 5 | Chauta Bazaar | area | 0.205 | 0.550 | Vendor encroachment (R) | 4 / 453 |
| 6 | Rly Station | area | 0.099 | 0.339 | Pedestrian flow | 45 / 302 |
| 7 | GauravPath | junction | 0.073 | 0.261 | Lane 2 (kerb) | 0 / 93 |

The ranking is correct end to end: the gridlocked market ranks worst, the free-flowing arterial ranks best. **This ordering only exists because of the occupancy fallback** — a count-based index inverts it.

### Per-lane detail on the junction cameras

| Camera | Lane | Occupancy | Density (PCU/km/ln) | Speed (km/h) | Flow (PCU/hr) | Typical LOS |
|---|---|---:|---:|---:|---:|---|
| GauravPath | Lane 1 (median) | 0.06 | ~15.5 | ~45.9 | ~728 | Free |
| GauravPath | Lane 2 (kerb) | 0.12 | ~4.4 | ~55.3 | ~221 | Free |
| Rokadiya Hanuman | Lane 1 (median) | 0.22 | ~14.9 | ~29.8 | ~428 | Free |
| Rokadiya Hanuman | Lane 2 (centre) | 0.37 | ~34.2 | ~25.6 | ~846 | Moderate |
| Rokadiya Hanuman | Lane 3 (kerb) | 0.46 | ~22.0 | ~18.8 | ~404 | Moderate |

*(`~` marks figures that depend on the metric scale — see §9.)*

**A key validation signal:** on Rokadiya Hanuman the speed gradient came out **median 29.8 > centre 25.6 > kerb 18.8 km/h**. That is the textbook pattern for an Indian arterial — the fast lane is fastest, the kerb lane is slowed by encroachment and turning movements. Nothing was tuned to produce that; it emerged from the measurement, which is good evidence the pipeline is behaving physically.

### Adaptive signal timing

Comparing an untuned fixed-time baseline (120 s cycle, equal green splits) against the Webster-optimal plan on measured demand:

| | Baseline | Optimised | Change |
|---|---:|---:|---|
| Cycle length | 120.0 s | 135.6 s | +15.6 s |
| **Mean delay** (Webster 2-term, as originally computed) | 91.5 s/PCU | 61.6 s/PCU | −32.7% |
| **Mean delay** (HCM control delay, finite above capacity) | **53.5 s/PCU** | **51.4 s/PCU** | **−3.8%** |
| **Worst-approach saturation** *x* | **1.01 — over capacity** | **0.929** | **crosses back under 1.0** |

Per approach:

| Approach | Plan | Green | Degree of saturation *x* | Delay |
|---|---|---:|---:|---:|
| GauravPath | baseline | 56.0 s | 0.87 | 42.6 s |
| Rokadiya Hanuman | baseline | 56.0 s | **1.01 — oversaturated** | 133.6 s |
| GauravPath | optimised | 59.0 s | 0.93 | 66.6 s |
| Rokadiya Hanuman | optimised | 68.5 s | 0.93 | 57.3 s |

**The mechanism is clear and explainable:** equal green splits drive the busier approach into oversaturation (x = 1.01 > 1), where delay explodes. Webster's rebalances the greens so both approaches sit at the same degree of saturation.

**Two independent checks that the optimiser is correct:**
1. **Sweeping cycle length** under Webster's own delay model puts the empirical delay minimum at **130 s**, against Webster's closed-form optimum of **135.6 s** — close agreement (the formula is an approximation). This curve is shown live in the dashboard. The check has to be run under Webster's delay model, because that is the model the closed form is derived from: under the HCM control-delay model — the dashboard's default above x = 0.85 — the swept minimum moves to **90 s**, since HCM's bounded overflow term penalises running near capacity far less. That is a property of the delay model, not a disagreement in the optimiser, and the dashboard reports both minima side by side.
2. The optimal plan **equalises degree of saturation across approaches (0.93 / 0.93)**, which is the known theoretical optimality condition for Webster green allocation.

The two delay models disagree by an order of magnitude, and that spread is itself the finding: Webster's random-delay term diverges as an approach reaches capacity, so it reads high on exactly the approach this plan is fixing, while HCM's time-dependent term stays finite above capacity. **The saturation crossing is the claim that survives either model**, and it is the one to quote. For context, the market systems surveyed in `Existing Solutions.docx` report ATSAC at 32% delay reduction and emerging research at 23.6% wait-time reduction; quoting our Webster figure alongside theirs would be comparing a number we know to be inflated against numbers measured on the street.

### Bottleneck detection

**31 alerts** across the network: **10 critical, 20 warning, 1 info**.

| Kind | Count |
|---|---:|
| Saturated gridlock | 26 |
| Standing queue | 3 |
| Encroachment | 2 |

By camera: Central Bus Depo 10, Gopi Talav 7, Rly Station 7, Rokadiya Hanuman 4, Mahidharpura 3, Chauta Bazaar 1.

Example alert:

> **[CRITICAL] Saturated conditions in Movement channel** — Mahidharpura
> *Evidence:* occupancy index 0.99 for 298 s; vehicles no longer individually separable, so counts under-report and occupancy is the governing measure here.
> *Recommendation:* Movement space is effectively full. Signal timing has no leverage on this. Options are demand management (access restriction at peak), reclaiming carriageway width from encroachment, or providing off-street parking.

Note the recommendation correctly identifies that **signal retiming would not help here** — the problem is geometric and demand-side. A system that recommended "extend the green" for every congestion alert would be giving bad engineering advice.

### Prediction

**21 zones forecast · 19 with a credible trend · 8 trending toward congestion.**

| Camera | Zone | Now | Forecast (60 s) | Range | Direction |
|---|---|---:|---:|---|---|
| Mahidharpura | Movement channel | 1.00 | 1.00 | 1.00–1.00 | rising |
| Gopi Talav | Vehicle channel | 0.91 | 0.93 | 0.84–1.00 | rising |
| Rokadiya Hanuman | Lane 3 (kerb) | 0.63 | **0.88** | 0.75–1.00 | rising |
| Rokadiya Hanuman | Lane 2 (centre) | 0.51 | **0.78** | 0.68–0.87 | rising |

The Rokadiya Hanuman lanes trending sharply upward is the actionable case: an approach heading toward congestion within the next minute, which is exactly the signal an adaptive controller would act on.

---

## 6B. FLUID — the max-pressure validation

Separate dataset, separate purpose: to show the max-pressure implementation works on data that contains the downstream queue it is defined by. **None of these figures describes Surat, and no Surat figure depends on them.**

### Scale of the validation

- **3 signalised intersections**, **14 recordings**, **305 minutes**, **20,860 vehicle trajectories**
- **Controller mode: `max-pressure`** — every downstream term measured, on every movement
- Turn ratios measured per vehicle; they sum to **1.000** per origin direction
- Arm geometry validated against the dataset's own direction labels: **96.8%** (entry) / **95.6%** (exit)

### Replay against the real signals

| Intersection | Cycle | Phases | Steps | Real signal: green with nothing to relieve | Max-pressure | Agreement |
|---|---:|---:|---:|---:|---:|---:|
| FI (4-way, 3-phase) | 96 s | 3 | 1,713 | **41.1%** | 1.5% | 17.3% |
| FIDRT (4-way, dedicated right turns) | 123 s | 4 | 1,272 | **66.3%** | 2.3% | 5.4% |
| TI (T-intersection) | 71 s | 2 | 1,428 | **46.6%** | 16.0% | 52.0% |

Mean pressure served — how much queue each controller's choice was actually relieving:

| Intersection | Real signal | Max-pressure | Ratio |
|---|---:|---:|---:|
| FI | 1.31 | **5.28** | 4.0× |
| FIDRT | 0.27 | **4.87** | 18.0× |
| TI | 0.64 | **1.30** | 2.0× |

**The headline observation:** these real fixed-time plans spend **41–66% of their green on a phase with nothing to relieve** — the queue had already cleared, or the receiving link was fuller than the approach feeding it. Max-pressure, on the same queues, does so 1.5–16% of the time.

This is an observation about the recorded signals, made from the recorded queues. It is deliberately **not** a delay-reduction claim: had max-pressure actually run, the queues would have evolved differently. The controlled counterfactual is the simulator in §4b.

### Controlled comparison (simulator)

| Scenario | Fixed-time | Max-pressure | Throughput | Spillback | Verdict |
|---|---:|---:|---:|---:|---|
| Steady, balanced | **18.6 s/PCU** | 20.8 s/PCU | −0.3% | 0 → 0 s | fixed-time wins by 12% |
| Demand swings between approaches | 24.5 s/PCU | **18.5 s/PCU** | +1.2% | 0 → 0 s | max-pressure −25% |
| Downstream blockage | 150.0 s/PCU | **102.9 s/PCU** | **+8.1%** | **289 → 88 s** | max-pressure −31% |

**Read together, §6B says:** max-pressure is correctly implemented, it demonstrably makes better phase choices than the real fixed plans on real intersections, and it earns its place when demand shifts or a link backs up — while losing on steady demand, which is exactly why Surat, whose cameras support only Webster's anyway, is the right place for Webster's.

---

## 7. Technology used and why

| Layer | Choice | Why this and not the alternative |
|---|---|---|
| **Detector** | YOLO11-S | Higher mAP than YOLOv8 at similar speed, notably better on small, distant and partially occluded vehicles. Drop-in through Ultralytics. |
| **Weights** | **UVH-26 fine-tune** | The single most important choice. Trained on 26,646 annotated 1080p frames from Indian Safe-City CCTV — same domain as our footage. Gives 14 India-specific classes including the auto-rickshaw, which COCO simply does not have. Paper reports 8.4–31.5% mAP50:95 gain over COCO baselines. Removed any need to train our own model on an unlabelled dataset. |
| **Tracker** | ByteTrack | Bundled with Ultralytics, strong on crowded scenes, associates low-confidence detections (important where vehicles are partly occluded). |
| **Density unit** | **PCU per IRC:106-1990** | Raw vehicle counts are meaningless in Indian mixed traffic. PCU is the unit Indian capacity standards are actually defined in, so the numbers mean something to a traffic engineer. |
| **LOS thresholds** | Derived from traffic-flow theory | Jam ≈ 130 PCU/km/lane, capacity ≈ 60 (1,800 PCU/hr @ 30 km/h), free flow < 20 — not picked by eye. |
| **Signal method** | Webster's (default) | Needs only arrival flow per approach, which is exactly what vision measures. Delay reported under both Webster's 3-term and HCM control delay, since Webster's diverges near capacity. |
| **Second controller** | Max-pressure (Varaiya 2013) | Throughput-optimal and offered as an operator choice, but gated on observability: no camera here sees a receiving link, so it reports `greedy-queue` and refuses rather than fabricating a phase choice. |
| **Saturation flow** | IRC:SP:41-1994, `S = 525·W` | Width-based rather than per-lane, because lane count is exactly what this dataset cannot pin down. Run as a band across the Ahmedabad field range 636·W–1283·W. |
| **Pedestrian minimum green** | IRC:103-2012 | Computed from each approach's crossing width at 0.8 m/s, not a flat constant. |
| **Flow estimation** | q = k·v | Density and speed are both measured directly. Counting tracker IDs is unreliable (see §8). |
| **Occupancy** | Canny edge density | Requires no object resolution, so it survives the saturation that breaks counting. Cheap enough to run per frame. |
| **Dashboard** | Streamlit | Fast to build, already installed, sufficient for an operator view. React + Kafka + PostGIS (the production stack the brief mentions) is deployment scaling, not hackathon scope. |
| **Charts** | Plotly, on a hand-built palette | Interactive hover and crosshairs without a JS build step. The palette is not Plotly's default: categorical, sequential and status roles are separated, and both light and dark sets were validated for colour-vision separation against their own surface. |
| **Language / libs** | Python 3.11, PyTorch 2.5 + CUDA 12.1, OpenCV 4.13, pandas, NumPy | Standard, GPU-accelerated, already present on the dev machine. |

**Hardware:** NVIDIA RTX 3050 6 GB laptop GPU. Confirmed sufficient — no cloud training was needed, because the UVH-26 weights removed the training step entirely.

---

## 8. Bugs found and fixed during development

Included because these are the kind of errors that produce **plausible-looking but wrong** numbers, and none of them would have been caught by unit tests. Each was found by checking output against physical reality.

### 1. Isotropic pixel-to-metre scale → speeds 5× too low

**Symptom:** free-flowing arterial traffic reported at 4–8 km/h.

**Cause:** the scale model treated one pixel of vertical movement as covering the same ground distance as one pixel of horizontal movement. On a road receding from the camera this is badly wrong, and the gap widens toward the horizon. Vehicles approaching a traffic camera move almost entirely "up the frame", so the error hit them at full force.

**Fix:** a pinhole flat-plane model with separate lateral and longitudinal scales —
`s_h(y) = C/(y − y_horizon)` and `s_v(y) = s_h(y)² · (focal/height)`. The two differ by a factor of 2.3× to 9.2× depending on image row.

### 2. Flow computed from tracker IDs → 10× too high

**Symptom:** 12,000–23,000 PCU/hr, against a real saturation flow near 1,800.

**Cause:** flow was computed by counting new ByteTrack IDs per window. ByteTrack reissues IDs whenever a track drops briefly — constant in dense mixed traffic — so ID churn was being counted as vehicle arrivals.

**Fix:** the fundamental relation **q = k·v**, using directly measured density and speed.

### 3. Webster's optimiser mixing per-lane and per-approach units

**Symptom:** the "optimised" plan came out **worse** than the baseline (−9.5%, i.e. delay increased).

**Cause:** flow ratio `y` was computed on the **critical lane**, but degree of saturation `x` on the **whole approach**. Mixing bases understated `x` (0.62 where theory requires `Y·C/(C−L)` = 0.93), which suppressed Webster's overflow-delay term. With that term flattened, only uniform delay remained — and that rises monotonically with cycle length, so no optimum existed at all.

**How it was caught:** sweeping cycle length and noticing the delay curve had **no minimum**, which is impossible if the implementation is correct.

**Fix:** anchor capacity, saturation and delay on the critical lane throughout. Verified by re-sweeping under Webster's delay model — the minimum now lands at 130 s against Webster's 135.6 s.

### 4. Module name collision breaking pandas

Our bottleneck module was originally named `bottleneck.py`. `bottleneck` is a real PyPI package that pandas imports opportunistically to accelerate its nan-aware reductions. The local module shadowed it and made **pandas fail to import for the entire project**. Renamed to `bottleneck_detect.py`.

### 5. Per-zone occupancy normalisation destroying cross-site comparison

The occupancy index was originally normalised against each zone's own observed range. That makes every zone its own yardstick: an empty road stretches its own noise across the full 0–1 scale and reports "busy" whenever it drifts slightly above its own average. Site-versus-site ranking — the headline output — became meaningless. Fixed by measuring absolute edge-density bounds across all seven cameras and sharing one scale.

### 6. `fillna(method="ffill")` removed in pandas 3

Broke the entire forecasting module. Replaced with `.ffill()`.

### 7. The pedestrian minimum-green "fix" that lowered the safety floor

Covered in §4. A flat 10 s was replaced with a width-derived figure — which returned **8.75 s**, shorter than the constant it condemned as unsafe, because the IRC branch sized the green as the bare traverse and omitted the start-up interval its own comment arithmetic included. Found by checking the code against the numbers written beside it. `ABSOLUTE_MIN_GREEN_S` is now enforced so a computed minimum can never come out below the value it superseded.

### 8. Max-pressure named a phase it had no data for

The observability gate correctly reported `mode = greedy-queue` and `usable = False` on the Surat cameras — and then populated `chosen_phase_id` anyway, so the dashboard displayed a confident phase recommendation next to a chip saying the algorithm could not run. A judge reading the recommendation would never see the chip. The refusal is now **structural**: `chosen_*` is populated only in full max-pressure mode, and what greedy queue control would serve is returned under a separate `greedy_*` name that cannot be mistaken for it.

### 9. A phase with no measured movements scored zero, and won

`phase_pressure` scored a phase whose movement ids did not resolve as `0.0` and marked it computable. Since real pressures go negative exactly during spillback, that phase would win the argmax precisely when the network was failing — holding green on an approach the system knew nothing about. It now returns `None`: no pressure, rather than a pressure of zero. A related sparseness bug in the FLUID adapter made empty-but-measured approaches vanish from a snapshot, so a phase carrying no traffic reported `0 of 0 movements observed` — indistinguishable on screen from *we have no downstream camera*, which is the one distinction the gate exists to make.

### 10. Four biases in the controller comparison, two of them in our own favour

Covered in §4b. The fixed-time plan was not paying its inter-greens; the delay metric billed a phantom queue on the boundary sink; the swing scenario silently dropped 18.6% of demand at the switch; and max-pressure was overcharged `lost + dt` per switch. The published gains moved from −52%/−38% to −25%/−31%, and the steady-state case moved from max-pressure losing by 49% to losing by 12%. Two of the four flattered the algorithm we were arguing for. `tests/verify_system.py` now asserts the lost-time symmetry and that the three scenarios genuinely differ.

### 11. Webster's delay formula was silently clamped

The docstring argued that capping the divergent random-delay term "is a fudge that hides the problem rather than solving it" — while the code directly beneath it clamped `x` to 0.98 and the delay to 120 s. That made a hopelessly oversaturated approach look merely busy, and the dashboard offers "Webster 1958" as an explicit choice, so the clamp was reachable. Webster's now returns `math.inf` above capacity, which is the truthful answer for a formula that has no finite one there; the default `auto` model switches to HCM control delay above x = 0.85 and stays finite.

---

## 9. Honest limitations

These are stated plainly because a system that hides them is worse than one that admits them.

### The metric scale is not calibrated

Converting pixels to metres needs **one true ground reference** — a surveyed road width, the camera's height and focal length, or a landmark of known size. **This dataset ships none of them.** Three independent recovery attempts disagreed with each other by a factor of two to four:

| Method | Why it failed here |
|---|---|
| Assume traced carriageway = N lanes × 3.5 m (IRC:86) | The traced polygons are not constant-width road — Rokadiya's follows a works-zone narrowing, so it *narrows* in the near field, which is geometrically impossible for a straight road |
| Regress on known vehicle widths | Confounded by viewing angle: a car seen ~20° off-axis presents a box of `W·cosθ + L·sinθ` ≈ 3 m, not its 1.7 m physical width |
| Fit horizon from road-edge convergence | Implied horizons ranged from y = −734 to y = −13,643 — not physically consistent |

**Consequence and how it is handled.** The system separates what it *measures* from what it *estimates*:

- **Scale-free and trustworthy** — congestion index, level of service, site ranking, occupancy, lane comparison, and the signal-timing before/after comparison. Unaffected by the scale.
- **Scale-dependent estimates** — anything in km/h, PCU/km/lane or PCU/hr. Displayed with a `~` prefix behind a persistent warning banner in the dashboard.

Every headline result in §6 is in the first category.

**Fixing it is one measurement.** `measurement_guide.py` renders marked-up frames showing exactly where to measure; a width can be read off Google Maps satellite view without a site visit. Then `apply_scale.py` fits the model, and setting that camera to `True` in `SCALE_VERIFIED` promotes its metric figures to primary. Configuration, not code.

### Other limitations

- **Lane polygons are traced, not surveyed.** Cross-checked against traffic heatmaps so they follow real vehicle paths, but they are estimates from a single view.
- **The two junction cameras face different corridors**, not opposing approaches of one intersection. The signal plan demonstrates the method on real measured demand; a deployable plan for a specific junction needs all its approaches — a configuration change, not a code change.
- **Forecasting has minutes of history, not weeks.** Deliberately simple, with uncertainty shown. Continuous operation would justify a seasonal model.
- **Licensing:** UVH-26 dataset is CC-BY-4.0; the YOLO weights and Ultralytics are **AGPL-3.0**. Fine for open-source and hackathon use. A commercial deployment needs either an Ultralytics licence or a swap to the Apache-2.0 RT-DETRv2 / DAMO-YOLO variants that the same UVH-26 release ships.

---

## 10. Every file, and what it does

### Core pipeline

| File | Purpose |
|---|---|
| `src/config.py` | Camera registry, IRC PCU factors, LOS thresholds, occupancy bounds, scale-verification flags. The single place tunable constants live. |
| `src/detector.py` | YOLO11 + ByteTrack wrapper. Yields a clean stream of tracked detections with PCU and road-contact anchor. |
| `src/zones.py` | Lane/zone polygon geometry, persistence, point-in-polygon assignment, perspective scale model, rendering. |
| `src/metrics.py` | The measurement engine. Density, speed, queue, flow, occupancy, saturation detection, LOS classification. |
| `src/analyze.py` | Main runner. Detect → track → assign → measure → CSV. |
| `src/signal_opt.py` | Webster's cycle and green splits, delay estimation, before/after comparison. |
| `src/bottleneck_detect.py` | Five sustained-pattern detectors with evidence and recommendations. |
| `src/forecast.py` | Damped trend forecasting with prediction intervals, plus profile/summary helpers. |
| `src/dashboard.py` | Streamlit operator UI. Two tracks selected by the control-strategy toggle: 8 Webster/Surat views and 4 max-pressure/FLUID views, which never show each other's data. Layout and copy only — every figure comes from `charts.py`. |
| `src/theme.py` | The visual system: validated light/dark palettes, the Plotly chrome every figure shares, and the HTML components (stat tile, status chip, hero figure, meter, alert card). |
| `src/charts.py` | Every figure. One function per chart, each choosing its form from the data's job before it chooses a colour. |
| `src/maxpressure.py` | Max-pressure control (Varaiya 2013) with the observability gate that reports `max-pressure` / `partial` / `greedy-queue` rather than guessing a missing downstream queue. |
| `src/network_sim.py` | Point-queue network simulator over two junctions in series; fixed-time, cyclic max-pressure and free-running max-pressure, across three demand scenarios. |
| `src/fluid.py` | FLUID adapter: extracts per-movement approach and receiving-link queues, measured turn ratios and the real phase structure from drone trajectories, and replays max-pressure against the recorded signal. |
| `src/calibration.py` | Metric-scale calibration: persistent state, perspective fitting from one measured width, physical-plausibility diagnostics, measurement-guide rendering. |
| `src/ingest.py` | Analysis of uploaded footage: automatic road-region detection from vehicle movement, zone construction, video and single-frame pipelines. |
| `src/report_html.py` | Standalone downloadable HTML report with live charts and the annotated frame embedded. |
| `src/signal_opt.py` → `recommend_controller()` | Reads this network's own measured demand and recommends which controller to run, with reasons. Observability is tested first (without a downstream view max-pressure is unavailable, not merely worse), then the *split* variability between approaches — dimensionless, so the uncalibrated metric scale cannot affect it. Surfaced beside the sidebar toggle. |
| `tests/verify_system.py` | End-to-end verification, 16 checks. Recomputes every figure quoted in this report and asserts the behaviours that matter: the observability gate refuses on Surat and runs on FLUID, the argmax is scale-invariant, the simulator conserves vehicles and charges both controllers the same lost time, every chart survives degenerate input, no window has zero duration. Run it before believing anything here. |

### Calibration and setup tools

| File | Purpose |
|---|---|
| `src/reference_frames.py` | Extracts a clean reference frame per camera, plus a grid-overlaid copy for reading coordinates. |
| `src/seed_calibration.py` | Generates first-pass lane/zone polygons by interpolating between traced road edges. |
| `src/calibrate.py` | Interactive click-to-draw polygon editor for refinement. |
| `src/calibrate_occupancy.py` | Measures absolute edge-density bounds across all cameras. |
| `src/calibrate_scale.py` | Fits the perspective scale by regression on known vehicle widths. |
| `src/measurement_guide.py` | Renders marked-up frames showing exactly where a real-world width is needed. |
| `src/apply_scale.py` | Applies a measured real-world width to pin a camera's metric scale. |

### Analysis and output tools

| File | Purpose |
|---|---|
| `src/traffic_heatmap.py` | Accumulates where vehicles actually travel — used to validate polygons empirically. |
| `src/validate_detection.py` | Detection sanity check with annotated sample frames and class distribution. |
| `src/render_demo.py` | Presentation-grade annotated MP4 with live LOS shading and metrics panel. |
| `scripts/download_models.py` | Fetches UVH-26 weights from HuggingFace and verifies the class list. |

### Data and outputs

| Path | Contents |
|---|---|
| `configs/cameras.json` | All lane/zone polygons and scale models |
| `outputs/logs/*_metrics.csv` | Per-window metrics, 25 columns |
| `outputs/annotated/*_demo.mp4` | Presentation videos |
| `outputs/frames/reference/` | Reference + grid frames |
| `outputs/frames/calibration/` | Zone overlay previews |
| `outputs/frames/heatmap/` | Traffic heatmaps (overall and per vehicle class) |
| `outputs/frames/measurement_guide/` | Marked-up frames for the scale measurement |

---

## 11. How to run it

### Setup

```bash
pip install -r requirements.txt
python scripts/download_models.py        # UVH-26 YOLO11 weights, ~55 MB
python src/reference_frames.py           # one clean frame per camera (the Site
                                         # detail overlays and the Calibration
                                         # guide images read these)
python tests/verify_system.py            # 16 checks; recomputes every quoted figure
```

**If the dashboard shows no scene images**, `reference_frames.py` has not been run — the overlays are drawn onto those frames. **If a detection button reports missing weights**, run `download_models.py`; every other view works without them, because they read the metrics CSVs. **If the max-pressure track reports "FLUID not found"**, set `FLUID_ROOT` to wherever the dataset was downloaded.

Needs an NVIDIA GPU for practical throughput.

### Full pipeline

```bash
# 1. reference frames + first-pass calibration
python src/reference_frames.py
python src/seed_calibration.py

# 2. (optional) refine polygons by hand
python src/calibrate.py rokadiya_hanuman

# 3. analyse — writes outputs/logs/<camera>_metrics.csv
python src/analyze.py --all --stride 4

# 4. dashboard
streamlit run src/dashboard.py

# 5. presentation videos
python src/render_demo.py --junctions --seconds 60
```

### Pinning the metric scale (promotes speeds to measured values)

```bash
python src/measurement_guide.py --all
# measure the marked lines on site, or via Google Maps satellite "Measure distance"
python src/apply_scale.py gauravpath --row 400 --width-m 7.5 --row2 1000 --width2-m 7.5
# then mark it verified in the dashboard's Calibration view (or it is
# recorded automatically when you apply the width there) and re-run analyze
```

### Diagnostics

```bash
python src/validate_detection.py gauravpath   # is detection sane?
python src/traffic_heatmap.py --all           # where does traffic actually go?
python src/calibrate_occupancy.py             # absolute occupancy bounds
```

---

## Summary

We built a complete CCTV-to-decision pipeline for Surat traffic that delivers all four PS-08 requirements, running faster than real time on a laptop GPU — plus a second signal controller, validated on a dataset obtained for that purpose.

**The architecture in one line:** *Surat's junctions run Webster's; max-pressure is validated on FLUID; neither borrows from the other, and the test suite fails if that stops being true.*

The three things that distinguish it from a standard YOLO-counting project:

1. **It detects the vehicles that are actually on Surat's roads** — weights fine-tuned on Indian traffic CCTV, auto-rickshaws as a first-class class, density in the PCU units Indian capacity standards are written in, saturation flow from IRC:SP:41 and pedestrian minimum green from IRC:103.

2. **It stays correct when detection breaks down.** In the densest scenes the detector returns *fewer* boxes, not more — so a naive system would rank the city's worst gridlock as its emptiest road. The texture-based occupancy path and explicit saturation flagging fix that, and the correct network ranking is the proof.

3. **It offers two controllers and is honest about which one the data can support.** Max-pressure is implemented in full and refuses to name a phase on Surat, where no camera observes a receiving link — because substituting zero for that term does not give a degraded max-pressure, it gives greedy queue control, which Varaiya proves is not stabilising. So it is validated on FLUID instead, where the whole intersection is visible, and on those real signals it makes measurably better phase choices.

**Headline results.**

*Surat (Webster's):* adaptive signal timing takes the busier approach from **x = 1.01, over capacity, to 0.929** — a claim no delay model can argue with, validated against Webster's theory two independent ways; **31 evidence-backed bottleneck alerts**; a **network congestion ranking that correctly orders all seven sites**, which only exists because of the occupancy fallback.

*FLUID (max-pressure):* on three real signalised intersections, the recorded fixed-time plans spend **41–66% of their green on a phase with nothing to relieve**, against 1.5–16% for max-pressure — and max-pressure's choices relieve **2–18× more queue** per second of green.

**And where the data does not support a claim, the system says so** rather than printing a confident number it cannot stand behind — the uncalibrated metric scale is marked `~` throughout, max-pressure refuses on Surat, and every figure in this report is recomputed by `tests/verify_system.py`, which fails if the report and the code ever disagree.
