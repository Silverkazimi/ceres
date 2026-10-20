""" (2026-09-07): one JSON codec for the large payloads.

The stdlib encoder is where a whale account spent its time: 10 s to parse a 45 MB collection,
2.4 s to dump the 38 MB snapshot on EVERY plot open, ~10 s to dump the 186 MB card-pool
sidecar on every board load. `orjson` does the same work 5-10x faster. This module is the
only place that decides which codec runs, and it falls back to the stdlib for anything orjson
refuses (a type it does not know), so behaviour never changes -- only speed.

Kept out of `source_hash` on purpose: that value is a stable fingerprint and the two encoders
do not print every float identically; a fingerprint keeps the encoder it was born with.
"""

from __future__ import annotations

import json
from typing import Any

try:  # pragma: no cover - exercised by whichever environment runs this
    import orjson as _orjson
except Exception:  # orjson missing: stdlib everywhere, same results, slower
    _orjson = None


def dumps(value: Any, *, sort_keys: bool = False) -> bytes:
    """Compact UTF-8 JSON bytes, equivalent to
    `json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=sort_keys).encode()`."""
    if _orjson is not None:
        option = _orjson.OPT_NON_STR_KEYS
        if sort_keys:
            option |= _orjson.OPT_SORT_KEYS
        try:
            return _orjson.dumps(value, option=option)
        except TypeError:
            pass
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=sort_keys).encode("utf-8")


def _is_allocation_failure(exc: BaseException) -> bool:
    """orjson raises its JSONDecodeError for an allocation failure too; only the message
    tells the two apart ()."""
    return "not enough memory" in str(exc).lower()


def loads(data: bytes | bytearray | memoryview | str) -> Any:
    """`json.loads` with the fast decoder when it is available. Raises `json.JSONDecodeError`
    on invalid input either way, so callers keep their existing `except` clauses -- and
    `MemoryError` when the decoder could not get its parse buffer (), which no
    `except JSONDecodeError` clause swallows. This is the only decode path (there is no
    file-object `load`); `load_snapshot` and the caches all read bytes and call this."""
    if _orjson is not None:
        try:
            return _orjson.loads(data)
        except _orjson.JSONDecodeError as exc:  # subclass of json.JSONDecodeError and ValueError
            if _is_allocation_failure(exc):
                # orjson reports a failed parse-buffer allocation as a decode error
                # ("Not enough memory to allocate buffer for parsing"). Under the capture
                # worker's RLIMIT_DATA cap that happened on a 98.7 MB snapshot, and the caller
                # mislabelled a perfectly good file as corrupt. An OOM is not invalid JSON.
                raise MemoryError(str(exc)) from exc
            raise json.JSONDecodeError(str(exc), data if isinstance(data, str) else "", 0) from exc
    if isinstance(data, (bytes, bytearray, memoryview)):
        data = bytes(data).decode("utf-8")
    return json.loads(data)
