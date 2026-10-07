"""Adaptive signal cycle optimisation from measured lane demand.

Uses Webster's method, the standard basis for fixed-time signal design:

    optimal cycle   C_opt = (1.5 L + 5) / (1 - Y)
    green split     g_i   = (C_opt - L) * y_i / Y

where y_i = q_i / s_i is the flow ratio on the critical lane of approach i,
Y = sum of the critical y_i, and L is total lost time per cycle.

This is one of two controllers the system offers. The other, max-pressure, is
in `maxpressure.py`; `config.OPTIMIZATION_MODE` selects between them and the
dashboard exposes that as an operator toggle. They are not interchangeable,
and the difference is about what each one needs to see:

    Webster's      needs arrival flow per approach - exactly what one camera
                   per approach measures, which is what the Surat network is.
                   Optimal when demand is steady enough to plan a cycle for.

    max-pressure   needs the queue on the *downstream* link of every movement.
                   Throughput-optimal and it cannot be fooled into discharging
                   into a blocked link - but no Surat camera sees a receiving
                   link, so there it reports `greedy-queue` and refuses. It is
                   measured instead on the FLUID drone dataset, where a whole
                   intersection fits in one aerial frame.

So Webster's is the default here because it is the one this footage can
actually support, not because max-pressure was skipped. See `maxpressure.py`
for the observability gate and `fluid.py` for where it is exercised for real.

Delay is estimated under two models, because the answer depends on which you
believe: Webster's own three-term formula diverges as an approach reaches
capacity - exactly where the busier approach here sits - so above a degree of
saturation of 0.85 the HCM control-delay form takes over, whose time-dependent
term stays finite above capacity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import pandas as pd

import config as cfg
from metrics import SATURATION_FLOW_PCU_PER_LANE_PER_HR


# Typical Indian urban signal parameters.
LOST_TIME_PER_PHASE_S = 4.0     # start-up lost time + clearance
MAX_GREEN_S = 90.0

# --------------------------------------------------------------------------
# Pedestrian minimum green
# --------------------------------------------------------------------------
# A flat 10 s used to sit here labelled "pedestrian crossing minimum". It is
# not one. The minimum is a function of how far somebody has to walk, and under
# either governing standard 10 s does not clear even the narrowest plausible
# crossing:
#
#   IRC:103-2012 (Guidelines for Pedestrian Facilities — the Indian code that
#   governs Surat): walking speed 1.0 m/s general, 0.8 m/s where elderly
#   pedestrians are a high proportion. The traverse itself is L/v — a 15 m
#   crossing at 0.8 m/s is 18.8 s — and a start-up interval is provided before
#   it, for the pedestrian to perceive the signal and step off the kerb.
#
#   MUTCD (2009 §4E.06 / 2023 Ch. 4I), for comparison, makes the same two
#   parts explicit: a 7 s WALK interval, then clearance at 1.0668 m/s
#   (3.5 ft/s), then a 3 s buffer.
#
# Both standards are therefore start-up PLUS traverse, never traverse alone.
# Getting that wrong is not a rounding difference: with the start-up interval
# dropped, a 7 m crossing computes to 8.8 s, which is *shorter* than the flat
# 10 s this block replaced — a change made in the name of pedestrian safety
# that quietly lowered the safety floor. The absolute floor below is set so
# that cannot recur.
#
# At our default 7 m approach the floor is 11.8 s (IRC) or 16.6 s (MUTCD); at
# 12 m, 18.0 s or 21.3 s. Raising the floor lengthens the cycle on a low-demand
# approach, and that trade-off is real — but it is a pedestrian safety
# requirement, not a tuning parameter, so the optimiser has to carry it.
IRC_WALK_SPEED_MPS = 1.0            # IRC:103-2012 general
IRC_WALK_SPEED_ELDERLY_MPS = 0.8    # where elderly pedestrians are common
IRC_STARTUP_INTERVAL_S = 3.0        # perceive the signal and step off the kerb
MUTCD_WALK_INTERVAL_S = 7.0
MUTCD_WALK_SPEED_MPS = 1.0668       # 3.5 ft/s
MUTCD_BUFFER_S = 3.0

# Never below the flat constant this replaced. A computed minimum that comes
# out under the value it superseded is a regression however good its
# derivation looks, so the floor is enforced rather than trusted.
ABSOLUTE_MIN_GREEN_S = 10.0


def pedestrian_min_green_s(crossing_length_m: float,
                           walk_speed_mps: float = IRC_WALK_SPEED_ELDERLY_MPS,
                           standard: str = "IRC") -> float:
    """Green a pedestrian needs to cross `crossing_length_m` safely.

    Both standards are start-up plus traverse:

        IRC     start-up (3 s) + L / v,  v = 0.8 m/s where elderly
                pedestrians are common
        MUTCD   7 s WALK + L / 1.0668 + 3 s buffer

    We default to IRC at the conservative 0.8 m/s, because it is the code that
    applies here and because Surat's crossings carry a wide age range. The
    result is floored at `ABSOLUTE_MIN_GREEN_S` — a very short crossing still
    needs a green a person can actually react to and use.
    """
    L = max(0.0, float(crossing_length_m))
    v = max(0.3, float(walk_speed_mps))
    if standard.upper() == "MUTCD":
        return max(ABSOLUTE_MIN_GREEN_S,
                   MUTCD_WALK_INTERVAL_S + L / MUTCD_WALK_SPEED_MPS
                   + MUTCD_BUFFER_S)
    return max(ABSOLUTE_MIN_GREEN_S, IRC_STARTUP_INTERVAL_S + L / v)


# Kept as the fallback when no approach width is known. Derived, not guessed:
# the IRC figure for a 7 m crossing at 0.8 m/s.
MIN_GREEN_S = pedestrian_min_green_s(7.0)
AMBER_S = 3.0
ALL_RED_S = 2.0
MIN_CYCLE_S = 40.0
MAX_CYCLE_S = 150.0

# --------------------------------------------------------------------------
# Saturation flow
# --------------------------------------------------------------------------
# IRC:SP:41-1994 gives saturation flow as a function of *approach width* rather
# than lane count: S = 525 * W PCU/hr for widths above 5.5 m. That form matters
# here, because "number of lanes" is precisely the quantity this dataset cannot
# pin down — the traced carriageways are not constant-width road, which is what
# defeated the first attempt at recovering a metric scale. A width-based
# formula never invokes a lane at all.
#
# Field measurement at Vijay Cross-road, Ahmedabad (approach widths 7.1–10.2 m)
# found real Indian saturation flow running well above the IRC formula:
# 636·W to 1283·W depending on which PCU convention is used. So the formula is
# a floor, not a point estimate, and the optimiser is run as a band across that
# range rather than pretending to one number.
IRC_SATURATION_FACTOR = 525.0          # IRC:SP:41-1994, PCU/hr per metre
FIELD_SATURATION_FACTOR_LOW = 636.0    # Ahmedabad field, Justo & Tuladhar PCU
FIELD_SATURATION_FACTOR_HIGH = 1283.0  # Ahmedabad field, IRC PCU
DEFAULT_APPROACH_WIDTH_M = 7.0

# HCM incremental-delay parameters.
HCM_ANALYSIS_PERIOD_HR = 0.25   # T — 15-minute analysis period
HCM_INCREMENTAL_K = 0.5         # k — pretimed control
HCM_UPSTREAM_FILTER_I = 1.0     # I — isolated intersection


# IRC:SP:41-1994 states the linear form for approach widths above 5.5 m; below
# that the code gives tabulated values rather than the formula. We keep the
# linear form throughout — it degrades sensibly, giving roughly one lane's
# worth at 3.5 m — but refuse to extrapolate it below a width no vehicle fits
# through. The dashboard's width control accepts values from 2.0 m, so without
# this floor a mistyped width silently produces a saturation flow of a few
# hundred PCU/hr, which then makes every approach look oversaturated.
MIN_APPROACH_WIDTH_M = 3.0          # one running lane
IRC_FORMULA_VALID_ABOVE_M = 5.5     # IRC:SP:41-1994's own stated range


def saturation_flow(width_m: float,
                    factor: float = IRC_SATURATION_FACTOR) -> float:
    """S = factor * W, PCU/hr for the whole approach (IRC:SP:41-1994).

    Width is floored at one running lane. Below `IRC_FORMULA_VALID_ABOVE_M`
    the linear form is outside the range the code states it for; it is still
    the best available estimate and is used, but `saturation_flow_note` says
    so, and the dashboard surfaces that note rather than presenting an
    extrapolation as if it were the standard.
    """
    w = max(MIN_APPROACH_WIDTH_M, float(width_m))
    return max(1.0, float(factor) * w)


def saturation_flow_note(width_m: float) -> str:
    """Empty when the width is inside IRC's stated range; a caveat when not."""
    w = float(width_m)
    if w < MIN_APPROACH_WIDTH_M:
        return (f"Approach width {w:.1f} m is narrower than one running lane; "
                f"saturation flow computed at the {MIN_APPROACH_WIDTH_M:.1f} m "
                f"floor.")
    if w < IRC_FORMULA_VALID_ABOVE_M:
        return (f"IRC:SP:41-1994 states S = {IRC_SATURATION_FACTOR:.0f}·W for "
                f"widths above {IRC_FORMULA_VALID_ABOVE_M:.1f} m and tabulates "
                f"narrower ones. At {w:.1f} m this is an extrapolation of the "
                f"linear form, not the standard's own figure.")
    return ""


@dataclass
class ApproachDemand:
    """Measured demand on one signal approach."""

    approach_id: str
    label: str
    camera_id: str
    lanes: int
    flow_pcu_per_hr: float
    critical_lane_flow_pcu_per_hr: float
    mean_speed_kmph: float | None = None
    mean_density_pcu_per_km_lane: float | None = None
    saturation_flow_per_lane: float = SATURATION_FLOW_PCU_PER_LANE_PER_HR
    # When the operator has supplied a real approach width, saturation flow is
    # derived from it per IRC:SP:41 instead of assuming a per-lane constant.
    approach_width_m: float | None = None
    saturation_factor: float = IRC_SATURATION_FACTOR

    def __post_init__(self):
        if self.approach_width_m:
            self.saturation_flow_per_lane = (
                saturation_flow(self.approach_width_m, self.saturation_factor)
                / max(1, self.lanes))

    @property
    def flow_ratio(self) -> float:
        """y_i = q_i / s_i on the critical lane."""
        s = self.saturation_flow_per_lane
        return min(0.95, self.critical_lane_flow_pcu_per_hr / s) if s else 0.0

    def lane_capacity(self, green_s: float, cycle_s: float) -> float:
        """Capacity of the *critical lane* under a given green split.

        Everything in Webster's method is anchored on the critical lane: the
        signal has to clear the worst lane on the approach, not the average
        one. Capacity and degree of saturation must therefore be computed on
        that same per-lane basis as y_i. Comparing whole-approach flow against
        whole-approach capacity while defining y_i per lane mixes two different
        units and silently understates saturation — enough to erase the delay
        minimum that Webster's optimal cycle is supposed to sit at.
        """
        if cycle_s <= 0:
            return 0.0
        return self.saturation_flow_per_lane * (green_s / cycle_s)


@dataclass
class PhasePlan:
    approach_id: str
    label: str
    green_s: float
    amber_s: float
    all_red_s: float
    flow_ratio: float
    capacity_pcu_per_hr: float
    degree_of_saturation: float
    est_delay_s_per_pcu: float
    delay_model: str = "Webster"

    @property
    def total_s(self) -> float:
        return self.green_s + self.amber_s + self.all_red_s


@dataclass
class SignalPlan:
    junction: str
    cycle_s: float
    lost_time_s: float
    total_flow_ratio: float
    phases: list[PhasePlan] = field(default_factory=list)
    feasible: bool = True
    note: str = ""

    @property
    def mean_delay_s_per_pcu(self) -> float:
        """Flow-weighted mean delay across approaches."""
        num = sum(p.est_delay_s_per_pcu * p.flow_ratio for p in self.phases)
        den = sum(p.flow_ratio for p in self.phases)
        return num / den if den else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mean_delay_s_per_pcu"] = round(self.mean_delay_s_per_pcu, 1)
        return d


# --------------------------------------------------------------------------
# Webster
# --------------------------------------------------------------------------

def websters_cycle(total_flow_ratio: float, lost_time_s: float) -> tuple[float, bool]:
    """Optimal cycle length. Returns (cycle, feasible).

    Y >= 1 means demand exceeds what the junction can discharge no matter how
    the green is divided; no cycle length fixes that, and saying otherwise
    would be misleading. We clamp to the maximum cycle and flag it instead.
    """
    if total_flow_ratio >= 0.95:
        return MAX_CYCLE_S, False
    c = (1.5 * lost_time_s + 5.0) / (1.0 - total_flow_ratio)
    return float(min(MAX_CYCLE_S, max(MIN_CYCLE_S, c))), True


def websters_delay(
    cycle_s: float, green_s: float, flow_pcu_per_hr: float,
    capacity_pcu_per_hr: float, three_term: bool = True,
) -> float:
    """Average delay per PCU (s), Webster's 1958 formula.

        d = C(1-l)^2 / [2(1 - l*x)]  +  x^2 / [2q(1 - x)]
              -  0.65 (C/q^2)^(1/3) x^(2 + 5l)

    with l = g/C. The third term is Webster's empirical correction and takes
    5-15% off the two-term estimate; without it the formula reads high.

    The second term diverges as x approaches 1, which is the formula's known
    failure mode and exactly where our busier approach sits. That divergence is
    reported, not suppressed: an earlier version silently clamped x to 0.98 and
    the delay to 120 s, which made a hopelessly oversaturated approach look
    merely busy and quietly flattered every plan measured against it. The
    honest response to a formula that has stopped working is to say so and use
    a different one, which is what `hcm_delay` below is for and what
    `approach_delay` switches to above x = 0.85.

    Above capacity the return is `math.inf`. Webster's has no finite answer
    there, and returning one would be inventing it.
    """
    if capacity_pcu_per_hr <= 0 or cycle_s <= 0:
        return 0.0
    x = flow_pcu_per_hr / capacity_pcu_per_hr
    lam = green_s / cycle_s
    if lam <= 0:
        return 0.0
    if x >= 1.0:
        return math.inf

    d1 = ((cycle_s * (1 - lam) ** 2) / (2 * (1 - lam * x))
          if (1 - lam * x) > 1e-6 else 0.0)
    q = max(1e-6, flow_pcu_per_hr / 3600.0)   # arrivals per second
    d2 = (x ** 2) / (2 * q * (1 - x))

    d3 = 0.0
    if three_term and q > 0:
        d3 = 0.65 * (cycle_s / (q ** 2)) ** (1 / 3) * (x ** (2 + 5 * lam))
        d3 = min(d3, 0.35 * (d1 + d2))   # the correction is a trim, not a veto
    return float(max(0.0, d1 + d2 - d3))


def hcm_delay(
    cycle_s: float, green_s: float, flow_pcu_per_hr: float,
    capacity_pcu_per_hr: float,
    T: float = HCM_ANALYSIS_PERIOD_HR, k: float = HCM_INCREMENTAL_K,
    I: float = HCM_UPSTREAM_FILTER_I,
) -> float:
    """HCM control delay, d = d1 + d2 (no initial-queue term).

        d1 = 0.5 C (1 - g/C)^2 / [1 - min(1, X) (g/C)]
        d2 = 900 T [ (X-1) + sqrt( (X-1)^2 + 8 k I X / (c T) ) ]

    The point of d2 is that it stays **finite above capacity**: the square root
    keeps the overflow bounded by the length of the analysis period, so an
    oversaturated approach yields a large-but-real delay instead of Webster's
    divergence. That is not a detail here — the baseline fixed-time plan drives
    the busier approach to x > 1, which is the whole reason retiming helps, and
    a capped Webster term would understate exactly the case being fixed.
    """
    if capacity_pcu_per_hr <= 0 or cycle_s <= 0:
        return 0.0
    lam = green_s / cycle_s
    if lam <= 0:
        return 0.0
    X = flow_pcu_per_hr / capacity_pcu_per_hr

    denom = 1.0 - min(1.0, X) * lam
    d1 = (0.5 * cycle_s * (1 - lam) ** 2) / denom if denom > 1e-6 else 0.0

    c = capacity_pcu_per_hr
    inner = (X - 1.0) ** 2 + (8.0 * k * I * X) / max(1e-6, c * T)
    d2 = 900.0 * T * ((X - 1.0) + math.sqrt(max(0.0, inner)))
    return float(max(0.0, d1 + d2))


# Above this degree of saturation Webster's random-delay term stops being
# trustworthy and the HCM time-dependent form takes over.
HCM_SWITCH_X = 0.85


def approach_delay(cycle_s: float, green_s: float, flow_pcu_per_hr: float,
                   capacity_pcu_per_hr: float,
                   model: str = "auto") -> tuple[float, str]:
    """Delay plus the name of the model that produced it."""
    if capacity_pcu_per_hr <= 0:
        return 0.0, "none"
    x = flow_pcu_per_hr / capacity_pcu_per_hr
    use_hcm = model == "hcm" or (model == "auto" and x >= HCM_SWITCH_X)
    if use_hcm:
        return hcm_delay(cycle_s, green_s, flow_pcu_per_hr,
                         capacity_pcu_per_hr), "HCM"
    return websters_delay(cycle_s, green_s, flow_pcu_per_hr,
                          capacity_pcu_per_hr), "Webster"


def common_degree_of_saturation(Y: float, cycle_s: float,
                                lost_time_s: float) -> float:
    """X = Y·C / (C − L) — the value every critical group lands on.

    Webster's proportional split gives group i a green of g_i = (y_i/Y)(C − L),
    so its degree of saturation is

        x_i = y_i·C / g_i = y_i·C·Y / (y_i (C − L)) = Y·C / (C − L)

    which has no i in it. Equal saturation across approaches is therefore not a
    happy accident of the optimiser; it is what proportional allocation *is*.
    That makes X the single number worth putting on a signal dashboard.
    """
    eff = cycle_s - lost_time_s
    return float(Y * cycle_s / eff) if eff > 0 else float("inf")


def websters_cmin(lost_time_s: float, Y: float, Xc: float = 0.90) -> float:
    """C_min = L·X_c / (X_c − Y): the shortest cycle that holds v/c at X_c."""
    denom = Xc - Y
    if denom <= 1e-6:
        return float("inf")
    return float(lost_time_s * Xc / denom)


def optimise(
    approaches: list[ApproachDemand],
    junction_name: str = "Junction",
    lost_time_per_phase_s: float = LOST_TIME_PER_PHASE_S,
    delay_model: str = "auto",
) -> SignalPlan:
    """Webster-optimal cycle and green splits for the measured demand."""
    n = len(approaches)
    if n == 0:
        return SignalPlan(junction=junction_name, cycle_s=0, lost_time_s=0,
                          total_flow_ratio=0, feasible=False,
                          note="No approach demand supplied.")

    lost_time = n * lost_time_per_phase_s
    Y = sum(a.flow_ratio for a in approaches)
    cycle, feasible = websters_cycle(Y, lost_time)

    effective_green_total = max(1.0, cycle - lost_time)
    phases: list[PhasePlan] = []

    greens: list[float] = []
    for a in approaches:
        share = (a.flow_ratio / Y) if Y > 0 else 1.0 / n
        g = effective_green_total * share
        # The floor is this approach's own pedestrian crossing time, not a
        # constant: a wider approach takes longer to walk across.
        ped_min = pedestrian_min_green_s(
            a.approach_width_m or DEFAULT_APPROACH_WIDTH_M)
        greens.append(float(min(MAX_GREEN_S, max(ped_min, g))))

    # A clamp breaks the identity C = sum(g) + L that Webster's split rests on.
    # Raised to a pedestrian minimum, the greens no longer fit in the cycle and
    # the plan describes a signal that cannot exist; capped at the maximum
    # green, the cycle carries seconds nobody is served in. Either way every
    # capacity, saturation and delay figure below would be computed against a
    # cycle the controller does not actually run. So the cycle follows the
    # greens. When nothing is clamped this is the same cycle to within
    # rounding and is left untouched.
    needed = sum(greens) + lost_time
    pedestrian_overrun = False
    if abs(needed - cycle) > 0.05:
        pedestrian_overrun = needed > MAX_CYCLE_S
        cycle = float(max(MIN_CYCLE_S, needed))

    for a, g in zip(approaches, greens):
        # Critical-lane basis throughout, consistent with y_i.
        lane_cap = a.lane_capacity(g, cycle)
        x = a.critical_lane_flow_pcu_per_hr / lane_cap if lane_cap > 0 else 0.0
        delay, model = approach_delay(cycle, g, a.critical_lane_flow_pcu_per_hr,
                                      lane_cap, model=delay_model)

        phases.append(PhasePlan(
            approach_id=a.approach_id,
            label=a.label,
            green_s=round(g, 1),
            amber_s=AMBER_S,
            all_red_s=ALL_RED_S,
            flow_ratio=round(a.flow_ratio, 4),
            capacity_pcu_per_hr=round(lane_cap * a.lanes, 1),  # reported per approach
            degree_of_saturation=round(x, 3),
            est_delay_s_per_pcu=round(delay, 1),
            delay_model=model,
        ))

    note = "" if feasible else (
        "Demand exceeds junction capacity (Y >= 0.95). No signal timing can "
        "clear this; the fix is geometric or demand-side, not signal retiming."
    )
    if pedestrian_overrun:
        note = (note + " " if note else "") + (
            f"The pedestrian minimum greens alone need a {cycle:.0f}s cycle, "
            f"past the {MAX_CYCLE_S:.0f}s practical maximum. Pedestrian safety "
            f"is kept and the cycle runs long; a staggered crossing or a "
            f"refuge island would shorten it.")
    return SignalPlan(
        junction=junction_name,
        cycle_s=round(cycle, 1),
        lost_time_s=round(lost_time, 1),
        total_flow_ratio=round(Y, 4),
        phases=phases,
        feasible=feasible,
        note=note,
    )


def evaluate_fixed_plan(
    approaches: list[ApproachDemand],
    cycle_s: float,
    green_splits: dict[str, float] | None = None,
    junction_name: str = "Junction (existing fixed timer)",
    delay_model: str = "auto",
) -> SignalPlan:
    """Delay under an existing fixed-time plan, for the before/after baseline.

    With no split supplied we assume equal greens, which is what an untuned
    fixed-timer junction effectively does.
    """
    n = len(approaches)
    lost_time = n * LOST_TIME_PER_PHASE_S
    effective_green_total = max(1.0, cycle_s - lost_time)

    phases: list[PhasePlan] = []
    for a in approaches:
        g = (green_splits or {}).get(a.approach_id, effective_green_total / n)
        lane_cap = a.lane_capacity(g, cycle_s)
        x = a.critical_lane_flow_pcu_per_hr / lane_cap if lane_cap > 0 else 0.0
        delay, model = approach_delay(cycle_s, g, a.critical_lane_flow_pcu_per_hr,
                                      lane_cap, model=delay_model)
        phases.append(PhasePlan(
            approach_id=a.approach_id,
            label=a.label,
            green_s=round(g, 1),
            amber_s=AMBER_S,
            all_red_s=ALL_RED_S,
            flow_ratio=round(a.flow_ratio, 4),
            capacity_pcu_per_hr=round(lane_cap * a.lanes, 1),
            degree_of_saturation=round(x, 3),
            est_delay_s_per_pcu=round(delay, 1),
            delay_model=model,
        ))

    return SignalPlan(
        junction=junction_name,
        cycle_s=round(cycle_s, 1),
        lost_time_s=round(lost_time, 1),
        total_flow_ratio=round(sum(a.flow_ratio for a in approaches), 4),
        phases=phases,
        feasible=True,
        note="Baseline: untuned fixed-time operation with equal green splits.",
    )


def compare(baseline: SignalPlan, optimised: SignalPlan) -> dict:
    """Before/after summary, so the benefit is quoted rather than asserted."""
    b, o = baseline.mean_delay_s_per_pcu, optimised.mean_delay_s_per_pcu
    improvement = ((b - o) / b * 100.0) if b > 0 else 0.0
    return {
        "baseline_cycle_s": baseline.cycle_s,
        "optimised_cycle_s": optimised.cycle_s,
        "baseline_mean_delay_s": round(b, 1),
        "optimised_mean_delay_s": round(o, 1),
        "delay_reduction_pct": round(improvement, 1),
        "feasible": optimised.feasible,
        "note": optimised.note,
    }


# --------------------------------------------------------------------------
# Building demand from measured metrics
# --------------------------------------------------------------------------

def demand_from_metrics(
    df: pd.DataFrame,
    camera_id: str,
    approach_label: str | None = None,
    window: tuple[float, float] | None = None,
) -> ApproachDemand | None:
    """Aggregate a camera's lane metrics into one approach demand.

    The critical lane (busiest, not average) sets the flow ratio, per Webster's
    method — a signal must clear the worst lane, not the mean one.
    """
    lanes = df[(df.camera_id == camera_id) & (df.zone_kind == "lane")]
    if window:
        lanes = lanes[(lanes.window_start_s >= window[0])
                      & (lanes.window_end_s <= window[1])]
    if lanes.empty:
        return None

    per_lane = lanes.groupby("zone_id").agg(
        flow=("flow_pcu_per_hr", "mean"),
        speed=("mean_speed_kmph", "mean"),
        density=("density_pcu_per_km_lane", "mean"),
    )

    cam = cfg.camera_meta(camera_id)
    return ApproachDemand(
        approach_id=camera_id,
        label=approach_label or cam["name"],
        camera_id=camera_id,
        lanes=int(len(per_lane)),
        flow_pcu_per_hr=float(per_lane["flow"].sum()),
        critical_lane_flow_pcu_per_hr=float(per_lane["flow"].max()),
        mean_speed_kmph=float(per_lane["speed"].mean()),
        mean_density_pcu_per_km_lane=float(per_lane["density"].mean()),
    )


def format_plan(plan: SignalPlan) -> str:
    lines = [
        f"{plan.junction}",
        f"  cycle {plan.cycle_s:.0f}s | lost time {plan.lost_time_s:.0f}s | "
        f"Y={plan.total_flow_ratio:.3f} | mean delay "
        f"{plan.mean_delay_s_per_pcu:.1f}s/PCU",
    ]
    for p in plan.phases:
        lines.append(
            f"    {p.label[:34]:<35} green {p.green_s:5.1f}s  "
            f"y={p.flow_ratio:.3f}  x={p.degree_of_saturation:.2f}  "
            f"delay {p.est_delay_s_per_pcu:5.1f}s"
        )
    if plan.note:
        lines.append(f"  ! {plan.note}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Which controller should this junction actually run?
# --------------------------------------------------------------------------

@dataclass
class ControllerAdvice:
    """A recommendation between Webster's and max-pressure, with its reasons."""

    recommended: str                  # "webster" | "max_pressure"
    confidence: str                   # "decisive" | "clear" | "marginal"
    headline: str
    reasons: list[str] = field(default_factory=list)
    against: list[str] = field(default_factory=list)
    split_variability: float = 0.0    # 0 = perfectly steady split
    downstream_observed: bool = False
    oversaturated: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# Below this, the split of demand between approaches is steady enough that a
# plan computed once is still right, and max-pressure's extra switching is
# pure cost. Calibrated against the simulator: at a split coefficient of
# variation near zero, fixed-time wins; the crossover in our runs sits around
# 0.15-0.20.
SPLIT_STEADY_THRESHOLD = 0.15


def approach_split_variability(df, camera_ids: list[str]) -> float:
    """How much the *share* of demand between approaches moves over the clip.

    Deliberately the share and not the level. Both controllers handle a rising
    tide about equally; what separates them is demand moving *between*
    approaches, because that is what invalidates a plan computed once from the
    average. Returns the coefficient of variation of one approach's share, so
    it is dimensionless and unaffected by the uncalibrated metric scale.
    """
    import numpy as np
    if len(camera_ids) < 2:
        return 0.0
    sub = df[df.camera_id.isin(camera_ids)]
    if sub.empty or "queue_length_pcu" not in sub.columns:
        return 0.0
    # Bin to 10 s so a single noisy window cannot dominate.
    b = (sub.window_start_s // 10.0).astype(int)
    per = sub.groupby([b, sub.camera_id]).queue_length_pcu.sum().unstack()
    per = per.dropna(how="any")
    if len(per) < 3:
        return 0.0
    total = per.sum(axis=1)
    total = total[total > 1e-9]
    if len(total) < 3:
        return 0.0
    share = (per.loc[total.index, per.columns[0]] / total)
    m = float(share.mean())
    if m <= 1e-9:
        return 0.0
    return float(share.std() / m)


def recommend_controller(
    df,
    camera_ids: list[str],
    downstream_observed: bool = False,
    demands: "list[ApproachDemand] | None" = None,
) -> ControllerAdvice:
    """Recommend a controller for this junction from what was measured.

    The two controllers are not ranked in the abstract - each is better in a
    regime the other is not, and which regime a junction is in is a measurable
    property of its own traffic. This turns the operator's toggle from a guess
    into a decision backed by the same data everything else on the dashboard
    is built from.

    The ordering of the tests matters. Observability comes first because it is
    not a preference but a fact: without a downstream queue, max-pressure is
    not available at any confidence, and no amount of demand variability
    changes that.
    """
    reasons: list[str] = []
    against: list[str] = []

    cv = approach_split_variability(df, camera_ids)
    oversat = False
    if demands:
        try:
            Y = sum(d.flow_ratio for d in demands)
            oversat = Y >= 0.95
        except Exception:
            oversat = False

    # 1. Availability, not preference.
    if not downstream_observed:
        reasons.append(
            "No camera at this junction observes a receiving link, so the "
            "downstream term max-pressure is defined by cannot be measured. "
            "It is unavailable here, not merely worse.")
        reasons.append(
            "Webster's needs only arrival flow per approach, which is exactly "
            "what these cameras measure.")
        if cv > SPLIT_STEADY_THRESHOLD:
            against.append(
                f"The split of demand between approaches does move "
                f"(variability {cv:.2f}), which is the regime max-pressure is "
                f"for. A camera on each receiving link would unlock it.")
        return ControllerAdvice(
            recommended="webster", confidence="decisive",
            headline="Webster's — max-pressure is not observable here",
            reasons=reasons, against=against,
            split_variability=cv, downstream_observed=False,
            oversaturated=oversat)

    # 2. Both available: choose on the regime.
    if cv > SPLIT_STEADY_THRESHOLD:
        reasons.append(
            f"The split of demand between approaches moves over the clip "
            f"(variability {cv:.2f}, above the {SPLIT_STEADY_THRESHOLD:.2f} "
            f"threshold). A fixed plan computed from the average is wrong on "
            f"both approaches whenever the average stops holding.")
        reasons.append(
            "Max-pressure re-ranks from the queues actually present and needs "
            "no demand forecast.")
        against.append(
            "It switches more often, and every switch costs start-up and "
            "clearance time. The cyclic variant keeps a fixed cycle and lets "
            "pressure set only the splits, which pays exactly what Webster's "
            "pays.")
        conf = "clear" if cv > SPLIT_STEADY_THRESHOLD * 1.6 else "marginal"
        return ControllerAdvice(
            recommended="max_pressure", confidence=conf,
            headline="Max-pressure — demand is moving between approaches",
            reasons=reasons, against=against,
            split_variability=cv, downstream_observed=True,
            oversaturated=oversat)

    reasons.append(
        f"The split of demand between approaches is steady (variability "
        f"{cv:.2f}). A plan computed once from the average stays right, and "
        f"that is Webster's home ground.")
    reasons.append(
        "Max-pressure's extra switching buys better-targeted green, but on "
        "demand that is not moving there is nothing to target — so it pays "
        "the inter-greens without the benefit.")
    if oversat:
        against.append(
            "The junction is at or beyond capacity (Y >= 0.95). Neither "
            "controller can clear demand it does not have the capacity for; "
            "the fix is geometric or demand-side, not a signal timing.")
    return ControllerAdvice(
        recommended="webster", confidence="clear",
        headline="Webster's — demand split is steady",
        reasons=reasons, against=against,
        split_variability=cv, downstream_observed=True,
        oversaturated=oversat)
