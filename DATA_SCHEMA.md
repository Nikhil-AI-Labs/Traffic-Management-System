# Data Schema

E-Rakshak Hackathon 2026 · PS-08 · *Surat Traffic Intelligence*

> **On "database schema":** this system uses **no database**. It is a batch
> measurement pipeline whose durable state is a set of flat files — CSV for
> measurements, JSON for configuration. That is a deliberate choice, not an
> omission: the pipeline is write-once-read-many, a full run produces 2,451 rows,
> and committing the results as CSV is what lets the dashboard deploy and the
> figures be reproduced without redistributing 31 minutes of source video.
> The equivalent schema is documented below in full.
>
> A production deployment ingesting continuous RTSP would want PostGIS or
> TimescaleDB; the table below maps to it one-to-one, with
> `(camera_id, zone_id, window_start_s)` as the natural primary key.

---

## 1. Measurement table — `outputs/logs/*_metrics.csv`

One row per **zone**, per **time window** (2 s by default). Written by
`src/analyze.py`; produced by `MetricsEngine` in `src/metrics.py`.

Files: one per camera (`<camera_id>_metrics.csv`) plus a combined
`all_metrics.csv`. **2,451 rows** across 7 cameras and 21 zones.

**Primary key:** `(camera_id, zone_id, window_start_s)`

### Identity

| Column | Type | Description |
|---|---|---|
| `camera_id` | string | Camera key, joins to `configs/cameras.json`. One of the 7 dataset cameras, or a session id for an uploaded clip. |
| `zone_id` | string | Zone key within that camera, e.g. `lane_1`, `zone_2`. |
| `zone_label` | string | Human-readable zone name, e.g. `Lane 1 (median)`. |
| `zone_kind` | string | `lane` \| `carriageway` \| `pedestrian` \| `parking` \| `platform`. Governs which detectors and metrics apply. |
| `window_start_s` | float | Window start, seconds from clip start. |
| `window_end_s` | float | Window end. **Always > `window_start_s`** — asserted by the verification suite. |

### Counts

| Column | Type | Description |
|---|---|---|
| `n_detections_mean` | float | Mean detection count per frame in this window. |
| `counts_by_group` | JSON string | Mean count per vehicle group, e.g. `{"Two-wheeler": 4.2, "Auto": 1.8}`. Groups: Two-wheeler, Auto, Car, LCV/Tempo, Bus/Truck, Other. |
| `unique_tracks` | int | Distinct ByteTrack IDs seen. **Not a vehicle count** — IDs are reissued when a track drops (see `flow_pcu_per_hr`). |
| `pcu_mean` | float | Mean Passenger Car Units present, per IRC:106-1990. The count that matters. |

### Density — *scale-dependent*

| Column | Type | Description |
|---|---|---|
| `density_pcu_per_100m2` | float \| null | PCU per 100 m². Null when the zone has no real-area estimate. |
| `density_pcu_per_km_lane` | float \| null | The unit traffic engineers quote. `= density_pcu_per_100m2 × 35`. |
| `real_area_m2` | float \| null | Zone ground area, from the perspective scale model or a surveyed value. |

> ⚠️ These three carry an **unquantified scale factor** unless `scale_verified`
> is true. The dashboard prefixes them `~` and shows a persistent banner.

### Dynamics

| Column | Type | Description |
|---|---|---|
| `mean_speed_kmph` | float \| null | Mean tracked ground speed. Null when no track had enough history. Scale-dependent. |
| `stopped_fraction` | float | Fraction of tracked vehicles below 4 km/h. **0–1.** |
| `queue_length_pcu` | float | PCU standing still. Scale-free — this is what feeds max-pressure. |

### Flow

| Column | Type | Description |
|---|---|---|
| `arrivals` | int | New track IDs this window. Diagnostic only — unreliable, see below. |
| `flow_pcu_per_hr` | float | Flow from **q = k·v**, not from counting IDs. ByteTrack reissues IDs constantly in dense mixed traffic, which inflated ID-counted flow roughly tenfold — to tens of thousands of PCU/hr against a real saturation flow near 1,800. |

### Occupancy

| Column | Type | Description |
|---|---|---|
| `box_occupancy` | float | Detection-box area ÷ zone area. **May exceed 1.0** when boxes overlap — that is the occlusion signal, not an error, and it is never clamped. Do not read as a fraction. |
| `structural_occupancy` | float | Texture-based occupancy (Canny edge density) on a fixed **0–1** scale shared by every camera. Survives detector collapse. |

### State

| Column | Type | Description |
|---|---|---|
| `saturated` | bool | Counting can no longer be trusted in this zone. Either high density + heavy mutual overlap, **or** high texture with near-zero box coverage (the collapse case). |
| `measurement_mode` | string | `detection` \| `occupancy` — which path governs this row. |
| `scale_verified` | bool | Whether this camera has a real surveyed width. **False for all 7** in the shipped data. |
| `los` | string | Level of service: `Free` \| `Moderate` \| `Heavy` \| `Jam`. |
| `congestion_index` | float | **0–1**, the single cross-site comparable number. Drives the network ranking. |

### Invariants (asserted by `tests/verify_system.py`)

```
window_end_s          >  window_start_s        (no zero-length windows)
mean_speed_kmph       ∈  [0, 120]
structural_occupancy  ∈  [0, 1]
congestion_index      ∈  [0, 1]
density_pcu_per_km_lane ∈ [0, 400]
```

---

## 2. Camera & zone configuration — `configs/cameras.json`

Keyed by `camera_id`. Written by `seed_calibration.py` / `calibrate.py`.

```jsonc
{
  "gauravpath": {
    "camera_id":  "gauravpath",
    "mode":       "junction",        // junction | area
    "frame_size": [1920, 1080],
    "notes":      "",
    "scale_model": {                 // null when uncalibrated
      "type":            "perspective_v1",
      "y_horizon":       -133.98,    // fitted horizon row (may be off-frame)
      "c":               10.87,      // lateral scale constant
      "focal_px":        1663.0,     // assumption: ~60° HFOV at 1080p
      "camera_height_m": 7.0,        // assumption: pole height
      "calibration": {               // the two rows the fit came from
        "y_near": 1000.0, "px_width_near": 730.0,
        "y_far":   200.0, "px_width_far":  215.0,
        "real_width_m": 7.0
      }
    },
    "zones": [
      {
        "id":                "lane_1",
        "label":             "Lane 1 (median)",
        "polygon":           [[x, y], "… 26 points"],  // image space
        "kind":              "lane",
        "approach":          "Towards Kargil Chowk",   // null for area zones
        "real_area_m2":      null,      // surveyed area, overrides the model
        "lanes_equivalent":  1.0,       // for zones spanning several lanes
        "notes":             ""
      }
    ]
  }
}
```

**Scale model.** Ground scale is *anisotropic* — lateral metres-per-pixel scales
as Z, longitudinal as Z². Treating them as equal under-reads approach speeds by
2× or more. `focal_px` and `camera_height_m` are the two assumptions this model
carries; everything derived from them is reported as approximate.

---

## 3. Operator state — `configs/calibration_state.json`

Runtime settings that must survive a restart. Written by the dashboard, never
edited by hand.

```jsonc
{
  "scale_verified": { "gauravpath": true },   // per-camera, promotes metric figures
  "optimization_mode": "webster"              // webster | max_pressure
}
```

---

## 4. Derived artifacts (generated, not committed)

| Path | Produced by | Contents |
|---|---|---|
| `outputs/frames/reference/<cam>.jpg` | `reference_frames.py` | One clean frame per camera. **Committed** — the overlays draw onto these. |
| `outputs/frames/heatmap/<cam>_heatmap.jpg` | `traffic_heatmap.py` | Movement density, used to validate traced polygons. |
| `outputs/annotated/<cam>_annotated.mp4` | `analyze.py --video` | LOS-shaded zones with live detections. |
| `outputs/fluid_cache/` | `fluid.py` | Cached FLUID queue tables and derived geometry. |
| `models/weights/…` | `scripts/download_models.py` | UVH-26 YOLO11 weights (~55 MB). |

---

## 5. FLUID input schema (external)

Read-only; consumed by `src/fluid.py`. Not redistributed here — see
`ATTRIBUTION.md` for the DOI.

| File | Per row |
|---|---|
| `<site>/derived_data/traj/<rec>_Traj.csv` | Per-vehicle, per-frame: world x/y in metres, speed, class, entry/exit arm and turn annotation. |
| `<site>/derived_data/signal/<rec>_signal.csv` | Per-second recorded signal state, by `direction` + `turn`. |
| `<site>/derived_data/map/<site>.tif` | Orthorectified aerial GeoTIFF, 3840×2160, same pixel frame as the trajectories. |

Derived by our adapter: approach queue `x_l`, receiving-link queue `x_m`,
measured turn ratios `r_mk`, and the recovered phase structure.

---

## 6. REST API

**None.** The system ships a Streamlit dashboard, not a service API — there is
no HTTP surface beyond Streamlit's own. The programmatic interface is the Python
module API (`analyze.analyze()`, `signal_opt.optimise()`, `maxpressure.decide()`,
`fluid.replay()`), documented by docstring in each module and exercised by
`tests/verify_system.py`.
