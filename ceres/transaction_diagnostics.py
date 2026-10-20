"""Bounded transaction outcomes in the existing rotated Ceres service log."""
from __future__ import annotations

import json
import logging
import re

from . import __version__

_logger = logging.getLogger("ceres.transaction")
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_TX = re.compile(r"[0-9a-fA-F]{40}")


def record_verification(*, account, tx_id, attempt, elapsed_ms, operation_count, outcome=None, error=False, family="operation", operation_index=None):
    # Persist only an allowlist; operation bodies, upstream text and wallet data never enter this log.
    def token(value):
        return value if isinstance(value, str) and _TOKEN.fullmatch(value) else "unknown"

    try:
        outcome = outcome if isinstance(outcome, dict) else {}
        record = {
            "event": "verification_error" if error else "verification_result",
            "version": __version__, "account": token(account), "family": token(family),
            "operation_index": operation_index if type(operation_index) is int and operation_index >= 0 else None,
            "tx_id": tx_id.lower() if isinstance(tx_id, str) and _TX.fullmatch(tx_id) else None,
            "attempt": attempt if type(attempt) is int and 0 <= attempt <= 1000000 else None,
            "elapsed_ms": max(0, round(elapsed_ms)),
            "operation_count": min(1000, max(0, operation_count)),
            "state": token(outcome.get("state")),
            "refresh_error_count": len(outcome.get("refresh_errors") or []),
        }
        for key in ("chain", "game", "domain"):
            value = outcome.get(key)
            record[key] = token(value.get("status")) if isinstance(value, dict) else "unknown"
        _logger.log(logging.WARNING if error else logging.INFO, "%s", json.dumps(record, separators=(",", ":")))
    except Exception:
        # Diagnostics must never turn an accepted transaction into a failed request.
        return
