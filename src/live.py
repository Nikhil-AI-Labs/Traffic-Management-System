"""Live monitoring: the network as it happens, streamed to the browser.

Two sources feed one live picture:

* replay - every camera's recorded measurement windows, played back on a
           shared clock as if they were arriving now. It needs nothing but the
           committed CSVs, so it works on any deployment; the footage (or its
           annotated render) is shown in step when it is on disk, and the
           camera's reference frame shaded by the current level of service
           when it is not.
* detect - the real pipeline on one camera: YOLO11 + ByteTrack + the metrics
           engine, paced to wall-clock time. When inference cannot keep up it
           drops frames rather than falling behind, so what is on screen is
           always "now" and the frame rate it achieves is reported, not
           assumed. While it runs, that camera's tile, zones, alerts and video
           come from detection rather than from the CSV.

Alerts are raised the moment their evidence is complete - a sustained
condition becomes an alert at the end of its third window, exactly when
`bottleneck_detect` would first be able to see it - and cleared when the
condition ends. Replayed alerts quote the evidence available at that moment,
never the full-clip figures, so nothing on the live view knows the future.

Live detection also watches individual tracks for a vehicle standing still in
a running lane while the lane itself is flowing: a breakdown or an illegal
stop, which a zone-level average would never surface.
"""

from __future__ import annotations

import math
import threading
import time
import traceback
import uuid
from collections import Counter, OrderedDict, deque
from datetime import datetime, timezone

import cv2
import numpy as np
import pandas as pd

import config as cfg

cfg.quiet_opencv()
import bottleneck_detect as bn
import forecast as fc
import services as S
import theme as T
import zones as Z

WINDOW_S = 2.0                # metrics window, as everywhere else
FRAME_W = 1024                # width of streamed frames
JPEG_Q = 72
REPLAY_FPS = 8.0              # frame-rate cap for replayed footage
SEEK_GAP_S = 1.0              # seek rather than decode forward past this gap
VIEWER_GRACE_S = 8.0          # a frame source outlives its last viewer by this
DETECT_IDLE_STOP_S = 90.0     # stop live detection nobody is watching
STALL_MIN_S = 20.0            # standing still this long in a flowing lane
STALL_MOVE_FRAC = 0.35        # "moved" = anchor shift > this x box height
SPEEDS = (0.5, 1.0, 2.0, 5.0, 10.0)
MAX_ALERTS = 300
RISK_THRESHOLD = 0.45

STALL_KIND = "stationary_vehicle"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _f(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def zone_now(row: dict) -> dict:
    """One zone's current state, from a CSV row or a live ZoneWindow dict."""
    ci = _f(row.get("congestion_index")) or 0.0
    los = row.get("los")
    return {
        "zone_id": str(row.get("zone_id")),
        "label": str(row.get("zone_label") or row.get("zone_id")),
        "kind": row.get("zone_kind"),
        "ci": ci,
        "los": los if los in cfg.LOS_LEVELS else T.los_of(ci),
        "occupancy": _f(row.get("structural_occupancy")),
        "density": _f(row.get("density_pcu_per_km_lane")),
        "speed": _f(row.get("mean_speed_kmph")),
        "flow": _f(row.get("flow_pcu_per_hr")),
        "queue": _f(row.get("queue_length_pcu")),
        "saturated": bool(row.get("saturated") in (True, 1, "True", "true")),
        "mode": row.get("measurement_mode"),
        "t": _f(row.get("window_start_s")),
    }


def _camera_info(cam: str) -> dict:
    import charts as C
    m = cfg.camera_meta(cam)
    dataset = cam in cfg.CAMERAS
    return {"id": cam, "name": m["name"], "short": C.short_name(cam),
            "mode": m.get("mode", "area"),
            "has_video": dataset and cfg.camera_video_path(cam).exists(),
            "has_annotated": (cfg.ANNOTATED_DIR / f"{cam}_demo.mp4").exists(),
            "can_detect": (dataset and cfg.camera_video_path(cam).exists()
                           and Z.load(cam) is not None)}


def _mean_ci(zones: list[dict]) -> float:
    vals = [z["ci"] for z in zones if z.get("ci") is not None]
    return float(np.mean(vals)) if vals else 0.0


def encode(frame_bgr: np.ndarray, width: int = FRAME_W, quality: int = JPEG_Q) -> bytes:
    h, w = frame_bgr.shape[:2]
    if w > width:
        frame_bgr = cv2.resize(frame_bgr, (width, int(round(h * width / w))),
                               interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buf.tobytes()


def hud(frame: np.ndarray, lines: list[str], accent=(80, 200, 255)) -> np.ndarray:
    """Compact caption in the top-left corner, legible after downscaling."""
    out = frame
    scale = max(0.7, frame.shape[1] / 1600)
    th = int(34 * scale)
    width = int(max((cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, 0.85 * scale, 2)[0][0]
                     for s in lines), default=0) + 34 * scale)
    height = int(th * len(lines) + 18 * scale)
    x, y = int(18 * scale), int(18 * scale)
    overlay = out.copy()
    cv2.rectangle(overlay, (x, y), (x + width, y + height), (12, 12, 16), -1)
    out = cv2.addWeighted(overlay, 0.72, out, 0.28, 0)
    cv2.rectangle(out, (x, y), (x + int(6 * scale), y + height), accent, -1)
    for i, s in enumerate(lines):
        cv2.putText(out, s, (x + int(20 * scale), y + int(th * (i + 1) - 4 * scale)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85 * scale,
                    accent if i == 0 else (235, 235, 235), 2, cv2.LINE_AA)
    return out


# --------------------------------------------------------------------------
# Frame sharing
# --------------------------------------------------------------------------

class _Publisher:
    """Latest JPEG plus a sequence number; any number of viewers wait on it."""

    def __init__(self):
        self.cond = threading.Condition()
        self.jpeg: bytes | None = None
        self.seq = 0
        self.refs = 0
        self.last_view = time.monotonic()

    def publish(self, jpeg: bytes) -> None:
        with self.cond:
            self.jpeg = jpeg
            self.seq += 1
            self.cond.notify_all()

    def wait(self, after: int, timeout: float) -> tuple[bytes | None, int]:
        with self.cond:
            self.cond.wait_for(lambda: self.seq > after, timeout)
            if self.seq > after:
                return self.jpeg, self.seq
            return None, after

    def acquire(self) -> None:
        with self.cond:
            self.refs += 1
            self.last_view = time.monotonic()

    def release(self) -> None:
        with self.cond:
            self.refs = max(0, self.refs - 1)
            self.last_view = time.monotonic()

    def watched(self, grace: float) -> bool:
        return self.refs > 0 or time.monotonic() - self.last_view < grace


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------

class ReplayFeed:
    """One camera's recorded windows, addressable by clip time."""

    def __init__(self, cam: str, sub: pd.DataFrame):
        sub = sub.sort_values(["window_start_s", "zone_id"]).reset_index(drop=True)
        self.cam = cam
        self.info = _camera_info(cam)
        self.df = sub
        self.duration = float(sub.window_end_s.max())
        zid = sub.zone_id.astype(str)
        self.zone_order = list(dict.fromkeys(zid))
        self.rows = {z: g.to_dict("records") for z, g in sub.groupby(zid, sort=False)}
        self.starts = {z: np.array([r["window_start_s"] for r in rs], float)
                       for z, rs in self.rows.items()}
        self.series = sub.groupby("window_start_s").congestion_index.mean().sort_index()
        # When each recorded alert would first have been visible: at the end
        # of the window that completed its minimum sustained run.
        self.schedule = sorted(
            ((min(a.end_s, a.start_s + bn.MIN_SUSTAINED_WINDOWS * WINDOW_S), a)
             for a in bn.detect(sub, cam)), key=lambda x: x[0])

    def clip_time(self, t: float) -> float:
        return t % self.duration if self.duration > 0 else 0.0

    def lap(self, t: float) -> int:
        return int(t // self.duration) if self.duration > 0 else 0

    def zones_at(self, ct: float) -> list[dict]:
        out = []
        for z in self.zone_order:
            i = int(np.searchsorted(self.starts[z], ct, side="right")) - 1
            out.append(zone_now(self.rows[z][max(0, i)]))
        return out

    def spark(self, ct: float, n: int = 40) -> list[float]:
        s = self.series
        cur = s[s.index <= ct].to_numpy()
        if len(cur) < n:      # continue from the end of the previous lap
            prev = s[s.index > ct].to_numpy()[-(n - len(cur)):]
            vals = np.concatenate([prev, cur])
        else:
            vals = cur[-n:]
        return [round(float(v), 3) for v in vals if math.isfinite(v)]

    def history(self, ct: float, horizon: float = 120.0) -> pd.DataFrame:
        d = self.df
        cur = d[(d.window_end_s <= ct) & (d.window_end_s > ct - horizon)]
        if ct >= horizon:
            return cur
        prev = d[d.window_end_s > self.duration - (horizon - ct)].copy()
        prev["window_start_s"] -= self.duration
        prev["window_end_s"] -= self.duration
        return pd.concat([prev, cur], ignore_index=True)


class ReplayFrames(_Publisher):
    """A camera's footage in step with the replay clock, decoded once and
    shared by every viewer.

    Prefers the annotated render (boxes and LOS shading from the real run);
    falls back to the raw footage with zones shaded from the replayed
    windows; and to the reference frame when no footage is on disk at all,
    which is the hosted case.
    """

    def __init__(self, hub: "LiveHub", cam: str):
        super().__init__()
        self.hub = hub
        self.cam = cam
        self.thread: threading.Thread | None = None

    def ensure_running(self) -> None:
        with self.cond:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, daemon=True,
                                               name=f"replay-frames-{self.cam}")
                self.thread.start()

    def acquire(self) -> None:
        super().acquire()
        self.ensure_running()

    def _overlay(self, frame, zones: list[dict], calib) -> np.ndarray:
        if calib is None:
            return frame
        hl = {z["zone_id"]: cfg.LOS_COLORS.get(z["los"], (200, 200, 200)) for z in zones}
        return Z.draw_zones(frame, calib, alpha=0.3, show_labels=False, highlight=hl)

    def _caption(self, frame, feed: ReplayFeed, ct: float, src: str) -> np.ndarray:
        speed = self.hub.speed
        state = "REPLAY" if self.hub.playing else "REPLAY - PAUSED"
        sp = f"{speed:g}x" if self.hub.playing else ""
        return hud(frame, [f"{state} {sp}".strip(), feed.info["short"],
                           f"clip t = {ct:5.1f} s  ({src})"])

    def _run(self) -> None:
        while True:
            try:
                self._loop()
            except Exception:
                traceback.print_exc()
                time.sleep(1.0)
            # Leave only if nobody arrived while the loop was winding down.
            # Decided under the same lock viewers register with, so a viewer
            # either sees this thread still running or gets a fresh one.
            with self.cond:
                if not self.watched(VIEWER_GRACE_S):
                    self.thread = None
                    return

    def _loop(self) -> None:
        cam = self.cam
        calib = Z.load(cam) if cam in cfg.CAMERAS else None
        annotated = cfg.ANNOTATED_DIR / f"{cam}_demo.mp4"
        raw = cfg.camera_video_path(cam) if cam in cfg.CAMERAS else None
        if annotated.exists():
            path, src = annotated, "annotated render"
        elif raw is not None and raw.exists():
            path, src = raw, "recorded footage"
        else:
            path, src = None, "reference frame"

        cap, fps, pos = None, 20.0, -1.0
        last_ct = None
        while self.watched(VIEWER_GRACE_S):
            tick = time.monotonic()
            feed = self.hub.feed(cam)
            if feed is None:
                time.sleep(0.5)
                continue
            ct = feed.clip_time(self.hub.now())
            if last_ct is not None and abs(ct - last_ct) < 1e-3:
                time.sleep(0.15)            # paused: nothing new to show
                continue
            last_ct = ct
            zones = feed.zones_at(ct)

            if path is None:
                ref = S.reference_image(cam)
                if ref is None:
                    time.sleep(1.0)
                    continue
                frame = self._overlay(cv2.cvtColor(ref, cv2.COLOR_RGB2BGR), zones, calib)
                self.publish(encode(self._caption(frame, feed, ct, src)))
                time.sleep(0.5)
                continue

            if cap is None or ct < pos - 0.25 or ct - pos > SEEK_GAP_S:
                if cap is None or ct < pos - 0.25:
                    if cap is not None:
                        cap.release()
                    cap = cfg.open_video(path)
                    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(ct * fps))
                pos = ct
            step = 1.0 / fps
            ok = True
            while ok and pos + step < ct:
                ok = cap.grab()
                pos += step
            ok, frame = cap.read() if ok else (False, None)
            pos += step
            if not ok or frame is None:
                cap.release()
                cap = None
                continue
            if src != "annotated render":
                frame = self._overlay(frame, zones, calib)
            self.publish(encode(self._caption(frame, feed, ct, src)))
            time.sleep(max(0.0, 1.0 / REPLAY_FPS - (time.monotonic() - tick)))
        if cap is not None:
            cap.release()


# --------------------------------------------------------------------------
# Live detection
# --------------------------------------------------------------------------

def _live_detector():
    """The detector the live monitor uses: the OpenVINO export when there is
    one (faster on an Intel CPU), the PyTorch weights otherwise."""
    getter = getattr(S, "get_live_detector", None)
    return getter() if getter else S.get_detector()


class DetectSession(_Publisher):
    """The full pipeline on one camera, in real time."""

    def __init__(self, hub: "LiveHub", cam: str):
        super().__init__()
        self.hub = hub
        self.cam = cam
        self.info = _camera_info(cam)
        self.id = uuid.uuid4().hex[:8]
        self.status = "starting"          # starting | running | stopped | error
        self.message = "Waiting for the detector…"
        self.model = ""
        self.fps = 0.0
        self.frames = 0
        self.t = 0.0
        self.started = time.time()
        self.zones: dict[str, dict] = {}
        self.rows: deque = deque(maxlen=4000)
        self.tally: Counter = Counter()
        self.stall: dict[int, dict] = {}
        self._alert_keys: dict[tuple, str] = {}
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"live-detect-{cam}")

    @property
    def running(self) -> bool:
        return self.status in ("starting", "running")

    def start(self) -> None:
        self.thread.start()

    def stop(self, why: str = "Stopped by the operator.") -> None:
        self.message = why
        self._stop.set()

    def view(self) -> dict:
        return {"id": self.id, "camera_id": self.cam, "camera_short": self.info["short"],
                "status": self.status, "message": self.message, "model": self.model,
                "fps": round(self.fps, 2), "frames": self.frames,
                "t": round(self.t, 1), "elapsed_s": round(time.time() - self.started, 1),
                "vehicles": dict(self.tally)}

    def camera_state(self) -> dict:
        # Read from the hub's thread while the detection thread writes:
        # list(dict.values()) copies in one step under the GIL, where a
        # comprehension over the dict could see it change size mid-loop.
        zones = list(self.zones.values())
        ci = _mean_ci(zones)
        return {**self.info, "source": "detect", "clip_t": self.t, "duration": None,
                "lap": 0, "ci": ci, "los": T.los_of(ci), "zones": zones,
                "spark": self._spark()}

    def _spark(self, n: int = 40) -> list[float]:
        if not self.rows:
            return []
        d = pd.DataFrame(list(self.rows)[-n * 8:])
        s = d.groupby("window_start_s").congestion_index.mean().sort_index()
        return [round(float(v), 3) for v in s.to_numpy()[-n:] if math.isfinite(v)]

    def history_df(self, horizon: float = 120.0) -> pd.DataFrame:
        if not self.rows:
            return pd.DataFrame()
        d = pd.DataFrame(list(self.rows))
        return d[d.window_end_s > d.window_end_s.max() - horizon]

    # ---------------------------------------------------------------- loop

    def _run(self) -> None:
        if not S.DETECTOR_LOCK.acquire(timeout=2.0):
            self.status = "error"
            self.message = ("The detector is busy with an analysis job. Try "
                            "again when it finishes.")
            self.hub.emit({"type": "detect", "session": self.view()})
            return
        cap = None
        try:
            self.message = "Loading the detector…"
            self.hub.emit({"type": "detect", "session": self.view()})
            det = _live_detector()
            self.model = getattr(det, "label", "") or "YOLO11-S (PyTorch)"
            det.reset_tracker()
            calib = Z.load(self.cam)
            from metrics import MetricsEngine
            engine = MetricsEngine(self.cam, calib, window_s=WINDOW_S)
            src = cfg.camera_video_path(self.cam)
            cap = cfg.open_video(src)
            fps_src = cap.get(cv2.CAP_PROP_FPS) or 20.0
            self.status, self.message = "running", "Detecting in real time"
            self.hub.emit({"type": "detect", "session": self.view()})

            wall0 = time.monotonic()
            idx, lap_off, last_done = 0, 0.0, None
            while not self._stop.is_set():
                if (not self.watched(DETECT_IDLE_STOP_S)
                        and time.monotonic() - self.hub.last_seen > DETECT_IDLE_STOP_S):
                    self.message = "Stopped: nobody was watching."
                    break
                # Where wall-clock time says the footage is now. Frames the
                # detector had no time for are skipped, not queued.
                target = int((time.monotonic() - wall0) * fps_src)
                ok = True
                while ok and idx < target:
                    ok = cap.grab()
                    idx += 1
                frame = None
                if ok:
                    ok, frame = cap.read()
                    idx += 1
                if not ok or frame is None:          # end of clip: loop it
                    cap.release()
                    cap = cfg.open_video(src)
                    lap_off += idx / fps_src
                    idx, wall0 = 0, time.monotonic()
                    det.reset_tracker()
                    continue

                ts = lap_off + idx / fps_src
                dets = det.track_frame(frame)
                from detector import FrameResult
                closed = engine.process(FrameResult(idx, ts, dets, frame))
                for w in closed:
                    d = w.to_dict()
                    d.pop("counts_by_group", None)
                    self.rows.append(d)
                    self.zones[w.zone_id] = zone_now(d)
                if closed:
                    self._check_alerts()
                self._check_stalls(ts, dets, calib)
                for d in dets:
                    self.tally[d.group] += 1
                self.publish(encode(self._render(frame, dets, calib, ts)))

                now = time.monotonic()
                if last_done is not None:
                    inst = 1.0 / max(1e-3, now - last_done)
                    self.fps = inst if self.fps == 0 else 0.8 * self.fps + 0.2 * inst
                last_done = now
                self.frames += 1
                self.t = ts
        except Exception as e:  # surfaced on the live view
            traceback.print_exc()
            self.status = "error"
            self.message = f"{type(e).__name__}: {e}"
        finally:
            if cap is not None:
                cap.release()
            S.DETECTOR_LOCK.release()
            if self.status != "error":
                self.status = "stopped"
            for aid in list(self._alert_keys.values()):
                self.hub.clear_alert(aid)
            for s in self.stall.values():
                if s.get("aid"):
                    self.hub.clear_alert(s["aid"])
            self.hub.emit({"type": "detect", "session": self.view()})

    # ---------------------------------------------------------- analytics

    def _check_alerts(self) -> None:
        df = pd.DataFrame(list(self.rows))
        if df.empty:
            return
        latest = float(df.window_end_s.max())
        live = set()
        for a in bn.detect(df, self.cam):
            # Only conditions still in progress are live alerts.
            if a.end_s < latest - 2 * WINDOW_S - 1e-6:
                continue
            key = (a.zone_id, a.kind)
            live.add(key)
            aid = self._alert_keys.get(key)
            if aid and self.hub.is_active(aid):
                self.hub.update_alert(aid, end_s=a.end_s, duration_s=a.duration_s,
                                      evidence=a.evidence, headline=a.headline)
            else:
                self._alert_keys[key] = self.hub.raise_alert(a.to_dict(), "detect")
        for key, aid in list(self._alert_keys.items()):
            if key not in live:
                self.hub.clear_alert(aid)
                del self._alert_keys[key]

    def _check_stalls(self, ts: float, dets, calib) -> None:
        for d in dets:
            if d.track_id is None:
                continue
            x, y = d.anchor
            h = max(8.0, d.xyxy[3] - d.xyxy[1])
            s = self.stall.get(d.track_id)
            if s is None or math.hypot(x - s["x"], y - s["y"]) > STALL_MOVE_FRAC * h:
                if s and s.get("aid"):
                    self.hub.clear_alert(s["aid"])
                self.stall[d.track_id] = {"x": x, "y": y, "since": ts, "last": ts,
                                          "cls": d.cls_name, "aid": None,
                                          "box": d.xyxy}
                continue
            s["last"], s["box"] = ts, d.xyxy
            still = ts - s["since"]
            if still < STALL_MIN_S or s["aid"]:
                continue
            z = calib.assign(d.anchor)
            if z is None or z.kind != "lane":
                continue
            zs = self.zones.get(z.id)
            if zs and zs["los"] in ("Heavy", "Jam"):
                continue           # a queue, not an anomaly
            est = "" if cfg.scale_is_verified(self.cam) else "~"
            s["aid"] = self.hub.raise_alert({
                "camera_id": self.cam, "camera_name": self.info["name"],
                "zone_id": z.id, "zone_label": z.label, "kind": STALL_KIND,
                "severity": "warning", "start_s": round(s["since"], 1),
                "end_s": round(ts, 1), "duration_s": round(still, 1),
                "headline": f"Vehicle standing still in {z.label}",
                "evidence": (f"Track #{d.track_id} ({d.cls_name}) has moved less than a "
                             f"third of its own length in {still:.0f} s while the lane "
                             f"is {zs['los'] if zs else 'flowing'} "
                             f"({est}{(zs or {}).get('speed') or 0:.0f} km/h)."),
                "recommendation": ("Check the feed: a breakdown or an illegal stop in a "
                                   "running lane. Dispatch the nearest unit if it is "
                                   "blocking traffic."),
            }, "detect")
        for tid in [t for t, s in self.stall.items() if ts - s["last"] > 3.0]:
            s = self.stall.pop(tid)
            if s.get("aid"):
                self.hub.clear_alert(s["aid"])

    def _render(self, frame, dets, calib, ts) -> np.ndarray:
        from detector import draw_detections
        hl = {zid: cfg.LOS_COLORS.get(z["los"], (200, 200, 200))
              for zid, z in self.zones.items()}
        vis = Z.draw_zones(frame, calib, alpha=0.28, show_labels=False, highlight=hl)
        vis = draw_detections(vis, dets, show_id=False)
        for s in self.stall.values():
            if not s.get("aid"):
                continue
            x1, y1, x2, y2 = (int(v) for v in s["box"])
            cv2.rectangle(vis, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4), (40, 40, 230), 4)
            cv2.putText(vis, f"STOPPED {ts - s['since']:.0f}s", (x1, max(24, y1 - 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (40, 40, 230), 3, cv2.LINE_AA)
        return hud(vis, ["LIVE DETECTION", self.info["short"],
                         f"t = {ts:5.1f} s   {self.fps:4.1f} fps   {len(dets)} vehicles"],
                   accent=(60, 60, 235))


# --------------------------------------------------------------------------
# The hub
# --------------------------------------------------------------------------

class LiveHub:
    """Shared clock, replay feeds, alert stream and the detection session."""

    def __init__(self):
        self._lock = threading.RLock()
        self._df_ref = None
        self.feeds: dict[str, ReplayFeed] = {}
        self.playing = True
        self.speed = 1.0
        self._t_base = 0.0
        self._wall_base = time.monotonic()
        self.events: deque = deque(maxlen=400)
        self.seq = 0
        self._n_alerts = 0
        self.alerts: OrderedDict[str, dict] = OrderedDict()
        self.active: dict[str, dict] = {}
        self._replay_meta: dict[str, dict] = {}
        self._cursor: dict[str, float] = {}
        self.detect: DetectSession | None = None
        self.frames: dict[str, ReplayFrames] = {}
        self.last_seen = 0.0
        self._ticker: threading.Thread | None = None
        self._snap: tuple[float, dict | None] = (0.0, None)
        self._risk: tuple[float, list] = (0.0, [])

    # ---------------------------------------------------------- lifecycle

    def ensure_started(self) -> None:
        with self._lock:
            if self._ticker is None or not self._ticker.is_alive():
                self._ticker = threading.Thread(target=self._tick_loop, daemon=True,
                                                name="live-ticker")
                self._ticker.start()

    def touch(self) -> None:
        """A viewer is connected (called on every streamed update)."""
        self.last_seen = time.monotonic()

    # -------------------------------------------------------------- clock

    def now(self) -> float:
        with self._lock:
            if not self.playing:
                return self._t_base
            return self._t_base + (time.monotonic() - self._wall_base) * self.speed

    def control(self, playing: bool | None = None, speed: float | None = None,
                restart: bool = False) -> dict:
        if speed is not None and speed not in SPEEDS:
            raise ValueError(f"speed must be one of {SPEEDS}")
        with self._lock:
            t = 0.0 if restart else self.now()
            self._t_base, self._wall_base = t, time.monotonic()
            if playing is not None:
                self.playing = bool(playing)
            if speed is not None:
                self.speed = float(speed)
            if restart:
                self._cursor.clear()
                for aid in [a for a, al in self.active.items() if al["source"] == "replay"]:
                    self.clear_alert(aid)
            self._snap = (0.0, None)
            state = {"playing": self.playing, "speed": self.speed, "t": t}
        self.emit({"type": "control", **state})
        return state

    # -------------------------------------------------------------- feeds

    def _sync_feeds(self) -> None:
        df = S.load_metrics()
        if df is self._df_ref:
            return
        feeds = {}
        if not df.empty:
            for cam in S.analysed_cameras(df):
                sub = df[df.camera_id == cam]
                if len(sub) >= 3 and float(sub.window_end_s.max()) > 0:
                    feeds[cam] = ReplayFeed(cam, sub)
        with self._lock:
            self.feeds = feeds
            self._df_ref = df

    def feed(self, cam: str) -> ReplayFeed | None:
        self._sync_feeds()
        return self.feeds.get(cam)

    def cameras(self) -> list[dict]:
        self._sync_feeds()
        return [f.info for f in self.feeds.values()]

    # ------------------------------------------------------------- alerts

    def emit(self, ev: dict) -> None:
        with self._lock:
            self.seq += 1
            self.events.append({**ev, "seq": self.seq, "at": _iso()})

    def events_since(self, seq: int) -> tuple[list[dict], int]:
        with self._lock:
            if seq > self.seq:        # the server restarted under the client
                seq = 0
            return [e for e in self.events if e["seq"] > seq], self.seq

    def raise_alert(self, alert: dict, source: str) -> str:
        import charts as C
        with self._lock:
            self._n_alerts += 1
            aid = (f"live-{self._n_alerts}-{int(time.time())}|{alert['camera_id']}|"
                   f"{alert['zone_id']}|{alert['kind']}")
            a = {**alert, "id": aid, "source": source, "raised_at": _iso(),
                 "camera_short": C.short_name(alert["camera_id"])}
            self.alerts[aid] = a
            while len(self.alerts) > MAX_ALERTS:
                self.alerts.popitem(last=False)
            self.active[aid] = a
        self.emit({"type": "alert", "alert": a})
        return aid

    def update_alert(self, aid: str, **fields) -> None:
        with self._lock:
            a = self.alerts.get(aid)
            if a is not None:
                a.update(fields)

    def clear_alert(self, aid: str) -> None:
        with self._lock:
            a = self.active.pop(aid, None)
            self._replay_meta.pop(aid, None)
        if a is not None:
            self.emit({"type": "clear", "id": aid, "camera_short": a["camera_short"],
                       "zone_label": a["zone_label"], "kind": a["kind"]})

    def is_active(self, aid: str) -> bool:
        with self._lock:
            return aid in self.active

    def alert_log(self) -> list[dict]:
        with self._lock:
            return [dict(a, active=a["id"] in self.active) for a in self.alerts.values()]

    def find_alert(self, aid: str) -> dict | None:
        with self._lock:
            a = self.alerts.get(aid)
            return dict(a) if a else None

    # --------------------------------------------------------------- tick

    def _tick_loop(self) -> None:
        while True:
            time.sleep(0.5)
            try:
                self._advance()
            except Exception:
                traceback.print_exc()

    def _detecting(self, cam: str) -> bool:
        d = self.detect
        return d is not None and d.cam == cam and d.running

    def _advance(self) -> None:
        self._sync_feeds()
        t = self.now()
        for cam, feed in list(self.feeds.items()):
            prev = self._cursor.get(cam)
            self._cursor[cam] = t
            if self._detecting(cam):
                continue
            lap_now, ct = feed.lap(t), feed.clip_time(t)
            with self._lock:
                ending = [aid for aid, m in self._replay_meta.items()
                          if m["cam"] == cam and (m["lap"] < lap_now or ct >= m["clear_at"])]
            for aid in ending:
                self.clear_alert(aid)
            # A first tick, a restart or a long stall would otherwise raise
            # every alert of the skipped stretch at once.
            if prev is None or t < prev or t - prev > 30.0:
                continue
            for lap in range(feed.lap(prev), lap_now + 1):
                lo = prev - lap * feed.duration if lap == feed.lap(prev) else -1e-9
                hi = t - lap * feed.duration if lap == lap_now else feed.duration
                for raise_at, a in feed.schedule:
                    if lo < raise_at <= hi:
                        self._raise_replay(feed, a, raise_at, lap)

    def _raise_replay(self, feed: ReplayFeed, a, raise_at: float, lap: int) -> None:
        # The evidence as it stood when the alert fired, not the whole run's.
        part = feed.df[feed.df.window_end_s <= raise_at + 1e-6]
        now_view = next((x for x in bn.detect(part, feed.cam)
                         if x.zone_id == a.zone_id and x.kind == a.kind
                         and abs(x.start_s - a.start_s) < 1e-6), a)
        aid = self.raise_alert(now_view.to_dict(), "replay")
        with self._lock:
            self._replay_meta[aid] = {"cam": feed.cam, "lap": lap,
                                      "clear_at": a.end_s + WINDOW_S}

    # ----------------------------------------------------------- snapshot

    def snapshot(self) -> dict:
        """The whole live picture. Cached briefly: every connected viewer asks
        for it once a second."""
        now_m = time.monotonic()
        with self._lock:
            at, snap = self._snap
            if snap is not None and now_m - at < 0.4:
                return snap
        self._sync_feeds()
        t = self.now()
        cams = []
        for cam, feed in list(self.feeds.items()):
            if self._detecting(cam):
                cams.append(self.detect.camera_state())
                continue
            ct = feed.clip_time(t)
            zones = feed.zones_at(ct)
            ci = _mean_ci(zones)
            cams.append({**feed.info, "source": "replay", "clip_t": ct,
                         "duration": feed.duration, "lap": feed.lap(t), "ci": ci,
                         "los": T.los_of(ci), "zones": zones, "spark": feed.spark(ct)})
        with self._lock:
            active = [dict(a) for a in self.active.values()]
        snap = {"t": t, "playing": self.playing, "speed": self.speed,
                "speeds": list(SPEEDS), "cameras": cams,
                "active": sorted(active, key=lambda a: ({"critical": 0, "warning": 1}
                                                        .get(a["severity"], 2),
                                                        a["raised_at"])),
                "detect": self.detect.view() if self.detect else None,
                "risks": self._risks(t)}
        with self._lock:
            self._snap = (now_m, snap)
        return snap

    def _risks(self, t: float) -> list[dict]:
        """Rising-risk forecasts over the last two minutes of every feed."""
        now_m = time.monotonic()
        if now_m - self._risk[0] < 5.0:
            return self._risk[1]
        frames = []
        for cam, feed in list(self.feeds.items()):
            h = (self.detect.history_df() if self._detecting(cam)
                 else feed.history(feed.clip_time(t)))
            if not h.empty:
                frames.append(h)
        out = []
        if frames:
            import charts as C
            fcs = fc.forecast_all(pd.concat(frames, ignore_index=True), horizon_s=60.0)
            for f in fc.rising_risks(fcs, threshold=RISK_THRESHOLD)[:6]:
                out.append({"camera_id": f.camera_id,
                            "camera_short": C.short_name(f.camera_id),
                            "zone_label": f.zone_label, "current": f.current,
                            "predicted": f.predicted, "horizon_s": f.horizon_s})
        self._risk = (now_m, out)
        return out

    # ------------------------------------------------------------- frames

    def frame_source(self, cam: str) -> _Publisher:
        """The publisher a viewer of `cam` should read: the detection session
        while one runs on that camera, the shared replay frames otherwise."""
        if self._detecting(cam):
            return self.detect
        if self.feed(cam) is None:
            raise KeyError(cam)
        with self._lock:
            src = self.frames.get(cam)
            if src is None:
                src = self.frames[cam] = ReplayFrames(self, cam)
        return src          # the caller acquires it, which starts it

    # ------------------------------------------------------------ detect

    def start_detect(self, cam: str) -> DetectSession:
        with self._lock:
            d = self.detect
            if d is not None and d.running:
                if d.cam == cam:
                    return d
                raise RuntimeError(f"Live detection is already running on "
                                   f"{d.info['short']}. Stop it first.")
            d = self.detect = DetectSession(self, cam)
            self._snap = (0.0, None)
        # Replayed alerts for this camera give way to detected ones.
        with self._lock:
            mine = [aid for aid, m in self._replay_meta.items() if m["cam"] == cam]
        for aid in mine:
            self.clear_alert(aid)
        self.touch()
        d.start()
        return d

    def stop_detect(self) -> DetectSession | None:
        d = self.detect
        if d is not None and d.running:
            d.stop()
        with self._lock:
            self._snap = (0.0, None)
        return d


HUB = LiveHub()
