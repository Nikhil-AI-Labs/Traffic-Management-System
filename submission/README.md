# Stage-2 Submission

E-Rakshak Hackathon 2026 · Problem Statement PS-08 · *Surat Traffic Intelligence*

Every item the Stage-2 guideline lists, and where it is.

---

## ⚠️ Before submitting — two things only you can do

1. **Fill in the team name and members.** They are placeholders in two files:
   - `submission/build_deck.py` — lines `TEAM_NAME` / `TEAM_MEMBERS`
   - `submission/build_report.py` — same two constants

   Then rebuild both:
   ```bash
   python submission/build_deck.py     # then re-export the PDF
   python submission/build_report.py
   ```

2. **Make the GitHub repository public.** It currently returns 404 to anyone not
   signed in as the owner. The guideline requires *"Repository should be
   accessible"* and *"Ensure repository links remain accessible until the
   completion of the event."*

---

## Component 1 — Presentation

| Requirement | Status |
|---|---|
| Maximum 10–15 slides | ✅ 15 |
| PPTX format | ✅ `Surat-Traffic-Intelligence-PS08.pptx` |
| PDF format | ✅ `Surat-Traffic-Intelligence-PS08.pdf` |

Every slide the guideline names, in order:

| # | Slide | Guideline item |
|---|---|---|
| 1 | Title | Team name and members |
| 2 | PS-08 — what is being asked | Problem statement |
| 3 | What this data can and cannot support | Problem understanding |
| 4 | Detection counts fewer vehicles in gridlock | *(key discovery)* |
| 5 | Two controllers, two datasets | Proposed solution |
| 6 | Architecture — frame to recommendation | System architecture diagram |
| 7 | Process flow, end to end | Workflow / process flow |
| 8 | AI/ML models | AI/ML models |
| 9 | One dashboard, two tracks, 13 views | Key features |
| 10 | Results · Surat | *(results)* |
| 11 | Results · FLUID | *(results)* |
| 12 | Innovation & unique value | Innovation and USP |
| 13 | Technology stack | Technology stack |
| 14 | Eleven bugs found and fixed | Challenges faced |
| 15 | Future scope & limitations | Future scope |

> The deck is **generated** by `build_deck.py`, which recomputes the headline
> figures from the live pipeline at build time — so a slide cannot drift from
> the code.

---

## Component 2 — Technical repository

| Requirement | Where |
|---|---|
| Complete source code | `src/` — 27 modules, ~13,700 lines |
| README with setup and installation | [`README.md`](../README.md) |
| Project folder structure | [`README.md`](../README.md) § Repository layout |
| System architecture documentation | [`REPORT.md`](../REPORT.md) §4 · deck slide 6 |
| API documentation | [`DATA_SCHEMA.md`](../DATA_SCHEMA.md) §6 — **no REST API**; the interface is the Python module API, documented by docstring |
| Database schema | [`DATA_SCHEMA.md`](../DATA_SCHEMA.md) — **no database**; the equivalent CSV/JSON contract is documented in full |
| Dependencies and requirements | `requirements.txt` (deploy) · `requirements-full.txt` (pipeline) |
| Deployment instructions | [`DEPLOYMENT.md`](../DEPLOYMENT.md) |
| Third-party libraries, APIs, datasets, models | [`ATTRIBUTION.md`](../ATTRIBUTION.md) |
| Own work vs open-source components | [`ATTRIBUTION.md`](../ATTRIBUTION.md) §1 |
| Licence | [`LICENSE`](../LICENSE) — AGPL-3.0 |
| Version control platform | GitHub |

Extra, not required but worth pointing a judge at:

| | |
|---|---|
| [`REPORT.md`](../REPORT.md) | Full engineering report — every design decision, every bug, every number derived |
| `tests/verify_system.py` | 18 end-to-end checks, including one that recomputes every documented figure and fails if the docs drift |

---

## Component 3 — Product demonstration

| Requirement | Status |
|---|---|
| Working prototype | ✅ `streamlit run src/dashboard.py` |
| **2–3 minute demonstration video (MP4, 1080p)** | ⏳ **script ready — needs recording** → [`VIDEO_SCRIPT.md`](VIDEO_SCRIPT.md) |
| Hosted application URL *(if available)* | ⚪ optional — see [`DEPLOYMENT.md`](../DEPLOYMENT.md) |
| Login credentials | ⚪ N/A — no authentication |
| APK / installer | ⚪ N/A — web application |

**The video is the only mandatory item still outstanding.** `VIDEO_SCRIPT.md`
has a shot-by-shot script timed to 2:45, with a pre-recording checklist and
alternates for 2:00 and 3:00.

> ⚠️ Set `FLUID_ROOT` before recording, or five of the thirteen views will show
> a "data not found" callout on camera.

---

## Judging criteria — where each is addressed

| Criterion | Weight | Strongest evidence |
|---|---|---|
| Innovation | 25 | The observability gate (deck 12, report §6.1); occlusion-proof occupancy (deck 4, report §2.2) |
| Technical implementation | 25 | 27 modules; 18-check verification suite; two controllers with a test-enforced boundary |
| Real-world impact | 25 | Saturation 1.01 → 0.929 — an over-capacity approach brought back under; 31 evidence-backed alerts; runs on cameras the city already owns |
| Usability | 15 | 13-view operator dashboard; every chart has a table beside it; every estimate flagged as one |
| Presentation & demonstration | 10 | This deck, this report, and the video script |

---

## Files here

```
submission/
├── README.md                                    ← this file
├── Surat-Traffic-Intelligence-PS08.pptx         15 slides
├── Surat-Traffic-Intelligence-PS08.pdf          same, as PDF
├── Surat-Traffic-Intelligence-PS08-Report.pdf   7-page standalone report
├── VIDEO_SCRIPT.md                              2:45 shot-by-shot script
├── build_deck.py                                regenerates the deck
└── build_report.py                              regenerates the report
```
