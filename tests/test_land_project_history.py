from __future__ import annotations

from pathlib import Path

from ceres.worksite_swap import build_pending_change
from praetoria.cache import JsonCache
from praetoria.land_analysis import LandCapture, _select_prior_project
from praetoria.transport import ResponseData


DEED_UID = "I-298-history"
CONSTRUCTION_START = "2026-08-26T12:00:00.000Z"


def _response(data):
    return ResponseData(data=data, retrieved_at="2026-08-26T12:00:00Z", attempts=1)


def _bundle():
    return _response({
        "status": "success",
        "data": {
            "deeds": [{
                "deed_uid": DEED_UID,
                "region_number": 298,
                "tract_number": 1,
                "plot_number": 1,
                "resource_symbol": None,
                "worksite_type": "",
                "is_construction": True,
                "plot_status": "natural",
                "deed_type": "Canyon",
            }],
            "worksite_details": [{
                "deed_uid": DEED_UID,
                "project_number": 3,
                "project_type": "worksite_iron_construction",
                "start_date": CONSTRUCTION_START,
                "is_construction": True,
            }],
            "staking_details": [{
                "deed_uid": DEED_UID,
                "total_work_per_hour": 10.0,
                "worker_count": 1,
                "max_workers_allowed": 1,
            }],
        },
    })


class FakeLandClient:
    def __init__(self, history):
        self.history = history

    def bundle(self, player):
        return _bundle()

    def project_history(self, deed_uid):
        raise AssertionError("project history must not be fetched ()")

    def assets(self, deed_uid):
        return _response({"status": "success", "data": {"cards": [], "items": []}})

    def available_raw(self, stake_type_uid, deed_uid, player, offset=0, limit=5):
        return _response({"status": "success", "data": {"ids": []}})


class FakeCollectionClient:
    def collection(self, player):
        return _response({"player": player, "cards": []})


def _capture(tmp_path: Path, history):
    return LandCapture(
        land_client=FakeLandClient(history),
        collection_client=FakeCollectionClient(),
        cache=JsonCache(tmp_path),
        min_delay=0.0,
        concurrency=1,
    ).capture("player0005")


def _history():
    return {
        "status": "success",
        "data": [
            {
                "project_number": 1,
                "project_type": "worksite_grain",
                "start_date": "2026-01-01T00:00:00Z",
                "completed_date": "2026-02-01T00:00:00Z",
            },
            {
                "project_number": 2,
                "project_type": "worksite_research",
                "start_date": "2026-02-01T00:00:00Z",
                "completed_date": CONSTRUCTION_START,
            },
            {
                "project_number": 3,
                "project_type": "worksite_iron_construction",
                "start_date": CONSTRUCTION_START,
                "completed_date": None,
            },
        ],
    }


def test_capture_never_fetches_project_history_and_leaves_prior_resource_null(tmp_path):
    report = _capture(tmp_path, _history())
    plot = report["plots"][0]
    change = build_pending_change(
        DEED_UID,
        plot["worksite_facts"],
        plot["staking_facts"]["total_work_per_hour"],
        plot["facts"]["prior_resource"],
    )

    assert plot["facts"]["prior_resource"] is None
    assert change["from"] is None
    assert change["from_label"] is None


def test_prior_project_prefers_completed_date_match_then_highest_lower_number():
    projects = [
        {"project_number": 1, "project_type": "worksite_grain", "start_date": "a", "completed_date": "b"},
        {"project_number": 2, "project_type": "worksite_research", "start_date": "b", "completed_date": "c"},
    ]
    assert _select_prior_project(projects, {"project_number": 3, "start_date": "c"}) == projects[1]
    assert _select_prior_project(projects, {"project_number": 4, "start_date": "missing"}) == projects[1]
