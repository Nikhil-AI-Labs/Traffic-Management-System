# Deployment

E-Rakshak Hackathon 2026 · PS-08 · *Surat Traffic Intelligence*

The dashboard deploys as an ordinary small Python web app: **8.6 MB repo, no
GPU, no database, no model weights, no paid service.** This document says how,
and — just as importantly — what a deployed instance can and cannot show.

---

## 1. Why this deploys cheaply

Detection is the expensive part of any vision system, and it has **already run**.
The 2,451 measurement windows in `outputs/logs/` are committed, so the hosted
app reads results rather than computing them.

| | Local (full pipeline) | Deployed (dashboard) |
|---|---|---|
| Dependencies | `requirements-full.txt` (~2 GB, torch + ultralytics) | `requirements.txt` (~150 MB) |
| GPU | RTX 3050 6 GB | none |
| Model weights | 55 MB UVH-26 | none |
| Source video | 31.5 min of 1080p | none |
| Works out of the box | after `download_models.py` | **immediately** |

This is what makes it fit a free tier at all. Two code properties enforce it:

1. **`requirements.txt` excludes torch, torchvision and ultralytics.**
2. **`src/detector.py` imports `ultralytics` lazily**, inside
   `VehicleDetector.__init__`. `Detection` and `FrameResult` are plain
   dataclasses that `metrics` → `ingest` → `dashboard` all depend on, so a
   module-level import would drag a deep-learning stack into every page load.
   With the lazy import, the dashboard's entire import graph resolves without it.

> **If you change either, the deploy breaks.** Slimming requirements without the
> lazy import gives `ModuleNotFoundError: ultralytics` on boot; keeping torch in
> requirements exhausts a 1 GB free tier.

---

## 2. What a deployed instance shows

Be clear about this before demoing to anyone.

| Feature | Hosted | Why |
|---|---|---|
| **All 8 Webster views** | ✅ full | Driven entirely by committed CSVs |
| Network ranking, LOS bands, congestion index | ✅ | |
| Signal timing — cycle, splits, delay, saturation | ✅ | |
| 31 bottleneck alerts, 21 forecasts | ✅ | |
| Scene overlays, calibration view | ✅ | Reference frames are committed |
| **Movement heatmap panel** | ⚠️ notice | `outputs/frames/heatmap/` not committed — panel shows a "not rendered yet" note |
| **Annotated video panel** | ⚠️ absent | MP4s not committed (size) |
| **"Analyse footage" upload** | ⚠️ disabled | Needs the detector; degrades via `detector_ready()` to an explanatory notice, not a crash |
| **All 5 max-pressure views** | ⚠️ notice | FLUID is 18 GB and cannot be hosted. Views render an amber "Real-intersection data not found" callout |

The degradations are **designed**, not accidental — each renders an explanation
rather than blanking or raising. That behaviour is asserted by
`tests/verify_system.py`.

To demo the FLUID track, run locally with `FLUID_ROOT` set.

---

## 3. Streamlit Community Cloud (recommended)

Free, no card, purpose-built for this. ~10 minutes.

**Prerequisites**
- The GitHub repo must be **public** (or explicitly grant Streamlit access).
- All changes pushed — the host builds from GitHub, not your disk.

**Steps**
1. [share.streamlit.io](https://share.streamlit.io) → sign in with GitHub
2. **New app** → select this repository and branch
3. **Main file path:** `src/dashboard.py` ← the setting most often set wrong
4. *Advanced settings* → **Python 3.12** or **3.13**
5. **Deploy**

**Verify after deploy** — click all 8 Webster views; confirm Signal timing shows
`1.01 → 0.93` and the Network view lists 7 sites.

**Notes**
- Apps **sleep after ~12 h idle** and wake in a few seconds. Visit the URL shortly
  before any judging so it is warm.
- ~1 GB RAM. Comfortable now that torch is gone; not if it returns.
- `src/dashboard.py` inserts its own directory on `sys.path`, so no wrapper or
  packaging change is needed.

---

## 4. Hugging Face Spaces (fallback)

Free CPU Basic (2 vCPU, 16 GB RAM), no card. More headroom than Community Cloud
and it does not sleep as aggressively.

1. Create a Space → SDK **Streamlit**
2. Push this repo to the Space remote
3. Add to `README.md` front-matter:

```yaml
---
title: Surat Traffic Intelligence
sdk: streamlit
sdk_version: 1.44.0
app_file: src/dashboard.py
pinned: false
---
```

The extra RAM means you *could* include `requirements-full.txt` and enable live
detection — but a CPU-only Space runs YOLO11 at well under 1 FPS, so the upload
view would time out in front of a judge. Keep the slim set.

---

## 5. Docker (self-hosted)

No Dockerfile is committed, because none of the recommended hosts needs one.
If you self-host:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
HEALTHCHECK CMD curl -f http://localhost:8501/_stcore/health || exit 1
CMD ["streamlit", "run", "src/dashboard.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
```

`python:3.12-slim` needs no `libgl1` because `requirements.txt` pins
**opencv-python-headless**. If you switch to `opencv-python`, add
`RUN apt-get update && apt-get install -y libgl1`.

---

## 6. Local

```bash
pip install -r requirements.txt
streamlit run src/dashboard.py
```

Serves on `http://localhost:8501`. Health endpoint: `/_stcore/health` → `ok`.

For the full pipeline (new footage, calibration tool, FLUID track) use
`requirements-full.txt` instead — **not both**, as they pin different OpenCV
builds. See the header of either file.

---

## 7. Configuration

No secrets, no API keys, no environment file is required.

| Variable | Default | Purpose |
|---|---|---|
| `FLUID_ROOT` | auto-discovered | Path to the extracted FLUID dataset. Unset on a host — the max-pressure track then shows its "not found" callout. |
| `OPENCV_FFMPEG_LOGLEVEL` | `-8` | Set by `config.py` before cv2 loads, to suppress harmless H.264-in-AVI container chatter. |

`.streamlit/config.toml` carries the theme and is applied automatically.

---

## 8. Known issue for public instances

`config.save_optimization_mode()` writes the control-strategy toggle to
`configs/calibration_state.json` **on disk**, which on a hosted app is shared by
every visitor — two people viewing simultaneously will flip each other's view.

Single-viewer demos are unaffected. For a public URL with concurrent judges,
move the toggle to `st.session_state`. Left as-is deliberately: on a single
operator workstation, persisting the choice across restarts is the correct
behaviour, and that is the deployment target the code was written for.

---

## 9. Pre-deploy checklist

```
[ ] GitHub repo is public and the link resolves in a private browser window
[ ] All changes committed and pushed
[ ] requirements.txt still excludes torch / torchvision / ultralytics
[ ] src/detector.py still imports ultralytics lazily
[ ] Main file path set to src/dashboard.py
[ ] Python 3.12 or 3.13 selected
[ ] After deploy: all 8 Webster views open without error
[ ] After deploy: Signal timing shows 1.01 → 0.93
[ ] URL visited shortly before judging so the app is awake
```
