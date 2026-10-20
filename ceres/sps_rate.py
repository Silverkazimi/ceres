""" -- the current SPS per PP per hour, measured from real plots, never assumed.

SPS mining is a shared pool: a plot earns its share of the pool by its mining PP, and
the pool's rate per PP moves several times a day as players start, stop or change SPS
plots (maintainer, 2026-09-11). The game reports each SPS plot's own SPS/hr, so the rate is
read off producing plots -- the player's own first, the freshest rate saved for another
player on this server as the fallback. Display only: nothing is priced, gated or
broadcast on it, and a missing or unreadable measurement leaves the pool-share wording
in place. Every failure here is swallowed and logged, never raised.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _path(snapshots_dir: Path, player: str) -> Path:
    return Path(snapshots_dir) / f"sps-rate-{player}.json"


def measure(board: dict[str, Any]) -> dict[str, Any] | None:
    """PP-weighted rate over the board's producing SPS plots (sum SPS/hr / sum Total PP).
    A plot mid-worksite-change is skipped: its figures describe neither worksite."""
    work = harvest = 0.0
    plots = 0
    for row in board.get("plots") or []:
        if str(row.get("resource") or "").lower() != "sps" or row.get("pending_change"):
            continue
        # S6 / L3-07: the shared pool rate is the INTRINSIC production per PP. `work_per_hour` is already scaled by this account's own
        # DEC shortage efficiency, so dividing it by unscaled PP made another player's rate carry this account's penalty.
        w = row.get("work_per_hour_unscaled")
        if not isinstance(w, (int, float)) or isinstance(w, bool):
            w = row.get("work_per_hour")
        h = row.get("harvest_pp")
        if not isinstance(w, (int, float)) or not isinstance(h, (int, float)) or w <= 0 or h <= 0:
            continue
        work += w
        harvest += h
        plots += 1
    if not plots:
        return None
    return {"rate": work / harvest, "plots": plots, "measured_at": board.get("generated_at")}


def remember(snapshots_dir: Path, player: str, board: dict[str, Any]) -> dict[str, Any] | None:
    """Save (or clear) this player's measurement next to their board artifact."""
    try:
        measured = measure(board)
        path = _path(snapshots_dir, player)
        if measured is None:
            path.unlink(missing_ok=True)
            return None
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        with os.fdopen(fd, "w") as handle:
            json.dump(measured, handle)
        os.replace(tmp, path)
        return measured
    except Exception:  # display-only: a failed save must never fail a publish
        logger.warning("SPS rate not saved for %s", player, exc_info=True)
        return None


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    rate = value.get("rate") if isinstance(value, dict) else None
    return value if isinstance(rate, (int, float)) and rate > 0 else None


def current(snapshots_dir: Path, player: str, board: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The player's own rate, else the most recently saved rate of any other player on
    this server (never named in the result), else None."""
    try:
        own = _read(_path(snapshots_dir, player))
        if own is None and board is not None:
            own = remember(snapshots_dir, player, board)
        if own is not None:
            return {**own, "source": "own"}
        others = [p for p in Path(snapshots_dir).glob("sps-rate-*.json") if p != _path(snapshots_dir, player)]
        for path in sorted(others, key=lambda p: p.stat().st_mtime, reverse=True):
            other = _read(path)
            if other is not None:
                return {"rate": other["rate"], "plots": other.get("plots"), "measured_at": other.get("measured_at"), "source": "other"}
    except Exception:
        logger.warning("SPS rate lookup failed for %s", player, exc_info=True)
    return None
