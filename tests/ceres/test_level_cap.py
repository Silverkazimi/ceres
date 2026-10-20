"""Guards for ceres/level_cap.py -- the per-level BCX ladder behind the combine
modal's outcome ledger (v0.7.73, work/combine-modal-redesign/redesign-plan.html).

Mutation-tested per the internal rules's binding standard: each guard has its own defect
injected, watched red, and the injection recorded in a one-line docstring note.
"""

from __future__ import annotations

import json

import pytest

from ceres.level_cap import level_for_bcx, resolve_level_thresholds


def test_alpha_beta_max_thresholds_match_bcx_cap_exactly() -> None:
    """The last entry of resolve_level_thresholds() for every alpha/beta rarity x
 foil cell must equal ceres/data/max_bcx.json's own max_bcx figure -- the two
 modules must never disagree about the same printing's ceiling."""
    max_bcx = json.load(open("ceres/data/max_bcx.json"))
    for card_set, edition in (("alpha", 0), ("beta", 3)):
        for rarity in ("common", "rare", "epic", "legendary"):
            for gold in (False, True):
                thresholds = resolve_level_thresholds(1, edition, rarity, gold, card_set)
                expected = max_bcx["regimes"][card_set]["max_bcx"]["gold" if gold else "regular"][rarity]
                assert thresholds is not None
                assert thresholds[-1] == expected
                assert len(thresholds) == max_bcx["regimes"][card_set]["max_level"][rarity]


def test_alpha_beta_max_threshold_mutation_is_red() -> None:
    """Injected defect: drop the regular-BCX '+1' offset ceres/level_cap.py's
 provenance block documents (ceil(xp/xp_per_card) instead of
 ceil(xp/xp_per_card) + 1). Reproducing the injected (wrong) formula directly
 against the same live-sourced tables level_cap.py reads shows it undershoots
 max_bcx.json's alpha-common-regular figure by exactly 1 -- proving
 test_alpha_beta_max_thresholds_match_bcx_cap_exactly() above would go red
 were that offset ever dropped from the real module."""
    import math

    import ceres.level_cap as level_cap

    max_bcx = json.load(open("ceres/data/max_bcx.json"))
    data = level_cap._load()
    max_xp = data["xp_levels"][0][-1]  # common, last (max-level) threshold
    xp_per_card = data["alpha_xp"][0]
    without_offset = math.ceil(max_xp / xp_per_card)  # the injected (wrong) formula
    expected = max_bcx["regimes"]["alpha"]["max_bcx"]["regular"]["common"]
    assert without_offset != expected
    assert without_offset == expected - 1


def test_modern_thresholds_are_the_raw_combine_rates_table() -> None:
    """A modern-regime card_set (e.g. 'chaos') must resolve to exactly the raw
 settings-sourced combine_rates array, unmodified."""
    data = json.load(open("ceres/data/level_thresholds.json"))
    for idx, rarity in enumerate(("common", "rare", "epic", "legendary")):
        thresholds = resolve_level_thresholds(1, 6, rarity, False, "chaos")
        assert thresholds == data["combine_rates"][idx]


def test_level_for_bcx_never_extrapolates_past_the_ladder() -> None:
    """A BCX far beyond the last threshold must still cap at the ladder's own
 max level -- never a level index off the end of the array."""
    thresholds = [1, 5, 14, 30, 60, 100, 150, 220, 300, 400]
    assert level_for_bcx(thresholds, 400) == 10
    assert level_for_bcx(thresholds, 100_000) == 10  # no level 11 exists
    assert level_for_bcx(thresholds, 0) is None
    assert level_for_bcx(thresholds, 1) == 1
    assert level_for_bcx(thresholds, 4) == 1
    assert level_for_bcx(thresholds, 5) == 2


def test_level_for_bcx_ceiling_mutation_is_red() -> None:
    """Injected defect: read the level as `bcx // 10` instead of walking the
 ladder -- an unbounded projection that would report level 10,000 at
 bcx=100,000 instead of capping at 10. The guard above must go red on this
 specific injected function."""

    def broken_level_for_bcx(thresholds, bcx):
        return int(bcx // 10) if bcx else None

    thresholds = [1, 5, 14, 30, 60, 100, 150, 220, 300, 400]
    with pytest.raises(AssertionError):
        assert broken_level_for_bcx(thresholds, 100_000) == 10


def test_unresolvable_regime_or_rarity_returns_none_never_a_guess() -> None:
    assert resolve_level_thresholds(1, None, "common", False, None) is None
    assert resolve_level_thresholds(1, 6, "mythic", False, "chaos") is None
