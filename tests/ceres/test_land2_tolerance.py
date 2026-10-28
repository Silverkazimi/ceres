""" (Land 2.0 readiness, Phase 0): readers that survive a resource or structure Ceres does not know yet.

- the regional harvest quote skips a row with an unknown token (logged) and keeps every known row;
- the region balance readers keep an unknown resource column under `extra` instead of dropping it;
- an unknown `project_type` (a Land 2.0 building) is parsed without crashing and shown with the generic
  construction text, never as a fake worksite target.
For known tokens and today's rows every result is exactly what it was.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from ceres import harvest as hv
from ceres import routes_wallet
from ceres import worksite_swap as ws

BUILDING = "building_sawmill_construction"


def _quote_row(token, amount, grain=1.0, wood=0.0, stone=0.0, iron=0.0):
    return {"token_symbol": token, "amount_claimable": amount, "grain_required_for_food": grain,
            "wood_required": wood, "stone_required": stone, "iron_required": iron}


# regional harvest quote

def test_known_quote_is_unchanged():
    payload = {"status": "success", "data": [_quote_row("GRAIN", 90.0, grain=2.0), _quote_row("IRON", 9.0, grain=1.0, wood=3.0)]}
    out = hv.parse_region_quote(payload)
    assert out == {"net_by_token": {"GRAIN": 90.0, "IRON": 9.0},
                   "gross_by_token": {"GRAIN": 100.0, "IRON": 10.0},
                   "cost_now": {"GRAIN": 3.0, "WOOD": 3.0}}


def test_unknown_token_row_is_skipped_logged_and_the_rest_kept(caplog):
    payload = {"status": "success", "data": [_quote_row("GRAIN", 90.0, grain=2.0), _quote_row("TIN", 5.0, grain=4.0),
                                             _quote_row("IRON", 9.0, grain=1.0)]}
    with caplog.at_level(logging.WARNING, logger="ceres.harvest"):
        out = hv.parse_region_quote(payload)
    assert out["net_by_token"] == {"GRAIN": 90.0, "IRON": 9.0}
    assert out["cost_now"] == {"GRAIN": 3.0}
    assert out["skipped_tokens"] == ["TIN"]
    assert any("TIN" in record.getMessage() for record in caplog.records)


def test_unknown_row_is_skipped_even_when_its_own_fields_are_odd():
    payload = {"status": "success", "data": [{"token_symbol": "rhodium", "amount_claimable": None},
                                             _quote_row("WOOD", 9.0, grain=1.0)]}
    out = hv.parse_region_quote(payload)
    assert out["net_by_token"] == {"WOOD": 9.0} and out["skipped_tokens"] == ["RHODIUM"]


@pytest.mark.parametrize("row", [
    _quote_row("GRAIN", None),                        # known token, missing amount
    _quote_row("IRON", 5.0, wood=-1.0),               # known token, negative cost
    _quote_row("", 5.0),                              # no token at all
    "not an object",
])
def test_malformed_known_rows_still_fail_as_before(row):
    with pytest.raises(ValueError):
        hv.parse_region_quote({"status": "success", "data": [row]})


# region balances

LIVE_ROW = {"player": "player_owner", "name": "065 - Mandar Camden", "uid": "PR-CEF-65", "plots_owned": 101,
            "grain": 18491811.724, "wood": 1340361.112, "stone": 918278.675, "iron": 82866.014,
            "research": 935203.057, "aura": 585856.085}


def test_todays_balance_row_is_unchanged_and_has_no_extra():
    out = hv.parse_region_balances({"status": "success", "data": [dict(LIVE_ROW)]})
    assert out == {"PR-CEF-65": {"balances": {"GRAIN": 18491811.724, "WOOD": 1340361.112, "STONE": 918278.675,
                                              "IRON": 82866.014, "RESEARCH": 935203.057, "AURA": 585856.085},
                                 "name": "Mandar Camden", "plots_owned": 101}}
    regions = routes_wallet.regions_section({"status": "success", "data": [dict(LIVE_ROW)]})
    assert "extra" not in regions[0] and list(regions[0]) == ["region_uid", "name", "plots_owned", "balances"]


def test_unknown_balance_column_is_kept_under_extra(caplog):
    row = dict(LIVE_ROW, tin=12.5, copper="3", vanadium=None, note="text", flag=True)
    with caplog.at_level(logging.WARNING, logger="ceres.harvest"):
        out = hv.parse_region_balances({"status": "success", "data": [row]})
    entry = out["PR-CEF-65"]
    assert entry["extra"] == {"TIN": 12.5, "COPPER": 3.0}
    assert set(entry["balances"]) == {"GRAIN", "WOOD", "STONE", "IRON", "RESEARCH", "AURA"}
    assert any("TIN" in record.getMessage() for record in caplog.records)
    wallet = routes_wallet.regions_section({"status": "success", "data": [row]})
    assert wallet[0]["extra"] == {"TIN": 12.5, "COPPER": 3.0}


def test_the_four_natural_columns_stay_required():
    row = {k: v for k, v in LIVE_ROW.items() if k != "iron"}
    with pytest.raises(ValueError):
        hv.parse_region_balances({"status": "success", "data": [dict(row, tin=1)]})


# unknown project types

@pytest.mark.parametrize("project_type, target", [
    ("worksite_stone_construction", "stone"),
    ("worksite_aura_construction", "aura"),
    (BUILDING, None),
    ("foundry_construction", None),
    ("worksite_construction", None),
    ("castle_operation", None),
    ("", None),
    (None, None),
])
def test_construction_target(project_type, target):
    assert ws.construction_target(project_type) == target


def test_construction_facts_of_a_building_has_no_fake_target():
    facts = hv.construction_facts({"project_type": BUILDING, "project_id": 7, "pp_required": 10, "pp_spent": 2}, {})
    assert facts["to"] is None and facts["to_label"] is None and facts["project_id"] == 7
    known = hv.construction_facts({"project_type": "worksite_iron_construction"}, {})
    assert known["to"] == "iron" and known["to_label"] == "Ore Mine"


def test_pending_change_and_completion_of_a_building_are_generic():
    change = ws.build_pending_change("D-1", {"project_type": BUILDING, "projected_end": "2099-01-01T00:00:00Z"}, 10.0, "WOOD")
    assert change["to"] is None and change["to_label"] is None and change["from_label"] == "Logging Camp"
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    done = ws.ready_worksite_change({"project_type": BUILDING, "projected_end": past, "grain_required": 5}, 10.0)
    assert done["to"] is None and done["to_label"] is None
    known = ws.ready_worksite_change({"project_type": "worksite_wood_construction", "projected_end": past, "grain_required": 5}, 10.0)
    assert known["to"] == "wood" and known["to_label"] == "Logging Camp"


def test_worksite_token_of_a_building_falls_back_to_the_capture():
    assert hv._worksite_token({"project_type": BUILDING}, {"resource": "wood"}) == "WOOD"
    assert hv._worksite_token({"project_type": BUILDING}, {}) is None


def test_a_building_project_flows_through_the_board_without_error():
    from ceres.board import project_board
    from tests.ceres.conftest import build_report

    report = build_report()
    row = next(r for r in report["plots"] if r.get("worksite_facts"))
    row["worksite_facts"] = dict(row["worksite_facts"], project_type=BUILDING, worksite_type="Sawmill")
    board = project_board(report, {})
    out = next(r for r in board["plots"] if r["deed_uid"] == row["deed_uid"])
    for key in ("pending_change", "worksite_completion"):
        value = out.get(key)
        assert value is None or (value.get("to") is None and value.get("to_label") is None), key
