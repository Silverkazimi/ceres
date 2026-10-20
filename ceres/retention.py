""" (W2/W3b): what Ceres keeps about a player, for how long, and how it is deleted.

Three classes, never mixed:
- CACHE: public game data Ceres fetched for a board (snapshots, deed lists, collection tags). Expires seven days after the player's last board read and can be deleted
  on request. It is rebuilt from the game on the next visit.
- SPACE: the player's own saved routing setup, staged changes and their ten older versions (`player_space`). Never expires; deleted only by the player, with a fresh
  Posting signature.
- SAFETY: the tiny records of a routing transaction whose outcome is unknown (`routing_safety`) and the harvest receipts. They stop a second signature of the same spend;
  they are deleted when the chain settles them, not on request, and the deletion report says so.
Nothing here ever lists or reads the contents of a player's documents."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from . import player_space, routing_safety
from .security import validate_player

log = logging.getLogger("ceres.retention")
CACHE_DAYS = 7
SNAPSHOT_PATTERNS = ("board-{p}-*", "land-{p}-latest.json", "land-{p}-cards-latest.json", "sps-rate-{p}.json")
PRAETORIA_PATTERNS = ("land_deeds_{p}.json", "land_available_*_{p}_*.json", "collection_unbinding_v2_{p}.etag", "collection_unbinding_v2_{p}.etag.json")


def _praetoria_cache() -> Path:
    from .stored_data_cleanup import cache_root
    return cache_root()


def _files(root: Path, player: str) -> list[Path]:
    found: list[Path] = []
    snapshots = Path(root) / "snapshots"
    for pattern in SNAPSHOT_PATTERNS:
        found.extend(snapshots.glob(pattern.format(p=player)))
    cache = _praetoria_cache()
    for pattern in PRAETORIA_PATTERNS:
        found.extend(cache.glob(pattern.format(p=player)))
    return [path for path in found if path.is_file() and not path.is_symlink()]


def delete_cache(root: Path, player: str) -> dict[str, int]:
    """Delete this player's cached game data. Public data, rebuilt on the next visit; the saved setup is not touched."""
    player = validate_player(player)
    removed = size = 0
    for path in _files(root, player):
        try:
            size += path.stat().st_size
            path.unlink()
            removed += 1
        except OSError:
            continue
    deeds = Path(root) / "snapshots" / f"deeds-{player}"
    if deeds.is_dir() and not deeds.is_symlink():
        for path in deeds.rglob("*"):
            if path.is_file() and not path.is_symlink():
                try:
                    size += path.stat().st_size
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
    return {"files": removed, "bytes": size}


def purge_stale_caches(root: Path, *, now: float | None = None, days: int = CACHE_DAYS) -> dict[str, Any]:
    """Delete the cache of every player whose newest cache file is older than `days` (no board read since). Saved setups and safety records are never touched."""
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    players: set[str] = set()
    snapshots = Path(root) / "snapshots"
    for path in snapshots.glob("board-*-counter.json"):
        players.add(path.name[len("board-"):-len("-counter.json")])
    purged: list[str] = []
    for player in sorted(players):
        try:
            validate_player(player)
        except Exception:  # noqa: BLE001 -- a file name that is not an account name is left alone
            continue
        files = _files(root, player)
        if files and max(path.stat().st_mtime for path in files if path.exists()) < cutoff:
            delete_cache(root, player)
            purged.append(player)
    return {"players": len(purged)}


def delete_space(root: Path, player: str) -> dict[str, Any]:
    """Delete the player's saved routing setup, staged changes and all their older versions. Reports what is NOT deleted (unresolved routing transactions)."""
    player = validate_player(player)
    removed = player_space.delete_all(Path(root), player)
    return {"documents": removed, "kept_safety_records": len(routing_safety.unresolved(Path(root), player))}


def start_in_background(root: Path) -> None:
    """Daily sweep of expired caches (hosted: CERES_STORED_DATA_CLEANUP=1, like the other cleanup)."""
    import threading
    if os.environ.get("CERES_STORED_DATA_CLEANUP") != "1" or os.environ.get("CERES_NO_STORED_DATA_CLEANUP") == "1":
        return

    def loop() -> None:
        time.sleep(300)
        while True:
            try:
                summary = purge_stale_caches(root)
                if summary["players"]:
                    log.info("expired caches removed for %d player(s)", summary["players"])
            except Exception:  # noqa: BLE001 -- a sweep failure never touches serving
                log.exception("cache retention sweep failed")
            time.sleep(86400)
    threading.Thread(target=loop, name="cache-retention", daemon=True).start()
