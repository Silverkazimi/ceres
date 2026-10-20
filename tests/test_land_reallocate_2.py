from __future__ import annotations

from praetoria.land_reallocate import (
    MovableCard,
    Plot,
    _allocation_failure_reason,
    _cap_allocation,
    _score_candidate_points_for_plot,
    evaluate_candidates_for_plot,
)


def _plot(cards: list[dict], *, cap: float = 100_000.0, biome: dict | None = None) -> Plot:
    return Plot(
        "deed", "1-1-1", "common", "occupied", "GRAIN", "T", 5, 1.0, cap,
        biome or {"Red": 0.0, "Blue": 0.0}, cards,
    )


def _candidate(name: str, base: float, ref: str) -> MovableCard:
    return MovableCard(name, "Red", base, 0.0, "idle", None, asset_ref=ref)


def test_under_cap_pending_slot_scores_every_candidate():
    # Red mutation: restore the unconditional slot conversion and this returns no scored candidates.
    plot = _plot([{"name": "Pending", "color": "Red", "base": 1_000, "cur": 1_000, "asset_ref": "pending", "slot": None}])
    candidates = [_candidate("First", 1_000, "first"), _candidate("Second", 2_000, "second")]

    scored = _score_candidate_points_for_plot(
        plot, candidates, target_slot_occupant=None, details_by_id={}, base_pp_ceiling=100_000,
    )

    assert {card.asset_ref for card, _score, _breakdown in scored} == {"first", "second"}


def test_pending_slot_does_not_raise_while_finding_an_open_slot():
    # Red mutation: restore the bare int(card["slot"]) comprehension and this path raises TypeError.
    plot = _plot([{"name": "Pending", "color": "Red", "base": 1_000, "cur": 1_000, "asset_ref": "pending", "slot": None}])

    scored = _score_candidate_points_for_plot(
        plot, [_candidate("Incoming", 1_000, "incoming")],
        target_slot_occupant=None, details_by_id={}, base_pp_ceiling=100_000,
    )

    assert len(scored) == 1


def test_allocation_reason_separates_pending_detail_from_cap_failure():
    # Red mutation: use one cap_allocation_failed boolean for both cases and the reasons become identical.
    pending = [
        {"base": 60_000, "asset_ref": "pending", "slot": None, "stake_type_uid": "STK-LND-WKR"},
        {"base": 60_000, "asset_ref": "resident", "slot": 1, "stake_type_uid": "STK-LND-WKR"},
    ]
    duplicate_slots = [
        {"base": 60_000, "asset_ref": "first", "slot": 1, "stake_type_uid": "STK-LND-WKR"},
        {"base": 60_000, "asset_ref": "second", "slot": 1, "stake_type_uid": "STK-LND-WKR"},
    ]

    assert _allocation_failure_reason(pending, 100_000) == "worker_slot_pending"
    assert _allocation_failure_reason(duplicate_slots, 100_000) == "cap_allocation_failed"
    pending_evaluation = evaluate_candidates_for_plot(
        _plot(pending), [_candidate("Incoming", 1_000, "incoming")],
        target_slot_occupant=None, details_by_id={}, base_pp_ceiling=100_000,
    )[0]
    duplicate_evaluation = evaluate_candidates_for_plot(
        _plot(duplicate_slots), [_candidate("Incoming", 1_000, "incoming")],
        target_slot_occupant=None, details_by_id={}, base_pp_ceiling=100_000,
    )[0]
    assert pending_evaluation.plot_impact["reason"] == "worker_slot_pending"
    assert duplicate_evaluation.plot_impact["reason"] == "cap_allocation_failed"


def test_unknown_stays_unknown_and_real_zero_stays_zero():
    # Red mutation: coerce missing terrain to 0, or coerce a point zero to unknown; one assertion fails.
    unknown = evaluate_candidates_for_plot(
        _plot([], biome={"Blue": 0.0}), [_candidate("Unknown", 0.0, "unknown")],
        target_slot_occupant=None, details_by_id={}, base_pp_ceiling=100_000,
    )[0]
    zero = evaluate_candidates_for_plot(
        _plot([], biome={"Red": 0.0}), [_candidate("Zero", 0.0, "zero")],
        target_slot_occupant=None, details_by_id={}, base_pp_ceiling=100_000,
    )[0]

    assert unknown.construction_pp == {
        "status": "unknown", "value": None, "low": None, "high": None, "reason": "terrain_modifier_unknown",
    }
    assert zero.construction_pp == {
        "status": "point", "value": 0.0, "low": 0.0, "high": 0.0, "reason": None,
    }


def test_runi_null_slot_is_not_worker_slot_pending():
    # Red mutation: treat every null slot as a pending worker and mislabel the documented Runi shape.
    runi = [{"base": 60_000, "asset_ref": "runi", "slot": None, "stake_type_uid": "STK-LND-RUNI"},
            {"base": 60_000, "asset_ref": "worker", "slot": 1, "stake_type_uid": "STK-LND-WKR"}]

    assert _allocation_failure_reason(runi, 100_000) == "cap_allocation_failed"


def test_under_cap_null_slot_is_identity_allocation():
    # Red mutation: validate slots before the identity path and the legitimate pending row is rejected.
    cards = [{"base": 1_000, "asset_ref": "pending", "slot": None}]

    assert _cap_allocation(cards, 100_000) == {"pending": 1_000.0}
