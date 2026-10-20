"""Non-blocking in-memory accounting for outbound Ceres calls."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging
import re
import threading
import time
from urllib.parse import parse_qsl, urlsplit

_LOG = logging.getLogger("ceres.outbound_meter")
_RING_SIZE = 512
_DYNAMIC_SEGMENT = re.compile(r"^(?:[0-9a-f]{12,}|[A-Za-z0-9_-]{30,})$")
_DEED_SEGMENT = re.compile(r"^I-\d+-[0-9A-Za-z]+$")
_ACTIVE_ACTION: ContextVar["_Action | None"] = ContextVar("ceres_outbound_action", default=None)


@dataclass
class _Action:
    trigger: str
    player: str | None
    cap: int | None
    calls: int = 0
    context_token: object | None = None
    memo_hits: int = 0
    previous_player_action: "_Action | None" = None
    ended: bool = False


@dataclass(frozen=True)
class _Call:
    host: str
    path_template: str
    trigger: str
    player: str | None
    started: float
    action: _Action | None


def _template(path: str) -> str:
    try:
        parsed = urlsplit(path)
        segments = [":deed" if _DEED_SEGMENT.fullmatch(part) else ":id" if _DYNAMIC_SEGMENT.fullmatch(part) else part for part in parsed.path.split("/")]
        for index, part in enumerate(segments[:-1]):
            if part in {"history", "fragment_history", "details", "rewardactions"} and index + 1 < len(segments):
                segments[index + 1] = ":player" if part == "history" else ":deed" if part == "rewardactions" else ":id"
        route = "/".join(segments)
        keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
        return route + ("?" + "&".join(keys) if keys else "")
    except Exception:
        return "<unknown>"


class OutboundMeter:
    """A bounded, thread-safe call ring that never participates in request decisions."""

    def __init__(self, ring_size: int = _RING_SIZE):
        self._lock = threading.Lock()
        self._records = deque(maxlen=max(1, int(ring_size)))
        self._calls = 0
        self._memo_hits = 0
        self._active_by_player: dict[str, _Action] = {}

    @contextmanager
    def action(self, trigger: str, player: str | None = None, cap: int | None = None):
        """Group calls under one user action and log that action's count on exit."""
        action = self.begin_action(trigger, player, cap)
        try:
            yield action
        finally:
            self.end_action(action)

    def begin_action(self, trigger: str, player: str | None = None, cap: int | None = None) -> _Action:
        """Start an action context for subsequent records on this thread/task."""
        action = _Action(str(trigger), player, cap)
        try:
            action.context_token = _ACTIVE_ACTION.set(action)
            if player:
                with self._lock:
                    action.previous_player_action = self._active_by_player.get(player)
                    self._active_by_player[player] = action
        except Exception:
            pass
        return action

    def end_action(self, action: _Action) -> None:
        """End an action context and emit its single INFO call-count line."""
        try:
            if action.context_token is not None:
                _ACTIVE_ACTION.reset(action.context_token)
            if action.player:
                with self._lock:
                    action.ended = True
                    if self._active_by_player.get(action.player) is action:
                        # Overlapping actions may end out of order; never restore one that already ended.
                        previous = action.previous_player_action
                        while previous is not None and previous.ended:
                            previous = previous.previous_player_action
                        if previous is None:
                            self._active_by_player.pop(action.player, None)
                        else:
                            self._active_by_player[action.player] = previous
            _LOG.info("outbound action=%s player=%s calls=%d", action.trigger, action.player, action.calls)
        except Exception:
            pass

    def begin_call(self, host: str, path: str, player: str | None = None) -> _Call:
        """Capture call metadata before transport dispatch without affecting dispatch."""
        try:
            active = _ACTIVE_ACTION.get()
            if player is None:
                query_player = dict(parse_qsl(urlsplit(path).query)).get("player")
                player = query_player if isinstance(query_player, str) else None
            if active is None:
                with self._lock:
                    active = self._active_by_player.get(player) if player else None
                    if active is None and len(self._active_by_player) == 1:
                        active = next(iter(self._active_by_player.values()))
            if player is None and active is not None:
                player = active.player
            return _Call(str(host), _template(str(path)), active.trigger if active else "outbound",
                         player, time.perf_counter(), active)
        except Exception:
            return _Call("<unknown>", "<unknown>", "outbound", None, time.perf_counter(), None)

    def record(self, call: _Call) -> None:
        """Record one completed or failed outbound attempt; exceptions are swallowed."""
        try:
            elapsed = max(0.0, time.perf_counter() - call.started)
            row = {"host": call.host, "path_template": call.path_template, "trigger": call.trigger,
                   "player": call.player, "duration": elapsed}
            with self._lock:
                self._calls += 1
                self._records.append(row)
                if call.action is not None:
                    call.action.calls += 1
        except Exception:
            pass

    def memo_hit(self, count: int = 1) -> None:
        """Count an in-memory memo hit for the response cost snapshot."""
        try:
            hits = max(0, int(count))
            with self._lock:
                self._memo_hits += hits
                active = _ACTIVE_ACTION.get()
                if active is not None:
                    active.memo_hits += hits
        except Exception:
            pass

    def snapshot(self, cap: int | None = None) -> dict:
        """Return response cost totals and a copy of the bounded call ring."""
        try:
            active = _ACTIVE_ACTION.get()
            with self._lock:
                return {"cost": {"calls": active.calls if active is not None else self._calls,
                                  "cap": active.cap if cap is None and active is not None else cap,
                                  "memo_hits": active.memo_hits if active is not None else self._memo_hits},
                        "records": [dict(row) for row in self._records]}
        except Exception:
            return {"cost": {"calls": 0, "cap": cap, "memo_hits": 0}, "records": []}


meter = OutboundMeter()


def action(trigger: str, player: str | None = None, cap: int | None = None):
    return meter.action(trigger, player, cap)


def begin_action(trigger: str, player: str | None = None, cap: int | None = None) -> _Action:
    return meter.begin_action(trigger, player, cap)


def end_action(action_context: _Action) -> None:
    meter.end_action(action_context)


def begin_call(host: str, path: str, player: str | None = None) -> _Call:
    return meter.begin_call(host, path, player)


def record(call: _Call) -> None:
    meter.record(call)


def snapshot(cap: int | None = None) -> dict:
    return meter.snapshot(cap)
