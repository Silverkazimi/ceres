"""One clean end-to-end run of Settle (v0.7.79) + Arrive (v0.7.81) TOGETHER --
never previously observed running as one sequence (per the joint-verification
brief, 2026-08-30). Drives a REAL combine dialog against recorded board data and synthetic idle-card
partners through the real board UI: open -> sign (stubbed Keychain) -> /api/op-verify
domain-confirmed -> arrival renders AND settleCombineRefresh() polls
/api/capture/status AND the board recaptures AND the worker/idle row shows the
new value, with no manual refresh anywhere.

Everything mutation-shaped is intercepted client-side with `page.route` (the
same technique test_live_runi_confirmation.py already uses): `/api/op-verify`,
`/api/capture/status` and the post-settle `/api/board` are all faked, so this
never triggers a real background full-capture against the shared production
snapshot and never contends with any other agent's live capture. `/api/combine/
preview` and `/api/combine/build` are left REAL -- both are read-only/construction
routes (ceres/routes_combine.py's own docstring: "Read-only preview and
operation-building routes") that only read the on-disk snapshot, so hitting them
for real is safe and is what proves the dialog's own eligibility/preview logic
actually works against real data. Nothing is signed or broadcast --
`window.hive_keychain.requestBroadcast` is stubbed before the dialog is ever
opened.
"""

from __future__ import annotations

import copy
import json

import pytest
from playwright.sync_api import Browser, Page

from .conftest import LIVE_PLAYER, spawn_live_ceres


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.goto(f"{base_url}/?player={LIVE_PLAYER}")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    return page


def _find_real_combine_candidate(page: Page) -> dict:
    """Locate a recorded plot on the fixture board with a staked worker that has a
 combinable idle duplicate, re-deriving app.js's own eligibility rule
 (combineGroupKey/rowCanCombine/groupHasCombinableRow) against the real public
 /api/board and /api/idle-cards payloads -- app.js's BOARD/IDLE_CARDS_CACHE are
 module-scoped (index.html loads app.js as <script type="module">), invisible
 to page.evaluate()'s eval-in-page-world, so eligibility can't be read from the
 module's own state and must be re-derived from the same public data it reads."""
    board = page.evaluate("async () => (await fetch('/api/board')).json()")
    idle_resp = page.evaluate("async () => (await fetch('/api/idle-cards')).json()")
    idle_cards = idle_resp.get("cards", [])

    groups: dict[str, list[dict]] = {}
    for card in idle_cards:
        if card.get("card_detail_id") is None or card.get("edition") is None:
            continue
        key = f"{card.get('card_detail_id')}|{card.get('edition')}|{1 if card.get('foil') else 0}"
        groups.setdefault(key, []).append(card)

    def is_maxed(c: dict) -> bool:
        return c.get("bcx_max") is not None and c.get("bcx") is not None and c["bcx"] >= c["bcx_max"]

    def has_combinable_row(copies: list[dict]) -> bool:
        for c in copies:
            if is_maxed(c):
                continue
            partners = [o for o in copies if o is not c and o.get("available") is not False]
            if partners:
                return True
        return False

    # Per-deed `workers` detail is a LAZY field -- GET /api/board's own plot rows
    # carry an empty `workers: []` even when `assets_loaded` is true (bulk-only
    # board load); the real per-worker array only comes back from
    # GET /api/plots/{deed_uid}, the same detail endpoint the tile UI itself
    # fetches on row-expand. So candidates are found by walking that endpoint
    # not the bulk board payload.
    for index, row in enumerate(board["plots"]):
        deed_uid = row["deed_uid"]
        detail = page.evaluate(
            "async uid => (await fetch(`/api/plots/${encodeURIComponent(uid)}`)).json()", deed_uid
        )
        for worker in detail.get("workers", []):
            if worker.get("card_detail_id") is None or worker.get("edition") is None:
                continue
            key = f"{worker.get('card_detail_id')}|{worker.get('edition')}|{1 if worker.get('foil') else 0}"
            copies = groups.get(key)
            # >=3, not just >=2: the test forces the dialog down to its minimum
            # 2-copy selection so exactly one copy is destroyed, leaving the group
            # with >=2 copies (still combinable) to re-open and verify against
            # afterward -- a 2-copy group would leave nothing left to reopen.
            if copies and len(copies) >= 3 and has_combinable_row(copies):
                return {"index": index, "deedUid": deed_uid, "worker": worker, "copies": copies}
    raise AssertionError("fixture snapshot must expose at least one plot with a combinable staked worker")


def _expand_plot(page: Page, index: int) -> None:
    row = page.locator("#plot-tbody > tr.plot-row").nth(index)
    if row.locator("xpath=following-sibling::tr[1]").evaluate("node => node.style.display") == "none":
        row.click()
    expanded = page.locator("tr.expand-row").nth(index)
    expanded.locator(".slot-editor").wait_for(timeout=30_000)


def _install_settle_mocks(page: Page, survivor_uid: str, destroyed_uids: list[str]) -> dict:
    """Fake /api/op-verify (domain-confirmed, real combine_refresh shape),
 /api/capture/status (one 'running' tick then 'idle'/done -- exercises the real
 poll loop, not just its terminal state), and the post-settle /api/board +
 /api/idle-cards (survivor's bcx/level bumped, destroyed copies dropped).
 Real payloads are read first and mutated in place so every OTHER field stays
 exactly what a real recapture would return.

 LOCAL INVARIANT: a card that is Staked on Land can never be a combine input
 (game-enforced, the internal rules) -- the survivor is always drawn from
 IDLE_CARDS_CACHE, never from a plot's `workers[]`. So the surface that shows
 the settled result is the idle-cards cache (re-fetched fresh after
 IDLE_CARDS_CACHE is nulled post-loadBoard()), not the staked worker table.
    """
    board = page.evaluate("async () => (await fetch('/api/board')).json()")
    idle_resp = page.evaluate("async () => (await fetch('/api/idle-cards')).json()")
    idle_after = copy.deepcopy(idle_resp)
    bumped = {"bcx": None}
    kept_cards = []
    for card in idle_after["cards"]:
        if card.get("uid") in destroyed_uids:
            continue
        if card.get("uid") == survivor_uid:
            card["bcx"] = (card.get("bcx") or 0) + 1
            bumped["bcx"] = card["bcx"]
        kept_cards.append(card)
    idle_after["cards"] = kept_cards
    assert bumped["bcx"] is not None, "survivor must be a real idle card on the fixture"

    poll_calls = {"count": 0}

    def _status_route(route):
        poll_calls["count"] += 1
        if poll_calls["count"] == 1:
            route.fulfill(status=200, content_type="application/json",
                           body=json.dumps({"state": "running", "total_deeds": 130, "fetched_deeds": 40}))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"state": "idle"}))

    page.route("**/api/capture/status*", _status_route)
    page.route(
        "**/api/op-verify*",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "state": "domain-confirmed",
                "domain": {"status": "domain_verified"},
                "refreshed_deeds": [],
                "combine_refresh": {"triggered": True, "mode": "full", "already_running": False},
                "notices": [],
            }),
        ),
    )
    page.route(
        "**/api/board*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(board)),
    )
    page.route(
        "**/api/idle-cards*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(idle_after)),
    )
    page.evaluate(
        """window.__jointKeychain = {callback: false, requestBroadcast: (account, operations, key, callback) => {
 setTimeout(() => { window.__jointKeychain.callback = true; callback({success: true, result: {id: 'FAKE-JOINT-TX'}}); }, 25);
 }};
 window.hive_keychain = window.__jointKeychain;"""
    )
    return bumped


def test_settle_and_arrive_run_together_end_to_end(live_browser: tuple[Browser, str], tmp_path) -> None:
    page = _page(live_browser)
    candidate = _find_real_combine_candidate(page)
    _expand_plot(page, candidate["index"])

    combine_btn = page.locator("tr.expand-row").nth(candidate["index"]).locator(
        "button[data-slot-action='combine-plot']"
    )
    combine_btn.wait_for(timeout=30_000)
    combine_btn.click()

    overlay = page.locator("#combine-dialog-overlay")
    overlay.wait_for(timeout=30_000)
    # A plot with more than one combinable worker shows a chooser first (door 3
    # sub-step) -- pick the first group either way.
    chooser = page.locator(".combine-plot-chooser .picker-chip")
    if chooser.count():
        chooser.first.click()

    # Force the amount stepper down to its minimum (2 copies) -- the dialog's own
    # default is "to max level" (often the WHOLE group), which would leave nothing
    # combinable to re-open and verify against afterward.
    dec_btn = page.locator("#combine-dialog-body button[data-combine-qty='dec']")
    for _ in range(20):
        if dec_btn.count() == 0 or dec_btn.is_disabled():
            break
        dec_btn.click()

    sign_btn = page.locator("#combine-dialog-body button.combine-sign")
    sign_btn.wait_for(timeout=30_000)
    page.wait_for_function(
        "!document.querySelector('#combine-dialog-body button.combine-sign')?.disabled", timeout=30_000
    )
    # Read the rendered survivor code, not module state (also unreadable -- see
    # _find_real_combine_candidate's docstring) -- this is the real
    # api/combine/preview response, rendered.
    survivor_uid = page.locator("#combine-dialog-body .combine-copy-row.is-survives .combine-copy-id").get_attribute("data-uid")
    assert survivor_uid, "a real /api/combine/preview call must have returned a predicted survivor"
    destroyed_uids = page.eval_on_selector_all(
        "#combine-dialog-body .combine-copy-row.is-destroyed .combine-copy-id",
        "nodes => nodes.map(n => n.dataset.uid)",
    )
    assert destroyed_uids, "a real preview with >=2 selected copies must destroy at least one"
    destroyed_rows = page.locator("#combine-dialog-body .combine-copy-row.is-destroyed")
    for index in range(destroyed_rows.count()):
        text = destroyed_rows.nth(index).inner_text()
        assert " CC · Lv " in text, "every destroyed Card ID must show its CC and level"
    assert page.locator("#combine-dialog-body details").count() == 0

    page.screenshot(path=str(tmp_path / "01-dialog-ready-to-sign.png"))

    bumped = _install_settle_mocks(page, survivor_uid, destroyed_uids)

    sign_btn.click()
    page.wait_for_function("window.__jointKeychain.callback === true", timeout=30_000)

    # Arrive: the confirmed verdict renders the arrival state in the SAME modal.
    page.locator(".combine-arrival").wait_for(timeout=30_000)
    assert "Combine confirmed" in page.locator("#combine-dialog-title").inner_text()
    page.screenshot(path=str(tmp_path / "02-arrival-state.png"))

    # Settle: the "Updating board..." toast from the FIRST /api/capture/status
    # tick ('running') must appear -- this is the real poll loop firing, not a
    # stubbed instantaneous success.
    page.wait_for_function(
        "document.querySelector('#ceres-stage-toast')?.textContent?.includes('Updating board after combine')",
        timeout=30_000,
    )
    page.screenshot(path=str(tmp_path / "03-settle-updating-toast.png"))

    # Settle completes: board reloads (loadBoard() picks up the mocked
    # post-combine /api/board), toast confirms, and the confirmation surface
    # never needed a manual Refresh click.
    page.wait_for_function(
        "document.querySelector('#ceres-stage-toast')?.textContent?.includes('Board updated with the combined card')",
        timeout=30_000,
    )
    page.screenshot(path=str(tmp_path / "04-settle-done-toast.png"))

    # The combined card's new value is visible on re-opening the SAME combine
    # door, with no manual refresh anywhere: loadBoard() (triggered by settle
    # not by the player) nulled IDLE_CARDS_CACHE and re-fetched it, so the
    # dialog's own "you own N" count reflects the destroyed copy being gone and
    # the survivor's bumped CC -- this is the row a real combine actually
    # changes (a Staked card can never be a combine input, the internal rules), read
    # through the real render path, not a raw JSON diff.
    page.locator("#combine-dialog-body button[data-combine-action='close-dialog']").click()
    overlay.wait_for(state="hidden", timeout=30_000)
    combine_btn.click()
    chooser = page.locator(".combine-plot-chooser .picker-chip")
    if chooser.count():
        chooser.first.click()
    ledger_now = page.locator("#combine-dialog-body .combine-ledger-sm").first
    ledger_now.wait_for(timeout=30_000)
    page.wait_for_function(
        "text => document.querySelector('#combine-dialog-body .combine-ledger-sm')?.textContent?.includes(text)",
        arg=f"{bumped['bcx']} CC",
        timeout=30_000,
    )
    page.screenshot(path=str(tmp_path / "05-reopened-dialog-shows-settled-cc.png"))

    page.close()
