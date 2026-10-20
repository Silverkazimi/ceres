"""Pure BCX-cap lookup: (card_detail_id, edition, rarity, gold, card_set, foil) -> max BCX at max level.

Replaces the old observational derivation in board.py's `build_bcx_cap_table`, which
scanned one account's collection for a maxed specimen of the exact same printing and
read that copy's own `bcx` -- coverage was necessarily partial (85/182 staked
workers) and always missed printings with no maxed copy owned, including every
summoner.

This is a table lookup instead: BCX-to-level thresholds depend only on rarity,
print era (which selects a "regime" -- Alpha/Beta/Modern/Foundations) and foil,
never on the specific collection. The print era is keyed on `card_set` (the
card's actual print identity, e.g. "alpha"/"beta"/"chaos"/"conclave"), NOT on
the numeric `edition` field -- `edition` groups multiple distinct card_sets
under one number (confirmed: `card_detail_id 337` "Pelacor Bandit" is
`card_set: "chaos"` but `edition: 3`; edition 2 "Promo" alone spans six
different card_sets). `edition` is kept as a fallback for records that lack
`card_set`, using `card_detail_id` to break the Promo (edition 2) regime tie
the same way the old edition-only logic did -- see `_regime_for` below.

Source of truth: `ceres/data/max_bcx.json`, sourced from
`api.splinterlands.com/settings` (unauthenticated GET, live production config --
`combine_rates` / `combine_rates_gold` / `foundations_combine_rates`), fetched
2026-08-21 and cross-checked against a real 7,807-card collection with zero
contradictions. See that file's `_provenance` block for the full derivation and
cross-check detail -- this module holds no second copy of the numbers. It is
resolved relative to this module's own location, not the process CWD, because
this path must resolve the same way whether run from a dev shell or a deployed
service -- `work/bcx-max-research/max_bcx.json` is a research artifact that is
not part of the deployed package.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_TABLE_PATH = Path(__file__).resolve().parent / "data" / "max_bcx.json"

_FOUNDATIONS_EDITIONS = {15, 16}
_BETA_EDITIONS = {1, 3}
_PROMO_EDITION = 2
_PROMO_ALPHA_MAX_ID = 100  # promo card_detail_id < 100 -> Alpha-era, >= 100 -> Beta-era

# card_set -> regime, for the sets that DON'T resolve to "modern". Every other
# card_set string (chaos/untamed/rebellion/conclave/land/gladius/other/... and
# any future set) resolves to "modern" -- this matches ceres/data/max_bcx.json's
# own "modern" regime coverage note, which explicitly folds all of those in.
# Source: work/pp-per-cc/pp-per-cc-table.md, "SECOND PASS (2026-08-29)" -- a
# card_set × edition cross-tab over 11,754 cards on 2 accounts, checked against
# the regime table this file loads.
_CARD_SET_REGIME = {
    "alpha": "alpha",
    "beta": "beta",
    "foundations": "foundations",
}


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def _load_regimes() -> dict[str, Any]:
    try:
        with _TABLE_PATH.open() as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"BCX-cap data file missing at {_TABLE_PATH} -- ceres/data/max_bcx.json "
            "must ship with the deployed package; a 'work/' copy does not count."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"BCX-cap data file at {_TABLE_PATH} is not valid JSON: {exc}") from exc
    try:
        return data["regimes"]
    except KeyError as exc:
        raise RuntimeError(f"BCX-cap data file at {_TABLE_PATH} is missing the required 'regimes' key") from exc


def _regime_for(card_set: Any, edition: Any, card_detail_id: Any) -> str | None:
    """Regime selection keyed on `card_set` -- the card's actual print-era
    identity -- falling back to the numeric `edition` field only when `card_set`
    is absent from the record.

    The numeric `edition` field is NOT the card's print identity: it mixes
    several distinct print runs under one number. Confirmed two ways
    (work/pp-per-cc/pp-per-cc-table.md, "SECOND PASS (2026-08-29)", 11,754
    cards across 2 accounts): (1) `card_detail_id 337` "Pelacor Bandit" carries
    `card_set: "chaos"` (modern regime, max common BCX 400) but `edition: 3`,
    which the old edition-only dispatch hardcoded to the Beta regime (505) --
    a genuine false-negative on overlevelling checks; (2) edition 2 ("Promo")
    alone spans SIX card_set values (conclave/beta/rebellion/chaos/untamed/
    alpha) on one account, not just Alpha-or-Beta as the old card_detail_id<100
    tiebreak assumed. `card_set` resolves both cases directly and is a strict
    generalization of the old edition-based rule, not an approximation of it.

    Edition-based fallback is kept for records that predate `card_set` in the
    snapshot/cache (older cached data) or otherwise omit it -- every record
    checked from both the live collection endpoint and current land snapshots
    carries `card_set` (0 missing across 7,868 collection cards + 180 staked
    workers on player0005, and 3,725 collection cards on player0001), so the
    fallback path is defensive, not expected to fire on current data.
    """
    if isinstance(card_set, str):
        normalized = card_set.strip().lower()
        if normalized:
            regime = _CARD_SET_REGIME.get(normalized)
            if regime is not None:
                return regime
            return "modern"
    edition = _as_int(edition)
    if edition is None:
        return None
    if edition in _FOUNDATIONS_EDITIONS:
        return "foundations"
    detail_id = _as_int(card_detail_id)
    if edition == 0 or (edition == _PROMO_EDITION and detail_id is not None and detail_id < _PROMO_ALPHA_MAX_ID):
        return "alpha"
    if edition in _BETA_EDITIONS or (edition == _PROMO_EDITION and detail_id is not None and detail_id >= _PROMO_ALPHA_MAX_ID):
        return "beta"
    if edition >= 4:
        return "modern"
    return None


def resolve_bcx_max(
    card_detail_id: Any,
    edition: Any,
    rarity: Any,
    gold: Any,
    card_set: Any = None,
    foil: Any = None,
) -> int | None:
    """Max BCX at max level for this exact printing, or None if it cannot be
    resolved (unknown rarity, unmapped edition, missing edition) -- never a guessed
    value. Callers render None as the existing honest dash, same as before.
    Known foil codes 3 and 4 return zero as an always-maxed verdict sentinel,
    not as a numeric ceiling.

    `card_set` is optional only for backward compatibility with call sites that
    have not been updated; pass it whenever the caller has it -- it is the
    correct regime key, `edition` is the fallback. `foil` is optional for the
    same reason. See `_regime_for`."""
    regime_name = _regime_for(card_set, edition, card_detail_id)
    if regime_name is None:
        return None
    regime = _load_regimes().get(regime_name)
    if regime is None:
        return None
    if rarity not in {"common", "rare", "epic", "legendary"}:
        return None
    if _as_int(foil) in {3, 4}:
        # REASON: these variants are always maxed, so zero is a verdict sentinel rather than a ceiling.
        return 0
    foil_key = "gold" if gold else "regular"
    table = regime.get("max_bcx", {}).get(foil_key, {})
    return table.get(rarity)
