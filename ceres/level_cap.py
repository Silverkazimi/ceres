"""Pure BCX-threshold-per-level lookup: (card_detail_id, edition, rarity, gold, card_set) ->
a tuple of BCX thresholds, one per level starting at level 1 (thresholds[0] is the BCX
needed to BE level 1, thresholds[-1] is the BCX needed for max level).

Companion to `bcx_cap.py`, which resolves only the max-level BCX figure; this resolves
the whole ladder so a client can compute the LEVEL reached by an arbitrary BCX total (the
combine modal's "after" projection), not just whether a card is already maxed. Reuses
`bcx_cap._regime_for` for regime selection so the two modules can never disagree about
which regime a printing belongs to.

Source of truth: `ceres/data/level_thresholds.json`, a live fetch of
`https://api.splinterlands.com/settings` (2026-08-30) -- the same endpoint that sourced
`ceres/data/max_bcx.json`. Two representations exist in that live config:

- modern/foundations/soulkeep (`combine_rates*` families): BCX thresholds per level,
  directly usable as-is.
- alpha/beta (`xp_levels` + `alpha_xp`/`alpha_gold_xp`/`beta_xp`/`beta_gold_xp`): an XP-based
  system predating the BCX-combine mechanic. Converted here via the same formula
  `bcx_cap.py`'s provenance block already documents for the MAX-level case --
  `ceil(xp_threshold / xp_per_card) + 1` (regular) or `ceil(xp_threshold / xp_per_card)`
  (gold) -- generalized from "at max level" to "at every level". Cross-checked: applying
  this formula at the last threshold reproduces every one of `max_bcx.json`'s existing
  alpha/beta max_bcx values (8 rarity x foil cells) exactly -- see
  `tests/ceres/test_level_cap.py`.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from .bcx_cap import _regime_for

_TABLE_PATH = Path(__file__).resolve().parent / "data" / "level_thresholds.json"
_RARITY_INDEX = {"common": 0, "rare": 1, "epic": 2, "legendary": 3}

_BCX_TABLE_KEYS = {
    "modern": ("combine_rates", "combine_rates_gold"),
    "foundations": ("foundations_combine_rates", "foundations_combine_rates_gold"),
    "soulkeep": ("sk_combine_rates", "sk_combine_rates_gold"),
}
_XP_PER_CARD_KEYS = {
    ("alpha", False): "alpha_xp",
    ("alpha", True): "alpha_gold_xp",
    ("beta", False): "beta_xp",
    ("beta", True): "beta_gold_xp",
}


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    try:
        with _TABLE_PATH.open() as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"level-threshold data file missing at {_TABLE_PATH} -- ceres/data/level_thresholds.json "
            "must ship with the deployed package; a 'work/' or /tmp copy does not count."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"level-threshold data file at {_TABLE_PATH} is not valid JSON: {exc}") from exc
    return data


@lru_cache(maxsize=None)
def _thresholds_for(regime: str, rarity_idx: int, gold: bool) -> tuple[int, ...] | None:
    data = _load()
    if regime in _BCX_TABLE_KEYS:
        key = _BCX_TABLE_KEYS[regime][1 if gold else 0]
        table = data.get(key)
        if not table or rarity_idx >= len(table):
            return None
        return tuple(int(v) for v in table[rarity_idx])
    if regime in ("alpha", "beta"):
        xp_levels = data.get("xp_levels")
        if not xp_levels or rarity_idx >= len(xp_levels):
            return None
        xp_key = _XP_PER_CARD_KEYS[(regime, gold)]
        xp_per_card = data.get(xp_key, [None] * 4)[rarity_idx]
        if not xp_per_card:
            return None
        thresholds = [1]
        for xp_threshold in xp_levels[rarity_idx]:
            bcx_t = math.ceil(xp_threshold / xp_per_card) if gold else math.ceil(xp_threshold / xp_per_card) + 1
            thresholds.append(max(bcx_t, thresholds[-1]))  # monotonic guard against a source-data surprise
        return tuple(thresholds)
    return None


def resolve_level_thresholds(card_detail_id: Any, edition: Any, rarity: Any, gold: Any, card_set: Any = None) -> list[int] | None:
    """The full per-level BCX ladder for this exact printing, or None if it cannot be
    resolved (unknown rarity, unmapped edition/card_set). `thresholds[level - 1]` is the
    BCX needed to reach `level`; `len(thresholds)` is the max level."""
    regime = _regime_for(card_set, edition, card_detail_id)
    if regime is None:
        return None
    rarity_idx = _RARITY_INDEX.get(rarity)
    if rarity_idx is None:
        return None
    thresholds = _thresholds_for(regime, rarity_idx, bool(gold))
    return list(thresholds) if thresholds is not None else None


def level_for_bcx(thresholds: list[int] | tuple[int, ...] | None, bcx: Any) -> int | None:
    """The level reached at `bcx`, given a ladder from `resolve_level_thresholds`. Never
    extrapolates past the ladder's own max level -- the highest index whose threshold is
    met is the answer, full stop."""
    if not thresholds or bcx is None:
        return None
    try:
        bcx_value = float(bcx)
    except (TypeError, ValueError):
        return None
    level = 0
    for index, threshold in enumerate(thresholds):
        if bcx_value >= threshold:
            level = index + 1
        else:
            break
    return level or None
