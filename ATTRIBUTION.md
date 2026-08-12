# Attribution & Licensing

E-Rakshak Hackathon 2026 · Problem Statement PS-08 · *Surat Traffic Intelligence*

This document states exactly what in this project is third-party and what was
written for this hackathon, because the two should not be guessed at. Every
external library, dataset, pre-trained model and published standard is listed
with its licence and what it is used for.

---

## 1. What is ours

Every line of Python in `src/`, `scripts/` and `tests/` — **29 files,
~13,700 lines**, of which 27 modules and ~13,100 lines are `src/` — was
written for this hackathon. No part of it is a fork,
template or tutorial adaptation. Specifically, the following were designed and
implemented here rather than taken from a library:

| Component | File | What it is |
|---|---|---|
| **Structural occupancy index** | `metrics.py` | Texture-based congestion measure (Canny edge density against absolute cross-camera bounds) that keeps working when object detection collapses under occlusion. The response to Finding 1 below. |
| **Two-signature saturation test** | `metrics.py` | Detects when counting has stopped being trustworthy — including the "full scene, empty count" case that an overlap test structurally cannot catch. |
| **Webster's implementation** | `signal_opt.py` | Cycle, splits, dual delay models (Webster 3-term + HCM control delay with automatic switchover at x = 0.85), IRC saturation flow, per-approach pedestrian minima, and a controller recommender driven by measured demand-split variability. |
| **Max-pressure + observability gate** | `maxpressure.py` | Varaiya's controller, plus the refusal logic that reports `greedy-queue` and declines to name a phase when the downstream term is unobservable. The gate is our contribution, not the algorithm. |
| **FLUID adapter** | `fluid.py` | Queue extraction, turn-ratio measurement, phase recovery from recorded signal state, replay harness, world-to-pixel affine fit, and the aerial scene layer. |
| **Point-queue network simulator** | `network_sim.py` | Two junctions in series with finite receiving-link storage, so spillback exists and the two controllers can be compared fairly under identical lost time. |
| **Bottleneck detectors** | `bottleneck_detect.py` | Five sustained-pattern detectors, each emitting the evidence that raised it plus a specific engineering recommendation. |
| **Forecasting** | `forecast.py` | Damped-trend short-horizon projection with prediction intervals. |
| **Perspective scale model** | `zones.py` | Anisotropic ground-scale model separating lateral from longitudinal metres-per-pixel — the correction that stops speeds being under-read by 2×. |
| **Dashboard, theme, charts** | `dashboard.py`, `theme.py`, `charts.py` | Two-track operator UI, and a hand-built colour system validated for colour-vision separation in both light and dark. |
| **Verification suite** | `tests/verify_system.py` | 18 end-to-end checks, including the architectural separation assertion and a guard that recomputes every documented figure and fails if the docs drift. |

The **engineering judgements** are likewise ours: choosing PCU over raw counts,
choosing width-based saturation flow because lane count is unrecoverable from
this data, splitting the cameras into junction and area modes rather than
forcing lane density onto market streets, and refusing to run max-pressure on
data that cannot support it.

---

## 2. Pre-trained model

| | |
|---|---|
| **Model** | UVH-26-MV-YOLOv11-S (and -X) |
| **Source** | HuggingFace `iisc-aim/UVH-26`, revision `v1.0` |
| **Fetched by** | `scripts/download_models.py` |
| **Weights licence** | **AGPL-3.0** (inherited from Ultralytics YOLO) |
| **Dataset licence** | **CC-BY-4.0** |
| **Used for** | Vehicle detection — 14 India-specific classes |

**We did not train this model.** It is used as published. It was chosen because
it is fine-tuned on 26,646 annotated 1080p frames of Indian Safe-City CCTV —
the same domain as the Surat footage — and carries classes COCO lacks entirely,
most importantly the three-wheeler (auto-rickshaw), which dominates most of
these clips. Using it removed the need to train on an unlabelled dataset.

---

## 3. Datasets

Neither dataset is committed to this repository. See README for how to obtain
each.

| Dataset | Source | Licence | Role |
|---|---|---|---|
| **Surat CCTV** | Provided with problem statement PS-08 | Organiser-provided | 7 clips, 1920×1080 @ 20 fps, ≈31.5 min. The operational dataset — every Surat figure comes from it. |
| **FLUID** | figshare DOI [`10.6084/m9.figshare.29974954`](https://doi.org/10.6084/m9.figshare.29974954) | **CC BY 4.0** | 14 drone recordings of 3 real signalised intersections, Xuancheng, Anhui, China. Used *only* to validate the max-pressure implementation, because it contains the downstream-queue term the Surat cameras structurally cannot observe. |

> **FLUID is Chinese traffic.** It validates that the *controller implementation*
> is correct on data containing the terms it needs. No claim derived from it is
> transferred to Surat's fleet, and the dashboard keeps the two datasets on
> separate tracks that cannot render each other's data.

The **derived metrics CSVs** in `outputs/logs/` (2,451 measurement windows) are
our own output, produced by running our pipeline over the Surat clips. They are
committed so results are reproducible without redistributing the source video.

---

## 4. Third-party libraries

### Runtime (deployed dashboard) — `requirements.txt`

| Library | Licence | Used for |
|---|---|---|
| Streamlit | Apache-2.0 | Dashboard UI |
| Plotly | MIT | All interactive figures |
| pandas | BSD-3-Clause | Metrics tables and aggregation |
| NumPy | BSD-3-Clause | Numerics throughout |
| SciPy | BSD-3-Clause | Forecast fitting |
| opencv-python-headless | Apache-2.0 | Frame decode, polygon geometry, overlay rendering |

### Additional, full pipeline — `requirements-full.txt`

| Library | Licence | Used for |
|---|---|---|
| **Ultralytics** | **AGPL-3.0** | YOLO11 inference + ByteTrack tracking |
| PyTorch / torchvision | BSD-3-Clause | Inference backend (CUDA 12.1 locally) |
| huggingface_hub | Apache-2.0 | Fetching the UVH-26 weights |
| lap | BSD-2-Clause | Linear assignment, used by ByteTrack |
| opencv-python | Apache-2.0 | GUI build — `calibrate.py` needs window bindings |

### Algorithms used as published

| Algorithm | Source | Where |
|---|---|---|
| **ByteTrack** | Zhang et al., ECCV 2022 (via Ultralytics) | Multi-object tracking |
| **Canny edge detector** | Canny 1986 (via OpenCV) | Structural occupancy |

---

## 5. Standards and published methods

These are engineering standards and academic methods we **implemented**, not
code we imported. Each is cited where it is used.

| Standard / method | Applied to |
|---|---|
| **IRC:106-1990** — Capacity of Urban Roads in Plain Areas | PCU conversion factors for all 14 vehicle classes |
| **IRC:SP:41-1994** — Urban road intersections | Saturation flow, `S = 525·W` (width-based) |
| **IRC:103-2012** — Pedestrian Facilities | Minimum green, computed per approach at 0.8 m/s |
| **MUTCD** (2009 §4E.06 / 2023 Ch. 4I) | Cross-checked as the comparison pedestrian standard |
| **Webster (1958)** — traffic signal settings | Optimal cycle, green splits, 3-term delay |
| **HCM control delay** | Delay above x = 0.85, where Webster's diverges |
| **Varaiya (2013)**, *Transportation Research Part C* 36:177–195 | Max-pressure control law and scale-invariance proof |
| **Gregoire et al.**, arXiv:1309.6484 | Finite-queue limits of back-pressure |
| **Wongpiromsarn et al.**, ITSC 2012 | Max-pressure throughput optimality |
| **Tassiulas & Ephremides (1992)** | Back-pressure origin |
| **Gayah & Levin** | Isolated-intersection sink-link convention |
| **Justo & Tuladhar** — Ahmedabad field study | Saturation-flow band, 636·W–1283·W |
| **Little's law** | Delay accounting in the simulator |

---

## 6. Licence of this project

```
Copyright (C) 2026  [TEAM NAME]

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or (at
your option) any later version.

This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero
General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.
```

> ⚠️ **`[TEAM NAME]` above is a placeholder.** Replace it with the copyright
> holder — your team or institution name. AGPL-3.0 expects an identifiable
> copyright holder; `LICENSE` itself carries only the FSF's copyright on the
> licence text, which is correct and should not be edited.

The application code is released under **AGPL-3.0** — not a free choice, but a
requirement: Ultralytics and the YOLO weights are AGPL-3.0, and linking to them
makes the combined work AGPL-3.0. See `LICENSE` for the full terms.

Two consequences worth stating plainly:

- **The deployed dashboard does not link Ultralytics.** `requirements.txt`
  excludes it, and `src/detector.py` imports it lazily, so a hosted instance
  running the Webster track carries no AGPL detection component. The repository
  as a whole remains AGPL-3.0 because the full pipeline does.
- **A commercial deployment** would need either an Ultralytics commercial
  licence, or a swap to the Apache-2.0 RT-DETRv2 / DAMO-YOLO variants that the
  same UVH-26 release ships. This is a licence change, not a code rewrite.

Documentation, figures and the derived metrics CSVs are **CC BY 4.0**.

---

## 7. Correct citation

If any part of this work is referenced, please also cite the two external
resources it depends on most:

```bibtex
@misc{uvh26,
  title  = {UVH-26: Urban Vehicle Heterogeneity Dataset},
  author = {IISc AIM Lab},
  note   = {HuggingFace iisc-aim/UVH-26, CC-BY-4.0},
  year   = {2025}
}

@misc{fluid2025,
  title  = {FLUID: A Fine-grained Lightweight Urban Signalized-intersection
            Dataset with Dense conflict trajectories},
  doi    = {10.6084/m9.figshare.29974954},
  note   = {CC BY 4.0},
  year   = {2025}
}

@article{varaiya2013,
  title   = {Max pressure control of a network of signalized intersections},
  author  = {Varaiya, Pravin},
  journal = {Transportation Research Part C},
  volume  = {36}, pages = {177--195}, year = {2013}
}
```
