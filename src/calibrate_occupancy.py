"""Measure absolute edge-density ranges so occupancy is comparable across zones.

Normalising each zone against its own observed spread makes the index
self-referential: a permanently empty stretch of road stretches its own noise
across the full 0-1 range and reads as "busy" whenever it is fractionally
above its own average. That is fine for spotting change within one zone but
meaningless when ranking one site against another, which is exactly what the
network overview does.

So we fix the scale globally instead. This samples raw edge density across
every zone of every camera and reports the distribution, from which absolute
"clear" and "packed" bounds are chosen once and shared by all zones.

Needs no detector — just OpenCV — so it runs in seconds.

Usage:
    python src/calibrate_occupancy.py
"""

from __future__ import annotations

import argparse

import cv2
import numpy as np

import config as cfg
import zones as Z
from metrics import StructuralOccupancy


def sample_camera(camera_id: str, n_samples: int = 24) -> dict[str, list[float]]:
    calib = Z.load(camera_id)
    if calib is None:
        return {}

    cap = cfg.open_video(str(cfg.camera_video_path(camera_id)))
    if not cap.isOpened():
        return {}
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0

    occ = StructuralOccupancy(calib)
    out: dict[str, list[float]] = {z.id: [] for z in calib.zones}

    for i in range(n_samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * i / max(1, n_samples)))
        ok, frame = cap.read()
        if not ok:
            continue
        for z in calib.zones:
            out[z.id].append(occ.raw(frame, z))

    cap.release()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=24)
    args = ap.parse_args()

    print(f"{'camera':<20} {'zone':<26} {'kind':<13} "
          f"{'min':>7} {'mean':>7} {'max':>7}")
    print("-" * 90)

    all_vals: list[float] = []
    clear_candidates: list[float] = []
    packed_candidates: list[float] = []

    for cam in cfg.CAMERAS:
        vals = sample_camera(cam, args.samples)
        calib = Z.load(cam)
        if not vals or calib is None:
            continue
        for z in calib.zones:
            v = np.array(vals.get(z.id, []))
            if v.size == 0:
                continue
            all_vals.extend(v.tolist())
            print(f"{cam:<20} {z.label[:25]:<26} {z.kind:<13} "
                  f"{v.min():>7.4f} {v.mean():>7.4f} {v.max():>7.4f}")

            # An empty carriageway lane is the natural "clear" reference; a
            # parking bank or market street the natural "packed" one.
            if z.kind == "lane" and cam == "gauravpath":
                clear_candidates.extend(v.tolist())
            if z.kind == "parking" and cam in ("mahidharpura", "chauta_bazaar"):
                packed_candidates.extend(v.tolist())

    arr = np.array(all_vals)
    print("-" * 90)
    print(f"overall  p1={np.percentile(arr, 1):.4f}  p5={np.percentile(arr, 5):.4f}  "
          f"p50={np.percentile(arr, 50):.4f}  p95={np.percentile(arr, 95):.4f}  "
          f"p99={np.percentile(arr, 99):.4f}")

    if clear_candidates:
        c = np.array(clear_candidates)
        print(f"clear reference  (gauravpath lanes) : "
              f"p25={np.percentile(c, 25):.4f} median={np.median(c):.4f}")
    if packed_candidates:
        p = np.array(packed_candidates)
        print(f"packed reference (parking banks)    : "
              f"median={np.median(p):.4f} p75={np.percentile(p, 75):.4f}")

    lo = float(np.percentile(arr, 2))
    hi = float(np.percentile(arr, 98))
    print(f"\nSuggested absolute bounds for config.py:")
    print(f"  OCCUPANCY_EDGE_CLEAR  = {lo:.4f}")
    print(f"  OCCUPANCY_EDGE_PACKED = {hi:.4f}")


if __name__ == "__main__":
    main()
