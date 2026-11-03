""" fix round 1, server-side behaviour (astra-review.md F1-F10, aletheia-verdict.md MEDIUM-3/4, LOW-6).

Each test drives a REAL route or server function with fakes only for the chain/game transport and the clock. Every test
here failed on the pre-round worktree (snapshot of 2026-11-03, build-report "Fix round 1" records the before run) and
passes after. No network, no browser.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from ceres import routes_confirm as confirm

PLAYER = "alice"
ATTEMPT = "a1b2c3d4e5f60718"


def op(nonce, *, op_id="sm_land_operation", active=False, payload=None):
    body = payload if payload is not None else {"op": "harvest_all", "n": nonce}
    return ["custom_json", {"id": op_id, "required_auths": [PLAYER] if active else [],
                            "required_posting_auths": [] if active else [PLAYER], "json": json.dumps(body)}]


def stamp(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class Clock:
    def __init__(self, start=1_800_000_000.0):
        self.now = start

    def time(self):
        return self.now


def _request(tmp_path, body):
    class Request:
        app = SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(player=PLAYER, root=tmp_path)))
        headers = {}

        async def json(self):
            return body
    return Request()


@pytest.fixture
def server(monkeypatch, tmp_path):
    """The real /api/confirm routes with a frozen clock and a fake own-history/head; returns a call helper."""
    clock = Clock()
    world = {"rows": [], "head": None, "history_calls": [], "pages": None}
    monkeypatch.setattr(confirm, "time", SimpleNamespace(time=clock.time), raising=False)
    monkeypatch.setattr(confirm, "_require_same_origin", lambda _: None)
    monkeypatch.setattr(confirm.client_limits, "limit", lambda *_: None)
    from ceres import harvest_routing_bridge

    def history(account, start):
        world["history_calls"].append((account, start))
        if world["pages"] is not None:
            return world["pages"].get(start, [])
        if world.get("log") is not None:      # fix round 2: a real index-contiguous account history (condenser slicing)
            log = world["log"]
            if start < 0:
                return log[-101:]
            return [row for row in log if start - min(100, start) <= row[0] <= start]
        return world["rows"]
    monkeypatch.setattr(harvest_routing_bridge, "_history", history)
    monkeypatch.setattr(confirm, "hive_head_time", lambda: world["head"] if world["head"] is not None else clock.now, raising=False)

    class FrozenDate(confirm.datetime):
        @classmethod
        def now(cls, tz=None):
            return confirm.datetime.fromtimestamp(clock.now, tz=tz)
    monkeypatch.setattr(confirm, "datetime", FrozenDate)

    def call(route, body):
        handler = {"find": confirm.find, "intent": getattr(confirm, "intent", None), "verdict": confirm.verdict}[route]
        if handler is None:
            raise AssertionError(f"/api/confirm/{route} does not exist")
        return asyncio.run(handler(_request(tmp_path, body)))
    return SimpleNamespace(clock=clock, world=world, call=call, tmp=tmp_path)


def find_body(ops, clock, **extra):
    # Both the pre-round contract (since, expiry_check) and the round-1 contract (attempt, answered) are sent, so the same
    # test runs on both versions.
    return {"operations": ops, "since": stamp(clock.now) + "Z", "attempt": ATTEMPT, "answered": True, **extra}


def test_f7_clock_skew_a_browser_clock_ten_minutes_ahead_still_finds_the_action(server):
    """Clock skew (astra "Browser/server clock skew"): the browser's `since` is 10 min in the server's future. The server
    must judge every window by its own clock. Before: 422 'Confirmation search window expired' -> the finder stops."""
    ops = [op("Skew000001")]
    server.world["rows"] = [[7, {"trx_id": "e" * 40, "op": ops[0], "timestamp": stamp(server.clock.now)}]]
    body = find_body(ops, server.clock)
    body["since"] = stamp(server.clock.now + 600) + "Z"
    try:
        answer = server.call("find", body)
    except confirm.HTTPException as exc:
        answer = {"status": f"http-{exc.status_code}"}
    assert answer["status"] == "found" and answer["tx_id"] == "e" * 40, answer


def test_low6_f6_the_server_counts_at_most_six_normal_reads_per_attempt_in_ten_minutes(server):
    """ 'a few bounded reads within about 10 minutes', enforced by the SERVER per attempt (two tabs, a reload or a
    lost localStorage never add reads). The client asks every second for 11 minutes. Before: every ask read history."""
    ops = [op("Budget0001")]
    server.world["rows"] = []
    for _ in range(660):
        try:
            server.call("find", find_body(ops, server.clock))
        except confirm.HTTPException:
            pass
        server.clock.now += 1
    assert 1 <= len(server.world["history_calls"]) <= 6, len(server.world["history_calls"])


def test_f6_two_tabs_share_one_budget(server):
    """Two tabs ask at the same moment: one read, the other is told when to ask again. Before: two reads."""
    ops = [op("TwoTabs001")]
    server.call("find", find_body(ops, server.clock))          # read 1 at offset 0
    server.clock.now += 3                                      # offset 2 s is due (the next one, 4 s, is not)
    first = server.call("find", find_body(ops, server.clock))
    second = server.call("find", find_body(ops, server.clock))
    assert len(server.world["history_calls"]) == 2, server.world["history_calls"]
    assert second["status"] == "wait" and second["next_in_s"] > 0, (first, second)


def history_log(begin, *, count=50, newest_after=None):
    """Fix round 2: an index-contiguous own history of `count` other operations, the oldest two hours before `begin`;
    with `newest_after`, one more own operation at that moment (the newest edge)."""
    rows = [[i, {"trx_id": f"{i + 1:040x}", "op": op(f"Other{i:05d}"), "timestamp": stamp(begin - 7200 + i)}] for i in range(count)]
    if newest_after is not None:
        rows.append([count, {"trx_id": "e" * 39 + "1", "op": op("Newer00001"), "timestamp": stamp(newest_after)}])
    return rows


def _signing_session(server, ops, *, answered_after=0.0):
    # Fix round 2: the begin (the wallet opened) and a CLOSING answer (P0-1), both timed by the server.
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "begin"})
    server.clock.now += answered_after
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "answered", "closed": True})
    for _ in range(6):                                          # the normal window, nothing found
        server.call("find", find_body(ops, server.clock))
        server.clock.now += 100


def test_f7_expiry_is_anchored_to_the_wallet_answer_not_the_request(server):
    """Keychain held the request 30 min before answering (no id). The expiry read may happen only 3,900 s after the
    ANSWER (server clock), never 3,900 s after the browser's pre-request time. Before: no /api/confirm/intent, and the
    expiry read was allowed from the browser's `since`."""
    ops = [op("Anchor0001")]
    begin = server.clock.now
    server.world["log"] = history_log(begin, newest_after=begin + 1800 + 3700)
    _signing_session(server, ops, answered_after=1800)
    answered = begin + 1800
    server.clock.now = begin + 3900 + 60                         # past the request's expiry, not the answer's
    early = server.call("find", find_body(ops, server.clock, expiry_check=True))
    assert early["status"] == "wait", early
    server.clock.now = answered + 3900 + 1
    calls = len(server.world["history_calls"])
    final = server.call("find", find_body(ops, server.clock, expiry_check=True))
    assert final["status"] == "absent" and len(server.world["history_calls"]) > calls, final


def test_f7_absence_needs_the_newest_edge_the_node_head_past_the_expiry(server):
    """A lagging history node: its pages reach back past the signing moment (oldest edge covered) but its newest own
    operation is before the transaction's latest possible inclusion. Fix round 2 (astra-r2 P0-2): a fresh head from
    another node proves nothing; absence needs the newest edge IN the same history response. Before round 2: a head from
    get_dynamic_global_properties (another request, possibly another node) let absence through."""
    ops = [op("HeadLag001")]
    begin = server.clock.now
    server.world["log"] = history_log(begin)                     # nothing newer than two hours before signing
    _signing_session(server, ops)
    server.clock.now = begin + 3900 + 5
    server.world["head"] = None                                   # any other node's head is fresh: irrelevant now
    answer = server.call("find", find_body(ops, server.clock, expiry_check=True))
    assert answer["status"] not in ("absent",) and not (answer["status"] == "not_found" and answer.get("covered")), answer
    server.world["log"] = history_log(begin, newest_after=begin + 3700)   # the account has a newer own operation
    server.clock.now += confirm.OPEN_RETRY_S + 1 if hasattr(confirm, "OPEN_RETRY_S") else 61
    assert server.call("find", find_body(ops, server.clock, expiry_check=True))["status"] == "absent"


def test_medium4_uncovered_expiry_read_pages_back_over_own_history_and_finds_the_action(server):
    """MEDIUM-4: a busy account -- the latest page no longer reaches the signing moment (covered:false). The server pages
    back over the signer's OWN history, only as far as the signing moment, and finds the action. Before: not_found,
    covered:false -> pending for ever."""
    ops = [op("Busy000001")]
    begin = server.clock.now
    tx = "9" * 40
    log = [[i, {"trx_id": f"{i + 1:040x}", "op": op(f"Noise{i:05d}"), "timestamp": stamp(begin - 4000 + i)}] for i in range(0, 299)]
    log.append([299, {"trx_id": tx, "op": ops[0], "timestamp": stamp(begin + 30)}])
    log += [[i, {"trx_id": f"{i + 1:040x}", "op": op(f"Noise{i:05d}"), "timestamp": stamp(begin + 300 + i)}] for i in range(300, 501)]
    server.world["log"] = log
    _signing_session(server, ops)
    server.clock.now = begin + 3900 + 5
    answer = server.call("find", find_body(ops, server.clock, expiry_check=True))
    assert answer["status"] == "found" and answer["tx_id"] == tx, answer
    assert all(account == PLAYER for account, _ in server.world["history_calls"])          # own history only
    assert len([start for _, start in server.world["history_calls"] if start != -1]) <= 3   # bounded to the signing moment


def test_f8_sps_token_transfer_record_is_read_as_the_game_stores_it():
    """F8: the game stores an `sm_sps` transfer as type token_transfer with the params as data (the verified bridge shape).
    Before: 'does not match the signed operation' -> pending for ever."""
    params = {"to": "player0017", "qty": 5, "token": "SPS", "memo": "lpsid:7", "n": "ab" * 8}
    transfer = op("", op_id="sm_sps", active=True, payload={"action": "token_transfer", "params": params})
    record = {"id": "c" * 40, "player": PLAYER, "type": "token_transfer", "data": json.dumps(params), "success": True, "result": "{}"}
    assert confirm.game_op_verdict(record, PLAYER, transfer, "c" * 40)["state"] == "confirmed"


def test_f8_nested_success_without_top_level_success_is_confirmed():
    land = op("Nested0001")
    record = {"id": "d" * 40, "player": PLAYER, "type": "land_operation", "data": land[1]["json"],
              "result": json.dumps({"success": True, "result": {"success": True, "data": {}}})}
    assert confirm.game_op_verdict(record, PLAYER, land, "d" * 40)["state"] == "confirmed"


def test_f8_schema_drift_stays_pending_and_is_flagged_distinct_from_lag():
    land = op("Drift00001")
    record = {"id": "d" * 40, "player": PLAYER, "type": "land_operation_v2", "data": land[1]["json"], "success": True}
    verdict = confirm.game_op_verdict(record, PLAYER, land, "d" * 40)
    assert verdict["state"] == "pending" and verdict.get("drift") is True, verdict
    assert confirm.game_op_verdict(None, PLAYER, land, "d" * 40).get("drift") is None


# MEDIUM-3: routing /v2/unsent rests on the server's own proof
def test_medium3_unsent_without_the_servers_own_absence_proof_is_refused(tmp_path, monkeypatch):
    """Aletheia MEDIUM-3 (): a browser's claim that a no-id bundle was never sent must not stop the bundle and
    release its shares. Before: past expiry, mark_unsent accepted the bare claim."""
    from ceres import harvest_routing as routing, routing_runs_v2 as runs
    from tests.ceres.test_routing_e2e_dead_ends import PLAYER as RPLAYER, _preview
    clock = [1_800_000_000.0]
    monkeypatch.setattr(runs.time, "time", lambda: clock[0])
    with runs.locked(tmp_path, RPLAYER) as folder:
        record = runs.create(folder, RPLAYER, _preview(), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        clock[0] += runs.TX_EXPIRY_SECONDS + 1
        with pytest.raises(routing.RoutingError):
            runs.mark_unsent(folder, record, bundle_id)                       # the browser's word alone
        assert record["bundles"][bundle_id]["state"] == "claimed"
        operations = record["bundles"][bundle_id]["claim"]["operations"]
        wrong = {"status": "absent", "digest": "0" * 64}
        with pytest.raises(routing.RoutingError):
            runs.mark_unsent(folder, record, bundle_id, absence_proof=wrong)  # a proof for other operations
        proof = {"status": "absent", "digest": runs.absence_digest(RPLAYER, operations)}
        runs.mark_unsent(folder, record, bundle_id, absence_proof=proof)
        assert record["bundles"][bundle_id]["state"] == "stopped"


def test_medium3_the_proof_is_the_servers_expiry_read_bound_to_the_exact_operations(server):
    """The digest the routing server checks is the confirm ledger's own, written only by its own expiry read."""
    from ceres import routing_runs_v2 as runs
    ops = [op("Route00001")]
    request = _request(server.tmp, {})
    assert confirm.ledger_absence(request, PLAYER, ATTEMPT, ops) is None          # unknown attempt: no proof
    begin = server.clock.now
    server.world["log"] = history_log(begin, newest_after=begin + 3700)
    _signing_session(server, ops)
    assert confirm.ledger_absence(request, PLAYER, ATTEMPT, ops)["status"] != "absent"   # window only: no proof yet
    server.clock.now = begin + 3900 + 5
    assert server.call("find", find_body(ops, server.clock))["status"] == "absent"
    proof = confirm.ledger_absence(request, PLAYER, ATTEMPT, ops)
    assert proof == {"status": "absent", "digest": runs.absence_digest(PLAYER, ops)}
    assert confirm.ledger_absence(request, PLAYER, ATTEMPT, [op("Route00002")]) is None   # other operations


# F1: the settle route no longer reads own history on every settlement read
def test_f1_f7_settle_reads_own_history_for_the_funding_memo_only_when_asked(monkeypatch):
    """F7 (astra: 'Funding settlement repeatedly reads own history too'): a current receipt's lost SPS transfer is found
    by the shared confirmer; the settle route reads the player's own history only for a legacy receipt that asks once.
    Before: every settle read of an unfunded SPS sale read own history."""
    from ceres import routes_wallet_lp as lp, harvest_routing_bridge
    calls = []
    monkeypatch.setattr(lp, "_require_same_origin", lambda _: None, raising=False)
    monkeypatch.setattr(lp.client_limits, "limit", lambda *_: None)
    monkeypatch.setattr(harvest_routing_bridge, "_history", lambda account, start: calls.append(account) or [])
    monkeypatch.setattr(lp, "lookup_trx_info", lambda *_: {"id": "x"})
    effects = {"external_swap_required": True, "pending_swap_id": 7, "pending_external_amount": "5", "external_transfer_account": "player0017"}
    monkeypatch.setattr(lp, "_verify", lambda *_: {"status": "confirmed", "game_result": effects})
    monkeypatch.setattr(harvest_routing_bridge, "settle", lambda *a, **k: {"state": "pending"})

    async def body_of(_request):
        return {"tx_id": "a" * 40, "nonce": "ab" * 8, "kind": "swap_sell", "expected_payload": {"resource_symbol": "SPS"}}
    monkeypatch.setattr(lp, "_body", body_of)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(player="alice"))), headers={})
    monkeypatch.setattr(lp, "player_for_request", lambda *_: "alice")
    for _ in range(3):
        asyncio.run(lp.settle_swap(request))
    assert calls == [], calls


def test_shape28_five_slow_game_lookups_answer_together_within_the_clients_bound(monkeypatch):
    """Astra shape 28: the per-op game reads of a five-op transaction ran one after another, so slow lookups could exceed
    the client's bound on every read. They now run together. Before: about five lookup times."""
    import time as real_time
    ops = [op(f"Slow0000{i}0") for i in range(5)]
    monkeypatch.setattr(confirm, "_require_same_origin", lambda _: None)
    monkeypatch.setattr(confirm.client_limits, "limit", lambda *_: None)

    def slow(_get, lookup_id):
        real_time.sleep(0.4)
        return None
    monkeypatch.setattr(confirm, "lookup_trx_info", slow)

    class Request:
        app = SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(player=PLAYER)))

        async def json(self):
            return {"tx_id": "a" * 40, "operations": ops}
    started = real_time.monotonic()
    answer = asyncio.run(confirm.verdict(Request()))
    assert [row["state"] for row in answer["operations"]] == ["pending"] * 5
    assert real_time.monotonic() - started < 1.2, real_time.monotonic() - started


def test_f7_the_finders_first_read_alone_anchors_the_expiry_at_the_wallet_answer(server):
    """The finder's first read (at the wallet's answer) creates the server's record when no begin beacon arrived.
    Keychain held the request 30 min: the expiry read is allowed only 3,900 s after that first read, by the server's
    clock. Fix round 2 (astra-r2 P0-1, aletheia-r2 LOW-2): without the server's own begin, the oldest edge is unknown
    (the browser's elapsed duration is no longer trusted), so the expiry read may FIND the action but never prove
    absence."""
    ops = [op("Anchor0002")]
    begin = server.clock.now
    server.world["log"] = history_log(begin, newest_after=begin + 1800 + 3700)
    server.clock.now = begin + 1800                              # the wallet answers (no id) after 30 min
    server.call("find", find_body(ops, server.clock, elapsed_ms=1800 * 1000, closed=True))
    for _ in range(5):
        server.clock.now += 100
        server.call("find", find_body(ops, server.clock))
    server.clock.now = begin + 3900 + 60
    assert server.call("find", find_body(ops, server.clock))["status"] == "wait"
    server.clock.now = begin + 1800 + 3900 + 1
    assert server.call("find", find_body(ops, server.clock))["status"] == "unproven"
