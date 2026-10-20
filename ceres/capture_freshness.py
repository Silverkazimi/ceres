"""Capture-freshness primitive shared by every combine-then-restake gate.

Split out of the now-deleted planning ledger (`ceres/planning.py`, , removed
per the maintainer's worker-table rebuild spec) -- the ledger itself (entries, events,
`/api/planning`) is gone, but the live Cooling/restake flow still needs to refuse a
stake staged from a snapshot older than the combine it follows. This module is that
one comparison and nothing else: no ledger, no scheduler, no stored game quantity.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ValidationError(ValueError):
    """A capture-freshness comparison could not be made (missing or unparseable timestamp)."""


def _parse_timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def capture_is_newer_than(capture_at: Any, reference_at: Any) -> bool:
    """Is `capture_at` strictly newer than `reference_at`? The shared primitive behind
    every combine-freshness gate in this project -- a caller that knows its own combine
    timestamp (e.g. an idle-copy combine staged straight from a worker tile) calls this
    directly rather than re-deriving the comparison."""
    try:
        return _parse_timestamp(capture_at) > _parse_timestamp(reference_at)
    except (TypeError, ValueError) as exc:
        raise ValidationError("cannot verify capture freshness") from exc
