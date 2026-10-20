"""Combine-reachability doors (work/combine-reachability/spec.md): the worker-table
Combine button (door 1), the tile Combine affordance and its post-combine stake-or-not
prompt (door 2), and the capture-freshness gate the "stake it" choice shares with the
ledger-keyed worker_upgrade restake gate. The gather-all dialog itself, its op payload
and its chain/domain verification are untouched -- see test_combine_ui.py for those
contracts; this file covers only the new reachability surface.

 (campaign plan `ceres-modularisation-campaign.md` rev 3 Batch D,
census row 14, method `ceres-ir1854-retire-app-js-slice-harness.md`). The prior
version of this file sliced `renderEditorGroup`/`verifyCombineBroadcast`/
`handleCombineStakePromptAction`/the door-3 render functions/`settleCombineRefresh`
out of `ceres/static/app.js` by hand-picked string markers and ran each slice in a
bare `node -e`, with a `_foil_helpers()` patch (an earlier queue)
working around `cardFoilValue`/`isNonCombinableFoil` having been hoisted out of the
sliced byte range. retires the slicing technique entirely; this replaces it
with two real substitutes, chosen per test by whether the guard is about RENDERED,
CLICKED behaviour or about a pure function's return value:

- Door 1 and door 2's dialog flow are now real Playwright tests against a real
 running Ceres (`spawn_live_ceres`, model: `test_combine_sign_verify.py`), with
 `/api/idle-cards`, `/api/combine/preview`, `/api/combine/build` and
 `/api/op-verify` route-mocked and `window.hive_keychain` stubbed -- nothing real
 is ever signed. Every assertion reads rendered DOM.
- Door 3's render functions and `settleCombineRefresh` are pure-function/fetch-state
 guards with no meaningful DOM to read beyond the HTML STRING the function itself
 returns, so they now load the REAL, WHOLE module through
 `tests/ceres/vm_context.py`'s `vm.runInNewContext` sandbox instead of slicing --
 no more hand-stubbed `cardFoilValue`/`isNonCombinableFoil`, since the whole module
 carries its own real helpers.

Two rulings recorded here, per this ticket's brief:

(a) OVERTURNED, door 1 (census §3c: `process.exit(21)`, a genuine functional
 mismatch, not a stale stub). Reading `renderEditorGroup` (app.js ~3881-3960)
 shows why: its own comment says "The plot-level Combine control is REMOVED
 unconditionally... The per-tile chip is the single combine affordance." No
 code anywhere in app.js emits an element carrying
 `data-slot-action="combine-plot"` any more; `handleSlotEditorClick`'s branch
 for it (app.js ~6689) is live but unreachable from the rendered page. The old
 assertion ("button appears given a genuine duplicate") tested a contract the
 product deliberately dropped. This file now reads what the product does
 TODAY: the button stays ABSENT even under the single condition most likely to
 have shown it before (a genuine duplicate, filter on).
(b) PRODUCT CONTRADICTION, door 3 (census §3c: `process.exit(16)`, confirmed
 Class A against `cardFoilValue`, now moot since vm_context needs no stub).
 `combineTileActionHtml` (app.js :488) sets `const label = 'Combine';` -- the
 count is carried only in the tooltip/`aria-label` (app.js :489-490) -- while
 the adjacent comment (app.js :485-486) reads "Owner ruling (2026-08-30): the
 glyph rendered as tofu... word + count only, no parentheses..." The comment
 and the code it sits next to disagree about whether the count belongs in the
 visible label. This file asserts the label AS THE PRODUCT RENDERS IT TODAY
 (`'Combine'`, no count) and reports the contradiction rather than picking a
 side -- that is a product question, not a test-migration one ().

/ guard note, same as `test_combine_arrival.py`/`test_combine_sign_verify.py`:
door 1 and door 3/settle keep intact-plus-injected-red mutation pairs (cheap,
source-text injections run through the same sandbox/route). Door 2's two real-browser
tests do NOT have a mutation pair -- each docstring names the defect it would catch,
but the injection itself was not performed (budget), matching the precedent those two
already-converted files set.
"""

from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from playwright.sync_api import Page

from ceres import capture as capture_mod
from ceres import capture_freshness
from ceres.config import CeresConfig
from ceres.routes_op_preview import op_preview, plot_op_preview

from .conftest import LIVE_PLAYER, spawn_live_ceres, wait_for_board
from .vm_context import ENTRY, read_entry_source, run_in_module_context

ROOT = Path(__file__).parents[2]


# Backend: the capture-freshness gate the tile door's "stake it" choice reuses.
# Not an app.js reader -- left as-is per this ticket's scope (plot_op_preview
# freshness, backend-only).

def _report(generated_at: str) -> dict:
    deed = "deed-1"
    return {
        "generated_at": generated_at,
        "plots": [{
            "deed_uid": deed,
            "facts": {"plot": deed, "rarity": "common", "status": "natural", "resource": "GRAIN", "territory": "T", "worksite_type": "Grain Farm"},
            "worksite_facts": {"project_type": "worksite_grain"},
            "staking_facts": {"max_workers_allowed": 5, "worker_count": 1, "is_power_core_staked": True, "is_energized": False, "is_runi_staked": False},
        }],
        "workers": [{
            "asset_ref": "old-ref", "uid": "CARD-OLD", "stake_type_uid": "STK-LND-WKR",
            "stake_ref_uid": deed, "slot": 1, "asset_kind": "worker_card", "name": "Old",
            "delegated_to": None, "rental_type": None,
        }],
        "cards": [{
            "asset_ref": "new-ref", "uid": "CARD-NEW",
            "facts": {"uid": "CARD-NEW", "player": "player0005", "market_id": None, "delegated_to": None, "rental_type": None, "unlock_date": None},
        }],
        "bonus_assets": [],
        "unassigned_items": {},
    }


def _request(config):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))


def _stake_survivor_body(*, capture_generated_at: str = "2026-08-29T01:00:00Z") -> dict:
    return {
        "moves": [{"group_id": "worker", "slot_index": 0, "action": "replace", "asset_ref": "new-ref", "evict_asset_ref": "old-ref"}],
        "capture_generated_at": capture_generated_at,
        # The combine is claimed to have completed at 01:00:00Z -- the capture below
        # controls whether that claim is believed.
        "combine_verified_at": "2026-08-29T01:00:00Z",
    }


def _status_for(config, body) -> int:
    try:
        response = plot_op_preview("deed-1", body, _request(config))
        return response.status_code
    except HTTPException as exc:
        return exc.status_code


def test_stake_after_combine_refuses_a_capture_older_than_the_combine(tmp_path, monkeypatch):
    """The tile door's 'stake it' choice must refuse until a capture NEWER than the
 combine exists (the internal rules's standing restake rule: 'a restake must refuse until a
 capture newer than the combine exists') -- backed by
 `ceres.capture_freshness.capture_is_newer_than`."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    report = _report(generated_at="2026-08-29T00:59:59Z")  # captured BEFORE the combine
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    with pytest.raises(HTTPException) as raised:
        plot_op_preview("deed-1", _stake_survivor_body(), _request(config))
    assert raised.value.status_code == 409
    assert "reload the board" in str(raised.value.detail)


def test_stake_after_combine_accepts_a_capture_newer_than_the_combine(tmp_path, monkeypatch):
    """Counterpart to the refusal above: a capture taken AFTER the combine must not
 be blocked by the same gate -- otherwise the feature could never actually stake."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    report = _report(generated_at="2026-08-29T01:00:01Z")  # captured AFTER the combine
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    assert _status_for(config, _stake_survivor_body(capture_generated_at=report["generated_at"])) == 200


def test_stake_freshness_gate_mutation_is_red(tmp_path, monkeypatch):
    """Injected defect: monkeypatch capture_freshness.capture_is_newer_than() to always
 report fresh, as if routes_op_preview.py's gate were bypassed entirely -- the
 refusal proven above must then fail to fire."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    report = _report(generated_at="2026-08-29T00:59:59Z")  # still stale
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    # First call is the provenance gate (snapshot newer than board read? no)
    # every later call is the combine gate, forced to report fresh.
    checks = []
    monkeypatch.setattr(
        capture_freshness,
        "capture_is_newer_than",
        lambda capture_at, reference_at: (checks.append((capture_at, reference_at)) or len(checks) > 1),
    )
    with pytest.raises(AssertionError):
        assert _status_for(config, _stake_survivor_body()) == 409


# the ordinary picker route (no combine stamp) is gated on board provenance too.
def test_picker_route_requires_capture_provenance(tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    report = _report(generated_at="2026-08-29T01:00:00Z")
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    body = _stake_survivor_body()
    body.pop("capture_generated_at")
    body.pop("combine_verified_at")
    with pytest.raises(HTTPException) as raised:
        op_preview(body, _request(config))
    assert raised.value.status_code == 409
    assert "fresh board read" in str(raised.value.detail)


def test_picker_route_accepts_fresh_capture_provenance(tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    report = _report(generated_at="2026-08-29T01:00:00Z")
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    body = _stake_survivor_body()
    body.pop("combine_verified_at")
    assert op_preview(body, _request(config)).status_code == 200


def test_picker_route_refuses_stale_capture_provenance(tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    report = _report(generated_at="2026-08-29T01:00:00Z")
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    body = _stake_survivor_body(capture_generated_at="2026-08-29T00:59:59Z")
    body.pop("combine_verified_at")
    with pytest.raises(HTTPException) as raised:
        op_preview(body, _request(config))
    assert raised.value.status_code == 409
    assert "older board read" in str(raised.value.detail)


_PICKER_PREVIEW_PROVENANCE_BODY = r"""
BOARD = {player: 'player0005', generated_at: '2026-08-29T01:00:00Z', plots: [], totals: {}, notices: []};
CERES_STAGE.length = 0;
CERES_STAGE.push({deed_uid: 'deed-1', group_id: 'worker', slot_index: 0, action: 'replace', asset_ref: 'new-ref', evict_asset_ref: 'old-ref'});
const previewRequests = [];
fetch = async (url, options) => {
 previewRequests.push(JSON.parse(options.body));
 return {ok: true, json: async () => ({})};
};
(async () => {
 await requestOperationPreview({scope: 'portfolio'});
 const body = previewRequests[0];
 if (!body || body.capture_generated_at !== BOARD.generated_at) process.exit(30);
 if (body.combine_verified_at !== undefined) process.exit(31);
})().catch((error) => { console.error(error); process.exit(1); });
"""


def test_picker_preview_attaches_capture_provenance_via_real_module() -> None:
    """the ordinary picker/checkout preview carries the board generation stamp."""
    run_in_module_context(_PICKER_PREVIEW_PROVENANCE_BODY)


# Frontend shared helpers -- same shape as test_combine_arrival.py
# test_combine_sign_verify.py (duplicated by this package's own convention
# rather than factored further, since each combine test file is meant to stay
# self-contained beyond conftest.py/vm_context.py).

def _get(base_url: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=30) as response:
        return json.loads(response.read().decode())


def _first_combinable_worker(base_url: str) -> tuple[dict, dict]:
    """A worker the real `combineTileActionHtml()` will actually offer a chip
 for: besides `card_detail_id`/`edition`, it also requires
 `hasResolvedCombineCeiling()` (both `bcx` and `bcx_max` present) and NOT
 `isCardMaxed()` (`bcx < bcx_max`) -- a worker with an unresolved or
 already-maxed ceiling never renders the chip, no matter what idle-cards
 mock is installed."""
    board = _get(base_url, "/api/board")
    for row in board["plots"]:
        if not row.get("assets_loaded"):
            continue
        detail = _get(base_url, f"api/plots/{urllib.parse.quote(row['deed_uid'], safe='')}")
        for worker in detail.get("workers") or []:
            if worker.get("asset_ref") and isinstance(worker.get("slot"), int) and worker["slot"] >= 1 \
                    and worker.get("card_detail_id") is not None and worker.get("edition") is not None \
                    and worker.get("bcx") is not None and worker.get("bcx_max") is not None \
                    and worker["bcx"] < worker["bcx_max"]:
                return row, worker
    raise AssertionError("no staked worker with a resolved, non-maxed combine ceiling found -- cannot build the fixture")


def _survivor_and_consumed(worker: dict) -> tuple[dict, dict]:
    edition = worker["edition"]
    foil = worker.get("foil", False)
    base = {"card_detail_id": worker["card_detail_id"], "edition": edition, "foil": foil, "name": "Zintar Mortalis", "available": True, "bcx_max": 17}
    survivor = {**base, "uid": "MOCK-SURV-1", "bcx": 3, "level": 3}
    consumed = {**base, "uid": "MOCK-CONS-1", "bcx": 1, "level": 1}
    return survivor, consumed


def _install_idle_cards_mock(page: Page, copies: list[dict]) -> None:
    page.route(
        "**/api/idle-cards*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"cards": copies})),
    )


def _mock_combine_preview(page: Page, *, buildable: bool, survivor_uid: str, destroyed_uids: list[str]) -> None:
    body = json.dumps({
        "account": "player0005", "buildable": buildable,
        "predicted_survivor_uid": survivor_uid,
        "hazard": {"destroyed_uids": destroyed_uids},
    })
    page.route(
        "**/api/combine/preview*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=body),
    )


def _mock_combine_build(page: Page) -> None:
    body = json.dumps({"operation": ["custom_json", {
        "id": "sm_combine_cards", "json": "{}", "required_auths": [], "required_posting_auths": ["player0005"],
    }]})
    page.route(
        "**/api/combine/build*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=body),
    )


def _install_keychain_stub(page: Page, *, tx_id: str = "FAKE-COMBINE-TX", delay_ms: int = 10) -> None:
    page.add_init_script(
        f"""window.__ceresBroadcastCount = 0;
 window.hive_keychain = {{requestBroadcast: (account, operations, key, callback) => {{
 window.__ceresBroadcastCount += 1;
 setTimeout(() => callback({{success: true, result: {{id: '{tx_id}'}}}}), {delay_ms});
 }}}};"""
    )


def _mock_op_verify(page: Page, responses: list[dict]) -> None:
    state = {"index": 0}

    def _handle(route):
        body = responses[min(state["index"], len(responses) - 1)]
        state["index"] += 1
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/api/op-verify*", _handle)


def _expand_row(page: Page, base_url: str, row: dict):
    page.goto(f"{base_url}/?player={LIVE_PLAYER}")
    wait_for_board(page)
    page.wait_for_timeout(1200)
    page_row = page.locator(f"tr.plot-row[data-deed-uid='{row['deed_uid']}']")
    page_row.wait_for(timeout=15_000)
    page_row.click()
    expanded = page.locator(f"tr.expand-row[data-deed-uid='{row['deed_uid']}']")
    expanded.locator(".slot-editor").wait_for(timeout=15_000)
    return expanded


def _open_dialog_via_tile_chip(page: Page, base_url: str, row: dict, worker: dict) -> None:
    """Opens the real combine dialog through the real `.tile-combine-chip` --
 the only opener that sets `COMBINE_DIALOG.tileContext` (app.js :6714), which
 is what gates door 2's post-combine stake prompt."""
    expanded = _expand_row(page, base_url, row)
    chip = expanded.locator(
        f".slot-group[data-group-id='worker'] .slot-tile-cell:has(.slot-tile[data-slot-index='{worker['slot'] - 1}']) .tile-combine-chip"
    )
    chip.first.wait_for(timeout=15_000)
    chip.first.click()
    page.locator("#combine-dialog-body .combine-sign").wait_for(timeout=15_000)


# Door 1 -- the plot-level worker-table Combine button. OVERTURNED (ruling a
# see module docstring): the feature was removed from the product. This now
# reads what renders TODAY -- absence, even under the one condition (a genuine
# idle duplicate, filter on) that would have shown the button under the old
# now-dropped contract.

def test_door1_worker_table_combine_button_stays_absent_via_real_browser() -> None:
    """Real browser, real running app. A genuine idle duplicate of a staked
 worker is installed via a route-mocked /api/idle-cards, and the Combines
 filter (`#combines-filter`) is left ON (its default) -- the single
 condition most likely to show a plot-level Combine button under the OLD
 (overturned) contract. Asserts the expanded worker group renders NO element
 carrying `data-slot-action="combine-plot"` -- the per-tile chip
 (door 2, see `test_combine_sign_verify.py`) is the only combine affordance
 the product renders today. Injection this would catch: reintroducing the
 old plot-level button markup (see the mutation test below)."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            expanded = _expand_row(page, base_url, row)
            worker_group = expanded.locator(".slot-group[data-group-id='worker']")
            worker_group.wait_for(timeout=15_000)
            assert worker_group.locator('[data-slot-action="combine-plot"]').count() == 0, \
                "a plot-level Combine button rendered -- the product removed this control; the per-tile chip is the only combine affordance"
        finally:
            page.close()


def test_door1_worker_table_combine_button_mutation_is_red() -> None:
    """Injected defect: reintroduce the plot-level combine-plot button
 unconditionally into renderEditorGroup's own button row (app.js ~3915, the
 exact site the removed feature used to render from) -- served through
 `page.route` so the checked-in file is never touched. Proves the
 absence-guard above is genuinely sensitive to this control coming back, not
 a tautology that would pass regardless of what the code renders."""
    source = read_entry_source()
    anchor = "  }).join('');\n // Combine door 1 (reachability pass)"
    assert source.count(anchor) == 1, "anchor drifted -- update this injection"
    mutated = source.replace(
        anchor,
        "  }).join('') + '<button type=\"button\" data-slot-action=\"combine-plot\">Combine</button>';\n // Combine door 1 (reachability pass)",
        1,
    )
    assert mutated != source

    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        injection_seen = False

        def fulfill_injected_app(route) -> None:
            nonlocal injection_seen
            injection_seen = True
            route.fulfill(status=200, content_type="application/javascript", body=mutated)

        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            page.route("**/static/app.js*", fulfill_injected_app)
            expanded = _expand_row(page, base_url, row)
            worker_group = expanded.locator(".slot-group[data-group-id='worker']")
            worker_group.wait_for(timeout=15_000)
            assert injection_seen, "injected app.js response was not intercepted"
            assert worker_group.locator('[data-slot-action="combine-plot"]').count() > 0, \
                "injection did not reproduce the expected wrong behaviour -- test is not exercising the guarded site"
        finally:
            page.close()


# Door 2 (tile) never stakes the combine survivor without the explicit
# two-choice prompt -- "ask, never act automatically" -- and the two choices
# are exclusive: 'skip' never stages, 'stake' stages exactly once.

def test_door2_verify_never_autostakes_via_real_browser() -> None:
    """Real browser, real running app. Signs a real (route-mocked) combine
 through the tile chip; /api/op-verify reports domain-confirmed. Asserts the
 stake-prompt overlay (`#combine-stake-prompt-overlay`) appears and that
 nothing was staged yet (`#checkout-btn` stays hidden -- CERES_STAGE is
 empty) -- the shared verification path only ever OFFERS the choice, it must
 never stage the survivor itself. Injection this would catch: dropping the
 `if (tileContext) openCombineStakePrompt(...)` gate (app.js :7923) in favour
 of staking directly on domain-confirmed."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            _mock_combine_preview(page, buildable=True, survivor_uid=survivor["uid"], destroyed_uids=[consumed["uid"]])
            _mock_combine_build(page)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{"state": "domain-confirmed", "combine_refresh": None}])
            _open_dialog_via_tile_chip(page, base_url, row, worker)

            page.locator("#combine-dialog-body .combine-sign").click()
            overlay = page.locator("#combine-stake-prompt-overlay")
            overlay.wait_for(timeout=15_000)
            checkout_btn = page.locator("#checkout-btn")
            assert checkout_btn.count() == 0 or checkout_btn.is_hidden(), \
                "checkout shows a staged move before the player made a stake-prompt choice"
        finally:
            page.close()


def test_door2_prompt_choice_is_exclusive_via_real_browser() -> None:
    """Real browser, real running app, two independent dialog opens (fresh page
 each time -- the prompt closes on either choice, so a single flow can't
 exercise both). 'skip' (`[data-combine-stake-action="skip"]`) must never
 stage; 'stake' (`[data-combine-stake-action="stake"]`) must stage exactly
 once -- read from the real checkout dock (`#checkout-btn`/`#checkout-count`),
 never a call count. Injection this would catch: dropping the explicit-choice
 gate in `handleCombineStakePromptAction` (app.js ~7521) so a 'skip' click
 stages the survivor exactly like 'stake' would, which the maintainer's ruling
 ('ASK -- never act automatically... exactly two choices') forbids."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)

        # 'skip' must never stage.
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            _mock_combine_preview(page, buildable=True, survivor_uid=survivor["uid"], destroyed_uids=[consumed["uid"]])
            _mock_combine_build(page)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{"state": "domain-confirmed", "combine_refresh": None}])
            _open_dialog_via_tile_chip(page, base_url, row, worker)
            page.locator("#combine-dialog-body .combine-sign").click()
            skip = page.locator('[data-combine-stake-action="skip"]').last
            skip.wait_for(timeout=15_000)
            skip.click()
            page.locator("#combine-stake-prompt-overlay").wait_for(state="hidden", timeout=15_000)
            checkout_btn = page.locator("#checkout-btn")
            assert checkout_btn.count() == 0 or checkout_btn.is_hidden(), "'skip' staged a move"
        finally:
            page.close()

        # 'stake' must stage exactly once.
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            _mock_combine_preview(page, buildable=True, survivor_uid=survivor["uid"], destroyed_uids=[consumed["uid"]])
            _mock_combine_build(page)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{"state": "domain-confirmed", "combine_refresh": None}])
            _open_dialog_via_tile_chip(page, base_url, row, worker)
            page.locator("#combine-dialog-body .combine-sign").click()
            stake = page.locator('[data-combine-stake-action="stake"]')
            stake.wait_for(timeout=15_000)
            stake.click()
            page.locator("#combine-stake-prompt-overlay").wait_for(state="hidden", timeout=15_000)
            checkout_btn = page.locator("#checkout-btn")
            checkout_btn.wait_for(timeout=15_000)
            assert not checkout_btn.is_hidden(), "'stake' did not stage a move"
            assert page.locator("#checkout-count").inner_text() == "1", "'stake' staged more than one move"
        finally:
            page.close()


# Door 3 (candidate picker column) and the tile chip's rendered text, plus the
# global Combines filter -- real functions, whole module (vm_context), no
# hand-stubbed foil helpers.

_DOOR3_BODY = r"""
S.IDLE_CARDS_CACHE = [
 {uid: 'A1', card_detail_id: 100, edition: 1, foil: false, bcx: 1, bcx_max: 10, available: true, name: 'Card A'},
 {uid: 'A2', card_detail_id: 100, edition: 1, foil: false, bcx: 1, bcx_max: 10, available: true, name: 'Card A'},
 {uid: 'B1', card_detail_id: 200, edition: 1, foil: false, bcx: 1, bcx_max: 10, available: true, name: 'Card B'},
 {uid: 'C1', card_detail_id: 300, edition: 1, foil: false, bcx: 10, bcx_max: 10, available: true, name: 'Card C'},
 {uid: 'C2', card_detail_id: 300, edition: 1, foil: false, bcx: 10, bcx_max: 10, available: true, name: 'Card C'},
];
const candA = {uid: 'A1', card_detail_id: 100, edition: 1, foil: false, name: 'Card A'}; // 2 eligible copies
const candB = {uid: 'B1', card_detail_id: 200, edition: 1, foil: false, name: 'Card B'}; // only 1 copy owned
const candC = {uid: 'C1', card_detail_id: 300, edition: 1, foil: false, name: 'Card C'}; // both copies maxed

const htmlA = candidateCombineActionHtml(candA, 0);
if (!htmlA.includes('class="picker-combine"') || htmlA.includes('picker-combine-dash')) process.exit(10);
const htmlB = candidateCombineActionHtml(candB, 1);
if (!htmlB.includes('picker-combine-dash') || htmlB.includes('data-picker-action="combine"')) process.exit(11);
const htmlC = candidateCombineActionHtml(candC, 2);
if (!htmlC.includes('picker-combine-dash') || htmlC.includes('data-picker-action="combine"')) process.exit(12);

// Filter OFF -- absent everywhere, not even the dash, on both the picker
// column (door 3) and the tile chip (door 2), for a candidate/occupant that
// WOULD otherwise show the real Combine control.
S.COMBINES_FILTER_ENABLED = false;
if (candidateCombineActionHtml(candA, 0) !== '') process.exit(13);
const tileOff = combineTileActionHtml({deed_uid: 'p1'}, {id: 'worker'}, 0, {card_detail_id: 100, edition: 1, foil: false, name: 'Card A', bcx: 1, bcx_max: 10});
if (tileOff !== '') process.exit(14);

// Filter back ON -- the same occupant/candidate now shows the real control again.
S.COMBINES_FILTER_ENABLED = true;
const tileOn = combineTileActionHtml({deed_uid: 'p1'}, {id: 'worker'}, 0, {card_detail_id: 100, edition: 1, foil: false, name: 'Card A', bcx: 1, bcx_max: 10});
if (!tileOn.includes('class="tile-combine-chip"')) process.exit(15);

// Ruling (b), see module docstring: combineTileActionHtml's `label` is
// literally 'Combine' (app.js :488), NOT 'Combine N' -- the count lives only
// in the tooltip/aria-label (app.js :489-490), which CONTRADICTS the adjacent
// comment ("word + count only"). Asserted here as the product renders it
// TODAY; the contradiction is reported, not silently resolved in the test.
const tileLabel = tileOn.slice(tileOn.indexOf('>') + 1, tileOn.indexOf('</button>'));
if (tileLabel !== 'Combine') process.exit(16);
if (tileLabel.includes('Card A')) process.exit(17);
if (!tileOn.includes('aria-label="Combine up to 2 idle copies"')) process.exit(18); // the count DOES reach the tooltip/aria-label
"""


def test_door3_and_filter_contract_via_real_functions() -> None:
    """Calls the REAL candidateCombineActionHtml/combineTileActionHtml (whole
 module, vm_context) against synthetic data (a genuinely-combinable pair, a
 lone copy, and a maxed pair) and asserts on their actual returned HTML --
 not on source text. PRODUCT CONTRADICTION (ruling b, see module docstring):
 the tile chip's visible label is `'Combine'`, not `'Combine 2'` -- the
 adjacent source comment says count should be in the label; the code puts it
 in the tooltip/aria-label only. Asserted as rendered today; reported, not
 silently resolved."""
    run_in_module_context(_DOOR3_BODY)


def test_door3_gate_mutation_is_red() -> None:
    """Injected defect: candidateCombineInfo() drops its rowCanCombine() check, so
 a maxed pair or a lone copy would wrongly show a Combine button instead of the
 dash -- the internal rules's "absent, not disabled" rule for exactly this case."""
    source = read_entry_source()
    mutated = source.replace(
        "if (!self || !rowCanCombine(self, copies)) return null;",
        "if (!self) return null;",
        1,
    )
    assert mutated != source
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(_DOOR3_BODY, source=mutated)


def test_combines_filter_mutation_is_red() -> None:
    """Injected defect: candidateCombineActionHtml() ignores COMBINES_FILTER_ENABLED
 entirely (as if one render path forgot the flag) -- the maintainer's ruling that OFF
 means every combine affordance is absent, not merely disabled, must catch it."""
    source = read_entry_source()
    mutated = source.replace(
        "function candidateCombineActionHtml(candidate, index) {\n if (!S.COMBINES_FILTER_ENABLED) return '';\n",
        "function candidateCombineActionHtml(candidate, index) {\n",
        1,
    )
    assert mutated != source
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(_DOOR3_BODY, source=mutated)


# settleCombineRefresh() -- the post-combine board settle (). A
# confirmed combine mutates the collection server-side; this is the client
# half that waits for the server's own recapture before the board is touched
# instead of re-serving the stale pre-combine snapshot immediately. Real
# module (vm_context), fetch/loadBoard/setTimeout overridden in body_js after
# the real module has loaded -- no DOM needed, this is fetch-state-machine
# logic with a rendered-row-text assertion, not a click-driven UI flow.

def _settle_body_js() -> str:
    return r"""
setTimeout = (fn) => { Promise.resolve().then(fn); };
const toasts = [];
stageToast = (message, error) => { toasts.push({ message, error: !!error }); };

let boardRow = 'PRE-COMBINE bcx 14 level 7 PP 32941'; // the exact stale state the maintainer reported live
let capturePolls = 0;
fetch = async (url) => {
 capturePolls += 1;
 if (capturePolls === 1) {
 // First poll: the background recapture is still running -- the board must
 // NOT be touched yet, so boardRow stays at its pre-combine value here.
 return { json: async () => ({ state: 'running', fetched_deeds: 1, total_deeds: 2 }) };
 }
 // Second poll: the recapture landed.
 return { json: async () => ({ state: 'idle' }) };
};
// Stands in for the real loadBoard()/render() chain -- the actual assertion
// below is on the RENDERED ROW TEXT this produces, not on whether loadBoard
// was merely called.
loadBoard = async () => { boardRow = 'combined bcx 17 level 8 PP 40000'; };

(async () => {
 settleCombineRefresh({ triggered: true });
 // Flush enough microtask turns for the running -> idle -> loadBoard chain
 // (settleCombineRefresh() never returns a promise its caller awaits, by
 // design -- it must not block the dialog-close/toast that already ran).
 for (let i = 0; i < 30; i += 1) { await Promise.resolve(); }
 if (capturePolls < 2) process.exit(40); // never actually polled capture status
 if (boardRow !== 'combined bcx 17 level 8 PP 40000') process.exit(41); // rendered row still stale
 if (!toasts.some((t) => /updating/i.test(t.message))) process.exit(42); // no honest in-progress state shown
})().catch((error) => { console.error(error); process.exit(1); });
"""


def test_settle_combine_refresh_updates_rendered_row_via_real_function() -> None:
    """Calls the REAL settleCombineRefresh() (whole module, vm_context) against a
 fake capture-status sequence (running once, then idle) and asserts on the
 RENDERED ROW TEXT it produces -- not on a call count -- confirming the board
 only shows the new bcx/level/PP once the server's recapture has actually
 landed."""
    run_in_module_context(_settle_body_js())


def test_settle_combine_refresh_skips_status_check_mutation_is_red() -> None:
    """Injected defect: settleCombineRefresh() calls loadBoard() immediately
 instead of polling capture status first -- this is exactly the original bug
 (, then the board
 stayed on pre-combine values indefinitely). The real function must poll at
 least once before ever reaching loadBoard(); this mutation makes it reach
 loadBoard() on the very first tick, which the poll-count assertion above
 catches."""
    source = read_entry_source()
    settle_fn_start = "async function settleCombineRefresh("
    settle_fn_end = "\n// Background prefetch"
    settle_fn = source[source.index(settle_fn_start): source.index(settle_fn_end, source.index(settle_fn_start))]
    mutated_fn = settle_fn.replace(
        "  if (combineRefresh && combineRefresh.triggered === false) {",
        "  await loadBoard(); return;\n if (combineRefresh && combineRefresh.triggered === false) {",
        1,
    )
    assert mutated_fn != settle_fn
    mutated_source = source.replace(settle_fn, mutated_fn, 1)
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(_settle_body_js(), source=mutated_source)
