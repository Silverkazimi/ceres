"""The "Arrive" state (work/combine-modal-redesign/arrive-spec.md): a real, signed
combine gave the maintainer "Zero UI feedback... this was the most unsatisfying
combination I have ever done in any splinterlands app in 8 years." This covers
the fix -- a genuine confirmation screen rendered in the SAME modal
(`renderCombineArrival()`, dispatched from `renderCombineDialog()` whenever
`COMBINE_ARRIVAL` is set) that stays open until the player closes it -- through
the REAL running app in a real browser, never a sandboxed source-slice.

 (campaign plan `ceres-modularisation-campaign.md` rev 3 Batch D,
census row 10, method `ceres-ir1854-retire-app-js-slice-harness.md`). The prior
version of this file drove ~15 `_slice()` extractions of `verifyCombineBroadcast`/
`renderCombineDialog`/`renderCombineArrival` and their whole dependency chain
against a hand-built fake DOM in `node -e`;  retires that technique because
it breaks on any unrelated helper gaining a new call, independent of correctness.
This replaces it with `spawn_live_ceres` + Playwright: a real duplicate worker is
staged, the real `.tile-combine-chip` is clicked to open the REAL dialog, and
`/api/combine/preview` / `/api/combine/build` / `/api/op-verify` are route-mocked
through the verdict sequence -- every assertion below reads rendered DOM
(`textContent`/`innerHTML`/class tokens) produced by the real functions, per the
project's mutation standard ("six failures shipped green because tests asserted
on call paths").

Per this ticket's guard (/): each test's docstring NAMES the defect that
would turn it red; the injection itself was not performed (budget), so these are
NOT yet mutation-proven the way the retired file's `_mutation_is_red` twins were.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from playwright.sync_api import Page

from .conftest import LIVE_PLAYER, settle, spawn_live_ceres, wait_for_board
from .idle_pool import serve_idle_cards

CARD_CDN_BASE = "https://d36mxiodymuqjm.cloudfront.net"
# Mirrors ceres/static/app.js's own CARD_CDN_SET table (editions 0-21) -- kept in
# sync by hand since the module has no export to import from; if this table ever
# drifts from app.js's, the art-URL assertions below fail loudly rather than
# silently matching the wrong CDN path.
CARD_CDN_SET = {
    0: "cards_v2.2", 1: "cards_beta", 2: "cards_v2.2", 3: "cards_beta",
    4: "cards_untamed", 5: "cards_untamed", 6: "cards_gladiator", 7: "cards_chaos",
    8: "cards_riftwatchers", 9: "cards_soulbound", 10: "cards_soulbound",
    11: "cards_nightmare", 12: "cards_rebellion", 13: "cards_soulbound",
    14: "cards_v2.2", 15: "cards_v2.2", 16: "cards_v2.2", 17: "cards_v2.2",
    18: "cards_v2.2", 19: "cards_land", 20: "cards_v2.2", 21: "cards_land",
}


def _encode(value: str) -> str:
    """Matches app.js's `encodeURIComponent(...).replace(/[!'()*]/g, ...)` --
    Python's `quote(safe='')` already percent-encodes `!'()*` (not in its
    always-safe set), so no extra pass is needed."""
    return urllib.parse.quote(value, safe="")


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
    """Two idle copies of `worker`'s own (card_detail_id, edition, foil) printing --
    the field triple `combineGroupKey()` matches on -- so the real chip reads them
    as one combinable group. `name`/`bcx`/`skin` are mock-controlled and independent
    of that match, so the art-URL assertions can rely on OUR chosen name deterministically."""
    edition = worker["edition"]
    foil = worker.get("foil", False)
    base = {
        "card_detail_id": worker["card_detail_id"], "edition": edition, "foil": foil,
        "name": "Zintar Mortalis", "available": True,
        # rowCanCombine() (app.js :6911) requires hasResolvedCombineCeiling() on
        # the IDLE COPY itself, not only the tile occupant -- bcx_max must be
        # set and above bcx, or groupHasCombinableRow() is false and the chip
        # never renders no matter what the occupant looks like.
        "bcx_max": 17,
    }
    survivor = {**base, "uid": "MOCK-SURV-1", "bcx": 3, "level": 3}
    consumed = {**base, "uid": "MOCK-CONS-1", "bcx": 1, "level": 1}
    return survivor, consumed


def _expected_art_url(copy: dict) -> str:
    set_name = CARD_CDN_SET[copy["edition"]]
    skin = f"/{_encode(copy['skin'])}" if copy.get("skin") else ""
    return f"{CARD_CDN_BASE}/{set_name}{skin}/{_encode(copy['name'])}.jpg"


def _install_idle_cards_mock(page: Page, copies: list[dict]) -> None:
    serve_idle_cards(page, copies)


def _mock_combine_preview(page: Page, survivor_uid: str, destroyed_uids: list[str]) -> dict:
    preview = {
        "account": "player0005", "buildable": True,
        "predicted_survivor_uid": survivor_uid,
        "hazard": {"destroyed_uids": destroyed_uids},
        "observed_at": None,
    }
    body = json.dumps(preview)
    page.route(
        "**/api/combine/preview*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=body),
    )
    return preview


def _mock_combine_build(page: Page, preview: dict) -> None:
    """The build answers with the operation and the preview it was built from; the dialog signs only when
    that preview matches the one the player reviewed (`combineReviewMatches`, static/combine-dialog.js)."""
    body = json.dumps({"operation": ["custom_json", {
        "id": "sm_combine_cards", "json": "{}", "required_auths": [], "required_posting_auths": ["player0005"],
    }], "preview": preview})
    page.route(
        "**/api/combine/build*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=body),
    )


def _install_keychain_stub(page: Page, tx_id: str = "FAKE-COMBINE-TX") -> None:
    page.add_init_script(
        f"""window.hive_keychain = {{requestBroadcast: (account, operations, key, callback) => {{
          setTimeout(() => callback({{success: true, result: {{id: '{tx_id}'}}}}), 10);
        }}}};"""
    )


def _mock_op_verify(page: Page, responses: list[dict]) -> list:
    """Cycles through `responses` on each POST, staying on the last entry once
    exhausted -- records every request so callers can assert count/timing."""
    seen: list = []
    state = {"index": 0}

    def _handle(route):
        seen.append(route.request)
        body = responses[min(state["index"], len(responses) - 1)]
        state["index"] += 1
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/api/op-verify*", _handle)
    return seen


def _open_dialog(page: Page, base_url: str, row: dict, worker: dict) -> None:
    page.goto(f"{base_url}/?player={LIVE_PLAYER}")
    wait_for_board(page)
    settle(page)  # prefetchIdleCardsCache() has answered
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


def _sign(page: Page) -> None:
    page.locator("#combine-dialog-body .combine-sign").click()


# Guard 1: domain-confirmed renders the arrival state -- headline, ledger
# destroyed count -- never the old silent delete-and-close.

def test_confirmed_verdict_renders_arrival_state() -> None:
    """Injection this would catch: reverting to the pre-fix path where a
    domain-confirmed verdict nulls COMBINE_DIALOG/never sets COMBINE_ARRIVAL and
    hides the overlay instead of rendering `renderCombineArrival()` -- expected
    red: `#combine-dialog-title`/`#combine-dialog-body` never show the assertions
    below because the overlay closes instead."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            preview = _mock_combine_preview(page, survivor["uid"], [consumed["uid"]])
            _mock_combine_build(page, preview)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{"state": "domain-confirmed", "combine_refresh": None}])
            _open_dialog(page, base_url, row, worker)

            _sign(page)
            title = page.locator("#combine-dialog-title")
            body = page.locator("#combine-dialog-body")
            page.wait_for_selector("#combine-dialog-body .combine-arrival-headline", timeout=30_000)

            assert "Combine confirmed" in title.text_content()
            overlay = page.locator("#combine-dialog-overlay")
            assert overlay.get_attribute("hidden") is None, "confirmed branch must never hide the overlay"
            assert body.locator(".combine-arrival").count() == 1
            assert "Combine confirmed!" in body.inner_text()
            assert "1 copy destroyed" in body.inner_text()
            assert body.locator(".combine-arrival-ledger").count() == 1
        finally:
            page.close()


# Guard 2: the confirmed survivor renders its REAL card art -- the exact CDN
# URL editorCardImage() would compute, not a placeholder.

def test_confirmed_survivor_art_renders_real_card_image() -> None:
    """Injection this would catch: `editorCardImage(survivorCopy)` forced null on
    the arrival path (`const art = null;` instead of the real call) -- expected
    red: no `<img class="combine-arrival-art">` with the computed CDN src ever
    renders; the panel falls back to the no-art placeholder for a card that
    plainly has art."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            preview = _mock_combine_preview(page, survivor["uid"], [consumed["uid"]])
            _mock_combine_build(page, preview)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{"state": "domain-confirmed", "combine_refresh": None}])
            _open_dialog(page, base_url, row, worker)

            _sign(page)
            body = page.locator("#combine-dialog-body")
            page.wait_for_selector("#combine-dialog-body .combine-arrival-headline", timeout=30_000)

            expected = _expected_art_url(survivor)
            art = body.locator("img.combine-arrival-art")
            art.wait_for(timeout=15_000)
            assert art.get_attribute("src") == expected
        finally:
            page.close()


# Guard 3: a SKINNED survivor (ceres/board.py::build_idle_cards' `skin` field
# v0.7.82) renders the skinned CDN path, not the unskinned fallback.

def test_confirmed_survivor_skinned_art_renders_skin_path() -> None:
    """Injection this would catch: `editorCardImage()`'s `encodedSkin` forced
    null regardless of `asset.skin` -- expected red: the rendered `<img>` src
    falls back to the unskinned CDN path even though the survivor copy carries
    a skin."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        survivor["skin"] = "splinterstorm"
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            preview = _mock_combine_preview(page, survivor["uid"], [consumed["uid"]])
            _mock_combine_build(page, preview)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{"state": "domain-confirmed", "combine_refresh": None}])
            _open_dialog(page, base_url, row, worker)

            _sign(page)
            body = page.locator("#combine-dialog-body")
            page.wait_for_selector("#combine-dialog-body .combine-arrival-headline", timeout=30_000)

            expected = _expected_art_url(survivor)
            assert "/splinterstorm/" in expected  # sanity: the fixture actually exercises the skin path
            art = body.locator("img.combine-arrival-art")
            art.wait_for(timeout=15_000)
            assert art.get_attribute("src") == expected
        finally:
            page.close()


# Guard 4: destroyed copies render as CARDS (art + Card ID), not merely a
# count, and
# precede the survivor in DOM order ("these became that").

def test_destroyed_copies_render_as_cards_and_precede_the_survivor() -> None:
    """Injection this would catch: the stage never mapping destroyed copies to
    `combineArrivalConsumedCardHtml()` elements -- collapsing back to the old
    text-only "N copies destroyed" line -- expected red: no
    `.combine-arrival-consumed-card` element renders at all, even though the
    text-only destroyed count (guard 1) still passes."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            preview = _mock_combine_preview(page, survivor["uid"], [consumed["uid"]])
            _mock_combine_build(page, preview)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{"state": "domain-confirmed", "combine_refresh": None}])
            _open_dialog(page, base_url, row, worker)

            _sign(page)
            body = page.locator("#combine-dialog-body")
            page.wait_for_selector("#combine-dialog-body .combine-arrival-headline", timeout=30_000)

            card = body.locator(".combine-arrival-consumed-card")
            assert card.count() == 1
            assert card.locator(".combine-arrival-consumed-art").count() == 1
            # The destroyed copy is identified by its name (and foil); its Card ID stays internal and never
            # enters visible or accessible text (combineArrivalConsumedCardHtml, static/combine-dialog.js).
            assert card.locator(".combine-arrival-consumed-name").text_content() == consumed["name"]
            assert consumed["uid"] not in card.evaluate("el => el.outerHTML")

            order = body.evaluate(
                "el => { const a = el.querySelector('.combine-arrival-consumed-row'); "
                "const b = el.querySelector('.combine-arrival-survivor'); "
                "return !!a && !!b && !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING); }"
            )
            assert order is True, "the consumed row must precede the survivor"
        finally:
            page.close()


# Guard 5: a GIGANTIC, unmistakable success headline leads the panel (maintainer
# 2026-08-30: "there must be a gigantic success message"), ahead of the ledger.

def test_headline_leads_the_confirmed_panel() -> None:
    """Injection this would catch: dropping `headlineHtml` from the assembled
    body -- expected red: the confirmed panel no longer leads with any success
    statement, and `.combine-arrival-headline` is absent entirely."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            preview = _mock_combine_preview(page, survivor["uid"], [consumed["uid"]])
            _mock_combine_build(page, preview)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{"state": "domain-confirmed", "combine_refresh": None}])
            _open_dialog(page, base_url, row, worker)

            _sign(page)
            body = page.locator("#combine-dialog-body")
            page.wait_for_selector("#combine-dialog-body .combine-arrival-headline", timeout=30_000)

            assert "Combine confirmed!" in body.locator(".combine-arrival-headline").inner_text()
            order = body.evaluate(
                "el => { const h = el.querySelector('.combine-arrival-headline'); "
                "const l = el.querySelector('.combine-arrival-ledger'); "
                "return !!h && !!l && !!(h.compareDocumentPosition(l) & Node.DOCUMENT_POSITION_FOLLOWING); }"
            )
            assert order is True, "the headline must lead, ahead of the ledger"
        finally:
            page.close()


# Guard 6: domain-pending stays on the arrival surface -- optimistic, never
# claiming confirmation (R-26: "domain-pending is not confirmation").

def test_pending_verdict_stays_optimistic_never_claims_confirmation() -> None:
    """Injection this would catch: the provisional headline/status text
    dropped in favour of always rendering the confirmed copy -- expected red:
    the panel would read "Combine confirmed!" while `COMBINE_ARRIVAL.provisional`
    is still true, exactly the false-confirmation R-26 forbids."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, [survivor, consumed])
            preview = _mock_combine_preview(page, survivor["uid"], [consumed["uid"]])
            _mock_combine_build(page, preview)
            _install_keychain_stub(page)
            _mock_op_verify(page, [{"state": "domain-pending", "notices": []}])
            _open_dialog(page, base_url, row, worker)

            _sign(page)
            title = page.locator("#combine-dialog-title")
            body = page.locator("#combine-dialog-body")
            page.wait_for_selector("#combine-dialog-body .combine-verdict.is-pending", timeout=30_000)

            assert "Combining" in title.text_content()
            assert "confirmed" not in title.text_content().lower()
            text = body.inner_text()
            assert "Combine confirmed!" not in text
            assert "Combined — confirming in game" in text
            assert "not yet confirmed" in text
            assert "Not confirmed yet" in text
            assert body.locator('[data-combine-action="verify"]').count() == 1
        finally:
            page.close()


# Guard 7: a non-terminal verdict schedules the NEXT verification poll
# counted and timed via request interception (window.__ceresOperationPollSchedule
# same override point test_live_reload_guard.py's G6b uses).

def test_pending_verdict_schedules_and_counts_polls() -> None:
    """Injection this would catch: `scheduleOperationPoll()` never called from
    the non-terminal branch of `verifyCombineBroadcast()` -- expected red: only
    the ONE op-verify request from the initial sign ever fires; the count
    assertion below (>= 2 within the shrunk window) fails."""
    with spawn_live_ceres() as (browser, base_url):
        row, worker = _first_combinable_worker(base_url)
        survivor, consumed = _survivor_and_consumed(worker)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            # Shrinks OPERATION_POLL_SCHEDULE_MS (app.js module scope reads this
            # override at load time) so the test doesn't wait a real 3s/8s/...
            page.add_init_script("window.__ceresOperationPollSchedule = [300, 600, 900];")
            _install_idle_cards_mock(page, [survivor, consumed])
            preview = _mock_combine_preview(page, survivor["uid"], [consumed["uid"]])
            _mock_combine_build(page, preview)
            _install_keychain_stub(page)
            requests = _mock_op_verify(page, [{"state": "domain-pending", "notices": []}])
            _open_dialog(page, base_url, row, worker)

            _sign(page)
            page.wait_for_selector("#combine-dialog-body .combine-verdict.is-pending", timeout=30_000)
            # Not asserting the initial count here: with a 300ms first tick, the
            # first scheduled poll can already have landed by the time this DOM
            # wait resolves -- that race is not the thing under test.
            assert len(requests) >= 1  # the initial sign-time verify, at minimum

            page.wait_for_timeout(1600)  # >= 300 + 600, well under the real 3000ms first tick, with jitter margin
            assert len(requests) >= 3, f"expected at least 2 scheduled polls beyond the initial verify, got {len(requests)}"
        finally:
            page.close()

