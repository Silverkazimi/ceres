from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ceres.candidates import CandidateRow, construction_sort_key, rank_candidates, serialize_candidate
from praetoria.land_reallocate import (
    MovableCard,
    Plot,
    evaluate_candidates_for_plot,
    score_candidates_for_plot,
)


NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


def _plot(*, cap: float = 100_000, cards: list[dict] | None = None) -> Plot:
    return Plot(
        "deed", "1-1-1", "common", "occupied", "GRAIN", "T", 5, 1.0, cap,
        {"Red": 0.1, "Blue": -0.5, "White": 0.0, "Black": -0.5, "Green": 0.0, "Gold": 0.0},
        cards or [],
    )


def _candidate(name: str, base: float, color: str, ref: str) -> MovableCard:
    return MovableCard(name, color, base, 0.0, "idle", None, asset_ref=ref, card_set="chaos")


def _report(*, cap: float = 100_000, cards: list[dict] | None = None) -> dict:
    return {
        "plots": [{
            "deed_uid": "deed",
            "facts": {"plot": "1-1-1", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
            "staking_facts": {
                "max_workers_allowed": 5, "total_base_pp_cap": cap,
                "is_power_core_staked": True, "is_energized": False, "is_runi_staked": False,
                "deed_rarity_boost": 0, "totem_boost": 0, "title_boost": 0,
                "runi_boost": 0, "deed_status_token_boost": 0,
                "red_biome_modifier": 0.1, "blue_biome_modifier": -0.5,
                "white_biome_modifier": 0, "black_biome_modifier": -0.5,
                "green_biome_modifier": 0, "gold_biome_modifier": 0,
            },
        }],
        "workers": [],
        "cards": cards or [],
        "unassigned_items": {
            "totem": {
                "capturable": True,
                "items": [{"item_detail_id": 318, "name": "Common Totem", "count": 1}],
                "uids": ["I-318-one"],
            },
        },
    }


def test_structured_evaluator_retains_clipped_point_and_legacy_wrapper_returns_it() -> None:
    plot = _plot(cap=1_000, cards=[{"name": "Resident", "color": "Red", "base": 900, "cur": 990, "cur_known": True, "asset_ref": "resident", "slot": 1}])
    candidate = _candidate("Over cap", 200, "Red", "over-cap")
    evaluation = evaluate_candidates_for_plot(
        plot, [candidate], target_slot_occupant=None, details_by_id={}, base_pp_ceiling=1_000,
    )[0]

    assert evaluation.construction_pp["status"] == "point"
    assert evaluation.plot_impact["status"] == "point"
    scored = score_candidates_for_plot(
        plot, [candidate], target_slot_occupant=None, details_by_id={}, base_pp_ceiling=1_000,
    )
    assert scored
    assert scored[0][1] == pytest.approx(110.0)


def test_ranked_cap_candidate_is_point_visible_and_not_an_exclusion() -> None:
    report = _report(cap=1_000, cards=[
        {
            "asset_ref": "point", "classification": {"bucket": "unstaked_apparent"},
            "facts": {"card_detail_id": 1, "land_base_pp": 900, "level": 1, "edition": 3, "card_set": "chaos"},
        },
        {
            "asset_ref": "unknown", "classification": {"bucket": "unstaked_apparent"},
            "facts": {"card_detail_id": 2, "land_base_pp": 2_000, "level": 1, "edition": 3, "card_set": "rebellion"},
        },
    ])
    details = {
        1: {"name": "Point", "color": "Red"},
        2: {"name": "Unknown", "color": "Green"},
    }
    body = rank_candidates(report, "deed", "worker", player="player0005", now=NOW, details_by_id=details, limit=10)

    # CORRECTED 2026-08-27: the unknown card's own base PP (2,000, terrain-neutral) is a
    # genuinely bigger magnitude than the point card's terrain-adjusted value (990), so under
    # the magnitude-first sort it now leads -- the previous "point always first" expectation
    # was itself an instance of the rank-before-magnitude defect this fix corrects.
    assert [row["asset_ref"] for row in body["candidates"]] == ["unknown", "point"]
    assert body["candidates"][1]["construction_pp"]["value"] == pytest.approx(990)
    assert body["candidates"][0]["construction_pp"]["status"] == "point"
    assert body["candidates"][0]["net_gain"] is not None
    assert body["excluded_counts"]["base_cap"] == 0
    assert body["estimate_counts"] == {"point": 2, "range": 0, "unknown": 0, "not_applicable": 0}


def test_signed_terrain_and_card_set_are_serialized_with_stage_one_pp() -> None:
    report = _report(cards=[{
        "asset_ref": "zintar", "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 3, "land_base_pp": 11_000, "level": 1, "edition": 3, "card_set": "alpha"},
    }])
    body = rank_candidates(
        report, "deed", "worker", player="player0005", now=NOW,
        details_by_id={3: {"name": "Zintar Mortalis", "color": "Black"}},
    )
    row = body["candidates"][0]

    assert row["card_set"] == "alpha"
    assert row["terrain_boost"] == -0.5
    assert row["construction_pp"] == {
        "status": "point", "value": 5_500, "low": 5_500, "high": 5_500, "reason": None,
    }
    assert row["plot_impact"]["status"] == "point"


def test_missing_card_metadata_uses_neutral_default_but_keeps_impact_unknown() -> None:
    report = _report(cards=[{
        "asset_ref": "metadata-missing", "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 404, "land_base_pp": 1_000, "level": 1, "edition": 3, "card_set": "chaos"},
    }])
    row = rank_candidates(
        report, "deed", "worker", player="player0005", now=NOW, details_by_id={},
    )["candidates"][0]

    # An absent colour is normalized to Gray, whose stage-one terrain effect is a known neutral
    # value. The plot-wide impact still fails closed because the source metadata was absent.
    assert row["terrain_boost"] == 0.0
    assert row["construction_pp"] == {
        "status": "point", "value": 1000.0, "low": 1000.0, "high": 1000.0, "reason": None,
    }
    assert row["construction_pp_delta"]["reason"] == "terrain_modifier_unknown"
    assert row["plot_impact"]["reason"] == "terrain_modifier_unknown"
    assert row["net_gain"] is None


def test_construction_sort_orders_by_magnitude_with_status_as_tiebreak_only() -> None:
    # CORRECTED 2026-08-27: this test previously asserted point > range > unknown regardless
    # of magnitude -- i.e. it encoded the exact defect being fixed (estimate STATUS dominating
    # PP magnitude), with the fixture's own base values (100 / 10,000 / 20,000) chosen so the
    # bug's ordering and a magnitude-first ordering would have looked different, yet the
    # assertion still asked for the buggy one. The corrected expectation is magnitude-first
    # `rank` only breaking a tie between rows of equal magnitude.
    def row(name: str, base: float, ref: str, construction: dict, impact: dict | None = None) -> CandidateRow:
        return CandidateRow(
            _candidate(name, base, "Red", ref), {}, {}, "unstaked_apparent", "STK-LND-WKR", {"ready": True},
            construction_pp=construction, plot_impact=impact,
        )

    point = row("Point", 100, "p", {"status": "point", "value": 100, "low": 100, "high": 100, "reason": None})
    ranged = row("Range", 10_000, "r", {"status": "range", "value": None, "low": 5_000, "high": 8_000, "reason": "bounded"})
    unknown_b = row("Beta", 20_000, "u-b", {"status": "unknown", "value": None, "low": None, "high": None, "reason": "metadata_unavailable"})
    unknown_a = row("Alpha", 20_000, "u-a", {"status": "unknown", "value": None, "low": None, "high": None, "reason": "metadata_unavailable"})

    assert sorted([unknown_b, ranged, unknown_a, point], key=construction_sort_key) == [unknown_a, unknown_b, ranged, point]


def test_item_estimates_are_not_applicable_instead_of_zero() -> None:
    body = rank_candidates(_report(), "deed", "totem", player="player0005", now=NOW, details_by_id={})
    row = body["candidates"][0]

    assert row["construction_pp"]["status"] == "not_applicable"
    assert row["plot_impact"]["status"] == "not_applicable"
    assert row["net_gain"] is None
    assert row["net_gain_breakdown"] is None
    assert body["estimate_counts"]["not_applicable"] == 1


def test_serialization_rejects_non_finite_estimates() -> None:
    row = CandidateRow(
        _candidate("Invalid", 100, "Red", "invalid"), {}, {}, "unstaked_apparent", "STK-LND-WKR", {"ready": True},
        construction_pp={"status": "point", "value": float("nan"), "low": float("nan"), "high": float("nan"), "reason": None},
    )
    with pytest.raises(ValueError, match="finite"):
        serialize_candidate(row)
