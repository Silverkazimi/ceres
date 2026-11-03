""": DEC-triggered harvest rows follow the normal receipt amount rules."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from ceres.routes_dec import _harvest_receipt

UID = "PR-TST-1"
EVIDENCE = Path(__file__).resolve().parents[2] / "work/notes/CERES-S11770-DEC-HARVEST-EVIDENCE-2026-09-17.json"


def embedded(*rows):
    return {"data": {"pre_op_efficiency": 75, "post_op_efficiency": 80,
                     "harvestResults": {"success": True, "data": {"success": True, "results": list(rows)}}}}


def row(symbol="GRAIN", **changes):
    result = {"deed_uid": "test-deed", "result_message": "Successful", "resource_symbol": symbol,
              "resource_amount": 15, "tax_amount": 2, "received_amount": 13,
              "grain_eaten": 4, "grain_rewards_eaten": 0, "dec_spent": 0,
              "inputs_consumed": [], "fragment_roll": {"fragment_found": True, "fragment_type": "test"}}
    result.update(changes)
    return result


def member(*rows):
    result = _harvest_receipt(embedded(*rows), UID)
    assert result["state"] == "recorded"
    return result["members"]


def test_grain_rewards_already_deducted_from_grain_received():
    item = member(row(received_amount=10, grain_rewards_eaten=3))[0]
    assert item["outcome"] == "success"
    assert Decimal(item["received_by_token"]["GRAIN"]) == 10
    assert Decimal(item["costs_by_token"]["GRAIN"]) == 4
    assert item["finds"] == [{"kind": "fragment", "type": "test"}]
    assert item["region_uid"] == UID
    assert item["deed_uids"] == ["test-deed"]


def test_non_grain_rewards_are_grain_cost():
    item = member(row("WOOD", grain_rewards_eaten=3))[0]
    assert item["outcome"] == "success"
    assert Decimal(item["received_by_token"]["WOOD"]) == 13
    assert Decimal(item["costs_by_token"]["GRAIN"]) == 7


def test_lowercase_resource_groups_under_uppercase_symbol():
    items = member(row("wood"), row("WOOD"))
    assert all({k: Decimal(v) for k, v in item["received_by_token"].items()} ==
               {"WOOD": Decimal(13)} for item in items)
    assert all(item["taxes_by_token"].keys() == {"WOOD"} for item in items)


@pytest.mark.parametrize("received", [None, "not-a-number", float("nan")])
def test_invalid_or_missing_received_amount_is_pending(received):
    invalid = row("WOOD")
    if received is not None:
        invalid["received_amount"] = received
    else:
        del invalid["received_amount"]
    good, bad = member(row("GRAIN"), invalid)
    assert good["outcome"] == "success"
    assert bad["outcome"] == "pending"
    assert bad["received_by_token"] == {}
    assert bad["costs_by_token"] == {}
    assert bad["taxes_by_token"] == {}
    assert bad["finds"] == [{"kind": "fragment", "type": "test"}]


