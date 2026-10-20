""" phase 2: the live check of kept stage plans (ceres/stage_plans.py, /api/stage-plans/check).

Run in its own queue, never on the maintainer's laptop (golden rule 6).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ceres import capture as capture_mod
from ceres.app import create_app
from ceres.config import CeresConfig
from ceres.stage_plans import check_plans

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
PLOT_A = "I-1-f9a279e5354899"
PLOT_B = "I-2-97d9870f847ae4"


def _report(*, workers=(), cards=()):
    return {
        "generated_at": "2026-09-12T11:00:00Z",
        "plots": [{"deed_uid": PLOT_A}, {"deed_uid": PLOT_B}],
        "workers": list(workers),
        "cards": list(cards),
        "bonus_assets": [],
        "query": {"player": "player0005"},
    }


def _worker(ref, deed, slot):
    return {"asset_ref": ref, "uid": ref, "stake_type_uid": "STK-LND-WKR", "stake_ref_uid": deed, "slot": slot, "name": ref}


def _card(ref, *, end=None, survival=None):
    return {"asset_ref": ref, "uid": ref, "classification": {"bucket": "stake_expired_or_historical" if end else "unstaked_apparent"},
            "facts": {"uid": ref, "card_detail_id": 79, "stake_end_date": end, "survival_date": survival, "name": ref}}


def _plan(key, action, ref, deed=PLOT_A, slot=0, evict=None):
    return {"key": key, "deed_uid": deed, "group_id": "worker", "slot_index": slot, "action": action, "asset_ref": ref, "evict_asset_ref": evict}


def _one(report, plan):
    return check_plans(report, [plan], account="player0005", now=NOW)[0]


def test_unknown_card_and_plot_are_dropped_with_a_reason():
    report = _report()
    assert _one(report, _plan("a", "stake", "ghost"))["reason"] == "card_gone"
    assert _one(report, _plan("b", "stake", "ghost", deed="I-9-zzzzzzzzzzzzzz"))["reason"] == "plot_gone"


def test_idle_ready_card_is_ok_and_green():
    verdict = _one(_report(cards=[_card("idle", end="2026-09-01T00:00:00Z")]), _plan("a", "stake", "idle"))
    assert verdict["verdict"] == "ok" and verdict["cooldown"]["ready"] is True and verdict["location"] is None


def test_cooling_card_is_ok_but_red():
    verdict = _one(_report(cards=[_card("cool", end="2026-09-13T00:00:00Z")]), _plan("a", "stake", "cool"))
    assert verdict["verdict"] == "ok" and verdict["cooldown"]["state"] == "cooling"


def test_card_staked_on_another_plot_is_ok_with_its_live_location():
    verdict = _one(_report(workers=[_worker("moving", PLOT_B, 2)]), _plan("a", "stake", "moving"))
    assert verdict["verdict"] == "ok" and verdict["cooldown"]["state"] == "staked"
    assert verdict["location"]["deed_uid"] == PLOT_B


def test_stake_already_in_place_is_done():
    assert _one(_report(workers=[_worker("here", PLOT_A, 1)]), _plan("a", "stake", "here", slot=0))["verdict"] == "done"


def test_stake_into_a_taken_slot_is_dropped():
    report = _report(workers=[_worker("other", PLOT_A, 1)], cards=[_card("idle", end="2026-09-01T00:00:00Z")])
    assert _one(report, _plan("a", "stake", "idle", slot=0))["reason"] == "slot_taken"


def test_unstake_verdicts():
    report = _report(workers=[_worker("here", PLOT_A, 1), _worker("away", PLOT_B, 1)], cards=[_card("idle", end="2026-09-01T00:00:00Z")])
    assert _one(report, _plan("a", "unstake", "here"))["verdict"] == "ok"
    assert _one(report, _plan("b", "unstake", "away"))["reason"] == "card_moved"
    assert _one(report, _plan("c", "unstake", "idle"))["verdict"] == "done"


def test_route_requires_the_boards_own_capture(tmp_path, monkeypatch):
    report = _report(cards=[_card("idle", end="2026-09-01T00:00:00Z")])
    app = create_app(CeresConfig(root=tmp_path / ".ceres"))
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, type("Ref", (), {"generated_at": report["generated_at"]})()))
    monkeypatch.setattr(capture_mod, "load_card_pool", lambda value: value)
    client = TestClient(app)
    body = {"plans": [_plan("a", "stake", "idle")]}
    headers = {"Origin": "http://testserver"}
    missing = client.post("/api/stage-plans/check?player=player0005", json=body, headers=headers)
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "capture_unknown"
    older = client.post("/api/stage-plans/check?player=player0005", json={**body, "capture_generated_at": "2026-09-01T00:00:00Z"}, headers=headers)
    assert older.status_code == 409 and older.json()["detail"]["code"] == "capture_superseded"
    ok = client.post("/api/stage-plans/check?player=player0005", json={**body, "capture_generated_at": report["generated_at"]}, headers=headers)
    assert ok.status_code == 200 and ok.json()["verdicts"][0]["verdict"] == "ok"


def test_core_removal_waits_for_detail_then_accepts_exact_live_core():
    report = _report()
    plot = report["plots"][0]
    plot["asset_fetch"] = {"status": "not_requested"}
    plot["staking_facts"] = {"is_power_core_staked": True, "is_energized": True}
    plan = {**_plan("core", "unstake", "core-ref"), "group_id": "power_core"}
    assert _one(report, plan)["verdict"] == "needs_assets"
    plot["asset_fetch"]["status"] = "failed"
    assert _one(report, plan)["verdict"] == "needs_assets"
    plot["asset_fetch"]["status"] = "dropped_stale"
    assert _one(report, plan)["verdict"] == "needs_assets"
    plot["asset_fetch"]["status"] = "retrieved"
    report["bonus_assets"] = [{
        "asset_ref": "core-ref", "uid": "ITEM-CORE", "name": "Power Core",
        "stake_type_uid": "STK-LND-PCR", "stake_ref_uid": PLOT_A,
        "asset_kind": "power_core",
    }]
    assert _one(report, plan)["verdict"] == "ok"
    report["bonus_assets"][0]["stake_ref_uid"] = PLOT_B
    assert _one(report, plan)["reason"] == "card_moved"
    report["bonus_assets"] = []
    assert _one(report, plan)["reason"] == "card_gone"


def test_partial_plot_cannot_mark_unstake_done_from_idle_collection_alone():
    report = _report(cards=[_card("here")])
    report["plots"][0]["asset_fetch"] = {"status": "not_requested"}
    assert _one(report, _plan("worker", "unstake", "here"))["verdict"] == "needs_assets"
    report["plots"][0]["asset_fetch"]["status"] = "retrieved"
    assert _one(report, _plan("worker", "unstake", "here"))["verdict"] == "done"
