"""IN-0267 phase 1: planned values for a virtual board plan (ceres/board_plan_eval.py, /api/board-plans/evaluate).

Pure local arithmetic on the board's own capture: the route test fails if any network client is called.
Temporary storage only (tmp_path); never ~/.ceres.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ceres import board_plan_eval
from ceres import capture as capture_mod
from ceres.app import create_app
from ceres.board_plan_eval import evaluate_targets
from ceres.candidates import stage_impact
from ceres.config import CeresConfig

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
DETAILS = {40001: {"id": 40001, "name": "Ordinary", "color": "Red"}, 505: {"id": 505, "name": "Runi", "color": "Red"}}
PLOT_A = "plot-a"
PLOT_B = "plot-b"


def _plot(deed_uid: str, plot: str) -> dict:
    return {
        "deed_uid": deed_uid,
        "facts": {"plot": plot, "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T", "worksite_type": "Grain Farm"},
        "worksite_facts": {"project_type": "worksite_grain"},
        "staking_facts": {
            "max_workers_allowed": 5, "worker_count": 1, "total_base_pp_cap": 100000, "is_power_core_staked": True,
            "is_energized": False, "is_runi_staked": False, "deed_rarity_boost": 0, "totem_boost": 0, "title_boost": 0,
            "runi_boost": 0, "deed_status_token_boost": 0, "red_biome_modifier": 0, "blue_biome_modifier": 0,
            "white_biome_modifier": 0, "black_biome_modifier": 0, "green_biome_modifier": 0, "gold_biome_modifier": 0,
            "total_harvest_pp": 1000,
        },
    }


def _worker(ref: str, deed: str, slot: int, base: float = 1000) -> dict:
    return {"name": ref, "card_detail_id": 40001, "land_base_pp": base, "total_harvest_pp": base, "asset_ref": ref, "uid": ref,
            "stake_ref_uid": deed, "stake_type_uid": "STK-LND-WKR", "asset_kind": "worker_card", "slot": slot}


def _card(ref: str, *, base: float = 1500, end: str | None = "2026-09-01T00:00:00Z", survival: str | None = None) -> dict:
    return {"asset_ref": ref, "uid": ref, "classification": {"bucket": "stake_expired_or_historical" if end else "unstaked_apparent"},
            "facts": {"uid": ref, "card_detail_id": 40001, "land_base_pp": base, "level": 1, "stake_end_date": end,
                      "survival_date": survival, "name": ref}}


def _report() -> dict:
    return {
        "generated_at": "2026-09-12T11:00:00Z",
        "plots": [_plot(PLOT_A, "1-1-1"), _plot(PLOT_B, "1-1-2")],
        "workers": [_worker("resident", PLOT_A, 1), _worker("neighbour", PLOT_B, 1)],
        "bonus_assets": [],
        "cards": [_card("idle"), _card("cooling", end="2026-09-14T00:00:00Z"), _card("dead", survival="2026-09-20T00:00:00Z")],
        "unassigned_items": {},
        "query": {"player": "player0005"},
    }


def _placement(ref: str, slot: int = 0, group: str = "worker") -> dict:
    return {"group_id": group, "slot_index": slot, "asset_ref": ref}


def _evaluate(report: dict, rows: list[dict]) -> dict:
    return evaluate_targets(report, rows, now=NOW, details_by_id=DETAILS)


def test_empty_target_is_a_clean_sheet_not_the_live_roster():
    row = _evaluate(_report(), [{"deed_uid": PLOT_A, "placements": []}])["rows"][0]
    assert row["coverage"] == "complete"
    assert row["harvest_pp"] == 0
    assert row["base_pp"] == 0
    assert row["capacity"]["display"] == "0/5"


def test_planned_roster_matches_the_shared_evaluator_on_the_same_final_roster():
    report = _report()
    row = _evaluate(report, [{"deed_uid": PLOT_A, "placements": [_placement("idle", 0)]}])["rows"][0]
    expected = stage_impact(report, PLOT_A, [
        {"deed_uid": PLOT_A, "group_id": "worker", "action": "unstake", "asset_ref": "resident", "slot_index": 0},
        {"deed_uid": PLOT_A, "group_id": "worker", "action": "stake", "asset_ref": "idle", "slot_index": 0},
    ], details_by_id=DETAILS)
    assert row["coverage"] == "complete"
    assert row["harvest_pp"] == pytest.approx(expected["resulting_harvest"])
    assert row["harvest_pp"] > 0
    assert row["base_pp"] == pytest.approx(1500)
    assert row["base_pp_after_cap"] == pytest.approx(1500)
    assert row["capacity"]["display"] == "1/5"
    assert row["work_per_hour"] == pytest.approx(expected["resulting_work_per_hour"])


def test_incomplete_plot_detail_is_unknown_and_other_rows_stay_priced():
    report = _report()
    report["plots"][0]["asset_fetch"] = {"status": "not_requested"}
    result = _evaluate(report, [{"deed_uid": PLOT_A, "placements": [_placement("idle")]},
                                {"deed_uid": PLOT_B, "placements": [_placement("cooling")]}])
    by_deed = {row["deed_uid"]: row for row in result["rows"]}
    assert by_deed[PLOT_A] == {"deed_uid": PLOT_A, "coverage": "unknown", "reason": "plot_detail_missing"}
    assert by_deed[PLOT_B]["coverage"] == "complete"


def test_worker_count_larger_than_known_roster_is_unknown_never_zero():
    report = _report()
    report["plots"][0]["staking_facts"]["worker_count"] = 3
    row = _evaluate(report, [{"deed_uid": PLOT_A, "placements": []}])["rows"][0]
    assert row["coverage"] == "unknown" and "harvest_pp" not in row


def test_unknown_asset_and_plot_are_unknown():
    result = _evaluate(_report(), [{"deed_uid": PLOT_A, "placements": [_placement("ghost")]},
                                   {"deed_uid": "plot-gone", "placements": []}])
    assert [row["reason"] for row in result["rows"]] == ["asset_unknown", "plot_gone"]
    assert result["assets"]["ghost"] == {"asset_ref": "ghost", "known": False}


def test_asset_states_come_from_the_capture_and_survival_is_never_ready():
    result = _evaluate(_report(), [{"deed_uid": PLOT_A, "placements": [_placement("idle", 0), _placement("cooling", 1),
                                                                        _placement("dead", 2), _placement("neighbour", 3)]}])
    assets = result["assets"]
    assert assets["idle"]["cooldown"]["state"] == "ready" and assets["idle"]["staked"] is False
    assert assets["cooling"]["cooldown"]["state"] == "cooling" and assets["cooling"]["cooldown"]["ready"] is False
    assert assets["dead"]["cooldown"]["state"] == "survival_locked" and assets["dead"]["cooldown"]["ready"] is False
    assert assets["neighbour"]["staked"] is True and assets["neighbour"]["location_deed_uid"] == PLOT_B
    assert assets["neighbour"]["cooldown"]["state"] == "staked"


class _NetworkCalled(AssertionError):
    pass


@pytest.fixture
def no_network(monkeypatch):
    """Every outbound client the codebase could use raises; the list records any attempt."""
    calls: list[str] = []

    def refuse(name):
        def _refuse(*args, **kwargs):
            calls.append(name)
            raise _NetworkCalled(name)
        return _refuse

    monkeypatch.setattr(socket.socket, "connect", refuse("socket.connect"))
    monkeypatch.setattr(socket, "create_connection", refuse("socket.create_connection"))
    import httpx
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse("httpx.HTTPTransport"))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse("httpx.AsyncHTTPTransport"))
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", refuse("urllib.urlopen"))
    try:
        import requests
        monkeypatch.setattr(requests.Session, "request", refuse("requests.Session.request"))
    except ImportError:
        pass
    return calls


def _client(tmp_path, monkeypatch, report):
    app = create_app(CeresConfig(root=tmp_path / ".ceres"))
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, type("Ref", (), {"generated_at": report["generated_at"]})()))
    monkeypatch.setattr(capture_mod, "load_card_pool", lambda value: value)
    monkeypatch.setattr(board_plan_eval, "load_details_by_id", lambda: DETAILS)
    return TestClient(app)


HEADERS = {"Origin": "http://testserver"}
URL = "/api/board-plans/evaluate?player=player0005"


def test_route_is_capture_bound_and_makes_no_network_call(tmp_path, monkeypatch, no_network):
    report = _report()
    client = _client(tmp_path, monkeypatch, report)
    body = {"rows": [{"deed_uid": PLOT_A, "placements": [_placement("idle")]}]}
    missing = client.post(URL, json=body, headers=HEADERS)
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "capture_unknown"
    older = client.post(URL, json={**body, "capture_generated_at": "2026-09-01T00:00:00Z"}, headers=HEADERS)
    assert older.status_code == 409 and older.json()["detail"]["code"] == "capture_superseded"
    ok = client.post(URL, json={**body, "capture_generated_at": report["generated_at"]}, headers=HEADERS)
    assert ok.status_code == 200, ok.text
    payload = ok.json()
    assert payload["generated_at"] == report["generated_at"]
    assert payload["rows"][0]["coverage"] == "complete" and payload["rows"][0]["harvest_pp"] > 0
    assert payload["assets"]["idle"]["cooldown"]["state"] == "ready"
    assert no_network == []


def test_route_refuses_duplicates_and_oversized_batches(tmp_path, monkeypatch, no_network):
    report = _report()
    client = _client(tmp_path, monkeypatch, report)
    stamp = report["generated_at"]
    twice = {"capture_generated_at": stamp, "rows": [{"deed_uid": PLOT_A, "placements": [_placement("idle", 0)]},
                                                     {"deed_uid": PLOT_B, "placements": [_placement("idle", 0)]}]}
    assert client.post(URL, json=twice, headers=HEADERS).status_code == 422
    same_slot = {"capture_generated_at": stamp, "rows": [{"deed_uid": PLOT_A, "placements": [_placement("idle", 0), _placement("cooling", 0)]}]}
    assert client.post(URL, json=same_slot, headers=HEADERS).status_code == 422
    too_many = {"capture_generated_at": stamp, "rows": [{"deed_uid": f"plot-{i}", "placements": []} for i in range(51)]}
    assert client.post(URL, json=too_many, headers=HEADERS).status_code == 422
    assert no_network == []


def test_route_reports_incomplete_collection_without_pricing(tmp_path, monkeypatch, no_network):
    report = _report()
    client = _client(tmp_path, monkeypatch, report)

    def incomplete(value):
        raise LookupError("collection read incomplete")

    monkeypatch.setattr(capture_mod, "load_card_pool", incomplete)
    response = client.post(URL, json={"capture_generated_at": report["generated_at"], "rows": [{"deed_uid": PLOT_A, "placements": []}]}, headers=HEADERS)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "collection_incomplete"
    assert no_network == []
