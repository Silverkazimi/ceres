import pytest

from ceres.board import project_board
from tests.ceres.conftest import build_report, build_details_by_id

UID = "I-295-eb4b408d045087"


def case():
    report = build_report()
    plot = next(p for p in report["plots"] if p["deed_uid"] == UID)
    plot["staking_facts"].update(is_powered=True, is_power_core_staked=True,
        is_energized=False, is_runi_staked=False, worker_count=1, powered_worker_count=1)
    plot["asset_fetch"] = {"status": "not_requested"}
    worker = {"asset_ref": "wkr-test", "stake_ref_uid": UID, "slot": 1,
              "is_powered": False, "card_detail_id": 1, "name": "Test worker",
              "observed_at": plot["observed_at"]}
    report["workers"] = [worker]
    return report, plot, worker


def row(report, lean=True):
    return next(p for p in project_board(report, build_details_by_id(), lean=lean)["plots"] if p["deed_uid"] == UID)


@pytest.mark.parametrize("status", ["not_requested", "retrieved"])
@pytest.mark.parametrize("lean", [True, False])
def test_known_unpowered_worker_independent_of_item_details(status, lean):
    report, plot, worker = case()
    plot["asset_fetch"]["status"] = status
    result = row(report, lean)
    assert "unpowered_worker" in result["badges"]
    assert any(n["code"] == "unpowered_worker" for n in result["notices"])


@pytest.mark.parametrize("patch", [
    {"observed_at": "2000-01-01T00:00:00Z"}, {"observed_at": None},
    {"observed_at": "invalid"}, {"detail_state": "pending"},
    {"detail_pending_reason": "roster_predates_stake"},
    {"is_powered": True}, {"is_powered": None}, {"stake_type_uid": "STK-LND-RUNI"},
])
def test_unknown_stale_powered_and_runi_workers_do_not_emit_wkr(patch):
    report, plot, worker = case()
    worker.update(patch)
    assert "unpowered_worker" not in row(report)["badges"]


def test_known_slot_beyond_powered_capacity_needs_no_item_details():
    report, plot, worker = case()
    worker.update(slot=6, is_powered=None)
    assert "unpowered_worker" in row(report)["badges"]


def test_pending_roster_does_not_infer_power_count_deficit():
    report, plot, worker = case()
    plot["asset_fetch"]["worker_detail_pending"] = [worker["asset_ref"]]
    plot["staking_facts"].update(worker_count=6, powered_worker_count=5)
    worker.update(detail_state="pending")
    assert "unpowered_worker" not in row(report)["badges"]


def test_bulk_deficit_still_works_without_worker_detail():
    report, plot, worker = case()
    report["workers"] = []
    plot["staking_facts"].update(worker_count=6, powered_worker_count=5)
    assert "unpowered_worker" in row(report)["badges"]
