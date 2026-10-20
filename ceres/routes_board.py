"""GET /api/board, GET /api/plots/{deed_uid}, GET/POST /api/capture/* -- Phase 1 only.

`/api/board` never triggers a network capture (plan §6.1) -- it only ever reads the
newest snapshot already on disk via `capture.latest_snapshot` and returns 503 if none
exists yet.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from praetoria import fastjson
from praetoria.transport import ResponseTooLarge

from . import __version__
from . import board_artifact, client_limits, sps_rate
from .board import build_idle_cards, details_cache_mtime, load_details_by_id, project_board, project_single_plot, project_worker_upgrade_options
from .cooling import build_cooling_view
from .notices import format_notice
from .logging_setup import log_safe
from .security import player_for_request
from .worker_upgrade import WorkerUpgradeOptionsError

router = APIRouter()
logger = logging.getLogger("ceres.routes_board")


def _read_snapshot_or_503(capture_mod, config, player: str):
    """Shared `latest_snapshot` read for every board-reading route: 503 either way,
    but with a message that matches what will actually fix it () -- a genuinely
    missing snapshot needs a first capture, while a corrupt one on disk is recovered by
    the SAME refresh (`_save_merged` now overwrites rather than refusing on read
    failure, see `capture.py`), so telling the user to "delete the file" would be both
    wrong and needless."""
    try:
        report, ref = capture_mod.latest_snapshot(config, player)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="the stored board could not be read; reload the board before continuing",
        ) from exc
    if report is None:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing")
    return report, ref
# `_board_cache` LRU cap -- one entry per (snapshot path, player, lean) triple, unbounded
# growth otherwise across every player ever queried against a shared deployment.
BOARD_CACHE_MAXSIZE = 64

# `/api/board` load-time fix (2026-08-24): `project_board(lean=True)` recomputed the
# entire board projection from scratch on EVERY request, even against an unchanged
# snapshot -- measured ~450-800ms on its own, on top of `load_details_by_id()`'s own
# now-fixed per-request cost (see board.py). `project_board()` is a pure function of
# (report, details_by_id, lean); `report` is already the SAME cached dict object
# `capture.latest_snapshot()` returns while its snapshot file's mtime is unchanged
# (`capture._snapshot_cache`), and `details_by_id` is now cached the same way -- so
# caching this on top is safe PROVIDED the freshness key genuinely changes on every
# write. `get_board()` below mutates the returned dict's top-level keys
# (`age_seconds`, `stale`, `warnings`) in place, so callers get a shallow copy, never
# the cached dict itself -- a shallow copy is sufficient because only top-level keys
# are ever written after project_board() returns; nothing downstream mutates a plot
# row in place.
# Publication identity is the saver-owned token, not generated_at or filesystem mtime.
_board_cache: OrderedDict[tuple[str, str, bool, str], tuple[str, int | None, dict]] = OrderedDict()
_board_cache_lock = threading.Lock()
_board_cache_bytes: dict[tuple[str, str, bool, str], int] = {}
_board_cache_total_bytes = 0
# Byte budget alongside the entry cap above: `BOARD_CACHE_MAXSIZE` bounds ENTRY COUNT
# not size -- a whale (player0016) lean board alone retains 67.0 MB, so 64 entries is
# nominally gigabytes on a shared deployment. 150MB fits roughly two whale-sized boards
# at once, the realistic worst case for a small public deployment ().
BOARD_CACHE_MAX_BYTES = 150 * 1024 * 1024


def _board_size_bytes(board: dict) -> int:
    """Approximate a cached board projection's retained size as its compact-JSON byte
    length, paid once per cache insert (never on a cache hit)."""
    try:
        return len(json.dumps(board, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _require_same_origin(request: Request) -> None:
    """Refuse a state-changing POST that carries an Origin header naming another site.
    A missing Origin is deliberately allowed -- the threat model is a browser auto-
    submitting a cross-site form with cached Basic-Auth credentials, and browsers always
    send Origin on a cross-origin POST; refusing header-less requests would only break
    same-origin UI calls and curl/CI callers, which is not this guard's job."""
    origin = request.headers.get("origin")
    if not origin:
        return
    forwarded_host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    allowed = {
        f"{request.url.scheme}://{request.url.netloc}",
        f"{forwarded_proto.split(',', 1)[0].strip()}://{forwarded_host.split(',', 1)[0].strip()}",
    }
    if origin.rstrip("/").lower() not in {item.rstrip("/").lower() for item in allowed}:
        logger.warning("refused cross-origin POST from %s", log_safe(origin, 120))
        raise HTTPException(status_code=403, detail="cross-origin POST refused")


def _safe_capture_status(status) -> dict[str, Any]:
    """Redact the absolute snapshot path and raw capture-error text from a CaptureStatus
    before it reaches an HTTP response -- both leak server filesystem layout / OS username."""
    result = status.as_dict()
    snapshot = result.get("latest_snapshot")
    if isinstance(snapshot, dict):
        snapshot.pop("path", None)
    if result.get("error"):
        result["error"] = "the last board read failed"
    return result


def _board_status_payload(status, board: dict[str, Any]) -> dict[str, Any]:
    result = _safe_capture_status(status)
    coverage = board.get("coverage", {})
    assets = coverage.get("assets", {}) if isinstance(coverage, dict) else {}
    if not isinstance(assets, dict) or not assets:
        assets = coverage if isinstance(coverage, dict) else {}
    notices = []
    if isinstance(assets, dict):
        dropped = assets.get("dropped") or 0
        failed = assets.get("failed") or 0
        # missing worker detail is never a bar.
        # (2026-09-14): a detail dropped after the maintainer's own change is normal too (the plot reloads on open); only a failed read is announced.
        if failed > 0:
            dropped_clause = f", {dropped} dropped after a change" if dropped else ""
            notices.append(
                format_notice(
                    "assets_incomplete",
                    context={
                        "failed": failed,
                        "dropped_clause": dropped_clause,
                    },
                )
            )
    if status.error:
        result["warnings"] = ["The last board read failed."]
        notices.append(format_notice("capture_failed"))
    result["notices"] = notices
    result["token"] = result.get("latest_snapshot", {}).get("token") if result.get("latest_snapshot") else None
    return result


def _board_status_header(status, board: dict[str, Any]) -> str:
    payload = _board_status_payload(status, board)
    return json.dumps(
        {
            key: payload.get(key)
            for key in ("token", "state", "mode", "age_seconds", "stale", "notices", "warnings")
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _accepts_gzip(request: Request) -> bool:
    return "gzip" in request.headers.get("accept-encoding", "").lower()


def _board_cache_key(ref, player, lean, plots=None):
    """Shared key derivation for `_cached_project_board` (the board OBJECT cache) and
    `_cached_board_bytes`/`_cached_board_gzip` (the serialized-RESPONSE-bytes cache,
    ) below -- one definition of "what makes two board reads the same" so the
    two caching layers can never disagree about a key. Deriving it touches attributes
    this function does not control (`ref` may be any shape a caller passes -- test_api.py
    deliberately stubs `latest_snapshot` to return a bare `object()` sentinel in place
    of a real `SnapshotRef`, since it only cares that the second tuple element is
    opaque and unused by the endpoint otherwise). ANY failure deriving the key --
    missing attribute, anything -- degrades every cache built on this key to
    uncached: correct, just not accelerated, exactly like a cache miss. Caught
    broadly (not enumerated by exception type) so a new failure mode added later
    fails the same safe way instead of escaping as a 500.

    Returns (key, token, details_mtime_ns). `token` is the snapshot publication token
    (: a fresh live capture always mints a new token, so both caches below can
    never serve a board from an older snapshot than the page load's own live capture --
    the key changes and it is a miss, not a stale hit). `token` is `None` when no cache
    should be used at all (key derivation failed, or `ref` carries no usable token).
    """
    token = None
    details_mtime_ns = None
    ref_path = None
    try:
        ref_path = getattr(ref, "path", None)
        token = getattr(ref, "token", None)
        if not (ref_path and token):
            token = None
        else:
            details_mtime_ns = details_cache_mtime()
    except Exception:
        token = None
    key = (ref_path, player, lean, token or "")
    if plots is not None:
        # a filtered read (`/api/board?plots=`) is its own entry -- same snapshot, same
        # token, but a different body; the filter is part of the key so the two never collide.
        key = key + (("plots",) + tuple(plots),)
    return key, token, details_mtime_ns


def _evict_board_caches_locked():
    """Shared eviction for BOTH the board-OBJECT cache (`_board_cache`) and the
    serialized-RESPONSE-BYTES cache (`_board_bytes_cache`, ) below -- callers
    hold `_board_cache_lock` already. Per-cache entry-count caps
    (`BOARD_CACHE_MAXSIZE`) stay separate (one dict, one count each); the BYTE budget
    (`BOARD_CACHE_MAX_BYTES`) is charged to the single shared `_board_cache_total_bytes`
    counter for both caches combined, so the process ceiling stays one 150MB budget,
    not 150MB per cache. When over the shared byte budget, the bytes cache is evicted
    first -- it is cheaper to lose (a miss just re-serializes an object this process
    still has cached) than the object cache (a miss re-reads and re-projects from the
    snapshot on disk)."""
    global _board_cache_total_bytes
    while len(_board_bytes_cache) > BOARD_CACHE_MAXSIZE:
        evicted_key, _ = _board_bytes_cache.popitem(last=False)
        _board_cache_total_bytes -= _board_bytes_cache_sizes.pop(evicted_key, 0)
    while len(_board_cache) > BOARD_CACHE_MAXSIZE:
        evicted_key, _ = _board_cache.popitem(last=False)
        _board_cache_total_bytes -= _board_cache_bytes.pop(evicted_key, 0)
    while _board_cache_total_bytes > BOARD_CACHE_MAX_BYTES and (len(_board_bytes_cache) + len(_board_cache)) > 1:
        if _board_bytes_cache:
            evicted_key, _ = _board_bytes_cache.popitem(last=False)
            _board_cache_total_bytes -= _board_bytes_cache_sizes.pop(evicted_key, 0)
        elif _board_cache:
            evicted_key, _ = _board_cache.popitem(last=False)
            _board_cache_total_bytes -= _board_cache_bytes.pop(evicted_key, 0)
        else:
            break


def _cached_project_board(report, ref, details_by_id, lean, player):
    """Returns `(board, key, token, details_mtime_ns)` -- the caller in `get_board()`
    reuses `key`/`token`/`details_mtime_ns` for the serialized-bytes cache below
    instead of re-deriving them (a second `details_cache_mtime()` call could return a
    different value than the one this function actually cached against)."""
    global _board_cache_total_bytes
    # This cache is an OPTIMISATION over `/api/board`'s hard contract (never 500
    # 503 rather than an empty board -- module docstring above) -- it must never be
    # the reason a request fails; see `_board_cache_key`'s docstring for the safe-miss
    # contract every cache on this key shares.
    key, token, details_mtime_ns = _board_cache_key(ref, player, lean)
    if token:
        try:
            with _board_cache_lock:
                cached = _board_cache.get(key)
                if cached is not None and cached[0] == token and cached[1] == details_mtime_ns:
                    _board_cache.move_to_end(key)
                    return dict(cached[2]), key, token, details_mtime_ns
        except Exception:
            pass
    board = project_board(report, details_by_id, lean=lean)
    if token:
        try:
            with _board_cache_lock:
                _board_cache_total_bytes -= _board_cache_bytes.pop(key, 0)
                size = _board_size_bytes(board)
                _board_cache[key] = (token, details_mtime_ns, board)
                _board_cache.move_to_end(key)
                _board_cache_bytes[key] = size
                _board_cache_total_bytes += size
                _evict_board_caches_locked()
        except Exception:
            pass
    return dict(board), key, token, details_mtime_ns


# (2026-09-20 speed audit, F4): a whale account's board (`player0016`, 6,303
# plots) paid ~3s of CPU on EVERY `/api/board` read of the SAME snapshot -- build
# JSON-serialize and gzip -- even though `_cached_project_board` above already
# cached the built board OBJECT. The cost this cache removes is the serialize+gzip
# `get_board()` redid on every hit of that object cache. This layer only matters on
# the FALLBACK path of `get_board()` (the `else` branch below, no valid on-disk
# `board_artifact` for this exact snapshot yet -- e.g. right after a process
# restart, before the next capture republishes one): the happy path already serves
# pre-serialized raw+gzip bytes straight off disk (, `board_artifact.py`)
# without reaching this code at all.
# Keyed by `_board_cache_key` -- identical to the object cache above (`get_board()`
# reuses the SAME key `_cached_project_board` derived, never re-derives it -- a
# second `details_cache_mtime()` call is not guaranteed to return what the first one
# did). is enforced the same way as the object cache: the key includes the
# snapshot's publication token, so a fresh live capture always mints a new token and
# this cache can never serve bytes built from an older snapshot than the one the
# current page load just captured. `details_mtime_ns` is NOT part of the key -- it is
# a stored validity field on the entry, checked on every read (see below), the same
# role it plays in `_board_cache`.
# Entries are (token, details_mtime_ns, raw_bytes, gzip_bytes_or_None) -- gzip is
# filled lazily by `_cached_board_gzip` on the first request that actually accepts
# gzip, so a caller that never sends `Accept-Encoding: gzip` never pays to compress
# a copy nobody wants. Byte accounting is shared with the object cache's
# `_board_cache_total_bytes` and bounded together by `_evict_board_caches_locked`
# above (one 150MB budget for both caches, not one each).
_board_bytes_cache: OrderedDict[tuple[str, str, bool, str], tuple[str, int | None, bytes, bytes | None]] = OrderedDict()
_board_bytes_cache_sizes: dict[tuple[str, str, bool, str], int] = {}


def _cached_board_bytes(key, token, details_mtime_ns, board):
    """Serialize `board` to compact JSON bytes once per (snapshot, player, lean) key
    and reuse the bytes on every later hit of the SAME snapshot. Same never-500,
    safe-miss contract as `_cached_project_board`/`_board_cache_key`: any failure
    reading or writing the cache degrades to plain uncached `fastjson.dumps`."""
    global _board_cache_total_bytes
    if token:
        try:
            with _board_cache_lock:
                cached = _board_bytes_cache.get(key)
                if cached is not None and cached[0] == token and cached[1] == details_mtime_ns:
                    _board_bytes_cache.move_to_end(key)
                    return cached[2], cached[3]
        except Exception:
            pass
    raw = fastjson.dumps(board)
    if token:
        try:
            with _board_cache_lock:
                _board_cache_total_bytes -= _board_bytes_cache_sizes.pop(key, 0)
                _board_bytes_cache[key] = (token, details_mtime_ns, raw, None)
                _board_bytes_cache.move_to_end(key)
                _board_bytes_cache_sizes[key] = len(raw)
                _board_cache_total_bytes += len(raw)
                _evict_board_caches_locked()
        except Exception:
            pass
    return raw, None


def _cached_board_gzip(key, token, details_mtime_ns, raw):
    """Lazily fill and reuse the gzip copy alongside an already-cached `raw` entry
    from `_cached_board_bytes`. Validates the FULL entry identity on both the read
    and the write -- token, `details_mtime_ns`, AND `cached_raw is raw` (this exact
    bytes object, not merely an equal one) -- not just the token. Token-only
    validation let a late thread pair its gzip with a DIFFERENT (newer) raw entry
    that had replaced it at the same key in between (same token, but the entry had
    since been rebuilt after a `details_cache_mtime()` change): a raw/gzip mismatch
    that would persist, unnoticed, until the token itself next changed. Same
    safe-miss contract: a cache-write failure just means the freshly-compressed
    bytes are returned without being remembered."""
    global _board_cache_total_bytes
    if token:
        try:
            with _board_cache_lock:
                cached = _board_bytes_cache.get(key)
                if (
                    cached is not None
                    and cached[0] == token
                    and cached[1] == details_mtime_ns
                    and cached[2] is raw
                    and cached[3] is not None
                ):
                    _board_bytes_cache.move_to_end(key)
                    return cached[3]
        except Exception:
            pass
    gz = gzip.compress(raw, compresslevel=5, mtime=0)
    if token:
        try:
            with _board_cache_lock:
                cached = _board_bytes_cache.get(key)
                if (
                    cached is not None
                    and cached[0] == token
                    and cached[1] == details_mtime_ns
                    and cached[2] is raw
                ):
                    _board_cache_total_bytes -= _board_bytes_cache_sizes.pop(key, 0)
                    _board_bytes_cache[key] = (cached[0], cached[1], cached[2], gz)
                    _board_bytes_cache.move_to_end(key)
                    _board_bytes_cache_sizes[key] = len(cached[2]) + len(gz)
                    _board_cache_total_bytes += _board_bytes_cache_sizes[key]
                    _evict_board_caches_locked()
        except Exception:
            pass
    return gz


# Auto-reload, layer 2 (a changed file changes the served version hash): the
# browser-side poller in index.html hits this every 1.5s and shows a click-to-reload
# banner when the hash changes. Watched files cover every `.py` that shapes a response
# plus every static asset the browser serves directly -- the SAME set `reload_watch.py`
# (Layer 3, the process-level auto-restarter) watches, so "server changed" and "banner
# fires" always agree; both import `_dev_watch_hash`/`watched_paths` from here so there
# is exactly one definition of "what counts as a change", not two that can drift.
_WATCH_ROOT = Path(__file__).parent
_WATCH_GLOBS = ("*.py", "static/*.html", "static/*.js", "static/*.css")

# `ceres/*.py` imports `praetoria` and `fides_land` (board.py, capture.py, candidates.py
# op_preview.py, routes_op_preview.py, metrics.py all do), so a change to either sibling
# package changes what Ceres serves just as much as a change inside `ceres/` itself
# without this, editing e.g. `praetoria/land_reallocate.py` is served stale forever: no
# restart (reload_watch derives its *.py subset from this same function) and no browser
# banner (the hash below covers the same set). Both packages are flat directories with a
# couple dozen `.py` files total, so a non-recursive glob per package stays as cheap as the
# `ceres/` scan above -- a recursive walk was deliberately avoided as unnecessary cost.
_SIBLING_PY_ROOTS = (_WATCH_ROOT.parent / "praetoria", _WATCH_ROOT.parent / "fides_land")


def watched_paths() -> list[Path]:
    paths: list[Path] = []
    for pattern in _WATCH_GLOBS:
        paths.extend(_WATCH_ROOT.glob(pattern))
    for root in _SIBLING_PY_ROOTS:
        paths.extend(root.glob("*.py"))
    return sorted(paths)


def _dev_watch_hash() -> str:
    digest = hashlib.md5(__version__.encode())
    for path in watched_paths():
        digest.update(path.name.encode())
        try:
            digest.update(str(path.stat().st_mtime_ns).encode())
        except OSError:
            continue
    return digest.hexdigest()


def _config(request: Request):
    return request.app.state.ceres_config


# (audit finding #6): the dev loop (browser poll every 1.5 s + Layer-3 process
# watcher every 2 s, each a glob + stat() sweep) is wanted on the local board
# (127.0.0.1:27390, `ceres.service` sets nothing -> ON) and pure noise on a production server
# (1 vCPU). One switch: env `CERES_DEV_WATCH=0` (or `--no-reload-watch`) turns off the
# watcher thread, the endpoint's scanning and -- via the `enabled:false` reply the
# client reads on its first poll -- the browser polling. A production systemd unit gets
# `Environment=CERES_DEV_WATCH=0` at the next deploy (maintainer's decision
# the deploy script does not manage the unit). Read per call, so tests can flip it.
_DEV_WATCH_OFF = ("0", "false", "no", "off")


def dev_watch_enabled() -> bool:
    return os.environ.get("CERES_DEV_WATCH", "1").strip().lower() not in _DEV_WATCH_OFF


_RELEASE_DIR = _WATCH_ROOT.parent / "work" / "requirements" / "releases"
_CHANGELOG = _WATCH_ROOT.parent / "CHANGELOG.md"


_RELEASED_STAMP = _WATCH_ROOT / "released_version.txt"
_released_cache: dict[str, object] = {}


def _changelog_versions() -> set[str]:
    """Versions that have a real CHANGELOG section, cached on the file's mtime.

    The changelog is ~1 MB and /api/dev/watch is polled every 1.5 s per open tab, so
    re-reading it per call cost ~10 ms each time (review finding, ).
    """
    try:
        stamp = _CHANGELOG.stat().st_mtime_ns
    except OSError:
        return set()
    if _released_cache.get("mtime") != stamp:
        text = _CHANGELOG.read_text(encoding="utf-8")
        _released_cache["mtime"] = stamp
        _released_cache["versions"] = {
            line.split(" ", 2)[1] for line in text.splitlines()
            if line.startswith("## ") and len(line.split(" ", 2)) > 1
        }
    return _released_cache.get("versions", set())  # type: ignore[return-value]


def released_version() -> str:
    """: the version the board may ANNOUNCE.

    The banner asks the maintainer to reload, so it may only ever name a real release.
    A version string alone proves nothing: while work is in progress the served tree
    carries the next number before it is a release at all (/).

    Three sources, in order. `ceres/released_version.txt` is written at release time and
    ships with the code, so this holds in production too, where the repo's changelog and
    release records are not deployed ( review finding: the guard was inert live).
    Otherwise a version counts as released only when BOTH its changelog section and its
    release record exist; the newest version meeting both is reported, so the banner
    stays silent until a true release lands.
    """
    version = __version__
    try:
        if _RELEASED_STAMP.is_file():
            stamped = _RELEASED_STAMP.read_text(encoding="utf-8").strip()
            if stamped:
                return stamped
    except OSError:
        pass
    try:
        entries = _changelog_versions()
        if (_RELEASE_DIR / f"{version}.json").is_file() and version in entries:
            return version
        released = sorted(
            (path.stem for path in _RELEASE_DIR.glob("*.json") if path.stem in entries),
            key=lambda name: [int(part) for part in name.split(".") if part.isdigit()],
        )
        return released[-1] if released else version
    except (OSError, ValueError):
        return version


# (incident; maintainer order: "it can NEVER be possible to run the same version on local and atlas and have different
# code. prevent that forever"). A version label identifies exactly one code state. released_version() names the
# release; the SERVED version also says whether the bytes being served are that release
# * serving checkout is a git work tree and no shipped path differs from HEAD -> exactly the released version
# * any shipped path modified, staged, deleted or untracked (.gitignore respected) -> "<released>+local.<8 hex>"
# the hex a sha256 of the shipped diff against HEAD plus every untracked shipped file's path and bytes
# * not a git checkout (the production server's exported tree) -> the released version, plus "+local.<hash>" only when the deploy
# itself stamped one into ceres/build_commit.txt (a --force deploy of a dirty tree).
# The shipped set is tools/version_gate.py's SHIPPED (and the deploy's SYNC_DIRS/SYNC_FILES).
SHIPPED_PATHSPECS = ("ceres", "praetoria", "fides", "fides_cards", "fides_land", "mnemosyne", "requirements-ceres.txt")
LOCAL_SUFFIX = "+local."
_BUILD_COMMIT = _WATCH_ROOT / "build_commit.txt"  # written by tools/deploy-atlas.sh, never committed (.gitignore)
_IDENTITY_MIN_INTERVAL = 2.0
_identity_lock = threading.Lock()
_identity_cache: dict[str, Any] = {}


def base_version(version: str) -> str:
    """The release part of a served version ("+local.ab12cd34" -> "")."""
    return str(version or "").split("+", 1)[0]


def _git(repo: Path, *args: str) -> bytes:
    import subprocess

    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}  # never take the shared checkout's index lock
    return subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args], check=True, capture_output=True,
                          timeout=15, env=env).stdout


def read_build_stamp(path: Path = _BUILD_COMMIT) -> tuple[str | None, str | None]:
    """(commit, local hash) from the deploy's stamp; (None, None) when absent."""
    try:
        lines = path.read_text(encoding="utf-8").split()
    except OSError:
        return None, None
    commit = lines[0] if lines and all(c in "0123456789abcdef" for c in lines[0]) else None
    local = next((item.split("=", 1)[1] for item in lines[1:] if item.startswith("local=")), None)
    return commit, local or None


def compute_identity(repo: Path, released: str, build_stamp: Path | None = None) -> dict[str, Any]:
    """Uncached  identity of the code under `repo`: version, commit, local_changes, local_hash."""
    if not (repo / ".git").exists():
        commit, local = read_build_stamp(build_stamp if build_stamp is not None else repo / "ceres" / "build_commit.txt")
        version = f"{released}{LOCAL_SUFFIX}{local}" if local else released
        return {"version": version, "commit": commit, "local_changes": bool(local), "local_hash": local}
    try:
        commit = _git(repo, "rev-parse", "HEAD").decode().strip() or None
    except Exception:  # noqa: BLE001 -- an unreadable HEAD is still a checkout: fall through to the status check
        commit = None
    try:
        status = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *SHIPPED_PATHSPECS)
        if not status.strip(b"\0"):
            return {"version": released, "commit": commit, "local_changes": False, "local_hash": None}
        digest = hashlib.sha256(_git(repo, "diff", "HEAD", "--binary", "--no-color", "--no-ext-diff", "--full-index",
                                     "--", *SHIPPED_PATHSPECS))
        untracked = sorted(entry[3:] for entry in status.split(b"\0") if entry.startswith(b"?? "))
        for rel in untracked:
            digest.update(b"\0untracked\0" + rel + b"\0")
            try:
                digest.update((repo / rel.decode("utf-8", "surrogateescape")).read_bytes())
            except OSError:
                digest.update(b"<unreadable>")
        local = digest.hexdigest()[:8]
    except Exception:  # noqa: BLE001 -- a checkout whose state cannot be read must never claim to be the release
        local = "unknown"
    return {"version": f"{released}{LOCAL_SUFFIX}{local}", "commit": commit, "local_changes": True, "local_hash": local}


def _identity_signature(repo: Path) -> tuple:
    """Cheap stat() sweep: every file the dev watch watches, the other shipped packages, and git's own index/HEAD."""
    paths = list(watched_paths())
    for name in ("fides", "fides_cards", "mnemosyne"):
        paths.extend((repo / name).glob("*.py"))
    paths.extend((_WATCH_ROOT / "data").glob("*"))
    paths.extend((_WATCH_ROOT / "static" / "vendor").rglob("*"))
    paths.extend(repo / name for name in SHIPPED_PATHSPECS)  # directory mtimes catch added/removed files
    paths.extend((_WATCH_ROOT / "static", _RELEASED_STAMP, _BUILD_COMMIT, repo / ".git" / "index", repo / ".git" / "HEAD"))
    try:
        head = (repo / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            paths.append(repo / ".git" / head[5:])
    except OSError:
        pass
    signature = []
    for path in paths:
        try:
            signature.append((str(path), path.stat().st_mtime_ns))
        except OSError:
            signature.append((str(path), None))
    return tuple(signature)


def served_identity() -> dict[str, Any]:
    """: the identity of the code this process serves, recomputed at most every 2 s and only when a watched
    mtime changed (a git call happens only then, never per request)."""
    import time

    repo = _WATCH_ROOT.parent
    now = time.monotonic()
    with _identity_lock:
        cached = _identity_cache.get("identity")
        if cached is not None and now - _identity_cache.get("checked", 0.0) < _IDENTITY_MIN_INTERVAL:
            return cached
        _identity_cache["checked"] = now
        # No mtime sweep where the dev loop is off (the production server: ) or there is no git checkout: there the identity is
        # simply recomputed at most every 2 s (a stamp read on the production server; one ~15 ms git status on a checkout).
        signature = _identity_signature(repo) if dev_watch_enabled() and (repo / ".git").exists() else None
        released = released_version()
        if (cached is not None and signature is not None and _identity_cache.get("signature") == signature
                and _identity_cache.get("released") == released):
            return cached
        identity = compute_identity(repo, released)
        _identity_cache.update(identity=identity, signature=signature, released=released)
        return identity


def served_version() -> str:
    """: the version label for what is actually served -- page tag, asset cache keys, health, dev watch."""
    return served_identity()["version"]


LOCAL_MARKER_TEXT = "Local — unreleased changes, not on Atlas"


def stamp_version_tokens(html_text: str) -> str:
    """: fill the page's version tokens from ONE identity read.

    {{CERES_RELEASED_VERSION}}  the release (the #board-version tag text; signing reads it as ceres/<version>);
    {{CERES_LOCAL_MARKER}}      empty for the release, else the amber "+local.<hash>" marker beside the tag;
    {{CERES_VERSION}}           the served version (asset ?v= cache keys, everything else)."""
    from html import escape

    identity = served_identity()
    released = base_version(identity["version"])
    marker = ""
    if identity["local_changes"]:
        commit = (identity["commit"] or "unknown")[:9]
        body = (f"This server runs code that differs from release v{released} (commit {commit}): shipped files have "
                "uncommitted or untracked changes. Atlas serves the committed release only, so what you see here is "
                "not what Atlas runs.")
        suffix = identity["version"][len(released):]
        marker = (f'<span class="ceres-local-marker" id="ceres-local-marker" data-tip="{escape(LOCAL_MARKER_TEXT)}" '
                  f'data-tip-body="{escape(body)}" aria-label="{escape(LOCAL_MARKER_TEXT + ". " + body)}">'
                  f'<span class="ceres-local-marker-id">{escape(suffix)}</span> <span class="ceres-local-marker-text">'
                  f'{escape(LOCAL_MARKER_TEXT)}</span><span class="ceres-local-marker-short">Local</span></span>')
    return (html_text.replace("{{CERES_RELEASED_VERSION}}", released).replace("{{CERES_LOCAL_MARKER}}", marker)
            .replace("{{CERES_VERSION}}", identity["version"]))


@router.get("/api/dev/watch", include_in_schema=False)
def dev_watch() -> dict:
    if not dev_watch_enabled():
        return {"enabled": False, "version": served_version()}
    return {"enabled": True, "hash": _dev_watch_hash(), "version": served_version()}


@router.get("/api/health")
def health(request: Request) -> dict:
    # "version" is the released version the board serves and announces, the
    # same one the page tag and /api/dev/watch carry. "build" is the raw code string
    # which during work in progress runs ahead of the last release; it is kept for
    # diagnosis and is never shown to the maintainer as a version.
    # "version" is the SERVED version ("<released>+local.<hash>" when shipped files differ from the release)
    # "commit" the git HEAD (or the deploy's stamp on the production server), "local_changes" whether the code differs from that commit.
    released = released_version()
    identity = served_identity()
    payload = {"status": "ok", "version": identity["version"], "schema": "ceres.board.v1", "dev_watch": dev_watch_enabled(),
               "paused": client_limits.paused(request.app.state.ceres_config.root),  # C3 operator pause
               "commit": identity["commit"], "local_changes": identity["local_changes"]}
    if released != __version__:
        payload["build"] = __version__
    from . import fixture_upstream  # only present in fixture mode
    fixtures = fixture_upstream.active()
    if fixtures is not None:
        payload["fixture_upstream"] = fixtures.name
    return payload


# `/?plot=<label>` reads and draws that ONE plot
# first. `/api/board?plots=<label>[,<label>]` is the same lean board from the same on-disk
# publication, with `plots` cut to the named rows; every board-wide field (totals, notices
# coverage, badge meta, idle plots) stays the FULL board's value, and `partial: true` marks it so
# the page never reads board-wide numbers from it.
PLOT_FILTER_MAX_LABELS = 20


def _plot_label_key(value) -> str | None:
    """`065-09-083`, `65-9-83` -> `65-9-83` (region-tract-plot without leading zeros); None if not a label."""
    parts = str(value or "").strip().split("-")
    if len(parts) != 3 or not all(part.isdigit() and len(part) <= 6 for part in parts):
        return None
    return "-".join(str(int(part)) for part in parts)


def _parse_plot_filter(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    labels = []
    for item in raw.split(","):
        if not item.strip():
            continue
        key = _plot_label_key(item)
        if key is None:
            raise HTTPException(status_code=400, detail="plots= takes plot labels like 065-09-083")
        if key not in labels:
            labels.append(key)
    if not labels:
        raise HTTPException(status_code=400, detail="plots= takes plot labels like 065-09-083")
    if len(labels) > PLOT_FILTER_MAX_LABELS:
        raise HTTPException(status_code=400, detail=f"plots= takes at most {PLOT_FILTER_MAX_LABELS} labels")
    return tuple(sorted(labels))


def _filtered_board(full: dict[str, Any], wanted: tuple[str, ...]) -> dict[str, Any]:
    result = dict(full)
    wanted_set = set(wanted)
    result["plots"] = [row for row in full.get("plots") or [] if _plot_label_key(row.get("plot")) in wanted_set]
    result["partial"] = True
    result["plots_filter"] = list(wanted)
    return result


def _filtered_board_response(request, wanted, player, ref, status, status_board, full_board_source, details_mtime_ns):
    """The body of `/api/board?plots=`: cached per (snapshot, player, token, filter) in the shared
    board-bytes cache; a miss builds it from `full_board_source()` (the full board, parsed once) and
    keeps only the named rows. No matching row is a 404 (never an empty board), and is not cached."""
    key, token, _ = _board_cache_key(ref, player, True, plots=wanted)
    digest = hashlib.sha1(",".join(wanted).encode()).hexdigest()[:12]
    etag = f'"{token};plots={digest}"' if token else None
    if etag and _if_none_match(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag, "X-Ceres-Board-Status": _board_status_header(status, status_board)})
    cached = None
    if token:
        try:
            with _board_cache_lock:
                entry = _board_bytes_cache.get(key)
                if entry is not None and entry[0] == token and entry[1] == details_mtime_ns:
                    _board_bytes_cache.move_to_end(key)
                    cached = entry
        except Exception:
            cached = None
    if cached is not None:
        raw, gz = cached[2], cached[3]
    else:
        filtered = _filtered_board(full_board_source(), wanted)
        if not filtered["plots"]:
            return JSONResponse(status_code=404, content={"detail": "plot not on this board", "plots_filter": list(wanted)})
        raw, gz = _cached_board_bytes(key, token, details_mtime_ns, filtered)
    headers = {"X-Ceres-Board-Status": _board_status_header(status, status_board)}
    if etag:
        headers["ETag"] = etag
    content = raw
    if len(content) >= 1024 and _accepts_gzip(request):
        content = gz if gz is not None else _cached_board_gzip(key, token, details_mtime_ns, raw)
        headers["Content-Encoding"] = "gzip"
        headers["Vary"] = "Accept-Encoding"
    return Response(content=content, media_type="application/json", headers=headers)


@router.get("/api/board")
def get_board(request: Request, player: str | None = None, plots: str | None = None):
    from . import capture as capture_mod

    config = _config(request)
    player = player_for_request(request, player)
    wanted = _parse_plot_filter(plots)
    snapshot_path = capture_mod._latest_path(config, player)
    identity = board_artifact.cached_identity(config.snapshots_dir, player)
    artifact = board_artifact.cached_artifact(config.snapshots_dir, player)
    if identity is None:
        identity = board_artifact.read_identity(config.snapshots_dir, player)
    if identity is None or artifact is None or artifact.token != identity.token:
        details_mtime_ns = details_cache_mtime()
        _, details_size = board_artifact.details_identity()
        if details_mtime_ns is None:
            details_size = None
        identity, artifact = board_artifact.load_current_payload(
            config.snapshots_dir,
            player,
            snapshot_path=snapshot_path,
            details_mtime_ns=details_mtime_ns,
            details_size=details_size,
            identity=identity,
        )
    # while the current publication is the INTERIM artifact (), plot patches on disk can only be
    # left over from an earlier read -- an /expand is refused during the interim, and every patch is keyed to the
    # previous snapshot's base. Falling back to the snapshot here served the PREVIOUS read plus those patches: rows
    # claimed `assets_loaded: true` from the old detail (the page then paid a GET + a live /expand, ~4.8 s) and the board
    # was not this load's live read (). The interim artifact is the board until the final publish lands.
    interim_publication = identity is not None and getattr(identity, "interim", False) is True
    artifact_valid = artifact is not None and identity is not None and (
        interim_publication or not capture_mod.has_plot_patches(config, player)
    )
    if artifact_valid:
        ref = capture_mod.SnapshotRef(
            path=str(snapshot_path),
            generated_at=artifact.meta.get("generated_at"),
            player=player,
            token=identity.token,
            write_counter=identity.write_counter,
            snapshot_mtime_ns=identity.snapshot_mtime_ns,
            snapshot_size=identity.snapshot_size,
            details_mtime_ns=identity.details_mtime_ns,
            details_size=identity.details_size,
        )
        # the artifact bytes are served as they are; only `meta` feeds the status header.
        status = capture_mod.freshness_from_board(config, player, ref, artifact.meta, identity.interim)
        board = artifact.meta
        bytes_key = bytes_token = bytes_details_mtime_ns = None
    else:
        report, ref = _read_snapshot_or_503(capture_mod, config, player)
        logger.warning("no board artifact for %s; projecting the board on the request thread", player)
        status = capture_mod.freshness(config, player)
        details_by_id = load_details_by_id()
        # `_cached_project_board` returns the key it derived and cached
        # against, reused below for the serialized-bytes cache -- not re-derived, so
        # the two caches can never disagree about what `details_cache_mtime()` was at
        # read time.
        raw_board, bytes_key, bytes_token, bytes_details_mtime_ns = _cached_project_board(
            report, ref, details_by_id, lean=True, player=player
        )
        board = board_artifact.prepare_board(raw_board)
    if wanted is not None:
        if artifact_valid:
            source = lambda: fastjson.loads(artifact.raw)  # noqa: E731 -- parsed only on a filter-cache miss
            filter_mtime = identity.details_mtime_ns
        else:
            source = lambda: board  # noqa: E731
            filter_mtime = bytes_details_mtime_ns
        return _filtered_board_response(request, wanted, player, ref, status, board, source, filter_mtime)
    etag = f'"{ref.token}"' if getattr(ref, "token", None) else None
    status_header = _board_status_header(status, board)
    if artifact_valid and etag and _if_none_match(request.headers.get("if-none-match"), etag):
        response = Response(status_code=304, headers={"ETag": etag, "X-Ceres-Board-Status": status_header})
    else:
        headers = {"X-Ceres-Board-Status": status_header}
        if etag:
            headers["ETag"] = etag
        if artifact_valid:
            content = artifact.raw
            if len(content) >= 1024 and _accepts_gzip(request):
                content = artifact.gzip
                headers["Content-Encoding"] = "gzip"
                headers["Vary"] = "Accept-Encoding"
        else:
            content, cached_gzip = _cached_board_bytes(bytes_key, bytes_token, bytes_details_mtime_ns, board)
            if len(content) >= 1024 and _accepts_gzip(request):
                content = (
                    cached_gzip
                    if cached_gzip is not None
                    else _cached_board_gzip(bytes_key, bytes_token, bytes_details_mtime_ns, content)
                )
                headers["Content-Encoding"] = "gzip"
                headers["Vary"] = "Accept-Encoding"
        response = Response(content=content, media_type="application/json", headers=headers)
    return response


@router.get("/api/sps-rate")
def get_sps_rate(request: Request, player: str | None = None):
    """ -- current SPS per PP per hour for the worksite preview and the table
    tooltip. Fetched lazily by the page after the board; `{"rate": null}` when no SPS
    plot on this server has a measurement, and the page then keeps its pool-share text."""
    config = _config(request)
    player = player_for_request(request, player)
    # the measurement is taken at publish time (`sps_rate.remember`); the request
    # thread holds no parsed board to measure from.
    rate = sps_rate.current(config.snapshots_dir, player, None)
    return JSONResponse(content=rate or {"rate": None}, headers={"Cache-Control": "no-store"})


def _if_none_match(value: str | None, etag: str) -> bool:
    if not value:
        return False
    return any(candidate.strip() == etag for candidate in value.split(","))


@router.get("/api/board/status")
def board_status(request: Request, player: str | None = None):
    from . import capture as capture_mod

    config = _config(request)
    player = player_for_request(request, player)
    snapshot_path = capture_mod._latest_path(config, player)
    details_mtime_ns = details_cache_mtime()
    _, details_size = board_artifact.details_identity()
    if details_mtime_ns is None:
        details_size = None
    identity, board = board_artifact.load_current(
        config.snapshots_dir,
        player,
        snapshot_path=snapshot_path,
        details_mtime_ns=details_mtime_ns,
        details_size=details_size,
    )
    if identity is not None and board is not None:
        ref = capture_mod.SnapshotRef(
            path=str(snapshot_path),
            generated_at=board.get("generated_at"),
            player=player,
            token=identity.token,
            write_counter=identity.write_counter,
            snapshot_mtime_ns=identity.snapshot_mtime_ns,
            snapshot_size=identity.snapshot_size,
            details_mtime_ns=identity.details_mtime_ns,
            details_size=identity.details_size,
        )
        status = capture_mod.freshness_from_board(config, player, ref, board, identity.interim)
    else:
        report, ref = _read_snapshot_or_503(capture_mod, config, player)
        status = capture_mod.freshness(config, player)
        board = {"coverage": report.get("coverage", {})}
    payload = _board_status_payload(status, board)
    headers = {"Cache-Control": "no-store"}
    if getattr(ref, "token", None):
        headers["ETag"] = f'"{ref.token}"'
    return JSONResponse(content=payload, headers=headers)


def _refuse_while_board_finishing(capture_mod, config, player: str):
    """: while the current publication is the interim artifact (bulk read published, the
    collection sweep and final publish still running) a plot has no snapshot of ITS read to be
    served from -- and the previous read's snapshot is never the answer (). The browser
    keeps its spinner ("Finishing the read…") and retries once the status token changes."""
    if capture_mod._interim_identity(config, player) is None:
        return None
    if capture_mod.is_running(player):
        return JSONResponse(status_code=409, content={"reason": "board_finishing", "detail": "the board read is finishing; the plot opens once it lands"})
    raise HTTPException(status_code=503, detail="the board read did not finish; refresh the board before continuing")


@router.get("/api/plots/{deed_uid}")
def get_plot(deed_uid: str, request: Request, player: str | None = None):
    from . import capture as capture_mod

    config = _config(request)
    player = player_for_request(request, player)
    refused = _refuse_while_board_finishing(capture_mod, config, player)
    if refused is not None:
        return refused
    report, ref = _read_snapshot_or_503(capture_mod, config, player)

    details_by_id = load_details_by_id()
    # project_single_plot ( board-fix batch), not project_board() -- this used
    # to build the FULL board (every OTHER plot's row too) just to read one back out
    # on a whale account that's thousands of wasted _plot_row() calls per click. See
    # board.py's project_single_plot() docstring.
    row = project_single_plot(report, deed_uid, details_by_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown deed_uid: {deed_uid}")
    plot_row = next((p for p in report["plots"] if p["deed_uid"] == deed_uid), {})
    result = dict(row)
    result["worksite_facts"] = plot_row.get("worksite_facts")
    result["diagnostics_raw"] = plot_row.get("diagnostics")
    return result


@router.get("/api/plots/{deed_uid}/workers/{asset_ref}/upgrade-options")
def get_worker_upgrade_options(deed_uid: str, asset_ref: str, request: Request, player: str | None = None):
    """Read upgrade options for one staked worker without capture or mutation."""
    from . import capture as capture_mod

    config = _config(request)
    player = player_for_request(request, player)
    report, _ref = _read_snapshot_or_503(capture_mod, config, player)
    try:
        result = project_worker_upgrade_options(report, deed_uid, asset_ref, load_details_by_id())
    except WorkerUpgradeOptionsError as exc:
        status = 404 if exc.code == "worker_not_found" else 409
        detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
        if exc.reasons:
            detail["reasons"] = exc.reasons
        raise HTTPException(status_code=status, detail=detail) from exc
    return JSONResponse(content=result)


@router.get("/api/idle-cards")
def get_idle_cards(request: Request, player: str | None = None):
    """Lazy fetch for the Idle cards section ( board-fix batch). `GET /api/board`
    now omits this array from its own response in the interest of payload size --
    measured live against `player0005`'s fully-captured account, 2026-08-21: `idle_cards`
    alone was 89% of the ENTIRE board payload (3.04 MB of 3.4 MB), the single
    biggest field by far, bigger than the sum of every plot row's own `workers`
    field. `build_idle_cards()` costs nothing extra beyond `GET /api/board` already
    paid internally to compute `totals.idle_cards_count` -- it depends only on
    `report["coverage"]`/`report["cards"]`, never on the per-plot board build, so
    this route never touches `project_board()`/`project_single_plot()` at all."""
    from . import capture as capture_mod

    config = _config(request)
    player = player_for_request(request, player)
    # never the previous read's snapshot during a whale's interim window ().
    refused = _refuse_while_board_finishing(capture_mod, config, player)
    if refused is not None:
        return refused
    report, ref = _read_snapshot_or_503(capture_mod, config, player)

    details_by_id = load_details_by_id()
    idle_cards, idle_cards_loaded = build_idle_cards(report, details_by_id)
    # Explicit JSONResponse ( whale-account performance batch) -- see
    # get_board()'s own comment; this array can be multi-MB on a large collection
    # (2.7 MB measured for player0005's 7,609 idle cards), the same jsonable_encoder
    # cost applies here.
    return JSONResponse(content={"loaded": idle_cards_loaded, "cards": idle_cards})


@router.get("/api/cooling")
def get_cooling(request: Request, player: str | None = None, capture_generated_at: str | None = None):
    """The account-wide Cooling read, bound to the page load's own live capture (,  phase 1).

    The page sends the `generated_at` of the board it drew; anything else is refused -- a missing stamp as
    `capture_unknown`, an older one as `capture_superseded` (the page adopts the newer capture,  idiom).
    Never starts a capture itself, never answers from a snapshot of unknown age."""
    from . import capture as capture_mod
    from . import capture_freshness

    config = _config(request)
    player = player_for_request(request, player)
    # never the previous read's snapshot during a whale's interim window ().
    refused = _refuse_while_board_finishing(capture_mod, config, player)
    if refused is not None:
        return refused
    report, _ref = _read_snapshot_or_503(capture_mod, config, player)
    snapshot_at = report.get("generated_at") if isinstance(report, dict) else None
    if not capture_generated_at:
        raise HTTPException(status_code=409, detail={"code": "capture_unknown", "message": "The Cooling read needs the board's own capture stamp.", "snapshot_generated_at": snapshot_at})
    if snapshot_at != capture_generated_at:
        try:
            newer = capture_freshness.capture_is_newer_than(snapshot_at, capture_generated_at)
        except capture_freshness.ValidationError:
            newer = False
        raise HTTPException(status_code=409, detail={
            "code": "capture_superseded" if newer else "capture_unknown",
            "message": "The board was drawn from a different capture; the board is updating." if newer else "That capture is not the one this server holds.",
            "snapshot_generated_at": snapshot_at,
        })
    return JSONResponse(content=build_cooling_view(report, load_details_by_id()))


CAPTURE_STATUS_WAIT_MAX_MS = 5000
# (2026-09-20, review of v0.9.687): 50ms re-checked freshness() (a snapshot-cache
# read, not free) on every tick -- fine alone, but the regression below made the client
# call this route in a hot loop, so each 50ms tick was also a real cost multiplier. 200ms
# keeps the long-poll responsive (still far under one old client poll interval) while
# capping the worst-case re-check rate.
CAPTURE_STATUS_WAIT_POLL_SECONDS = 0.20


def _capture_status_busy(status, wait_for: str) -> bool:
    """True while a caller waiting on `wait=` should keep blocking.

    `wait_for="board_ready"` (readBoard()'s early-render poll): still running/queued and
    not yet board_ready -- matches board.js readBoard()'s own early-exit condition, so an
    interim (bulk-only) publish ends the wait even though the self-heal tail is still
    running.

    `wait_for="settled"` (waitForCaptureToSettle()'s post-render tail wait): still
    running/queued, full stop -- board_ready is IGNORED. : the settle loop's own
    exit condition is `state !== 'running'`, never board_ready; with the board_ready
    variant the server returned instantly on every call once the interim board published
    (state stays 'running' through the self-heal tail), and the settle loop -- which does
    not itself sleep after a `wait=` call -- turned into a request-per-tick busy spin
    (~80/s measured) until the tail actually finished. `settled` blocks past board_ready
    so the settle loop's own request cadence matches its own exit condition."""
    if status.state not in ("running", "queued"):
        return False
    if wait_for == "settled":
        return True
    return status.board_ready is not True


@router.get("/api/capture/status")
def capture_status(request: Request, player: str | None = None, wait: str | None = None, wait_for: str = "board_ready"):
    """/: `wait` (ms, capped at CAPTURE_STATUS_WAIT_MAX_MS) turns this into a
    server-side long poll -- the request blocks, re-checking `freshness()` on a short
    internal cadence, until the capture is no longer busy for `wait_for`'s definition (see
    `_capture_status_busy`) or the wait budget runs out, then returns the freshest status
    either way. Replaces client-side fixed-interval sleeping ( F3): the client waits
    for THIS call to return instead of sleeping a full interval after the capture already
    finished. `wait_for` defaults to `"board_ready"` (readBoard()'s early-render poll);
    `waitForCaptureToSettle()` passes `wait_for=settled` explicitly. `wait` omitted, 0, or
    not a valid non-negative integer (: never a 422 for a malformed query value from
    an old cached page or a hand-typed URL) behaves exactly as before -- one immediate
    read."""
    import time as _time

    from . import capture as capture_mod

    config = _config(request)
    player = player_for_request(request, player)
    try:
        wait_ms = int(wait) if wait is not None else 0
    except (TypeError, ValueError):
        wait_ms = 0
    if wait_for not in ("board_ready", "settled"):
        wait_for = "board_ready"
    status = capture_mod.freshness(config, player)
    if wait_ms > 0:
        deadline = _time.monotonic() + min(wait_ms, CAPTURE_STATUS_WAIT_MAX_MS) / 1000
        while _capture_status_busy(status, wait_for) and _time.monotonic() < deadline:
            _time.sleep(CAPTURE_STATUS_WAIT_POLL_SECONDS)
            status = capture_mod.freshness(config, player)
    return _safe_capture_status(status)


@router.get("/api/capture/estimate")
def capture_estimate(request: Request, player: str | None = None):
    from . import capture as capture_mod

    config = _config(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "estimate")  # C3: per-client limit before any upstream read
    try:
        return capture_mod.estimate_capture(player)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="could not preflight deed count") from exc


@router.post("/api/capture/refresh")
def capture_refresh(
    request: Request,
    player: str | None = None,
    load_id: str | None = None,
    load_generation: int | None = None,
):
    """Default board refresh -- QUICK (bulk-only) capture, 2026-08-21 correction: the
    main table needs no per-deed fan-out, so this is a small, account-size-independent
    handful of requests. (The per-worker Full capture route was removed,  Q9.)"""
    _require_same_origin(request)
    from . import capture as capture_mod

    config = _config(request)
    player = player_for_request(request, player)
    if (load_id is None) != (load_generation is None):
        raise HTTPException(status_code=400, detail="load_id and load_generation must be supplied together")
    if load_id is not None and (
        not load_id or len(load_id) > 128 or load_generation is None
        or load_generation < 1 or load_generation > 9_007_199_254_740_991
    ):
        raise HTTPException(status_code=400, detail="invalid board-load identity")
    gate = client_limits.capture_gate(request, player, "quick")  # C3: another client's read is joined or refused, never killed
    if gate is not None:
        return gate
    started = (
        capture_mod.start_refresh(config, player)
        if load_id is None
        else capture_mod.start_refresh(
            config, player, load_id=load_id, load_generation=load_generation,
        )
    )
    if not started:
        if load_id is not None:
            return JSONResponse(status_code=202, content={"state": "superseded", "mode": "quick"})
        raise HTTPException(status_code=503, detail="the replacement board read could not be started")
    client_limits.record_capture_owner(request, player, "quick")
    status = capture_mod.freshness(config, player)
    # `queued` lasts only while the superseded worker exits and is reaped; the browser
    # treats it like `running` and polls the same status endpoint.
    state = "queued" if status.state == "queued" else "running"
    return JSONResponse(status_code=202, content={"state": state, "mode": "quick", "started_at": status.started_at, "queue_position": status.queue_position})


@router.post("/api/plots/{deed_uid}/expand")
def expand_plot(deed_uid: str, request: Request, player: str | None = None):
    """Fetch one plot's live deed, worksite, staking and assets, then publish its patch.

    This synchronous row-open route returns the same PlotRow shape as
    GET /api/plots/{deed_uid}."""
    _require_same_origin(request)
    from . import capture as capture_mod

    config = _config(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "plot_read")  # C3: per-client limit before any upstream read
    # while the quick capture is in its `gaps` phase the board is already published
    # and a row expand is safe (both writers merge under the per-player lock). Refusing here
    # made every plot open fail for the whole self-heal window -- 22 s on a whale account.
    if capture_mod.is_running(player) and not capture_mod.board_ready(player):
        raise HTTPException(status_code=409, detail="a board read is already running for this player -- try again once it finishes")
    refused = _refuse_while_board_finishing(capture_mod, config, player)
    if refused is not None:
        return refused
    try:
        report = capture_mod.expand_plot(config, player, deed_uid)
    except LookupError as exc:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing") from exc
    except ResponseTooLarge as exc:
        # Preserve the failing endpoint and trace ID. The read fails before the patch
        # write, so a partial response cannot replace the stored plot detail.
        raise HTTPException(
            status_code=503,
            detail=(
                f"plot assets could not be read ({exc.endpoint} exceeded its response limit; "
                f"trace {exc.trace_id}); refresh the board and try again"
            ),
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="could not fetch plot assets") from exc

    details_by_id = load_details_by_id()
    # the per-expand picker prewarm added in v0.9.20 is gone -- on a 124,974-card
    # pool it re-ran a ~10 s CPU pass on every plot open. : the post-publish prewarm is
    # gone too; the first picker open builds the pool in `candidates._heavy_lane`.
    row = project_single_plot(report, deed_uid, details_by_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown deed_uid: {deed_uid}")
    plot_row = next((p for p in report["plots"] if p["deed_uid"] == deed_uid), {})
    result = dict(row)
    result["worksite_facts"] = plot_row.get("worksite_facts")
    result["diagnostics_raw"] = plot_row.get("diagnostics")
    return result
