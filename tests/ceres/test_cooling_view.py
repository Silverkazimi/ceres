from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ceres import capture as capture_mod, routes_board
from ceres.app import create_app
from ceres.cooling import build_cooling_view
from ceres.config import CeresConfig


NOW = datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc)
DETAILS = {
    79: {"id": 79, "name": "Worker", "color": "Red", "rarity": 2},
    505: {"id": 505, "name": "Runi", "color": "Red", "rarity": 4},
}


# `stake_active_unconfirmed` (this file's original bucket for a winding-down card)
# is now reserved by the classifier for a genuinely staked card with NO end date -- it is
# never admitted as a cooling candidate any more (COOLING_BUCKETS points at the new
# `stake_cooling_down`). Every fixture below that means "a card actually counting down"
# uses that name instead.
def _card(asset_ref: str, bucket: str, *, detail_id: int = 79, end: str | None = "2026-09-04T00:00:00Z", stake_type_uid=None, survival: str | None = None):
    facts = {
        "survival_date": survival,
        "uid": asset_ref,
        "card_detail_id": detail_id,
        "land_base_pp": 1000,
        "level": 1,
        "bcx": 1,
        "edition": "chaos-legion",
        "gold": False,
        "stake_end_date": end,
        "stake_type_uid": stake_type_uid,
    }
    return {"asset_ref": asset_ref, "classification": {"bucket": bucket}, "facts": facts}


def _report(cards, *, complete=True):
    return {
        "generated_at": "2026-09-03T19:06:27.883486Z",
        "cards": cards,
        "coverage": {"assets": {"complete": complete}},
        "query": {"player": "player0005"},
    }


def test_cooling_scans_idle_and_cooling_down_buckets():
    """G4: injecting the old idle-only filter makes the cooling-down row disappear."""
    body = build_cooling_view(
        _report([
            _card("idle", "stake_expired_or_historical"),
            _card("active", "stake_cooling_down"),
        ]),
        DETAILS,
        now=NOW,
    )
    assert {row["asset_ref"] for row in body["cooling"]} == {"idle", "active"}


def test_cooling_defaults_missing_stake_type_to_worker():
    """G3: injecting direct facts.get('stake_type_uid') makes the row unknown."""
    body = build_cooling_view(_report([_card("worker", "stake_cooling_down")]), DETAILS, now=NOW)
    row = body["cooling"][0]
    assert row["cooldown"]["state"] == "cooling"
    assert row["cooldown"]["ready_at"] == "2026-09-04T00:00:00Z"


def test_runi_is_absent_from_every_output_list():
    """G2: removing either Runi exclusion lets it leak into a list, including ready_at-null."""
    body = build_cooling_view(
        _report([_card("runi", "stake_expired_or_historical", detail_id=505, end="2026-09-04T01:00:00Z")]),
        DETAILS,
        now=NOW,
    )
    assert not body["cooling"] and not body["locked"]
    assert body["ready_recent_count"] == 0 and body["ready_older_count"] == 0


def test_incomplete_coverage_is_not_a_zero_result():
    """G6: injecting loaded=True makes an incomplete snapshot claim Cooling 0."""
    body = build_cooling_view(_report([_card("partial", "stake_cooling_down")], complete=False), DETAILS, now=NOW)
    assert body["loaded"] is False
    assert body["cooling"] == []
    assert body["locked"] == []


def test_staked_state_is_not_rendered_as_cooling():
    """G5: injecting a label based on not cooldown['ready'] renders a staked card as Cooling.
    A `stake_cooling_down` row with no end date is a malformed/edge case (the classifier
    never produces one), but `cooldown_state` must still refuse to call it "cooling"."""
    body = build_cooling_view(_report([_card("staked", "stake_cooling_down", end=None)]), DETAILS, now=NOW)
    assert body["cooling"] == []


def _app_with(report, tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path / ".ceres")
    app = create_app(config)
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, type("Ref", (), {"generated_at": report["generated_at"]})()))
    # the route reads the wall clock; pin it to the fixtures' moment so their cards are still counting down
    monkeypatch.setattr(routes_board, "build_cooling_view",
                        lambda report, details: build_cooling_view(report, details, now=NOW))
    return TestClient(app)


def test_cooling_route_answers_only_for_the_boards_own_capture(tmp_path, monkeypatch):
    """ /  phase 1: the stamp of the board the page drew is required and must match."""
    report = _report([_card("route", "stake_cooling_down")])
    client = _app_with(report, tmp_path, monkeypatch)
    body = client.get("/api/cooling", params={"player": "player0005", "capture_generated_at": report["generated_at"]}).json()
    assert body["generated_at"] == report["generated_at"]
    assert [row["asset_ref"] for row in body["cooling"]] == ["route"]
    assert "age_seconds" not in body and "stale" not in body


def test_cooling_route_refuses_missing_or_older_stamp(tmp_path, monkeypatch):
    report = _report([_card("route", "stake_cooling_down")])
    client = _app_with(report, tmp_path, monkeypatch)
    missing = client.get("/api/cooling", params={"player": "player0005"})
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "capture_unknown"
    older = client.get("/api/cooling", params={"player": "player0005", "capture_generated_at": "2026-09-01T00:00:00Z"})
    assert older.status_code == 409 and older.json()["detail"]["code"] == "capture_superseded"


def test_survival_dead_card_lands_in_locked():
    """The survival date is passed through: a future survival_date is survival_locked, never cooling or ready."""
    body = build_cooling_view(
        _report([_card("dead", "stake_expired_or_historical", end="2026-08-01T00:00:00Z", survival="2026-09-05T00:00:00Z")]),
        DETAILS,
        now=NOW,
    )
    assert [row["asset_ref"] for row in body["locked"]] == ["dead"]
    assert body["cooling"] == [] and body["ready_recent_count"] == 0


def test_survival_dead_card_without_end_date_is_counted_and_listed():
    body = build_cooling_view(
        _report([_card("dead-noend", "stake_expired_or_historical", end=None, survival="2026-09-05T00:00:00Z")]),
        DETAILS,
        now=NOW,
    )
    assert body["survival_locked_count"] == 1
    assert [row["asset_ref"] for row in body["locked"]] == ["dead-noend"] and body["cooling"] == []


def test_ready_cards_are_counts_split_at_seven_days():
    body = build_cooling_view(
        _report([
            _card("recent", "stake_expired_or_historical", end="2026-09-01T00:00:00Z"),
            _card("old", "stake_expired_or_historical", end="2026-08-01T00:00:00Z"),
        ]),
        DETAILS,
        now=NOW,
    )
    assert body["ready_recent_count"] == 1 and body["ready_older_count"] == 1
    assert "ready" not in body


def test_cooling_rows_are_grouped_by_ready_time():
    body = build_cooling_view(
        _report([
            _card("a", "stake_cooling_down", end="2026-09-04T00:00:00Z"),
            _card("b", "stake_cooling_down", end="2026-09-04T00:00:00Z"),
            _card("c", "stake_cooling_down", end="2026-09-05T00:00:00Z"),
        ]),
        DETAILS,
        now=NOW,
    )
    assert [(group["ready_at"], group["count"]) for group in body["groups"]] == [("2026-09-04T00:00:00Z", 2), ("2026-09-05T00:00:00Z", 1)]
