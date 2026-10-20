"""Offline sell-half routing guards; wallet and endpoint responses are synthetic."""

import json
from types import SimpleNamespace

import pytest

from ceres import harvest_routing as routing, routing_plan, routing_runs_v2 as runs
from ceres.config import CeresConfig


PLAYER = "fixtureowner"
REGION = "PR-AAA-1"
POOL = routing.Pool(1, "GRAIN", routing.amount("1000"), routing.amount("1000"),
                    routing.amount("1000"), False)


def _preview(pool=POOL):
    rule = {"id": "half-1", "kind": "liquidity", "source_region": REGION,
            "symbol": "GRAIN", "quantity_mode": "amount", "quantity": "10",
            "dec_source": "swap_half", "slippage": "2.5"}
    ledger = routing.Ledger({(REGION, "GRAIN"): routing.amount("20")}, {},
                            {(REGION, "GRAIN"): routing.amount("20")})
    steps = routing.plan_after_harvest(ledger, [rule], {"GRAIN": pool}, "2.5")
    manifest = routing_plan.compile_manifest(rules=[rule], steps=steps, pools={"GRAIN": pool},
                                             account=PLAYER, regions=1, enabled_kinds={"lp_sell_half"})
    return {"player": PLAYER, "phase": "after", "blocked": False,
            "preview_id": "offline-half-preview", "preview_digest": "offline-half-digest",
            "rules": [rule], "steps": steps, "manifest": manifest,
            "state": {"pools": [{"id": pool.id, "symbol": "GRAIN",
                "resource_quantity": routing.text(pool.resource), "dec_quantity": routing.text(pool.dec),
                "total_shares": routing.text(pool.shares), "external": pool.external}]}}


def _fresh(monkeypatch, balance="100", resource="1000", dec="1000"):
    calls = []

    class Transport:
        def __init__(self, *, timeout, retries):
            assert retries == 0

        def get_json(self, url):
            calls.append(url)
            if "/land/liquidity/pools" in url:
                return SimpleNamespace(data={"status": "success", "data": [{
                    "id": 1, "token_symbol": "GRAIN", "resource_quantity": resource,
                    "dec_quantity": dec, "total_shares": "1000", "is_external_resource": False}]})
            return SimpleNamespace(data=[{"player": PLAYER, "token": "DEC", "balance": balance}])

    monkeypatch.setattr(runs, "FixedGetTransport", Transport)
    return calls


def _confirmed_parent(record):
    sale = next(op for bundle in record["manifest"]["bundles"] for op in bundle["operations"]
                if op["kind"] == "liquidity_funding_swap")
    state = record["operations"][sale["id"]]
    state["state"] = "confirmed"
    received = routing.amount(sale["members"][0]["protected_minimum"]) + routing.amount("1")
    state["members"][sale["members"][0]["nonce"]].update(state="confirmed", received_dec=routing.text(received))
    return sale, received


def test_own_switch_is_off_and_compiler_preserves_dependency(monkeypatch):
    monkeypatch.delenv("CERES_ROUTING_LP_SELL_HALF", raising=False)
    assert "lp_sell_half" not in CeresConfig().routing_kinds
    preview = _preview()
    bundles = preview["manifest"]["bundles"]
    sale, child = (next(op for bundle in bundles for op in bundle["operations"] if op["kind"] == kind)
                   for kind in ("liquidity_funding_swap", "liquidity"))
    assert child["depends_on"] == [sale["id"]]
    assert child["members"][0]["input_pending_receipt"] is True
    assert all(len(bundle["operations"]) <= 4 and len({op["pool"] for op in bundle["operations"]})
               == len(bundle["operations"]) for bundle in bundles)
    with pytest.raises(routing.RoutingError, match="disabled"):
        routing_plan.compile_manifest(rules=preview["rules"], steps=preview["steps"],
                                      pools={"GRAIN": POOL}, account=PLAYER, regions=1,
                                      enabled_kinds={"lp_wallet"})
    monkeypatch.setenv("CERES_ROUTING_LP_SELL_HALF", "1")
    assert "lp_sell_half" in CeresConfig().routing_kinds


def test_child_uses_confirmed_proceeds_and_two_metered_reads(tmp_path, monkeypatch):
    calls = _fresh(monkeypatch)
    with runs.locked(tmp_path, PLAYER) as folder:
        record = runs.create(folder, PLAYER, _preview(), {"lp_sell_half"})
        sale_bundle, child_bundle = record["manifest"]["bundles"]
        with pytest.raises(routing.RoutingError, match="parent game receipt"):
            runs.claim(folder, record, child_bundle["id"], {"lp_sell_half"}, True)
        assert calls == []
        sale_claim = runs.claim(folder, record, sale_bundle["id"], {"lp_sell_half"}, True)
        assert json.loads(sale_claim["operations"][0][1]["json"])["op"] == "swap_tokens"
        sale, received = _confirmed_parent(record)
        claim = runs.claim(folder, record, child_bundle["id"], {"lp_sell_half"}, True)
        assert claim["authority"] == "Posting"
        assert len(claim["changed_amount_review"]) == 1
        review = claim["changed_amount_review"][0]
        assert routing.amount(review["confirmed_sale_dec"]) == received
        assert routing.amount(review["dec_amount"]) <= received
        assert len(calls) == record["call_budget"]["total"] == 2
        assert record["call_budget"]["actions"] == {"segment_context": 2}
        payload = json.loads(claim["operations"][0][1]["json"])
        assert payload["op"] == "add_liquidity" and payload["resource_symbol"] == "GRAIN"
        assert routing.amount(payload["resource_amount"]) == routing.amount(review["resource_amount"])
        assert routing.amount(payload["dec_amount"]) == routing.amount(review["dec_amount"])
        assert claim["member_nonces"] == [next(op for op in child_bundle["operations"] if op["kind"] == "liquidity")["members"][0]["nonce"]]
        assert runs.claim(folder, record, child_bundle["id"], {"lp_sell_half"}, True) == claim
        assert len(calls) == 2
        with pytest.raises(routing.RoutingError, match="different transaction"):
            runs.attach(folder, record, child_bundle["id"], "a" * 40)
            runs.attach(folder, record, child_bundle["id"], "b" * 40)


def test_insufficient_receipt_or_changed_pool_refuses_before_handoff(tmp_path, monkeypatch):
    calls = _fresh(monkeypatch, balance="1")
    with runs.locked(tmp_path, PLAYER) as folder:
        record = runs.create(folder, PLAYER, _preview(), {"lp_sell_half"})
        child_bundle = record["manifest"]["bundles"][1]
        _confirmed_parent(record)
        with pytest.raises(routing.RoutingError, match="protected LP resource minimum"):
            runs.claim(folder, record, child_bundle["id"], {"lp_sell_half"}, True)
        assert len(calls) == 2
        assert record["bundles"][child_bundle["id"]]["state"] == "planned"
        assert {item["state"] for item in record["reservations"].values()} <= {"held"}


def test_external_pool_is_rejected_at_compile():
    external = routing.Pool(1, "GRAIN", routing.amount("1000"), routing.amount("1000"),
                            routing.amount("1000"), True)
    with pytest.raises(routing.RoutingError, match="internal pool"):
        _preview(external)
