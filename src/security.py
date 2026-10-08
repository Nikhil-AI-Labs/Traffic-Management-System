"""Operator sign-in and rate limits for the JSON API.

Reading the dashboard needs nothing: every figure on it is derived from
committed data, and a control room wants any screen to be able to show it.
Changing state does - applying a calibration rewrites a camera's metrics,
uploads and analyses spend minutes of CPU, acknowledging an incident puts a
name in the log. Those endpoints depend on `operator`.

    STI_OPERATOR_TOKEN=<secret> python src/api.py

With the variable unset the API runs open, which is the right default for a
single operator workstation and for the test suite. Set it for any instance
reachable by someone who should only be watching - a hosted demo in front of
judges, a wall screen in a shared room.

One shared token rather than accounts: the deployment this was written for is
one control room, and a user database would be the largest attack surface in
the project for no gain. The operator's *name* travels with each request
(`X-Operator-Name`) so the incident log can still say who acknowledged what.

Rate limits apply whether or not sign-in is required. They are per client
address and per bucket, so a stuck "Analyse" button cannot queue fifty
detector jobs, and a script cannot pin the CPU with simulator runs.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from urllib.parse import unquote

from fastapi import HTTPException, Request

OPERATOR_TOKEN: str | None = os.environ.get("STI_OPERATOR_TOKEN", "").strip() or None
RATE_LIMITS_ON: bool = os.environ.get("STI_RATE_LIMIT", "1") != "0"


def auth_required() -> bool:
    return OPERATOR_TOKEN is not None


def _supplied_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return request.headers.get("x-operator-token", "").strip()


def token_ok(supplied: str) -> bool:
    if OPERATOR_TOKEN is None:
        return True
    # Constant-time comparison: a plain == leaks how many leading characters
    # matched through its timing.
    return bool(supplied) and hmac.compare_digest(supplied.encode(), OPERATOR_TOKEN.encode())


def operator(request: Request) -> str:
    """FastAPI dependency: the request must come from a signed-in operator.

    Returns the operator's display name ("operator" when none was given),
    for the incident log.
    """
    if not token_ok(_supplied_token(request)):
        raise HTTPException(401, "Operator sign-in is required for this action.",
                            headers={"WWW-Authenticate": "Bearer"})
    return operator_name(request)


def operator_name(request: Request) -> str:
    # Percent-encoded by the client: header values must be Latin-1, and an
    # operator in Surat may well type their name in Gujarati or Hindi.
    name = unquote(request.headers.get("x-operator-name", "")).strip()
    # Printable, short, and nothing that could break a CSV row or an HTML page.
    name = "".join(ch for ch in name if ch.isprintable() and ch not in "<>\"'`,;")[:40]
    return name or "operator"


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------

class _Buckets:
    """Token buckets keyed by (client, bucket). In memory: one process."""

    def __init__(self):
        self._b: dict[tuple[str, str], tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: tuple[str, str], per_min: float, burst: int) -> float:
        """Spend one token. Returns 0 if allowed, else seconds to wait."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._b.get(key, (float(burst), now))
            tokens = min(float(burst), tokens + (now - last) * per_min / 60.0)
            if tokens < 1.0:
                self._b[key] = (tokens, now)
                return (1.0 - tokens) * 60.0 / per_min
            self._b[key] = (tokens - 1.0, now)
            if len(self._b) > 10_000:          # a scan of many addresses
                self._b.clear()
            return 0.0

    def reset(self) -> None:
        with self._lock:
            self._b.clear()


BUCKETS = _Buckets()


def limit(bucket: str, per_min: float, burst: int):
    """FastAPI dependency factory: at most `per_min` calls a minute per client,
    with up to `burst` back to back."""

    def dep(request: Request) -> None:
        if not RATE_LIMITS_ON:
            return
        client = request.client.host if request.client else "unknown"
        wait = BUCKETS.take((client, bucket), per_min, burst)
        if wait > 0:
            raise HTTPException(
                429, f"Too many requests — try again in {wait:.0f} s.",
                headers={"Retry-After": str(max(1, round(wait)))})

    return dep
