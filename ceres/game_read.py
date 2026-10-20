"""One metered, paced game GET and the per-action outbound budget for the harvest window's own result check.

Moved unchanged from land_activity / receipts_api when the Receipts feature was removed ().
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from http.client import HTTPSConnection, HTTPException

from . import fixture_upstream, outbound_meter
from .hive_rpc import ActivityReadError

ACTION_MAX = 25
PLAYER_MINUTE_MAX = 60
MIN_DISPATCH_SECONDS = 0.300
_LOCK = threading.RLock()
_MINUTE_CALLS: dict[str, deque[float]] = {}
_pace_lock = threading.Lock()
_last_dispatch = [0.0]


class BudgetExceeded(RuntimeError):
    """An outbound dispatch would exceed the user action's hard call cap."""


class MinuteExceeded(RuntimeError):
    """An outbound dispatch would exceed this player's rolling minute ceiling."""


class OutboundBudget:
    """Reserve each outbound call before transport dispatch; makes zero calls itself."""

    def __init__(self, player: str, max_calls: int):
        self.player = player
        self.max_calls = max_calls
        self.calls = 0

    def spend(self) -> None:
        """Reserve one call against action and rolling-minute caps; zero outbound calls."""
        now = time.monotonic()
        with _LOCK:
            if self.calls >= self.max_calls:
                raise BudgetExceeded("receipt action call cap reached")
            minute = _MINUTE_CALLS.setdefault(self.player, deque())
            while minute and minute[0] <= now - 60:
                minute.popleft()
            if len(minute) >= PLAYER_MINUTE_MAX:
                raise MinuteExceeded("receipt player minute cap reached")
            minute.append(now)
            self.calls += 1


def _get(host, path, *, timeout=5, max_response_bytes=2_000_000):
    with _pace_lock:
        wait = _last_dispatch[0] + MIN_DISPATCH_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_dispatch[0] = time.monotonic()
    meter_call = outbound_meter.begin_call(host, path)
    connection = fixture_upstream.connection(host) if fixture_upstream.enabled() else HTTPSConnection(host, timeout=timeout)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        raw = response.read(max_response_bytes + 1)
        if response.status != 200 or len(raw) > max_response_bytes:
            raise ActivityReadError("Game evidence is unavailable")
        return json.loads(raw)
    except (OSError, ValueError, HTTPException) as exc:
        raise ActivityReadError("Game evidence is unavailable") from exc
    finally:
        outbound_meter.record(meter_call)
        connection.close()
