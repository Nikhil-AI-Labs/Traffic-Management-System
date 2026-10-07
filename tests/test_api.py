"""End-to-end checks of the JSON API behind the React dashboard.

    python -m pytest tests/test_api.py -q        # from the project root

Runs in-process through FastAPI's TestClient - no server needed. Every view's
endpoint is called the way the front end calls it, and the payload is checked
for the things the UI depends on: the headline figures (which must agree with
REPORT.md, exactly as verify_system.py demands of the Python API), strict JSON
(no NaN or infinity anywhere), and charts that arrive as Plotly figures in
both themes.

FLUID-dependent tests skip themselves when the dataset is not on the machine.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

import api  # noqa: E402
import fluid as FL  # noqa: E402

client = TestClient(api.app)
needs_fluid = pytest.mark.skipif(not FL.available(), reason="FLUID not present")


def get(url: str, status: int = 200) -> dict:
    r = client.get(url)
    assert r.status_code == status, f"{url} -> {r.status_code}: {r.text[:300]}"
    ctype = r.headers.get("content-type", "")
    return r.json() if ctype.startswith("application/json") else {}


def assert_strict_json(obj, path="$"):
    """No NaN/inf survives - browsers' JSON.parse rejects them outright."""
    if isinstance(obj, float):
        assert math.isfinite(obj), f"non-finite number at {path}"
    elif isinstance(obj, dict):
        for k, v in obj.items():
            assert_strict_json(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            assert_strict_json(v, f"{path}[{i}]")


def assert_figure(fig):
    assert isinstance(fig, dict) and "data" in fig and "layout" in fig


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------

def test_meta():
    m = get("/api/meta")
    assert_strict_json(m)
    ids = {c["id"] for c in m["cameras"]}
    assert {"gauravpath", "rokadiya_hanuman", "mahidharpura"} <= ids
    assert m["metrics"]["windows"] >= 2451
    assert m["mode"] in ("webster", "max_pressure")
    assert m["recommendation"]["recommended"] == "webster"


def test_unknown_api_route_is_json_404():
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404 and "detail" in r.json()


# --------------------------------------------------------------------------
# Webster track
# --------------------------------------------------------------------------

@pytest.mark.parametrize("theme", ["light", "dark"])
def test_network(theme):
    n = get(f"/api/network?theme={theme}")
    assert_strict_json(n)
    assert n["kpi"]["sites"] >= 7
    # The headline ranking from the README: Mahidharpura is the worst site.
    assert n["sites"][0]["id"] == "mahidharpura"
    assert abs(n["sites"][0]["mean_congestion"] - 0.939) < 1e-6
    for c in n["charts"].values():
        assert_figure(c)


def test_theme_changes_chart_colours():
    light = get("/api/network?theme=light")["charts"]["ranking"]
    dark = get("/api/network?theme=dark")["charts"]["ranking"]
    assert light["layout"]["font"]["color"] != dark["layout"]["font"]["color"]


def test_bad_theme_rejected():
    get("/api/network?theme=purple", status=422)


@pytest.mark.parametrize("cam", ["gauravpath", "rokadiya_hanuman", "chauta_bazaar",
                                 "mahidharpura", "gopi_talav", "rly_station",
                                 "central_bus_depo"])
def test_site(cam):
    s = get(f"/api/sites/{cam}")
    assert_strict_json(s)
    assert s["zones"] and s["camera"]["id"] == cam
    assert ("fundamental" in s["charts"]) == (s["camera"]["mode"] == "junction")
    for m in s["trend_metrics"]:
        t = get(f"/api/sites/{cam}/trend?metric={m['id']}")
        assert_strict_json(t)
        assert_figure(t["chart"])
        assert t["rows"]


def test_site_unknown_camera():
    get("/api/sites/nowhere", status=404)
    get("/api/sites/gauravpath/trend?metric=bogus", status=422)


def test_signal_matches_report():
    s = get("/api/signal")
    assert_strict_json(s)
    # README / REPORT: 120 s fixed timer at x 1.01 -> Webster 135.6 s at 0.929.
    assert s["opt"]["cycle_s"] == 135.6
    assert s["worst_before"] > 1.0
    assert abs(s["worst_after"] - 0.929) < 1e-3
    assert s["over_before"] == 1
    # The self-audit: under Webster's own delay model the swept minimum sits
    # at 130 s beside the closed form's 135.6 s (REPORT §"two checks"). Under
    # the default auto/HCM model it is shorter, and must be reported as such.
    assert s["best_cycle_webster"] == 130
    assert s["delay_model"] == "auto" and s["best_cycle"] == 90
    for c in s["charts"].values():
        assert_figure(c)


def test_signal_webster_model_diverges_honestly():
    """Webster's delay is infinite above capacity: null, flagged, never NaN."""
    s = get("/api/signal?delay_model=webster")
    assert_strict_json(s)
    row = next(r for r in s["delay_models"] if r["model"] == "webster")
    assert row["diverges"] is True and row["reduction_pct"] is None


def test_signal_widths_and_validation():
    s = get('/api/signal?sat_source=irc&widths={"gauravpath":10.5}')
    assert_strict_json(s)
    assert s["factor"] == 525.0
    get("/api/signal?sat_source=nonsense", status=422)
    get('/api/signal?sat_source=irc&widths={"gauravpath":99}', status=422)
    get("/api/signal?sat_source=irc&widths=notjson", status=422)
    get("/api/signal?baseline_cycle=10", status=422)


def test_bottlenecks_match_report():
    b = get("/api/bottlenecks")
    assert_strict_json(b)
    assert len(b["alerts"]) == 31
    assert b["counts"] == {"critical": 10, "warning": 20, "info": 1}


def test_outlook():
    o = get("/api/outlook?horizon=60")
    assert_strict_json(o)
    assert len(o["forecasts"]) == 21
    get("/api/outlook?horizon=5", status=422)


def test_method():
    m = get("/api/method")
    assert_strict_json(m)
    assert m["worst"]["id"] == "mahidharpura"
    assert_figure(m["charts"]["occlusion"])


@pytest.mark.parametrize("kind", ["overlay", "reference", "heatmap"])
def test_images(kind):
    r = client.get(f"/api/images/{kind}/gauravpath?w=400")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"


def test_missing_media_is_404():
    assert client.get("/api/images/overlay/nowhere").status_code == 404


# --------------------------------------------------------------------------
# Calibration (read-only; applying would re-measure a camera)
# --------------------------------------------------------------------------

def test_calibration_overview_and_fit():
    o = get("/api/calibration")
    assert_strict_json(o)
    assert any(c["id"] == "gauravpath" for c in o["cameras"])
    c = get("/api/calibration/gauravpath")
    assert c["rows"], "suggested measurement rows expected"
    y = c["rows"][-1]["row"]
    r = client.post("/api/calibration/gauravpath/fit",
                    json={"measurements": [[y, 7.0]], "camera_height_m": 7,
                          "hfov_deg": 60})
    assert r.status_code == 200, r.text
    assert "ok" in r.json()
    bad = client.post("/api/calibration/gauravpath/fit", json={"measurements": []})
    assert bad.status_code == 422
    g = client.get("/api/images/guide/gauravpath?w=400")
    assert g.status_code == 200 and g.content[:2] == b"\xff\xd8"


# --------------------------------------------------------------------------
# Uploads (validation only; analysis needs the detector and minutes of CPU)
# --------------------------------------------------------------------------

def test_upload_rejects_bad_extension():
    r = client.post("/api/uploads",
                    files={"file": ("evil.mp4:stream", b"x", "video/mp4")})
    assert r.status_code == 415
    r = client.post("/api/uploads", files={"file": ("notes.txt", b"x", "text/plain")})
    assert r.status_code == 415


def test_upload_undecodable_image_rejected():
    r = client.post("/api/uploads",
                    files={"file": ("broken.jpg", b"not a jpeg", "image/jpeg")})
    assert r.status_code == 422


def test_upload_unknown_id():
    get("/api/uploads/nope", status=404)
    get("/api/jobs/nope", status=404)


# --------------------------------------------------------------------------
# Max-pressure track
# --------------------------------------------------------------------------

@needs_fluid
def test_fluid_summary_matches_report():
    s = get("/api/fluid/summary")
    assert_strict_json(s)
    got = {r["site"]: round(r["wasted_actual"], 1) for r in s["rows"]}
    assert got == {"FI": 41.1, "FIDRT": 66.3, "TI": 46.6}
    assert s["genuine"] is True


@needs_fluid
def test_fluid_replay_instant_scene():
    rid = FL.recordings("TI")[0].id
    r = get(f"/api/fluid/replay?rid={rid}&min_green=15&downstream=queue")
    assert_strict_json(r)
    assert r["result"]["mode"] == "max-pressure"
    for c in r["charts"].values():
        assert_figure(c)

    i = get(f"/api/fluid/instant?rid={rid}")
    assert_strict_json(i)
    assert i["decision"]["mode"] == "max-pressure"
    assert i["observed"] == i["movements_total"]

    sc = get(f"/api/fluid/scene?rid={rid}")
    assert_strict_json(sc)
    img = client.get(f"/api/fluid/image/instant?rid={rid}&t={i['t']}&w=600")
    assert img.status_code == 200 and int(img.headers["X-Vehicles"]) >= 0
    for kind in ("zones", "heatmap"):
        assert client.get(f"/api/fluid/image/{kind}?rid={rid}&w=600").status_code == 200

    csv = client.get(f"/api/fluid/replay.csv?rid={rid}")
    assert csv.status_code == 200 and csv.text.startswith("t,")


@needs_fluid
def test_fluid_unknown_recording():
    get("/api/fluid/replay?rid=XX/none", status=404)


def test_mp_method_refuses_on_surat():
    m = get("/api/mp/method")
    assert m["mode"] == "greedy-queue"
    assert m["usable"] is False and m["chosen_phase_id"] is None
    assert m["observed"] == 0


def test_simulator():
    sc = get("/api/sim/scenarios")
    assert [s["id"] for s in sc["scenarios"]] == ["steady", "swing", "incident"]
    r = client.post("/api/sim/run", json={"scenario": "steady", "main": 900,
                                          "cross": 385})
    assert r.status_code == 200, r.text
    body = r.json()
    assert_strict_json(body)
    assert body["fixed"]["delay"] > 0
    bad = client.post("/api/sim/run", json={"scenario": "steady", "main": 5})
    assert bad.status_code == 422


@pytest.mark.parametrize("scenario,fixed,free", [("steady", 18.6, 20.8),
                                                 ("swing", 24.5, 18.5),
                                                 ("incident", 150.0, 102.9)])
def test_simulator_defaults_reproduce_report(scenario, fixed, free):
    """An untouched Run on Head-to-head must show REPORT.md's table."""
    r = client.post("/api/sim/run", json={"scenario": scenario}).json()
    assert round(r["fixed"]["delay"], 1) == fixed
    assert round(r["free"]["delay"], 1) == free


def test_mode_setting_round_trip():
    before = get("/api/meta")["mode"]
    try:
        r = client.post("/api/settings/mode", json={"mode": "max_pressure"})
        assert r.status_code == 200
        assert get("/api/meta")["mode"] == "max_pressure"
        bad = client.post("/api/settings/mode", json={"mode": "x"})
        assert bad.status_code == 422
    finally:
        client.post("/api/settings/mode", json={"mode": before})
