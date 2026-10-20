"""Server-side carry-forward of pre-combine card records, keyed by the combine op's
own nonce (`n`).

`/api/op-verify`'s domain check needs each combine input's PRE-combine BCX to predict
the survivor and confirm the merge landed. But by the time verification runs, the
destroyed copies are already gone from the live collection, and any capture taken
between the combine and the verify replaces the board snapshot with POST-combine data
-- so deriving `expected_records` from `capture.latest_snapshot()` at verify time can
never work by construction (the defect this module fixes).

The one moment this evidence is still knowable is `/api/combine/build`, which already
reads the live collection to resolve every input's BCX and predict the survivor. This
module persists exactly that read, keyed by the op's nonce -- the same `n` field the
client already round-trips verbatim through Keychain and back into its `/api/op-verify`
call (`fides_cards/ops.py::build_combine_op` always sets it; the client re-sends the
same `operation` object it got back from `/api/combine/build`) -- so `/api/op-verify`
can use it instead of re-deriving from a snapshot that may already be stale or absent.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import secrets
import fcntl
import time
from pathlib import Path
from typing import Any

# A04/A12: prepared/resolved cache entries have a bounded lifetime.
# Unresolved evidence is retained; new plans also carry authenticated recovery data.
_MAX_AGE_SECONDS = 24 * 60 * 60


def _dir(config: Any) -> Path:
    path = Path(config.root) / "combine_evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(config: Any, nonce: str) -> Path:
    safe = "".join(ch for ch in str(nonce) if ch.isalnum())
    if not safe:
        raise ValueError("nonce must contain at least one alphanumeric character")
    return _dir(config) / f"{safe}.json"


def save(config: Any, nonce: str, *, account: str, uids: list[str], records: list[dict[str, Any]]) -> None:
    """Persist the pre-combine records for `uids`, read live at build time. Best-effort
    -- a write failure here must never block returning the built operation; it degrades
    verification back to the snapshot fallback, never to a crash."""
    try:
        path = _path(config, nonce)
    except ValueError:
        return
    sweep(config)
    payload = {"account": account, "uids": list(uids), "records": records, "saved_at": time.time(), "lifecycle": "prepared"}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def load(config: Any, nonce: str, *, account: str, unresolved: bool = False) -> list[dict[str, Any]] | None:
    """Return the carried-forward pre-combine records for `nonce`, or `None` if
    unavailable, expired, unreadable, or recorded for a different account. Callers
    must treat `None` exactly like "no evidence" -- degrade to could-not-verify,
    never raise past it into a false confirm."""
    try:
        path = _path(config, nonce)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("account") != account:
        return None
    try:
        saved_at = float(payload.get("saved_at", 0))
    except (TypeError, ValueError):
        return None
    # A04: a known unresolved plan is never aged out at verification.
    # Legacy files without a lifecycle are retained conservatively by the sweep.
    if unresolved:
        payload["lifecycle"] = "unresolved"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    if not unresolved and time.time() - saved_at > _MAX_AGE_SECONDS:
        discard(config, nonce)
        return None
    records = payload.get("records")
    return records if isinstance(records, list) else None


def discard(config: Any, nonce: str) -> None:
    """Remove a nonce's evidence file once it is no longer needed (a confirmed
    verify). Never raises -- this is best-effort cleanup, not a correctness path."""
    try:
        _path(config, nonce).unlink(missing_ok=True)
    except (ValueError, OSError):
        pass


def load_plan_result(config: Any, nonce: str, *, account: str, tx_id: str, operations: list) -> dict | None:
    """ : resume a known verified combine without another capture."""
    try:
        payload = json.loads(_path(config, nonce).with_suffix('.verified.json').read_text(encoding='utf-8'))
        if (payload.get('account') == account and payload.get('tx_id') == tx_id
                and payload.get('operations') == operations
                and time.time() - float(payload.get('saved_at', 0)) <= _MAX_AGE_SECONDS):
            return payload.get('result')
    except (OSError, ValueError, TypeError):
        pass
    return None


def save_plan_result(config: Any, nonce: str, *, account: str, tx_id: str, operations: list, result: dict) -> None:
    """ : bounded result evidence, using the existing private evidence directory."""
    # A12: verification terminates input-evidence retention.
    source = _path(config, nonce)
    if source.exists():
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload.update(lifecycle="verified", saved_at=time.time())
        temporary = source.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(source)
    sweep(config)
    path = _path(config, nonce).with_suffix('.verified.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(account=account, tx_id=tx_id, operations=operations,
                                         result=result, saved_at=time.time(), lifecycle="verified")), encoding='utf-8')
    temporary.replace(path)


# A04/A12: the unresolved plan owns its immutable recovery evidence.
# The server stores only a service key and the existing short-lived cache, never
# an additional user document. The envelope survives cache expiry/restarts.
def _recovery_key(config):
    path = Path(config.root) / "combine-recovery.key"
    with path.open("a+b") as handle:
        os.chmod(path, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        key = handle.read()
        if not key:
            key = secrets.token_bytes(32)
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
        return key


def recovery_envelope(config, nonce, *, account, records, expected):
    payload = dict(account=account, nonce=nonce, records=records, expected=expected)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return dict(payload=payload, payload_json=data, seal=hmac.new(_recovery_key(config), data.encode(), hashlib.sha256).hexdigest())


def recover(config, envelope, *, account, nonce, uids):
    try:
        payload = envelope["payload"]
        # B01: JS JSON round trips rewrite 1000.0 as 1000. Keep signed bytes.
        data = envelope.get("payload_json") or json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if json.loads(data) != payload:
            return None
        seal = hmac.new(_recovery_key(config), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal, envelope["seal"]) or payload["account"] != account or payload["nonce"] != nonce:
            return None
        records = payload["records"]
        if sorted(row["uid"] for row in records) != sorted(uids):
            return None
        # The authenticated build expectation is immutable; do not derive it
        # again from a post-combine snapshot or changed card metadata.
        if payload["expected"]["bcx"] != sum(row["bcx"] for row in records):
            return None
        return records
    except (ValueError, TypeError, KeyError, OSError, RecursionError, AttributeError):
        return None


def sweep(config):
    """ A12: expire operational caches; unresolved plans retain envelopes."""
    for path in _dir(config).glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if path.name.endswith('.verified.json') or payload.get("lifecycle") in {"prepared", "verified", "failed", "finished"}:
                expired = time.time() - float(payload.get("saved_at", 0)) > _MAX_AGE_SECONDS
            else:
                expired = False  # unresolved or legacy-unknown; never sacrifice recovery
            if expired:
                path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
