"""Offline  run-service guards. No wallet, broadcast or live endpoint is used."""

import json
import time

import pytest

from ceres import harvest_routing as routing, routing_plan, routing_runs_v2 as runs, routing_safety


def _preview(player):
    """Build one server-shaped Posting sale preview without physical calls."""
    pool = routing.Pool(1, "GRAIN", routing.amount("1000"), routing.amount("1000"),
                        routing.amount("1000"), False)
    rule = {"id": "sale-1", "kind": "sell", "source_region": "PR-AAA-1", "symbol": "GRAIN",
            "destination_symbol": "DEC", "quantity": "10", "slippage": "2.5"}
    quote = routing.swap_quote("GRAIN", "DEC", "10", {"GRAIN": pool}, "2.5")
    step = {"id": "sale-1", "kind": "sell", "status": "ready", "source_region": "PR-AAA-1",
            "destination_region": routing.INVENTORY, "symbol": "GRAIN", "destination_symbol": "DEC",
            "quantity": "10", "slippage": "2.5", "quote": quote.public()}
    manifest = routing_plan.compile_manifest(rules=[rule], steps=[step], pools={"GRAIN": pool},
                                             account=player, regions=1, enabled_kinds={"sale_dec"})
    return {"player": player, "phase": "after", "blocked": False, "manifest": manifest,
            "preview_id": "offline-preview", "preview_digest": "offline-digest", "rules": [rule],
            "steps": [step], "state": {"pools": [{"id": 1, "symbol": "GRAIN",
                "resource_quantity": "1000", "dec_quantity": "1000", "total_shares": "1000",
                "external": False}]}}


def test_claim_attach_and_exact_member_verdict_are_durable(tmp_path):
    """The one signed bundle has stable bytes, one tx identity and two counted reads."""
    player = "fixtureowner"
    with runs.locked(tmp_path, player) as folder:
        with pytest.raises(routing.RoutingError, match="disabled"):
            runs.create(folder, player, _preview(player), set())
        record = runs.create(folder, player, _preview(player), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        claim = runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        assert runs.claim(folder, record, bundle_id, {"sale_dec"}, True) == claim
        assert claim["authority"] == "Posting"
        assert len(claim["operations"]) == len(claim["member_nonces"]) == 1
        tx_id = "a" * 40
        runs.attach(folder, record, bundle_id, tx_id)
        with pytest.raises(routing.RoutingError, match="different transaction"):
            runs.attach(folder, record, bundle_id, "b" * 40)
        runs.reconcile(folder, record, bundle_id, "chain",
                       chain_get=lambda _tx: ({"operations": claim["operations"]}, None))
        payload = json.loads(claim["operations"][0][1]["json"])
        reply = {"trx_info": {"id": tx_id, "player": player, "type": "land_operation",
                              "data": json.dumps(payload), "success": True,
                              "result": {"success": True, "result": {"success": True,
                                         "data": {"dec_amount": "9"}}}}}
        runs.reconcile(folder, record, bundle_id, "game", 0, game_get=lambda _url: reply)
        saved = record   # a finished run is deleted in the same step; the answer carries the final state
        assert (player, record["run_id"]) not in runs._RUNS
        assert saved["operations"]["sale-1"]["state"] == "confirmed"
        assert {item["state"] for item in saved["reservations"].values()} == {"consumed"}
        assert saved["state"] == "complete"
        assert saved["call_budget"]["total"] == 2
        assert saved["call_budget"]["actions"] == {"chain_probe": 1, "game_verdict": 1}


def test_unknown_probe_exhaustion_preserves_claim_and_counts(tmp_path):
    """Two failed physical probes are counted; the third is refused before dispatch."""
    player = "fixtureowner"
    with runs.locked(tmp_path, player) as folder:
        record = runs.create(folder, player, _preview(player), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        runs.attach(folder, record, bundle_id, "a" * 40)
        called = []

        def unavailable(tx_id):
            """Represent one failed node request per invocation."""
            called.append(tx_id)
            return None, "lookup_failed"

        runs.reconcile(folder, record, bundle_id, "chain", chain_get=unavailable)
        runs.reconcile(folder, record, bundle_id, "chain", chain_get=unavailable)
        with pytest.raises(routing.RoutingError, match="allowance"):
            runs.reconcile(folder, record, bundle_id, "chain", chain_get=unavailable)
        saved = runs.read(folder, player, record["run_id"])
        assert len(called) == saved["call_budget"]["total"] == 2
        assert saved["bundles"][bundle_id]["state"] == "outcome_unknown"
        assert {item["state"] for item in saved["reservations"].values()} == {"held"}
        assert saved["bundles"][bundle_id]["claim"]["member_nonces"]


def test_all_action_allocations_sum_to_120_and_stop_before_next_dispatch(tmp_path):
    """The full physical-call schedule never admits a 121st attempt."""
    player = "fixtureowner"
    with runs.locked(tmp_path, player) as folder:
        record = runs.create(folder, player, _preview(player), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        for action, ceiling in routing_plan.ACTION_CEILINGS.items():
            for _ in range(ceiling):
                runs._charge(folder, record, action, action, player)
        assert record["call_budget"]["total"] == 120
        with pytest.raises(routing.RoutingError, match="Run call budget"):
            runs._charge(folder, record, "game_verdict", "/transactions/lookup", player)
        saved = runs.read(folder, player, record["run_id"])
        assert saved["call_budget"]["total"] == 120
        assert saved["call_budget"]["stopped"] is True
        assert saved["bundles"][bundle_id]["claim"]["member_nonces"]
        assert saved["bundles"][bundle_id]["state"] == "outcome_unknown"


def test_reload_stop_keeps_claimed_bundle_for_lookup_only(tmp_path):
    """A page reload cannot prepare another signature for unverified work."""
    player = "fixtureowner"
    with runs.locked(tmp_path, player) as folder:
        record = runs.create(folder, player, _preview(player), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        claim = runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        runs.stop(folder, record)
        saved = runs.read(folder, player, record["run_id"])
        assert saved["bundles"][bundle_id]["state"] == "outcome_unknown"
        assert saved["bundles"][bundle_id]["claim"] == claim
        with pytest.raises(routing.RoutingError, match="stopped"):
            runs.claim(folder, saved, bundle_id, {"sale_dec"}, True)


def test_refused_parent_skips_only_its_dependent():
    """An independent operation in the same later bundle remains planned."""
    record = {"step_to_operation": {"parent-step": "parent"},
              "step_to_member": {"parent-step": "parent-nonce"},
              "operations": {
                  "parent": {"state": "refused", "members": {"parent-nonce": {"state": "refused"}}},
                  "dependent": {"state": "planned", "members": {"child-nonce": {"state": "planned"}}},
                  "independent": {"state": "planned", "members": {"other-nonce": {"state": "planned"}}},
              }, "reservations": {"child-nonce": {"state": "held"},
                                  "other-nonce": {"state": "held"}},
              "manifest": {"bundles": [{"id": "later", "operations": [
                  {"id": "dependent", "depends_on": ["parent-step"]},
                  {"id": "independent", "depends_on": []}]}]},
              "bundles": {"later": {"state": "planned"}}, "state": "ready"}
    runs._refresh_state(record)
    assert record["operations"]["dependent"]["state"] == "skipped"
    assert record["reservations"]["child-nonce"]["state"] == "released"
    assert record["reservations"]["other-nonce"]["state"] == "held"
    assert record["operations"]["independent"]["state"] == "planned"
    assert record["bundles"]["later"]["state"] == "planned"


def test_coalesced_sale_keeps_independent_member_verdicts():
    """One route share can confirm while another remains protected and unknown."""
    operation = {"destination_symbol": "DEC", "members": [
        {"nonce": "one", "source_amount": "10", "protected_minimum": "3"},
        {"nonce": "two", "source_amount": "10", "protected_minimum": "6"}]}
    state = {"members": {"one": {"state": "planned"}, "two": {"state": "planned"}}}
    result = runs._attribute_members(operation, state, "confirmed", {"effects": {"dec_amount": "10"}})
    assert result == "outcome_unknown"
    assert state["members"]["one"]["state"] == "confirmed"
    assert state["members"]["two"]["state"] == "outcome_unknown"
    assert routing.amount(state["members"]["one"]["received_dec"]) == routing.amount("5")
    assert routing.amount(state["members"]["two"]["received_dec"]) == routing.amount("5")


def test_a_huge_plan_packs_without_recursion_or_delay():
    """ (Astra, whale scale): 1,100 operations used to raise RecursionError in _pack; now one first-fit pass packs any size."""
    import time
    from ceres import routing_plan
    pools = ["WOOD", "STONE", "IRON", "GRAIN", "RESEARCH", "AURA"]
    operations = [{"id": f"o{i:05d}", "pool": pools[i % 6], "depends_on": [], "members": []} for i in range(1100)]
    started = time.time()
    bundles = routing_plan._pack(operations, 4)
    assert time.time() - started < 5
    assert sum(len(bundle) for bundle in bundles) == 1100
    for bundle in bundles:
        assert len(bundle) <= 4 and len({op["pool"] for op in bundle}) == len(bundle)


def test_an_earlier_run_never_blocks_a_new_preview_and_is_deleted(tmp_path):
    """: any earlier run (unsent, claimed, checking) is retired when the account creates another; it never blocks."""
    player = "fixtureowner"
    with runs.locked(tmp_path, player) as folder:
        first = _preview(player)
        first["harvest_attempt_ids"] = ["attempt-one"]
        old = runs.create(folder, player, first, {"sale_dec"})
        second = _preview(player)
        second.update(preview_id="offline-preview-2", harvest_attempt_ids=["attempt-two"])
        fresh = runs.create(folder, player, second, {"sale_dec"})
        assert fresh["run_id"] != old["run_id"]
        assert (player, old["run_id"]) in runs._RUNS                                      # another open page keeps its own run; it never blocks this one
        assert routing_safety.unresolved(tmp_path, player) == []                          # it never reached a wallet: nothing to remember
        third = _preview(player)
        third.update(preview_id="offline-preview-3", harvest_attempt_ids=["attempt-two"])
        again = runs.create(folder, player, third, {"sale_dec"})                          # the same harvest again after an unsent run: allowed
        assert again["run_id"] not in {old["run_id"], fresh["run_id"]}


def test_a_deleted_unknown_run_leaves_only_a_silent_tx_reservation(tmp_path):
    """ keeps only tx id, expiry and receipt ids after the disposable run is retired."""
    player = "fixtureowner"
    with runs.locked(tmp_path, player) as folder:
        first = _preview(player)
        first["harvest_attempt_ids"] = ["attempt-one"]
        record = runs.create(folder, player, first, {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        runs.attach(folder, record, bundle_id, "a" * 40)
        runs.retire(folder, record)                                                       # a page refresh / a newer run / the lease
        assert (player, record["run_id"]) not in runs._RUNS
        safety_records = routing_safety.unresolved(tmp_path, player)
        assert len(safety_records) == 1
        assert set(safety_records[0]) == {"tx_id", "expires_at", "attempt_ids"}
        assert safety_records[0]["attempt_ids"] == ["attempt-one"]
        assert routing_safety.overlapping(tmp_path, player, ["attempt-one"]) == safety_records


def test_legacy_record_file_is_not_exposed_or_purged_as_a_reservation(tmp_path):
    folder = tmp_path / routing_safety.SAFETY_DIR
    folder.mkdir()
    (folder / "fixtureowner.json").write_text('[{"id": "old:1", "attempt_ids": ["x"], "tx_id": null}]')
    (folder / ".lock").write_text("")
    assert routing_safety.unresolved(tmp_path, "fixtureowner") == [] and routing_safety.overlapping(tmp_path, "fixtureowner", ["x"]) == []
    assert routing_safety.purge_all(tmp_path) == 0 and folder.exists()
    assert routing_safety.purge_all(tmp_path) == 0                                        # nothing left, nothing to fail on
    assert routing_safety.check(tmp_path, "fixtureowner", "old:1", not_sent=True) == {"state": "gone"}


def test_purge_all_deletes_every_run_at_start_and_the_idle_lease_only_the_old_ones(tmp_path):
    player = "fixtureowner"
    with runs.locked(tmp_path, player) as folder:
        record = runs.create(folder, player, _preview(player), {"sale_dec"})
        key = (player, record["run_id"])
        assert key in runs._RUNS
    assert runs.purge_all(tmp_path, idle_only=True) == 0 and key in runs._RUNS                      # young: kept by the lease sweep
    assert runs.purge_all(tmp_path, now=time.time() + runs.IDLE_LEASE_SECONDS + 60, idle_only=True) == 1 and key not in runs._RUNS
    with runs.locked(tmp_path, player) as folder:
        runs.create(folder, player, _preview(player), {"sale_dec"})
    assert runs.purge_all(tmp_path) == 1                                                       # server start: nothing survives
    assert runs._RUNS == {}


def test_the_discard_and_safety_routes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ceres import client_limits, routes_routing_v2
    monkeypatch.setenv(client_limits.ENV_SWITCH, "off")
    player = "fixtureowner"
    app = FastAPI()
    app.state.ceres_config = SimpleNamespace(host="127.0.0.1", port=27390, player=None, root=tmp_path, routing_kinds={"sale_dec"}, harvest_routing_signing_enabled=True)
    app.include_router(routes_routing_v2.router)
    client = TestClient(app, base_url="http://127.0.0.1:27390", headers={"Origin": "http://127.0.0.1:27390"})
    with runs.locked(tmp_path, player) as folder:
        preview = _preview(player)
        preview["harvest_attempt_ids"] = ["attempt-one"]
        record = runs.create(folder, player, preview, {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
    assert client.post(f"/api/harvest/routing/v2/discard?player={player}", json={"run_id": "f" * 32}).json() == {"ok": True}       # already gone: idempotent
    assert client.post(f"/api/harvest/routing/v2/discard?player={player}", json={"run_id": record["run_id"]}, headers={"X-Run-Token": "x" * 43}).json() == {"ok": True}   # another caller's token: the same answer, nothing changed
    assert (player, record["run_id"]) in runs._RUNS
    assert client.post(f"/api/harvest/routing/v2/discard?player={player}", json={"run_id": record["run_id"]}, headers={"X-Run-Token": record["run_token"]}).json() == {"ok": True}
    assert (player, record["run_id"]) not in runs._RUNS
    assert client.get(f"/api/harvest/routing/v2/safety?player={player}").json() == {"records": []}                                      # nothing is kept
    checked = client.post(f"/api/harvest/routing/v2/safety/check?player={player}", json={"record_id": "run1:b1", "not_sent": True}).json()
    assert checked["state"] == "gone" and checked["records"] == []


def test_a_run_needs_its_own_token_and_a_claim_is_on_disk_the_moment_it_exists(tmp_path):
    player = "fixtureowner"
    with runs.locked(tmp_path, player) as folder:
        preview = _preview(player)
        preview["harvest_attempt_ids"] = ["attempt-one"]
        record = runs.create(folder, player, preview, {"sale_dec"})
        assert "token_hash" in record and "run_token" in record and record["run_token"] not in json.dumps(runs._RUNS[(player, record["run_id"])])   # only the hash is kept
        stored = runs.read(folder, player, record["run_id"])
        runs.verify_token(stored, record["run_token"])
        for wrong in (None, "", "x" * 43, "y" * 5):
            with pytest.raises(routing.RoutingError, match="not found"):
                runs.verify_token(stored, wrong)
        assert "token_hash" not in runs.public(stored)
        assert routing_safety.unresolved(tmp_path, player) == []
        bundle_id = record["manifest"]["bundles"][0]["id"]
        runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        assert routing_safety.unresolved(tmp_path, player) == []                           # nothing about a claimed run is kept
        with pytest.raises(routing.RoutingError, match="not found"):
            runs.create(folder, player, preview, {"sale_dec"}, token="z" * 43)         # the same preview from another caller does not hand over the run


def test_the_old_v1_routing_routes_are_off_unless_an_operator_turns_them_on(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from ceres.app import create_app
    from ceres.config import CeresConfig
    client = TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005")))
    monkeypatch.delenv("CERES_LEGACY_ROUTING", raising=False)
    assert client.post("/api/harvest/routing/review?player=player0005", json={"run_id": "d" * 32}).status_code == 404
    monkeypatch.setenv("CERES_LEGACY_ROUTING", "1")
    assert client.post("/api/harvest/routing/review?player=player0005", json={"run_id": "d" * 32}).status_code != 404
