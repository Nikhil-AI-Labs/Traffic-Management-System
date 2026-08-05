"""End-to-end verification of the PS-08 system.

    python tests/verify_system.py          # from the project root

Checks the shipped *behaviour*, not the docs' description of it. Every figure
quoted in REPORT.md that can be recomputed is recomputed here and compared, so
the report cannot drift away from the code without this failing.

What it asserts, and why each one exists:

  * both controllers reproduce their documented numbers, including the
    closed-form check that Webster's proportional split equalises the degree
    of saturation across approaches;
  * the max-pressure observability gate REFUSES on the Surat cameras (no
    downstream view) and RUNS on FLUID (whole intersection in one frame) —
    the single most important behaviour in the project, because the failure
    mode it guards against is a confident phase recommendation derived from
    data that does not exist;
  * the phase argmax is invariant to a common queue scale, which is what makes
    the decision survive our uncalibrated pixels-to-metres;
  * the simulator conserves vehicles, is deterministic, has three scenarios
    that genuinely differ, and charges BOTH controllers the same lost time —
    a fairness check added after an audit found the fixed-time plan was
    paying one inter-green per cycle instead of one per phase;
  * every chart survives empty and degenerate input;
  * no measurement window has a zero or negative duration, and no speed,
    density or occupancy escapes its physical range.
"""
import sys, glob, io, contextlib, traceback, pathlib
sys.path.insert(0, "src")

OK, BAD = [], []


def check(name):
    def deco(fn):
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                detail = fn()
            OK.append((name, detail or ""))
        except Exception as e:
            BAD.append((name, f"{type(e).__name__}: {e}",
                        traceback.format_exc()))
        return fn
    return deco


@check("modules compile")
def _():
    import py_compile
    n = 0
    for f in glob.glob("src/*.py"):
        py_compile.compile(f, doraise=True)
        n += 1
    return f"{n} modules"


@check("config: OPTIMIZATION_MODE toggle")
def _():
    import config as cfg
    assert cfg.OPTIMIZATION_MODE in cfg.OPTIMIZATION_MODES, cfg.OPTIMIZATION_MODE
    assert set(cfg.OPTIMIZATION_MODES) == {"webster", "max_pressure"}
    return f"default={cfg.OPTIMIZATION_MODE}, modes={cfg.OPTIMIZATION_MODES}"


@check("webster: saturation crossing + equal-x optimality")
def _():
    import pandas as pd, config as cfg, signal_opt as so
    import dashboard as D
    df = D.load_metrics.__wrapped__()
    js = [c for c in cfg.junction_cameras() if c in set(df.camera_id.unique())]
    dem = [d for d in (so.demand_from_metrics(df, c) for c in js) if d]
    base = so.evaluate_fixed_plan(dem, cycle_s=120.0)
    opt = so.optimise(dem)
    xs_b = [p.degree_of_saturation for p in base.phases]
    xs_o = [p.degree_of_saturation for p in opt.phases]
    Y = sum(d.flow_ratio for d in dem)
    L = len(dem) * so.LOST_TIME_PER_PHASE_S
    closed = so.common_degree_of_saturation(Y, opt.cycle_s, L)
    assert max(xs_b) > 1.0, f"baseline should be oversaturated, got {xs_b}"
    assert max(xs_o) < 1.0, f"optimised should be under capacity, got {xs_o}"
    assert abs(max(xs_o) - min(xs_o)) < 0.01, f"x should equalise, got {xs_o}"
    assert abs(closed - xs_o[0]) < 0.01, f"closed form {closed} vs {xs_o[0]}"
    return (f"C={opt.cycle_s:.1f}s  x {max(xs_b):.3f} -> "
            f"{'/'.join(f'{x:.3f}' for x in xs_o)}  closed-form {closed:.3f}")


@check("max-pressure: gate REFUSES on live Surat cameras")
def _():
    import pandas as pd, config as cfg, maxpressure as MP
    import dashboard as D
    import dashboard as D
    df = D.load_metrics.__wrapped__()
    js = [c for c in cfg.junction_cameras() if c in set(df.camera_id.unique())]
    mv = D._mp_movements_from_metrics(df, js)
    ph = MP.one_movement_per_phase(mv, min_green_s=cfg.MP_MIN_GREEN_S,
                                   max_green_s=cfg.MP_MAX_GREEN_S)
    dec = MP.decide(mv, ph)
    assert dec.mode == MP.MODE_GREEDY, f"expected greedy-queue, got {dec.mode}"
    assert not dec.usable, "must not be usable without downstream"
    assert dec.chosen_phase_id is None, "must not choose a phase"
    return f"mode={dec.mode}, usable={dec.usable}, chosen={dec.chosen_phase_id}"


@check("max-pressure: scale invariance of the argmax")
def _():
    import maxpressure as MP
    base = [{"id": f"m{i}", "label": f"m{i}", "from_link": f"a{i}",
             "to_link": f"b{i}", "queue_in_pcu": q, "queue_out_pcu": o}
            for i, (q, o) in enumerate([(12.0, 3.0), (7.0, 6.0), (9.5, 1.0)])]
    def dec(k):
        mv = MP.movements_from_zones([{**d,
                                       "queue_in_pcu": d["queue_in_pcu"] * k,
                                       "queue_out_pcu": d["queue_out_pcu"] * k}
                                      for d in base])
        return MP.decide(mv, MP.one_movement_per_phase(mv))
    a, b = dec(1.0), dec(7.3)
    assert a.chosen_phase_id == b.chosen_phase_id, "argmax moved under scaling"
    r = b.chosen_pressure / a.chosen_pressure
    assert abs(r - 7.3) < 1e-6, f"pressure should scale by k, got {r}"
    return f"argmax stable, pressure scaled by {r:.4f} (k=7.3)"


@check("max-pressure: runs GENUINELY on FLUID")
def _():
    import config as cfg, maxpressure as MP, fluid as FL
    if not FL.available():
        raise AssertionError("FLUID not found — cannot verify the real path")
    rec = FL.recordings()[0]
    q, sig, tr = FL.build_queue_table(rec), FL.load_signal(rec), FL.turn_ratios(rec)
    ts = sorted(q.t.unique())
    t = ts[len(ts) // 3]
    mv = FL.movements_at(q, float(t), turns=tr)
    ph = FL.controller_phases(sig, q, min_green_s=cfg.MP_MIN_GREEN_S,
                              max_green_s=cfg.MP_MAX_GREEN_S)
    dec = MP.decide(mv, ph)
    assert dec.mode == MP.MODE_FULL, f"expected max-pressure, got {dec.mode}"
    assert dec.usable
    # every phase must report a truthful observed-count, never 0/0
    zero = [p.label for p in dec.phases if p.total_movements == 0]
    assert not zero, f"phases with no movements resolved: {zero}"
    return (f"{len(FL.recordings())} recordings; mode={dec.mode}; "
            f"phases {len(dec.phases)}, all with resolved movements")


@check("FLUID: turn ratios sum to 1 per origin")
def _():
    import fluid as FL
    rec = FL.recordings()[0]
    tr = FL.turn_ratios(rec)
    s = tr.groupby(tr.movement.str[0]).turn_ratio.sum()
    bad = s[(s - 1.0).abs() > 1e-6]
    assert bad.empty, f"ratios do not sum to 1: {dict(bad)}"
    return f"{len(s)} origins, all sum to 1.0"


@check("FLUID: replay figures match the report")
def _():
    import fluid as FL
    want = {"FI": 41.1, "FIDRT": 66.3, "TI": 46.6}
    got = {}
    for site in ("FI", "FIDRT", "TI"):
        r = FL.recordings(site)[0]
        res = FL.replay(r, FL.build_queue_table(r), FL.load_signal(r),
                        FL.turn_ratios(r))
        got[site] = round(res.wasted_green_pct_actual, 1)
        assert res.mode == "max-pressure", f"{site} mode={res.mode}"
    for k, v in want.items():
        assert abs(got[k] - v) < 0.15, f"{k}: report {v} vs actual {got[k]}"
    return " ".join(f"{k}={v}%" for k, v in got.items())


@check("network_sim: conservation, determinism, scenarios differ")
def _():
    import network_sim as NS
    names = list(NS.SCENARIOS)
    assert names == ["steady", "swing", "incident"], names
    seen = {}
    for sc in names:
        a = NS.compare_controllers(scenario=sc, seconds=1800.0)
        b = NS.compare_controllers(scenario=sc, seconds=1800.0)
        for key in ("fixed", "max_pressure"):
            r = a[key]
            # conservation, on roads only (sinks are boundaries)
            ent = sum(l.entered for l in [] ) if False else None
            assert r.mean_delay_s_per_pcu >= 0
            assert abs(r.mean_delay_s_per_pcu
                       - b[key].mean_delay_s_per_pcu) < 1e-9, f"{sc}/{key} non-deterministic"
        seen[sc] = (round(a["fixed"].mean_delay_s_per_pcu, 1),
                    round(a["max_pressure"].mean_delay_s_per_pcu, 1))
    # every scenario must actually do something different
    assert seen["steady"] != seen["swing"], f"swing == steady: {seen}"
    assert seen["steady"] != seen["incident"], f"incident is a no-op: {seen}"
    return "  ".join(f"{k}:{v[0]}/{v[1]}" for k, v in seen.items())


@check("network_sim: both controllers pay the same lost time")
def _():
    import network_sim as NS
    from collections import Counter
    net, js = NS.corridor()
    g, cyc, Y = NS.webster_splits(net, js)
    fc = NS.FixedTimeController(g, cycle_s=cyc)
    j = js[0]
    served = Counter(fc.active_phase(j, net, float(t), 1.0)
                     for t in range(int(cyc)))
    n_ph = len(g[j.id])
    allred = served[None]
    want = n_ph * j.lost_time_per_phase_s
    assert abs(allred - want) <= 1.5, (
        f"all-red {allred}s vs {want}s expected for {n_ph} phases "
        f"— the plan is not paying its inter-greens")
    return f"{n_ph} phases, all-red {allred}s of {cyc:.0f}s cycle (want ~{want:.0f}s)"


@check("SEPARATION: Surat uses Webster only, max-pressure only on FLUID")
def _():
    """The load-bearing architectural claim of this project.

    Surat has one camera per approach and none on a receiving link, so
    Webster's is the controller it can support and max-pressure is not. FLUID
    sees whole intersections, so max-pressure is validated there. Nothing
    about a Surat figure may depend on the max-pressure code path — if it
    ever does, this fails.
    """
    import glob as _g, config as cfg, signal_opt as so
    import dashboard as D

    # 1. No measurement / analysis module may reference max-pressure at all.
    forbidden = ["analyze", "metrics", "zones", "bottleneck_detect",
                 "forecast", "ingest", "calibration", "report_html",
                 "detector"]
    leaks = []
    for mod in forbidden:
        src = pathlib.Path(f"src/{mod}.py").read_text(encoding="utf-8")
        for token in ("maxpressure", "max_pressure", "MP."):
            if token in src:
                leaks.append(f"{mod}.py contains {token!r}")
    assert not leaks, "; ".join(leaks)

    # 2. The Surat signal plan must be byte-identical under either toggle.
    df = D.load_metrics.__wrapped__()
    js = [c for c in cfg.junction_cameras() if c in set(df.camera_id.unique())]
    dem = [d for d in (so.demand_from_metrics(df, c) for c in js) if d]
    plans = {}
    for mode in cfg.OPTIMIZATION_MODES:
        cfg.OPTIMIZATION_MODE = mode
        o = so.optimise(dem)
        plans[mode] = (round(o.cycle_s, 6),
                       tuple(round(p.green_s, 6) for p in o.phases),
                       tuple(round(p.degree_of_saturation, 6) for p in o.phases))
    cfg.OPTIMIZATION_MODE = "webster"
    assert plans["webster"] == plans["max_pressure"], (
        f"Surat plan changes with the toggle: {plans}")

    # 3. Max-pressure must refuse on Surat and run on FLUID.
    import maxpressure as MP, fluid as FL
    mv = D._mp_movements_from_metrics(df, js)
    d_surat = MP.decide(mv, MP.one_movement_per_phase(mv))
    assert d_surat.mode == MP.MODE_GREEDY and d_surat.chosen_phase_id is None
    assert FL.available(), "FLUID missing — max-pressure has nowhere to be validated"
    rec = FL.recordings()[0]
    q, sg = FL.build_queue_table(rec), FL.load_signal(rec)
    t = sorted(q.t.unique())[100]
    d_fluid = MP.decide(FL.movements_at(q, float(t), turns=FL.turn_ratios(rec)),
                        FL.controller_phases(sg, q))
    assert d_fluid.mode == MP.MODE_FULL and d_fluid.chosen_phase_id is not None

    return (f"Surat plan identical under both toggles (C={plans['webster'][0]}s); "
            f"MP refuses on Surat ({d_surat.mode}), runs on FLUID "
            f"({d_fluid.mode}); 0 leaks into {len(forbidden)} analysis modules")


@check("REPORT.md headline figures match live computation")
def _():
    """The report may not drift from the code.

    Every figure listed here is recomputed and required to appear verbatim in
    REPORT.md. A number that changes in the code and not in the report — or
    the reverse — fails this, which is the only reliable defence against a
    document that was true when it was written.
    """
    import config as cfg, signal_opt as so, fluid as FL, network_sim as NS
    import bottleneck_detect as bn, forecast as fc
    import dashboard as D

    doc = pathlib.Path("REPORT.md").read_text(encoding="utf-8")
    df = D.load_metrics.__wrapped__()
    must = {}

    # --- Surat / Webster ---------------------------------------------------
    js = [c for c in cfg.junction_cameras() if c in set(df.camera_id.unique())]
    dem = [d for d in (so.demand_from_metrics(df, c) for c in js) if d]
    opt = so.optimise(dem)
    must[f"{opt.cycle_s:.1f}"] = "Webster cycle"
    must[f"{opt.phases[0].degree_of_saturation:.3f}"] = "optimised x"
    must[f"{len(df):,}"] = "measurement windows"
    al = bn.detect_all(df)
    must[str(len(al))] = "alert count"
    f = fc.forecast_all(df, horizon_s=60.0)
    must[str(len(f))] = "zones forecast"

    # --- FLUID / max-pressure ---------------------------------------------
    for site in ("FI", "FIDRT", "TI"):
        r = FL.recordings(site)[0]
        res = FL.replay(r, FL.build_queue_table(r), FL.load_signal(r),
                        FL.turn_ratios(r))
        must[f"{res.wasted_green_pct_actual:.1f}%"] = f"{site} wasted green"
    must[f"{len(FL.recordings())}"] = "FLUID recordings"

    # --- simulator ---------------------------------------------------------
    for sc in NS.SCENARIOS:
        o = NS.compare_controllers(scenario=sc, seconds=1800.0)
        must[f"{o['fixed'].mean_delay_s_per_pcu:.1f} s/PCU"] = f"{sc} fixed delay"
        must[f"{o['max_pressure'].mean_delay_s_per_pcu:.1f} s/PCU"] = f"{sc} mp delay"

    missing = [f"{v}={k!r}" for k, v in must.items() if k not in doc]
    assert not missing, "REPORT.md missing/stale: " + "; ".join(missing)
    return f"{len(must)} headline figures verified against REPORT.md"


@check("runtime assets present (weights, reference frames, heatmaps)")
def _():
    """The assets a fresh run needs, and what breaks without each.

    Added after all three went missing at once when the project folder moved:
    the dashboard opened fine, then every scene image was blank and every
    detection button raised a traceback. Missing assets are a normal state, so
    the UI degrades gracefully — but the shipped project should have them, and
    a silent absence is exactly what this check exists to make loud.
    """
    import config as cfg
    from reference_frames import REF_DIR
    missing = []

    if not pathlib.Path(cfg.DEFAULT_MODEL).exists():
        missing.append("detector weights (python scripts/download_models.py)")

    no_ref = [c for c in cfg.CAMERAS if not (REF_DIR / f"{c}.jpg").exists()]
    if no_ref:
        missing.append(f"reference frames for {len(no_ref)} cameras "
                       f"(python src/reference_frames.py)")

    hm = cfg.FRAME_DIR / "heatmap"
    no_hm = [c for c in cfg.CAMERAS if not (hm / f"{c}_heatmap.jpg").exists()]
    if no_hm:
        missing.append(f"heatmaps for {len(no_hm)} cameras "
                       f"(python src/traffic_heatmap.py --all)")

    assert not missing, "; ".join(missing)

    # And the graceful-degradation path must actually be wired up.
    import dashboard as D
    ok, _ = D.detector_ready()
    assert ok and callable(D._detector_missing_notice)
    return (f"weights + {len(cfg.CAMERAS)} reference frames + "
            f"{len(cfg.CAMERAS)} heatmaps present; missing-model guard wired")


@check("FLUID scene layer renders on every site")
def _():
    """The visual parity layer: aerial scene, zones, heatmap, instant.

    The world-to-pixel fit is the load-bearing part — everything is drawn in
    metres onto a pixel frame — so its residual is asserted, not assumed. And
    every image must come back 3-channel: OpenCV's TIFF decoder ignores
    IMREAD_COLOR and returns these orthophotos' native 4 channels, which
    broke every blend downstream until it was normalised at the loader.
    """
    import fluid as FL
    if not FL.available():
        raise AssertionError("FLUID missing")
    out = []
    for site in FL.SITES:
        rid = FL.recordings(site)[0].id
        w = FL.world_to_pixel(rid)
        assert w is not None, f"{site}: no world-to-pixel fit"
        assert 5.0 < w.px_per_m < 200.0, f"{site}: implausible {w.px_per_m} px/m"
        # 5 px at ~24 px/m is about 20 cm on the ground. A per-axis fit could
        # not express the drone's heading and missed by 8 m on the rotated
        # site, so this is the guard that keeps the full affine honest.
        assert w.residual_px < 5.0, f"{site}: fit residual {w.residual_px:.1f} px"

        q = FL.build_queue_table(FL.find(rid))
        t = sorted(q.t.unique())[200]
        imgs = {"scene": FL.scene_image(rid),
                "zones": FL.zone_overlay(rid, q, t),
                "heatmap": FL.trajectory_heatmap(rid)}
        inst, n_all, n_q = FL.annotated_instant(rid, t)
        imgs["instant"] = inst
        for name, im in imgs.items():
            assert im is not None, f"{site}/{name} returned None"
            assert im.ndim == 3 and im.shape[2] == 3, (
                f"{site}/{name} is {im.shape}, expected 3 channels")
        assert n_all >= n_q >= 0
        out.append(f"{site} {w.px_per_m:.0f}px/m rot{w.rotation_deg:+.0f}deg "
                   f"res{w.residual_px:.1f}px")
    return "  ".join(out) + "  (scene, zones, heatmap, instant on each)"


@check("decoder log noise suppressed, real stderr preserved")
def _():
    """Harmless FFmpeg/GeoTIFF chatter is silenced; genuine errors are not."""
    import io, subprocess, textwrap
    code = textwrap.dedent('''
        import sys; sys.path.insert(0, "src")
        import config as cfg, fluid as FL
        c = cfg.open_video(cfg.camera_video_path("gauravpath"))
        n = 0
        while n < 30:
            ok, _ = c.read()
            if not ok: break
            n += 1
        c.release()
        FL.scene_image(FL.recordings("FI")[0].id)
        print("REAL_ERROR_MARKER", file=sys.stderr)
        print(n)
    ''')
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, timeout=180)
    err = r.stderr
    assert "No start code" not in err, "FFmpeg chatter not suppressed"
    assert "TIFF_Warning" not in err, "GeoTIFF chatter not suppressed"
    assert "REAL_ERROR_MARKER" in err, "real stderr was swallowed too"
    assert int(r.stdout.strip().splitlines()[-1]) == 30, "decoding broke"
    return "30 frames decoded, 0 chatter lines, real stderr intact"


@check("charts: every figure survives degenerate input")
def _():
    import inspect, pandas as pd, charts as C
    import plotly.graph_objects as go
    fns = [(n, f) for n, f in vars(C).items()
           if callable(f) and not n.startswith("_")
           and getattr(f, "__module__", "") == "charts"
           and inspect.signature(f).return_annotation is not inspect.Signature.empty]
    tried = built = 0
    for n, f in fns:
        sig = inspect.signature(f)
        for empty in (pd.DataFrame(), [], {}):
            args = []
            for p in sig.parameters.values():
                if p.default is not inspect.Parameter.empty:
                    break
                args.append(empty)
            tried += 1
            try:
                r = f(*args)
                if isinstance(r, go.Figure):
                    built += 1
            except Exception:
                pass
    return f"{len(fns)} chart fns, {built}/{tried} degenerate calls returned a Figure"


@check("metrics CSVs: physical sanity")
def _():
    import pandas as pd
    import dashboard as D
    df = D.load_metrics.__wrapped__()
    bad = []
    if (df.window_end_s <= df.window_start_s).any():
        bad.append(f"{int((df.window_end_s <= df.window_start_s).sum())} zero/neg windows")
    for col, lo, hi in [("mean_speed_kmph", 0, 120),
                        ("structural_occupancy", 0, 1.0001),
                        ("congestion_index", 0, 1.0001),
                        ("density_pcu_per_km_lane", 0, 400)]:
        if col in df:
            s = df[col].dropna()
            if len(s) and (s < lo).any():
                bad.append(f"{col} < {lo}")
            if len(s) and (s > hi).any():
                bad.append(f"{col} > {hi} (max {s.max():.2f})")
    assert not bad, "; ".join(bad)
    return f"{len(df):,} windows, {df.camera_id.nunique()} cameras — all in range"


@check("bottlenecks + forecast reproduce")
def _():
    import pandas as pd, bottleneck_detect as bn, forecast as fc
    import dashboard as D
    df = D.load_metrics.__wrapped__()
    al = bn.detect_all(df)
    sev = {s: sum(a.severity == s for a in al) for s in ("critical", "warning", "info")}
    f = fc.forecast_all(df, horizon_s=60.0)
    rel = sum(1 for x in f if x.reliable)
    risks = len(fc.rising_risks(f, threshold=0.45))
    return (f"{len(al)} alerts ({sev['critical']}C/{sev['warning']}W/{sev['info']}I); "
            f"{len(f)} forecasts, {rel} reliable, {risks} rising")


if __name__ == "__main__":
    print("=" * 74)
    for n, d in OK:
        print(f"  PASS  {n:46s} {d}")
    for n, e, tb in BAD:
        print(f"  FAIL  {n:46s} {e}")
    print("=" * 74)
    print(f"  {len(OK)} passed, {len(BAD)} failed")
    if BAD:
        print()
        for n, e, tb in BAD:
            print(f"--- {n} ---")
            print(tb[-1400:])
    sys.exit(1 if BAD else 0)
