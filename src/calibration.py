"""Metric-scale calibration: state, fitting, diagnostics, and guide rendering.

The one thing this dataset cannot give us is a pixel-to-metre scale. There is
no surveyed distance, no camera intrinsics, no landmark of known size. Three
attempts to recover it from the footage alone disagreed with each other by a
factor of two to four, so the system reports everything metric as an estimate
and says so.

Recovering it needs exactly one thing from a human: **the real width of the
road at one identifiable image row**. That is a two-minute job on Google Maps
satellite view - right-click, Measure distance, across the carriageway at a
landmark visible in both the camera view and from above. This module turns that
one number into a full perspective model, records where it came from, and marks
the camera verified so its speeds and densities stop being hedged.

Everything here is deliberately reversible and auditable: the measurement is
stored alongside the fitted model, so a later reader can see what was supplied
and re-derive the result.

State lives in `configs/calibration_state.json` rather than in source, so
calibrating through the dashboard survives a restart without editing code.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

import config as cfg
import zones as Z

STATE_PATH = cfg.CONFIG_DIR / "calibration_state.json"
GUIDE_DIR = cfg.FRAME_DIR / "measurement_guide"

# Rows to offer for measurement, as fractions of frame height. Deliberately
# spread in depth: two widely separated rows determine the horizon, and the
# horizon is what sets the longitudinal scale and therefore the speeds.
DEFAULT_ROW_FRACTIONS = (0.32, 0.60, 0.88)

GUIDE_COLORS = [(60, 60, 240), (30, 180, 250), (230, 90, 220)]


# --------------------------------------------------------------------------
# Persistent state
# --------------------------------------------------------------------------

def _blank_state() -> dict:
    return {"scale_verified": {}, "measurements": {}}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return _blank_state()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _blank_state()
    s.setdefault("scale_verified", {})
    s.setdefault("measurements", {})
    return s


def save_state(state: dict) -> None:
    cfg.write_json_atomic(STATE_PATH, state)


def is_verified(camera_id: str) -> bool:
    return bool(load_state()["scale_verified"].get(camera_id, False))


def set_verified(camera_id: str, verified: bool) -> None:
    s = load_state()
    s["scale_verified"][camera_id] = bool(verified)
    save_state(s)
    cfg.refresh_scale_verified()


def measurement_for(camera_id: str) -> dict | None:
    return load_state()["measurements"].get(camera_id)


def clear(camera_id: str) -> None:
    """Undo a calibration: restore the model it replaced, drop the record and
    the verified flag.

    Restored, not deleted. The junction cameras ship with a *seeded* scale
    model - estimated, but it is what every speed, density and flow figure on
    those cameras is computed from. Undo used to set the model to None, so
    re-measuring after an undo produced no speeds or densities at all, the
    signal plan lost its demand, and "back to estimated figures" was in fact
    "back to no figures". The seed is kept in the measurement record when a
    calibration is applied, and put back here.
    """
    s = load_state()
    record = s["measurements"].get(camera_id) or {}
    s["scale_verified"].pop(camera_id, None)
    s["measurements"].pop(camera_id, None)
    save_state(s)
    cfg.refresh_scale_verified()

    calib = Z.load(camera_id)
    if calib is not None:
        calib.scale_model = record.get("previous_scale_model")
        Z.save(calib)


# --------------------------------------------------------------------------
# Geometry: how many pixels wide is the road at row y?
# --------------------------------------------------------------------------

def _union_mask(calib: Z.CameraCalibration, kinds: tuple[str, ...]) -> np.ndarray:
    w, h = calib.frame_size
    mask = np.zeros((int(h), int(w)), dtype=np.uint8)
    for z in calib.zones:
        if z.kind in kinds:
            cv2.fillPoly(mask, [z.np_polygon], 255)
    return mask


def road_pixel_width(calib: Z.CameraCalibration, row: int,
                     kinds: tuple[str, ...] = ("lane", "carriageway")) -> int:
    """Widest contiguous run of carriageway at image row `row`.

    Rasterised rather than taken from min/max x of the polygons: on area
    cameras the drivable zones can be disjoint, and spanning a gap between two
    of them would silently inflate the measured width and halve every speed
    downstream.
    """
    mask = _union_mask(calib, kinds)
    row = int(np.clip(row, 0, mask.shape[0] - 1))
    line = mask[row] > 0
    if not line.any():
        return 0

    best = run = 0
    for v in line:
        run = run + 1 if v else 0
        best = max(best, run)
    return int(best)


def road_span(calib: Z.CameraCalibration, row: int,
              kinds: tuple[str, ...] = ("lane", "carriageway")) -> tuple[int, int] | None:
    """(x_start, x_end) of the widest run at `row`, for drawing the guide line."""
    mask = _union_mask(calib, kinds)
    row = int(np.clip(row, 0, mask.shape[0] - 1))
    line = mask[row] > 0
    if not line.any():
        return None

    best_len = best_start = run = 0
    start = 0
    for i, v in enumerate(line):
        if v:
            if run == 0:
                start = i
            run += 1
            if run > best_len:
                best_len, best_start = run, start
        else:
            run = 0
    return (best_start, best_start + best_len - 1)


def suggested_rows(calib: Z.CameraCalibration) -> list[int]:
    """Rows where the carriageway is actually visible, spread in depth."""
    h = int(calib.frame_size[1])
    out = []
    for frac in DEFAULT_ROW_FRACTIONS:
        y = int(h * frac)
        # Walk toward the frame centre until the row actually crosses road.
        for delta in range(0, int(h * 0.22), 8):
            for cand in (y - delta, y + delta):
                if 0 <= cand < h and road_pixel_width(calib, cand) > 30:
                    out.append(cand)
                    break
            else:
                continue
            break
    # Keep them distinct and ordered far-to-near.
    seen, uniq = set(), []
    for y in sorted(out):
        if all(abs(y - u) > 40 for u in uniq):
            uniq.append(y)
            seen.add(y)
    return uniq


# --------------------------------------------------------------------------
# Optics
# --------------------------------------------------------------------------

def focal_px_from_hfov(hfov_deg: float, frame_width: int = 1920) -> float:
    """Focal length in pixels from horizontal field of view.

    Operators know (or can look up) a camera's field of view far more readily
    than its focal length in pixels, so the UI asks for degrees and converts
    here. 1920 px at 60 deg gives 1663 px, the previous hard-coded default.
    """
    hfov_deg = float(np.clip(hfov_deg, 5.0, 170.0))
    return (frame_width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def hfov_from_focal_px(focal_px: float, frame_width: int = 1920) -> float:
    return math.degrees(2.0 * math.atan((frame_width / 2.0) / max(1.0, focal_px)))


# --------------------------------------------------------------------------
# Fitting
# --------------------------------------------------------------------------

@dataclass
class ScaleResult:
    """Everything the UI needs to show after a fit."""

    camera_id: str
    ok: bool
    message: str
    model: dict | None = None
    rows: list[dict] | None = None          # per-row scale diagnostics
    zone_areas: list[dict] | None = None    # per-zone real area
    warnings: list[str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def fit_scale(
    camera_id: str,
    measurements: list[tuple[int, float]],
    camera_height_m: float = 7.0,
    hfov_deg: float = 60.0,
    calib: Z.CameraCalibration | None = None,
) -> ScaleResult:
    """Fit the perspective model from one or two measured widths.

    `measurements` is [(image_row, real_width_metres), ...].

    With **two** rows the horizon is determined outright, which is what fixes
    the longitudinal scale and therefore every speed. With **one** row only the
    lateral scale at that row is known, and the horizon has to be inferred from
    how the traced road edges converge - workable, but weaker, and we say so.
    """
    calib = calib or Z.load(camera_id)
    if calib is None:
        return ScaleResult(camera_id, False,
                           "No zone calibration for this camera yet.")

    pairs = []
    warnings: list[str] = []
    for row, width_m in measurements:
        px = road_pixel_width(calib, int(row))
        if px <= 0:
            warnings.append(f"Row {row}: no carriageway crosses this row — skipped.")
            continue
        if width_m <= 0:
            warnings.append(f"Row {row}: width must be greater than zero — skipped.")
            continue
        pairs.append((int(row), float(width_m), int(px)))

    if not pairs:
        return ScaleResult(camera_id, False,
                           "None of the supplied rows cross the traced "
                           "carriageway. Pick a row inside the shaded zones.",
                           warnings=warnings)

    focal_px = focal_px_from_hfov(hfov_deg, int(calib.frame_size[0]))

    if len(pairs) >= 2:
        # Use the two most widely separated rows: the further apart they are,
        # the better conditioned the horizon solve.
        pairs.sort(key=lambda p: p[0])
        near = pairs[-1]
        far = pairs[0]
        if near[0] - far[0] < 80:
            warnings.append(
                "The two rows are close together, so the horizon solve is "
                "poorly conditioned. Rows further apart in depth give more "
                "trustworthy speeds.")
        # Physical sanity: the nearer row must be wider in pixels.
        if near[2] <= far[2]:
            warnings.append(
                f"The near row (y={near[0]}) measures {near[2]} px against "
                f"{far[2]} px at the far row (y={far[0]}). A straight road "
                "cannot narrow toward the camera — check the zone polygons, "
                "or that the widths were read at the right rows.")
        model = Z.build_scale_model(
            y_near=near[0], px_width_near=near[2],
            y_far=far[0], px_width_far=far[2],
            real_width_m=(near[1] + far[1]) / 2.0 if abs(near[1] - far[1]) < 1e-9
            else near[1],
            focal_px=focal_px, camera_height_m=camera_height_m,
        )
        if abs(near[1] - far[1]) > 1e-9:
            # Different real widths at the two rows: solve directly for the
            # scale at each row instead of assuming one common width.
            model = _fit_two_widths(near, far, focal_px, camera_height_m)
        note = (f"Fitted from two rows: y={near[0]} ({near[2]} px = {near[1]:g} m) "
                f"and y={far[0]} ({far[2]} px = {far[1]:g} m).")
    else:
        row, width_m, px = pairs[0]
        horizon = _horizon_from_edges(calib)
        if horizon is None:
            return ScaleResult(
                camera_id, False,
                "One row is not enough here: the horizon could not be inferred "
                "from the zone geometry. Supply a second measured row.",
                warnings=warnings)
        far_row = int(horizon + max(40.0, (row - horizon) * 0.28))
        far_px = max(1.0, px * (far_row - horizon) / (row - horizon))
        model = Z.build_scale_model(
            y_near=row, px_width_near=px,
            y_far=far_row, px_width_far=far_px,
            real_width_m=width_m,
            focal_px=focal_px, camera_height_m=camera_height_m,
        )
        note = (f"Fitted from one row (y={row}, {px} px = {width_m:g} m). "
                f"Horizon inferred from edge convergence (y={horizon:.0f}).")
        warnings.append(
            "Only one row supplied. The lateral scale at that row is now "
            "correct, but the horizon — and so the longitudinal scale that "
            "sets every speed — is inferred, not measured. A second row at a "
            "different depth removes that assumption.")

    model["measured_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    model["source"] = "operator measurement"
    model["hfov_deg"] = float(hfov_deg)

    rows_diag, zone_diag, more_warnings = diagnose(calib, model)
    return ScaleResult(
        camera_id=camera_id, ok=True, message=note, model=model,
        rows=rows_diag, zone_areas=zone_diag,
        warnings=warnings + more_warnings,
    )


def _fit_two_widths(near, far, focal_px, camera_height_m) -> dict:
    """Horizon solve when the two rows have genuinely different real widths."""
    y_n, w_n, px_n = near
    y_f, w_f, px_f = far
    s_n = w_n / px_n          # metres per pixel, laterally, at the near row
    s_f = w_f / px_f
    if abs(s_n - s_f) < 1e-12:
        y_h = y_f - 1e6
    else:
        y_h = (s_n * y_n - s_f * y_f) / (s_n - s_f)
    c = s_n * (y_n - y_h)
    return {
        "type": "perspective_v1",
        "y_horizon": float(y_h),
        "c": float(c),
        "focal_px": float(focal_px),
        "camera_height_m": float(camera_height_m),
        "calibration": {
            "y_near": float(y_n), "px_width_near": float(px_n),
            "y_far": float(y_f), "px_width_far": float(px_f),
            "real_width_m": float(w_n), "real_width_far_m": float(w_f),
        },
    }


def _horizon_from_edges(calib: Z.CameraCalibration) -> float | None:
    """Horizon row implied by how the carriageway narrows with height.

    For a flat road under a pinhole camera the pixel width is linear in row and
    reaches zero at the horizon, so a least-squares line through several rows
    gives its intercept.
    """
    h = int(calib.frame_size[1])
    rows, widths = [], []
    for y in range(int(h * 0.20), int(h * 0.98), max(8, h // 60)):
        px = road_pixel_width(calib, y)
        if px > 20:
            rows.append(y)
            widths.append(px)
    if len(rows) < 6:
        return None

    A = np.vstack([np.asarray(rows, float), np.ones(len(rows))]).T
    m, c = np.linalg.lstsq(A, np.asarray(widths, float), rcond=None)[0]
    if m <= 1e-9:
        return None
    y_h = -c / m
    if not np.isfinite(y_h) or y_h > min(rows):
        return None
    return float(y_h)


# --------------------------------------------------------------------------
# Diagnostics — is the fitted model physically plausible?
# --------------------------------------------------------------------------

# A ~7 m urban lane pair viewed by a 1080p camera lands somewhere near 1–6 cm
# per pixel laterally across the usable part of the frame. Well outside that
# means the fit is wrong, and a wrong scale is worse than no scale because it
# looks authoritative.
LATERAL_CM_PER_PX_MIN = 0.15
LATERAL_CM_PER_PX_MAX = 40.0


def diagnose(calib: Z.CameraCalibration,
             model: dict) -> tuple[list[dict], list[dict], list[str]]:
    """Per-row scale, per-zone area, and any physical implausibility."""
    h = int(calib.frame_size[1])
    rows_diag: list[dict] = []
    warnings: list[str] = []

    for y in [int(h * f) for f in (0.28, 0.45, 0.62, 0.80, 0.96)]:
        lat = Z.lateral_m_per_px(model, y)
        lon = Z.longitudinal_m_per_px(model, y)
        px = road_pixel_width(calib, y)
        rows_diag.append({
            "row": y,
            "road_px": px,
            "road_m": round(px * lat, 2) if lat and px else None,
            "lateral_cm_per_px": round(lat * 100, 3) if lat else None,
            "longitudinal_cm_per_px": round(lon * 100, 3) if lon else None,
            "anisotropy": round(lon / lat, 2) if lat and lon else None,
        })

    lats = [r["lateral_cm_per_px"] for r in rows_diag if r["lateral_cm_per_px"]]
    if lats:
        if max(lats) > LATERAL_CM_PER_PX_MAX or min(lats) < LATERAL_CM_PER_PX_MIN:
            warnings.append(
                f"Implied ground scale runs {min(lats):.2f}–{max(lats):.2f} cm "
                f"per pixel, which is outside the plausible range for a 1080p "
                f"traffic camera. Re-check the measured width and the row it "
                f"was read at.")

    y_h = model.get("y_horizon")
    if y_h is not None and y_h > 0:
        warnings.append(
            f"The fitted horizon sits at row {y_h:.0f}, inside the image. That "
            f"is only physical if the camera looks up at the sky above the "
            f"road; check the two rows were not transposed.")

    zone_diag: list[dict] = []
    for z in calib.zones:
        area = Z.zone_real_area(z, model)
        zone_diag.append({
            "zone": z.label,
            "kind": z.kind,
            "area_m2": round(area) if area else None,
            "road_length_m": (round(area / (cfg.LANE_WIDTH_M
                                            * max(1.0, z.lanes_equivalent)))
                              if area else None),
        })

    lane_areas = [d for d in zone_diag if d["kind"] == "lane" and d["road_length_m"]]
    for d in lane_areas:
        if d["road_length_m"] > 400:
            warnings.append(
                f"'{d['zone']}' comes out {d['road_length_m']} m long. A single "
                f"camera view rarely covers more than ~150 m of road, so the "
                f"scale is probably too coarse.")
            break
        if d["road_length_m"] < 8:
            warnings.append(
                f"'{d['zone']}' comes out only {d['road_length_m']} m long, "
                f"which is shorter than a bus. The scale is probably too fine.")
            break

    return rows_diag, zone_diag, warnings


def apply(camera_id: str, result: ScaleResult, mark_verified: bool = True,
          measurements: list[tuple[int, float]] | None = None,
          camera_height_m: float = 7.0, hfov_deg: float = 60.0) -> None:
    """Persist a successful fit: model into the calibration, record into state."""
    if not result.ok or not result.model:
        raise ValueError("Cannot apply an unsuccessful fit.")

    calib = Z.load(camera_id)
    if calib is None:
        raise ValueError(f"No calibration for '{camera_id}'.")

    s = load_state()
    # The model this one replaces, so `clear` can restore it. Re-applying over
    # an existing measurement keeps the *original* seed rather than chaining
    # back only as far as the previous measurement.
    prior = s["measurements"].get(camera_id) or {}
    previous_model = (prior["previous_scale_model"]
                      if "previous_scale_model" in prior else calib.scale_model)

    calib.scale_model = result.model
    Z.save(calib)

    s["measurements"][camera_id] = {
        "previous_scale_model": previous_model,
        "rows": [[int(r), float(w)] for r, w in (measurements or [])],
        "camera_height_m": float(camera_height_m),
        "hfov_deg": float(hfov_deg),
        "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": result.message,
        "warnings": result.warnings or [],
    }
    s["scale_verified"][camera_id] = bool(mark_verified)
    save_state(s)
    cfg.refresh_scale_verified()


# --------------------------------------------------------------------------
# Guide rendering
# --------------------------------------------------------------------------

def guide_image(camera_id: str, rows: list[int],
                calib: Z.CameraCalibration | None = None,
                base: np.ndarray | None = None) -> np.ndarray | None:
    """Reference frame with the chosen measurement rows drawn across the road.

    Returned RGB, ready for st.image. Each line is labelled with its row and
    pixel span so the operator can match what they see here to what they
    measure on satellite view.
    """
    calib = calib or Z.load(camera_id)
    if calib is None:
        return None

    if base is None:
        from reference_frames import REF_DIR
        base = cv2.imread(str(REF_DIR / f"{camera_id}.jpg"))
    if base is None:
        return None

    frame = base.copy()
    overlay = frame.copy()
    for z in calib.zones:
        if z.kind in ("lane", "carriageway"):
            cv2.fillPoly(overlay, [z.np_polygon], (90, 190, 90))
    frame = cv2.addWeighted(overlay, 0.16, frame, 0.84, 0)

    # The guide is read on screen at maybe a third of native width, so the
    # annotation is drawn oversized deliberately — a label that is legible in
    # the JPEG and illegible in the browser has failed at its only job.
    h, w = frame.shape[:2]
    s = max(1.0, w / 1400.0)
    fs, th = 1.5 * s, max(3, int(3 * s))

    for i, y in enumerate(rows):
        span = road_span(calib, int(y))
        if span is None:
            continue
        x0, x1 = span
        color = GUIDE_COLORS[i % len(GUIDE_COLORS)]
        cap = max(18, int(26 * s))

        cv2.line(frame, (x0, int(y)), (x1, int(y)), color,
                 max(4, int(6 * s)), cv2.LINE_AA)
        for x in (x0, x1):
            cv2.line(frame, (x, int(y) - cap), (x, int(y) + cap), color,
                     max(4, int(6 * s)), cv2.LINE_AA)
            cv2.circle(frame, (x, int(y)), int(11 * s), (255, 255, 255), -1,
                       cv2.LINE_AA)
            cv2.circle(frame, (x, int(y)), int(11 * s), color, th, cv2.LINE_AA)

        label = f"LINE {i + 1}   y={int(y)}   {x1 - x0} px"
        (tw, tht), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
        tx = int(np.clip((x0 + x1) / 2 - tw / 2, 10, w - tw - 10))
        ty = int(np.clip(y - cap - 14, tht + 12, h - 12))
        cv2.rectangle(frame, (tx - 10, ty - tht - 10), (tx + tw + 10, ty + 10),
                      (18, 18, 18), -1)
        cv2.putText(frame, label, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX,
                    fs, color, th, cv2.LINE_AA)

    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def write_guides(camera_ids: list[str] | None = None) -> list[Path]:
    """Regenerate the on-disk guide JPEGs (kept for the CLI workflow)."""
    out: list[Path] = []
    GUIDE_DIR.mkdir(parents=True, exist_ok=True)
    for cam in (camera_ids or list(cfg.CAMERAS.keys())):
        calib = Z.load(cam)
        if calib is None:
            continue
        rows = suggested_rows(calib)
        img = guide_image(cam, rows, calib)
        if img is None:
            continue
        p = GUIDE_DIR / f"{cam}_measure.jpg"
        cv2.imwrite(str(p), cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
        out.append(p)
    return out


if __name__ == "__main__":
    for p in write_guides():
        print(p)
    st = load_state()
    print(json.dumps(st, indent=2))
