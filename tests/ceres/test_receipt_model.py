"""Fixture-backed checks of the pure receipt model; no network."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from ceres import receipt_model

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _header(lookup, index=0):
    info = lookup["trx_info"]
    return {"tx_id": info["id"].split("-")[0], "operation_index": index,
            "player": info["player"], "custom_json_id": "sm_" + info["type"],
            "payload": json.loads(info["data"]), "block": info["block_num"],
            "timestamp": info["created_date"]}


def test_regional_harvest_net_tax_and_field_provenance():
    lookup = _fixture("lookup-harvest_all.json")
    row = receipt_model.receipt(_header(lookup), lookup, {"fragment_history": {"status": "success", "data": []}})
    assert row["id"] == lookup["trx_info"]["id"] + ":0"
    assert row["link"] == "https://hivehub.dev/tx/" + row["tx_id"]
    assert row["state"] == "confirmed"
    assert len(row["plots"]) == 8
    assert row["net"] == {"GRAIN": -6914.259, "WOOD": 39985.523, "STONE": 14863.611,
                          "IRON": 5870.434, "SPS": 185.836}
    assert round(sum(row["tax"].values()), 3) == 23180.715
    assert row["provenance"]["received"]["GRAIN"].endswith("received_amount")
    assert row["provenance"]["tax"]["GRAIN"].endswith("tax_amount")
    assert row["provenance"]["net"]["GRAIN"] == "derived: received minus same-resource costs"
    assert row["finds"] == []


def test_plot_harvest_and_tax_collection_shapes_and_deduplicated_hits():
    plot = _fixture("lookup-harvest_resources.json")
    plot_row = receipt_model.receipt(_header(plot), plot, {"fragment_history": {"data": []}})
    assert plot_row["received"]["SPS"] == 56.619
    assert plot_row["costs"] == {"GRAIN": 5690.925, "WOOD": 2845.463,
                                 "STONE": 1138.185, "IRON": 284.546}

    tax = _fixture("lookup-tax_collection-op2.json")
    fragment = _fixture("fragment-history-tax_collection-op2.json")
    tax_row = receipt_model.receipt(_header(tax, 2), tax, {"fragment_history": fragment})
    assert tax_row["received"]["GRAIN"] == 3721.62
    assert tax_row["costs"] == {"GRAIN": 1000.0}
    assert tax_row["net"]["GRAIN"] == 2721.62
    assert len(tax_row["finds"]) == 1
    echoed = copy.deepcopy(fragment)
    echoed["data"] = [copy.deepcopy(fragment["data"][0]) for _ in range(5)]
    assert len(receipt_model.finds(echoed, tax["trx_info"]["id"])) == 1
    echoed["data"][0]["fragment_found"] = False
    assert len(receipt_model.finds(echoed, tax["trx_info"]["id"])) == 1


def test_mismatched_game_record_cannot_supply_amounts():
    lookup = _fixture("lookup-harvest_all.json")
    header = _header(lookup)
    header["player"] = "other-player"
    row = receipt_model.receipt(header, lookup)
    assert row["state"] == "unverified"
    assert row["received"] == {}
    assert row["gaps"] == ["game_record_mismatch"]
