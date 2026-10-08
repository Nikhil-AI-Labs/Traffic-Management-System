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
    if kind == "heatmap" and not (api.S.HEATMAP_DIR / "gauravpath_heatmap.jpg").exists():
        pytest.skip("heatmaps are generated, not committed")
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


# --------------------------------------------------------------------------
# Operator sign-in and rate limits
# --------------------------------------------------------------------------

import incidents as INC  # noqa: E402
import security as SEC  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_buckets():
    """Rate limits are per client, and every test is the same client."""
    SEC.BUCKETS.reset()
    yield
    SEC.BUCKETS.reset()


@pytest.fixture
def incident_log(tmp_path, monkeypatch):
    """Point the incident log at a scratch file for the test."""
    monkeypatch.setattr(INC, "LOG_PATH", tmp_path / "incidents.json")
    return INC.LOG_PATH


def test_meta_reports_auth_and_incidents(incident_log):
    m = get("/api/meta")
    assert m["auth"] == {"required": False}
    assert m["incidents"]["open"] == 31 and m["incidents"]["open_critical"] == 10


def test_writes_need_a_token_when_one_is_set(monkeypatch):
    monkeypatch.setattr(SEC, "OPERATOR_TOKEN", "s3cret")
    assert get("/api/meta")["auth"]["required"] is True
    get("/api/network")                                   # reading stays open
    for method, url, body in [("post", "/api/live/control", {"speed": 1.0}),
                              ("post", "/api/uploads/demo", {"camera": "gauravpath"}),
                              ("put", "/api/locations/gauravpath", {"lat": 21.16, "lon": 72.78}),
                              ("delete", "/api/live/detect", None)]:
        kw = {"json": body} if body is not None else {}
        r = getattr(client, method)(url, **kw)
        assert r.status_code == 401, f"{method} {url} -> {r.status_code}"
    good = client.post("/api/auth/check", headers={"Authorization": "Bearer s3cret",
                                                   "X-Operator-Name": "Insp.%20Desai"})
    assert good.status_code == 200 and good.json()["name"] == "Insp. Desai"
    assert client.post("/api/auth/check",
                       headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.post("/api/live/control", json={"speed": 1.0},
                    headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_rate_limit_answers_429(monkeypatch):
    monkeypatch.setattr(SEC, "RATE_LIMITS_ON", True)
    codes = [client.post("/api/auth/check").status_code for _ in range(8)]
    assert codes[:5] == [200] * 5 and set(codes[5:]) == {429}
    r = client.post("/api/auth/check")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


# --------------------------------------------------------------------------
# Incident workflow
# --------------------------------------------------------------------------

def test_alerts_carry_incident_ids(incident_log):
    b = get("/api/bottlenecks")
    assert all(a["id"] and a["status"] == "open" and a["actionable"] for a in b["alerts"])
    s = get("/api/sites/mahidharpura")
    assert all("|" in a["id"] for a in s["alerts"])


def test_incident_lifecycle(incident_log):
    items = get("/api/incidents")["items"]
    assert len(items) == 31 and items[0]["severity"] == "critical"
    iid = items[0]["id"]

    def act(action, note=""):
        return client.post(f"/api/incidents/{iid}/action", json={"action": action, "note": note},
                           headers={"X-Operator-Name": "Insp.%20Desai"})

    r = act("acknowledge", "unit dispatched")
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    assert act("acknowledge").status_code == 409          # not from "acknowledged"
    assert act("resolve", "cleared").json()["status"] == "resolved"
    assert act("reopen").json()["status"] == "open"
    assert act("explode").status_code == 409
    hist = next(i for i in get("/api/incidents?status=all")["items"] if i["id"] == iid)["history"]
    assert [h["action"] for h in hist] == ["acknowledge", "resolve", "reopen"]
    assert hist[0]["by"] == "Insp. Desai" and hist[0]["note"] == "unit dispatched"
    # Persisted, and the status shows on the alert views and the badge.
    assert incident_log.exists()
    act("resolve")
    b = get("/api/bottlenecks")
    assert next(a for a in b["alerts"] if a["id"] == iid)["status"] == "resolved"
    assert get("/api/meta")["incidents"]["resolved"] == 1
    csv = client.get("/api/incidents.csv")
    assert csv.status_code == 200 and csv.text.startswith("id,source,status")
    assert client.post("/api/incidents/nope/action", json={"action": "resolve"}).status_code == 404


def test_live_alert_that_clears_is_not_left_open(incident_log):
    """A replay left running re-raises the same conditions every lap; if a
    cleared live alert stayed "open", the open count would grow forever."""
    from urllib.parse import quote
    hub = api.LIVE
    aid = hub.raise_alert({"camera_id": "gauravpath", "camera_name": "GauravPath",
                           "zone_id": "lane_1", "zone_label": "Lane 1",
                           "kind": "standing_queue", "severity": "critical",
                           "start_s": 1.0, "end_s": 9.0, "duration_s": 8.0,
                           "headline": "test", "evidence": "test",
                           "recommendation": "test"}, "detect")
    try:
        def status():
            items = get("/api/incidents?status=all&source=live")["items"]
            return next(i for i in items if i["id"] == aid)["status"]
        assert status() == "open"
        hub.clear_alert(aid)
        assert status() == "cleared"
        assert aid not in {i["id"] for i in get("/api/incidents?status=open")["items"]}
        # It can still be put on record after the fact.
        r = client.post(f"/api/incidents/{quote(aid, safe='')}/action",
                        json={"action": "resolve", "note": "checked on camera"})
        assert r.status_code == 200 and r.json()["status"] == "resolved"
        assert status() == "resolved"
    finally:
        hub.alerts.pop(aid, None)
        hub.active.pop(aid, None)


def test_incident_filters(incident_log):
    assert get("/api/incidents?status=resolved")["items"] == []
    assert len(get("/api/incidents?source=recorded")["items"]) == 31
    get("/api/incidents?status=bogus", status=422)


# --------------------------------------------------------------------------
# What-if, map, briefing
# --------------------------------------------------------------------------

def test_whatif_headroom():
    w = get("/api/signal/whatif")
    assert_strict_json(w)
    h = w["today"]["headroom"]
    # Y from the uncapped flow ratios; Webster's practical and ultimate limits.
    assert abs(h["Y"] - 0.875) < 0.002
    assert abs(h["reserve_ultimate_pct"] - 8.24) < 0.05
    assert h["reserve_practical_pct"] < 0                  # already past x = 0.90
    assert abs(h["years_to_failure"] - 1.62) < 0.02        # at 5 %/yr
    assert_figure(w["chart"])
    assert w["benefits"]["vehicle_hours_per_year"] > 0
    # Losing capacity on one approach can only make things worse.
    s = get("/api/signal/whatif?loss_approach=rokadiya_hanuman&loss_pct=20&growth=10")["scenario"]
    assert s["worst_x_adaptive"] > w["today"]["worst_x_adaptive"]
    assert s["opt"]["feasible"] is False
    get("/api/signal/whatif?loss_approach=nowhere&loss_pct=5", status=422)
    get("/api/signal/whatif?growth=500", status=422)


def test_locations(tmp_path, monkeypatch):
    loc = get("/api/locations")
    assert len(loc["cameras"]) == 7
    assert all(c["lat"] is not None for c in loc["cameras"])
    target = tmp_path / "camera_locations.json"
    target.write_text(api.LOCATIONS.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(api, "LOCATIONS", target)
    r = client.put("/api/locations/gauravpath", json={"lat": 21.16, "lon": 72.78})
    assert r.status_code == 200 and r.json()["verified"] is True
    g = next(c for c in get("/api/locations")["cameras"] if c["id"] == "gauravpath")
    assert g["verified"] and g["lat"] == 21.16
    assert client.put("/api/locations/gauravpath", json={"lat": 28.6, "lon": 77.2}).status_code == 422
    assert client.put("/api/locations/nowhere", json={"lat": 21.2, "lon": 72.8}).status_code == 404


def test_briefing():
    r = client.get("/api/report/briefing.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "network briefing" in r.text and "Mahidharpura" in r.text
    assert "headroom" in r.text and ">nan" not in r.text.lower()
    d = client.get("/api/report/briefing.html?download=true")
    assert "attachment" in d.headers.get("content-disposition", "")


# --------------------------------------------------------------------------
# Live monitoring (replay; live detection needs the detector - see e2e)
# --------------------------------------------------------------------------

def test_live_state():
    s = get("/api/live/state")
    assert_strict_json(s)
    assert len(s["cameras"]) >= 7 and s["speeds"] == [0.5, 1.0, 2.0, 5.0, 10.0]
    c = next(c for c in s["cameras"] if c["id"] == "gauravpath")
    assert c["source"] == "replay" and 0 <= c["clip_t"] <= c["duration"]
    assert c["zones"] and all(z["los"] in ("Free", "Moderate", "Heavy", "Jam") for z in c["zones"])


def test_live_events_stream():
    import json
    with client.stream("GET", "/api/live/events?limit=2") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = b"".join(r.iter_bytes()).decode()
    ticks = [line[6:] for line in body.splitlines() if line.startswith("data: ")]
    assert len(ticks) == 2
    d = json.loads(ticks[-1])
    assert set(d) == {"state", "events"} and d["state"]["cameras"]


def test_live_frame_and_stream():
    f = client.get("/api/live/frame.jpg?cam=rokadiya_hanuman")
    assert f.status_code == 200 and f.content[:2] == b"\xff\xd8"
    with client.stream("GET", "/api/live/stream.mjpg?cam=rokadiya_hanuman&frames=2") as r:
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
        body = b"".join(r.iter_bytes())
    assert body.count(b"--frame") == 2
    assert client.get("/api/live/frame.jpg?cam=nowhere").status_code == 404


def test_live_control_validation():
    try:
        assert client.post("/api/live/control", json={"speed": 3.0}).status_code == 422
        assert client.post("/api/live/control", json={"playing": "yes"}).status_code == 422
        r = client.post("/api/live/control", json={"playing": False})
        assert r.status_code == 200 and r.json()["playing"] is False
        t1 = get("/api/live/state")["t"]
        assert get("/api/live/state")["t"] == t1             # paused: the clock stands
    finally:
        client.post("/api/live/control", json={"playing": True, "speed": 1.0})
    assert client.post("/api/live/detect", json={"camera": "nowhere"}).status_code == 404
