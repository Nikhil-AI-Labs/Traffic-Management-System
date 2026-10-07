"""Visual system for the dashboard: palette, Plotly chrome, HTML components.

Every colour here does one job, and the job decides the palette:

  * **categorical** — telling series apart (lanes, vehicle classes). Fixed hue
    order, never cycled, never assigned by rank.
  * **sequential** — magnitude (occupancy, congestion index). One hue,
    light to dark.
  * **status** — level of service and alert severity. Reserved; never reused as
    a series colour, and never the only channel carrying the meaning, so every
    status is shipped with its name in text beside a coloured dot.

Both the light and the dark set were checked for colour-vision separation
against the surface they actually render on, rather than picked by eye:
worst adjacent pair 9.1 (light) / 8.4 (dark) in OKLab dE x100, against a
target of 8. Three of the light hues sit under 3:1 contrast on the light
surface, which is why every chart here also ships direct labels and a table
view - the colour is never the only way to read a value.

Dark is a selected palette, not an inversion: the same six hues re-stepped for
a dark surface, and the sequential ramp reversed so "near zero" recedes toward
the background in both modes.
"""

from __future__ import annotations

import contextlib
import contextvars

# Streamlit is imported inside the functions that render with it, not here.
# The palette and the Plotly chrome are also used by the JSON API behind the
# React dashboard, which must be able to build every figure without a
# Streamlit runtime — and with an explicit light or dark mode per request,
# since there is no `st.context` to ask.


class _LazyStreamlit:
    """`st.<anything>` that imports Streamlit on first use, not at import."""

    def __getattr__(self, name):
        import streamlit
        return getattr(streamlit, name)


st = _LazyStreamlit()


# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------

# Categorical slots, in fixed order. A series keeps its slot no matter how many
# other series are on screen - filtering one out must never repaint the rest.
_SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
_SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"]

# Single-hue magnitude ramp. Reversed under dark so the low end recedes toward
# the surface in both modes rather than glowing against it.
_SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
        "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281",
        "#0d366b"]

# Reserved status palette. Identical in both modes - these mean something
# fixed, so they must not drift with the theme.
STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

# Level of service is a four-state status variable, so it takes status colours
# rather than categorical ones. Always rendered with its name in text.
LOS_STATUS = {"Free": "good", "Moderate": "warning",
              "Heavy": "serious", "Jam": "critical"}
LOS_ORDER = ["Free", "Moderate", "Heavy", "Jam"]

SEVERITY_STATUS = {"critical": "critical", "warning": "warning", "info": None}

# `muted` is a decoration colour only — 3.4:1 on the light surface, which is
# fine for a chart axis tick sitting beside a table view but not for anything a
# reader has to actually read. All UI copy uses `text2` (7.9:1 light,
# 11.4:1 dark) or `text`. Getting this wrong is the usual reason a themed
# Streamlit app ends up with text you have to squint at.
LIGHT = {
    "mode": "light",
    "surface": "#fcfcfb",      # chart/card surface, sits above the page
    "page": "#f9f9f7",
    "raised": "#ffffff",
    "sunken": "#f0efec",
    "text": "#0b0b0b",
    "text2": "#52514e",
    "muted": "#898781",        # decoration only — see note above
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "border": "rgba(11,11,11,0.10)",
    "hairline": "#e6e5df",
    "shadow": "0 1px 2px rgba(11,11,11,.04), 0 6px 16px -10px rgba(11,11,11,.14)",
    "series": _SERIES_LIGHT,
    "seq": _SEQ,
    "deemphasis": "#c9c8c1",
    "tint": 0.13,              # status-chip wash strength
}

DARK = {
    "mode": "dark",
    "surface": "#1a1a19",
    "page": "#0d0d0d",
    "raised": "#212120",
    "sunken": "#151514",
    "text": "#ffffff",
    "text2": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "border": "rgba(255,255,255,0.11)",
    "hairline": "#2c2c2a",
    "shadow": "0 1px 2px rgba(0,0,0,.5), 0 8px 22px -12px rgba(0,0,0,.7)",
    "series": _SERIES_DARK,
    "seq": list(reversed(_SEQ)),
    "deemphasis": "#4a4a47",
    "tint": 0.20,
}

FONT = ('system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", '
        "Arial, sans-serif")
MONO = 'ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, monospace'


_MODE_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "theme_mode", default=None)


@contextlib.contextmanager
def use_mode(m: str | None):
    """Build every figure inside the block in `m` ("light" or "dark").

    A context variable rather than a module global, because the API serves
    concurrent requests from a thread pool and two viewers can be looking at
    different themes at the same moment.
    """
    token = _MODE_OVERRIDE.set(m if m in ("light", "dark") else None)
    try:
        yield
    finally:
        _MODE_OVERRIDE.reset(token)


def mode() -> str:
    """Whichever theme the viewer is actually looking at."""
    forced = _MODE_OVERRIDE.get()
    if forced:
        return forced
    try:
        # Only ask Streamlit inside a running Streamlit app. Outside one -
        # the API, the tests, a script - `st.context` logs a "missing
        # ScriptRunContext" warning on every single call, which buried the
        # verification suite's output under hundreds of lines of noise.
        from streamlit.runtime import exists
        if not exists():
            return "light"
        import streamlit as st
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def P() -> dict:
    """The active palette."""
    return DARK if mode() == "dark" else LIGHT


def series(i: int) -> str:
    """Categorical slot i, wrapping only after the six-slot ceiling.

    Past six series the answer is to fold the tail into 'Other' or facet, not
    to invent a seventh hue - a generated hue is indistinguishable from an
    existing one under colour-vision deficiency.
    """
    s = P()["series"]
    return s[i % len(s)]


def status(name: str) -> str:
    return STATUS.get(name, P()["muted"])


def los_color(los: str) -> str:
    key = LOS_STATUS.get(los)
    return STATUS[key] if key else P()["muted"]


def severity_color(sev: str) -> str:
    key = SEVERITY_STATUS.get(sev)
    return STATUS[key] if key else P()["series"][0]


def los_of(ci: float) -> str:
    """Level of service band for a 0-1 congestion index."""
    return ("Jam" if ci >= 0.75 else "Heavy" if ci >= 0.5
            else "Moderate" if ci >= 0.25 else "Free")


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def tint(hex_color: str, alpha: float | None = None) -> str:
    return _rgba(hex_color, P()["tint"] if alpha is None else alpha)


def on_fill(hex_color: str) -> str:
    """Ink or white for a label set *inside* a coloured fill.

    A label on a mark is the one place text may sit on a series colour, and it
    has to clear contrast against whatever step that colour happens to be -
    the same ordinal ramp runs dark-on-light in one mode and light-on-dark in
    the other, so a fixed white would fail half the time.
    """
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
           for c in (r, g, b)]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#0b0b0b" if lum > 0.36 else "#ffffff"


# --------------------------------------------------------------------------
# Plotly chrome
# --------------------------------------------------------------------------

def layout(height: int = 300, legend: bool = True, **kw) -> dict:
    """Base layout: recessive hairline grid, transparent surface, no clutter."""
    p = P()
    base = dict(
        height=height,
        margin=dict(l=8, r=16, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=12, color=p["text2"]),
        hoverlabel=dict(
            bgcolor=p["raised"], bordercolor=p["border"],
            font=dict(family=FONT, size=12, color=p["text"]),
        ),
        xaxis=dict(
            showgrid=True, gridcolor=p["grid"], gridwidth=1, griddash="solid",
            zeroline=False, linecolor=p["axis"], linewidth=1,
            ticks="outside", tickcolor=p["axis"], ticklen=4,
            tickfont=dict(color=p["text2"], size=11),
            title=dict(font=dict(color=p["text2"], size=11.5)),
        ),
        yaxis=dict(
            showgrid=True, gridcolor=p["grid"], gridwidth=1, griddash="solid",
            zeroline=False, linecolor=p["axis"], linewidth=1,
            ticks="outside", tickcolor=p["axis"], ticklen=4,
            tickfont=dict(color=p["text2"], size=11),
            title=dict(font=dict(color=p["text2"], size=11.5)),
        ),
        showlegend=legend,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            font=dict(size=11.5, color=p["text2"]),
            bgcolor="rgba(0,0,0,0)", itemsizing="constant",
            # Plotly reverses legends on stacked bars. Our stacks run left to
            # right, so the visual order is the trace order and reversing it
            # puts the legend backwards against the chart.
            traceorder="normal",
        ),
        modebar=dict(remove=["lasso", "select", "autoscale"]),
    )
    for k, v in kw.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


PLOT_CONFIG = {"displayModeBar": False, "displaylogo": False,
               "scrollZoom": False}


def show(fig, height: int | None = None, key: str | None = None):
    """Render a figure with the standard config."""
    if height is not None:
        fig.update_layout(height=height)
    st.plotly_chart(fig, width="stretch", config=PLOT_CONFIG, key=key)


# --------------------------------------------------------------------------
# CSS
# --------------------------------------------------------------------------

def css() -> str:
    p = P()
    accent_wash = tint(p["series"][0], 0.16)
    return f"""
<style>
  html, body, [class*="st-"] {{ font-family: {FONT}; }}

  /* Streamlit's default top padding costs a third of the first screen. */
  .block-container {{ padding-top: 1.4rem; padding-bottom: 5rem;
                      max-width: 1560px; }}
  h1, h2, h3, h4, h5 {{ font-family: {FONT}; letter-spacing: -0.017em; }}
  hr {{ border-color:{p['hairline']}; }}
  a {{ text-decoration: none; }}

  /* ---------------- app header ---------------- */
  .topbar {{ display:flex; align-items:flex-end; justify-content:space-between;
             gap:18px; flex-wrap:wrap; padding-bottom:12px;
             border-bottom:1px solid {p['hairline']}; margin-bottom:.2rem; }}
  .topbar-l {{ min-width:0; }}
  .topbar-eyebrow {{ font-size:.70rem; font-weight:700; letter-spacing:.13em;
                     text-transform:uppercase; color:{p['text2']};
                     margin-bottom:3px; }}
  .topbar-t {{ font-size:1.95rem; font-weight:690; letter-spacing:-.026em;
               line-height:1.1; color:{p['text']}; }}
  .topbar-s {{ font-size:.88rem; color:{p['text2']}; margin-top:4px; }}
  .topbar-r {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}

  /* ---------------- section rule ---------------- */
  .sec {{ display:flex; align-items:baseline; gap:.65rem;
          margin: 2.0rem 0 .5rem 0; }}
  .sec-t {{ font-size:.80rem; font-weight:750; letter-spacing:.10em;
            text-transform:uppercase; color:{p['text']}; white-space:nowrap; }}
  .sec-r {{ flex:1; height:1px; background:{p['hairline']}; }}
  .sec-n {{ font-size:.80rem; color:{p['text2']}; white-space:nowrap; }}

  /* ---------------- stat tiles ---------------- */
  .tiles {{ display:grid; gap:11px; margin:.25rem 0 .35rem 0;
            align-items:stretch; }}
  /* The column count is set inline per row of tiles; on a narrow screen a
     fixed four-across squeezes each tile below the width of its own value. */
  @media (max-width: 900px) {{
    .tiles {{ grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }}
  }}
  @media (max-width: 520px) {{
    .tiles {{ grid-template-columns: minmax(0, 1fr) !important; }}
  }}
  .tile {{ background:{p['surface']}; border:1px solid {p['border']};
           border-radius:11px; padding:15px 17px 14px 17px;
           box-shadow:{p['shadow']}; }}
  .tile-l {{ font-size:.74rem; font-weight:680; letter-spacing:.055em;
             text-transform:uppercase; color:{p['text2']}; margin-bottom:7px; }}
  .tile-v {{ font-size:1.9rem; font-weight:660; line-height:1.05;
             color:{p['text']}; letter-spacing:-.02em; }}
  .tile-u {{ font-size:.95rem; font-weight:500; color:{p['text2']};
             margin-left:3px; letter-spacing:0; }}
  .tile-d {{ font-size:.815rem; color:{p['text2']}; margin-top:7px;
             line-height:1.5; }}

  /* ---------------- hero figure ---------------- */
  .hero-v {{ font-size:3.5rem; font-weight:670; line-height:1;
             color:{p['text']}; letter-spacing:-.035em; }}
  .hero-u {{ font-size:1.3rem; font-weight:500; color:{p['text2']};
             margin-left:6px; letter-spacing:0; }}
  .hero-l {{ font-size:.75rem; font-weight:720; letter-spacing:.10em;
             text-transform:uppercase; color:{p['text2']}; margin-bottom:9px; }}
  .hero-d {{ font-size:.885rem; color:{p['text2']}; margin-top:11px;
             max-width:48ch; line-height:1.55; }}

  /* ---------------- status chip ----------------
     Tinted wash + solid dot + the state's NAME. Hue never carries the meaning
     on its own, and the ink is a text token so it clears contrast whatever
     the status colour happens to be. */
  .chip {{ display:inline-flex; align-items:center; gap:6px;
           padding:3px 10px 3px 8px; border-radius:999px;
           font-size:.745rem; font-weight:670; color:{p['text']};
           white-space:nowrap; vertical-align:middle; line-height:1.35; }}
  .dot {{ width:8px; height:8px; border-radius:50%; flex:none; }}

  .kv {{ font-size:.85rem; color:{p['text2']}; }}
  .kv b {{ color:{p['text']}; font-weight:640; }}
  .mono {{ font-family:{MONO}; font-variant-numeric: tabular-nums; }}

  /* ---------------- callout ---------------- */
  .callout {{ background:{p['surface']}; border:1px solid {p['border']};
              border-left:3px solid var(--accent, {p['axis']});
              border-radius:9px; padding:13px 16px; margin:.5rem 0 .2rem 0;
              font-size:.855rem; color:{p['text2']}; line-height:1.58; }}
  .callout b, .callout strong {{ color:{p['text']}; font-weight:640; }}
  .callout-t {{ font-size:.80rem; font-weight:720; letter-spacing:.045em;
                text-transform:uppercase; color:{p['text']};
                margin-bottom:5px; }}
  .callout code, .note code {{ font-family:{MONO}; font-size:.86em;
                   background:{p['sunken']}; padding:1px 5px;
                   border-radius:4px; }}

  /* ---------------- alert card ---------------- */
  .alert {{ background:{p['surface']}; border:1px solid {p['border']};
            border-left:3px solid var(--sev); border-radius:9px;
            padding:13px 16px; margin-bottom:10px;
            box-shadow:{p['shadow']}; }}
  .alert-h {{ font-size:.98rem; font-weight:650; color:{p['text']};
              margin:6px 0 3px 0; letter-spacing:-.008em; }}
  .alert-m {{ font-size:.775rem; color:{p['text2']}; }}
  .alert-b {{ font-size:.845rem; color:{p['text2']}; line-height:1.55;
              margin-top:6px; }}
  .alert-b b {{ color:{p['text']}; font-weight:640; }}

  /* ---------------- site card ---------------- */
  .site {{ background:{p['surface']}; border:1px solid {p['border']};
           border-radius:10px; padding:11px 13px 12px 13px;
           box-shadow:{p['shadow']}; }}
  .site-n {{ font-size:.855rem; font-weight:650; color:{p['text']};
             line-height:1.28; min-height:2.55em; }}
  .site-m {{ font-size:.755rem; color:{p['text2']}; margin-top:2px; }}

  [data-testid="stImage"] img {{ border-radius:9px; display:block; }}
  [data-testid="stImage"] figcaption {{ font-size:.80rem;
                                        color:{p['text2']}; }}

  /* ---------------- meter ---------------- */
  .meter {{ height:6px; border-radius:3px; overflow:hidden; margin-top:9px; }}
  .meter > i {{ display:block; height:100%; border-radius:3px; }}

  /* ---------------- numbered steps ---------------- */
  .steps {{ counter-reset: s; margin:.3rem 0 .2rem 0; }}
  .step {{ position:relative; padding:0 0 14px 34px; counter-increment:s;
           font-size:.865rem; color:{p['text2']}; line-height:1.55; }}
  .step:before {{ content:counter(s); position:absolute; left:0; top:-1px;
                  width:22px; height:22px; border-radius:50%;
                  background:{accent_wash}; color:{p['text']};
                  font-size:.73rem; font-weight:720; display:flex;
                  align-items:center; justify-content:center; }}
  .step b {{ color:{p['text']}; font-weight:640; }}

  .note {{ font-size:.855rem; color:{p['text2']}; line-height:1.6;
           margin-top:.45rem; }}
  .note b {{ color:{p['text']}; font-weight:640; }}

  /* ---------------- Streamlit widget legibility ----------------
     The stock label, caption and slider-tick colours drop below 4.5:1 on a
     themed surface. These pull each back to a real text token. */
  [data-testid="stWidgetLabel"] label p,
  [data-testid="stWidgetLabel"] label span,
  .stRadio label p, .stCheckbox label p, .stToggle label p {{
      color:{p['text']} !important; font-size:.865rem !important;
      font-weight:560 !important; }}
  [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{
      color:{p['text2']} !important; font-size:.82rem !important; }}
  [data-testid="stExpander"] summary p {{ color:{p['text']} !important;
      font-weight:600 !important; font-size:.87rem !important; }}
  [data-testid="stMetricLabel"] p {{ color:{p['text2']} !important; }}
  [data-testid="stFileUploaderDropzoneInstructions"] span,
  [data-testid="stFileUploaderDropzoneInstructions"] small {{
      color:{p['text2']} !important; }}
  [data-testid="stSliderTickBarMin"], [data-testid="stSliderTickBarMax"],
  [data-testid="stThumbValue"] {{ color:{p['text2']} !important; }}

  [data-testid="stDataFrame"] {{ font-variant-numeric: tabular-nums; }}
  [data-testid="stTabs"] button p {{ font-size:.885rem !important;
                                     font-weight:600 !important; }}

  /* Primary actions, targeted by their widget key. */
  .st-key-run_analysis button, .st-key-apply_scale button {{
      font-weight:640; letter-spacing:.005em; }}

  /* Sidebar */
  [data-testid="stSidebar"] .stRadio label p {{ font-size:.90rem !important;
                                                font-weight:560 !important; }}
  .side-brand {{ font-size:1.02rem; font-weight:680; line-height:1.25;
                 color:{p['text']}; letter-spacing:-.015em; }}
  .side-sub {{ font-size:.775rem; color:{p['text2']}; margin-top:5px; }}
  .side-h {{ font-size:.68rem; font-weight:730; letter-spacing:.12em;
             text-transform:uppercase; color:{p['text2']};
             margin:.2rem 0 .35rem 0; }}
</style>
"""


def inject():
    st.markdown(css(), unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------

def topbar(title: str, subtitle: str = "", eyebrow: str = "", right: str = ""):
    """App header: what you are looking at, and the system's live status."""
    e = f'<div class="topbar-eyebrow">{eyebrow}</div>' if eyebrow else ""
    s = f'<div class="topbar-s">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div class="topbar"><div class="topbar-l">{e}'
        f'<div class="topbar-t">{title}</div>{s}</div>'
        f'<div class="topbar-r">{right}</div></div>',
        unsafe_allow_html=True,
    )


def section(title: str, note: str = ""):
    n = f'<span class="sec-n">{note}</span>' if note else ""
    st.markdown(
        f'<div class="sec"><span class="sec-t">{title}</span>'
        f'<span class="sec-r"></span>{n}</div>',
        unsafe_allow_html=True,
    )


def chip(label: str, color: str) -> str:
    """Status chip HTML — dot + name, on a wash of the status colour."""
    return (f'<span class="chip" style="background:{tint(color)}">'
            f'<span class="dot" style="background:{color}"></span>{label}</span>')


def los_chip(los: str) -> str:
    return chip(los, los_color(los))


def callout(body: str, title: str = "", accent: str | None = None):
    a = accent or P()["series"][0]
    t = f'<div class="callout-t">{title}</div>' if title else ""
    st.markdown(f'<div class="callout" style="--accent:{a}">{t}{body}</div>',
                unsafe_allow_html=True)


def steps(items: list[str]):
    st.markdown('<div class="steps">'
                + "".join(f'<div class="step">{s}</div>' for s in items)
                + "</div>", unsafe_allow_html=True)


def note(body: str):
    st.markdown(f'<div class="note">{body}</div>', unsafe_allow_html=True)


def hero(label: str, value: str, unit: str = "", detail: str = ""):
    u = f'<span class="hero-u">{unit}</span>' if unit else ""
    d = f'<div class="hero-d">{detail}</div>' if detail else ""
    st.markdown(
        f'<div class="hero-l">{label}</div>'
        f'<div class="hero-v">{value}{u}</div>{d}',
        unsafe_allow_html=True,
    )


def tile(label: str, value: str, unit: str = "", detail: str = "",
         meter: tuple[float, str] | None = None) -> str:
    """Stat-tile HTML. `meter` is (0-1 fraction, fill colour)."""
    u = f'<span class="tile-u">{unit}</span>' if unit else ""
    d = f'<div class="tile-d">{detail}</div>' if detail else ""
    # A tile with no figure is a text card; don't reserve the value row's
    # line-height for nothing.
    v = f'<div class="tile-v">{value}{u}</div>' if value != "" else ""
    m = ""
    if meter:
        frac, col = meter
        frac = max(0.0, min(1.0, float(frac)))
        m = (f'<div class="meter" style="background:{tint(col, 0.16)}">'
             f'<i style="width:{frac * 100:.1f}%;background:{col}"></i></div>')
    return (f'<div class="tile"><div class="tile-l">{label}</div>'
            f'{v}{m}{d}</div>')


def tiles(items: list[str], per_row: int | None = None):
    """Lay stat tiles out in one responsive grid row."""
    n = max(1, per_row or len(items))
    st.markdown(
        f'<div class="tiles" style="grid-template-columns:repeat({n},1fr)">'
        + "".join(items) + "</div>",
        unsafe_allow_html=True,
    )
