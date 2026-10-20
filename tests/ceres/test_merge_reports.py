"""`ceres.capture.merge_reports` (2026-08-21 bulk-first redesign): a quick refresh or a
single-deed expand must never regress asset coverage a prior full capture or lazy
expand already earned."""

from __future__ import annotations

from ceres.capture import merge_reports


def _plot(uid, retrieved):
    return {"deed_uid": uid, "asset_fetch": {"status": "retrieved" if retrieved else "not_requested"}}


def _report(plots, workers=None, bonus_assets=None, cards=None, unassigned_items=None, collection_coverage=None):
    retrieved = [p["deed_uid"] for p in plots if p["asset_fetch"]["status"] == "retrieved"]
    coverage = {"assets": {"retrieved": len(retrieved), "expected": len(plots), "complete": len(retrieved) == len(plots)}}
    if collection_coverage is not None:
        coverage["collection"] = collection_coverage
    return {
        "plots": plots,
        "workers": workers or [],
        "bonus_assets": bonus_assets or [],
        "cards": cards if cards is not None else [],
        "unassigned_items": unassigned_items,
        "coverage": coverage,
    }


def test_base_none_returns_incoming_unchanged():
    incoming = _report([_plot("a", False)])
    assert merge_reports(None, incoming) is incoming


def test_quick_refresh_does_not_regress_a_prior_full_capture():
    full = _report(
        [_plot("a", True), _plot("b", True)],
        workers=[
            {"asset_ref": "card-a1", "stake_ref_uid": "a", "slot": 1, "name": "W1"},
            {"asset_ref": "card-a2", "stake_ref_uid": "a", "slot": 2, "name": "W2"},
            {"asset_ref": "card-b1", "stake_ref_uid": "b", "slot": 1, "name": "W3"},
        ],
    )
    quick = _report(
        [_plot("a", False), _plot("b", False)],
        workers=[
            {"asset_ref": "card-a1", "stake_ref_uid": "a"},
            {"asset_ref": "card-b1", "stake_ref_uid": "b"},
        ],
    )
    merged = merge_reports(full, quick)
    assert {p["deed_uid"]: p["asset_fetch"]["status"] for p in merged["plots"]} == {"a": "retrieved", "b": "retrieved"}
    workers = {w["asset_ref"]: w for w in merged["workers"]}
    assert {w["stake_ref_uid"] for w in merged["workers"]} == {"a", "b"}
    assert workers["card-a1"]["slot"] == 1
    assert workers["card-a1"]["name"] == "W1"
    assert workers["card-a2"]["slot"] == 2
    assert workers["card-a2"]["name"] == "W2"
    assert workers["card-b1"]["slot"] == 1
    assert merged["coverage"]["assets"]["complete"] is True


def test_worker_detail_overlay_requires_the_same_deed():
    base = _report(
        [_plot("a", True), _plot("b", True)],
        workers=[{"asset_ref": "card-a1", "stake_ref_uid": "a", "slot": 1, "name": "W1"}],
    )
    incoming = _report(
        [_plot("a", False), _plot("b", False)],
        workers=[{"asset_ref": "card-a1", "stake_ref_uid": "b"}],
    )
    merged = merge_reports(base, incoming)
    worker = merged["workers"][0]
    assert worker["stake_ref_uid"] == "b"
    assert "slot" not in worker
    assert "name" not in worker


def test_expand_one_deed_merges_into_existing_quick_snapshot():
    quick = _report([_plot("a", False), _plot("b", False), _plot("c", False)])
    expanded = _report([_plot("a", False), _plot("b", True), _plot("c", False)], workers=[{"stake_ref_uid": "b", "name": "W2"}])
    merged = merge_reports(quick, expanded)
    statuses = {p["deed_uid"]: p["asset_fetch"]["status"] for p in merged["plots"]}
    assert statuses == {"a": "not_requested", "b": "retrieved", "c": "not_requested"}
    assert [w["stake_ref_uid"] for w in merged["workers"]] == ["b"]
    assert merged["coverage"]["assets"]["retrieved"] == 1
    assert merged["coverage"]["assets"]["complete"] is False


def test_two_sequential_expands_accumulate_coverage():
    quick = _report([_plot("a", False), _plot("b", False)])
    expand_a = _report([_plot("a", True), _plot("b", False)], workers=[{"stake_ref_uid": "a", "name": "W1"}])
    after_a = merge_reports(quick, expand_a)
    expand_b = _report([_plot("a", False), _plot("b", True)], workers=[{"stake_ref_uid": "b", "name": "W2"}])
    after_b = merge_reports(after_a, expand_b)
    statuses = {p["deed_uid"]: p["asset_fetch"]["status"] for p in after_b["plots"]}
    assert statuses == {"a": "retrieved", "b": "retrieved"}
    assert {w["stake_ref_uid"] for w in after_b["workers"]} == {"a", "b"}
    assert after_b["coverage"]["assets"]["complete"] is True


def test_incoming_retrieved_wins_over_base_retrieved_stale_copy():
    """When BOTH sides retrieved the same deed, incoming's (freshest) data wins."""
    base = _report([_plot("a", True)], workers=[{"asset_ref": "card-a", "stake_ref_uid": "a", "name": "STALE"}])
    incoming = _report([_plot("a", True)], workers=[{"asset_ref": "card-a", "stake_ref_uid": "a", "name": "FRESH"}])
    merged = merge_reports(base, incoming)
    assert [w["name"] for w in merged["workers"]] == ["FRESH"]


def test_expand_deed_carries_forward_base_collection_and_unassigned_items():
    """expand_deed() reports carry no collection sweep or unassigned-item capture of
    their own (as_of=None, collection_payload=None) -- a lazy expand must not wipe
    those out if the base snapshot already had them."""
    quick = _report([_plot("a", False)], cards=[{"asset_ref": "c1"}], unassigned_items={"totem": {"capturable": True}})
    expanded = _report([_plot("a", True)], workers=[{"stake_ref_uid": "a"}], cards=[], unassigned_items=None)
    merged = merge_reports(quick, expanded)
    assert merged["cards"] == [{"asset_ref": "c1"}]
    assert merged["unassigned_items"] == {"totem": {"capturable": True}}


def test_quick_refresh_carries_forward_a_prior_full_capture_collection_coverage():
    """ regression: a full capture() sets coverage.collection.complete=True
    alongside its real collection sweep in `cards`; capture_quick() never fetches the
    collection at all (collection_payload=None -> collection.complete=False on the
    incoming report). merge_reports already carried `cards` forward from base so it
    was never actually dropped -- but it left coverage["collection"] at incoming's
    always-False value, so /api/combine/preview's `_collection()` gate
    (routes_combine.py) refused every combine with `collection_incomplete` against a
    snapshot that in fact held the full collection. Injected the defect this guards
    (deleting the `carried_forward_cards` collection-coverage carry-forward block in
    merge_reports) and watched this assertion fail before the fix."""
    full = _report(
        [_plot("a", True)],
        workers=[{"stake_ref_uid": "a"}],
        cards=[{"asset_ref": f"c{i}"} for i in range(50)],
        collection_coverage={"retrieved": 50, "expected": None, "complete": True},
    )
    quick = _report(
        [_plot("a", False)],
        cards=[],
        collection_coverage={"retrieved": 0, "expected": None, "complete": False},
    )
    merged = merge_reports(full, quick)
    assert merged["cards"] == [{"asset_ref": f"c{i}"} for i in range(50)]
    assert merged["coverage"]["collection"]["complete"] is True


def test_failed_refresh_preserves_last_known_collection_and_balances():
    """A failed quick collection/balance refresh must retain the last known values;
    deleting the existing carry-forward branches was injected and made this assertion
    fail before the branches were restored."""
    base = _report(
        [_plot("a", True)],
        cards=[{"asset_ref": "known-card"}],
        collection_coverage={"retrieved": 1, "expected": None, "complete": True},
    )
    base["tc_balance"] = {"value": 3.0}
    base["permit_balance"] = {"value": 4.0}
    failed = _report(
        [_plot("a", False)],
        cards=[],
        collection_coverage={"retrieved": 0, "expected": None, "complete": False},
    )
    failed["tc_balance"] = None
    failed["permit_balance"] = None
    merged = merge_reports(base, failed)
    assert merged["cards"] == [{"asset_ref": "known-card"}]
    assert merged["coverage"]["collection"]["complete"] is True
    assert merged["tc_balance"] == {"value": 3.0}
    assert merged["permit_balance"] == {"value": 4.0}


def test_successful_empty_collection_replaces_stale_cards():
    """A successful empty sweep is authoritative: changing the complete-coverage
    check back to a cards-only check made this test retain stale cards."""
    base = _report(
        [_plot("a", True)],
        cards=[{"asset_ref": "stale-card"}],
        collection_coverage={"retrieved": 1, "expected": None, "complete": True},
    )
    incoming = _report(
        [_plot("a", False)],
        cards=[],
        collection_coverage={"retrieved": 0, "expected": None, "complete": True},
    )
    merged = merge_reports(base, incoming)
    assert merged["cards"] == []
    assert merged["coverage"]["collection"]["complete"] is True


def test_a_fresh_collection_sweep_on_incoming_is_never_overridden_by_a_stale_base():
    """The carry-forward is one-directional: when `incoming` itself ran a real
    collection sweep (a full capture), its own fresh coverage must win outright, even
    if base also had one -- merge_reports must not resurrect a stale base flag over a
    fresh incoming one."""
    old_full = _report(
        [_plot("a", True)],
        workers=[{"stake_ref_uid": "a"}],
        cards=[{"asset_ref": "stale"}],
        collection_coverage={"retrieved": 1, "expected": None, "complete": True},
    )
    new_full = _report(
        [_plot("a", True)],
        workers=[{"stake_ref_uid": "a"}],
        cards=[{"asset_ref": "fresh"}],
        collection_coverage={"retrieved": 1, "expected": None, "complete": True},
    )
    merged = merge_reports(old_full, new_full)
    assert merged["cards"] == [{"asset_ref": "fresh"}]
    assert merged["coverage"]["collection"]["complete"] is True


def test_a_fresh_empty_collection_sweep_clears_stale_base_cards():
    base = _report(
        [_plot("a", True)],
        cards=[{"asset_ref": "stale"}],
        collection_coverage={"retrieved": 1, "expected": None, "complete": True},
    )
    incoming = _report(
        [_plot("a", True)],
        cards=[],
        collection_coverage={"retrieved": 0, "expected": None, "complete": True},
    )
    merged = merge_reports(base, incoming)
    assert merged["cards"] == []
    assert merged["coverage"]["collection"]["complete"] is True
