"""End-to-end run of every detector-backed flow through the real API.

    python tests/e2e_pipeline.py            # from the project root; ~3 min on CPU

Uploads (image, video, dataset clip), report downloads, keep-alongside, and
calibration apply -> re-measure -> undo -> re-measure. Needs the detector
weights and the dataset videos, so it is not part of CI; it skips itself
(exit 0) when either is missing.

Every project file the flow touches is backed up first and restored at the
end, so the committed dataset - and any clip the operator kept themselves - is
left exactly as it was.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cv2  # noqa: E402

import config as cfg  # noqa: E402
import services as S  # noqa: E402

NEEDED = ("chauta_bazaar", "rokadiya_hanuman", "gauravpath")
if not S.detector_ready()[0] or not all(cfg.camera_video_path(c).exists()
                                        for c in NEEDED):
    print("SKIP  detector weights or dataset videos not on this machine")
    sys.exit(0)

from fastapi.testclient import TestClient  # noqa: E402

import api  # noqa: E402
import ingest  # noqa: E402

c = TestClient(api.app)
results = []
WORK = Path(tempfile.mkdtemp(prefix="sti_e2e_"))


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}", flush=True)


def wait(job, limit=1800):
    t0 = time.time()
    last = -1
    while time.time() - t0 < limit:
        j = c.get(f"/api/jobs/{job['id']}").json()
        pct = int(j["progress"] * 100)
        if pct // 20 != last // 20:
            print(f"      … {j['label']}: {pct}% {j['message']}", flush=True)
            last = pct
        if j["status"] in ("done", "error"):
            return j
        time.sleep(1.0)
    raise TimeoutError(job["label"])


def backup(files: list[Path], into: Path) -> dict[Path, bool]:
    into.mkdir(exist_ok=True)
    existed = {}
    for f in files:
        existed[f] = f.exists()
        if f.exists():
            shutil.copy2(f, into / f.name)
    return existed


def restore(existed: dict[Path, bool], src: Path) -> None:
    for f, was in existed.items():
        if was:
            shutil.copy2(src / f.name, f)
        else:
            f.unlink(missing_ok=True)


# ---------------------------------------------------------------- media prep
cap = cfg.open_video(cfg.camera_video_path("chauta_bazaar"))
cap.set(cv2.CAP_PROP_POS_FRAMES, 1500)
ok, frame = cap.read()
cap.release()
still = WORK / "e2e_still.jpg"
cv2.imwrite(str(still), frame)

clip = WORK / "e2e_clip.mp4"
cap = cfg.open_video(cfg.camera_video_path("rokadiya_hanuman"))
w, _ = cfg.video_writer(clip, 20, (1920, 1080))
for _ in range(240):                       # 12 s at 20 fps
    ok, f = cap.read()
    if not ok:
        break
    w.write(f)
w.release()
cap.release()

# ---------------------------------------------------------------- 1. still
print("\n== image upload", flush=True)
with open(still, "rb") as fh:
    r = c.post("/api/uploads", files={"file": ("junction still.jpg", fh, "image/jpeg")})
check("image upload accepted", r.status_code == 200, r.text[:120])
u = r.json()
r = c.post(f"/api/uploads/{u['id']}/roi", json={"method": "whole", "n_lanes": 0}).json()
check("image roi is whole-frame, no job",
      r["job"] is None and r["upload"]["roi"]["method"] == "whole-frame")
check("zones preview image", c.get(f"/api/uploads/{u['id']}/zones.jpg?w=400").status_code == 200)
j = wait(c.post(f"/api/uploads/{u['id']}/analyze", json={}).json()["job"])
check("image analysis job done", j["status"] == "done", j["error"])
rep = c.get(f"/api/uploads/{u['id']}/report").json()
check("image report", rep["kind"] == "image" and rep["vehicles"] > 0,
      f"{rep['vehicles']} vehicles, {rep['pcu']:.1f} PCU")
check("image annotated preview",
      c.get(f"/api/uploads/{u['id']}/preview.jpg?w=400").status_code == 200)
check("image cannot be kept", c.post(f"/api/uploads/{u['id']}/persist").status_code == 409)
c.delete(f"/api/uploads/{u['id']}")

# ---------------------------------------------------------------- 2. video
print("\n== video upload (find road from traffic)", flush=True)
with open(clip, "rb") as fh:
    r = c.post("/api/uploads", files={"file": ("e2e clip.mp4", fh, "video/mp4")})
check("video upload accepted", r.status_code == 200, r.text[:120])
u = r.json()
check("video probed", u["media"]["kind"] == "video" and u["media"]["duration_s"] > 10,
      u["media"]["label"])
r = c.post(f"/api/uploads/{u['id']}/roi", json={"method": "traffic", "n_lanes": 3}).json()
j = wait(r["job"])
roi = (j["result"] or {}).get("roi") or {}
check("road found from traffic",
      j["status"] == "done" and roi.get("method") in ("traffic", "motion"),
      f"{roi.get('method', j['error'])}, {roi.get('zones')} zones")
j = wait(c.post(f"/api/uploads/{u['id']}/analyze",
                json={"stride": 4, "seconds": 12}).json()["job"])
check("video analysis job done", j["status"] == "done", j["error"])
rep = c.get(f"/api/uploads/{u['id']}/report?theme=dark").json()
check("video report", rep["kind"] == "video" and not rep["empty"] and rep["windows"] > 0,
      f"{rep.get('windows')} windows, {rep.get('vehicles')} vehicles, {rep.get('mode')} mode")
check("video report charts", all(rep["charts"][k] for k in ("zone_los", "count_vs_occupancy")))
t = c.get(f"/api/uploads/{u['id']}/trend?metric=congestion_index").json()
check("video trend", t["chart"] is not None and len(t["rows"]) > 0)
csv = c.get(f"/api/uploads/{u['id']}/metrics.csv")
check("metrics CSV download", csv.status_code == 200 and csv.text.startswith("camera_id,"),
      f"{len(csv.text.splitlines()) - 1} rows")
html = c.get(f"/api/uploads/{u['id']}/report.html")
check("HTML report download",
      html.status_code == 200 and "<!doctype html>" in html.text.lower(),
      f"{len(html.text) // 1024} KB")

# Keep-alongside writes into configs/ and outputs/logs/. Only this test's own
# session is removed afterwards; anything the operator kept is restored as-is.
kept_files = [ingest.PERSISTED_CAMERAS, ingest.PERSISTED_CAMERAS.with_suffix(".names.json")]
kept_backup = backup(kept_files, WORK / "kept_backup")
sessions_before = set(ingest.persisted_sessions())
before = c.get("/api/meta").json()["metrics"]["windows"]
try:
    p = c.post(f"/api/uploads/{u['id']}/persist").json()
    after_meta = c.get("/api/meta").json()
    new_ids = [s for s in ingest.persisted_sessions() if s not in sessions_before]
    check("kept clip appears in the network",
          after_meta["metrics"]["windows"] > before and new_ids,
          f"{p['file']}; windows {before} -> {after_meta['metrics']['windows']}; {new_ids}")
    net = c.get("/api/network").json()
    check("kept clip ranked in Network view", any(s["id"] in new_ids for s in net["sites"]))
    check("kept clip has a Site detail",
          bool(new_ids) and c.get(f"/api/sites/{new_ids[0]}").status_code == 200)
    check("upload delete", c.delete(f"/api/uploads/{u['id']}").json()["deleted"])
finally:
    for sid in set(ingest.persisted_sessions()) - sessions_before:
        ingest.persisted_metrics_path(sid).unlink(missing_ok=True)
    restore(kept_backup, WORK / "kept_backup")
check("kept clip removed again", c.get("/api/meta").json()["metrics"]["windows"] == before)

# ---------------------------------------------------------------- 3. demo clip
print("\n== dataset clip through the upload path", flush=True)
u = c.post("/api/uploads/demo", json={"camera": "gauravpath"}).json()
r = c.post(f"/api/uploads/{u['id']}/roi", json={"method": "whole", "n_lanes": 2}).json()
# Whole frame has no traffic corridor to split into lanes, so a lane count is
# ignored and the frame is one area-mode zone (the UI says so up front).
check("demo clip whole frame = one area zone",
      r["upload"]["roi"]["zones"] == 1 and r["upload"]["roi"]["mode"] == "area")
j = wait(c.post(f"/api/uploads/{u['id']}/analyze",
                json={"stride": 8, "seconds": 8}).json()["job"])
rep = c.get(f"/api/uploads/{u['id']}/report").json()
check("demo clip analysed", j["status"] == "done" and rep.get("windows", 0) > 0,
      f"{rep.get('windows')} windows")
c.delete(f"/api/uploads/{u['id']}")
check("demo clip source not deleted", cfg.camera_video_path("gauravpath").exists())

# ---------------------------------------------------------------- 4. calibration
print("\n== calibration apply -> re-measure -> undo -> re-measure", flush=True)
touched = [cfg.CAMERA_CONFIG, cfg.LOG_DIR / "gauravpath_metrics.csv",
           cfg.LOG_DIR / "all_metrics.csv", cfg.CALIBRATION_STATE]
cal_backup = backup(touched, WORK / "cal_backup")
seed_model = json.loads(cfg.CAMERA_CONFIG.read_text("utf-8"))["gauravpath"]["scale_model"]
orig = c.get("/api/sites/gauravpath").json()
calibrated_before = c.get("/api/meta").json()["calibrated"]
try:
    cam = c.get("/api/calibration/gauravpath").json()
    rows = [r["row"] for r in cam["rows"]]
    meas = [[rows[0], 7.0], [rows[-1], 7.0]]
    fit = c.post("/api/calibration/gauravpath/fit",
                 json={"measurements": meas, "camera_height_m": 7, "hfov_deg": 60}).json()
    check("fit from two rows", fit["ok"], fit["message"])
    r = c.post("/api/calibration/gauravpath/apply",
               json={"measurements": meas, "camera_height_m": 7, "hfov_deg": 60}).json()
    j = wait(r["job"])
    check("apply + re-measure job", j["status"] == "done", j["error"] or j["result"])
    m = c.get("/api/meta").json()
    check("camera now verified", m["calibrated"] == calibrated_before + 1)
    s = c.get("/api/sites/gauravpath").json()
    check("site reports measured scale", s["camera"]["scale_verified"]
          and not any(t["estimate"] for t in s["trend_metrics"]))
    st = json.loads(cfg.CALIBRATION_STATE.read_text("utf-8"))
    check("previous model recorded for undo",
          st["measurements"]["gauravpath"]["previous_scale_model"] == seed_model)

    r = c.delete("/api/calibration/gauravpath").json()
    j = wait(r["job"])
    check("undo + re-measure job", j["status"] == "done", j["error"] or j["result"])
    restored = json.loads(cfg.CAMERA_CONFIG.read_text("utf-8"))["gauravpath"]["scale_model"]
    check("undo restored the seeded scale model", restored == seed_model)
    s = c.get("/api/sites/gauravpath").json()
    check("speeds and densities present after undo",
          any(z["speed"] is not None for z in s["zones"])
          and not s["camera"]["scale_verified"])
    check("signal timing still computes after undo", c.get("/api/signal").status_code == 200)
    # How closely does today's pipeline reproduce the committed measurement?
    check("re-measured window count matches committed", s["windows"] == orig["windows"],
          f"{s['windows']} vs {orig['windows']}")
    print(f"      mean congestion: committed {orig['mean_congestion']:.3f}  "
          f"re-measured {s['mean_congestion']:.3f}", flush=True)
finally:
    restore(cal_backup, WORK / "cal_backup")
    cfg.refresh_scale_verified()
    print("      restored", [f.name for f in touched], flush=True)

check("committed data restored",
      c.get("/api/sites/gauravpath").json()["mean_congestion"] == orig["mean_congestion"])

# ---------------------------------------------------------------- 5. live
print("\n== live detection", flush=True)
r = c.post("/api/live/detect", json={"camera": "gauravpath"})
check("live detection starts", r.status_code == 200, r.text[:120])
t0, st = time.time(), {}
while time.time() - t0 < 180:
    st = c.get("/api/live/state").json()
    d = st.get("detect") or {}
    cam = next(x for x in st["cameras"] if x["id"] == "gauravpath")
    if d.get("status") == "error" or (d.get("frames", 0) >= 20 and cam["zones"]):
        break
    time.sleep(1.0)
d = st.get("detect") or {}
cam = next(x for x in st["cameras"] if x["id"] == "gauravpath")
check("live detection runs", d.get("status") == "running" and d.get("frames", 0) >= 20,
      f"{d.get('frames')} frames at {d.get('fps')} fps on {d.get('model')} — {d.get('message')}")
check("camera switches to detection", cam["source"] == "detect")
check("zones measured live", len(cam["zones"]) > 0 and all(z["los"] for z in cam["zones"]),
      ", ".join(f"{z['label']}: {z['los']}" for z in cam["zones"]))
f = c.get("/api/live/frame.jpg?cam=gauravpath")
check("annotated live frame", f.status_code == 200 and f.content[:2] == b"\xff\xd8",
      f"{len(f.content) // 1024} KB")
busy = c.post("/api/live/detect", json={"camera": "rokadiya_hanuman"})
check("second session refused while one runs", busy.status_code == 409, busy.text[:100])
c.delete("/api/live/detect")
t0, stopped = time.time(), False
while time.time() - t0 < 30:
    d = c.get("/api/live/state").json().get("detect") or {}
    if d.get("status") == "stopped":
        stopped = True
        break
    time.sleep(0.5)
check("live detection stops and frees the detector",
      stopped and S.DETECTOR_LOCK.acquire(blocking=False))
if stopped:
    S.DETECTOR_LOCK.release()

shutil.rmtree(WORK, ignore_errors=True)
n_fail = sum(1 for _, ok_, _ in results if not ok_)
print(f"\n{len(results) - n_fail} passed, {n_fail} failed", flush=True)
sys.stdout.flush()
sys.stderr.flush()
# os._exit, not sys.exit: by now this process holds both detectors (PyTorch
# and the OpenVINO GPU build) and the live monitor's daemon threads, and
# letting the interpreter tear them down together crashed it after every
# check had passed (exit code 5 on Windows), which would report a pass as a
# failure. Each piece exits cleanly on its own; the result is already printed.
import os  # noqa: E402
os._exit(1 if n_fail else 0)
