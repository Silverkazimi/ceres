"""sell-half is hidden from the route-kind list, but offered (and allowed) when the wallet DEC cannot fund an LP route."""
from pathlib import Path

from fastapi.testclient import TestClient

from ceres import harvest_routing as routing
from ceres.app import create_app
from ceres.config import CeresConfig

STATIC = Path(__file__).parents[2] / "ceres" / "static"
REGION = "PR-AAA-1"
POOL = routing.Pool(1, "GRAIN", routing.amount("1000"), routing.amount("1000"), routing.amount("1000"), False)


def test_wallet_lp_lists_sell_half_for_every_route(monkeypatch):
    monkeypatch.setenv("CERES_ROUTING_LP_WALLET", "1")
    monkeypatch.delenv("CERES_ROUTING_LP_SELL_HALF", raising=False)
    config = CeresConfig()
    assert {"lp_wallet", "lp_sell_half"} <= config.routing_kinds and config.routing_offer_kinds == frozenset()


def test_the_explicit_switch_lists_sell_half_always(monkeypatch):
    monkeypatch.setenv("CERES_ROUTING_LP_WALLET", "1")
    monkeypatch.setenv("CERES_ROUTING_LP_SELL_HALF", "1")
    assert CeresConfig().routing_offer_kinds == frozenset()


def test_without_wallet_lp_nothing_new_is_enabled(monkeypatch):
    monkeypatch.delenv("CERES_ROUTING_LP_WALLET", raising=False)
    monkeypatch.delenv("CERES_ROUTING_LP_SELL_HALF", raising=False)
    assert "lp_sell_half" not in CeresConfig().routing_kinds


def test_kinds_endpoint_lists_sell_half_without_offer_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CERES_ROUTING_LP_WALLET", "1")
    monkeypatch.delenv("CERES_ROUTING_LP_SELL_HALF", raising=False)
    client = TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="fixtureowner")))
    body = client.get("/api/harvest/routing/v2/kinds?player=fixtureowner").json()
    assert "lp_sell_half" in body["enabled_kinds"] and body["offer_only_kinds"] == []


def test_preview_reports_the_wallet_dec_shortfall_the_screen_offers_sell_half_for():
    rule = {"id": "lp", "kind": "liquidity", "source_region": REGION, "symbol": "GRAIN", "quantity_mode": "percent",
            "quantity": "100", "dec_source": "wallet"}
    ledger = routing.Ledger({(REGION, "GRAIN"): routing.amount("500"), (routing.INVENTORY, "DEC"): routing.amount("100")},
                            {}, {(REGION, "GRAIN"): routing.amount("500")})
    step = routing.plan_after_harvest(ledger, [rule], {"GRAIN": POOL}, "2.5")[0]
    assert float(step["dec_funding"]["shortfall"]) > 0 and step["dec_source"] == "wallet"


def test_screen_hides_sell_half_until_needed_and_offers_it_in_one_tap():
    setup = (STATIC / "routing-setup.js").read_text()
    assert "!offerOnlyKinds.includes('lp_sell_half') || selectedKind === 'lp_sell_half' || short" in setup
    assert "data-r3=\"use-sell-half\"" in setup and "name === 'use-sell-half'" in setup
    assert "Number(step?.dec_funding?.shortfall)" in setup and "offer_only_kinds" in setup
    modal = (STATIC / "harvest-modal.js").read_text()
    assert "'lp_sell_half'].includes(kind)" in modal and "offerOnlyKinds: M.v2OfferOnly" in modal
