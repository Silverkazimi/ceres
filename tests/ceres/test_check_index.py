"""the plan check indexes the collection once per request (ceres/stage_plans.py, ceres/op_preview.py).

The index must not change a single verdict or asset record: precedence stays workers, then bonus_assets,
then cards, then unassigned items; the first row of each list wins; Survival facts still merge from the
first matching collection card. The reference below is the pre-index linear search, copied verbatim.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from ceres import op_preview, stage_plans
from ceres.board import RUNI_CARD_DETAIL_ID
from ceres.op_preview import GROUP_STAKE_TYPES, STAKE_TYPE_WORKER, AssetIndex, _asset_record, _uid
from ceres.stage_plans import check_plans

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
A, B, C = "I-1-f9a279e5354899", "I-2-97d9870f847ae4", "I-3-a5bcbc81e4a2ba"


def _legacy_merge(report, asset_ref, facts):
    card = next((row for row in report.get("cards", []) if str(row.get("asset_ref")) == asset_ref), None)
    card_facts = card.get("facts") if isinstance(card, dict) else None
    if not isinstance(card_facts, dict):
        return facts
    for key in ("survival_date", "survival_bracket", "survival_mode_disabled"):
        if key in card_facts:
            facts[key] = card_facts[key]
    return facts


def _legacy_asset_record(report: dict[str, Any], asset_ref: str, group_id: str, index: Any = None) -> dict[str, Any] | None:
    wanted = GROUP_STAKE_TYPES[group_id]
    for row in report.get("workers", []):
        if str(row.get("asset_ref")) == asset_ref:
            facts = _legacy_merge(report, asset_ref, dict(row))
            return {"asset_ref": asset_ref, "uid": _uid(row), "stake_type_uid": row.get("stake_type_uid", STAKE_TYPE_WORKER),
                    "deed_uid": row.get("stake_ref_uid"), "slot": row.get("slot"), "staked": True, "facts": facts, "bucket": "staked"}
    for row in report.get("bonus_assets", []):
        if str(row.get("asset_ref")) == asset_ref:
            facts = _legacy_merge(report, asset_ref, dict(row))
            return {"asset_ref": asset_ref, "uid": _uid(row), "stake_type_uid": row.get("stake_type_uid", wanted),
                    "deed_uid": row.get("stake_ref_uid"), "slot": None, "staked": True, "facts": facts, "bucket": "staked"}
    for row in report.get("cards", []):
        if str(row.get("asset_ref")) == asset_ref:
            facts = dict(row.get("facts") or {})
            if row.get("uid") and not facts.get("uid"):
                facts["uid"] = row["uid"]
            staked = bool(facts.get("stake_ref_uid")) and facts.get("stake_end_date") in (None, "")
            bucket = "staked" if staked else (row.get("classification", {}).get("bucket") or "unstaked_apparent")
            inferred = "STK-LND-RUNI" if facts.get("card_detail_id") == RUNI_CARD_DETAIL_ID else STAKE_TYPE_WORKER
            return {"asset_ref": asset_ref, "uid": _uid(row), "stake_type_uid": facts.get("stake_type_uid", inferred),
                    "deed_uid": facts.get("stake_ref_uid"), "slot": facts.get("slot"), "staked": staked, "facts": facts, "bucket": bucket}
    for family in (report.get("unassigned_items") or {}).values():
        if not isinstance(family, dict):
            continue
        if asset_ref in {str(value) for value in family.get("uids", [])}:
            return {"asset_ref": asset_ref, "uid": asset_ref, "stake_type_uid": wanted, "deed_uid": None, "slot": None,
                    "staked": False, "facts": {}, "bucket": "unstaked_apparent"}
    return None


def _worker(ref, deed, slot, **extra):
    return {"asset_ref": ref, "uid": ref, "stake_type_uid": "STK-LND-WKR", "stake_ref_uid": deed, "slot": slot, "name": ref, **extra}


def _card(ref, *, end=None, survival=None, stake_ref=None, detail=79, bracket=None, name=None):
    facts = {"uid": ref, "card_detail_id": detail, "stake_end_date": end, "survival_date": survival, "name": name or ref}
    if stake_ref:
        facts["stake_ref_uid"] = stake_ref
    if bracket:
        facts["survival_bracket"] = bracket
    return {"asset_ref": ref, "uid": ref, "classification": {"bucket": "stake_expired_or_historical" if end else "unstaked_apparent"}, "facts": facts}


def _mixed_report() -> dict[str, Any]:
    return {
        "generated_at": "2026-09-12T11:00:00Z",
        "plots": [{"deed_uid": A}, {"deed_uid": B}, {"deed_uid": C, "asset_fetch": {"status": "not_requested"}}],
        "workers": [
            _worker("w1", A, 1), _worker("w2", A, 2), _worker("w3", B, 1),
            _worker("dup", B, 3), _worker("dup", A, 4),  # first row wins
            _worker("surv", A, 5),
            {"asset_ref": "runi1", "uid": "runi1", "stake_type_uid": "STK-LND-RUNI", "stake_ref_uid": B, "name": "Runi"},
        ],
        "bonus_assets": [
            {"asset_ref": "pc1", "uid": "pc1", "stake_type_uid": "STK-LND-PC", "stake_ref_uid": A},
            {"asset_ref": "w2", "uid": "w2-bonus", "stake_type_uid": "STK-LND-TOT", "stake_ref_uid": B},  # shadowed by worker
            {"asset_ref": "tot1", "stake_ref_uid": B},  # stake type falls back to the group asked for
        ],
        "cards": [
            _card("idle", end="2026-09-01T00:00:00Z"),
            _card("cool", end="2026-09-13T00:00:00Z"),
            _card("surv", survival="2026-09-20T00:00:00Z", bracket="gold"),  # merged into the worker row
            _card("w1", name="collection copy of w1"),  # shadowed by the worker row, but Survival merge reads it
            _card("idle", end="2026-08-01T00:00:00Z", name="second idle"),  # first card wins
            _card("stuck", stake_ref=B),  # stake ref with no end date reads as staked
            _card("gone", stake_ref=B, end="2026-09-02T00:00:00Z"),  # stale stake ref, idle
            _card("runicard", detail=RUNI_CARD_DETAIL_ID, end="2026-09-01T00:00:00Z"),
            {"asset_ref": 12345, "uid": "num", "facts": {"card_detail_id": 79, "name": "numeric ref"}},
        ],
        "unassigned_items": {"totems": {"uids": ["tot-free", 777]}, "broken": "not a dict", "titles": {"uids": ["tot-free"]}},
        "query": {"player": "player0005"},
    }


def _plan(key, action, ref, deed=A, slot=0, group="worker", evict=None):
    return {"key": key, "deed_uid": deed, "group_id": group, "slot_index": slot, "action": action, "asset_ref": ref, "evict_asset_ref": evict}


MIXED_PLANS = [
    _plan("p1", "stake", "idle", slot=5),
    _plan("p2", "stake", "cool", deed=B, slot=4),
    _plan("p3", "stake", "w3", deed=A, slot=6),
    _plan("p4", "stake", "w1", slot=0),
    _plan("p5", "unstake", "w2"),
    _plan("p6", "unstake", "w3"),
    _plan("p7", "unstake", "idle"),
    _plan("p8", "replace", "idle", slot=0, evict="w1"),
    _plan("p9", "replace", "idle", slot=0, evict="w3"),
    _plan("p10", "replace", "cool", slot=1, evict="idle"),
    _plan("p11", "stake", "dup", deed=B, slot=2),
    _plan("p12", "stake", "dup", deed=A, slot=3),
    _plan("p13", "stake", "surv", deed=B, slot=6),
    _plan("p14", "stake", "pc1", deed=A, group="power_core"),
    _plan("p15", "stake", "pc1", deed=B, group="power_core"),
    _plan("p16", "stake", "tot1", deed=A, group="totem"),
    _plan("p17", "stake", "tot-free", deed=A, group="totem"),
    _plan("p18", "stake", "777", deed=B, group="title"),
    _plan("p19", "stake", "stuck", deed=A, slot=7),
    _plan("p20", "stake", "gone", deed=A, slot=7),
    _plan("p21", "stake", "runicard", deed=A, group="runi"),
    _plan("p22", "stake", "runi1", deed=A, group="runi"),
    _plan("p23", "stake", "12345", deed=A, slot=8),
    _plan("p24", "stake", "ghost"),
    _plan("p25", "stake", "idle", deed="I-9-zzzzzzzzzzzzzz"),
    _plan("p26", "stake", "ghost", deed=C),
    _plan("p27", "unstake", "w1", deed=C),
    _plan("p28", "stake", "idle", deed=C, slot=1),
    _plan("p29", "stake", "idle", group="nonsense"),
    _plan("p30", "teleport", "idle"),
    _plan("p31", "stake", ""),
    _plan("p32", "replace", "cool", slot=0, evict="ghost"),
]


ALL_REFS = ["w1", "w2", "w3", "dup", "surv", "runi1", "pc1", "tot1", "idle", "cool", "stuck", "gone", "runicard",
            "12345", "tot-free", "777", "ghost", "", "None"]


@pytest.mark.parametrize("group", sorted(GROUP_STAKE_TYPES))
def test_indexed_asset_record_matches_the_linear_search_for_every_ref(group):
    report = _mixed_report()
    index = AssetIndex(report)
    for ref in ALL_REFS:
        expected = _legacy_asset_record(report, ref, group)
        assert _asset_record(report, ref, group, index) == expected, ref
        assert _asset_record(report, ref, group) == expected, ref  # unindexed callers (op preview) are unchanged


def test_mixed_plan_set_verdicts_are_identical_before_and_after_the_index(monkeypatch):
    report = _mixed_report()
    after = check_plans(report, MIXED_PLANS, account="player0005", now=NOW)
    with monkeypatch.context() as patch:
        patch.setattr(stage_plans, "_asset_record", _legacy_asset_record)
        before = check_plans(report, MIXED_PLANS, account="player0005", now=NOW)
    assert after == before
    kinds = {v["verdict"] for v in after}
    assert {"ok", "done", "drop", "needs_assets"} <= kinds  # the set really is mixed


def test_check_builds_the_index_once_per_request(monkeypatch):
    built = []
    real = op_preview.AssetIndex

    class Counting(real):
        def __init__(self, report):
            built.append(1)
            super().__init__(report)

    monkeypatch.setattr(stage_plans, "AssetIndex", Counting)
    check_plans(_mixed_report(), MIXED_PLANS, account="player0005", now=NOW)
    assert len(built) == 1


def test_returned_records_are_fresh_copies_not_the_indexed_rows():
    report = _mixed_report()
    index = AssetIndex(report)
    first = _asset_record(report, "surv", "worker", index)
    first["facts"]["name"] = "mutated"
    assert _asset_record(report, "surv", "worker", index)["facts"]["name"] == "surv"
    assert report["workers"][5]["name"] == "surv"
