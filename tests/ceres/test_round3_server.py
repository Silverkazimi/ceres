""" fix round 3, server side (astra-review-r3.md N1, N2, N3, N6, N7; aletheia-verdict-r3.md L1, L2, L4).

Each test drives the REAL /api/confirm routes, the real scanner or /api/op-inclusion with fakes only for the own-history
transport, the Hive node answers and the clock (the reviewers' probes, as tests). No network, no browser. Each failed on
the round-2 tree (snapshot before-r3, build-report "Fix round 3") unless marked as a control.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ceres import routes_confirm as confirm
from tests.ceres.test_round1_server import ATTEMPT, PLAYER, _request, find_body, op, server, stamp  # noqa: F401

ATTEMPT_B = "0b0b0b0b0b0b0b0b"


def own_log(begin, *, count, target_at=None, ops=None, newest_after=None):
    """`count` contiguous own operations, one every 10 s from `begin`; with `target_at` the action sits at index 0."""
    rows = [[i, {"trx_id": f"{i + 1:040x}", "op": op(f"Other{i:05d}"), "timestamp": stamp(begin + i * 10)}] for i in range(count)]
    if target_at is not None:
        rows[0] = [0, {"trx_id": "7" * 40, "op": ops[0], "timestamp": stamp(target_at)}]
    if newest_after is not None:
        rows.append([len(rows), {"trx_id": "e" * 39 + "1", "op": op("Newer00001"), "timestamp": stamp(newest_after)}])
    return rows


def window(server, ops, attempt=ATTEMPT, **extra):
    for _ in range(11):
        server.call("find", find_body(ops, server.clock, attempt=attempt, **extra))
        server.clock.now += 50


def expiry(server, ops, attempt=ATTEMPT, **extra):
    return server.call("find", find_body(ops, server.clock, attempt=attempt, **extra))


# N1: the oldest edge is a begin recorded BEFORE the wallet was asked
def test_n1_a_late_begin_never_becomes_the_oldest_edge(server):
    """Astra r3 N1 probe: the wallet broadcast at T; the begin reached the server only at T+1500 (the browser had stopped
    waiting and opened the wallet, so it never presents a token). 502 own operations, the action at index 0, a newer own
    operation after closure + 1 h. Before: 'absent' (scan stopped at begin - 120 s, the action never read)."""
    ops = [op("LateBegin1")]
    t = server.clock.now
    server.world["log"] = own_log(t, count=502, target_at=t, ops=ops, newest_after=t + 1500 + 3600 + 3000)
    server.clock.now = t + 1500
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "begin"})   # token never presented
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "answered", "closed": True})
    window(server, ops, closed=True)
    server.clock.now = t + 1500 + 3900 + 700
    statuses = []
    for _ in range(6):
        answer = expiry(server, ops, closed=True)
        statuses.append(answer["status"])
        if answer["status"] in ("absent", "found"):
            break
        server.clock.now += max(answer.get("next_in_s", 5), 5) + 1
    assert "absent" not in statuses, statuses
    assert confirm.ledger_absence(_request(server.tmp, {}), PLAYER, ATTEMPT, ops)["status"] != "absent"


def test_n1_a_confirmed_begin_scans_back_to_the_begin_itself(server):
    """With the token presented, the begin is a real pre-sign bound: the scan reaches back past it (less the clock
    margin only), so an action signed one second after the begin is FOUND, never declared absent. Control for the
    rule: no 120 s receipt allowance is needed, the scan stops only below the begin."""
    ops = [op("Confirmed1")]
    t = server.clock.now
    token = server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "begin"})["begin_token"]
    server.world["log"] = own_log(t - 600, count=400, newest_after=t + 3600 + 3000)
    server.world["log"][61] = [61, {"trx_id": "7" * 40, "op": ops[0], "timestamp": stamp(t + 1)}]
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "answered", "closed": True, "begin_token": token})
    log = server.world["log"]
    server.world["log"] = None
    server.world["pages"] = None
    server.world["rows"] = log[-101:]                              # the ten-minute reads see only the latest page
    window(server, ops, closed=True, begin_token=token)
    server.world["rows"] = None
    server.world["log"] = log
    server.clock.now = t + 3900 + 700
    for _ in range(6):
        answer = expiry(server, ops, closed=True, begin_token=token)
        if answer["status"] in ("absent", "found"):
            break
        server.clock.now += max(answer.get("next_in_s", 5), 5) + 1
    assert answer["status"] == "found" and answer["tx_id"] == "7" * 40, answer


def test_n1_the_begin_answer_carries_a_token_only_for_the_attempts_first_contact(server):
    """A begin that arrives after another report of the same attempt is not a pre-sign bound: no token."""
    ops = [op("FirstOnly1")]
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "answered", "closed": True})
    late = server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "begin"})
    assert "begin_token" not in late, late
    fresh = server.call("intent", {"operations": ops, "attempt": ATTEMPT_B, "event": "begin"})
    assert isinstance(fresh.get("begin_token"), str) and len(fresh["begin_token"]) == 32, fresh


# N2 / L1: closure, scan, expiry and proof are per attempt; stale workers are ignored
def test_n2_a_delayed_closure_of_an_old_attempt_never_closes_the_new_one():
    """Astra r3 N2 probe on the real _apply_event: begin A, close A, begin B, delayed close A. Before: B's ledger was
    closed by A's late beacon (closed_at set, expiry reset) although B's wallet prompt was still open."""
    row = {"digest": "d", "first_seen": 1700000000, "reads": 0, "windows": 0, "attempt_ids": []}
    confirm._apply_event(row, {"event": "begin"}, 1700000000, "begin", ATTEMPT)
    confirm._apply_event(row, {"closed": True, "answered": True}, 1700000001, "answered", ATTEMPT)
    confirm._apply_event(row, {"event": "begin"}, 1700000002, "begin", ATTEMPT_B)
    confirm._apply_event(row, {"closed": True, "answered": True}, 1700000003, "answered", ATTEMPT)
    b = row["attempts"][ATTEMPT_B]
    assert b.get("closed_at") is None and b.get("answered_at") is None, b
    assert row["attempts"][ATTEMPT]["closed_at"] == 1700000001


def test_n2_an_old_attempts_expiry_worker_never_settles_the_new_attempt(server, monkeypatch):
    """Astra r3 N2 race: attempt A's expiry worker is running (outside the lock) when attempt B begins; A's worker then
    proves A absent. B must stay unproven, and the routing / SPS re-offer proof for B must not be 'absent'. Before: the
    action-wide record took A's 'absent' for every attempt."""
    ops = [op("Race000001")]
    t = server.clock.now
    token = server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "begin"})["begin_token"]
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "answered", "closed": True, "begin_token": token})
    server.world["log"] = own_log(t - 1000, count=90, newest_after=t + 3600 + 3000)
    window(server, ops, closed=True, begin_token=token)
    server.clock.now = t + 3900 + 700
    real = confirm.prove_own_action

    def slow_proof(*args, **kwargs):
        # While A's worker reads history, attempt B of the same operations begins (A was declined locally).
        server.call("intent", {"operations": ops, "attempt": ATTEMPT_B, "event": "begin"})
        return real(*args, **kwargs)
    monkeypatch.setattr(confirm, "prove_own_action", slow_proof)
    first = expiry(server, ops, closed=True, begin_token=token)
    monkeypatch.setattr(confirm, "prove_own_action", real)
    assert first["status"] == "absent", first                      # A's own proof stands for A
    request = _request(server.tmp, {})
    assert confirm.ledger_absence(request, PLAYER, ATTEMPT, ops)["status"] == "absent"
    assert (confirm.ledger_absence(request, PLAYER, ATTEMPT_B, ops) or {}).get("status") != "absent"
    b = server.call("find", find_body(ops, server.clock, attempt=ATTEMPT_B))
    assert b["status"] != "absent", b


def test_n2_a_stale_run_of_the_same_attempt_is_rejected(server, monkeypatch):
    """A worker whose lease ran out (another run started) or whose attempt was reopened (new generation) never records
    'absent'. Before: record() wrote the result unconditionally."""
    ops = [op("Stale00002")]
    t = server.clock.now
    token = server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "begin"})["begin_token"]
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "answered", "closed": True, "begin_token": token})
    server.world["log"] = own_log(t - 1000, count=90, newest_after=t + 3600 + 3000)
    window(server, ops, closed=True, begin_token=token)
    server.clock.now = t + 3900 + 700
    real = confirm.prove_own_action

    def reopened(*args, **kwargs):
        # A wallet answer arrives for the attempt while its worker runs: the absence proof is reopened (reanchor).
        confirm.ledger_update(_request(server.tmp, {}), PLAYER, ops, lambda row: row["attempts"][ATTEMPT].update(gen=row["attempts"][ATTEMPT].get("gen", 0) + 1))
        return real(*args, **kwargs)
    monkeypatch.setattr(confirm, "prove_own_action", reopened)
    answer = expiry(server, ops, closed=True, begin_token=token)
    assert answer["status"] == "unproven" and answer.get("reason") == "stale", answer
    assert confirm.ledger_absence(_request(server.tmp, {}), PLAYER, ATTEMPT, ops)["status"] != "absent"


# N3 / L4: a known id is never-sent only on Hive's expired-irreversible status
def _inclusion(server, monkeypatch, *, answers, intent=None, token=None, age=3900 + 60):
    from ceres import hive_rpc, routes_op_preview as preview
    monkeypatch.setattr(preview, "get_transaction", lambda tx_id: (None, "not_found"))
    monkeypatch.setattr(preview, "player_for_request", lambda *_: PLAYER)
    monkeypatch.setattr(preview.client_limits, "limit", lambda *_: None)
    asked = []

    def answer_at(host, method, params):
        asked.append((host, method))
        value = answers.get((host, method))
        if value is None or isinstance(value, Exception):
            raise hive_rpc._NodeUnavailable("down")
        return value
    monkeypatch.setattr(preview, "known_transaction_status", lambda tx, bound: hive_rpc.known_transaction_status(tx, bound, answer_at=answer_at))
    request = _request(server.tmp, {})
    tx = "d" * 40
    body = {"tx_id": tx, **({"intent": intent} if intent else {}), **({"begin_token": token} if token else {})}
    first = json.loads(preview.op_inclusion(body, request).body)                 # the server's first sight of the id
    monkeypatch.setattr(preview, "time", SimpleNamespace(time=lambda: server.clock.now + age))
    later = json.loads(preview.op_inclusion(body, request).body)
    return first, later, asked


def _begun(server, ops):
    token = server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "begin"})["begin_token"]
    server.call("intent", {"operations": ops, "attempt": ATTEMPT, "event": "answered", "closed": True, "begin_token": token})
    return token


NOT_FOUND = ("error", {"code": -32003, "message": "Unable to find transaction dddd in database"})


def test_n3_one_nodes_not_found_past_the_lifetime_is_not_never_sent(server, monkeypatch):
    """Astra r3 N3: get_transaction 'not found' from one node, 65 min by the server's clock, with a known attempt.
    Without an expired-irreversible status the answer stays unknown. Before: included=false (claim released)."""
    token = _begun(server, [op("Known00001")])
    answers = {("api.hive.blog", "condenser_api.get_transaction"): NOT_FOUND,
               ("api.hive.blog", "transaction_status_api.find_transaction"): ("result", {"status": "unknown"})}
    _, later, _ = _inclusion(server, monkeypatch, answers=answers, intent=ATTEMPT, token=token)
    assert later["included"] is None, later


def test_n3_expired_irreversible_from_the_next_node_proves_it(server, monkeypatch):
    """The first node cannot be read; the second answers both reads itself: id unknown and the expiration bound
    expired_irreversible. That is Hive's own proof: never sent."""
    token = _begun(server, [op("Known00002")])
    answers = {("api.openhive.network", "condenser_api.get_transaction"): NOT_FOUND,
               ("api.openhive.network", "transaction_status_api.find_transaction"): ("result", {"status": "expired_irreversible"})}
    _, later, asked = _inclusion(server, monkeypatch, answers=answers, intent=ATTEMPT, token=token)
    assert later["included"] is False, (later, asked)
    assert ("api.hive.blog", "condenser_api.get_transaction") in asked


def test_n3_a_node_contradicting_itself_is_not_proof(server, monkeypatch):
    """Lookup says unknown, status says in a block: no verdict (pending), and no later node overrides it."""
    token = _begun(server, [op("Known00003")])
    answers = {("api.hive.blog", "condenser_api.get_transaction"): NOT_FOUND,
               ("api.hive.blog", "transaction_status_api.find_transaction"): ("result", {"status": "within_irreversible_block"}),
               ("api.openhive.network", "condenser_api.get_transaction"): NOT_FOUND,
               ("api.openhive.network", "transaction_status_api.find_transaction"): ("result", {"status": "expired_irreversible"})}
    _, later, _ = _inclusion(server, monkeypatch, answers=answers, intent=ATTEMPT, token=token)
    assert later["included"] is None, later


def test_n3_without_a_confirmed_pre_sign_begin_a_known_id_stays_unknown(server, monkeypatch):
    """The status plugin only tracks recent blocks; without the attempt's confirmed begin there is no lower bound for
    the inclusion, so even expired_irreversible is not used. Stays pending."""
    answers = {("api.hive.blog", "condenser_api.get_transaction"): NOT_FOUND,
               ("api.hive.blog", "transaction_status_api.find_transaction"): ("result", {"status": "expired_irreversible"})}
    _, later, _ = _inclusion(server, monkeypatch, answers=answers)
    assert later["included"] is None, later


def test_n3_young_ids_never_ask_hive_for_status(server, monkeypatch):
    """Control: inside the lifetime grace nothing is asked and nothing is decided."""
    token = _begun(server, [op("Known00004")])
    first, _, asked = _inclusion(server, monkeypatch, answers={}, intent=ATTEMPT, token=token, age=10)
    assert first["included"] is None and not any(method.startswith("transaction_status") for _, method in asked), (first, asked)


# N6: no own-history read later than the reference swap's
SWAP_REFERENCE_S = (0, 2, 4, 8, 12, 18, 26, 36, 50, 70)    # 15c6a6da5:ceres/static/wallet-lp.js own-history offsets


def test_n6_the_no_id_finder_is_never_slower_than_the_reference_swap():
    """Astra r3 N6: for every moment the action can first appear in own history within the swap's 70 s, the first
    server-permitted read at or after it is no later than the swap's. Before (0,2,4,10,60,480): appearing at 5 s was
    read at 10 s (swap: 8 s); at 11 s, read at 60 s (swap: 12 s)."""
    worse = []
    for tenth in range(0, 701):
        t = tenth / 10
        swap = next(o for o in SWAP_REFERENCE_S if o >= t)
        new = next((o for o in confirm.NORMAL_OFFSETS_S if o >= t), None)
        if new is None or new > swap:
            worse.append((t, swap, new))
    assert not worse, worse[:5]
    assert len(confirm.NORMAL_OFFSETS_S) <= 11 and confirm.NORMAL_OFFSETS_S[-1] <= confirm.WINDOW_S


def test_n6_a_lost_ack_appearing_at_eleven_seconds_is_found_at_twelve(server):
    """The deterministic scenario of N6 on the real route: the wallet answered without id at 0; the action appears in
    own history at 11 s; the client asks whenever the server allows. Found at 12 s (reference swap: 12 s; before: 60 s)."""
    ops = [op("Speed00011")]
    t0 = server.clock.now
    target = [500, {"trx_id": "9" * 40, "op": ops[0], "timestamp": stamp(t0 + 11)}]
    others = [[i, {"trx_id": f"{i + 1:040x}", "op": op(f"Other{i:05d}"), "timestamp": stamp(t0 - 600 + i)}] for i in range(400, 500)]
    found_at = None
    while server.clock.now - t0 < 120:
        server.world["rows"] = others + ([target] if server.clock.now - t0 >= 11 else [])
        answer = server.call("find", find_body(ops, server.clock))
        if answer["status"] == "found":
            found_at = server.clock.now - t0
            break
        server.clock.now += max(0.25, float(answer.get("next_in_s", 1)))
    assert found_at is not None and found_at <= 12.5, found_at


# N7: a large new top gap never starves the scan
def test_n7_a_large_new_top_gap_is_filled_across_calls_and_the_target_below_is_reached():
    """Astra r3 N7: a saved cursor covers [880, 1000]; the account then adds 2,000 own operations; the action is at
    index 100 (below the old bottom). Each step reads at most PAGES_PER_CALL pages. Before: every step re-read the same
    upper gap and returned the old cursor for ever. After: progress is kept and the action is found."""
    ops = [op("Gap0000002")]
    base = 1_800_000_000.0
    rows = [[i, {"trx_id": f"{i + 1:040x}", "op": op(f"Old{i:07d}"), "timestamp": stamp(base + i)}] for i in range(3001)]
    rows[100] = [100, {"trx_id": "8" * 40, "op": ops[0], "timestamp": stamp(base + 100)}]

    def history(account, start):
        if start < 0:
            return rows[-101:]
        return [row for row in rows if start - min(100, start) <= row[0] <= start]
    scan = {"top": 1000, "bottom": 880, "bottom_time": base + 880}
    statuses = []
    for _ in range(8):
        step = confirm.scan_own_history(PLAYER, ops, scan, earliest=base, history=history)
        statuses.append(step["status"])
        if step["status"] == "found":
            break
        assert step["scan"] != scan, "a step that read pages must keep its progress"
        scan = step["scan"]
    assert statuses[-1] == "found" and step["tx_id"] == "8" * 40, statuses


def test_n7_ranges_merge_into_one_covered_stretch():
    """Control of the cursor arithmetic: touching ranges join; a gap stays a gap."""
    ranges = confirm._add_range([], 900, 1000, 9.0)
    ranges = confirm._add_range(ranges, 0, 100, 0.0)
    assert len(ranges) == 2
    ranges = confirm._add_range(ranges, 101, 899, 1.0)
    assert ranges == [[0, 1000, 0.0]], ranges


# L2: dead code removed
def test_l2_the_browser_clock_absence_helper_is_gone():
    from ceres import routes_op_preview as preview
    assert not hasattr(preview, "_old_enough_to_be_absent")
    assert not hasattr(confirm, "HISTORY_MARGIN_S")
