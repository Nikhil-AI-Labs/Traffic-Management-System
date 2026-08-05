"""Max-pressure signal control (Varaiya 2013), with an honest observability gate.

Max-pressure is the throughput-optimal decentralised signal controller: at every
control step it serves the phase with the greatest "pressure", using only local
queue measurements and no knowledge of demand, and it stabilises any arrival
rate the junction is physically capable of clearing.

    movement          an approved turn from an incoming link l to an
                      outgoing (receiving) link m, written (l, m)
    phase             a set of movements that may run simultaneously without
                      conflicting
    x_l               queue on link l, in PCU
    s_lm              saturation flow of movement (l, m), PCU/hr
    r_mk              turn proportion of traffic leaving link m toward k

    movement pressure     w_lm = x_l  -  SUM_k r_mk * x_k
    phase pressure        P(p)  = SUM over (l,m) in p of  s_lm * w_lm
    control law           serve argmax_p P(p), subject to minimum green

With a single receiving link and no onward turn split this reduces to the form
usually quoted, w = x_in - x_out.

WHY THE DOWNSTREAM TERM IS THE WHOLE POINT
------------------------------------------
Drop `x_out` and the pressure becomes w = x_in, so the controller serves
whichever approach has the longest queue. That is a real algorithm - greedy
queue control - but it is *not* max-pressure and it is *not* throughput-optimal.

This is not a cautious guess; it is a named counterexample in the source paper.
Varaiya section 3.1 defines exactly that controller, u-dagger, which maximises
instantaneous intersection utilisation, and proves it fails to stabilise demands
that max-pressure handles. His Example 2 needs only two entry links and two exit
links with Bernoulli arrivals of mean (0.5 - delta): u-dagger is unstable for
every delta below 0.0358, on a junction as simple as 2x2. Gregoire et al.
(arXiv:1309.6484, Theorem 1) go further and show that even true back-pressure
loses work conservation under finite queues, and construct a three-node deadlock.

So an implementation that quietly substitutes zero for an unobserved downstream
queue is not a degraded max-pressure. It is a different, provably weaker
controller wearing the name.

A caveat worth stating, because a sharp reviewer will raise it: at a genuinely
*isolated* junction the downstream term is dropped legitimately. Exit links are
sinks, and Gayah & Levin's capacity-aware formulation says so outright - "all
outgoing links are regarded as sink links ... therefore only the incoming links
for isolated intersections are considered". That convention is implemented here
(see the boundary handling in network_sim). But it also means max-pressure at an
isolated junction *is* greedy max-weight, with none of the network property that
makes it worth having. The argument for our data is therefore stronger than
"we cannot see downstream": we have no intersection at all - our two junction
cameras watch different corridors - and no observable turning ratios.

This module therefore refuses to pretend. Every movement carries whether its
receiving-link queue was actually observed, and the result reports the mode it
ran in:

    max-pressure    every movement has an observed downstream queue
    partial         some do - pressures are computed but flagged
    greedy-queue    none do - reported under its own name, not as max-pressure

SCALE INVARIANCE
----------------
Both the movement weight and the phase pressure are homogeneous of degree 1 in
the queue vector (Varaiya eq. 21-22): for any k > 0,

    w(l,m)(kX) = k*x_l - SUM_p r(m,p)*k*x_p = k * w(l,m)(X)
    P(p)(kX)   = SUM c(l,m) * k*w(l,m) * S(l,m) = k * P(p)(X)

so argmax_p P(p)(kX) = argmax_p P(p)(X). The *decision* is exactly invariant to
one common unknown factor - which is precisely our uncalibrated pixels-to-metres
scale. The printed pressure value is not; it scales by k.

Three limits on that, and the third is the one that actually threatens us:

  * NOT invariant to *per-link* scales. If two links are measured by cameras
    with independently-fitted scales, the ranking is driven by the calibration
    difference rather than by traffic. `consistent_scale` records this.
  * NOT invariant to an additive bias unless the bias is identical upstream and
    downstream and the turn ratios sum to one.
  * Our occlusion saturation is exactly such a bias, and it is neither constant
    nor symmetric: detection loss grows with density and so hits the *longest*
    queue hardest - the very term the pressure is differencing. A state-
    dependent negative bias on x_in is the one error mode that can systematically
    flip the argmax. It is a bigger threat to max-pressure here than the missing
    metric scale is.

The right input is therefore `queue_length_pcu` - a count, scale-free by
construction - and not any metric queue length.

Sources: Varaiya, "Max pressure control of a network of signalized
intersections", Transportation Research Part C 36 (2013) 177-195;
Wongpiromsarn et al., "Distributed traffic signal control for maximum network
throughput", ITSC 2012; Tassiulas & Ephremides (1992) for the backpressure
origin.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

# A movement whose receiving link is unobserved contributes an unknown
# downstream term. We never guess it.
UNOBSERVED = None

MODE_FULL = "max-pressure"
MODE_PARTIAL = "partial"
MODE_GREEDY = "greedy-queue"


@dataclass
class Movement:
    """One approved turn, from an incoming link to a receiving link."""

    id: str
    label: str
    from_link: str
    to_link: str | None = None
    queue_in_pcu: float = 0.0
    queue_out_pcu: float | None = UNOBSERVED
    saturation_flow_pcu_per_hr: float = 1800.0

    # This movement's share of its approach. Informational only: it is NOT a
    # term in the weight below. `queue_in_pcu` is already the queue for *this*
    # movement, so multiplying by the share again would count it twice. It is
    # carried so the dashboard can show how the approach splits.
    turn_ratio: float = 1.0

    # The receiving link's queue decomposed by where it goes next:
    # [(r(m,k), x(m,k)), ...]. Present only when the receiving link is itself
    # inside the network and its onward movements are observable. See the
    # `pressure` docstring for what its absence means.
    downstream_split: list[tuple[float, float]] | None = None

    # Provenance, so the dashboard can say where each number came from.
    queue_in_source: str = ""
    queue_out_source: str = ""

    @property
    def downstream_observed(self) -> bool:
        return self.queue_out_pcu is not None

    @property
    def pressure(self) -> float | None:
        """Varaiya's movement weight, or None when the receiving link is unseen.

            w(l,m) = x(l,m) - SUM_k r(m,k) x(m,k)

        The subtracted term is the receiving link's queue *decomposed by where
        it goes next*. Two cases, and which one applies is a property of the
        network, not a modelling preference:

        **Receiving link inside the network.** Its onward movements exist and
        are measurable, so `downstream_split` carries [(r(m,k), x(m,k))] and
        the sum is computed exactly as written. This is the case at every
        junction that discharges into another junction.

        **Receiving link on the network boundary.** Its onward movements leave
        the modelled network entirely, so there is no r(m,k) to weight by;
        Varaiya, and Gayah & Levin explicitly, treat such links as sinks. The
        term is then the measured queue standing on that link, which is what
        `queue_out_pcu` holds. Every exit at a single isolated intersection is
        of this kind - so at the FLUID intersections the sum has exactly one
        term and collapses to `x_in - x_out`.

        The distinction is worth stating because the collapsed form is *not* an
        approximation of the general one: it is the general one, evaluated on a
        network whose exits are boundaries.
        """
        if self.queue_out_pcu is None:
            return None
        if self.downstream_split:
            down = sum(float(r) * float(x) for r, x in self.downstream_split)
        else:
            down = float(self.queue_out_pcu)
        return float(self.queue_in_pcu) - down

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pressure"] = self.pressure
        d["downstream_observed"] = self.downstream_observed
        return d


@dataclass
class Phase:
    """A set of movements that may run together."""

    id: str
    label: str
    movement_ids: list[str] = field(default_factory=list)
    min_green_s: float = 10.0
    max_green_s: float = 90.0


@dataclass
class PhasePressure:
    phase_id: str
    label: str
    pressure: float | None          # None when any movement is unobservable
    movements: list[dict] = field(default_factory=list)
    observed_movements: int = 0
    total_movements: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MaxPressureDecision:
    mode: str                       # max-pressure | partial | greedy-queue
    phases: list[PhasePressure]
    chosen_phase_id: str | None
    chosen_label: str
    chosen_pressure: float | None
    all_non_positive: bool
    consistent_scale: bool
    note: str = ""
    warnings: list[str] = field(default_factory=list)

    # What a *greedy queue* controller would serve on the same data. Kept
    # strictly apart from `chosen_*`, which is the max-pressure answer and is
    # populated only when every downstream queue was actually observed.
    #
    # These have to be separate fields rather than one set of "chosen"
    # fields plus a mode string, because a caller that reads `chosen_label`
    # and renders it gets a confident phase recommendation whether or not the
    # data supports one — and a mode chip elsewhere on the page does not undo
    # that. Making the refusal structural means a caller cannot accidentally
    # present greedy-queue output as max-pressure; it has to ask for it by a
    # name that says what it is.
    greedy_phase_id: str | None = None
    greedy_label: str = "—"
    greedy_pressure: float | None = None

    @property
    def usable(self) -> bool:
        """Is this genuinely max-pressure, or something weaker wearing the name?"""
        return self.mode == MODE_FULL

    def to_dict(self) -> dict:
        d = asdict(self)
        d["usable"] = self.usable
        return d


# --------------------------------------------------------------------------
# The controller
# --------------------------------------------------------------------------

def phase_pressure(phase: Phase, movements: dict[str, Movement],
                   allow_unobserved: bool) -> PhasePressure:
    """Saturation-weighted sum of movement pressures for one phase.

    Weighting by s_lm is not decoration: it converts a queue difference into the
    rate at which serving this movement would actually relieve it, which is what
    makes the argmax throughput-optimal rather than merely queue-greedy.
    """
    mvs = [movements[m] for m in phase.movement_ids if m in movements]
    observed = [m for m in mvs if m.downstream_observed]

    # A phase none of whose movements resolve has no pressure - not a pressure
    # of zero. Scoring it 0.0 and calling that computable would let it win the
    # argmax over every genuinely-measured phase the moment real pressures go
    # negative, which is exactly the spillback regime the controller exists
    # for: it would hold green on a phase we know nothing about, precisely
    # when the network is failing.
    if not mvs:
        return PhasePressure(
            phase_id=phase.id, label=phase.label, pressure=None, movements=[],
            observed_movements=0, total_movements=0,
            note=("none of this phase's movements are present in the "
                  "measurement, so it has no pressure — not a pressure of 0"))

    rows, total, computable = [], 0.0, True
    for m in mvs:
        w = m.pressure
        if w is None:
            if not allow_unobserved:
                computable = False
                w_eff = None
            else:
                # Greedy fallback: the downstream term is dropped, not guessed.
                # Named as such by the caller.
                w_eff = float(m.queue_in_pcu)
        else:
            w_eff = w
        if w_eff is not None:
            total += (m.saturation_flow_pcu_per_hr / 3600.0) * w_eff
        rows.append({**m.to_dict(), "effective_pressure": w_eff})

    note = ""
    if len(observed) < len(mvs):
        missing = [m.label for m in mvs if not m.downstream_observed]
        note = ("downstream queue not observed for: " + ", ".join(missing))

    # Not rounded. The docstring's scale-invariance claim is exact - scale
    # every queue by k and the argmax is unchanged - and rounding to a fixed
    # number of decimals breaks exactly that, because two pressures that
    # differ far below the rounding step tie at one scale and separate at
    # another. Rounding is a presentation concern and belongs in the caller.
    return PhasePressure(
        phase_id=phase.id, label=phase.label,
        pressure=(total if computable else None),
        movements=rows,
        observed_movements=len(observed), total_movements=len(mvs),
        note=note,
    )


def decide(
    movements: list[Movement],
    phases: list[Phase],
    allow_greedy_fallback: bool = True,
    consistent_scale: bool = True,
) -> MaxPressureDecision:
    """Pick the phase to serve, and say honestly what was actually computed."""
    mv = {m.id: m for m in movements}
    if not phases or not mv:
        return MaxPressureDecision(
            mode=MODE_GREEDY, phases=[], chosen_phase_id=None, chosen_label="—",
            chosen_pressure=None, all_non_positive=True,
            consistent_scale=consistent_scale,
            note="No movements or phases configured.")

    n_obs = sum(1 for m in movements if m.downstream_observed)
    if n_obs == len(movements):
        mode = MODE_FULL
    elif n_obs == 0:
        mode = MODE_GREEDY
    else:
        mode = MODE_PARTIAL

    allow_unobserved = allow_greedy_fallback and mode != MODE_FULL
    scored = [phase_pressure(p, mv, allow_unobserved) for p in phases]

    warnings: list[str] = []
    if mode == MODE_GREEDY:
        warnings.append(
            "No receiving-link queue is observed anywhere at this junction, so "
            "the downstream term of every movement is missing. What is computed "
            "below is greedy queue control — serve the longest queue — which is "
            "a different controller from max-pressure and is not "
            "throughput-optimal. It cannot see a blocked downstream link, which "
            "is the failure max-pressure exists to prevent.")
    elif mode == MODE_PARTIAL:
        warnings.append(
            "Some movements have no observed receiving-link queue. Those fall "
            "back to their incoming queue alone, so the ranking mixes "
            "max-pressure and greedy-queue terms and is not throughput-optimal.")
    if not consistent_scale:
        warnings.append(
            "Queues come from cameras with different, uncalibrated pixel scales. "
            "Max-pressure's phase choice is invariant to one common scale factor "
            "but not to different factors per link, so the ranking below may be "
            "driven by calibration differences rather than by traffic.")

    usable = [s for s in scored if s.pressure is not None]
    if not usable:
        return MaxPressureDecision(
            mode=mode, phases=scored, chosen_phase_id=None, chosen_label="—",
            chosen_pressure=None, all_non_positive=True,
            consistent_scale=consistent_scale, warnings=warnings,
            note="No phase pressure could be computed from the available data.")

    best = max(usable, key=lambda s: s.pressure)
    # Strictly negative, and at least one queue actually present. An empty
    # junction scores every phase at exactly 0.0, which is not spillback — it
    # is nothing happening — and the note below asserts network-wide gridlock,
    # which would be an alarming thing to tell an operator at 4 a.m. on a
    # deserted road.
    any_traffic = any(m.queue_in_pcu > 1e-9 for m in movements)
    all_non_positive = any_traffic and all(s.pressure <= 0 for s in usable)

    note = ""
    if all_non_positive:
        # Varaiya's controller still serves the argmax here; the point is that
        # every receiving link is at least as full as the approach feeding it,
        # so no phase relieves the network and the fix is not a signal timing.
        note = ("Every phase has non-positive pressure: each receiving link is "
                "at least as congested as the approach feeding it. Serving any "
                "phase discharges into a queue that cannot absorb it. "
                "Max-pressure still serves the least-bad phase, but this is the "
                "signature of network-wide spillback, and signal timing has no "
                "leverage on it.")

    # The argmax is only a *max-pressure* answer when every downstream queue
    # behind it was observed. Below that, the same arithmetic is greedy queue
    # control, and it is returned under that name instead — see the note on
    # the dataclass for why this is enforced structurally rather than left to
    # each caller to remember.
    # What greedy queue control would serve: the saturation-weighted longest
    # *incoming* queue, with no downstream term at all. Computed from the
    # movements directly rather than reused from `best`, because in MODE_FULL
    # `best` is the max-pressure answer and the two generally differ - that
    # difference is the entire point of the comparison.
    by_id = {p.id: p for p in phases}
    g_best_id, g_best_label, g_best = None, "—", None
    for p in scored:
        mvs = [mv[i] for i in by_id[p.phase_id].movement_ids if i in mv]
        if not mvs:
            continue
        score = sum((m.saturation_flow_pcu_per_hr / 3600.0) * m.queue_in_pcu
                    for m in mvs)
        if g_best is None or score > g_best:
            g_best_id, g_best_label, g_best = p.phase_id, p.label, score

    if mode == MODE_FULL:
        return MaxPressureDecision(
            mode=mode, phases=scored,
            chosen_phase_id=best.phase_id, chosen_label=best.label,
            chosen_pressure=best.pressure, all_non_positive=all_non_positive,
            consistent_scale=consistent_scale, note=note, warnings=warnings,
            greedy_phase_id=g_best_id, greedy_label=g_best_label,
            greedy_pressure=g_best,
        )

    return MaxPressureDecision(
        mode=mode, phases=scored,
        chosen_phase_id=None, chosen_label="—", chosen_pressure=None,
        all_non_positive=all_non_positive,
        consistent_scale=consistent_scale, warnings=warnings,
        note=(note + (" " if note else "") +
              "No max-pressure phase choice is reported: the downstream term "
              "is missing, so the ranking above is greedy queue control. What "
              "that weaker controller would serve is in `greedy_*`, named so "
              "it cannot be mistaken for a max-pressure recommendation."),
        greedy_phase_id=g_best_id, greedy_label=g_best_label,
        greedy_pressure=g_best,
    )


# --------------------------------------------------------------------------
# Running it over time
# --------------------------------------------------------------------------

@dataclass
class ControllerState:
    """Minimum-green bookkeeping for a controller stepped over time.

    Pure max-pressure re-decides every instant, which in practice produces a
    signal that flickers and that no pedestrian could cross. Every deployable
    variant holds a phase for a minimum green; that is the single most important
    practical departure from the paper.
    """

    current_phase_id: str | None = None
    time_in_phase_s: float = 0.0
    switches: int = 0
    # Hysteresis. Textbook max-pressure re-decides in a slotted model where
    # switching is free; charge it the real start-up and clearance time and an
    # unmodified controller thrashes, burning more capacity on inter-greens
    # than it recovers by adapting. A challenger phase must therefore beat the
    # incumbent by this margin, not merely tie it. Every deployable variant has
    # some version of this.
    switch_margin: float = 0.0

    def step(self, decision: MaxPressureDecision, dt_s: float,
             phases: list[Phase]) -> tuple[str | None, bool]:
        """Advance by dt. Returns (phase to serve, whether it switched)."""
        by_id = {p.id: p for p in phases}
        want = decision.chosen_phase_id

        if self.current_phase_id is None:
            # No phase yet. If the decision cannot name one - which is now the
            # normal result whenever the downstream term is missing - there is
            # nothing to latch onto, and claiming a switch to None would report
            # a permanent all-red as a switch on every single tick while the
            # switch counter stayed at zero.
            if want is None:
                return None, False
            self.current_phase_id = want
            # 0.0, matching the switch path below, so the first phase of a run
            # is held for the same minimum green as every later one rather
            # than min_green - dt.
            self.time_in_phase_s = 0.0
            self.switches += 1
            return self.current_phase_id, True

        self.time_in_phase_s += dt_s
        cur = by_id.get(self.current_phase_id)
        min_green = cur.min_green_s if cur else 10.0
        max_green = cur.max_green_s if cur else 90.0

        held = self.time_in_phase_s < min_green
        starved = self.time_in_phase_s >= max_green

        if self.switch_margin > 0 and want and want != self.current_phase_id:
            pressures = {p.phase_id: p.pressure for p in decision.phases}
            here = pressures.get(self.current_phase_id)
            there = pressures.get(want)
            if here is not None and there is not None:
                if there - here < self.switch_margin:
                    want = self.current_phase_id

        if (want and want != self.current_phase_id and not held) or starved:
            target = want if want and want != self.current_phase_id else None
            if starved and target is None:
                # Force a rotation so a saturated phase cannot hold green
                # forever and starve everyone else.
                #
                # Rotate to the strongest *challenger*, not simply to the next
                # phase in list order. List order is an arbitrary artefact of
                # how the phases were configured, so rotating by it hands green
                # to whichever phase happens to be declared next even when a
                # different one has far more queue waiting — and at a junction
                # with three or more phases that repeatedly favours the same
                # neighbour. Ranking the challengers keeps the forced rotation
                # consistent with what the controller is for.
                rivals = [s for s in decision.phases
                          if s.phase_id != self.current_phase_id
                          and s.pressure is not None]
                if rivals:
                    target = max(rivals, key=lambda s: s.pressure).phase_id
                else:
                    # No pressures to rank on (all unobserved): fall back to
                    # positional rotation purely to guarantee liveness.
                    ids = [p.id for p in phases]
                    if len(ids) > 1 and self.current_phase_id in ids:
                        i = ids.index(self.current_phase_id)
                        target = ids[(i + 1) % len(ids)]
            if target:
                self.current_phase_id = target
                self.time_in_phase_s = 0.0
                self.switches += 1
                return self.current_phase_id, True

        return self.current_phase_id, False


# --------------------------------------------------------------------------
# Building movements from what our pipeline measures
# --------------------------------------------------------------------------

def movements_from_zones(
    approaches: list[dict],
    saturation_flow_pcu_per_hr: float = 1800.0,
) -> list[Movement]:
    """Turn measured approach state into movements.

    `approaches` is a list of dicts with, at minimum:
        id, label, queue_in_pcu, and optionally queue_out_pcu / to_link.

    Anything without a `queue_out_pcu` stays unobserved rather than being
    defaulted to zero — see the module docstring for why that distinction is
    load-bearing.
    """
    out = []
    for a in approaches:
        out.append(Movement(
            id=str(a["id"]),
            label=str(a.get("label", a["id"])),
            from_link=str(a.get("from_link", a["id"])),
            to_link=a.get("to_link"),
            queue_in_pcu=float(a.get("queue_in_pcu", 0.0) or 0.0),
            queue_out_pcu=(float(a["queue_out_pcu"])
                           if a.get("queue_out_pcu") is not None else None),
            saturation_flow_pcu_per_hr=float(
                a.get("saturation_flow_pcu_per_hr", saturation_flow_pcu_per_hr)),
            turn_ratio=float(a.get("turn_ratio", 1.0)),
            queue_in_source=str(a.get("queue_in_source", "")),
            queue_out_source=str(a.get("queue_out_source", "")),
        ))
    return out


def one_movement_per_phase(movements: list[Movement],
                           min_green_s: float = 10.0,
                           max_green_s: float = 90.0) -> list[Phase]:
    """The simplest phase structure: each approach gets its own phase.

    Correct for a junction where every approach conflicts with every other,
    which is the conservative assumption when the approved movement set is not
    known. A real junction with a proper phase table should supply one.
    """
    return [Phase(id=f"phase_{m.id}", label=m.label, movement_ids=[m.id],
                  min_green_s=min_green_s, max_green_s=max_green_s)
            for m in movements]
