"""Regression guards for land-ability capability discovery and rendering."""

from __future__ import annotations

from ceres.board import project_board
from ceres.metrics import land_ability_row


_TABLE_BACKED_DETAIL_ID = -1


def _table_backed_detail() -> dict:
    return {
        "id": _TABLE_BACKED_DETAIL_ID,
        "name": "Capability Table Worker",
        "stats": {"land_abilities": [[["ENERGIZED"]]]},
    }


def test_every_land_ability_table_has_a_level_one_row(details_by_id):
    details = {**details_by_id, _TABLE_BACKED_DETAIL_ID: _table_backed_detail()}
    ability_detail_ids = [
        detail_id
        for detail_id, detail in details.items()
        if (detail.get("stats") or {}).get("land_abilities")
    ]

    positive_count = len(ability_detail_ids)

    assert positive_count
    assert all(land_ability_row(detail_id, 1, details) is not None for detail_id in ability_detail_ids)


def test_plot_row_renders_codes_from_a_table_backed_worker():
    details = {_TABLE_BACKED_DETAIL_ID: _table_backed_detail()}
    report = {
        "query": {"player": "test-player"},
        "plots": [{
            "deed_uid": "test-deed",
            "facts": {
                "plot": "test-plot", "rarity": "common", "status": "occupied",
                "terrain": "Plains", "resource": "WOOD", "worksite_type": "Lumber Camp",
                "deed_stats": {},
            },
            "staking_facts": {
                "worker_count": 1, "max_workers_allowed": 5, "is_runi_staked": False,
                "total_harvest_pp": 1, "total_dec_staked": 1,
            },
            "diagnostics": [],
            "asset_fetch": {"status": "retrieved"},
        }],
        "workers": [{
            "asset_ref": "test-worker", "stake_ref_uid": "test-deed", "slot": 1,
            "name": "Capability Table Worker", "card_detail_id": _TABLE_BACKED_DETAIL_ID,
        }],
        "cards": [{"asset_ref": "test-worker", "facts": {"level": 1}}],
        "bonus_assets": [],
        "coverage": {},
    }

    plot_row = project_board(report, details)["plots"][0]
    rendered_codes = plot_row["ability_holders"][0]["codes"]

    assert rendered_codes
