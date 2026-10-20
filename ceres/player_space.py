""": a player's own server space for their ROOT routing setup and their staged changes (reached only through the Posting-key login).

Each document is stored whole, with a revision, and is never expired by any cleanup. A save must name the revision it was based on (compare-and-swap under a
per-account lock); a stale save is refused with the current document, so a second device or tab can never silently overwrite newer work. Before a document is
replaced its previous revision is kept (the last HISTORY_KEEP). Size limits only refuse NEW saves; they never trim what is stored.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .security import resolve_under, validate_player

KINDS = ("routing", "staged")
SCHEMA_VERSION = 1
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_DEPTH = 32
HISTORY_KEEP = 10


class SpaceError(Exception):
    """A refused request (status carries the HTTP meaning)."""

    def __init__(self, message: str, status: int = 422, current: dict | None = None):
        super().__init__(message)
        self.status, self.current = status, current


def _folder(root: Path, account: str) -> Path:
    base = root / "player-space"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder = resolve_under(base, base / validate_player(account))
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


@contextmanager
def locked(root: Path, account: str):
    folder = _folder(root, account)
    with (folder / ".lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield folder
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _depth(value: Any, level: int = 0) -> int:
    if level > MAX_DEPTH:
        return level
    if isinstance(value, dict):
        return max([_depth(item, level + 1) for item in value.values()] or [level + 1])
    if isinstance(value, list):
        return max([_depth(item, level + 1) for item in value] or [level + 1])
    return level


# B01: raw recovery records are immutable, validated separately from display numbers.
def _validate_chain_recovery(envelope):
    import re
    from .combine_chain import canonical_fact
    if not isinstance(envelope, dict) or set(envelope) - {"payload", "seal", "payload_json"} or not isinstance(envelope.get("seal"), str) or not re.fullmatch(r"[a-f0-9]{64}", envelope["seal"]):
        raise SpaceError("invalid recovery envelope")
    payload = envelope.get("payload")
    if (not isinstance(payload, dict) or not isinstance(payload.get("account"), str)
            or not isinstance(payload.get("nonce"), str) or not isinstance(payload.get("expected"), dict)
            or not isinstance(payload.get("records"), list) or not 0 < len(payload["records"]) <= 200):
        raise SpaceError("invalid recovery payload")
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"level", "bcx", "bcx_max", "cc", "base_pp", "land_base_pp", "card_detail_id", "edition", "slot_index"} and item is not None and canonical_fact(item) is None:
                    raise SpaceError("invalid recovery PP")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and (len(value) > 65536 or "<" in value or ">" in value):
            raise SpaceError("invalid recovery text")
        elif type(value) in (int, float) and canonical_fact(value) is None:
            raise SpaceError("invalid recovery number")
    for row in payload["records"]:
        if not isinstance(row, dict) or not isinstance(row.get("uid"), str) or not row["uid"] or not canonical_fact(row.get("bcx")):
            raise SpaceError("invalid recovery card")
    if "payload_json" in envelope:
        try:
            encoded = json.loads(envelope["payload_json"])
        except (TypeError, ValueError, RecursionError):
            raise SpaceError("invalid recovery bytes") from None
        if encoded != payload:
            raise SpaceError("recovery bytes do not match payload")
    visit(payload)


# A03: saved data is untrusted, including nested numeric display fields.
def _validate_chain_values(plan):
    numeric = {"level", "bcx", "bcx_max", "cc", "base_pp", "land_base_pp", "card_detail_id", "edition", "slot_index"}
    objects = {"card", "expected", "result", "combine", "unstake", "origin", "source", "asset", "problem"}
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "recovery" and item is not None:
                    _validate_chain_recovery(item)
                    continue
                if key in numeric and item is not None and (type(item) not in (int, float) or item < 0 or item > 9007199254740991 or not math.isfinite(item)):
                    raise SpaceError("invalid finite chain number")
                if key in objects and item is not None and not isinstance(item, dict):
                    raise SpaceError("invalid nested chain record")
                if key in {"tx_id", "nonce", "uid", "name", "image", "signed_at", "confirmed_at", "observed_at"} and item is not None and not isinstance(item, str):
                    raise SpaceError("invalid chain text")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, float) and not math.isfinite(value):
            raise SpaceError("invalid finite chain number")
    if _depth(plan) > MAX_DEPTH:
        raise SpaceError("the chain plan is nested too deeply")
    visit(plan)
    result = plan.get("result")
    if result is not None and (plan.get("problem") or {}).get("code") != "combine_mismatch":
        if any(type(result.get(key)) not in (int, float) or not math.isfinite(result[key]) for key in ("bcx", "base_pp", "level")):
            raise SpaceError("incomplete combine result")
    combine = plan.get("combine")
    if combine is not None:
        if not isinstance(combine.get("nonce"), str) or not combine["nonce"]:
            raise SpaceError("invalid combine attempt")
        op = combine.get("operation")
        if not isinstance(op, list) or len(op) != 2 or op[0] != "custom_json" or not isinstance(op[1], dict):
            raise SpaceError("invalid combine operation")


def validate_payload(kind: str, payload: Any) -> None:
    if kind not in KINDS:
        raise SpaceError("unknown document", 404)
    if not isinstance(payload, dict):
        raise SpaceError("the document must be a JSON object")
    entries = payload.get("rules" if kind == "routing" else "plans")
    if entries is not None and (not isinstance(entries, (list, dict)) or len(entries) > MAX_ENTRIES):
        raise SpaceError(f"at most {MAX_ENTRIES} entries per document")
    if kind == "staged" and "chains" in payload:
        chains = payload["chains"]
        if not isinstance(chains, dict) or sum(not (isinstance(plan, dict) and plan.get("tombstone") is True) for plan in chains.values()) > 200:
            raise SpaceError("chains must be an object with at most 200 plans")
        states = {"planned", "cooling", "ready", "combine_signed", "combined", "done", "cancelled"}
        for plan_id, plan in chains.items():
            if not isinstance(plan_id, str) or not plan_id.startswith("cc-") or not isinstance(plan, dict):
                raise SpaceError("invalid chain plan")
            if plan.get("version") != 2 or plan.get("state") not in states:
                raise SpaceError("invalid chain plan version or state")
            if not all(isinstance(plan.get(key), str) for key in ("created_at", "updated_at")):
                raise SpaceError("chain plan timestamps are required")
            if plan.get("tombstone") is True:
                if plan["state"] not in {"done", "cancelled"} or plan.get("inputs") != [] or plan.get("combine"):
                    raise SpaceError("invalid terminal tombstone")
                continue
            _validate_chain_values(plan)
            inputs = plan.get("inputs")
            if not isinstance(inputs, list) or len(inputs) > 200:
                raise SpaceError("chain inputs must be an array of at most 200 copies")
            seen: set[str] = set()
            for item in inputs:
                if not isinstance(item, dict) or not isinstance(item.get("uid"), str) or not item["uid"] or item["uid"] in seen:
                    raise SpaceError("chain inputs need unique Card IDs")
                seen.add(item["uid"])
                if item.get("status") not in {"active", "left_out", "unavailable"} or item.get("kind") not in {"staked", "cooling", "free"}:
                    raise SpaceError("invalid chain input state")
                if not isinstance(item.get("cc"), (int, float)) or isinstance(item["cc"], bool) or item["cc"] <= 0:
                    raise SpaceError("invalid chain input CC")
                if item.get("unstake") is not None and (not isinstance(item["unstake"], dict) or not isinstance(item["unstake"].get("tx_id"), str)):
                    raise SpaceError("invalid signed unstake record")
            if plan.get("state") != "cancelled" and (not isinstance(plan.get("origin"), dict) or not isinstance(plan.get("card"), dict) or not isinstance(plan.get("expected"), dict)):
                raise SpaceError("chain plan is missing its origin, card or expected result")
    if _depth(payload) > MAX_DEPTH:
        raise SpaceError("the document is nested too deeply")
    if len(json.dumps(payload, separators=(",", ":"))) > MAX_DOCUMENT_BYTES:
        raise SpaceError("the document is too large", 413)


def etag(document: dict) -> str:
    return f'"{document["generation"]}:{document["revision"]}"'


def _path(folder: Path, kind: str) -> Path:
    return folder / f"{kind}.json"


def _read_file(path: Path) -> dict | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise SpaceError("the stored document cannot be read; nothing was changed", 500) from exc
    if not isinstance(document, dict) or not isinstance(document.get("revision"), int):
        raise SpaceError("the stored document cannot be read; nothing was changed", 500)
    return document


def _atomic_write(path: Path, value: Any) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read(root: Path, account: str, kind: str) -> dict | None:
    if kind not in KINDS:
        raise SpaceError("unknown document", 404)
    with locked(root, account) as folder:
        return _read_file(_path(folder, kind))


def write(root: Path, account: str, kind: str, payload: Any, *, if_match: str | None, create_only: bool) -> dict:
    """Save one document. `if_match` is the ETag of the version the caller edited; `create_only` means "only if none exists". A mismatch is refused (412)
    with the current document; nothing is ever merged or overwritten silently."""
    validate_payload(kind, payload)
    with locked(root, account) as folder:
        path = _path(folder, kind)
        current = _read_file(path)
        if current is None:
            if not create_only and if_match is not None:
                raise SpaceError("there is no saved document to update", 412)
            revision, generation = 1, secrets.token_hex(8)
        else:
            if create_only:
                raise SpaceError("a saved document already exists", 412, current)
            if if_match is None:
                raise SpaceError("send the version you edited (If-Match)", 428, current)
            if if_match != etag(current):
                raise SpaceError("the saved document changed since you opened it", 412, current)
            revision, generation = current["revision"] + 1, current["generation"]
            history = folder / "history" / kind
            history.mkdir(parents=True, exist_ok=True, mode=0o700)
            _atomic_write(history / f"{current['revision']:08d}.json", current)   # the previous revision is on disk BEFORE the replacement
            for old in sorted(history.glob("*.json"))[:-HISTORY_KEEP]:
                old.unlink()
        document = {"schema_version": SCHEMA_VERSION, "account": account, "kind": kind, "revision": revision, "generation": generation,
                    "saved_at": int(time.time()), "payload_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(), "payload": payload}
        _atomic_write(path, document)
        return document


def export_all(root: Path, account: str) -> dict:
    with locked(root, account) as folder:
        return {"account": account, "documents": {kind: _read_file(_path(folder, kind)) for kind in KINDS}}


def delete_all(root: Path, account: str) -> int:
    """Delete every document of the account and all their older versions; returns how many files went. Under the same lock as every write, so a save in flight
    either finishes first or finds nothing to update (its ETag belongs to a generation that no longer exists)."""
    import shutil
    removed = 0
    with locked(root, account) as folder:
        for kind in KINDS:
            path = _path(folder, kind)
            if path.exists():
                path.unlink()
                removed += 1
        history = folder / "history"
        if history.is_dir() and not history.is_symlink():
            removed += sum(1 for item in history.rglob("*.json") if item.is_file())
            shutil.rmtree(history)
    return removed
