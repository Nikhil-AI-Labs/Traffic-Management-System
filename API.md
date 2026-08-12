# API Reference

E-Rakshak Hackathon 2026 · PS-08 · *Surat Traffic Intelligence*

> **There is no REST API.** The system ships a Streamlit dashboard, not a
> service, so the only HTTP surface is Streamlit's own (`/` and
> `/_stcore/health`). Exposing a network API was not in scope: the pipeline is
> batch, and the deliverable is an operator view.
>
> The programmatic interface is the **Python module API** documented below. It
> is stable enough to be depended on — `tests/verify_system.py` calls it
> directly, so a signature change here fails the suite.

All examples assume `sys.path` includes `src/`:

```python
import sys; sys.path.insert(0, "src")
```

**Related:** [`ARCHITECTURE.md`](ARCHITECTURE.md) ·
[`DATA_SCHEMA.md`](DATA_SCHEMA.md) (return-value field meanings)

---

## Quick start

Load the measurements and get an optimised signal plan:

```python
import sys; sys.path.insert(0, "src")
import pandas as pd, config as cfg, signal_opt as so, bottleneck_detect as bn

df = load_metrics()                                     # see the note below
dem  = [so.demand_from_metrics(df, c) for c in cfg.junction_cameras()]
plan = so.optimise([d for d in dem if d])               # Webster-optimal
print(so.format_plan(plan))
# Junction
#   cycle 136s | lost time 8s | Y=0.875 | mean delay 51.4s/PCU
#     GauravPath - Towards Kargil Chowk    green 59.0s  y=0.405  x=0.93
#     Rokadiya Hanuman - Towards Bhatena   green 68.5s  y=0.470  x=0.93

print(f"{len(bn.detect_all(df))} bottleneck alerts")    # 31
```

### ⚠️ Loading the metrics correctly

Do **not** read `outputs/logs/all_metrics.csv` directly, and do **not** glob
`*_metrics.csv`. Both give subtly wrong counts with no obviously wrong number
anywhere:

| Approach | Rows | Alerts | Why |
|---|---|---|---|
| glob `*_metrics.csv` | 4,905 | inflated | `all_metrics.csv` is a concatenation of the others, so every window loads twice |
| `all_metrics.csv` | 2,454 | **32** | stale — holds 3 zero-length windows written before `metrics.py` guarded against them |
| **per-camera + filter** | **2,451** | **31** | correct |

Load them the way the dashboard does:

```python
def load_metrics() -> pd.DataFrame:
    frames = [pd.read_csv(cfg.LOG_DIR / f"{c}_metrics.csv")
              for c in cfg.CAMERAS
              if (cfg.LOG_DIR / f"{c}_metrics.csv").exists()]
    df = pd.concat(frames, ignore_index=True)
    return df[df.window_end_s > df.window_start_s].reset_index(drop=True)
```

A window with `end == start` produces a per-hour rate with a zero denominator,
which is why it is dropped rather than tolerated. Every figure quoted in the
README, report and deck uses the filtered 2,451.

---

## 1. `analyze` — run the pipeline

```python
analyze(camera_id, detector, frames=None, stride=2,
        window_s=2.0, write_video=False, video_fps=10) -> pd.DataFrame
```

Detect → track → assign to zone → measure, over one camera. Writes
`outputs/logs/<camera_id>_metrics.csv` and returns the same rows.

Requires model weights (`scripts/download_models.py`) and the source video.

```python
from detector import VehicleDetector
import analyze
df = analyze.analyze("gauravpath", VehicleDetector(), stride=4)
```

**CLI:** `python src/analyze.py --all --stride 4`

---

## 2. `metrics` — the measurement engine

```python
class MetricsEngine(camera_id, calib, window_s=2.0, compute_structural=True)
    .process(frame_result) -> list[ZoneWindow]   # feed one frame
    .flush(end_t)          -> list[ZoneWindow]   # close the final window
```

Consumes `FrameResult`s, emits one `ZoneWindow` per zone per window. See
[`DATA_SCHEMA.md`](DATA_SCHEMA.md) §1 for every field.

```python
classify_los(density_pcu_per_100m2, occupancy_index, mode,
             scale_verified=False) -> tuple[str, float]
```

Returns `(LOS label, congestion index 0–1)`. Density leads **only** when the
camera has a verified metric scale; otherwise the scale-free occupancy index
does, because bucketing an uncalibrated density into LOS bands would dress
unknown error up as an engineering verdict.

Supporting classes: `SpeedTracker`, `StructuralOccupancy`, `ZoneAccumulator`.

---

## 3. `signal_opt` — Webster's method

The main entry points:

```python
demand_from_metrics(df, camera_id, approach_label=None,
                    window=None) -> ApproachDemand | None
optimise(approaches, junction_name="Junction",
         lost_time_per_phase_s=4.0, delay_model="auto") -> SignalPlan
evaluate_fixed_plan(approaches, cycle_s, green_splits=None, ...) -> SignalPlan
compare(baseline, optimised) -> dict
format_plan(plan) -> str
```

`demand_from_metrics` aggregates a camera's lane rows into one approach demand,
using the **critical (busiest) lane** to set the flow ratio — a signal must
clear the worst lane, not the mean one.

```python
dem  = [so.demand_from_metrics(df, c) for c in js]
base = so.evaluate_fixed_plan(dem, cycle_s=120.0)   # untuned baseline
opt  = so.optimise(dem)                             # Webster-optimal
so.compare(base, opt)
# {'baseline_cycle_s': 120.0, 'optimised_cycle_s': 135.6,
#  'delay_reduction_pct': 3.8, 'feasible': True, ...}
```

### Delay models

```python
websters_delay(cycle_s, green_s, flow, capacity, three_term=True) -> float
hcm_delay(cycle_s, green_s, flow, capacity, T=0.25, k=0.5, I=1.0) -> float
approach_delay(cycle_s, green_s, flow, capacity,
               model="auto") -> tuple[float, str]
```

`model="auto"` switches to HCM above a degree of saturation of **0.85**, where
Webster's random-delay term stops being trustworthy.

> ⚠️ `websters_delay` returns **`math.inf`** at or above capacity. That is
> deliberate — Webster's has no finite answer there and returning one would be
> inventing it. Callers computing a percentage change must handle a non-finite
> baseline.

### Standards

```python
saturation_flow(width_m, factor=525.0) -> float        # IRC:SP:41-1994, S = 525·W
saturation_flow_note(width_m) -> str                   # caveat outside IRC's range
pedestrian_min_green_s(crossing_length_m,
                       walk_speed_mps=0.8,
                       standard="IRC") -> float        # IRC:103-2012
websters_cycle(total_flow_ratio, lost_time_s) -> tuple[float, bool]
common_degree_of_saturation(Y, cycle_s, lost_time_s) -> float   # Y·C/(C−L)
websters_cmin(lost_time_s, Y, Xc=0.90) -> float
```

### Controller recommendation

```python
recommend_controller(df, camera_ids, downstream_observed=False,
                     demands=None) -> ControllerAdvice
approach_split_variability(df, camera_ids) -> float
```

Recommends between the two controllers from measured demand-split variability.
Observability is tested **first**, because it is a fact rather than a preference:
without a downstream queue, max-pressure is unavailable at any confidence.

---

## 4. `maxpressure` — max-pressure control

```python
decide(movements, phases, allow_greedy_fallback=True,
       consistent_scale=True) -> MaxPressureDecision
movements_from_zones(approaches, saturation_flow_pcu_per_hr=1800.0) -> list[Movement]
one_movement_per_phase(movements, min_green_s=10.0, max_green_s=90.0) -> list[Phase]
phase_pressure(phase, movements, allow_unobserved) -> PhasePressure
```

### The observability contract — read before using

`MaxPressureDecision` keeps two answers in **separate fields**:

| Field | Meaning |
|---|---|
| `mode` | `"max-pressure"` \| `"partial"` \| `"greedy-queue"` |
| `usable` | `True` only when `mode == "max-pressure"` |
| `chosen_phase_id`, `chosen_label`, `chosen_pressure` | the **max-pressure** answer. `None` unless every downstream queue was observed |
| `greedy_phase_id`, `greedy_label`, `greedy_pressure` | what **greedy queue control** would serve |

```python
d = MP.decide(movements, phases)
if d.usable:
    serve(d.chosen_phase_id)          # a real max-pressure recommendation
else:
    print(d.mode)                     # 'greedy-queue'
    print(d.chosen_phase_id)          # None — it refuses to name a phase
    print(d.greedy_label)             # available, but only under this name
```

Dropping the downstream term does not give a degraded max-pressure; it gives
greedy queue control, which Varaiya §3.1 Example 2 proves fails to stabilise
demands max-pressure handles. The separation is structural so a caller cannot
present one as the other by accident.

`Movement.queue_out_pcu is None` means **unobserved**, never zero.

### Stepping over time

```python
class ControllerState(current_phase_id=None, time_in_phase_s=0.0,
                      switches=0, switch_margin=0.0)
    .step(decision, dt_s, phases) -> tuple[str | None, bool]
```

Enforces minimum green, maximum green (starvation guard) and hysteresis. Pure
max-pressure re-decides every instant, which produces a signal no pedestrian
could cross.

---

## 5. `bottleneck_detect` — sustained-pattern alerts

```python
detect(df, camera_id) -> list[Alert]
detect_all(df)        -> list[Alert]      # sorted: critical → warning → info
to_frame(alerts)      -> pd.DataFrame
```

Five detectors: congestion bottleneck, standing queue, saturated gridlock,
encroachment, lane imbalance. Each looks for a pattern **sustained over
consecutive windows**, not a single reading.

Every `Alert` carries `evidence` (what triggered it, with the thresholds) and
`recommendation` (a specific engineering action), so a reader can check the
reasoning rather than trust it.

---

## 6. `forecast` — short-horizon projection

```python
forecast_zone(z, camera_id, metric="congestion_index", horizon_s=60.0) -> Forecast
forecast_all(df, metric="congestion_index", horizon_s=60.0) -> list[Forecast]
rising_risks(forecasts, threshold=0.5) -> list[Forecast]
time_profile(df, camera_id, bin_s=10.0) -> pd.DataFrame
camera_summary(df) -> pd.DataFrame
```

Damped trend with prediction intervals. `Forecast.reliable` is False where the
history is too thin to support the projection.

---

## 7. `zones` — geometry and metric scale

```python
load(camera_id, path=cfg.CAMERA_CONFIG) -> CameraCalibration | None
load_all(path) -> dict[str, CameraCalibration]
save(calib, path) -> None
draw_zones(frame, calib, alpha=0.25, show_labels=True, highlight=None) -> np.ndarray
```

```python
build_scale_model(y_near, px_width_near, y_far, px_width_far,
                  real_width_m, focal_px=1663.0, camera_height_m=7.0) -> dict
lateral_m_per_px(model, y)      -> float | None     # across the road,  ∝ Z
longitudinal_m_per_px(model, y) -> float | None     # along the road,   ∝ Z²
ground_displacement_m(model, x0, y0, x1, y1) -> float | None
zone_real_area(zone, scale_model) -> float | None
```

The scale is **anisotropic** — treating lateral and longitudinal pixels as equal
under-reads approach speeds by 2× or more, because vehicles approaching a camera
move almost entirely up the frame.

---

## 8. `fluid` — FLUID dataset adapter

```python
available() -> bool                       # is the dataset present?
recordings(site=None) -> list[Recording]  # 14 with both traj and signal
find(recording_id) -> Recording | None

build_queue_table(rec, hz=1.0, use_cache=True) -> pd.DataFrame
turn_ratios(rec) -> pd.DataFrame          # measured r_mk, not assumed
load_signal(rec) -> pd.DataFrame          # per-second recorded state
movements_at(queues, t, saturation_flow_pcu_per_hr=1800.0, turns=None) -> list[Movement]
controller_phases(sig, queues, min_green_s=10.0, max_green_s=90.0) -> list[Phase]
replay(rec, queues, signal, turns) -> ReplayResult
```

Always guard with `available()` — the dataset is 18 GB and not committed.

```python
if FL.available():
    rec = FL.recordings("FI")[0]
    res = FL.replay(rec, FL.build_queue_table(rec),
                    FL.load_signal(rec), FL.turn_ratios(rec))
    print(res.wasted_green_pct_actual)     # 41.1
```

Scene rendering: `scene_image`, `zone_overlay`, `trajectory_heatmap`,
`annotated_instant`, `world_to_pixel`.

---

## 9. `network_sim` — controller comparison

```python
compare_controllers(scenario="steady", seconds=1800.0, dt=1.0, ...) -> dict
corridor(main_arrival_pcu_per_hr=700.0, cross_arrival_pcu_per_hr=300.0, ...) 
build(scenario="steady", seconds=1800.0, **kw) -> tuple[Network, list[Junction]]
webster_splits(net, junctions, ...) -> tuple[dict, float, float]
```

Scenarios: `"steady"`, `"swing"`, `"incident"`. Controllers:
`FixedTimeController`, `MaxPressureController`, `CyclicMaxPressureController`.

```python
r = NS.compare_controllers(scenario="incident", seconds=1800.0)
r["fixed"].mean_delay_s_per_pcu          # 150.0
r["max_pressure"].mean_delay_s_per_pcu   # 102.9
```

Deterministic — the same arguments give byte-identical results, asserted by the
verification suite. Both controllers are charged the same lost time.

---

## 10. `config` — registry and constants

```python
CAMERAS               # the 7 dataset cameras
camera_meta(cam_id)   # metadata for any id, dataset or uploaded
junction_cameras()    # ['gauravpath', 'rokadiya_hanuman']
area_cameras()
PCU_FACTORS           # IRC:106-1990, per vehicle class
LOS_THRESHOLDS        # PCU per 100 m²
scale_is_verified(camera_id) -> bool
load_optimization_mode() / save_optimization_mode(mode)
register_camera(cam_id, meta)     # add an uploaded clip at runtime
```

---

## Error handling

| Condition | Behaviour |
|---|---|
| Model weights missing | `VehicleDetector.__init__` raises `FileNotFoundError` with the fix command |
| No calibration for a camera | `analyze` raises `RuntimeError` naming `seed_calibration.py` |
| FLUID absent | `available()` returns False; other calls raise `IndexError` — guard first |
| Demand exceeds capacity (Y ≥ 0.95) | `SignalPlan.feasible = False`, `note` explains no cycle can clear it |
| Approach at/over capacity | `websters_delay` returns `math.inf`; use `approach_delay` for the automatic HCM switch |
| Downstream queue unobserved | `decide()` returns `chosen_phase_id = None`, `mode = "greedy-queue"` |
