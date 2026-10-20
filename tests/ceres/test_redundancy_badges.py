from __future__ import annotations

from ceres.board import BADGE_META, project_board
from ceres.notices import BADGE_ORDER, badge_rank
from tests.ceres.conftest import build_details_by_id, build_report


def _row(report: dict, deed_uid: str):
    return next(row for row in project_board(report, build_details_by_id())["plots"] if row["deed_uid"] == deed_uid)


def test_filled_unpowered_worker_emits_explicit_slot_warning_and_live_pp():
    report = build_report()
    deed_uid = "I-295-eb4b408d045087"
    target_workers = [worker for worker in report["workers"] if worker.get("stake_ref_uid") == deed_uid]
    removed = next(worker for worker in target_workers if worker.get("slot") == 4)
    report["workers"].remove(removed)
    worker = next(worker for worker in report["workers"] if worker.get("stake_ref_uid") == deed_uid and worker.get("slot") == 5)
    worker["is_powered"] = False
    worker["total_harvest_pp"] = "11880"
    plot = next(plot for plot in report["plots"] if plot["deed_uid"] == deed_uid)
    plot["staking_facts"].update({
        "is_powered": True,
        "is_power_core_staked": False,
        "is_energized": False,
        "worker_count": 5,
        "max_workers_allowed": 4,
        "powered_worker_count": 5,
    })

    row = _row(report, deed_uid)
    assert "unpowered_worker" in row["badges"]
    assert "unpowered" not in row["badges"]
    warning = next(notice for notice in row["notices"] if notice["code"] == "unpowered_worker")
    assert "Worker 5" in warning["message"]
    assert "11,880 PP" in warning["message"]
    assert "Power Core" in warning["message"]
    assert "Energized" in warning["message"]
    assert warning["blocking"] == "none"


def test_unpowered_worker_badge_is_union_of_worker_fact_and_bulk_deficit():
    """Injection: make either evidence source overrule the other.

 The first case injects an unpowered worker while the bulk pair is healthy;
 the second injects a bulk deficit while no per-worker rows are available.
 Both independent facts must emit the same badge.
    """
    worker_fact_report = build_report()
    deed_uid = "I-295-eb4b408d045087"
    worker_fact_plot = next(plot for plot in worker_fact_report["plots"] if plot["deed_uid"] == deed_uid)
    worker_fact = next(worker for worker in worker_fact_report["workers"] if worker.get("stake_ref_uid") == deed_uid and worker.get("slot") == 5)
    worker_fact["is_powered"] = False
    worker_fact_plot["staking_facts"].update({"worker_count": 5, "powered_worker_count": 5})
    worker_fact_row = _row(worker_fact_report, deed_uid)
    assert worker_fact_row["capacity"]["worker_count"] == worker_fact_row["capacity"]["powered_worker_count"]
    assert "unpowered_worker" in worker_fact_row["badges"]

    bulk_report = build_report()
    bulk_plot = next(plot for plot in bulk_report["plots"] if plot["deed_uid"] == deed_uid)
    bulk_report["workers"] = [worker for worker in bulk_report["workers"] if worker.get("stake_ref_uid") != deed_uid]
    bulk_plot["staking_facts"].update({"worker_count": 5, "powered_worker_count": 4})
    bulk_row = _row(bulk_report, deed_uid)
    assert bulk_row["workers"] == []
    assert "unpowered_worker" in bulk_row["badges"]


def test_empty_unpowered_plot_shows_no_power_badge():
    # (maintainer, 2026-09-14): "Remove the PWR badge on plots that have no workers. These plots are just
    # inactive and have no problem." An empty, unpowered plot is idle only.
    row = _row(build_report(), "I-294-38216c06ea35c7")
    assert "idle" in row["badges"]
    assert "unpowered" not in row["badges"]
    assert "unpowered_worker" not in row["badges"]


def test_redundant_core_requires_energized_and_excludes_runi():
    report = build_report()
    energized_without_runi = next(plot for plot in report["plots"] if plot["deed_uid"] == "I-294-8bb3ce6436e586")
    energized_without_runi["staking_facts"]["is_energized"] = True
    row = _row(report, "I-294-8bb3ce6436e586")
    assert "redundant_power_core" in row["badges"]

    core_and_runi = _row(build_report(), "I-295-eb4b408d045087")
    assert "redundant_power_core" not in core_and_runi["badges"]


def test_dark_discount_warning_requires_runi_and_loaded_worker_ability():
    report = build_report()
    kinbound = next(worker for worker in report["workers"] if worker.get("name") == "Kinbound Druid")
    kinbound["level"] = 8
    kinbound["observed_at"] = next(plot for plot in report["plots"] if plot["deed_uid"] == "I-294-62529e285ddcf0")["observed_at"]
    runi_plot = _row(report, "I-294-62529e285ddcf0")
    assert "dark_discount_wasted" in runi_plot["badges"]
    warning = next(notice for notice in runi_plot["notices"] if notice["code"] == "dark_discount_wasted")
    assert warning["message"] == "Runi already removes this plot's DEC staking requirement, so Dark Discount does nothing here. It would reduce DEC elsewhere."
    assert warning["blocking"] == "none"

    dark_discount_without_runi = _row(build_report(), "I-294-8bb3ce6436e586")
    assert "dark_discount_wasted" not in dark_discount_without_runi["badges"]


def test_worker_badges_fail_closed_for_retrieved_rows_from_an_older_capture():
    report = build_report()
    deed_uid = "I-295-eb4b408d045087"
    plot = next(plot for plot in report["plots"] if plot["deed_uid"] == deed_uid)
    worker = next(worker for worker in report["workers"] if worker.get("stake_ref_uid") == deed_uid and worker.get("slot") == 5)
    worker["is_powered"] = False
    worker["observed_at"] = "2026-08-26T20:00:16Z"
    plot["staking_facts"].update({"is_powered": True, "is_power_core_staked": False, "is_energized": False})
    assert "unpowered_worker" not in _row(report, deed_uid)["badges"]


def test_new_warning_codes_use_the_grouped_order_and_are_board_emitted():
    report = build_report()
    energized = next(plot for plot in report["plots"] if plot["deed_uid"] == "I-294-8bb3ce6436e586")
    energized["staking_facts"]["is_energized"] = True
    kinbound = next(worker for worker in report["workers"] if worker.get("name") == "Kinbound Druid")
    kinbound["level"] = 8
    unpowered = next(worker for worker in report["workers"] if worker.get("stake_ref_uid") == "I-295-eb4b408d045087" and worker.get("slot") == 5)
    unpowered["is_powered"] = False
    board = project_board(report, build_details_by_id())
    emitted = {code for row in board["plots"] for code in row["badges"]}
    assert {"unpowered_worker", "redundant_power_core", "dark_discount_wasted"} <= emitted
    assert badge_rank("unpowered_worker") < badge_rank("over_cap")
    assert all(
        badge_rank(code) < badge_rank(property_code)
        for code in ("redundant_power_core", "dark_discount_wasted")
        for property_code in ("sole_ability_holder", "runi", "building_in_box", "assets_not_loaded")
    )
    assert BADGE_ORDER[:2] == ["unpowered", "unpowered_worker"]


def test_dark_discount_badge_does_not_require_plot_item_expansion():
    import copy
    report = build_report()
    deed_uid = "I-294-62529e285ddcf0"
    plot = next(plot for plot in report["plots"] if plot["deed_uid"] == deed_uid)
    report["workers"].append({"asset_ref": "dd-test-worker", "stake_ref_uid": deed_uid,
                              "card_detail_id": 867, "name": "Kinbound Druid", "slot": 1,
                              "observed_at": plot["observed_at"]})
    report["card_index"] = [{"asset_ref": "dd-test-worker", "facts": {"level": 8}}]
    for status in ("not_requested", "retrieved"):
        current = copy.deepcopy(report)
        next(p for p in current["plots"] if p["deed_uid"] == deed_uid)["asset_fetch"] = {"status": status}
        for lean in (True, False):
            row = next(p for p in project_board(current, build_details_by_id(), lean=lean)["plots"] if p["deed_uid"] == deed_uid)
            assert row["capacity"]["runi_staked"]
            assert "dark_discount_wasted" in row["badges"]
            assert any(n["code"] == "dark_discount_wasted" for n in row["notices"])
    no_runi = copy.deepcopy(report)
    next(p for p in no_runi["plots"] if p["deed_uid"] == deed_uid)["staking_facts"]["is_runi_staked"] = False
    assert "dark_discount_wasted" not in _row(no_runi, deed_uid)["badges"]


def test_dark_discount_badge_does_not_infer_an_unavailable_ability():
    report = build_report()
    deed_uid = "I-294-62529e285ddcf0"
    report["workers"] = [w for w in report["workers"] if w.get("stake_ref_uid") != deed_uid]
    plot = next(p for p in report["plots"] if p["deed_uid"] == deed_uid)
    plot["asset_fetch"] = {"status": "not_requested"}
    assert "dark_discount_wasted" not in _row(report, deed_uid)["badges"]
