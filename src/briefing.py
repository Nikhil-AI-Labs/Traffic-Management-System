"""Network briefing: the whole picture on a few printable pages.

What a traffic officer takes into a meeting: which sites are worst, what the
signal should do and for how long that answer holds, which bottlenecks need
someone, what is rising, and what the control room has done about it. One
self-contained HTML file - no external assets, one optional print button -
so it prints cleanly to PDF from any browser and can be mailed as it is.

Every figure comes from the same modules the dashboard uses; nothing here
computes traffic. Figures that depend on the uncalibrated scale keep their
"~", exactly as on screen.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape

import pandas as pd

import config as cfg
import forecast as fc
import services as S
import signal_opt as so
import theme as T
import whatif as W

IST = timezone(timedelta(hours=5, minutes=30))

LOS_HEX = {"Free": "#0ca30c", "Moderate": "#fab219", "Heavy": "#ec835a", "Jam": "#d03b3b"}
SEV_HEX = {"critical": "#d03b3b", "warning": "#c98500", "info": "#2a78d6"}

CSS = """
*{box-sizing:border-box}body{font:14px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;
color:#111;margin:0;background:#f6f6f3}main{max-width:900px;margin:0 auto;background:#fff;padding:36px 44px 48px}
h1{font-size:24px;margin:0;letter-spacing:-.02em}h2{font-size:13px;letter-spacing:.1em;text-transform:uppercase;
margin:30px 0 10px;padding-bottom:6px;border-bottom:1px solid #e1e0d9}.sub{color:#52514e;font-size:13px;margin-top:4px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:18px}.kpi{border:1px solid #e1e0d9;
border-radius:9px;padding:10px 12px}.kpi .l{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:#52514e;
font-weight:650}.kpi .v{font-size:22px;font-weight:700;margin-top:3px}.kpi .d{font-size:11.5px;color:#52514e}
table{width:100%;border-collapse:collapse;font-size:12.5px}th{text-align:left;color:#52514e;font-weight:650;
border-bottom:1px solid #c3c2b7;padding:5px 6px}td{border-bottom:1px solid #eeede8;padding:5px 6px;vertical-align:top}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}.sw{display:inline-block;width:9px;height:9px;
border-radius:50%;margin-right:6px;vertical-align:0}.bar{height:7px;border-radius:4px;background:#2a78d6}
.alert{border-left:3px solid var(--c);padding:6px 10px;margin:8px 0;background:#fafaf8}.alert b{font-size:13px}
.alert .e{font-size:12px;color:#52514e;margin-top:2px}.tag{display:inline-block;font-size:10.5px;font-weight:700;
padding:1px 7px;border-radius:9px;background:#efeee9;margin-right:6px}.note{font-size:12px;color:#52514e}
.callout{border:1px solid #e1e0d9;border-left:3px solid #fab219;border-radius:8px;padding:9px 12px;font-size:12.5px;
color:#333;margin-top:10px}.print{position:fixed;right:18px;top:18px;font:600 13px system-ui;padding:8px 14px;
border-radius:8px;border:1px solid #c3c2b7;background:#fff;cursor:pointer}
@media (max-width:700px){main{padding:20px 16px}.kpis{grid-template-columns:repeat(2,1fr)}table{font-size:11.5px}}
@media print{body{background:#fff}main{padding:0;max-width:none}.print{display:none}h2{break-after:avoid}
.alert,tr{break-inside:avoid}}@page{size:A4;margin:14mm}
"""


def _e(v) -> str:
    return escape(str(v)) if v is not None else "—"


def _n(v, d: int = 0) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f or f in (float("inf"), float("-inf")):
        return "—"
    return f"{f:,.{d}f}"


def _los(los: str) -> str:
    return f'<span class="sw" style="background:{LOS_HEX.get(los, "#999")}"></span>{_e(los)}'


def build(df: pd.DataFrame, incident_items: list[dict]) -> str:
    now = datetime.now(IST)
    summary = fc.camera_summary(df)
    windows = len(df)
    minutes = float(df.window_end_s.groupby(df.camera_id).max().sum() / 60) if windows else 0.0
    over = float(df.los.isin(["Heavy", "Jam"]).mean() * 100) if windows else 0.0
    open_crit = [i for i in incident_items if i["status"] == "open" and i.get("severity") == "critical"]
    counts = {s: sum(1 for i in incident_items if i["status"] == s)
              for s in ("open", "acknowledged", "resolved")}

    # ---- signal -------------------------------------------------------
    demands = S.junction_demands(df)
    sig_html, head_kpi = "", ("—", "")
    if demands:
        base = so.evaluate_fixed_plan(demands, cycle_s=120.0)
        opt = so.optimise(demands)
        cmp = so.compare(base, opt)
        head = W.headroom(demands)
        ben = W.benefits(base, opt, demands, W.fleet_shares(df, [d.approach_id for d in demands]))
        rows = "".join(
            f"<tr><td>{_e(b.label)}</td><td class=n>{_n(b.green_s, 1)} → <b>{_n(o.green_s, 1)}</b> s</td>"
            f"<td class=n>{_n(b.degree_of_saturation, 3)} → <b>{_n(o.degree_of_saturation, 3)}</b></td>"
            f"<td class=n>{_n(b.est_delay_s_per_pcu, 1)} → {_n(o.est_delay_s_per_pcu, 1)}</td></tr>"
            for b, o in zip(base.phases, opt.phases))
        r_u, yrs = head["reserve_ultimate_pct"], head["years_to_failure"]
        head_kpi = (f"{r_u:+.1f}%" if r_u is not None else "—",
                    f"{_n(yrs, 1)} yr at {head['growth_per_year_pct']:.0f}%/yr" if yrs else "")
        sig_html = f"""
<table><tr><th>Approach</th><th class=n>Green</th><th class=n>Saturation x</th>
<th class=n>Delay (s/PCU)</th></tr>{rows}</table>
<p>Cycle <b>{_n(base.cycle_s)} s → {_n(opt.cycle_s, 1)} s</b> (Webster). Worst approach
x <b>{_n(max(p.degree_of_saturation for p in base.phases), 3)} →
{_n(max(p.degree_of_saturation for p in opt.phases), 3)}</b>; mean delay
{_n(cmp['baseline_mean_delay_s'], 1)} → {_n(cmp['optimised_mean_delay_s'], 1)} s/PCU
({_e(opt.phases[0].delay_model)} model).</p>
<p><b>How long it holds.</b> Total flow ratio Y = {_n(head['Y'], 3)}. At the longest
acceptable cycle ({_n(head['max_cycle_s'])} s) no timing can serve Y above
{_n(head['Y_ultimate'], 3)}: <b>{_n(r_u, 1)}% headroom</b>, about
<b>{_n(yrs, 1)} years</b> at {head['growth_per_year_pct']:.0f}% annual growth. The practical
limit (x = {head['x_practical']:.2f}) is {_n(head['reserve_practical_pct'], 1)}% away —
{'already passed' if (head['reserve_practical_pct'] or 0) < 0 else 'still ahead'}.
Beyond that the remedy is physical capacity or demand management, not retiming.</p>
<p class=note>Indicative value of the retiming at this demand for 6 h a day, 300 days a
year: {_n(ben['vehicle_hours_per_year'])} vehicle-hours, {_n(ben['fuel_l_per_year'])} L of
idling fuel, {_n(ben['co2_t_per_year'], 1)} t CO₂, ≈ ₹{_n(ben['rupees_per_year'] / 1e5, 1)} lakh.
The two junction cameras watch different corridors, so this plan demonstrates the method
on measured demand rather than being a deployable plan for one intersection.</p>"""

    # ---- sites --------------------------------------------------------
    site_rows = ""
    for _, r in summary.iterrows():
        ci = float(r.mean_congestion)
        site_rows += (
            f"<tr><td>{_e(cfg.camera_name(r.camera_id))}</td>"
            f"<td>{_e(cfg.camera_meta(r.camera_id).get('mode', 'area'))}</td>"
            f"<td>{_los(T.los_of(ci))}</td><td class=n>{ci:.2f}</td>"
            f"<td style='width:110px'><div class=bar style='width:{max(2, ci * 100):.0f}%;"
            f"background:{LOS_HEX[T.los_of(ci)]}'></div></td>"
            f"<td class=n>{_n(r.peak_congestion, 2)}</td><td>{_e(r.worst_zone)}</td>"
            f"<td class=n>{_n(r.saturated_windows / max(1, r.total_windows) * 100)}%</td></tr>")

    # ---- alerts -------------------------------------------------------
    rec = [i for i in incident_items
           if i.get("source", "recorded") == "recorded" and i.get("current", True)]
    top = sorted(rec, key=lambda i: ({"critical": 0, "warning": 1}.get(i["severity"], 2),
                                     -float(i.get("duration_s") or 0)))[:8]
    alert_html = "".join(
        f"<div class=alert style='--c:{SEV_HEX.get(a['severity'], '#999')}'>"
        f"<span class=tag>{_e(a['severity']).upper()}</span><span class=tag>{_e(a['status'])}</span>"
        f"<b>{_e(a['headline'])}</b><div class=e>{_e(a['camera_name'])} · {_e(a['zone_label'])} · "
        f"{_n(a['start_s'])}–{_n(a['end_s'])} s</div><div class=e>{_e(a['evidence'])}</div>"
        f"<div class=e><b>Do:</b> {_e(a['recommendation'])}</div></div>" for a in top)

    # ---- outlook ------------------------------------------------------
    risks = fc.rising_risks(fc.forecast_all(df, horizon_s=60.0), threshold=0.45)[:8]
    risk_html = (
        "<table><tr><th>Site</th><th>Zone</th><th class=n>Now</th><th class=n>In 60 s</th></tr>"
        + "".join(f"<tr><td>{_e(cfg.camera_name(f.camera_id))}</td><td>{_e(f.zone_label)}</td>"
                  f"<td class=n>{f.current:.2f}</td><td class=n><b>{f.predicted:.2f}</b></td></tr>"
                  for f in risks) + "</table>"
    ) if risks else "<p class=note>No zone is credibly rising.</p>"

    # ---- incident log -------------------------------------------------
    acted = [(h, i) for i in incident_items for h in i.get("history", [])]
    acted.sort(key=lambda hi: hi[0]["at"], reverse=True)
    log_html = (
        "<table><tr><th>When (UTC)</th><th>Who</th><th>Action</th><th>Incident</th><th>Note</th></tr>"
        + "".join(f"<tr><td>{_e(h['at'][:16].replace('T', ' '))}</td><td>{_e(h['by'])}</td>"
                  f"<td>{_e(h['action'])}</td><td>{_e(i.get('camera_short') or i.get('camera_name'))} · "
                  f"{_e(i.get('headline'))}</td><td>{_e(h.get('note') or '')}</td></tr>"
                  for h, i in acted[:10]) + "</table>"
    ) if acted else "<p class=note>No incident has been acknowledged or resolved yet.</p>"

    worst = summary.iloc[0] if len(summary) else None
    verified = sum(1 for c in cfg.CAMERAS if cfg.scale_is_verified(c))
    worst_name = _e(cfg.camera_name(worst.camera_id).split("-")[0].strip()) if worst is not None else "—"
    worst_detail = (f"{_los(T.los_of(float(worst.mean_congestion)))} · index "
                    f"{float(worst.mean_congestion):.2f}") if worst is not None else ""
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Network briefing · Surat Traffic Intelligence · {now:%d %b %Y}</title><style>{CSS}</style></head>
<body><button class=print onclick="window.print()">Print / save as PDF</button><main>
<h1>Surat Traffic Intelligence — network briefing</h1>
<div class=sub>Generated {now:%d %B %Y, %H:%M} IST · {len(summary)} cameras · {windows:,} two-second
measurement windows · {minutes:.1f} min of recorded footage · PS-08, E-Rakshak Hackathon 2026</div>

<div class=kpis>
<div class=kpi><div class=l>At or over capacity</div><div class=v>{over:.0f}%</div>
<div class=d>of monitored time, network-wide</div></div>
<div class=kpi><div class=l>Worst site</div><div class=v style="font-size:17px">{worst_name}</div>
<div class=d>{worst_detail}</div></div>
<div class=kpi><div class=l>Open critical alerts</div><div class=v>{len(open_crit)}</div>
<div class=d>{counts['acknowledged']} acknowledged · {counts['resolved']} resolved</div></div>
<div class=kpi><div class=l>Signal headroom</div><div class=v>{head_kpi[0]}</div>
<div class=d>{head_kpi[1] or 'before no timing can cope'}</div></div>
</div>

<h2>Sites, worst first</h2>
<table><tr><th>Site</th><th>Mode</th><th>Level of service</th><th class=n>Mean</th><th></th>
<th class=n>Peak</th><th>Worst zone</th><th class=n>Saturated</th></tr>{site_rows}</table>
<p class=note>Congestion index on a 0–1 scale normalised against absolute occupancy bounds shared
by every camera, so sites compare directly. "Saturated" is the share of windows where vehicles
overlap too much to count, and occupancy leads instead.</p>

<h2>Signal timing — recommended plan</h2>{sig_html or '<p class=note>No junction camera analysed.</p>'}

<h2>Bottlenecks needing attention</h2>{alert_html or '<p class=note>No sustained bottleneck.</p>'}

<h2>Rising in the next minute</h2>{risk_html}

<h2>Control-room log</h2>{log_html}

<div class=callout><b>Reading these figures.</b> {verified} of {len(cfg.CAMERAS)} cameras have a
measured pixel-to-metre scale; speeds, densities and flows marked "~" elsewhere carry an unknown
multiplier until one road width is measured per camera. The ranking, level-of-service bands,
saturation flags and the signal before/after comparison are scale-free. Footage recorded in
July 2026.</div>
</main></body></html>"""
