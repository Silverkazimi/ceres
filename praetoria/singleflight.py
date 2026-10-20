"""Share identical upstream reads only while their fetch is pending."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable


@dataclass
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    value: Any = None
    error: BaseException | None = None


_lock = threading.Lock()
_pending: dict[Hashable, _Pending] = {}


def singleflight(key: Hashable, fetch: Callable[[], Any]) -> Any:
    with _lock:
        pending = _pending.get(key)
        leader = pending is None
        if leader:
            pending = _Pending()
            _pending[key] = pending

    if not leader:
        pending.event.wait()
        if pending.error is not None:
            raise pending.error
        return pending.value

    try:
        value = fetch()
    except BaseException as exc:
        with _lock:
            _pending.pop(key, None)
            pending.error = exc
            pending.event.set()
        raise
    with _lock:
        _pending.pop(key, None)
        pending.value = value
        pending.event.set()
    return value
