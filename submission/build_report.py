"""Build the standalone PS-08 submission report (PDF).

    python submission/build_report.py

A self-contained document a judge can read without opening the repository.
Distilled from REPORT.md rather than duplicating it — REPORT.md remains the
full engineering record; this is the submission narrative.

Rendered with PyMuPDF's Story engine so the generator has no Office dependency
and runs anywhere. Headline figures are recomputed from the live pipeline at
build time for the same reason the deck does it: a number quoted in a document
should come from the place the system actually gets it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "Surat-Traffic-Intelligence-PS08-Report.pdf"

TEAM_NAME = "[TEAM NAME]"
TEAM_MEMBERS = "[Member 1] · [Member 2] · [Member 3] · [Member 4]"

CSS = """
* { font-family: sans-serif; }
body { font-size: 10pt; color: #1a1a18; line-height: 1.5; }

h1 { font-size: 26pt; font-weight: bold; color: #0b0b0b;
     margin-top: 0pt; margin-bottom: 2pt; }
h2 { font-size: 14pt; font-weight: bold; color: #1c5cab;
     margin-top: 20pt; margin-bottom: 5pt; }
h3 { font-size: 11pt; font-weight: bold; color: #0b0b0b;
     margin-top: 13pt; margin-bottom: 3pt; }
p  { margin-top: 0pt; margin-bottom: 8pt; text-align: justify; }

.eyebrow { font-size: 9pt; font-weight: bold; color: #2a78d6;
           margin-bottom: 6pt; }
.sub  { font-size: 12pt; color: #52514e; margin-bottom: 3pt; }
.team { font-size: 10pt; color: #52514e; margin-bottom: 16pt; }
.lead { font-size: 11pt; color: #1a1a18; margin-bottom: 10pt; }
.note { font-size: 9pt; color: #52514e; margin-bottom: 8pt; }

.rule { background-color: #e1e0d9; height: 1.4pt; margin-bottom: 12pt;
        margin-top: 4pt; }

.callout { background-color: #e7f0fc; padding: 9pt; margin-bottom: 10pt; }
.callout-warn { background-color: #fdf3dd; padding: 9pt; margin-bottom: 10pt; }
.callout b { color: #0b0b0b; }

table { width: 100%; margin-bottom: 10pt; font-size: 9pt; }
th { background-color: #f0efec; color: #52514e; font-weight: bold;
     text-align: left; padding: 4pt; font-size: 8.5pt; }
td { padding: 4pt; border-bottom: 0.6pt solid #e1e0d9; vertical-align: top; }
td.n { text-align: right; }

li { margin-bottom: 4pt; }
code { font-family: monospace; font-size: 9pt; color: #1c5cab; }
.cap { font-size: 8.5pt; color: #898781; margin-bottom: 12pt; }
"""


def live() -> dict:
    r = dict(cycle="135.6", xb="1.01", xa="0.929", win="2,451",
             alerts="31", fc="21", cams="7", zones="21")
    try:
        sys.path.insert(0, str(ROOT / "src"))
        import config as cfg, signal_opt as so
        import bottleneck_detect as bn, forecast as fo
        import dashboard as D
        df = D.load_metrics.__wrapped__()
        js = [c for c in cfg.junction_cameras() if c in set(df.camera_id.unique())]
        dem = [d for d in (so.demand_from_metrics(df, c) for c in js) if d]
        base, opt = so.evaluate_fixed_plan(dem, cycle_s=120.0), so.optimise(dem)
        r.update(cycle=f"{opt.cycle_s:.1f}",
                 xb=f"{max(p.degree_of_saturation for p in base.phases):.2f}",
                 xa=f"{opt.phases[0].degree_of_saturation:.3f}",
                 win=f"{len(df):,}",
                 alerts=str(len(bn.detect_all(df))),
                 fc=str(len(fo.forecast_all(df, horizon_s=60.0))),
                 cams=str(df.camera_id.nunique()),
                 zones=str(df.groupby(['camera_id', 'zone_id']).ngroups))
        print(f"  live: C={r['cycle']}s  x {r['xb']}->{r['xa']}  {r['win']} windows")
    except Exception as e:
        print(f"  ! live recompute unavailable ({type(e).__name__}); using constants")
    return r


def html(R: dict) -> str:
    return f"""
<div class="eyebrow">E-RAKSHAK HACKATHON 2026 &nbsp;·&nbsp; PROBLEM STATEMENT PS-08</div>
<h1>Surat Traffic Intelligence</h1>
<div class="sub">Data-Driven Traffic Optimization &amp; Adaptive Infrastructure Intelligence</div>
<div class="team"><b>{TEAM_NAME}</b> &nbsp;—&nbsp; {TEAM_MEMBERS}</div>
<div class="rule"></div>

<p class="lead">A traffic measurement and signal-optimisation system built on Surat's
existing CCTV network. It measures lane density and congestion from video, computes an
adaptive signal plan from measured demand, detects sustained bottlenecks with the evidence
that raised each one, and forecasts short-horizon congestion. It ships two signal
controllers — and runs each only on data that can actually support it.</p>

<div class="callout"><b>The one-line architecture.</b> Surat's junctions run Webster's
method. Max-pressure is validated on the FLUID drone dataset. Neither borrows from the
other, and an automated test fails if that ever stops being true.</div>

<h2>1. Executive summary</h2>

<p>Problem statement PS-08 asks for traffic density measurement, adaptive signal
optimisation, bottleneck detection and infrastructure recommendations, using the CCTV
network a city already has. We were given seven Surat clips — 1920×1080 at 20 fps,
about 31.5 minutes in total — with no ground truth, no camera calibration and no signal
timing data.</p>

<p>The system analyses all seven cameras into <b>{R['win']} two-second measurement
windows</b> across <b>{R['zones']} zones</b>, and produces:</p>

<table>
<tr><th>Output</th><th>Result</th></tr>
<tr><td>Worst-approach degree of saturation</td>
    <td><b>{R['xb']} → {R['xa']}</b> — an over-capacity approach brought back under 1.0</td></tr>
<tr><td>Optimal cycle length</td><td>{R['cycle']} s, against a 120 s fixed-timer baseline</td></tr>
<tr><td>Mean delay</td><td>53.5 → 51.4 s/PCU (HCM control delay), −3.8%</td></tr>
<tr><td>Bottleneck alerts</td><td>{R['alerts']}, each with its triggering evidence and a specific recommendation</td></tr>
<tr><td>Zone forecasts</td><td>{R['fc']}, with prediction intervals</td></tr>
<tr><td>Max-pressure validation</td><td>Real fixed-time signals waste 41–66% of green; max-pressure 1.5–16%</td></tr>
</table>

<p>Two findings shaped the design more than any algorithm choice, and both are cases where
the obvious implementation would have produced confident, plausible, wrong answers.</p>

<h2>2. What the data actually supports</h2>

<p>Reading the footage before writing the system changed the design twice.</p>

<h3>2.1 Only two of seven cameras can support the requested measurement</h3>

<p>Two cameras — GauravPath and Rokadiya Hanuman — watch divided, lane-marked
carriageways. The other five watch a dense market street, a station forecourt, a
recreational gate area and a bus depot platform. There is visibly no lane discipline in
those scenes and no signal to optimise. "Lane density" is not a measurable quantity
there, and forcing it would mean inventing numbers.</p>

<p>So the registry splits cameras into two modes. <b>Junction</b> cameras support per-lane
density and signal-timing recommendations. <b>Area</b> cameras use functional zones —
movement channel, encroachment, pedestrian area — and report occupancy and crowding
instead. Every number the system reports stays defensible.</p>

<h3>2.2 In the worst gridlock, detection counts fewer vehicles, not more</h3>

<table>
<tr><th>Camera</th><th>Detections</th><th>Actual condition</th></tr>
<tr><td>Rokadiya Hanuman</td><td class="n">7,664</td><td>flowing arterial</td></tr>
<tr><td>Central Bus Depo</td><td class="n">2,905</td><td>busy</td></tr>
<tr><td>GauravPath</td><td class="n">1,957</td><td>free-flowing</td></tr>
<tr><td><b>Mahidharpura</b></td><td class="n"><b>463</b></td><td><b>worst gridlock in the dataset</b></td></tr>
</table>

<p>At extreme density a wall of overlapping vehicles stops being separable and the
detector's recall collapses. A system that equates more detections with more congestion
would therefore rank Surat's worst street among its emptiest — confidently, and backwards,
with every intermediate number looking entirely reasonable.</p>

<div class="callout-warn"><b>The fix.</b> A texture-based occupancy index — Canny edge
density inside each zone, normalised against absolute bounds measured across all seven
cameras — plus an explicit saturation flag. It has no notion of an object, so occlusion
cannot defeat it. The bounds must be absolute rather than per-zone: normalising a zone
against its own observed range makes it its own yardstick and destroys any comparison
between sites. Mahidharpura now flags 452 of 453 windows as saturated and ranks correctly
as the most congested site.</div>

<h2>3. Architecture</h2>

<p>The pipeline has seven stages. Video frames are detected and tracked, located into
lane or zone polygons, then measured along <b>two parallel paths</b> whose outputs feed
signal optimisation, bottleneck detection and forecasting, and finally a two-track
dashboard.</p>

<table>
<tr><th>Stage</th><th>Module</th><th>What it does</th></tr>
<tr><td>1. Detect &amp; track</td><td>detector.py</td><td>YOLO11-S (UVH-26 fine-tune) + ByteTrack, 14 Indian vehicle classes</td></tr>
<tr><td>2. Locate</td><td>zones.py</td><td>Lane / zone polygons, anisotropic perspective scale model</td></tr>
<tr><td>3. Measure</td><td>metrics.py</td><td>Detection path (PCU density, speed, queue, flow) and occupancy path (texture index)</td></tr>
<tr><td>4. Optimise</td><td>signal_opt.py, maxpressure.py</td><td>Webster's cycle and splits; max-pressure with its observability gate</td></tr>
<tr><td>5. Detect bottlenecks</td><td>bottleneck_detect.py</td><td>Five sustained-pattern detectors with recommendations</td></tr>
<tr><td>6. Predict</td><td>forecast.py</td><td>Damped trend with prediction intervals</td></tr>
<tr><td>7. Present</td><td>dashboard.py</td><td>Two tracks, thirteen views</td></tr>
</table>

<p>Which measurement path leads is decided per zone by a saturation check, and the active
mode is carried in the output so the dashboard can be explicit about it. The saturation
check uses two independent signatures — high density with heavy mutual overlap, and
"full scene, empty count" where texture says packed but detection boxes cover almost
nothing. Only the second catches the collapse case, because once detection fails there
are too few boxes left to overlap.</p>

<h2>4. Signal control — two controllers, one boundary</h2>

<table>
<tr><th></th><th>Webster's</th><th>Max-pressure (Varaiya 2013)</th></tr>
<tr><td>Needs to observe</td><td>arrival flow on each approach</td>
    <td>approach queue <b>and</b> the queue on the link it discharges into</td></tr>
<tr><td>Cameras required</td><td>one per approach</td>
    <td>one per approach <b>plus one per receiving link</b></td></tr>
<tr><td>Surat CCTV provides</td><td>exactly this</td><td>no camera watches a receiving link</td></tr>
<tr><td>Runs on</td><td>the 7 Surat clips — every operational figure</td>
    <td>FLUID: 14 drone recordings of 3 real intersections</td></tr>
<tr><td>On Surat it reports</td><td>cycle, splits, delay, saturation</td>
    <td><code>greedy-queue</code> — and refuses to name a phase</td></tr>
</table>

<p>Dropping the downstream term does not yield a degraded max-pressure. It yields
<b>greedy queue control</b>, which Varaiya's §3.1 Example 2 proves fails to stabilise
demands that max-pressure handles — on a junction as simple as 2×2. So rather than
substitute zero and keep the better name, the implementation refuses: every movement
carries whether its receiving-link queue was actually observed, and the decision object
returns the greedy answer in separate fields so a caller cannot render it as
max-pressure.</p>

<p>To validate the controller for real, we obtained a dataset that contains the missing
term. FLUID is 14 drone recordings of three real signalised intersections, where the whole
junction fits in one aerial frame, so every approach <i>and</i> every receiving link is
visible at once.</p>

<h2>5. Results</h2>

<h3>5.1 Surat — the operational system</h3>

<p>The existing fixed timer runs a 120 s cycle with equal green splits, which drives the
busier approach to a degree of saturation of <b>{R['xb']}</b> — over capacity, where the
queue never clears within the cycle. Webster's optimal plan brings it to
<b>{R['xa']}</b>.</p>

<p>The saturation crossing is the claim that survives any delay model. Delay itself falls
3.8% (53.5 → 51.4 s/PCU under HCM control delay), and we quote that smaller figure rather
than the more flattering one available under other assumptions.</p>

<p>Two independent checks confirm the optimiser. Sweeping cycle length puts the empirical
delay minimum at <b>130 s</b> against Webster's closed form of <b>{R['cycle']} s</b>. And
the optimal plan equalises degree of saturation across approaches (0.929 / 0.929), which
is the known optimality condition for Webster green allocation.</p>

<table>
<tr><th>#</th><th>Site</th><th>Mode</th><th>Mean congestion</th><th>Saturated windows</th></tr>
<tr><td>1</td><td>Mahidharpura – Diamond Mkt</td><td>area</td><td class="n">0.939</td><td class="n">452 / 453</td></tr>
<tr><td>2</td><td>Gopi Talav Gate</td><td>area</td><td class="n">0.749</td><td class="n">167 / 456</td></tr>
<tr><td>3</td><td>Central Bus Depo</td><td>area</td><td class="n">0.483</td><td class="n">213 / 450</td></tr>
<tr><td>4</td><td>Rokadiya Hanuman</td><td>junction</td><td class="n">0.437</td><td class="n">0 / 244</td></tr>
<tr><td>5</td><td>Chauta Bazaar</td><td>area</td><td class="n">0.205</td><td class="n">4 / 453</td></tr>
<tr><td>6</td><td>Rly Station</td><td>area</td><td class="n">0.099</td><td class="n">45 / 302</td></tr>
<tr><td>7</td><td>GauravPath</td><td>junction</td><td class="n">0.073</td><td class="n">0 / 93</td></tr>
</table>

<p>Plus <b>{R['alerts']} evidence-backed bottleneck alerts</b> and <b>{R['fc']} zone
forecasts</b> with prediction intervals.</p>

<h3>5.2 FLUID — the max-pressure validation</h3>

<table>
<tr><th>Intersection</th><th>Cycle</th><th>Real signal: green with nothing to relieve</th><th>Max-pressure</th></tr>
<tr><td>FI (4-way, 3-phase)</td><td class="n">96 s</td><td class="n"><b>41.1%</b></td><td class="n">1.5%</td></tr>
<tr><td>FIDRT (dedicated right turns)</td><td class="n">123 s</td><td class="n"><b>66.3%</b></td><td class="n">2.3%</td></tr>
<tr><td>TI (T-intersection)</td><td class="n">71 s</td><td class="n"><b>46.6%</b></td><td class="n">16.0%</td></tr>
</table>

<p>These real fixed-time plans spend 41–66% of their green on a phase whose queue had
already cleared, or whose receiving link was fuller than the approach feeding it.</p>

<p>That is deliberately <b>not</b> a delay-reduction claim. It is an observation about the
recorded signals, made from the recorded queues — had max-pressure actually run, the
queues would have evolved differently. The counterfactual needs a model, so we built a
point-queue simulator over two junctions in series, with finite receiving-link storage so
spillback exists.</p>

<table>
<tr><th>Scenario</th><th>Fixed-time</th><th>Max-pressure</th><th>Spillback</th><th>Verdict</th></tr>
<tr><td>Steady, balanced</td><td class="n"><b>18.6 s/PCU</b></td><td class="n">20.8 s/PCU</td><td class="n">0 → 0 s</td><td>fixed-time wins by 12%</td></tr>
<tr><td>Demand swings between approaches</td><td class="n">24.5 s/PCU</td><td class="n"><b>18.5 s/PCU</b></td><td class="n">0 → 0 s</td><td>max-pressure −25%</td></tr>
<tr><td>Downstream blockage</td><td class="n">150.0 s/PCU</td><td class="n"><b>102.9 s/PCU</b></td><td class="n">289 → 88 s</td><td>max-pressure −31%</td></tr>
</table>

<div class="callout"><b>The operator's decision rule — narrower than the literature's
enthusiasm suggests.</b> Max-pressure earns its place when the <i>split</i> of demand moves
between approaches, and above all when something downstream backs up. On steady demand it
is the <b>worse</b> choice, because every extra switch pays an inter-green for a benefit
that is not there. A system offering it as a straight upgrade would be selling the wrong
thing. Ours measures which regime a junction is in and recommends accordingly.</div>

<h2>6. Innovation and unique value</h2>

<h3>6.1 The observability gate</h3>
<p>Max-pressure declines to name a phase when its defining term is unmeasurable, and
returns the weaker controller's answer under its own name. The refusal is structural — it
lives in the shape of the returned object, not in a warning string a caller can ignore.</p>

<h3>6.2 Occlusion-proof measurement</h3>
<p>The texture-based occupancy index keeps working exactly where object detection
collapses. This is the difference between a plausible ranking and a correct one.</p>

<h3>6.3 Measurement separated from estimation</h3>
<p>No camera here has a verified metric scale — three independent attempts to recover it
disagreed with each other by a factor of two to four. So the system separates what it
<b>measures</b> from what it <b>estimates</b>. Congestion index, level of service, site
ranking, occupancy and the entire signal-timing comparison are scale-free and
trustworthy. Anything in km/h or PCU/km/lane is displayed with a <code>~</code> prefix
behind a persistent banner. <b>Every headline result above is in the first
category.</b></p>

<h3>6.4 Documentation that cannot drift</h3>
<p>The verification suite recomputes every figure quoted in the README and engineering
report and fails if a number changes in the code but not the documentation, or the
reverse.</p>

<h2>7. Verification</h2>

<p><code>python tests/verify_system.py</code> runs 18 end-to-end checks. Beyond
recomputing the documented figures, it asserts the things most likely to break
silently:</p>

<ul>
<li><b>The separation</b> — the Surat plan is recomputed under both controller settings
and must come out byte-identical, and the string <code>maxpressure</code> must not appear
in any of the nine measurement and analysis modules.</li>
<li><b>The observability gate</b> — max-pressure must <i>refuse</i> on Surat and
<i>run</i> on FLUID.</li>
<li><b>Scale invariance</b> — multiplying every queue by 7.3 must leave the phase choice
unchanged, and scale the pressure by exactly 7.30.</li>
<li><b>Simulator fairness</b> — both controllers must be charged the same lost time, and
all three demand scenarios must genuinely differ.</li>
<li><b>Geometry</b> — the world-to-pixel fit on every FLUID site must land within 5 px.</li>
<li><b>Physical sanity</b> — no zero-length measurement window, and no speed, density or
occupancy outside its physical range.</li>
</ul>

<h2>8. Challenges faced</h2>

<p>Eleven bugs were found and fixed during development. Four are worth showing, because
three of the four produced numbers that looked entirely reasonable on screen.</p>

<table>
<tr><th>Symptom</th><th>Root cause</th><th>How it was caught</th></tr>
<tr><td>Speeds 5× too low — 4–8 km/h on a free-flowing arterial</td>
    <td>Isotropic pixel-to-metre scale; vertical and horizontal pixels treated as covering equal ground</td>
    <td>Physical implausibility of the reported speed</td></tr>
<tr><td>Flow 10× too high — 12,000–23,000 PCU/hr</td>
    <td>Counting new ByteTrack IDs as arrivals; IDs churn constantly in dense mixed traffic</td>
    <td>Compared against real saturation flow ≈1,800 PCU/hr</td></tr>
<tr><td>The "optimised" plan was worse than the baseline (−9.5%)</td>
    <td>Flow ratio computed on the critical lane but saturation on the whole approach — mixed units suppressed the overflow-delay term</td>
    <td>Swept cycle length and found the delay curve had no minimum, which is impossible if the implementation is correct</td></tr>
<tr><td>Max-pressure named a phase it had no data for</td>
    <td>A missing downstream queue silently treated as zero</td>
    <td>Audit against Varaiya §3.1 — the result was a different, provably weaker controller</td></tr>
</table>

<p>What caught these was checking outputs against physical limits and known theory, not
testing that the code ran.</p>

<h2>9. Honest limitations</h2>

<ul>
<li><b>The metric scale is not calibrated.</b> Converting pixels to metres needs one true
ground reference, and this dataset ships none. One measured road width promotes every
metric figure to primary, and the Calibration view does it in about two minutes without a
site visit.</li>
<li><b>Lane polygons are traced and heatmap-validated, not surveyed.</b></li>
<li><b>The two junction cameras face different corridors</b>, not opposing approaches of
one intersection. The combined signal plan therefore demonstrates the method on real
measured demand rather than being deployable at a specific junction — a configuration
change, not a code change.</li>
<li><b>Forecasting has minutes of history, not weeks.</b></li>
<li><b>FLUID is Chinese traffic.</b> It validates that the controller implementation is
correct on data containing the terms it needs. No claim from it transfers to Surat's
fleet, and the dashboard keeps the two datasets on tracks that cannot render each other's
data.</li>
</ul>

<h2>10. Future scope</h2>

<h3>Immediate — no new hardware</h3>
<ul>
<li><b>One surveyed road width</b> promotes every metric figure from estimate to primary.</li>
<li><b>Cameras on receiving links</b> unlock true max-pressure on Surat; the controller is
already implemented and validated.</li>
<li><b>Continuous RTSP ingest</b> replaces 31 minutes of clips with a live feed; the
pipeline is already windowed for it.</li>
</ul>

<h3>Longer term</h3>
<ul>
<li><b>Seasonal forecasting</b> once weeks of continuous operation justify it.</li>
<li><b>Coordinated corridors</b> — green-wave offsets across adjacent junctions; the
simulator already models two in series.</li>
<li><b>Production stack</b> — React + Kafka + PostGIS for city scale, with the measurement
core unchanged.</li>
</ul>

<h2>11. Technology and attribution</h2>

<table>
<tr><th>Layer</th><th>Choice</th><th>Licence / note</th></tr>
<tr><td>Detector weights</td><td>UVH-26-MV-YOLOv11-S</td><td>AGPL-3.0 weights, CC-BY-4.0 dataset. Used as published — we did not train it.</td></tr>
<tr><td>Detection / tracking</td><td>Ultralytics YOLO11 + ByteTrack</td><td>AGPL-3.0</td></tr>
<tr><td>Validation dataset</td><td>FLUID</td><td>CC BY 4.0, DOI 10.6084/m9.figshare.29974954</td></tr>
<tr><td>Density unit</td><td>PCU per IRC:106-1990</td><td>Standard implemented, not imported</td></tr>
<tr><td>Saturation flow</td><td>IRC:SP:41-1994, S = 525·W</td><td>Standard implemented</td></tr>
<tr><td>Pedestrian minimum green</td><td>IRC:103-2012</td><td>Standard implemented</td></tr>
<tr><td>Signal methods</td><td>Webster (1958), HCM control delay, Varaiya (2013)</td><td>Published methods implemented</td></tr>
<tr><td>Dashboard</td><td>Streamlit + Plotly</td><td>Apache-2.0 / MIT</td></tr>
</table>

<p>All 27 Python modules — roughly 13,700 lines — were written for this hackathon. The
project is released under AGPL-3.0, inherited from Ultralytics. Full detail, including
what is ours versus third-party, is in <code>ATTRIBUTION.md</code>.</p>

<h2>12. Running the system</h2>

<p>The metrics CSVs are committed, so the entire Webster track works immediately — no
video download, no GPU, no model weights:</p>

<p><code>pip install -r requirements.txt</code><br/>
<code>streamlit run src/dashboard.py</code></p>

<p>The full pipeline from raw video uses <code>requirements-full.txt</code> and adds
detection, tracking and the interactive calibration tool. Deployment guidance —
including what a hosted instance can and cannot show — is in
<code>DEPLOYMENT.md</code>; the measurement data contract is in
<code>DATA_SCHEMA.md</code>; and the complete engineering record, with every design
decision and every bug, is in <code>REPORT.md</code>.</p>
"""


def build():
    R = live()
    story = fitz.Story(html=html(R), user_css=CSS)
    writer = fitz.DocumentWriter(str(OUT))
    page = fitz.paper_rect("a4")
    frame = page + (58, 56, -58, -60)

    more, n = 1, 0
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(frame)
        story.draw(dev)
        writer.end_page()
        n += 1
    writer.close()

    # Page furniture, stamped in a second pass — Story has no page-level hooks.
    doc = fitz.open(str(OUT))
    for i, pg in enumerate(doc):
        if i:                                   # not on the title page
            pg.draw_line(fitz.Point(58, page.height - 44),
                         fitz.Point(page.width - 58, page.height - 44),
                         color=(0.88, 0.88, 0.85), width=0.6)
            pg.insert_text((58, page.height - 32),
                           "Surat Traffic Intelligence  ·  E-Rakshak Hackathon 2026  ·  PS-08",
                           fontname="helv", fontsize=7.5, color=(0.54, 0.53, 0.51))
        pg.insert_text((page.width - 70, page.height - 32), f"{i + 1} / {doc.page_count}",
                       fontname="helv", fontsize=7.5, color=(0.54, 0.53, 0.51))
    doc.saveIncr()
    print(f"\n  {n} pages -> {OUT}")
    return OUT


if __name__ == "__main__":
    build()
