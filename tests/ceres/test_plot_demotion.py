""" coverage and per-worker detail contract tests.

These fixtures model a quick collection join against a retrieved base snapshot;
they deliberately retain the worker-side carry-forward suppression from .
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from ceres.board import project_board
from ceres import capture as capture_mod
from ceres.capture import merge_reports
from ceres.config import CeresConfig
from ceres import routes_board


def _plot(uid: str, status: str, worker_count: int = 1, observed_at: str = "2099-01-01T00:00:00Z") -> dict:
    return {
        "deed_uid": uid,
        "staking_facts": {"worker_count": worker_count},
        "asset_fetch": {"status": status},
        "observed_at": observed_at,
    }


def _report(plots: list[dict], workers: list[dict], assignments: list[dict]) -> dict:
    return {
        "plots": plots,
        "workers": workers,
        "assignments": assignments,
        "bonus_assets": [],
        "cards": [],
        "coverage": {"assets": {"retrieved": 1, "expected": 1, "complete": True}},
    }


def _worker(deed_uid: str, asset_ref: str, slot: int | None = None, name: str | None = None) -> dict:
    worker = {"asset_ref": asset_ref, "stake_ref_uid": deed_uid}
    if slot is not None:
        worker["slot"] = slot
    if name is not None:
        worker["name"] = name
    return worker


def _perturbation(changed: int = 24) -> tuple[dict, dict, list[str], list[str]]:
    deed_uids = [f"deed-{index:03d}" for index in range(129)]
    worker_deeds = deed_uids[:58]
    changed_deeds = worker_deeds[:changed]
    base_plots = [_plot(uid, "retrieved", 1 if uid in worker_deeds else 0) for uid in deed_uids]
    incoming_plots = [_plot(uid, "not_requested", 1 if uid in worker_deeds else 0) for uid in deed_uids]
    base_workers = [_worker(uid, f"old-{uid}", index + 1, f"Worker {index + 1}") for index, uid in enumerate(worker_deeds)]
    base_assignments = [
        {"asset_ref": f"old-{uid}", "deed_uid": uid, "slot": index + 1, "confidence": "confident"}
        for index, uid in enumerate(worker_deeds)
    ]
    incoming_workers = [
        _worker(uid, f"new-{uid}" if uid in changed_deeds else f"old-{uid}")
        for uid in worker_deeds
    ]
    incoming_assignments = [
        {
            "asset_ref": f"new-{uid}" if uid in changed_deeds else f"old-{uid}",
            "deed_uid": uid,
            "slot": None,
            "confidence": "collection_join",
        }
        for uid in worker_deeds
    ]
    return (
        _report(base_plots, base_workers, base_assignments),
        _report(incoming_plots, incoming_workers, incoming_assignments),
        changed_deeds,
        worker_deeds,
    )


def test_24_deed_collection_join_keeps_retrieved_coverage_and_counts_pending_detail() -> None:
    """ fixes the pre-fix coverage output {'retrieved': 105, 'expected': 129, 'dropped': 0, 'failed': 0, 'not_requested': 24}; pending worker detail must not demote the plots."""
    base, incoming, changed_deeds, _ = _perturbation()

    merged = merge_reports(base, incoming)
    assets = merged["coverage"]["assets"]

    assert assets["retrieved"] == 129
    assert assets["expected"] == 129
    assert assets["dropped"] == 0
    assert assets["failed"] == 0
    assert assets["not_requested"] == 0
    assert assets["complete"] is True
    assert assets["worker_detail_pending"] == 24
    assert assets["worker_detail_pending_deeds"] == changed_deeds


def test_one_deed_collection_join_keeps_retrieved_coverage_and_marks_pending_detail() -> None:
    base, incoming, changed_deeds, _ = _perturbation(changed=1)

    merged = merge_reports(base, incoming)

    assert merged["coverage"]["assets"]["retrieved"] == 129
    assert merged["coverage"]["assets"]["worker_detail_pending"] == 1
    assert merged["coverage"]["assets"]["worker_detail_pending_deeds"] == changed_deeds


def test_matching_collection_ref_preserves_base_slot_identity_and_marks_complete() -> None:
    base = _report(
        [_plot("a", "retrieved")],
        [_worker("a", "card-a", 3, "Worker")],
        [{"asset_ref": "card-a", "deed_uid": "a", "slot": 3, "confidence": "confident"}],
    )
    incoming = _report(
        [_plot("a", "not_requested")],
        [_worker("a", "card-a")],
        [{"asset_ref": "card-a", "deed_uid": "a", "slot": None, "confidence": "collection_join"}],
    )

    merged = merge_reports(base, incoming)
    worker = merged["workers"][0]

    assert worker["slot"] == 3
    assert worker["name"] == "Worker"
    assert worker["detail_state"] == "complete"


def test_bulk_count_discriminator_still_drops_stale_detail() -> None:
    base, incoming, changed_deeds, worker_deeds = _perturbation()
    base["workers"] = [worker for worker in base["workers"] if worker["stake_ref_uid"] not in changed_deeds]
    incoming["workers"] = [_worker(uid, f"incoming-{uid}", 1) for uid in worker_deeds]
    incoming["assignments"] = [
        {"asset_ref": f"incoming-{uid}", "deed_uid": uid, "slot": 1, "confidence": "confident"}
        for uid in worker_deeds
    ]

    merged = merge_reports(base, incoming)
    assets = merged["coverage"]["assets"]
    diagnostics = merged["diagnostics"]

    assert assets["dropped"] == 24
    assert all(
        next(plot for plot in merged["plots"] if plot["deed_uid"] == uid)["asset_fetch"]["status"] == "dropped_stale"
        for uid in changed_deeds
    )
    assert sum(item.get("code") == "worker_detail_dropped_stale" for item in diagnostics) == 24
    assert set(changed_deeds) <= set(assets["needs_refetch"])


def test_pending_refetch_queue_prioritizes_failures_and_caps_oldest_pending_deeds() -> None:
    base, incoming, pending_deeds, _ = _perturbation(changed=10)
    failure_deeds = [f"failed-{index}" for index in range(3)]
    base["plots"] = [_plot(uid, "not_requested", 0) for uid in failure_deeds] + base["plots"]
    incoming["plots"] = [_plot(uid, "failed", 0) for uid in failure_deeds] + incoming["plots"]
    for index, uid in enumerate(pending_deeds):
        base_plot = next(plot for plot in base["plots"] if plot["deed_uid"] == uid)
        base_plot["observed_at"] = f"2026-01-{index + 1:02d}T00:00:00Z"

    merged = merge_reports(base, incoming)
    queue = merged["coverage"]["assets"]["needs_refetch"]

    assert queue[:3] == failure_deeds
    assert queue[3:] == pending_deeds[:4]
    assert len(queue) == 7
    assert len(queue) == len(set(queue))

    clean = merge_reports(None, _report([_plot("clean", "retrieved", 0)], [], []))
    assert clean["coverage"]["assets"]["needs_refetch"] == []


def test_pending_detail_preserves_worker_carry_forward_suppression() -> None:
    base, incoming, changed_deeds, _ = _perturbation(changed=1)

    merged = merge_reports(base, incoming)
    affected = changed_deeds[0]

    assert [worker["asset_ref"] for worker in merged["workers"] if worker["stake_ref_uid"] == affected] == [f"new-{affected}"]
    assert f"old-{affected}" not in {worker["asset_ref"] for worker in merged["workers"]}


def test_pending_diagnostic_is_per_deed_sorted_and_distinguishes_missing_rows(caplog) -> None:
    base = _report(
        [_plot("a", "retrieved", 2)],
        [_worker("a", "z-card"), _worker("a", "a-card")],
        [],
    )
    incoming = _report(
        [_plot("a", "not_requested", 2)],
        [_worker("a", "z-card"), _worker("a", "a-card")],
        [
            {"asset_ref": "z-card", "deed_uid": "a", "slot": None, "confidence": "collection_join"},
            {"asset_ref": "missing-card", "deed_uid": "a", "slot": None, "confidence": "collection_join"},
            {"asset_ref": "a-card", "deed_uid": "a", "slot": None, "confidence": "collection_join"},
        ],
    )

    caplog.set_level(logging.WARNING, logger="ceres.capture")
    merged = merge_reports(base, incoming)
    pending = [item for item in merged["diagnostics"] if item.get("code") == "collection_join_worker_detail_pending"]

    assert len(pending) == 1
    assert pending[0]["asset_refs"] == ["a-card", "missing-card", "z-card"]
    assert pending[0]["reasons"] == {
        "a-card": "roster_predates_stake",
        "missing-card": "no_roster_row",
        "z-card": "roster_predates_stake",
    }
    assert pending[0]["queued_for_refetch"] is True
    warning_lines = [record.getMessage() for record in caplog.records if record.name == "ceres.capture"]
    assert len(warning_lines) == 1
    assert "roster_predates_stake" in warning_lines[0]
    assert "no_roster_row" in warning_lines[0]


def test_pending_plot_does_not_emit_false_unpowered_or_runi_messages(report, details_by_id) -> None:
    target = next(
        plot for plot in report["plots"]
        if any(
            worker.get("stake_ref_uid") == plot["deed_uid"]
            and worker.get("stake_type_uid") != "STK-LND-RUNI"
            for worker in report["workers"]
        )
    )
    target["asset_fetch"] = {"status": "retrieved", "worker_detail_pending": ["pending-card"]}
    target["staking_facts"]["powered_worker_count"] = max(int(target["staking_facts"]["worker_count"]) - 1, 0)
    target_workers = [worker for worker in report["workers"] if worker.get("stake_ref_uid") == target["deed_uid"]]
    target["observed_at"] = "2099-01-01T00:00:00Z"
    for worker in target_workers:
        worker["observed_at"] = target["observed_at"]
        worker["is_powered"] = True

    board = project_board(report, details_by_id)
    row = next(item for item in board["plots"] if item["deed_uid"] == target["deed_uid"])
    notice_codes = {notice["code"] for notice in row["notices"]}

    assert "unpowered_worker" not in notice_codes
    assert "dark_discount_wasted" not in notice_codes
    assert "runi" not in notice_codes


def test_board_coverage_passthrough_includes_pending_detail_without_incomplete_notice(monkeypatch, tmp_path) -> None:
    report = _report([_plot("a", "retrieved", 0)], [], [])
    report["coverage"]["assets"].update(
        {
            "retrieved": 1,
            "expected": 1,
            "fresh": 1,
            "dropped": 0,
            "failed": 0,
            "not_requested": 0,
            "worker_detail_pending": 1,
            "worker_detail_pending_deeds": ["a"],
            "oldest_observed_at": report["plots"][0]["observed_at"],
            "complete": True,
        }
    )
    config = CeresConfig(root=tmp_path, player="player0005")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))
    monkeypatch.setattr(routes_board, "_read_snapshot_or_503", lambda *_args: (report, SimpleNamespace(generated_at="2099")))
    # Security hardening 5618d210: route status fakes implement CaptureStatus.as_dict().
    monkeypatch.setattr(
        capture_mod,
        "freshness",
        lambda *_args: SimpleNamespace(error=None, as_dict=lambda: {"error": None}),
    )
    monkeypatch.setattr(routes_board, "load_details_by_id", lambda: {})
    monkeypatch.setattr(
        routes_board,
        "_cached_project_board",
        lambda *_args, **_kwargs: ({"coverage": report["coverage"], "notices": []}, None, None, None),
    )

    response = routes_board.get_board(request)
    payload = json.loads(response.body)

    assert payload["coverage"]["worker_detail_pending"] == 1
    assert payload["coverage"]["worker_detail_pending_deeds"] == ["a"]
    assert not any(notice["code"] == "assets_incomplete" for notice in payload["notices"])
