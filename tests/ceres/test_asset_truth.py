""" regression fixtures for per-worker detail truth after .

The two pending-detail tests preserve the original fixtures; the matching-slot and
direct-retrieval tests remain the negative half against making every join retrieved.
"""

from __future__ import annotations

from ceres.capture import merge_reports


def _plot(uid: str, status: str, worker_count: int = 1) -> dict:
    return {
        "deed_uid": uid,
        "staking_facts": {"worker_count": worker_count},
        "asset_fetch": {"status": status},
        "observed_at": "2099-01-01T00:00:00Z",
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


def test_collection_join_marks_worker_detail_pending_without_demoting_the_plot() -> None:
    """(a): "the card is simply absent. It must not produce a `collection_join` assignment or worker row for that deed, must not demote the plot's `asset_fetch.status` to `not_requested`, and must not reduce `retrieved`."  keeps the plot loaded while marking the worker detail pending."""
    base = _report(
        [_plot("a", "retrieved")],
        [{"asset_ref": "old-card", "stake_ref_uid": "a", "slot": 1, "name": "Old"}],
        [{"asset_ref": "old-card", "deed_uid": "a", "slot": 1, "confidence": "confident"}],
    )
    incoming = _report(
        [_plot("a", "not_requested")],
        [{"asset_ref": "new-card", "stake_ref_uid": "a", "name": "New"}],
        [{"asset_ref": "new-card", "deed_uid": "a", "slot": None, "confidence": "collection_join"}],
    )

    merged = merge_reports(base, incoming)

    plot = merged["plots"][0]
    assert plot["asset_fetch"]["status"] == "retrieved"
    assert plot["asset_fetch"]["worker_detail_pending"] == ["new-card"]
    assert merged["coverage"]["assets"]["worker_detail_pending"] == 1
    assert "a" in merged["coverage"]["assets"]["needs_refetch"]
    pending = [w for w in merged["workers"] if w.get("detail_state") == "pending"]
    assert [w["asset_ref"] for w in pending] == ["new-card"]
    assert merged["coverage"]["assets"]["complete"] is True


def test_slotless_base_rows_mark_worker_detail_pending_without_demoting_the_plot() -> None:
    """(a): "the card is simply absent. It must not produce a `collection_join` assignment or worker row for that deed, must not demote the plot's `asset_fetch.status` to `not_requested`, and must not reduce `retrieved`."  preserves the pending annotation for an existing slotless row."""
    base = _report(
        [_plot("a", "retrieved")],
        [{"asset_ref": "card-a", "stake_ref_uid": "a", "name": "Worker"}],
        [{"asset_ref": "card-a", "deed_uid": "a", "slot": None, "confidence": "collection_join"}],
    )
    incoming = _report(
        [_plot("a", "not_requested")],
        [{"asset_ref": "card-a", "stake_ref_uid": "a", "name": "Worker"}],
        [{"asset_ref": "card-a", "deed_uid": "a", "slot": None, "confidence": "collection_join"}],
    )

    merged = merge_reports(base, incoming)

    assert merged["plots"][0]["asset_fetch"]["status"] == "retrieved"
    assert merged["plots"][0]["asset_fetch"]["worker_detail_pending"] == ["card-a"]
    assert merged["coverage"]["assets"]["worker_detail_pending"] == 1
    assert "a" in merged["coverage"]["assets"]["needs_refetch"]
    pending = [w for w in merged["workers"] if w.get("detail_state") == "pending"]
    assert [w["asset_ref"] for w in pending] == ["card-a"]
    assert merged["coverage"]["assets"]["complete"] is True


def test_matching_base_slot_means_collection_join_did_not_lose_worker_detail() -> None:
    base = _report(
        [_plot("a", "retrieved")],
        [{"asset_ref": "card-a", "stake_ref_uid": "a", "slot": 1, "name": "Worker"}],
        [{"asset_ref": "card-a", "deed_uid": "a", "slot": 1, "confidence": "confident"}],
    )
    incoming = _report(
        [_plot("a", "not_requested")],
        [{"asset_ref": "card-a", "stake_ref_uid": "a", "name": "Worker"}],
        [{"asset_ref": "card-a", "deed_uid": "a", "slot": None, "confidence": "collection_join"}],
    )

    merged = merge_reports(base, incoming)

    assert merged["plots"][0]["asset_fetch"]["status"] == "retrieved"
    assert merged["coverage"]["assets"]["complete"] is True


def test_retrieved_detail_without_collection_substitution_stays_loaded() -> None:
    base = _report([_plot("a", "not_requested")], [], [])
    incoming = _report(
        [_plot("a", "retrieved")],
        [{"asset_ref": "card-a", "stake_ref_uid": "a", "slot": 2, "name": "Worker"}],
        [{"asset_ref": "card-a", "deed_uid": "a", "slot": 2, "confidence": "confident"}],
    )

    merged = merge_reports(base, incoming)

    assert merged["plots"][0]["asset_fetch"]["status"] == "retrieved"
    assert merged["coverage"]["assets"]["complete"] is True


def test_collection_join_pending_detail_keeps_base_bonus_assets() -> None:
    base = _report(
        [_plot("a", "retrieved")],
        [{"asset_ref": "old-card", "stake_ref_uid": "a", "slot": 1, "name": "Old"}],
        [{"asset_ref": "old-card", "deed_uid": "a", "slot": 1, "confidence": "confident"}],
    )
    base["bonus_assets"] = [{
        "asset_ref": "core-ref",
        "uid": "core-uid",
        "stake_ref_uid": "a",
        "asset_kind": "power_core",
    }]
    incoming = _report(
        [_plot("a", "not_requested")],
        [{"asset_ref": "new-card", "stake_ref_uid": "a", "name": "New"}],
        [{"asset_ref": "new-card", "deed_uid": "a", "slot": None, "confidence": "collection_join"}],
    )

    merged = merge_reports(base, incoming)

    assert merged["bonus_assets"] == base["bonus_assets"]
