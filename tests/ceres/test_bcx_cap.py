"""Tests for ceres.bcx_cap -- the pure table lookup that replaced board.py's
observational `build_bcx_cap_table` derivation. No network I/O: reads the
committed `ceres/data/max_bcx.json` table from disk, same as the
module under test does at import/call time."""

from __future__ import annotations

import pytest

from ceres.bcx_cap import _regime_for, resolve_bcx_max
from ceres.board import _worker_row
from ceres.worker_upgrade import _bcx_max


# regime selection, edition-only fallback (card_set=None)
# `_regime_for` now takes (card_set, edition, card_detail_id); passing
# card_set=None exercises the fallback path these tests were written against.

def test_regime_foundations():
    assert _regime_for(None, 15, 12345) == "foundations"
    assert _regime_for(None, 16, 12345) == "foundations"


def test_regime_alpha_plain():
    assert _regime_for(None, 0, 1) == "alpha"


def test_regime_alpha_promo_low_id():
    # edition 2 (Promo) + card_detail_id < 100 -> Alpha-era promo.
    assert _regime_for(None, 2, 42) == "alpha"


def test_regime_beta_promo_high_id():
    # edition 2 (Promo) + card_detail_id >= 100 -> Beta-era promo.
    assert _regime_for(None, 2, 150) == "beta"


def test_regime_beta_plain_and_reward():
    assert _regime_for(None, 1, 500) == "beta"
    assert _regime_for(None, 3, 500) == "beta"


def test_regime_modern():
    for edition in (4, 5, 6, 7, 8, 9, 10, 12, 14, 17, 18, 19, 20, 21):
        assert _regime_for(None, edition, 1000) == "modern", edition


def test_regime_promo_without_card_detail_id_is_unknown():
    # Ambiguous -- cannot pick Alpha vs Beta without the id, so this must stay
    # unresolved rather than guessing.
    assert _regime_for(None, 2, None) is None


def test_regime_missing_edition_is_unknown():
    assert _regime_for(None, None, 1) is None


def test_regime_blank_card_set_falls_back_to_edition():
    # An empty/whitespace card_set string is treated as absent, not as an
    # unrecognized-set-means-modern signal.
    assert _regime_for("", 0, 1) == "alpha"
    assert _regime_for("   ", 1, 500) == "beta"


# regime selection, keyed on card_set (the primary path)

def test_regime_card_set_alpha_beta_foundations():
    assert _regime_for("alpha", 0, 1) == "alpha"
    assert _regime_for("beta", 3, 500) == "beta"
    assert _regime_for("foundations", 15, 1) == "foundations"


def test_regime_card_set_case_and_whitespace_insensitive():
    assert _regime_for(" Alpha ", 0, 1) == "alpha"
    assert _regime_for("BETA", 3, 500) == "beta"


def test_regime_card_set_modern_sets():
    for card_set in ("chaos", "untamed", "rebellion", "conclave", "land", "gladius", "other"):
        assert _regime_for(card_set, 3, 1) == "modern", card_set


def test_regime_card_set_unknown_string_resolves_modern():
    # Any card_set string not in the alpha/beta/foundations set is treated as
    # modern -- matching max_bcx.json's own "modern" regime coverage note
    # which explicitly folds every era from Untamed onward (and anything not
    # yet named) into one table. Never a guess: it's the documented default.
    assert _regime_for("some-future-set", 3, 1) == "modern"


def test_regime_card_set_overrides_misleading_edition():
    # MUTATION-TESTED: this is the exact repro from
    # work/pp-per-cc/pp-per-cc-table.md (card_detail_id 337 "Pelacor Bandit"
    # card_set "chaos", edition 3). Reverting `_regime_for` to select on
    # `edition` alone (dropping the card_set branch) makes this assert
    # "beta" instead of "modern" and the test goes red.
    assert _regime_for("chaos", 3, 337) == "modern"


def test_regime_card_set_resolves_promo_grab_bag_without_tiebreak():
    # MUTATION-TESTED: edition 2 (Promo) mixes six card_set eras on one real
    # account (conclave/beta/rebellion/chaos/untamed/alpha) -- the old
    # card_detail_id<100 tiebreak only ever recovered an Alpha/Beta split
    # within that bucket. Reverting to the edition-only tiebreak (which never
    # looks at card_set) makes conclave/rebellion/untamed resolve to
    # "alpha" or "beta" by card_detail_id instead of "modern" by card_set
    # and this test goes red.
    assert _regime_for("conclave", 2, 5) == "modern"
    assert _regime_for("rebellion", 2, 500) == "modern"
    assert _regime_for("untamed", 2, 50) == "modern"


# max_bcx cells

def test_alpha_regular_table():
    assert resolve_bcx_max(1, 0, "common", False) == 379
    assert resolve_bcx_max(1, 0, "rare", False) == 86
    assert resolve_bcx_max(1, 0, "epic", False) == 32
    assert resolve_bcx_max(1, 0, "legendary", False) == 8


def test_alpha_gold_table():
    assert resolve_bcx_max(1, 0, "common", True) == 31
    assert resolve_bcx_max(1, 0, "rare", True) == 17
    assert resolve_bcx_max(1, 0, "epic", True) == 8
    assert resolve_bcx_max(1, 0, "legendary", True) == 3


def test_beta_regular_table():
    assert resolve_bcx_max(1, 1, "common", False) == 505
    assert resolve_bcx_max(1, 1, "rare", False) == 115
    assert resolve_bcx_max(1, 1, "epic", False) == 46
    assert resolve_bcx_max(1, 1, "legendary", False) == 11


def test_beta_gold_table():
    assert resolve_bcx_max(1, 1, "common", True) == 38
    assert resolve_bcx_max(1, 1, "rare", True) == 22
    assert resolve_bcx_max(1, 1, "epic", True) == 10
    assert resolve_bcx_max(1, 1, "legendary", True) == 4


def test_modern_regular_table():
    assert resolve_bcx_max(1, 21, "common", False) == 400
    assert resolve_bcx_max(1, 21, "rare", False) == 115
    assert resolve_bcx_max(1, 21, "epic", False) == 46
    assert resolve_bcx_max(1, 21, "legendary", False) == 11


def test_modern_gold_table():
    assert resolve_bcx_max(1, 21, "common", True) == 38
    assert resolve_bcx_max(1, 21, "rare", True) == 22
    assert resolve_bcx_max(1, 21, "epic", True) == 10
    assert resolve_bcx_max(1, 21, "legendary", True) == 4


def test_foundations_regular_table():
    assert resolve_bcx_max(1, 15, "common", False) == 60
    assert resolve_bcx_max(1, 15, "rare", False) == 25
    assert resolve_bcx_max(1, 15, "epic", False) == 10
    assert resolve_bcx_max(1, 15, "legendary", False) == 3


def test_foundations_gold_table():
    assert resolve_bcx_max(1, 16, "common", True) == 5
    assert resolve_bcx_max(1, 16, "rare", True) == 4
    assert resolve_bcx_max(1, 16, "epic", True) == 2
    assert resolve_bcx_max(1, 16, "legendary", True) == 1


# unknown inputs -> null, never a guess

def test_unknown_rarity_is_null():
    assert resolve_bcx_max(1, 1, "mythic", False) is None
    assert resolve_bcx_max(1, 1, None, False) is None


def test_unmapped_edition_is_null():
    assert resolve_bcx_max(None, 2, "common", False) is None  # promo, no card_detail_id
    assert resolve_bcx_max(1, None, "common", False) is None


def test_resolve_bcx_max_uses_card_set_over_misleading_edition():
    """Injection: selecting the regime from edition instead of card_set makes this returned-value guard red."""
    # End-to-end version of the mutation-tested regime repro above: card
    # 337 "Pelacor Bandit" (card_set "chaos", edition 3) must resolve the
    # modern common ceiling (400), not beta's 505.
    assert resolve_bcx_max(337, 3, "common", False, "chaos") == 400
    assert resolve_bcx_max(337, 3, "common", False, "chaos") != 505


def test_resolve_bcx_max_card_set_kwarg_is_optional():
    # A caller that hasn't been updated to pass card_set still gets the old
    # edition-only behavior -- no silent behavior change for an omitted arg.
    assert resolve_bcx_max(1, 21, "common", False) == 400


def test_summoner_style_card_resolves_by_rarity_alone():
    # Summoners have no bearing on the lookup -- it only needs rarity/edition/foil
    # so a card with no meaningful `stats` arrays (the old observational path's
    # blind spot) resolves exactly like a monster of the same printing profile.
    assert resolve_bcx_max(999, 4, "legendary", False) == 11


@pytest.mark.parametrize("foil", [3, 4])
@pytest.mark.parametrize("rarity", ["common", "rare", "epic", "legendary"])
def test_black_and_arcane_foils_resolve_as_always_maxed(foil, rarity):
    """Injection: reverting to regular/gold-only lookup returns a table value instead of the always-maxed verdict and goes red."""
    assert resolve_bcx_max(774, 14, rarity, False, "conclave", foil) == 0


def test_known_regular_and_gold_ceilings_remain_unchanged():
    """Injection: perturbing a max_bcx table value makes this returned-value guard red."""
    assert resolve_bcx_max(337, 3, "common", False, "chaos", 0) == 400
    assert resolve_bcx_max(337, 3, "common", True, "chaos", 1) == 38


def test_foil_verdict_does_not_override_unknown_rarity():
    assert resolve_bcx_max(774, 14, "mythic", False, "conclave", 3) is None


def test_board_and_worker_upgrade_pass_the_raw_foil_code():
    worker = {"card_detail_id": 774, "edition": 14, "card_set": "conclave", "foil": 3, "gold": False}
    details = {774: {"rarity": 1}}
    assert _worker_row(worker, None, None, "complete", details, [])["bcx_max"] == 0
    assert _bcx_max(worker, details) == 0
