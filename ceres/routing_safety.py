"""Silent, transaction-id-only duplicate-spend reservations for retired routing runs."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from . import harvest_evidence
from .security import resolve_under, validate_player

SAFETY_DIR = "routing_safety"
TX_EXPIRY_SECONDS = 3900          # Hive refuses a transaction whose expiration is over an hour ahead (+ margin, as everywhere else)
MAX_RECORDS = 200


def _folder(root: Path, player: str) -> Path:
    base = root / SAFETY_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base


def _path(root: Path, player: str) -> Path:
    base = _folder(root, player)
    return resolve_under(base, base / f"{validate_player(player)}.json")


@contextmanager
def locked(root: Path, player: str):
    base = _folder(root, player)
    with (base / ".lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load(root: Path, player: str) -> list[dict]:
    path = _path(root, player)
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return []   # an unreadable file never blocks routing; it is kept on disk (never rewritten blindly) by the next successful save only
    if not isinstance(records, list):
        return []
    clean = []
    for item in records:
        if not isinstance(item, dict):
            continue
        tx_id = item.get("tx_id")
        expires = item.get("expires_at")
        attempts = item.get("attempt_ids")
        if (isinstance(tx_id, str) and re.fullmatch(r"[0-9a-f]{40}", tx_id)
                and isinstance(expires, (int, float)) and isinstance(attempts, list)
                and len(attempts) <= 5000 and all(isinstance(value, str) for value in attempts)):
            clean.append({"tx_id": tx_id, "expires_at": expires, "attempt_ids": attempts})
    return clean


def _save(root: Path, player: str, records: list[dict]) -> None:
    path = _path(root, player)
    if records:
        harvest_evidence._atomic_write(path, records[-MAX_RECORDS:])
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def records_from_run(run: dict) -> list[dict]:
    """The tiny records of every bundle of a run that reached a wallet and is not settled."""
    out = []
    for bundle_id, status in (run.get("bundles") or {}).items():
        if status.get("state") not in {"claimed", "attached", "checking", "outcome_unknown"}:
            continue
        claim = status.get("claim") or {}
        started = status.get("attached_at") or claim.get("claimed_at") or run.get("updated") or time.time()
        out.append({"id": f"{run.get('run_id')}:{bundle_id}", "player": run.get("player"), "run_id": run.get("run_id"), "bundle_id": bundle_id,
                    "attempt_ids": list(run.get("harvest_attempt_ids") or []), "tx_id": status.get("tx_id"),
                    "operations": claim.get("operations") or [], "started": started, "expires_at": started + TX_EXPIRY_SECONDS,
                    "token_hash": run.get("token_hash")})
    return out


def sync_run(root: Path, player: str, run: dict) -> None:
    """Retain only known transaction IDs, expiry, and the harvest receipts the run could spend."""
    with locked(root, player):
        records = _load(root, player)
        settled = {status.get("tx_id", "").lower() for status in (run.get("bundles") or {}).values()
                   if status.get("state") in {"confirmed", "refused"} and status.get("tx_id")}
        records = [item for item in records if str(item.get("tx_id") or "").lower() not in settled]
        for status in (run.get("bundles") or {}).values():
            if status.get("state") not in {"claimed", "attached", "checking", "outcome_unknown"}:
                continue
            tx_id = status.get("tx_id")
            if not isinstance(tx_id, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", tx_id):
                continue
            started = status.get("attached_at") or time.time()
            item = {"tx_id": tx_id.lower(), "expires_at": started + TX_EXPIRY_SECONDS,
                    "attempt_ids": list(run.get("harvest_attempt_ids") or [])}
            records = [r for r in records if r.get("tx_id") != item["tx_id"]]
            records.append(item)
        _save(root, player, records)


def add(root: Path, player: str, run: dict) -> int:
    before = len(_load(root, player))
    sync_run(root, player, run)
    return max(0, len(_load(root, player)) - before)


def unresolved(root: Path, player: str) -> list[dict]:
    return _load(root, player)


def public(record: dict, now: float | None = None) -> dict:
    """What the page may show: no operations."""
    now = time.time() if now is None else now
    return {"id": record.get("id"), "tx_id": record.get("tx_id"), "started": record.get("started"), "expires_at": record.get("expires_at"),
            "expired": now > float(record.get("expires_at") or 0), "attempt_ids": list(record.get("attempt_ids") or [])}


def overlapping(root: Path, player: str, attempt_ids: list[str]) -> list[dict]:
    wanted = set(attempt_ids or [])
    return [r for r in _load(root, player) if wanted.intersection(r.get("attempt_ids") or [])]


def filter_attempts(root: Path, player: str, attempt_ids: list[str], lookup: Callable[[str], Any], *, now: float | None = None) -> list[str]:
    """Silently reserve unsettled receipts; settle inclusion or discard expired absence by known-id lookup."""
    now = time.time() if now is None else now
    with locked(root, player):
        requested = set(attempt_ids or [])
        records = _load(root, player)
        unrelated = [item for item in records if not requested.intersection(item.get("attempt_ids") or [])]
        relevant = [item for item in records if requested.intersection(item.get("attempt_ids") or [])]
        keep, reserved = [], set()
        for item in relevant:
            included = False
            settled_without_spend = False
            try:
                response = lookup(item["tx_id"])
                info = response.get("trx_info") if isinstance(response, dict) else None
                result = info.get("result") if isinstance(info, dict) else None
                result = json.loads(result) if isinstance(result, str) else result
                matches = (isinstance(info, dict) and info.get("id") == item["tx_id"]
                           and info.get("player") == player and info.get("type") == "land_operation")
                inner = result.get("result") if isinstance(result, dict) else None
                if isinstance(inner, str):
                    inner = json.loads(inner)
                if matches and (info.get("success") is False or
                                (isinstance(result, dict) and result.get("success") is False) or
                                (isinstance(inner, dict) and inner.get("success") is False)):
                    settled_without_spend = True
                included = (matches and info.get("success") is True and isinstance(result, dict)
                            and result.get("success") is True and isinstance(inner, dict)
                            and inner.get("success") is True)
            except Exception:
                pass
            if included:
                mark_routed(root, player, item.get("attempt_ids") or [])
                reserved.update(item.get("attempt_ids") or [])
                continue
            if settled_without_spend:
                continue
            if now > float(item.get("expires_at") or 0):
                continue
            keep.append(item)
            reserved.update(item.get("attempt_ids") or [])
        _save(root, player, unrelated + keep)
    return [identifier for identifier in attempt_ids if identifier not in reserved]


def mark_routed(root: Path, player: str, attempt_ids: list[str]) -> None:
    """The receipts of these harvest attempts are consumed by a routing transaction that reached the chain: they are never routed again."""
    for attempt_id in attempt_ids or []:
        try:
            harvest_evidence.update_attempt(root, player, attempt_id, routed_at=time.time())
        except (ValueError, OSError):
            continue   # an attempt that is gone has nothing left to protect


def is_routed(root: Path, player: str, attempt_ids: list[str]) -> bool:
    for attempt_id in attempt_ids or []:
        record = harvest_evidence.load_attempt(root, player, attempt_id)
        if record and record.get("routed_at"):
            return True
    return False


def check(root: Path, player: str, record_id: str, **_: Any) -> dict:
    """: there is no record to settle (older pages may still ask)."""
    return {"state": "gone"}


def purge_all(root: Path) -> int:
    """Legacy startup hook: current tiny transaction reservations must survive restart."""
    return 0
