"""Build a standalone HTML report for an analysed clip.

Self-contained on purpose: the charts are live Plotly figures with the library
inlined, and the annotated frame is embedded as a data URI, so the file opens
correctly on a machine with no network and no copy of this project. A traffic
engineer should be able to forward it and have it just work.

The report says what was measured, what was estimated, and what the footage
could not support — the same separation the dashboard makes, because a document
that drops the caveats on its way out of the tool is worse than no document.
"""

from __future__ import annotations

import base64
import html
import io
from datetime import datetime

import cv2
import numpy as np
import pandas as pd

import charts as C
import config as cfg
import theme as T

# The report renders on white regardless of the viewer's dashboard theme — it
# is a document, and a document that changes colour with the app that made it
# is confusing to file and impossible to print.
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
LINE = "#e6e5df"
SURFACE = "#fcfcfb"
PAGE = "#ffffff"
ACCENT = "#2a78d6"
STATUS = dict(T.STATUS)
LOS_COLOR = {k: STATUS[v] for k, v in T.LOS_STATUS.items()}


def _esc(s) -> str:
    return html.escape(str(s if s is not None else "—"))


def _img_tag(rgb: np.ndarray | None, alt: str = "") -> str:
    if rgb is None:
        return ""
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    if w > 1400:
        bgr = cv2.resize(bgr, (1400, int(h * 1400 / w)),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        return ""
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return (f'<figure><img src="data:image/jpeg;base64,{b64}" alt="{_esc(alt)}">'
            f'<figcaption>{_esc(alt)}</figcaption></figure>')


def _chip(label: str, color: str) -> str:
    h = color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (f'<span class="chip" style="background:rgba({r},{g},{b},.13)">'
            f'<i style="background:{color}"></i>{_esc(label)}</span>')


def _table(df: pd.DataFrame, max_rows: int = 200) -> str:
    if df is None or df.empty:
        return '<p class="muted">No rows.</p>'
    d = df.head(max_rows)
    head = "".join(f"<th>{_esc(c)}</th>" for c in d.columns)
    body = "".join(
        "<tr>" + "".join(
            f"<td>{_esc(round(v, 3) if isinstance(v, float) else v)}</td>"
            for v in row) + "</tr>"
        for row in d.itertuples(index=False))
    more = (f'<p class="muted">Showing {max_rows} of {len(df):,} rows; the '
            f'full set is in the CSV.</p>' if len(df) > max_rows else "")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{more}"


class _Figs:
    """Emits Plotly figures, inlining the library exactly once."""

    def __init__(self):
        self.first = True

    def __call__(self, fig) -> str:
        if fig is None:
            return ""
        fig.update_layout(
            paper_bgcolor=PAGE, plot_bgcolor=PAGE,
            font=dict(family=T.FONT, size=12, color=INK2),
        )
        html_str = fig.to_html(
            full_html=False,
            include_plotlyjs=("inline" if self.first else False),
            config={"displayModeBar": False, "displaylogo": False},
            default_width="100%",
        )
        self.first = False
        return html_str


_CSS = f"""
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:{PAGE}; color:{INK};
        font-family:{T.FONT}; font-size:15px; line-height:1.55;
        -webkit-font-smoothing:antialiased; }}
.wrap {{ max-width: 1120px; margin: 0 auto; padding: 40px 28px 90px; }}
header {{ border-bottom:1px solid {LINE}; padding-bottom:20px;
          margin-bottom:26px; }}
.eyebrow {{ font-size:.70rem; font-weight:700; letter-spacing:.14em;
            text-transform:uppercase; color:{INK2}; }}
h1 {{ font-size:2.05rem; font-weight:690; letter-spacing:-.028em;
      margin:.35rem 0 .2rem; }}
h2 {{ font-size:.80rem; font-weight:750; letter-spacing:.10em;
      text-transform:uppercase; color:{INK}; margin:2.6rem 0 .1rem;
      padding-bottom:.5rem; border-bottom:1px solid {LINE}; }}
h3 {{ font-size:1.0rem; font-weight:640; margin:1.5rem 0 .3rem; }}
p {{ margin:.55rem 0; color:{INK2}; }}
p b, li b, td b {{ color:{INK}; font-weight:640; }}
.sub {{ color:{INK2}; font-size:.93rem; }}
.muted {{ color:{MUTED}; font-size:.83rem; }}

.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
          gap:11px; margin:1.1rem 0 .4rem; }}
.tile {{ background:{SURFACE}; border:1px solid {LINE}; border-radius:11px;
         padding:15px 17px; }}
.tile .l {{ font-size:.70rem; font-weight:690; letter-spacing:.06em;
            text-transform:uppercase; color:{INK2}; }}
.tile .v {{ font-size:1.85rem; font-weight:660; letter-spacing:-.02em;
            margin-top:6px; }}
.tile .u {{ font-size:.9rem; font-weight:500; color:{INK2}; margin-left:3px; }}
.tile .d {{ font-size:.80rem; color:{INK2}; margin-top:6px; line-height:1.5; }}

.hero {{ font-size:3.2rem; font-weight:670; letter-spacing:-.035em;
         line-height:1; }}
.hero-u {{ font-size:1.2rem; font-weight:500; color:{INK2}; margin-left:5px; }}

.chip {{ display:inline-flex; align-items:center; gap:6px; padding:3px 10px 3px 8px;
         border-radius:999px; font-size:.74rem; font-weight:670; color:{INK};
         white-space:nowrap; }}
.chip i {{ width:8px; height:8px; border-radius:50%; display:block; }}

.callout {{ background:{SURFACE}; border:1px solid {LINE};
            border-left:3px solid var(--a,{ACCENT}); border-radius:9px;
            padding:14px 17px; margin:1rem 0; font-size:.875rem; color:{INK2}; }}
.callout .t {{ font-size:.78rem; font-weight:730; letter-spacing:.05em;
               text-transform:uppercase; color:{INK}; margin-bottom:5px; }}

.alert {{ background:{SURFACE}; border:1px solid {LINE};
          border-left:3px solid var(--s,{MUTED}); border-radius:9px;
          padding:13px 16px; margin-bottom:10px; }}
.alert .h {{ font-weight:650; color:{INK}; margin:6px 0 3px; }}
.alert .m {{ font-size:.77rem; color:{INK2}; }}
.alert .b {{ font-size:.85rem; color:{INK2}; margin-top:5px; }}

table {{ border-collapse:collapse; width:100%; font-size:.83rem;
         font-variant-numeric:tabular-nums; margin:.7rem 0; }}
th {{ text-align:left; font-weight:680; font-size:.72rem; letter-spacing:.05em;
      text-transform:uppercase; color:{INK2}; padding:8px 10px;
      border-bottom:1px solid {LINE}; white-space:nowrap; }}
td {{ padding:7px 10px; border-bottom:1px solid {LINE}; color:{INK2}; }}
tbody tr:hover td {{ background:{SURFACE}; }}
.scroll {{ overflow-x:auto; }}

figure {{ margin:1rem 0; }}
figure img {{ width:100%; border-radius:10px; border:1px solid {LINE};
              display:block; }}
figcaption {{ font-size:.80rem; color:{INK2}; margin-top:7px; }}

footer {{ margin-top:3.5rem; padding-top:18px; border-top:1px solid {LINE};
          font-size:.80rem; color:{INK2}; }}
@media print {{
  .wrap {{ max-width:none; padding:0; }}
  h2 {{ break-after:avoid; }}
  .tile, .alert, figure {{ break-inside:avoid; }}
}}
"""


def _tile(label: str, value: str, unit: str = "", detail: str = "") -> str:
    u = f'<span class="u">{_esc(unit)}</span>' if unit else ""
    d = f'<div class="d">{detail}</div>' if detail else ""
    return (f'<div class="tile"><div class="l">{_esc(label)}</div>'
            f'<div class="v">{_esc(value)}{u}</div>{d}</div>')


def build(res, alerts: list, forecasts: list, scale_verified: bool) -> str:
    """Assemble the report for an analysed video clip."""
    df: pd.DataFrame = res.metrics
    fig = _Figs()
    est = "" if scale_verified else "~"
    now = datetime.now().strftime("%d %B %Y, %H:%M")

    over_cap = float(df.los.isin(["Heavy", "Jam"]).mean() * 100) if len(df) else 0.0
    mean_ci = float(df.congestion_index.mean()) if len(df) else 0.0
    sat = int(df.saturated.sum()) if "saturated" in df else 0
    worst = df.loc[df.congestion_index.idxmax()] if len(df) else None

    parts: list[str] = []

    # ---- header ----
    parts.append(f"""
<header>
  <div class="eyebrow">Surat Traffic Intelligence · PS-08</div>
  <h1>{_esc(cfg.camera_name(res.session))}</h1>
  <p class="sub">{_esc(res.media.path.name)} — {_esc(res.media.label)} ·
     {len(df):,} measurement windows over {res.seconds_covered:.0f}s of footage ·
     analysed {_esc(now)}</p>
  <p>{_chip(T.los_of(mean_ci), LOS_COLOR.get(T.los_of(mean_ci), MUTED))}
     {_chip('Scale measured' if scale_verified else 'Scale estimated',
            STATUS['good'] if scale_verified else STATUS['warning'])}
     {_chip(res.calib.mode + ' mode', ACCENT)}</p>
</header>""")

    # ---- headline ----
    parts.append(f"""
<h2>Headline</h2>
<div class="hero">{over_cap:.0f}<span class="hero-u">% of time at or beyond capacity</span></div>
<p>{'Worst moment: <b>' + _esc(worst.zone_label) + '</b> at an index of '
    + f'{worst.congestion_index:.2f}, {worst.window_start_s:.0f}s in.'
    if worst is not None else ''}</p>
<div class="tiles">
  {_tile("Mean congestion", f"{mean_ci:.2f}", "", T.los_of(mean_ci) + " overall")}
  {_tile("Zones measured", str(df.zone_id.nunique()) if len(df) else "0", "",
         f"{res.calib.mode} mode")}
  {_tile("Vehicles detected", f"{sum(res.group_counts.values()):,}", "",
         "across all processed frames")}
  {_tile("Saturated windows", f"{sat:,}", "",
         f"{sat / max(1, len(df)) * 100:.0f}% — where counting stops being reliable")}
</div>""")

    # ---- what this means ----
    parts.append(f"""
<h2>How to read this</h2>
<div class="callout" style="--a:{ACCENT}">
  <div class="t">Two measurement paths</div>
  Density, speed, queue and flow come from detecting and tracking individual
  vehicles, and are valid while vehicles stay separable. <b>Structural
  occupancy</b> comes from image texture instead and keeps working when they do
  not — in the densest scenes an object detector returns <i>fewer</i> boxes, not
  more, so a count-based index would call the most jammed street the emptiest.
  Windows where that happens are flagged <b>saturated</b> and occupancy governs.
</div>
<div class="callout" style="--a:{STATUS['warning'] if not scale_verified else STATUS['good']}">
  <div class="t">{'Metric scale not pinned' if not scale_verified else 'Metric scale measured'}</div>
  {"Nothing in this footage fixes pixels to metres, so every figure in km/h, "
   "PCU/km/lane or PCU/hr is marked <b>~</b> and carries an unknown multiplier "
   "— read them as relative to each other, not as absolute values. The "
   "congestion index, the level-of-service bands and the saturation flags are "
   "scale-free and are unaffected."
   if not scale_verified else
   "A real-world width was supplied for this camera, so distances, speeds and "
   "PCU densities are measured values rather than estimates."}
</div>""")

    # ---- scene ----
    if res.preview is not None:
        parts.append("<h2>Scene</h2>")
        parts.append(_img_tag(res.preview,
                              "A processed frame — measurement zones shaded, "
                              "detections boxed and coloured by vehicle class"))

    if res.group_counts:
        mix = pd.DataFrame([{"Class group": g, "Detections": n,
                             "Share": f"{n / max(1, sum(res.group_counts.values())) * 100:.1f}%"}
                            for g, n in sorted(res.group_counts.items(),
                                               key=lambda kv: -kv[1])])
        parts.append("<h3>What was on the road</h3>")
        parts.append('<div class="scroll">' + _table(mix) + "</div>")
        parts.append('<p class="muted">Classes come from YOLO11-S fine-tuned on '
                     'UVH-26 (26,646 annotated frames of Indian Safe-City CCTV). '
                     'Three-wheelers are a first-class class; a COCO-trained '
                     'model has none and files them as cars, which corrupts '
                     'every PCU figure downstream.</p>')

    # ---- charts ----
    parts.append("<h2>Level of service over time</h2>")
    parts.append(fig(C.zone_los_timeline(df, res.session)))

    prof = None
    try:
        import forecast as fc
        prof = fc.time_profile(df, res.session, bin_s=10.0)
    except Exception:
        prof = None
    if prof is not None and not prof.empty:
        parts.append("<h2>Congestion trend</h2>")
        parts.append(fig(C.zone_trend(prof, "congestion_index")))

    parts.append("<h2>Counting against occupancy</h2>")
    parts.append(fig(C.count_vs_occupancy(df, res.session)))
    parts.append('<p class="muted">Two panels sharing one time axis, never two '
                 'y-scales: the measures have unrelated scales and overlaying '
                 'them would imply a fixed relationship between counts and '
                 'occupancy that is exactly what fails in dense traffic.</p>')

    # ---- alerts ----
    parts.append(f"<h2>Bottlenecks — {len(alerts)} raised</h2>")
    if not alerts:
        parts.append("<p>No sustained bottleneck pattern was found. An alert "
                     "only fires after a condition holds for at least three "
                     "consecutive windows, so a single dense moment does not "
                     "trigger one.</p>")
    else:
        for a in alerts:
            col = STATUS.get(T.SEVERITY_STATUS.get(a.severity) or "", ACCENT)
            parts.append(f"""
<div class="alert" style="--s:{col}">
  {_chip(a.severity.upper(), col)}
  <span class="m">&nbsp;{a.start_s:.0f}–{a.end_s:.0f}s ({a.duration_s:.0f}s)</span>
  <div class="h">{_esc(a.headline)}</div>
  <div class="b"><b>Evidence</b> — {_esc(a.evidence)}</div>
  <div class="b"><b>Recommendation</b> — {_esc(a.recommendation)}</div>
</div>""")

    # ---- forecast ----
    if forecasts:
        reliable = [f for f in forecasts if f.reliable]
        parts.append("<h2>Short-horizon outlook</h2>")
        parts.append(fig(C.forecast_chart(forecasts, 60.0)))
        parts.append(
            f"<p>{len(reliable)} of {len(forecasts)} zones show a trend that "
            f"beats their own noise over the next 60 seconds. The rest are "
            f"reported as steady rather than given a direction — a few minutes "
            f"of footage cannot support more than that.</p>")
        parts.append('<div class="scroll">' + _table(pd.DataFrame([{
            "Zone": f.zone_label, "Now": f.current, "Forecast": f.predicted,
            "Low": f.lower, "High": f.upper, "Direction": f.direction,
            "Reliable": "yes" if f.reliable else "no",
        } for f in forecasts])) + "</div>")

    # ---- data ----
    parts.append("<h2>Measurements by zone</h2>")
    per_zone = df.groupby("zone_label").agg(
        windows=("congestion_index", "size"),
        mean_congestion=("congestion_index", "mean"),
        peak_congestion=("congestion_index", "max"),
        occupancy=("structural_occupancy", "mean"),
        detections=("n_detections_mean", "mean"),
        pcu=("pcu_mean", "mean"),
        density=("density_pcu_per_km_lane", "mean"),
        speed=("mean_speed_kmph", "mean"),
        flow=("flow_pcu_per_hr", "mean"),
        saturated=("saturated", "sum"),
    ).round(2).reset_index()
    per_zone.columns = ["Zone", "Windows", "Mean congestion", "Peak",
                        "Occupancy", "Detections/window", "PCU/window",
                        f"{est}Density (PCU/km/lane)", f"{est}Speed (km/h)",
                        f"{est}Flow (PCU/hr)", "Saturated windows"]
    parts.append('<div class="scroll">' + _table(per_zone) + "</div>")

    # ---- method ----
    parts.append(f"""
<h2>Method</h2>
<p><b>Detection</b> — YOLO11-S with UVH-26 weights (fine-tuned on Indian traffic
CCTV, 14 India-specific classes), tracked with ByteTrack. Each detection is
assigned to a zone by its bottom-centre ground-contact point, not its box
centre, so a tall vehicle is not credited to the lane behind it.</p>
<p><b>Density</b> — in Passenger Car Units per <b>IRC:106-1990</b>: two-wheeler
0.5, car 1.0, auto-rickshaw 1.2, LCV 1.4, bus/truck 2.2. Raw vehicle counts are
not comparable in Indian mixed traffic.</p>
<p><b>Flow</b> — from the fundamental relation <b>q = k·v</b>, using measured
density and speed. Counting tracker IDs is unreliable: trackers reissue IDs
freely in dense traffic and the churn reads as arrivals.</p>
<p><b>Occupancy</b> — Canny edge density inside each zone, normalised against
absolute bounds measured across the whole camera set, so an occupancy of 0.8
means the same amount of stuff on the ground wherever it appears.</p>
<p><b>Zones</b> — {_esc(res.calib.notes or 'traced for this camera')}</p>
<p><b>Level of service</b> — bands derived from traffic-flow theory rather than
chosen by eye: jam density ≈ 130 PCU/km/lane, capacity ≈ 60 (1,800 PCU/hr at
30 km/h), free flow below 20.</p>""")

    parts.append(f"""
<footer>
  Generated by Surat Traffic Intelligence — E-Rakshak Hackathon 2026, PS-08
  “Data-Driven Traffic Optimization &amp; Adaptive Infrastructure
  Intelligence”. Source clip: {_esc(res.media.path.name)}.
  {len(df):,} windows · {res.frames_processed:,} frames processed in
  {res.elapsed_s:.0f}s. Figures marked ~ depend on a metric scale that
  {'has been measured for this camera' if scale_verified else 'has not been pinned'}.
</footer>""")

    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Traffic report — {_esc(cfg.camera_name(res.session))}</title>"
            f"<style>{_CSS}</style></head><body><div class=\"wrap\">"
            + "".join(parts) + "</div></body></html>")
