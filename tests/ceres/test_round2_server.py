""" fix round 2, server side (astra-review-r2.md P0-1, P0-2, P1-6; aletheia-verdict-r2.md MEDIUM-1, LOW-2/3).

Each test drives the REAL /api/confirm routes (or /api/op-inclusion) with fakes only for the own-history transport, a
foreign node's head and the clock. Each failed on the round-1 tree (snapshot before-r2, build-report "Fix round 2") and
passes now, except the one control marked as such. No network, no browser.
"""
from __future__ import annotations

import json

import pytest

from ceres import routes_confirm as confirm
from tests.ceres.test_round1_server import ATTEMPT, PLAYER, _request, find_body, op, other_op, server, stamp  # noqa: F401

OTHER_ATTEMPT = "0f0e0d0c0b0a0908"


def log_rows(begin, *, count=50, newest_after=None, extra=()):
    rows = [[i, {"trx_id": f"{i + 1:040x}", "op": other_op(f"Other{i:05d}"), "timestamp": stamp(begin - 7200 + i)}] for i in range(count)]
    for row in extra:
        rows.append([len(rows), row])
    if newest_after is not None:
        rows.append([len(rows), {"trx_id": "e" * 39 + "1", "op": other_op("Newer00001"), "timestamp": stamp(newest_after)}])
    return rows


def begin_and_window(server, ops, *, closed=True, attempt=ATTEMPT, answered=True):
    token = server.call("intent", {"operations": ops, "attempt": attempt, "event": "begin"}).get("begin_token")   # fix round 3 (N1)
    for _ in range(6):
        server.call("find", find_body(ops, server.clock, attempt=attempt, answered=answered, begin_token=token, **({"closed": True} if closed else {})))
        server.clock.now += 100


def expiry_answer(server, ops, **extra):
    return server.call("find", find_body(ops, server.clock, expiry_check=True, **extra))


# P0-1: wallet silence is never a closed request
def test_p0_1_a_request_the_wallet_never_closed_is_never_proven_absent(server):
    """The round-1 client sent `answered: true` after two minutes of SILENCE. The request may still be approved later,
    so the server must never prove absence for it, even with complete coverage and a newer own operation. Before:
    'absent' at answer + 3,900 s (claim released, SPS re-offered)."""
    ops = [op("Silent0001")]
    begin = server.clock.now
    server.world["log"] = log_rows(begin, newest_after=begin + 3800)
    begin_and_window(server, ops, closed=False)
    server.clock.now = begin + 3900 + 700
    answer = expiry_answer(server, ops)
    assert answer["status"] == "unproven" and answer.get("reason") == "open", answer
    assert confirm.ledger_absence(_request(server.tmp, {}), PLAYER, ATTEMPT, ops)["status"] != "absent"


def test_p0_1_control_a_closed_request_is_proven_absent(server):
    """Control (passed before too, NOT claimed as a fix): the same with a wallet answer that closed the request."""
    ops = [op("Closed0001")]
    begin = server.clock.now
    server.world["log"] = log_rows(begin, newest_after=begin + 3800)
    begin_and_window(server, ops, closed=True)
    server.clock.now = begin + 3900 + 700
    assert expiry_answer(server, ops, closed=True)["status"] == "absent"


def test_p0_1_without_the_servers_own_begin_absence_is_never_proven(server):
    """The first contact came after signing (no begin beacon): the server cannot know the oldest edge, and the browser's
    elapsed duration is not trusted (LOW-2). Found is still possible; absent never. Before: absent."""
    ops = [op("NoBegin001")]
    begin = server.clock.now
    server.world["log"] = log_rows(begin, newest_after=begin + 3800)
    for _ in range(6):
        server.call("find", find_body(ops, server.clock, elapsed_ms=1000, closed=True))
        server.clock.now += 100
    server.clock.now = begin + 3900 + 700
    assert expiry_answer(server, ops, closed=True)["status"] == "unproven"


# P0-2: the newest edge from the same history response; contiguous pages
def test_p0_2_a_fresh_head_from_another_node_never_yields_absence_from_stale_history(server, monkeypatch):
    """Head from node A is fresh (past the expiry); the history (node B) is stale: its newest own operation is before
    the transaction's latest possible inclusion. Absence must not be claimed. Before: 'absent' (head check only)."""
    ops = [op("Stale00001")]
    begin = server.clock.now
    server.world["log"] = log_rows(begin)                  # newest own op: two hours before signing
    begin_and_window(server, ops)
    server.clock.now = begin + 3900 + 700
    server.world["head"] = server.clock.now               # another node: perfectly fresh
    answer = expiry_answer(server, ops, closed=True)
    assert answer["status"] == "unproven" and answer.get("reason") == "newest-edge", answer


def test_p0_2_an_empty_or_short_older_page_is_never_the_start_of_history(server):
    """A stale/filtered node answers an older page with nothing. That is not 'the beginning of the account'. Before: an
    empty page counted as covered -> absent."""
    ops = [op("Short00001")]
    begin = server.clock.now
    latest = [[i, {"trx_id": f"{i + 1:040x}", "op": other_op(f"Recent{i:04d}"), "timestamp": stamp(begin + 3700 + i)}] for i in range(400, 501)]
    server.world["pages"] = {-1: latest}                   # every older page: [] (stale node)
    begin_and_window(server, ops)
    server.clock.now = begin + 3900 + 700
    answer = expiry_answer(server, ops, closed=True)
    assert answer["status"] == "unproven" and answer.get("reason") == "shape", answer


def test_p0_2_a_page_with_a_gap_is_rejected(server, monkeypatch):
    """Index continuity: an older page missing rows (a pruned/filtered response) cannot complete the proof."""
    ops = [op("Gap0000001")]
    begin = server.clock.now
    rows = [[i, {"trx_id": f"{i + 1:040x}", "op": other_op(f"Other{i:05d}"), "timestamp": stamp(begin - 600 + i * 8)}] for i in range(300)]
    rows.append([300, {"trx_id": "e" * 39 + "1", "op": other_op("Newer00001"), "timestamp": stamp(begin + 3800)}])
    full = {row[0]: row for row in rows}     # rows from index ~60 on are after the signing: older pages are needed

    def pages(start):
        if start < 0:
            return rows[-101:]
        page = [full[i] for i in range(start - min(100, start), start + 1) if i in full]
        return [row for row in page if row[0] % 7]            # every seventh row missing
    server.world["pages"] = None
    server.world["log"] = None
    from ceres import harvest_routing_bridge
    calls = server.world["history_calls"]
    monkeypatch.setattr(harvest_routing_bridge, "_history", lambda account, start: calls.append((account, start)) or pages(start))
    begin_and_window(server, ops)
    server.clock.now = begin + 3900 + 700
    answer = expiry_answer(server, ops, closed=True)
    assert answer["status"] == "unproven", answer


# MEDIUM-1: the read budget belongs to the ACTION
def test_medium1_a_fresh_attempt_id_buys_no_new_reads(server):
    """Two attempt ids for the same exact operations ask every second for eleven minutes. Before: six reads per
    attempt id (twelve). After: one budget for the action (at most six)."""
    ops = [op("Budget0002")]
    server.world["rows"] = []
    for second in range(660):
        for attempt in (ATTEMPT, OTHER_ATTEMPT):
            try:
                server.call("find", find_body(ops, server.clock, attempt=attempt))
            except confirm.HTTPException:
                pass
        server.clock.now += 1
    # Revision 6 §4: each admitted epoch may use one fast window, while all
    # later checks share the account tail. A second id cannot double 11 minutes
    # of reads or exceed the rolling-hour ceiling.
    calls = len(server.world["history_calls"])
    assert 1 <= calls <= confirm.normal_reads_until(660) + 10, calls
    assert calls <= 600


# LOW-2: browser times never shape the absence proof
def test_low2_a_browser_expiration_cannot_bring_the_expiry_read_forward(server):
    """The browser claims the signed transaction expires 60 s after its begin. Only the server's clock and a closed
    request count: no expiry read (and no absence) before closure + 3,900 s. Before: 'absent' about six minutes in."""
    ops = [op("Expire0001")]
    begin = server.clock.now
    server.world["log"] = log_rows(begin, newest_after=begin + 200)
    expiration = stamp(begin + 60) + "Z"
    begin_and_window(server, ops)
    # Fix round 4 (: the swap's 15-second reads go on until the expiry read; none of them may be (or start) an
    # expiry read before closure + 3,900 s, whatever the browser's expiration says.
    statuses = set()
    server.clock.now = begin + 900
    while server.clock.now < begin + 3800:
        answer = server.call("find", find_body(ops, server.clock, closed=True, expiration=expiration))
        statuses.add(answer["status"])
        server.clock.now += 15
    assert statuses == {"wait"}, statuses


def test_low3_inclusion_never_ages_a_transaction_by_the_browsers_created_at(server, monkeypatch):
    """/api/op-inclusion: a 'not found' transaction whose browser created_at is two hours old, first heard of by the
    server just now, is not absent yet. Before: included=false (the duplicate-sign guard released)."""
    from ceres import routes_op_preview as preview
    monkeypatch.setattr(preview, "get_transaction", lambda tx_id: (None, "not_found"))
    monkeypatch.setattr(preview, "player_for_request", lambda *_: PLAYER)
    monkeypatch.setattr(preview.client_limits, "limit", lambda *_: None)
    request = _request(server.tmp, {})
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()   # the old rule compared with the real clock
    body = {"tx_id": "d" * 40, "created_at": old, "answered_ms_ago": 7200 * 1000}
    first = json.loads(preview.op_inclusion(body, request).body)
    assert first["included"] is None, first


# P1-6: a persisted cursor; bounded work per call
def test_p1_6_more_than_five_thousand_own_operations_continue_from_a_persisted_cursor(server):
    """6,500 own operations after signing. Revision 6 §4 coverage fairness limits each
    worker turn to two pages, persists the cursor, then requeues it. The many
    turns must still find the action without declaring a false absence."""
    ops = [op("Deep000001")]
    begin = server.clock.now
    tx = "7" * 40
    rows = [[i, {"trx_id": f"{i + 1:040x}", "op": other_op(f"Old{i:07d}"), "timestamp": stamp(begin - 7200 + i)}] for i in range(100)]
    rows.append([100, {"trx_id": tx, "op": ops[0], "timestamp": stamp(begin + 20)}])
    rows += [[i, {"trx_id": f"{i + 1:040x}", "op": other_op(f"New{i:07d}"), "timestamp": stamp(begin + 30 + i * 0.5)}] for i in range(101, 6601)]
    server.world["log"] = rows
    begin_and_window(server, ops)
    server.clock.now = begin + 3900 + 700
    statuses, per_call = [], []
    for _ in range(100):
        before = len(server.world["history_calls"])
        answer = expiry_answer(server, ops, closed=True)
        per_call.append(len(server.world["history_calls"]) - before)
        statuses.append(answer["status"])
        if answer["status"] == "found":
            break
        server.clock.now += max(answer.get("next_in_s", 5), 5) + 1
    assert statuses[-1] == "found" and "unprovable" not in statuses, statuses
    assert max(per_call) <= 2, per_call
