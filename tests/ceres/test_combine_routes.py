from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from ceres import capture as capture_mod
from ceres import routes_combine
from ceres.config import CeresConfig
from ceres.routes_combine import combine_build, combine_preview


OBSERVED_AT = "2026-08-27T09:00:00Z"


def card(uid: str, bcx: int, card_id: int | str = 27, **extra):
    return {
        "uid": uid,
        "card_detail_id": card_id,
        "name": "Lyanna Natura",
        "edition": 0,
        "gold": True,
        "bcx": bcx,
        "level": bcx,
        **extra,
    }


def report(cards):
    return {
        "generated_at": OBSERVED_AT,
        "coverage": {"collection": {"complete": True}},
        "cards": cards,
    }


@pytest.fixture
def client(tmp_path):
    config = CeresConfig(root=tmp_path, player="player0005")
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))


def set_snapshot(monkeypatch, cards):
    monkeypatch.setattr(routes_combine, "_live_collection", lambda player: (report(cards), OBSERVED_AT))
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report(cards), object()))


def response_body(response):
    return json.loads(response.body)


def test_preview_returns_exact_destroyed_uids_and_bcx_survivor(client, monkeypatch):
    """Injection run: clearing hazard destroyed_uids made this test fail with an empty list; restored after observing red."""
    cards = [
        card("G-TESTCARD01", 14, level=7),
        card("G0-27-HB89SOFR8W", 2, level=4),
        card("G-9TKP51WYN4", 1, level=3),
    ]
    set_snapshot(monkeypatch, cards)

    response = combine_preview({"uids": [row["uid"] for row in cards]}, client)

    body = response_body(response)
    assert set(body) == {"account", "uids", "predicted_survivor_uid", "resulting_bcx", "hazard", "buildable", "observed_at", "witness"}
    assert [item["uid"] for item in body["witness"]] == [row["uid"] for row in cards]
    assert body["predicted_survivor_uid"] == "G-TESTCARD01"
    assert body["resulting_bcx"] == 17
    assert body["hazard"]["destroyed_uids"] == ["G0-27-HB89SOFR8W", "G-9TKP51WYN4"]
    assert body["hazard"]["warning"].count("G0-27-HB89SOFR8W") == 1
    assert body["hazard"]["warning"].count("G-9TKP51WYN4") == 1


def test_preview_refuses_selector_shape(client, monkeypatch):
    """Injection run: disabling the explicit-selector guard made this test go red with an unhashable selector TypeError; restored after observing red."""
    set_snapshot(monkeypatch, [card("C-A", 1), card("C-B", 2)])

    with pytest.raises(HTTPException) as raised:
        combine_preview({"uids": [{"id": 27, "gold": True, "edition": "0"}, {"id": 27, "gold": True, "edition": "0"}]}, client)

    assert raised.value.status_code == 400
    assert set(raised.value.detail) == {"code", "message"}
    assert "sm_combine_all" in str(raised.value.detail)


def test_preview_refuses_staked_or_unstaking_card_with_reason(client, monkeypatch):
    """Injection run: disabling the staked-state refusal made this test go red with missing structured reasons; restored after observing red."""
    cards = [card("G-STAKED", 14, stake_ref_uid="I-41-1", stake_end_date="2999-08-27T12:00:00Z"), card("G-FREE", 2)]
    set_snapshot(monkeypatch, cards)

    with pytest.raises(HTTPException) as raised:
        combine_preview({"uids": ["G-STAKED", "G-FREE"]}, client)

    assert raised.value.status_code == 409
    detail = raised.value.detail
    assert set(detail) == {"code", "message", "reasons", "preview"}
    assert any(item["uid"] == "G-STAKED" and "Unstaking from Land" in item["reason"] for item in detail["reasons"])
    assert detail["preview"]["hazard"]["destroyed_uids"] == ["G-FREE"]


def test_preview_allows_card_with_ended_land_stake(client, monkeypatch):
    """stake_ref_uid persists after a stake ends -- a past stake_end_date must not block.

    Injection run: reverting `_state_reasons` to `elif record.get("stake_ref_uid"):`
    (bare presence, no stake_end_date check) made this go red -- combine was refused
    for both cards. Restored after observing red.
    """
    cards = [
        card("G-TESTCARD01", 14, stake_ref_uid="I-294-a2b30476977796", stake_end_date="2026-02-25T00:00:00Z"),
        card("G-9TKP51WYN4", 1),
    ]
    set_snapshot(monkeypatch, cards)

    response = combine_preview({"uids": ["G-TESTCARD01", "G-9TKP51WYN4"]}, client)

    body = response_body(response)
    assert body["buildable"] is True
    assert body["predicted_survivor_uid"] == "G-TESTCARD01"


def test_preview_still_refuses_live_land_stake_with_no_end_date(client, monkeypatch):
    """A live stake (stake_ref_uid present, stake_end_date unset) must still block.

    Injection run: dropping the `stake_ref_uid` block entirely (removing the `elif`
    branch from `_state_reasons`) made this go red -- the live-staked card was
    silently accepted. Restored after observing red.
    """
    cards = [card("C-ZKE52WCJ40", 6, stake_ref_uid="I-294-5d33fb0c6734d1", stake_end_date=None), card("C-FREE", 2)]
    set_snapshot(monkeypatch, cards)

    with pytest.raises(HTTPException) as raised:
        combine_preview({"uids": ["C-ZKE52WCJ40", "C-FREE"]}, client)

    assert raised.value.status_code == 409
    detail = raised.value.detail
    assert any(item["uid"] == "C-ZKE52WCJ40" and item["reason"] == "Staked on Land" for item in detail["reasons"])


def test_preview_tie_breaks_alphabetically_independent_of_input_order(client, monkeypatch):
    """Injection run: changing survivor selection to first-array-position made this reversed-order test return C-Z; restored after observing red."""
    cards = [card("C-Z", 3, card_id="Card Z"), card("C-A", 3, card_id="Card A")]
    set_snapshot(monkeypatch, cards)

    with pytest.raises(HTTPException) as raised:
        combine_preview({"uids": ["C-Z", "C-A"]}, client)

    assert raised.value.status_code == 409
    body = raised.value.detail["preview"]
    assert body["predicted_survivor_uid"] == "C-A"
    assert body["hazard"]["destroyed_uids"] == ["C-Z"]


def test_build_returns_explicit_posting_operation(client, monkeypatch):
    cards = [card("C-A", 1), card("C-B", 2)]
    set_snapshot(monkeypatch, cards)

    response = combine_build({"uids": ["C-A", "C-B"]}, client)

    body = response_body(response)
    assert body["operation"][0] == "custom_json"
    assert body["operation"][1]["id"] == "sm_combine_cards"
    assert body["operation"][1]["required_auths"] == []
    assert body["operation"][1]["required_posting_auths"] == ["player0005"]


def test_combine_build_rejects_live_missing_card_despite_stored_preview(client, monkeypatch):
    cards = [card("C-A", 1), card("C-B", 2)]
    set_snapshot(monkeypatch, cards)
    monkeypatch.setattr(routes_combine, "_live_collection", lambda player: (report(cards[:1]), OBSERVED_AT))
    with pytest.raises(HTTPException) as exc:
        combine_build({"uids": ["C-A", "C-B"]}, client)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "selected_cards_missing"


def test_combine_build_uses_live_bcx(client, monkeypatch):
    set_snapshot(monkeypatch, [card("C-A", 1), card("C-B", 2)])
    monkeypatch.setattr(routes_combine, "_live_collection", lambda player: (report([card("C-A", 10), card("C-B", 2)]), OBSERVED_AT))
    body = response_body(combine_build({"uids": ["C-A", "C-B"]}, client))
    assert body["preview"]["resulting_bcx"] == 12
    assert body["preview"]["predicted_survivor_uid"] == "C-A"


def test_live_collection_failure_never_falls_back_to_snapshot(monkeypatch):
    def fail(*args):
        raise RuntimeError("offline")
    monkeypatch.setattr(capture_mod, "MnemosyneEvidence", fail)
    with pytest.raises(HTTPException) as exc:
        routes_combine._live_collection("player0005")
    assert exc.value.status_code == 503
