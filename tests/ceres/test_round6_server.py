"""Bounded checks of the round-6 durable nonce registration seam."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ceres import routes_confirm as confirm
from ceres.routes_confirm import ledger_update


def _request(root):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(root=root))))


def _ops(nonce):
    return [["custom_json", {"id": "sm_land_operation", "json": {"n": nonce},
                             "required_posting_auths": ["alice"], "required_auths": []}]]


def test_reused_signed_nonce_is_rejected_after_restart(tmp_path):
    first = _request(tmp_path)
    ops = _ops("abcdefghij")
    assert ledger_update(first, "alice", ops, lambda row: True, register_attempt="a" * 16)
    # Replaying the exact begin for the same attempt is safe.
    assert ledger_update(_request(tmp_path), "alice", ops, lambda row: True, register_attempt="a" * 16)
    with pytest.raises(HTTPException) as conflict:
        ledger_update(_request(tmp_path), "alice", ops, lambda row: True, register_attempt="b" * 16)
    assert conflict.value.status_code == 409
    # A distinct account can use the same nonce; a new nonce can be registered here.
    assert ledger_update(_request(tmp_path), "bob", ops, lambda row: True, register_attempt="b" * 16)
    assert ledger_update(_request(tmp_path), "alice", _ops("klmnopqrst"), lambda row: True, register_attempt="b" * 16)


def test_action_id_survives_revision_and_repeat_is_distinct(tmp_path):
    request = _request(tmp_path)
    first = _ops("abcdefghij")
    changed = _ops("klmnopqrst")
    a = "a" * 16
    b = "b" * 16
    c = "c" * 16
    def begin(ops, attempt, action_id=None):
        return ledger_update(request, "alice", ops,
                             lambda row: confirm._apply_event(row, {"operations": ops}, 100, "begin", attempt),
                             register_attempt=attempt, attempt=attempt, action_id=action_id)
    first_result = begin(first, a)
    assert first_result["revision"] == 1
    revised = begin(changed, b, first_result["action_id"])
    assert revised["action_id"] == first_result["action_id"]
    assert revised["revision"] == 2
    repeated = begin(_ops("mnopqrstuv"), c)
    assert repeated["action_id"] != first_result["action_id"]


def test_semantic_candidate_respects_first_begin_and_retains_provenance(tmp_path):
    request = _request(tmp_path)
    first = _ops("abcdefghij")
    changed_nonce = _ops("klmnopqrst")
    a = "a" * 16
    identity = ledger_update(request, "alice", first,
                             lambda row: confirm._apply_event(row, {"operations": first}, 200, "begin", a),
                             register_attempt=a, attempt=a)["action_id"]
    def row(index, timestamp, nonce, tx):
        from datetime import datetime, timezone
        return [index, {"timestamp": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                        "trx_id": tx, "op": _ops(nonce)[0]}]
    too_early = "1" * 40
    at_boundary = "2" * 40
    candidates = confirm.retain_candidates(request, "alice", first, a,
        [row(1, 79, "klmnopqrst", too_early), row(2, 80, "klmnopqrst", at_boundary)], action_id=identity)
    by_id = {item["tx_id"]: item for item in candidates["matches"]}
    assert not by_id[too_early]["eligible"]
    assert by_id[at_boundary]["eligible"]
    assert by_id[at_boundary]["semantic"] == [1]
    assert by_id[at_boundary]["exact"] == []
    assert by_id[at_boundary]["indexes"] == [2]
    assert candidates["found"] == at_boundary
    assert confirm.ledger_absence(request, "alice", a, first)["status"] == "found"
    assert confirm.ledger_attempt(request, "alice", a)["action_match"] is True


def test_overflow_is_immediate_and_survives_reload(tmp_path):
    request = _request(tmp_path)
    for i in range(18):
        nonce = f"{i:010d}"
        ops = _ops(nonce)
        attempt = f"{i:016x}"
        result = ledger_update(request, "alice", ops,
                               lambda row, ops=ops, attempt=attempt:
                                   confirm._apply_event(row, {"operations": ops}, 100 + i, "begin", attempt),
                               register_attempt=attempt, attempt=attempt)
        assert result["read_mode"] == "fast"
    ops = _ops("overflow01")
    result = ledger_update(_request(tmp_path), "alice", ops,
                           lambda row: confirm._apply_event(row, {"operations": ops}, 119, "begin", "f" * 16),
                           register_attempt="f" * 16, attempt="f" * 16)
    assert result["overflow"] is True
    assert result["status_line"] == "Many actions at once: checking every 15 seconds"


def test_stale_expiry_worker_cannot_publish_or_claim_absence():
    row = {"attempts": {"a": {"gen": 2, "expiry": {"run": "fresh"},
                              "closed_at": 1, "begin_at": 0, "begin_confirmed": True}}}
    stale = confirm.expiry_settle({"status": "found", "tx_id": "1" * 40}, "a", "old", 1)
    assert stale(row) is False
    assert "found" not in row
    row["matches"] = {"2" * 40: {"eligible": True, "excluded_final_owner": False}}
    current = confirm.expiry_settle({"status": "absent"}, "a", "fresh", 2)
    assert current(row) is False
    assert row["attempts"]["a"]["expiry"]["run"] == "fresh"


def test_every_candidate_keeps_its_own_game_verdict(tmp_path):
    request = _request(tmp_path)
    ops = _ops("abcdefghij")
    attempt = "a" * 16
    action_id = ledger_update(request, "alice", ops,
                              lambda row: confirm._apply_event(row, {"operations": ops}, 200, "begin", attempt),
                              register_attempt=attempt, attempt=attempt)["action_id"]
    from datetime import datetime, timezone
    rows = [[index, {"timestamp": datetime.fromtimestamp(200 + index, timezone.utc).isoformat(),
                     "trx_id": f"{index:040x}", "op": _ops(nonce)[0]}]
            for index, nonce in ((1, "abcdefghij"), (2, "klmnopqrst"))]
    confirm.retain_candidates(request, "alice", ops, attempt, rows, action_id=action_id)
    confirm.retain_game_verdict(request, "alice", ops, f"{1:040x}", [{"state": "confirmed"}])
    confirm.retain_game_verdict(request, "alice", ops, f"{2:040x}", [{"state": "refused"}])
    result = confirm.retain_candidates(_request(tmp_path), "alice", ops, attempt, rows, action_id=action_id)
    assert {item["tx_id"]: item["verdicts"][0]["state"] for item in result["matches"]} == {
        f"{1:040x}": "confirmed", f"{2:040x}": "refused"}


def _budget():
    return {"fast_calls": [], "tail_calls": [], "coverage_calls": [], "fast_remaining": {},
            "inflight": {}, "tail_last": None, "coverage_queue": [], "coverage_active": None}


def test_four_fast_windows_keep_independent_due_offsets_with_three_in_flight():
    budget = _budget()
    rows = []
    for index, begin in enumerate((0, .1, .2, .3)):
        action_id = f"{index:032x}"
        attempt = f"{index:016x}"
        row = {"action_id": action_id, "attempts": {attempt: {"first_at": begin,
                 "fast_anchor": begin, "fast_index": 0, "read_mode": "fast"}}}
        budget["fast_remaining"][f"{action_id}:{attempt}"] = 20
        rows.append((row, attempt, begin))
    first = [confirm._account_read_plan(row, budget, attempt, begin) for row, attempt, begin in rows]
    assert [item.get("read") for item in first] == ["fast", "fast", "fast", None]
    assert first[3]["wait"] == 1
    assert len(budget["inflight"]) == 3
    budget["inflight"].pop(first[0]["token"])
    fourth = confirm._account_read_plan(rows[3][0], budget, rows[3][1], 16)
    assert fourth["read"] == "fast"
    assert len(budget["inflight"]) == 3
    assert [row["attempts"][attempt]["fast_index"] for row, attempt, _ in rows] == [1, 1, 1, 5]


def test_one_tail_reader_serves_fifty_epochs_and_rolls_at_hour_boundary():
    budget = _budget()
    rows = [( {"action_id": f"{index:032x}", "attempts": {f"{index:016x}": {
                 "first_at": 0, "read_mode": "overflow"}}}, f"{index:016x}")
            for index in range(50)]
    for tick in range(0, 3600, 15):
        for row, attempt in rows:
            plan = confirm._account_read_plan(row, budget, attempt, tick)
            if "token" in plan:
                budget["inflight"].pop(plan["token"])
        assert len(budget["tail_calls"]) <= 240
    assert len(budget["tail_calls"]) == 240
    assert "wait" in confirm._account_read_plan(rows[0][0], budget, rows[0][1], 3599)
    next_hour = confirm._account_read_plan(rows[0][0], budget, rows[0][1], 3600)
    assert next_hour["read"] == "tail"
    assert len(budget["tail_calls"]) == 240


def test_coverage_quantum_requeues_fifo_after_failure():
    budget = _budget()
    a = {"action_id": "a" * 32, "attempts": {"a": {}}}
    b = {"action_id": "b" * 32, "attempts": {"b": {}}}
    first = confirm._coverage_plan(a, budget, "a", 0)
    assert "run" in first
    assert confirm._coverage_plan(b, budget, "b", 0)["reason"] == "coverage-queue"
    assert confirm._coverage_release(budget, first["run"], requeue=True)
    second = confirm._coverage_plan(b, budget, "b", 1)
    assert "run" in second
    assert budget["coverage_queue"] == ["b" * 32 + ":b", "a" * 32 + ":a"]


def test_claimed_routing_run_replays_after_process_memory_loss(tmp_path):
    from ceres import routing_runs_v2 as runs
    from tests.ceres.test_routing_v2_service import _preview
    player = "fixtureowner"
    with runs.locked(tmp_path, player) as folder:
        record = runs.create(folder, player, _preview(player), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        run_id = record["run_id"]
        runs._RUNS.pop((player, run_id))
        restored = runs.read(folder, player, run_id)
        assert restored["bundles"][bundle_id]["state"] == "claimed"
        assert restored["bundles"][bundle_id]["claim"]["operations"]
    runs.purge_all(tmp_path)
    with runs.locked(tmp_path, player) as folder:
        replayed = runs.read(folder, player, run_id)
        assert replayed["bundles"][bundle_id]["state"] == "claimed"


def test_finalized_owner_excludes_old_tx_but_open_owner_blocks_repeat(tmp_path):
    from datetime import datetime, timezone
    request = _request(tmp_path)
    a_ops = _ops("abcdefghij")
    b_ops = _ops("klmnopqrst")
    a = "a" * 16
    b = "b" * 16
    a_id = ledger_update(request, "alice", a_ops,
        lambda row: confirm._apply_event(row, {"operations": a_ops}, 100, "begin", a),
        register_attempt=a, attempt=a)["action_id"]
    b_id = ledger_update(request, "alice", b_ops,
        lambda row: confirm._apply_event(row, {"operations": b_ops}, 220, "begin", b),
        register_attempt=b, attempt=b)["action_id"]
    tx = "3" * 40
    page = [[1, {"timestamp": datetime.fromtimestamp(100, timezone.utc).isoformat(),
                 "trx_id": tx, "op": a_ops[0]}]]
    confirm.retain_candidates(request, "alice", a_ops, a, page, action_id=a_id)
    open_b = confirm.retain_candidates(request, "alice", b_ops, b, page, action_id=b_id)
    assert open_b["found"] == tx
    def final(row):
        row["final"] = True
    ledger_update(request, "alice", a_ops, final, action_id=a_id, attempt=a)
    separated = confirm.retain_candidates(request, "alice", b_ops, b, page, action_id=b_id)
    candidate = next(item for item in separated["matches"] if item["tx_id"] == tx)
    assert candidate["excluded_final_owner"] is True


def test_unsafe_digest_migration_retracts_absence_and_keeps_tombstones(tmp_path):
    import json
    request = _request(tmp_path)
    ops = _ops("abcdefghij")
    digest = confirm._digest("alice", ops)
    old = {f"alice:{digest}": {"digest": digest, "first_seen": 1,
                             "attempts": {"a" * 16: {"first_at": 1,
                                                        "expiry": {"status": "absent"}}},
                             "found_by": {"a" * 16: "1" * 40}},
           "__nonce_owners__": {"alice:abcdefghij": [digest, "a" * 16]}}
    (tmp_path / "confirm_ledger.json").write_text(json.dumps(old))
    record = ledger_update(request, "alice", ops, lambda row: row, create=False, attempt="a" * 16)
    assert record["legacy_unsafe"] and record["disputed"]
    assert record["attempts"]["a" * 16]["expiry"]["status"] == "retry"
    data = json.loads((tmp_path / "confirm_ledger.json").read_text())
    assert data["__nonce_owners__"]["alice:abcdefghij"][0] == record["action_id"]
    assert f"alice:action:{record['action_id']}" in data


def test_finality_requires_closed_covered_attempt_and_all_verdicts():
    row = {"attempts": {"a": {"closed_at": 10, "expiry": {"status": "found", "covered": True}}},
           "matches": {"1" * 40: {"eligible": True, "verdicts": [{"state": "confirmed"}]},
                       "2" * 40: {"eligible": True, "verdicts": [{"state": "pending"}]}}}
    assert not confirm._refresh_action_finality(row)
    row["matches"]["2" * 40]["verdicts"] = [{"state": "refused"}]
    assert confirm._refresh_action_finality(row)
    row["attempts"]["b"] = {"first_at": 11}
    assert not confirm._refresh_action_finality(row)


def test_routing_release_predicate_and_crash_replay(tmp_path):
    from ceres import routing_runs_v2 as runs
    from tests.ceres.test_routing_v2_service import _preview
    request = _request(tmp_path)
    player = "fixtureowner"
    with runs.locked(tmp_path, player) as folder:
        record = runs.create(folder, player, _preview(player), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        claim = runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        ops = claim["operations"]
        attempt = "a" * 16
        action_id = ledger_update(request, player, ops,
            lambda row: confirm._apply_event(row, {"operations": ops}, 100, "begin", attempt),
            register_attempt=attempt, attempt=attempt)["action_id"]
        assert confirm.ledger_routing_release(request, player, ops) is None
        def covered(row):
            row["attempts"][attempt].update(begin_confirmed=True, closed_at=200,
                                              expiry={"status": "absent", "covered": True})
        ledger_update(request, player, ops, covered, action_id=action_id, attempt=attempt)
        proof = confirm.ledger_routing_release(request, player, ops)
        assert proof and proof["status"] == "absent"
        record["bundles"][bundle_id]["claimed_at"] -= runs.TX_EXPIRY_SECONDS + 1
        runs.replay_release(folder, record, lambda _: proof)
        first = record["bundles"][bundle_id]["state"]
        assert first == "stopped"
        run_id = record["run_id"]
        runs._RUNS.pop((player, run_id), None)
        restored = runs.read(folder, player, run_id)
        assert restored["bundles"][bundle_id]["state"] == first
        runs.replay_release(folder, restored, lambda _: proof)
        assert restored["bundles"][bundle_id]["state"] == first


def test_by_id_budget_fences_physical_hive_call_before_transport(tmp_path):
    import json
    import time
    from ceres import hive_rpc
    request = _request(tmp_path)
    now = time.time()
    (tmp_path / "confirm_ledger.json").write_text(json.dumps({
        "__accounts__": {"alice": {"by_id_calls": [now] * 240}}}))
    assert confirm.charge_account_read(request, "alice", "V") is False
    token = hive_rpc.BY_ID_ADMIT.set(lambda: confirm.charge_account_read(request, "alice", "V"))
    try:
        with pytest.raises(hive_rpc._NodeUnavailable, match="budget"):
            hive_rpc._answer_at("api.hive.blog", "condenser_api.get_transaction", ["1" * 40])
    finally:
        hive_rpc.BY_ID_ADMIT.reset(token)


def test_wallet_callback_tx_owner_is_bound_before_history_and_survives_reload(tmp_path):
    request = _request(tmp_path)
    tx = "1" * 40
    first, second = _ops("abcdefghij"), _ops("klmnopqrst")
    a, b = "a" * 16, "b" * 16
    def begin(ops, attempt):
        return ledger_update(request, "alice", ops,
            lambda row: confirm._apply_event(row, {"operations": ops}, 100, "begin", attempt),
            register_attempt=attempt, attempt=attempt)
    a_begin = begin(first, a)
    b_begin = begin(second, b)
    def callback(ops, attempt, action_id, token):
        return ledger_update(_request(tmp_path), "alice", ops,
            lambda row: confirm._apply_event(row, {"operations": ops,
                "begin_token": token}, 101, "answered", attempt),
            action_id=action_id, attempt=attempt, wallet_tx_id=tx)
    callback(first, a, a_begin["action_id"], a_begin["begin_token"])
    assert confirm._load(confirm._ledger_path(request))["__wallet_tx_owners__"]["alice:" + tx] == [a_begin["action_id"], a]
    with pytest.raises(HTTPException) as conflict:
        callback(second, b, b_begin["action_id"], b_begin["begin_token"])
    assert conflict.value.status_code == 409


def test_retained_first_hit_ends_duplicate_watch_only_after_complete_coverage():
    tx = "1" * 40
    row = {"action_id": "a" * 32, "found": tx, "matches": {tx: {"eligible": True,
           "excluded_final_owner": False, "verdicts": [{"state": "confirmed"}]}},
           "attempts": {"b" * 16: {"gen": 0, "expiry": {"run": "scan"},
           "begin_at": 100, "begin_confirmed": True, "closed_at": 110}}}
    apply = confirm.expiry_settle({"status": "absent", "covered": True}, "b" * 16, "scan", 0)
    assert apply(row) == {"found": tx, "covered": True}
    expiry = row["attempts"]["b" * 16]["expiry"]
    assert expiry["status"] == "found" and expiry["tx_id"] == tx and expiry["covered"] is True
