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

**The deck follows the guideline's own list, in the guideline's own order.**
Every bullet the brief names appears, and each slide's eyebrow is that bullet's
name, so a judge scanning for an item finds it without hunting.

| # | Guideline bullet | Slide title |
|---|---|---|
| 1 | **Team Name and Team Members** | Surat Traffic Intelligence |
| 2 | **Problem Statement** | PS-08 — what is being asked |
| 3 | **Problem Understanding** | What this data can — and cannot — support |
| 4 | *Problem Understanding, continued* | In the worst gridlock, detection counts fewer vehicles |
| 5 | **Proposed Solution** | Two controllers, two datasets, one hard boundary |
| 6 | **Key Features** | One dashboard, two tracks, thirteen views |
| 7 | **Technology Stack** | Chosen for what the data needs |
| 8 | **System Architecture Diagram** | From video frame to signal recommendation |
| 9 | **Workflow / Process Flow** | End to end, in five steps |
| 10 | **AI/ML Models** | The weights matter more than the architecture |
| 11 | *Results* | The operational system — Webster's |
| 12 | *Results* | Max-pressure, validated on real signals |
| 13 | **Innovation and Unique Value Proposition** | Four things a standard pipeline does not do |
| 14 | **Challenges Faced** | Eleven bugs found and fixed — four worth showing |
| 15 | **Future Scope** | What one measurement, and more data, would unlock |

All 12 required bullets are covered. The three italicised slides are additions,
placed where they do the most work: the discovery sits inside problem
understanding because it *is* the understanding, and the two results slides sit
before the innovation claim so that claim arrives already evidenced.

The order lives in one list — `SLIDES` at the bottom of `build_deck.py` — which
pairs each slide with the guideline bullet it answers, so compliance is checkable
by reading that list rather than by clicking through fifteen slides.

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
