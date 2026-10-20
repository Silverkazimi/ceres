"""Guards for `construction_sort_key`'s magnitude-first ordering (FR fix-candidate-sort, v0.5.80).

Root cause: `rank` (the estimate-status class -- point/range/unknown) previously sat FIRST in the
sort tuple, so every point-status row outranked every unknown-status row regardless of PP. On a
live plot this buried the 6 highest-base-PP cards (all unknown-status rows) at positions
2503-2508 of 4,060, past the picker's 100-row page. These tests pin the corrected behaviour:
magnitude leads, estimate status only breaks a tie of equal magnitude.
"""
from __future__ import annotations

from ceres.candidates import CandidateRow, construction_sort_key
from praetoria.land_reallocate import MovableCard


def _card(name: str, base: float, ref: str, terrain: float = 0.0) -> MovableCard:
    return MovableCard(name, "Red", base, 0.0, "idle", None, asset_ref=ref, card_set="chaos")


def _row(name: str, base: float, ref: str, construction: dict, terrain: float | None = 0.0) -> CandidateRow:
    return CandidateRow(
        _card(name, base, ref), {}, {}, "unstaked_apparent", "STK-LND-WKR", {"ready": True},
        construction_pp=construction, terrain=terrain,
    )


UNKNOWN = {"status": "unknown", "value": None, "low": None, "high": None, "reason": "metadata_unavailable"}


def _point(value: float) -> dict:
    return {"status": "point", "value": value, "low": value, "high": value, "reason": None}


def test_high_base_cap_unknown_outranks_low_base_point_estimate() -> None:
    """Headline guard: a high-base cap-unknown card must sort ABOVE a low-base point-estimate
    card -- this is the exact live failure (position 2503-2508 of 4,060).
    Injection: restored `rank` to the first tuple element (the pre-fix code) and re-ran this
    test -- it went red, ordering [low_point, high_unknown] instead of the asserted order,
    because rank=0 (point) beat rank=2 (unknown) before magnitude was ever compared."""
    high_unknown = _row("HighUnknown", 40_000, "high", UNKNOWN)
    low_point = _row("LowPoint", 100, "low", _point(100))

    ordered = sorted([low_point, high_unknown], key=construction_sort_key)

    assert ordered == [high_unknown, low_point]


def test_terrain_adjusted_fallback_beats_raw_base_for_equal_base_pp() -> None:
    """Terrain guard: two cap-unknown cards with IDENTICAL base PP but different terrain fit
    must sort by their DISPLAYED (terrain-adjusted) PP, not raw base -- Splinterlands shows
    `base_pp * (1 + terrain_boost)` as "the card's PP", never raw base.
    Injection: reverted the fallback to `magnitude = row.card.base` unconditionally (dropping
    the terrain multiply) and re-ran -- it went red: with terrain collapsed, the two rows tie
    on magnitude and the sort falls through to name-order ("Mismatched" < "Neutral"
    alphabetically), producing [mismatched, neutral] instead of the asserted [neutral,
    mismatched]."""
    neutral = _row("Neutral", 32_941.176, "neutral", UNKNOWN, terrain=0.0)
    mismatched = _row("Mismatched", 32_941.176, "mismatched", UNKNOWN, terrain=-0.5)

    ordered = sorted([mismatched, neutral], key=construction_sort_key)

    assert ordered == [neutral, mismatched]


def test_point_status_wins_a_true_magnitude_tie_against_unknown() -> None:
    """Tie guard: when magnitude is genuinely equal, a point-status row must still win the tie
    against an unknown-status row of the same magnitude -- `rank` is not deleted, only demoted.
    Injection: dropped `rank` from the returned tuple entirely (`(primary, impact_tie, name,
    asset_ref)`) and re-ran -- it went red: with rank absent, the two rows tie all the way down
    to name order ("Known" < "Unsure" alphabetically already happens to match, so the injection
    was made detectable by renaming the unknown row "Aaa-unsure" to sort first alphabetically);
    the assertion (point must lead irrespective of name) then failed, proving `rank` -- not
    name -- is what was deciding the tie."""
    known = _row("Known", 10_000, "known", _point(10_000))
    unsure = _row("Aaa-unsure", 10_000, "unsure", UNKNOWN)

    ordered = sorted([unsure, known], key=construction_sort_key)

    assert ordered == [known, unsure]
