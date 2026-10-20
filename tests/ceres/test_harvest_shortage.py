"""same-harvest grain must cover its own regional harvest cost."""

from pytest import approx

from ceres.harvest import affordability
from ceres import routes_harvest
from ceres.routes_harvest import MARGIN_HOURS


def test_region_preview_does_not_block_the_exact_owner_numbers(monkeypatch):
    region_uid = "PR-BRL-009"
    quote_payload = {
        "status": "success",
        "data": [{
            "token_symbol": "GRAIN",
            "amount_claimable": 501.85,
            "grain_required_for_food": 253.46,
            "wood_required": 0.0,
            "stone_required": 0.0,
            "iron_required": 0.0,
        }],
    }
    monkeypatch.setattr(
        routes_harvest,
        "_summaries",
        lambda report, ref, player: [{
            "region_uid": region_uid,
            "region_number": 9,
            "plots": 13,
            "harvesting": 13,
            "cost_per_hour": {"GRAIN": 13.2},
            "tax_collection": [],
            "unknown": 0,
        }],
    )
    monkeypatch.setattr(
        routes_harvest,
        "_balances",
        lambda player, fresh=False: ({region_uid: {
            "balances": {"GRAIN": 0.01, "WOOD": 0.0, "STONE": 0.0, "IRON": 0.0},
            "name": "Quegmoor",
        }}, None),
    )
    monkeypatch.setattr(routes_harvest, "_cached", lambda key, fn, *args, fresh=False: quote_payload)

    rows, issues = routes_harvest._region_rows(
        "alice", {"kind": "regions", "region_uids": [region_uid]}, {"plots": []}, object(), True
    )

    assert issues == []
    assert rows[0]["status"] == "included"
    assert rows[0]["affordability"]["short"] == {}
    assert rows[0]["affordability"]["rows"][0]["available"] == approx(501.86)


def test_region_009_grain_credit_covers_the_exact_owner_numbers():
    result = affordability(
        {"GRAIN": 253.46},
        {"GRAIN": 0.01, "WOOD": 0.0, "STONE": 0.0, "IRON": 0.0},
        {"GRAIN": 13.2},
        MARGIN_HOURS,
        {"GRAIN": 501.85},
    )

    assert result["status"] == "covered"
    assert result["short"] == {}
    assert result["rows"][0]["available"] == approx(501.86)
    assert result["rows"][0]["after"] == approx(247.96)


def test_real_shortfall_still_uses_one_canonical_number():
    result = affordability(
        {"GRAIN": 253.46},
        {"GRAIN": 0.01, "WOOD": 0.0, "STONE": 0.0, "IRON": 0.0},
        {"GRAIN": 13.2},
        MARGIN_HOURS,
        {"GRAIN": 0.0},
    )

    assert result["status"] == "short"
    assert result["short"]["GRAIN"] == approx(253.89)
    assert result["rows"][0]["short"] == approx(result["short"]["GRAIN"])
