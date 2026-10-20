""" harvest previews and attempts -- durable, account-bound records (plan §8.1/§8.3).

Previews live in memory (short-lived, 10-minute retention); attempts are written atomically
to disk BEFORE any wallet handoff. Files sit under `<ceres root>/harvest_runs/<player>/`,
mode 600, unique temp names; no keys or tokens are ever stored.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .security import resolve_under, validate_player

PREVIEW_TTL_SECONDS = 600
_MAX_PREVIEWS = 64
_previews: dict[str, dict[str, Any]] = {}
_preview_lock = threading.Lock()  # S10 / B5-06: short-held, apart from the durable attempt lock
_lock = threading.Lock()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _runs_dir(root: Path, player: str) -> Path:
    player = validate_player(player)
    base = root / "harvest_runs"
    base.mkdir(parents=True, exist_ok=True)
    path = resolve_under(base, base / player)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write(path: Path, value: Any) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_preview(player: str, preview: dict[str, Any]) -> dict[str, Any]:
    preview_id = secrets.token_urlsafe(18)
    record = dict(preview, preview_id=preview_id, player=player, created=time.time())
    record["preview_digest"] = digest({k: v for k, v in record.items() if k not in ("preview_id", "created", "preview_digest")})
    with _preview_lock:
        now = time.time()
        for key in [k for k, v in _previews.items() if now - v["created"] > PREVIEW_TTL_SECONDS]:
            _previews.pop(key, None)
        while len(_previews) >= _MAX_PREVIEWS:
            _previews.pop(next(iter(_previews)))
        _previews[preview_id] = record
    return record


def load_preview(player: str, preview_id: str, preview_digest: str) -> dict[str, Any] | None:
    with _preview_lock:
        record = _previews.get(preview_id)
    if not record or record["player"] != player or record["preview_digest"] != preview_digest:
        return None
    if time.time() - record["created"] > PREVIEW_TTL_SECONDS:
        return None
    return record


def _attempt_path(root: Path, player: str, attempt_id: str) -> Path:
    if not isinstance(attempt_id, str) or not (8 <= len(attempt_id) <= 64) or not attempt_id.replace("-", "").isalnum():
        raise ValueError("invalid attempt_id")
    return _runs_dir(root, player) / f"attempt-{attempt_id}.json"


def begin_attempt(root: Path, player: str, preview: dict[str, Any], chunk_index: int, attempt_id: str) -> dict[str, Any]:
    with _lock:
        return _begin_attempt(root, player, preview, chunk_index, attempt_id)


def _begin_attempt(root: Path, player: str, preview: dict[str, Any], chunk_index: int, attempt_id: str) -> dict[str, Any]:
    path = _attempt_path(root, player, attempt_id)
    if type(chunk_index) is not int or not 0 <= chunk_index < len(preview["chunks"]):
        raise ValueError("chunk_index out of range")
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if (existing.get("player") != player or existing.get("preview_id") != preview["preview_id"]
                or existing.get("preview_digest") != preview["preview_digest"] or existing.get("chunk_index") != chunk_index):
            raise ValueError("attempt_id already belongs to a different harvest")
        return existing
    chunk = preview["chunks"][chunk_index]
    record = {"attempt_id": attempt_id, "player": player, "preview_id": preview["preview_id"],
              "preview_digest": preview["preview_digest"], "chunk_index": chunk_index,
              "operations": chunk["operations"], "members": chunk["members"],
              "state": "awaiting_keychain", "created": time.time(), "tx_id": None}
    _atomic_write(path, record)
    return record


def update_attempt(root: Path, player: str, attempt_id: str, **fields: Any) -> dict[str, Any] | None:
    with _lock:
        record = load_attempt(root, player, attempt_id)
        if record is None:
            return None
        if record.get("tx_id"):
            if fields.get("tx_id") not in (None, record["tx_id"]):
                raise ValueError("attempt already has a different transaction")
            fields.pop("tx_id", None)
            if fields.get("state") == "rejected_before_send":
                fields.pop("state", None)
        if record.get("state") in ("reconciled", "game_refused") and fields.get("state") in ("submitted", "included"):
            fields.pop("state", None)
        if fields.get("state") == "signed_queued" and record.get("state") != "awaiting_keychain":
            fields.pop("state", None)
        if record.get("state") in ("reconciled", "game_refused") and fields.get("state") == "failed":
            fields.pop("state", None)
        record.update(fields)
        _atomic_write(_attempt_path(root, player, attempt_id), record)
        return record


def load_attempt(root: Path, player: str, attempt_id: str) -> dict[str, Any] | None:
    try:
        path = _attempt_path(root, player, attempt_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    return record if record.get("player") == player else None
