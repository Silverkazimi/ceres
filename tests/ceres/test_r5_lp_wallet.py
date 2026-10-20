"""Offline guards for the separate wallet DEC liquidity release."""

import json
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ceres import harvest_routing as routing, harvest_routing_operations as operations
from ceres import routing_plan, routing_runs_v2 as runs
from ceres.config import CeresConfig
from fides.op import DEFAULT_MAX_OPS_PER_TX


PLAYER = "fixtureowner"
REGION = "PR-AAA-1"
POOL = routing.Pool(1, "GRAIN", routing.amount("1000"), routing.amount("1000"),
                    routing.amount("1000"), False)


def _preview(count=1, received="20"):
    rules = [{"id": f"lp-{index}", "kind": "liquidity", "source_region": REGION,
              "symbol": "GRAIN", "quantity_mode": "amount", "quantity": "10",
              "dec_source": "wallet", "reserved_dec": "10", "protected_dec_minimum": "10",
              "slippage": "2.5"} for index in range(count)]
    ledger = routing.Ledger({(REGION, "GRAIN"): routing.amount("100"),
                             (routing.INVENTORY, "DEC"): routing.amount("100")},
                            {(routing.INVENTORY, "DEC"): routing.amount("10")},
                            {(REGION, "GRAIN"): routing.amount(received)})
    steps = routing.plan_after_harvest(ledger, rules, {"GRAIN": POOL}, "2.5")
    manifest = routing_plan.compile_manifest(rules=rules, steps=steps, pools={"GRAIN": POOL},
                                             account=PLAYER, regions=1, enabled_kinds={"lp_wallet"})
    return {"player": PLAYER, "phase": "after", "blocked": False,
            "preview_id": "offline-lp-preview", "preview_digest": "offline-lp-digest",
            "rules": rules, "steps": steps, "reserves": [{"region_uid": routing.INVENTORY,
              "symbol": "DEC", "amount": "10"}], "manifest": manifest,
            "state": {"pools": [{"id": 1, "symbol": "GRAIN", "resource_quantity": "1000",
                                "dec_quantity": "1000", "total_shares": "1000", "external": False}]}}


def _fresh(monkeypatch, balance="19.8", pool=True):
    calls = []

    class Transport:
        def __init__(self, *, timeout, retries):
            assert retries == 0

        def get_json(self, url):
            calls.append(url)
            if "/land/liquidity/pools" in url:
                if not pool:
                    raise RuntimeError("pool unavailable")
                return SimpleNamespace(data={"status": "success", "data": [{
                    "id": 1, "token_symbol": "GRAIN", "resource_quantity": 1000,
                    "dec_quantity": 1000, "total_shares": 1000,
                    "is_external_resource": False}]})
            if balance is None:
                raise RuntimeError("balance unavailable")
            return SimpleNamespace(data=[{"player": PLAYER, "token": "DEC", "balance": balance}])

    monkeypatch.setattr(runs, "FixedGetTransport", Transport)
    return calls


def test_switch_and_compiler_gate(monkeypatch):
    monkeypatch.delenv("CERES_ROUTING_LP_WALLET", raising=False)
    assert "lp_wallet" not in CeresConfig().routing_kinds
    preview = _preview()
    with pytest.raises(routing.RoutingError, match="disabled"):
        routing_plan.compile_manifest(rules=preview["rules"], steps=preview["steps"],
                                      pools={"GRAIN": POOL}, account=PLAYER, regions=1,
                                      enabled_kinds=set())
    monkeypatch.setenv("CERES_ROUTING_LP_WALLET", "1")
    assert "lp_wallet" in CeresConfig().routing_kinds


def test_one_pool_per_bundle_and_harvest_receipt_amount():
    preview = _preview(2)
    bundles = preview["manifest"]["bundles"]
    assert len(bundles) == 2
    assert all(len(bundle["operations"]) <= DEFAULT_MAX_OPS_PER_TX for bundle in bundles)
    assert all(len({op["pool"] for op in bundle["operations"]}) == len(bundle["operations"])
               for bundle in bundles)
    assert all(routing.amount(member["source_amount"]) == routing.amount("10") for bundle in bundles
               for op in bundle["operations"] for member in op["members"])
    with pytest.raises(routing.RoutingError):
        _preview(2, received="10")


def test_run_requires_post_harvest_preview(tmp_path):
    preview = _preview()
    preview["phase"] = "before"
    with runs.locked(tmp_path, PLAYER) as folder:
        with pytest.raises(routing.RoutingError, match="post-harvest"):
            runs.create(folder, PLAYER, preview, {"lp_wallet"})


def test_claim_reads_fresh_context_caps_dec_and_reuses_envelope(tmp_path, monkeypatch):
    calls = _fresh(monkeypatch)
    with runs.locked(tmp_path, PLAYER) as folder:
        record = runs.create(folder, PLAYER, _preview(), {"lp_wallet"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        claim = runs.claim(folder, record, bundle_id, {"lp_wallet"}, True)
        assert runs.claim(folder, record, bundle_id, {"lp_wallet"}, True) == claim
        assert len(calls) == 2
        assert record["call_budget"]["total"] == 2
        assert record["call_budget"]["actions"]["segment_context"] == 2
        assert len(claim["operations"]) == 1
        kind, envelope = claim["operations"][0]
        payload = json.loads(envelope["json"])
        assert kind == "custom_json" and envelope["id"] == "sm_land_operation"
        assert envelope["required_auths"] == [] and envelope["required_posting_auths"] == [PLAYER]
        assert payload["op"] == "add_liquidity" and payload["region_uid"] == REGION
        assert payload["resource_symbol"] == "GRAIN" and payload["shares_out"] == 0
        assert payload["dec_amount"] == payload["resource_amount"] == 10.0
        assert record["reservations"][claim["member_nonces"][0]]["dec_amount"] == "10.000"


def test_lp_wallet_add_liquidity_wire_payload_matches_on_chain_guard():
    # Reference transaction: 5bfc000c85cfae2b364642f7f477817e9c01f63d.
    deposit = routing.liquidity_deposit(POOL, "10")
    built = operations.build(PLAYER, {"kind": "liquidity", "source_region": REGION,
                                      "deposit": deposit}, "2.5")
    operation_type, envelope = built["operations"][0]
    payload = json.loads(envelope["json"])

    assert operation_type == "custom_json"
    assert envelope["id"] == "sm_land_operation"
    assert envelope["required_auths"] == []
    assert envelope["required_posting_auths"] == [PLAYER]
    assert built["authority"] == "Posting"
    assert type(payload["resource_amount"]) in (int, float)
    assert type(payload["dec_amount"]) in (int, float)
    assert type(payload["shares_out"]) in (int, float) and payload["shares_out"] == 0
    assert Decimal(str(payload["resource_amount"])).quantize(Decimal("0.001")) == Decimal(str(payload["resource_amount"]))
    assert Decimal(str(payload["dec_amount"])).quantize(Decimal("0.001")) == Decimal(str(payload["dec_amount"]))
    assert set(payload) == {"op", "region_uid", "resource_amount", "dec_amount", "shares_out",
                            "resource_symbol", "app", "n"}
    assert payload["op"] == "add_liquidity"
    assert re.fullmatch(r"ceres/\d+\.\d+\.\d+", payload["app"])


@pytest.mark.parametrize("pool,balance,expected_calls", [(False, "19.8", 1), (True, None, 2)])
def test_failed_fresh_read_never_claims(tmp_path, monkeypatch, pool, balance, expected_calls):
    calls = _fresh(monkeypatch, pool=pool, balance=balance)
    with runs.locked(tmp_path, PLAYER) as folder:
        record = runs.create(folder, PLAYER, _preview(), {"lp_wallet"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        with pytest.raises(routing.RoutingError, match="unavailable"):
            runs.claim(folder, record, bundle_id, {"lp_wallet"}, True)
        assert len(calls) == record["call_budget"]["total"] == expected_calls
        assert record["bundles"][bundle_id]["state"] == "planned"
        assert all(item["state"] == "held" for item in record["reservations"].values())
