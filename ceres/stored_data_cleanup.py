""" /  Q5: remove stored account data this version no longer uses, once per start.

Owner,  Q5: "DELETE AUTOMATICALLY at the first start of the version that stops writing it (only the
files that version no longer uses; never his receipts/records from Q4; log what was removed)."

Only files NO code path of this version reads or writes are removed, and only from the praetoria cache
(`~/.praetoria/cache`, or `PRAETORIA_CACHE_DIR`):

- ``land_assets_<ref>.json`` -- per-deed asset reads; written only by the Full capture (removed, Q9) and by
  plot opens before ; read only by the Full capture's six-hour resume.
- ``land_owned_counts_<player>.json`` -- the Full capture estimate's 5-minute cache (route removed, Q9).
- ``land_project_history_*.json`` -- no caller since  ().
- ``collection_<player>.json`` and the pre-v2 ``collection_<player>.etag``/``.etag.json`` -- the raw
  collection copy; not written since , never read (the ETag memo is ``collection_unbinding_v2_*``,
  still used until its own step).
- abandoned ``*.tmp`` write leftovers of the above, older than a day.

 (Receipts removed): also, under the Ceres root (``<ceres root>``), the whole ``account_activity/`` directory
(``<player>.json`` activity state, ``worker-N.lock``, ``*.tmp``) and ``snapshots/deed-owners-<player>.json`` (+ ``.tmp``);
nothing writes them any more. Harvest attempts, evidence and the journal stay.

Never touched: ``card_details``/``item_details`` (Q3), ``land_deeds_*``, ``land_available_*`` and
``collection_unbinding_v2_*`` (still read by this version's page load), ``~/.ceres`` (snapshots are still
this version's data; harvest attempts, evidence and the journal are Q4 records). What was removed is logged
(``ceres.cleanup``) and appended to ``<ceres root>/stored-data-cleanup.jsonl``. Runs in a daemon thread two minutes
after start, in small spaced batches, so it never shares the disk with the first page load. ``CERES_NO_STORED_DATA_CLEANUP=1`` switches it off (tests).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("ceres.cleanup")

DISABLE_ENV = "CERES_NO_STORED_DATA_CLEANUP"
ENABLE_ENV = "CERES_STORED_DATA_CLEANUP"  # S10 / L5-09: opt in; a fresh local install never deletes files it did not write
TMP_MIN_AGE_SECONDS = 86400
# ("must not make the page slower in any way"): start well after the first page load and unlink in
# small, spaced batches (11,452 files took 1.8 s in one go, measured 2026-09-28), so no page read shares the disk.
START_DELAY_SECONDS = 120.0
BATCH_FILES = 200
BATCH_PAUSE_SECONDS = 0.05
_PATTERNS = {
    "land_assets": re.compile(r"^land_assets_[0-9a-f]{8,64}\.json$"),
    "land_owned_counts": re.compile(r"^land_owned_counts_[A-Za-z0-9_.-]+\.json$"),
    "land_project_history": re.compile(r"^land_project_history_[A-Za-z0-9_.-]+\.json$"),
    "collection_legacy": re.compile(r"^collection_(?!unbinding_v2_)[A-Za-z0-9_.-]+?(\.json|\.etag|\.etag\.json)$"),
}
_TMP = re.compile(r"^\.?(land_assets_|land_owned_counts_|land_project_history_|collection_(?!unbinding_v2_)).*\.tmp$")


def _category(name: str, now: float, path: Path) -> str | None:
    for category, pattern in _PATTERNS.items():
        if pattern.match(name):
            return category
    if _TMP.match(name):
        try:
            if now - path.stat().st_mtime >= TMP_MIN_AGE_SECONDS:
                return "tmp_leftover"
        except OSError:
            return None
    return None


def cache_root() -> Path:
    return Path(os.environ.get("PRAETORIA_CACHE_DIR") or Path.home() / ".praetoria" / "cache")


def remove_unused(cache_dir: Path, now: float | None = None, pause: float = 0.0) -> dict[str, Any]:
    """Delete the unused files in ``cache_dir``; returns counts and bytes per category."""
    now = time.time() if now is None else now
    summary: dict[str, Any] = {"files": 0, "bytes": 0, "by_category": {}, "errors": 0}
    if not cache_dir.is_dir():
        return summary
    for path in cache_dir.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        category = _category(path.name, now, path)
        if category is None:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            summary["errors"] += 1
            continue
        entry = summary["by_category"].setdefault(category, {"files": 0, "bytes": 0})
        entry["files"] += 1
        entry["bytes"] += size
        summary["files"] += 1
        summary["bytes"] += size
        if pause and summary["files"] % BATCH_FILES == 0:
            time.sleep(pause)
    return summary


_DEED_OWNERS = re.compile(r"^deed-owners-[A-Za-z0-9_.-]+\.(json|tmp)$")


def remove_receipts_files(ceres_root: Path, summary: dict[str, Any]) -> None:
    """: delete what the removed Receipts feature stored under the Ceres root; adds to ``summary``."""
    targets = []
    activity = ceres_root / "account_activity"
    if activity.is_dir() and not activity.is_symlink():
        targets += [path for path in activity.iterdir() if path.is_file() and not path.is_symlink()]
    snapshots = ceres_root / "snapshots"
    if snapshots.is_dir():
        targets += [path for path in snapshots.iterdir()
                    if _DEED_OWNERS.match(path.name) and path.is_file() and not path.is_symlink()]
    for path in targets:
        category = "receipts_activity_state" if path.parent == activity else "receipts_deed_owners"
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            summary["errors"] += 1
            continue
        entry = summary["by_category"].setdefault(category, {"files": 0, "bytes": 0})
        entry["files"] += 1
        entry["bytes"] += size
        summary["files"] += 1
        summary["bytes"] += size
    if activity.is_dir() and not activity.is_symlink():
        try:
            activity.rmdir()
        except OSError:
            pass


def run(ceres_root: Path, version: str, pause: float = 0.0) -> dict[str, Any] | None:
    if os.environ.get(DISABLE_ENV) == "1":
        return None
    directory = cache_root()
    summary = remove_unused(directory, pause=pause)
    remove_receipts_files(ceres_root, summary)
    if summary["files"] or summary["errors"]:
        record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "version": version, "cache_dir": str(directory), **summary}
        logger.warning("removed %d unused stored-data file(s), %.1f MB, from %s: %s",
                       summary["files"], summary["bytes"] / 1e6, directory, summary["by_category"])
        try:
            ceres_root.mkdir(parents=True, exist_ok=True)
            with (ceres_root / "stored-data-cleanup.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            logger.warning("could not append the cleanup record under %s", ceres_root)
    return summary


def start_in_background(ceres_root: Path, version: str) -> threading.Thread | None:
    if os.environ.get(ENABLE_ENV) != "1" or os.environ.get(DISABLE_ENV) == "1":
        return None
    thread = threading.Thread(target=_safe_run, args=(ceres_root, version, START_DELAY_SECONDS), name="stored-data-cleanup", daemon=True)
    thread.start()
    return thread


def _safe_run(ceres_root: Path, version: str, delay: float = 0.0) -> None:
    try:
        if delay:
            time.sleep(delay)
        run(ceres_root, version, pause=BATCH_PAUSE_SECONDS)
    except Exception:  # noqa: BLE001 -- a cleanup failure never touches serving
        logger.exception("stored-data cleanup failed")
