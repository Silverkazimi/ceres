import json
import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from ceres import routes_wallet as wallet
from ceres import routes_wallet_lp as lp_routes
from ceres import wallet_lp_operations as operations
from ceres.config import CeresConfig
from ceres.app import create_app
from ceres.harvest_routing import Pool


def test_capability_matrix_is_default_off_and_every_kind_opens_when_on():
    disabled = lp_routes.capabilities(False)
    assert all(not value["enabled"] for value in disabled.values())
    assert all("CERES_WALLET_LP_SIGNING" in value["reason"] for value in disabled.values())
    enabled = lp_routes.capabilities(True)
    assert all(enabled[kind]["enabled"] for kind in ("add", "swap_sell", "swap_resource"))
    assert enabled["remove"] == {"enabled": True, "reason": "Available."}  # G1 proven 2026-10-04 ()
    assert enabled["swap_buy"] == {"enabled": True, "reason": "Available."}  # G2 proven 2026-09-28


def test_lookup_matches_exact_player_operation_and_nonce_and_returns_game_result():
    expected = operations.sell("player0005", "PR-CEF-65", "GRAIN", 2, 1, 2.5)["payload"]
    tx = "a" * 40
    info = {"id": tx, "player": "player0005", "type": "land_operation", "data": json.dumps(expected),
            "success": True, "result": json.dumps({"data": {"amount": 1}})}
    body = {"tx_id": tx, "nonce": expected["n"], "kind": "swap_sell", "resource_symbol": "GRAIN",
            "region_uid": "PR-CEF-65", "expected_payload": expected}
    assert lp_routes._verify(info, "player0005", tx, body) == {"status": "confirmed", "game_result": {"amount": 1}}
    assert lp_routes._verify(None, "player0005", tx, body) == {"status": "not_found"}  # parity
    for wrong in ({**info, "player": "other"}, {**info, "type": "claim_rewards"},
                  {**info, "data": json.dumps({**expected, "n": "f" * 16})}):
        with pytest.raises(HTTPException):
            lp_routes._verify(wrong, "player0005", tx, body)


def test_lookup_refusal_is_a_game_failure_not_confirmation():
    expected = operations.add("player0005", "PR-CEF-65", "GRAIN", 1, 1)["payload"]
    info = {"id": "b" * 40, "player": "player0005", "type": "land_operation", "data": json.dumps(expected),
            "success": False, "error": "not enough balance"}
    body = {"nonce": expected["n"], "kind": "add", "expected_payload": expected}
    assert lp_routes._verify(info, "player0005", "b" * 40, body) == {"status": "failed", "message": "not enough balance"}


def test_preview_and_sign_refresh_each_make_three_fresh_reads(monkeypatch, tmp_path):
    calls = {"pools": 0, "balances": 0, "regions": 0}
    def pools():
        calls["pools"] += 1
        return {"GRAIN": Pool(1, "GRAIN", Decimal("100"), Decimal("100"), Decimal("100"))}
    def balances(player):
        calls["balances"] += 1
        assert player == "player0005"
        return [{"player": player, "token": "DEC", "balance": 100}]
    class Land:
        def __init__(self, **_):
            pass
        def regional_balances(self, player):
            calls["regions"] += 1
            assert player == "player0005"
            return type("Response", (), {"data": {"status": "success", "data": [
                {"uid": "PR-CEF-65", "grain": 10, "wood": 0, "stone": 0, "iron": 0, "research": 0, "aura": 0}
            ]}})()
    monkeypatch.setattr(lp_routes, "_pools", pools)
    monkeypatch.setattr(lp_routes, "_balances", balances)
    monkeypatch.setattr(lp_routes, "LandClient", Land)
    app = create_app(CeresConfig(root=tmp_path, player="player0005", wallet_lp_signing_enabled=True,
                                 card_art_cache_enabled=False))
    with TestClient(app) as client:
        first = client.post("/api/wallet/lp/preview", json={"kind": "add", "region_uid": "PR-CEF-65",
                            "resource_symbol": "GRAIN", "resource_amount": "1"}).json()
        assert first["authority"] == "Posting" and first["enabled"] is True
        assert calls == {"pools": 1, "balances": 1, "regions": 1}
        fresh = client.post("/api/wallet/lp/preview", json={"kind": "add", "region_uid": "PR-CEF-65",
                            "resource_symbol": "GRAIN", "resource_amount": "1", "sign": True,
                            "nonce": first["nonce"], "reviewed_payload": first["payload"]})
        assert fresh.status_code == 200
        assert calls == {"pools": 2, "balances": 2, "regions": 2}


def test_preview_refuses_cross_origin(monkeypatch, tmp_path):
    app = create_app(CeresConfig(root=tmp_path, player="player0005", wallet_lp_signing_enabled=True,
                                 card_art_cache_enabled=False))
    with TestClient(app) as client:
        cross_origin = client.post("/api/wallet/lp/preview", headers={"Origin": "https://outside.example"},
                                   json={"kind": "add"})
        assert cross_origin.status_code == 403


def test_reviewed_drift_allows_only_protected_directions():
    reviewed_add = operations.add("player0005", "PR-CEF-65", "GRAIN", 1, 2, nonce="a" * 16)["payload"]
    fresh_add = {**reviewed_add, "dec_amount": 2.01}
    lp_routes._check_drift(reviewed_add, fresh_add)
    with pytest.raises(HTTPException):
        lp_routes._check_drift(reviewed_add, {**reviewed_add, "dec_amount": 2.1})
    reviewed_sell = operations.sell("player0005", "PR-CEF-65", "GRAIN", 1, 2, 2.5, nonce="b" * 16)["payload"]
    with pytest.raises(HTTPException):
        lp_routes._check_drift(reviewed_sell, {**reviewed_sell, "shares_out": 1.94})


# the swap card's live quote and the Review projections.
def _swap_fakes(monkeypatch, calls):
    def pools():
        calls["pools"] += 1
        return {"GRAIN": Pool(1, "GRAIN", Decimal("1000000"), Decimal("100000"), Decimal("100"))}
    def balances(player):
        calls["balances"] += 1
        return [{"player": player, "token": "DEC", "balance": 100}]
    class Land:
        def __init__(self, **_):
            pass
        def regional_balances(self, player):
            calls["regions"] += 1
            return type("Response", (), {"data": {"status": "success", "data": [
                {"uid": "PR-CEF-65", "grain": 5000, "wood": 0, "stone": 0, "iron": 0, "research": 0, "aura": 0}]}})()
    monkeypatch.setattr(lp_routes, "_pools", pools)
    monkeypatch.setattr(lp_routes, "_balances", balances)
    monkeypatch.setattr(lp_routes, "LandClient", Land)


def test_quote_makes_exactly_one_pools_read_and_builds_no_operation(monkeypatch, tmp_path):
    calls = {"pools": 0, "balances": 0, "regions": 0}
    _swap_fakes(monkeypatch, calls)
    app = create_app(CeresConfig(root=tmp_path, player="player0005", card_art_cache_enabled=False))
    with TestClient(app) as client:
        quote = client.post("/api/wallet/lp/quote", json={"kind": "swap_sell", "resource_symbol": "GRAIN", "amount": "1000"})
        assert quote.status_code == 200
        body = quote.json()
        assert calls == {"pools": 1, "balances": 0, "regions": 0}
        assert "operations" not in body and "payload" not in body
        assert (body["pay_symbol"], body["receive_symbol"], body["input"], body["slippage"]) == ("GRAIN", "DEC", "1000", "2.5")
        assert body["pool_cost"] == "100.000"  # the single-leg 10 % pool fee on the input, in the paid token
        assert Decimal(body["minimum"]) == (Decimal(body["output"]) * Decimal("0.975")).quantize(Decimal("0.001"), rounding="ROUND_DOWN")
        assert body["rates"] == {"unit": "0.090", "inverse": "11.111"}  # "Sell 1 GRAIN for 0.090 DEC" / "Buy 1 DEC for 11.111 GRAIN"
        # BUY (G2 proven): DEC in, the resource out, the same single-leg maths; SPS is out of scope; cross-origin is refused.
        bought = client.post("/api/wallet/lp/quote", json={"kind": "swap_buy", "resource_symbol": "GRAIN", "amount": "1"})
        assert bought.status_code == 200
        buy = bought.json()
        assert (buy["pay_symbol"], buy["receive_symbol"], buy["input"], buy["pool_cost"]) == ("DEC", "GRAIN", "1", "0.100")
        assert buy["output"] == "9.000"  # 1 DEC x 0.9 x 1,000,000 / (100,000 + 0.9) = 8.99992, rounded like the game
        assert buy["rates"]["unit"] == "9.000"
        assert client.post("/api/wallet/lp/quote", json={"kind": "swap_sell", "resource_symbol": "SPS", "amount": "1"}).status_code == 422
        assert client.post("/api/wallet/lp/quote", headers={"Origin": "https://outside.example"},
                           json={"kind": "swap_sell", "resource_symbol": "GRAIN", "amount": "1"}).status_code == 403


def test_sell_preview_projects_after_balances_from_the_same_fresh_reads(monkeypatch, tmp_path):
    calls = {"pools": 0, "balances": 0, "regions": 0}
    _swap_fakes(monkeypatch, calls)
    app = create_app(CeresConfig(root=tmp_path, player="player0005", card_art_cache_enabled=False, wallet_lp_signing_enabled=False))
    with TestClient(app) as client:
        review = client.post("/api/wallet/lp/preview", json={"kind": "swap_sell", "region_uid": "PR-CEF-65",
                             "resource_symbol": "GRAIN", "resource_amount": "1000"})
        assert review.status_code == 200
        data = review.json()
        assert calls == {"pools": 1, "balances": 1, "regions": 1}
        assert data["signing_enabled"] is False and data["authority"] == "Posting"
        assert data["projected"] == {"dec_after": format(Decimal(100) + Decimal(data["quote"]["output"]), "f"),
                                     "region_after": "4000.000"}
        assert data["payload"]["shares_out"] == float(data["quote"]["output"]) and data["payload"]["max_slippage"] == 2.5
        # Signing is refused while CERES_WALLET_LP_SIGNING is off.
        signed = client.post("/api/wallet/lp/preview", json={"kind": "swap_sell", "region_uid": "PR-CEF-65", "resource_symbol": "GRAIN",
                             "resource_amount": "1000", "sign": True, "nonce": data["nonce"], "reviewed_payload": data["payload"]})
        assert signed.status_code == 409


def test_swap_client_never_stores_and_never_prints_server_text():
    from pathlib import Path
    js = (Path(__file__).parents[2] / "ceres" / "static" / "wallet-lp.js").read_text()
    # (no stored board data) still holds: the only thing this module keeps in the browser is the open-swap intent that stops a second
    # signature after a reload ( S10, local to this browser, no board data, no keys); no session storage, no other key.
    assert "sessionStorage" not in js
    assert set(re.findall(r"localStorage\.\w+\(([^,)]*)", js)) <= {"INTENT_KEY(player", "INTENT_KEY(ctx.player", "WATCH_KEY(player", "WATCH_KEY(ctx.player"}  # the watch list holds the same intent record
    # the one exception -- a refused player-to-player transfer shows the game's own
    # refusal reason, escaped, on its red card. Exactly this line; any other server text still fails the guard.
    transfer_refusal = "Object.assign(st, { stage: 'refused', refusal: String(result.message || '') });"
    assert js.count(transfer_refusal) == 1
    js = js.replace(transfer_refusal, "")
    assert ".detail" not in js and ".message" not in js  # no server text reaches the screen
    assert js.count("requestBroadcastThroughGate(") == 2  # one signature per Sign, plus the Active step 2 of an SPS sale ()
    assert "QUOTE_IDLE_MS = 700" in js


def test_buy_preview_builds_the_owners_proven_shape_and_projects_both_balances(monkeypatch, tmp_path):
    calls = {"pools": 0, "balances": 0, "regions": 0}
    _swap_fakes(monkeypatch, calls)
    app = create_app(CeresConfig(root=tmp_path, player="player0005", card_art_cache_enabled=False))
    with TestClient(app) as client:
        review = client.post("/api/wallet/lp/preview", json={"kind": "swap_buy", "region_uid": "PR-CEF-65",
                             "resource_symbol": "GRAIN", "dec_amount": "1"})
        assert review.status_code == 200
        data = review.json()
        assert calls == {"pools": 1, "balances": 1, "regions": 1}
        payload = data["payload"]
        # Field for field with a game transaction <tx id> (only app identity and nonce are ours).
        assert set(payload) == {"op", "region_uid", "dec_amount", "shares_out", "max_slippage", "resource_symbol", "app", "n"}
        assert (payload["op"], payload["region_uid"], payload["dec_amount"], payload["max_slippage"], payload["resource_symbol"]) == \
            ("swap_tokens", "PR-CEF-65", 1.0, 2.5, "GRAIN")
        assert payload["shares_out"] == float(data["quote"]["output"]) == 9.0
        assert data["operations"][0][1]["required_posting_auths"] == ["player0005"] and data["operations"][0][1]["required_auths"] == []
        assert data["projected"] == {"dec_after": "99.000", "region_after": "5009.000"}
        # More DEC than the wallet holds is refused before anything is built.
        too_much = client.post("/api/wallet/lp/preview", json={"kind": "swap_buy", "region_uid": "PR-CEF-65",
                               "resource_symbol": "GRAIN", "dec_amount": "101"})
        assert too_much.status_code == 422


def test_wallet_lp_signing_is_on_by_default_and_the_env_can_switch_it_off(monkeypatch, tmp_path):
    # the live service had no Swap button because the switch defaulted to off. Ceres only hands the
    # transaction to the maintainer's wallet; it never signs itself.
    monkeypatch.delenv("CERES_WALLET_LP_SIGNING", raising=False)
    assert CeresConfig(root=tmp_path, player="player0005").wallet_lp_signing_enabled is True
    monkeypatch.setenv("CERES_WALLET_LP_SIGNING", "0")
    assert CeresConfig(root=tmp_path, player="player0005").wallet_lp_signing_enabled is False


def test_one_click_swap_builds_with_the_shown_figure_and_refuses_a_worse_one(monkeypatch, tmp_path):
    # one Swap button, no Review. The fresh build is signable when it still gives at least the shown
    # "You receive" minus the fixed 2.5 %; otherwise 409 and nothing is built.
    calls = {"pools": 0, "balances": 0, "regions": 0}
    _swap_fakes(monkeypatch, calls)
    app = create_app(CeresConfig(root=tmp_path, player="player0005", card_art_cache_enabled=False, wallet_lp_signing_enabled=True))
    body = {"kind": "swap_sell", "region_uid": "PR-CEF-65", "resource_symbol": "GRAIN", "resource_amount": "1000", "sign": True}
    with TestClient(app) as client:
        shown = client.post("/api/wallet/lp/quote", json={"kind": "swap_sell", "resource_symbol": "GRAIN", "amount": "1000"}).json()["output"]
        ok = client.post("/api/wallet/lp/preview", json={**body, "seen_output": shown})
        assert ok.status_code == 200 and ok.json()["operations"][0][1]["required_posting_auths"] == ["player0005"]
        assert calls == {"pools": 2, "balances": 1, "regions": 1}  # one quote read, then one fresh 3-read build
        worse = client.post("/api/wallet/lp/preview", json={**body, "seen_output": str(Decimal(shown) * 2)})
        assert worse.status_code == 409
        assert client.post("/api/wallet/lp/preview", json=body).status_code == 422  # no shown figure, no build


# (maintainer: "make all LPs in the wallet accept an input in the receiving field and calculate the required
# input backwards"): "You receive" typed -> the smallest "You pay" whose quoted output reaches it, capped at the balance.
def test_quote_works_backwards_from_the_amount_to_receive(monkeypatch, tmp_path):
    calls = {"pools": 0, "balances": 0, "regions": 0}
    _swap_fakes(monkeypatch, calls)
    app = create_app(CeresConfig(root=tmp_path, player="player0005", card_art_cache_enabled=False))
    with TestClient(app) as client:
        body = client.post("/api/wallet/lp/quote", json={"kind": "swap_buy", "resource_symbol": "GRAIN",
                                                          "target_output": "9", "max_input": "100"}).json()
        assert calls["pools"] == 1  # still one pools read
        assert body["target_output"] == "9" and body["capped"] is False
        assert Decimal(body["output"]) >= Decimal("9")
        one_less = client.post("/api/wallet/lp/quote", json={"kind": "swap_buy", "resource_symbol": "GRAIN",
                                                              "amount": str(Decimal(body["input"]) - Decimal("0.001"))}).json()
        assert Decimal(one_less["output"]) < Decimal("9")  # the smallest input that reaches the target
        sell = client.post("/api/wallet/lp/quote", json={"kind": "swap_sell", "resource_symbol": "GRAIN",
                                                          "target_output": "50", "max_input": "5000"}).json()
        assert sell["pay_symbol"] == "GRAIN" and Decimal(sell["output"]) >= Decimal("50") and sell["capped"] is False
        capped = client.post("/api/wallet/lp/quote", json={"kind": "swap_buy", "resource_symbol": "GRAIN",
                                                            "target_output": "5000", "max_input": "2"}).json()
        assert capped["capped"] is True and Decimal(capped["input"]) == 2  # never more than the balance the card shows
