"""Snapshot IO, background capture, and freshness for the Ceres board.

Ceres owns its own on-disk snapshot store under ``~/.ceres/snapshots/`` -- never
``~/.praetoria/`` (Praetoria's own cache/snapshot store) -- because
``LandCapture.capture(offline=True)`` serves arbitrarily stale data and discards
the ``CacheInfo`` age entirely (see ``praetoria/land_analysis.py``). Ceres needs a
real, persisted freshness surface of its own rather than trusting the cache.

``/api/board`` never triggers a network call (see ``routes_board.py``); this module
is the only place in Ceres that does.
"""

from __future__ import annotations

import atexit
import hashlib
import inspect
import json
import logging
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from praetoria.api import SplinterlandsClient
from praetoria.cache import JsonCache
from praetoria import fastjson
from praetoria.land_analysis import CONCURRENT_DISPATCH_INTERVAL_SECONDS, DEFAULT_REQUEST_BUDGET, LandCapture, load_snapshot, normalize_bundle, project_worksite_facts, save_snapshot
from praetoria.errors import CacheError, SchemaError
from praetoria.transport import ResponseData
from mnemosyne.accounting import active_context

from .config import CeresConfig
from . import card_pool_index, security

logger = logging.getLogger("ceres.capture")

# (a), maintainer choice 2026-09-11: ONE shared client per game origin for every capture, so the
# reads of one run reuse an open TLS connection. (2026-09-14): the board read runs in a worker
# child (`ceres.capture_worker`), so each job pays one handshake (0.45 s measured warm-network
# accepted for v1); the parent's keep-warm loop is gone with it.
from praetoria.land_analysis import LandClient  # noqa: E402
_SHARED_LAND_CLIENT = LandClient()
_SHARED_COLLECTION_CLIENT = SplinterlandsClient()


def _shared_capture() -> LandCapture:
    capture = LandCapture(land_client=_SHARED_LAND_CLIENT, collection_client=_SHARED_COLLECTION_CLIENT)
    capture.budget_for_deeds = derive_request_budget  # sized from the live deed list, first read included
    # (2026-09-14): Ceres fetches the collection live on every read (ttl=0, ) and
    # never reads `collection_<player>.json` back, yet each whale read wrote it to the praetoria
    # cache (162.8 MB per read on player0016). Skip the write; `land_deeds_<player>` stays persisted
    # for `previous_bundle` in capture_quick. Ordinary plot expand now uses narrow live reads.
    capture.persist_cache = lambda key: not key.startswith("collection_")
    return capture


# ("all storage of current board data is strictly forbidden"): the only praetoria cache keys a
# plot open may write -- the game's card stat table and item catalogue (static game reference, Q3).
STATIC_REFERENCE_CACHE_KEYS = frozenset({"card_details", "item_details"})


def persist_static_reference_only(key: str) -> bool:
    return key in STATIC_REFERENCE_CACHE_KEYS


_lock = threading.Lock()
_run_state: dict[str, "_RunState"] = {}

# Per-player write serialisation (): `expand_plot` and `_save_merged` are both
# read-modify-write (`latest_snapshot()` -> merge -> `save_snapshot()`), and FastAPI
# runs sync routes off its own threadpool, so two concurrent expands for the same
# player raced this sequence -- even with `save_snapshot`'s temp-file fix (unique per
# writer), the LATER writer's merge still silently dropped whatever the EARLIER one had
# just merged in, because each read the pre-write snapshot. A lock per player (not one
# global lock) keeps different players' captures/expands running fully in parallel.
_snapshot_write_locks: dict[str, threading.Lock] = {}
_snapshot_write_locks_guard = threading.Lock()


def _snapshot_write_lock(player: str) -> threading.Lock:
    with _snapshot_write_locks_guard:
        lock = _snapshot_write_locks.get(player)
        if lock is None:
            lock = threading.Lock()
            _snapshot_write_locks[player] = lock
        return lock


def _publish_board_artifact(snapshots_dir: Path, player: str, identity: Any, report: dict[str, Any]) -> None:
    """: project and write the lean artifact (+ gzip) synchronously, inside the snapshot
    save and BEFORE the board is announced ready. Nothing is left running after "ready"."""
    from . import board_artifact
    from .board import load_details_by_id

    try:
        board_artifact.publish(snapshots_dir, player, identity, report, load_details_by_id())
    except Exception as exc:
        logger.warning("lean board artifact publish failed for %s: %s", player, exc)


# Parsed-snapshot cache keyed by path -> (mtime_ns, parsed report), 2026-08-21
# (`work/ceres-plot-expand-bug.md`). `latest_snapshot()` used to re-read and
# re-`json.loads()` the on-disk snapshot on EVERY call -- 18.5 MB for player0005's
# live account, measured at ~1.2s per `GET /api/plots/{deed_uid}` call because
# that cost was paid fresh per request. Read-only caching of a read-only board
# invalidated purely by the file's own mtime, so a capture refresh that writes a
# new snapshot is picked up on the very next request with no staleness window.
# Never mutated in place downstream (`board.py` only ever reads `report`), so
# sharing the same parsed dict across requests is safe.
_snapshot_cache: "OrderedDict[str, tuple[int, dict[str, Any]]]" = OrderedDict()
_snapshot_cache_lock = threading.Lock()
_snapshot_cache_bytes: dict[str, int] = {}
_snapshot_cache_total_bytes = 0
_plot_overlay_cache: OrderedDict[str, tuple[tuple[Any, ...], dict[str, Any]]] = OrderedDict()
_plot_overlay_lock = threading.Lock()
# Byte budget, not just an entry cap: one parsed whale report (player0016, 6,303 deeds)
# retains 105.6 MB, so a handful of distinct players already exhausts a 1.5GB production
# cgroup (). 200MB fits roughly two whale-sized reports at once, which is the
# realistic worst case for a small public deployment.
_SNAPSHOT_CACHE_MAX_BYTES = 200 * 1024 * 1024
_CARD_POOL_SCHEMA = "ceres.card-pool.v1"
_card_pool_cache: dict[tuple[str, str], tuple[int, list[dict[str, Any]], str]] = {}
_card_pool_cache_lock = threading.Lock()
_CARD_POOL_CACHE_MAX = 6  # S10 / B5-08: whole collections of the most recent accounts only


def _remember_card_pool(key, value) -> None:
    """Insert under `_card_pool_cache_lock` (held by the caller), oldest entries out first."""
    _card_pool_cache.pop(key, None)
    _card_pool_cache[key] = value
    while len(_card_pool_cache) > _CARD_POOL_CACHE_MAX:
        _card_pool_cache.pop(next(iter(_card_pool_cache)))


class CardPoolUnavailable(LookupError):
    pass


class MnemosyneEvidence:
    """Attempt-local, account-bound fresh Land observations."""

    def __init__(self, account: str, capture: LandCapture | None = None, attempt: int | None = None):
        self.account = security.validate_player(account)
        self.attempt = attempt
        self.accounting_identity = active_context()
        self._capture_instance = capture
        self._bundle: ResponseData | None = None
        self._bundle_error: Exception | None = None
        self._assets: dict[str, ResponseData] = {}
        self._asset_errors: dict[str, Exception] = {}
        self._collection: ResponseData | None = None
        self._collection_error: Exception | None = None
        self._worksite_map: dict[str, dict[str, Any] | None] | None = None
        self._worksite_error: Exception | None = None

    @property
    def capture(self) -> LandCapture:
        if self._capture_instance is None:
            self._capture_instance = _shared_capture()
        return self._capture_instance

    def _raise(self, error: Exception) -> None:
        raise error

    def bundle(self) -> ResponseData:
        if self._bundle_error is not None:
            self._raise(self._bundle_error)
        if self._bundle is None:
            try:
                self._bundle = self.capture.fresh_read(lambda: self.capture.land_client.bundle(self.account))
                self.capture.fit_request_budget(getattr(self._bundle, "data", self._bundle))
            except Exception as exc:
                self._bundle_error = exc
                raise
        return self._bundle

    def assets(self, deed_uid: str) -> ResponseData:
        uid = str(deed_uid)
        if uid in self._asset_errors:
            self._raise(self._asset_errors[uid])
        if uid not in self._assets:
            try:
                self._assets[uid] = self.capture.fresh_read(lambda: self.capture.land_client.assets(uid))
            except Exception as exc:
                self._asset_errors[uid] = exc
                raise
        return self._assets[uid]

    def collection(self) -> ResponseData:
        if self._collection_error is not None:
            self._raise(self._collection_error)
        if self._collection is None:
            try:
                self._collection = self.capture.fresh_read(lambda: self.capture.collection_client.collection(self.account))
            except Exception as exc:
                self._collection_error = exc
                raise
        return self._collection

    def worksite_facts(self, deed_uid: str) -> dict[str, Any] | None:
        if self._worksite_error is not None:
            self._raise(self._worksite_error)
        if self._worksite_map is None:
            try:
                response = self.bundle()
                deeds, worksites, _ = normalize_bundle(response.data, observed_at=response.retrieved_at)
                deed_ids = {deed.deed_uid for deed in deeds}
                worksite_map = {worksite.deed_uid: worksite.facts for worksite in worksites}
                self._worksite_map = {uid: worksite_map.get(uid) if uid in deed_ids else None for uid in deed_ids | set(worksite_map)}
            except Exception as exc:
                self._worksite_error = exc
                raise
        return self._worksite_map.get(str(deed_uid))

    def provenance(self) -> dict[str, Any]:
        resources: dict[str, Any] = {}
        if self._bundle is not None:
            resources["bundle"] = {"retrieved_at": self._bundle.retrieved_at, "attempts": self._bundle.attempts}
        assets = {
            uid: {"retrieved_at": response.retrieved_at, "attempts": response.attempts}
            for uid, response in self._assets.items()
        }
        if assets:
            resources["assets"] = assets
        if self._collection is not None:
            resources["collection"] = {"retrieved_at": self._collection.retrieved_at, "attempts": self._collection.attempts}
        result = {"account": self.account, "resources": resources, "capture": {"requests": self._capture_instance._requests if self._capture_instance is not None else 0}}
        if self.attempt is not None:
            result["attempt"] = self.attempt
        return result


def _report_size_bytes(report: dict[str, Any]) -> int:
    """Approximate a report's retained size when no written-file size is available."""
    try:
        return len(json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _evict_snapshot_cache_locked(new_key: str | None = None) -> None:
    """Caller must hold `_snapshot_cache_lock`. Evicts oldest entries (LRU order,
    `OrderedDict` insertion/move_to_end order) until total retained bytes fits the
    budget, skipping `new_key` itself so a single very large report is never evicted
    the instant it is inserted."""
    global _snapshot_cache_total_bytes
    while _snapshot_cache_total_bytes > _SNAPSHOT_CACHE_MAX_BYTES and _snapshot_cache:
        oldest_key = next(iter(_snapshot_cache))
        if oldest_key == new_key and len(_snapshot_cache) == 1:
            break
        _snapshot_cache.pop(oldest_key, None)
        _snapshot_cache_total_bytes -= _snapshot_cache_bytes.pop(oldest_key, 0)


def _put_snapshot_cache_locked(
    key: str,
    mtime_ns: int,
    report: dict[str, Any],
    size_bytes: int | None = None,
) -> None:
    """Caller must hold `_snapshot_cache_lock`. Inserts/replaces one entry, keeps the
    running byte total in sync, moves it to most-recently-used, then evicts down to
    budget. `size_bytes` is the written file size when the report came from disk."""
    global _snapshot_cache_total_bytes
    _snapshot_cache_total_bytes -= _snapshot_cache_bytes.pop(key, 0)
    size = _report_size_bytes(report) if size_bytes is None else size_bytes
    _snapshot_cache[key] = (mtime_ns, report)
    _snapshot_cache.move_to_end(key)
    _snapshot_cache_bytes[key] = size
    _snapshot_cache_total_bytes += size
    _evict_snapshot_cache_locked(new_key=key)

# Overhead beyond one request per deed: the deeds bundle itself (1), the collection
# sweep (1), and up to ~3 paginated /available calls for the unassigned-item families
# (totem/title/power_core -- see `_capture_unassigned_items`; Runi is declared unsupported
# rather than fetched, , so it costs no request), plus a 10% margin
# for short pages/retries. Derived, not a raised constant -- a fixed budget the size of
# `DEFAULT_REQUEST_BUDGET` (200) fails outright for a whale account (e.g. 6,303 deeds
# measured live for "player0016" on 2026-08-21).
_REQUEST_OVERHEAD = 6
_BUDGET_MARGIN = 1.10


def derive_request_budget(deed_count: int) -> int:
    """Size the Land capture request budget from the actual (preflighted) deed count."""
    if deed_count <= 0:
        return DEFAULT_REQUEST_BUDGET
    base = deed_count + _REQUEST_OVERHEAD
    return max(DEFAULT_REQUEST_BUDGET, int(base * _BUDGET_MARGIN) + 10)


def estimate_capture_seconds(deed_count: int) -> float:
    """Rough wall-clock estimate for a FULL capture at the concurrent fan-out's own
    dispatch ceiling (2026-08-21 concurrency feature -- see
    `CONCURRENT_DISPATCH_INTERVAL_SECONDS`) -- shown to the user before they commit to
    a long one. This is the actual binding constraint in practice: at the default
    concurrency (8) the pool's own natural throughput (~13 req/s) exceeds the dispatch
    ceiling (10 req/s), so the ceiling -- not raw per-request latency -- is what
    determines wall-clock time. Never lowered further to go faster than the
    live-measured-safe ceiling; this only predicts its cost."""
    budget = derive_request_budget(deed_count)
    return budget * CONCURRENT_DISPATCH_INTERVAL_SECONDS


# a measured-rate model for the QUICK read. An estimate is only ever derived from
# a measurement taken on a Ceres server, never from an invented constant (: never quote
# a page-load time that was not measured). The learned model is persisted to
# `quick-timing.json` under the Ceres data directory (`config.root`) so a restart or deploy
# does not forget it; until a read has been measured it is seeded from measured reads
# (`QUICK_TIMING_SEED`, each value naming its measurement) so the very first visitor after a
# deploy still gets an estimate.
_QUICK_TIMING_LOCK = threading.Lock()
_quick_base_seconds: float | None = None       # fixed cost seen on the smallest measured read
_quick_seconds_per_deed: float | None = None   # EMA of (bulk seconds / deed count)
_quick_timing_samples: int = 0                 # measurements folded into the model (this process + disk)
_quick_timing_loaded: bool = False             # disk read attempted once per process
# (v0.9.376), his words on v0.9.375: "the timing is off. The loading is much faster than
# anticipated." The shared model is a rate for accounts this server has never read; a repeat
# load is timed against ITSELF: {player: {"seconds", "deeds", "at"}}, EMA over that player's
# own runs, kept in the same file (the last _QUICK_TIMING_MAX_PLAYERS players by `at`).
_quick_timing_players: dict[str, dict[str, Any]] = {}
_QUICK_TIMING_ALPHA = 0.3
_QUICK_TIMING_PLAYER_ALPHA = 0.5
_QUICK_TIMING_MAX_PLAYERS = 200
_QUICK_TIMING_DEED_DRIFT = 0.10  # plot count moved by more than this: scale the player's value
_QUICK_TIMING_FILENAME = "quick-timing.json"

# seed -- MEASURED, not invented (). Each value names its measurement.
# Both rig runs 2026-09-14: refresh -> first board read, worktree ir5340 code, one pytest queue
# running concurrently on the same machine.
# player0005, 129 plots: 4.8 s -> base_seconds (the floor, from the smaller account)
# player0001, 461 plots: 6.6 s -> seconds_per_deed (the per-plot ratio, from the larger account)
# estimate = max(base, per_deed * n), so a 4357-plot account seeds at ~62 s.
QUICK_TIMING_SEED: dict[str, float] = {"base_seconds": 4.8, "seconds_per_deed": 6.6 / 461}


def _quick_timing_path(config: CeresConfig) -> Path:
    """The persisted model: `quick-timing.json` next to the snapshots, under `config.root`."""
    return Path(config.root) / _QUICK_TIMING_FILENAME


def _positive_finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _load_quick_timing(config: CeresConfig) -> None:
    """Read the persisted model once per process. Any error leaves the globals untouched."""
    global _quick_base_seconds, _quick_seconds_per_deed, _quick_timing_samples, _quick_timing_loaded
    with _QUICK_TIMING_LOCK:
        if _quick_timing_loaded:
            return
        _quick_timing_loaded = True
        try:
            raw = json.loads(_quick_timing_path(config).read_text(encoding="utf-8"))
            base = _positive_finite(raw.get("base_seconds"))
            per_deed = _positive_finite(raw.get("seconds_per_deed"))
            samples = int(raw.get("samples") or 0)
        except Exception:
            return
        players = raw.get("players")
        if isinstance(players, dict):
            for name, entry in players.items():
                if name in _quick_timing_players or not isinstance(entry, dict):
                    continue  # this process's own measurement wins over the disk copy
                seconds = _positive_finite(entry.get("seconds"))
                deeds = _positive_finite(entry.get("deeds"))
                if seconds is None or deeds is None:
                    continue
                _quick_timing_players[str(name)] = {"seconds": seconds, "deeds": int(deeds), "at": str(entry.get("at") or "")}
        if base is None or per_deed is None:
            return
        if _quick_base_seconds is None or _quick_seconds_per_deed is None:
            _quick_base_seconds = base
            _quick_seconds_per_deed = per_deed
            _quick_timing_samples = max(samples, 0)


def _save_quick_timing(config: CeresConfig) -> None:
    """Write the model atomically (tmp file + os.replace). OSError is swallowed: a courtesy."""
    with _QUICK_TIMING_LOCK:
        payload = {
            "base_seconds": _quick_base_seconds,
            "seconds_per_deed": _quick_seconds_per_deed,
            "samples": _quick_timing_samples,
            "players": dict(_quick_timing_players),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    if payload["base_seconds"] is None or payload["seconds_per_deed"] is None:
        return
    try:
        config.ensure_dirs()
        target = _quick_timing_path(config)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".quick-timing-", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError:
        pass


def record_quick_timing(deed_count: int, bulk_seconds: float, config: CeresConfig | None = None, player: str | None = None) -> None:
    """Learn from one finished bulk read: seconds from run start to board_ready.
    With `config`, the updated model is persisted so a restart does not forget it.
    With `player`, the account's own memory is updated too (, v0.9.376)."""
    global _quick_base_seconds, _quick_seconds_per_deed, _quick_timing_samples
    if deed_count <= 0 or bulk_seconds <= 0:
        return
    if config is not None:
        _load_quick_timing(config)  # fold disk knowledge in before the first in-process sample
    rate = float(bulk_seconds) / float(deed_count)
    with _QUICK_TIMING_LOCK:
        if player:
            prior = _quick_timing_players.get(player)
            seconds = float(bulk_seconds)
            if prior is not None:
                seconds = _QUICK_TIMING_PLAYER_ALPHA * seconds + (1.0 - _QUICK_TIMING_PLAYER_ALPHA) * float(prior["seconds"])
            _quick_timing_players[player] = {"seconds": seconds, "deeds": int(deed_count), "at": datetime.now(timezone.utc).isoformat()}
            if len(_quick_timing_players) > _QUICK_TIMING_MAX_PLAYERS:
                oldest = sorted(_quick_timing_players, key=lambda name: _quick_timing_players[name].get("at") or "")
                for name in oldest[: len(_quick_timing_players) - _QUICK_TIMING_MAX_PLAYERS]:
                    _quick_timing_players.pop(name, None)
        if _quick_seconds_per_deed is None:
            _quick_seconds_per_deed = rate
        else:
            _quick_seconds_per_deed = _QUICK_TIMING_ALPHA * rate + (1.0 - _QUICK_TIMING_ALPHA) * _quick_seconds_per_deed
        prior_base = _quick_base_seconds if _quick_base_seconds is not None else float("inf")
        _quick_base_seconds = min(prior_base, float(bulk_seconds))
        _quick_timing_samples += 1
    if config is not None:
        _save_quick_timing(config)


def estimate_quick_seconds(deed_count: int, config: CeresConfig | None = None, player: str | None = None) -> float | None:
    """The player's own last measured read when this server has one (, v0.9.376; scaled
    by the plot-count ratio when the count moved by more than 10 %), else the shared model:
    max(base, per_deed*deed_count) rounded up to whole seconds. Learned values first (in
    memory, then the persisted file when `config` is given); otherwise the measured seed
    (`QUICK_TIMING_SEED`). Never None in practice; the Optional stays for the call shape."""
    if config is not None:
        _load_quick_timing(config)
    with _QUICK_TIMING_LOCK:
        base, per_deed = _quick_base_seconds, _quick_seconds_per_deed
        own = dict(_quick_timing_players[player]) if player and player in _quick_timing_players else None
    if own is not None:
        seconds = float(own["seconds"])
        known = int(own.get("deeds") or 0)
        count = max(int(deed_count), 0)
        if known > 0 and count > 0 and abs(count - known) / known > _QUICK_TIMING_DEED_DRIFT:
            seconds *= count / known
        return max(0.1, round(seconds, 1))
    if base is None or per_deed is None:
        base = QUICK_TIMING_SEED["base_seconds"]
        per_deed = QUICK_TIMING_SEED["seconds_per_deed"]
    return float(math.ceil(max(base, per_deed * max(int(deed_count), 0))))


@dataclass
class _RunState:
    # "queued" = accepted and waiting only for a superseded worker to exit and be reaped.
    state: Literal["idle", "queued", "running", "failed"] = "idle"
    mode: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    requests_made: int | None = None
    error: str | None = None
    outcome: dict[str, Any] | None = None
    total_deeds: int | None = None
    fetched_deeds: int | None = None
    queue_position: int | None = None  # 1 = next to run; None unless queued
    # measured-rate estimate for the quick read, set as soon as the plot count is known.
    estimated_seconds: float | None = None
    # R-149, his ruling: the default board read is a QUICK, bulk-only capture, so
    # `fetched_deeds`/`total_deeds` stay null throughout it and there is no N of M to
    # report. His choice was to show the real PHASE instead of a fabricated fraction.
    # These are the capture's own sequential steps, named at the Ceres layer -- no
    # praetoria change and nothing estimated.
    phase: str | None = None
    # (2026-09-07): true from the moment the merged snapshot is on disk, which is
    # BEFORE the `gaps` self-heal batch runs. The board was already readable ~2.5 s
    # before the run reported idle; the browser can render on this and re-read once.
    board_ready: bool = False
    run_token: object | None = field(default=None, repr=False, compare=False)


@dataclass
class SnapshotRef:
    path: str
    generated_at: str
    player: str
    token: str | None = None
    write_counter: int | None = None
    snapshot_mtime_ns: int | None = None
    snapshot_size: int | None = None
    details_mtime_ns: int | None = None
    details_size: int | None = None


@dataclass
class CaptureStatus:
    state: Literal["idle", "queued", "running", "failed"] = "idle"
    mode: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    requests_made: int | None = None
    error: str | None = None
    outcome: dict[str, Any] | None = None
    latest_snapshot: SnapshotRef | None = None
    age_seconds: int | None = None
    stale: bool = False
    total_deeds: int | None = None
    fetched_deeds: int | None = None
    estimated_seconds: float | None = None  # mirrored from _RunState
    # R-149: carried from _RunState so the status payload can name the running step.
    # Added here AS WELL AS on _RunState -- two different dataclasses, and the first
    # version of this change added it to only one, which made the status route 500 the
    # moment a real capture ran. Found by triggering an actual capture; every check
    # before that had exercised the loader without one.
    phase: str | None = None
    # mirrored from _RunState, for the same reason `phase` is mirrored above.
    board_ready: bool = False
    assets_complete: bool = False
    oldest_observed_at: str | None = None
    assets_retrieved: int | None = None
    assets_expected: int | None = None
    assets_dropped: int | None = None
    assets_failed: int | None = None
    queue_position: int | None = None  # mirrored from _RunState
    # true while the current publication is the interim (bulk-only) artifact
    # the board is drawn, the collection sweep and final publish are still running.
    interim: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "queue_position": self.queue_position,
            "mode": self.mode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "requests_made": self.requests_made,
            "error": self.error,
            "outcome": self.outcome,
            "latest_snapshot": None if self.latest_snapshot is None else {
                "path": self.latest_snapshot.path,
                "generated_at": self.latest_snapshot.generated_at,
                "player": self.latest_snapshot.player,
                "token": self.latest_snapshot.token,
            },
            "age_seconds": self.age_seconds,
            "stale": self.stale,
            "total_deeds": self.total_deeds,
            "fetched_deeds": self.fetched_deeds,
            "estimated_seconds": self.estimated_seconds,
            "phase": self.phase,
            # follow-up (2026-09-07): the field was added to both dataclasses in
            # v0.9.18 and NOT to this hand-written dict, so the status route never carried
            # it and the browser's early render never fired. Found by a player0016 reproduction
            # that printed `board_ready: None` from the live status endpoint.
            "board_ready": self.board_ready,
            "interim": self.interim,
            "assets_complete": self.assets_complete,
            "oldest_observed_at": self.oldest_observed_at,
            "assets_retrieved": self.assets_retrieved,
            "assets_expected": self.assets_expected,
            "assets_dropped": self.assets_dropped,
            "assets_failed": self.assets_failed,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _latest_path(config: CeresConfig, player: str) -> Path:
    player = security.validate_player(player)
    return security.resolve_under(
        config.snapshots_dir,
        config.snapshots_dir / f"land-{player}-latest.json",
    )


def _plot_patch_path(config: CeresConfig, player: str, deed_uid: str) -> Path:
    digest = hashlib.sha256(deed_uid.encode("utf-8")).hexdigest()[:24]
    return security.resolve_under(config.snapshots_dir, config.snapshots_dir / f"land-{player}-plot-{digest}.json")


def _plot_patch_files(config: CeresConfig, player: str) -> list[Path]:
    player = security.validate_player(player)
    return sorted(config.snapshots_dir.glob(f"land-{player}-plot-*.json"))


def has_plot_patches(config: CeresConfig, player: str) -> bool:
    return bool(_plot_patch_files(config, player))


def _merge_plot_patches(config: CeresConfig, player: str, base: dict[str, Any], base_stat: os.stat_result) -> tuple[dict[str, Any], str | None]:
    files = _plot_patch_files(config, player)
    if not files:
        return base, None
    file_stats = []
    for path in files:
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        file_stats.append((str(path), stat.st_mtime_ns, stat.st_size))
    if not file_stats:
        return base, None
    signature = (base_stat.st_mtime_ns, base_stat.st_size, tuple(file_stats))
    cache_key = str(_latest_path(config, player))
    with _plot_overlay_lock:
        cached = _plot_overlay_cache.get(cache_key)
        if cached and cached[0] == signature:
            _plot_overlay_cache.move_to_end(cache_key)
            return cached[1], hashlib.sha256(repr(signature).encode()).hexdigest()[:16]
    merged = base
    applied = False
    newest = str(base.get("generated_at") or "")
    for path in files:
        try:
            patch = fastjson.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        if not isinstance(patch, dict) or patch.get("base_mtime_ns") != base_stat.st_mtime_ns or patch.get("base_size") != base_stat.st_size:
            continue
        expanded = patch.get("report")
        deed_uid = patch.get("deed_uid")
        if not isinstance(expanded, dict) or not isinstance(deed_uid, str):
            continue
        laid_over = _asset_repair_patch(merged, expanded, [deed_uid])
        merged = merge_reports(merged, laid_over, stale_after_seconds=config.stale_after_seconds)
        newest = max(newest, str(expanded.get("generated_at") or ""))
        applied = True
    if not applied:
        return base, None
    merged["generated_at"] = newest
    with _plot_overlay_lock:
        _plot_overlay_cache[cache_key] = (signature, merged)
        _plot_overlay_cache.move_to_end(cache_key)
        while len(_plot_overlay_cache) > 2:
            _plot_overlay_cache.popitem(last=False)
    return merged, hashlib.sha256(repr(signature).encode()).hexdigest()[:16]


def _cards_path(config: CeresConfig, player: str) -> Path:
    player = security.validate_player(player)
    return security.resolve_under(
        config.snapshots_dir,
        config.snapshots_dir / f"land-{player}-cards-latest.json",
    )


def _card_index(report: dict[str, Any]) -> list[dict[str, Any]]:
    refs = {
        str(worker.get("asset_ref"))
        for worker in report.get("workers", [])
        if worker.get("asset_ref") is not None
    }
    index: list[dict[str, Any]] = []
    for row in report.get("cards", []):
        if not isinstance(row, dict) or str(row.get("asset_ref")) not in refs:
            continue
        facts = row.get("facts")
        if not isinstance(facts, dict):
            continue
        index.append({"asset_ref": row.get("asset_ref"), "level": facts.get("level"), "skin": facts.get("skin")})
    return index


def _inline_card_fetched_at(report: dict[str, Any]) -> str | None:
    collection = (report.get("coverage") or {}).get("collection")
    if not isinstance(collection, dict):
        return None
    value = collection.get("fetched_at")
    return str(value) if value else None


def _parse_card_fetched_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_card_sidecar(path: Path) -> tuple[dict[str, Any], int] | None:
    try:
        sidecar_stat = path.stat()
    except OSError:
        return None
    try:
        payload = fastjson.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise CardPoolUnavailable("full card collection sidecar is unavailable; refresh the collection") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != _CARD_POOL_SCHEMA
        or not isinstance(payload.get("cards"), list)
        or _parse_card_fetched_at(payload.get("fetched_at")) is None
    ):
        raise CardPoolUnavailable("full card collection sidecar is invalid; refresh the collection")
    return payload, sidecar_stat.st_mtime_ns


def _choose_card_pool(
    report: dict[str, Any], sidecar: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], str | None]:
    inline_cards = report.get("cards")
    has_inline = isinstance(inline_cards, list)
    if has_inline and sidecar is None:
        return inline_cards, _inline_card_fetched_at(report)
    if not has_inline and sidecar is None:
        raise CardPoolUnavailable("full card collection is unavailable; refresh the collection")
    if sidecar is None:
        return inline_cards, _inline_card_fetched_at(report)
    sidecar_cards = sidecar["cards"]
    sidecar_fetched_at = str(sidecar["fetched_at"])
    if not has_inline:
        expected = (report.get("card_pool") or {}).get("fetched_at")
        expected_time = _parse_card_fetched_at(expected)
        sidecar_time = _parse_card_fetched_at(sidecar_fetched_at)
        # sidecar-first publication has a short reverse window in which a reader
        # can still hold the preceding snapshot. The sidecar is a newer live collection
        # so it is safe to use; only an absent or genuinely older sidecar needs recovery.
        if expected != sidecar_fetched_at and (
            expected_time is None or sidecar_time is None or sidecar_time <= expected_time
        ):
            raise CardPoolUnavailable("full card collection is older than the board snapshot; refresh the collection")
        return sidecar_cards, sidecar_fetched_at
    inline_fetched_at = _inline_card_fetched_at(report)
    sidecar_time = _parse_card_fetched_at(sidecar_fetched_at)
    inline_time = _parse_card_fetched_at(inline_fetched_at)
    if inline_time is None or sidecar_time is None:
        raise CardPoolUnavailable("both card collections exist without comparable fetched_at provenance")
    # During migration, freshness is the authority: the sidecar does not win merely
    # because it is the new storage format; equal timestamps keep the inline copy
    # deterministically because it is the snapshot's own representation.
    if sidecar_time > inline_time:
        return sidecar_cards, sidecar_fetched_at
    return inline_cards, inline_fetched_at


def _write_card_pool(report: dict[str, Any], path: Path) -> None:
    cards = report.get("cards")
    coverage = report.get("coverage", {}).get("collection", {})
    if not cards or not isinstance(cards, list) or not isinstance(coverage, dict) or coverage.get("complete") is not True:
        return
    fetched_at = coverage.get("fetched_at") or report.get("generated_at")
    if not fetched_at:
        raise CardPoolUnavailable("card collection has no fetched_at provenance")
    payload = {"schema_version": _CARD_POOL_SCHEMA, "player": report.get("query", {}).get("player"), "fetched_at": fetched_at, "cards": cards}
    encoded_payload = fastjson.dumps(payload) + b"\n"  # ~10 s -> ~1.5 s on a 186 MB pool
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded_payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot_without_card_pool(report: dict[str, Any], cards: list[dict[str, Any]] | None = None, fetched_at: str | None = None) -> dict[str, Any]:
    snapshot = dict(report)
    source_cards = cards if cards is not None else snapshot.pop("cards", None)
    snapshot.pop("cards", None)
    # an EMPTY list is not a card pool, it is the absence of one. A capture or
    # expand that did not fetch the collection arrives with `cards: []`; merge_reports has
    # just carried the good `card_index` (and its complete `coverage.collection`) forward
    # and rebuilding the index from an empty list overwrote it with [] -- which cost every
    # staked worker its level, and with the level every ability chip and the ENERGIZED
    # badge derived from them. `_write_card_pool` above guards this correctly (`if not
    # cards: return`); this path must guard it the same way.
    if source_cards and isinstance(source_cards, list):
        coverage = dict(snapshot.get("coverage") or {})
        collection = dict(coverage.get("collection") or {})
        snapshot["coverage"] = coverage
        coverage["collection"] = collection
        if isinstance(collection, dict) and collection.get("complete") is True:
            indexed_report = dict(report)
            indexed_report["cards"] = source_cards
            snapshot["card_index"] = _card_index(indexed_report)
            selected_fetched_at = fetched_at or collection.get("fetched_at") or snapshot.get("generated_at")
            snapshot["card_pool"] = {
                "fetched_at": selected_fetched_at,
                "count": len(source_cards),
            }
            if fetched_at:
                collection["fetched_at"] = fetched_at
    return snapshot


def _with_card_context(report: dict[str, Any], config: CeresConfig, player: str) -> dict[str, Any]:
    report["_cards_sidecar_path"] = str(_cards_path(config, player))
    # recovery -- a snapshot written before the guard in `_snapshot_without_card_pool`
    # carries an EMPTY `card_index` while the sidecar still holds the whole collection, and
    # every ability on the board stays blank until some later capture happens to rebuild it.
    # Rebuild it here instead, once, from the sidecar that is already correct. An intact
    # snapshot pays only a truth test on a list already in memory, so the board's critical
    # path remains off the card pool (R-111), and a sidecar that cannot be trusted for this
    # snapshot leaves the report exactly as it was.
    if not report.get("card_index") and report.get("workers"):
        try:
            hydrated = load_card_pool(report)
        except CardPoolUnavailable:
            return report
        recovered = _card_index(hydrated)
        if recovered:
            report["card_index"] = recovered
    return report


def load_card_pool(report: dict[str, Any]) -> dict[str, Any]:
    raw_path = report.get("_cards_sidecar_path")
    if raw_path:
        path = Path(str(raw_path))
        try:
            sidecar_stat = path.stat()
        except OSError:
            sidecar_stat = None
        cache_hint = _inline_card_fetched_at(report) or (report.get("card_pool") or {}).get("fetched_at")
        if cache_hint:
            cache_key = (str(raw_path), str(cache_hint))
            with _card_pool_cache_lock:
                cached = _card_pool_cache.get(cache_key)
            # an entry with no mtime is one this process seeded from memory before (or
            # while) writing the sidecar; it is authoritative for its fetched_at regardless of
            # what is on disk. An entry with an mtime must still match the file.
            if cached is not None and (cached[0] is None or (sidecar_stat is not None and cached[0] == sidecar_stat.st_mtime_ns)):
                cached_sidecar = {"cards": cached[1], "fetched_at": cached[2]}
                cards, _fetched_at = _choose_card_pool(report, cached_sidecar)
                if isinstance(report.get("cards"), list) and cards is report["cards"]:
                    return report
                hydrated = dict(report)
                hydrated["cards"] = cards
                return hydrated
    sidecar = _read_card_sidecar(Path(str(raw_path))) if raw_path else None
    sidecar_payload = sidecar[0] if sidecar is not None else None
    cards, _fetched_at = _choose_card_pool(report, sidecar_payload)
    if isinstance(report.get("cards"), list) and cards is report["cards"]:
        return report
    if sidecar is None:
        hydrated = dict(report)
        hydrated["cards"] = cards
        return hydrated
    sidecar_stat = sidecar[1]
    expected = str(_inline_card_fetched_at(report) or (report.get("card_pool") or {}).get("fetched_at") or _fetched_at or "")
    cache_key = (str(raw_path), expected)
    with _card_pool_cache_lock:
        cached = _card_pool_cache.get(cache_key)
        if cached is not None and cached[0] == sidecar_stat:
            hydrated = dict(report)
            hydrated["cards"] = cached[1]
            return hydrated
    with _card_pool_cache_lock:
        _remember_card_pool(cache_key, (sidecar_stat, sidecar_payload["cards"], str(sidecar_payload["fetched_at"])))
    hydrated = dict(report)
    hydrated["cards"] = cards
    return hydrated


def latest_snapshot(config: CeresConfig, player: str) -> tuple[dict[str, Any] | None, SnapshotRef | None]:
    """Read the newest snapshot from disk. Returns (None, None) if none exists yet.
    Performs no network I/O. Cached by (path, mtime_ns) -- see `_snapshot_cache`
    above -- so repeat requests against an unchanged snapshot skip the re-parse."""
    path = _latest_path(config, player)
    try:
        snapshot_stat = path.stat()
    except OSError:
        return None, None
    key = str(path)
    with _snapshot_cache_lock:
        cached = _snapshot_cache.get(key)
        if cached is not None and cached[0] == snapshot_stat.st_mtime_ns:
            report = cached[1]
            _snapshot_cache.move_to_end(key)
        else:
            report = load_snapshot(path, include_cards=False)
            if isinstance(report.get("cards"), list):
                sidecar_path = _cards_path(config, player)
                try:
                    sidecar = _read_card_sidecar(sidecar_path)
                    selected_cards, selected_fetched_at = _choose_card_pool(report, sidecar[0] if sidecar else None)
                    if sidecar is None or selected_cards is report["cards"]:
                        _write_card_pool(report, sidecar_path)
                    snapshot_report = _snapshot_without_card_pool(report, selected_cards, selected_fetched_at)
                    if snapshot_report.get("card_pool"):
                        save_snapshot(snapshot_report, path)
                        report = snapshot_report
                        snapshot_stat = path.stat()
                except CardPoolUnavailable:
                    raise
                except (OSError, ValueError) as exc:
                    logger.warning("legacy card pool migration failed for %s: %s", player, exc)
            _put_snapshot_cache_locked(key, snapshot_stat.st_mtime_ns, report, snapshot_stat.st_size)
    report, patch_token = _merge_plot_patches(config, player, report, snapshot_stat)
    _with_card_context(report, config, player)
    publication = _board_artifact_identity(config, player, path, snapshot_stat)
    ref = SnapshotRef(
        path=str(path),
        generated_at=report.get("generated_at"),
        player=player,
        token=(f"{publication.token}-p{patch_token}" if publication is not None and patch_token else publication.token if publication is not None else None),
        write_counter=publication.write_counter if publication is not None else None,
        snapshot_mtime_ns=snapshot_stat.st_mtime_ns,
        snapshot_size=snapshot_stat.st_size,
        details_mtime_ns=publication.details_mtime_ns if publication is not None else None,
        details_size=publication.details_size if publication is not None else None,
    )
    return report, ref


def _board_artifact_identity(config: CeresConfig, player: str, path: Path, snapshot_stat: os.stat_result | None):
    from . import board_artifact

    identity = board_artifact.read_identity(config.snapshots_dir, player)
    if identity is None or snapshot_stat is None or not identity.complete:
        return identity
    if identity.snapshot_path != str(path):
        return None
    if identity.snapshot_mtime_ns != snapshot_stat.st_mtime_ns or identity.snapshot_size != snapshot_stat.st_size:
        return None
    return identity


def _save_snapshot_and_refresh_cache(
    report: dict[str, Any], path: Path, *, pool_may_have_changed: bool = True, before_pool_write: Any = None,
) -> None:
    report_player = str(report.get("query", {}).get("player", ""))
    if not report_player:
        report_player = path.name.removeprefix("land-").removesuffix("-latest.json")
    player = security.validate_player(report_player)
    with _snapshot_write_lock(player):
        _save_snapshot_and_refresh_cache_locked(
            report,
            path,
            pool_may_have_changed=pool_may_have_changed,
            before_pool_write=before_pool_write,
        )


def _save_snapshot_and_refresh_cache_locked(
    report: dict[str, Any], path: Path, *, pool_may_have_changed: bool = True, before_pool_write: Any = None,
) -> tuple[Any, dict[str, Any], dict[str, Any], Path | None, str | None]:
    """Write a new card pool before the snapshot that names it, then push the snapshot into
    `_snapshot_cache` ourselves rather than relying on a later `stat()` to notice
    the write. A quick-refresh-then-expand sequence (`_save_merged` then
    `expand_plot`, both below) can write twice fast enough that the filesystem's
    mtime resolution doesn't distinguish the two writes -- observed causing
    `latest_snapshot()` to keep serving the pre-merge cached copy after
    `expand_plot` had already persisted the merge. Overwriting the cache entry
    unconditionally (not comparing mtimes) sidesteps that resolution question
    entirely for writes this process makes itself."""
    from . import board_artifact

    report_player = str(report.get("query", {}).get("player", ""))
    if not report_player:
        report_player = path.name.removeprefix("land-").removesuffix("-latest.json")
    player = security.validate_player(report_player)
    with board_artifact.publication_lock(path.parent, player):
        token, write_counter, epoch = board_artifact.reserve_identity(path.parent, player)
        collection = report.get("coverage", {}).get("collection", {})
        has_new_pool = isinstance(report.get("cards"), list) and isinstance(collection, dict) and collection.get("complete") is True
        write_pool = bool(has_new_pool and pool_may_have_changed and report.get("cards"))
        pool_path = None
        pool_fetched_at = None
        if write_pool:
            pool_path = path.with_name(f"land-{player}-cards-latest.json")
            pool_fetched_at = str(collection.get("fetched_at") or report.get("generated_at") or "")
            with _card_pool_cache_lock:
                _remember_card_pool((str(pool_path), pool_fetched_at), (None, report["cards"], pool_fetched_at))
            card_pool_index.invalidate(player)
        snapshot_report = _snapshot_without_card_pool(report)
        if pool_path is not None:
            # the fixed-name sidecar must exist before a snapshot that points at its
            # fetched_at is visible. The former snapshot-first order left the web process
            # reading the preceding sidecar while the capture worker encoded the new one.
            _write_card_pool(report, pool_path)
            try:
                pool_mtime = pool_path.stat().st_mtime_ns
            except OSError:
                pool_mtime = None
            with _card_pool_cache_lock:
                _remember_card_pool((str(pool_path), pool_fetched_at), (pool_mtime, report["cards"], pool_fetched_at))
        save_snapshot(snapshot_report, path)
        try:
            snapshot_stat = path.stat()
        except OSError:
            snapshot_stat = None
        if snapshot_stat is not None:
            with _snapshot_cache_lock:
                _put_snapshot_cache_locked(str(path), snapshot_stat.st_mtime_ns, snapshot_report, snapshot_stat.st_size)
            details_mtime_ns, details_size = board_artifact.details_identity()
            identity = board_artifact.commit_snapshot_identity(
                path.parent,
                player,
                token,
                write_counter,
                epoch,
                path,
                snapshot_stat,
                details_mtime_ns,
                snapshot_generated_at=snapshot_report.get("generated_at"),
                details_size=details_size,
            )
        else:
            identity = None
        # ordering guarantee: the artifact (+ .gz) is on disk before the legacy-named
        # `before_pool_write` callback runs, so `board_ready` is never true without a servable
        # artifact. also requires its card sidecar to be present by this point.
        if identity is not None:
            _publish_board_artifact(path.parent, player, identity, snapshot_report)
        for patch_path in path.parent.glob(f"land-{player}-plot-*.json"):
            try:
                patch = fastjson.loads(patch_path.read_bytes())
            except (OSError, ValueError):
                patch = None
            if not isinstance(patch, dict) or snapshot_stat is None or (
                patch.get("base_mtime_ns"), patch.get("base_size")
            ) != (snapshot_stat.st_mtime_ns, snapshot_stat.st_size):
                patch_path.unlink(missing_ok=True)
        with _plot_overlay_lock:
            _plot_overlay_cache.pop(str(path), None)
        if before_pool_write is not None:
            try:
                # a hook that takes an argument receives the publication identity (token
                # generated_at) so the board_ready event can name the snapshot it announces.
                try:
                    takes_identity = bool(inspect.signature(before_pool_write).parameters)
                except (TypeError, ValueError):
                    takes_identity = False
                before_pool_write(identity) if takes_identity else before_pool_write()
            except Exception as exc:
                logger.warning("board-ready callback failed: %s", exc)
        return identity, snapshot_report, report, pool_path, pool_fetched_at


def freshness(config: CeresConfig, player: str) -> CaptureStatus:
    """Combine the in-memory capture-run state with the on-disk snapshot's age.
    Performs no network I/O -- reads only the local snapshot file."""
    run = _run_state.get(player, _RunState())
    report = None
    ref = None
    read_error: str | None = None
    try:
        report, ref = latest_snapshot(config, player)
    except Exception as exc:  # invalid/corrupt snapshot on disk
        logger.warning("failed to read latest snapshot for %s: %s", player, exc)
        read_error = str(exc)
    age = None
    stale = False
    assets = (report or {}).get("coverage", {}).get("assets", {})
    interim = _interim_identity(config, player)
    if interim is not None:
        # the interim artifact IS the current board; the snapshot on disk (if any)
        # is the previous read and must not describe this one.
        assets = dict(interim.artifact_assets or {})
        ref = SnapshotRef(
            path=str(_latest_path(config, player)),
            generated_at=interim.artifact_generated_at,
            player=player,
            token=interim.token,
            write_counter=interim.write_counter,
            details_mtime_ns=interim.details_mtime_ns,
            details_size=interim.details_size,
        )
        read_error = None
    oldest_observed_at = assets.get("oldest_observed_at")
    if ref is not None:
        age_reference = oldest_observed_at or ref.generated_at
        try:
            observed = datetime.fromisoformat(age_reference.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            age = int(max(0, (datetime.now(timezone.utc) - observed).total_seconds()))
            stale = age > config.stale_after_seconds
        except (AttributeError, TypeError, ValueError):
            if age_reference != ref.generated_at:
                generated = datetime.fromisoformat(ref.generated_at.replace("Z", "+00:00"))
                if generated.tzinfo is None:
                    generated = generated.replace(tzinfo=timezone.utc)
                age = int(max(0, (datetime.now(timezone.utc) - generated).total_seconds()))
                stale = age > config.stale_after_seconds
    assets_complete = bool(assets.get("complete"))
    return CaptureStatus(
        state=run.state,
        mode=run.mode,
        started_at=run.started_at,
        finished_at=run.finished_at,
        requests_made=run.requests_made,
        error=run.error or read_error,
        outcome=run.outcome,
        latest_snapshot=ref,
        age_seconds=age,
        stale=stale,
        total_deeds=run.total_deeds,
        fetched_deeds=run.fetched_deeds,
        estimated_seconds=run.estimated_seconds,
        phase=run.phase,
        board_ready=run.board_ready,
        interim=interim is not None,
        queue_position=run.queue_position,
        assets_complete=assets_complete,
        oldest_observed_at=oldest_observed_at,
        assets_retrieved=assets.get("retrieved"),
        assets_expected=assets.get("expected"),
        assets_dropped=assets.get("dropped"),
        assets_failed=assets.get("failed"),
    )


def _interim_identity(config: CeresConfig, player: str) -> Any:
    """: the current publication identity when it is a complete interim artifact, else None."""
    from . import board_artifact

    try:
        identity = board_artifact.cached_identity(config.snapshots_dir, player) or board_artifact.read_identity(config.snapshots_dir, player)
    except Exception:
        return None
    return identity if identity is not None and identity.interim and identity.complete else None


def freshness_from_board(config: CeresConfig, player: str, ref: SnapshotRef, board: dict[str, Any], interim: bool = False) -> CaptureStatus:
    """Build the status tail from a published board's `meta` (: the identity's
    `generated_at` / `coverage.assets`, never a parsed board) without reading its snapshot."""
    with _lock:
        run = _run_state.get(player, _RunState())
    coverage = board.get("coverage", {})
    assets = coverage.get("assets", {}) if isinstance(coverage, dict) else {}
    if not isinstance(assets, dict) or not assets:
        assets = coverage if isinstance(coverage, dict) else {}
    age = None
    age_reference = assets.get("oldest_observed_at") or board.get("generated_at")
    try:
        observed = datetime.fromisoformat(str(age_reference).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age = int(max(0, (datetime.now(timezone.utc) - observed).total_seconds()))
    except (AttributeError, TypeError, ValueError):
        pass
    return CaptureStatus(
        state=run.state,
        mode=run.mode,
        started_at=run.started_at,
        finished_at=run.finished_at,
        requests_made=run.requests_made,
        error=run.error,
        outcome=run.outcome,
        latest_snapshot=ref,
        age_seconds=age,
        stale=age is not None and age > config.stale_after_seconds,
        total_deeds=run.total_deeds,
        fetched_deeds=run.fetched_deeds,
        estimated_seconds=run.estimated_seconds,
        phase=run.phase,
        board_ready=run.board_ready,
        interim=interim,
        queue_position=run.queue_position,
        assets_complete=bool(assets.get("complete")) or (
            assets.get("expected") is not None and assets.get("retrieved") == assets.get("expected")
        ),
        oldest_observed_at=assets.get("oldest_observed_at"),
        assets_retrieved=assets.get("retrieved"),
        assets_expected=assets.get("expected"),
        assets_dropped=assets.get("dropped"),
        assets_failed=assets.get("failed"),
    )


def _fetch_balances(player: str) -> list[Any] | None:
    """One extra, best-effort read (`SplinterlandsClient.balances`, unauthenticated
    -- confirmed live 2026-08-26, `work/ceres-worksite-phase2/recon-notes.md`)
    alongside every capture. The worksite-change modal's Time Crystal footer and
    the Aura Lab tile's Permit line both need a real balance, and `GET /api/board`
    itself must never trigger a network call (see this module's own docstring) --
    so a capture is the only place in Ceres allowed to fetch it. Fetched ONCE and
    shared between `_tc_balance`/`_permit_balance` below rather than one call per
    token -- the endpoint returns every token in a single response anyway. Never
    raises: a failed fetch must not fail the whole land capture, and both callers
    already render an honest "balance unknown" state (never a silent zero) when
    this returns None."""
    try:
        return SplinterlandsClient().balances(player).data
    except Exception as exc:  # network/schema/upstream -- keep serving the previous balances
        logger.warning("balances fetch failed for %s: %s", player, exc)
        return None


_OPTIONAL_BALANCE_WAIT_SECONDS = 0.0


def _start_optional_balances(player: str) -> tuple[ThreadPoolExecutor, Any]:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ceres-balances")
    return executor, executor.submit(_fetch_balances, player)


def _finish_optional_balances(executor: ThreadPoolExecutor, future: Any, player: str) -> list[Any] | None:
    try:
        return future.result(timeout=_OPTIONAL_BALANCE_WAIT_SECONDS)
    except FutureTimeoutError:
        logger.info("balances not ready for %s; saving the board with unknown footer balances", player)
        return None
    except Exception as exc:
        logger.warning("optional balances read failed for %s: %s", player, exc)
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _extract_balance(rows: list[Any] | None, token: str) -> dict[str, Any] | None:
    """Pull one token's balance out of a `_fetch_balances` result. No matching row
    at all is treated as UNKNOWN, not zero -- a missing row and a genuine zero
    balance are indistinguishable from this response alone, and the standing rule
    here is an honest unknown over a fabricated number."""
    if not rows:
        return None
    for row in rows:
        if isinstance(row, dict) and str(row.get("token", "")).upper() == token:
            try:
                value = float(row.get("balance"))
            except (TypeError, ValueError):
                return None
            return {"value": value, "fetched_at": _now_iso(), "last_update_date": row.get("last_update_date")}
    return None


def _tc_balance(rows: list[Any] | None) -> dict[str, Any] | None:
    return _extract_balance(rows, "TC")


def _permit_balance(rows: list[Any] | None) -> dict[str, Any] | None:
    """Aura Lab Permit balance -- `PERMIT.AL` on the same balances endpoint (confirmed
    live 2026-08-26, `work/ceres-permit-balance/`). Account-bound, non-transferable;
    required to build or change into an Aura Lab (`support.splinterlands.com`'s
    Aura Lab and Artisan Quarter Item Crafting FAQs)."""
    return _extract_balance(rows, "PERMIT.AL")


def _refresh_card_details() -> None:
    """Best-effort refresh of Praetoria's OWN `card_details` cache (the shared,
    default `~/.praetoria/cache/card_details.json` -- not Ceres's `~/.ceres/`
    snapshot store), which `board.py::load_details_by_id` reads offline-only.
    Only a capture is allowed to make a network call (see this module's
    docstring), so this is the one place that keeps that cache populated --
    never seeded or rsynced by deploy tooling, which goes stale silently the
    moment the game adds a card. TTL 86400 matches every other `card_details`
    consumer (`praetoria/cli.py`, `praetoria/query.py`), so a healthy cache
    costs zero extra requests on most captures. Never raises: a failed fetch
    leaves whatever cache entry (fresh, stale, or absent) already existed in
    place, and `board.py` already renders degraded (null) ability fields
    rather than failing when the cache is missing."""
    try:
        JsonCache().get_or_fetch("card_details", 86400, False, False, SplinterlandsClient().details)
    except Exception as exc:  # keep the capture itself alive; card art/detail degrades, not the board
        logger.warning("card_details cache refresh failed: %s", exc)


_DEFAULT_ASSET_STALE_AFTER_SECONDS = 60


def _contradicted_by_bulk_count(plot: dict[str, Any], base_worker_count: int) -> bool:
    incoming_worker_count = (plot.get("staking_facts") or {}).get("worker_count")
    if incoming_worker_count is None:
        return False
    try:
        return int(float(incoming_worker_count)) != base_worker_count
    except (TypeError, ValueError):
        return False


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=timezone.utc)


def _collection_join_refs(report: dict[str, Any]) -> dict[str, set[str]]:
    refs_by_deed: dict[str, set[str]] = {}
    for assignment in report.get("assignments", []):
        if assignment.get("confidence") != "collection_join":
            continue
        deed_uid = assignment.get("deed_uid")
        asset_ref = assignment.get("asset_ref")
        if deed_uid is not None and asset_ref is not None:
            refs_by_deed.setdefault(str(deed_uid), set()).add(str(asset_ref))
    for worker in report.get("workers", []):
        if worker.get("confidence") != "collection_join":
            continue
        deed_uid = worker.get("stake_ref_uid")
        asset_ref = worker.get("asset_ref")
        if deed_uid is not None and asset_ref is not None:
            refs_by_deed.setdefault(str(deed_uid), set()).add(str(asset_ref))
    return refs_by_deed


def _collection_join_pending_detail(report: dict[str, Any], workers: list[dict[str, Any]]) -> dict[str, list[str]]:
    workers_by_deed_ref = {
        (str(worker.get("stake_ref_uid")), str(worker.get("asset_ref"))): worker
        for worker in workers
        if worker.get("stake_ref_uid") is not None and worker.get("asset_ref") is not None
    }
    pending: dict[str, list[str]] = {}
    for deed_uid, asset_refs in _collection_join_refs(report).items():
        offending_refs: list[str] = []
        for asset_ref in asset_refs:
            worker = workers_by_deed_ref.get((deed_uid, asset_ref))
            if worker is None or worker.get("slot") is None:
                offending_refs.append(asset_ref)
        if offending_refs:
            pending[deed_uid] = sorted(offending_refs)
    return pending


def _annotate_pending_detail(
    report: dict[str, Any], pending_detail: dict[str, list[str]],
) -> dict[str, dict[str, str]]:
    workers = [dict(worker) for worker in report.get("workers", [])]
    workers_by_deed_ref = {
        (str(worker.get("stake_ref_uid")), str(worker.get("asset_ref"))): worker
        for worker in workers
        if worker.get("stake_ref_uid") is not None and worker.get("asset_ref") is not None
    }
    for worker in workers:
        worker["detail_state"] = "complete"
        worker.pop("detail_pending_reason", None)

    reasons: dict[str, dict[str, str]] = {}
    for deed_uid, asset_refs in pending_detail.items():
        deed_reasons: dict[str, str] = {}
        for asset_ref in asset_refs:
            worker = workers_by_deed_ref.get((deed_uid, asset_ref))
            reason = "roster_predates_stake" if worker is not None and worker.get("slot") is None else "no_roster_row"
            deed_reasons[asset_ref] = reason
            if worker is not None:
                worker["detail_state"] = "pending"
                worker["detail_pending_reason"] = reason
        reasons[deed_uid] = deed_reasons
    report["workers"] = workers

    plots: list[dict[str, Any]] = []
    for plot in report.get("plots", []):
        deed_uid = str(plot.get("deed_uid"))
        refs = pending_detail.get(deed_uid)
        if not refs:
            asset_fetch = plot.get("asset_fetch") or {}
            if "worker_detail_pending" not in asset_fetch:
                plots.append(plot)
                continue
            cleaned_plot = dict(plot)
            cleaned_asset_fetch = dict(asset_fetch)
            cleaned_asset_fetch.pop("worker_detail_pending", None)
            cleaned_plot["asset_fetch"] = cleaned_asset_fetch
            plots.append(cleaned_plot)
            continue
        annotated_plot = dict(plot)
        asset_fetch = dict(annotated_plot.get("asset_fetch") or {})
        asset_fetch["worker_detail_pending"] = list(refs)
        annotated_plot["asset_fetch"] = asset_fetch
        plots.append(annotated_plot)
    report["plots"] = plots
    return reasons


def _record_pending_detail_observability(
    report: dict[str, Any], pending_detail: dict[str, list[str]], reasons: dict[str, dict[str, str]],
) -> None:
    if not pending_detail:
        return
    assets = report.get("coverage", {}).get("assets", {})
    queued = set(assets.get("needs_refetch", []))
    diagnostics = list(report.get("diagnostics") or [])
    # (2026-09-07): `if diagnostic not in diagnostics` compared every new entry against
    # every existing one by dict equality -- 4,461 pending deeds x 4,465 diagnostics on a whale
    # account, 2.0 s on EVERY merge, i.e. every plot open. A key set does the same de-dup
    # (same code + deed + refs + reasons + queued flag) in one pass.
    def _key(entry: dict[str, Any]) -> tuple:
        return (
            entry.get("code"), entry.get("deed_uid"), tuple(entry.get("asset_refs") or ()),
            tuple(sorted((entry.get("reasons") or {}).items())), bool(entry.get("queued_for_refetch")),
        )
    seen = {_key(entry) for entry in diagnostics if isinstance(entry, dict) and entry.get("code") == "collection_join_worker_detail_pending"}
    for deed_uid, asset_refs in pending_detail.items():
        diagnostic = {
            "code": "collection_join_worker_detail_pending",
            "deed_uid": deed_uid,
            "asset_refs": list(asset_refs),
            "reasons": dict(reasons[deed_uid]),
            "queued_for_refetch": deed_uid in queued,
            "reason": (
                f"{len(asset_refs)} collection-join worker row(s) have no per-deed roster detail; "
                "plot coverage retained, worker detail pending refetch"
            ),
        }
        key = _key(diagnostic)
        if key not in seen:
            seen.add(key)
            diagnostics.append(diagnostic)
    report["diagnostics"] = diagnostics
    summary_parts = []
    for deed_uid, asset_refs in sorted(pending_detail.items())[:10]:
        rendered_refs = ",".join(f"{asset_ref}:{reasons[deed_uid][asset_ref]}" for asset_ref in asset_refs)
        summary_parts.append(f"{deed_uid}[{rendered_refs}]")
    omitted = len(pending_detail) - len(summary_parts)
    suffix = f" (+{omitted} more)" if omitted else ""
    logger.warning(
        "collection-join worker detail pending on %d deed(s) after merge (%d queued for refetch): %s",
        len(pending_detail), sum(deed_uid in queued for deed_uid in pending_detail), ", ".join(summary_parts) + suffix,
    )


def _recompute_asset_coverage(report: dict[str, Any], stale_after_seconds: int) -> None:
    plots = report.get("plots", [])
    statuses = [(plot, (plot.get("asset_fetch") or {}).get("status")) for plot in plots]
    retrieved = sum(status == "retrieved" for _, status in statuses)
    dropped = sum(status == "dropped_stale" for _, status in statuses)
    failed = sum(status == "failed" for _, status in statuses)
    pending_deeds = [
        str(plot.get("deed_uid"))
        for plot, _ in statuses
        if (plot.get("asset_fetch") or {}).get("worker_detail_pending")
    ]
    not_requested = sum(status == "not_requested" or status is None for _, status in statuses)
    now = datetime.now(timezone.utc)
    observed: list[tuple[datetime, str]] = []
    fresh = 0
    for plot, status in statuses:
        stamp = plot.get("observed_at")
        parsed = _parse_timestamp(stamp)
        if parsed is not None:
            observed.append((parsed, stamp))
            if status == "retrieved" and (now - parsed).total_seconds() <= stale_after_seconds:
                fresh += 1
    total = len(plots)
    coverage = report.get("coverage", {})
    coverage = dict(coverage) if isinstance(coverage, dict) else {}
    assets = coverage.get("assets", {})
    assets = dict(assets) if isinstance(assets, dict) else {}
    assets["retrieved"] = retrieved
    assets["expected"] = total
    assets["complete"] = total > 0 and retrieved == total
    assets["dropped"] = dropped
    assets["failed"] = failed
    assets["not_requested"] = not_requested
    assets["worker_detail_pending"] = len(pending_deeds)
    assets["worker_detail_pending_deeds"] = pending_deeds
    assets["oldest_observed_at"] = min(observed, key=lambda item: item[0])[1] if observed else None
    assets["fresh"] = fresh
    assets["current"] = assets["complete"] and fresh == total
    needs_refetch: list[str] = []
    for plot, status in statuses:
        uid = plot.get("deed_uid")
        if status in {"dropped_stale", "failed"} and uid not in needs_refetch:
            needs_refetch.append(uid)
    failure_count = len(needs_refetch)
    pending_plots = [
        plot for plot, _ in statuses
        if (plot.get("asset_fetch") or {}).get("worker_detail_pending")
    ]
    pending_plots.sort(
        key=lambda plot: (
            _parse_timestamp(plot.get("observed_at")) is not None,
            _parse_timestamp(plot.get("observed_at")) or datetime.min.replace(tzinfo=timezone.utc),
        )
    )
    for plot in pending_plots:
        uid = plot.get("deed_uid")
        if uid in needs_refetch:
            continue
        needs_refetch.append(uid)
        if len(needs_refetch) - failure_count >= _MAX_PENDING_REFETCH:
            break
    # a plot whose assets were NEVER fetched (`not_requested` / no status)
    # was re-queued by nothing, so a quick read left it unloaded until a full read -- the
    # live board sat at "107 of 129 plots loaded" for ten days. Every such plot is queued
    # uncapped; `_self_heal_refetches` fetches all of them in the same capture.
    for plot, status in statuses:
        uid = plot.get("deed_uid")
        if status in (None, "not_requested") and uid is not None and uid not in needs_refetch:
            needs_refetch.append(uid)
    assets["needs_refetch"] = needs_refetch
    coverage["assets"] = assets
    report["coverage"] = coverage


def merge_reports(
    base: dict[str, Any] | None,
    incoming: dict[str, Any],
    *,
    stale_after_seconds: int = _DEFAULT_ASSET_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """Combine a fresh capture with prior per-deed asset coverage.

    A genuinely retrieved incoming deed wins. Otherwise a prior retrieved deed is
    carried unless `_contradicted_by_bulk_count` proves its detail stale. A prior
    `failed` or `dropped_stale` deed outranks an incoming `not_requested` deed, keeping
    its reason and observation timestamp until a later retrieval clears it. Warnings
    are an order-stable union of incoming warnings and base warnings that still
    describe a failed or dropped deed.

    The function performs no network or file I/O. It recomputes
    `coverage.assets.needs_refetch` on every merge for `dropped_stale`, `failed`, and
    capped pending-detail deeds; capture runners consume that queue separately.
    """
    capture = incoming.get("capture") or {}
    if capture.get("mode") in {"quick", "quick_bulk"} and capture.get("offline") is False:
        # A fresh board cannot relabel prior per-deed assets as part of this read.
        # Current bulk facts and the collection provide the board; asset identities
        # load on demand through the existing fresh per-deed path.
        base = None
    if base is None:
        pending_detail = _collection_join_pending_detail(incoming, incoming.get("workers", []))
        reasons = _annotate_pending_detail(incoming, pending_detail)
        _recompute_asset_coverage(incoming, stale_after_seconds)
        _record_pending_detail_observability(incoming, pending_detail, reasons)
        return incoming
    base_plots = {p["deed_uid"]: p for p in base.get("plots", [])}
    # Per-deed worker COUNT from base's own per-deed asset rows (`base["workers"]`)
    # used below as a cheap, no-extra-fan-out contradiction check against the bulk
    # `worker_count` a quick refresh always has, even without per-deed assets. This is
    # the guard against carrying a stale enriched plot forward past a mutation (a card
    # unstaked through Ceres itself, or in-game) that the bulk deed count already knows
    # about: see the `domain-confirmed` capture-refresh fix in `routes_op_preview.py`
    # for the primary fix -- this is the cheap class-wide backstop for every OTHER path
    # that can leave a stale worker behind a quick refresh.
    base_worker_count_by_deed: dict[str, int] = {}
    for worker in base.get("workers", []):
        stake_ref = worker.get("stake_ref_uid")
        if stake_ref:
            base_worker_count_by_deed[stake_ref] = base_worker_count_by_deed.get(stake_ref, 0) + 1

    merged_plots: list[dict[str, Any]] = []
    retrieved_uids: set[str] = set()
    carried_uids: set[str] = set()
    generated_warnings: list[str] = []
    generated_diagnostics: list[dict[str, Any]] = []
    for plot in incoming.get("plots", []):
        uid = plot["deed_uid"]
        incoming_status = (plot.get("asset_fetch") or {}).get("status", "not_requested")
        if incoming_status == "retrieved":
            merged_plots.append(plot)
            retrieved_uids.add(uid)
            continue
        base_plot = base_plots.get(uid)
        base_asset = (base_plot or {}).get("asset_fetch") or {}
        base_status = base_asset.get("status")
        base_retrieved = base_status == "retrieved"
        if base_retrieved and _contradicted_by_bulk_count(plot, base_worker_count_by_deed.get(uid, 0)):
            incoming_worker_count = (plot.get("staking_facts") or {}).get("worker_count")
            base_worker_count = base_worker_count_by_deed.get(uid, 0)
            reason = (
                f"bulk worker_count {incoming_worker_count} contradicts {base_worker_count} "
                "retrieved worker rows; detail dropped as stale"
            )
            dropped_plot = dict(plot)
            dropped_plot["asset_fetch"] = {"status": "dropped_stale", "reason": reason, "dropped_at": _now_iso()}
            merged_plots.append(dropped_plot)
            # (2026-09-14): dropping outdated detail after the maintainer's own change is normal
            # self-healing (the plot reloads its workers when opened) -- a diagnostic, never a
            # `warnings` entry, which the board renders as a bar. The drop itself stays ().
            generated_diagnostics.append(
                {
                    "code": "worker_detail_dropped_stale",
                    "deed_uid": uid,
                    "bulk_worker_count": incoming_worker_count,
                    "retrieved_worker_count": base_worker_count,
                    "reason": reason,
                }
            )
            continue
        if base_retrieved:
            # (2026-09-11): carry the prior per-deed ASSET detail only. The bulk sections
            # (facts, staking, worksite, metrics, diagnostics) come from THIS capture's live bulk
            # read; carrying them made the board keep a days-old worksite record (065-04-029 said
            # last harvest Sep 6 07:13 while the live bulk said 11 Sep 13:34), which the harvest
            # box then painted as a far-too-high capacity.
            carried = dict(base_plot)
            for key in ("facts", "staking_facts", "worksite_facts", "metrics", "diagnostics"):
                if key in plot:
                    carried[key] = plot[key]
            merged_plots.append(carried)
            retrieved_uids.add(uid)
            carried_uids.add(uid)
        elif base_plot is not None and base_status in {"failed", "dropped_stale"} and incoming_status == "not_requested":
            merged_plots.append(base_plot)
        else:
            merged_plots.append(plot)

    merged = dict(incoming)
    merged["plots"] = merged_plots

    incoming_workers = list(incoming.get("workers", []))
    incoming_worker_refs = {
        worker.get("asset_ref") for worker in incoming_workers if worker.get("asset_ref") is not None
    }
    base_workers_by_ref = {
        worker.get("asset_ref"): worker
        for worker in base.get("workers", [])
        if worker.get("asset_ref") is not None
    }
    merged_workers: list[dict[str, Any]] = []
    for worker in incoming_workers:
        base_worker = base_workers_by_ref.get(worker.get("asset_ref"))
        if (
            base_worker is not None
            and worker.get("stake_ref_uid") == base_worker.get("stake_ref_uid")
            and worker.get("stake_ref_uid") in retrieved_uids
        ):
            enriched_worker = dict(base_worker)
            enriched_worker.update(worker)
            merged_workers.append(enriched_worker)
        else:
            merged_workers.append(worker)
    pending_detail = _collection_join_pending_detail(incoming, merged_workers)
    merged["workers"] = merged_workers + [
        dict(worker) for worker in base.get("workers", [])
        if worker.get("stake_ref_uid") in carried_uids
        and worker.get("stake_ref_uid") not in pending_detail
        and worker.get("asset_ref") not in incoming_worker_refs
    ]
    incoming_bonus_refs = {
        bonus.get("asset_ref") for bonus in incoming.get("bonus_assets", []) if bonus.get("asset_ref") is not None
    }
    merged["bonus_assets"] = list(incoming.get("bonus_assets", [])) + [
        bonus for bonus in base.get("bonus_assets", [])
        if bonus.get("stake_ref_uid") in carried_uids
        and bonus.get("asset_ref") not in incoming_bonus_refs
    ]
    reasons = _annotate_pending_detail(merged, pending_detail)

    # expand_deed() carries no account-wide collection sweep or unassigned-item capture
    # of its own (as_of=None, collection_payload=None) -- keep the base's if incoming's
    # own is missing, so a single row-expand never wipes those out.
    # `coverage["collection"]` must travel WITH `cards` whenever incoming is incomplete
    # because `expand_deed()` passes `collection_payload=None`, and a capture whose
    # collection sweep fails also arrives without a complete collection. Otherwise
    # carrying the base's real 7,000+ card collection forward while leaving incoming's
    # coverage flag in place made a snapshot that actually HAD a complete collection
    # look incomplete again (), re-breaking combine previews.
    incoming_collection = incoming.get("coverage", {}).get("collection")
    incoming_collection_complete = isinstance(incoming_collection, dict) and incoming_collection.get("complete") is True
    base_has_card_pool = bool(base.get("cards")) or bool(base.get("card_index")) or bool(base.get("card_pool"))
    carried_forward_cards = not incoming_collection_complete and base_has_card_pool
    if not incoming_collection_complete and isinstance(base.get("card_index"), list):
        merged["card_index"] = list(base["card_index"])
    if carried_forward_cards:
        if "cards" in base:
            merged["cards"] = base["cards"]
        for key in ("card_index", "card_pool"):
            if key in base:
                merged[key] = base[key]
    if not incoming.get("unassigned_items") and base.get("unassigned_items"):
        merged["unassigned_items"] = base["unassigned_items"]
    if not incoming.get("item_catalogue") and base.get("item_catalogue"):
        merged["item_catalogue"] = base["item_catalogue"]
    # A failed/skipped balance fetch on THIS capture must not blank out a balance a
    # prior capture already earned -- same "never regress" principle as the two
    # fields above.
    if not incoming.get("tc_balance") and base.get("tc_balance"):
        merged["tc_balance"] = base["tc_balance"]
    if not incoming.get("permit_balance") and base.get("permit_balance"):
        merged["permit_balance"] = base["permit_balance"]

    warnings = list(incoming.get("warnings") or []) + generated_warnings
    carried_statuses = {
        plot["deed_uid"]: (plot.get("asset_fetch") or {}).get("status")
        for plot in merged_plots
    }
    for warning in base.get("warnings", []) or []:
        if any(
            status in {"failed", "dropped_stale"} and f"deed {uid}" in str(warning)
            for uid, status in carried_statuses.items()
        ):
            warnings.append(warning)
    merged["warnings"] = list(dict.fromkeys(warnings))
    diagnostics = list(incoming.get("diagnostics") or [])
    for diagnostic in generated_diagnostics:
        if diagnostic not in diagnostics:
            diagnostics.append(diagnostic)
    for diagnostic in base.get("diagnostics", []) or []:
        if not isinstance(diagnostic, dict):
            continue
        if carried_statuses.get(diagnostic.get("deed_uid")) in {"failed", "dropped_stale"} and diagnostic not in diagnostics:
            diagnostics.append(diagnostic)
    merged["diagnostics"] = diagnostics
    if carried_forward_cards:
        base_collection_cov = base.get("coverage", {}).get("collection")
        if isinstance(base_collection_cov, dict):
            merged.setdefault("coverage", {})["collection"] = dict(base_collection_cov)
    _recompute_asset_coverage(merged, stale_after_seconds)
    _record_pending_detail_observability(merged, pending_detail, reasons)
    return merged


def estimate_capture(player: str) -> dict[str, Any]:
    """Preflight the deed count (one cheap, cache-reusable request) and report what a
    FULL (per-worker) capture would cost -- shown before committing to one. The default
    board refresh (`start_refresh`, `capture_quick()`) needs no estimate: its cost is a
    small, account-size-independent handful of requests, never the per-deed fan-out."""
    player = security.validate_player(player)
    capture = LandCapture()
    capture.persist_cache = persist_static_reference_only  # the owned counts are read live, not written
    deed_count = capture.preflight_deed_count(player)
    budget = derive_request_budget(deed_count)
    seconds = estimate_capture_seconds(deed_count)
    return {
        "player": player,
        "deeds": deed_count,
        "requests_estimated": budget,
        "estimated_seconds": round(seconds, 1),
        "estimated_minutes": round(seconds / 60, 1),
    }


def is_running(player: str) -> bool:
    player = security.validate_player(player)
    # A replacement waiting for the prior worker to exit is still an active read.
    return _run_state.get(player, _RunState()).state in ("running", "queued")


def _begin_run(player: str, mode: str) -> bool:
    with _lock:
        if _run_state.get(player, _RunState()).state in ("running", "queued"):
            return False
        _run_state[player] = _RunState(state="running", mode=mode, started_at=_now_iso(), run_token=object())
    return True


def _in_process_requested() -> bool:
    """`CERES_CAPTURE_IN_PROCESS=1` keeps the pre- in-process thread path (tests with
    fake `LandCapture` classes need it: a monkeypatch never reaches a child process)."""
    return os.getenv("CERES_CAPTURE_IN_PROCESS", "0").strip().lower() in {"1", "true", "yes", "on"}


CAPTURE_MEMORY_MB_DEFAULT = 2048
CAPTURE_MEMORY_MB_MAX = 3072
# RLIMIT_DATA counts the allocator's RESERVATIONS, not resident memory. orjson parsing a
# 98.7 MB snapshot peaked at 638 MB RSS but needed ~1.3 GB of DATA room (cap 1100 -> allocation
# failure, cap 1400 -> SIGSEGV, cap 1600 -> fine), i.e. ~13x the file size, so the cap is sized
# from the stored board: 14x its size plus headroom, never below the default.
_CAPTURE_MEMORY_SNAPSHOT_FACTOR = 14
_CAPTURE_MEMORY_HEADROOM_MB = 256
# Set by the worker child to the cap it applied, so a failure can name it ().
_active_capture_cap_mb: int | None = None


def capture_memory_mb(snapshot_size_bytes: int | None = None) -> int:
    """: the worker child's hard memory cap (RLIMIT_DATA). Default 2048 MB (; the production
    server has 3.9 GB RAM and MemoryMax=3G on the unit), overridable with `CERES_CAPTURE_MEMORY_MB`.
    Given the player's stored snapshot size, the cap grows to `size * 14 + 256 MB`, capped at
    3072 MB -- orjson needs ~13x the file size in RLIMIT_DATA room to parse it (see above)."""
    try:
        default = max(64, int(os.getenv("CERES_CAPTURE_MEMORY_MB", str(CAPTURE_MEMORY_MB_DEFAULT))))
    except ValueError:
        default = CAPTURE_MEMORY_MB_DEFAULT
    if not snapshot_size_bytes or snapshot_size_bytes <= 0:
        return default
    needed = int(snapshot_size_bytes) * _CAPTURE_MEMORY_SNAPSHOT_FACTOR // (1024 * 1024) + _CAPTURE_MEMORY_HEADROOM_MB
    return min(max(default, needed), CAPTURE_MEMORY_MB_MAX)


def _stored_snapshot_size(config: CeresConfig, player: str) -> int:
    try:
        return _latest_path(config, player).stat().st_size
    except OSError:
        return 0


class SnapshotUnreadableError(RuntimeError):
    """: the existing board snapshot could not be READ (out of memory under the capture
    cap, or any error that is not a corrupt file). The save aborts and nothing is overwritten."""


def start_refresh(
    config: CeresConfig,
    player: str,
    *,
    in_process: bool | None = None,
    load_id: str | None = None,
    load_generation: int | None = None,
) -> bool:
    """Start a background QUICK (bulk-only) capture for `player` -- the default board
    refresh, 2026-08-21 correction: the main table needs no per-deed fan-out, so this
    is a small, account-size-independent handful of requests regardless of whether the
    account has 130 deeds or 6,303. A new supervised request supersedes any active
    or not-yet-started request. By default the read is handed to the capture supervisor,
    which runs it in a memory-capped worker child, one heavy job at a time; `in_process=True` (or
    `CERES_CAPTURE_IN_PROCESS=1`) runs it on a thread in this process instead."""
    player = security.validate_player(player)
    if in_process is None:
        in_process = _in_process_requested()
    if not in_process:
        return _supervisor.submit(
            config, player, "quick", load_id=load_id, load_generation=load_generation,
        )
    if not _begin_run(player, "quick"):
        return False
    thread = threading.Thread(target=_run_capture_quick_then_release, args=(config, player), daemon=True)
    thread.start()
    return True


class _RunStateDriver:
    """: the ONE writer of `_run_state` while a capture runs. The capture body never
    touches run state; it reports events (`deed_count`, `progress`, `phase`, `board_ready`,
    `repair`, `done`, `failed`) and this object applies them -- the same way whether the body
    ran on a thread in this process or in a worker child whose stdout lines are replayed here.
    `external=True` (child mode) also drops this process's caches for the player on
    `board_ready`, because the files were written by another process."""

    def __init__(
        self, config: CeresConfig, player: str, mode: str, started_at: str | None, *,
        external: bool = False, run_token: object | None = None,
    ):
        self.config = config
        self.player = player
        self.mode = mode
        self.started_at = started_at
        self.external = external
        self.run_token = run_token
        self.started_monotonic = time.monotonic()
        self.total_deeds: int | None = None
        self.finished = False
        self.events: list[str] = []
        self._timing_recorded = False

    def __call__(self, event: str, **fields: Any) -> None:
        self.handle(event, fields)

    def handle(self, event: str, fields: dict[str, Any]) -> None:
        self.events.append(event)
        player = self.player
        if not self._owns_run():
            return
        if event == "deed_count":
            total = int(fields.get("total") or 0)
            estimated = fields.get("estimated_seconds")
            if estimated is None and self.mode == "quick":
                estimated = estimate_quick_seconds(total, self.config, player)
            self.total_deeds = total
            with _lock:
                state = _run_state.get(player)
                if state is not None and state.state == "running" and state.run_token is self.run_token:
                    state.total_deeds = total
                    if self.mode == "quick":
                        # `fetched_deeds` stays None: the N of M text belongs to the full capture.
                        state.estimated_seconds = estimated
                    else:
                        state.fetched_deeds = 0
        elif event == "progress":
            with _lock:
                state = _run_state.get(player)
                if state is not None and state.state == "running" and state.run_token is self.run_token:
                    state.fetched_deeds = int(fields.get("fetched") or 0)
                    state.total_deeds = int(fields.get("total") or 0)
        elif event == "phase":
            _set_phase(player, str(fields.get("phase")), self.run_token)
        elif event == "board_ready":
            if self.external:
                self._invalidate_parent_caches()
            _mark_board_ready(player, self.run_token)
            self._record_timing()
        elif event == "repair":
            if self.external:
                self._invalidate_parent_caches()
            logger.info("self-heal repaired %s deeds for %s", fields.get("repaired_deeds"), player)
        elif event == "done":
            self.finished = True
            if self.external:
                self._invalidate_parent_caches()
            report = {"capture": {"requests": fields.get("requests"), "cache_hits": fields.get("cache_hits")}}
            _finish_run(player, self.started_at, self.mode, report, fields.get("total_deeds"), self.run_token)
            logger.info(
                "ceres %s capture complete for %s (%s requests, %s cache hits, %s deeds, worker peak RSS %s MB)",
                self.mode, player, fields.get("requests"), fields.get("cache_hits"), fields.get("total_deeds"), fields.get("peak_rss_mb"),
            )
        elif event == "failed":
            self.finished = True
            error = str(fields.get("error"))
            outcome = fields.get("outcome")
            if isinstance(outcome, dict) and outcome.get("code") == "memory_limit":
                # The child hit its RLIMIT_DATA cap: one honest message for the board, the
                # cap size stays in the outcome for the log.
                outcome = {**outcome, "detail": error}
                error = "the read ran out of memory"
            _fail_run(player, self.started_at, self.mode, error, outcome, self.run_token)

    def _owns_run(self) -> bool:
        with _lock:
            state = _run_state.get(self.player)
            return bool(state is not None and state.state == "running" and state.run_token is self.run_token)

    def _record_timing(self) -> None:
        # seconds from run start to board_ready, fed to the measured-rate model. A
        # courtesy, never a failure; one measurement per run, quick reads only.
        # (v0.9.376): measured HERE, in the parent, from the job's own start (the child's
        # spawn included) -- what the page actually waited -- and filed under the player.
        if self._timing_recorded or self.mode != "quick":
            return
        self._timing_recorded = True
        try:
            record_quick_timing(self.total_deeds or 0, time.monotonic() - self.started_monotonic, self.config, self.player)
        except Exception:
            pass

    def _invalidate_parent_caches(self) -> None:
        """The child wrote the snapshot, sidecar, artifact and identity index; drop what this
        process remembers so the next request re-reads them from disk (all caches are also
        mtime/token keyed -- this only removes the seeded entries the in-process path relies on)."""
        from . import board_artifact

        try:
            card_pool_index.invalidate(self.player)
        except Exception:
            pass
        snapshot_key = str(_latest_path(self.config, self.player))
        with _snapshot_cache_lock:
            _drop_snapshot_cache_locked(snapshot_key)
        pool_path = str(_cards_path(self.config, self.player))
        with _card_pool_cache_lock:
            for key in [key for key in _card_pool_cache if key[0] == pool_path]:
                _card_pool_cache.pop(key, None)
        try:
            board_artifact.read_identity(self.config.snapshots_dir, self.player)  # refreshes the identity cache
        except Exception:
            pass


def _drop_snapshot_cache_locked(key: str) -> None:
    global _snapshot_cache_total_bytes
    if key in _snapshot_cache:
        _snapshot_cache.pop(key, None)
        _snapshot_cache_total_bytes -= _snapshot_cache_bytes.pop(key, 0)


@dataclass
class _CaptureJob:
    config: CeresConfig
    player: str
    mode: str
    cancelled: bool = False
    run_token: object = field(default_factory=object)


class CaptureSupervisor:
    """ONE memory-capped worker child at a time, with the latest request taking priority.
    The parent never allocates the whale: the child does the read and every disk write;
    the parent replays the child's stdout events into `_run_state`. A child that dies
    without a `done`/`failed` event (OOM kill, memory cap, crash) fails its run honestly and
    the web process stays small and alive. A replacement request terminates the active
    child and discards any request that has not started yet."""

    TERMINATE_GRACE_SECONDS = 1.0
    CLIENT_GENERATION_LIMIT = 4096

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._wake = threading.Condition(self._guard)
        self._queue: deque[_CaptureJob] = deque()
        self._active: _CaptureJob | None = None
        self._child: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._client_generations: OrderedDict[str, int] = OrderedDict()

    def submit(
        self,
        config: CeresConfig,
        player: str,
        mode: str,
        *,
        load_id: str | None = None,
        load_generation: int | None = None,
    ) -> bool:
        with self._guard:
            if (load_id is None) != (load_generation is None):
                raise ValueError("load_id and load_generation must be supplied together")
            if load_id is not None:
                if not load_id or len(load_id) > 128 or load_generation is None or load_generation < 1:
                    raise ValueError("invalid board-load identity")
                previous_generation = self._client_generations.get(load_id)
                if previous_generation is not None and load_generation <= previous_generation:
                    self._client_generations.move_to_end(load_id)
                    return False
                self._client_generations[load_id] = load_generation
                self._client_generations.move_to_end(load_id)
                while len(self._client_generations) > self.CLIENT_GENERATION_LIMIT:
                    self._client_generations.popitem(last=False)
            job = _CaptureJob(config, player, mode)
            stale_jobs = list(self._queue)
            self._queue.clear()
            if self._active is not None:
                self._active.cancelled = True
                stale_jobs.append(self._active)
                child = self._child
                if child is not None and child.poll() is None:
                    self._terminate_child_locked(child)
            with _lock:
                for stale in stale_jobs:
                    state = _run_state.get(stale.player)
                    if state is not None and state.state in ("running", "queued") and state.run_token is stale.run_token:
                        _run_state[stale.player] = _RunState(
                            state="failed", mode=state.mode, started_at=state.started_at,
                            finished_at=_now_iso(), error="the board read was superseded by a newer request",
                            outcome={"code": "superseded"},
                        )
                _run_state[player] = _RunState(
                    state="queued", mode=mode, started_at=_now_iso(), queue_position=1,
                    run_token=job.run_token,
                )
            self._queue.append(job)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="ceres-capture-supervisor", daemon=True)
                self._thread.start()
            self._wake.notify()
        return True

    def queued_players(self) -> list[str]:
        with self._guard:
            return [job.player for job in self._queue]

    def shutdown(self) -> None:
        with self._guard:
            if self._active is not None:
                self._active.cancelled = True
            child = self._child
            if child is not None and child.poll() is None:
                self._terminate_child_locked(child)

    def _terminate_child_locked(self, child: subprocess.Popen[str]) -> None:
        try:
            child.terminate()
        except OSError:
            return

        def kill_if_needed() -> None:
            with self._guard:
                if self._child is child and child.poll() is None:
                    try:
                        child.kill()
                    except OSError:
                        pass

        timer = threading.Timer(self.TERMINATE_GRACE_SECONDS, kill_if_needed)
        timer.daemon = True
        timer.start()

    def _renumber_locked(self) -> None:
        with _lock:
            for position, job in enumerate(self._queue, start=1):
                state = _run_state.get(job.player)
                if state is not None and state.state == "queued" and state.run_token is job.run_token:
                    state.queue_position = position

    def _loop(self) -> None:
        while True:
            with self._guard:
                while not self._queue:
                    self._wake.wait()
                job = self._queue.popleft()
                self._active = job
                self._renumber_locked()
            try:
                self._run_job(job)
            except Exception as exc:  # the supervisor thread must survive anything
                with self._guard:
                    if not job.cancelled and self._active is job:
                        _fail_run(job.player, None, job.mode, exc, run_token=job.run_token)
            finally:
                with self._guard:
                    if self._active is job:
                        self._active = None

    def _run_job(self, job: _CaptureJob) -> None:
        started_at = _now_iso()
        with self._guard:
            if job.cancelled or self._active is not job:
                return
            with _lock:
                state = _run_state.get(job.player)
                if state is None or state.run_token is not job.run_token:
                    return
                _run_state[job.player] = _RunState(
                    state="running", mode=job.mode, started_at=started_at, run_token=job.run_token,
                )
        driver = _RunStateDriver(
            job.config, job.player, job.mode, started_at, external=True, run_token=job.run_token,
        )
        snapshot_bytes = _stored_snapshot_size(job.config, job.player)
        memory_mb = capture_memory_mb(snapshot_bytes)
        logger.info("ceres %s read for %s: capture cap %d MB (stored board %.1f MB)", job.mode, job.player, memory_mb, snapshot_bytes / (1024 * 1024))
        # `known_plots` is null on purpose: the child sizes its budget from the stored board
        # itself (); the parent never parses a snapshot for a read it does not run.
        known_plots = None
        payload = {
            "player": job.player,
            "mode": job.mode,
            "root": str(job.config.root),
            "stale_after_seconds": int(job.config.stale_after_seconds),
            "capture_concurrency": int(job.config.capture_concurrency),
            "memory_mb": memory_mb,
            "known_plots": known_plots,
        }
        setup = os.getenv("CERES_CAPTURE_WORKER_SETUP")  # tests only: `module:function` run inside the child
        if setup:
            payload["worker_setup"] = setup
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(path for path in sys.path if path)
        try:
            child = subprocess.Popen(
                [sys.executable, "-m", "ceres.capture_worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                text=True, bufsize=1, env=env, cwd=str(_PROJECT_ROOT),
            )
        except OSError as exc:
            with self._guard:
                if not job.cancelled and self._active is job:
                    _fail_run(
                        job.player, started_at, job.mode,
                        f"could not start the board read worker: {exc}", run_token=job.run_token,
                    )
            return
        with self._guard:
            self._child = child
            if job.cancelled or self._active is not job:
                self._terminate_child_locked(child)
        returncode: int | None = None
        stream_complete = False
        try:
            assert child.stdin is not None
            try:
                child.stdin.write(json.dumps(payload) + "\n")
            except (OSError, ValueError):
                pass
            finally:
                try:
                    child.stdin.close()
                except (OSError, ValueError):
                    pass
            assert child.stdout is not None
            for line in child.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                name = event.pop("event", None)
                if isinstance(name, str):
                    with self._guard:
                        if job.cancelled or self._active is not job:
                            continue
                        try:
                            driver.handle(name, event)
                        except Exception as exc:
                            logger.warning("capture event %s for %s not applied: %s", name, job.player, exc)
            stream_complete = True
            returncode = child.wait()
        finally:
            if child.poll() is None and (job.cancelled or not stream_complete):
                try:
                    child.terminate()
                except OSError:
                    pass
            if child.poll() is None:
                try:
                    returncode = child.wait(timeout=self.TERMINATE_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    try:
                        child.kill()
                    except OSError:
                        pass
                    returncode = child.wait()
                except OSError:
                    pass
            if child.stdout is not None:
                try:
                    child.stdout.close()
                except (OSError, ValueError):
                    pass
            with self._guard:
                if self._child is child:
                    self._child = None
        with self._guard:
            if job.cancelled or self._active is not job or driver.finished:
                return
            if returncode is None:
                _fail_run(
                    job.player, started_at, job.mode, "the board read worker could not be reaped",
                    {"code": "worker_exit", "returncode": None, "memory_mb": memory_mb}, job.run_token,
                )
                return
            if returncode == WORKER_EXIT_MEMORY or returncode in (-9, 137):
                message = "the read ran out of memory"
            elif returncode < 0:
                message = f"the board read worker was killed (signal {-returncode})"
            else:
                message = f"the board read worker exited with code {returncode}"
            _fail_run(
                job.player, started_at, job.mode, message,
                {"code": "worker_exit", "returncode": returncode, "memory_mb": memory_mb}, job.run_token,
            )


WORKER_EXIT_MEMORY = 3
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_supervisor = CaptureSupervisor()
atexit.register(_supervisor.shutdown)


def _finish_run(
    player: str, started_at: str | None, mode: str, report: dict[str, Any],
    total_deeds: int | None, run_token: object | None = None,
) -> None:
    with _lock:
        current = _run_state.get(player)
        if current is None or current.run_token is not run_token:
            return
        _run_state[player] = _RunState(
            state="idle",
            mode=mode,
            started_at=started_at,
            finished_at=_now_iso(),
            requests_made=report.get("capture", {}).get("requests"),
            total_deeds=total_deeds,
            fetched_deeds=total_deeds,
        )


def _fail_run(
    player: str, started_at: str | None, mode: str, exc: Exception | str,
    outcome: dict[str, Any] | None = None, run_token: object | None = None,
) -> None:
    message = str(exc)
    with _lock:
        prior = _run_state.get(player, _RunState())
        if prior.run_token is not run_token:
            return
        logger.warning("ceres %s capture failed for %s: %s", mode, player, message)
        _run_state[player] = _RunState(
            state="failed", mode=mode, started_at=started_at, finished_at=_now_iso(), error=message,
            outcome=outcome,
            total_deeds=prior.total_deeds, fetched_deeds=prior.fetched_deeds,
        )


# a quick capture registers here what to run the moment its final board is on disk.
# now guarantees the card-pool sidecar is already paired at that point. Keyed by player
# and read by `_save_merged`, so the call shape
# `_save_merged(config, player, report)` that tests and other callers rely on is unchanged.
_board_ready_hooks: dict[str, Any] = {}


def _save_merged(config: CeresConfig, player: str, report: dict[str, Any]) -> dict[str, Any]:
    """Merge with whatever snapshot is already on disk (never regress coverage a prior
    full capture or lazy expand already earned) and persist the latest copy only --
    snapshots/ never accumulates more than one file per player (2026-08-21 cleanup).

    Held under the per-player write lock: read (`latest_snapshot`), merge, and write
    (`_save_snapshot_and_refresh_cache`) must happen as one step, or a concurrent
    writer's merge silently vanishes ().

    A corrupt on-disk snapshot must not block a capture from recovering it ():
    `latest_snapshot` raising `CacheError`/`SchemaError` (a genuinely invalid file) is
    treated as "nothing to merge with" rather than propagating, so this freshly captured
    `report` overwrites the corrupt file outright instead of leaving the account
    permanently 503/failed.

    : ONLY those two. A `MemoryError` (orjson could not get its parse buffer under
    the worker's cap -- a 98.7 MB player0016 board) or any other failure while READING the
    existing snapshot aborts the save without writing anything and fails the run; the
    old blanket catch overwrote a good 6303-plot board with a 0-of-6303 one."""
    with _snapshot_write_lock(player):
        try:
            existing, _ = latest_snapshot(config, player)
        except (CacheError, SchemaError) as exc:
            logger.warning("existing snapshot for %s unreadable, capture will overwrite it: %s", player, exc)
            existing = None
        except MemoryError as exc:
            cap = _active_capture_cap_mb
            where = f"under the capture cap {cap} MB" if cap else "under the capture cap"
            raise SnapshotUnreadableError(
                f"could not read the existing board snapshot (out of memory {where}); nothing was overwritten"
            ) from exc
        except Exception as exc:
            raise SnapshotUnreadableError(
                f"could not read the existing board snapshot ({exc}); nothing was overwritten"
            ) from exc
        merged = merge_reports(existing, report, stale_after_seconds=config.stale_after_seconds)
        incoming_collection = report.get("coverage", {}).get("collection", {})
        incoming_has_pool = isinstance(report.get("cards"), list) and isinstance(incoming_collection, dict) and incoming_collection.get("complete") is True
        with _lock:
            on_board_ready = _board_ready_hooks.pop(player, None)
        _save_snapshot_and_refresh_cache_locked(
            merged, _latest_path(config, player), pool_may_have_changed=incoming_has_pool,
            before_pool_write=on_board_ready,
        )
    try:
        _append_coverage_journal(config, player, merged, _latest_path(config, player))
    except Exception as exc:
        logger.warning("coverage journal write failed: %s", exc)
    return merged


_MAX_SELF_HEAL_DEEDS = 8
_MAX_PENDING_REFETCH = 4
# (2026-09-14, his ruling: "on demand for every account"): the never-fetched background batch is gone
# _MAX_UNFETCHED_PER_READ (, 200 per read) and _ON_DEMAND_DETAIL_ABOVE_PLOTS (1000) were removed with it.


def _append_coverage_journal(config: CeresConfig, player: str, report: dict[str, Any], snapshot_path: Path) -> None:
    assets = report.get("coverage", {}).get("assets", {})
    journal_path = config.snapshots_dir.parent / "coverage-journal.jsonl"
    if journal_path.exists() and journal_path.stat().st_size > 5 * 1024 * 1024:
        os.replace(journal_path, journal_path.with_name("coverage-journal.1.jsonl"))
    snapshot_bytes = snapshot_path.stat().st_size
    entry = {
        "at": _now_iso(),
        "player": player,
        "plots": len(report.get("plots", [])),
        "retrieved": assets.get("retrieved"),
        "dropped": assets.get("dropped"),
        "failed": assets.get("failed"),
        "not_requested": assets.get("not_requested"),
        "worker_detail_pending": assets.get("worker_detail_pending"),
        "complete": assets.get("complete"),
        "needs_refetch": list(assets.get("needs_refetch", [])[:20]),
        "pending_deeds": list(assets.get("worker_detail_pending_deeds", [])[:50]),
        "snapshot_bytes": snapshot_bytes,
        "oldest_observed_at": assets.get("oldest_observed_at"),
    }
    with journal_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, separators=(",", ":")) + "\n")


def _asset_repair_patch(base: dict[str, Any], repaired: dict[str, Any], queued: list[str]) -> dict[str, Any]:
    target_uids = set(queued)
    repaired_plots = {
        str(plot.get("deed_uid")): plot for plot in repaired.get("plots", [])
        if str(plot.get("deed_uid")) in target_uids
    }
    patch = dict(base)
    patch["plots"] = [
        repaired_plots.get(str(plot.get("deed_uid")), plot)
        for plot in base.get("plots", [])
    ]
    patch["workers"] = [
        worker for worker in base.get("workers", [])
        if str(worker.get("stake_ref_uid")) not in target_uids
    ] + [
        worker for worker in repaired.get("workers", [])
        if str(worker.get("stake_ref_uid")) in target_uids
    ]
    for key in ("bonus_assets", "assignments"):
        patch[key] = [
            item for item in base.get(key, [])
            if str(item.get("stake_ref_uid") or item.get("deed_uid")) not in target_uids
        ] + [
            item for item in repaired.get(key, [])
            if str(item.get("stake_ref_uid") or item.get("deed_uid")) in target_uids
        ]
    for key in ("findings", "diagnostics"):
        patch[key] = [
            item for item in base.get(key, [])
            if str(item.get("deed_uid")) not in target_uids
        ] + [
            item for item in repaired.get(key, [])
            if str(item.get("deed_uid")) in target_uids
        ]
    patch["warnings"] = list(dict.fromkeys(
        list(base.get("warnings") or []) + list(repaired.get("warnings") or [])
    ))
    patch["capture"] = repaired.get("capture", {})
    patch["generated_at"] = repaired.get("generated_at", base.get("generated_at"))
    return patch


def _publish_asset_repair(
    config: CeresConfig,
    player: str,
    prior: dict[str, Any],
    repaired: dict[str, Any],
    queued: list[str],
) -> dict[str, Any]:
    with _snapshot_write_lock(player):
        current, _ = latest_snapshot(config, player)
        current = current or prior
        prior_collection = prior.get("coverage", {}).get("collection", {})
        current_collection = current.get("coverage", {}).get("collection", {})
        if prior_collection.get("fetched_at") != current_collection.get("fetched_at"):
            return current
        prior_plots = {str(plot.get("deed_uid")): plot for plot in prior.get("plots", [])}
        current_plots = {str(plot.get("deed_uid")): plot for plot in current.get("plots", [])}
        for deed_uid in queued:
            if current_plots.get(deed_uid) != prior_plots.get(deed_uid):
                return current
        patch = _asset_repair_patch(current, repaired, queued)
        published = merge_reports(current, patch, stale_after_seconds=config.stale_after_seconds)
        for section in ("item_catalogue", "unassigned_items"):
            prior_section = current.get(section)
            if prior_section and published.get(section) != prior_section:
                raise AssertionError(f"asset repair lost account section: {section}")
        path = _latest_path(config, player)
        _save_snapshot_and_refresh_cache_locked(published, path, pool_may_have_changed=False)
    try:
        _append_coverage_journal(config, player, published, path)
    except Exception as exc:
        logger.warning("coverage journal write failed: %s", exc)
    return published


def _self_heal_refetches(
    config: CeresConfig,
    player: str,
    merged: dict[str, Any],
    *,
    capture: LandCapture,
    emit: Any = None,
) -> dict[str, Any]:
    # (2026-09-14, his ruling: "on demand for every account"): the background worker-detail batch after
    # the board is on screen is gone for every account. A plot loads its workers the first time it is opened
    # (measured 1.0-1.9 s live vs 0.2-0.75 s prefetched, once per plot); only the 8 retries for failed/stale plots
    # stay. Nothing runs on the server after the board is published. This retires 's "every page load ends
    # with every plot fetched" and 's budget-exhausted batch; a never-read plot (incl. "budget exhausted")
    # is on demand, so it is kept out of the retry trickle.
    def _never_read(plot: dict[str, Any]) -> bool:
        fetch = plot.get("asset_fetch") or {}
        return fetch.get("status") in (None, "not_requested") or (
            fetch.get("status") == "failed" and "budget exhausted" in str(fetch.get("reason") or ""))
    unfetched = [
        plot.get("deed_uid")
        for plot in merged.get("plots", [])
        if plot.get("deed_uid") is not None and _never_read(plot)
    ]
    retries = [
        uid for uid in merged.get("coverage", {}).get("assets", {}).get("needs_refetch", [])
        if uid not in unfetched
    ][:_MAX_SELF_HEAL_DEEDS]
    # history: v0.9.83 sent all 6,300 never-fetched player0016 plots in one batch and wedged the 1-vCPU
    # webserver; v0.9.84 capped it at 200 per read; v0.9.359 skipped it above 1000 plots; v0.9.360 ()
    # skips it for every account -- only retries are queued.
    if unfetched:
        logger.info("self-heal: %d never-fetched plots left on demand", len(unfetched))
    queued = retries
    if not queued:
        return merged
    try:
        repaired = capture.repair_assets(queued, player)
        published = _publish_asset_repair(config, player, merged, repaired, queued)
        if emit is not None:
            from . import board_artifact
            identity = board_artifact.cached_identity(config.snapshots_dir, player)
            emit("repair", token=getattr(identity, "token", None), repaired_deeds=len(queued))
        return published
    except Exception as exc:
        logger.warning("self-heal asset batch failed for %s (%s deeds): %s", player, len(queued), exc)
        failed_report = dict(merged)
        failed_plots = []
        for plot in merged.get("plots", []):
            failed_plot = dict(plot)
            if plot.get("deed_uid") in queued:
                failed_plot["asset_fetch"] = {"status": "failed", "reason": str(exc)}
            failed_plots.append(failed_plot)
        failed_report["plots"] = failed_plots
        failed_report["generated_at"] = _now_iso()
        failed_report["warnings"] = list(merged.get("warnings") or [])
        failed_report["warnings"].append(f"PARTIAL DATA: asset repair batch: {exc}")
        return _save_merged(config, player, failed_report)


def _set_phase(player: str, phase: str, run_token: object | None = None) -> None:
    """R-149. Publish which step of the read is running, so the loading surface can say
    something true while a quick capture is in flight. A quick capture has no per-deed
    fan-out to count -- these are its actual sequential steps, and each is set as it
    BEGINS, so the text never claims a step that has not started."""
    with _lock:
        state = _run_state.get(player)
        if state is not None and state.run_token is run_token:
            state.phase = phase


# the "wait for the first board read" pause and the automatic picker prewarm
# (`_prewarm_picker`) are gone -- nothing heavy runs after the board is announced ready. The
# picker builds its candidate pool on its first explicit open (`candidates._heavy_lane`).


def board_ready(player: str) -> bool:
    """. True while a quick capture is still running but its board is already on disk
    (the `gaps` self-heal phase). A row expand is safe then: both writers merge under the
    per-player write lock and neither regresses the other's coverage."""
    with _lock:
        state = _run_state.get(player)
        return bool(state is not None and state.state == "running" and state.board_ready)


def _mark_board_ready(player: str, run_token: object | None = None) -> None:
    """. Set once the merged snapshot is on disk and `/api/board` can serve it,
    which happens before the `gaps` self-heal batch. `_finish_run` replaces the whole
    state afterwards, so the flag lives exactly as long as the run is still going."""
    with _lock:
        state = _run_state.get(player)
        if state is not None and state.run_token is run_token:
            state.board_ready = True


def _release_memory() -> None:
    """: hand freed heap back to the system. A whale read parses a 160 MB collection
    and the process kept the freed pages (RSS stayed ~0.9 GB after the report was gone;
    malloc_trim brought it to ~60 MB). glibc only; a no-op elsewhere."""
    try:
        import ctypes, gc
        gc.collect()
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def _run_capture_quick_then_release(config: CeresConfig, player: str) -> None:
    try:
        _run_capture_quick(config, player)
    finally:
        _release_memory()


def _peak_rss_mb() -> float | None:
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:
        return None


def _run_capture_quick(config: CeresConfig, player: str) -> None:
    """In-process QUICK run (thread path / tests): the body reports into a `_RunStateDriver`."""
    state = _run_state.get(player, _RunState())
    driver = _RunStateDriver(config, player, "quick", state.started_at, run_token=state.run_token)
    try:
        _run_capture_body(config, player, "quick", driver)
    except MemoryError:
        driver("failed", error="the read ran out of memory", outcome={"code": "memory_limit"})


def _run_capture_body(config: CeresConfig, player: str, mode: str, emit: Any, *, known_plots: int | None = None) -> None:
    """: run one capture to completion, reporting ONLY through `emit(event, **fields)` --
    never touching `_run_state`. Runs unchanged in this process (`_RunStateDriver`) or in the
    worker child (`ceres.capture_worker`, JSON lines on stdout). Every exception except
    MemoryError becomes a `failed` event; MemoryError propagates so the worker can exit 3."""
    try:
        # Q9: the Full capture (per-deed fan-out with its six-hour resume cache) is removed
        # every read is the quick board read.
        _capture_quick_body(config, player, emit, known_plots=known_plots)
    except MemoryError:
        raise
    except Exception as exc:  # keep serving the previous snapshot; report failure loudly
        emit("failed", error=str(exc), outcome=None)


def _publish_interim_artifact(config: CeresConfig, player: str, report: dict[str, Any]) -> Any:
    """: project the bulk report into an interim artifact (+ .gz) with its own token and an
    `interim: true` identity; returns the identity, or None when the publish was refused."""
    from . import board_artifact
    from .board import load_details_by_id

    with _snapshot_write_lock(player):
        published = board_artifact.publish(config.snapshots_dir, player, None, report, load_details_by_id(), interim=True)
    if published is None:
        return None
    return board_artifact.read_identity(config.snapshots_dir, player)


def _board_ready_hook(emit: Any, report: dict[str, Any]) -> Any:
    """The legacy-named `before_pool_write` hook: runs once the paired sidecar, merged
    snapshot, and artifact are on disk. Receives the publication identity."""
    def _hook(identity: Any = None) -> None:
        emit(
            "board_ready",
            token=getattr(identity, "token", None),
            generated_at=getattr(identity, "snapshot_generated_at", None) or report.get("generated_at"),
        )
    return _hook


def _capture_quick_body(config: CeresConfig, player: str, emit: Any, *, known_plots: int | None = None) -> None:
    config.ensure_dirs()
    # (2026-09-07): `card_details` used to be a 1.2 s serial phase in front
    # of `plots`. v0.9.18 moved it to a parallel thread -- which made `capture_quick`'s
    # own `_card_names` read (same cache key, same TTL) miss the not-yet-written cache
    # and fetch the 1.2 MB details a SECOND time, queued behind the collection sweep
    # on a 6,303-deed account that pushed `item_details` to 27 s. The quick read's
    # `_card_names` IS the refresh now: one fetch, on the pool, into the same cache the
    # board reads. `_refresh_card_details` stays for the full capture.
    emit("phase", phase="plots")
    capture = _shared_capture()
    # the quick read kept praetoria's flat 200-request budget, so a 474-plot account (player0001)
    # exhausted it after 216 reads -- 200 plots failed, 258 never requested. Size it from the plot count the
    # last stored board already knows (a local read: no extra network wait on page load).
    if known_plots is None:
        try:
            previous, _ = latest_snapshot(config, player)
            known_plots = len((previous or {}).get("plots") or [])
        except MemoryError:
            raise  # an unreadable board must fail the read, not shrink its budget to zero
        except Exception:
            known_plots = 0
    capture.request_budget = derive_request_budget(int(known_plots or 0))
    balance_executor, balance_future = _start_optional_balances(player)
    balance_resolved = False
    try:
        def _publish_interim(interim: dict[str, Any]) -> None:
            # v0.9.27: the bulk board is live and complete at plot level; publish
            # it and let the browser render while the collection sweep finishes.
            # the interim board is a compact artifact ONLY -- no `_save_merged`, no
            # 98.7 MB snapshot write, no wait for the cards phase. It is the live bulk read of
            # THIS run (never a stored snapshot); the final publish supersedes it by token.
            try:
                emit("phase", phase="publishing")
                identity = _publish_interim_artifact(config, player, interim)
                if identity is not None:
                    emit("board_ready", token=identity.token, generated_at=identity.artifact_generated_at, interim=True)
                emit("phase", phase="cards")
            except Exception as exc:
                logger.warning("interim board publish failed for %s: %s", player, exc)

        capture.on_bulk_ready = _publish_interim  # attribute, not kwarg: fakes keep their call shape (v0.9.29)

        def _publish_deed_count(count: int) -> None:
            # the plot count is known one request in; publish it and the
            # measured-rate estimate so the loading text can say what it is reading.
            emit("deed_count", total=int(count), estimated_seconds=estimate_quick_seconds(int(count), config, player))

        capture.on_deed_count = _publish_deed_count
        try:
            report = capture.capture_quick(player, include_asset_ids=True)
        finally:
            capture.on_deed_count = None  # closes over `player`; the shared object outlives this run
        if report.get("status") == "response_too_large":
            _finish_optional_balances(balance_executor, balance_future, player)
            balance_resolved = True
            emit("failed", error=str(report["status"]), outcome=report.get("response_too_large"))
            return
        emit("phase", phase="balances")
        _balances = _finish_optional_balances(balance_executor, balance_future, player)
        balance_resolved = True
        report["tc_balance"] = _tc_balance(_balances)
        report["permit_balance"] = _permit_balance(_balances)
        emit("phase", phase="merging")
        # The interim live artifact has already made the board renderable. The final
        # snapshot is announced only after its card-pool sidecar is paired ().
        with _lock:
            _board_ready_hooks[player] = _board_ready_hook(emit, report)
        try:
            merged = _save_merged(config, player, report)
        finally:
            with _lock:
                _board_ready_hooks.pop(player, None)
        # no wait-for-first-read and no picker prewarm -- the self-heal retries are
        # the only work left, and the run ends right after them.
        emit("phase", phase="gaps")
        merged = _self_heal_refetches(config, player, merged, capture=capture, emit=emit)
        emit(
            "done",
            requests=report.get("capture", {}).get("requests"),
            cache_hits=report.get("capture", {}).get("cache_hits"),
            total_deeds=len(merged.get("plots", [])),
            peak_rss_mb=_peak_rss_mb(),
        )
    finally:
        if not balance_resolved:
            _finish_optional_balances(balance_executor, balance_future, player)


def expand_plot(
    config: CeresConfig, player: str, deed_uid: str, *,
    include_collection: bool = False, bundle_ttl: int | None = None,
) -> dict[str, Any]:
    """Fetch one live deed, worksite, staking record, and its assets for a row open.

    Ordinary opens publish a small per-deed patch tied to the current snapshot. Full
    capture and explicit collection refresh still use the account snapshot path.
    Synchronous, not backgrounded -- this is a single-deed fetch, not a whale-scale
    capture. Raises if no snapshot exists yet (nothing to merge into) or if the fetch
    itself fails; callers turn either into an HTTP error.

    The network fetch (`expand_deed`, the slow part) happens OUTSIDE the per-player
    write lock; only the read-merge-write around the on-disk snapshot is serialised
    () -- concurrent expands for the same player still fetch in parallel, they
    just persist one at a time, so no merge is silently dropped by a racing writer."""
    player = security.validate_player(player)
    config.ensure_dirs()
    # Fail fast before paying for the network fetch if there is plainly nothing to
    # merge into yet; re-checked under the write lock below since this precheck reads
    # outside it.
    precheck, _ = latest_snapshot(config, player)
    if precheck is None:
        raise LookupError(f"no snapshot for {player}; capture the board first")
    if bundle_ttl is None:
        bundle_ttl = 0
    plot_id = None
    if not include_collection:
        source_plot = next((plot for plot in precheck.get("plots", []) if str(plot.get("deed_uid")) == str(deed_uid)), None)
        if source_plot is None:
            raise LookupError(f"unknown deed_uid: {deed_uid}")
        plot_id = (source_plot.get("facts") or {}).get("plot_id")
        if isinstance(plot_id, bool) or not isinstance(plot_id, int) or plot_id <= 0:
            raise LookupError("the board has no numeric plot_id for this deed; refresh the board")
    capture = LandCapture()
    capture.persist_cache = persist_static_reference_only  # no land_assets_<ref>.json per open
    expanded = capture.expand_deed(deed_uid, player, bundle_ttl=bundle_ttl, include_collection=include_collection, narrow=not include_collection, plot_id=plot_id if not include_collection else None)
    capture_report = expanded
    with _snapshot_write_lock(player):
        existing, ref = latest_snapshot(config, player)
        if existing is None:
            raise LookupError(f"no snapshot for {player}; capture the board first")
        if not include_collection and any(str(p.get("deed_uid")) == str(deed_uid) for p in existing.get("plots", [])):
            # `expand_deed` analysed this one deed only, so the report carries one
            # plot. `merge_reports` keeps only the plots the incoming report names, so the
            # single-deed result is first laid over the stored snapshot as a patch (the same
            # path the self-heal batch uses) and the patch is what gets merged. A deed the
            # stored snapshot does not list yet takes the plain merge below instead.
            expanded = _asset_repair_patch(existing, expanded, [str(deed_uid)])
        merged = merge_reports(existing, expanded, stale_after_seconds=config.stale_after_seconds)
        expanded_collection = expanded.get("coverage", {}).get("collection", {})
        expanded_has_pool = isinstance(expanded.get("cards"), list) and isinstance(expanded_collection, dict) and expanded_collection.get("complete") is True
        if include_collection:
            _save_snapshot_and_refresh_cache_locked(
                merged, _latest_path(config, player), pool_may_have_changed=expanded_has_pool,
            )
        else:
            save_snapshot({
                "base_mtime_ns": ref.snapshot_mtime_ns,
                "base_size": ref.snapshot_size,
                "deed_uid": str(deed_uid),
                "report": capture_report,
            }, _plot_patch_path(config, player, str(deed_uid)))
    return merged


class _NoStore:
    """ / : a cache that refuses every use -- the Sign-time live read keeps nothing."""

    def read(self, *_args, **_kwargs):
        raise CacheError("the Sign-time live read must not read a cache")

    def write(self, *_args, **_kwargs):
        raise CacheError("the Sign-time live read must not write a cache")


def read_plot_live(player: str, deed_uid: str, plot_id: int) -> dict[str, Any]:
    reader = LandCapture(cache=_NoStore())
    reader.persist_cache = lambda _key: False
    return reader.expand_deed(deed_uid, player, bundle_ttl=0, include_collection=False, narrow=True, plot_id=plot_id)


def read_plots_live(
    config: CeresConfig, player: str, deed_uids: list[str] | tuple[str, ...] | set[str], base_report: dict[str, Any],
) -> dict[str, Any]:
    """: read the named deeds live from the game, in memory only, and lay them over a copy
    of `base_report` for this one request.  "Fresh read before every signature"; 
    "We ask the API or we have no data";  no stored board data -- nothing is written: no
    snapshot, no plot patch, no praetoria cache. Returns {"report": merged, "failed": [deed_uid]}."""
    player = security.validate_player(player)
    wanted = sorted({str(uid) for uid in deed_uids if uid})
    plot_ids: dict[str, int] = {}
    failed: list[str] = []
    for uid in wanted:
        plot = next((p for p in base_report.get("plots", []) if str(p.get("deed_uid")) == uid), None)
        plot_id = ((plot or {}).get("facts") or {}).get("plot_id")
        if isinstance(plot_id, bool) or not isinstance(plot_id, int) or plot_id <= 0:
            failed.append(uid)
        else:
            plot_ids[uid] = plot_id
    results: dict[str, dict[str, Any]] = {}
    if plot_ids:
        with ThreadPoolExecutor(max_workers=min(4, len(plot_ids))) as pool:
            futures = {uid: pool.submit(read_plot_live, player, uid, pid) for uid, pid in plot_ids.items()}
            for uid, future in futures.items():
                try:
                    expanded = future.result()
                except Exception as exc:  # noqa: BLE001 -- an unanswered read is "no data yet", never an error text
                    logger.info("Sign-time live read for %s/%s did not answer: %s", player, uid, exc)
                    failed.append(uid)
                    continue
                plot = next((p for p in expanded.get("plots", []) if str(p.get("deed_uid")) == uid), None)
                if not plot or (plot.get("asset_fetch") or {}).get("status") != "retrieved":
                    failed.append(uid)
                    continue
                results[uid] = expanded
    merged = json.loads(json.dumps(base_report))
    for uid, expanded in results.items():
        patch = _asset_repair_patch(merged, expanded, [uid])
        merged = merge_reports(merged, patch, stale_after_seconds=config.stale_after_seconds)
    return {"report": merged, "failed": sorted(set(failed)), "read": sorted(results)}


def expand_plots(
    config: CeresConfig,
    player: str,
    deed_uids: list[str] | tuple[str, ...] | set[str],
    *,
    evidence: MnemosyneEvidence,
    include_collection: bool = True,
) -> dict[str, Any]:
    """Refresh several touched deeds, then merge and persist once under the lock."""
    player = security.validate_player(player)
    if evidence.account != player:
        raise ValueError("evidence account does not match requested player")
    requested = sorted({str(uid) for uid in deed_uids if uid})
    config.ensure_dirs()
    precheck, _ = latest_snapshot(config, player)
    if precheck is None:
        raise LookupError(f"no snapshot for {player}; capture the board first")
    report = evidence.capture.expand_deeds(
        requested, player, evidence=evidence, include_collection=include_collection,
    )
    with _snapshot_write_lock(player):
        existing, _ = latest_snapshot(config, player)
        if existing is None:
            raise LookupError(f"no snapshot for {player}; capture the board first")
        merged = merge_reports(existing, report, stale_after_seconds=config.stale_after_seconds)
        expanded_collection = report.get("coverage", {}).get("collection", {})
        expanded_has_pool = isinstance(report.get("cards"), list) and isinstance(expanded_collection, dict) and expanded_collection.get("complete") is True
        _save_snapshot_and_refresh_cache_locked(
            merged, _latest_path(config, player), pool_may_have_changed=expanded_has_pool,
        )
    refreshed = [
        str(plot.get("deed_uid")) for plot in report.get("plots", [])
        if str(plot.get("deed_uid")) in requested and (plot.get("asset_fetch") or {}).get("status") == "retrieved"
    ]
    failed = [
        str(plot.get("deed_uid")) for plot in report.get("plots", [])
        if str(plot.get("deed_uid")) in requested and (plot.get("asset_fetch") or {}).get("status") == "failed"
    ]
    failed.extend(
        str(item.get("deed_uid")) for item in report.get("capture", {}).get("failures", [])
        if item.get("deed_uid") in requested and str(item.get("deed_uid")) not in failed
    )
    failed.extend(
        str(item.get("deed_uid")).split(":", 1)[1] for item in report.get("capture", {}).get("failures", [])
        if str(item.get("deed_uid", "")).startswith("prior_resources:")
        and str(item.get("deed_uid")).split(":", 1)[1] in requested
    )
    shared_failures = [
        item for item in report.get("capture", {}).get("failures", [])
        if item.get("deed_uid") in {"collection", "card_names"}
    ]
    merged.setdefault("capture", {})["refreshed_deeds"] = refreshed
    merged["capture"]["refresh_errors"] = sorted(set(failed) | (set(requested) if shared_failures else set()))
    return merged


def fetch_land_assets(config: CeresConfig, player: str, deed_uid: str) -> dict[str, Any]:
    """Fetch one fresh domain-state response through Praetoria's read-only client."""
    player = security.validate_player(player)
    capture = LandCapture()
    return capture.land_client.assets(deed_uid).data


def fetch_worksite_facts(config: CeresConfig, player: str, deed_uid: str) -> dict[str, Any] | None:
    """Fresh, assets-free re-read of one deed's worksite facts for verification."""
    player = security.validate_player(player)
    capture = LandCapture()
    response = capture.fresh_read(lambda: capture.land_client.bundle(player))
    return project_worksite_facts(response.data, deed_uid, response.retrieved_at)
