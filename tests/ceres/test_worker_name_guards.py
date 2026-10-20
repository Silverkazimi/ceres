from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ceres import capture as capture_mod
from ceres import routes_candidates
from praetoria.land_analysis import normalize_collection_worker, normalize_worker
from praetoria.land_reallocate import build_plots


DEED_UID = "deed-name-guard"
WORKER_UID = "worker-name-guard"
DETAIL_ID = 40001
DETAILS = {DETAIL_ID: {"id": DETAIL_ID, "color": "Red"}}


def _request() -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(player="player0005"))))


def _report_with_name_less_worker() -> dict:
    collection_worker = normalize_collection_worker(
        {
            "uid": WORKER_UID,
            "name": "Collection label that the producer omitted",
            "card_detail_id": DETAIL_ID,
            "player": "player0005",
            "stake_ref_uid": DEED_UID,
            "stake_type_uid": "STK-LND-WKR",
            "delegated_to": None,
            "rental_type": None,
        },
        observed_at="2026-09-04T00:00:00Z",
        card_names={},
    ).facts
    assert "name" not in collection_worker

    asset_worker = normalize_worker(
        {
            "uid": WORKER_UID,
            "card_detail_id": DETAIL_ID,
            "stake_ref_uid": DEED_UID,
            "stake_type_uid": "STK-LND-WKR",
            "slot": 1,
            "land_base_pp": 1000,
            "total_construction_pp": 1000,
            "total_harvest_pp": 1000,
        },
        observed_at="2026-09-04T00:00:00Z",
    ).facts
    collection_worker.update({
        key: value
        for key, value in asset_worker.items()
        if key not in {"stake_ref_uid", "uid", "name"}
    })
    assert "name" not in collection_worker

    return {
        "plots": [{
            "deed_uid": DEED_UID,
            "facts": {
                "plot": "1-1-1",
                "rarity": "common",
                "status": "occupied",
                "resource": "GRAIN",
                "territory": "T",
            },
            "staking_facts": {
                "max_workers_allowed": 1,
                "total_base_pp_cap": 100000,
                "total_harvest_pp": 1000,
                "is_power_core_staked": True,
                "is_energized": False,
                "is_runi_staked": False,
                "deed_rarity_boost": 0,
                "totem_boost": 0,
                "title_boost": 0,
                "runi_boost": 0,
                "deed_status_token_boost": 0,
                "red_biome_modifier": 0,
                "blue_biome_modifier": 0,
                "white_biome_modifier": 0,
                "black_biome_modifier": 0,
                "green_biome_modifier": 0,
                "gold_biome_modifier": 0,
            },
        }],
        "workers": [collection_worker],
        "cards": [{
            "asset_ref": collection_worker["asset_ref"],
            "classification": {"bucket": "assigned_active"},
            "facts": {"card_detail_id": DETAIL_ID, "level": 1},
        }],
        "bonus_assets": [],
    }


def _install_snapshot(monkeypatch, report: dict) -> None:
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, "fixture"))
    monkeypatch.setattr(routes_candidates, "load_details_by_id", lambda: DETAILS)


def _raise_internal_keyerror(*args, **kwargs):
    raise KeyError("internal_field")


@pytest.mark.parametrize("endpoint", ["candidates", "removal", "stage"])
def test_name_less_producer_worker_keeps_all_three_endpoints_alive(monkeypatch, endpoint):
    """Injection: restore ``worker["name"]``; the silent failure is an unrelated server error from each endpoint."""
    report = _report_with_name_less_worker()
    _install_snapshot(monkeypatch, report)
    request = _request()
    asset_ref = report["workers"][0]["asset_ref"]

    if endpoint == "candidates":
        # Red mutation: call get_candidates directly; unresolved Query defaults fail before ranking
        # or explicit pagination alone filters every row through an unresolved edition default.
        app = FastAPI()
        app.state.ceres_config = request.app.state.ceres_config
        app.include_router(routes_candidates.router)
        with TestClient(app) as client:
            response = client.get(f"/api/plots/{DEED_UID}/candidates", params={"group_id": "worker"})
        assert response.json()["pool_size"] > 0
    elif endpoint == "removal":
        response = routes_candidates.post_removal_impact(DEED_UID, {"asset_refs": [asset_ref]}, request)
    else:
        response = routes_candidates.post_stage_impact(
            DEED_UID,
            {"moves": [{"action": "unstake", "asset_ref": asset_ref, "slot_index": 0}]},
            request,
        )

    assert response.status_code == 200


@pytest.mark.parametrize("endpoint", ["candidates", "removal", "stage"])
def test_missing_deed_keyerror_remains_an_honest_404(monkeypatch, endpoint):
    """Injection: swallow the ``KeyError(deed_uid)`` case; the silent failure is an unknown deed losing its honest 404 contract."""
    report = _report_with_name_less_worker()
    _install_snapshot(monkeypatch, report)
    request = _request()

    with pytest.raises(HTTPException) as caught:
        if endpoint == "candidates":
            routes_candidates.get_candidates("missing-deed", request, group_id="worker")
        elif endpoint == "removal":
            routes_candidates.post_removal_impact("missing-deed", {"asset_refs": ["any-ref"]}, request)
        else:
            routes_candidates.post_stage_impact(
                "missing-deed",
                {"moves": [{"action": "unstake", "asset_ref": "any-ref", "slot_index": 0}]},
                request,
            )

    assert caught.value.status_code == 404
    assert caught.value.detail == "unknown deed_uid: missing-deed"


@pytest.mark.parametrize(
    ("endpoint", "function_name"),
    [("candidates", "rank_candidates"), ("removal", "plot_removal_delta"), ("stage", "stage_impact")],
)
def test_unrelated_keyerror_is_not_misreported_as_unknown_deed(monkeypatch, endpoint, function_name):
    """Injection: restore a bare ``except KeyError``; the silent failure is any internal KeyError becoming a misleading unknown-deed 404."""
    report = _report_with_name_less_worker()
    _install_snapshot(monkeypatch, report)
    monkeypatch.setattr(routes_candidates, function_name, _raise_internal_keyerror)
    request = _request()

    with pytest.raises(KeyError) as caught:
        if endpoint == "candidates":
            routes_candidates.get_candidates(DEED_UID, request, group_id="worker")
        elif endpoint == "removal":
            routes_candidates.post_removal_impact(DEED_UID, {"asset_refs": ["any-ref"]}, request)
        else:
            routes_candidates.post_stage_impact(
                DEED_UID,
                {"moves": [{"action": "unstake", "asset_ref": "any-ref", "slot_index": 0}]},
                request,
            )

    assert "unknown deed_uid" not in str(caught.value)
    assert caught.value.args == ("internal_field",)


def test_name_less_worker_degrades_to_an_absent_label_without_fabrication(monkeypatch):
    """Injection: substitute a placeholder for the missing name; the silent failure is a placeholder, empty label, text None, or wrong card name."""
    report = _report_with_name_less_worker()
    _install_snapshot(monkeypatch, report)

    plots = build_plots(report)
    assert plots[DEED_UID].cards[0]["name"] is None

    response = routes_candidates.post_removal_impact(
        DEED_UID,
        {"asset_refs": [report["workers"][0]["asset_ref"]]},
        _request(),
    )
    body = json.loads(response.body)
    removed = body["removed"][0]
    assert removed["name"] is None
    assert "Unknown card" not in json.dumps(body)
    assert '"None"' not in json.dumps(body)
