"""§6.6 test_ability_holders.py

Real fixture data does not carry a level-matched collection-card entry for its Land/
Verico workers (the collection sample is a subset, keyed by a different card uid than
the staked copy), so ability-holder detection is exercised here against a small,
hand-built plot shaped like the real Tireless-Farmhand incident
(docs/land-worker-abilities.md) rather than against `conftest.report()`.
"""

from __future__ import annotations

from ceres.board import project_board
from ceres.metrics import ability_codes, land_ability_row, sole_holder_for

KINBOUND_DRUID_ID = 867
DETAILS = {
    KINBOUND_DRUID_ID: {
        "id": KINBOUND_DRUID_ID,
        "rarity": 2,
        "stats": {
            # level-8 (max) row per docs/land-worker-abilities.md §2a
            "land_abilities": [None] * 7 + [[["DD", -0.99], ["BLOODLINE", 0.3, "Elf"], ["WOOD", 0.7]]],
        },
    },
}


def _minimal_plot(deed_uid: str, workers: list[dict]) -> dict:
    return {
        "deed_uid": deed_uid,
        "facts": {
            "plot": "65-9-83", "rarity": "common", "status": "occupied", "terrain": "Plains",
            "resource": "WOOD", "worksite_type": "Lumber Camp", "magic_type": None,
            "deed_stats": {}, "tax_rate": "0.10", "time_crystal_value": 1000,
            "territory": "T", "region": "R",
        },
        "staking_facts": {
            "worker_count": len(workers), "max_workers_allowed": 5, "is_runi_staked": False,
            "total_harvest_pp": 1000.0, "total_dec_staked": 1000.0,
        },
        "diagnostics": [],
        "asset_fetch": {"status": "retrieved"},
    }, workers


def _report(plots_and_workers: list[tuple[dict, list[dict]]], cards: list[dict]) -> dict:
    plots = [p for p, _ in plots_and_workers]
    workers = [w for _, ws in plots_and_workers for w in ws]
    return {"query": {"player": "player0005"}, "generated_at": "2026-08-20T00:00:00Z",
            "plots": plots, "workers": workers, "cards": cards, "bonus_assets": [], "warnings": [], "coverage": {}}


def test_sole_ability_holder_gets_the_badge():
    holder = {"asset_ref": "w1", "stake_ref_uid": "TEST-A", "name": "Kinbound Druid",
               "card_detail_id": KINBOUND_DRUID_ID, "slot": 1}
    companion = {"asset_ref": "w2", "stake_ref_uid": "TEST-A", "name": "Ordinary Elf Card",
                  "card_detail_id": 9999, "slot": 2}
    plot, workers = _minimal_plot("TEST-A", [holder, companion])
    cards = [{"asset_ref": "w1", "facts": {"level": 8}}]
    report = _report([(plot, workers)], cards)

    board = project_board(report, DETAILS)
    row = board["plots"][0]
    assert "sole_ability_holder" in row["badges"]
    assert row["ability_holders"], "the DD/BLOODLINE/WOOD holder must be listed"
    holder_row = row["ability_holders"][0]
    assert set(holder_row["sole_holder_for"]) == {"DD", "BLOODLINE", "WOOD"}


def test_two_holders_of_the_same_code_get_empty_sole_holder_for():
    holder_a = {"asset_ref": "w1", "stake_ref_uid": "TEST-B", "name": "Kinbound Druid A",
                 "card_detail_id": KINBOUND_DRUID_ID, "slot": 1}
    holder_b = {"asset_ref": "w2", "stake_ref_uid": "TEST-B", "name": "Kinbound Druid B",
                 "card_detail_id": KINBOUND_DRUID_ID, "slot": 2}
    plot, workers = _minimal_plot("TEST-B", [holder_a, holder_b])
    cards = [{"asset_ref": "w1", "facts": {"level": 8}}, {"asset_ref": "w2", "facts": {"level": 8}}]
    report = _report([(plot, workers)], cards)

    board = project_board(report, DETAILS)
    row = board["plots"][0]
    for holder_row in row["ability_holders"]:
        assert holder_row["sole_holder_for"] == []


def test_sole_holder_for_helper_directly():
    row = land_ability_row(KINBOUND_DRUID_ID, 8, DETAILS)
    codes = ability_codes(row)
    assert codes == ["DD", "BLOODLINE", "WOOD"]
    assert sole_holder_for(codes, [[]]) == codes
    assert sole_holder_for(codes, [["DD"]]) == ["BLOODLINE", "WOOD"]
