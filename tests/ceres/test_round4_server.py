""" fix round 4, server side (astra-review-r4.md , , , ; aletheia-verdict-r4.md 1, 2).

Each test drives the REAL /api/confirm routes, the real scanner or /api/op-inclusion with fakes only for the own-history
transport, the Hive node answers and the clock (the reviewers' probes, as tests). No network, no browser. Each failed on
the round-3 tree (snapshot before-r4, build-report "Round 4") unless marked as a control.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from ceres import routes_confirm as confirm
from tests.ceres.test_round1_server import ATTEMPT, PLAYER, _request, find_body, op, server, stamp  # noqa: F401
from tests.ceres.test_round3_server import ATTEMPT_B, expiry, own_log, window

# 15c6a6da5:ceres/static/wallet-lp.js: VERIFY_SCHEDULE_MS, then RECHECK_MS = 15 s while the id is missing (MAX_ROUNDS 400).
SWAP_FAST_S = (0, 2, 4, 8, 12, 18, 26, 36, 50, 70)


def swap_reads(until_s):
    times, index = [], 0
    while True:
        t = SWAP_FAST_S[index] if index < len(SWAP_FAST_S) else 70 + 15 * (index - len(SWAP_FAST_S) + 1)
        if t > until_s or index >= 400:
            return times
        times.append(t)
        index += 1


# without a timely begin token, discovery still reaches the real transaction
def test_r1_a_tokenless_attempt_still_finds_a_transaction_below_its_late_begin(server):
    """Astra r4  probe: 502 own operations, the action at index 0 (time T), the begin reached the server only at
    T+1500 (no token presented), the newest own operation at T+6000. Absence stays impossible, but successive expiry
    passes must reach and FIND the action. Before: three passes all 'unproven/open' with ranges [[110, 501]]."""
    ops = [op("Tokenless1")]
    t = server.clock.now
    server.world["log"] = own_log(t, count=502, target_at=t, ops=ops, newest_after=t + 6000)
    server.clock.now = t + 1500
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "begin"})            # token never presented
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "answered"})          # an ambiguous answer
    window(server, ops)
    server.clock.now = t + 1500 + 3900 + 700
    statuses = []
    for _ in range(12):
        answer = expiry(server, ops)
        statuses.append(answer["status"])
        if answer["status"] in ("absent", "found"):
            break
        server.clock.now += max(answer.get("next_in_s", 5), 5) + 1
    assert "absent" not in statuses, statuses
    assert answer["status"] == "found" and answer["tx_id"] == "7" * 40, statuses


def test_r1_the_tokenless_discovery_depth_is_bounded():
    """The widening doubles from Hive's lifetime and stops at the ledger's keep time (bounded reads, )."""
    depth, steps = confirm.TOKENLESS_DEPTH_S, 0
    while depth < confirm.TOKENLESS_MAX_DEPTH_S:
        depth = min(confirm.TOKENLESS_MAX_DEPTH_S, depth * 2)
        steps += 1
    assert confirm.TOKENLESS_DEPTH_S == confirm.EXPIRY_AFTER_S and steps <= 6 and depth == confirm.LEDGER_KEEP_S


# one attempt's transaction is never given to another
def test_r3_an_id_found_for_the_old_attempt_is_not_handed_to_the_retry(server):
    """Astra r4 : attempt A's transaction was found by the server. The maintainer signs again (attempt B, a real begin).
    B is never answered with A's id from the action-wide cache; when B's own transaction appears, B gets that one.
    Before: B was answered 'found' with A's id at once."""
    ops = [op("TwoAttempt")]
    t = server.clock.now
    tx_a, tx_b = "a1" * 20, "b2" * 20
    server.world["rows"] = [[10, {"trx_id": tx_a, "op": ops[0], "timestamp": stamp(t)}]]
    first = server.call("find", find_body(ops, server.clock))
    assert first["status"] == "found" and first["tx_id"] == tx_a, first
    server.clock.now += 300
    server.call("intent", {"operations": ops, "attempt": ATTEMPT_B, "event": "begin"})
    early = server.call("find", find_body(ops, server.clock, attempt=ATTEMPT_B))
    assert early.get("tx_id") != tx_a, early
    server.world["rows"] = [[10, {"trx_id": tx_a, "op": ops[0], "timestamp": stamp(t)}],
                            [11, {"trx_id": tx_b, "op": ops[0], "timestamp": stamp(server.clock.now)}]]
    for _ in range(10):
        server.clock.now += 2
        answer = server.call("find", find_body(ops, server.clock, attempt=ATTEMPT_B))
        if answer["status"] == "found":
            break
    assert answer["status"] == "found" and answer["tx_id"] == tx_b, answer
    again = server.call("find", find_body(ops, server.clock))
    assert again["tx_id"] == tx_a, again                                    # A keeps its own


def test_r3_the_browser_can_name_ids_of_other_attempts(server):
    """B's wallet returned tx B; the old attempt A's finder names it: A is never given B's id (it keeps looking)."""
    ops = [op("Exclude001")]
    tx_b = "b3" * 20
    server.world["rows"] = [[10, {"trx_id": tx_b, "op": ops[0], "timestamp": stamp(server.clock.now)}]]
    answer = server.call("find", find_body(ops, server.clock, exclude_tx_ids=[tx_b]))
    assert answer["status"] == "wait", answer


def test_r3_a_match_the_browser_attributed_elsewhere_never_proves_absence():
    """The scanner: a match excluded only by the browser keeps the scan from ending `covered` (never proof)."""
    ops = [op("NotProof01")]
    t = 1_800_000_000.0
    log = own_log(t, count=50, target_at=t + 5, ops=ops, newest_after=t + 9000)
    history = lambda account, start: log[-101:] if start < 0 else [r for r in log if start - min(100, start) <= r[0] <= start]  # noqa: E731
    proof = confirm.prove_own_action(PLAYER, ops, earliest=t, until=t + 100, history=history, closed=True, exclude={"7" * 40})
    assert proof["status"] == "unproven", proof
    control = confirm.prove_own_action(PLAYER, ops, earliest=t, until=t + 100, history=history, closed=True,
                                       exclude={"7" * 40}, absence_exclude={"7" * 40})
    assert control["status"] == "absent", control                           # the SERVER gave that id to another attempt


# no read later than the reference swap
def test_r5_the_no_id_finder_is_never_slower_than_the_reference_swap_for_the_whole_hour():
    """Astra r4 : the first server-permitted read at or after any appearance time up to 3,900 s is no later than the
    reference swap's (ten offsets, then every 15 s). Before: appearing at 71 s was read at 480 s (swap: 85 s)."""
    swap = swap_reads(3900)
    worse = []
    for t in range(0, 3900):
        reference = next(o for o in swap if o >= t) if t <= swap[-1] else None
        index = 0
        while confirm.normal_offset(index) < t and confirm.normal_offset(index) <= confirm.NORMAL_UNTIL_S:
            index += 1
        new = confirm.normal_offset(index) if confirm.normal_offset(index) <= confirm.NORMAL_UNTIL_S else None
        if reference is not None and (new is None or new > reference):
            worse.append((t, reference, new))
    assert not worse, worse[:5]


def test_r5_seventy_one_and_eighty_six_seconds_on_the_real_route(server):
    """Astra's table: history first shows the action at 71 s / 86 s; the client asks whenever the server allows. Found
    at 85 s / 100 s, as the swap. Before: 480 s for both."""
    for appear, expected in ((71, 85), (86, 100)):
        ops = [op(f"Speed{appear:05d}")]
        t0 = server.clock.now
        target = [500, {"trx_id": f"{appear:040x}", "op": ops[0], "timestamp": stamp(t0 + appear)}]
        others = [[i, {"trx_id": f"{i + 1:040x}", "op": op(f"Other{i:05d}"), "timestamp": stamp(t0 - 600 + i)}] for i in range(400, 500)]
        found_at = None
        while server.clock.now - t0 < 700:
            server.world["rows"] = others + ([target] if server.clock.now - t0 >= appear else [])
            answer = server.call("find", find_body(ops, server.clock))
            if answer["status"] == "found":
                found_at = server.clock.now - t0
                break
            server.clock.now += max(0.25, float(answer.get("next_in_s", 1)))
        assert found_at is not None and found_at <= expected + 0.5, (appear, found_at)
        server.clock.now += 10_000


def test_r5_a_retry_after_never_asked_gets_a_fresh_fast_window(server):
    """Astra r4  second case: A used up its reads; the maintainer signs again at T+4000 (B's begin). B's first find reads
    at once. Before: next_in_s 3899 (the action budget was spent)."""
    ops = [op("FreshWin01")]
    t = server.clock.now
    server.world["rows"] = []
    for _ in range(300):
        server.call("find", find_body(ops, server.clock, answered=False))
        server.clock.now += 13
    server.clock.now = t + 4000
    server.call("intent", {"operations": ops, "attempt": ATTEMPT_B, "event": "begin"})
    calls = len(server.world["history_calls"])
    answer = server.call("find", find_body(ops, server.clock, attempt=ATTEMPT_B, answered=False))
    assert len(server.world["history_calls"]) == calls + 1 and answer["status"] == "wait" and answer["next_in_s"] <= 2.5, answer


def test_r5_control_an_attempt_id_without_a_begin_still_buys_no_reads(server):
    """Control (MEDIUM-1 kept): a second attempt id that did not begin a re-sign shares the one window and gets no fast
    reads of its own."""
    ops = [op("NoBegin001")]
    server.world["rows"] = []
    server.call("find", find_body(ops, server.clock))
    calls = len(server.world["history_calls"])
    answer = server.call("find", find_body(ops, server.clock, attempt=ATTEMPT_B))
    assert len(server.world["history_calls"]) == calls and answer["status"] == "wait", answer


# Aletheia 1/2: the "never asked" lifetime is the server's own clock
def test_aletheia2_the_find_answer_carries_the_servers_unanswered_age(server):
    """The browser's IN-0888 lifetime ending is measured by the server: `unanswered_for_s` = server seconds since its
    first record of the attempt while no answer is recorded; gone once one is. The browser's `since` (an hour old here)
    never changes it. Before: no such field (the browser used its own clock)."""
    ops = [op("Unanswer01")]
    t = server.clock.now
    server.world["rows"] = []
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "begin"})
    server.clock.now = t + 1800
    body = find_body(ops, server.clock, answered=False)
    body["since"] = stamp(t - 3600) + "Z"
    answer = server.call("find", body)
    assert abs(answer.get("unanswered_for_s", -1) - 1800) < 1, answer
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "answered"})
    later = server.call("find", find_body(ops, server.clock, answered=False))
    assert "unanswered_for_s" not in later, later


# the inclusion route fails over when its first node cannot answer
def _route(server, monkeypatch, first, answers, *, intent=None, token=None, age=0):
    from ceres import hive_rpc, routes_op_preview as preview
    monkeypatch.setattr(preview, "get_transaction", lambda tx_id: first)
    monkeypatch.setattr(preview, "player_for_request", lambda *_: PLAYER)
    monkeypatch.setattr(preview.client_limits, "limit", lambda *_: None)
    asked = []

    def answer_at(host, method, params):
        asked.append((host, method))
        value = answers.get((host, method))
        if value is None:
            raise hive_rpc._NodeUnavailable("down")
        return value
    monkeypatch.setattr(preview, "known_transaction_status", lambda tx, bound: hive_rpc.known_transaction_status(tx, bound, answer_at=answer_at))
    request = _request(server.tmp, {})
    body = {"tx_id": "d" * 40, **({"intent": intent} if intent else {}), **({"begin_token": token} if token else {})}
    json.loads(preview.op_inclusion(body, request).body)
    monkeypatch.setattr(preview, "time", SimpleNamespace(time=lambda: server.clock.now + age))
    return json.loads(preview.op_inclusion(body, request).body), asked


def test_r6_an_unreachable_first_node_still_finds_the_inclusion_on_the_next(server, monkeypatch):
    """Astra r4  probe: the first lookup fails (None, 'lookup_failed'); the next configured node has the transaction.
    The route answers included. Before: included None and zero failover calls."""
    from ceres import hive_rpc
    second = hive_rpc.HIVE_RPC_NODES[1]
    answer, asked = _route(server, monkeypatch, (None, "lookup_failed"),
                           {(second, "condenser_api.get_transaction"): ("result", {"block_num": 4242})})
    assert answer == {"included": True, "block_num": 4242}, (answer, asked)
    assert not any(method.startswith("transaction_status") for _, method in asked), asked   # young: inclusion only


def test_r6_past_the_lifetime_the_failover_can_also_prove_expiry(server, monkeypatch):
    """With a recorded pre-sign begin and past the lifetime, an unreachable first node no longer blocks the
    expired-irreversible proof from the next node (same rules as round 3)."""
    from ceres import hive_rpc
    ops = [op("Failover01")]
    token = server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "begin"})["begin_token"]
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "answered", "closed": True, "begin_token": token})
    second = hive_rpc.HIVE_RPC_NODES[1]
    answers = {(second, "condenser_api.get_transaction"): ("error", {"code": -32003, "message": "Unable to find transaction dddd in database"}),
               (second, "transaction_status_api.find_transaction"): ("result", {"status": "expired_irreversible"})}
    answer, asked = _route(server, monkeypatch, (None, "lookup_failed"), answers, intent=ATTEMPT, token=token, age=3900 + 60)
    assert answer["included"] is False, (answer, asked)


def test_r6_control_an_unavailable_node_alone_is_never_absence(server, monkeypatch):
    """Control: every node down -> unknown (pending), never absent."""
    answer, _ = _route(server, monkeypatch, (None, "lookup_failed"), {}, age=3900 + 60)
    assert answer["included"] is None, answer
