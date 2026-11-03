""": the shared finder must never adopt a different signed action."""
import json
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ceres.routes_confirm import find_exact_action


def op(nonce, *, player="player_owner", op_id="sm_harvest"):
    return ["custom_json", {"id": op_id, "required_auths": [], "required_posting_auths": [player],
                            "json": json.dumps({"n": nonce, "action": "harvest"})}]


def history(tx, ops):
    return [[i, {"trx_id": tx, "op": item, "timestamp": "2026-11-03T09:00:00"}] for i, item in enumerate(ops)]


def test_second_op_and_full_order_match():
    tx = "a" * 40
    expected = [op("1" * 16), op("2" * 16)]
    assert find_exact_action(history(tx, expected), "player_owner", expected) == tx
    assert find_exact_action(history(tx, expected), "player_owner", expected[:1]) is None
    assert find_exact_action(history(tx, expected[::-1]), "player_owner", expected) is None


def test_result_size_is_irrelevant_and_nonce_is_exact():
    tx = "b" * 40
    expected = [op("3" * 16)]
    rows = history(tx, expected)
    rows[0][1]["result"] = "x" * 56000
    assert find_exact_action(rows, "player_owner", expected) == tx
    assert find_exact_action(rows, "player_owner", [op("4" * 16)]) is None


def test_other_account_and_missing_nonce_refused():
    tx = "c" * 40
    expected = [op("5" * 16)]
    assert find_exact_action(history(tx, [op("5" * 16, player="someone_else")]), "player_owner", expected) is None
    with pytest.raises(ValueError):
        find_exact_action([], "player_owner", [op("")])


def test_active_sps_transfer_has_nested_nonce():
    tx = "d" * 40
    active = ["custom_json", {"id": "sm_sps", "required_auths": ["player_owner"],
                              "required_posting_auths": [], "json": json.dumps({
                                  "action": "token_transfer", "params": {"n": "6" * 16, "token": "SPS"}})}]
    assert find_exact_action(history(tx, [active]), "player_owner", [active]) == tx


def test_reviewed_deed_lock_uuid_nonce():
    tx = "f" * 40
    lock = op("12345678-1234-4234-8234-123456789abc", op_id="sm_lock_assets")
    lock[1]["required_auths"], lock[1]["required_posting_auths"] = ["player_owner"], []
    assert find_exact_action(history(tx, [lock]), "player_owner", [lock]) == tx


def test_fides_land_and_combine_alphanumeric_nonces():
    tx = "1" * 40
    expected = [op("Ab3De5Gh7J"), op("K9Lm2No4Pq", op_id="sm_combine_cards")]
    assert find_exact_action(history(tx, expected), "player_owner", expected) == tx


def test_endpoint_reads_only_the_requested_signers_last_100_ops(monkeypatch):
    from ceres import routes_confirm as confirm, harvest_routing_bridge

    class FrozenDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 3, 9, 0, tzinfo=timezone.utc)

    calls = []
    expected = [op("7" * 16, player="alice")]
    monkeypatch.setattr(confirm, "datetime", FrozenDate)
    monkeypatch.setattr(confirm, "_require_same_origin", lambda _: None)
    monkeypatch.setattr(confirm.client_limits, "limit", lambda *_: None)
    monkeypatch.setattr(harvest_routing_bridge, "_history", lambda account, cursor: calls.append((account, cursor)) or history("e" * 40, expected))

    class Request:
        app = SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(player="alice")))

        async def json(self):
            return {"operations": expected, "since": "2026-11-03T08:59:59Z", "attempt": "a1b2c3d4e5f60718", "answered": True}

    answer = asyncio.run(confirm.find(Request()))
    assert (answer["status"], answer["tx_id"]) == ("found", "e" * 40)
    assert calls == [("alice", -1)]


def test_game_verdict_is_per_op_and_keeps_the_refusal():
    from ceres.routes_confirm import game_op_verdict

    first, second = op("1" * 16), op("2" * 16)
    tx = "a" * 40
    assert game_op_verdict(None, "player_owner", first, tx)["state"] == "pending"
    accepted = {"id": tx, "player": "player_owner", "type": "harvest",
                "data": first[1]["json"], "success": True, "result": json.dumps({"amount": 7})}
    refused = {"id": tx + "-1", "player": "player_owner", "type": "harvest",
               "data": second[1]["json"], "success": False, "error": "Game rejected op 2"}
    assert game_op_verdict(accepted, "player_owner", first, tx) == {"state": "confirmed", "result": {"amount": 7}}
    assert game_op_verdict(refused, "player_owner", second, tx + "-1")["error"] == "Game rejected op 2"
    assert game_op_verdict(refused, "someone_else", second, tx + "-1")["state"] == "pending"


def test_verdict_endpoint_recovers_after_one_failed_lookup(monkeypatch):
    from ceres import routes_confirm as confirm

    tx = "d" * 40
    operations = [op("1" * 16, player="alice"), op("2" * 16, player="alice")]
    calls = []
    records = [
        {"id": tx, "player": "alice", "type": "harvest", "data": operations[0][1]["json"],
         "success": True, "result": '{"amount":7}'},
        {"id": tx + "-1", "player": "alice", "type": "harvest", "data": operations[1][1]["json"],
         "success": False, "error": "Second operation refused"},
    ]

    def lookup(_get, lookup_id):
        calls.append(lookup_id)
        if calls.count(tx) == 1 and lookup_id == tx:   # the first read of op 0 fails (reads run concurrently, fix round 1)
            raise RuntimeError("service restart")
        return records[0] if lookup_id == tx else records[1]

    monkeypatch.setattr(confirm, "_require_same_origin", lambda _: None)
    monkeypatch.setattr(confirm.client_limits, "limit", lambda *_: None)
    monkeypatch.setattr(confirm, "lookup_trx_info", lookup)

    class Request:
        app = SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(player="alice")))

        async def json(self):
            return {"tx_id": tx, "operations": operations}

    first = asyncio.run(confirm.verdict(Request()))
    assert [row["state"] for row in first["operations"]] == ["pending", "refused"]
    second = asyncio.run(confirm.verdict(Request()))
    assert [row["state"] for row in second["operations"]] == ["confirmed", "refused"]
    assert sorted(calls) == sorted([tx, tx + "-1", tx, tx + "-1"])
