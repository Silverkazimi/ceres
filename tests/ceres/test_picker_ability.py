"""Ability choices filter the entire worker pool before pagination."""

from copy import deepcopy
from datetime import datetime, timezone

from ceres.candidates import rank_candidates


NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


def test_ability_filter_reaches_matches_beyond_first_unfiltered_page(report, details_by_id):
    report = deepcopy(report)
    details = deepcopy(details_by_id)
    details[999] = deepcopy(details[2])
    details[999].setdefault("stats", {})["land_abilities"] = [[['GRAIN', 0.1]]]
    for index in range(30):
        report["cards"].append({
            "asset_ref": f"no-ability-{index}", "classification": {"bucket": "unstaked_apparent"},
            "facts": {"card_detail_id": 2, "land_base_pp": 3000 - index, "level": 1, "edition": 0},
        })
    for index in range(2):
        report["cards"].append({
            "asset_ref": f"grain-{index}", "classification": {"bucket": "unstaked_apparent"},
            "facts": {"card_detail_id": 999, "land_base_pp": 10 - index, "level": 1, "edition": 0},
        })
    deed = report["plots"][0]["deed_uid"]
    kw = dict(player="player0005", now=NOW, limit=1, details_by_id=details)
    first = rank_candidates(report, deed, "worker", ability_filter=frozenset({"GRAIN"}), **kw)
    second = rank_candidates(report, deed, "worker", ability_filter=frozenset({"GRAIN"}), offset=1, **kw)
    assert first["total"] == second["total"] == 2
    assert {first["candidates"][0]["asset_ref"], second["candidates"][0]["asset_ref"]} == {"grain-0", "grain-1"}
    assert rank_candidates(report, deed, "worker", ability_filter=frozenset({"__ANY__"}), **kw)["total"] == 2
    assert rank_candidates(report, deed, "worker", ability_filter=frozenset({"WOOD"}), **kw)["total"] == 0
