""" step 1: exact copy refs and fail-closed read-only eligibility."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ceres import routes_cards
from ceres import routes_combine
from ceres.app import create_app
from ceres.card_actions import ACTIONS, action_eligibility, copy_ref, resolve_ref
from ceres.card_browser import build_card_rows
from ceres.config import CeresConfig

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
DETAILS = {101: {"name": "Sample Worker", "rarity": 1, "card_set": "chaos", "stats": {}}}


def card(uid="C7-101-SECRET_A", **fields):
    facts = {"uid": uid, "player": "sampleowner", "card_detail_id": 101, "edition": 7,
             "card_set": "chaos", "foil": 0, "bcx": 1, "level": 1, **fields}
    return {"uid": uid, "asset_ref": f"land-{uid[-1]}", "facts": facts,
            "classification": {"bucket": "unstaked_apparent"}}


def report():
    return {"generated_at": "2026-10-18T11:00:00Z",
            "cards": [card(), card("C7-101-SECRET_B")],
            "plots": [{"deed_uid": "I-290-internal", "facts": {"plot": "65-7-89"}}]}


def test_exact_refs_are_stable_distinct_account_bound_and_uid_free():
    rows = build_card_rows(report(), DETAILS, player="sampleowner", now=NOW)
    assert rows[0]["asset_ref"] != rows[1]["asset_ref"]
    assert rows[0]["asset_ref"] == copy_ref("sampleowner", "C7-101-SECRET_A")
    assert rows[0]["asset_ref"] != copy_ref("other_player", "C7-101-SECRET_A")
    assert "SECRET" not in json.dumps(rows)
    assert resolve_ref("sampleowner", rows[0]["asset_ref"], report()["cards"]) == "C7-101-SECRET_A"
    assert resolve_ref("other_player", rows[0]["asset_ref"], report()["cards"]) is None
    assert resolve_ref("sampleowner", "C7-101-SECRET_A", report()["cards"]) is None


@pytest.mark.parametrize(("fields", "code"), [
    ({"edition": 6}, "soulbound"),
    ({"market_id": "listing-1"}, "listed"),
    ({"rental_type": "season", "rental_direction": "out"}, "rented"),
    ({"delegated_to": "other_player"}, "delegated"),
    ({"stake_end_date": "2026-10-20T12:00:00Z", "stake_ref_uid": "I-290-internal"}, "cooldown"),
    ({"survival_date": "2026-10-20T12:00:00Z"}, "survival_lock"),
    ({"stake_ref_uid": "I-290-internal"}, "staked"),
    ({"unlock_date": "2026-10-20T12:00:00Z"}, "security_lock"),
    ({"stake_end_date": "bad date"}, "unknown"),
    ({"stake_type_uid": "STK-UNKNOWN"}, "unknown"),
    ({"player": "other_player"}, "not_controlled"),
    ({"edition": 9}, "not_land_card"),
])
def test_stake_reason_paths(fields, code):
    actions = action_eligibility(card(**fields), player="sampleowner", plots={"I-290-internal": "065-07-089"}, now=NOW)
    assert actions["stake"]["available"] is False
    assert actions["stake"]["reason_code"] == code
    assert actions["stake"]["reason"]
    if code == "cooldown":
        assert actions["stake"]["ready_at"] == fields["stake_end_date"]
        assert actions["unstake"]["reason_code"] == "not_staked"
    if code == "staked":
        assert actions["unstake"]["available"] is True
        assert actions["plot_link"]["href"] == "/?plot=065-07-089"


def test_unverified_actions_are_never_offered():
    actions = action_eligibility(card()["facts"], player="sampleowner", plots={}, now=NOW)
    assert actions["stake"]["available"] is True
    for action in ("delegate", "undelegate_return", "transfer", "burn"):
        assert actions[action] == {"available": False, "reason_code": "not_yet_supported", "reason": "Not yet supported"}
    assert set(actions) == set(ACTIONS)
    assert actions["unstake"]["reason_code"] == "not_staked"
    assert actions["plot_link"]["reason_code"] == "not_staked"
    assert all(not value["available"] for value in action_eligibility(None, player="sampleowner", plots={}, now=NOW).values())


def test_incoming_rental_with_market_id_can_still_be_used_on_land():
    incoming = card(player="other_player", delegated_to="sampleowner", rental_type="season",
                    rental_direction="in", market_id="rental-market-1")
    actions = action_eligibility(incoming, player="sampleowner", plots={}, now=NOW)
    assert actions["stake"]["available"] is True


def test_reserved_copy_blocks_every_action():
    actions = action_eligibility(card()["facts"], player="sampleowner", plots={}, now=NOW, reserved_plot="065-07-089")
    assert actions["stake"]["reason_code"] == "reserved"
    assert actions["unstake"]["reason_code"] == "reserved"
    for action in ("delegate", "undelegate_return", "transfer", "burn"):
        assert actions[action]["reason_code"] == "not_yet_supported"
        assert actions[action]["reserved"] is True
    assert actions["plot_link"]["reason_code"] == "not_staked"


def test_endpoint_fresh_reservation_and_no_uid_in_response(tmp_path, monkeypatch):
    current = report()
    routes_cards._ROWS_CACHE.clear()
    monkeypatch.setattr(routes_cards.capture_mod, "latest_snapshot", lambda config, player: (current, SimpleNamespace(token="t1", details_mtime_ns=1, generated_at=current["generated_at"])))
    monkeypatch.setattr(routes_cards.capture_mod, "load_card_pool", lambda value: value)
    monkeypatch.setattr(routes_cards, "load_details_by_id", lambda: DETAILS)
    monkeypatch.setattr(routes_cards, "_live_collection", lambda account: ({"cards": [dict(row["facts"]) for row in current["cards"]]}, "2026-10-18T12:00:00Z"))
    client = TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="sampleowner")))
    cards_response = client.get("/api/cards")
    assert cards_response.status_code == 200
    refs = [row["asset_ref"] for row in cards_response.json()["cards"]]
    response = client.post("/api/cards/eligibility", json={"asset_refs": refs + ["cc1_" + "0" * 48],
        "staged_plans": [{"key": "p1", "action": "stake", "asset_ref": "land-A", "plot": "65-7-89"}]})
    assert response.status_code == 200
    body = response.json()
    assert body["copies"][0]["actions"]["transfer"]["reason_code"] == "not_yet_supported"
    assert body["copies"][0]["actions"]["transfer"]["reserved"] is True
    assert body["copies"][1]["actions"]["stake"]["available"] is True
    assert body["copies"][2]["actions"]["stake"]["reason_code"] == "unknown"
    assert "SECRET" not in cards_response.text + response.text
    assert "C7-101" not in cards_response.text + response.text
    assert "I-290-internal" not in cards_response.text + response.text
    invalid = client.post("/api/cards/eligibility", json={"asset_refs": ["C7-101-SECRET_A"], "staged_plans": []})
    assert invalid.status_code == 422 and "SECRET" not in invalid.text


def test_endpoint_fresh_absence_is_unknown(tmp_path, monkeypatch):
    current = report()
    monkeypatch.setattr(routes_cards.capture_mod, "latest_snapshot", lambda config, player: (current, SimpleNamespace(token="t2", details_mtime_ns=1)))
    monkeypatch.setattr(routes_cards.capture_mod, "load_card_pool", lambda value: value)
    monkeypatch.setattr(routes_cards, "_live_collection_recent", lambda account: ({"cards": []}, "2026-10-18T12:00:00Z"))
    client = TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="sampleowner")))
    response = client.post("/api/cards/eligibility", json={"asset_refs": [copy_ref("sampleowner", "C7-101-SECRET_A")], "staged_plans": []})
    assert response.status_code == 200
    assert all(not state["available"] and state["reason_code"] == "unknown" for state in response.json()["copies"][0]["actions"].values())


def test_empty_eligibility_does_not_read_snapshot_or_upstream(tmp_path, monkeypatch):
    monkeypatch.setattr(routes_cards.capture_mod, "latest_snapshot", lambda *args: pytest.fail("snapshot read"))
    monkeypatch.setattr(routes_cards, "_live_collection", lambda *args: pytest.fail("upstream collection read"))
    client = TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="sampleowner")))
    response = client.post("/api/cards/eligibility", json={"asset_refs": [], "staged_plans": []})
    assert response.status_code == 200
    assert response.json()["copies"] == []
    assert response.json()["observed_at"] is None


def test_eligibility_spends_preview_limit_before_snapshot(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(routes_cards.client_limits, "limit", lambda request, bucket: calls.append(bucket))
    monkeypatch.setattr(routes_cards.capture_mod, "latest_snapshot", lambda *args: (None, None))
    client = TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="sampleowner")))
    response = client.post("/api/cards/eligibility", json={"asset_refs": [], "staged_plans": [{"key": "x"}]})
    assert response.status_code == 503
    assert calls == ["preview"]


def test_live_collection_reuses_only_recent_success(monkeypatch):
    routes_cards._ELIGIBILITY_COLLECTION_CACHE.clear()
    calls = []
    class Evidence:
        def __init__(self, account):
            self.account = account
        def collection(self):
            calls.append(self.account)
            return SimpleNamespace(data={"cards": []}, retrieved_at="fresh")
    monkeypatch.setattr(routes_combine.capture_mod, "MnemosyneEvidence", Evidence)
    one = routes_cards._live_collection_recent("sampleowner")
    two = routes_cards._live_collection_recent("sampleowner")
    other = routes_cards._live_collection_recent("otherowner")
    assert calls == ["sampleowner", "otherowner"]
    assert one == two
    assert other[1] == "fresh"
    routes_cards._ELIGIBILITY_COLLECTION_CACHE.clear()


def test_combine_live_collection_always_reads_upstream(monkeypatch):
    calls = []
    class Evidence:
        def __init__(self, account):
            self.account = account
        def collection(self):
            calls.append(self.account)
            return SimpleNamespace(data={"cards": []}, retrieved_at=str(len(calls)))
    monkeypatch.setattr(routes_combine.capture_mod, "MnemosyneEvidence", Evidence)
    one = routes_combine._live_collection("sampleowner")
    two = routes_combine._live_collection("sampleowner")
    assert calls == ["sampleowner", "sampleowner"]
    assert one[1] != two[1]
