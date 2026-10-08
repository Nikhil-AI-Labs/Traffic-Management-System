"""What-if analysis on the Surat signal plan.

The Signal timing view answers "what should the signal do now?". The
questions a traffic engineer asks next are about the future and about the
street, and this module answers them from the same demand and the same
Webster optimiser:

* headroom   - how much more traffic can the junction take before even the
               best possible timing fails? Webster's closed form gives it
               directly: at the longest acceptable cycle C_max, the common
               degree of saturation is x = Y*C_max / (C_max - L), so the
               junction stays workable while Y <= x_p (C_max - L) / C_max.
               The gap between today's Y and that limit is the reserve
               capacity, and at an assumed growth rate it is a number of
               years - the shelf life of a retiming before it needs concrete.
* growth     - re-optimise for demand scaled up or down.
* capacity   - knock a share off one approach's saturation flow: kerb
  loss         parking, a hawker encroachment, a broken-down bus, a lane
               closed for works.
* benefit    - the delay the adaptive plan saves, turned into vehicle-hours,
               fuel, CO2 and rupees a year. Every assumption is a parameter
               with its value printed beside the result; the defaults are
               indicative, not measured.

Flow ratios here are *uncapped*. `signal_opt.ApproachDemand.flow_ratio`
clips y at 0.95 per approach so the optimiser stays defined; reserve capacity
computed from clipped ratios would read as headroom that is not there.
"""

from __future__ import annotations

import ast
import math
from dataclasses import fields

import numpy as np
import pandas as pd

import config as cfg
import signal_opt as so

PRACTICAL_X = 0.90          # degree of saturation a design should not exceed
DEFAULT_GROWTH_PER_YEAR = 0.05

# Indicative idling fuel use per vehicle, litres per hour, and the fuel each
# group runs on. Order-of-magnitude figures for a stopped engine at idle;
# override any of them in the request. Autos are counted as petrol-equivalent.
IDLE_FUEL_L_PER_H = {"Two-wheeler": 0.10, "Auto": 0.25, "Car": 0.60,
                     "LCV/Tempo": 0.80, "Bus/Truck": 2.00, "Other": 0.50}
FUEL_OF = {"Two-wheeler": "petrol", "Auto": "petrol", "Car": "petrol",
           "LCV/Tempo": "diesel", "Bus/Truck": "diesel", "Other": "petrol"}
CO2_KG_PER_L = {"petrol": 2.31, "diesel": 2.68}     # combustion factors
PRICE_RS_PER_L = {"petrol": 100.0, "diesel": 90.0}
# PCU of a representative vehicle in each reporting group (IRC:106-1990).
GROUP_PCU = {"Two-wheeler": 0.5, "Auto": 1.2, "Car": 1.0, "LCV/Tempo": 1.4,
             "Bus/Truck": 2.2, "Other": 1.0}


# --------------------------------------------------------------------------
# Demand scenarios
# --------------------------------------------------------------------------

def y_raw(d: so.ApproachDemand) -> float:
    """Critical-lane flow ratio without the optimiser's 0.95 clip."""
    s = d.saturation_flow_per_lane
    return d.critical_lane_flow_pcu_per_hr / s if s else 0.0


def scenario(demands: list[so.ApproachDemand], growth: float = 1.0,
             loss: dict[str, float] | None = None) -> list[so.ApproachDemand]:
    """Demands scaled by `growth`, with `loss[approach_id]` (0-1) taken off
    that approach's saturation flow."""
    names = {f.name for f in fields(so.ApproachDemand)}
    out = []
    for d in demands:
        kw = {k: getattr(d, k) for k in names}
        kw["flow_pcu_per_hr"] = d.flow_pcu_per_hr * growth
        kw["critical_lane_flow_pcu_per_hr"] = d.critical_lane_flow_pcu_per_hr * growth
        cut = float((loss or {}).get(d.approach_id, 0.0))
        if cut:
            if d.approach_width_m:          # __post_init__ re-derives s from width
                kw["saturation_factor"] = d.saturation_factor * (1.0 - cut)
            else:
                kw["saturation_flow_per_lane"] = d.saturation_flow_per_lane * (1.0 - cut)
        out.append(so.ApproachDemand(**kw))
    return out


# --------------------------------------------------------------------------
# Reserve capacity
# --------------------------------------------------------------------------

def headroom(demands: list[so.ApproachDemand], growth_per_year: float = DEFAULT_GROWTH_PER_YEAR,
             x_practical: float = PRACTICAL_X) -> dict:
    n = len(demands)
    L = n * so.LOST_TIME_PER_PHASE_S
    C = so.MAX_CYCLE_S
    Y = sum(y_raw(d) for d in demands)
    y_ult = (C - L) / C
    y_prac = x_practical * (C - L) / C

    def reserve(limit: float) -> float | None:
        return (limit / Y - 1.0) if Y > 0 else None

    def years(r: float | None) -> float | None:
        if r is None or growth_per_year <= 0:
            return None
        return math.log1p(r) / math.log1p(growth_per_year) if r > 0 else 0.0

    r_p, r_u = reserve(y_prac), reserve(y_ult)
    best_x = Y * C / (C - L) if C > L else None
    return {
        "Y": Y, "lost_time_s": L, "max_cycle_s": C, "x_practical": x_practical,
        "Y_practical": y_prac, "Y_ultimate": y_ult,
        "best_x_at_max_cycle": best_x,
        "reserve_practical_pct": None if r_p is None else r_p * 100.0,
        "reserve_ultimate_pct": None if r_u is None else r_u * 100.0,
        "growth_per_year_pct": growth_per_year * 100.0,
        "years_to_practical": years(r_p),
        "years_to_failure": years(r_u),
        "approaches": [{"id": d.approach_id, "y": y_raw(d),
                        "share_pct": (y_raw(d) / Y * 100.0) if Y else 0.0}
                       for d in demands],
    }


def growth_curve(demands: list[so.ApproachDemand], baseline_cycle: float,
                 delay_model: str = "auto", lo: float = 0.7, hi: float = 1.5,
                 step: float = 0.02) -> pd.DataFrame:
    """Best-achievable and fixed-timer performance as demand scales."""
    rows = []
    for g in np.arange(lo, hi + 1e-9, step):
        dg = scenario(demands, float(g))
        opt = so.optimise(dg, delay_model=delay_model)
        fix = so.evaluate_fixed_plan(dg, cycle_s=baseline_cycle, delay_model=delay_model)
        Y = sum(y_raw(d) for d in dg)
        rows.append({
            "growth_pct": round((g - 1.0) * 100.0, 1),
            "Y": Y,
            "x_adaptive": max(p.degree_of_saturation for p in opt.phases),
            "x_fixed": max(p.degree_of_saturation for p in fix.phases),
            "delay_adaptive": opt.mean_delay_s_per_pcu,
            "delay_fixed": fix.mean_delay_s_per_pcu,
            "cycle_s": opt.cycle_s,
            "feasible": bool(opt.feasible),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Benefits
# --------------------------------------------------------------------------

def _parse_groups(cell) -> dict:
    if isinstance(cell, dict):
        return cell
    try:
        v = ast.literal_eval(str(cell))
        return v if isinstance(v, dict) else {}
    except (ValueError, SyntaxError):
        return {}


def fleet_shares(df: pd.DataFrame, camera_ids: list[str]) -> dict[str, float]:
    """Share of vehicles by reporting group at the given cameras' lanes."""
    sub = df[df.camera_id.isin(camera_ids)]
    if "zone_kind" in sub:
        sub = sub[sub.zone_kind == "lane"]
    tot: dict[str, float] = {}
    for cell in sub.get("counts_by_group", []):
        for k, v in _parse_groups(cell).items():
            tot[k] = tot.get(k, 0.0) + float(v)
    n = sum(tot.values())
    if n <= 0:
        return {"Car": 1.0}
    return {g: tot.get(g, 0.0) / n for g in cfg.GROUP_ORDER if tot.get(g, 0.0) > 0}


def benefits(base: so.SignalPlan, opt: so.SignalPlan,
             demands: list[so.ApproachDemand], shares: dict[str, float],
             hours_per_day: float = 6.0, days_per_year: float = 300.0,
             idle_fuel: dict | None = None, price: dict | None = None) -> dict:
    """Delay saved by the adaptive plan, in vehicle-hours, fuel, CO2 and money.

    Per approach: (baseline delay - adaptive delay) x that approach's flow.
    The delay is the critical lane's, applied to the whole approach, so the
    total leans high; it is a Webster/HCM model delay, not a measured one.
    """
    idle = {**IDLE_FUEL_L_PER_H, **(idle_fuel or {})}
    rs = {**PRICE_RS_PER_L, **(price or {})}
    flows = {d.approach_id: d.flow_pcu_per_hr for d in demands}
    bmap = {p.approach_id: p for p in base.phases}

    pcu_h_per_h = 0.0
    per_approach = []
    for p in opt.phases:
        b = bmap.get(p.approach_id)
        if b is None:
            continue
        saved_s = (b.est_delay_s_per_pcu or 0.0) - (p.est_delay_s_per_pcu or 0.0)
        q = flows.get(p.approach_id, 0.0)
        pcu_h_per_h += q * saved_s / 3600.0
        per_approach.append({"id": p.approach_id, "flow_pcu_per_hr": q,
                             "delay_before_s": b.est_delay_s_per_pcu,
                             "delay_after_s": p.est_delay_s_per_pcu,
                             "saved_s_per_pcu": saved_s})

    pcu_per_veh = sum(s * GROUP_PCU.get(g, 1.0) for g, s in shares.items()) or 1.0
    veh_h_per_h = pcu_h_per_h / pcu_per_veh
    veh_h_day = veh_h_per_h * hours_per_day

    fuel = {"petrol": 0.0, "diesel": 0.0}
    for g, s in shares.items():
        fuel[FUEL_OF.get(g, "petrol")] += veh_h_day * s * idle.get(g, 0.5)
    litres = sum(fuel.values())
    co2_kg = sum(v * CO2_KG_PER_L[k] for k, v in fuel.items())
    rupees = sum(v * rs[k] for k, v in fuel.items())

    return {
        "pcu_hours_per_hour": pcu_h_per_h,
        "vehicle_hours_per_day": veh_h_day,
        "vehicle_hours_per_year": veh_h_day * days_per_year,
        "fuel_l_per_day": litres,
        "fuel_l_per_year": litres * days_per_year,
        "co2_t_per_year": co2_kg * days_per_year / 1000.0,
        "rupees_per_year": rupees * days_per_year,
        "per_approach": per_approach,
        "fleet": [{"group": g, "share_pct": s * 100.0,
                   "idle_l_per_h": idle.get(g, 0.5), "fuel": FUEL_OF.get(g, "petrol")}
                  for g, s in sorted(shares.items(), key=lambda kv: -kv[1])],
        "assumptions": {
            "hours_per_day": hours_per_day, "days_per_year": days_per_year,
            "pcu_per_vehicle": pcu_per_veh,
            "price_rs_per_l": rs, "co2_kg_per_l": CO2_KG_PER_L,
            "note": ("Indicative. Idle fuel rates and prices are order-of-magnitude "
                     "defaults, the delays are model delays, and the measured demand "
                     "is assumed to hold for the stated hours each day."),
        },
    }
