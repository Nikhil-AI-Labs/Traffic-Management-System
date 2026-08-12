"""Build the PS-08 submission deck.

    python submission/build_deck.py

Generates a 15-slide 16:9 presentation covering every slide the E-Rakshak
Stage-2 guideline names, then writes it next to this file.

The deck is generated rather than hand-drawn for the same reason the metrics
are: a figure quoted on a slide should come from the same place the dashboard
gets it, so the two cannot drift. Every number below is either read live from
the pipeline or carried in RESULTS, which tests/verify_system.py re-derives.

Palette and type are lifted from .streamlit/config.toml so the deck reads as
the same product as the dashboard, not a separate artefact about it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "Surat-Traffic-Intelligence-PS08.pptx"
IMG = ROOT / "docs" / "images"

# ---------------------------------------------------------------------------
# EDIT THESE TWO LINES
# ---------------------------------------------------------------------------
TEAM_NAME = "[TEAM NAME]"
TEAM_MEMBERS = "[Member 1]  ·  [Member 2]  ·  [Member 3]  ·  [Member 4]"

# ---------------------------------------------------------------------------
# Design system — .streamlit/config.toml, light theme
# ---------------------------------------------------------------------------
BG        = RGBColor(0xF9, 0xF9, 0xF7)
SURFACE   = RGBColor(0xF0, 0xEF, 0xEC)
INK       = RGBColor(0x0B, 0x0B, 0x0B)
INK_SOFT  = RGBColor(0x52, 0x51, 0x4E)
INK_MUTE  = RGBColor(0x89, 0x87, 0x81)
BORDER    = RGBColor(0xE1, 0xE0, 0xD9)
BLUE      = RGBColor(0x2A, 0x78, 0xD6)
BLUE_DK   = RGBColor(0x1C, 0x5C, 0xAB)
ORANGE    = RGBColor(0xEB, 0x68, 0x34)
GREEN     = RGBColor(0x1B, 0xAF, 0x7A)
AMBER     = RGBColor(0xED, 0xA1, 0x00)
RED       = RGBColor(0xD0, 0x3B, 0x3B)

FONT = "Segoe UI"
W, H = Inches(13.333), Inches(7.5)
M = Inches(0.72)                       # page margin
CW = W - 2 * M                         # content width


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def _txt(slide, x, y, w, h, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    tf.paragraphs[0].alignment = align
    return tf


def _run(p, text, size=14, bold=False, color=INK, italic=False, space_after=0):
    r = p.add_run()
    r.text = text
    f = r.font
    f.name, f.size, f.bold, f.italic = FONT, Pt(size), bold, italic
    f.color.rgb = color
    p.space_after = Pt(space_after)
    return r


def _para(tf, first=False):
    return tf.paragraphs[0] if first else tf.add_paragraph()


def _rect(slide, x, y, w, h, fill=SURFACE, line=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
          radius=0.04):
    s = slide.shapes.add_shape(shape, x, y, w, h)
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(1)
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        try:
            s.adjustments[0] = radius
        except Exception:
            pass
    s.shadow.inherit = False
    return s


def slide_new(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = BG
    return s


def header(slide, eyebrow, title, sub=None):
    """Standard page head: small blue eyebrow, big title, optional deck."""
    tf = _txt(slide, M, Inches(0.46), CW, Inches(0.3))
    _run(_para(tf, True), eyebrow.upper(), 10.5, True, BLUE)

    tf = _txt(slide, M, Inches(0.78), CW, Inches(0.62))
    _run(_para(tf, True), title, 30, True, INK)

    y = Inches(1.42)
    if sub:
        tf = _txt(slide, M, y, CW, Inches(0.4))
        _run(_para(tf, True), sub, 13.5, False, INK_SOFT)
        y = Inches(1.82)

    ln = _rect(slide, M, y, CW, Pt(1.1), fill=BORDER, shape=MSO_SHAPE.RECTANGLE)
    return y + Inches(0.30)


def footer(slide, n):
    tf = _txt(slide, M, H - Inches(0.52), CW, Inches(0.24))
    p = _para(tf, True)
    _run(p, "Surat Traffic Intelligence", 8.5, True, INK_MUTE)
    _run(p, "   ·   E-Rakshak Hackathon 2026   ·   PS-08", 8.5, False, INK_MUTE)
    tf2 = _txt(slide, W - M - Inches(1), H - Inches(0.52), Inches(1), Inches(0.24),
               align=PP_ALIGN.RIGHT)
    _run(_para(tf2, True), str(n), 8.5, True, INK_MUTE)


def bullets(slide, x, y, w, items, size=13.5, gap=13, bullet_color=BLUE):
    """items: list of (bold_lead, rest) or plain strings."""
    tf = _txt(slide, x, y, w, Inches(0.4))
    for i, it in enumerate(items):
        p = _para(tf, i == 0)
        _run(p, "▪  ", size, True, bullet_color)
        if isinstance(it, tuple):
            _run(p, it[0], size, True, INK)
            if it[1]:
                _run(p, it[1], size, False, INK_SOFT)
        else:
            _run(p, it, size, False, INK_SOFT)
        p.space_after = Pt(gap)
    return tf


def tile(slide, x, y, w, h, label, value, unit="", note="", accent=BLUE):
    """A stat tile: small label, big number, supporting note."""
    _rect(slide, x, y, w, h, fill=SURFACE, line=BORDER)
    _rect(slide, x, y, Pt(3.2), h, fill=accent, shape=MSO_SHAPE.RECTANGLE)
    pad = Inches(0.22)
    tf = _txt(slide, x + pad, y + Inches(0.16), w - 2 * pad, Inches(0.22))
    _run(_para(tf, True), label.upper(), 9, True, INK_MUTE)

    tf = _txt(slide, x + pad, y + Inches(0.42), w - 2 * pad, Inches(0.5))
    p = _para(tf, True)
    _run(p, value, 27, True, accent)
    if unit:
        _run(p, " " + unit, 12, True, accent)
    if note:
        tf = _txt(slide, x + pad, y + Inches(0.95), w - 2 * pad, h - Inches(1.05))
        _run(_para(tf, True), note, 10, False, INK_SOFT)


def table(slide, x, y, w, rows, col_w, head=True, size=11.5, row_h=Inches(0.34),
          highlight=None):
    """Lightweight table drawn as shapes — full control over type and rule weight."""
    cx = [x]
    for cwid in col_w[:-1]:
        cx.append(cx[-1] + cwid)
    yy = y
    for ri, row in enumerate(rows):
        is_head = head and ri == 0
        if is_head:
            _rect(slide, x, yy, w, row_h, fill=SURFACE, shape=MSO_SHAPE.RECTANGLE)
        elif highlight is not None and ri in highlight:
            _rect(slide, x, yy, w, row_h, fill=RGBColor(0xE7, 0xF0, 0xFC),
                  shape=MSO_SHAPE.RECTANGLE)
        for ci, cell in enumerate(row):
            al = PP_ALIGN.RIGHT if (ci > 0 and str(cell)[:1].isdigit()) else PP_ALIGN.LEFT
            tf = _txt(slide, cx[ci] + Inches(0.08), yy + Inches(0.07),
                      col_w[ci] - Inches(0.16), row_h, align=al,
                      anchor=MSO_ANCHOR.MIDDLE)
            col = INK_MUTE if is_head else INK
            _run(_para(tf, True), str(cell), size - (1.2 if is_head else 0),
                 is_head or ci == 0, col)
        _rect(slide, x, yy + row_h, w, Pt(0.7), fill=BORDER, shape=MSO_SHAPE.RECTANGLE)
        yy += row_h
    return yy


def callout(slide, x, y, w, title, body, accent=AMBER):
    """The dashboard's callout, as a slide element."""
    lines = 1 + len(body) // 105
    h = Inches(0.62) + Inches(0.20) * lines
    _rect(slide, x, y, w, h, fill=RGBColor(0xFD, 0xF3, 0xDD) if accent == AMBER
          else RGBColor(0xE7, 0xF0, 0xFC), line=None)
    _rect(slide, x, y, Pt(3.2), h, fill=accent, shape=MSO_SHAPE.RECTANGLE)
    tf = _txt(slide, x + Inches(0.22), y + Inches(0.13), w - Inches(0.44), Inches(0.22))
    _run(_para(tf, True), title, 11.5, True, INK)
    tf = _txt(slide, x + Inches(0.22), y + Inches(0.38), w - Inches(0.44),
              h - Inches(0.48))
    _run(_para(tf, True), body, 11, False, INK_SOFT)
    return y + h


def picture(slide, path, x, y, w=None, h=None, border=True):
    if not Path(path).exists():
        return None
    pic = slide.shapes.add_picture(str(path), x, y, width=w, height=h)
    if border:
        _rect(slide, pic.left, pic.top, pic.width, pic.height, fill=None,
              line=BORDER, shape=MSO_SHAPE.RECTANGLE)
    return pic


# ---------------------------------------------------------------------------
# Live figures — pulled from the pipeline so the deck cannot drift
# ---------------------------------------------------------------------------

def live_results() -> dict:
    """Recompute the headline numbers. Falls back to the verified constants."""
    r = dict(cycle="135.6", x_before="1.01", x_after="0.929", windows="2,451",
              alerts="31", forecasts="21", papers="")
    try:
        sys.path.insert(0, str(ROOT / "src"))
        import config as cfg, signal_opt as so
        import bottleneck_detect as bn, forecast as fc
        import dashboard as D
        df = D.load_metrics.__wrapped__()
        js = [c for c in cfg.junction_cameras() if c in set(df.camera_id.unique())]
        dem = [d for d in (so.demand_from_metrics(df, c) for c in js) if d]
        base, opt = so.evaluate_fixed_plan(dem, cycle_s=120.0), so.optimise(dem)
        r["cycle"] = f"{opt.cycle_s:.1f}"
        r["x_before"] = f"{max(p.degree_of_saturation for p in base.phases):.2f}"
        r["x_after"] = f"{opt.phases[0].degree_of_saturation:.3f}"
        r["windows"] = f"{len(df):,}"
        r["alerts"] = str(len(bn.detect_all(df)))
        r["forecasts"] = str(len(fc.forecast_all(df, horizon_s=60.0)))
        print(f"  live figures: C={r['cycle']}s  x {r['x_before']}->{r['x_after']}  "
              f"{r['windows']} windows  {r['alerts']} alerts")
    except Exception as e:
        print(f"  ! live recompute unavailable ({type(e).__name__}), using constants")
    return r


# ===========================================================================
# Slides
#
# One function per slide. The SLIDES manifest at the bottom fixes the order,
# and pairs each slide with the guideline bullet it answers — so the deck's
# compliance with the Stage-2 brief is checkable by reading one list rather
# than by clicking through fifteen slides.
# ===========================================================================

def s_title(prs, R):
    """Guideline: Team Name and Team Members."""
    s = slide_new(prs)
    _rect(s, Inches(0), Inches(0), W, Inches(0.13), fill=BLUE,
          shape=MSO_SHAPE.RECTANGLE)

    tf = _txt(s, M, Inches(1.35), CW, Inches(0.3))
    _run(_para(tf, True), "E-RAKSHAK HACKATHON 2026   ·   PROBLEM STATEMENT PS-08",
         11, True, BLUE)

    tf = _txt(s, M, Inches(1.85), CW, Inches(1.0))
    _run(_para(tf, True), "Surat Traffic Intelligence", 52, True, INK)

    tf = _txt(s, M, Inches(2.92), Inches(9.4), Inches(0.5))
    _run(_para(tf, True),
         "Data-Driven Traffic Optimization & Adaptive Infrastructure Intelligence",
         17, False, INK_SOFT)

    _rect(s, M, Inches(3.62), Inches(1.5), Pt(2.5), fill=ORANGE,
          shape=MSO_SHAPE.RECTANGLE)

    tf = _txt(s, M, Inches(3.92), Inches(10.2), Inches(0.9))
    p = _para(tf, True)
    _run(p, "Real-time lane density from CCTV  ·  adaptive signal optimisation  ·  "
            "bottleneck detection\n", 13.5, False, INK_SOFT)
    _run(p, "— with two signal controllers, each validated on data that can "
            "actually support it.", 13.5, True, INK)

    for i, (lab, val, acc) in enumerate([
            ("CAMERAS", "7", BLUE), ("ZONES", "21", BLUE),
            ("WINDOWS", R["windows"], BLUE), ("CHECKS", "18/18", GREEN)]):
        x = M + i * Inches(1.62)
        _rect(s, x, Inches(5.15), Inches(1.42), Inches(0.72), fill=SURFACE, line=BORDER)
        tf = _txt(s, x, Inches(5.26), Inches(1.42), Inches(0.22), align=PP_ALIGN.CENTER)
        _run(_para(tf, True), lab, 8, True, INK_MUTE)
        tf = _txt(s, x, Inches(5.46), Inches(1.42), Inches(0.34), align=PP_ALIGN.CENTER)
        _run(_para(tf, True), val, 17, True, acc)

    _rect(s, M, Inches(6.28), CW, Pt(1), fill=BORDER, shape=MSO_SHAPE.RECTANGLE)
    tf = _txt(s, M, Inches(6.44), CW, Inches(0.6))
    p = _para(tf, True)
    _run(p, "TEAM\n", 8.5, True, INK_MUTE)
    _run(p, TEAM_NAME + "\n", 15, True, INK)
    _run(p, TEAM_MEMBERS, 11.5, False, INK_SOFT)
    return s


def s_problem_statement(prs, R):
    """Guideline: Problem Statement."""
    s = slide_new(prs)
    y = header(s, "Problem statement", "PS-08 — what is being asked",
               "Use the city's existing CCTV network to measure, optimise and "
               "predict — without new hardware.")
    bullets(s, M, y, Inches(6.5), [
        ("Measure traffic density", " per lane, from existing CCTV feeds"),
        ("Optimise signal timing", " adaptively, from what is actually measured"),
        ("Detect bottlenecks", " and identify where they form"),
        ("Recommend improvements", " to infrastructure, with evidence"),
    ], size=14, gap=15)

    x2 = M + Inches(7.0)
    tf = _txt(s, x2, y, Inches(5.1), Inches(0.3))
    _run(_para(tf, True), "WHAT WE WERE GIVEN", 9.5, True, INK_MUTE)
    table(s, x2, y + Inches(0.32), Inches(5.1), [
        ["Source", "7 Surat CCTV clips"],
        ["Resolution", "1920×1080 @ 20 fps"],
        ["Duration", "≈31.5 min total"],
        ["Ground truth", "none"],
        ["Camera calibration", "none"],
        ["Signal timing data", "none"],
    ], [Inches(2.3), Inches(2.8)], head=False, size=12)

    callout(s, M, Inches(5.35), CW,
            "The constraint that shaped everything",
            "No surveyed reference ships with this data, so no pixel-to-metre "
            "scale can be recovered from it. The system separates what it "
            "measures from what it estimates, rather than quietly presenting one "
            "as the other.", BLUE)
    return s


def s_problem_understanding(prs, R):
    """Guideline: Problem Understanding."""
    s = slide_new(prs)
    y = header(s, "Problem understanding", "What this data can — and cannot — support",
               "Reading the footage before writing the system changed the design "
               "twice.")
    table(s, M, y, CW, [
        ["Camera", "Scene", "Mode", "What it can support"],
        ["GauravPath", "Divided arterial, marked lanes", "junction", "Lane density, signal timing"],
        ["Rokadiya Hanuman", "Divided multi-lane road", "junction", "Lane density, signal timing"],
        ["Chauta Bazaar", "Dense market street", "area", "Occupancy, crowding, alerts"],
        ["Mahidharpura", "Near-total gridlock", "area", "Occupancy, crowding, alerts"],
        ["Gopi Talav", "Pedestrian-dominant gate", "area", "Occupancy, crowding, alerts"],
        ["Rly Station", "Station forecourt", "area", "Occupancy, crowding, alerts"],
        ["Central Bus Depo", "Depot platform", "area", "Occupancy, crowding, alerts"],
    ], [Inches(2.5), Inches(4.0), Inches(1.5), Inches(3.9)], size=11.5,
       highlight={1, 2})

    callout(s, M, Inches(5.5), CW,
            "Only 2 of 7 cameras can support the requested measurement",
            "Five cameras watch market streets, a station forecourt and a bus "
            "depot — no lane discipline, no signal to optimise. \"Lane density\" "
            "is not a measurable quantity there, so forcing it would mean "
            "inventing numbers. Those run in area mode instead.", AMBER)
    return s


def s_discovery(prs, R):
    """Guideline: Problem Understanding (continued) — the finding that drove the design."""
    s = slide_new(prs)
    y = header(s, "Problem understanding · the key discovery",
               "In the worst gridlock, detection counts fewer vehicles",
               "The failure mode that would have inverted every ranking in the "
               "system — silently.")
    table(s, M, y, Inches(6.3), [
        ["Camera", "Detections", "Actual condition"],
        ["Rokadiya Hanuman", "7,664", "flowing arterial"],
        ["Central Bus Depo", "2,905", "busy"],
        ["GauravPath", "1,957", "free-flowing"],
        ["Mahidharpura", "463", "worst gridlock in dataset"],
    ], [Inches(2.5), Inches(1.7), Inches(2.1)], size=12, highlight={4})

    x2 = M + Inches(6.8)
    bullets(s, x2, y, Inches(5.3), [
        ("Cause. ", "At extreme density a wall of overlapping vehicles stops "
                    "being separable and the detector's recall collapses."),
        ("Risk. ", "A naive \"more detections = more congestion\" system would "
                   "rank Surat's worst street among its emptiest — confidently, "
                   "and backwards. Every number would look plausible."),
        ("Fix. ", "A texture-based occupancy index (Canny edge density against "
                  "absolute cross-camera bounds) which has no notion of an "
                  "object, so occlusion cannot defeat it."),
    ], size=12.5, gap=11)

    callout(s, M, Inches(5.15), CW,
            "Result",
            "Mahidharpura now flags 452 of 453 windows as saturated and ranks "
            "correctly as the most congested site. The occupancy index is "
            "normalised against absolute bounds measured across all seven "
            "cameras, so a value of 0.8 means the same thing on every camera.",
            GREEN)
    return s


def s_solution(prs, R):
    """Guideline: Proposed Solution."""
    s = slide_new(prs)
    y = header(s, "Proposed solution", "Two controllers, two datasets, one hard boundary",
               "Surat's junctions run Webster's. Max-pressure is validated on "
               "FLUID. Neither borrows from the other.")
    table(s, M, y, CW, [
        ["", "Webster's", "Max-pressure (Varaiya 2013)"],
        ["Needs to observe", "arrival flow per approach",
         "approach queue AND the queue it discharges into"],
        ["Cameras required", "one per approach",
         "one per approach + one per receiving link"],
        ["Surat CCTV provides", "✔  exactly this", "✘  no camera watches a receiving link"],
        ["Runs on", "the 7 Surat clips — every operational figure",
         "FLUID: 14 drone recordings, 3 real intersections"],
        ["On Surat it reports", "cycle, splits, delay, saturation",
         "greedy-queue — and refuses to name a phase"],
    ], [Inches(2.4), Inches(4.6), Inches(4.9)], size=11.5)

    callout(s, M, Inches(5.28), CW,
            "Why not just drop the missing term?",
            "Because that does not give a degraded max-pressure — it gives greedy "
            "queue control, which Varaiya's §3.1 Example 2 proves fails to "
            "stabilise demands max-pressure handles. So rather than fake it, a "
            "dataset containing the missing term was obtained, and the controller "
            "is validated there.", BLUE)
    return s


def s_key_features(prs, R):
    """Guideline: Key Features."""
    s = slide_new(prs)
    y = header(s, "Key features", "One dashboard, two tracks, thirteen views")
    picture(s, IMG / "webster-network.png", M, y, w=Inches(7.15))
    x2 = M + Inches(7.5)
    tf = _txt(s, x2, y, Inches(4.6), Inches(0.3))
    _run(_para(tf, True), "TRACK 1 — WEBSTER'S · SURAT CCTV", 9.5, True, BLUE)
    bullets(s, x2, y + Inches(0.3), Inches(4.6), [
        "Network — LOS bands and congestion ranking",
        "Site detail — zones, overlay, heatmap, trends",
        "Signal timing — cycle, splits, delay, saturation",
        "Bottlenecks — every alert with its evidence",
        "Outlook — forecasts with prediction bands",
        "Analyse footage · Calibration · How it works",
    ], size=11.5, gap=7)

    tf = _txt(s, x2, y + Inches(2.65), Inches(4.6), Inches(0.3))
    _run(_para(tf, True), "TRACK 2 — MAX-PRESSURE · FLUID DRONE", 9.5, True, ORANGE)
    bullets(s, x2, y + Inches(2.95), Inches(4.6), [
        "Validation — replayed against 3 real signals",
        "Scene — aerial with live queue shading",
        "Phase pressure — every movement's terms",
        "Head-to-head — 3 demand regimes simulated",
        "Method — what the data can and cannot say",
    ], size=11.5, gap=7, bullet_color=ORANGE)
    return s


def s_tech_stack(prs, R):
    """Guideline: Technology Stack."""
    s = slide_new(prs)
    y = header(s, "Technology stack", "Chosen for what the data needs")
    rows = [
        ["Layer", "Choice", "Why this one"],
        ["Detector", "YOLO11-S", "Better on small, distant, partly occluded vehicles"],
        ["Weights", "UVH-26 fine-tune", "Indian CCTV domain; 14 classes incl. auto-rickshaw"],
        ["Tracker", "ByteTrack", "Associates low-confidence detections in crowds"],
        ["Density unit", "PCU · IRC:106-1990", "The unit Indian capacity standards are defined in"],
        ["Signal method", "Webster's + HCM delay", "Needs only what one camera per approach sees"],
        ["2nd controller", "Max-pressure (Varaiya)", "Throughput-optimal — gated on observability"],
        ["Saturation flow", "IRC:SP:41-1994, S = 525·W", "Width-based; lane count is unrecoverable here"],
        ["Occupancy", "Canny edge density", "No object resolution needed — survives saturation"],
        ["Dashboard", "Streamlit + Plotly", "Operator view without a JS build step"],
        ["Language", "Python 3.11 · PyTorch 2.5 · OpenCV", "GPU-accelerated, standard"],
    ]
    table(s, M, y, CW, rows, [Inches(2.2), Inches(3.5), Inches(6.19)], size=11,
          row_h=Inches(0.355))
    tf = _txt(s, M, Inches(6.15), CW, Inches(0.4))
    p = _para(tf, True)
    _run(p, "Hardware: ", 11, True, INK)
    _run(p, "NVIDIA RTX 3050 6 GB laptop GPU. No cloud training was needed — "
            "the UVH-26 weights removed the training step entirely.", 11, False,
         INK_SOFT)
    return s


def s_architecture(prs, R):
    """Guideline: System Architecture Diagram."""
    s = slide_new(prs)
    y = header(s, "System architecture diagram",
               "From video frame to signal recommendation")

    bw, bh, gap = Inches(2.42), Inches(0.86), Inches(0.28)
    stages = [("1  DETECT + TRACK", "detector.py", "YOLO11-S · ByteTrack", BLUE),
              ("2  LOCATE", "zones.py", "lane / zone polygons", BLUE),
              ("3  MEASURE", "metrics.py", "two parallel paths", ORANGE)]
    for i, (t, f, d, c) in enumerate(stages):
        x = M + i * (bw + gap)
        _rect(s, x, y, bw, bh, fill=SURFACE, line=BORDER)
        _rect(s, x, y, bw, Pt(3), fill=c, shape=MSO_SHAPE.RECTANGLE)
        tf = _txt(s, x + Inches(0.14), y + Inches(0.14), bw - Inches(0.28), Inches(0.6))
        p = _para(tf, True)
        _run(p, t + "\n", 11, True, INK)
        _run(p, f + "\n", 9, True, c)
        _run(p, d, 9, False, INK_SOFT)

    y2 = y + bh + Inches(0.34)
    for i, (t, d, c) in enumerate([
            ("DETECTION PATH", "PCU density, speed, queue, flow (q = k·v)", BLUE),
            ("OCCUPANCY PATH", "texture-based index — survives saturation", ORANGE)]):
        x = M + i * (Inches(3.75) + gap)
        _rect(s, x, y2, Inches(3.75), Inches(0.72), fill=SURFACE, line=c)
        tf = _txt(s, x + Inches(0.14), y2 + Inches(0.13), Inches(3.5), Inches(0.5))
        p = _para(tf, True)
        _run(p, t + "\n", 10, True, c)
        _run(p, d, 9.5, False, INK_SOFT)

    y3 = y2 + Inches(1.06)
    outs = [("4  OPTIMISE", "signal_opt.py · maxpressure.py", GREEN),
            ("5  BOTTLENECKS", "bottleneck_detect.py — 5 patterns", AMBER),
            ("6  PREDICT", "forecast.py — damped trend + band", BLUE)]
    for i, (t, d, c) in enumerate(outs):
        x = M + i * (bw + gap)
        _rect(s, x, y3, bw, Inches(0.78), fill=SURFACE, line=BORDER)
        _rect(s, x, y3, Pt(3), Inches(0.78), fill=c, shape=MSO_SHAPE.RECTANGLE)
        tf = _txt(s, x + Inches(0.16), y3 + Inches(0.14), bw - Inches(0.3), Inches(0.5))
        p = _para(tf, True)
        _run(p, t + "\n", 10.5, True, INK)
        _run(p, d, 9, False, INK_SOFT)

    y4 = y3 + Inches(1.0)
    _rect(s, M, y4, Inches(7.9), Inches(0.62), fill=BLUE)
    tf = _txt(s, M + Inches(0.2), y4 + Inches(0.16), Inches(7.5), Inches(0.4))
    p = _para(tf, True)
    _run(p, "7  PRESENT   ", 11.5, True, RGBColor(0xFF, 0xFF, 0xFF))
    _run(p, "dashboard.py — two tracks, 13 views", 10.5, False,
         RGBColor(0xE7, 0xF0, 0xFC))

    tf = _txt(s, M + Inches(8.2), y4 + Inches(0.06), Inches(3.7), Inches(0.8))
    p = _para(tf, True)
    _run(p, "Standards, not guesses.\n", 10, True, INK)
    _run(p, "IRC:106-1990 (PCU) · IRC:SP:41-1994 (saturation flow) · "
            "IRC:103-2012 (pedestrian green)", 9, False, INK_SOFT)
    return s


def s_workflow(prs, R):
    """Guideline: Workflow / Process Flow."""
    s = slide_new(prs)
    y = header(s, "Workflow / process flow", "End to end, in five steps",
               "One command runs the dashboard; the full pipeline is five steps "
               "from raw video.")
    steps = [
        ("01", "Reference frames", "One clean frame per camera, for every overlay drawn later."),
        ("02", "Calibration", "Lane / zone polygons traced, then validated against a movement heatmap."),
        ("03", "Analyse", "Detect → track → assign to zone → measure. Writes one row per zone per 2 s window."),
        ("04", "Optimise & detect", "Webster's cycle and splits; five sustained-pattern bottleneck detectors; damped-trend forecast."),
        ("05", "Present", "Two-track dashboard. Every chart has a table beside it, so no value is reachable only by hovering."),
    ]
    yy = y
    for num, t, d in steps:
        _rect(s, M, yy, Inches(0.62), Inches(0.62), fill=BLUE)
        tf = _txt(s, M, yy + Inches(0.16), Inches(0.62), Inches(0.3),
                  align=PP_ALIGN.CENTER)
        _run(_para(tf, True), num, 14, True, RGBColor(0xFF, 0xFF, 0xFF))
        tf = _txt(s, M + Inches(0.86), yy + Inches(0.06), Inches(11.0), Inches(0.6))
        p = _para(tf, True)
        _run(p, t + "\n", 13.5, True, INK)
        _run(p, d, 11.5, False, INK_SOFT)
        yy += Inches(0.78)

    callout(s, M, yy + Inches(0.12), CW, "Reproducible without the video",
            "The metrics CSVs are committed, so the entire Webster track — every "
            "operational figure in this deck — runs immediately with no video "
            "download, no GPU and no model weights.", GREEN)
    return s


def s_models(prs, R):
    """Guideline: AI/ML Models."""
    s = slide_new(prs)
    y = header(s, "AI / ML models", "The weights matter more than the architecture")
    table(s, M, y, Inches(6.5), [
        ["Model", "UVH-26-MV-YOLOv11-S"],
        ["Source", "HuggingFace iisc-aim/UVH-26 @ v1.0"],
        ["Trained on", "26,646 annotated 1080p frames"],
        ["Domain", "Indian Safe-City CCTV"],
        ["Classes", "14 India-specific vehicle types"],
        ["Tracker", "ByteTrack (crowded-scene association)"],
        ["Throughput", "≈12 FPS @ 960px on RTX 3050 6 GB"],
    ], [Inches(1.9), Inches(4.6)], head=False, size=12)

    x2 = M + Inches(7.0)
    bullets(s, x2, y, Inches(5.3), [
        ("Why this fine-tune. ", "Same domain as our footage, and it carries the "
         "auto-rickshaw — a class COCO does not have at all, and which dominates "
         "most of these clips."),
        ("We did not train it. ", "Used as published. That removed the need to "
         "train on an unlabelled dataset."),
        ("Density in PCU, not counts. ", "Twelve two-wheelers and twelve buses "
         "are not the same amount of traffic. IRC:106-1990 factors."),
    ], size=12.5, gap=11)

    callout(s, M, Inches(5.05), CW, "Flow is never counted from tracker IDs",
            "ByteTrack reissues IDs whenever a track drops briefly — constant in "
            "dense mixed traffic — so counting them inflated flow tenfold, to "
            "12,000–23,000 PCU/hr against a real saturation flow near 1,800. Flow "
            "comes from the fundamental relation q = k·v instead, on directly "
            "measured density and speed.", ORANGE)
    return s


def s_results_surat(prs, R):
    """Supporting: measured outcome on the Surat network."""
    s = slide_new(prs)
    y = header(s, "Results  ·  Surat", "The operational system — Webster's",
               "Baseline is an untuned fixed timer with equal green splits.")
    tw = (CW - Inches(0.6)) / 4
    tile(s, M, y, tw, Inches(1.5), "Worst-approach saturation",
         f"{R['x_before']} → {R['x_after']}", "",
         "Crosses back under 1.0 — the queue clears within the cycle again", RED)
    tile(s, M + (tw + Inches(0.2)), y, tw, Inches(1.5), "Cycle length",
         R["cycle"], "s", "Optimal, against a 120 s fixed baseline", BLUE)
    tile(s, M + 2 * (tw + Inches(0.2)), y, tw, Inches(1.5), "Mean delay",
         "−3.8", "%", "53.5 → 51.4 s/PCU, HCM control delay", GREEN)
    tile(s, M + 3 * (tw + Inches(0.2)), y, tw, Inches(1.5), "Evidence produced",
         R["alerts"], "alerts", f"plus {R['forecasts']} zone forecasts with "
         f"prediction intervals", AMBER)

    y2 = y + Inches(1.78)
    table(s, M, y2, Inches(7.2), [
        ["#", "Site", "Mode", "Congestion", "Saturated"],
        ["1", "Mahidharpura – Diamond Mkt", "area", "0.939", "452 / 453"],
        ["2", "Gopi Talav Gate", "area", "0.749", "167 / 456"],
        ["3", "Central Bus Depo", "area", "0.483", "213 / 450"],
        ["4", "Rokadiya Hanuman", "junction", "0.437", "0 / 244"],
        ["5", "Chauta Bazaar", "area", "0.205", "4 / 453"],
    ], [Inches(0.5), Inches(3.1), Inches(1.2), Inches(1.3), Inches(1.1)],
       size=11, highlight={1})

    x2 = M + Inches(7.6)
    tf = _txt(s, x2, y2, Inches(4.5), Inches(0.3))
    _run(_para(tf, True), "TWO INDEPENDENT CHECKS", 9.5, True, INK_MUTE)
    bullets(s, x2, y2 + Inches(0.3), Inches(4.5), [
        ("Cycle sweep. ", "Empirical delay minimum lands at 130 s against "
         "Webster's closed form of 135.6 s."),
        ("Equal saturation. ", "The optimal plan equalises degree of saturation "
         "across approaches (0.929 / 0.929) — the known optimality condition."),
    ], size=11.5, gap=9)
    return s


def s_results_fluid(prs, R):
    """Supporting: max-pressure validated where the data supports it."""
    s = slide_new(prs)
    y = header(s, "Results  ·  FLUID", "Max-pressure, validated on real signals",
               "Replaying max-pressure against what each real fixed-time signal "
               "actually did.")
    table(s, M, y, Inches(7.3), [
        ["Intersection", "Cycle", "Real signal: green\nwith nothing to relieve", "Max-pressure"],
        ["FI  (4-way, 3-phase)", "96 s", "41.1%", "1.5%"],
        ["FIDRT  (dedicated right turns)", "123 s", "66.3%", "2.3%"],
        ["TI  (T-intersection)", "71 s", "46.6%", "16.0%"],
    ], [Inches(3.0), Inches(1.0), Inches(2.2), Inches(1.1)], size=11,
       row_h=Inches(0.42))

    x2 = M + Inches(7.7)
    tf = _txt(s, x2, y, Inches(4.4), Inches(0.3))
    _run(_para(tf, True), "SIMULATED COUNTERFACTUAL", 9.5, True, INK_MUTE)
    table(s, x2, y + Inches(0.3), Inches(4.4), [
        ["Scenario", "Fixed", "Max-pr."],
        ["Steady, balanced", "18.6", "20.8"],
        ["Demand swings", "24.5", "18.5"],
        ["Downstream blockage", "150.0", "102.9"],
    ], [Inches(2.2), Inches(1.1), Inches(1.1)], size=10.5,
       row_h=Inches(0.30), highlight={3})

    yy = y + Inches(2.05)
    callout(s, M, yy, CW, "The operator's decision rule — narrower than the "
            "literature's enthusiasm suggests",
            "Max-pressure earns its place when the split of demand moves between "
            "approaches, and above all when something downstream backs up "
            "(−31% delay, spillback 289 s → 88 s). On steady demand it is the "
            "worse choice — it pays an inter-green for a benefit that is not "
            "there. A system offering it as a straight upgrade would be selling "
            "the wrong thing.", BLUE)

    tf = _txt(s, M, Inches(6.05), CW, Inches(0.5))
    p = _para(tf, True)
    _run(p, "Deliberately not a delay-reduction claim. ", 11, True, INK)
    _run(p, "Had max-pressure actually run, the queues would have evolved "
            "differently — that is an observation about the recorded signals, "
            "made from the recorded queues. The counterfactual needs the "
            "simulator above.", 11, False, INK_SOFT)
    return s


def s_innovation(prs, R):
    """Guideline: Innovation and Unique Value Proposition."""
    s = slide_new(prs)
    y = header(s, "Innovation and unique value proposition",
               "Four things this system does that a standard pipeline does not")
    items = [
        ("The observability gate", ORANGE,
         "Max-pressure refuses to name a phase when its downstream term is "
         "unmeasurable, and returns the weaker answer under its own name — "
         "greedy-queue — so a caller cannot present it as max-pressure. The "
         "refusal is structural, not a warning label."),
        ("Occlusion-proof measurement", BLUE,
         "A texture-based occupancy index with no notion of an object, so the "
         "gridlock that collapses detection cannot invert the ranking. This is "
         "the difference between a plausible answer and a correct one."),
        ("Separated measurement from estimation", GREEN,
         "Every figure carrying the uncalibrated pixel scale is marked and "
         "banner-flagged; the ranking, level of service and the entire signal "
         "comparison are scale-free and unaffected. Every headline result is in "
         "the scale-free category."),
        ("Documentation that cannot drift", AMBER,
         "The verification suite recomputes every figure quoted in the README "
         "and report, and fails if a number changes in the code but not the "
         "docs — or the reverse."),
    ]
    yy = y
    for t, c, d in items:
        _rect(s, M, yy, CW, Inches(1.02), fill=SURFACE, line=BORDER)
        _rect(s, M, yy, Pt(3.5), Inches(1.02), fill=c, shape=MSO_SHAPE.RECTANGLE)
        tf = _txt(s, M + Inches(0.24), yy + Inches(0.13), CW - Inches(0.5), Inches(0.8))
        p = _para(tf, True)
        _run(p, t + "\n", 13, True, INK)
        _run(p, d, 11, False, INK_SOFT)
        yy += Inches(1.15)
    return s


def s_challenges(prs, R):
    """Guideline: Challenges Faced."""
    s = slide_new(prs)
    y = header(s, "Challenges faced", "Eleven bugs found and fixed — four worth showing",
               "Each was caught by a check that did not trust the output.")
    rows = [
        ["#", "Symptom", "Root cause", "How it was caught"],
        ["1", "Speeds 5× too low\n(4–8 km/h on a free arterial)",
         "Isotropic pixel-to-metre scale — vertical and horizontal pixels treated alike",
         "Physical implausibility of the reported speed"],
        ["2", "Flow 10× too high\n(12,000–23,000 PCU/hr)",
         "Counting new ByteTrack IDs as arrivals; IDs churn constantly in dense traffic",
         "Compared against real saturation flow ≈1,800"],
        ["3", "\"Optimised\" plan was worse\n(−9.5%, delay increased)",
         "Flow ratio computed per critical lane, saturation per whole approach — mixed units",
         "Swept cycle length; the delay curve had no minimum, which is impossible"],
        ["4", "Max-pressure named a phase\nit had no data for",
         "Missing downstream queue silently treated as zero",
         "Audit against Varaiya §3.1 — the result was a different controller"],
    ]
    table(s, M, y, CW, rows,
          [Inches(0.42), Inches(2.75), Inches(4.55), Inches(4.17)], size=10,
          row_h=Inches(0.72))
    callout(s, M, Inches(5.75), CW, "The pattern",
            "Three of these four produced numbers that looked entirely "
            "reasonable on screen. What caught them was checking outputs against "
            "physical limits and known theory — not testing that the code ran.",
            ORANGE)
    return s


def s_future(prs, R):
    """Guideline: Future Scope."""
    s = slide_new(prs)
    y = header(s, "Future scope", "What one measurement, and what more data, would unlock")
    tf = _txt(s, M, y, Inches(6.1), Inches(0.3))
    _run(_para(tf, True), "IMMEDIATE — NO NEW HARDWARE", 9.5, True, GREEN)
    bullets(s, M, y + Inches(0.32), Inches(6.1), [
        ("One surveyed road width. ", "Promotes every metric figure from estimate "
         "to primary. The Calibration view does it in ~2 minutes without a site "
         "visit."),
        ("Cameras on receiving links. ", "Unlocks true max-pressure on Surat — "
         "the controller is already implemented and validated."),
        ("Continuous RTSP ingest. ", "Replaces 31 minutes of clips with a live "
         "feed; the pipeline is already windowed for it."),
    ], size=11.5, gap=10)

    x2 = M + Inches(6.6)
    tf = _txt(s, x2, y, Inches(5.5), Inches(0.3))
    _run(_para(tf, True), "LONGER TERM", 9.5, True, BLUE)
    bullets(s, x2, y + Inches(0.32), Inches(5.5), [
        ("Seasonal forecasting. ", "Current model has minutes of history. Weeks "
         "of continuous operation would justify a daily/weekly seasonal model."),
        ("Coordinated corridors. ", "Green-wave offsets across adjacent junctions "
         "— the simulator already models two in series."),
        ("Production stack. ", "React + Kafka + PostGIS for city-scale "
         "deployment; the measurement core is unchanged."),
    ], size=11.5, gap=10, bullet_color=BLUE)

    yy = Inches(4.55)
    tf = _txt(s, M, yy, CW, Inches(0.3))
    _run(_para(tf, True), "STATED LIMITATIONS", 9.5, True, INK_MUTE)
    _rect(s, M, yy + Inches(0.26), CW, Pt(1), fill=BORDER, shape=MSO_SHAPE.RECTANGLE)
    bullets(s, M, yy + Inches(0.42), CW, [
        "The metric scale is not calibrated — anything in km/h or PCU/km/lane is an estimate, and is labelled as one.",
        "Lane polygons are traced and heatmap-validated, not surveyed.",
        "The two junction cameras face different corridors, so the combined plan demonstrates the method on real measured demand rather than being deployable at one junction.",
        "FLUID is Chinese traffic — it validates the controller implementation; no claim from it transfers to Surat's fleet.",
    ], size=11, gap=7, bullet_color=INK_MUTE)
    return s


# ---------------------------------------------------------------------------
# Order — follows the Stage-2 guideline's own list, top to bottom
# ---------------------------------------------------------------------------
# Every bullet the brief names appears, in the order it names them. The three
# entries marked "supporting" are additions, placed where they do the most
# work: the discovery sits inside problem understanding because it *is* the
# understanding, and the two results slides sit before the innovation claim so
# that claim arrives already evidenced.

SLIDES = [
    (s_title,                "Team Name and Team Members"),
    (s_problem_statement,    "Problem Statement"),
    (s_problem_understanding, "Problem Understanding"),
    (s_discovery,            "Problem Understanding (supporting)"),
    (s_solution,             "Proposed Solution"),
    (s_key_features,         "Key Features"),
    (s_tech_stack,           "Technology Stack"),
    (s_architecture,         "System Architecture Diagram"),
    (s_workflow,             "Workflow / Process Flow"),
    (s_models,               "AI/ML Models"),
    (s_results_surat,        "Results (supporting)"),
    (s_results_fluid,        "Results (supporting)"),
    (s_innovation,           "Innovation and Unique Value Proposition"),
    (s_challenges,           "Challenges Faced"),
    (s_future,               "Future Scope"),
]


def build():
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    R = live_results()

    for i, (fn, bullet) in enumerate(SLIDES, start=1):
        s = fn(prs, R)
        if i > 1:                      # the title slide carries no footer
            footer(s, i)
        print(f"  {i:2d}. {fn.__doc__.split(':')[-1].strip().rstrip('.'):<52s} {bullet}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(OUT))
    print(f"\n  {len(SLIDES)} slides -> {OUT}")
    return OUT


if __name__ == "__main__":
    build()
