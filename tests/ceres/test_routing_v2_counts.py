""" contracts for the later routing compiler and its counted run service.

The fixtures separate observed transaction counts from modeled route allocations.
No test in this file sends a wallet request or uses a live endpoint.
"""

from collections import Counter, defaultdict
from decimal import Decimal
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceres import harvest_routing as routing
from ceres import routes_harvest_routing as routes
from ceres import routing_plan
from ceres.app import create_app
from ceres.config import CeresConfig


FIXTURES = Path(__file__).parent / "fixtures" / "routing-runs"
PHYSICAL_CALL_CEILINGS = {
    "preview_context": 9,
    "harvest_receipt": 3,
    "segment_context": 8,
    "hive_head": 4,
    "chain_probe": 16,
    "game_verdict": 65,
    "bridge_history": 8,
    "pending_id": 1,
    "manual_retry": 6,
}


def _fixture(name):
    """Load a redacted historical run and its explicitly modeled route allocation."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _minimum_bundles(pools, cap=4):
    """Exhaustively find the small-case minimum under cap and unique-pool constraints."""
    ordered = sorted(pools, key=lambda pool: (-pools.count(pool), pool))
    for count in range(1, len(ordered) + 1):
        bins = [[] for _ in range(count)]

        def place(index):
            """Try every legal bin for one operation; symmetry pruning changes no minimum."""
            if index == len(ordered):
                return True
            seen = set()
            for bucket in bins:
                signature = tuple(bucket)
                if signature in seen:
                    continue
                seen.add(signature)
                if len(bucket) >= cap or ordered[index] in bucket:
                    continue
                bucket.append(ordered[index])
                if place(index + 1):
                    return True
                bucket.pop()
            return False

        if place(0):
            return count
    return 0


def _preview(client, monkeypatch, fixture, *, return_response=False):
    """Feed the existing preview endpoint bounded, offline maintainer context and receipts."""
    model = fixture["model"]
    receipt_totals = defaultdict(Decimal)
    for item in model["routes"]:
        receipt_totals[(item["region"], item["symbol"])] += Decimal(item["amount"])
    balances = dict(receipt_totals)
    balances[(routing.INVENTORY, "DEC")] = Decimal("100000000")
    balances[(routing.INVENTORY, "SPS")] = Decimal("0")
    pools = {symbol: routing.Pool(index, symbol, Decimal("100000000"), Decimal("100000000"),
                                  Decimal("1000000"), symbol == "SPS")
             for index, symbol in enumerate(("GRAIN", "WOOD", "STONE", "IRON", "SPS"), 1)}
    monkeypatch.setattr(routes, "_context", lambda request, player: {
        "public": {"blocked": False, "observed_at": "offline-fixture", "signing_enabled": False,
                   "regions": [{"region_uid": region} for region in model["regions"]]},
        "pools": pools, "ledger": routing.Ledger(balances),
    })
    monkeypatch.setattr(routes, "_receipts", lambda request, player, ids: dict(receipt_totals))
    rules = []
    for item in model["routes"]:
        kind = item["kind"]
        rule = {"id": item["id"], "kind": "liquidity" if kind.startswith("lp_") else "sell",
                "source_region": item["region"], "symbol": item["symbol"],
                "quantity_mode": "amount", "quantity": item["amount"], "slippage": item["slippage"]}
        if kind.startswith("lp_"):
            rule["dec_source"] = "wallet" if kind == "lp_wallet" else "swap_half"
        else:
            rule["destination_symbol"] = "SPS" if kind == "sps_out" else "DEC"
        rules.append(rule)
    response = client.post("/api/harvest/routing/preview?player=fixtureowner", json={
        "scope": {"kind": "regions", "region_uids": model["regions"]}, "phase": "after", "manifest_version": 2,
        "rules": rules, "slippage": "2.5", "reserves": [], "harvest_attempt_ids": ["offline-receipt"],
    })
    if return_response:
        return response
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("run,actual,forecast", [("a", 11, 3), ("b", 11, 4), ("c", 7, 2)])
def test_historical_runs_compile_to_exact_counts_and_members(tmp_path, monkeypatch, run, actual, forecast):
    """Prove 11/11/7 historical routing tx become 3/4/2 plus one harvest; red today because preview has no manifest or coalesced membership."""
    fixture = _fixture(run)
    assert fixture["historical"]["routing_transactions"] == actual
    assert sum(fixture["historical"]["transaction_operations"]) == fixture["historical"]["routing_operations"]
    config = CeresConfig(root=tmp_path / ".ceres", player="fixtureowner")
    config.routing_kinds = {item["kind"] for item in fixture["model"]["routes"]}
    preview = _preview(TestClient(create_app(config)), monkeypatch, fixture)
    assert "manifest" in preview, " must add the immutable compiler manifest to preview"
    manifest = preview["manifest"]
    assert manifest["approval_counts"]["routing"] == forecast
    assert manifest["approval_counts"]["harvest"] == 1
    assert manifest["approval_counts"]["total"] == forecast + 1
    bundles = manifest["bundles"]
    assert len(bundles) == forecast
    assert Counter(member["route_id"] for bundle in bundles for operation in bundle["operations"]
                   for member in operation["members"]) == Counter({route["id"]: 2 if route["kind"] in
                   {"sps_out", "lp_sell_half"} else 1 for route in fixture["model"]["routes"]})
    assert all(len(bundle["operations"]) <= 4 for bundle in bundles)
    assert all(len({operation["pool"] for operation in bundle["operations"]}) == len(bundle["operations"])
               for bundle in bundles)
    assert sorted(len(bundle["operations"]) for bundle in bundles) == sorted(
        count for wave in fixture["model"]["expected"]["wave_bundle_operations"] for count in wave)
    for wave in {bundle["wave"] for bundle in bundles}:
        wave_bundles = [bundle for bundle in bundles if bundle["wave"] == wave]
        pools = [operation["pool"] for bundle in wave_bundles for operation in bundle["operations"]]
        assert len(wave_bundles) == _minimum_bundles(pools)
    merged = {frozenset(member["route_id"] for member in operation["members"])
              for bundle in bundles for operation in bundle["operations"] if len(operation["members"]) > 1}
    assert {frozenset(group) for group in fixture["model"]["expected"]["merged_sale_members"]}.issubset(merged)
    members_by_route = defaultdict(list)
    for bundle in bundles:
        for operation in bundle["operations"]:
            for member in operation["members"]:
                members_by_route[member["route_id"]].append(member)
                if len(operation["members"]) > 1:
                    assert Decimal(member["source_amount"]) > 0
                    assert Decimal(member["protected_minimum"]) >= 0
                    assert "allocation" in member
    for route in fixture["model"]["routes"]:
        expected_inputs = ([route["sale_amount"], route["deposit_amount"]] if route["kind"] == "lp_sell_half"
                           else [route["amount"]])
        actual_inputs = {Decimal(member["source_amount"]) for member in members_by_route[route["id"]]}
        if route["kind"] == "lp_sell_half" and fixture["model"].get("exact_lp_split") is False:
            # Option b (see the fixture's exact_lp_split_note): conserve the route total, not the historical split.
            total = sum((Decimal(member["source_amount"]) for member in members_by_route[route["id"]]), Decimal(0))
            assert abs(total - sum(Decimal(value) for value in expected_inputs)) <= Decimal("0.002"), (route["id"], total)
            continue
        assert {Decimal(value) for value in expected_inputs}.issubset(actual_inputs)


def test_default_disabled_kinds_are_a_server_boundary(tmp_path, monkeypatch):
    """Prove no route can execute with empty routing_kinds; red today because legacy preview has no enabled_kinds boundary."""
    fixture = _fixture("c")
    config = CeresConfig(root=tmp_path / ".ceres", player="fixtureowner")
    client = TestClient(create_app(config))
    response = _preview(client, monkeypatch, fixture, return_response=True)
    assert response.status_code in {403, 422}
    assert "disabled" in response.text.lower()
    state = client.get("/api/harvest/routing/state?player=fixtureowner")
    assert state.status_code == 200, state.text
    assert state.json()["enabled_kinds"] == []


def test_physical_call_budget_is_exposed_before_a_run(tmp_path, monkeypatch):
    """Prove the 120-call account-scoped budget and action caps are visible; red today because no shared physical-call guard exists."""
    assert sum(PHYSICAL_CALL_CEILINGS.values()) == 120
    fixture = _fixture("b")
    config = CeresConfig(root=tmp_path / ".ceres", player="fixtureowner")
    config.routing_kinds = {item["kind"] for item in fixture["model"]["routes"]}
    preview = _preview(TestClient(create_app(config)), monkeypatch, fixture)
    budget = preview["manifest"]["call_budget"]
    assert budget["run_ceiling"] == 120
    assert budget["action_ceilings"] == PHYSICAL_CALL_CEILINGS
    assert budget["account"] == "fixtureowner"
    assert budget["planning_limits"] == {}  # no fixed plan-size limits any more


def test_many_regions_are_never_refused_by_count(tmp_path, monkeypatch):
    """: 150 selected regions reach the normal context read; no region-count refusal comes first."""
    calls = []

    def stop_at_context(request, player):
        calls.append(player)
        raise routing.RoutingError("stop here")

    monkeypatch.setattr(routes, "_context", stop_at_context)
    config = CeresConfig(root=tmp_path / ".ceres", player="fixtureowner")
    response = TestClient(create_app(config)).post("/api/harvest/routing/preview?player=fixtureowner", json={
        "scope": {"kind": "regions", "region_uids": [f"REGION_{index}" for index in range(150)]},
        "phase": "after", "manifest_version": 2, "rules": [], "harvest_attempt_ids": [],
    })
    assert response.status_code == 422
    assert "stop here" in response.text and "five regions" not in response.text.lower()
    assert calls == ["fixtureowner"]


def test_call_budget_grows_with_the_plan_and_keeps_the_old_floor():
    """: a small plan keeps the old 120-call budget; a 150-bundle plan gets proportionally more."""
    def bundle(lp=False):
        return {"operations": [{"kind": "liquidity" if lp else "sell", "members": [{"kind": "lp_wallet" if lp else "sale_dec"}]}] * 4}
    small = routing_plan.call_budget([bundle() for _ in range(3)])
    assert small["run_ceiling"] == 120 and small["action_ceilings"] == PHYSICAL_CALL_CEILINGS
    big = routing_plan.call_budget([bundle() for _ in range(150)] + [bundle(True) for _ in range(10)])
    assert big["action_ceilings"]["chain_probe"] == 320 and big["action_ceilings"]["game_verdict"] == 5 * 160 * 4
    assert big["action_ceilings"]["segment_context"] == 20
    assert big["run_ceiling"] == sum(big["action_ceilings"].values())


def test_whale_regions_route_and_regions_without_harvest_are_left_out(tmp_path, monkeypatch):
    """: 150 extra owned regions that harvested nothing neither block the preview nor enter it."""
    fixture = _fixture("b")
    harvested = set(fixture["model"]["regions"])
    fixture["model"]["regions"] = fixture["model"]["regions"] + [f"PR-BRL-{900 + index}" for index in range(150)]
    config = CeresConfig(root=tmp_path / ".ceres", player="fixtureowner")
    config.routing_kinds = {item["kind"] for item in fixture["model"]["routes"]}
    preview = _preview(TestClient(create_app(config)), monkeypatch, fixture)
    members = [member for bundle in preview["manifest"]["bundles"] for op in bundle["operations"] for member in op["members"]]
    assert members
    regions = {op["source_region"] for bundle in preview["manifest"]["bundles"] for op in bundle["operations"]}
    assert regions <= harvested | {routing.INVENTORY}
