import json
import re

import pytest

from ceres import wallet_lp_operations as lp
from ceres.harvest_routing import RoutingError


REGION = "PR-CEF-65"


def test_add_sell_and_resource_swap_have_exact_posting_envelopes():
    built = lp.add("player0005", REGION, "GRAIN", "1.25", "2.5", nonce="0123456789abcdef")
    op_id, envelope = built["operations"][0]
    payload = json.loads(envelope["json"])
    assert op_id == "custom_json" and envelope["id"] == "sm_land_operation"
    assert envelope["required_auths"] == [] and envelope["required_posting_auths"] == ["player0005"]
    assert set(payload) == {"op", "region_uid", "resource_amount", "dec_amount", "shares_out", "resource_symbol", "app", "n"}
    assert payload["op"] == "add_liquidity" and payload["shares_out"] == 0
    assert payload["app"].startswith("ceres/") and payload["n"] == "0123456789abcdef"
    assert lp.validate_operation(built["operations"][0], "player0005") == payload

    sold = lp.sell("player0005", REGION, "WOOD", 3, 2, "2.5")
    assert set(sold["payload"]) == {"op", "region_uid", "resource_amount", "shares_out", "max_slippage", "resource_symbol", "app", "n"}
    swapped = lp.resource_swap("player0005", REGION, "PR-PNW-1", "GRAIN", "WOOD", 3, 1, 2, "2.5")
    assert set(swapped["payload"]) == {"op", "from_region_uid", "to_region_uid", "in_amount", "out_amount_1",
                                       "out_amount_2", "from_symbol", "to_symbol", "max_slippage", "app", "n"}


def test_remove_is_open_since_g1_and_buy_since_g2():
    # G1 proven -- the game's own remove_liquidity shape (cryptomancer tx <tx id>: shares_out 0.2, both amounts 0)
    removed = lp.remove("player0005", REGION, "GRAIN", "0.125")
    assert removed["payload"]["shares_out"] == 0.125
    assert {k: removed["payload"][k] for k in ("op", "region_uid", "resource_amount", "dec_amount", "resource_symbol")} == \
        {"op": "remove_liquidity", "region_uid": REGION, "resource_amount": 0, "dec_amount": 0, "resource_symbol": "GRAIN"}
    lp.validate_payload(removed["payload"])
    bought = lp.buy("player0005", REGION, "GRAIN", 2, 1, "2.5")
    assert bought["payload"]["dec_amount"] == 2
    lp.validate_payload(bought["payload"])  # verify accepts a buy without any gate flag


def test_buy_payload_matches_the_owners_game_transaction_field_for_field():
    # a synthetic payload in the shape of the game's own swap transaction (Posting, sm_land_operation)
    import json
    game = {"op": "swap_tokens", "region_uid": "PR-BRL-41", "dec_amount": 1, "shares_out": 104.201, "max_slippage": 2.5,
            "resource_symbol": "GRAIN", "app": "splinterlands/0.7.60", "n": "KlMnOpQrSt"}
    built = lp.buy("player0005", "PR-BRL-41", "GRAIN", 1, "104.201", "2.5")
    ours = json.loads(built["operations"][0][1]["json"])
    assert set(ours) == set(game)
    for key in ("op", "region_uid", "dec_amount", "shares_out", "max_slippage", "resource_symbol"):
        assert ours[key] == game[key], key
    envelope = built["operations"][0][1]
    assert envelope["id"] == "sm_land_operation" and envelope["required_auths"] == [] and envelope["required_posting_auths"] == ["player0005"]


@pytest.mark.parametrize("value", [0, "0", "1.001", "1.0001", 1.01, True])
def test_remove_fraction_is_strictly_bounded_and_floor_three(value):
    with pytest.raises(RoutingError):
        lp.remove("player0005", REGION, "GRAIN", value)


def test_wallet_tokens_add_and_remove_with_no_region_and_land_resources_need_one():
    # Voucher and SPS live in the account wallet (region_uid "", as the game's Voucher hub sends it)
    for symbol in ("VOUCHER", "SPS"):
        assert lp.add("player0005", "", symbol, 2, 1)["payload"]["region_uid"] == ""
        assert lp.remove("player0005", "", symbol, "0.5")["payload"]["region_uid"] == ""
        with pytest.raises(RoutingError):
            lp.add("player0005", REGION, symbol, 2, 1)
    with pytest.raises(RoutingError):
        lp.remove("player0005", "", "GRAIN", "0.5")
    with pytest.raises(RoutingError):
        lp.add("player0005", REGION, "RESEARCH", 1, 1)


@pytest.mark.parametrize("kwargs", [
    {"symbol": "SPS"}, {"region_uid": "PR-CEF-0"}, {"resource_amount": "1.0001"},
])
def test_builder_rejects_unsupported_values(kwargs):
    values = {"symbol": "GRAIN", "region_uid": REGION, "resource_amount": 1, "dec_amount": 1}
    values.update(kwargs)
    with pytest.raises((RoutingError, ValueError)):
        lp.add("player0005", values["region_uid"], values["symbol"], values["resource_amount"], values["dec_amount"])


def test_exact_allowlist_rejects_extra_keys_bad_slippage_and_bad_nonce():
    payload = lp.sell("player0005", REGION, "GRAIN", 1, 1, 2.5)["payload"]
    with pytest.raises(RoutingError, match="keys"):
        lp.validate_payload({**payload, "extra": True})
    with pytest.raises(RoutingError):
        lp.sell("player0005", REGION, "GRAIN", 1, 1, 30)
    with pytest.raises(RoutingError, match="nonce"):
        lp.add("player0005", REGION, "GRAIN", 1, 1, nonce="short")
    assert re.fullmatch(r"[a-f0-9]{16}", payload["n"])
