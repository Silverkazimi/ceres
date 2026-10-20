""" (2026-09-14): stream-parse `/cards/collection/<player>`.

The whale response is 85 MB / 65,422 cards (player0006). The old path inflated it into one
bytes object, `loads`-ed it into 65k dicts of ~60 keys, then copied every dict once more to drop
the keys nobody reads. This module decodes the body chunk by chunk, parses one card object at a
time and keeps only the keys a consumer reads, so neither the 85 MB body nor the 65k full dicts
ever exist at once. Every row is kept (the card-pool sidecar, the upgrade panel and the picker
read every row -- blocked ones included, to explain WHY a copy is blocked); only keys are dropped.

Dropped keys, measured 2026-09-14 across ceres/, praetoria/, fides*/, mnemosyne/ (Python, JS,
HTML), tests excluded: 's list (no reader anywhere) plus `set_id`,
`stake_plot`, `stake_region` (no reader anywhere). `collection_power`, `delegated_to_display_name`
and `delegation_tx` are read by `praetoria/query.py`, which fetches its own full copy and is not
narrowed; they stay out of the board's rows since no board code reads them.

The row boundary trick: a card object is flat, so it ends at the first `}` after its `{` at which
the slice is valid JSON -- a `}` inside a string leaves the string unterminated and the slice
fails to decode, so the scan extends to the next `}`. JSON objects are prefix-free, so the first
slice that decodes IS the object. Bodies that do not carry a top-level `"cards": [` array (an
error envelope, an unexpected shape) fall back to one whole-body parse.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from . import fastjson

# collection-card fields no code path reads (checked across ceres/, praetoria
# fides*/, mnemosyne/ -- Python and the board's JS). Kept out of the in-memory card pool.
UNUSED_COLLECTION_FIELDS = (
    "buy_price", "cancel_date", "cancel_player", "cancel_tx", "card_deposited_date",
    "created_block", "created_date", "created_tx", "last_buy_currency", "last_buy_price",
    "last_used_block", "last_used_date",
    "last_used_player", "lock_days", "market_created_date", "market_listing_status",
    "market_listing_type", "mint", "next_rental_payment", "renewal_date", "renewal_tx",
    "rental_date", "rental_days", "wagon_uid", "xp",
)
# also unread anywhere (same sweep, 2026-09-14).
UNREAD_COLLECTION_FIELDS = ("set_id", "stake_plot", "stake_region")
DROPPED_COLLECTION_FIELDS = frozenset(UNUSED_COLLECTION_FIELDS) | frozenset(UNREAD_COLLECTION_FIELDS)

_CARDS_KEY = b'"cards"'
_SKIP = frozenset(b" \t\r\n,")
_CHUNK_BYTES = 1 << 20
_HEAD_LIMIT = 4 << 20


def narrow_card(card: Any) -> Any:
    """A card dict without the dropped keys, built at its final size (: popping keys
    from a 60-key dict leaves it at its 60-key table). Non-dicts pass through untouched so the
    schema check downstream still sees them."""
    if not isinstance(card, dict):
        return card
    return {key: value for key, value in card.items() if key not in DROPPED_COLLECTION_FIELDS}


def narrow_collection(payload: Any) -> Any:
    """The already-parsed fallback: same rows, same keys dropped."""
    if isinstance(payload, dict) and isinstance(payload.get("cards"), list):
        out = dict(payload)
        out["cards"] = [narrow_card(card) for card in payload["cards"]]
        return out
    return payload


def _drain(stream: Any, buf: bytes, chunk_bytes: int) -> bytes:
    while True:
        chunk = stream.read(chunk_bytes)
        if not chunk:
            return buf
        buf += chunk


def _fallback(stream: Any, buf: bytes, chunk_bytes: int, row: Callable[[Any], Any]) -> Any:
    payload = fastjson.loads(_drain(stream, buf, chunk_bytes))
    if isinstance(payload, dict) and isinstance(payload.get("cards"), list):
        payload["cards"] = [row(card) for card in payload["cards"]]
    return payload


def parse_collection_stream(stream: Any, row: Callable[[Any], Any] = narrow_card, chunk_bytes: int = _CHUNK_BYTES) -> Any:
    """Parse a collection body from `stream.read(size)` calls, applying `row` to each card as it
    is decoded. Returns the envelope dict with `cards` replaced by the kept rows. Raises
    `json.JSONDecodeError` (as the whole-body path would) on malformed input."""
    buf = b""
    # 1. the head: everything up to `"cards"` ws `:` ws `[`
    while True:
        at = buf.find(_CARDS_KEY)
        if at != -1:
            break
        if len(buf) > _HEAD_LIMIT:
            return _fallback(stream, buf, chunk_bytes, row)
        chunk = stream.read(chunk_bytes)
        if not chunk:
            return _fallback(stream, buf, chunk_bytes, row)
        buf += chunk
    pos = at + len(_CARDS_KEY)
    while len(buf) - pos < 64:
        chunk = stream.read(chunk_bytes)
        if not chunk:
            break
        buf += chunk
    while pos < len(buf) and buf[pos] in b" \t\r\n":
        pos += 1
    if pos >= len(buf) or buf[pos] != 0x3A:
        return _fallback(stream, buf, chunk_bytes, row)
    pos += 1
    while pos < len(buf) and buf[pos] in b" \t\r\n":
        pos += 1
    if pos >= len(buf) or buf[pos] != 0x5B:
        return _fallback(stream, buf, chunk_bytes, row)
    head = buf[:at]
    buf = buf[pos + 1:]
    pos = 0
    rows: list[Any] = []
    eof = False
    # 2. the array: one object at a time
    while True:
        while True:
            while pos < len(buf) and buf[pos] in _SKIP:
                pos += 1
            if pos < len(buf):
                break
            if eof:
                raise json.JSONDecodeError("collection array is not terminated", "", 0)
            chunk = stream.read(chunk_bytes)
            if not chunk:
                eof = True
            buf, pos = buf[pos:] + chunk, 0
        first = buf[pos]
        if first == 0x5D:  # ']' -- the tail closes the envelope
            tail = _drain(stream, buf[pos + 1:], chunk_bytes)
            envelope = fastjson.loads(head + b'"cards":[]' + tail)
            if not isinstance(envelope, dict):
                raise json.JSONDecodeError("collection envelope is not an object", "", 0)
            envelope["cards"] = rows
            return envelope
        if first != 0x7B:
            raise json.JSONDecodeError("collection array holds a non-object", "", pos)
        end = pos
        while True:
            end = buf.find(b"}", end + 1)
            if end == -1:
                if eof:
                    raise json.JSONDecodeError("collection card is not terminated", "", pos)
                end = len(buf) - 1
                chunk = stream.read(chunk_bytes)
                if not chunk:
                    eof = True
                buf += chunk
                continue
            try:
                card = fastjson.loads(buf[pos:end + 1])
            except json.JSONDecodeError:
                continue  # a '}' inside a string or a nested object: extend to the next one
            rows.append(row(card))
            pos = end + 1
            break
        if pos >= chunk_bytes:
            buf, pos = buf[pos:], 0
