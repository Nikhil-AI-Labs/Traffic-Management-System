"""FLUID adapter: real signalised-intersection data for max-pressure.

The Surat cameras cannot support max-pressure - no camera sees a receiving link,
so the downstream term simply is not in the data, and the controller correctly
refuses. FLUID supplies exactly what is missing.

    FLUID: A Fine-Grained Lightweight Urban Signalized-Intersection Dataset of
    Dense Conflict Trajectories. Xuancheng, Anhui, China. DJI Mini 3 at
    100-120 m. figshare DOI 10.6084/m9.figshare.29974954, CC BY 4.0.

Three intersections, each captured whole in one bird's-eye frame, so every
approach AND every receiving link is visible at once:

    FI      4-way, 3-phase
    FIDRT   4-way with dedicated right-turn lanes, 4-phase
    TI      T-intersection, 2-phase

What this module extracts, and why each matters for max-pressure:

    queue per movement      x_l   - the incoming term. Measured, in vehicles.
    queue on receiving link x_m   - the downstream term. THE thing Surat lacks.
    turn ratios             r_mk  - measured from entry/exit annotations, not
                                    assumed. Max-pressure needs these and they
                                    are unobservable from a single approach view.
    real signal state             - ground truth to compare a controller against.

GEOMETRY
--------
World coordinates are metres with the intersection near the origin. Each arm is
identified by the dominant axis, and entry and exit carriageways are separated
by the sign of the cross-axis offset (right-hand traffic, verified against the
data: N entry sits at cx ~ -7.6 m and N exit at cx ~ +6.0 m).

A vehicle is on the *approach* for its entry direction while it is still outside
the intersection box on that arm, and on the *receiving link* for its exit
direction once it is outside the box on that arm. Everything in between is
inside the junction and belongs to neither queue.
"""

from __future__ import annotations

import glob
import os
import json
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

import config as cfg

cfg.quiet_opencv()

def _find_fluid_root() -> Path:
    """Locate the FLUID download, wherever it landed.

    It is ~18 GB and is often downloaded somewhere other than the project tree
    — in this machine's case into a leftover OneDrive Desktop folder after
    Desktop backup was switched off. Searching a few sensible places beats
    hard-coding one and failing with a confusing "no data" message. Override
    with the FLUID_ROOT environment variable.
    """
    env = os.environ.get("FLUID_ROOT")
    candidates = [Path(env)] if env else []
    candidates += [
        cfg.ROOT / "29974954",
        cfg.ROOT / "data" / "29974954",
        cfg.ROOT / "data" / "FLUID",
        cfg.ROOT.parent / "29974954",
        Path.home() / "OneDrive" / "Desktop" / "PS8_dataset" / "29974954",
        Path.home() / "Desktop" / "PS8_dataset" / "29974954",
        Path.home() / "Downloads" / "29974954",
    ]
    for c in candidates:
        try:
            if c.exists() and any((c / s).exists() for s in ("FI", "FIDRT", "TI")):
                return c
        except OSError:
            continue
    return cfg.ROOT / "29974954"


FLUID_ROOT = _find_fluid_root()
CACHE_DIR = cfg.OUTPUT_DIR / "fluid_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SITES = ("FI", "FIDRT", "TI")
SITE_LABELS = {
    "FI": "FI — 4-way, 3-phase",
    "FIDRT": "FIDRT — 4-way with dedicated right turns, 4-phase",
    "TI": "TI — T-intersection, 2-phase",
}
DIRECTIONS = ("N", "S", "E", "W")
TURN_LABELS = {"s": "straight", "l": "left/U-turn", "r": "right",
               "p": "pedestrian"}

VEHICLE_TYPES = ("car", "moped", "bus", "truck", "van", "tricycle")

# A vehicle below this speed is treated as queued. Above walking pace, so a
# creeping queue still counts, but clear of GPS/tracking jitter at rest.
QUEUE_SPEED_MPS = 1.5
# Half-width of the intersection box. Inside it a vehicle is in the junction
# and belongs to neither the approach queue nor the receiving queue.
JUNCTION_BOX_M = 22.0
# How far up each arm we look. Beyond this the arm leaves the drone's frame.
ARM_RANGE_M = 80.0

# PCU per FLUID class, on the same IRC:106-1990 basis the rest of the project
# uses, so a queue here means what a queue means everywhere else.
FLUID_PCU = {"car": 1.0, "van": 1.0, "moped": 0.5, "tricycle": 1.2,
             "bus": 2.2, "truck": 2.2, "pedestrian": 0.0}


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

@dataclass
class Recording:
    site: str
    name: str
    traj_path: Path
    signal_path: Path

    @property
    def id(self) -> str:
        return f"{self.site}/{self.name}"

    @property
    def label(self) -> str:
        return f"{self.site} · {self.name}"

    @property
    def video_path(self) -> Path | None:
        """The 4K drone recording, if it was downloaded.

        The derived CSVs are a few MB; the videos are ~1 GB each. Everything
        the controller needs is in the CSVs, so the video is treated as
        optional throughout and its absence degrades the scene views rather
        than breaking the validation.
        """
        for pat in (FLUID_ROOT / self.site / "video" / "video" / f"{self.name}.mp4",
                    FLUID_ROOT / self.site / "video" / f"{self.name}.mp4"):
            if pat.exists():
                return pat
        return None

    @property
    def map_path(self) -> Path | None:
        """Orthorectified aerial image of the intersection, 3840x2160.

        Same pixel frame as the video and the trajectories' `cx`/`cy`, so
        anything in world metres can be drawn straight onto it.
        """
        for sub in ("derived_data/map", "derived_data/derived_data/map"):
            t = FLUID_ROOT / self.site / sub / f"{self.site}.tif"
            if t.exists():
                return t
        return None


def available() -> bool:
    return FLUID_ROOT.exists() and any(
        (FLUID_ROOT / s / "derived_data" / "signal").exists() for s in SITES)


def recordings(site: str | None = None) -> list[Recording]:
    """Every recording that has BOTH a trajectory file and a signal file."""
    out: list[Recording] = []
    for s in ([site] if site else SITES):
        sig_dir = FLUID_ROOT / s / "derived_data" / "signal"
        traj_dir = FLUID_ROOT / s / "derived_data" / "traj"
        if not sig_dir.exists() or not traj_dir.exists():
            continue
        for sig in sorted(sig_dir.glob("*_signal.csv")):
            stem = sig.name.replace("_signal.csv", "")
            traj = traj_dir / f"{stem}_Traj.csv"
            if traj.exists():
                out.append(Recording(s, stem, traj, sig))
    return out


def find(recording_id: str) -> Recording | None:
    return next((r for r in recordings() if r.id == recording_id), None)


# --------------------------------------------------------------------------
# Signal
# --------------------------------------------------------------------------

def load_signal(rec: Recording) -> pd.DataFrame:
    s = pd.read_csv(rec.signal_path)
    s["movement"] = s.direction.astype(str) + "-" + s.turn.astype(str)
    return s


def signal_phases(sig: pd.DataFrame) -> pd.DataFrame:
    """The real phase structure, recovered from which groups turn green together.

    FLUID gives one row per signal group per state interval, not a phase table.
    Two groups belong to the same phase when they go green at the same instant,
    so grouping the green intervals by their start time recovers the controller's
    actual staging.
    """
    g = sig[(sig.state == "G") & (sig.turn != "p")]
    if g.empty:
        return pd.DataFrame()
    grp = (g.groupby(["cycle", "begin_time"])
             .agg(movements=("movement", lambda v: tuple(sorted(set(v)))),
                  end_time=("end_time", "max"),
                  duration=("duration", "max"))
             .reset_index())
    grp["phase_key"] = grp.movements.map(lambda m: "+".join(m))
    return grp.sort_values("begin_time").reset_index(drop=True)


def phase_catalogue(sig: pd.DataFrame) -> list[dict]:
    """Distinct phases, with how often and how long each ran."""
    ph = signal_phases(sig)
    if ph.empty:
        return []
    out = (ph.groupby("phase_key")
             .agg(movements=("movements", "first"),
                  times_run=("phase_key", "size"),
                  mean_green_s=("duration", "mean"),
                  total_green_s=("duration", "sum"))
             .reset_index()
             .sort_values("total_green_s", ascending=False))
    return out.to_dict("records")


def green_at(sig: pd.DataFrame, t: float) -> set[str]:
    """Which movements are green at time t."""
    m = sig[(sig.state == "G") & (sig.begin_time <= t) & (sig.end_time > t)]
    return set(m.movement)


class _GreenIndex:
    """`green_at` for a caller asking about every second of a recording.

    The same test on plain arrays: filtering a DataFrame once per second was
    the largest single cost of a replay.
    """

    def __init__(self, sig: pd.DataFrame):
        g = sig[sig.state == "G"]
        self.begin = g.begin_time.to_numpy(dtype=float)
        self.end = g.end_time.to_numpy(dtype=float)
        self.movement = g.movement.to_numpy(dtype=object)

    def __call__(self, t: float) -> set[str]:
        m = (self.begin <= t) & (self.end > t)
        return set(self.movement[m])


def cycle_length_s(sig: pd.DataFrame) -> float:
    ph = signal_phases(sig)
    if len(ph) < 2:
        return 0.0
    starts = ph.groupby("cycle").begin_time.min().sort_values()
    return float(starts.diff().dropna().median()) if len(starts) > 1 else 0.0


# --------------------------------------------------------------------------
# Trajectories -> per-second queues
# --------------------------------------------------------------------------

def _arm(cx: np.ndarray, cy: np.ndarray) -> np.ndarray:
    """Which arm each point sits on, by dominant axis."""
    horiz = np.abs(cx) >= np.abs(cy)
    return np.where(horiz, np.where(cx >= 0, "E", "W"),
                    np.where(cy >= 0, "N", "S"))


def _along(cx: np.ndarray, cy: np.ndarray, arm: np.ndarray) -> np.ndarray:
    """Distance out along the arm from the intersection centre."""
    return np.where(np.isin(arm, ["E", "W"]), np.abs(cx), np.abs(cy))


def build_queue_table(rec: Recording, hz: float = 1.0,
                      use_cache: bool = True) -> pd.DataFrame:
    """Per-second, per-movement queue on the approach and on the receiving link.

    One row per (time, movement). `queue_in_pcu` is what is waiting to be
    served; `queue_out_pcu` is what is already sitting on the link that movement
    discharges into. Both are measured, in PCU, from the same trajectories.
    """
    # The sample rate is part of the key. Without it a 2 Hz request was
    # answered from the 1 Hz cache - half the rows, silently. The default keeps
    # its old name so existing caches stay valid.
    rate = "" if abs(hz - 1.0) < 1e-9 else f"_{hz:g}hz"
    cache = CACHE_DIR / f"{rec.site}_{rec.name}_queues{rate}.csv"
    if use_cache and cache.exists():
        return pd.read_csv(cache)

    cols = ["id", "type", "time", "speed", "cx_m", "cy_m",
            "entry_direction", "exit_direction"]
    d = pd.read_csv(rec.traj_path, usecols=cols)
    d = d[d.type.isin(VEHICLE_TYPES)].copy()
    d = d[d.entry_direction.isin(DIRECTIONS)
          & d.exit_direction.isin(DIRECTIONS)]

    # Snap to the sampling grid. FLUID is 10 FPS; 1 Hz is ample for a queue.
    step = 1.0 / hz
    d["t"] = (d.time / step).round() * step
    d = d.drop_duplicates(subset=["id", "t"])

    arm = _arm(d.cx_m.to_numpy(), d.cy_m.to_numpy())
    along = _along(d.cx_m.to_numpy(), d.cy_m.to_numpy(), arm)
    d["arm"] = arm
    d["along"] = along
    d["pcu"] = d.type.map(FLUID_PCU).fillna(1.0)
    d["movement"] = d.entry_direction + "-" + d.exit_direction

    in_range = (d.along > JUNCTION_BOX_M) & (d.along < ARM_RANGE_M)
    # On the approach: still out on the arm it entered from.
    on_approach = in_range & (d.arm == d.entry_direction)
    # On the receiving link: out on the arm it will leave by.
    on_receiving = in_range & (d.arm == d.exit_direction)
    queued = d.speed < QUEUE_SPEED_MPS

    approach = d[on_approach & queued]
    q_in = (approach.groupby(["t", "movement"]).pcu.sum()
            .rename("queue_in_pcu").reset_index())

    # The receiving-link queue is a property of the LINK, so it is counted once
    # per exit direction and then attached to every movement feeding it. That
    # is what Varaiya's downstream term is: the state of the link being
    # discharged into, not of the movement doing the discharging.
    receiving = d[on_receiving & queued]
    q_out_link = (receiving.groupby(["t", "exit_direction"]).pcu.sum()
                  .rename("queue_out_pcu").reset_index()
                  .rename(columns={"exit_direction": "to_dir"}))

    # Everything on the receiving link, moving or not. Varaiya's x_m is a
    # queue, so the stopped-only count above is the faithful reading — but at an
    # uncongested exit that term is almost always zero, which makes the
    # downstream signal invisible. The occupancy variant is what several
    # practical implementations use instead, and offering both lets the
    # dashboard show how much the downstream term is actually doing.
    all_recv = d[on_receiving]
    occ_out_link = (all_recv.groupby(["t", "exit_direction"]).pcu.sum()
                    .rename("receiving_occupancy_pcu").reset_index()
                    .rename(columns={"exit_direction": "to_dir"}))

    grid = q_in.copy()
    grid["from_dir"] = grid.movement.str[0]
    grid["to_dir"] = grid.movement.str[-1]
    out = grid.merge(q_out_link, on=["t", "to_dir"], how="left")
    out = out.merge(occ_out_link, on=["t", "to_dir"], how="left")
    out["queue_out_pcu"] = out.queue_out_pcu.fillna(0.0)
    out["receiving_occupancy_pcu"] = out.receiving_occupancy_pcu.fillna(0.0)

    # Vehicles present but moving, for context on the dashboard.
    moving = d[on_approach & ~queued]
    m_in = (moving.groupby(["t", "movement"]).pcu.sum()
            .rename("approaching_pcu").reset_index())
    out = out.merge(m_in, on=["t", "movement"], how="left")
    out["approaching_pcu"] = out.approaching_pcu.fillna(0.0)

    out = out.sort_values(["t", "movement"]).reset_index(drop=True)
    out.to_csv(cache, index=False)
    return out


def turn_ratios(rec: Recording) -> pd.DataFrame:
    """Measured r(l,m): of everything entering from l, what share leaves by m.

    Max-pressure needs these and they cannot be observed from a single approach
    camera — which is the second reason, after the downstream queue, that the
    Surat network cannot support it.
    """
    cache = CACHE_DIR / f"{rec.site}_{rec.name}_turns.csv"
    if cache.exists():
        return pd.read_csv(cache)

    d = pd.read_csv(rec.traj_path,
                    usecols=["id", "type", "entry_direction", "exit_direction"])
    d = d[d.type.isin(VEHICLE_TYPES)]
    d = d[d.entry_direction.isin(DIRECTIONS) & d.exit_direction.isin(DIRECTIONS)]
    per = d.groupby("id").first()
    t = (per.groupby(["entry_direction", "exit_direction"]).size()
         .rename("vehicles").reset_index())
    t["turn_ratio"] = t.vehicles / t.groupby("entry_direction").vehicles.transform("sum")
    t["movement"] = t.entry_direction + "-" + t.exit_direction
    t.to_csv(cache, index=False)
    return t


# --------------------------------------------------------------------------
# Movements and phases for the controller
# --------------------------------------------------------------------------

def _turn_of(from_dir: str, to_dir: str) -> str:
    """Classify a movement as straight / left / right / u-turn.

    Right-hand traffic (China), compass directions. Used to map FLUID's
    movement labels onto the signal groups, which are keyed by turn type.
    """
    order = ["N", "E", "S", "W"]
    if from_dir not in order or to_dir not in order:
        return "?"
    # A vehicle entering FROM the north is travelling south.
    heading = {"N": "S", "S": "N", "E": "W", "W": "E"}[from_dir]
    i, j = order.index(heading), order.index(to_dir)
    delta = (j - i) % 4
    return {0: "s", 1: "r", 2: "u", 3: "l"}[delta]


DOWNSTREAM_QUEUE = "queue"          # Varaiya: stopped vehicles on the link
DOWNSTREAM_OCCUPANCY = "occupancy"  # all vehicles on the link


def movements_at(queues: pd.DataFrame, t: float,
                 saturation_flow_pcu_per_hr: float = 1800.0,
                 turns: pd.DataFrame | None = None,
                 downstream: str = DOWNSTREAM_QUEUE,
                 snap: pd.DataFrame | None = None,
                 universe: set[str] | None = None) -> list:
    """Max-pressure Movement objects for one instant, from measured queues.

    The queue table is sparse: a movement with no queued vehicle at time t has
    no row. Those movements are emitted here anyway, with a queue of zero.

    That matters for more than tidiness. Dropping them makes a phase whose
    approaches happen to be empty report `0 of 0 movements observed`, which is
    indistinguishable on screen from `we have no downstream camera` — and
    telling those two apart is the entire job of the observability gate. An
    empty approach is a *measurement* of zero, not an absence of measurement,
    and the drone saw it just as clearly as it saw a full one.

    The receiving-link term is looked up per exit direction rather than
    defaulted to zero, because a link can be queued even when no vehicle is
    currently turning onto it — which is precisely the blocked-downstream case
    max-pressure exists to notice.

    `snap` (the rows at `t`) and `universe` (every movement in the recording)
    may be passed in by a caller stepping through many instants; `replay` does,
    because recomputing both from the full table every second made a replay
    quadratic in the recording's length.
    """
    import maxpressure as MP
    if snap is None:
        snap = queues[np.isclose(queues.t, t)]
    out_col = ("receiving_occupancy_pcu"
               if downstream == DOWNSTREAM_OCCUPANCY else "queue_out_pcu")
    if out_col not in snap.columns:
        out_col = "queue_out_pcu"
    ratio = {}
    if turns is not None and len(turns):
        ratio = dict(zip(turns.movement, turns.turn_ratio))

    # Receiving-link state at this instant, keyed by exit direction. Grouped
    # by (t, to_dir) upstream, so every row sharing a to_dir carries the same
    # value and taking the first is exact, not an approximation.
    # itertuples, not iterrows: iterrows builds a Series per row, and at one
    # call per second of recording that was a third of a replay's run time.
    # Same rows, same order, same values.
    rows = list(snap.itertuples(index=False))
    recv: dict[str, float] = {}
    for r in rows:
        recv.setdefault(str(r.to_dir), float(getattr(r, out_col)))

    def _mk(mid: str, q_in: float, q_out: float, measured_here: bool):
        f, to = str(mid).split("-")
        return MP.Movement(
            id=str(mid),
            label=f"{f}→{to} ({TURN_LABELS.get(_turn_of(f, to), '?')})",
            from_link=f"{f}_in",
            to_link=f"{to}_out",
            queue_in_pcu=float(q_in),
            queue_out_pcu=float(q_out),
            saturation_flow_pcu_per_hr=saturation_flow_pcu_per_hr,
            turn_ratio=float(ratio.get(mid, 1.0)),
            queue_in_source=("FLUID drone trajectories — measured"
                             if measured_here else
                             "FLUID drone trajectories — measured, no queue"),
            queue_out_source="FLUID drone trajectories — measured",
        )

    out = [_mk(r.movement, r.queue_in_pcu, getattr(r, out_col), True)
           for r in rows]

    # The movement universe is every movement seen anywhere in the recording,
    # so a turn the junction physically forbids is never invented.
    present = {m.id for m in out}
    if universe is None:
        universe = set(queues.movement.unique())
    for mid in sorted(universe - present):
        out.append(_mk(mid, 0.0, recv.get(str(mid).split("-")[-1], 0.0), False))
    return out


def controller_phases(sig: pd.DataFrame, queues: pd.DataFrame,
                      min_green_s: float = 10.0,
                      max_green_s: float = 90.0) -> list:
    """Phases for the controller, taken from the junction's REAL staging.

    Each signal group is `direction-turn`; each measured movement is
    `from-to`. A movement belongs to a phase when the phase serves the signal
    group matching its entry direction and its turn type. Using the real phase
    table rather than inventing one keeps the comparison against the actual
    signal like-for-like.

    NOT every measured movement lands in a phase, and that is correct rather
    than a gap. At these intersections 4 of 16 movements (3 of 9 at the
    T-junction) belong to no phase, and every one of them is a **right turn** —
    the free turn in right-hand traffic, permitted on red and therefore
    carrying no signal group at all. A signal controller has no lever over
    them, so ranking them in a phase pressure would attribute to the signal an
    influence it does not have. Their queues are still measured, and still
    count toward the receiving-link term of the movements that *are*
    controlled, which is where they genuinely matter.

    One modelling limitation worth naming: arms are not split into inbound and
    outbound carriageways, so for a U-turn (entry direction == exit direction)
    the receiving link is the same arm the vehicle is queued on, and those
    vehicles are counted in both terms. That depresses U-turn pressure toward
    zero. It affects 0.7-3.8% of queued PCU across the three sites, which is
    why it is documented rather than modelled away.
    """
    import maxpressure as MP
    cat = phase_catalogue(sig)
    known = sorted(queues.movement.unique())
    phases = []
    for i, p in enumerate(cat):
        groups = set(p["movements"])
        members = []
        for mv in known:
            f, to = mv.split("-")
            turn = _turn_of(f, to)
            # 'u' (U-turn) is served with the left-turn group.
            key = f"{f}-{'l' if turn == 'u' else turn}"
            if key in groups:
                members.append(mv)
        if members:
            phases.append(MP.Phase(
                id=p["phase_key"], label=" + ".join(sorted(groups)),
                movement_ids=members,
                min_green_s=min_green_s, max_green_s=max_green_s))
    return phases


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

@dataclass
class ReplayResult:
    recording: str
    seconds: float
    steps: int
    agreement_pct: float
    mode: str
    total_queue_actual: float
    pressure_served_actual: float
    pressure_served_maxpressure: float
    pressure_served_best: float
    pressure_gain_pct: float
    headroom_pct: float
    mean_pressure_actual: float = 0.0
    mean_pressure_maxpressure: float = 0.0
    mean_pressure_best: float = 0.0
    wasted_green_pct_actual: float = 0.0
    wasted_green_pct_maxpressure: float = 0.0
    phase_share_actual: dict = field(default_factory=dict)
    phase_share_maxpressure: dict = field(default_factory=dict)
    phases: list = field(default_factory=list)
    history: list = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def replay(rec: Recording, queues: pd.DataFrame, sig: pd.DataFrame,
           turns: pd.DataFrame | None = None,
           min_green_s: float = 10.0,
           saturation_flow_pcu_per_hr: float = 1800.0,
           max_seconds: float | None = None,
           downstream: str = DOWNSTREAM_QUEUE) -> ReplayResult:
    """Counterfactual: what would max-pressure have served, second by second?

    This is deliberately NOT a simulation. The queues are the ones that really
    formed under the real signal, so we cannot claim what the queues *would*
    have been under a different controller — that needs a traffic model, and
    claiming it from a replay would be exactly the kind of unsupported number
    this project refuses to print.

    What a replay CAN establish, honestly:
      * how often max-pressure would have chosen the phase the real signal
        chose — an agreement rate;
      * how much pressure the real choice relieved against the maximum
        available at that instant — a measure of the headroom left on the table.
    """
    import maxpressure as MP

    phases = controller_phases(sig, queues, min_green_s=min_green_s)
    if not phases:
        return ReplayResult(rec.id, 0, 0, 0.0, "none", 0.0, 0.0, 0.0, 0.0,
                            0.0, 0.0,
                            note="Could not recover a phase structure.")

    times = sorted(queues.t.unique())
    if max_seconds:
        times = [t for t in times if t <= max_seconds]

    by_key = {p.id: p for p in phases}
    state = MP.ControllerState(switch_margin=0.0)
    agree = 0
    counted = 0
    served_actual = 0.0
    served_mp = 0.0
    served_best = 0.0
    wasted_actual = 0
    wasted_mp = 0
    share_actual: dict = {}
    share_mp: dict = {}
    total_q = 0.0
    hist = []
    mode = MP.MODE_FULL

    # Split the table by instant once. Every `t` comes from `queues.t` itself,
    # so the group key is the exact value the per-step scan would have matched.
    by_t = {k: g for k, g in queues.groupby("t", sort=False)}
    universe = set(queues.movement.unique())
    greens = _GreenIndex(sig)

    for t in times:
        movements = movements_at(queues, t, saturation_flow_pcu_per_hr,
                                 turns, downstream=downstream,
                                 snap=by_t.get(t, queues.iloc[0:0]),
                                 universe=universe)
        if not movements:
            continue
        decision = MP.decide(movements, phases)
        mode = decision.mode
        chosen, _ = state.step(decision, 1.0, phases)

        # What the real signal was serving at this instant.
        green = greens(t)
        actual_key = None
        for p in phases:
            if set(p.id.split("+")) & green:
                actual_key = p.id
                break

        pressures = {s.phase_id: (s.pressure or 0.0) for s in decision.phases}
        best = max(pressures.values()) if pressures else 0.0
        # Only instants where the real signal's green maps onto a modelled
        # phase are counted. Roughly 9% of steps do not: all-red transitions,
        # and intervals where the only groups green are pedestrian ones. There
        # is no vehicle phase to compare against at those instants, and
        # scoring them as "green with nothing to relieve" would charge the
        # fixed plan for time it was correctly not serving traffic.
        if actual_key is not None:
            counted += 1
            if actual_key == chosen:
                agree += 1
            served_actual += pressures.get(actual_key, 0.0)
            # What max-pressure ACTUALLY served, under its own minimum-green
            # constraint — not the unconstrained instantaneous maximum. The
            # controller cannot switch every second either, and comparing the
            # real signal against a bound no controller can achieve would
            # manufacture a gain that does not exist.
            served_mp += pressures.get(chosen, 0.0)
            served_best += best
            # Green spent on a phase with nothing to relieve. The single most
            # interpretable inefficiency: a fixed cycle keeps an approach green
            # after its queue has cleared, while another approach waits.
            if pressures.get(actual_key, 0.0) <= 0:
                wasted_actual += 1
            if pressures.get(chosen, 0.0) <= 0:
                wasted_mp += 1
            share_actual[actual_key] = share_actual.get(actual_key, 0) + 1
            share_mp[chosen] = share_mp.get(chosen, 0) + 1

        q = float(sum(m.queue_in_pcu for m in movements))
        total_q += q
        hist.append({
            "t": float(t), "total_queue_pcu": round(q, 2),
            "actual_phase": actual_key or "—",
            "maxpressure_phase": chosen or "—",
            "actual_pressure": round(pressures.get(actual_key, 0.0), 3),
            "best_pressure": round(best, 3),
            "agree": bool(actual_key == chosen),
        })

    gain = ((served_mp - served_actual) / abs(served_actual) * 100.0
            if abs(served_actual) > 1e-6 else 0.0)
    return ReplayResult(
        recording=rec.id, seconds=float(times[-1]) if times else 0.0,
        steps=len(hist),
        agreement_pct=(agree / counted * 100.0) if counted else 0.0,
        mode=mode,
        total_queue_actual=total_q / max(1, len(hist)),
        pressure_served_actual=served_actual,
        pressure_served_maxpressure=served_mp,
        pressure_served_best=served_best,
        pressure_gain_pct=gain,
        headroom_pct=((served_best - served_actual) / abs(served_best) * 100.0
                      if abs(served_best) > 1e-6 else 0.0),
        mean_pressure_actual=served_actual / max(1, counted),
        mean_pressure_maxpressure=served_mp / max(1, counted),
        mean_pressure_best=served_best / max(1, counted),
        wasted_green_pct_actual=wasted_actual / max(1, counted) * 100.0,
        wasted_green_pct_maxpressure=wasted_mp / max(1, counted) * 100.0,
        phase_share_actual={k: v / max(1, counted) * 100.0
                            for k, v in share_actual.items()},
        phase_share_maxpressure={k: v / max(1, counted) * 100.0
                                 for k, v in share_mp.items()},
        phases=[{"id": p.id, "label": p.label,
                 "movements": p.movement_ids} for p in phases],
        history=hist,
    )


def summary(rec: Recording) -> dict:
    """Headline facts about one recording, for the dashboard."""
    sig = load_signal(rec)
    q = build_queue_table(rec)
    return {
        "recording": rec.id,
        "site": rec.site,
        "label": SITE_LABELS.get(rec.site, rec.site),
        "duration_s": float(q.t.max()) if len(q) else 0.0,
        "cycles": int(sig.cycle.nunique()),
        "cycle_length_s": cycle_length_s(sig),
        "movements": int(q.movement.nunique()),
        "phases": len(phase_catalogue(sig)),
        "mean_queue_pcu": float(q.groupby("t").queue_in_pcu.sum().mean()),
        "peak_queue_pcu": float(q.groupby("t").queue_in_pcu.sum().max()),
    }


# --------------------------------------------------------------------------
# Scene rendering
# --------------------------------------------------------------------------
# The orthophoto shares its pixel frame with the video and with the
# trajectories' cx/cy columns, and the mapping from world metres to that frame
# is a plain isotropic affine - about 24 px per metre, y inverted. Fitting it
# from the data rather than hard-coding it keeps every site correct without a
# per-site constant, and the residual is reported so a bad fit is visible.

@dataclass
class WorldToPixel:
    """Full 2-D affine from world metres to image pixels.

    A full affine, not two independent per-axis fits, because the drone's
    heading is not aligned to the world axes and a per-axis fit cannot express
    rotation at all. The three sites are rotated by -0.2 deg, +2.3 deg and
    +156.8 deg; on the last of those the per-axis version was placing zones
    about 8 m out, while the affine lands within 2 px. It is the difference
    between a picture that agrees with the numbers and one that quietly does
    not.
    """

    m: np.ndarray          # 2x3: [[a, b, tx], [c, d, ty]]
    residual_px: float
    rotation_deg: float

    def __call__(self, mx, my):
        mx = np.asarray(mx, dtype=float)
        my = np.asarray(my, dtype=float)
        X = self.m[0, 0] * mx + self.m[0, 1] * my + self.m[0, 2]
        Y = self.m[1, 0] * mx + self.m[1, 1] * my + self.m[1, 2]
        return X, Y

    @property
    def px_per_m(self) -> float:
        return float(np.hypot(self.m[0, 0], self.m[1, 0]))

    def scaled(self, k: float) -> "WorldToPixel":
        return WorldToPixel(self.m * k, self.residual_px, self.rotation_deg)

    def translated(self, dx: float, dy: float) -> "WorldToPixel":
        m = self.m.copy()
        m[0, 2] += dx
        m[1, 2] += dy
        return WorldToPixel(m, self.residual_px, self.rotation_deg)


@lru_cache(maxsize=8)
def world_to_pixel(recording_id: str):
    """Fit metres -> image pixels from the trajectory file's own columns.

    Both coordinate systems are given per row, so this is a plain least-squares
    fit with no assumptions about the drone's position or heading.
    """
    rec = find(recording_id)
    if rec is None:
        return None
    d = pd.read_csv(rec.traj_path, usecols=["cx", "cy", "cx_m", "cy_m"]).dropna()
    if len(d) < 100:
        return None
    if len(d) > 200_000:
        d = d.sample(200_000, random_state=0)

    A = np.c_[d.cx_m.to_numpy(), d.cy_m.to_numpy(), np.ones(len(d))]
    px, *_ = np.linalg.lstsq(A, d.cx.to_numpy(), rcond=None)
    py, *_ = np.linalg.lstsq(A, d.cy.to_numpy(), rcond=None)
    res = float(max(np.median(np.abs(A @ px - d.cx.to_numpy())),
                    np.median(np.abs(A @ py - d.cy.to_numpy()))))
    m = np.array([px, py], dtype=float)
    rot = float(np.degrees(np.arctan2(m[1, 0], m[0, 0])))
    return WorldToPixel(m, res, rot)


@lru_cache(maxsize=4)
def scene_image(recording_id: str, max_width: int = 1600):
    """Aerial reference image for a recording: orthophoto, else a video frame."""
    import cv2
    rec = find(recording_id)
    if rec is None:
        return None
    img = None
    if rec.map_path is not None:
        img = cv2.imread(str(rec.map_path), cv2.IMREAD_COLOR)
    if img is None and rec.video_path is not None:
        cap = cfg.open_video(str(rec.video_path))
        cap.set(cv2.CAP_PROP_POS_FRAMES, 30)
        ok, frame = cap.read()
        cap.release()
        img = frame if ok else None
    if img is None:
        return None

    # Force 3-channel BGR. `IMREAD_COLOR` is not enough: OpenCV's TIFF decoder
    # returns the file's native channel count regardless, and these orthophotos
    # carry an alpha channel. A 4-channel array then breaks every downstream
    # blend and colour conversion with a broadcast error that reads as a bug in
    # the caller rather than in the loader.
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    elif img.shape[2] != 3:
        img = img[:, :, :3]

    box = _scene_crop(recording_id)
    if box is not None:
        h, w = img.shape[:2]
        x0 = int(max(0, min(box[0], w - 2)))
        y0 = int(max(0, min(box[1], h - 2)))
        x1 = int(min(w, max(box[2], x0 + 2)))
        y1 = int(min(h, max(box[3], y0 + 2)))
        if x1 - x0 > 50 and y1 - y0 > 50:
            img = img[y0:y1, x0:x1]

    if img.shape[1] > max_width:
        s = max_width / img.shape[1]
        img = cv2.resize(img, (max_width, int(img.shape[0] * s)),
                         interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(img)


@lru_cache(maxsize=8)
def _scene_crop(recording_id: str, margin_m: float = 18.0):
    """Pixel box of the area worth showing, in native 3840-wide coordinates.

    The orthophotos are rotated mosaics, so a large share of the frame is black
    padding outside the captured footprint. Cropping to the region traffic
    actually occupies - the trajectory extent plus a margin - removes it, and
    incidentally frames the junction rather than leaving it small and
    off-centre.
    """
    rec = find(recording_id)
    w2p = world_to_pixel(recording_id)
    if rec is None or w2p is None:
        return None
    d = pd.read_csv(rec.traj_path, usecols=["cx_m", "cy_m"]).dropna()
    if d.empty:
        return None
    lo_x, hi_x = d.cx_m.quantile([0.001, 0.999])
    lo_y, hi_y = d.cy_m.quantile([0.001, 0.999])
    corners = [(x, y) for x in (lo_x - margin_m, hi_x + margin_m)
               for y in (lo_y - margin_m, hi_y + margin_m)]
    X, Y = w2p([c[0] for c in corners], [c[1] for c in corners])
    return (float(X.min()), float(Y.min()), float(X.max()), float(Y.max()))


def _scaled(w2p, img, recording_id: str = ""):
    """Map the native-frame fit into the displayed image's coordinates.

    The displayed image is the native frame cropped to the data extent and
    then resized, so the fit has to follow the same two steps in the same
    order: translate by the crop origin, then scale by the resize factor.
    Getting the order wrong puts every zone a few metres out, which looks
    plausible enough on screen to go unnoticed.
    """
    box = _scene_crop(recording_id) if recording_id else None
    if box is not None:
        native_w = max(2.0, box[2] - box[0])
        w2p = w2p.translated(-box[0], -box[1])
    else:
        native_w = 3840.0
    return w2p.scaled(img.shape[1] / native_w)


_ARM_VEC = {"N": (0.0, 1.0), "S": (0.0, -1.0), "E": (1.0, 0.0), "W": (-1.0, 0.0)}


def zone_overlay(recording_id: str, queues=None, t=None,
                 half_width_m: float = 11.0):
    """The intersection with its approach and receiving zones drawn on.

    The direct analogue of the Surat scene overlay, answering the same
    question: *where exactly is the system measuring?* Approaches are shaded by
    the queue standing on them at instant `t`, so the picture and the pressure
    table cannot disagree.
    """
    import cv2
    img = scene_image(recording_id)
    w2p = world_to_pixel(recording_id)
    if img is None or w2p is None:
        return None
    m = _scaled(w2p, img, recording_id)
    out = img.copy()
    overlay = out.copy()

    q_by_dir = {}
    if queues is not None and t is not None and len(queues):
        snap = queues[np.isclose(queues.t, t)]
        if len(snap):
            q_by_dir = snap.groupby("from_dir").queue_in_pcu.sum().to_dict()
    qmax = max(q_by_dir.values()) if q_by_dir else 0.0

    def quad(d, near, far, receiving):
        """Polygon along arm `d`, between `near` and `far` metres from centre."""
        ux, uy = _ARM_VEC[d]
        px, py = -uy, ux
        # Right-hand traffic: approach and receiving carriageways sit on
        # opposite sides of the arm centreline.
        off = half_width_m * 0.5 * (1 if receiving else -1)
        pts = []
        for dist, sign in ((near, -1), (far, -1), (far, 1), (near, 1)):
            w = off + sign * half_width_m * 0.5
            X, Y = m(ux * dist + px * w, uy * dist + py * w)
            pts.append([int(X), int(Y)])
        return np.array(pts, np.int32)

    for d in DIRECTIONS:
        cv2.fillPoly(overlay, [quad(d, JUNCTION_BOX_M, ARM_RANGE_M, True)],
                     (150, 150, 150))
        q = float(q_by_dir.get(d, 0.0))
        frac = (q / qmax) if qmax > 1e-9 else 0.0
        col = (int(60 + 40 * frac), int(200 - 150 * frac), int(60 + 190 * frac))
        cv2.fillPoly(overlay, [quad(d, JUNCTION_BOX_M, ARM_RANGE_M, False)], col)

    out = cv2.addWeighted(overlay, 0.34, out, 0.66, 0)

    X0, Y0 = m(-JUNCTION_BOX_M, -JUNCTION_BOX_M)
    X1, Y1 = m(JUNCTION_BOX_M, JUNCTION_BOX_M)
    cv2.rectangle(out, (int(X0), int(Y0)), (int(X1), int(Y1)),
                  (255, 225, 120), 2)

    s = max(0.6, img.shape[1] / 1600.0)
    for d in DIRECTIONS:
        ux, uy = _ARM_VEC[d]
        X, Y = m(ux * (ARM_RANGE_M * 0.72), uy * (ARM_RANGE_M * 0.72))
        q = float(q_by_dir.get(d, 0.0))
        txt = f"{d}  {q:.0f} PCU" if q_by_dir else d
        (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.7 * s, 2)
        x, y = int(X - tw / 2), int(Y)
        cv2.rectangle(out, (x - 7, y - th - 7), (x + tw + 7, y + 7),
                      (20, 20, 20), -1)
        cv2.putText(out, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7 * s,
                    (255, 255, 255), 2, cv2.LINE_AA)
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)


@lru_cache(maxsize=4)
def trajectory_heatmap(recording_id: str, blur: int = 21):
    """Where vehicles actually travel, accumulated over the whole recording.

    The same empirical check the Surat zones were given: a corridor drawn from
    geometry can look right and still sit off the path traffic really takes.
    """
    import cv2
    img = scene_image(recording_id)
    w2p = world_to_pixel(recording_id)
    rec = find(recording_id)
    if img is None or w2p is None or rec is None:
        return None
    m = _scaled(w2p, img, recording_id)
    d = pd.read_csv(rec.traj_path, usecols=["cx_m", "cy_m", "type"]).dropna()
    d = d[d.type != "pedestrian"]
    if d.empty:
        return None

    h, w = img.shape[:2]
    acc = np.zeros((h, w), np.float32)
    X, Y = m(d.cx_m.to_numpy(), d.cy_m.to_numpy())
    X = np.clip(X.astype(int), 0, w - 1)
    Y = np.clip(Y.astype(int), 0, h - 1)
    np.add.at(acc, (Y, X), 1.0)
    acc = cv2.GaussianBlur(acc, (blur, blur), 0)
    if acc.max() > 0:
        acc = acc / acc.max()
    acc = np.power(acc, 0.42)          # lift the long tail into view

    heat = cv2.applyColorMap((acc * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    mask = (acc > 0.06)[..., None].astype(np.float32)
    out = (img * (1 - mask * 0.72) + heat * (mask * 0.72)).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)


@lru_cache(maxsize=2)
def _vehicle_positions(recording_id: str) -> pd.DataFrame:
    """Every non-pedestrian position in a recording, slimmed for repeat use.

    `annotated_instant` is called once per move of the dashboard's time
    slider, and used to parse the whole trajectory CSV - up to 145 MB - every
    time to draw one instant. Read once, keep the five columns it needs at
    float32, and the slider stays interactive.
    """
    rec = find(recording_id)
    if rec is None:
        return pd.DataFrame(columns=["time", "cx_m", "cy_m", "speed"])
    d = pd.read_csv(rec.traj_path,
                    usecols=["time", "cx_m", "cy_m", "speed", "type"]).dropna()
    d = d[d.type != "pedestrian"].drop(columns="type")
    # time stays float64: it is matched with np.isclose against the queue
    # table's grid, and float32 rounding at ~2000 s is coarser than its rtol.
    return d.astype({"cx_m": "float32", "cy_m": "float32",
                     "speed": "float32"}).reset_index(drop=True)


def annotated_instant(recording_id: str, t: float, radius_m: float = 2.2):
    """Every vehicle at one instant: queued in red, moving in green.

    The picture behind the pressure table - a reader can count the queues that
    produced the numbers instead of taking them on trust.
    """
    import cv2
    img = scene_image(recording_id)
    w2p = world_to_pixel(recording_id)
    rec = find(recording_id)
    if img is None or w2p is None or rec is None:
        return None, 0, 0
    m = _scaled(w2p, img, recording_id)
    d = _vehicle_positions(recording_id)
    d = d[np.isclose(d.time.to_numpy(), t)]
    out = img.copy()
    r = max(3, int(radius_m * m.px_per_m * 0.5))
    for _, v in d.iterrows():
        X, Y = m(v.cx_m, v.cy_m)
        queued = v.speed < QUEUE_SPEED_MPS
        col = (60, 60, 235) if queued else (90, 200, 90)
        cv2.circle(out, (int(X), int(Y)), r, col, -1, cv2.LINE_AA)
        cv2.circle(out, (int(X), int(Y)), r, (255, 255, 255), 1, cv2.LINE_AA)
    return (cv2.cvtColor(out, cv2.COLOR_BGR2RGB), int(len(d)),
            int((d.speed < QUEUE_SPEED_MPS).sum()))


if __name__ == "__main__":
    print("FLUID available:", available())
    for r in recordings():
        print(" ", r.id)
