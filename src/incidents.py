"""Incident workflow: what the operator did about each alert.

The alerts themselves are derived - `bottleneck_detect` recomputes them from
the metrics on every request, and the live monitor raises them as footage
plays - so they carry no state of their own. This module is the state: an
append-only history per alert of who acknowledged it, who resolved it, and
what they wrote, persisted to `outputs/incidents.json`.

An alert with no entry here is open. Each entry keeps a snapshot of the alert
as it was when first acted on, so the log still reads correctly after the
alert itself has gone - a live alert from yesterday's session, or a recorded
one whose camera has since been re-measured.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import config as cfg

LOG_PATH: Path = cfg.OUTPUT_DIR / "incidents.json"

# action -> resulting status, and the statuses it may be taken from. A live
# alert that cleared before anyone acted is shown as "cleared" (see annotate)
# but has no log entry, so in the log it is still "open" and can be
# acknowledged or resolved after the fact.
TRANSITIONS = {
    "acknowledge": ("acknowledged", {"open"}),
    "resolve": ("resolved", {"open", "acknowledged"}),
    "reopen": ("open", {"acknowledged", "resolved"}),
}
STATUSES = ("open", "acknowledged", "resolved")
MAX_NOTE = 500

_lock = threading.Lock()


def alert_id(a: dict) -> str:
    """Stable id for a recorded alert: the same condition on the same clip
    keeps its id across requests and restarts."""
    return f"{a['camera_id']}|{a['zone_id']}|{a['kind']}|{float(a['start_s']):.1f}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> dict:
    """The whole log. A missing or unreadable file is an empty log, never an
    error: losing the workflow state must not take the alert views down."""
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("incidents"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "incidents": {}}


def act(incident_id: str, action: str, by: str, note: str = "",
        snapshot: dict | None = None) -> dict:
    """Apply one action. Raises ValueError for an unknown action or one that
    does not apply in the incident's current status."""
    if action not in TRANSITIONS:
        raise ValueError(f"action must be one of {sorted(TRANSITIONS)}")
    status, allowed_from = TRANSITIONS[action]
    note = (note or "").strip()[:MAX_NOTE]
    with _lock:
        log = load()
        rec = log["incidents"].get(incident_id) or {"status": "open", "history": []}
        if rec["status"] not in allowed_from:
            raise ValueError(f"cannot {action} an incident that is {rec['status']}")
        rec["status"] = status
        rec["history"].append({"at": _now(), "action": action, "by": by, "note": note})
        if snapshot and "alert" not in rec:
            rec["alert"] = snapshot
        log["incidents"][incident_id] = rec
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_json_atomic(LOG_PATH, log)
        return rec


def annotate(alerts: list[dict]) -> list[dict]:
    """Attach status and history to alert dicts (each must carry an "id"),
    and append logged incidents whose alert is no longer being raised.

    A live alert that cleared on its own before anyone acted on it is
    "cleared", not open: the condition is over and nobody owes it anything.
    Without that, a replay left running re-raises the same conditions every
    lap and the open count - and the badge built on it - grows without bound.
    """
    log = load()["incidents"]
    out, seen = [], set()
    for a in alerts:
        rec = log.get(a["id"], {})
        status = rec.get("status", "open")
        if not rec and a.get("source") in ("replay", "detect") and a.get("active") is False:
            status = "cleared"
        out.append({**a, "status": status,
                    "history": rec.get("history", []), "current": True})
        seen.add(a["id"])
    for iid, rec in log.items():
        if iid in seen or "alert" not in rec:
            continue
        out.append({**rec["alert"], "id": iid, "status": rec["status"],
                    "history": rec["history"], "current": False})
    return out


def counts(items: list[dict]) -> dict:
    """Open/acknowledged/resolved/cleared totals, and open criticals for the
    badge."""
    c = {s: sum(1 for i in items if i["status"] == s) for s in (*STATUSES, "cleared")}
    c["open_critical"] = sum(1 for i in items
                             if i["status"] == "open" and i.get("severity") == "critical"
                             and i.get("current", True))
    return c


def to_rows(items: list[dict]) -> list[dict]:
    """Flat rows for the CSV export: one per alert, last action inline."""
    rows = []
    for i in items:
        last = i["history"][-1] if i.get("history") else {}
        rows.append({
            "id": i["id"], "source": i.get("source", "recorded"),
            "status": i["status"], "severity": i.get("severity"),
            "camera": i.get("camera_name"), "zone": i.get("zone_label"),
            "kind": i.get("kind"), "headline": i.get("headline"),
            "clip_start_s": i.get("start_s"), "clip_end_s": i.get("end_s"),
            "raised_at": i.get("raised_at", ""),
            "last_action": last.get("action", ""), "last_by": last.get("by", ""),
            "last_at": last.get("at", ""), "last_note": last.get("note", ""),
            "actions": len(i.get("history", [])),
        })
    return rows
