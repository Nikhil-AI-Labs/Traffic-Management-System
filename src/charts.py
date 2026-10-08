"""Charts for the dashboard.

Every figure here picks its form from the data's job before it picks a colour:
magnitude gets a bar or a single-hue ramp, identity gets the categorical slots,
state gets the reserved status palette, and a single headline number gets a
stat tile rather than a one-bar chart.

Two rules are load-bearing and worth stating outright:

  * **No dual-axis charts.** Where two measures of different scale need to be
    read together - detection counts against occupancy, the whole point of
    `count_vs_occupancy` - they go on stacked panels sharing one time axis, not
    on two y-scales. Two arbitrary scales on one plot invent a correlation the
    data does not contain, which is exactly the claim this project exists to
    disprove.
  * **Colour never carries a meaning alone.** Level of service and alert
    severity always ship their name in text; every chart has a table view
    beside it.

Shared mark specs: 2px lines, >=8px markers ringed in the surface colour, a 2px
surface gap between touching fills, hairline solid gridlines, and direct labels
used sparingly - on the endpoint, the extreme, or the one series that matters.
"""

from __future__ import annotations

import ast

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import config as cfg
import theme as T


def short_name(camera_id: str) -> str:
    """Site name without the direction suffix - fits an axis tick."""
    return cfg.camera_name(camera_id).split("-")[0].strip()


def _ring(width: int = 2) -> dict:
    return dict(width=width, color=T.P()["surface"])


def _missing(df, *cols: str) -> bool:
    """True when `df` cannot feed a chart: absent, empty, or lacking columns.

    A fresh install has no metrics at all, so every chart has to be able to
    say "nothing to plot" rather than raise a KeyError on a column-less frame.
    """
    return df is None or len(df) == 0 or not set(cols) <= set(df.columns)


def _blank(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**T.layout(height=140, legend=False))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.add_annotation(text=msg, showarrow=False, xref="paper", yref="paper",
                       x=0.5, y=0.5, font=dict(color=T.P()["muted"], size=12))
    return fig


# --------------------------------------------------------------------------
# Level-of-service timelines
# --------------------------------------------------------------------------

def _runs(times: np.ndarray, ends: np.ndarray,
          states: list[str]) -> list[tuple[float, float, str]]:
    """Collapse a per-window state series into contiguous runs.

    2,454 windows drawn as 2,454 bars is both slow and unreadable; the eye
    wants the blocks, not the samples.
    """
    out: list[tuple[float, float, str]] = []
    if not len(states):
        return out
    s0, cur = times[0], states[0]
    for i in range(1, len(states)):
        if states[i] != cur:
            out.append((float(s0), float(ends[i - 1]), cur))
            s0, cur = times[i], states[i]
    out.append((float(s0), float(ends[-1]), cur))
    return out


def _los_band(rows: list[tuple[str, list[tuple[float, float, str]]]],
              height: int, xtitle: str = "Seconds into clip") -> go.Figure:
    """Horizontal state bands, one row per label."""
    p = T.P()
    fig = go.Figure()
    labels = [r[0] for r in rows]
    seen: set[str] = set()

    for los in T.LOS_ORDER:
        xs, ys, bases, texts = [], [], [], []
        for label, runs in rows:
            for a, b, st in runs:
                if st != los:
                    continue
                xs.append(b - a)
                ys.append(label)
                bases.append(a)
                texts.append(f"{label}<br>{los} · {a:.0f}–{b:.0f}s "
                             f"({b - a:.0f}s)")
        if not xs:
            continue
        seen.add(los)
        fig.add_trace(go.Bar(
            x=xs, y=ys, base=bases, orientation="h", name=los,
            marker=dict(color=T.los_color(los), line=_ring(2)),
            hovertemplate="%{customdata}<extra></extra>", customdata=texts,
        ))

    fig.update_layout(**T.layout(height=height, legend=len(seen) >= 2,
                                 barmode="stack", bargap=0.42,
                                 margin=dict(l=8, r=16, t=34, b=34)))
    fig.update_xaxes(title=xtitle, showgrid=True, gridcolor=p["grid"])
    fig.update_yaxes(showgrid=False, autorange="reversed",
                     categoryorder="array", categoryarray=labels,
                     tickfont=dict(color=p["text2"], size=11.5))
    return fig


def network_los_timeline(df: pd.DataFrame, order: list[str]) -> go.Figure:
    """One band per site: the worst zone's level of service, second by second.

    Worst-zone rather than mean-zone deliberately - a junction with one lane at
    a standstill is not "moderate" because the other two are clear.
    """
    rows = []
    for cam in order:
        sub = df[df.camera_id == cam]
        if sub.empty:
            continue
        w = (sub.groupby("window_start_s")
                .agg(ci=("congestion_index", "max"),
                     end=("window_end_s", "max"))
                .reset_index().sort_values("window_start_s"))
        states = [T.los_of(float(c)) for c in w.ci]
        rows.append((short_name(cam),
                     _runs(w.window_start_s.to_numpy(), w.end.to_numpy(), states)))
    if not rows:
        return _blank("No metrics to plot.")
    return _los_band(rows, height=64 + 34 * len(rows))


def zone_los_timeline(df: pd.DataFrame, camera_id: str) -> go.Figure:
    """The same band, per zone, for one camera."""
    if _missing(df, "camera_id", "zone_id", "congestion_index"):
        return _blank("No metrics for this camera.")
    sub = df[df.camera_id == camera_id]
    if sub.empty:
        return _blank("No metrics for this camera.")
    rows = []
    for zid, z in sub.groupby("zone_id"):
        z = z.sort_values("window_start_s")
        states = [T.los_of(float(c)) for c in z.congestion_index]
        rows.append((str(z.zone_label.iloc[0])[:26],
                     _runs(z.window_start_s.to_numpy(),
                           z.window_end_s.to_numpy(), states)))
    rows.sort(key=lambda r: r[0])
    return _los_band(rows, height=70 + 36 * len(rows))


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def congestion_ranking(summary: pd.DataFrame) -> go.Figure:
    """Mean congestion per site, with the peak marked.

    One measure, one hue: the bars are all slot 1 rather than shaded by value.
    Colouring a bar darker because it is longer spends the only free channel on
    information the length already carries.
    """
    if _missing(summary, "camera_id", "mean_congestion", "peak_congestion"):
        return _blank("No sites to rank.")
    p = T.P()
    s = summary.sort_values("mean_congestion")
    names = [short_name(c) for c in s.camera_id]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=s.mean_congestion, y=names, orientation="h", name="Mean over clip",
        marker=dict(color=T.series(0), cornerradius=4),
        text=[f"{v:.2f}" for v in s.mean_congestion],
        textposition="outside", cliponaxis=False,
        textfont=dict(color=p["text2"], size=11),
        hovertemplate="%{y}<br>mean %{x:.3f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=s.peak_congestion, y=names, mode="markers", name="Peak window",
        marker=dict(symbol="line-ns", size=13,
                    line=dict(color=p["text2"], width=2)),
        hovertemplate="%{y}<br>peak %{x:.3f}<extra></extra>",
    ))

    fig.update_layout(**T.layout(height=58 + 38 * len(s), bargap=0.5,
                                 margin=dict(l=8, r=48, t=34, b=34)))
    fig.update_xaxes(title="Congestion index (0 = clear, 1 = jammed)",
                     range=[0, 1.08], tickformat=".1f")
    fig.update_yaxes(showgrid=False, tickfont=dict(color=p["text2"], size=11.5))
    return fig


# --------------------------------------------------------------------------
# Trends
# --------------------------------------------------------------------------

METRIC_AXIS = {
    "congestion_index": ("Congestion index", ".2f", ""),
    "occupancy": ("Structural occupancy", ".2f", ""),
    "density_pcu_per_km_lane": ("Density (PCU/km/lane)", ".0f", "~"),
    "speed_kmph": ("Mean speed (km/h)", ".0f", "~"),
    "flow_pcu_per_hr": ("Flow (PCU/hr)", ".0f", "~"),
}


def zone_trend(prof: pd.DataFrame, metric: str) -> go.Figure:
    """One line per zone over the clip, direct-labelled at the right edge."""
    p = T.P()
    title, fmt, est = METRIC_AXIS.get(metric, (metric, ".2f", ""))
    if _missing(prof, "bin", "zone_label", metric):
        return _blank(f"{title} is not measured on this camera.")
    pivot = prof.pivot(index="bin", columns="zone_label", values=metric)
    pivot = pivot.dropna(axis=1, how="all")
    if pivot.empty:
        return _blank(f"{title} is not measured on this camera.")

    cols = list(pivot.columns)

    # Direct end-labels only when the series actually separate at the right
    # edge. Nudging converged labels apart detaches them from their lines and
    # reads as noise, so below that gap the legend and tooltip carry it.
    ends = [pivot[c].dropna() for c in cols]
    finals = sorted(float(s.iloc[-1]) for s in ends if len(s))
    vals = pivot.to_numpy(dtype=float)
    span = float(np.nanmax(vals) - np.nanmin(vals))
    gap = min((b - a for a, b in zip(finals, finals[1:])), default=span)
    label_ends = bool(span > 0 and gap > 0.11 * span)

    fig = go.Figure()
    for i, col in enumerate(cols):
        c = T.series(i)
        y = pivot[col]
        fig.add_trace(go.Scatter(
            x=pivot.index, y=y, mode="lines", name=str(col),
            line=dict(color=c, width=2, shape="spline", smoothing=0.4),
            hovertemplate=f"%{{y:{fmt}}}<extra>{col}</extra>",
        ))
        last = y.dropna()
        if len(last):
            fig.add_trace(go.Scatter(
                x=[last.index[-1]], y=[last.iloc[-1]],
                mode="markers+text" if label_ends else "markers",
                text=[format(float(last.iloc[-1]), fmt)] if label_ends else None,
                textposition="middle right", cliponaxis=False,
                textfont=dict(color=p["text2"], size=10.5),
                marker=dict(color=c, size=9, line=_ring(2)),
                showlegend=False, hoverinfo="skip",
            ))

    xspan = float(pivot.index.max() - pivot.index.min()) or 1.0
    fig.update_layout(**T.layout(
        height=310, legend=len(cols) >= 2, hovermode="x unified",
        margin=dict(l=8, r=16, t=34, b=38)))
    fig.update_xaxes(title="Seconds into clip", showspikes=True,
                     spikemode="across", spikethickness=1,
                     spikecolor=p["axis"], spikedash="solid",
                     range=[pivot.index.min() - xspan * 0.01,
                            pivot.index.max()
                            + xspan * (0.07 if label_ends else 0.03)])
    fig.update_yaxes(title=f"{est}{title}" if est else title,
                     rangemode="tozero", tickformat=fmt)
    return fig


# --------------------------------------------------------------------------
# Fleet composition
# --------------------------------------------------------------------------

def _parse_groups(cell) -> dict:
    if not isinstance(cell, str) or not cell.startswith("{"):
        return {}
    try:
        d = ast.literal_eval(cell)
        return d if isinstance(d, dict) else {}
    except (ValueError, SyntaxError):
        return {}


def fleet_table(df: pd.DataFrame) -> pd.DataFrame:
    """Share of detections by vehicle group, per site."""
    if _missing(df, "camera_id", "counts_by_group"):
        return pd.DataFrame()
    rows = []
    for cam, sub in df.groupby("camera_id"):
        tot: dict[str, float] = {}
        for cell in sub.counts_by_group:
            for k, v in _parse_groups(cell).items():
                tot[k] = tot.get(k, 0.0) + float(v)
        n = sum(tot.values())
        if n <= 0:
            continue
        row = {"camera_id": cam, "Site": short_name(cam)}
        for g in cfg.GROUP_ORDER:
            row[g] = 100.0 * tot.get(g, 0.0) / n
        rows.append(row)
    return pd.DataFrame(rows)


def fleet_mix(mix: pd.DataFrame) -> go.Figure:
    """Stacked composition bar. Six groups is at the categorical soft cap, so
    the legend does the identifying and the segments carry no inline labels -
    a label that would not fit inside its segment is worse than no label."""
    if mix.empty:
        return _blank("No class counts recorded.")
    p = T.P()
    groups = [g for g in cfg.GROUP_ORDER if g in mix.columns
              and float(mix[g].sum()) > 0.5]

    fig = go.Figure()
    for i, g in enumerate(groups):
        fig.add_trace(go.Bar(
            x=mix[g], y=mix["Site"], orientation="h", name=g,
            marker=dict(color=T.series(i), line=_ring(2)),
            hovertemplate=f"%{{y}}<br>{g} %{{x:.1f}}%<extra></extra>",
        ))

    fig.update_layout(**T.layout(height=70 + 36 * len(mix), barmode="stack",
                                 bargap=0.42,
                                 margin=dict(l=8, r=16, t=34, b=34)))
    fig.update_xaxes(title="Share of detected vehicles (%)", range=[0, 100],
                     ticksuffix="%")
    fig.update_yaxes(showgrid=False, tickfont=dict(color=p["text2"], size=11.5))
    return fig


# --------------------------------------------------------------------------
# Traffic-flow theory
# --------------------------------------------------------------------------

def fundamental_diagram(df: pd.DataFrame, camera_id: str) -> go.Figure:
    """Speed against density - the classic speed-density curve.

    Its shape is the check that the measurement chain is behaving: speed must
    fall as density rises, and the points must thin out past capacity density.
    A cloud with no downward slope would mean the speed or the density figure
    is wrong, which is how the earlier scale bug was caught.
    """
    p = T.P()
    if _missing(df, "camera_id", "zone_kind", "density_pcu_per_km_lane",
                "mean_speed_kmph"):
        return _blank("No metrics for this camera.")
    sub = df[(df.camera_id == camera_id) & (df.zone_kind == "lane")].dropna(
        subset=["density_pcu_per_km_lane", "mean_speed_kmph"])
    if len(sub) < 8:
        return _blank("Speed-density needs lane zones with a metric scale; "
                      "this camera reports occupancy instead.")

    fig = go.Figure()
    for los in T.LOS_ORDER:
        s = sub[sub.los == los]
        if s.empty:
            continue
        fig.add_trace(go.Scatter(
            x=s.density_pcu_per_km_lane, y=s.mean_speed_kmph, mode="markers",
            name=los,
            marker=dict(color=T.los_color(los), size=9, line=_ring(2),
                        opacity=0.9),
            customdata=np.stack([s.zone_label, s.window_start_s], axis=-1),
            hovertemplate=("%{customdata[0]} · t=%{customdata[1]:.0f}s<br>"
                           "~%{x:.0f} PCU/km/lane · ~%{y:.0f} km/h"
                           "<extra></extra>"),
        ))

    cap = cfg.CAPACITY_DENSITY_PCU_PER_KM_LANE
    if float(sub.density_pcu_per_km_lane.max()) > cap * 0.55:
        fig.add_vline(x=cap, line=dict(color=p["axis"], width=1),
                      annotation_text=f"capacity density ~{cap:.0f}",
                      annotation_position="top right",
                      annotation_font=dict(color=p["muted"], size=10))

    fig.update_layout(**T.layout(height=330, hovermode="closest",
                                 margin=dict(l=8, r=16, t=34, b=38)))
    fig.update_xaxes(title="~Density (PCU/km/lane)", rangemode="tozero")
    fig.update_yaxes(title="~Mean speed (km/h)", rangemode="tozero")
    return fig


# --------------------------------------------------------------------------
# The occlusion finding
# --------------------------------------------------------------------------

def occlusion_paradox(df: pd.DataFrame) -> go.Figure:
    """Detections against measured congestion, one point per site.

    Emphasis rather than categorical: the story is a single point, so that
    point takes the accent hue and the rest recede to grey. Mahidharpura sits
    top-left - the most congested street in the set, and the one where the
    detector finds the fewest vehicles.
    """
    p = T.P()
    if _missing(df, "camera_id", "n_detections_mean", "congestion_index"):
        return _blank("No metrics to plot.")
    g = df.groupby("camera_id").agg(
        det=("n_detections_mean", "mean"),
        ci=("congestion_index", "mean"),
    ).reset_index()
    if g.empty:
        return _blank("No metrics to plot.")

    worst = g.loc[g.ci.idxmax(), "camera_id"]
    g["name"] = g.camera_id.map(short_name)
    rest, hero = g[g.camera_id != worst], g[g.camera_id == worst]

    fig = go.Figure()
    # Points on the right would push their labels off the plot; flip those to
    # the left rather than let a site name get clipped mid-word.
    dmax = float(g.det.max())
    side = ["middle left" if d > dmax * 0.62 else "middle right"
            for d in rest.det]

    fig.add_trace(go.Scatter(
        x=rest.det, y=rest.ci, mode="markers+text", name="Other sites",
        text=rest.name, textposition=side, cliponaxis=False,
        textfont=dict(color=p["text2"], size=11),
        marker=dict(color=p["deemphasis"], size=11, line=_ring(2)),
        hovertemplate=("%{text}<br>%{x:.1f} detections/window · "
                       "congestion %{y:.2f}<extra></extra>"),
    ))
    fig.add_trace(go.Scatter(
        x=hero.det, y=hero.ci, mode="markers+text",
        name="Worst-congested site",
        text=hero.name, textposition="middle right", cliponaxis=False,
        textfont=dict(color=p["text"], size=11.5),
        marker=dict(color=T.series(0), size=15, line=_ring(2)),
        hovertemplate=("%{text}<br>%{x:.1f} detections/window · "
                       "congestion %{y:.2f}<extra></extra>"),
    ))

    fig.update_layout(**T.layout(height=340, hovermode="closest",
                                 margin=dict(l=8, r=16, t=34, b=38)))
    fig.update_xaxes(title="Mean detections per measurement window",
                     range=[-dmax * 0.06, dmax * 1.16])
    fig.update_yaxes(title="Congestion index (occupancy-based)",
                     range=[0, 1.24], tickformat=".1f")
    fig.add_annotation(
        x=float(hero.det.iloc[0]), y=float(hero.ci.iloc[0]),
        ax=104, ay=-34, text="fewest vehicles found,<br>worst gridlock measured",
        showarrow=True, arrowhead=0, arrowwidth=1, arrowcolor=p["axis"],
        font=dict(color=p["text2"], size=10.5), align="left",
    )
    return fig


def count_vs_occupancy(df: pd.DataFrame, camera_id: str) -> go.Figure:
    """Counts and occupancy over time, on stacked panels sharing one x-axis.

    Two panels rather than two y-axes: the measures have unrelated scales, and
    a shared secondary axis would imply a fixed relationship between them that
    is precisely what breaks down here.
    """
    p = T.P()
    if _missing(df, "camera_id", "window_start_s", "n_detections_mean",
                "structural_occupancy"):
        return _blank("No metrics for this camera.")
    sub = df[df.camera_id == camera_id]
    if sub.empty:
        return _blank("No metrics for this camera.")
    w = (sub.groupby("window_start_s")
            .agg(det=("n_detections_mean", "sum"),
                 occ=("structural_occupancy", "max"))
            .reset_index().sort_values("window_start_s"))

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.13,
                        subplot_titles=("Vehicles detected per window",
                                        "Structural occupancy of the scene"))
    fig.add_trace(go.Scatter(
        x=w.window_start_s, y=w.det, mode="lines", name="Detections",
        line=dict(color=T.series(0), width=2),
        fill="tozeroy", fillcolor=T.tint(T.series(0), 0.10),
        hovertemplate="%{y:.1f} detections<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=w.window_start_s, y=w.occ, mode="lines", name="Occupancy",
        line=dict(color=T.series(1), width=2),
        fill="tozeroy", fillcolor=T.tint(T.series(1), 0.10),
        hovertemplate="occupancy %{y:.2f}<extra></extra>"), row=2, col=1)

    fig.update_layout(**T.layout(height=340, legend=False,
                                 hovermode="x unified",
                                 margin=dict(l=8, r=16, t=28, b=38)))
    fig.update_xaxes(showgrid=True, gridcolor=p["grid"], zeroline=False,
                     linecolor=p["axis"], ticks="outside",
                     tickcolor=p["axis"], ticklen=4,
                     tickfont=dict(color=p["muted"], size=11))
    fig.update_yaxes(showgrid=True, gridcolor=p["grid"], zeroline=False,
                     linecolor=p["axis"], ticks="outside",
                     tickcolor=p["axis"], ticklen=4,
                     tickfont=dict(color=p["muted"], size=11),
                     rangemode="tozero")
    fig.update_xaxes(title="Seconds into clip", row=2, col=1)
    fig.update_yaxes(range=[0, 1.05], row=2, col=1)
    for a in fig.layout.annotations:
        a.font = dict(family=T.FONT, size=11.5, color=p["text2"])
        a.x, a.xanchor = 0, "left"
    return fig


# --------------------------------------------------------------------------
# Signal timing
# --------------------------------------------------------------------------

# Ordinal ramp: the parts of a cycle are ordered stages, not distinct
# identities, so they take steps of one hue rather than separate categorical
# slots. Waiting time is de-emphasis grey - it is the absence of a stage.
def _cycle_steps() -> dict:
    seq = T.P()["seq"]
    return {"Green": seq[9], "Amber": seq[6], "All-red": seq[3],
            "Waiting (other phases)": T.P()["deemphasis"]}


def cycle_composition(base, opt) -> go.Figure:
    """How each plan spends its cycle, per approach.

    The cycle shortening and the green redistributing are the whole
    recommendation, so they are shown as lengths on a common time axis rather
    than asserted in a table.
    """
    p = T.P()
    steps = _cycle_steps()
    rows: list[tuple[str, object, object]] = []
    for tag, plan in (("Optimised", opt), ("Baseline", base)):
        for ph in plan.phases:
            rows.append((f"{tag} · {ph.label.split('-')[0].strip()[:22]}",
                         ph, plan))
    if not rows:
        return _blank("No phase plan to draw.")

    labels = [r[0] for r in rows]
    fig = go.Figure()
    for stage, colr in steps.items():
        xs = []
        for _lab, ph, plan in rows:
            if stage == "Green":
                xs.append(ph.green_s)
            elif stage == "Amber":
                xs.append(ph.amber_s)
            elif stage == "All-red":
                xs.append(ph.all_red_s)
            else:
                xs.append(max(0.0, plan.cycle_s - ph.total_s))
        fig.add_trace(go.Bar(
            x=xs, y=labels, orientation="h", name=stage,
            marker=dict(color=colr, line=_ring(2)),
            hovertemplate=f"%{{y}}<br>{stage} %{{x:.1f}}s<extra></extra>",
        ))

    # The one number the chart is about: green seconds, on the green segment.
    # Ink chosen against that segment's own luminance - the ordinal ramp runs
    # dark-on-light in one mode and light-on-dark in the other.
    green_ink = T.on_fill(steps["Green"])
    for i, (_lab, ph, _plan) in enumerate(rows):
        fig.add_annotation(
            x=ph.green_s / 2, y=labels[i], text=f"{ph.green_s:.0f}s green",
            showarrow=False, font=dict(color=green_ink, size=10.5),
        )

    fig.update_layout(**T.layout(height=76 + 40 * len(rows), barmode="stack",
                                 bargap=0.4,
                                 margin=dict(l=8, r=16, t=34, b=34)))
    fig.update_xaxes(title="Seconds of cycle")
    fig.update_yaxes(showgrid=False, autorange="reversed",
                     tickfont=dict(color=p["text2"], size=11.5))
    return fig


def saturation_dumbbell(base, opt) -> go.Figure:
    """Degree of saturation before and after, per approach.

    Anything at or above 1.0 cannot clear within the cycle. Webster's optimum
    equalises this across approaches, so the two dots landing on the same value
    is the visual form of the optimality condition.
    """
    p = T.P()
    b = {ph.approach_id: ph for ph in base.phases}
    pairs = [(ph.label.split("-")[0].strip()[:24],
              b[ph.approach_id].degree_of_saturation, ph.degree_of_saturation)
             for ph in opt.phases if ph.approach_id in b]
    if not pairs:
        return _blank("No matching approaches to compare.")

    labels = [x[0] for x in pairs]
    fig = go.Figure()
    for lab, x0, x1 in pairs:
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[lab, lab], mode="lines",
            line=dict(color=p["deemphasis"], width=2),
            showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=[x[1] for x in pairs], y=labels, mode="markers+text",
        name="Fixed timer",
        text=[f"{x[1]:.2f}" for x in pairs], textposition="bottom center",
        textfont=dict(color=p["muted"], size=10.5), cliponaxis=False,
        marker=dict(color=T.P()["seq"][4], size=13, line=_ring(2)),
        hovertemplate="%{y}<br>x = %{x:.2f} before<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=[x[2] for x in pairs], y=labels, mode="markers+text",
        name="Adaptive plan",
        text=[f"{x[2]:.2f}" for x in pairs], textposition="top center",
        textfont=dict(color=p["text2"], size=11), cliponaxis=False,
        marker=dict(color=T.P()["seq"][10], size=13, line=_ring(2)),
        hovertemplate="%{y}<br>x = %{x:.2f} after<extra></extra>"))

    fig.add_vline(x=1.0, line=dict(color=T.status("critical"), width=1),
                  annotation_text="capacity",
                  annotation_position="top",
                  annotation_font=dict(color=p["muted"], size=10))

    # A dot plot encodes by position, not by length, so it carries no
    # obligation to start at zero - and the whole comparison here lives in a
    # narrow band just under capacity. Windowing on the data keeps the before
    # and after dots apart instead of stacking them against the right edge.
    vals = [v for x in pairs for v in (x[1], x[2])]
    lo = max(0.0, min(vals) - 0.14)
    hi = max(1.08, max(vals) + 0.10)
    fig.update_layout(**T.layout(height=104 + 52 * len(pairs),
                                 margin=dict(l=8, r=16, t=52, b=44)))
    fig.update_xaxes(title="Degree of saturation  x = flow / capacity",
                     range=[lo, hi], tickformat=".2f")
    fig.update_yaxes(showgrid=False, tickfont=dict(color=p["text2"], size=11.5))
    return fig


def delay_curve(curve: pd.DataFrame, webster_c: float) -> go.Figure:
    """Delay against cycle length, with the empirical minimum marked.

    This is the optimiser's own audit: Webster's closed-form cycle should land
    on the minimum of the swept curve. When an earlier version of the delay
    calculation was wrong, this curve had no minimum at all - which is what
    exposed the bug.
    """
    p = T.P()
    if curve is None or curve.empty or len(curve.columns) == 0:
        return _blank("No delay curve to plot.")
    fig = go.Figure()
    for i, col in enumerate(curve.columns):
        fig.add_trace(go.Scatter(
            x=curve.index, y=curve[col], mode="lines", name=str(col),
            line=dict(color=T.series(i), width=2),
            hovertemplate=f"%{{y:.1f}} s/PCU<extra>{col}</extra>"))

    key = curve.columns[-1]
    best_c = float(curve[key].idxmin())
    best_d = float(curve[key].min())
    fig.add_trace(go.Scatter(
        x=[best_c], y=[best_d], mode="markers", showlegend=False,
        marker=dict(color=T.series(len(curve.columns) - 1), size=12,
                    line=_ring(2)),
        hovertemplate=f"minimum · {best_c:.0f}s<extra></extra>"))
    fig.add_annotation(
        x=best_c, y=best_d, ax=0, ay=-38, showarrow=True, arrowhead=0,
        arrowwidth=1, arrowcolor=p["axis"],
        text=f"swept minimum {best_c:.0f}s",
        font=dict(color=p["text2"], size=10.5))
    fig.add_vline(x=webster_c, line=dict(color=p["axis"], width=1),
                  annotation_text=f"Webster {webster_c:.0f}s",
                  annotation_position="bottom right",
                  annotation_font=dict(color=p["muted"], size=10))

    fig.update_layout(**T.layout(height=320, hovermode="x unified",
                                 margin=dict(l=8, r=16, t=34, b=38)))
    fig.update_xaxes(title="Cycle length (s)", showspikes=True,
                     spikemode="across", spikethickness=1,
                     spikecolor=p["axis"], spikedash="solid")
    fig.update_yaxes(title="Mean delay (s per PCU)", rangemode="tozero")
    return fig


def growth_headroom(curve: pd.DataFrame, practical_x: float = 0.90) -> go.Figure:
    """Worst degree of saturation as demand grows, fixed timer vs adaptive.

    The adaptive line is Webster's delay-optimal plan. Near capacity its cycle
    is capped at the maximum, where proportional splits give the lowest worst
    x any timing can reach - so where it crosses 1.0, retiming stops being an
    answer. (At low demand Webster's shorter cycle shows a higher x than a long
    fixed one; it is minimising delay, not x.) Both thresholds are drawn as
    reference lines and named in text, so the chart reads without colour.
    """
    if _missing(curve, "growth_pct", "x_adaptive", "x_fixed"):
        return _blank("No junction demand to project.")
    p = T.P()
    fig = go.Figure()
    for i, (col, name) in enumerate((("x_fixed", "Fixed 120 s timer"),
                                     ("x_adaptive", "Adaptive (Webster)"))):
        fig.add_trace(go.Scatter(
            x=curve.growth_pct, y=curve[col], mode="lines", name=name,
            line=dict(color=T.series(i), width=2),
            hovertemplate=f"x = %{{y:.3f}}<extra>{name}</extra>"))
    for level, label, status in ((1.0, "capacity (x = 1)", "critical"),
                                 (practical_x, f"practical limit (x = {practical_x:.2f})",
                                  "warning")):
        fig.add_hline(y=level, line=dict(color=T.STATUS[status], width=1, dash="dot"),
                      annotation_text=label, annotation_position="top left",
                      annotation_font=dict(color=p["text2"], size=10.5))
    fig.add_vline(x=0, line=dict(color=p["axis"], width=1),
                  annotation_text="today", annotation_position="bottom right",
                  annotation_font=dict(color=p["muted"], size=10))
    fig.update_layout(**T.layout(height=320, hovermode="x unified",
                                 margin=dict(l=8, r=16, t=34, b=38)))
    fig.update_xaxes(title="Demand change from today (%)", ticksuffix="%",
                     showspikes=True, spikemode="across", spikethickness=1,
                     spikecolor=p["axis"], spikedash="solid")
    fig.update_yaxes(title="Worst degree of saturation x")
    return fig


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------

def alert_gantt(alerts: list) -> go.Figure:
    """Every sustained alert on a common clip clock, by site.

    Severities are grouped into their own sub-rows rather than overlaid: one
    site can carry a long low-severity episode and several short critical ones
    at once, and overlaying them lets the longest bar hide the worst.
    """
    if not alerts:
        return _blank("No sustained bottlenecks detected.")
    p = T.P()
    sites = sorted({short_name(a.camera_id) for a in alerts})

    fig = go.Figure()
    for sev in ("critical", "warning", "info"):
        rows = [a for a in alerts if a.severity == sev]
        if not rows:
            continue
        fig.add_trace(go.Bar(
            x=[max(a.duration_s, 1.5) for a in rows],
            y=[short_name(a.camera_id) for a in rows],
            base=[a.start_s for a in rows], orientation="h",
            name=sev.capitalize(), offsetgroup=sev,
            marker=dict(color=T.severity_color(sev), line=_ring(1),
                        cornerradius=2),
            customdata=[f"{a.zone_label} · {a.kind.replace('_', ' ')}<br>"
                        f"{a.start_s:.0f}–{a.end_s:.0f}s ({a.duration_s:.0f}s)"
                        for a in rows],
            hovertemplate="%{customdata}<extra></extra>",
        ))

    fig.update_layout(**T.layout(height=76 + 52 * len(sites), barmode="group",
                                 bargap=0.34, bargroupgap=0.06,
                                 margin=dict(l=8, r=16, t=34, b=34)))
    fig.update_xaxes(title="Seconds into clip")
    fig.update_yaxes(showgrid=False, categoryorder="array",
                     categoryarray=sites, autorange="reversed",
                     tickfont=dict(color=p["text2"], size=11.5))
    return fig


def alert_mix(alerts: list) -> go.Figure:
    """How many of each kind of bottleneck. One measure, one hue."""
    if not alerts:
        return _blank("No alerts.")
    p = T.P()
    counts: dict[str, int] = {}
    for a in alerts:
        k = a.kind.replace("_", " ")
        counts[k] = counts.get(k, 0) + 1
    s = pd.Series(counts).sort_values()

    fig = go.Figure(go.Bar(
        x=s.values, y=s.index, orientation="h",
        marker=dict(color=T.series(0), cornerradius=4),
        text=s.values, textposition="outside", cliponaxis=False,
        textfont=dict(color=p["text2"], size=11),
        hovertemplate="%{y}: %{x} alerts<extra></extra>"))
    fig.update_layout(**T.layout(height=56 + 36 * len(s), legend=False,
                                 bargap=0.5,
                                 margin=dict(l=8, r=40, t=10, b=34)))
    fig.update_xaxes(title="Alerts raised",
                     range=[0, float(s.max()) * 1.18], dtick=max(1, int(s.max() // 5)))
    fig.update_yaxes(showgrid=False, tickfont=dict(color=p["text2"], size=11.5))
    return fig


# --------------------------------------------------------------------------
# Forecast
# --------------------------------------------------------------------------

def forecast_chart(forecasts: list, horizon_s: float, top: int = 12) -> go.Figure:
    """Now to forecast per zone, with the prediction interval drawn.

    The band is the point. A single forecast number off a few minutes of
    footage would imply a confidence this data cannot support, so the interval
    is a mark on the chart rather than a footnote.
    """
    if not forecasts:
        return _blank("Not enough history to forecast.")
    p = T.P()
    fs = sorted(forecasts, key=lambda f: -f.predicted)[:top][::-1]

    def _lbl(f) -> str:
        z = f.zone_label if len(f.zone_label) <= 26 else f.zone_label[:25] + "…"
        return f"{short_name(f.camera_id)} · {z}"

    labels = [_lbl(f) for f in fs]

    fig = go.Figure()
    for i, f in enumerate(fs):
        fig.add_trace(go.Scatter(
            x=[f.lower, f.upper], y=[labels[i], labels[i]], mode="lines",
            line=dict(color=T.tint(T.series(0), 0.30), width=7),
            showlegend=False,
            hovertemplate=f"interval {f.lower:.2f}–{f.upper:.2f}<extra></extra>"))
        fig.add_trace(go.Scatter(
            x=[f.current, f.predicted], y=[labels[i], labels[i]], mode="lines",
            line=dict(color=p["deemphasis"], width=2),
            showlegend=False, hoverinfo="skip"))

    fig.add_trace(go.Scatter(
        x=[f.current for f in fs], y=labels, mode="markers", name="Now",
        marker=dict(color=p["deemphasis"], size=11, line=_ring(2)),
        hovertemplate="%{y}<br>now %{x:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=[f.predicted for f in fs], y=labels, mode="markers",
        name=f"In {horizon_s:.0f}s",
        marker=dict(color=[T.series(0) if f.reliable else p["deemphasis"]
                           for f in fs],
                    size=12, line=_ring(2),
                    symbol=["circle" if f.reliable else "circle-open"
                            for f in fs]),
        customdata=[("directional trend" if f.reliable
                     else "within noise — treated as steady") for f in fs],
        hovertemplate="%{y}<br>forecast %{x:.2f}<br>%{customdata}<extra></extra>"))

    fig.update_layout(**T.layout(height=76 + 34 * len(fs),
                                 margin=dict(l=8, r=16, t=34, b=38)))
    fig.update_xaxes(title="Congestion index", range=[0, 1.05],
                     tickformat=".1f")
    fig.update_yaxes(showgrid=False, tickfont=dict(color=p["text2"], size=11))
    return fig


# --------------------------------------------------------------------------
# Signal-controller comparison
# --------------------------------------------------------------------------

def controller_comparison(results: list) -> go.Figure:
    """Delay, queue and throughput per controller, on three small panels.

    Three measures with three different units, so three panels sharing one
    categorical axis rather than one plot with three scales.
    """
    if not results:
        return _blank("Nothing to compare.")
    p = T.P()
    names = [r.controller for r in results]
    panels = [
        ("Mean delay (s/PCU)", [r.mean_delay_s_per_pcu for r in results], ".0f"),
        ("Mean queue (PCU)", [r.mean_total_queue_pcu for r in results], ".1f"),
        ("Throughput (PCU)", [r.throughput_pcu for r in results], ".0f"),
    ]
    fig = make_subplots(rows=1, cols=3, shared_yaxes=True,
                        horizontal_spacing=0.07,
                        subplot_titles=[t for t, _, _ in panels])
    for i, (title, vals, fmt) in enumerate(panels, start=1):
        fig.add_trace(go.Bar(
            x=vals, y=names, orientation="h",
            marker=dict(color=T.series(i - 1), cornerradius=4),
            text=[format(v, fmt) for v in vals],
            textposition="outside", cliponaxis=False,
            textfont=dict(color=p["text2"], size=11),
            hovertemplate=f"%{{y}}<br>{title}: %{{x:{fmt}}}<extra></extra>",
            showlegend=False), row=1, col=i)

    fig.update_layout(**T.layout(height=90 + 52 * len(results), legend=False,
                                 bargap=0.45,
                                 margin=dict(l=8, r=16, t=44, b=34)))
    fig.update_xaxes(showgrid=True, gridcolor=p["grid"], zeroline=False,
                     linecolor=p["axis"], ticks="outside", tickcolor=p["axis"],
                     ticklen=4, tickfont=dict(color=p["text2"], size=10.5),
                     rangemode="tozero")
    fig.update_yaxes(showgrid=False, linecolor=p["axis"],
                     tickfont=dict(color=p["text2"], size=11))
    for a in fig.layout.annotations:
        a.font = dict(family=T.FONT, size=11, color=p["text2"])
    return fig


def queue_history(series: dict) -> go.Figure:
    """Total network queue over time, one line per controller."""
    if not series:
        return _blank("No history recorded.")
    p = T.P()
    fig = go.Figure()
    for i, (name, hist) in enumerate(series.items()):
        if not hist:
            continue
        fig.add_trace(go.Scatter(
            x=[h["t"] for h in hist], y=[h["total_queue"] for h in hist],
            mode="lines", name=name,
            line=dict(color=T.series(i), width=2),
            hovertemplate="%{y:.1f} PCU<extra>" + name + "</extra>"))

    fig.update_layout(**T.layout(height=320, hovermode="x unified",
                                 margin=dict(l=8, r=16, t=34, b=38)))
    fig.update_xaxes(title="Seconds of simulation", showspikes=True,
                     spikemode="across", spikethickness=1,
                     spikecolor=p["axis"], spikedash="solid")
    fig.update_yaxes(title="Total vehicles queued in the network (PCU)",
                     rangemode="tozero")
    return fig


# --------------------------------------------------------------------------
# FLUID — real signalised intersections
# --------------------------------------------------------------------------

def turn_ratio_chart(turns: pd.DataFrame) -> go.Figure:
    """Measured turn split per entry direction, as a composition."""
    if turns is None or turns.empty:
        return _blank("No turn ratios measured.")
    p = T.P()
    piv = (turns.pivot_table(index="entry_direction", columns="exit_direction",
                             values="turn_ratio", aggfunc="sum")
           .fillna(0.0) * 100.0)
    order = [d for d in ("N", "E", "S", "W") if d in piv.columns]

    fig = go.Figure()
    for i, ex in enumerate(order):
        fig.add_trace(go.Bar(
            x=piv[ex], y=[f"from {d}" for d in piv.index], orientation="h",
            name=f"to {ex}", marker=dict(color=T.series(i), line=_ring(2)),
            hovertemplate=f"%{{y}} → {ex}: %{{x:.1f}}%<extra></extra>"))

    fig.update_layout(**T.layout(height=76 + 40 * len(piv), barmode="stack",
                                 bargap=0.42,
                                 margin=dict(l=8, r=16, t=34, b=34)))
    fig.update_xaxes(title="Share of vehicles entering from that direction (%)",
                     range=[0, 100], ticksuffix="%")
    fig.update_yaxes(showgrid=False, tickfont=dict(color=p["text2"], size=11.5))
    return fig


def phase_share_chart(actual: dict, maxpressure: dict) -> go.Figure:
    """How each controller divides green between phases."""
    keys = sorted(set(actual) | set(maxpressure))
    if not keys:
        return _blank("No phase share to compare.")
    p = T.P()
    labels = [str(k)[:34] for k in keys]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[actual.get(k, 0) for k in keys], y=labels, orientation="h",
        name="Real signal", marker=dict(color=T.series(0), cornerradius=4),
        text=[f"{actual.get(k, 0):.0f}%" for k in keys],
        textposition="outside", cliponaxis=False,
        textfont=dict(color=p["text2"], size=11),
        hovertemplate="%{y}<br>real signal %{x:.1f}%<extra></extra>"))
    fig.add_trace(go.Bar(
        x=[maxpressure.get(k, 0) for k in keys], y=labels, orientation="h",
        name="Max-pressure", marker=dict(color=T.series(1), cornerradius=4),
        text=[f"{maxpressure.get(k, 0):.0f}%" for k in keys],
        textposition="outside", cliponaxis=False,
        textfont=dict(color=p["text2"], size=11),
        hovertemplate="%{y}<br>max-pressure %{x:.1f}%<extra></extra>"))

    top = max(list(actual.values()) + list(maxpressure.values()) + [1])
    fig.update_layout(**T.layout(height=90 + 52 * len(keys), barmode="group",
                                 bargap=0.34, bargroupgap=0.08,
                                 margin=dict(l=8, r=52, t=34, b=34)))
    fig.update_xaxes(title="Share of green time (%)", range=[0, top * 1.22],
                     ticksuffix="%")
    fig.update_yaxes(showgrid=False, tickfont=dict(color=p["text2"], size=11))
    return fig


def fluid_timeline(history: list) -> go.Figure:
    """Observed queue, and pressure served against pressure available.

    Two stacked panels on one time axis rather than two y-scales: PCU and
    pressure are unrelated units, and overlaying them would imply a fixed
    relationship the data does not contain.
    """
    if not history:
        return _blank("No replay history.")
    p = T.P()
    h = pd.DataFrame(history)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.13,
                        subplot_titles=("Total approach queue observed (PCU)",
                                        "Pressure relieved: real signal against best available"))
    fig.add_trace(go.Scatter(
        x=h.t, y=h.total_queue_pcu, mode="lines", name="Queue",
        line=dict(color=T.series(0), width=2),
        fill="tozeroy", fillcolor=T.tint(T.series(0), 0.10),
        hovertemplate="%{y:.1f} PCU<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=h.t, y=h.best_pressure, mode="lines", name="Best available",
        line=dict(color=p["deemphasis"], width=2),
        hovertemplate="best %{y:.2f}<extra></extra>"), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=h.t, y=h.actual_pressure, mode="lines", name="Real signal",
        line=dict(color=T.series(1), width=2),
        hovertemplate="served %{y:.2f}<extra></extra>"), row=2, col=1)

    fig.update_layout(**T.layout(height=380, legend=True, hovermode="x unified",
                                 margin=dict(l=8, r=16, t=30, b=38)))
    fig.update_xaxes(showgrid=True, gridcolor=p["grid"], zeroline=False,
                     linecolor=p["axis"], ticks="outside", tickcolor=p["axis"],
                     ticklen=4, tickfont=dict(color=p["text2"], size=11))
    fig.update_yaxes(showgrid=True, gridcolor=p["grid"], zeroline=False,
                     linecolor=p["axis"], ticks="outside", tickcolor=p["axis"],
                     ticklen=4, tickfont=dict(color=p["text2"], size=11))
    fig.update_xaxes(title="Seconds into recording", row=2, col=1)
    for a in fig.layout.annotations:
        a.font = dict(family=T.FONT, size=11.5, color=p["text2"])
        a.x, a.xanchor = 0, "left"
    return fig


def fluid_validation_chart(d) -> go.Figure:
    """Wasted green, real signal against max-pressure, one row per intersection.

    A grouped bar rather than a dumbbell: this is a magnitude comparison of two
    named controllers, not a before/after on one thing moving, and the reader's
    question is "how big is each" rather than "how far did it travel".
    """
    if d is None or len(d) == 0:
        return _blank("No FLUID recordings available.")
    p = T.P()
    labels = [str(x).split("—")[0].strip() for x in d.label]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=labels, x=d.wasted_actual, orientation="h", name="Real fixed-time signal",
        marker=dict(color=T.status("critical"), cornerradius=3, line=_ring(1)),
        text=[f"{v:.1f}%" for v in d.wasted_actual],
        textposition="outside", textfont=dict(color=p["text"], size=11.5),
        cliponaxis=False,
        hovertemplate="%{y}<br>real signal: %{x:.1f}% of green wasted<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        y=labels, x=d.wasted_mp, orientation="h", name="Max-pressure",
        marker=dict(color=T.status("good"), cornerradius=3, line=_ring(1)),
        text=[f"{v:.1f}%" for v in d.wasted_mp],
        textposition="outside", textfont=dict(color=p["text2"], size=11.5),
        cliponaxis=False,
        hovertemplate="%{y}<br>max-pressure: %{x:.1f}% of green wasted<extra></extra>",
    ))

    fig.update_layout(**T.layout(height=90 + 62 * len(d), legend=True,
                                 barmode="group", bargap=0.32, bargroupgap=0.08,
                                 margin=dict(l=8, r=58, t=44, b=40)))
    fig.update_xaxes(title="Share of green spent on a phase with nothing to relieve",
                     range=[0, max(100.0, float(d.wasted_actual.max()) * 1.22)],
                     ticksuffix="%")
    fig.update_yaxes(showgrid=False, tickfont=dict(color=p["text2"], size=11.5))
    return fig


def fluid_fleet_mix(mix) -> go.Figure:
    """Vehicle-class share for a FLUID recording.

    Its own function rather than reusing `fleet_mix`: FLUID's classes are not
    the Surat class groups, and quietly mapping one onto the other would put
    Chinese tricycles in a bar labelled with an Indian category.
    """
    if mix is None or len(mix) == 0:
        return _blank("No class counts in this recording.")
    p = T.P()
    m = mix.sort_values("share", ascending=True)
    fig = go.Figure(go.Bar(
        x=m.share, y=m["class"], orientation="h",
        marker=dict(color=[T.series(i) for i in range(len(m))],
                    cornerradius=3, line=_ring(1)),
        text=[f"{v:.1f}%" for v in m.share], textposition="outside",
        textfont=dict(color=p["text2"], size=11), cliponaxis=False,
        customdata=m.pcu,
        hovertemplate="%{y}<br>%{x:.1f}% of vehicles · %{customdata:.1f} PCU each"
                      "<extra></extra>",
    ))
    fig.update_layout(**T.layout(height=76 + 34 * len(m),
                                 margin=dict(l=8, r=54, t=26, b=38)))
    fig.update_xaxes(title="Share of tracked vehicles", ticksuffix="%",
                     range=[0, float(m.share.max()) * 1.24])
    fig.update_yaxes(showgrid=False, tickfont=dict(color=p["text2"], size=11.5))
    return fig
