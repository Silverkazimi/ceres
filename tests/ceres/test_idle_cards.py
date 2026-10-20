"""§6.6 test_idle_cards.py"""

from __future__ import annotations

from ceres.board import project_board
from ceres.metrics import classify_idle_availability


def test_both_idle_buckets_appear(report, details_by_id):
    board = project_board(report, details_by_id)
    buckets = {card["bucket"] for card in board["idle_cards"]}
    assert "unstaked_apparent" in buckets
    assert "stake_expired_or_historical" in buckets


def test_blocked_card_is_present_but_flagged_available_false(report, details_by_id):
    # Force one idle card into a blocked state (market-listed) without dropping it
    # this is the the internal rules hard rule: present-but-flagged, never silently excluded.
    for card in report["cards"]:
        if card["classification"]["bucket"] in ("unstaked_apparent", "stake_expired_or_historical"):
            card["facts"]["market_id"] = "1-market-listing"
            target_ref = card["asset_ref"]
            break
    else:
        raise AssertionError("fixture must contain at least one idle card")

    board = project_board(report, details_by_id)
    row = next(c for c in board["idle_cards"] if c["asset_ref"] == target_ref)
    assert row["available"] is False
    assert row["blocked_reason"] is not None


def test_classify_idle_availability_reasons():
    assert classify_idle_availability({})[0] is True
    assert classify_idle_availability({"market_id": "x"}) == (False, "listed on market")
    assert classify_idle_availability({"delegated_to": "someone"}) == (False, "delegated to someone")
    assert classify_idle_availability({"rental_type": "fixed"}) == (False, "rented (fixed)")
    available, reason = classify_idle_availability({"unlock_date": "2099-01-01T00:00:00Z"})
    assert available is False and "locked until" in reason


def test_classify_idle_availability_distinguishes_rental_direction():
    assert classify_idle_availability({"rental_type": "fixed", "rental_direction": "in"}) == (True, None)
    assert classify_idle_availability({"rental_type": "fixed", "rental_direction": "out"}) == (False, "rented (fixed)")
    assert classify_idle_availability({"rental_type": "fixed"}) == (False, "rented (fixed)")
