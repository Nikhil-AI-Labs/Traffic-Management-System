"""Central configuration: camera registry, PCU factors, and paths.

PS-08 "Data-Driven Traffic Optimization & Adaptive Infrastructure Intelligence"
E-Rakshak Hackathon 2026 — Surat city CCTV network.
"""

import os
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Decoder log noise
# --------------------------------------------------------------------------
# Two streams of harmless warnings otherwise fill the terminal and bury real
# errors, which is the actual cost - they make a healthy run look broken.
#
#   "[h264 @ ...] No start code is found."
#       The Surat exports are H.264 inside an AVI container. AVI has no way to
#       carry H.264 parameter sets, so FFmpeg probes for a start code, does not
#       find one at the container boundary, says so, and then decodes the
#       stream correctly anyway. Every frame this project has ever measured
#       came through that path. Verified: frame counts, timestamps and
#       detections are unaffected.
#
#   "TIFFReadDirectory: Unknown field with tag 34735 ..."
#       FLUID's orthophotos are GeoTIFFs. Those tags are the georeferencing
#       block; OpenCV has no use for them and reads the pixels regardless.
#
# Set before cv2 is imported anywhere, because the FFmpeg backend reads this
# at initialisation. Raise it to "info" when actually debugging a decode.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")   # AV_LOG_QUIET
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")     # OpenCV's own channel


def quiet_opencv() -> None:
    """Silence OpenCV's warning channel, for callers that import cv2 directly.

    The env vars above cover a fresh process; this covers the case where cv2
    was already imported and its logger initialised. `cv2.setLogLevel` alone
    is not enough - it is a thin alias that does not reach the utils.logging
    logger the TIFF and FFmpeg backends actually write to.
    """
    try:
        import cv2
        try:
            from cv2.utils import logging as _l
            _l.setLogLevel(_l.LOG_LEVEL_SILENT)
        except Exception:
            cv2.setLogLevel(0)
    except Exception:
        pass


import contextlib


@contextlib.contextmanager
def muffle_stderr():
    """Swallow C-level stderr for the duration of the block.

    FFmpeg's h264 parser writes "No start code is found." straight to fd 2 from
    C, below every Python and OpenCV logging control, so the only way to stop
    it is to redirect the file descriptor. Scoped to the smallest possible
    block - opening a capture - because a blanket redirect would also hide
    genuine errors, which would be a far worse trade than a noisy terminal.

    Silently does nothing if the platform will not allow the dup.
    """
    try:
        fd = sys.stderr.fileno()
    except Exception:
        yield
        return
    try:
        saved = os.dup(fd)
    except Exception:
        yield
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        sys.stderr.flush()
        os.dup2(devnull, fd)
        yield
    finally:
        sys.stderr.flush()
        os.dup2(saved, fd)
        os.close(saved)
        os.close(devnull)


def open_video(path):
    """cv2.VideoCapture without the decoder's container-probe chatter."""
    import cv2
    with muffle_stderr():
        return cv2.VideoCapture(str(path))


def video_writer(path, fps: float, size: tuple[int, int]):
    """An MP4 writer whose output a web browser can actually play.

    'mp4v' - what this project used to write - is MPEG-4 Part 2, which no
    current browser decodes in a <video> element, so every annotated video was
    a dead player in both dashboards. H.264 ('avc1') is tried first: OpenCV's
    FFmpeg build needs the OpenH264 DLL for it, and on Windows falls back to
    Media Foundation, which encodes H.264 natively. Only if neither can open
    is 'mp4v' used, with a warning, so a render never fails outright.

    Returns (writer, codec_name).
    """
    import cv2
    for fourcc in ("avc1", "mp4v"):
        with muffle_stderr():
            w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc),
                                float(fps), (int(size[0]), int(size[1])))
        if w.isOpened():
            if fourcc == "mp4v":
                print("  warning: no H.264 encoder available; wrote MPEG-4 "
                      "Part 2, which browsers will not play. Install the "
                      "OpenH264 library or re-encode with ffmpeg.")
            return w, fourcc
        w.release()
    raise RuntimeError(f"Could not open a video writer for {path}")


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent

MODEL_DIR = ROOT / "models"
CONFIG_DIR = ROOT / "configs"
OUTPUT_DIR = ROOT / "outputs"

ANNOTATED_DIR = OUTPUT_DIR / "annotated"
LOG_DIR = OUTPUT_DIR / "logs"
FRAME_DIR = OUTPUT_DIR / "frames"

CAMERA_CONFIG = CONFIG_DIR / "cameras.json"

# UVH-26 weights, fine-tuned on 26k annotated frames from Bengaluru Safe-City
# CCTV. Same domain as our Surat footage (1080p fixed traffic cameras), and it
# carries India-specific classes that COCO lacks entirely — most importantly
# the three-wheeler (auto-rickshaw), which is the single most common vehicle in
# most of our clips.
MODEL_S = MODEL_DIR / "weights" / "YOLOv11-S" / "UVH-26-MV-YOLOv11-S.pt"
MODEL_X = MODEL_DIR / "weights" / "YOLOv11-X" / "UVH-26-MV-YOLOv11-X.pt"

DEFAULT_MODEL = MODEL_S

for _d in (MODEL_DIR, CONFIG_DIR, OUTPUT_DIR, ANNOTATED_DIR, LOG_DIR, FRAME_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Vehicle classes (UVH-26 model, 14 classes)
# --------------------------------------------------------------------------

CLASS_NAMES = {
    0: "Hatchback",
    1: "Sedan",
    2: "SUV",
    3: "MUV",
    4: "Bus",
    5: "Truck",
    6: "Three-wheeler",
    7: "Two-wheeler",
    8: "LCV",
    9: "Mini-bus",
    10: "tempo-traveller",
    11: "bicycle",
    12: "Van",
    13: "Others",
}

# Passenger Car Unit factors per IRC:106-1990 (Guidelines for Capacity of Urban
# Roads in Plain Areas). Raw vehicle counts are misleading in Indian mixed
# traffic — twelve two-wheelers and twelve buses occupy vastly different road
# space. Converting to PCU is what makes the density figure mean something to a
# traffic engineer, and it is what IRC-based capacity thresholds are defined in.
#
# IRC table entries used:
#   two-wheeler 0.5 | passenger car / pick-up van 1.0 | auto-rickshaw 1.2
#   LCV 1.4 | truck or bus 2.2 | cycle 0.5
# Classes with no direct IRC row (MUV, SUV, Van, Mini-bus, tempo-traveller) are
# mapped to their nearest physical equivalent; noted per line below.
PCU_FACTORS = {
    "Hatchback": 1.0,
    "Sedan": 1.0,
    "SUV": 1.0,          # IRC passenger-car row
    "MUV": 1.0,          # IRC passenger-car row
    "Van": 1.0,          # IRC "pick-up van"
    "Three-wheeler": 1.2,  # auto-rickshaw
    "Two-wheeler": 0.5,
    "bicycle": 0.5,      # IRC cycle
    "LCV": 1.4,
    "tempo-traveller": 1.4,  # nearest to LCV by footprint
    "Bus": 2.2,
    "Mini-bus": 2.2,     # bus row; conservative (over- rather than under-counts)
    "Truck": 2.2,
    "Others": 1.0,       # unknown object, assume car-equivalent
}

# Coarser buckets for reporting/plots, so the dashboard isn't showing 14 series.
CLASS_GROUPS = {
    "Hatchback": "Car",
    "Sedan": "Car",
    "SUV": "Car",
    "MUV": "Car",
    "Van": "Car",
    "Three-wheeler": "Auto",
    "Two-wheeler": "Two-wheeler",
    "bicycle": "Two-wheeler",
    "LCV": "LCV/Tempo",
    "tempo-traveller": "LCV/Tempo",
    "Bus": "Bus/Truck",
    "Mini-bus": "Bus/Truck",
    "Truck": "Bus/Truck",
    "Others": "Other",
}

GROUP_ORDER = ["Two-wheeler", "Auto", "Car", "LCV/Tempo", "Bus/Truck", "Other"]


# --------------------------------------------------------------------------
# Camera registry
# --------------------------------------------------------------------------
# mode:
#   "junction" — structured, lane-marked carriageway. Supports per-lane density
#                and adaptive signal-timing recommendations.
#   "area"     — unstructured shared space (market street, depot, station
#                plaza). No lane discipline and no signal to optimise, so we
#                report zone occupancy, crowding and bottleneck alerts instead.
#
# Forcing "lane density" onto the area cameras would be dishonest — there are
# visibly no lanes in those scenes. Splitting the modes keeps every number we
# report defensible.

CAMERAS = {
    "gauravpath": {
        "name": "GauravPath - Towards Kargil Chowk",
        "video": "Gauravpath/ExportedMedia/Export__GauravPath - Towards Kargil Chowk_Monday July 27 2026122148  aa50cd0.avi",
        "mode": "junction",
        "resource_id": "1011",
        "start_time": "2026-07-27T05:39:00Z",
        "duration_s": 61.7,
        "notes": "Divided multi-lane arterial, clear lane markings, free-flowing.",
    },
    "rokadiya_hanuman": {
        "name": "Rokadiya Hanuman - Towards Bhatena",
        "video": "Export__Rokadiya Hanuman-Towards Bhatena_Monday July 27 2026123151  e6f4a55.avi",
        "mode": "junction",
        "resource_id": "1510",
        "start_time": "2026-07-27T05:39:40Z",
        "duration_s": 121.2,
        "notes": "Divided multi-lane road, dashed markings, moderate-heavy mixed traffic.",
    },
    "chauta_bazaar": {
        "name": "Chauta Bazaar - 003",
        "video": "Export__Chauta Bazaar-003_Friday July 10 202655158  c62a714.avi",
        "mode": "area",
        "start_time": "2026-07-10T05:51:58Z",
        "duration_s": 300.9,
        "notes": "Dense market street, mixed pedestrian/vehicle shared space.",
    },
    "mahidharpura": {
        "name": "Mahidharpura - Pipla Sheri Diamond Mkt",
        "video": "Export__Mahidharpura-Pipla Sheri Diamond Mkt_Friday July 10 202655441  beb5fa4.avi",
        "mode": "area",
        "start_time": "2026-07-10T05:54:41Z",
        "duration_s": 301.0,
        "notes": "Severe congestion, near-total gridlock. Heavy occlusion — "
                 "per-object counting degrades here; occupancy estimate is the "
                 "more trustworthy signal.",
        "high_occlusion": True,
    },
    "gopi_talav": {
        "name": "Gopi Talav - Towards Gopi Talav Gate",
        "video": "Export__Gopi Talav-Towards Gopi Talav Gate_Friday July 10 202661000  dc1f515.avi",
        "mode": "area",
        "start_time": "2026-07-10T06:10:00Z",
        "duration_s": 305.6,
        "notes": "Recreational gate area, pedestrian-dominant with vendor stalls.",
    },
    "rly_station": {
        "name": "Rly Station - Towards Bismillah Rest left",
        "video": "Export__Rly Station-Towards Bismillah Rest left_Friday July 10 202661242  09a94cc.avi",
        "mode": "area",
        "start_time": "2026-07-10T06:12:42Z",
        "duration_s": 301.2,
        "notes": "Station forecourt, auto-rickshaw pickup/drop, pedestrian flow.",
    },
    "central_bus_depo": {
        "name": "Central Bus Depo - Entry Gate Platform Area",
        "video": "Export__Central Bus Depo-Entry Gate Platform Area_Friday July 10 2026110138  b33bb2a.avi",
        "mode": "area",
        "start_time": "2026-07-10T11:01:38Z",
        "duration_s": 300.1,
        "notes": "GSRTC depot platform, bus manoeuvring plus heavy pedestrian movement.",
    },
}


# Cameras added at runtime — an uploaded clip analysed through the dashboard.
# Kept separate from CAMERAS so the seven dataset cameras stay the fixed,
# reproducible set, while everything downstream (alerts, signal timing, charts)
# can still look a session up by id without special-casing it.
SESSION_CAMERAS: dict[str, dict] = {}

_UNKNOWN_CAMERA = {
    "name": "Unknown source",
    "mode": "area",
    "notes": "",
    "video": "",
}


def register_camera(cam_id: str, meta: dict) -> dict:
    """Add an ad-hoc camera (an upload) for this process's lifetime."""
    m = {**_UNKNOWN_CAMERA, **meta}
    SESSION_CAMERAS[cam_id] = m
    return m


def camera_meta(cam_id: str) -> dict:
    """Metadata for any camera id, dataset or uploaded.

    Falls back rather than raising: an uploaded clip must be able to flow
    through the alert and chart code paths without those modules knowing
    anything about uploads.
    """
    if cam_id in CAMERAS:
        return CAMERAS[cam_id]
    if cam_id in SESSION_CAMERAS:
        return SESSION_CAMERAS[cam_id]
    return {**_UNKNOWN_CAMERA, "name": str(cam_id)}


def camera_name(cam_id: str) -> str:
    return camera_meta(cam_id)["name"]


def camera_video_path(cam_id: str) -> Path:
    """Absolute path to a camera's source video."""
    if cam_id in SESSION_CAMERAS:
        return Path(SESSION_CAMERAS[cam_id]["video"])
    return ROOT / CAMERAS[cam_id]["video"]


def junction_cameras() -> list[str]:
    return [c for c, v in CAMERAS.items() if v["mode"] == "junction"]


def area_cameras() -> list[str]:
    return [c for c, v in CAMERAS.items() if v["mode"] == "area"]


# --------------------------------------------------------------------------
# Signal control strategy
# --------------------------------------------------------------------------
# Two controllers, and they answer different questions.
#
#   "webster"       Fixed-time plan from measured arrival flow. Needs only what
#                   one camera per approach can see, which is exactly what the
#                   Surat network gives us. Optimal when demand is steady and
#                   predictable, which is what a signal plan assumes.
#
#   "max_pressure"  Serve the phase with the greatest queue difference between
#                   the incoming approach and the link it discharges into.
#                   Throughput-optimal, and it cannot be fooled into feeding a
#                   blocked downstream link — but it NEEDS that downstream
#                   queue, and no camera in this dataset can see one.
#
# The mode is a genuine operator choice, not a cosmetic switch: selecting
# max_pressure without downstream observation does not silently degrade to a
# worse algorithm wearing a better name. See src/maxpressure.py.
OPTIMIZATION_MODE = "webster"          # webster | max_pressure
OPTIMIZATION_MODES = ("webster", "max_pressure")

# Max-pressure practicalities. Textbook max-pressure re-decides continuously in
# a slotted model where switching is free; charged the real start-up and
# clearance time of a signal it thrashes, and in our own simulation its
# throughput fell *below* the fixed-time plan it was meant to beat. These are
# the guards that make it deployable.
MP_MIN_GREEN_S = 10.0        # pedestrian crossing floor
MP_MAX_GREEN_S = 90.0        # starvation guard
MP_SWITCH_MARGIN = 1.0       # hysteresis: a challenger must beat the incumbent
MP_CYCLIC = True             # keep a cycle and let pressure set the splits


# --------------------------------------------------------------------------
# Level of Service thresholds
# --------------------------------------------------------------------------
# Derived from traffic-flow theory rather than picked by eye, so the bands mean
# something a traffic engineer can check.
#
# Working in the standard unit of PCU per km per lane:
#   jam density      k_j ~ 130 PCU/km/lane  (vehicles at standstill, bumper to
#                                            bumper, mixed Indian traffic)
#   capacity density k_c ~  60 PCU/km/lane  (= 1800 PCU/hr at ~30 km/h)
#   free flow            <  20 PCU/km/lane
#
# A 3.5 m lane covers 3500 m^2 per km, so to convert to PCU per 100 m^2:
#   density_100m2 = (PCU/km/lane) / 3500 * 100 = (PCU/km/lane) / 35
# giving  free < 0.6, capacity ~ 1.7, jam ~ 3.7.

LOS_LEVELS = ["Free", "Moderate", "Heavy", "Jam"]

LOS_COLORS = {
    "Free": (0, 200, 0),
    "Moderate": (0, 200, 220),
    "Heavy": (0, 120, 255),
    "Jam": (0, 0, 220),
}

LANE_WIDTH_M = 3.5           # IRC:86 urban lane
JAM_DENSITY_PCU_PER_KM_LANE = 130.0
CAPACITY_DENSITY_PCU_PER_KM_LANE = 60.0

# Upper bound of each band, in PCU per 100 m^2.
LOS_THRESHOLDS = {
    "Free": 0.6,       # < 21 PCU/km/lane
    "Moderate": 1.2,   # < 42 PCU/km/lane
    "Heavy": 2.2,      # < 77 PCU/km/lane, at/over capacity
    # above that is Jam
}

# Density at which a lane is considered fully jammed, for the 0-1 index.
JAM_DENSITY_PCU_PER_100M2 = JAM_DENSITY_PCU_PER_KM_LANE / 35.0  # ~3.71


# --------------------------------------------------------------------------
# Structural occupancy scale
# --------------------------------------------------------------------------
# Absolute edge-density bounds shared by every zone, measured across all seven
# cameras by src/calibrate_occupancy.py.
#
# These must be absolute, not per-zone. Normalising a zone against its own
# observed range makes the index self-referential: an empty carriageway
# stretches its own noise across the full 0-1 scale and reports "busy" whenever
# it drifts slightly above its own average, while a permanently packed street
# reports "clear" at its own quietest moment. Site-versus-site comparison then
# becomes meaningless — which is precisely what the network overview does.
#
# Observed span: empty arterial lanes ~0.02-0.05, market gridlock ~0.14-0.19.
OCCUPANCY_EDGE_CLEAR = 0.022    # bare carriageway
OCCUPANCY_EDGE_PACKED = 0.175   # wall-to-wall vehicles and people


# --------------------------------------------------------------------------
# Metric scale confidence
# --------------------------------------------------------------------------
# No camera here has a verified metric scale, and we say so rather than
# implying otherwise.
#
# Converting pixels to metres needs one true ground reference — a surveyed
# road width, the camera's height and focal length, or a landmark of known
# size. This dataset ships none of these, and three independent attempts to
# recover the scale disagreed with each other by a factor of two to four:
#
#   * assuming the traced carriageway is N lanes x 3.5 m — but the traced
#     polygons are not constant-width road (one follows a works-zone
#     narrowing), so the assumption does not hold;
#   * regressing on known vehicle widths — biased in the near field, because a
#     vehicle's box width is set by its widest point at roughly waist height,
#     not by its ground contact, so close vehicles measure far too wide;
#   * fitting the horizon from road-edge convergence — the traced edges do not
#     converge at a rate consistent with either of the above.
#
# So anything in metres, km/h or PCU/km/lane here is an *estimate carrying an
# unquantified scale factor*, and is labelled as such throughout. Congestion
# ranking, level of service and the signal-timing comparison are driven by
# scale-free measures instead, and are unaffected.
#
# Calibrating a camera promotes its metric figures to primary. That is done
# through the dashboard's Calibration view (or src/apply_scale.py), and the
# result is written to configs/calibration_state.json rather than into this
# file — an operator should not have to edit source to record a road width, and
# the setting has to survive a restart.
#
# Anything listed here is a build-time default, overridden by that file.
SCALE_VERIFIED_DEFAULTS: dict[str, bool] = {}

CALIBRATION_STATE = CONFIG_DIR / "calibration_state.json"


def _load_scale_verified() -> dict[str, bool]:
    import json
    out = dict(SCALE_VERIFIED_DEFAULTS)
    try:
        with open(CALIBRATION_STATE, "r", encoding="utf-8") as f:
            out.update({k: bool(v) for k, v in
                        json.load(f).get("scale_verified", {}).items()})
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return out


SCALE_VERIFIED = _load_scale_verified()


def refresh_scale_verified() -> dict[str, bool]:
    """Re-read the state file. Called after the dashboard writes a calibration
    so the running process sees it without a restart."""
    global SCALE_VERIFIED
    SCALE_VERIFIED = _load_scale_verified()
    return SCALE_VERIFIED


def load_optimization_mode() -> str:
    """The operator's controller choice, as last saved.

    Persisted rather than session-only: a control strategy is a standing
    operational decision, and one that silently reverted to the default on
    every page reload would be worse than no toggle at all — an operator who
    selected max-pressure would be shown Webster's numbers under the heading
    they chose.
    """
    import json
    try:
        with open(CALIBRATION_STATE, "r", encoding="utf-8") as f:
            m = json.load(f).get("optimization_mode")
        if m in OPTIMIZATION_MODES:
            return str(m)
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return OPTIMIZATION_MODE


def write_json_atomic(path, obj) -> None:
    """Write JSON so a reader never sees a half-written file.

    The state files are read back with a `JSONDecodeError` fallback to a blank
    state, so a crash or a concurrent reader mid-write used to be silent data
    loss: the next save started from that blank state and overwrote every
    stored calibration. Writing to a sibling temp file and renaming over the
    target is atomic on every platform Python supports.
    """
    import json
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_optimization_mode(mode: str) -> None:
    import json
    if mode not in OPTIMIZATION_MODES:
        raise ValueError(f"unknown optimisation mode: {mode!r}")
    try:
        with open(CALIBRATION_STATE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    state["optimization_mode"] = mode
    write_json_atomic(CALIBRATION_STATE, state)


def scale_is_verified(camera_id: str) -> bool:
    return SCALE_VERIFIED.get(camera_id, False)


def pcu_per_100m2_to_per_km_lane(d: float) -> float:
    """Convert to the unit traffic engineers actually quote."""
    return d * 35.0


# --------------------------------------------------------------------------
# Detection / tracking defaults
# --------------------------------------------------------------------------

DETECT_CONF = 0.25       # UVH-26 model is domain-matched; 0.25 keeps small
                         # distant two-wheelers without much false-positive cost
DETECT_IOU = 0.5
DETECT_IMGSZ = 960       # 1080p source; 960 retains small distant vehicles
                         # far better than the default 640
TRACKER = "bytetrack.yaml"

# Process every Nth frame. Source is 20 fps; stride 2 gives 10 fps, which is
# ample for density/queue metrics and halves inference time.
DEFAULT_STRIDE = 2
