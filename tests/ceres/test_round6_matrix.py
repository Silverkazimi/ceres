""" r6: finite interleavings through the production confirmation reducers.

Eight templates x C(8, 4) x seven clock partitions x two transport latencies.
The matrix is intentionally in-process: no browser, network, sleeps, or surrogate
scheduler state machine. The 55 s bound applies to this test on the CI runner.
"""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import combinations
from time import perf_counter

from ceres import routes_confirm as confirm

TEMPLATES = (
    ("marked A / delayed B", "exact", "none"),
    ("unmarked A/B duplicate", "semantic", "semantic"),
    ("excluded page two", "none", "semantic"),
    ("known id expiry", "none", "none"),
    ("silent sign only routing", "none", "none"),
    ("unanswered direct Keychain", "none", "none"),
    ("local versus relay timeout", "none", "none"),
    ("reload changed terms", "exact", "semantic"),
)
PARTITIONS = (0, 14, 15, 3899, 3900, 3901, 3915)
LATENCIES = (0, 16)


def _ops(nonce):
    return [["custom_json", {"id": "sm_land_operation", "json": {"n": nonce, "amount": "1.000 DEC"},
                              "required_posting_auths": ["alice"], "required_auths": []}]]


def _page(ops, index, at, tx):
    return [[index, {"timestamp": datetime.fromtimestamp(at, timezone.utc).isoformat(),
                     "trx_id": tx, "op": ops[0]}]]


def _trace(template, order, boundary, latency):
    name, match_a, match_b = template
    row = {"action_id": "a" * 32, "digest": "d", "first_seen": 0, "revision": 1,
           "revisions": [{"revision": 1, "operations": _ops("abcdefghij")}],
           "attempts": {}, "attempt_ids": [], "matches": {}}
    budget = {"fast_calls": [], "tail_calls": [], "coverage_calls": [], "fast_remaining": {},
              "inflight": {}, "tail_last": None, "coverage_queue": [], "coverage_active": None}
    attempts = ("a" * 16, "b" * 16)
    ops = (_ops("abcdefghij"), _ops("klmnopqrst"))
    tx = ("1" * 40, "2" * 40)
    positions = set(order)
    stages = [0, 0]
    found = set()
    for position in range(8):
        actor = 0 if position in positions else 1
        stage = stages[actor]
        stages[actor] += 1
        now = (boundary * position) / 7 + (latency if stage == 2 else 0)
        attempt = attempts[actor]
        if stage == 0:
            confirm._apply_event(row, {"operations": ops[actor]}, now, "begin", attempt)
            row["attempts"][attempt]["read_mode"] = "fast"
            row["attempts"][attempt]["fast_anchor"] = now
            row["attempts"][attempt]["fast_index"] = 0
            budget["fast_remaining"][f"{row['action_id']}:{attempt}"] = 20
        elif stage == 1:
            if name != "unanswered direct Keychain" or actor == 0:
                confirm._apply_event(row, {"operations": ops[actor], "answered": True}, now, "answered", attempt)
        elif stage == 2:
            plan = confirm._account_read_plan(row, budget, attempt, now)
            assert "read" in plan or "wait" in plan
            if "token" in plan:
                budget["inflight"].pop(plan["token"])
            kind = (match_a, match_b)[actor]
            if kind != "none":
                observed = ops[actor] if kind == "exact" else _ops("mnopqrstuv")
                candidates = confirm._history_candidates(_page(observed, actor + 1, now, tx[actor]),
                                                         "alice", row["revisions"], 0)
                if candidates and candidates[0]["eligible"]:
                    found.add(candidates[0]["tx_id"])
                    row["matches"][tx[actor]] = candidates[0]
            assert len(budget["inflight"]) <= 3
            assert len(budget["fast_calls"]) + sum(budget["fast_remaining"].values()) <= 360
        else:
            rec = row["attempts"][attempt]
            rec["gen"] = 0
            rec["expiry"] = {"run": attempt, "status": "running"}
            rec["closed_at"] = now
            rec["begin_confirmed"] = True
            result = {"status": "found", "tx_id": next(iter(found))} if found else {"status": "unproven", "reason": "depth"}
            confirm.expiry_settle(result, attempt, attempt, 0)(row)
        # A retained match dominates every attempt; no reducer may leave an absence proof.
        if found:
            assert all((rec.get("expiry") or {}).get("status") != "absent" for rec in row["attempts"].values())
        assert len(set(row["attempt_ids"])) == len(row["attempt_ids"])
    assert stages == [4, 4]


def test_round6_production_seam_matrix_7840_traces_under_55_seconds():
    started = perf_counter()
    count = 0
    for template in TEMPLATES:
        for positions in combinations(range(8), 4):
            for boundary in PARTITIONS:
                for latency in LATENCIES:
                    _trace(template, positions, boundary, latency)
                    count += 1
    assert count == 8 * 70 * 7 * 2 == 7840
    assert perf_counter() - started < 55
