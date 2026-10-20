"""Atomic, token-addressed storage for the lean board projection."""

from __future__ import annotations

import hashlib
import gzip
import json
import logging
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from praetoria.cache import JsonCache

from . import SCHEMA_BOARD
from . import sps_rate
from .board import project_board
from .notices import format_notice

logger = logging.getLogger("ceres.board_artifact")
ARTIFACT_SCHEMA = "ceres.board-artifact.v1"
REPRESENTATION = "lean-v1"
_PLAYER_RE = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_TOKEN_RE = re.compile(r"^[0-9a-f]+-[0-9]+$")
_identity_locks: dict[tuple[str, str], threading.RLock] = {}
_identity_locks_guard = threading.Lock()
_identity_cache: dict[tuple[str, str], "PublicationIdentity"] = {}
_artifact_cache: dict[tuple[str, str], "ArtifactPayload"] = {}
_artifact_cache_guard = threading.Lock()


@dataclass(frozen=True)
class ArtifactPayload:
    """: the web process serves the artifact BYTES. `meta` carries the identity fields
    the status header needs (`generated_at`, `coverage.assets`, `notice_count`); the board
    itself is never parsed on a request thread."""
    token: str
    raw: bytes
    gzip: bytes
    meta: dict[str, Any]


@dataclass(frozen=True)
class PublicationIdentity:
    player: str
    token: str
    write_counter: int
    epoch: str
    snapshot_path: str | None
    snapshot_mtime_ns: int | None
    snapshot_size: int | None
    snapshot_generated_at: str | None
    details_mtime_ns: int | None
    details_size: int | None
    complete: bool
    artifact_name: str
    schema: str = SCHEMA_BOARD
    representation: str = REPRESENTATION
    # an interim identity names a compact artifact built right after the bulk read
    # BEFORE the collection sweep -- no snapshot exists for it yet (the snapshot fields are
    # null). The final publish after cards/merge supersedes it by token.
    interim: bool = False
    artifact_sha256: str | None = None
    artifact_size: int | None = None
    artifact_gzip_size: int | None = None
    artifact_generated_at: str | None = None
    artifact_assets: dict[str, Any] | None = None
    artifact_notice_count: int | None = None


def artifact_meta(identity: "PublicationIdentity") -> dict[str, Any]:
    """The board-shaped subset `freshness_from_board` / the status header read ()."""
    return {
        "generated_at": identity.artifact_generated_at or identity.snapshot_generated_at,
        "coverage": {"assets": dict(identity.artifact_assets)} if isinstance(identity.artifact_assets, dict) else {},
        "notice_count": identity.artifact_notice_count or 0,
    }


def _meta_from_board(board: dict[str, Any]) -> dict[str, Any]:
    coverage = board.get("coverage")
    assets = coverage.get("assets") if isinstance(coverage, dict) else None
    if not isinstance(assets, dict):
        assets = coverage if isinstance(coverage, dict) else {}
    notices = board.get("notices")
    return {
        "artifact_generated_at": board.get("generated_at"),
        "artifact_assets": dict(assets),
        "artifact_notice_count": len(notices) if isinstance(notices, list) else 0,
    }


def _validate_player(player: str) -> str:
    player = str(player)
    if not _PLAYER_RE.fullmatch(player):
        raise ValueError("invalid player for board artifact")
    return player


def _lock_for(snapshots_dir: Path, player: str) -> threading.RLock:
    key = (str(snapshots_dir.resolve()), _validate_player(player))
    with _identity_locks_guard:
        return _identity_locks.setdefault(key, threading.RLock())


def publication_lock(snapshots_dir: Path, player: str) -> threading.RLock:
    return _lock_for(Path(snapshots_dir), player)


def index_path(snapshots_dir: Path, player: str) -> Path:
    return Path(snapshots_dir) / f"board-{_validate_player(player)}-index.json"


def counter_path(snapshots_dir: Path, player: str) -> Path:
    return Path(snapshots_dir) / f"board-{_validate_player(player)}-counter.json"


def artifact_path(snapshots_dir: Path, player: str, token: str) -> Path:
    player = _validate_player(player)
    if not _TOKEN_RE.fullmatch(str(token)):
        raise ValueError("invalid board artifact token")
    return Path(snapshots_dir) / f"board-{player}-{token}.json"


def gzip_artifact_path(snapshots_dir: Path, player: str, token: str) -> Path:
    return artifact_path(snapshots_dir, player, token).with_name(artifact_path(snapshots_dir, player, token).name + ".gz")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _cache_key(snapshots_dir: Path, player: str) -> tuple[str, str]:
    return str(Path(snapshots_dir).resolve()), _validate_player(player)


def _remember_identity(identity: PublicationIdentity | None, snapshots_dir: Path | None = None) -> None:
    if identity is None or snapshots_dir is None:
        return
    key = _cache_key(snapshots_dir, identity.player)
    with _artifact_cache_guard:
        previous = _identity_cache.get(key)
        _identity_cache[key] = identity
        if previous is not None and previous.token != identity.token:
            _artifact_cache.pop(key, None)


def cached_identity(snapshots_dir: Path, player: str) -> PublicationIdentity | None:
    with _artifact_cache_guard:
        return _identity_cache.get(_cache_key(snapshots_dir, player))


def cached_artifact(snapshots_dir: Path, player: str) -> ArtifactPayload | None:
    with _artifact_cache_guard:
        return _artifact_cache.get(_cache_key(snapshots_dir, player))


def details_identity() -> tuple[int | None, int | None]:
    try:
        stat = (JsonCache().root / "card_details.json").stat()
    except OSError:
        return None, None
    return stat.st_mtime_ns, stat.st_size


def _snapshot_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reserve_identity(snapshots_dir: Path, player: str) -> tuple[str, int, str]:
    """Reserve a never-reused epoch/counter pair before replacing a snapshot."""
    snapshots_dir = Path(snapshots_dir)
    player = _validate_player(player)
    with _lock_for(snapshots_dir, player):
        current = _read_json(index_path(snapshots_dir, player)) or {}
        counter = _read_json(counter_path(snapshots_dir, player)) or {}
        epoch = str(current.get("epoch") or counter.get("epoch") or uuid.uuid4().hex)
        try:
            current_counter = int(current.get("write_counter", 0) or 0)
        except (TypeError, ValueError):
            current_counter = 0
        try:
            reserved_counter = int(counter.get("write_counter", 0) or 0)
        except (TypeError, ValueError):
            reserved_counter = 0
        previous = max(current_counter, reserved_counter)
        write_counter = previous + 1
        _atomic_write(
            counter_path(snapshots_dir, player),
            _canonical_json({"schema": ARTIFACT_SCHEMA, "epoch": epoch, "write_counter": write_counter}),
        )
        return f"{epoch}-{write_counter}", write_counter, epoch


def _identity_from_dict(value: dict[str, Any]) -> PublicationIdentity | None:
    try:
        token = str(value["token"])
        if not _TOKEN_RE.fullmatch(token):
            return None
        write_counter = int(value["write_counter"])
        if int(token.rsplit("-", 1)[1]) != write_counter:
            return None
        return PublicationIdentity(
            player=str(value["player"]),
            token=token,
            write_counter=write_counter,
            epoch=str(value["epoch"]),
            snapshot_path=None if value.get("snapshot_path") is None else str(value["snapshot_path"]),
            snapshot_mtime_ns=None if value.get("snapshot_mtime_ns") is None else int(value["snapshot_mtime_ns"]),
            snapshot_size=None if value.get("snapshot_size") is None else int(value["snapshot_size"]),
            snapshot_generated_at=value.get("snapshot_generated_at"),
            details_mtime_ns=value.get("details_mtime_ns"),
            details_size=int(value["details_size"]) if value.get("details_size") is not None else None,
            complete=bool(value.get("complete")),
            artifact_name=str(value["artifact_name"]),
            schema=str(value.get("schema", SCHEMA_BOARD)),
            representation=str(value.get("representation", REPRESENTATION)),
            artifact_sha256=value.get("artifact_sha256"),
            artifact_size=int(value["artifact_size"]) if value.get("artifact_size") is not None else None,
            artifact_gzip_size=int(value["artifact_gzip_size"]) if value.get("artifact_gzip_size") is not None else None,
            artifact_generated_at=value.get("artifact_generated_at"),
            artifact_assets=value.get("artifact_assets") if isinstance(value.get("artifact_assets"), dict) else None,
            artifact_notice_count=int(value["artifact_notice_count"]) if value.get("artifact_notice_count") is not None else None,
            interim=bool(value.get("interim", False)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def read_identity(snapshots_dir: Path, player: str) -> PublicationIdentity | None:
    snapshots_dir = Path(snapshots_dir)
    identity = _identity_from_dict(_read_json(index_path(snapshots_dir, player)) or {})
    _remember_identity(identity, snapshots_dir)
    return identity


def commit_snapshot_identity(
    snapshots_dir: Path,
    player: str,
    token: str,
    write_counter: int,
    epoch: str,
    snapshot_path: Path,
    snapshot_stat: os.stat_result,
    details_mtime_ns: int | None,
    *,
    snapshot_generated_at: str | None = None,
    details_size: int | None = None,
) -> PublicationIdentity:
    snapshots_dir = Path(snapshots_dir)
    player = _validate_player(player)
    snapshot_path = Path(snapshot_path)
    identity = PublicationIdentity(
        player=player,
        token=token,
        write_counter=write_counter,
        epoch=epoch,
        snapshot_path=str(snapshot_path),
        snapshot_mtime_ns=snapshot_stat.st_mtime_ns,
        snapshot_size=snapshot_stat.st_size,
        snapshot_generated_at=snapshot_generated_at,
        details_mtime_ns=details_mtime_ns,
        details_size=details_size,
        complete=False,
        artifact_name=artifact_path(snapshots_dir, player, token).name,
    )
    _write_identity(snapshots_dir, identity)
    _remember_identity(identity, snapshots_dir)
    return identity


def _write_identity(snapshots_dir: Path, identity: PublicationIdentity) -> None:
    payload = {
        "schema": identity.schema,
        "representation": identity.representation,
        "player": identity.player,
        "token": identity.token,
        "write_counter": identity.write_counter,
        "epoch": identity.epoch,
        "snapshot_path": identity.snapshot_path,
        "snapshot_mtime_ns": identity.snapshot_mtime_ns,
        "snapshot_size": identity.snapshot_size,
        "snapshot_generated_at": identity.snapshot_generated_at,
        "details_mtime_ns": identity.details_mtime_ns,
        "details_size": identity.details_size,
        "complete": identity.complete,
        "artifact_name": identity.artifact_name,
        "artifact_sha256": identity.artifact_sha256,
        "artifact_size": identity.artifact_size,
        "artifact_gzip_size": identity.artifact_gzip_size,
        "artifact_generated_at": identity.artifact_generated_at,
        "artifact_assets": identity.artifact_assets,
        "artifact_notice_count": identity.artifact_notice_count,
        "interim": identity.interim,
    }
    _atomic_write(index_path(snapshots_dir, identity.player), _canonical_json(payload))


def _same_snapshot(identity: PublicationIdentity, snapshot_path: Path) -> bool:
    try:
        stat = snapshot_path.stat()
        return (
            stat.st_mtime_ns == identity.snapshot_mtime_ns
            and stat.st_size == identity.snapshot_size
        )
    except OSError:
        return False


def load_current(
    snapshots_dir: Path,
    player: str,
    *,
    snapshot_path: Path,
    details_mtime_ns: int | None,
    details_size: int | None = None,
) -> tuple[PublicationIdentity | None, dict[str, Any] | None]:
    identity, payload = load_current_payload(
        snapshots_dir,
        player,
        snapshot_path=snapshot_path,
        details_mtime_ns=details_mtime_ns,
        details_size=details_size,
    )
    return identity, None if payload is None else payload.meta


def load_current_payload(
    snapshots_dir: Path,
    player: str,
    *,
    snapshot_path: Path,
    details_mtime_ns: int | None,
    details_size: int | None = None,
    identity: PublicationIdentity | None = None,
) -> tuple[PublicationIdentity | None, ArtifactPayload | None]:
    snapshots_dir = Path(snapshots_dir)
    player = _validate_player(player)
    identity = read_identity(snapshots_dir, player) if identity is None else identity
    if identity is None or identity.player != _validate_player(player) or not identity.complete or identity.schema != SCHEMA_BOARD or identity.representation != REPRESENTATION:
        return identity, None
    key = _cache_key(snapshots_dir, player)
    with _artifact_cache_guard:
        cached = _artifact_cache.get(key)
    if cached is not None and cached.token == identity.token:
        return identity, cached
    # an interim identity has no snapshot to agree with -- the artifact is the board.
    if not identity.interim and (
        identity.snapshot_path is None or Path(identity.snapshot_path) != Path(snapshot_path) or not _same_snapshot(identity, Path(snapshot_path))
    ):
        return identity, None
    if identity.details_mtime_ns != details_mtime_ns or identity.details_size != details_size:
        return identity, None
    path = Path(snapshots_dir) / identity.artifact_name
    if path != artifact_path(snapshots_dir, player, identity.token):
        return identity, None
    try:
        artifact_stat = path.stat()
        if identity.artifact_sha256 is None or identity.artifact_size != artifact_stat.st_size:
            return identity, None
        with path.open("rb") as handle:
            raw = handle.read()
        if hashlib.sha256(raw).hexdigest() != identity.artifact_sha256:
            return identity, None
    except OSError:
        return identity, None
    if identity.artifact_generated_at is not None and identity.snapshot_generated_at is not None and identity.artifact_generated_at != identity.snapshot_generated_at:
        return identity, None
    # no `json.loads` of the board here -- the bytes are served as they are and the
    # status header reads the identity's `meta`. The gzip copy was written by `publish`; a
    # pre- artifact without one is compressed once (a warning) and cached.
    gz = b""
    gz_path = gzip_artifact_path(snapshots_dir, player, identity.token)
    try:
        if identity.artifact_gzip_size is not None and gz_path.stat().st_size == identity.artifact_gzip_size:
            with gz_path.open("rb") as handle:
                gz = handle.read()
    except OSError:
        gz = b""
    if not gz:
        logger.warning("board artifact %s for %s has no gzip copy; compressing on the request thread", identity.token, player)
        gz = gzip.compress(raw, compresslevel=5, mtime=0)
    payload = ArtifactPayload(
        token=identity.token,
        raw=raw,
        gzip=gz,
        meta=artifact_meta(identity),
    )
    with _artifact_cache_guard:
        _artifact_cache[key] = payload
    return identity, payload


def prepare_board(board: dict[str, Any]) -> dict[str, Any]:
    """Keep the cached and published board representations byte-equivalent."""
    result = dict(board)
    coverage = result.get("coverage")
    assets = coverage.get("assets", {}) if isinstance(coverage, dict) else None
    if isinstance(assets, dict):
        result["coverage"] = {
            key: assets.get(key)
            for key in (
                "expected", "retrieved", "fresh", "dropped", "failed", "not_requested",
                "worker_detail_pending", "worker_detail_pending_deeds", "oldest_observed_at",
            )
        }
        dropped = assets.get("dropped", 0)
        failed = assets.get("failed", 0)
        # (2026-09-14): missing worker detail is never a bar.
        # (2026-09-14): a detail dropped after the maintainer's own change is normal too (the plot reloads on open); only a failed read is announced.
        if failed > 0:
            dropped_clause = f", {dropped} dropped after a change" if dropped else ""
            notice = format_notice(
                "assets_incomplete",
                context={
                    "failed": failed,
                    "dropped_clause": dropped_clause,
                },
            )
            result["notices"] = list(result.get("notices", []))
            if notice not in result["notices"]:
                result["notices"].append(notice)
    return result


def publish(
    snapshots_dir: Path,
    player: str,
    identity: PublicationIdentity | None,
    report: dict[str, Any],
    details_by_id: dict[int, dict[str, Any]] | None = None,
    *,
    interim: bool = False,
) -> Path | None:
    """Project and atomically publish one immutable artifact (+ its gzip copy), then mark it
    current. : called synchronously by the snapshot save BEFORE the board is announced
    ready, so a `board_ready` reader always finds the artifact on disk.

    : `interim=True` publishes the bulk board right after the bulk read, before the
    collection sweep -- it reserves its own token, writes NO snapshot, and its identity has
    `interim: true` with null snapshot fields. `identity` is ignored then."""
    snapshots_dir = Path(snapshots_dir)
    player = _validate_player(player)
    board = prepare_board(project_board(report, details_by_id or {}, lean=True))
    encoded = _canonical_json(board)
    compressed = gzip.compress(encoded, compresslevel=5, mtime=0)
    meta = _meta_from_board(board)
    with _lock_for(snapshots_dir, player):
        current_details_mtime, current_details_size = details_identity()
        if interim:
            token, write_counter, epoch = reserve_identity(snapshots_dir, player)
            identity = PublicationIdentity(
                player=player,
                token=token,
                write_counter=write_counter,
                epoch=epoch,
                snapshot_path=None,
                snapshot_mtime_ns=None,
                snapshot_size=None,
                snapshot_generated_at=None,
                details_mtime_ns=current_details_mtime,
                details_size=current_details_size,
                complete=False,
                artifact_name=artifact_path(snapshots_dir, player, token).name,
                interim=True,
            )
        else:
            current = read_identity(snapshots_dir, player)
            if (
                identity is None
                or current is None
                or current.token != identity.token
                or current.snapshot_generated_at != identity.snapshot_generated_at
                or current.details_mtime_ns != current_details_mtime
                or current.details_size != current_details_size
            ):
                return None
        path = artifact_path(snapshots_dir, player, identity.token)
        gz_path = gzip_artifact_path(snapshots_dir, player, identity.token)
        _atomic_write(gz_path, compressed)
        _atomic_write(path, encoded)
        artifact_stat = path.stat()
        complete = PublicationIdentity(
            **{
                **identity.__dict__,
                "complete": True,
                "artifact_sha256": hashlib.sha256(encoded).hexdigest(),
                "artifact_size": artifact_stat.st_size,
                "artifact_gzip_size": len(compressed),
                **meta,
            }
        )
        _write_identity(snapshots_dir, complete)
        _remember_identity(complete, snapshots_dir)
        _prune(snapshots_dir, player, identity.token)
    sps_rate.remember(snapshots_dir, player, board)  # display-only, never fails the publish
    return path


def _prune(snapshots_dir: Path, player: str, current_token: str) -> None:
    prefix = f"board-{_validate_player(player)}-"
    keep = {
        index_path(snapshots_dir, player).name,
        counter_path(snapshots_dir, player).name,
        artifact_path(snapshots_dir, player, current_token).name,
        gzip_artifact_path(snapshots_dir, player, current_token).name,
    }
    for path in list(Path(snapshots_dir).glob(f"{prefix}*.json")) + list(Path(snapshots_dir).glob(f"{prefix}*.json.gz")):
        if path.name not in keep:
            try:
                path.unlink()
            except OSError:
                pass
