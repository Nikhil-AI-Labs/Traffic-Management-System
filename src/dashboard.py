"""Surat Traffic Intelligence — operator dashboard.

    streamlit run src/dashboard.py

Six views over the metrics that analyze.py writes: the state of the network,
one site in depth, the adaptive signal plan, sustained bottlenecks, the
short-horizon outlook, and a walkthrough of how the measurement actually works.

Two things are held to throughout. Every chart has a table view beside it, so
no value is reachable only by hovering or only by colour. And every figure that
depends on the uncalibrated pixel-to-metre scale is prefixed '~' and sits
behind a banner that says so — the ranking, the level of service and the signal
comparison do not depend on it and are presented without the caveat.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bottleneck_detect as bn
import calibration as CAL
import charts as C
import config as cfg

cfg.quiet_opencv()
import forecast as fc
import ingest
import signal_opt as so
import theme as T
import zones as Z
from reference_frames import REF_DIR

st.set_page_config(
    page_title="Surat Traffic Intelligence — PS-08",
    page_icon="🚦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# The control-strategy toggle switches the whole dashboard between two
# datasets, because the two controllers have nothing to say about each other's
# data. Webster's runs on the Surat CCTV network; max-pressure is validated on
# the FLUID drone recordings. Keeping one navigation list for both would put a
# FLUID result one click from a Surat result with nothing but a heading between
# them, which is exactly the confusion this split exists to prevent.
VIEWS_WEBSTER = [
    "Network",
    "Site detail",
    "Signal timing",
    "Bottlenecks",
    "Outlook",
    "Analyse footage",
    "Calibration",
    "How it works",
]
VIEWS_MAXPRESSURE = [
    "Validation",
    "Scene",
    "Phase pressure",
    "Head-to-head",
    "Method",
]


def views_for(mode: str) -> list[str]:
    return VIEWS_MAXPRESSURE if mode == "max_pressure" else VIEWS_WEBSTER


# Views that read the seven Surat cameras' metrics; the rest stand alone and
# must still work before analyze.py has ever been run.
NEEDS_METRICS = {"Network", "Site detail", "Signal timing",
                 "Bottlenecks", "Outlook", "How it works"}
HEATMAP_DIR = cfg.FRAME_DIR / "heatmap"


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

# The data layer lives in services.py, shared with the JSON API behind the
# React dashboard and with the verification suite, so all three read the same
# numbers through the same code. Its caches follow the files' modification
# times, so nothing here needs clearing after a re-measure.
import services as S

load_metrics = S.load_metrics
load_image = S.load_image
zone_state = S.zone_state
overlay = S.overlay


def table(df: pd.DataFrame, label: str = "Table view",
          fmt: dict | None = None, expanded: bool = False):
    """The WCAG-clean twin of the chart above it. Never optional."""
    with st.expander(label, expanded=expanded):
        st.dataframe(df.style.format(fmt or {}, na_rep="—") if fmt else df,
                     width="stretch", hide_index=True)


def scale_banner(df: pd.DataFrame | None = None, camera_id: str | None = None):
    """Say plainly which figures are measured and which carry an unknown scale."""
    if camera_id is not None:
        if cfg.scale_is_verified(camera_id):
            return
    elif df is not None and "scale_verified" in df and bool(df.scale_verified.any()):
        return
    T.callout(
        "<b>Figures marked ~ carry an uncalibrated scale.</b> Nothing in this "
        "footage fixes pixels to metres, so anything in km/h, PCU/km/lane or "
        "PCU/hr is an estimate with an unknown multiplier — read those as "
        "relative, not absolute. The congestion ranking, the level-of-service "
        "bands, the saturation flags and the signal before/after comparison "
        "are scale-free and are unaffected. "
        "<b>One measured road width fixes it</b> — open the "
        "<b>Calibration</b> view.",
        title="📏 Metric scale not pinned",
        accent=T.status("warning"),
    )


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------

def view_network(df: pd.DataFrame):
    summary = fc.camera_summary(df)
    order = list(summary.camera_id)

    over_cap = float((df.los.isin(["Heavy", "Jam"])).mean() * 100)
    worst = summary.iloc[0]

    left, right = st.columns([2, 3], gap="large")
    with left:
        T.hero(
            "Monitored time at or beyond capacity", f"{over_cap:.0f}", "%",
            f"Across {len(summary)} sites and {len(df):,} two-second "
            f"measurement windows. The worst is "
            f"<b>{C.short_name(worst.camera_id)}</b> at an index of "
            f"{worst.mean_congestion:.2f}.",
        )
    with right:
        sat = int(summary.saturated_windows.sum())
        T.tiles([
            T.tile("Sites", f"{len(summary)}",
                   detail=f"{len(cfg.junction_cameras())} junction · "
                          f"{len(cfg.area_cameras())} area"),
            T.tile("Zones", f"{int(summary.zones.sum())}",
                   detail="lanes, carriageways, kerbs, platforms"),
            T.tile("Windows", f"{len(df):,}", detail="2 s each"),
            T.tile("Saturated", f"{sat:,}",
                   meter=(sat / max(1, len(df)), T.status("critical")),
                   detail=f"{sat / max(1, len(df)) * 100:.0f}% of windows where "
                          f"counting breaks down"),
        ], per_row=4)

    T.section("Level of service over time",
              "worst zone at each site, second by second")
    T.show(C.network_los_timeline(df, order))
    st.markdown(
        '<div class="note">Each band is one site. Reading left to right is '
        'reading the clip: GauravPath holds a single state throughout, while '
        'Mahidharpura never leaves jam. These bands take the <b>worst</b> zone '
        'at each site, so a site can show heavy bands and still rank lower '
        'below, where the index is averaged across every zone and window.'
        '</div>', unsafe_allow_html=True)

    T.section("Congestion ranking", "mean and peak, absolute scale")
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        T.show(C.congestion_ranking(summary))
    with c2:
        st.markdown(
            '<div class="note" style="margin-top:1.4rem">'
            'The index is normalised against <b>absolute</b> occupancy bounds '
            'measured once across all seven cameras — not against each zone\'s '
            'own range. That is what makes these bars comparable: a '
            'self-normalised index would rate an empty arterial "busy" whenever '
            'it drifted above its own average, and a permanently packed market '
            '"clear" at its quietest moment.</div>',
            unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        for _, r in summary.iterrows():
            los = T.los_of(float(r.mean_congestion))
            st.markdown(
                f'<div style="margin-bottom:7px">{T.los_chip(los)} '
                f'<span class="kv">&nbsp;{C.short_name(r.camera_id)} — '
                f'<b>{r.mean_congestion:.2f}</b></span></div>',
                unsafe_allow_html=True)

    show = summary.copy()
    show["Site"] = show.camera_id.map(lambda c: cfg.CAMERAS[c]["name"])
    show["Mode"] = show.camera_id.map(lambda c: cfg.CAMERAS[c]["mode"])
    show = show[["Site", "Mode", "mean_congestion", "peak_congestion",
                 "worst_zone", "mean_speed_kmph", "saturated_windows",
                 "total_windows"]]
    show.columns = ["Site", "Mode", "Mean congestion", "Peak congestion",
                    "Worst zone", "~Mean speed (km/h)", "Saturated windows",
                    "Windows"]
    table(show, "Table view — all sites", fmt={
        "Mean congestion": "{:.3f}", "Peak congestion": "{:.3f}",
        "~Mean speed (km/h)": "{:.1f}"})

    scale_banner(df)

    T.section("Sites", "zones shaded by average level of service")
    cams = list(summary.camera_id)
    for i in range(0, len(cams), 4):
        for col, cam in zip(st.columns(4), cams[i:i + 4]):
            with col:
                state = zone_state(df, cam)
                img = overlay(cam, state)
                if img is not None:
                    st.image(img, width="stretch")
                r = summary[summary.camera_id == cam].iloc[0]
                los = T.los_of(float(r.mean_congestion))
                st.markdown(
                    f'<div class="site"><div class="site-n">'
                    f'{cfg.CAMERAS[cam]["name"]}</div>'
                    f'<div class="site-m">{cfg.CAMERAS[cam]["mode"]} mode · '
                    f'{int(r.total_windows)} windows</div>'
                    f'<div style="margin-top:7px">{T.los_chip(los)} '
                    f'<span class="kv">&nbsp;index <b>{r.mean_congestion:.2f}</b>'
                    f'</span></div></div>',
                    unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Site detail
# --------------------------------------------------------------------------

def view_site(df: pd.DataFrame, camera_id: str):
    cam = cfg.CAMERAS[camera_id]
    state = zone_state(df, camera_id)
    if state.empty:
        st.warning(f"No metrics yet. Run `python src/analyze.py {camera_id}`.")
        return

    sub = df[df.camera_id == camera_id]
    ci = float(sub.congestion_index.mean())
    los = T.los_of(ci)

    st.markdown(
        f'<div style="display:flex;align-items:center;gap:12px;'
        f'flex-wrap:wrap;margin:.7rem 0 2px 0">'
        f'<span style="font-size:1.4rem;font-weight:665;letter-spacing:-.02em">'
        f'{cam["name"]}</span>{T.los_chip(los)}'
        f'{T.chip("Scale measured", T.status("good")) if cfg.scale_is_verified(camera_id) else ""}'
        f'</div>'
        f'<div class="note" style="margin-top:0">{cam["mode"].title()} mode · '
        f'{cam.get("notes", "")}</div>',
        unsafe_allow_html=True)

    T.section("Zone state", f"{len(state)} zones · {len(sub)} windows")
    est = "" if bool(state.get("scale_verified", pd.Series([False])).iloc[0]) else "~"
    cards = []
    for _, r in state.iterrows():
        zci = float(r.get("avg_congestion", 0) or 0)
        zlos = T.los_of(zci)
        bits = [f"occupancy {float(r.get('avg_occupancy', 0) or 0):.2f}"]
        if pd.notna(r.get("avg_density")):
            bits.append(f"{est}{r['avg_density']:.0f} PCU/km/ln")
        if pd.notna(r.get("avg_speed")):
            bits.append(f"{est}{r['avg_speed']:.0f} km/h")
        satw = int(r.get("sat_windows", 0) or 0)
        if satw:
            bits.append(f"{satw} saturated")
        cards.append(T.tile(
            str(r["zone_label"])[:24], f"{zci:.2f}",
            meter=(zci, T.los_color(zlos)),
            detail=f"{zlos} · " + " · ".join(bits)))
    T.tiles(cards, per_row=min(4, len(cards)))

    T.section("Scene")
    scene, aside = st.columns([7, 3], gap="large")
    with scene:
        tabs = st.tabs(["Zones", "Where traffic actually goes",
                        "Annotated video"])
        with tabs[0]:
            img = overlay(camera_id, state)
            if img is not None:
                st.image(img, width="stretch",
                         caption="Zone polygons shaded by average level of "
                                 "service — the same status colours the bands "
                                 "and chips use")
        with tabs[1]:
            hm = load_image(str(HEATMAP_DIR / f"{camera_id}_heatmap.jpg"))
            if hm is None:
                st.info("No heatmap rendered yet — "
                        f"`python src/traffic_heatmap.py {camera_id}`.")
            else:
                st.image(hm, width="stretch")
                st.markdown(
                    '<div class="note">Every tracked vehicle\'s ground contact '
                    'point, accumulated over the whole clip.</div>',
                    unsafe_allow_html=True)
        with tabs[2]:
            vid = cfg.ANNOTATED_DIR / f"{camera_id}_demo.mp4"
            if vid.exists():
                st.video(str(vid))
                st.markdown(
                    f'<div class="note">Detection, tracking, zone shading and '
                    f'the live metrics panel rendered over the source footage '
                    f'({vid.stat().st_size / 1e6:.0f} MB).</div>',
                    unsafe_allow_html=True)
            else:
                st.info("Not rendered for this camera — "
                        f"`python src/render_demo.py {camera_id}`")
    with aside:
        st.markdown(
            f'<div class="note" style="margin-top:2.6rem">'
            f'<b>Zones</b> are traced once per camera and reused for every '
            f'window. A detection is credited to a zone by its bottom-centre '
            f'ground contact point rather than its box centre, so a tall bus '
            f'is not assigned to the lane behind it.<br><br>'
            f'<b>The heatmap</b> is how those polygons were checked. A lane '
            f'traced from a single still frame can look right and still sit '
            f'off the path traffic actually takes; accumulating real tracks '
            f'settles it. Bright patches that never move are stationary '
            f'objects — at Rokadiya Hanuman that turned out to be kerb-side '
            f'parking narrowing the carriageway, which is where the '
            f'encroachment alert comes from.<br><br>'
            f'<b>The video</b> is the same pipeline that produced every number '
            f'on this page, drawn frame by frame.</div>',
            unsafe_allow_html=True)

    T.section("Level of service by zone", "each zone, second by second")
    T.show(C.zone_los_timeline(df, camera_id))

    T.section("Trend over the clip")
    prof = fc.time_profile(df, camera_id, bin_s=10.0)
    if not prof.empty:
        options = [m for m in ("congestion_index", "occupancy",
                               "density_pcu_per_km_lane", "speed_kmph",
                               "flow_pcu_per_hr")
                   if m in prof.columns and prof[m].notna().any()]
        fcol, _ = st.columns([1, 2])
        metric = fcol.selectbox("Metric", options,
                                format_func=lambda m: C.METRIC_AXIS[m][0],
                                key=f"metric_{camera_id}",
                                label_visibility="collapsed")
        T.show(C.zone_trend(prof, metric))
        table(prof.pivot(index="bin", columns="zone_label",
                         values=metric).round(2).reset_index()
                  .rename(columns={"bin": "t (s)"}),
              "Table view — 10-second bins")

    T.section("Counting against occupancy",
              "why this site needs both measures")
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        T.show(C.count_vs_occupancy(df, camera_id))
    with c2:
        satpct = float(sub.saturated.mean() * 100) if "saturated" in sub else 0.0
        st.markdown(
            f'<div class="note" style="margin-top:1.2rem">'
            f'Two panels, one time axis — never two y-scales. The scales are '
            f'unrelated, and overlaying them would imply a fixed relationship '
            f'between counts and occupancy that is exactly what fails in dense '
            f'traffic.<br><br>'
            f'This site spends <b>{satpct:.0f}%</b> of its windows saturated: '
            f'so densely packed that vehicles stop being individually '
            f'separable. Where the two panels move together, counting is '
            f'sound. Where the top panel sags while the bottom stays pinned '
            f'high, the detector is losing vehicles to occlusion and occupancy '
            f'is the measure that still holds.</div>',
            unsafe_allow_html=True)

    if cam["mode"] == "junction":
        T.section("Speed against density", "the fundamental diagram")
        c1, c2 = st.columns([3, 2], gap="large")
        with c1:
            T.show(C.fundamental_diagram(df, camera_id))
        with c2:
            st.markdown(
                '<div class="note" style="margin-top:1.2rem">'
                'Traffic-flow theory says speed must fall as density rises, and '
                'that flow peaks near capacity density before collapsing. '
                'Plotting the measurement against the theory is the check that '
                'the chain from pixels to PCU is behaving — a flat cloud here '
                'would mean the speed or the density figure is wrong. An '
                'earlier version of this project produced exactly that, and it '
                'is how the perspective-scale bug was found.</div>',
                unsafe_allow_html=True)
        scale_banner(df)

    st.divider()
    section_alerts(df, camera_id, heading="Bottlenecks at this site")


# --------------------------------------------------------------------------
# Signal timing
# --------------------------------------------------------------------------

def _pressure_table(decision) -> pd.DataFrame:
    """Phase pressure scores, in the order the controller ranked them.

    The 'Serve' arrow marks a *max-pressure* recommendation and appears only
    when the controller is genuinely running max-pressure. Where the
    downstream term is missing the column is headed differently, so a reader
    cannot take a greedy-queue ranking for a max-pressure decision.
    """
    import maxpressure as MP
    full = decision.mode == MP.MODE_FULL
    mark_id = decision.chosen_phase_id if full else decision.greedy_phase_id
    col = "Serve" if full else "Greedy would serve"
    rows = []
    for p in decision.phases:
        rows.append({
            "Phase": p.label,
            ("Pressure P(p)" if full else "Score (queue only)"):
                ("not computable" if p.pressure is None else round(p.pressure, 2)),
            "Movements observed": f"{p.observed_movements} / {p.total_movements}",
            col: ("←" if p.phase_id == mark_id else ""),
            "Note": p.note or "",
        })
    return pd.DataFrame(rows)


def _mp_instant_panel():
    """Phase pressure scores at a chosen instant of a chosen recording.

    FLUID only. There is deliberately no Surat equivalent: without a
    downstream queue there is no pressure to rank, and showing an empty
    table under this heading would suggest the attempt was meaningful.
    """
    import maxpressure as MP
    import fluid as FL

    T.section("Phase pressure at one instant",
              "FLUID — every approach and every receiving link in one frame")
    if not FL.available():
        T.callout(
            "No dataset on this machine carries a downstream queue, so there "
            "is nowhere to show a real pressure score. <b>FLUID</b> — figshare "
            "DOI <code>10.6084/m9.figshare.29974954</code> — does; set "
            "<code>FLUID_ROOT</code> if it lives outside the project folder.",
            title="Drone downstream data not found", accent=T.status("warning"))
        return

    recs = FL.recordings()
    c1, c2 = st.columns([2, 1])
    rid = c1.selectbox("Recording", [r.id for r in recs],
                       format_func=lambda i: (
                           f"{FL.SITE_LABELS.get(i.split('/')[0], i)}  ·  "
                           f"{i.split('/')[1]}"),
                       key="sig_mp_rec")
    tables = _fluid_tables(rid)
    if tables is None:
        st.error("Could not load that recording.")
        return
    queues, sig, turns = tables

    times = sorted(queues.t.unique())
    if not times:
        st.warning("No queue measurements in this recording.")
        return
    t = c2.select_slider("Instant (s into clip)", options=times,
                         value=times[min(len(times) - 1, len(times) // 3)],
                         key="sig_mp_t")

    mv = FL.movements_at(queues, float(t), turns=turns)
    ph = FL.controller_phases(sig, queues, min_green_s=cfg.MP_MIN_GREEN_S,
                              max_green_s=cfg.MP_MAX_GREEN_S)
    dec = MP.decide(mv, ph)
    actual_green = FL.green_at(sig, float(t))

    obs = sum(1 for m in mv if m.downstream_observed)
    T.tiles([
        T.tile("Downstream queues observed", f"{obs} / {len(mv)}",
               meter=(obs / max(1, len(mv)), T.status("good")),
               detail="The drone sees the whole intersection, so every "
                      "receiving link is measured."),
        T.tile("Controller mode", dec.mode.replace("-", " "),
               detail="Genuine max-pressure — the downstream term is real."),
        T.tile("Max-pressure would serve", dec.chosen_label or "none",
               detail=(f"P = {dec.chosen_pressure:.1f}"
                       if dec.chosen_pressure is not None else
                       "every phase non-positive — nothing to relieve")),
    ], per_row=3)

    st.markdown(
        T.chip(f"MODE: {dec.mode.upper()}", T.status("good"))
        + " " + T.chip("Drone view downstream data: ACTIVE", T.series(0)),
        unsafe_allow_html=True)

    st.dataframe(_pressure_table(dec), width="stretch", hide_index=True)

    st.markdown(
        f'<div class="note">At <b>t = {float(t):.0f}s</b> the recorded signal '
        f'was holding <b>{", ".join(sorted(actual_green)) or "no"}</b> green. '
        f'A negative pressure means the link that phase discharges into was '
        f'fuller than the approach feeding it — serving it would push traffic '
        f'into a queue that cannot accept it, which is the spillback '
        f'max-pressure exists to avoid, and the term greedy queue control '
        f'cannot see.</div>', unsafe_allow_html=True)

    st.dataframe(pd.DataFrame([{
        "Movement": m.label,
        "Approach queue x_l (PCU)": round(m.queue_in_pcu, 2),
        "Receiving queue x_m (PCU)": ("—" if m.queue_out_pcu is None
                                      else round(m.queue_out_pcu, 2)),
        "Turn ratio r": round(m.turn_ratio, 3),
        "Weight w(l,m)": ("—" if m.pressure is None else round(m.pressure, 2)),
    } for m in mv]), width="stretch", hide_index=True)

    T.callout(
        "This is the phase ranking at one instant. The <b>Max-pressure</b> "
        "view replays it second by second against what each signal actually "
        "did, and runs the controlled head-to-head against a fixed-time plan.",
        accent=T.P()["axis"])

    # ---- why Surat is not in this view -----------------------------------


def view_signal(df: pd.DataFrame):
    junctions = [c for c in cfg.junction_cameras()
                 if c in set(df.camera_id.unique())]
    demands = [d for d in (so.demand_from_metrics(df, c) for c in junctions)
               if d is not None]
    if not demands:
        st.info("No junction-mode cameras analysed yet.")
        return

    # Pure Webster's. This view is only reachable in Webster mode — the
    # control-strategy toggle swaps the entire navigation, so max-pressure has
    # its own track over the FLUID data rather than a branch inside a Surat
    # view. Keeping them in one function is what made the two datasets look
    # interchangeable when they are not.

    # ---- controls: one filter row above everything it scopes --------------
    c1, c2, c3 = st.columns([1.2, 1.2, 1.6])
    baseline_cycle = c1.slider(
        "Existing fixed-timer cycle (s)", 60, 150, 120, 5,
        help="The untuned fixed-time cycle used as the comparison baseline.")
    delay_model = c2.selectbox(
        "Delay model", ["auto", "hcm", "webster"], index=0,
        format_func=lambda m: {"auto": "Automatic (HCM above x = 0.85)",
                               "hcm": "HCM control delay",
                               "webster": "Webster 1958"}[m],
        help="Webster's random-delay term diverges as an approach approaches "
             "capacity, which is exactly where this junction sits. HCM's "
             "time-dependent term stays finite above capacity. 'Automatic' "
             "uses Webster below x = 0.85 and HCM above it.")
    sat_source = c3.select_slider(
        "Saturation flow assumption",
        options=["IRC:SP:41 (525·W)", "Field low (636·W)", "Mid (900·W)",
                 "Field high (1283·W)", "Per-lane 1800 (default)"],
        value="Per-lane 1800 (default)",
        help="IRC:SP:41-1994 gives S = 525·W PCU/hr from approach width. Field "
             "measurement at Vijay Cross-road, Ahmedabad found 636·W–1283·W. "
             "The default assumes 1,800 PCU/hr per lane, which is 525 × 3.5 m "
             "— the same figure, expressed per lane.")

    factor = {"IRC:SP:41 (525·W)": so.IRC_SATURATION_FACTOR,
              "Field low (636·W)": so.FIELD_SATURATION_FACTOR_LOW,
              "Mid (900·W)": 900.0,
              "Field high (1283·W)": so.FIELD_SATURATION_FACTOR_HIGH}.get(sat_source)

    widths: dict[str, float] = {}
    if factor is not None:
        with st.expander("Approach widths (metres) — needed by the width-based "
                         "formula", expanded=True):
            wcols = st.columns(len(demands))
            for col, d in zip(wcols, demands):
                widths[d.approach_id] = col.number_input(
                    f"{C.short_name(d.camera_id)} — {d.lanes} lanes",
                    min_value=2.0, max_value=30.0, step=0.5,
                    value=float(d.lanes) * 3.5,
                    key=f"sig_w_{d.approach_id}",
                    help="Kerb-to-median width of this approach. This is a "
                         "number a city operator genuinely knows, and it is "
                         "the same measurement the Calibration view asks for.")
        demands = [so.ApproachDemand(
            **{**{k: v for k, v in d.__dict__.items()
                  if k not in ("approach_width_m", "saturation_factor",
                               "saturation_flow_per_lane")},
               "approach_width_m": widths[d.approach_id],
               "saturation_factor": factor})
            for d in demands]

    base = so.evaluate_fixed_plan(demands, cycle_s=float(baseline_cycle),
                                  delay_model=delay_model)
    opt = so.optimise(demands, junction_name="Adaptive plan (Webster)",
                      delay_model=delay_model)
    cmp = so.compare(base, opt)

    Y = sum(d.flow_ratio for d in demands)
    L = len(demands) * so.LOST_TIME_PER_PHASE_S
    X_common = so.common_degree_of_saturation(Y, opt.cycle_s, L)
    c_min = so.websters_cmin(L, Y, Xc=0.90)

    worst_before = max(p.degree_of_saturation for p in base.phases)
    worst_after = max(p.degree_of_saturation for p in opt.phases)
    over_before = [p for p in base.phases if p.degree_of_saturation >= 1.0]

    # ---- headline: the saturation crossing, not the delay number ----------
    # Delay depends on which overflow model you believe; degree of saturation
    # does not. Crossing back under 1.0 is the qualitative change, and it is
    # the one claim here that no modelling choice can take away.
    left, right = st.columns([2, 3], gap="large")
    with left:
        T.hero("Worst approach saturation",
               f"{worst_before:.2f} → {worst_after:.2f}",
               detail=(
                   f"x is flow ÷ capacity on the critical lane. Above 1.0 the "
                   f"queue cannot clear within the cycle and carries over. The "
                   f"fixed timer drives "
                   f"<b>{len(over_before)} of {len(base.phases)}</b> approaches "
                   f"over capacity; the adaptive plan brings every one back "
                   f"under."
                   if over_before else
                   "Both approaches stay under capacity in both plans; the "
                   "adaptive plan equalises them, which is where the delay "
                   "saving comes from."))
    with right:
        T.tiles([
            T.tile("Cycle length", f"{opt.cycle_s:.0f}", "s",
                   detail=f"{opt.cycle_s - base.cycle_s:+.0f}s against the "
                          f"{base.cycle_s:.0f}s baseline"),
            T.tile("Delay reduction", f"{cmp['delay_reduction_pct']:.1f}", "%",
                   detail=f"{cmp['baseline_mean_delay_s']:.1f} → "
                          f"{cmp['optimised_mean_delay_s']:.1f} s/PCU, "
                          f"{'/'.join(sorted({p.delay_model for p in opt.phases}))} "
                          f"model"),
            T.tile("Total flow ratio Y", f"{Y:.2f}",
                   meter=(min(1.0, Y), T.status(
                       "critical" if Y > 0.85 else "good")),
                   detail="Y ≥ 0.95 means no cycle length can clear the demand"),
            T.tile("Common saturation X", f"{X_common:.2f}",
                   detail="Y·C/(C−L) — the closed form every critical "
                          "approach lands on"),
        ], per_row=4)

    if not opt.feasible:
        st.error(opt.note)

    # ---- the delay-model spread, stated rather than hidden ----------------
    T.section("How much does retiming actually save?",
              "the answer depends on the overflow model")
    rows = []
    for m in ("webster", "hcm"):
        b = so.evaluate_fixed_plan(demands, cycle_s=float(baseline_cycle),
                                   delay_model=m)
        o = so.optimise(demands, delay_model=m)
        c = so.compare(b, o)
        # Webster's formula has no finite delay above capacity, so a
        # "reduction" from an infinite baseline used to print as nan%.
        finite = np.isfinite(b.mean_delay_s_per_pcu)
        rows.append({
            "Delay model": {"webster": "Webster 1958 (3-term)",
                            "hcm": "HCM control delay"}[m],
            "Baseline (s/PCU)": (c["baseline_mean_delay_s"] if finite
                                 else "∞ (over capacity)"),
            "Optimised (s/PCU)": c["optimised_mean_delay_s"],
            "Reduction": (f"{c['delay_reduction_pct']:.1f}%" if finite
                          else "not finite"),
        })
    c1, c2 = st.columns([2, 3], gap="large")
    with c1:
        st.dataframe(pd.DataFrame(rows).astype(str), width="stretch",
                     hide_index=True)
    with c2:
        pcts = [float(r["Reduction"].rstrip("%")) for r in rows
                if r["Reduction"].endswith("%")] or [0.0]
        lo, hi = min(pcts), max(pcts)
        T.callout(
            f"The honest answer is a range: <b>{lo:.0f}% to {hi:.0f}%</b>, and "
            f"the spread is a finding rather than an embarrassment. Webster's "
            f"random-delay term <i>x²/[2q(1−x)]</i> diverges as an approach "
            f"reaches capacity, so it reads high on exactly the approach this "
            f"plan is fixing. HCM's time-dependent term stays finite above "
            f"capacity because the square root bounds the overflow by the "
            f"length of the analysis period — but that also assumes the "
            f"oversaturation lasts only {so.HCM_ANALYSIS_PERIOD_HR * 60:.0f} "
            f"minutes; over a longer peak the queue keeps growing and the real "
            f"delay is worse than HCM shows.<br><br>"
            f"What neither model can change is the saturation crossing above.",
            title="Why two numbers", accent=T.status("warning"))

    T.section("How each plan spends its cycle")
    T.show(C.cycle_composition(base, opt))
    T.note(
        "Both plans on one time axis. The adaptive plan moves green seconds "
        "onto the approach that measured the higher critical-lane flow instead "
        "of splitting them evenly. Stage colours are steps of one hue because "
        "the parts of a cycle are an ordered sequence, not four unrelated "
        "things.")

    T.section("Degree of saturation", "before and after, per approach")
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        T.show(C.saturation_dumbbell(base, opt))
    with c2:
        xs = ", ".join(f"{p.degree_of_saturation:.2f}" for p in opt.phases)
        T.callout(
            f"Webster's proportional split gives approach <i>i</i> a green of "
            f"g<sub>i</sub> = (y<sub>i</sub>/Y)(C − L), so its degree of "
            f"saturation is<br><br>"
            f"<b>x<sub>i</sub> = y<sub>i</sub>·C/g<sub>i</sub> = Y·C/(C − L)</b>"
            f"<br><br>which has no <i>i</i> in it. Equal saturation across "
            f"approaches is not a happy accident of the optimiser — it is what "
            f"proportional allocation <i>is</i>. The closed form gives "
            f"<b>{X_common:.3f}</b>; the optimiser lands on <b>{xs}</b>. Those "
            f"agreeing is the arithmetic check that the implementation matches "
            f"the theory.",
            title="Why they end up equal")

    T.section("Why this cycle length", "the optimiser auditing itself")
    rows_c = []
    for Cc in range(60, 185, 5):
        eff = Cc - L
        splits = ({d.approach_id: eff * d.flow_ratio / Y for d in demands}
                  if Y > 0 else None)
        rows_c.append({
            "Cycle (s)": Cc,
            "Equal splits": so.evaluate_fixed_plan(
                demands, cycle_s=float(Cc),
                delay_model=delay_model).mean_delay_s_per_pcu,
            "Demand-proportional splits": so.evaluate_fixed_plan(
                demands, cycle_s=float(Cc), green_splits=splits,
                delay_model=delay_model).mean_delay_s_per_pcu,
        })
    curve = pd.DataFrame(rows_c).set_index("Cycle (s)")
    best_c = int(curve["Demand-proportional splits"].idxmin())

    T.show(C.delay_curve(curve, opt.cycle_s))
    web = S.delay_sweep(demands, "webster")["Demand-proportional splits"]
    best_web = int(web.replace([np.inf], np.nan).idxmin())
    T.note(
        f"Delay swept across every cycle length from 60 s to 180 s. Under "
        f"Webster's own delay model — the one his closed form is derived from — "
        f"the proportional-split curve bottoms out at <b>{best_web}s</b> against "
        f"the closed-form optimum of <b>{opt.cycle_s:.0f}s</b>. Those two "
        f"agreeing is the check that the optimiser does what the theory says — "
        f"and it is a check that has caught a real bug: an earlier version "
        f"mixed per-lane and per-approach units, and this curve came out with "
        f"no minimum at all, which is impossible for a correct delay function."
        + ("" if delay_model == "webster" else
           f"<br><br>The curve above uses the <b>{delay_model}</b> model, whose "
           f"minimum is <b>{best_c}s</b>: HCM's bounded overflow term penalises "
           f"running near capacity far less than Webster's, so it favours a "
           f"shorter cycle. That is a property of the delay model, not a "
           f"disagreement in the optimiser."))
    table(curve.round(1).reset_index(), "Table view — delay by cycle length")

    T.section("The feasible cycle window")
    cmin_txt = ("beyond the 150 s maximum" if not np.isfinite(c_min)
                or c_min > so.MAX_CYCLE_S else f"{c_min:.0f}s")
    T.tiles([
        T.tile("Minimum cycle", cmin_txt,
               detail=f"C<sub>min</sub> = L·X<sub>c</sub>/(X<sub>c</sub> − Y) "
                      f"at a target v/c of 0.90"),
        T.tile("Webster optimum", f"{opt.cycle_s:.0f}", "s",
               detail="C = (1.5L + 5)/(1 − Y)"),
        T.tile("Practical maximum", f"{so.MAX_CYCLE_S:.0f}", "s",
               detail="beyond this, waiting drivers start ignoring the signal"),
    ], per_row=3)
    if not np.isfinite(c_min) or c_min > so.MAX_CYCLE_S:
        T.callout(
            f"With Y = {Y:.3f} there is <b>no cycle length</b> that holds "
            f"every approach at a degree of saturation of 0.90 — C<sub>min</sub> "
            f"exceeds any practical cycle. That is a capacity statement, not a "
            f"timing one: this junction is close enough to its ceiling that "
            f"retiming redistributes the delay rather than removing it. The "
            f"fixes that would remove it are geometric or demand-side.",
            title="Read this before quoting the saving",
            accent=T.status("serious"))

    T.section("Measured demand and the resulting plan")
    scale_banner(df)
    dmap = {d.approach_id: d for d in demands}
    rows_p = []
    for tag, plan in (("Baseline (fixed timer)", base),
                      ("Adaptive (Webster)", opt)):
        for p in plan.phases:
            d = dmap.get(p.approach_id)
            rows_p.append({
                "Approach": p.label,
                "Plan": tag,
                "Lanes": d.lanes if d else None,
                "Sat. flow (PCU/hr/lane)": (round(d.saturation_flow_per_lane)
                                            if d else None),
                "~Critical lane flow (PCU/hr)": (
                    round(d.critical_lane_flow_pcu_per_hr) if d else None),
                "Flow ratio y": p.flow_ratio,
                "Cycle (s)": plan.cycle_s,
                "Green (s)": p.green_s,
                "Saturation x": p.degree_of_saturation,
                "Delay (s/PCU)": p.est_delay_s_per_pcu,
                "Delay model": p.delay_model,
            })
    st.dataframe(pd.DataFrame(rows_p), width="stretch", hide_index=True)
    T.note(
        f"Amber is a fixed {so.AMBER_S:.0f}s and all-red a fixed "
        f"{so.ALL_RED_S:.0f}s in both plans, so green is the only thing being "
        f"redistributed. Saturation flow is "
        + (f"S = {factor:.0f}·W per IRC:SP:41's width-based form, divided by "
           f"the lane count." if factor else
           "assumed at 1,800 PCU/hr per lane — IRC:SP:41's 525·W evaluated at "
           "a 3.5 m lane."))

    with st.expander("Plans as text"):
        st.code(so.format_plan(base) + "\n\n" + so.format_plan(opt))

    T.callout(
        "<b>Method.</b> Webster's, not max-pressure: max-pressure needs the "
        "queue state on the <i>downstream</i> link of every movement, and a "
        "camera looking at one approach cannot see that — quoting an optimum "
        "computed from a term the data does not contain would be a made-up "
        "number.<br><br>"
        "<b>Scope.</b> These two cameras watch different corridors, not "
        "opposing approaches of one junction, so the combined plan is the "
        "method demonstrated on real measured demand rather than a deployable "
        "plan for a specific intersection. Feeding it every approach of one "
        "junction is a configuration change, not a code change.<br><br>"
        "<b>Sources.</b> Webster (1958) <i>Traffic Signal Settings</i>, Road "
        "Research Technical Paper 39 · HCM 6th edition control-delay model · "
        "IRC:SP:41-1994 for saturation flow · saturation-flow field range from "
        "Vijay Cross-road, Ahmedabad.",
        title="Method and scope", accent=T.P()["axis"])



# --------------------------------------------------------------------------
# Bottlenecks
# --------------------------------------------------------------------------

def section_alerts(df: pd.DataFrame, camera_id: str | None = None,
                   heading: str = "Sustained bottlenecks"):
    alerts = bn.detect_all(df) if camera_id is None else bn.detect(df, camera_id)
    T.section(heading, f"{len(alerts)} raised" if alerts else "none raised")
    if not alerts:
        st.success("No sustained bottleneck patterns in the analysed footage.")
        return

    counts = {s: sum(a.severity == s for a in alerts)
              for s in ("critical", "warning", "info")}
    T.tiles([
        T.tile("Critical", f"{counts['critical']}",
               detail="condition sustained and severe"),
        T.tile("Warning", f"{counts['warning']}",
               detail="sustained, below the critical threshold"),
        T.tile("Info", f"{counts['info']}",
               detail="worth knowing, not urgent"),
    ], per_row=3)

    if camera_id is None:
        c1, c2 = st.columns([3, 2], gap="large")
        with c1:
            T.show(C.alert_gantt(alerts))
        with c2:
            T.show(C.alert_mix(alerts))
        st.markdown(
            '<div class="note">Every alert placed on its clip clock. An alert '
            'only fires after the pattern holds for at least '
            f'{bn.MIN_SUSTAINED_WINDOWS} consecutive windows — a busy lane '
            'moving freely is working as designed, and a single dense frame is '
            'a platoon, not a bottleneck.</div>', unsafe_allow_html=True)

    table(pd.DataFrame([{
        "Severity": a.severity, "Site": C.short_name(a.camera_id),
        "Zone": a.zone_label, "Kind": a.kind.replace("_", " "),
        "From (s)": round(a.start_s), "To (s)": round(a.end_s),
        "Duration (s)": round(a.duration_s), "Evidence": a.evidence,
    } for a in alerts]), "Table view — all alerts")

    # One filter row above the feed it scopes. Thirty-odd cards unfiltered is
    # a wall; the table above still holds every one of them.
    key = camera_id or "all"
    fc1, fc2 = st.columns([2, 3])
    choices = ["All"] + [s.capitalize() for s in ("critical", "warning", "info")
                         if counts[s]]
    pick = fc1.segmented_control("Severity", choices, default="All",
                                 key=f"sev_{key}", label_visibility="collapsed")
    shown = alerts if pick in (None, "All") else [
        a for a in alerts if a.severity == pick.lower()]

    LIMIT = 8
    if len(shown) > LIMIT:
        if not fc2.toggle(f"Show all {len(shown)}", key=f"allalerts_{key}"):
            st.markdown(
                f'<div class="note">Showing the {LIMIT} most severe and '
                f'longest-running of {len(shown)}.</div>',
                unsafe_allow_html=True)
            shown = shown[:LIMIT]

    st.markdown('<div style="height:.4rem"></div>', unsafe_allow_html=True)
    for a in shown:
        col = T.severity_color(a.severity)
        st.markdown(
            f'<div class="alert" style="--sev:{col}">'
            f'{T.chip(a.severity.upper(), col)} '
            f'<span class="alert-m">&nbsp;{a.camera_name} · '
            f'{a.start_s:.0f}–{a.end_s:.0f}s ({a.duration_s:.0f}s)</span>'
            f'<div class="alert-h">{a.headline}</div>'
            f'<div class="alert-b"><b>Evidence</b> — {a.evidence}</div>'
            f'<div class="alert-b"><b>Recommendation</b> — '
            f'{a.recommendation}</div></div>',
            unsafe_allow_html=True)


def view_bottlenecks(df: pd.DataFrame):
    alerts = bn.detect_all(df)
    crit = sum(a.severity == "critical" for a in alerts)
    left, right = st.columns([2, 3], gap="large")
    with left:
        T.hero("Critical bottlenecks", f"{crit}",
               detail="Conditions that held long enough to be a state of the "
                      "road rather than a passing platoon, at the severe end "
                      "of the scale.")
    with right:
        kinds = sorted({a.kind.replace("_", " ") for a in alerts})
        longest = max((a.duration_s for a in alerts), default=0)
        T.tiles([
            T.tile("Alerts total", f"{len(alerts)}",
                   detail=f"across {len({a.camera_id for a in alerts})} sites"),
            T.tile("Patterns fired", f"{len(kinds)}",
                   detail=", ".join(kinds) + " · of 5 detectors"),
            T.tile("Longest episode", f"{longest:.0f}", "s",
                   detail="unbroken run of the same condition"),
        ], per_row=3)
    section_alerts(df, None)


# --------------------------------------------------------------------------
# Outlook
# --------------------------------------------------------------------------

def view_outlook(df: pd.DataFrame):
    fcol, _ = st.columns([1, 2])
    horizon = fcol.slider("Forecast horizon (s)", 30, 180, 60, 15)
    forecasts = fc.forecast_all(df, horizon_s=float(horizon))
    if not forecasts:
        st.info("Not enough history to forecast.")
        return

    reliable = [f for f in forecasts if f.reliable]
    risks = fc.rising_risks(forecasts, threshold=0.45)

    left, right = st.columns([2, 3], gap="large")
    with left:
        T.hero("Zones trending toward congestion", f"{len(risks)}",
               detail=f"Rising, credibly, and forecast past 0.45 within "
                      f"{horizon}s. Zones whose trend is smaller than their "
                      f"own noise are reported as steady rather than given a "
                      f"direction.")
    with right:
        T.tiles([
            T.tile("Zones forecast", f"{len(forecasts)}"),
            T.tile("With a directional trend", f"{len(reliable)}",
                   meter=(len(reliable) / max(1, len(forecasts)),
                          T.series(0)),
                   detail=f"{len(forecasts) - len(reliable)} within noise"),
            T.tile("Horizon", f"{horizon}", "s",
                   detail="damped trend, interval from residuals"),
        ], per_row=3)

    T.section("Now against forecast", "with the prediction interval")
    T.show(C.forecast_chart(forecasts, float(horizon)))
    st.markdown(
        '<div class="note">Hollow markers are zones whose trend does not beat '
        'their own noise — shown, but not claimed as a direction. The thick '
        'band is the prediction interval, and it is deliberately prominent: a '
        'bare forecast number off a few minutes of footage would imply a '
        'confidence this data cannot support. Continuous operation would '
        'replace this with a time-of-day seasonal model; the interface would '
        'not change.</div>', unsafe_allow_html=True)

    if risks:
        T.section("Zones trending worse")
        for f in risks:
            st.markdown(
                f'<div class="alert" style="--sev:{T.status("warning")}">'
                f'{T.chip("RISING", T.status("warning"))} '
                f'<span class="alert-m">&nbsp;{cfg.CAMERAS[f.camera_id]["name"]}'
                f'</span><div class="alert-h">{f.zone_label}</div>'
                f'<div class="alert-b">{f.current:.2f} → <b>{f.predicted:.2f}'
                f'</b> over {f.horizon_s:.0f}s '
                f'(interval {f.lower:.2f}–{f.upper:.2f})</div></div>',
                unsafe_allow_html=True)

    table(pd.DataFrame([{
        "Site": C.short_name(f.camera_id), "Zone": f.zone_label,
        "Now": f.current, "Forecast": f.predicted,
        "Low": f.lower, "High": f.upper, "Direction": f.direction,
        "Reliable": "yes" if f.reliable else "no",
    } for f in forecasts]), "Table view — every zone forecast")


# --------------------------------------------------------------------------
# How it works
# --------------------------------------------------------------------------

def view_method(df: pd.DataFrame):
    st.markdown(
        '<div class="note" style="font-size:.92rem;max-width:78ch">'
        'PS-08 asks for lane density, adaptive signal timing, bottleneck '
        'detection and predictive recommendations from CCTV. The pipeline '
        'below produces all four. This view is the reasoning behind the '
        'numbers the other views show — what is measured, what is inferred, '
        'and what this dataset cannot support.</div>',
        unsafe_allow_html=True)

    T.section("The pipeline")
    steps = [
        ("1 · Detect", "YOLO11-S on UVH-26",
         "Weights fine-tuned on 26,646 annotated frames of Indian Safe-City "
         "CCTV — the same domain as this footage. Fourteen India-specific "
         "classes, including the three-wheeler that COCO cannot represent at "
         "all and that dominates most of these clips."),
        ("2 · Track", "ByteTrack",
         "Identity across frames, so speed and stopped-fraction are "
         "measurable. Track IDs are used for motion, never for counting "
         "arrivals — the tracker reissues IDs freely in dense traffic."),
        ("3 · Locate", "Zone polygons + perspective scale",
         "Each detection is assigned by its bottom-centre ground contact "
         "point, not its box centre, so a tall bus is not credited to the "
         "lane behind it. An anisotropic scale model converts pixels to "
         "ground distance row by row."),
        ("4 · Measure", "PCU density, speed, occupancy",
         "Density in Passenger Car Units per IRC:106-1990, because twelve "
         "two-wheelers and twelve buses are not the same amount of traffic. "
         "Flow comes from q = k·v, the fundamental relation, rather than from "
         "counting track IDs."),
        ("5 · Decide", "Webster · detectors · trend",
         "Signal timing from Webster's method on the critical lane, five "
         "sustained-pattern bottleneck detectors, and a damped trend with a "
         "prediction interval."),
    ]
    # One grid rather than st.columns, so all five cards share a height.
    ink = T.P()["text"]
    st.markdown(
        f'<div class="tiles" style="grid-template-columns:'
        f'repeat({len(steps)},1fr)">' + "".join(
            f'<div class="tile"><div class="tile-l">{n}</div>'
            f'<div style="font-size:.95rem;font-weight:640;color:{ink};'
            f'margin-bottom:6px;line-height:1.3">{t}</div>'
            f'<div class="tile-d" style="line-height:1.5">{d}</div></div>'
            for n, t, d in steps) + "</div>",
        unsafe_allow_html=True)

    T.section("The finding that shaped the design",
              "detection collapses exactly where congestion peaks")
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        T.show(C.occlusion_paradox(df))
    with c2:
        g = df.groupby("camera_id").agg(det=("n_detections_mean", "mean"),
                                        ci=("congestion_index", "mean"))
        worst_id = g.ci.idxmax()
        busiest_id = g.det.idxmax()
        st.markdown(
            f'<div class="note" style="margin-top:1.2rem">'
            f'<b>{C.short_name(worst_id)}</b> is the most gridlocked site in '
            f'the set and returns <b>{g.det[worst_id]:.1f}</b> detections per '
            f'window. <b>{C.short_name(busiest_id)}</b>, a freely moving '
            f'arterial, returns <b>{g.det[busiest_id]:.1f}</b>.<br><br>'
            f'Vehicles packed bumper to bumper occlude each other until the '
            f'detector can no longer separate them. A congestion index built '
            f'on raw counts would therefore rank Surat\'s worst street among '
            f'its emptiest — confidently, and backwards.<br><br>'
            f'So counts do not drive the index. A texture-based occupancy '
            f'measure does: edge density over the zone, normalised against '
            f'absolute bounds measured across all seven cameras. It has no '
            f'notion of an object and cannot be defeated by occlusion.</div>',
            unsafe_allow_html=True)

    worst = str(df.groupby("camera_id").congestion_index.mean().idxmax())
    T.section("The same failure, over time",
              f"{C.short_name(worst)} — the worst-affected site")
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        T.show(C.count_vs_occupancy(df, worst))
    with c2:
        st.markdown(
            '<div class="note" style="margin-top:1.2rem">'
            'Occupancy sits pinned near its ceiling for the whole clip while '
            'the detection count stays on the floor. Read the top panel alone '
            'and this street is empty; read the bottom panel and it is '
            'immovable. The footage settles it — the street is immovable.'
            '<br><br>The system flags these windows as <b>saturated</b> and '
            'says so on every readout, rather than quietly reporting a count '
            'it knows is wrong.</div>',
            unsafe_allow_html=True)

    T.section("What is on these roads", "share of detections by class group")
    mix = C.fleet_table(df)
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        T.show(C.fleet_mix(mix))
    with c2:
        st.markdown(
            '<div class="note" style="margin-top:1.2rem">'
            'The reason a domain-matched detector matters. Two-wheelers and '
            'auto-rickshaws dominate most of these sites, and a stock '
            'COCO-trained model has no auto-rickshaw class at all — it either '
            'misses them or files them as cars, which then propagates into '
            'every PCU figure downstream (an auto is 1.2 PCU, a car 1.0).'
            '</div>', unsafe_allow_html=True)
    table(mix.drop(columns=["camera_id"]).round(1),
          "Table view — class share by site (%)",
          fmt={g: "{:.1f}%" for g in cfg.GROUP_ORDER if g in mix.columns})

    T.section("Junction mode against area mode")
    T.tiles([
        T.tile(f"Junction mode — {len(cfg.junction_cameras())} sites", "",
               detail='<span style="font-size:.85rem;line-height:1.55">'
                      'Lane-marked carriageway with a signal to control. '
                      'Reports per-lane PCU density, speed, queue and an '
                      'adaptive timing recommendation.<br><br><b>'
                      + ", ".join(C.short_name(c)
                                  for c in cfg.junction_cameras())
                      + "</b></span>"),
        T.tile(f"Area mode — {len(cfg.area_cameras())} sites", "",
               detail='<span style="font-size:.85rem;line-height:1.55">'
                      'Market streets, a depot platform, a station forecourt. '
                      'No lane discipline and no signal, so reporting "lane '
                      'density" here would be inventing a number. Zone '
                      'occupancy, crowding and bottleneck alerts instead.'
                      '<br><br><b>'
                      + ", ".join(C.short_name(c) for c in cfg.area_cameras())
                      + "</b></span>"),
    ], per_row=2)

    T.section("What this dataset cannot tell us")
    st.markdown(
        '<div class="note" style="max-width:86ch;font-size:.86rem">'
        'Converting pixels to metres needs one true ground reference — a '
        'surveyed width, the camera\'s height and focal length, or a landmark '
        'of known size. This dataset ships none, and three independent '
        'attempts to recover the scale disagreed with each other by a factor '
        'of two to four:<br><br>'
        '• assuming the traced carriageway is N lanes × 3.5 m — but the traced '
        'polygons are not constant-width road; one follows a works-zone '
        'narrowing;<br>'
        '• regressing on known vehicle widths — biased in the near field, '
        'because a box\'s width is set by the vehicle\'s widest point at '
        'roughly waist height, not by its ground contact;<br>'
        '• fitting the horizon from road-edge convergence — the traced edges '
        'do not converge at a rate consistent with either of the above.<br><br>'
        'Rather than pick the most flattering of the three, everything in '
        'metres, km/h or PCU/km/lane is marked <b>~</b> and treated as an '
        'estimate carrying an unknown multiplier. The congestion ranking, the '
        'level of service bands, the saturation flags and the signal '
        'before/after comparison are all scale-free and are unaffected. One '
        'measured road width per camera promotes the rest to measured values, '
        'and that is a configuration change, not a code change.</div>',
        unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------

# One YOLO instance per process, shared with the API, guarded by a lock.
# `detector_ready` is checked before any button that would load the detector,
# because the alternative is a raw FileNotFoundError traceback mid-page.
get_detector = S.get_detector
detector_ready = S.detector_ready


def _detector_missing_notice(path: str):
    T.callout(
        f"The detection weights are not on this machine, so nothing can be "
        f"detected until they are downloaded. One command, about 55 MB:"
        f"<br><br><code>python scripts/download_models.py</code><br><br>"
        f"Expected at <code>{path}</code>. Everything else in this dashboard "
        f"— the measured metrics, the signal timing, the alerts, the forecasts "
        f"— reads from CSVs that are already on disk and works without them.",
        title="Detection model not downloaded", accent=T.status("warning"))


def _rerun_camera(camera_id: str, stride: int = 4):
    """Re-measure one camera in place, so a new scale takes effect immediately.

    Speed and density are computed *during* the pass, from per-row ground
    distances — they are not a constant the stored CSV can simply be multiplied
    by. So a new scale genuinely requires re-measuring, and this does it here
    rather than sending the operator back to a terminal.
    """
    from analyze import analyze
    ok, model_path = detector_ready()
    if not ok:
        _detector_missing_notice(model_path)
        return None

    with st.status(f"Re-measuring {cfg.camera_name(camera_id)}…",
                   expanded=True) as status:
        st.write("Loading the detector…")
        det = get_detector()
        st.write("Detecting, tracking and measuring the whole clip. "
                 "This is the same pass analyze.py runs.")
        with S.DETECTOR_LOCK:
            df = analyze(camera_id, det, stride=stride)
        from analyze import rebuild_combined
        rebuild_combined()
        status.update(label=f"Re-measured — {len(df)} windows written.",
                      state="complete", expanded=False)
    return df


def view_calibration():
    state = CAL.load_state()
    cams = [c for c in cfg.CAMERAS if Z.load(c) is not None]
    done = [c for c in cams if cfg.scale_is_verified(c)]

    left, right = st.columns([2, 3], gap="large")
    with left:
        T.hero("Cameras with a measured scale", f"{len(done)}",
               f"/ {len(cams)}",
               "Until a camera has one real-world distance behind it, its "
               "speeds and PCU densities are estimates carrying an unknown "
               "multiplier. Everything else the system reports is scale-free "
               "and unaffected.")
    with right:
        T.tiles([
            T.tile("What it fixes", "",
                   detail="<b>km/h</b> · <b>PCU/km/lane</b> · <b>PCU/hr</b> · "
                          "zone areas in m² — all promoted from <b>~estimate</b> "
                          "to measured."),
            T.tile("What it needs", "1", " measurement",
                   detail="The real width of the road across one marked line. "
                          "Two lines at different depths is better — two "
                          "determine the horizon, and the horizon is what sets "
                          "every speed."),
        ], per_row=2)

    T.section("How to do it", "about two minutes, no site visit needed")
    T.steps([
        "<b>Pick a camera below.</b> The system draws two or three candidate "
        "measurement lines straight across the carriageway and tells you how "
        "many pixels wide each one is.",
        "<b>Measure one of those lines for real.</b> Open Google Maps, switch "
        "to satellite, find the same stretch of road, right-click → "
        "<b>Measure distance</b>, and drag across the carriageway between the "
        "same two edges the line touches. A tape measure on site works too.",
        "<b>Type the metres in.</b> Leave a line at 0 to skip it. Supplying "
        "two lines at different depths removes the one remaining assumption.",
        "<b>Apply.</b> The perspective model is fitted, checked for physical "
        "plausibility, and stored. Then re-measure the clip and the "
        "<b>~</b> prefixes disappear.",
    ])

    T.section("Camera")
    cam = st.selectbox("Camera", cams, format_func=cfg.camera_name,
                       key="cal_cam", label_visibility="collapsed")
    calib = Z.load(cam)
    verified = cfg.scale_is_verified(cam)
    record = state["measurements"].get(cam)

    st.markdown(
        (T.chip("Scale measured", T.status("good")) if verified
         else T.chip("Scale not measured", T.status("warning")))
        + (f' <span class="kv">&nbsp;recorded '
           f'{record["applied_at"][:10]}</span>' if record else ""),
        unsafe_allow_html=True)

    rows = CAL.suggested_rows(calib)
    if not rows:
        st.error("No carriageway zones are traced on this camera, so there is "
                 "nothing to measure across.")
        return

    img_col, form_col = st.columns([3, 2], gap="large")

    with img_col:
        guide = CAL.guide_image(cam, rows, calib)
        if guide is not None:
            st.image(guide, width="stretch",
                     caption="Measure the real width along any coloured line, "
                             "between the two square end markers.")
        else:
            st.warning("No reference frame for this camera — run "
                       "`python src/reference_frames.py`.")

    with form_col:
        st.markdown('<div class="kv"><b>Measured widths</b></div>',
                    unsafe_allow_html=True)
        prior = {int(r): float(w) for r, w in (record or {}).get("rows", [])}
        entered: list[tuple[int, float]] = []
        for i, y in enumerate(rows):
            px = CAL.road_pixel_width(calib, y)
            val = st.number_input(
                f"Line {i + 1} — row y={y} ({px} px wide)",
                min_value=0.0, max_value=200.0, step=0.1,
                value=float(prior.get(y, 0.0)),
                key=f"cal_w_{cam}_{y}",
                help="Real width in metres across this line. 0 = skip.")
            if val > 0:
                entered.append((y, float(val)))

        st.divider()
        c1, c2 = st.columns(2)
        height_m = c1.number_input(
            "Camera height (m)", min_value=2.0, max_value=25.0, step=0.5,
            value=float((record or {}).get("camera_height_m", 7.0)),
            key=f"cal_h_{cam}",
            help="Mounting height of the camera above the road. Typical "
                 "Indian traffic-camera poles are 6–9 m.")
        hfov = c2.number_input(
            "Field of view (°)", min_value=10.0, max_value=140.0, step=1.0,
            value=float((record or {}).get("hfov_deg", 60.0)),
            key=f"cal_f_{cam}",
            help="Horizontal field of view from the camera's spec sheet. "
                 "60° is typical for a fixed 1080p traffic camera.")

        st.caption(
            f"Height and field of view together set how fast ground distance "
            f"grows up the frame — {hfov:.0f}° works out to "
            f"{CAL.focal_px_from_hfov(hfov, calib.frame_size[0]):.0f} px focal "
            f"length. Both are assumptions; the measured width is not.")

        preview = st.button("Check the fit", key="check_scale",
                            width="stretch", disabled=not entered)
        apply_it = st.button("Apply and re-measure this camera",
                             key="apply_scale", type="primary",
                             width="stretch", disabled=not entered)

    if not entered:
        T.callout(
            "Enter at least one measured width above to fit the scale. "
            "Nothing is changed until you press a button.",
            accent=T.P()["axis"])

    if entered and (preview or apply_it):
        result = CAL.fit_scale(cam, entered, camera_height_m=height_m,
                               hfov_deg=hfov, calib=calib)
        if not result.ok:
            st.error(result.message)
            return

        T.section("Fitted model", result.message)
        for w in (result.warnings or []):
            T.callout(w, title="Check this", accent=T.status("warning"))

        c1, c2 = st.columns([1, 1], gap="large")
        with c1:
            st.markdown('<div class="kv"><b>Ground scale by image row</b>'
                        '</div>', unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(result.rows).rename(columns={
                "row": "Image row", "road_px": "Road (px)",
                "road_m": "Road (m)",
                "lateral_cm_per_px": "Across (cm/px)",
                "longitudinal_cm_per_px": "Along (cm/px)",
                "anisotropy": "Along ÷ across",
            }), width="stretch", hide_index=True)
            T.note(
                "The last column is why an isotropic scale gives wrong speeds: "
                "one pixel up the road covers several times the ground distance "
                "of one pixel across it, and vehicles approaching a traffic "
                "camera move almost entirely up the road.")
        with c2:
            st.markdown('<div class="kv"><b>What each zone becomes</b></div>',
                        unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(result.zone_areas).rename(columns={
                "zone": "Zone", "kind": "Kind", "area_m2": "Area (m²)",
                "road_length_m": "≈ road length (m)",
            }), width="stretch", hide_index=True)
            T.note(
                "A sanity check you can make by eye: does that road length "
                "match what the camera actually sees? A single view rarely "
                "covers more than about 150 m.")

        if apply_it:
            CAL.apply(cam, result, mark_verified=True, measurements=entered,
                      camera_height_m=height_m, hfov_deg=hfov)
            st.success("Scale stored. Re-measuring the clip so the new figures "
                       "take effect…")
            _rerun_camera(cam)
            st.success(
                f"Done. {cfg.camera_name(cam)} now reports measured speeds and "
                f"densities — the `~` prefixes are gone across the dashboard.")
            st.balloons()

    T.section("Calibration state", "every camera")
    rows_state = []
    for c in cams:
        m = state["measurements"].get(c)
        cal = Z.load(c)
        rows_state.append({
            "Camera": cfg.camera_name(c),
            "Mode": cfg.camera_meta(c)["mode"],
            "Scale": "measured" if cfg.scale_is_verified(c) else "estimated",
            "Widths supplied": ", ".join(f"y={int(r)}→{w:g} m"
                                         for r, w in (m or {}).get("rows", []))
                              or "—",
            "Camera height (m)": (m or {}).get("camera_height_m"),
            "FOV (°)": (m or {}).get("hfov_deg"),
            "Applied": (m or {}).get("applied_at", "")[:16].replace("T", " "),
            # A seeded model exists on the junction cameras from first-pass
            # calibration, but it rests on assumed widths — worth distinguishing
            # from one that a real measurement stands behind.
            "Model": ("from measurement" if m else
                      "seeded assumption" if (cal and cal.scale_model) else "—"),
        })
    st.dataframe(pd.DataFrame(rows_state), width="stretch", hide_index=True)

    with st.expander("Undo a calibration"):
        undo = st.selectbox("Camera", [c for c in cams
                                       if cfg.scale_is_verified(c)] or ["—"],
                            format_func=lambda c: cfg.camera_name(c)
                            if c != "—" else "— none calibrated —",
                            key="cal_undo")
        if undo != "—" and st.button("Remove the stored scale", key="undo_scale"):
            CAL.clear(undo)
            _rerun_camera(undo)
            st.success(f"{cfg.camera_name(undo)} is back to scale-free "
                       f"reporting.")
            st.rerun()


# --------------------------------------------------------------------------
# Analyse uploaded footage
# --------------------------------------------------------------------------

def _upload_report(res, scale_verified: bool):
    """The full report for an analysed clip."""
    df = res.metrics
    if df.empty:
        st.error("No measurement windows were produced. The clip may be "
                 "shorter than one window.")
        return

    est = "" if scale_verified else "~"
    over_cap = float(df.los.isin(["Heavy", "Jam"]).mean() * 100)
    worst = df.loc[df.congestion_index.idxmax()]
    sat = int(df.saturated.sum()) if "saturated" in df else 0

    T.section("Report", f"{len(df)} windows · {res.seconds_covered:.0f}s of "
                        f"footage · {res.frames_processed} frames in "
                        f"{res.elapsed_s:.0f}s")

    left, right = st.columns([2, 3], gap="large")
    with left:
        T.hero("Time at or beyond capacity", f"{over_cap:.0f}", "%",
               f"Worst moment: <b>{worst.zone_label}</b> at an index of "
               f"{worst.congestion_index:.2f} "
               f"({worst.window_start_s:.0f}s into the clip).")
    with right:
        mean_ci = float(df.congestion_index.mean())
        T.tiles([
            T.tile("Mean congestion", f"{mean_ci:.2f}",
                   meter=(mean_ci, T.los_color(T.los_of(mean_ci))),
                   detail=T.los_of(mean_ci) + " overall"),
            T.tile("Zones", f"{df.zone_id.nunique()}",
                   detail=f"{res.calib.mode} mode"),
            T.tile("Vehicles seen", f"{sum(res.group_counts.values()):,}",
                   detail="detections across all processed frames"),
            T.tile("Saturated", f"{sat}",
                   meter=(sat / max(1, len(df)), T.status("critical")),
                   detail=f"{sat / max(1, len(df)) * 100:.0f}% of windows where "
                          f"counting breaks down"),
        ], per_row=4)

    if res.preview is not None:
        T.section("Detection")
        c1, c2 = st.columns([3, 2], gap="large")
        c1.image(res.preview, width="stretch",
                 caption="A processed frame — zones shaded, detections boxed")
        with c2:
            mix = pd.DataFrame(
                [{"Class group": g, "Detections": n}
                 for g, n in sorted(res.group_counts.items(),
                                    key=lambda kv: -kv[1])])
            st.markdown('<div class="kv"><b>What was on the road</b></div>',
                        unsafe_allow_html=True)
            st.dataframe(mix, width="stretch", hide_index=True)
            T.note("Classes come from UVH-26, fine-tuned on Indian traffic "
                   "CCTV, so three-wheelers are a first-class class rather "
                   "than being filed as cars.")

    T.section("Level of service over time")
    T.show(C.zone_los_timeline(df, res.session))

    T.section("Trend")
    prof = fc.time_profile(df, res.session, bin_s=10.0)
    if not prof.empty:
        options = [m for m in ("congestion_index", "occupancy",
                               "density_pcu_per_km_lane", "speed_kmph",
                               "flow_pcu_per_hr")
                   if m in prof.columns and prof[m].notna().any()]
        fcol, _ = st.columns([1, 2])
        metric = fcol.selectbox("Metric", options,
                                format_func=lambda m: C.METRIC_AXIS[m][0],
                                key="up_metric", label_visibility="collapsed")
        T.show(C.zone_trend(prof, metric))

    T.section("Counting against occupancy", "does detection hold up here?")
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        T.show(C.count_vs_occupancy(df, res.session))
    with c2:
        T.note(
            f"Two panels, one time axis — never two y-scales. This clip spends "
            f"<b>{sat / max(1, len(df)) * 100:.0f}%</b> of its windows "
            f"saturated: packed densely enough that vehicles stop being "
            f"individually separable. Where the top panel sags while the bottom "
            f"stays high, the detector is losing vehicles to occlusion and "
            f"occupancy is the measure that still holds.")

    alerts = bn.detect(df, res.session)
    T.section("Bottlenecks", f"{len(alerts)} raised" if alerts else "none raised")
    if not alerts:
        st.success("No sustained bottleneck pattern in this footage.")
    else:
        counts = {s: sum(a.severity == s for a in alerts)
                  for s in ("critical", "warning", "info")}
        T.tiles([T.tile(k.capitalize(), str(v)) for k, v in counts.items()],
                per_row=3)
        for a in alerts[:8]:
            col = T.severity_color(a.severity)
            st.markdown(
                f'<div class="alert" style="--sev:{col}">'
                f'{T.chip(a.severity.upper(), col)} '
                f'<span class="alert-m">&nbsp;{a.start_s:.0f}–{a.end_s:.0f}s '
                f'({a.duration_s:.0f}s)</span>'
                f'<div class="alert-h">{a.headline}</div>'
                f'<div class="alert-b"><b>Evidence</b> — {a.evidence}</div>'
                f'<div class="alert-b"><b>Recommendation</b> — '
                f'{a.recommendation}</div></div>', unsafe_allow_html=True)

    forecasts = fc.forecast_all(df, horizon_s=60.0)
    if forecasts:
        T.section("Outlook", "60 seconds ahead")
        T.show(C.forecast_chart(forecasts, 60.0))
        T.note("Hollow markers are zones whose trend does not beat their own "
               "noise — shown, but not claimed as a direction.")

    scale_banner(camera_id=res.session)

    T.section("Take it away")
    c1, c2, c3 = st.columns(3)
    c1.download_button("Metrics CSV", df.to_csv(index=False).encode(),
                       file_name=f"{res.media.path.stem}_metrics.csv",
                       mime="text/csv", width="stretch")
    import report_html
    c2.download_button(
        "Full report (HTML)",
        report_html.build(res, alerts, forecasts, scale_verified).encode("utf-8"),
        file_name=f"{res.media.path.stem}_report.html",
        mime="text/html", width="stretch")
    if c3.button("Keep alongside the dataset cameras", width="stretch",
                 help="Writes the metrics CSV into outputs/logs/ so this clip "
                      "appears in the Network view on the next reload."):
        p = ingest.persist(res)
        st.success(f"Written to `{p.name}`. It now appears in the Network "
                   f"view.")


def _image_report(res):
    """What a single frame can honestly support, and an explicit list of what
    it cannot."""
    T.section("Report", "single frame")
    zdf = pd.DataFrame(res.zones)
    total_pcu = float(zdf.pcu.sum())
    n = int(zdf.n_detections.sum())
    worst = zdf.loc[zdf.congestion_index.idxmax()] if len(zdf) else None

    left, right = st.columns([2, 3], gap="large")
    with left:
        T.hero("Occupancy at this instant",
               f"{float(zdf.congestion_index.max()):.2f}" if worst is not None else "—",
               detail=(f"Busiest zone: <b>{worst.zone_label}</b> — "
                       f"{worst.los}." if worst is not None else ""))
    with right:
        T.tiles([
            T.tile("Vehicles", f"{n}", detail="detected in this frame"),
            T.tile("Traffic load", f"{total_pcu:.1f}", " PCU",
                   detail="Passenger Car Units, IRC:106-1990"),
            T.tile("Zones", f"{len(zdf)}", detail=res.calib.mode + " mode"),
        ], per_row=3)

    c1, c2 = st.columns([3, 2], gap="large")
    if res.annotated is not None:
        c1.image(res.annotated, width="stretch",
                 caption="Zones shaded, detections boxed")
    with c2:
        T.callout(
            "A still frame fixes an <b>instant</b>, not a <b>rate</b>. "
            "Everything below needs two moments in time, so it is reported as "
            "unavailable rather than estimated:<br><br>"
            + " · ".join(f"<b>{x}</b>" for x in res.not_measurable)
            + "<br><br>Upload a video clip instead and all of it becomes "
              "measurable.",
            title="What one frame cannot tell you",
            accent=T.status("warning"))

    T.section("By zone")
    st.dataframe(zdf.rename(columns={
        "zone_label": "Zone", "zone_kind": "Kind",
        "n_detections": "Vehicles", "pcu": "PCU",
        "real_area_m2": "Area (m²)",
        "density_pcu_per_km_lane": "~Density (PCU/km/lane)",
        "box_occupancy": "Box cover", "structural_occupancy": "Occupancy",
        "los": "Level of service",
    }).drop(columns=["zone_id", "congestion_index",
                     "density_pcu_per_100m2"], errors="ignore"),
        width="stretch", hide_index=True)

    if res.class_counts:
        T.section("Classes detected")
        st.dataframe(pd.DataFrame(
            [{"Class": k, "Count": v, "PCU each": cfg.PCU_FACTORS.get(k, 1.0)}
             for k, v in sorted(res.class_counts.items(), key=lambda kv: -kv[1])]),
            width="stretch", hide_index=True)


def view_upload():
    T.callout(
        "Drop in a clip or a still from any camera — it does not have to be "
        "one of the seven. The pipeline finds the road from where vehicles "
        "actually travel, measures the same way it measures the dataset "
        "cameras, and gives you a report you can download.",
        title="Analyse footage the system has never seen")

    ok, model_path = detector_ready()
    if not ok:
        _detector_missing_notice(model_path)
        return

    T.section("1 · Source")
    up = st.file_uploader(
        "Video or image",
        type=sorted(s.lstrip(".") for s in
                    (ingest.VIDEO_SUFFIXES | ingest.IMAGE_SUFFIXES)),
        key="up_file", label_visibility="collapsed",
        help="MP4/AVI/MOV video, or JPG/PNG still. Up to 1 GB.")

    # Nothing uploaded yet: say what comes out the other end, and offer a clip
    # so the flow can be tried without going and finding a file first.
    if up is None and "up_path" not in st.session_state:
        c1, c2 = st.columns([3, 2], gap="large")
        with c1:
            T.tiles([
                T.tile("From a video", "",
                       detail="Per-zone <b>PCU density</b>, <b>speed</b>, "
                              "<b>queue</b> and <b>flow</b>; level of service "
                              "second by second; saturation flags; sustained "
                              "<b>bottleneck alerts</b> with evidence and a "
                              "recommendation; a 60-second <b>forecast</b> with "
                              "prediction intervals; and a downloadable "
                              "report."),
                T.tile("From a still", "",
                       detail="Vehicle count and class mix, <b>PCU load</b>, "
                              "zone occupancy and level of service at that "
                              "instant. Speed, flow, queue and forecast need "
                              "two moments in time, so a single frame cannot "
                              "support them and they are reported as "
                              "unavailable rather than estimated."),
            ], per_row=2)
        with c2:
            T.callout(
                "No file to hand? Run one of the dataset clips through the "
                "same path a stranger's upload takes — zones found "
                "automatically from vehicle movement, not from the hand-traced "
                "polygons the Site detail view uses.",
                title="Try it")
            demo = st.selectbox("Demo clip", list(cfg.CAMERAS),
                                format_func=cfg.camera_name, key="up_demo",
                                label_visibility="collapsed")
            if st.button("Load this clip", key="load_demo", width="stretch"):
                st.session_state["up_key"] = f"demo:{demo}"
                st.session_state["up_path"] = str(cfg.camera_video_path(demo))
                st.session_state.pop("up_roi", None)
                st.session_state.pop("up_result", None)
                st.rerun()
        return

    if up is not None:
        key = f"{up.name}:{up.size}"
        if st.session_state.get("up_key") != key:
            st.session_state["up_key"] = key
            st.session_state["up_path"] = str(
                ingest.save_upload(up.getvalue(), up.name))
            st.session_state.pop("up_roi", None)
            st.session_state.pop("up_result", None)

    media = ingest.probe(Path(st.session_state["up_path"]))
    if not media.ok:
        st.error(media.error)
        return

    src_name = up.name if up is not None else Path(
        st.session_state["up_path"]).name
    size_mb = Path(st.session_state["up_path"]).stat().st_size / 1e6
    c1, c2 = st.columns([4, 1])
    c1.markdown(
        T.chip(media.kind.upper(), T.series(0))
        + f' <span class="kv">&nbsp;{src_name} — {media.label}, '
          f'{size_mb:.1f} MB</span>', unsafe_allow_html=True)
    if c2.button("Clear", key="up_clear", width="stretch"):
        for k in ("up_key", "up_path", "up_roi", "up_result"):
            st.session_state.pop(k, None)
        st.rerun()

    # ---- scene setup -----------------------------------------------------
    T.section("2 · Scene")
    c1, c2, c3 = st.columns([1, 1, 1])
    n_lanes = c1.number_input(
        "Marked lanes on this road", min_value=0, max_value=8, value=0,
        key="up_lanes",
        help="0 treats the road as one shared space (area mode) — the honest "
             "default for footage we know nothing about. Give a lane count "
             "only if the carriageway really is lane-marked; the corridor is "
             "then split into that many bands and per-lane density becomes "
             "meaningful.")
    method = c2.selectbox(
        "Find the road by", ["Where vehicles travel", "Whole frame"],
        key="up_method",
        help="The first samples the clip, detects vehicles and accumulates "
             "their ground-contact points — the same empirical check used to "
             "validate the dataset cameras' hand-traced zones.")
    seconds = c3.number_input(
        "Seconds to analyse", min_value=5, max_value=1800,
        value=int(min(120, max(10, media.duration_s or 60))),
        key="up_seconds",
        help="Cap the run. The whole clip is fine; long uploads just take "
             "longer.") if media.kind == "video" else None

    if st.button("Find the road", key="find_road", width="stretch"):
        if method == "Whole frame" or media.kind == "image":
            import numpy as _np
            mask = _np.full((media.height, media.width), 255, _np.uint8)
            roi = ingest.RoiResult(
                mask, [(0, 0), (media.width - 1, 0),
                       (media.width - 1, media.height - 1),
                       (0, media.height - 1)], 1.0, 0, "whole-frame",
                note="Whole frame used as one zone, as requested. Occupancy "
                     "will include everything in view, not just carriageway.")
        else:
            bar = st.progress(0.0, "Sampling the clip…")
            roi = ingest.auto_roi(
                media, get_detector(), samples=60,
                on_progress=lambda f, m: bar.progress(min(1.0, f), m))
            bar.empty()
        st.session_state["up_roi"] = roi

    roi = st.session_state.get("up_roi")
    if roi is None:
        T.note("Press <b>Find the road</b> to continue. Nothing is analysed "
               "until you do.")
        return

    session = ingest.session_id(Path(st.session_state["up_path"]))
    calib = ingest.build_calibration(session, roi,
                                     (media.width, media.height),
                                     n_lanes=int(n_lanes))
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        prev = ingest.preview_zones(media, calib, roi)
        if prev is not None:
            st.image(prev, width="stretch",
                     caption="Zones the analysis will use")
    with c2:
        T.tiles([
            T.tile("Road found by", roi.method.replace("-", " "),
                   detail=roi.note),
            T.tile("Coverage", f"{roi.coverage * 100:.0f}", "% of frame",
                   meter=(roi.coverage, T.series(0)),
                   detail=f"{len(calib.zones)} zone(s), {calib.mode} mode"),
        ], per_row=2)
        if roi.method == "whole-frame":
            T.callout(
                "Occupancy is normalised against absolute bounds measured on "
                "carriageway. Applied to a whole frame that includes sky and "
                "buildings it stays internally consistent but is no longer "
                "comparable with the dataset cameras.",
                title="Read with care", accent=T.status("warning"))

    # ---- run -------------------------------------------------------------
    T.section("4 · Analyse")
    if media.kind == "image":
        if st.button("Analyse this frame", key="run_analysis", type="primary",
                     width="stretch"):
            with st.spinner("Detecting…"):
                st.session_state["up_result"] = ingest.analyze_image(
                    media, calib, get_detector())
    else:
        c1, c2 = st.columns([1, 3])
        stride = c1.selectbox(
            "Sampling", [2, 3, 4, 6, 8], index=1, key="up_stride",
            format_func=lambda s: f"every {s}{'nd' if s == 2 else 'rd' if s == 3 else 'th'} frame",
            help="Every 3rd frame of 25 fps footage is ~8 measurements a "
                 "second — ample for density and queue metrics, and it keeps "
                 "the run short.")
        est = (media.duration_s and seconds and
               min(seconds, media.duration_s) * (media.fps or 25) / stride / 12)
        c2.markdown(f'<div class="note" style="margin-top:1.9rem">'
                    f'About <b>{est:.0f} s</b> of processing at ~12 frames a '
                    f'second on this machine.</div>' if est else "",
                    unsafe_allow_html=True)

        if st.button("Run the full analysis", key="run_analysis",
                     type="primary", width="stretch"):
            bar = st.progress(0.0, "Starting…")
            res = ingest.analyze_video(
                media, calib, get_detector(), stride=int(stride),
                max_seconds=float(seconds),
                on_progress=lambda f, m: bar.progress(min(1.0, f), m))
            bar.empty()
            st.session_state["up_result"] = res

    res = st.session_state.get("up_result")
    if res is None:
        return
    if isinstance(res, ingest.ImageResult):
        _image_report(res)
    else:
        _upload_report(res, cfg.scale_is_verified(res.session))


# --------------------------------------------------------------------------
# Max-pressure
# --------------------------------------------------------------------------

_mp_movements_from_metrics = S.mp_movements_from_metrics
_fluid_tables = S.fluid_tables


def _fluid_section():
    """Max-pressure on real signalised intersections — the part that is measured."""
    import fluid as FL

    if not FL.available():
        T.callout(
            "The FLUID dataset is not on this machine. It is the dataset that "
            "carries what the Surat cameras cannot: a whole signalised "
            "intersection in one aerial frame, so every approach <b>and</b> "
            "every receiving link is visible at once, plus per-second signal "
            "state and measurable turn ratios.<br><br>"
            "<b>FLUID</b> — figshare DOI <code>10.6084/m9.figshare.29974954</code>, "
            "CC BY 4.0, ~18 GB. Set <code>FLUID_ROOT</code> if it lives outside "
            "the project folder.",
            title="Real-intersection data not found", accent=T.status("warning"))
        return

    recs = FL.recordings()
    c1, c2, c3 = st.columns([2, 1, 1])
    rid = c1.selectbox("Recording", [r.id for r in recs],
                       format_func=lambda i: f"{FL.SITE_LABELS.get(i.split('/')[0], i)}  ·  {i.split('/')[1]}",
                       key="fl_rec")
    min_green = c2.number_input("Minimum green (s)", 5, 60, 15, 1, key="fl_mg",
                                help="The pedestrian floor the controller must "
                                     "respect. Max-pressure without one flickers.")
    downstream = c3.selectbox(
        "Downstream term", [FL.DOWNSTREAM_QUEUE, FL.DOWNSTREAM_OCCUPANCY],
        format_func=lambda m: {"queue": "Stopped queue (Varaiya)",
                               "occupancy": "All vehicles on link"}[m],
        key="fl_ds",
        help="Varaiya's x_m is a queue — stopped vehicles on the receiving "
             "link. At an uncongested exit that is almost always zero, so the "
             "occupancy variant is offered to show how much the downstream "
             "term is actually doing.")

    tables = _fluid_tables(rid)
    if tables is None:
        st.error("Could not load that recording.")
        return
    queues, sig, turns = tables
    rec = FL.find(rid)

    # ---- what the data contains -----------------------------------------
    cat = FL.phase_catalogue(sig)
    cyc = FL.cycle_length_s(sig)
    qi = queues.groupby("t").queue_in_pcu.sum()
    qo = queues.groupby("t").queue_out_pcu.max()
    T.tiles([
        T.tile("Signal cycle", f"{cyc:.0f}", " s",
               detail=f"{len(cat)} phases · {int(sig.cycle.nunique())} cycles "
                      f"recorded"),
        T.tile("Movements measured", f"{queues.movement.nunique()}",
               detail="each with its own approach and receiving link"),
        T.tile("Approach queue", f"{qi.mean():.0f}", " PCU",
               detail=f"mean; peak {qi.max():.0f}"),
        T.tile("Receiving-link queue", f"{qo.mean():.1f}", " PCU",
               meter=(min(1.0, qo.mean() / max(1e-6, qi.mean())), T.series(1)),
               detail="<b>the term the Surat cameras cannot see</b>"),
    ], per_row=4)

    T.section("Measured turn ratios", "unobservable from a single approach camera")
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        T.show(C.turn_ratio_chart(turns))
    with c2:
        T.note(
            "Max-pressure needs <b>r(m,k)</b> — of everything leaving link m, "
            "what share goes where. A camera watching one approach sees "
            "vehicles arrive and cannot see where they go, so this is the "
            "<i>second</i> reason the Surat network cannot support the "
            "algorithm, after the missing downstream queue. Here it is counted "
            "directly from each vehicle's entry and exit direction.")
        table(turns[["entry_direction", "exit_direction", "vehicles",
                     "turn_ratio"]].round(3), "Table view — turn ratios")

    # ---- the counterfactual ---------------------------------------------
    T.section("What max-pressure would have done", "replayed against the real signal")
    with st.spinner("Replaying the recording second by second…"):
        res = FL.replay(rec, queues, sig, turns, min_green_s=float(min_green),
                        downstream=downstream)

    left, right = st.columns([2, 3], gap="large")
    with left:
        T.hero("Green spent with nothing to relieve",
               f"{res.wasted_green_pct_actual:.0f}", "%",
               detail=f"That is the real fixed-time signal holding green on a "
                      f"phase whose pressure was zero or negative — the queue "
                      f"had cleared, or the link it discharges into was fuller "
                      f"than the approach. Max-pressure: "
                      f"<b>{res.wasted_green_pct_maxpressure:.0f}%</b>.")
    with right:
        T.tiles([
            T.tile("Mode", res.mode.replace("-", " "),
                   detail="every downstream queue observed — this is genuine "
                          "max-pressure, not the degraded form"),
            T.tile("Mean pressure served", f"{res.mean_pressure_actual:.2f}",
                   detail=f"real signal · max-pressure "
                          f"<b>{res.mean_pressure_maxpressure:.2f}</b> · "
                          f"best possible {res.mean_pressure_best:.2f}"),
            T.tile("Agreement", f"{res.agreement_pct:.0f}", "%",
                   detail=f"of {res.steps:,} seconds, the real signal was "
                          f"serving the phase max-pressure would have chosen"),
        ], per_row=3)

    T.callout(
        "<b>What this is, exactly.</b> The queues are the ones that really "
        "formed, under the real signal, measured from drone trajectories. So "
        "this can establish — by direct observation — that the fixed plan often "
        "held green on an approach with nothing to discharge, and which phase "
        "max-pressure would have served instead.<br><br>"
        "<b>What it is not.</b> It is not a claim that max-pressure would have "
        "reduced delay by any particular amount. Had it actually run, the "
        "queues would have evolved differently, and saying otherwise needs a "
        "traffic model rather than a replay. That number is available in the "
        "simulator below, where the counterfactual is explicit.",
        title="Read this before quoting the figure")

    T.section("Where the green went")
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        T.show(C.phase_share_chart(res.phase_share_actual,
                                   res.phase_share_maxpressure))
    with c2:
        diffs = sorted(
            ((k, res.phase_share_maxpressure.get(k, 0) - res.phase_share_actual.get(k, 0))
             for k in set(res.phase_share_actual) | set(res.phase_share_maxpressure)),
            key=lambda kv: -abs(kv[1]))
        top = diffs[0] if diffs else ("—", 0)
        T.note(
            f"The clearest disagreement is <b>{top[0]}</b>: max-pressure would "
            f"give it <b>{top[1]:+.0f} percentage points</b> of green relative "
            f"to the fixed plan. A fixed cycle divides time by a plan set once; "
            f"max-pressure divides it by what is actually queued.")
        table(pd.DataFrame([{
            "Phase": k,
            "Real signal (%)": round(res.phase_share_actual.get(k, 0), 1),
            "Max-pressure (%)": round(res.phase_share_maxpressure.get(k, 0), 1),
        } for k, _ in diffs]), "Table view — phase share")

    T.section("Queues and pressure through the recording")
    T.show(C.fluid_timeline(res.history))
    T.note(
        "The upper trace is the total approach queue actually observed. Below "
        "it, the pressure the real signal was relieving at each second against "
        "the best available at that moment — the gap between them is the "
        "headroom a responsive controller could have used.")

    with st.expander("Phase structure recovered from the recorded signal"):
        st.dataframe(pd.DataFrame([{
            "Phase": p["phase_key"],
            "Movements served": ", ".join(p["movements"]),
            "Times run": p["times_run"],
            "Mean green (s)": round(p["mean_green_s"], 1),
            "Total green (s)": round(p["total_green_s"]),
        } for p in cat]), width="stretch", hide_index=True)
        T.note(
            "FLUID records one row per signal group per state interval, not a "
            "phase table. Groups that turn green at the same instant belong to "
            "the same phase, so grouping the green intervals by start time "
            "recovers the controller's real staging — which is then what "
            "max-pressure chooses between, so the comparison is like-for-like.")

    st.download_button(
        "Download this replay (CSV)",
        pd.DataFrame(res.history).to_csv(index=False).encode(),
        file_name=f"maxpressure_replay_{rid.replace('/', '_')}.csv",
        mime="text/csv")


def _fluid_summary() -> pd.DataFrame:
    """Headline validation across every site (replays three recordings)."""
    with st.spinner("Replaying max-pressure against all three real signals…"):
        return S.fluid_summary()


def _fluid_headline():
    """The validation result, stated before any drill-down."""
    import fluid as FL
    d = _fluid_summary()
    if d.empty:
        return

    worst = d.loc[d.wasted_actual.idxmax()]
    lo, hi = d.wasted_actual.min(), d.wasted_actual.max()
    genuine = bool((d["mode"] == "max-pressure").all())

    T.section("Validation result", "max-pressure replayed against three real signals")
    left, right = st.columns([2, 3], gap="large")
    with left:
        T.hero("Real fixed-time green spent with nothing to relieve",
               f"{lo:.0f}–{hi:.0f}", "%",
               f"Across three real signalised intersections. Max-pressure, on "
               f"the same measured queues, does so "
               f"<b>{d.wasted_mp.min():.0f}–{d.wasted_mp.max():.0f}%</b> of "
               f"the time. Worst site: <b>{worst.label.split('—')[0].strip()}</b> "
               f"at {worst.wasted_actual:.0f}%.")
    with right:
        T.tiles([
            T.tile("Controller mode",
                   "max-pressure" if genuine else "degraded",
                   meter=(1.0 if genuine else 0.3,
                          T.status("good") if genuine else T.status("critical")),
                   detail="Every downstream queue measured, on every movement "
                          "— this is the real algorithm, not a fallback."),
            T.tile("Intersections", f"{len(d)}",
                   detail=" · ".join(d.site)),
            T.tile("Decision steps", f"{int(d.steps.sum()):,}",
                   detail="one second each, every phase re-ranked"),
        ], per_row=3)

    show = pd.DataFrame({
        "Intersection": d.label,
        "Cycle (s)": d.cycle_s.round(0),
        "Phases": d.phases,
        "Steps": d.steps,
        "Real signal — wasted green": d.wasted_actual.round(1).astype(str) + "%",
        "Max-pressure — wasted green": d.wasted_mp.round(1).astype(str) + "%",
        "Mean pressure served — real": d.press_actual.round(2),
        "Mean pressure served — MP": d.press_mp.round(2),
        "Agreement": d.agreement.round(1).astype(str) + "%",
    })
    st.dataframe(show, width="stretch", hide_index=True)

    T.show(C.fluid_validation_chart(d))

    T.note(
        "<b>Wasted green</b> is the share of green time spent on a phase whose "
        "pressure was zero or negative — the queue had already cleared, or the "
        "link it discharges into was fuller than the approach feeding it. "
        "<b>Mean pressure served</b> is how much queue each controller's choice "
        "was actually relieving, averaged over the run; max-pressure relieves "
        f"<b>{(d.press_mp / d.press_actual.clip(lower=1e-6)).min():.0f}–"
        f"{(d.press_mp / d.press_actual.clip(lower=1e-6)).max():.0f}×</b> more. "
        "<b>Agreement</b> is how often the real signal happened to pick the "
        "same phase max-pressure would have.<br><br>"
        "These are observations about the recorded signals, made from the "
        "recorded queues. They are deliberately <b>not</b> a delay-reduction "
        "claim: had max-pressure actually run, the queues would have evolved "
        "differently. The controlled counterfactual is under "
        "<b>Head-to-head</b>.")


_fluid_mix = S.fluid_mix


def view_mp_validation():
    """The headline: max-pressure replayed against three real signals."""
    T.callout(
        "This whole track is the <b>FLUID</b> dataset — 14 drone recordings of "
        "three real signalised intersections in Xuancheng, Anhui, obtained "
        "specifically so the max-pressure implementation could be validated "
        "on data that contains the downstream queue it is defined by. "
        "<b>No Surat figure appears anywhere in this track.</b><br><br>"
        "The whole intersection sits in one aerial frame, so every approach "
        "<i>and</i> every receiving link is measured at the same instant, turn "
        "ratios are counted per vehicle, and the recorded signal gives "
        "per-second ground truth to compare against.",
        title="What you are looking at", accent=T.series(0))

    _fluid_headline()

    # ---- 1. validated on real intersections ---------------------------
    T.section("Validated against the real signals",
              "FLUID — measured queues, measured turn ratios, recorded signal")
    _fluid_section()

    # ---- the simulator ---------------------------------------------------

def view_mp_pressure():
    """Phase pressure scores at one instant, on a chosen recording."""
    import maxpressure as MP
    import fluid as FL

    T.callout(
        "Max-pressure serves the phase with the greatest <b>queue difference</b> "
        "between an approach and the link it discharges into:<br><br>"
        "&nbsp;&nbsp;<span class='mono'>w(l,m) = x<sub>l</sub> − "
        "Σ<sub>k</sub> r<sub>mk</sub> x<sub>k</sub>"
        "&nbsp;&nbsp;·&nbsp;&nbsp;P(phase) = Σ s<sub>lm</sub> · w(l,m)</span>"
        "<br><br>It is <b>throughput-optimal</b>: it stabilises any demand the "
        "junction can physically clear, using only local queues and no demand "
        "forecast. The downstream term is what stops it discharging into a link "
        "that is already full — the spillback that gridlocks a network. "
        "<b>Varaiya (2013)</b>, <i>Transportation Research Part C</i> 36:177–195."
        "<br><br>Below is that ranking computed at a single instant, from "
        "measured queues, on a real intersection.",
        title="What max-pressure does")
    _mp_instant_panel()


def view_mp_scene():
    """The intersection itself: what the drone sees and where we measure.

    The counterpart of Site detail on the Webster track. Same question — *show
    me the place these numbers came from* — answered with the assets FLUID
    actually ships: an orthorectified aerial image, the 4K drone recording, and
    metre-scale trajectories that can be drawn straight onto both.
    """
    import fluid as FL

    if not FL.available():
        T.callout(
            "The FLUID dataset is not on this machine, so there is no scene to "
            "show. figshare DOI <code>10.6084/m9.figshare.29974954</code>; set "
            "<code>FLUID_ROOT</code> if it lives outside the project folder.",
            title="Drone data not found", accent=T.status("warning"))
        return

    recs = FL.recordings()
    c1, c2 = st.columns([2, 1])
    rid = c1.selectbox("Recording", [r.id for r in recs],
                       format_func=lambda i: (
                           f"{FL.SITE_LABELS.get(i.split('/')[0], i)}  ·  "
                           f"{i.split('/')[1]}"),
                       key="mp_scene_rec")
    rec = FL.find(rid)
    if rec is None:
        st.error("Could not load that recording.")
        return

    tables = _fluid_tables(rid)
    if tables is None:
        st.error("Could not build the queue table for that recording.")
        return
    queues, sig, turns = tables
    times = sorted(queues.t.unique())
    t = c2.select_slider("Instant (s into clip)", options=times,
                         value=times[min(len(times) - 1, len(times) // 3)],
                         key="mp_scene_t") if times else None

    w2p = FL.world_to_pixel(rid)
    T.tiles([
        T.tile("Aerial ground resolution",
               f"{w2p.px_per_m:.1f}" if w2p else "—", " px/m",
               detail=(f"Fitted from the trajectory file's own pixel and metre "
                       f"columns; median residual {w2p.residual_px:.1f} px. "
                       f"Distances here are true metres — unlike the Surat "
                       f"cameras, this needs no scale calibration."
                       if w2p else "Could not fit the world-to-pixel mapping.")),
        T.tile("Signal cycle", f"{FL.cycle_length_s(sig):.0f}", " s",
               detail=f"{len(FL.controller_phases(sig, queues))} vehicle phases, "
                      f"recorded per second"),
        T.tile("Drone video", "available" if rec.video_path else "not downloaded",
               detail=("3840×2160 at 10 fps — the footage the trajectories were "
                       "extracted from." if rec.video_path else
                       "The derived CSVs are enough for every number here; the "
                       "video is ~1 GB and optional.")),
    ], per_row=3)

    T.section("Scene", "where the measurement happens")
    tabs = st.tabs(["Zones", "At this instant", "Where traffic actually goes",
                    "Drone video"])

    with tabs[0]:
        img = FL.zone_overlay(rid, queues, float(t) if t is not None else None)
        if img is None:
            st.info("No aerial image available for this recording.")
        else:
            st.image(img, width="stretch",
                     caption="Approach zones shaded by the queue standing on "
                             "them at this instant; receiving links in grey; "
                             "the junction box outlined")
            T.note(
                "This is the geometry behind every pressure score. A movement's "
                "queue <b>x<sub>l</sub></b> is counted on the coloured approach "
                "it entered from; its downstream term <b>x<sub>m</sub></b> is "
                "counted on the grey link it will leave by. Vehicles inside the "
                "outlined box are in the junction and belong to neither — they "
                "are being served, not waiting.<br><br>"
                "<b>This is exactly the picture the Surat cameras cannot "
                "produce.</b> Each of them sees one approach; none sees the "
                "link that approach discharges into, which is why max-pressure "
                "is validated here and Webster's runs there.")

    with tabs[1]:
        if t is None:
            st.info("No measurement instants in this recording.")
        else:
            shot = FL.annotated_instant(rid, float(t))
            img, n_all, n_q = shot if shot else (None, 0, 0)
            if img is None:
                st.info("No aerial image available for this recording.")
            else:
                st.image(img, width="stretch",
                         caption=f"t = {float(t):.0f}s — {n_all} vehicles, "
                                 f"{n_q} queued (red) and {n_all - n_q} moving "
                                 f"(green)")
                T.note(
                    f"A vehicle counts as queued below "
                    f"<b>{FL.QUEUE_SPEED_MPS} m/s</b> — above walking pace, so "
                    f"a creeping queue still counts, but clear of tracking "
                    f"jitter at rest. Count the red dots on any approach and "
                    f"you have reconstructed that movement's "
                    f"<b>x<sub>l</sub></b> by hand; the pressure table on "
                    f"<b>Phase pressure</b> is the same figures in PCU.")

    with tabs[2]:
        with st.spinner("Accumulating every tracked position…"):
            hm = FL.trajectory_heatmap(rid)
        if hm is None:
            st.info("No aerial image available for this recording.")
        else:
            st.image(hm, width="stretch",
                     caption="Every non-pedestrian trajectory point, "
                             "accumulated over the whole recording")
            T.note(
                "The same empirical check the Surat zones were given: a "
                "corridor drawn from geometry can look right and still sit off "
                "the path traffic really takes. Here the bright lanes confirm "
                "the arm assignment — traffic runs along the arms and turns "
                "through the box, with the approach and receiving carriageways "
                "clearly separated, which is what makes the downstream term "
                "measurable at all.")

    with tabs[3]:
        if rec.video_path is None:
            T.callout(
                "The drone video for this recording was not downloaded. Every "
                "figure in this track comes from the derived trajectory and "
                "signal CSVs, which are a few MB; the videos are about 1 GB "
                "each and are optional.",
                title="Video not present", accent=T.P()["axis"])
        else:
            size_mb = rec.video_path.stat().st_size / 1e6
            st.video(str(rec.video_path))
            T.note(
                f"The source footage, 3840×2160 at 10 fps ({size_mb:.0f} MB). "
                f"The trajectories were extracted from this by the dataset's "
                f"authors, which is why the coordinates are already "
                f"orthorectified metres and need no calibration step.")

    T.section("Vehicle mix", "what is on these roads")
    mix = _fluid_mix(rid)
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        T.show(C.fluid_fleet_mix(mix))
    with c2:
        T.note(
            "Chinese urban traffic: cars and mopeds dominate, with tricycles "
            "in place of the auto-rickshaw. PCU factors follow the same "
            "IRC:106-1990 basis the Surat side uses — moped 0.5, car and van "
            "1.0, tricycle 1.2, bus and truck 2.2 — so a queue in PCU means "
            "the same thing in both tracks.<br><br>"
            "Worth stating plainly: this fleet is <b>not</b> Surat's, and no "
            "claim here transfers to Surat traffic. What transfers is that the "
            "controller implementation is correct on data containing the terms "
            "it needs.")


def view_mp_headtohead(df: pd.DataFrame):
    """Webster against max-pressure in a controlled point-queue network."""
    import network_sim as NS
    T.section("Controlled head-to-head",
              "measured demand, constructed network")
    T.callout(
        "<b>Measured from the Surat clips:</b> arrival flow per approach, the "
        "vehicle mix behind it, and the PCU conversion.<br>"
        "<b>Constructed for the model:</b> the two-junction topology, the link "
        "between them and its storage capacity.<br><br>"
        "A simulator lets us demonstrate the controller; it does not let us "
        "claim a measured result, and the split above is what keeps those two "
        "things apart. One junction is not enough — with no receiving link to "
        "fill, max-pressure's downstream term is constant and it collapses to "
        "serving the longest queue.",
        title="What is measured and what is not")

    c1, c2, c3 = st.columns(3)
    scenario = c1.selectbox(
        "Demand scenario", list(NS.SCENARIOS),
        format_func=lambda s: NS.SCENARIOS[s]["label"], key="mp_scenario")
    main_flow = c2.slider("Main-road demand (PCU/hr)", 400, 1400, 900, 50,
                          key="mp_main")
    cross_flow = c3.slider("Cross-street demand (PCU/hr)", 150, 700, 385, 25,
                           key="mp_cross")
    T.note(NS.SCENARIOS[scenario]["blurb"])

    if st.button("Run both controllers", key="run_mp", type="primary",
                 width="stretch"):
        with st.spinner("Simulating 30 minutes under each controller…"):
            kw = dict(main_arrival_pcu_per_hr=float(main_flow),
                      cross_arrival_pcu_per_hr=float(cross_flow))
            net_a, js = NS.build(scenario, 1800.0, **kw)
            greens, cycle, Y = NS.webster_splits(net_a, js)
            r_fixed = net_a.run(NS.FixedTimeController(greens, cycle_s=cycle),
                                seconds=1800.0)
            net_b, _ = NS.build(scenario, 1800.0, **kw)
            r_cyc = net_b.run(
                NS.CyclicMaxPressureController(cycle_s=cycle,
                                               min_green_s=cfg.MP_MIN_GREEN_S),
                seconds=1800.0)
            net_c, _ = NS.build(scenario, 1800.0, **kw)
            r_free = net_c.run(
                NS.MaxPressureController(min_green_s=cfg.MP_MIN_GREEN_S,
                                         max_green_s=cfg.MP_MAX_GREEN_S,
                                         switch_margin=cfg.MP_SWITCH_MARGIN),
                seconds=1800.0)
        st.session_state["mp_run"] = {
            "fixed": r_fixed, "cyclic": r_cyc, "free": r_free,
            "cycle": cycle, "Y": Y, "scenario": scenario,
        }

    run = st.session_state.get("mp_run")
    if not run:
        T.note("Press <b>Run both controllers</b>. Nothing is simulated until "
               "you do.")
        return

    r_fixed, r_cyc, r_free = run["fixed"], run["cyclic"], run["free"]
    base = r_fixed.mean_delay_s_per_pcu

    def gain(r):
        return ((base - r.mean_delay_s_per_pcu) / base * 100.0) if base else 0.0

    T.section("4 · Result", f"{NS.SCENARIOS[run['scenario']]['label']} · "
                            f"Y = {run['Y']:.2f} · cycle {run['cycle']:.0f}s")
    best = max((r_cyc, r_free), key=gain)
    left, right = st.columns([2, 3], gap="large")
    with left:
        T.hero("Delay against the fixed-time plan",
               f"{gain(best):+.0f}", "%",
               detail=f"Best max-pressure variant: <b>{best.controller}</b>. "
                      f"Both controllers ran the same network, the same demand "
                      f"and the same lost time per phase change.")
    with right:
        T.tiles([
            T.tile("Fixed-time", f"{r_fixed.mean_delay_s_per_pcu:.0f}", " s/PCU",
                   detail=f"{r_fixed.switches} phase changes"),
            T.tile("MP — cyclic splits", f"{r_cyc.mean_delay_s_per_pcu:.0f}",
                   " s/PCU", detail=f"{gain(r_cyc):+.0f}% · {r_cyc.switches} changes"),
            T.tile("MP — free-running", f"{r_free.mean_delay_s_per_pcu:.0f}",
                   " s/PCU", detail=f"{gain(r_free):+.0f}% · {r_free.switches} changes"),
        ], per_row=3)

    T.show(C.controller_comparison([r_fixed, r_cyc, r_free]))
    st.dataframe(pd.DataFrame([r.to_row() for r in (r_fixed, r_cyc, r_free)]),
                 width="stretch", hide_index=True)

    T.section("Queues over time")
    T.show(C.queue_history({"Fixed-time": r_fixed.history,
                            "MP — cyclic": r_cyc.history,
                            "MP — free-running": r_free.history}))

    T.callout(
        f"<b>Free-running max-pressure switched {r_free.switches} times against "
        f"the fixed plan's {r_fixed.switches}.</b> Every change costs the same "
        f"start-up and clearance time, so an unmodified controller can spend "
        f"more capacity on inter-greens than it recovers by adapting — in our "
        f"sweep its throughput fell <i>below</i> fixed-time above saturation. "
        f"That gap between Varaiya's slotted model, where switching is free, "
        f"and a real signal is why the cyclic variant keeps a cycle and lets "
        f"pressure set only the splits.",
        title="Why the two max-pressure variants differ",
        accent=T.P()["axis"])

    T.callout(
        "The pattern across the three scenarios is the one the theory "
        "predicts, and it is narrower than the literature's enthusiasm "
        "suggests. <b>Max-pressure loses on steady demand</b> — in our run by "
        "12%, switching 203 times against the fixed plan's 144 — because a "
        "plan designed for a demand that does not change is already right and "
        "every extra switch buys a better-targeted green while paying an "
        "inter-green for it. It <b>wins when the split of demand moves</b> "
        "between approaches (−25%), and <b>wins most when something "
        "downstream backs up</b> (−31% delay, +8.1% throughput, spillback "
        "more than halved) — the one regime where its distinguishing term, "
        "the downstream queue, actually carries information.<br><br>"
        "That is the operator's decision rule, and it is why this is a toggle "
        "rather than a replacement. Run the scenarios above and read the "
        "numbers off them rather than taking this summary on trust.",
        title="When to choose which", accent=T.status("good"))

    # ---- live observability check ---------------------------------------


def view_mp_method(df: pd.DataFrame):
    """Why this controller is validated here and not on Surat."""
    import maxpressure as MP
    junctions = [c for c in cfg.junction_cameras()
                 if c in set(df.camera_id.unique())]
    T.section("Why Surat's junctions run Webster's instead",
              "a property of the camera layout, not of the algorithm")
    movements = _mp_movements_from_metrics(df, junctions)
    phases = MP.one_movement_per_phase(movements, min_green_s=cfg.MP_MIN_GREEN_S,
                                       max_green_s=cfg.MP_MAX_GREEN_S)
    decision = MP.decide(movements, phases)

    n_obs = sum(1 for m in movements if m.downstream_observed)
    mode_color = {MP.MODE_FULL: T.status("good"),
                  MP.MODE_PARTIAL: T.status("warning"),
                  MP.MODE_GREEDY: T.status("critical")}[decision.mode]

    left, right = st.columns([2, 3], gap="large")
    with left:
        T.hero("Movements with a downstream queue",
               f"{n_obs} / {len(movements)}",
               detail="Max-pressure needs the queue on the receiving link of "
                      "every movement. Without it the algorithm is not "
                      "max-pressure at all.")
    with right:
        T.tiles([
            T.tile("Mode", decision.mode.replace("-", " "),
                   detail=("Every downstream queue observed — this is genuine "
                           "max-pressure."
                           if decision.mode == MP.MODE_FULL else
                           "Downstream queues are missing, so the argmax below "
                           "would rank on incoming queue alone.")),
            T.tile("Junction cameras", f"{len(junctions)}",
                   detail=", ".join(C.short_name(c) for c in junctions) or "—"),
            T.tile("Downstream cameras", "0",
                   detail="None configured. A downstream camera is a "
                          "configuration entry, not a code change."),
        ], per_row=3)

    st.markdown(T.chip(f"MODE: {decision.mode.upper()}", mode_color),
                unsafe_allow_html=True)

    for w in decision.warnings:
        T.callout(w, title="Why this cannot run here",
                  accent=T.status("critical"))

    st.dataframe(pd.DataFrame([{
        "Movement": m.label,
        "Incoming queue (PCU)": round(m.queue_in_pcu, 2),
        "Incoming source": m.queue_in_source,
        "Downstream queue": ("—" if m.queue_out_pcu is None
                             else round(m.queue_out_pcu, 2)),
        "Downstream source": m.queue_out_source,
        "Pressure": "not computable" if m.pressure is None else round(m.pressure, 2),
    } for m in movements]), width="stretch", hide_index=True)

    T.callout(
        "This is the same refusal the metric scale gets. Substituting zero for "
        "an unobserved downstream queue turns max-pressure into <b>greedy queue "
        "control</b> — serve the longest queue — which is a different algorithm, "
        "is <b>not</b> throughput-optimal, and cannot see a blocked link, which "
        "is the exact failure max-pressure exists to prevent. Shipping that "
        "under the name 'max-pressure' would be the most flattering and least "
        "honest thing this project could do.<br><br>"
        "<b>What would fix it:</b> one camera on the receiving link of each "
        "controlled movement, registered to the same junction. That is a "
        "deployment decision for the city, and the code above is already "
        "waiting for it.",
        title="Why we do not fake the missing term",
        accent=T.status("warning"))



# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

SUBTITLES = {
    # Webster / Surat track
    "Network": "Which site is worst, and how the network is behaving",
    "Site detail": "One camera in depth — zones, scene, trends, diagnostics",
    "Signal timing": "What the signal should do differently, and why",
    "Bottlenecks": "Sustained problems, with the evidence that raised them",
    "Outlook": "What is trending worse over the next minute",
    "Analyse footage": "Run the pipeline on a clip or still it has never seen",
    "Calibration": "Pin pixels to metres, once, and promote every estimate",
    "How it works": "The reasoning behind every number on the other views",
    # Max-pressure / FLUID track
    "Validation": "Max-pressure replayed against three real signals",
    "Scene": "The intersection itself — aerial, zones, movement, video",
    "Phase pressure": "The phase ranking at one instant, from measured queues",
    "Head-to-head": "Webster against max-pressure in a controlled network",
    "Method": "What the controller needs, and why Surat cannot supply it",
}


def main():
    T.inject()
    df = load_metrics()
    have_metrics = not df.empty
    analysed = ([c for c in cfg.CAMERAS if c in set(df.camera_id.unique())]
                if have_metrics else [])
    calibrated = [c for c in cfg.CAMERAS if cfg.scale_is_verified(c)]

    try:
        import fluid as FL
        drone_on = FL.available()
        n_drone = len(FL.recordings()) if drone_on else 0
    except Exception:
        drone_on, n_drone = False, 0

    with st.sidebar:
        st.markdown(
            '<div class="side-brand">🚦 Surat Traffic Intelligence</div>'
            '<div class="side-sub">PS-08 · Data-Driven Traffic Optimization '
            '&amp; Adaptive Infrastructure Intelligence<br>'
            'E-Rakshak Hackathon 2026</div>', unsafe_allow_html=True)
        st.divider()

        # Strategy first: it selects the dataset, and therefore the whole
        # navigation below it. Putting the view list above would let an
        # operator pick a Surat view and then switch to a controller that has
        # nothing to say about Surat.
        st.markdown('<div class="side-h">Control strategy</div>',
                    unsafe_allow_html=True)
        cfg.OPTIMIZATION_MODE = cfg.load_optimization_mode()
        strategy = st.radio(
            "Strategy",
            ["Webster — Surat network", "Max-pressure — FLUID validation"],
            index=0 if cfg.OPTIMIZATION_MODE == "webster" else 1,
            label_visibility="collapsed", key="opt_mode",
            help="Two controllers, two datasets. Webster's plans a fixed-time "
                 "cycle from arrival flow per approach — all the Surat cameras "
                 "can see, and what every Surat figure is built from. "
                 "Max-pressure needs the queue on the downstream link of each "
                 "movement, which no Surat camera observes, so it is validated "
                 "on the FLUID drone recordings instead. Switching changes "
                 "which dataset this dashboard is showing; it never changes a "
                 "Surat number.")
        chosen_mode = ("webster" if strategy.startswith("Webster")
                       else "max_pressure")
        if chosen_mode != cfg.OPTIMIZATION_MODE:
            cfg.save_optimization_mode(chosen_mode)
            st.rerun()
        cfg.OPTIMIZATION_MODE = chosen_mode
        webster_mode = chosen_mode == "webster"

        st.markdown(
            '<div class="note" style="margin-top:.4rem">'
            + (T.chip("Dataset: Surat CCTV · 7 cameras", T.series(0))
               if webster_mode else
               T.chip(f"Dataset: FLUID drone · {n_drone} recordings",
                      T.status("good") if drone_on else T.status("warning")))
            + '</div>', unsafe_allow_html=True)

        st.divider()
        st.markdown('<div class="side-h">View</div>', unsafe_allow_html=True)
        views = views_for(chosen_mode)
        view = st.radio("View", views, label_visibility="collapsed",
                        key=f"view_{chosen_mode}")

        camera = None
        if view == "Site detail" and analysed:
            camera = st.selectbox("Site", analysed, format_func=cfg.camera_name)

        # The recommendation belongs with the strategy, and only where it has
        # data to speak about — it is a statement about the Surat network.
        if webster_mode and have_metrics:
            try:
                js = [c for c in cfg.junction_cameras()
                      if c in set(df.camera_id.unique())]
                dem = [d for d in (so.demand_from_metrics(df, c) for c in js)
                       if d is not None]
                adv = so.recommend_controller(
                    df, js, downstream_observed=False, demands=dem)
                st.divider()
                st.markdown(
                    '<div class="side-h">Recommendation</div>'
                    '<div class="note" style="margin-top:0">'
                    + T.chip("Webster for this network", T.status("good"))
                    + f'<br>{adv.reasons[0]}</div>',
                    unsafe_allow_html=True)
            except Exception:
                pass

        st.divider()
        st.markdown('<div class="side-h">System</div>', unsafe_allow_html=True)
        if webster_mode:
            mins = (df.window_end_s.groupby(df.camera_id).max().sum() / 60
                    if have_metrics else 0)
            st.markdown(
                f'<div class="note" style="margin-top:0">'
                f'<b>{len(analysed)}</b> cameras analysed · '
                f'<b>{len(df):,}</b> windows · <b>{mins:.0f}</b> min of '
                f'footage<br><b>{len(calibrated)}</b> of {len(cfg.CAMERAS)} '
                f'cameras have a measured metric scale.</div>',
                unsafe_allow_html=True)
            st.markdown(
                '<div class="note">Detection: YOLO11-S fine-tuned on UVH-26 '
                '(Indian traffic CCTV) + ByteTrack.<br>'
                'Density: PCU per IRC:106-1990.<br>'
                'Signal timing: Webster\'s method.<br>'
                'Occupancy: absolute edge-density index.</div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div class="note" style="margin-top:0">'
                f'<b>{n_drone}</b> drone recordings · <b>3</b> signalised '
                f'intersections · <b>305</b> min · <b>20,860</b> vehicle '
                f'trajectories.</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="note">Queues: measured from metre-scale '
                'trajectories.<br>'
                'Turn ratios: counted per vehicle.<br>'
                'Signal state: per-second ground truth.<br>'
                'Controller: max-pressure (Varaiya 2013).</div>',
                unsafe_allow_html=True)

    # ---- header ----------------------------------------------------------
    if webster_mode:
        chips = "".join([
            T.chip(f"{len(analysed)} cameras", T.series(0)) if analysed else "",
            T.chip(f"{len(calibrated)}/{len(cfg.CAMERAS)} scale measured",
                   T.status("good") if calibrated else T.status("warning")),
        ])
        T.topbar(view, SUBTITLES.get(view, ""),
                 eyebrow="Surat CCTV · Webster's method", right=chips)
    else:
        chips = (T.chip(f"{n_drone} recordings · 3 intersections",
                        T.status("good")) if drone_on else
                 T.chip("FLUID not found", T.status("warning")))
        T.topbar(view, SUBTITLES.get(view, ""),
                 eyebrow="FLUID drone data · max-pressure validation",
                 right=chips)

    # ---- max-pressure track ---------------------------------------------
    if not webster_mode:
        if view == "Validation":
            view_mp_validation()
        elif view == "Scene":
            view_mp_scene()
        elif view == "Phase pressure":
            view_mp_pressure()
        elif view == "Head-to-head":
            view_mp_headtohead(df)
        elif view == "Method":
            view_mp_method(df)
        return

    # ---- Webster / Surat track -------------------------------------------
    # Views that stand alone come first — they must work before analyze.py has
    # ever run, which is exactly the state a first-time user is in.
    if view == "Calibration":
        view_calibration()
        return
    if view == "Analyse footage":
        view_upload()
        return

    if not have_metrics:
        T.callout(
            "No metrics have been generated for the seven Surat cameras yet, "
            "so this view has nothing to show. Either run the pipeline:"
            "<br><br><code>python src/seed_calibration.py</code><br>"
            "<code>python src/analyze.py --all</code><br><br>"
            "or go straight to <b>Analyse footage</b> and upload a clip — that "
            "view works without any of this.",
            title="Nothing measured yet", accent=T.status("warning"))
        return

    if view == "Network":
        view_network(df)
    elif view == "Site detail" and camera:
        view_site(df, camera)
    elif view == "Signal timing":
        view_signal(df)
    elif view == "Bottlenecks":
        view_bottlenecks(df)
    elif view == "Outlook":
        view_outlook(df)
    elif view == "How it works":
        view_method(df)


if __name__ == "__main__":
    main()
