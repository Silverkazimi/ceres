""": one `/transactions/lookup` read of a KNOWN transaction, with the game's "not found" answer kept apart.

For a transaction it has no record of, the game answers HTTP 200 with `{"error": "Transaction [<id>] not found.",
"error_code": 1}` (measured 2026-09-29). The Praetoria transport raises that body error as an `UpstreamError`, so every
route that checked `if not trx_info: -> not_found` answered 502 instead and its "not found" branch was never reached.
Here that one answer becomes `None` (no record); every other failure is raised unchanged.
"""
from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urlencode

from praetoria.errors import UpstreamError
from praetoria.transport import API_ORIGIN

_NOT_FOUND = re.compile(r"Transaction \[[0-9A-Za-z-]+\] not found", re.IGNORECASE)


def lookup_trx_info(get_json: Callable[[str], Any], tx: str) -> dict | None:
    """The game's trx_info for `tx`, or None when the game has no record of it."""
    try:
        response = get_json(API_ORIGIN + "/transactions/lookup?" + urlencode({"trx_id": tx}))
    except UpstreamError as exc:
        if _NOT_FOUND.search(str(exc)):
            return None
        raise
    data = response.data if isinstance(response.data, dict) else {}
    info = data.get("trx_info")
    return info if isinstance(info, dict) and info else None
