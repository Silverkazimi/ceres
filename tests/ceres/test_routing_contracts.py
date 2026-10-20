import asyncio
from decimal import Decimal as D
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceres import harvest_routing as routing
from ceres import harvest_evidence
from ceres import harvest_routing_runs as runs
from ceres import harvest_routing_operations as operations
from ceres.app import create_app

from .conftest import spawn_live_ceres
from ceres.config import CeresConfig
from ceres import routes_harvest_routing as routing_routes
from tests.ceres.vm_context import run_in_module_context


def pools():
    return {s: routing.Pool(i, s, D(100000), D(100000), D(10000)) for i, s in enumerate(["GRAIN", "WOOD"], 1)}


def ledger():
    return routing.Ledger({("PR-BRL-41", "GRAIN"): D(1000), ("PR-BRL-65", "GRAIN"): D(0), (routing.INVENTORY, "DEC"): D(1000)}, harvested={("PR-BRL-41", "GRAIN"): D(100)})


def rule(kind="transfer", quantity="50"):
    return {"id": "one", "kind": kind, "source_region": "PR-BRL-41", "symbol": "GRAIN", "destination_region": "PR-BRL-65", "destination_symbol": "DEC", "quantity_mode": "percent", "quantity": quantity}


def test_transfer_spends_only_receipt_even_with_larger_inventory():
    book = ledger()
    step = routing.plan_after_harvest(book, [rule()], pools(), "2.5")[0]
    assert D(step["quote"]["input"]) == D(50)
    assert book.allocated[("PR-BRL-41", "GRAIN")] == D(50)
    assert operations.build("player0005", step, "2.5")["authority"] == "Posting"


def test_saved_inventory_flag_cannot_route_old_inventory():
    book = ledger(); book.harvested.clear()
    saved = rule(); saved["include_inventory"] = True
    assert routing.plan_after_harvest(book, [saved], pools(), "2.5")[0]["status"] == "empty"
    assert not book.allocated


def test_overlapping_rules_cannot_double_spend_harvest():
    second = rule(quantity="60"); second["id"] = "two"
    with pytest.raises(routing.RoutingError):
        routing.plan_after_harvest(ledger(), [rule(quantity="60"), second], pools(), "2.5")


def test_reserves_protect_balances_and_atomic_debits_roll_back():
    book=ledger(); book.reserves[("PR-BRL-41", "GRAIN")]=D(990)
    with pytest.raises(routing.RoutingError):
        book.allocate_many([("PR-BRL-41", "GRAIN", "5", True), (routing.INVENTORY, "DEC", "1001", True)])
    assert book.allocated == {}
    assert book.available("PR-BRL-41", "GRAIN", harvest_only=True) == D(10)


def test_liquidity_default_uses_funding_sale_without_spending_wallet_dec():
    step = routing.plan_after_harvest(ledger(), [rule("liquidity")], pools(), "2.5")
    assert [item["kind"] for item in step] == ["liquidity_funding_swap", "liquidity"]
    assert step[0]["dec_source"] == "swap_half"
    assert step[1]["dec_source"] == "swap_half"


def test_liquidity_dec_source_validation_is_lp_only_and_enum_bounded():
    bad_lp = dict(rule("liquidity"), dec_source="automatic")
    with pytest.raises(routing.RoutingError, match="Choose wallet DEC"):
        routing.validate_rules([bad_lp], ledger(), pools(), {"PR-BRL-41"})
    bad_sale = dict(rule("sell"), dec_source="wallet")
    with pytest.raises(routing.RoutingError, match="only valid for liquidity"):
        routing.validate_rules([bad_sale], ledger(), pools(), {"PR-BRL-41"})
    for malformed in ([], {}, True, 1):
        with pytest.raises(routing.RoutingError, match="Choose wallet DEC"):
            routing.validate_rules([dict(rule("liquidity"), dec_source=malformed)],
                                   ledger(), pools(), {"PR-BRL-41"})


def test_liquidity_adaptive_default_is_swap_half_even_when_harvest_dec_is_available():
    book = ledger()
    book.harvested[(routing.INVENTORY, "DEC")] = D("60")
    steps = routing.plan_after_harvest(book, [rule("liquidity")], pools(), "2.5")
    assert len(steps) == 2
    assert steps[0]["dec_source"] == "swap_half"
    assert steps[1]["dec_source"] == "swap_half"
    assert steps[1]["dec_source_defaulted"] is True


def test_liquidity_adaptive_default_uses_dependent_swap_when_harvest_dec_is_short():
    steps = routing.plan_after_harvest(ledger(), [rule("liquidity")], pools(), "2.5")
    assert [step["kind"] for step in steps] == ["liquidity_funding_swap", "liquidity"]
    first, deposit = steps
    assert first["id"] == "one:dec"
    assert deposit["depends_on"] == first["id"]
    assert deposit["dec_source"] == "swap_half"
    assert deposit["dec_source_defaulted"] is True
    split = deposit["dec_funding"]["split"]
    assert D(split["swap_resource"]) > D(split["total_resource"]) / 2
    assert D(split["protected_dec"]) >= D(split["required_dec"])
    assert D(split["swap_resource"]) + D(split["deposit_resource"]) == D(split["total_resource"])


def test_swap_half_solver_protects_one_more_slippage_bound_for_the_deposit():
    split = routing.liquidity_funding_split(
        routing.Pool(1, "GRAIN", D("1000"), D("1000"), D("100")), D("50"), "2.5")
    assert split["swap_resource"] == "26.629"
    assert split["deposit_resource"] == "23.371"
    assert split["protected_dec"] == "22.819"
    assert split["required_dec"] == "22.261"
    assert D(split["protected_dec"]) >= routing.ceiling(D(split["required_dec"]) * D("1.025"))


def test_swap_half_solver_rejects_below_precision_split():
    with pytest.raises(routing.RoutingError, match="too small|protected|split"):
        routing.liquidity_funding_split(pools()["GRAIN"], D("0.002"), "2.5")


def test_preview_blocks_unfundable_tiny_lp_rule_without_losing_later_sale(tmp_path, monkeypatch):
    body = {"scope": {"kind": "regions", "region_uids": ["PR-BRL-41"]}, "phase": "after",
            "rules": [dict(rule("liquidity"), quantity_mode="amount", quantity="0.002"),
                      dict(rule("sell"), id="sale", quantity_mode="amount", quantity="10")],
            "reserves": [], "slippage": "2.5", "harvest_attempt_ids": ["receipt"]}

    monkeypatch.setattr(routing_routes, "_context", lambda request, player: {
        "public": {"blocked": False, "observed_at": "x", "signing_enabled": True,
                   "regions": [{"region_uid": "PR-BRL-41"}]},
        "pools": {"GRAIN": routing.Pool(1, "GRAIN", D("1000"), D("1000"), D("1000"))},
        "ledger": ledger()})
    monkeypatch.setattr(routing_routes, "_receipts", lambda *args: {
        ("PR-BRL-41", "GRAIN"): D("100")})
    client = TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005")))

    response = client.post("/api/harvest/routing/preview?player=player0005", json=body)

    assert response.status_code == 200, response.text
    result = response.json()
    # sales are planned before liquidity
    liquidity = next(step for step in result["steps"] if step["kind"] != "sell")
    sale = next(step for step in result["steps"] if step["kind"] == "sell")
    assert result["blocked"] is True
    assert liquidity["status"] == "awaiting_funds"
    assert liquidity["blocked"] is True
    assert liquidity["dec_source"] == "swap_half"
    assert liquidity["dec_source_defaulted"] is True
    assert liquidity["reason"] == "The pool cannot fund this liquidity split"
    assert sale["kind"] == "sell"
    assert sale["status"] == "ready"


def test_multiple_adaptive_lp_rules_use_funding_sales_in_order():
    book = ledger()
    book.harvested[(routing.INVENTORY, "DEC")] = D("40")
    first = rule("liquidity", "30")
    second = dict(rule("liquidity", "30"), id="two")
    steps = routing.plan_after_harvest(book, [first, second], pools(), "2.5")
    assert [step["kind"] for step in steps] == ["liquidity_funding_swap", "liquidity", "liquidity_funding_swap", "liquidity"]
    assert all(step["dec_source"] == "swap_half" for step in steps)
    assert not any(step.get("blocked") for step in steps)


def test_explicit_wallet_lp_shortage_does_not_switch_to_swap_half():
    book = ledger()
    book.balances[(routing.INVENTORY, "DEC")] = D("40")
    first = dict(rule("liquidity", "30"), dec_source="wallet")
    second = dict(rule("liquidity", "30"), id="two", dec_source="wallet")
    steps = routing.plan_after_harvest(book, [first, second], pools(), "2.5")
    assert [step["dec_source"] for step in steps] == ["wallet", "wallet"]
    # a wallet shortage
    # at planning never blocks; the run checks the live wallet right before signing the LP step.
    assert not steps[1].get("blocked")
    assert "live wallet" in steps[1]["note"]


def test_wallet_dec_is_explicit_and_does_not_consume_harvest_provenance():
    book = ledger()
    book.harvested[(routing.INVENTORY, "DEC")] = D("100")
    wallet_rule = dict(rule("liquidity"), dec_source="wallet")
    step = routing.plan_after_harvest(book, [wallet_rule], pools(), "2.5")[0]
    assert step["dec_source"] == "wallet"
    assert book.allocated[(routing.INVENTORY, "DEC")] == D("51.250")
    assert (routing.INVENTORY, "DEC") not in book.harvested_allocated
    assert book.available(routing.INVENTORY, "DEC", harvest_only=True) == D("100.000")


def test_wallet_shortage_remains_visible_on_the_lp_step():
    wallet_book = ledger()
    wallet_book.balances[(routing.INVENTORY, "DEC")] = D("40")
    wallet = routing.plan_after_harvest(
        wallet_book, [dict(rule("liquidity"), dec_source="wallet")], pools(), "2.5")[0]
    assert wallet["status"] == "ready" and not wallet.get("blocked")  # checked at signing, not here
    assert wallet["dec_funding"]["shortfall"] == "11.250"

def test_swap_half_materializes_dependency_and_additional_signature():
    preview = routing.plan_after_harvest(ledger(), [rule("liquidity")], pools(), "2.5")
    durable = runs._materialize_steps(preview)
    assert durable[1]["depends_on_step_id"] == durable[0]["step_id"]
    assert sum(step["signature_cost"] for step in durable) == 2
    record = {"phase": "after", "steps": durable, "phase_steps": {}, "harvest_preview": {"chunks": [{}, {}]},
              "harvest_attempts": [{"attempt_id": "one"}, {"attempt_id": "two"}],
              "harvest_signature_ids": ["a" * 40, "b" * 40]}
    runs._update_signature_totals(record)
    assert record["signatures_total"] == 4
    assert record["total_signatures"] == 4
    assert record["signed_signatures"] == 2


def test_signature_total_counts_bundled_sales_as_one_prompt_and_distinct_tx_once():
    steps = []
    for index, symbol in enumerate(("GRAIN", "WOOD", "STONE")):
        steps.append({"step_id": str(index) * 32, "kind": "sell", "signature_cost": 1,
                      "quote": {"source_symbol": symbol, "destination_symbol": "DEC", "external": False}})
    record = {"phase": "after", "steps": steps, "phase_steps": {}, "harvest_preview": {"chunks": []}}
    runs._update_signature_totals(record)
    assert record["signatures_total"] == 1
    for step in steps:
        step["tx_id"] = "a" * 40
    runs._update_signature_totals(record)
    assert record["signed_signatures"] == 1
    assert record["signatures_total"] == 1


def test_external_sps_swap_half_counts_four_signatures_and_two_extra():
    book = routing.Ledger({(routing.INVENTORY, "SPS"): D("100"),
                           (routing.INVENTORY, "DEC"): D("1000")},
                          harvested={(routing.INVENTORY, "SPS"): D("100")})
    lp_rule = {"id": "sps-lp", "kind": "liquidity", "source_region": routing.INVENTORY,
               "symbol": "SPS", "quantity_mode": "percent", "quantity": "50",
               "dec_source": "swap_half"}
    steps = routing.plan_after_harvest(book, [lp_rule], pools_with_sps(), "2.5")
    assert [step["signature_cost"] for step in steps] == [2, 2]
    assert steps[1]["dec_funding"]["extra_signatures"] == 2
    record = {"phase": "after", "steps": runs._materialize_steps(steps), "phase_steps": {},
              "harvest_preview": {"chunks": []}}
    runs._update_signature_totals(record)
    assert record["signatures_total"] == 4


def test_signature_total_is_exposed_only_after_full_after_plan_and_stays_fixed():
    record = {"phase": "harvest", "steps": [], "phase_steps": {}, "harvest_preview": {"chunks": [{}, {}]},
              "harvest_attempts": [{"attempt_id": "one"}, {"attempt_id": "two"}],
              "harvest_signature_ids": ["a" * 40, "b" * 40]}
    runs._update_signature_totals(record)
    assert "signatures_total" not in record
    record.update(phase="after", steps=[{"kind": "liquidity", "signature_cost": 1}])
    runs._update_signature_totals(record)
    assert record["signatures_total"] == 3
    record["steps"][0]["state"] = "confirmed"
    runs._update_signature_totals(record)
    assert record["signatures_total"] == 3


def test_harvest_signature_evidence_excludes_rejected_before_send(monkeypatch):
    attempts = {
        "sent": {"tx_id": "a" * 40},
        "unsent": {"state": "rejected_before_send"},
    }
    monkeypatch.setattr(harvest_evidence, "load_attempt", lambda root, player, identifier: attempts[identifier])
    assert runs.harvest_signature_ids(Path("/unused"), "player0005", ["sent", "unsent"]) == ["a" * 40]


def test_external_c_funding_contract_and_dependency_cascade():
    effects = {"external_transfer_account": "sps.bridge", "external_swap_token": "SPS",
               "pending_swap_id": 123, "pending_external_amount": "1.234"}
    funding = operations.build_sps_funding("player0005", effects, "liquidity_funding_swap")
    assert funding["payload"]["params"]["memo"] == "lpsid:123"
    steps = runs._materialize_steps(routing.plan_after_harvest(
        routing.Ledger({(routing.INVENTORY, "SPS"): D("100"), (routing.INVENTORY, "DEC"): D("1000")},
                       harvested={(routing.INVENTORY, "SPS"): D("100")}),
        [{"id": "sps-lp", "kind": "liquidity", "source_region": routing.INVENTORY,
          "symbol": "SPS", "quantity_mode": "percent", "quantity": "50", "dec_source": "swap_half"}],
        pools_with_sps(), "2.5"))
    steps[0]["state"] = "failed"
    runs.cascade_dependents({"steps": steps})
    assert steps[1]["state"] == "skipped"
    assert steps[1]["skip_metadata"]["reason"] == "dependency_unfunded"


def test_swap_half_fresh_deposit_uses_only_exact_dependency_proceeds(monkeypatch):
    parent_id = "a" * 32
    child = {"step_id": "b" * 32, "state": "planned", "kind": "liquidity",
             "source_region": "PR-BRL-41", "symbol": "GRAIN", "quantity": "50.000",
             "dec_source": "swap_half", "depends_on_step_id": parent_id,
             "dec_funding": {"source": "swap_half", "split": {"swap_resource": "50.000"}}}
    record = {"phase": "after", "slippage": "2.5", "reserves": [],
              "harvest_attempt_ids": [], "steps": [
                  {"step_id": parent_id, "state": "confirmed", "kind": "liquidity_funding_swap",
                   "receipt": {"received": [{"symbol": "DEC", "amount": "49.999"}]}}, child]}
    context = {"public": {"blocked": False, "observed_at": "x"}, "pools": pools(), "ledger": ledger()}
    monkeypatch.setattr(routing_routes, "_remaining_harvest", lambda *args: {
        ("PR-BRL-41", "GRAIN"): D("50"), (routing.INVENTORY, "DEC"): D("49.999")})

    with pytest.raises(routing.RoutingError, match="short by 0.001 DEC"):
        routing_routes._fresh_step(None, "player0005", record, child, context)

    record["steps"][0]["receipt"]["received"][0]["amount"] = "60.000"
    context["ledger"] = ledger()
    monkeypatch.setattr(routing_routes, "_remaining_harvest", lambda *args: {
        ("PR-BRL-41", "GRAIN"): D("50"), (routing.INVENTORY, "DEC"): D("60")})
    fresh, _public = routing_routes._fresh_step(None, "player0005", record, child, context)
    assert fresh["deposit"]["dec_amount"] == "50.000"
    assert fresh["dec_funding"]["available"] == "60.000"
    assert fresh["dec_funding"]["split"] == {"swap_resource": "50.000"}
    assert fresh["dec_funding"]["confirmed_dec"] == "60.000"
    assert fresh["dec_funding"]["remainder_dec"] == "10.000"


def test_review_auto_skips_short_swap_half_deposit_and_continues(tmp_path, monkeypatch):
    parent_id = "a" * 32
    deposit_id = "b" * 32
    sale_id = "c" * 32
    parent = {"step_id": parent_id, "state": "confirmed", "kind": "liquidity_funding_swap",
              "receipt": {"received": [{"symbol": "DEC", "amount": "22.500"}]}}
    deposit = {"step_id": deposit_id, "state": "planned", "kind": "liquidity",
               "source_region": "PR-BRL-41", "symbol": "GRAIN", "quantity": "25.000",
               "dec_source": "swap_half", "depends_on_step_id": parent_id,
               "dec_funding": {"source": "swap_half"}}
    sale = {"step_id": sale_id, "state": "planned", "kind": "sell",
            "source_region": "PR-BRL-41", "symbol": "GRAIN", "quantity": "10.000",
            "destination_region": routing.INVENTORY,
            "quote": {"source_symbol": "GRAIN", "destination_symbol": "DEC",
                      "input": "10.000", "output": "8.000", "minimum": "7.800",
                      "intermediate_dec": "0", "external": False}}
    record = {"run_id": "d" * 32, "player": "player0005", "created": 1.0, "updated": 1.0,
              "phase": "after", "state": "ready",
              "scope": {"kind": "regions", "region_uids": ["PR-BRL-41"]},
              "steps": [parent, deposit, sale], "rules": [], "reserves": [], "slippage": "2.5",
              "harvest_attempt_ids": []}
    root = tmp_path / ".ceres"
    with runs.locked(root, "player0005") as folder:
        runs.write(folder, record)
    pool = routing.Pool(1, "GRAIN", D("1000"), D("902.56"), D("100"))
    monkeypatch.setattr(routing_routes, "_context", lambda request, player: {
        "public": {"blocked": False, "observed_at": "x"}, "pools": {"GRAIN": pool},
        "ledger": ledger()})
    monkeypatch.setattr(routing_routes, "_remaining_harvest", lambda *args: {
        ("PR-BRL-41", "GRAIN"): D("35"), (routing.INVENTORY, "DEC"): D("22.500")})
    monkeypatch.setattr(routing_routes, "_require_posting_contract", lambda: None)
    client = TestClient(create_app(CeresConfig(root=root, player="player0005",
                                               harvest_routing_signing_enabled=True)))

    response = client.post("/api/harvest/routing/review?player=player0005",
                           json={"run_id": "d" * 32})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["review"] is None
    saved_deposit = result["run"]["steps"][1]
    assert saved_deposit["state"] == "skipped"
    assert saved_deposit["skip_metadata"]["reason"] == "insufficient_dependency_proceeds"
    assert saved_deposit["error"] == (
        "The selected swap half DEC source is short by 0.064 DEC "
        "(22.500 available; 22.564 required)")
    assert runs.next_step(result["run"])["step_id"] == sale_id


def test_fresh_wallet_source_rechecks_the_wallet_budget(monkeypatch):
    base_step = {"step_id": "b" * 32, "state": "planned", "kind": "liquidity",
                 "source_region": "PR-BRL-41", "symbol": "GRAIN", "quantity": "50.000"}
    record = {"phase": "after", "slippage": "2.5", "reserves": [],
              "harvest_attempt_ids": [], "steps": [base_step]}
    monkeypatch.setattr(routing_routes, "_remaining_harvest", lambda *args: {
        ("PR-BRL-41", "GRAIN"): D("50"), (routing.INVENTORY, "DEC"): D("50")})
    wallet = dict(base_step, dec_source="wallet")
    fresh, _public = routing_routes._fresh_step(None, "player0005", record, wallet, {
        "public": {"blocked": False, "observed_at": "x"}, "pools": pools(), "ledger": ledger()})
    assert fresh["dec_funding"]["available"] == "1000.000"
def test_remaining_harvest_debits_dec_for_funding_sale_lp(monkeypatch):
    monkeypatch.setattr(routing_routes, "_receipts", lambda *args: {
        ("PR-BRL-41", "GRAIN"): D("100"), (routing.INVENTORY, "DEC"): D("100")})
    step = {"state": "confirmed", "kind": "liquidity", "source_region": "PR-BRL-41",
            "symbol": "GRAIN", "dec_source": "wallet",
            "deposit": {"resource_amount": "10.000", "dec_amount": "10.000"}}
    record = {"harvest_attempt_ids": [], "steps": [step]}
    wallet = routing_routes._remaining_harvest(None, "player0005", record)
    assert wallet[("PR-BRL-41", "GRAIN")] == D("90")
    assert wallet[(routing.INVENTORY, "DEC")] == D("100")
    step["dec_source"] = "swap_half"
    funding_sale = routing_routes._remaining_harvest(None, "player0005", record)
    assert funding_sale[(routing.INVENTORY, "DEC")] == D("90")


def test_preview_returns_structured_blocked_liquidity_card(monkeypatch):
    body = {"scope": {"kind": "regions", "region_uids": ["PR-BRL-41"]}, "phase": "after",
            "rules": [dict(rule("liquidity"), dec_source="harvest")], "reserves": [],
            "slippage": "2.5", "harvest_attempt_ids": ["receipt"]}
    async def fake_body(_request):
        return body
    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(routing_routes, "_body", fake_body)
    monkeypatch.setattr(routing_routes.asyncio, "to_thread", inline)
    monkeypatch.setattr(routing_routes, "player_for_request", lambda request, player: player)
    wallet_book = ledger()
    wallet_book.balances[(routing.INVENTORY, "DEC")] = D("50")
    monkeypatch.setattr(routing_routes, "_context", lambda request, player: {
        "public": {"blocked": False, "observed_at": "x", "signing_enabled": True,
                   "regions": [{"region_uid": "PR-BRL-41"}]},
        "pools": pools(), "ledger": wallet_book})
    monkeypatch.setattr(routing_routes, "_receipts", lambda *args: {
        ("PR-BRL-41", "GRAIN"): D("100"), (routing.INVENTORY, "DEC"): D("50")})
    coroutine = routing_routes.preview(None, player="player0005")
    with pytest.raises(StopIteration) as completed:
        coroutine.send(None)
    result = completed.value.value
    assert not result["blocked"]  # a wallet shortage is checked at signing, not in the preview
    assert result["steps"][0]["dec_funding"] == {
        "source": "wallet", "available": "50.000", "required": "50.000",
        "up_to": "51.250", "reserved": "51.250", "shortfall": "1.250"}
    assert not any("short by" in issue for issue in result["issues"])


def test_sales_do_not_credit_unconfirmed_output():
    book=ledger();before=dict(book.balances)
    step=routing.plan_after_harvest(book,[rule("sell")],pools(),"2.5")[0]
    assert D(step["quote"]["output"]) > 0
    assert book.balances == before
    assert (routing.INVENTORY,"DEC") not in book.harvested


@pytest.mark.parametrize("attempt", [None, {"state":"awaiting_wallet"}, {"tx_id":"a", "members":[{"outcome":"success","game_state":"pending"}]}])
def test_unknown_harvest_never_becomes_allocatable(attempt):
    with pytest.raises(routing.RoutingError): runs.classify_harvest_attempt(attempt)


def test_partial_harvest_keeps_only_evidenced_success():
    good={"outcome":"success","game_state":"accepted","evidence":{"received":1}}
    bad={"outcome":"failed","game_state":"refused"}
    assert runs.classify_harvest_attempt({"tx_id":"a","members":[good,bad]}) == ([good],[bad])


def test_sps_funding_requires_exact_game_reference_and_active_authority():
    effects={"external_transfer_account":"sps.bridge","external_swap_token":"SPS","pending_swap_id":123,"pending_external_amount":"1.234"}
    result=operations.build_sps_funding("player0005",effects,"sell")
    assert result["authority"] == "Active"
    assert result["payload"]["params"]["memo"] == "lpsid:123"
    for changed in ({"pending_swap_id":True},{"pending_external_amount":"1.2345"},{"external_swap_token":"DEC"}):
        with pytest.raises((ValueError,routing.RoutingError)):operations.build_sps_funding("player0005",dict(effects,**changed),"sell")


def test_external_sps_sale_rejects_unsupported_resource_to_resource_payload():
    step = {
        "kind": "sell", "source_region": "PR-BRL-41",
        "quote": {"source_symbol": "GRAIN", "destination_symbol": "SPS",
                  "input": "100.000", "output": "2.500", "external": True},
    }
    with pytest.raises(routing.RoutingError, match="DEC/resource contract"):
        operations.build("player0005", step, "2.5")


def test_external_sps_contract_is_reserved_for_dec_resource_swap():
    step = {
        "kind": "sell", "source_region": "inventory",
        "destination_region": "inventory",
        "quote": {"source_symbol": "DEC", "destination_symbol": "SPS",
                  "input": "100.000", "output": "2.500", "external": True},
    }
    payload = operations.build("player0005", step, "2.5")["payload"]
    assert payload["resource_symbol"] == "SPS"
    assert payload["dec_amount"] == 100.0
    assert payload["shares_out"] == 2.5
    assert "from_symbol" not in payload


def test_internal_resource_sale_keeps_two_resource_payload():
    step = {
        "kind": "sell", "source_region": "PR-BRL-41",
        "destination_region": "inventory",
        "quote": {"source_symbol": "GRAIN", "destination_symbol": "WOOD",
                  "input": "100.000", "output": "2.500", "intermediate_dec": "1.000",
                  "external": False},
    }
    payload = operations.build("player0005", step, "2.5")["payload"]
    assert payload["from_symbol"] == "GRAIN"
    assert payload["to_symbol"] == "WOOD"


def pools_with_sps():
    both = pools()
    both["SPS"] = routing.Pool(3, "SPS", D(100000), D(100000), D(10000), external=True)
    return both


def sps_rule(quantity="50"):
    return {"id": "sale", "kind": "sell", "source_region": "PR-BRL-41", "symbol": "GRAIN",
            "destination_symbol": "SPS", "quantity_mode": "percent", "quantity": quantity}


def test_resource_to_sps_sale_plans_two_dependent_steps():
    live_pools = pools_with_sps()
    book = ledger()
    steps = routing.plan_after_harvest(book, [sps_rule()], live_pools, "2.5")
    assert len(steps) == 2
    first, second = steps
    assert first["id"] == "sale:dec"
    assert first["two_step"] == {"role": "first", "final_symbol": "SPS"}
    assert first["quote"]["source_symbol"] == "GRAIN" and first["quote"]["destination_symbol"] == "DEC"
    expected_first = routing.swap_quote("GRAIN", "DEC", D("50"), live_pools, "2.5")
    assert D(first["quote"]["output"]) == expected_first.output
    assert book.allocated[("PR-BRL-41", "GRAIN")] == D(50)

    assert second["id"] == "sale:sps"
    assert second["depends_on"] == "sale:dec"
    assert second["quantity"] == "0"
    assert second["status"] == "ready"
    assert second["quote"]["estimate"] is True
    assert second["quote"]["source_symbol"] == "DEC" and second["quote"]["destination_symbol"] == "SPS"
    expected_second = routing.swap_quote("DEC", "SPS", expected_first.output, live_pools, "2.5")
    assert D(second["quote"]["output"]) == expected_second.output
    # B allocates NOTHING in the ledger at planning — its DEC does not exist yet.
    assert (routing.INVENTORY, "DEC") not in book.allocated


def test_resource_to_resource_sale_stays_one_step_even_with_an_sps_pool_present():
    live_pools = pools_with_sps()
    book = ledger()
    book.balances[("PR-BRL-65", "WOOD")] = D(0)
    to_wood = {"id": "two", "kind": "sell", "source_region": "PR-BRL-41", "symbol": "GRAIN",
               "destination_region": "PR-BRL-65", "destination_symbol": "WOOD",
               "quantity_mode": "percent", "quantity": "50"}
    steps = routing.plan_after_harvest(book, [to_wood], live_pools, "2.5")
    assert len(steps) == 1
    assert "two_step" not in steps[0]
    assert steps[0]["quote"]["destination_symbol"] == "WOOD"


def test_two_step_second_leg_reviewed_form_builds_with_its_actual_input():
    step = {
        "kind": "sell", "source_region": "inventory", "destination_region": "inventory",
        "quote": {"source_symbol": "DEC", "destination_symbol": "SPS",
                  "input": "123.456", "output": "1.234", "external": True},
    }
    payload = operations.build("player0005", step, "2.5")["payload"]
    assert payload["resource_symbol"] == "SPS"
    assert payload["dec_amount"] == 123.456
    assert payload["shares_out"] == 1.234
    assert "from_symbol" not in payload


# handoff accepts pool drift within the reviewed slippage

def _ir6860_record(root: Path, step: dict, slippage: str = "2.5") -> None:
    record = {
        "run_id": "a" * 32, "player": "player0005", "created": 1.0, "updated": 1.0,
        "phase": "after", "state": "ready",
        "scope": {"kind": "regions", "region_uids": ["PR-BRL-41"]},
        "steps": [step], "rules": [], "reserves": [], "slippage": slippage,
        "harvest_attempt_ids": [],
    }
    with runs.locked(root, "player0005") as folder:
        runs.write(folder, record)


def _ir6860_step(quote: dict, slippage: str = "2.5") -> dict:
    step = {"step_id": "d" * 32, "state": "reviewed", "kind": "sell",
            "source_region": "PR-BRL-41", "symbol": "WOOD", "quantity": "100.000",
            "slippage": slippage, "quote": dict(quote)}
    reviewed = operations.build("player0005", step, slippage)
    step["review"] = {"payload": reviewed["payload"], "operations": reviewed["operations"],
                       "authority": "Posting", "created": time.time(), "digest": "reviewed-digest"}
    return step


def _ir6860_client(tmp_path: Path, monkeypatch, fresh_quote: dict):
    fake_context = {"public": {"blocked": False}, "ledger": None, "pools": None}
    monkeypatch.setattr(routing_routes, "_context", lambda request, player: fake_context)
    monkeypatch.setattr(routing_routes, "_require_posting_contract", lambda: None)

    def fake_fresh_step(request, player, record, member, context=None):
        fresh = {"step_id": member["step_id"], "state": member["state"], "kind": "sell",
                 "source_region": member["source_region"], "symbol": member["symbol"],
                 "quantity": member["quantity"], "slippage": member["slippage"],
                 "quote": dict(fresh_quote)}
        return fresh, {"observed_at": "x"}

    monkeypatch.setattr(routing_routes, "_fresh_step", fake_fresh_step)
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005", harvest_routing_signing_enabled=True)
    return TestClient(create_app(config))


def test_handoff_accepts_output_drift_inside_reviewed_slippage(tmp_path: Path, monkeypatch):
    reviewed_quote = {"source_symbol": "WOOD", "destination_symbol": "DEC",
                       "input": "100.000", "output": "50.000", "minimum": "48.750",
                       "intermediate_dec": "0", "external": False}
    # 2% drop in the AMM output — inside the reviewed 2.5% max_slippage.
    fresh_quote = dict(reviewed_quote, output="49.000")
    step = _ir6860_step(reviewed_quote)
    _ir6860_record(tmp_path / ".ceres", step)
    client = _ir6860_client(tmp_path, monkeypatch, fresh_quote)

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})

    assert response.status_code == 200, response.text
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    saved_step = saved["steps"][0]
    assert saved_step["state"] == "awaiting_wallet"
    # The wallet is handed the FRESH payload, not the stale reviewed one.
    assert saved_step["review"]["payload"]["shares_out"] == 49.0
    assert response.json()["operations"] == saved_step["review"]["operations"]


def test_close_endpoint_marks_the_run_closed_and_refuses_a_later_handoff(tmp_path: Path, monkeypatch, caplog):
    quote = {"source_symbol": "WOOD", "destination_symbol": "DEC",
             "input": "100.000", "output": "50.000", "minimum": "48.750",
             "intermediate_dec": "0", "external": False}
    _ir6860_record(tmp_path / ".ceres", _ir6860_step(quote))
    client = _ir6860_client(tmp_path, monkeypatch, quote)

    with caplog.at_level("ERROR"):
        closed = client.post("/api/harvest/routing/stop?player=player0005", json={"run_id": "a" * 32})

    assert closed.status_code == 200, closed.text
    assert closed.json()["state"] == "stopped"
    assert closed.json()["routing_closed"] is True
    assert closed.json()["failure"]["code"] == "CERES_ROUTING_INTERRUPTED"
    assert any("CERES_ROUTING_INTERRUPTED" in line for line in caplog.messages)

    handoff = client.post("/api/harvest/routing/handoff?player=player0005",
                          json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})
    assert handoff.status_code == 409, handoff.text
    assert handoff.json()["detail"] == "This immediate routing run has ended"


def test_handoff_rejects_output_drift_beyond_reviewed_slippage(tmp_path: Path, monkeypatch):
    reviewed_quote = {"source_symbol": "WOOD", "destination_symbol": "DEC",
                       "input": "100.000", "output": "50.000", "minimum": "48.750",
                       "intermediate_dec": "0", "external": False}
    # 6% drop — beyond the reviewed 2.5% max_slippage.
    fresh_quote = dict(reviewed_quote, output="47.000")
    step = _ir6860_step(reviewed_quote)
    _ir6860_record(tmp_path / ".ceres", step)
    client = _ir6860_client(tmp_path, monkeypatch, fresh_quote)

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Pool amounts changed. Review the new quote before signing"
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    assert saved["steps"][0]["state"] == "reviewed"


def test_handoff_rejects_a_changed_committed_amount_even_with_unchanged_output(tmp_path: Path, monkeypatch):
    reviewed_quote = {"source_symbol": "WOOD", "destination_symbol": "DEC",
                       "input": "100.000", "output": "50.000", "minimum": "48.750",
                       "intermediate_dec": "0", "external": False}
    # The player-committed input amount changed; shares_out (output) is untouched.
    fresh_quote = dict(reviewed_quote, input="99.000")
    step = _ir6860_step(reviewed_quote)
    _ir6860_record(tmp_path / ".ceres", step)
    client = _ir6860_client(tmp_path, monkeypatch, fresh_quote)

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Pool amounts changed. Review the new quote before signing"


def _routing_handoff_error_markup() -> dict[str, str | list[str]]:
    from .vm_context import run_in_module_context

    body_js = r"""
(async () => {
  const stepId = 'b'.repeat(32);
  const review = {digest: 'review-digest', member_ids: [stepId], operations: [], authority: 'Posting'};
  const run = {run_id: 'a'.repeat(32), player: 'player0005', state: 'ready', phase: 'after',
    scope: {kind: 'regions', region_uids: ['PR-BRL-41']}, phase_steps: {before: []},
    signatures_total: 1, signed_signatures: 0, confirmed_signatures: 0,
    steps: [{step_id: stepId, state: 'reviewed', tx_id: '', kind: 'sell', source_region: 'PR-BRL-41',
      symbol: 'GRAIN', quote: {source_symbol: 'GRAIN', destination_symbol: 'DEC'}, review}], group_review: review};
  const calls = [];
  walletReady = () => true;
  globalThis.fetch = window.fetch = async (url, options = {}) => {
    const text = String(url);
    calls.push(text);
    if (text.includes('/state?')) return {ok: true, status: 200, async json() {
      return {regions: [], inventory: {}, pools: [], signing_enabled: true, issues: []};
    }};
    if (text.includes('/runs?')) return {ok: true, status: 200, async json() { return {player: 'player0005', runs: [run]}; }};
    if (text.includes('/handoff?')) return {ok: false, status: 409, async json() {
      return {detail: 'Pool amounts changed. Review the new quote before signing'};
    }};
    return {ok: true, status: 200, async json() { return {logged: true}; }};
  };
  const routing = createHarvestRouting({player: 'player0005', scope: run.scope, activeRunId: run.run_id,
    onRender() {}, onBack() {}, onDone() {}, onHarvest() {}, isCurrent: () => true});
  await routing.load();
  await routing.action('sign');
  process.stdout.write(JSON.stringify({html: routing.html(), footer: routing.footerHtml(), calls}));
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
"""
    result = run_in_module_context(body_js, entry=Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js")
    return json.loads(result.stdout)


def test_ui_halts_handoff_errors_in_needs_attention_without_auto_rereview():
    rendered = _routing_handoff_error_markup()
    error = 'Pool amounts changed. Review the new quote before signing'
    assert '<footer class="hm-foot hr-bundle-action">' in rendered["footer"]
    assert rendered["html"].count(error) == 1
    assert rendered["footer"].count(error) == 1
    assert rendered["html"].count('role="alert"') == 1
    assert rendered["footer"].count('role="alert"') == 0
    assert "<h3>Needs attention</h3>" in rendered["html"]
    assert not any("/review?" in call for call in rendered["calls"])


def test_ui_footer_keeps_the_inline_error_and_sticky_contract():
    rendered = _routing_handoff_error_markup()
    assert '<span class="hr-error">Pool amounts changed. Review the new quote before signing</span>' in rendered["footer"]
    css = (Path(__file__).parents[2] / "ceres" / "static" / "ceres.css").read_text()
    assert ".hr-bundle-action .hr-error { flex-basis: 100%; }" in css
    assert "#harvest-modal-overlay .hr-bundle-action { position: sticky; inset-block-end: 0;" in css
    assert "env(safe-area-inset-bottom)" in css


def _routing_footer_markup(step_state: str = "reviewed", queue_state: str | None = None) -> str:
    from tests.ceres.vm_context import run_in_module_context

    tx_id = "f" * 40 if queue_state else ""
    queue_setup = (f"localStorage.setItem('ceres:signed-queue:v1:player0005', "
                   f"JSON.stringify([{{txId: '{tx_id}', account: 'player0005', state: '{queue_state}'}}]));"
                   if queue_state else "")
    body_js = r"""
(async () => {
  const review = {digest: 'review-digest', member_ids: ['b'.repeat(32)], operations: [], authority: 'Posting'};
  const run = {run_id: 'a'.repeat(32), player: 'player0005', state: 'ready', phase: 'after',
    scope: {kind: 'regions', region_uids: ['PR-BRL-41']}, phase_steps: {before: []},
    steps: [{step_id: 'b'.repeat(32), state: '__STEP_STATE__', tx_id: '__TX_ID__', kind: 'sell', source_region: 'PR-BRL-41',
      symbol: 'GRAIN', review}], group_review: review};
  __QUEUE_SETUP__
  globalThis.fetch = window.fetch = async url => ({ok: true, json: async () => String(url).includes('/state?')
    ? {regions: [], inventory: {}, pools: [], signing_enabled: true, issues: []}
    : {player: 'player0005', runs: [run]}});
  const routing = createHarvestRouting({player: 'player0005', scope: run.scope, activeRunId: run.run_id,
    onRender() {}, onBack() {}, onDone() {}, onHarvest() {}, isCurrent: () => true});
  await routing.load();
  process.stdout.write(routing.footerHtml());
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""".replace("__STEP_STATE__", step_state).replace("__TX_ID__", tx_id).replace("__QUEUE_SETUP__", queue_setup)
    return run_in_module_context(body_js, entry=Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js").stdout


@pytest.mark.parametrize("viewport", [{"width": 1024, "height": 640}, {"width": 390, "height": 700}])
def test_routing_status_footer_stays_inside_the_viewport_at_both_scroll_extremes(viewport):

    static = Path(__file__).parents[2] / "ceres" / "static"
    styles = "\n".join((static / name).read_text() for name in ("canonical.css", "buttons.css", "ceres.css"))
    footer = _routing_footer_markup()
    filler = "".join(f'<article class="hr-step"><h3>Routing step {index}</h3><p>Review details</p></article>' for index in range(18))
    page_html = f'''<!doctype html><html><head><meta charset="utf-8"><style>{styles}</style></head><body>
      <div id="harvest-modal-overlay" class="harvest-overlay"><div class="harvest-panel" role="dialog">
      <header class="hm-headr"><h2>Harvest &amp; routing</h2></header>
      <div class="hm-body"><section class="harvest-routing">{filler}</section></div>{footer}</div></div></body></html>'''

    # The shared live browser, not a second Playwright driver (see test_routing_review_caps_real_cards_and_footer_at_56rem).
    with spawn_live_ceres() as (browser, _base_url):
        page = browser.new_page(viewport=viewport)
        try:
            page.set_content(page_html)
            rects = []
            for edge in ("top", "bottom"):
                page.locator(".hm-body").evaluate("(node, edge) => { node.scrollTop = edge === 'top' ? 0 : node.scrollHeight; }", edge)
                rects.append(page.locator(".hr-signing-status").evaluate("node => { const r = node.getBoundingClientRect(); return {top:r.top, bottom:r.bottom, left:r.left, right:r.right}; }"))
        finally:
            page.close()

    for rect in rects:
        assert rect["top"] >= 0 and rect["bottom"] <= viewport["height"]
        assert rect["left"] >= 0 and rect["right"] <= viewport["width"]
    print({"viewport": viewport, "top": rects[0], "bottom": rects[1]})


@pytest.mark.parametrize(("server_state", "label"), [
    ("confirmed", "Done"),
    ("failed", "Needs attention"),
    ("skipped", "Done"),
    ("settling", "Waiting for the game to settle the DEC → SPS swap and prepare the SPS funding transfer."),
])
def test_footer_collapses_internal_states_to_plain_words(server_state, label):
    footer = _routing_footer_markup(server_state, "included")

    assert label in footer


def test_pending_server_status_uses_plain_confirmation_words():
    footer = _routing_footer_markup("pending", "included")

    assert "Confirming with the game" in footer


def test_signing_chain_can_review_an_independent_step_while_the_previous_is_submitted():
    record = {"phase": "after", "steps": [
        _chain_sale("a", "GRAIN", "submitted"),
        _chain_sale("b", "WOOD", "planned"),
    ]}

    assert routing_routes._next_signable_step(record)["step_id"] == "b" * 32


def _chain_sale(identifier: str, symbol: str, state: str = "planned") -> dict:
    return {"step_id": identifier * 32, "state": state, "kind": "sell",
            "quote": {"source_symbol": symbol, "destination_symbol": "DEC", "external": False}}


def test_signing_chain_waits_for_a_dependent_step_and_stops_after_a_refusal():
    dependent = _chain_sale("b", "WOOD") | {"depends_on_step_id": "a" * 32}
    assert routing_routes._next_signable_step({"phase": "after", "steps": [
        _chain_sale("a", "GRAIN", "submitted"), dependent,
    ]}) is None
    assert routing_routes._next_signable_step({"phase": "after", "steps": [
        _chain_sale("a", "GRAIN", "failed"), _chain_sale("c", "WOOD"),
    ]}) is None


@pytest.mark.parametrize("blocker", [
    {"step_id": "a" * 32, "state": "submitted", "kind": "transfer"},
    _chain_sale("a", "GRAIN", "submitted"),
])
def test_signing_chain_does_not_cross_an_unsafe_in_flight_boundary(blocker):
    phase = "before" if blocker.get("kind") == "sell" else "after"
    record = {"phase": phase, "steps": [blocker, _chain_sale("b", "WOOD")]}

    assert routing_routes._next_signable_step(record) is None


def test_signing_chain_never_reuses_an_in_flight_pool():
    record = {"phase": "after", "steps": [
        _chain_sale("a", "GRAIN", "submitted"),
        _chain_sale("b", "GRAIN"),
        _chain_sale("c", "WOOD"),
    ]}

    assert routing_routes._next_signable_step(record) is None


def test_signing_chain_group_stops_before_a_pool_used_by_an_earlier_envelope():
    first = _chain_sale("b", "WOOD")
    record = {"phase": "after", "steps": [
        _chain_sale("a", "GRAIN", "submitted"),
        first,
        _chain_sale("c", "GRAIN"),
        _chain_sale("d", "STONE"),
    ]}

    assert routing_routes._next_signable_step(record) is first
    assert routing_routes._review_members(record, first) == [first]


def test_cancel_remaining_uses_cancel_and_modal_close_uses_account_bound_stop():
    routing_source = (Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js").read_text()
    modal_source = (Path(__file__).parents[2] / "ceres" / "static" / "harvest-modal.js").read_text()
    assert "name === 'cancel') { S.run = await api('cancel', { run_id: S.run.run_id })" in routing_source
    assert "fetch('/api/harvest/routing/stop?player=' + encodeURIComponent(account)" in modal_source
    assert "body: JSON.stringify({ run_id: runId }), keepalive: true" in modal_source
    assert "S.preview = null;" in routing_source
    assert "S.run = null;" in routing_source


# round 2: add_liquidity dec_amount tolerance (ceiling-only)

def _ir6860_liquidity_step(deposit: dict) -> dict:
    step = {"step_id": "d" * 32, "state": "reviewed", "kind": "liquidity",
            "source_region": "PR-BRL-41", "symbol": "WOOD", "quantity": "100.000",
            "deposit": dict(deposit)}
    reviewed = operations.build("player0005", step, "2.5")
    step["review"] = {"payload": reviewed["payload"], "operations": reviewed["operations"],
                       "authority": "Posting", "created": time.time(), "digest": "reviewed-digest"}
    return step


def _ir6860_liquidity_client(tmp_path: Path, monkeypatch, fresh_deposit: dict):
    fake_context = {"public": {"blocked": False}, "ledger": None, "pools": None}
    monkeypatch.setattr(routing_routes, "_context", lambda request, player: fake_context)
    monkeypatch.setattr(routing_routes, "_require_posting_contract", lambda: None)

    def fake_fresh_step(request, player, record, member, context=None):
        fresh = {"step_id": member["step_id"], "state": member["state"], "kind": "liquidity",
                 "source_region": member["source_region"], "symbol": member["symbol"],
                 "quantity": member["quantity"], "deposit": dict(fresh_deposit)}
        return fresh, {"observed_at": "x"}

    monkeypatch.setattr(routing_routes, "_fresh_step", fake_fresh_step)
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005", harvest_routing_signing_enabled=True)
    return TestClient(create_app(config))


_LIQUIDITY_REVIEWED = {"symbol": "WOOD", "pool_id": 2, "resource_amount": "100.000",
                       "dec_amount": "50.000", "estimated_shares": "70", "external": False}


def test_handoff_accepts_liquidity_dec_amount_rise_inside_run_slippage(tmp_path: Path, monkeypatch):
    # +2% rise, inside the run's 2.5% slippage — accepted, and the FRESH dec_amount is signed.
    fresh_deposit = dict(_LIQUIDITY_REVIEWED, dec_amount="51.000")
    step = _ir6860_liquidity_step(_LIQUIDITY_REVIEWED)
    _ir6860_record(tmp_path / ".ceres", step)
    client = _ir6860_liquidity_client(tmp_path, monkeypatch, fresh_deposit)

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})

    assert response.status_code == 200, response.text
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    payload = saved["steps"][0]["review"]["payload"]
    assert payload["dec_amount"] == 51.0
    # The player's committed resource input never drifts.
    assert payload["resource_amount"] == 100.0


def test_handoff_rejects_liquidity_dec_amount_rise_beyond_run_slippage(tmp_path: Path, monkeypatch):
    # +5% rise, beyond the run's 2.5% slippage — rejected.
    fresh_deposit = dict(_LIQUIDITY_REVIEWED, dec_amount="52.500")
    step = _ir6860_liquidity_step(_LIQUIDITY_REVIEWED)
    _ir6860_record(tmp_path / ".ceres", step)
    client = _ir6860_liquidity_client(tmp_path, monkeypatch, fresh_deposit)

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Pool amounts changed. Review the new quote before signing"


def test_handoff_accepts_any_liquidity_dec_amount_fall_no_upper_bound_needed(tmp_path: Path, monkeypatch):
    # A FALL in what the player pays is always favorable and never bounded, even a large one.
    fresh_deposit = dict(_LIQUIDITY_REVIEWED, dec_amount="30.000")
    step = _ir6860_liquidity_step(_LIQUIDITY_REVIEWED)
    _ir6860_record(tmp_path / ".ceres", step)
    client = _ir6860_liquidity_client(tmp_path, monkeypatch, fresh_deposit)

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})

    assert response.status_code == 200, response.text


def test_handoff_refuses_liquidity_dec_source_not_carried_by_review(tmp_path: Path, monkeypatch):
    step = _ir6860_liquidity_step(_LIQUIDITY_REVIEWED)
    step["dec_source"] = "wallet"
    step["review"]["step"] = {**{key: value for key, value in step.items() if key != "review"},
                                "dec_source": "swap_half"}
    _ir6860_record(tmp_path / ".ceres", step)
    client = _ir6860_liquidity_client(tmp_path, monkeypatch, _LIQUIDITY_REVIEWED)

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "The DEC source changed after review. Review the liquidity deposit again"


# round 2: signing a drifted swap never lowers the reviewed floor

def test_handoff_preserves_the_reviewed_floor_when_signing_a_drifted_swap(tmp_path: Path, monkeypatch):
    reviewed_quote = {"source_symbol": "WOOD", "destination_symbol": "DEC",
                       "input": "100.000", "output": "50.000", "minimum": "48.750",
                       "intermediate_dec": "0", "external": False}
    fresh_quote = dict(reviewed_quote, output="49.000")  # 2%, inside 2.5% slippage
    step = _ir6860_step(reviewed_quote)
    _ir6860_record(tmp_path / ".ceres", step)
    client = _ir6860_client(tmp_path, monkeypatch, fresh_quote)

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})

    assert response.status_code == 200, response.text
    signed = json.loads(response.json()["operations"][0][1]["json"])
    reviewed_floor = D("50.000") * (D(1) - D("2.5") / D(100))
    fresh_floor = D(str(signed["shares_out"])) * (D(1) - D(str(signed["max_slippage"])) / D(100))
    assert fresh_floor >= reviewed_floor
    # The tightened slippage never exceeds what was actually reviewed.
    assert D(str(signed["max_slippage"])) <= D("2.5")


# round 2: a 3-member group still rejects as one unit

def _ir6860_group_member(step_id: str, symbol: str, output: str) -> dict:
    quote = {"source_symbol": symbol, "destination_symbol": "DEC", "input": "100.000",
             "output": output, "minimum": routing.text(D(output) * D("975") / D(1000)),
             "intermediate_dec": "0", "external": False}
    step = {"step_id": step_id, "state": "reviewed", "kind": "sell",
            "source_region": "PR-BRL-41", "symbol": symbol, "quantity": "100.000",
            "slippage": "2.5", "quote": quote}
    reviewed = operations.build("player0005", step, "2.5")
    step["review"] = {"payload": reviewed["payload"], "operations": reviewed["operations"], "authority": "Posting"}
    return step


def test_handoff_group_rejects_as_one_unit_when_any_member_drifts_beyond_tolerance(tmp_path: Path, monkeypatch):
    m1 = _ir6860_group_member("b" * 32, "WOOD", "50.000")
    m2 = _ir6860_group_member("c" * 32, "STONE", "40.000")
    m3 = _ir6860_group_member("d" * 32, "IRON", "30.000")
    group_review = {"member_ids": [m1["step_id"], m2["step_id"], m3["step_id"]],
                     "digest": "group-digest", "created": time.time(), "authority": "Posting",
                     "operations": m1["review"]["operations"] + m2["review"]["operations"] + m3["review"]["operations"]}
    record = {"run_id": "a" * 32, "player": "player0005", "created": 1.0, "updated": 1.0,
              "phase": "after", "state": "ready",
              "scope": {"kind": "regions", "region_uids": ["PR-BRL-41"]},
              "steps": [m1, m2, m3], "rules": [], "reserves": [], "slippage": "2.5",
              "harvest_attempt_ids": [], "group_review": group_review}
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        runs.write(folder, record)

    fake_context = {"public": {"blocked": False}, "ledger": None, "pools": None}
    monkeypatch.setattr(routing_routes, "_context", lambda request, player: fake_context)
    monkeypatch.setattr(routing_routes, "_require_posting_contract", lambda: None)
    # WOOD -0.2% (fine), STONE -10% (beyond 2.5%), IRON improves — mixed drift, one bad member.
    fresh_outputs = {"WOOD": "49.900", "STONE": "36.000", "IRON": "30.500"}

    def fake_fresh_step(request, player, record, member, context=None):
        fresh = {"step_id": member["step_id"], "state": member["state"], "kind": "sell",
                 "source_region": member["source_region"], "symbol": member["symbol"],
                 "quantity": member["quantity"], "slippage": member["slippage"],
                 "quote": dict(member["quote"], output=fresh_outputs[member["symbol"]])}
        return fresh, {"observed_at": "x"}

    monkeypatch.setattr(routing_routes, "_fresh_step", fake_fresh_step)
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005", harvest_routing_signing_enabled=True)
    client = TestClient(create_app(config))

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "group-digest"})

    assert response.status_code == 409, response.text
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    assert [s["state"] for s in saved["steps"]] == ["reviewed", "reviewed", "reviewed"]


def _reviewed_sale_group(root: Path, *, age: float = 0):
    members = [
        _ir6860_group_member("b" * 32, "WOOD", "50.000"),
        _ir6860_group_member("c" * 32, "STONE", "40.000"),
        _ir6860_group_member("d" * 32, "IRON", "30.000"),
    ]
    created = time.time() - age
    group_review = {"member_ids": [member["step_id"] for member in members],
                    "digest": "group-digest", "created": created, "authority": "Posting",
                    "operations": [op for member in members for op in member["review"]["operations"]],
                    "step": members[0]}
    record = {"run_id": "a" * 32, "player": "player0005", "created": 1.0, "updated": 1.0,
              "phase": "after", "state": "ready",
              "scope": {"kind": "regions", "region_uids": ["PR-BRL-41"]},
              "steps": members, "rules": [], "reserves": [], "slippage": "2.5",
              "harvest_attempt_ids": [], "group_review": group_review}
    with runs.locked(root, "player0005") as folder:
        runs.write(folder, record)
    return members, group_review


def _sale_group_client(tmp_path: Path, monkeypatch, fresh_outputs: dict[str, str]):
    monkeypatch.setattr(routing_routes, "_context", lambda request, player: {
        "public": {"blocked": False}, "ledger": None, "pools": None})
    monkeypatch.setattr(routing_routes, "_require_posting_contract", lambda: None)

    def fake_fresh_step(request, player, record, member, context=None):
        fresh = {"step_id": member["step_id"], "state": member["state"], "kind": "sell",
                 "source_region": member["source_region"], "symbol": member["symbol"],
                 "quantity": member["quantity"], "slippage": member["slippage"],
                 "quote": dict(member["quote"], output=fresh_outputs[member["symbol"]])}
        return fresh, {"observed_at": "fresh-observation"}

    monkeypatch.setattr(routing_routes, "_fresh_step", fake_fresh_step)
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005", harvest_routing_signing_enabled=True)
    return TestClient(create_app(config))


def test_expired_sale_group_inside_tolerance_refreshes_and_hands_off_atomically(tmp_path: Path, monkeypatch):
    members, old_review = _reviewed_sale_group(tmp_path / ".ceres", age=61)
    client = _sale_group_client(tmp_path, monkeypatch, {"WOOD": "49.900", "STONE": "40.100", "IRON": "30.000"})

    response = client.post("/api/harvest/routing/handoff?player=player0005", json={
        "run_id": "a" * 32, "review_digest": "group-digest",
        "member_ids": [member["step_id"] for member in members],
    })

    assert response.status_code == 200, response.text
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    assert [member["state"] for member in saved["steps"]] == ["awaiting_wallet"] * 3
    assert saved["group_review"]["created"] > old_review["created"]
    assert saved["group_review"]["digest"] != "group-digest"
    assert all(member["review"]["created"] == saved["group_review"]["created"] for member in saved["steps"])
    assert len(response.json()["operations"]) == 3


def test_expired_sale_group_beyond_tolerance_rejects_without_partial_refresh(tmp_path: Path, monkeypatch):
    members, old_review = _reviewed_sale_group(tmp_path / ".ceres", age=61)
    client = _sale_group_client(tmp_path, monkeypatch, {"WOOD": "49.900", "STONE": "36.000", "IRON": "30.500"})

    response = client.post("/api/harvest/routing/handoff?player=player0005", json={
        "run_id": "a" * 32, "review_digest": "group-digest",
        "member_ids": [member["step_id"] for member in members],
    })

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Pool amounts changed. Review the new quote before signing"
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    assert [member["state"] for member in saved["steps"]] == ["reviewed"] * 3
    assert saved["group_review"] == old_review
    assert all("created" not in member["review"] for member in saved["steps"])


def test_sale_group_older_than_hard_ceiling_keeps_expiry_rejection(tmp_path: Path, monkeypatch):
    members, _old_review = _reviewed_sale_group(tmp_path / ".ceres", age=901)
    client = _sale_group_client(tmp_path, monkeypatch, {"WOOD": "50.000", "STONE": "40.000", "IRON": "30.000"})

    response = client.post("/api/harvest/routing/handoff?player=player0005", json={
        "run_id": "a" * 32, "review_digest": "group-digest",
        "member_ids": [member["step_id"] for member in members],
    })

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "The pool quote expired. Review fresh amounts before signing"


def test_handoff_subset_moves_only_selected_members_and_excludes_the_rest(tmp_path: Path, monkeypatch):
    members, _old_review = _reviewed_sale_group(tmp_path / ".ceres")
    client = _sale_group_client(tmp_path, monkeypatch, {"WOOD": "50.000", "STONE": "40.000", "IRON": "30.000"})
    selected = [members[1]["step_id"], members[2]["step_id"]]

    response = client.post("/api/harvest/routing/handoff?player=player0005", json={
        "run_id": "a" * 32, "review_digest": "group-digest", "member_ids": selected,
    })

    assert response.status_code == 200, response.text
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    by_id = {member["step_id"]: member for member in saved["steps"]}
    assert [member["step_id"] for member in saved["steps"][:2]] == selected
    assert [by_id[identifier]["state"] for identifier in selected] == ["awaiting_wallet", "awaiting_wallet"]
    assert by_id[members[0]["step_id"]]["state"] == "skipped"
    assert by_id[members[0]["step_id"]]["skip_metadata"]["reason"] == "user_excluded_from_reviewed_group"
    assert saved["group_review"]["member_ids"] == selected
    assert runs.next_step(saved)["step_id"] == selected[0]
    assert len(response.json()["operations"]) == 2


def test_handoff_empty_subset_skips_the_whole_reviewed_group(tmp_path: Path, monkeypatch):
    members, _old_review = _reviewed_sale_group(tmp_path / ".ceres")
    client = _sale_group_client(tmp_path, monkeypatch, {"WOOD": "50.000", "STONE": "40.000", "IRON": "30.000"})

    response = client.post("/api/harvest/routing/handoff?player=player0005", json={
        "run_id": "a" * 32, "review_digest": "group-digest", "member_ids": [],
    })

    assert response.status_code == 200, response.text
    assert response.json()["skipped"] is True
    saved = response.json()["run"]
    assert all(member["state"] == "skipped" for member in saved["steps"])
    assert all(member["skip_metadata"]["reason"] == "user_excluded_from_reviewed_group"
               for member in saved["steps"])
    assert saved["signatures_total"] == 0


def test_handoff_empty_subset_cannot_skip_a_non_optional_operation(tmp_path: Path, monkeypatch):
    members, _old_review = _reviewed_sale_group(tmp_path / ".ceres")
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        record = runs.read(folder, "a" * 32, "player0005")
        record["steps"][0]["two_step"] = {"role": "first", "final_symbol": "SPS"}
        runs.write(folder, record)
    client = _sale_group_client(tmp_path, monkeypatch, {"WOOD": "50.000", "STONE": "40.000", "IRON": "30.000"})

    response = client.post("/api/harvest/routing/handoff?player=player0005", json={
        "run_id": "a" * 32, "review_digest": "group-digest", "member_ids": [],
    })

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Only optional reviewed sales can be excluded"
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    assert [member["state"] for member in saved["steps"]] == ["reviewed"] * len(members)


@pytest.mark.parametrize("member_ids", [["f" * 32]])
def test_handoff_refuses_outside_member_selection(tmp_path: Path, monkeypatch, member_ids):
    _members, _old_review = _reviewed_sale_group(tmp_path / ".ceres")
    client = _sale_group_client(tmp_path, monkeypatch, {"WOOD": "50.000", "STONE": "40.000", "IRON": "30.000"})

    response = client.post("/api/harvest/routing/handoff?player=player0005", json={
        "run_id": "a" * 32, "review_digest": "group-digest", "member_ids": member_ids,
    })

    assert response.status_code == 409, response.text
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    assert [member["state"] for member in saved["steps"]] == ["reviewed"] * 3


# (1): a run reviewed at the game's 0.5% slippage floor has no room to shrink

def test_handoff_at_floor_slippage_rejects_any_downward_drift(tmp_path: Path, monkeypatch):
    reviewed_quote = {"source_symbol": "WOOD", "destination_symbol": "DEC",
                       "input": "100.000", "output": "50.000", "minimum": "49.750",
                       "intermediate_dec": "0", "external": False}
    step = _ir6860_step(reviewed_quote, slippage="0.5")
    _ir6860_record(tmp_path / ".ceres", step, slippage="0.5")
    fresh_quote = dict(reviewed_quote, output="49.999")  # any fall at all, even tiny
    client = _ir6860_client(tmp_path, monkeypatch, fresh_quote)

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "Pool amounts fell and the 0.5% minimum leaves no room. Review the new quote before signing")
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    assert saved["steps"][0]["state"] == "reviewed"


def test_handoff_at_floor_slippage_accepts_upward_or_equal_drift(tmp_path: Path, monkeypatch):
    reviewed_quote = {"source_symbol": "WOOD", "destination_symbol": "DEC",
                       "input": "100.000", "output": "50.000", "minimum": "49.750",
                       "intermediate_dec": "0", "external": False}
    step = _ir6860_step(reviewed_quote, slippage="0.5")
    _ir6860_record(tmp_path / ".ceres", step, slippage="0.5")
    fresh_quote = dict(reviewed_quote, output="50.000")  # unchanged — equal drift accepted
    client = _ir6860_client(tmp_path, monkeypatch, fresh_quote)

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})

    assert response.status_code == 200, response.text
    signed = json.loads(response.json()["operations"][0][1]["json"])
    assert signed["max_slippage"] == 0.5
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    assert saved["steps"][0]["state"] == "awaiting_wallet"


# (2): the refreshed "Minimum received" reflects the slippage actually signed

def test_handoff_refreshed_minimum_reflects_the_preserved_floor(tmp_path: Path, monkeypatch):
    reviewed_quote = {"source_symbol": "WOOD", "destination_symbol": "DEC",
                       "input": "100.000", "output": "50.000", "minimum": "48.750",
                       "intermediate_dec": "0", "external": False}
    fresh_quote = dict(reviewed_quote, output="49.000")  # 2%, inside 2.5% slippage — triggers a shrink
    step = _ir6860_step(reviewed_quote)
    _ir6860_record(tmp_path / ".ceres", step)
    client = _ir6860_client(tmp_path, monkeypatch, fresh_quote)

    response = client.post("/api/harvest/routing/handoff?player=player0005",
                           json={"run_id": "a" * 32, "review_digest": "reviewed-digest"})

    assert response.status_code == 200, response.text
    signed = json.loads(response.json()["operations"][0][1]["json"])
    with runs.locked(tmp_path / ".ceres", "player0005") as folder:
        saved = runs.read(folder, "a" * 32, "player0005")
    saved_quote = saved["steps"][0]["review"]["step"]["quote"]
    # The step card's "Minimum received" must be recomputed from the SHRUNK slippage
    # actually signed, not the stale reviewed-tolerance figure _fresh_step first computed.
    expected_minimum = routing.floor(D("49.000") * (D(1) - D(str(signed["max_slippage"])) / D(100)))
    assert D(saved_quote["minimum"]) == expected_minimum
    assert D(saved_quote["minimum"]) != D("48.750")


# (3): the LP deposit review card shows the up-to-slippage maximum DEC

def test_lp_deposit_card_shows_up_to_slippage_dec_line():
    source = (Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js").read_text()
    # Hidden once the deposit is confirmed, like the sibling price-impact/external warnings.
    assert "detail.deposit && !confirmed && S.slippage != null" in source
    assert "Up to ${fmt2(" in source
    # Uses the same slippage source the preview's own "Minimum received" reads before a run
    # exists (S.slippage, not S.run.slippage), so the line is present and identical in
    # preview and in a started run.
    assert "Number(detail.deposit.dec_amount) * (1 + Number(S.slippage) / 100)" in source


def test_signature_plan_counts_full_three_sale_inventory_and_never_grows_after_skip():
    plain = {"step_id": "1" * 32, "state": "planned", "kind": "sell", "symbol": "GRAIN",
             "quote": {"source_symbol": "GRAIN", "destination_symbol": "DEC", "external": False}}
    first = {"step_id": "2" * 32, "state": "planned", "kind": "sell", "symbol": "WOOD",
             "quote": {"source_symbol": "WOOD", "destination_symbol": "DEC", "external": False},
             "two_step": {"role": "first", "final_symbol": "SPS"}}
    second = {"step_id": "3" * 32, "state": "planned", "kind": "sell", "symbol": "DEC",
              "quote": {"source_symbol": "DEC", "destination_symbol": "SPS", "external": True},
              "depends_on_step_id": first["step_id"], "two_step": {"role": "second"}}
    liquidity = {"step_id": "4" * 32, "state": "planned", "kind": "liquidity", "symbol": "STONE",
                 "deposit": {"symbol": "STONE", "external": False}}
    record = {"phase": "after", "steps": [plain, first, second, liquidity], "phase_steps": {"before": []}}

    assert runs.signature_plan(record)["total"] == 5
    with_harvest = {**record, "harvest_preview": {"chunks": [["PR-BRL-41"]]}}
    assert runs.signature_plan(with_harvest)["total"] == 6

    liquidity["state"] = "skipped"
    shrunk = runs.expose_signature_plan(record)
    assert shrunk["signatures_total"] == 4
    liquidity["state"] = "planned"
    assert runs.signature_plan(record)["total"] == 4


def test_signature_plan_projects_the_full_pre_harvest_run_without_phase_growth():
    rules = [
        {"id": "plain", "kind": "sell", "symbol": "GRAIN", "destination_symbol": "DEC"},
        {"id": "sps", "kind": "sell", "symbol": "WOOD", "destination_symbol": "SPS"},
        {"id": "lp", "kind": "liquidity", "symbol": "STONE"},
    ]
    before = {"phase": "before", "steps": [], "rules": rules, "projected_harvest_chunks": 1}
    initial = runs.expose_signature_plan(before)
    assert initial["signatures_total"] == 6

    after = {"phase": "after", "steps": [], "rules": rules,
             "phase_steps": {"before": []}, "harvest_preview": {"chunks": [["PR-BRL-41"]]},
             "signatures_total": initial["signatures_total"]}
    plain = {"step_id": "1" * 32, "state": "planned", "kind": "sell",
             "quote": {"source_symbol": "GRAIN", "destination_symbol": "DEC", "external": False}}
    first = {"step_id": "2" * 32, "state": "planned", "kind": "sell",
             "quote": {"source_symbol": "WOOD", "destination_symbol": "DEC", "external": False},
             "two_step": {"role": "first", "final_symbol": "SPS"}}
    second = {"step_id": "3" * 32, "state": "planned", "kind": "sell", "depends_on_step_id": "2" * 32,
              "quote": {"source_symbol": "DEC", "destination_symbol": "SPS", "external": True},
              "two_step": {"role": "second"}}
    liquidity = {"step_id": "4" * 32, "state": "planned", "kind": "liquidity",
                 "deposit": {"symbol": "STONE", "external": False}}
    after["steps"] = [plain, first, second, liquidity]
    assert runs.expose_signature_plan(after)["signatures_total"] == 6


def test_lp_dec_source_has_exactly_two_button_choices_and_no_harvest_branch():
    source = (Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js").read_text()
    server = (Path(__file__).parents[2] / "ceres" / "harvest_routing.py").read_text()
    routes = (Path(__file__).parents[2] / "ceres" / "routes_harvest_routing.py").read_text()
    assert source.count('data-hr-dec-source="${value}"') == 2
    assert source.count("decSourceChoice(rule, 'swap_half'") == 1
    assert source.count("decSourceChoice(rule, 'wallet'") == 1
    assert source.count('class="ceres-btn ceres-btn--choice hr-dec-source-choice"') == 2
    assert source.count('data-control-wrap-labels') >= 2
    assert 'type="radio"' not in source
    assert "choice('harvest'" not in source
    assert "DEC from this harvest" not in source
    assert "aria-pressed=" in source
    assert "tipAttrs({ title, body: tooltip })" in source
    assert 'DEC_SOURCES = frozenset({"wallet", "swap_half"})' in server
    assert 'dec_source == "harvest"' not in server
    assert 'harvest_only=dec_source' not in routes
    assert "dec_source = 'harvest'" not in routes
    assert "funding.extra_signatures" in source
    assert "S.run.signatures_total ?? S.run.total_signatures" in source


def test_legacy_dec_source_is_migrated_to_wallet_in_durable_plans():
    record = {"rules": [{"kind": "liquidity", "dec_source": "harvest"}],
              "steps": [{"dec_source": "harvest", "review": {"step": {"dec_source": "harvest"}}}]}
    runs.migrate_legacy_dec_sources(record)
    assert record["rules"][0]["dec_source"] == "wallet"
    assert record["steps"][0]["dec_source"] == "wallet"
    assert record["steps"][0]["review"]["step"]["dec_source"] == "wallet"


def test_old_lp_rule_default_is_saved_only_after_the_review_card_is_shown():
    source = (Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js").read_text()
    load_start = source.index("async function prepareReceiptRouting()")
    load_end = source.index("function harvestFinished()", load_start)
    assert "S.preview = preview" in source[load_start:load_end]
    assert "api('run'" not in source[load_start:load_end]
    start_action = source.index("else if (name === 'start')")
    assert "S.preview.rules" in source[start_action:start_action + 700]
    assert "save();" in source[start_action:start_action + 700]


def test_routing_review_defaults_all_sales_selected_and_updates_counts_when_deselected():
    from .vm_context import run_in_module_context

    body_js = """
(async () => {
  const ids = ['b'.repeat(32), 'c'.repeat(32), 'd'.repeat(32)];
  const steps = ids.map((step_id, index) => ({
    step_id, state: 'reviewed', kind: 'sell', source_region: 'PR-BRL-41', symbol: ['WOOD', 'STONE', 'IRON'][index],
    quote: { source_symbol: ['WOOD', 'STONE', 'IRON'][index], destination_symbol: 'DEC', input: '10.000', output: '5.000' },
    review: { step: { step_id }, payload: {}, operations: [] },
  }));
  const group_review = { member_ids: ids, digest: 'group-digest', step: steps[0], operations: [], authority: 'Posting' };
  const run = { run_id: 'a'.repeat(32), phase: 'after', state: 'ready', scope: { kind: 'regions', region_uids: ['PR-BRL-41'] },
    signatures_total: 1, signed_signatures: 0, confirmed_signatures: 0, steps, phase_steps: { before: [] }, group_review };
  globalThis.__routingResponses = [
    { regions: [{ region_uid: 'PR-BRL-41', region_number: 41 }], inventory: {}, pools: [], issues: [], signing_enabled: true },
    { runs: [run] },
  ];
  walletReady = () => true;
  const routing = createHarvestRouting({ player: 'player0005', scope: run.scope, activeRunId: run.run_id,
    onRender() {}, onBack() {}, onDone() {}, onHarvest() {}, isCurrent() { return true; } });
  await routing.load();
  let html = routing.html();
  let footer = routing.footerHtml();
  if ((html.match(/data-hr-member=/g) || []).length !== 3) throw new Error('expected three sale selectors: ' + html);
  if ((html.match(/data-hr-member=[^>]+ checked/g) || []).length !== 3) throw new Error('all sale selectors must default checked: ' + html);
  if (!footer.includes('Sign routing · 1 approvals')) throw new Error('plan approval count is wrong: ' + footer);

  routing.change({ dataset: { hrMember: ids[1] }, checked: false });
  footer = routing.footerHtml();
  if (!footer.includes('Sign routing · 1 approvals')) throw new Error('deselecting inside one bundle changed its approval count: ' + footer);

  routing.change({ dataset: { hrMember: ids[0] }, checked: false });
  routing.change({ dataset: { hrMember: ids[2] }, checked: false });
  footer = routing.footerHtml();
  if (!footer.includes('Sign routing · 0 approvals') || !/data-hr="sign"[^>]* disabled/.test(footer)) {
    throw new Error('empty optional group must show zero approvals and disable signing: ' + footer);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    extra_context_js = """
context.fetch = async () => ({ ok: true, status: 200, async json() { return context.__routingResponses.shift(); } });
context.window.fetch = context.fetch;
"""
    run_in_module_context(
        body_js,
        entry=Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js",
        extra_context_js=extra_context_js,
    )


def test_routing_review_count_includes_sps_legs_funding_and_harvest_chunks():
    from .vm_context import run_in_module_context

    body_js = """
(async () => {
  const optionalId = 'b'.repeat(32);
  const firstId = 'c'.repeat(32);
  const secondId = 'd'.repeat(32);
  const steps = [
    {step_id: optionalId, state: 'reviewed', kind: 'sell', source_region: 'PR-BRL-41', symbol: 'WOOD',
      quote: {source_symbol: 'WOOD', destination_symbol: 'DEC', input: '10.000', output: '5.000'},
      review: {step: {step_id: optionalId}, payload: {}, operations: []}},
    {step_id: firstId, state: 'reviewed', kind: 'sell', source_region: 'PR-BRL-41', symbol: 'WOOD',
      quote: {source_symbol: 'WOOD', destination_symbol: 'SPS', external: true},
      two_step: {role: 'first', final_symbol: 'SPS'}, review: {step: {step_id: firstId}, payload: {}, operations: []}},
    {step_id: secondId, state: 'planned', kind: 'sell', source_region: 'PR-BRL-41', symbol: 'DEC',
      quote: {source_symbol: 'DEC', destination_symbol: 'SPS', external: true}, depends_on_step_id: firstId,
      two_step: {role: 'second'}},
  ];
  const group_review = {member_ids: [optionalId], digest: 'group-digest', step: steps[0], operations: [], authority: 'Posting'};
  const run = {run_id: 'a'.repeat(32), phase: 'after', state: 'ready', scope: {kind: 'regions', region_uids: ['PR-BRL-41']},
    signatures_total: 1, signed_signatures: 0, confirmed_signatures: 0, steps, phase_steps: {before: []},
    group_review, harvest_preview: {chunks: [['PR-BRL-41'], ['PR-BRL-65']]}};
  globalThis.__routingResponses = [
    {regions: [{region_uid: 'PR-BRL-41', region_number: 41}], inventory: {}, pools: [], issues: [], signing_enabled: true},
    {runs: [run]},
  ];
  walletReady = () => true;
  const routing = createHarvestRouting({player: 'player0005', scope: run.scope, activeRunId: run.run_id,
    onRender() {}, onBack() {}, onDone() {}, onHarvest() {}, isCurrent() { return true; }});
  await routing.load();
  let footer = routing.footerHtml();
  if (!footer.includes('Sign routing · 7 approvals')) throw new Error('live plan count omitted fixed approvals: ' + footer);
  routing.change({dataset: {hrMember: optionalId}, checked: false});
  footer = routing.footerHtml();
  if (!footer.includes('Sign routing · 6 approvals')) throw new Error('deselecting optional group changed fixed approvals incorrectly: ' + footer);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    extra_context_js = """
context.fetch = async () => ({ ok: true, status: 200, async json() { return context.__routingResponses.shift(); } });
context.window.fetch = context.fetch;
"""
    run_in_module_context(
        body_js,
        entry=Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js",
        extra_context_js=extra_context_js,
    )


def test_v2_unticked_sale_stays_excluded_through_rerender_and_signing_handoff():
    from .vm_context import run_in_module_context

    body_js = r"""
walletReady = () => true;
walletCanSignTransaction = () => false;
navigator.locks = { request: async (_name, callback) => callback() };
const routeIds = ['route-a', 'route-b', 'route-c'];
const members = routeIds.map((route_id, index) => ({ route_id, nonce: `member-${index}`, kind: 'sale_dec', source_amount: '10' }));
const operation = { id: 'operation-1', source_region: 'land-PNW-041', source_symbol: 'WOOD', destination_symbol: 'DEC', kind: 'sale_dec', members };
const bundle = { id: 'bundle-1', wave: 0, approval: 1, operations: [operation] };
const record = { run_id: 'a'.repeat(32), preview_id: 'preview-1', state: 'ready', manifest: { bundles: [bundle], approval_counts: { routing: 1 } },
  bundles: { 'bundle-1': { state: 'planned' } }, operations: {}, call_budget: { total: 0, ceiling: 120, actions: {} } };
let excludedAtClaim = null;
let signedOperations = null;
const onlyIncluded = members.filter(member => member.route_id !== 'route-b');
const api = async (_player, path, body) => {
  if (path === 'run' || path === 'state') return record;
  if (path === 'claim') {
    excludedAtClaim = body.excluded_route_ids;
    record.bundles['bundle-1'].state = 'claimed';
    return { authority: 'Posting', member_nonces: onlyIncluded.map(member => member.nonce),
      operations: [['custom_json', { id: 'sm_land_operation', json: JSON.stringify({ n: onlyIncluded.map(member => member.nonce) }) }]] };
  }
  if (path === 'head') return {};
  if (path === 'attach') { record.bundles['bundle-1'] = { state: 'confirmed', tx_id: 'tx' }; return record; }
  throw Error('unexpected endpoint ' + path);
};
(async () => {
  const run = createRoutingRun({ player: 'fixtureowner', api, prepare: async () => [], sign: async (_player, operations) => {
    signedOperations = operations; return { txId: 'tx' };
  } });
  await run.create({ preview_id: 'preview-1', preview_digest: 'digest' });
  run.action('toggle-member', { dataset: { r3Route: 'route-b' }, checked: false });
  const excludedHtml = run.html();
  if (!excludedHtml.includes('data-r3-route="route-b"') || !excludedHtml.includes('data-r3-route="route-b" aria-label="Include Wood sale from Region 041 · Pristine Northwest"'))
    throw Error('unticked sale disappeared from the route preview');
  const rerenderedHtml = run.html();
  const excludedRow = rerenderedHtml.match(/<input[^>]*data-r3-route="route-b"[^>]*>/)?.[0];
  if (!excludedRow || /\bchecked\b/.test(excludedRow)) throw Error('unticked sale was reselected on re-preview');
  await run.action('sign');
  if (JSON.stringify(excludedAtClaim) !== JSON.stringify(['route-b'])) throw Error('claim did not exclude unticked sale');
  if (!signedOperations || JSON.stringify(JSON.parse(signedOperations[0][1].json).n) !== JSON.stringify(['member-0', 'member-2']))
    throw Error('excluded sale appeared in operations handed to signing');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
"""
    run_in_module_context(body_js, entry=Path(__file__).parents[2] / "ceres" / "static" / "routing-run.js")


def _rendered_routing_review_html() -> str:
    from .vm_context import run_in_module_context

    body_js = """
(async () => {
  const ids = ['b'.repeat(32), 'c'.repeat(32), 'd'.repeat(32)];
  const steps = ids.map((step_id, index) => ({
    step_id, state: 'reviewed', kind: 'sell', source_region: 'PR-BRL-41', symbol: ['WOOD', 'STONE', 'IRON'][index],
    quote: { source_symbol: ['WOOD', 'STONE', 'IRON'][index], destination_symbol: 'DEC', input: '10.000', output: '5.000' },
    review: { step: { step_id }, payload: {}, operations: [] },
  }));
  const group_review = { member_ids: ids, digest: 'group-digest', step: steps[0], operations: [], authority: 'Posting' };
  const run = { run_id: 'a'.repeat(32), phase: 'after', state: 'ready', scope: { kind: 'regions', region_uids: ['PR-BRL-41'] },
    steps, phase_steps: { before: [] }, group_review };
  globalThis.__routingResponses = [
    { regions: [{ region_uid: 'PR-BRL-41', region_number: 41 }], inventory: {}, pools: [], issues: [], signing_enabled: true },
    { runs: [run] },
  ];
  const routing = createHarvestRouting({ player: 'player0005', scope: run.scope, activeRunId: run.run_id,
    onRender() {}, onBack() {}, onDone() {}, onHarvest() {}, isCurrent() { return true; } });
  await routing.load();
  process.stdout.write(routing.html() + '\\u0000' + routing.footerHtml());
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    extra_context_js = """
context.fetch = async () => ({ ok: true, status: 200, async json() { return context.__routingResponses.shift(); } });
context.window.fetch = context.fetch;
"""
    result = run_in_module_context(
        body_js,
        entry=Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js",
        extra_context_js=extra_context_js,
    )
    html_part, _, footer_part = result.stdout.partition("\u0000")
    return html_part, footer_part


@pytest.mark.parametrize("viewport_width,device_scale_factor", [(1536, 1.25), (1920, 1)])
def test_routing_review_caps_real_cards_and_footer_at_56rem(viewport_width, device_scale_factor):

    static = Path(__file__).parents[2] / "ceres" / "static"
    styles = "\n".join((static / name).read_text() for name in (
        "canonical.css", "buttons.css", "ceres.css", "mobile-plot.css"))
    html_part, footer_part = _rendered_routing_review_html()
    # A second, standalone sync_playwright() in this process is refused once the shared live browser has run (see the
    # note in test_harvest_modal_side_by_side.py); this static render borrows that one shared browser instead.
    with spawn_live_ceres() as (browser, _base_url):
        context = None
        try:
            context = browser.new_context(
                viewport={"width": viewport_width, "height": 900},
                device_scale_factor=device_scale_factor,
            )
            page = context.new_page()
            # footerHtml() is rendered by harvest-modal.js as a sibling of .hm-body inside
            # .harvest-panel (see M.routing.footerHtml() in harvest-modal.js), not nested inside
            # .harvest-routing itself, so the real modal skeleton is reproduced here too.
            page.set_content(
                f"<!doctype html><html><head><style>{styles}</style></head><body>" f"<div id='harvest-modal-overlay'><div class='harvest-panel'>" f"<div class='hm-body'>{html_part}</div>{footer_part}" f"</div></div></body></html>"
            )
            measured = page.evaluate("""
() => {
  const panel = document.querySelector('.harvest-panel');
  const routing = panel.querySelector('.harvest-routing');
  const cards = [...routing.querySelectorAll('.hr-step')];
  const footer = panel.querySelector('.hr-bundle-action');
  const box = element => element.getBoundingClientRect();
  return {
    routing: box(routing).width,
    cards: cards.map(card => box(card).width),
    footer: box(footer).width,
    overflow: routing.scrollWidth - routing.clientWidth,
  };
}
""")
        finally:
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
    print(f"routing-width viewport={viewport_width} dpr={device_scale_factor} " f"container={measured['routing']:.2f} cards={','.join(f'{width:.2f}' for width in measured['cards'])} " f"footer={measured['footer']:.2f} overflow={measured['overflow']}")

    assert measured["routing"] <= 56 * 16 + .01
    assert all(abs(width - measured["routing"]) <= .01 for width in measured["cards"])
    # The sticky action footer () is a sibling of the routing content at the panel level
    # not nested inside .harvest-routing, so it spans the full sticky action region rather than
    # being capped to the 56rem content column; it must never be narrower than that column.
    assert measured["footer"] >= measured["routing"] - .01
    assert measured["overflow"] <= 0


def test_does_not_reuse_prefetched_review_or_assert_harvest_readiness():
    routing_source = (Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js").read_text()
    queue_source = (Path(__file__).parents[2] / "ceres" / "static" / "transaction-queue.js").read_text()
    endpoint_source = (Path(__file__).parents[2] / "ceres" / "routes_harvest_routing.py").read_text()
    handoff_source = endpoint_source.split('@router.post("/api/harvest/routing/handoff")', 1)[1].split(
        '@router.post("/api/harvest/routing/transaction")', 1)[0]

    # The head reference is a short-lived transaction-reference optimization, not a quote.
    # It is partitioned by routing run and still passes the live-head freshness predicate.
    assert "const QUEUE_HEAD_CACHE_MS = 30000;" in queue_source
    assert "const queueHeadCache = new Map();" in queue_source
    assert "const cached = runId ? queueHeadCache.get(runId) : null;" in queue_source
    assert "queueHeadCache.delete(cachedRunId)" in queue_source
    assert "now - cached.cachedAt < QUEUE_HEAD_CACHE_MS && queueHeadIsFresh(cached.props)" in queue_source
    assert "if (runId) queueHeadCache.set(runId, { props, cachedAt: Date.now() });" in queue_source

    # The candidate review begins while the prior wallet window is open, but it is never
    # passed directly to the wallet: every item signs the newly handed-off operations.
    assert "api('prefetch', { run_id: runId, step_id: handoff.step_id })" in routing_source
    assert "const prepared = await prefetchedReview;" in routing_source
    assert "requestSignatureThroughGate(player, item.handoff.operations" in routing_source
    assert "const handoff = await api('handoff', { run_id: runId, review_digest: review.digest" in routing_source

    # Handoff reconstructs the current state under the run lock and rejects material drift
    # before returning the operations which the wallet receives.
    assert "context = _context(request, player)" in handoff_source
    assert "fresh, public = _fresh_step(request, player, record, member, context)" in handoff_source
    assert "built = build_operation(player, fresh, record[\"slippage\"])" in handoff_source
    assert "raise routing.RoutingError(\"Pool amounts changed. Review the new quote before signing\")" in handoff_source
    assert "fresh = _build_preview(request, player, record[\"scope\"], fresh=True)" in endpoint_source
    assert "Harvest state is not confirmed" in endpoint_source
    assert "api('review', { run_id: runId })" in routing_source
    assert "if (name === 'sign' && isCurrent() && S.run?.state === 'ready_harvest'" in routing_source


def test_queue_head_cache_is_run_scoped_and_expires_in_vm():
    entry = Path(__file__).parents[2] / "ceres" / "static" / "transaction-queue.js"
    setup = """
context.crypto = require('node:crypto').webcrypto;
context.TextEncoder = TextEncoder;
let __queueClockMs = Date.parse('2026-09-21T12:00:00Z');
const __queueNativeDate = Date;
class __queueClockDate extends __queueNativeDate {
  constructor(...args) {
    return args.length ? new __queueNativeDate(...args) : new __queueNativeDate(__queueClockMs);
  }
  static now() { return __queueClockMs; }
}
context.Date = __queueClockDate;
context.advanceQueueClock = milliseconds => { __queueClockMs += milliseconds; };
"""
    body = r"""
(async () => {
  const assert = (value, message) => { if (!value) throw Error(message); };
  CURRENT_PLAYER = 'alice';
  BOARD = { player: 'alice', plots: [] };
  window.hive_keychain = { requestBroadcast() {}, requestSignTx() {} };
  const operation = n => ['custom_json', {
    id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'],
    json: JSON.stringify({ op: 'tax_collection', deed_uids: ['I-1-aaaaaaaa'], n }),
  }];
  let headCalls = 0;
  queueRpc = async method => {
    assert(method === 'condenser_api.get_dynamic_global_properties', 'unexpected RPC ' + method);
    headCalls += 1;
    return {
      head_block_number: 100 + headCalls,
      head_block_id: 'd6908f068242802d3791ff650ddcaefbb54782a5',
      time: new Date().toISOString().slice(0, 19),
    };
  };

  await prepareQueueBatch('alice', [[operation('first')]], 'Posting', { runId: 'run-a' });
  advanceQueueClock(29999);
  await prepareQueueBatch('alice', [[operation('same-run')]], 'Posting', { runId: 'run-a' });
  assert(headCalls === 1, 'same run missed its still-valid 30 second head cache');
  await prepareQueueBatch('alice', [[operation('other-run')]], 'Posting', { runId: 'run-b' });
  assert(headCalls === 2, 'a different run reused run-a head state');
  advanceQueueClock(2);
  await prepareQueueBatch('alice', [[operation('expired')]], 'Posting', { runId: 'run-a' });
  assert(headCalls === 3, 'an expired per-run head reference was reused');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    run_in_module_context(body, entry=entry, extra_context_js=setup)
