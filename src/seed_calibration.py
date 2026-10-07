"""Generate a first-pass calibration for every camera.

Lane polygons are built by interpolating between two hand-traced road-edge
polylines rather than clicking each lane corner by corner. On a perspective
view the lanes converge, so interpolating between the edges keeps the lane
widths proportionally correct all the way up the frame — which clicking rarely
achieves by hand.

Edge polylines and zone outlines below were traced against the grid-overlaid
reference frames and cross-checked against the empirical traffic heatmaps
(outputs/frames/heatmap), so they follow where vehicles actually travel rather
than where the road merely looks drivable.

Run `python src/calibrate.py <camera>` afterwards to refine interactively.

Usage:
    python src/seed_calibration.py           # seed all cameras, write previews
    python src/seed_calibration.py --camera gauravpath
"""

from __future__ import annotations

import argparse

import cv2
import numpy as np

import config as cfg
import zones as Z
from calibrate import preview


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------

def _interp_polyline(polyline: list[tuple[int, int]], ys: list[int]) -> list[int]:
    """x-coordinate of a polyline at each requested image row."""
    pts = sorted(polyline, key=lambda p: p[1])
    py = [p[1] for p in pts]
    px = [p[0] for p in pts]
    return [int(round(float(np.interp(y, py, px)))) for y in ys]


def lanes_from_edges(
    left_edge: list[tuple[int, int]],
    right_edge: list[tuple[int, int]],
    n_lanes: int,
    y_top: int,
    y_bottom: int,
    steps: int = 12,
    fractions: list[float] | None = None,
) -> list[list[tuple[int, int]]]:
    """Split the corridor between two edges into n lane polygons.

    `fractions` optionally gives the lane boundaries as fractions across the
    carriageway (e.g. [0, 0.45, 1.0] for two unequal lanes). Defaults to equal
    widths.
    """
    ys = [int(y_top + (y_bottom - y_top) * i / steps) for i in range(steps + 1)]
    lx = _interp_polyline(left_edge, ys)
    rx = _interp_polyline(right_edge, ys)

    if fractions is None:
        fractions = [i / n_lanes for i in range(n_lanes + 1)]

    polys = []
    for i in range(n_lanes):
        f0, f1 = fractions[i], fractions[i + 1]
        left_pts = [(int(lx[j] + (rx[j] - lx[j]) * f0), ys[j]) for j in range(len(ys))]
        right_pts = [(int(lx[j] + (rx[j] - lx[j]) * f1), ys[j]) for j in range(len(ys))]
        polys.append(left_pts + list(reversed(right_pts)))
    return polys


# --------------------------------------------------------------------------
# Per-camera definitions
# --------------------------------------------------------------------------
# Junction cameras get lane polygons on the active carriageway.
# Area cameras get functional zones instead — there are no lanes to speak of.

JUNCTION_DEFS = {
    "gauravpath": {
        # Carriageway carrying traffic toward the camera (Towards Kargil Chowk).
        # The opposite carriageway is empty throughout this clip; the heatmap
        # shows zero activity there, so it is recorded but not lane-split.
        "left_edge":  [(838, 60), (855, 300), (875, 600), (890, 900), (900, 1080)],
        # Right edge follows the kerb line (black/white posts). Kept tight so
        # the paved footpath beyond the kerb stays outside the lane polygons —
        # otherwise parked two-wheelers on the footpath inflate lane density.
        "right_edge": [(1075, 60), (1290, 300), (1400, 600), (1540, 900), (1630, 1080)],
        "n_lanes": 2,
        "y_top": 90,
        "y_bottom": 1075,
        # Divider placed to match the painted dashed line, which sits slightly
        # right of the carriageway mid-point on this view.
        "fractions": [0.0, 0.52, 1.0],
        "lane_labels": ["Lane 1 (median)", "Lane 2 (kerb)"],
        "approach": "Towards Kargil Chowk",
        "extra_zones": [
            {
                "id": "opposite_carriageway",
                "label": "Opposite carriageway",
                "kind": "carriageway",
                "polygon": [(575, 300), (810, 300), (890, 1075), (180, 1075)],
                "notes": "Outbound direction. No flow during this clip.",
            },
        ],
    },
    "rokadiya_hanuman": {
        # Near-field left of the carriageway is fenced off by orange barricades
        # (visible works zone), so the left edge follows the white edge line at
        # the top and the barricade line lower down.
        "left_edge":  [(605, 160), (530, 320), (470, 470), (620, 600),
                       (760, 750), (880, 920), (960, 1075)],
        # Right edge follows the median kerb. Held tight in the near field so
        # the shoulder — where vehicles stop and park — stays out of Lane 3.
        "right_edge": [(1140, 160), (1215, 330), (1305, 500), (1450, 700),
                       (1560, 900), (1660, 1075)],
        "n_lanes": 3,
        "y_top": 180,
        "y_bottom": 1075,
        "lane_labels": ["Lane 1 (median)", "Lane 2 (centre)", "Lane 3 (kerb)"],
        "approach": "Towards Bhatena",
        "extra_zones": [
            {
                "id": "kerbside_encroachment",
                "label": "Kerb-side encroachment",
                "kind": "parking",
                "polygon": [(1150, 200), (1420, 240), (1560, 470), (1230, 430)],
                "notes": "Persistent stationary hotspot in the heatmap - parked "
                         "two-wheelers and a vendor stall narrowing the kerb lane.",
            },
        ],
    },
}


AREA_DEFS = {
    "chauta_bazaar": [
        {
            "id": "market_carriageway",
            "label": "Market carriageway",
            "kind": "carriageway",
            "polygon": [(830, 60), (1180, 60), (1290, 560), (1180, 1075),
                        (560, 1075), (700, 560)],
            "notes": "Shared movement channel. No lane discipline.",
        },
        {
            "id": "vendor_encroachment_left",
            "label": "Vendor encroachment (L)",
            "kind": "parking",
            "polygon": [(0, 380), (700, 380), (560, 1075), (0, 1075)],
            "notes": "Stalls and parked two-wheelers narrowing the usable width.",
        },
        {
            "id": "vendor_encroachment_right",
            "label": "Vendor encroachment (R)",
            "kind": "parking",
            "polygon": [(1290, 300), (1919, 300), (1919, 1075), (1200, 1075)],
            "notes": "Shopfront overflow narrowing the usable width.",
        },
    ],
    "mahidharpura": [
        {
            "id": "movement_channel",
            "label": "Movement channel",
            "kind": "carriageway",
            "polygon": [(600, 150), (860, 150), (1050, 560), (1120, 1075),
                        (450, 1075), (520, 560)],
            "notes": "The only walkable/rideable channel left between parked "
                     "two-wheeler banks. Detection saturates here - occupancy "
                     "is the trustworthy signal, not object count.",
        },
        {
            "id": "parking_bank_left",
            "label": "Parked bank (L)",
            "kind": "parking",
            "polygon": [(330, 260), (600, 200), (520, 560), (450, 1075), (140, 1075)],
            "notes": "Two-wheeler parking occupying carriageway width.",
        },
        {
            "id": "parking_bank_right",
            "label": "Parked bank (R)",
            "kind": "parking",
            "polygon": [(880, 200), (1250, 300), (1500, 1075), (1120, 1075), (1050, 560)],
            "notes": "Two-wheeler parking occupying carriageway width.",
        },
    ],
    "gopi_talav": [
        {
            "id": "gate_approach",
            "label": "Gate approach",
            "kind": "pedestrian",
            "polygon": [(700, 180), (1180, 180), (1300, 700), (1250, 1075), (620, 1075)],
            "notes": "Pedestrian flow through the gate - crowd-density zone.",
        },
        {
            "id": "vehicle_channel",
            "label": "Vehicle channel",
            "kind": "carriageway",
            "polygon": [(180, 120), (700, 180), (620, 1075), (0, 1075), (0, 300)],
            "notes": "Mixed two-wheeler / auto movement alongside the gate.",
        },
        {
            "id": "vendor_zone",
            "label": "Vendor zone",
            "kind": "parking",
            "polygon": [(1300, 250), (1919, 250), (1919, 1075), (1250, 1075)],
            "notes": "Stalls and hawkers - fixed obstruction.",
        },
    ],
    "rly_station": [
        {
            "id": "forecourt_carriageway",
            "label": "Forecourt carriageway",
            "kind": "carriageway",
            "polygon": [(560, 60), (1919, 60), (1919, 900), (900, 1075), (400, 700)],
            "notes": "Auto-rickshaw pickup / drop movement area.",
        },
        {
            "id": "pedestrian_flow",
            "label": "Pedestrian flow",
            "kind": "pedestrian",
            "polygon": [(0, 60), (560, 60), (400, 700), (700, 1075), (0, 1075)],
            "notes": "Passenger walking route across the forecourt.",
        },
    ],
    "central_bus_depo": [
        {
            "id": "bus_manoeuvring",
            "label": "Bus manoeuvring area",
            "kind": "carriageway",
            "polygon": [(330, 60), (1919, 60), (1919, 560), (1000, 620), (330, 400)],
            "notes": "Bus arrival / departure movement area.",
        },
        {
            "id": "platform_waiting",
            "label": "Platform waiting area",
            "kind": "platform",
            "polygon": [(0, 200), (330, 200), (400, 1075), (0, 1075)],
            "notes": "Queueing passengers - crowd-density zone.",
        },
        {
            "id": "concourse",
            "label": "Concourse",
            "kind": "pedestrian",
            "polygon": [(330, 420), (1919, 580), (1919, 1075), (400, 1075)],
            "notes": "Open pedestrian circulation between platform and buses.",
        },
    ],
}


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------

def seed_junction(camera_id: str) -> Z.CameraCalibration:
    d = JUNCTION_DEFS[camera_id]
    polys = lanes_from_edges(
        d["left_edge"], d["right_edge"], d["n_lanes"],
        d["y_top"], d["y_bottom"], fractions=d.get("fractions"),
    )

    zs: list[Z.Zone] = []
    for i, poly in enumerate(polys):
        label = d["lane_labels"][i]
        zs.append(Z.Zone(
            id=f"lane_{i + 1}",
            label=label,
            polygon=poly,
            kind="lane",
            approach=d["approach"],
        ))

    for extra in d.get("extra_zones", []):
        zs.append(Z.Zone(
            id=extra["id"], label=extra["label"], polygon=extra["polygon"],
            kind=extra["kind"], notes=extra.get("notes", ""),
        ))

    return Z.CameraCalibration(
        camera_id=camera_id,
        mode="junction",
        zones=zs,
        scale_model=SCALE_MODELS.get(camera_id),
        notes="Seeded from traced road edges; refine with src/calibrate.py.",
    )


def seed_area(camera_id: str) -> Z.CameraCalibration:
    zs = [
        Z.Zone(
            id=z["id"], label=z["label"], polygon=z["polygon"],
            kind=z["kind"], notes=z.get("notes", ""),
        )
        for z in AREA_DEFS[camera_id]
    ]
    return Z.CameraCalibration(
        camera_id=camera_id,
        mode="area",
        zones=zs,
        scale_model=SCALE_MODELS.get(camera_id),
        notes="Functional zones - no lane discipline in this view.",
    )


# --------------------------------------------------------------------------
# Metric scale
# --------------------------------------------------------------------------
# Anchored on IRC:86 urban lane width (3.5 m). We measure the pixel width of a
# known-width feature at two image rows and derive ground area per pixel,
# letting density be reported in PCU per 100 m^2 rather than an arbitrary
# pixel-relative figure.
#
# These are estimates from a single view, not a survey. Treated as approximate
# throughout, and every derived figure is labelled as such.

SCALE_MODELS = {
    "gauravpath": Z.build_scale_model(
        # Carriageway is 2 lanes => 7.0 m at IRC:86 urban lane width of 3.5 m.
        # Measured span between the traced edges: ~730 px at y=1000,
        # ~215 px at y=200.
        y_near=1000, px_width_near=730,
        y_far=200, px_width_far=215,
        real_width_m=7.0,
    ),
    "rokadiya_hanuman": Z.build_scale_model(
        # 3 lanes => 10.5 m. Edge span ~700 px at y=1000, ~300 px at y=300.
        y_near=1000, px_width_near=700,
        y_far=300, px_width_far=300,
        real_width_m=10.5,
    ),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", choices=list(cfg.CAMERAS.keys()))
    args = ap.parse_args()

    cams = [args.camera] if args.camera else list(cfg.CAMERAS.keys())

    for c in cams:
        if c in JUNCTION_DEFS:
            calib = seed_junction(c)
        elif c in AREA_DEFS:
            calib = seed_area(c)
        else:
            print(f"  {c}: no definition, skipped")
            continue

        # A camera an operator has calibrated keeps its measured scale. Seeding
        # used to overwrite it with the assumed model while the state file
        # still flagged the camera "measured", so every figure on it would be
        # presented as measured while resting on an assumption.
        existing = Z.load(c)
        if (existing is not None and existing.scale_model
                and cfg.scale_is_verified(c)):
            calib.scale_model = existing.scale_model
            print(f"  {c}: keeping the operator-measured scale model")

        Z.save(calib)
        areas = []
        for z in calib.zones:
            a = Z.zone_real_area(z, calib.scale_model)
            areas.append(f"{z.label}={a:.0f}m2" if a else f"{z.label}")
        print(f"  {c:<20} {len(calib.zones)} zones  [{calib.mode}]")
        for line in areas:
            print(f"      {line}")
        preview(c)

    print(f"\nCalibration written to {cfg.CAMERA_CONFIG}")


if __name__ == "__main__":
    main()
