""" maintainer decisions 2026-10-28: no Ash, Stake/Unstake through the board's Changes, Recall + Return apart
from paid rentals, and each action released only once its operation is verified (greyed with a reason otherwise)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ceres import routes_cards
from ceres.app import create_app
from ceres.card_actions import ACTIONS, UNVERIFIED, UNVERIFIED_REASONS, action_eligibility, copy_ref
from ceres.config import CeresConfig

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
MENU = STATIC / "card-actions-menu.js"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
PLOTS = {"I-290-internal": "065-07-089"}
DETAILS = {101: {"name": "Sample Worker", "rarity": 1, "card_set": "chaos", "stats": {}}}
ME = "sampleowner"


def facts(**fields):
    return {"uid": "C7-101-SECRET_A", "player": ME, "card_detail_id": 101, "edition": 7, "card_set": "chaos",
            "foil": 0, "bcx": 1, "level": 1, **fields}


STATES = {
    "owned_unstaked": facts(),
    "owned_staked": facts(stake_ref_uid="I-290-internal"),
    "lent_out": facts(delegated_to="borrower1"),
    "borrowed": facts(player="player0007", delegated_to=ME),
    "soulbound": facts(edition=6),
    "rented_out": facts(delegated_to="renter1", rental_type="season", rental_direction="out"),
    "rented_in": facts(player="player0007", delegated_to=ME, rental_type="season", rental_direction="in"),
}


def eligibility(state):
    return action_eligibility(STATES[state], player=ME, plots=PLOTS, now=NOW)


def test_no_phoenix_ash_anywhere_in_the_card_menu():
    assert not any("ash" in action for action in ACTIONS)
    for path in (MENU, STATIC / "card-cart.js", STATIC / "card-handoff.js", ROOT / "ceres" / "card_actions.py"):
        text = path.read_text(encoding="utf-8")
        assert "Phoenix" not in text.replace("Phoenix Ash is left out", "")
        assert "burn_ash" not in text and "Burn for Ash" not in text


def test_action_set_splits_recall_and_return():
    assert ACTIONS == ("stake", "unstake", "plot_link", "delegate", "recall_delegation", "return_card", "transfer", "burn")
    assert UNVERIFIED == {"delegate", "recall_delegation", "return_card", "transfer", "burn"}


@pytest.mark.parametrize("state", sorted(STATES))
def test_unverified_operations_are_never_available(state):
    actions = eligibility(state)
    assert set(actions) == set(ACTIONS)
    for action in UNVERIFIED:
        assert actions[action]["available"] is False
        assert actions[action]["reason"] and "\n" not in actions[action]["reason"]


# Expected (reason_code) per state for each greyed action; active ones are None.
EXPECTED = {
    "owned_unstaked": {"stake": None, "unstake": "not_staked", "plot_link": "not_staked", "delegate": "unverified",
                       "recall_delegation": "not_delegated", "return_card": "not_borrowed", "transfer": "unverified", "burn": "unverified"},
    "owned_staked": {"stake": "staked", "unstake": None, "plot_link": None, "delegate": "unverified",
                     "recall_delegation": "not_delegated", "return_card": "not_borrowed", "transfer": "unverified", "burn": "unverified"},
    "lent_out": {"stake": "delegated", "unstake": "not_staked", "plot_link": "not_staked", "delegate": "delegated",
                 "recall_delegation": "unverified", "return_card": "not_borrowed", "transfer": "unverified", "burn": "unverified"},
    "borrowed": {"stake": None, "unstake": "not_staked", "plot_link": "not_staked", "delegate": "not_owner",
                 "recall_delegation": "not_owner", "return_card": "unverified", "transfer": "not_owner", "burn": "not_owner"},
    "soulbound": {"stake": "soulbound", "unstake": "not_staked", "plot_link": "not_staked", "delegate": "soulbound",
                  "recall_delegation": "not_delegated", "return_card": "not_borrowed", "transfer": "soulbound", "burn": "soulbound"},
    "rented_out": {"stake": "rented", "delegate": "rented", "recall_delegation": "rented", "return_card": "not_borrowed"},
    "rented_in": {"stake": None, "delegate": "not_owner", "recall_delegation": "not_owner", "return_card": "rented"},
}


@pytest.mark.parametrize("state", sorted(EXPECTED))
def test_reason_codes_per_card_state(state):
    actions = eligibility(state)
    for action, code in EXPECTED[state].items():
        assert actions[action]["reason_code"] == code, (state, action, actions[action])
        assert actions[action]["available"] is (code is None)


def test_reasons_name_the_unverified_operation_and_burn_waits_for_a_live_reward():
    assert eligibility("owned_unstaked")["burn"]["reason"] == "Not available yet — DEC reward handling is not proven on a live transaction"
    assert "delegation operation is not verified" in eligibility("owned_unstaked")["delegate"]["reason"]
    assert "recall operation is not verified" in eligibility("lent_out")["recall_delegation"]["reason"]
    assert "return operation is not verified" in eligibility("borrowed")["return_card"]["reason"]
    assert eligibility("lent_out")["delegate"]["reason"] == "Already delegated to borrower1"
    assert eligibility("rented_out")["recall_delegation"]["reason"] == "Paid rental — managed on the market"
    assert eligibility("soulbound")["burn"]["reason"] == "Soulbound cards cannot be burned for DEC"
    assert set(UNVERIFIED_REASONS) == set(UNVERIFIED)


def test_missing_owner_is_unknown_for_every_action():
    actions = action_eligibility({"uid": "x", "edition": 7}, player=ME, plots={}, now=NOW)
    assert all(value["reason_code"] == "unknown" for value in actions.values())


# ── board handoff endpoint ──────────────────────────────────────────────────────────────────────────────────────

def _client(tmp_path, monkeypatch, cards):
    current = {"generated_at": "2026-10-28T11:00:00Z", "plots": [{"deed_uid": "I-290-internal", "facts": {"plot": "65-7-89"}}],
               "cards": [{"uid": c["uid"], "asset_ref": c["uid"], "facts": c} for c in cards]}
    routes_cards._ROWS_CACHE.clear()
    routes_cards._ELIGIBILITY_COLLECTION_CACHE.clear()
    monkeypatch.setattr(routes_cards.capture_mod, "latest_snapshot", lambda config, player: (current, SimpleNamespace(token="h1", details_mtime_ns=1)))
    monkeypatch.setattr(routes_cards.capture_mod, "load_card_pool", lambda value: value)
    monkeypatch.setattr(routes_cards, "load_details_by_id", lambda: DETAILS)
    monkeypatch.setattr(routes_cards, "_live_collection", lambda account: ({"cards": [dict(c) for c in cards]}, "2026-10-28T12:00:00Z"))
    return TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player=ME)))


def test_handoff_resolves_an_unstake_for_the_board(tmp_path, monkeypatch):
    staked = facts(uid="C7-101-STAKED", stake_ref_uid="I-290-internal")
    client = _client(tmp_path, monkeypatch, [staked])
    ref = copy_ref(ME, "C7-101-STAKED")
    response = client.post("/api/cards/board-handoff", json={"action": "unstake", "asset_refs": [ref], "staged_plans": []})
    assert response.status_code == 200
    copy = response.json()["copies"][0]
    assert copy == {"asset_ref": ref, "ok": True, "board_ref": "C7-101-STAKED", "name": "Sample Worker", "level": 1,
                    "deed_uid": "I-290-internal", "plot": "065-07-089"}
    assert response.headers["cache-control"] == "no-store"


def test_handoff_refuses_what_the_menu_greys(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [facts(uid="C7-101-SOUL", edition=6), facts(uid="C7-101-FREE")])
    soul, free = copy_ref(ME, "C7-101-SOUL"), copy_ref(ME, "C7-101-FREE")
    body = client.post("/api/cards/board-handoff", json={"action": "stake", "asset_refs": [soul]}).json()
    assert body["copies"] == [{"asset_ref": soul, "ok": False, "reason": "Soulbound"}]
    # A copy already staged on the board () is refused by the plot label the board knows it by.
    staged = [{"deed_uid": "I-290-internal", "action": "stake", "asset_ref": "C7-101-FREE"}]
    body = client.post("/api/cards/board-handoff", json={"action": "stake", "asset_refs": [free], "staged_plans": staged}).json()
    assert body["copies"] == [{"asset_ref": free, "ok": False, "reason": "Already staged for plot 065-07-089"}]
    assert "C7-101" not in json.dumps(body)


def test_handoff_rejects_other_actions_and_several_stakes(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [facts(uid="C7-101-FREE")])
    ref = copy_ref(ME, "C7-101-FREE")
    for action in ("burn", "delegate", "transfer", "recall_delegation", ""):
        assert client.post("/api/cards/board-handoff", json={"action": action, "asset_refs": [ref]}).status_code == 422
    two = [ref, copy_ref(ME, "C7-101-OTHER")]
    assert client.post("/api/cards/board-handoff", json={"action": "stake", "asset_refs": two}).status_code == 422
    assert client.post("/api/cards/board-handoff", json={"action": "unstake", "asset_refs": []}).status_code == 422


def test_eligibility_labels_board_plans_saved_without_a_plot_label(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [facts(uid="C7-101-FREE")])
    ref = copy_ref(ME, "C7-101-FREE")
    staged = [{"deed_uid": "I-290-internal", "action": "unstake", "asset_ref": "C7-101-FREE"}]
    body = client.post("/api/cards/eligibility", json={"asset_refs": [ref], "staged_plans": staged}).json()
    assert body["copies"][0]["actions"]["stake"]["reason"] == "Already staged for plot 065-07-089"


# ── menu selection and greyed reasons (the real module under node) ─────────────────────────────────────────────

def run_menu(script: str):
    source = f"import * as m from '{MENU.as_uri()}'; const out = await (async () => {{ {script} }})(); console.log(JSON.stringify(out));"
    result = subprocess.run(["node", "--input-type=module", "-e", source], capture_output=True, text=True, check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_menu_lists_eight_actions_with_owner_labels_and_no_ash():
    labels = run_menu("return m.CARD_ACTIONS.map(([key, label]) => [key, label]);")
    assert labels == [["stake", "Stake"], ["unstake", "Unstake"], ["plot_link", "Go to plot"], ["delegate", "Delegate"],
                      ["recall_delegation", "Recall delegation"], ["return_card", "Return card"],
                      ["transfer", "Transfer"], ["burn", "Burn for DEC"]]


def test_menu_selection_rules_and_reasons():
    out = run_menu("""
      const ok = { available: true, reason: null };
      const e = new Map([
        ['a', { stake: ok, unstake: { available: false, reason: 'Card is not currently staked' }, burn: { available: false, reason: 'Not available yet — DEC' } }],
        ['b', { stake: ok, unstake: ok, plot_link: { available: true, href: '/?plot=065-07-089' } }],
        ['c', { unstake: ok, plot_link: { available: true, href: '/?plot=001-01-001' } }],
      ]);
      const A = { asset_ref: 'a' }, B = { asset_ref: 'b' }, C = { asset_ref: 'c' };
      return {
        none: m.selectedEligibility('stake', [], e),
        stakeOne: m.selectedEligibility('stake', [A], e),
        stakeTwo: m.selectedEligibility('stake', [A, B], e),
        unstakeMixed: m.selectedEligibility('unstake', [A, B], e),
        unstakeBoth: m.selectedEligibility('unstake', [B, C], e),
        linkTwo: m.selectedEligibility('plot_link', [B, C], e),
        linkOne: m.selectedEligibility('plot_link', [B], e),
        burn: m.selectedEligibility('burn', [A], e),
        missing: m.selectedEligibility('delegate', [A], e),
        url: m.boardHandoffUrl('unstake', [B, C], 'sampleowner'),
      };
    """)
    assert out["none"] == {"allowed": False, "reason": "Select a copy first"}
    assert out["stakeOne"]["allowed"] is True
    assert out["stakeTwo"] == {"allowed": False, "reason": "Select one copy to stake"}
    assert out["unstakeMixed"] == {"allowed": False, "reason": "Card is not currently staked"}
    assert out["unstakeBoth"]["allowed"] is True
    assert out["linkTwo"] == {"allowed": False, "reason": "Select exactly one staked copy"}
    assert out["linkOne"]["allowed"] is True
    assert out["burn"] == {"allowed": False, "reason": "Not available yet — DEC"}
    assert out["missing"] == {"allowed": False, "reason": "Card status could not be checked"}  # fail closed
    assert out["url"] == "/?player=sampleowner&open=unstake&copy=b&copy=c"


def test_menu_press_explains_greyed_actions_and_hands_stake_to_the_board():
    out = run_menu("""
      const e = new Map([['a', { stake: { available: true }, burn: { available: false, reason: 'Not available yet — DEC reward handling is not proven on a live transaction' } }]]);
      const copies = [{ asset_ref: 'a' }];
      const went = [];
      const ctx = { copies, selectedIndexes: new Set([0]), eligibility: e, player: 'sampleowner', navigate: (url) => went.push(url) };
      const press = (key) => m.cardActionPress({ dataset: { cardAction: key } }, ctx);
      const html = m.sharedCardActionsHtml(copies, new Set([0]), e, '');
      return { burn: press('burn'), stake: press('stake'), other: press('nonsense'), went,
               greyed: (html.match(/aria-disabled="true"/g) || []).length, disabled: html.includes(' disabled'),
               buttons: (html.match(/data-card-action=/g) || []).length };
    """)
    assert out["burn"] == "Burn for DEC: Not available yet — DEC reward handling is not proven on a live transaction"
    assert out["stake"].startswith("Opening the board")
    assert out["went"] == ["/?player=sampleowner&open=stake&copy=a"]
    assert out["other"] is None
    assert out["buttons"] == 8 and out["greyed"] == 7 and out["disabled"] is False


def test_menu_never_signs_and_board_handoff_uses_ordinary_staging():
    menu = MENU.read_text(encoding="utf-8")
    handoff = (STATIC / "card-handoff.js").read_text(encoding="utf-8")
    for text in (menu, handoff):
        for forbidden in ("requestSignatureThroughGate", "prepareSigningBatch", "keychain", "requestCustomJson", "sm_"):
            assert forbidden not in text
    assert "stagePricedRemovals(row, [spec]" in handoff
    assert "checkout-btn" in handoff
    assert "uid" not in menu.lower()
    picker = (STATIC / "picker-actions.js").read_text(encoding="utf-8")
    assert "S.CERES_CARD_HANDOFF" in picker
    assert "runCardHandoff(CURRENT_PLAYER)" in (STATIC / "app.js").read_text(encoding="utf-8")
