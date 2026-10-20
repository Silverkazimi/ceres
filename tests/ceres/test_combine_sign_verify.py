"""States 05 (Sign), 06 (Verify) and 06b (non-success verdicts) of the combine
journey (work/combine-modal-redesign, v0.7.84). Between pressing Combine and a
verdict, the modal used to show nothing distinguishable; this covers the fix --
three named steps (waiting -> active -> done) during the wait, and three
visibly distinct surfaces for the outcomes that are not domain-confirmed
(domain-pending, domain-could-not-verify, domain-mismatch, plus escalated
pending). Batch B reassigned test_combine_ui's sign/verify half and the
pending-surface contract HERE (census row 16).

 (campaign plan rev 3 Batch D, method
`ceres-ir1854-retire-app-js-slice-harness.md`). The prior version of this file
called `combineProgressStepsHtml`/`combineVerdictHtml` directly against 2
`_slice()`d dependency chains in `node -e`. retires that technique.
This replaces it with `spawn_live_ceres` + Playwright (model:
`tests/ceres/test_live_checkout.py`): a real duplicate worker is staged, the
real `.tile-combine-chip` opens the real dialog, and `/api/combine/preview` /
`/api/combine/build` / `/api/op-verify` are route-mocked -- delaying the
Keychain callback and the verify response deliberately, so the transient
'keychain'/'chain' progress steps can actually be observed mid-flight rather
than assumed. Every assertion reads rendered DOM.

Same / guard note as test_combine_arrival.py: each test's docstring
names the defect it would catch; the injection itself was not performed here
(budget) so these are not yet mutation-proven.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from playwright.sync_api import Page

from .conftest import LIVE_PLAYER, spawn_live_ceres, wait_for_board


def _get(base_url: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=30) as response:
        return json.loads(response.read().decode())


def _first_combinable_worker(base_url: str) -> tuple[dict, dict]:
    """A worker the real `combineTileActionHtml()` will actually offer a chip
 for: besides `card_detail_id`/`edition`, it also requires
 `hasResolvedCombineCeiling()` (both `bcx` and `bcx_max` present, app.js
 :6891) and NOT `isCardMaxed()` (`bcx < bcx_max`, app.js :6887) -- a worker
 with an unresolved or already-maxed ceiling never renders the chip, no
 matter what idle-cards mock is installed."""
    board = _get(base_url, "/api/board")
    for row in board["plots"]:
        if not row.get("assets_loaded"):
            continue
        detail = _get(base_url, f"/api/plots/{urllib.parse.quote(row['deed_uid'], safe='')}")
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
    # rowCanCombine() (app.js :6911) requires hasResolvedCombineCeiling() on the
    # IDLE COPY itself, not only the tile occupant -- bcx_max must be set and
    # above bcx, or groupHasCombinableRow() is false and the chip never renders.
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
    """Tracks `window.__ceresBroadcastCount` (guard 4: one broadcast per confirm)
 and delays the callback by `delay_ms` -- large enough on the 'keychain'-step
 test to observe the step while the promise is still pending."""
    page.add_init_script(
        f"""window.__ceresBroadcastCount = 0;
 window.hive_keychain = {{requestBroadcast: (account, operations, key, callback) => {{
 window.__ceresBroadcastCount += 1;
 setTimeout(() => callback({{success: true, result: {{id: '{tx_id}'}}}}), {delay_ms});
 }}}};"""
    )


def _mock_op_verify(page: Page, responses: list[dict], *, delay_ms: int = 0) -> list:
    seen: list = []
    state = {"index": 0}

    def _handle(route):
        seen.append(route.request)
        body = responses[min(state["index"], len(responses) - 1)]
        state["index"] += 1
        if delay_ms:
            time.sleep(delay_ms / 1000)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/api/op-verify*", _handle)
    return seen


def _open_dialog(page: Page, base_url: str, row: dict, worker: dict) -> None:
    page.goto(f"{base_url}/?player={LIVE_PLAYER}")
    wait_for_board(page)
    page.wait_for_timeout(1200)
    page_row = page.locator(f"tr.plot-row[data-deed-uid='{row['deed_uid']}']")
    page_row.wait_for(timeout=15_000)
    page_row.click()
    expanded = page.locator(f"tr.expand-row[data-deed-uid='{row['deed_uid']}']")
    expanded.locator(".slot-editor").wait_for(timeout=15_000)
    chip = expanded.locator(
        f".slot-group[data-group-id='worker'] .slot-tile-cell:has(.slot-tile[data-slot-index='{worker['slot'] - 1}']) .tile-combine-chip"
    )
    chip.first.wait_for(timeout=15_000)
    chip.first.click()
    page.locator("#combine-dialog-body .combine-sign").wait_for(timeout=15_000)


# Guard 1: sign is enabled ONLY when preview.buildable is true.

def test_sign_disabled_until_preview_reports_buildable() -> None:
    """Injection this would catch: the `canSign` gate dropping its
 `!!preview?.buildable` term -- expected red: the sign button would render
 enabled even while the mocked preview below says `buildable: false`."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            _mock_combine_preview(page, buildable=False, survivor_uid=survivor["uid"], destroyed_uids=[consumed["uid"]])
            _open_dialog(page, base_url, row, worker)

            sign = page.locator("#combine-dialog-body .combine-sign")
            assert sign.is_disabled(), "sign must be disabled while preview.buildable is false"
        finally:
            page.close()


def test_sign_enables_once_preview_reports_buildable() -> None:
    """Positive half of guard 1, on a fresh dialog open (never toggled mid-flow,
 which would ALSO exercise the quantity-stepper's own re-preview path -- kept
 out of scope here to isolate the buildable gate itself)."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            _mock_combine_preview(page, buildable=True, survivor_uid=survivor["uid"], destroyed_uids=[consumed["uid"]])
            _open_dialog(page, base_url, row, worker)

            sign = page.locator("#combine-dialog-body .combine-sign")
            assert not sign.is_disabled(), "sign must enable once preview.buildable is true"
        finally:
            page.close()


# Guard 2: the wait renders NAMED steps -- 'keychain' active while awaiting
# the Keychain callback -- not a static/blank dialog.

def test_keychain_step_renders_while_awaiting_the_extension() -> None:
    """Injection this would catch: `COMBINE_SIGN_STEP` never set before the
 `requestBroadcastPromise` await -- expected red: no
 `[data-combine-step="keychain"].is-active` element ever appears; the dialog
 would look frozen during the wait, the exact defect this state exists to fix."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            _mock_combine_preview(page, buildable=True, survivor_uid=survivor["uid"], destroyed_uids=[consumed["uid"]])
            _mock_combine_build(page)
            _install_keychain_stub(page, delay_ms=800)
            _mock_op_verify(page, [{"state": "domain-confirmed", "combine_refresh": None}])
            _open_dialog(page, base_url, row, worker)

            sign = page.locator("#combine-dialog-body .combine-sign")
            sign.click()
            assert sign.is_disabled()
            step = page.locator('[data-combine-step="keychain"]')
            step.wait_for(timeout=5_000)
            assert "is-active" in (step.get_attribute("class") or "")
            assert "Approve the transaction in the extension window" in page.locator("#combine-dialog-body").inner_text()
        finally:
            page.close()


# NOT WRITTEN -- STOP per method, reported rather than forced green
# a 'chain'-step-is-visibly-active guard (symmetric with the 'keychain' guard
# above) was attempted here and consistently failed even with a 2.5s artificial
# api/op-verify delay. Root cause, read from app.js's actual control flow, not
# assumed: `confirmCombineBroadcast()` sets `COMBINE_SIGN_STEP = {step: 'chain'
# txId}` and calls `renderCombineDialog()`, then immediately (no `await` in
# between) calls `verifyCombineBroadcast()`, whose OWN first lines set
# `COMBINE_SIGN_STEP = null` and `COMBINE_VERIFY = {loading: true, ...}` and
# call `renderCombineDialog()` again -- both renders happen in the same
# synchronous JS turn, so the browser never paints the intermediate 'chain'-step
# DOM at all; a real page can only ever observe the FINAL state of that turn
# ('verify' step active), never 'chain'. The retired slice test never caught
# this because it called `combineProgressStepsHtml('chain', {txId})` directly
# with a hand-picked phase, which proves the function CAN render that markup
# but never proves the app's own orchestration ever reaches a paintable frame
# showing it -- exactly the class of false confidence exists to retire.
# This is a genuine (if minor) product finding, not a test-authoring gap
# forbids fixing it inside a test migration, so it is reported, not patched.


# Guard 3: exactly ONE broadcast per confirm click -- no duplicate Keychain
# popup from a re-render or a retried verify.

def test_one_broadcast_per_confirm() -> None:
    """Injection this would catch: `confirmCombineBroadcast()` losing its
 disable-on-click guard (`signButton.disabled = true`), so a second click
 (or a re-render triggering another handler bind) fires a second
 `requestBroadcast` -- expected red: `window.__ceresBroadcastCount` reads 2
 instead of 1."""
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
            _open_dialog(page, base_url, row, worker)

            sign = page.locator("#combine-dialog-body .combine-sign")
            sign.click()
            page.wait_for_selector("#combine-dialog-body .combine-arrival-headline", timeout=30_000)
            assert page.evaluate("window.__ceresBroadcastCount") == 1
        finally:
            page.close()


# Guard 4: the pending surface never claims confirmation (R-26), and offers a
# retry reading its OWN label ("Check again"), not the notice's message text.

def test_pending_surface_offers_retry_labelled_check_again() -> None:
    """Injection this would catch: `combineRetryButtonHtml()` reading the
 pending notice's own message instead of its action's label -- expected red:
 the button text would read the long "Not confirmed yet..." sentence
 instead of "Check again"."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            _mock_combine_preview(page, buildable=True, survivor_uid=survivor["uid"], destroyed_uids=[consumed["uid"]])
            _mock_combine_build(page)
            _install_keychain_stub(page)
            requests = _mock_op_verify(page, [{"state": "domain-pending", "notices": []}])
            _open_dialog(page, base_url, row, worker)

            page.locator("#combine-dialog-body .combine-sign").click()
            retry = page.locator('[data-combine-action="verify"]')
            retry.wait_for(timeout=15_000)
            assert retry.inner_text() == "Check again"

            before = len(requests)
            retry.click()
            page.wait_for_timeout(500)
            assert len(requests) > before, "clicking retry must fire another /api/op-verify request"
        finally:
            page.close()


# Guard 5: the three non-success verdicts each render their OWN, pairwise
# distinct surface -- none shares a verdict class with another.

def test_mismatch_verdict_renders_its_own_surface() -> None:
    """Injection this would catch: `domain-mismatch` falling into the
 `domain-could-not-verify` branch -- expected red: the mismatch panel would
 read "Could not check game state" instead of "The game did not accept
 this" / "Nothing was combined"."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            _mock_combine_preview(page, buildable=True, survivor_uid=survivor["uid"], destroyed_uids=[consumed["uid"]])
            _mock_combine_build(page)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{"state": "domain-mismatch", "notices": []}])
            _open_dialog(page, base_url, row, worker)

            page.locator("#combine-dialog-body .combine-sign").click()
            verdict = page.locator(".combine-verdict.is-mismatch")
            verdict.wait_for(timeout=15_000)
            text = verdict.inner_text()
            assert "The game did not accept this" in text
            assert "Nothing was combined" in text
            assert page.locator(".combine-verdict.is-pending").count() == 0
            assert page.locator(".combine-verdict.is-could-not-verify").count() == 0
        finally:
            page.close()


def test_could_not_verify_verdict_renders_its_own_surface() -> None:
    """Injection this would catch: `domain-could-not-verify` collapsed into the
 `domain-mismatch` branch -- expected red: the panel would wrongly claim
 "Nothing was combined" about a transaction whose game-state check merely
 failed, not one the server actively rejected."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            _mock_combine_preview(page, buildable=True, survivor_uid=survivor["uid"], destroyed_uids=[consumed["uid"]])
            _mock_combine_build(page)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{"state": "domain-could-not-verify", "notices": []}])
            _open_dialog(page, base_url, row, worker)

            page.locator("#combine-dialog-body .combine-sign").click()
            verdict = page.locator(".combine-verdict.is-could-not-verify")
            verdict.wait_for(timeout=15_000)
            text = verdict.inner_text()
            assert "Could not check game state" in text
            assert page.locator(".combine-verdict.is-mismatch").count() == 0
            assert page.locator(".combine-verdict.is-pending").count() == 0
        finally:
            page.close()


def test_escalated_pending_renders_its_own_surface() -> None:
    """Injection this would catch: the `escalated` flag hardcoded false,
 ignoring `domain.evidence.pending_metadata.requires_escalation` -- expected
 red: a genuinely escalated pending verdict would keep rendering the
 ordinary `.is-pending` surface ("The game may still be catching up.")
 forever, instead of `.is-pending-escalated` ("Still not confirmed... it may
 need attention")."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            _mock_combine_preview(page, buildable=True, survivor_uid=survivor["uid"], destroyed_uids=[consumed["uid"]])
            _mock_combine_build(page)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{
                "state": "domain-pending", "notices": [],
                "domain": {"evidence": {"pending_metadata": {"requires_escalation": True}}},
            }])
            _open_dialog(page, base_url, row, worker)

            page.locator("#combine-dialog-body .combine-sign").click()
            # CSS class selectors match whole tokens -- `.is-pending` does NOT
            # match an element whose class is `is-pending-escalated` (no
            # substring leakage), so these two locators are genuinely exclusive.
            escalated = page.locator(".combine-verdict.is-pending-escalated")
            escalated.wait_for(timeout=15_000)
            assert "Still not confirmed" in escalated.inner_text()
            assert page.locator(".combine-verdict.is-pending").count() == 0
        finally:
            page.close()
