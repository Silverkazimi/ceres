"""Per-client limits, the "already running" answer, and the operator pause (v1.0 C3, spec -c3).

The one limiter for routes that call the game's APIs. A client is ``request.client.host``, which uvicorn
resolves from ``X-Forwarded-For`` only for a trusted proxy (loopback), so a client can't pick its own key by
sending that header. Counters are sliding windows kept on ``app.state`` (a fresh app has fresh counters).

* :func:`limit` answers 429 with ``Retry-After`` when a bucket is full. ``CERES_CLIENT_LIMITS=off`` disables it.
* :func:`capture_gate` decides a board read while another runs: the same client supersedes (as before), another
  client asking for the same account gets 409 (the page then joins the running read), and another client asking
  for another account gets 429. The running read is never killed by a stranger.
* The operator pause: ``CERES_PAUSED=1`` or a ``PAUSED`` file in the data root (checked at most once a second, so it
  works without a restart). While paused every ``/api/*`` route except health, dev/watch and csp-report answers
  503, the running capture child is stopped, and history passes are cancelled.

Makes no outbound call.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

ENV_SWITCH = "CERES_CLIENT_LIMITS"
PAUSE_ENV = "CERES_PAUSED"
PAUSE_FILE = "PAUSED"
PAUSE_ALLOWED = ("/api/health", "/api/dev/watch", "/api/csp-report")
PAUSE_DETAIL = "Ceres is paused by its operator."
GLOBAL = "*"

# bucket -> ((max_events, window_seconds), ...); the maintainer's normal use stays far below every one.
BUCKETS: dict[str, tuple[tuple[int, float], ...]] = {
    "capture": ((20, 60.0), (200, 3600.0)),
    "capture_full": ((3, 3600.0),),
    "capture_global": ((60, 600.0),),
    "estimate": ((20, 60.0),),
    "plot_read": ((60, 60.0),),
    "preview": ((30, 60.0),),
    "wallet_lp": ((30, 60.0),),
    "verify": ((240, 60.0),),     # S1 / L1-05: by-id transaction lookups (DEC, plot lock, inclusion)
    "routing": ((45, 60.0),),     # S1 / L1-05: routing state and preview (own bucket: harvest preflight uses "preview")
    "csp_report": ((30, 60.0),),
    "auth": ((5, 60.0), (20, 3600.0)),   # login challenges and proofs (own bucket; refused before any state or Hive read)
    "space_read": ((120, 60.0),),         # the signed-in player's own space
    "space_write": ((60, 60.0),),
    "market": ((30, 60.0), (600, 3600.0)),  # Market mode reads on entry and on each settled filter change (debounced 400 ms; 60 s upstream cache)
}


def client_key(request: Request) -> str:
    return request.client.host if request.client else "-"


def limits_enabled() -> bool:
    return os.environ.get(ENV_SWITCH, "on").strip().lower() not in ("off", "0", "false", "no")


MAX_TRACKED_KEYS = 4096
HARD_MAX_KEYS = 16384
SWEEP_SECONDS = 30.0


class Limiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[tuple[str, str, int], deque[float]] = {}
        self._swept = float('-inf')
        self.capture_owner: dict[str, Any] | None = None

    def hit(self, bucket: str, key: str, now: float | None = None) -> float | None:
        """Record one event; return None when allowed, else the seconds until a slot frees (nothing recorded)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._events) > MAX_TRACKED_KEYS and now - self._swept >= SWEEP_SECONDS:  # S10 / B4-05, / B5-07: amortised, per-bucket expiry
                self._swept = now
                for stale in [k for k, q in self._events.items() if not q or q[-1] <= now - BUCKETS[k[0]][k[2]][1]]:
                    del self._events[stale]
                overflow = len(self._events) - HARD_MAX_KEYS
                if overflow > 0:  # still too many live clients: forget the ones that have been quiet longest
                    for oldest in sorted(self._events, key=lambda k: self._events[k][-1] if self._events[k] else 0.0)[:overflow]:
                        del self._events[oldest]
            waits = []
            for index, (limit, window) in enumerate(BUCKETS[bucket]):
                events = self._events.setdefault((bucket, key, index), deque())
                while events and events[0] <= now - window:
                    events.popleft()
                if len(events) >= limit:
                    waits.append(events[0] + window - now)
            if waits:
                return max(1.0, max(waits))
            for index, _rule in enumerate(BUCKETS[bucket]):
                self._events[(bucket, key, index)].append(now)
            return None


def limiter(request: Request) -> Limiter:
    state = request.app.state
    if not hasattr(state, "client_limiter"):
        state.client_limiter = Limiter()
    return state.client_limiter


def _too_many(wait: float, detail: str = "Too many requests; please wait a moment.") -> HTTPException:
    return HTTPException(status_code=429, detail=detail, headers={"Retry-After": str(int(wait + 0.999))})


def _served(request: Any) -> bool:
    """Only an HTTP request that came through the app is limited; a direct in-process call (tests, tools) is not."""
    # an in-process caller may hand over a stand-in that carries app.state (routes read their config
    # there), so "has app.state" alone let such calls into the limiter. A served request is a Starlette Request.
    return isinstance(request, Request) and hasattr(request.app, "state")


def limit(request: Request, bucket: str) -> None:
    """Spend one event of ``bucket`` for this client, or raise 429. Call before any upstream work."""
    if not limits_enabled() or not _served(request):
        return
    wait = limiter(request).hit(bucket, client_key(request))
    if wait is not None:
        raise _too_many(wait)


def capture_gate(request: Request, player: str, mode: str) -> JSONResponse | None:
    """None when this client may start (or supersede) a board read; otherwise the answer to send instead."""
    from . import capture as capture_mod

    if not _served(request):
        return None
    config = request.app.state.ceres_config
    state = limiter(request)
    owner = state.capture_owner
    client = client_key(request)
    if owner is not None and owner["client"] != client:
        status = capture_mod.freshness(config, owner["player"])
        if status.state in ("running", "queued"):
            if owner["player"] == player:
                return JSONResponse(status_code=409, content={"state": "running", "mode": owner["mode"],
                                                              "started_at": status.started_at})
            estimate = getattr(status, "estimated_seconds", None)
            wait = max(5, int(estimate or 5))
            return JSONResponse(status_code=429, headers={"Retry-After": str(wait)},
                                content={"detail": "Another board read is running on this server. Try again shortly."})
    if limits_enabled():
        for bucket in ("capture", "capture_full") if mode == "full" else ("capture",):
            wait = state.hit(bucket, client)
            if wait is not None:
                raise _too_many(wait)
        wait = state.hit("capture_global", GLOBAL)
        if wait is not None:
            raise _too_many(wait, "This server is busy with board reads; please wait a moment.")
    return None


def record_capture_owner(request: Request, player: str, mode: str) -> None:
    if not _served(request):
        return
    limiter(request).capture_owner = {"client": client_key(request), "player": player, "mode": mode}


# operator pause

_pause_cache: dict[str, tuple[float, bool]] = {}
_pause_lock = threading.Lock()


def paused(root: Path) -> bool:
    if os.environ.get(PAUSE_ENV, "").strip() == "1":
        return True
    key = str(root)
    now = time.monotonic()
    with _pause_lock:
        cached = _pause_cache.get(key)
        if cached is not None and now - cached[0] < 1.0:
            return cached[1]
        value = (Path(root) / PAUSE_FILE).exists()
        _pause_cache[key] = (now, value)
        return value


def _enter_pause(app) -> None:
    """Stop the running board read, once per transition into the pause."""
    from . import capture as capture_mod

    capture_mod._supervisor.shutdown()


async def pause_middleware(request: Request, call_next):
    app = request.app
    is_paused = paused(app.state.ceres_config.root)
    was_paused = getattr(app.state, "operator_paused", False)
    app.state.operator_paused = is_paused
    if is_paused and not was_paused:
        _enter_pause(app)
    path = request.url.path
    if is_paused and path.startswith("/api/") and not path.startswith(PAUSE_ALLOWED):
        return JSONResponse(status_code=503, content={"detail": PAUSE_DETAIL})
    return await call_next(request)
