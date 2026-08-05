"""A small point-queue network simulator, to run Webster against max-pressure.

Our cameras cannot see a receiving link, so max-pressure cannot be evaluated on
the Surat footage directly - the downstream queue it needs is simply not in the
data. Rather than fake it, this builds an explicit network model and states
plainly which parts are measured and which are constructed:

    MEASURED from the Surat clips   arrival flow per approach (PCU/hr),
                                    vehicle mix and PCU conversion,
                                    saturation flow assumption
    CONSTRUCTED for the model       the junction topology, the link between the
                                    two junctions, its storage capacity, and the
                                    turn split

That separation is the whole point. A simulator lets us demonstrate the
controller honestly; it does not let us claim a measured result.

WHY TWO JUNCTIONS
-----------------
On an isolated junction max-pressure has nothing to be right about: with no
receiving link to fill, its downstream term is constant and it collapses to
serving the longest queue. The smallest network where the downstream term
carries information is two junctions in series - the first discharges into a
link of finite storage that the second drains. When the second is slow, that
link fills, and a controller that cannot see it keeps pushing traffic into a
space that does not exist. That is spillback, and preventing it is the entire
argument for max-pressure.

THE MODEL
---------
A point-queue (store-and-forward) model. Each link holds a queue of PCU with a
finite storage capacity. In one step of dt seconds:

    arrivals        q_l  <-  q_l + a_l * dt              (external inflow)
    discharge       f_lm = min( s_lm * dt,               service capacity
                                q_l,                     what is there
                                cap_m - q_m )            receiving space
    transfer        q_l -= f_lm ;  q_m += f_lm
    exit            sinks drain at their own rate

The `cap_m - q_m` term is the receiving-space constraint. Without it there is no
spillback and no reason for max-pressure to exist, so it is not optional.

Delay is accumulated as vehicle-seconds of queueing and converted with Little's
law: mean delay = total queue-time / total vehicles discharged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import maxpressure as MP


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------

@dataclass
class Link:
    id: str
    label: str
    storage_pcu: float = 60.0        # jam storage; ~7 PCU per 100 m per lane
    arrival_pcu_per_hr: float = 0.0  # external demand entering here
    is_sink: bool = False
    sink_rate_pcu_per_hr: float = 4000.0
    queue: float = 0.0
    # Multiplier on the arrival rate as a function of time. This is what
    # separates the two controllers: a fixed-time plan is designed once, for the
    # average, and cannot notice that the average stopped being true.
    arrival_profile: "Callable[[float], float] | None" = None
    sink_profile: "Callable[[float], float] | None" = None

    # accounting
    entered: float = 0.0
    exited: float = 0.0
    queue_seconds: float = 0.0
    blocked_seconds: float = 0.0
    peak_queue: float = 0.0

    @property
    def space(self) -> float:
        return max(0.0, self.storage_pcu - self.queue)

    @property
    def full(self) -> bool:
        return self.queue >= self.storage_pcu - 1e-9


@dataclass
class SimMovement:
    id: str
    label: str
    from_link: str
    to_link: str
    saturation_flow_pcu_per_hr: float = 1800.0
    served_pcu: float = 0.0
    # Multiplier on discharge capacity over time. This is how a *downstream*
    # blockage is modelled: an obstruction beyond the stop line does not
    # reduce demand, it reduces the rate at which green can clear the queue,
    # so the link feeding it backs up. Throttling the boundary sink instead
    # would be invisible - a sink is an accounting device that by convention
    # neither blocks nor is seen by the controller.
    capacity_profile: "Callable[[float], float] | None" = None


@dataclass
class Junction:
    id: str
    label: str
    phases: list[MP.Phase]
    movement_ids: list[str]
    lost_time_per_phase_s: float = 4.0


@dataclass
class SimResult:
    controller: str
    seconds: float
    throughput_pcu: float
    mean_delay_s_per_pcu: float
    mean_total_queue_pcu: float
    peak_total_queue_pcu: float
    spillback_seconds: float
    switches: int
    per_link: dict = field(default_factory=dict)
    served_by_phase: dict = field(default_factory=dict)
    history: list = field(default_factory=list)

    def to_row(self) -> dict:
        return {
            "Controller": self.controller,
            "Throughput (PCU)": round(self.throughput_pcu, 1),
            "Mean delay (s/PCU)": round(self.mean_delay_s_per_pcu, 1),
            "Mean queue (PCU)": round(self.mean_total_queue_pcu, 1),
            "Peak queue (PCU)": round(self.peak_total_queue_pcu, 1),
            "Spillback (s)": round(self.spillback_seconds, 1),
            "Phase switches": self.switches,
        }


class Network:
    """Links, movements and junctions, stepped forward in time."""

    def __init__(self, links: list[Link], movements: list[SimMovement],
                 junctions: list[Junction]):
        self.links = {l.id: l for l in links}
        self.movements = {m.id: m for m in movements}
        self.junctions = junctions

    def reset(self) -> None:
        for l in self.links.values():
            l.queue = 0.0
            l.entered = l.exited = l.queue_seconds = l.blocked_seconds = 0.0
            l.peak_queue = 0.0
        for m in self.movements.values():
            m.served_pcu = 0.0

    # -- one step ---------------------------------------------------------

    def _arrive(self, dt: float, t: float = 0.0) -> None:
        for l in self.links.values():
            if l.arrival_pcu_per_hr <= 0:
                continue
            rate = l.arrival_pcu_per_hr
            if l.arrival_profile is not None:
                rate *= max(0.0, float(l.arrival_profile(t)))
            want = rate / 3600.0 * dt
            took = min(want, l.space)
            l.queue += took
            l.entered += took
            if took < want - 1e-9:
                # Demand that could not even enter the network. Counted so an
                # apparently-good throughput cannot hide traffic being turned
                # away at the boundary.
                l.blocked_seconds += dt

    def _serve(self, movement_ids: list[str], dt: float,
               t: float = 0.0) -> None:
        for mid in movement_ids:
            m = self.movements.get(mid)
            if m is None:
                continue
            src, dst = self.links[m.from_link], self.links[m.to_link]
            # A boundary exit does not push back. `_receiving_queue` already
            # reports a sink's queue as zero to the controller, per Varaiya's
            # convention that an exit link is an accounting device rather than
            # a road; letting the same link's storage throttle discharge here
            # would model it as a road after all, and the two halves would
            # disagree — the controller told the exit is empty while the
            # physics refuses to discharge into it.
            capacity = src.queue if dst.is_sink else min(src.queue, dst.space)
            sat = m.saturation_flow_pcu_per_hr
            if m.capacity_profile is not None:
                sat *= max(0.0, float(m.capacity_profile(t)))
            f = min(sat / 3600.0 * dt, capacity)
            if f <= 0:
                continue
            src.queue -= f
            src.exited += f
            dst.queue += f
            dst.entered += f
            m.served_pcu += f

    def _drain_sinks(self, dt: float, t: float = 0.0) -> float:
        out = 0.0
        for l in self.links.values():
            if not l.is_sink:
                continue
            rate = l.sink_rate_pcu_per_hr
            if l.sink_profile is not None:
                rate *= max(0.0, float(l.sink_profile(t)))
            f = min(rate / 3600.0 * dt, l.queue)
            l.queue -= f
            l.exited += f
            out += f
        return out

    def _account(self, dt: float) -> tuple[float, float]:
        """Queue-seconds and spillback, counted on roads only.

        Sink links are excluded. Whatever sits in one has already left the
        modelled network, so counting its queue-seconds bills the controller
        for waiting that happens on a stretch of road the simulation does not
        represent — and because everything served eventually lands in a sink,
        that phantom term grows with throughput and ends up dominating the
        mean delay it is supposed to measure.
        """
        total_q = 0.0
        spill = 0.0
        for l in self.links.values():
            if l.is_sink:
                continue
            l.queue_seconds += l.queue * dt
            l.peak_queue = max(l.peak_queue, l.queue)
            total_q += l.queue
            if l.full:
                spill += dt
        return total_q, spill

    # -- run --------------------------------------------------------------

    def run(self, controller, seconds: float = 900.0, dt: float = 1.0,
            label: str = "") -> SimResult:
        self.reset()
        controller.reset()

        t = 0.0
        throughput = 0.0
        q_sum = 0.0
        spill_total = 0.0
        n_steps = 0
        history: list[dict] = []
        served_by_phase: dict[str, float] = {}

        while t < seconds:
            self._arrive(dt, t)

            for j in self.junctions:
                active = controller.active_phase(j, self, t, dt)
                if active:
                    ph = next((p for p in j.phases if p.id == active), None)
                    if ph:
                        before = sum(self.movements[m].served_pcu
                                     for m in ph.movement_ids
                                     if m in self.movements)
                        self._serve(ph.movement_ids, dt, t)
                        after = sum(self.movements[m].served_pcu
                                    for m in ph.movement_ids
                                    if m in self.movements)
                        served_by_phase[ph.id] = (
                            served_by_phase.get(ph.id, 0.0) + (after - before))

            throughput += self._drain_sinks(dt, t)
            total_q, spill = self._account(dt)
            q_sum += total_q
            spill_total += spill
            n_steps += 1

            if n_steps % max(1, int(5 / dt)) == 0:
                history.append({
                    "t": round(t, 1),
                    "total_queue": round(total_q, 2),
                    "throughput": round(throughput, 2),
                    **{f"q_{k}": round(v.queue, 2) for k, v in self.links.items()},
                })
            t += dt

        queue_seconds = sum(l.queue_seconds for l in self.links.values())
        # Little's law, over every vehicle whose waiting is in the numerator.
        #
        # Dividing by throughput alone would put the waiting of vehicles still
        # queued at the end into the numerator while leaving them out of the
        # denominator. That is not "conservative" — it is a bias whose sign
        # depends on which controller happens to finish with the longer queue,
        # which is exactly the quantity under test. Counting them on both
        # sides makes the metric indifferent to how the run happens to end.
        leftover = sum(l.queue for l in self.links.values() if not l.is_sink)
        served_or_waiting = throughput + leftover
        mean_delay = (queue_seconds / served_or_waiting
                      if served_or_waiting > 1e-6 else float("inf"))

        return SimResult(
            controller=label or controller.name,
            seconds=seconds,
            throughput_pcu=throughput,
            mean_delay_s_per_pcu=mean_delay,
            mean_total_queue_pcu=q_sum / max(1, n_steps),
            peak_total_queue_pcu=max((l.peak_queue for l in self.links.values()),
                                     default=0.0),
            spillback_seconds=spill_total,
            switches=getattr(controller, "switches", 0),
            per_link={k: {"entered": round(v.entered, 1),
                          "exited": round(v.exited, 1),
                          "peak_queue": round(v.peak_queue, 1),
                          "final_queue": round(v.queue, 1)}
                      for k, v in self.links.items()},
            served_by_phase={k: round(v, 1) for k, v in served_by_phase.items()},
            history=history,
        )


# --------------------------------------------------------------------------
# Controllers
# --------------------------------------------------------------------------

class FixedTimeController:
    """A fixed-time plan: cycle the phases with preset green splits.

    This is what a Webster plan is once it is installed — the splits come from
    measured demand, but they then run open-loop and cannot respond to a
    receiving link filling up.
    """

    name = "Fixed-time (Webster splits)"

    def __init__(self, greens: dict[str, dict[str, float]], cycle_s: float,
                 lost_time_per_phase_s: float = 4.0):
        self.greens = greens                # junction_id -> phase_id -> green s
        self.cycle_s = cycle_s
        self.lost = lost_time_per_phase_s
        self.switches = 0
        self._last: dict[str, str | None] = {}

    def reset(self) -> None:
        self.switches = 0
        self._last = {}

    def active_phase(self, junction: Junction, net: Network, t: float,
                     dt: float) -> str | None:
        greens = self.greens.get(junction.id, {})
        order = [p.id for p in junction.phases if p.id in greens]
        if not order:
            return None
        # Walk the cycle: each phase gets its green, then its lost time.
        # Walk the cycle as green, inter-green, green, inter-green, ... The
        # inter-green has to be stepped over explicitly. Folding it into the
        # next phase's start offset (`acc += g + lost`, testing only
        # `pos < acc + g`) looks equivalent but is not: the loop reaches phase
        # i+1 for any pos past phase i's green, so phase i+1's window silently
        # absorbs the inter-green ahead of it and the plan is served during
        # time it should be all-red. The cycle then pays one lost time in
        # total rather than one per phase — inflating green utilisation well
        # above what the plan was designed for, and flattering whichever
        # controller is being measured against the other.
        pos = t % self.cycle_s
        acc = 0.0
        chosen = None
        for pid in order:
            g = greens[pid]
            if pos < acc + g:
                chosen = pid
                break
            acc += g
            if pos < acc + self.lost:
                chosen = None          # inter-green: serve nothing
                break
            acc += self.lost
        # Count a change of *served phase*, ignoring the all-red between them.
        # Comparing against the immediately previous value would never count
        # anything now that the cycle walk returns None during each
        # inter-green: every transition is phase -> None -> phase, and both
        # legs fail a "both ends non-None" test.
        if chosen is not None:
            prev = self._last.get(junction.id)
            if prev is not None and chosen != prev:
                self.switches += 1
            self._last[junction.id] = chosen
        return chosen


def _receiving_queue(net: "Network", link_id: str) -> float:
    """Downstream queue for pressure, with the boundary convention applied.

    A movement that discharges off the edge of the modelled network has no real
    receiving queue - the "exit" is an accounting device, not a road. Varaiya
    treats exit links as having zero queue for exactly this reason, and the
    convention is load-bearing: leave it out and a sink that accumulates its own
    backlog produces negative pressure on the movements feeding it, so the
    controller stops serving the very traffic that is trying to leave.
    """
    link = net.links[link_id]
    return 0.0 if link.is_sink else link.queue


class CyclicMaxPressureController:
    """Max-pressure used to set the green splits, inside a fixed cycle.

    Free-running max-pressure re-decides continuously, and once it is charged
    the real start-up and clearance time of a signal it thrashes: at high demand
    it switched every ~15 s in this model and lost 27% of capacity to
    inter-greens, against 5% for a 150 s fixed-time cycle. Its throughput
    collapsed below the plan it was supposed to beat. That is not a quirk of
    this implementation — it is the gap between Varaiya's slotted model, where
    switching is free, and a junction where it is not.

    The deployable form keeps the cycle. Phases are served in a fixed order, so
    the lost time per cycle is identical to the fixed-time plan and the
    comparison is like-for-like; what max-pressure supplies is the *split*,
    recomputed at the top of every cycle from the pressures measured then:

        g_i = max(g_min, (C - L) * max(0, P_i) / SUM_j max(0, P_j))

    So it adapts once per cycle instead of continuously, pays exactly what the
    fixed plan pays, and keeps a cycle structure a pedestrian phase can live in.
    """

    name = "Max-pressure (cyclic splits)"

    def __init__(self, cycle_s: float, min_green_s: float = 8.0,
                 lost_time_per_phase_s: float = 4.0):
        self.cycle_s = cycle_s
        self.min_green = min_green_s
        self.lost = lost_time_per_phase_s
        self.switches = 0
        self.plans: dict[str, dict[str, float]] = {}
        self.decisions: dict[str, MP.MaxPressureDecision] = {}
        self._cycle_index: dict[str, int] = {}
        self._last: dict[str, str | None] = {}

    def reset(self) -> None:
        self.switches = 0
        self.plans = {}
        self.decisions = {}
        self._cycle_index = {}
        self._last = {}

    def _replan(self, junction: Junction, net: Network) -> dict[str, float]:
        movements = []
        for mid in junction.movement_ids:
            m = net.movements.get(mid)
            if m is None:
                continue
            movements.append(MP.Movement(
                id=m.id, label=m.label, from_link=m.from_link, to_link=m.to_link,
                queue_in_pcu=net.links[m.from_link].queue,
                queue_out_pcu=_receiving_queue(net, m.to_link),
                saturation_flow_pcu_per_hr=m.saturation_flow_pcu_per_hr,
                queue_in_source="simulated", queue_out_source="simulated"))
        phases = [MP.Phase(id=p.id, label=p.label, movement_ids=p.movement_ids,
                           min_green_s=self.min_green)
                  for p in junction.phases]
        decision = MP.decide(movements, phases)
        self.decisions[junction.id] = decision

        eff = max(1.0, self.cycle_s - len(phases) * self.lost)
        pos = {s.phase_id: max(0.0, s.pressure or 0.0) for s in decision.phases}
        total = sum(pos.values())
        if total <= 1e-9:
            # Every phase non-positive: nothing to relieve, so fall back to an
            # even split rather than starving an approach on a coin toss.
            return {pid: eff / len(phases) for pid in pos}

        raw = {pid: eff * p / total for pid, p in pos.items()}
        # Apply the minimum green, then rescale the rest so the cycle still fits.
        floored = {pid: max(self.min_green, g) for pid, g in raw.items()}
        over = sum(floored.values()) - eff
        if over > 1e-9:
            slack = {pid: max(0.0, floored[pid] - self.min_green) for pid in floored}
            s_tot = sum(slack.values())
            if s_tot > 1e-9:
                for pid in floored:
                    floored[pid] -= over * slack[pid] / s_tot
        return floored

    def active_phase(self, junction: Junction, net: Network, t: float,
                     dt: float) -> str | None:
        idx = int(t // self.cycle_s)
        if self._cycle_index.get(junction.id) != idx:
            self._cycle_index[junction.id] = idx
            self.plans[junction.id] = self._replan(junction, net)

        greens = self.plans.get(junction.id, {})
        order = [p.id for p in junction.phases if p.id in greens]
        if not order:
            return None

        # Walk the cycle as green, inter-green, green, inter-green, ... The
        # inter-green has to be stepped over explicitly. Folding it into the
        # next phase's start offset (`acc += g + lost`, testing only
        # `pos < acc + g`) looks equivalent but is not: the loop reaches phase
        # i+1 for any pos past phase i's green, so phase i+1's window silently
        # absorbs the inter-green ahead of it and the plan is served during
        # time it should be all-red. The cycle then pays one lost time in
        # total rather than one per phase — inflating green utilisation well
        # above what the plan was designed for, and flattering whichever
        # controller is being measured against the other.
        pos = t % self.cycle_s
        acc = 0.0
        chosen = None
        for pid in order:
            g = greens[pid]
            if pos < acc + g:
                chosen = pid
                break
            acc += g
            if pos < acc + self.lost:
                chosen = None          # inter-green: serve nothing
                break
            acc += self.lost
        # Count a change of *served phase*, ignoring the all-red between them.
        # Comparing against the immediately previous value would never count
        # anything now that the cycle walk returns None during each
        # inter-green: every transition is phase -> None -> phase, and both
        # legs fail a "both ends non-None" test.
        if chosen is not None:
            prev = self._last.get(junction.id)
            if prev is not None and chosen != prev:
                self.switches += 1
            self._last[junction.id] = chosen
        return chosen


class MaxPressureController:
    """Max-pressure with minimum green, using the real receiving-link queues.

    In simulation every downstream queue is observable by construction, so this
    runs in true max-pressure mode. That is exactly the gap the Surat cameras
    have, and why this comparison lives in a simulator rather than on the
    footage.
    """

    name = "Max-pressure (free-running)"

    def __init__(self, min_green_s: float = 12.0, max_green_s: float = 90.0,
                 lost_time_per_phase_s: float = 4.0,
                 switch_margin: float = 0.25):
        self.min_green = min_green_s
        self.max_green = max_green_s
        self.lost = lost_time_per_phase_s
        self.switch_margin = switch_margin
        self.state: dict[str, MP.ControllerState] = {}
        self.switches = 0
        self.decisions: dict[str, MP.MaxPressureDecision] = {}
        # Every phase change costs the same start-up and clearance time the
        # fixed-time plan pays. Without this the comparison is rigged: an
        # adaptive controller that switches freely at zero cost would win on
        # arithmetic that no real signal enjoys.
        self._clearing: dict[str, float] = {}
        self._started: set[str] = set()

    def reset(self) -> None:
        self.state = {}
        self.switches = 0
        self.decisions = {}
        self._clearing = {}
        self._started = set()

    def active_phase(self, junction: Junction, net: Network, t: float,
                     dt: float) -> str | None:
        movements = []
        for mid in junction.movement_ids:
            m = net.movements.get(mid)
            if m is None:
                continue
            movements.append(MP.Movement(
                id=m.id, label=m.label,
                from_link=m.from_link, to_link=m.to_link,
                queue_in_pcu=net.links[m.from_link].queue,
                queue_out_pcu=_receiving_queue(net, m.to_link),
                saturation_flow_pcu_per_hr=m.saturation_flow_pcu_per_hr,
                queue_in_source="simulated", queue_out_source="simulated",
            ))
        phases = [MP.Phase(id=p.id, label=p.label,
                           movement_ids=p.movement_ids,
                           min_green_s=self.min_green,
                           max_green_s=self.max_green)
                  for p in junction.phases]

        decision = MP.decide(movements, phases)
        self.decisions[junction.id] = decision

        # Serve nothing while clearing, exactly as the fixed-time plan does
        # during its inter-green.
        clearing = self._clearing.get(junction.id, 0.0)
        if clearing > 0:
            self._clearing[junction.id] = clearing - dt
            return None

        st = self.state.setdefault(
            junction.id, MP.ControllerState(switch_margin=self.switch_margin))
        phase_id, switched = st.step(decision, dt, phases)
        if switched:
            self.switches += 1
            # The very first phase at THIS junction is not a change from
            # anything, so it is not charged a clearance. The test has to be
            # per-junction: a controller-wide counter makes every junction
            # after the first inherit a non-zero count and start its run
            # blacked out for a full inter-green it never earned.
            first_here = junction.id not in self._started
            self._started.add(junction.id)
            if not first_here:
                # This step already serves nothing, so it is the first second
                # of the clearance rather than a free extra one on top of it.
                # Charging the full `lost` here as well would make every
                # max-pressure switch cost `lost + dt`, quietly handicapping
                # the controller being measured against the fixed plan.
                self._clearing[junction.id] = max(0.0, self.lost - dt)
                return None
        return phase_id


# --------------------------------------------------------------------------
# The demonstration network
# --------------------------------------------------------------------------

def corridor(
    main_arrival_pcu_per_hr: float = 700.0,
    cross_arrival_pcu_per_hr: float = 300.0,
    downstream_exit_pcu_per_hr: float = 2400.0,
    link_storage_pcu: float = 12.0,
    saturation_flow_pcu_per_hr: float = 1800.0,
) -> tuple[Network, list[Junction]]:
    """Two signalised junctions in series, sharing one short link.

    J1 takes a main-road arrival and a cross-street arrival and discharges both
    into `mid`, a short link with limited storage. J2 drains `mid` onto the exit,
    and also serves its own cross-street. When the exit is slow, `mid` fills,
    and J1 must stop feeding it — which requires seeing it.

    `downstream_exit_pcu_per_hr` is the throttle: lower it and the mid link
    becomes the binding constraint, which is the regime max-pressure is for.
    """
    links = [
        Link("main_in", "Main road approach",
             storage_pcu=120.0, arrival_pcu_per_hr=main_arrival_pcu_per_hr),
        Link("cross1_in", "Cross street (J1)",
             storage_pcu=80.0, arrival_pcu_per_hr=cross_arrival_pcu_per_hr),
        Link("mid", "Link between the junctions", storage_pcu=link_storage_pcu),
        Link("cross2_in", "Cross street (J2)",
             storage_pcu=80.0, arrival_pcu_per_hr=cross_arrival_pcu_per_hr * 0.6),
        Link("exit", "Exit", storage_pcu=400.0, is_sink=True,
             sink_rate_pcu_per_hr=downstream_exit_pcu_per_hr),
    ]
    movements = [
        SimMovement("m_main", "Main → mid", "main_in", "mid",
                    saturation_flow_pcu_per_hr),
        SimMovement("m_cross1", "Cross (J1) → mid", "cross1_in", "mid",
                    saturation_flow_pcu_per_hr),
        SimMovement("m_mid", "Mid → exit", "mid", "exit",
                    saturation_flow_pcu_per_hr),
        SimMovement("m_cross2", "Cross (J2) → exit", "cross2_in", "exit",
                    saturation_flow_pcu_per_hr),
    ]
    j1 = Junction("J1", "Junction 1", movement_ids=["m_main", "m_cross1"],
                  phases=[MP.Phase("J1_main", "Main road", ["m_main"]),
                          MP.Phase("J1_cross", "Cross street", ["m_cross1"])])
    j2 = Junction("J2", "Junction 2", movement_ids=["m_mid", "m_cross2"],
                  phases=[MP.Phase("J2_main", "From junction 1", ["m_mid"]),
                          MP.Phase("J2_cross", "Cross street", ["m_cross2"])])
    return Network(links, movements, [j1, j2]), [j1, j2]


def webster_splits(net: Network, junctions: list[Junction],
                   saturation_flow_pcu_per_hr: float = 1800.0,
                   lost_time_per_phase_s: float = 4.0
                   ) -> tuple[dict, float, float]:
    """Webster cycle and splits from the *arrival* demand, as a planner would.

    The planner sees arrival flow, not the receiving link — which is precisely
    the information asymmetry this comparison is about.
    """
    ys: dict[str, dict[str, float]] = {}
    for j in junctions:
        ys[j.id] = {}
        for p in j.phases:
            q = 0.0
            for mid in p.movement_ids:
                m = net.movements[mid]
                src = net.links[m.from_link]
                # Internal links have no external arrival; use what feeds them.
                q += (src.arrival_pcu_per_hr if src.arrival_pcu_per_hr > 0
                      else _upstream_demand(net, src.id))
            ys[j.id][p.id] = min(0.95, q / saturation_flow_pcu_per_hr)

    Y = max(sum(v.values()) for v in ys.values())
    L = max(len(j.phases) for j in junctions) * lost_time_per_phase_s
    cycle = ((1.5 * L + 5.0) / (1.0 - Y)) if Y < 0.95 else 150.0
    cycle = float(min(150.0, max(40.0, cycle)))

    greens: dict[str, dict[str, float]] = {}
    for j in junctions:
        tot = sum(ys[j.id].values()) or 1.0
        eff = max(1.0, cycle - len(j.phases) * lost_time_per_phase_s)
        raw = {pid: eff * y / tot for pid, y in ys[j.id].items()}

        # Apply the minimum green, then give back what flooring took, in
        # proportion to each phase's slack above the floor. Flooring without
        # rescaling lets the greens plus lost time exceed the cycle, and a
        # plan handed more green than its cycle contains is not a plan the
        # signal could run — it also quietly gives the fixed-time controller
        # capacity that its declared cycle never paid for.
        MIN_G = 8.0
        floored = {pid: max(MIN_G, g) for pid, g in raw.items()}
        over = sum(floored.values()) - eff
        if over > 1e-9:
            slack = {pid: max(0.0, floored[pid] - MIN_G) for pid in floored}
            s_tot = sum(slack.values())
            if s_tot > 1e-9:
                for pid in floored:
                    floored[pid] -= over * slack[pid] / s_tot
            else:
                # Every phase is at the floor: the cycle is too short to hold
                # them all. Scale the cycle up rather than overrun it.
                cycle = (len(j.phases) * lost_time_per_phase_s
                         + sum(floored.values()))
        greens[j.id] = floored
    return greens, cycle, Y


def _upstream_demand(net: Network, link_id: str) -> float:
    """Total arrival rate feeding an internal link, for split planning."""
    total = 0.0
    for m in net.movements.values():
        if m.to_link == link_id:
            src = net.links[m.from_link]
            total += (src.arrival_pcu_per_hr if src.arrival_pcu_per_hr > 0
                      else 0.0)
    return total


SCENARIOS = {
    "steady": {
        "label": "Steady, balanced demand",
        "blurb": "Demand constant and matching exactly what the fixed-time "
                 "plan was designed for — Webster's home ground. Any gain here "
                 "is not adaptation to demand but green reclamation: a fixed "
                 "plan holds green for its full allocation even after the "
                 "queue has cleared, while max-pressure moves on. This is the "
                 "smallest honest gap between the two.",
    },
    "swing": {
        "label": "Demand swings between approaches",
        "blurb": "Total demand is unchanged, but which approach carries it "
                 "flips halfway through — a school turning out, a shift "
                 "change, an event. The fixed-time splits were computed from "
                 "the average and are now wrong on both approaches.",
    },
    "incident": {
        "label": "Downstream blockage",
        "blurb": "Something downstream halves the exit rate for four minutes. "
                 "The link between the junctions backs up. A fixed-time plan "
                 "cannot see it and keeps discharging into a link with no "
                 "room, which is exactly the spillback max-pressure exists to "
                 "prevent.",
    },
}


def _profiles(scenario: str, seconds: float):
    """Arrival and sink multipliers over time for a named scenario."""
    half = seconds / 2.0
    if scenario == "swing":
        # Multipliers chosen so TOTAL demand is genuinely constant across the
        # switch and only its *split* between approaches moves: 300 PCU/hr is
        # shifted from the cross approaches to the main one and then back.
        #   first half   main 700*1.4286 = 1000, cross 480*0.375 = 180
        #   second half  main 700*0.5714 =  400, cross 480*1.625 = 780
        # both totalling 1180 PCU/hr. Picking round multipliers instead (1.55
        # and 0.45 on unequal bases) drops total demand by 18.6% at the
        # switch, which confounds the experiment: part of any delay change is
        # then simply less traffic, not better control.
        return {
            "main_in": lambda t: 1000.0 / 700.0 if t < half else 400.0 / 700.0,
            "cross1_in": lambda t: 0.375 if t < half else 1.625,
            "cross2_in": lambda t: 0.375 if t < half else 1.625,
        }, None
    if scenario == "incident":
        a, b = seconds * 0.30, seconds * 0.30 + 420.0
        # Applied to the movement that discharges the mid-link, so the mid-link
        # - a real, observable link with only 12 PCU of storage - is what backs
        # up. That is the point of the scenario: the queue forms somewhere the
        # controller can see, so max-pressure can stop feeding it while a
        # fixed-time plan keeps discharging into a link with no room.
        return {}, {"m_mid": (lambda t: 0.18 if a <= t < b else 1.0)}
    return {}, None


def build(scenario: str = "steady", seconds: float = 1800.0,
          **corridor_kwargs) -> tuple[Network, list[Junction]]:
    net, junctions = corridor(**corridor_kwargs)
    arr, cap = _profiles(scenario, seconds)
    for lid, fn in arr.items():
        if lid in net.links:
            net.links[lid].arrival_profile = fn
    for mid, fn in (cap or {}).items():
        if mid in net.movements:
            net.movements[mid].capacity_profile = fn
    return net, junctions


def compare_controllers(
    scenario: str = "steady",
    seconds: float = 1800.0,
    dt: float = 1.0,
    min_green_s: float = 12.0,
    switch_margin: float = 0.25,
    **corridor_kwargs,
) -> dict:
    """Run both controllers on the same network, demand and random-free model.

    The fixed-time plan is computed from the *average* demand, which is what a
    planner would have. Max-pressure gets no extra demand information — only the
    queues it can see. That asymmetry is the experiment.
    """
    sat = corridor_kwargs.get("saturation_flow_pcu_per_hr", 1800.0)

    net_a, junctions_a = build(scenario, seconds, **corridor_kwargs)
    greens, cycle, Y = webster_splits(net_a, junctions_a,
                                      saturation_flow_pcu_per_hr=sat)
    r_fixed = net_a.run(FixedTimeController(greens, cycle_s=cycle),
                        seconds=seconds, dt=dt)

    net_b, _ = build(scenario, seconds, **corridor_kwargs)
    r_mp = net_b.run(MaxPressureController(min_green_s=min_green_s,
                                          switch_margin=switch_margin),
                     seconds=seconds, dt=dt)

    def pct(a: float, b: float) -> float:
        return ((a - b) / a * 100.0) if a > 1e-9 and math.isfinite(a) else 0.0

    return {
        "scenario": scenario,
        "scenario_label": SCENARIOS.get(scenario, {}).get("label", scenario),
        "blurb": SCENARIOS.get(scenario, {}).get("blurb", ""),
        "fixed": r_fixed,
        "max_pressure": r_mp,
        "cycle_s": cycle,
        "Y": Y,
        "greens": greens,
        "delay_reduction_pct": pct(r_fixed.mean_delay_s_per_pcu,
                                   r_mp.mean_delay_s_per_pcu),
        "throughput_gain_pct": -pct(r_fixed.throughput_pcu,
                                    r_mp.throughput_pcu),
        "spillback_reduction_pct": pct(r_fixed.spillback_seconds,
                                       r_mp.spillback_seconds),
        "queue_reduction_pct": pct(r_fixed.mean_total_queue_pcu,
                                   r_mp.mean_total_queue_pcu),
    }


if __name__ == "__main__":
    for sc in SCENARIOS:
        out = compare_controllers(scenario=sc, seconds=1800.0)
        f, m = out["fixed"], out["max_pressure"]
        print(f"{out['scenario_label'][:34]:36s} C={out['cycle_s']:5.1f}s Y={out['Y']:.2f} | "
              f"delay {f.mean_delay_s_per_pcu:6.1f} -> {m.mean_delay_s_per_pcu:6.1f} "
              f"({out['delay_reduction_pct']:+5.1f}%) | "
              f"thru {f.throughput_pcu:6.1f} -> {m.throughput_pcu:6.1f} "
              f"({out['throughput_gain_pct']:+5.1f}%) | "
              f"queue {f.mean_total_queue_pcu:5.1f} -> {m.mean_total_queue_pcu:5.1f} | "
              f"spill {f.spillback_seconds:5.0f} -> {m.spillback_seconds:5.0f}s")
