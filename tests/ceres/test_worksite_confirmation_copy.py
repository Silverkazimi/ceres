""" Phase 0 -- live copy guards for the worksite confirmation surface.

A worksite change is a 24-hour build, not an instant flip. These tests drive the
current Chromium modal through a mocked preview, Keychain response, and domain
verification response, then assert the copy a player can actually read.
"""

from __future__ import annotations

import json
import urllib.request

import pytest
from playwright.sync_api import Browser, Page

from .conftest import settle, spawn_live_ceres, wait_for_board


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def _worksite_api_route(
    route,
    request,
    selected_label: dict[str, str | None],
    deed_uid: dict[str, str | None],
    fresh_row: dict[str, dict | None],
) -> None:
    path = request.url.split("?", 1)[0]
    if request.method == "POST" and path.endswith("/worksite-preview"):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "account": "player0005",
                "key_tier": "Posting",
                "chunks": [{"operations": [["custom_json", {"id": "sm_land_operation", "json": "{}"}]]}],
                "notices": [],
                "time_crystals": False,
            }),
        )
        return
    if request.method == "GET" and path.rstrip("/").split("/")[-1] == (deed_uid["value"] or ""):
        updated = dict(fresh_row["value"] or {})
        updated["deed_uid"] = deed_uid["value"]
        updated["pending_change"] = {
            "to_label": selected_label["value"],
            "ready_at": "2099-01-02T03:04:05+00:00",
            "stalled": False,
        }
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(updated),
        )
        return
    route.continue_()


def _confirmed_worksite(live_browser: tuple[Browser, str]) -> tuple[Page, object, str]:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    selected_label: dict[str, str | None] = {"value": None}
    deed_uid: dict[str, str | None] = {"value": None}
    fresh_row: dict[str, dict | None] = {"value": None}
    page.route(
        "**/api/plots/**",
        lambda route, request: _worksite_api_route(route, request, selected_label, deed_uid, fresh_row),
    )
    page.route(
        "**/api/op-verify*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"state": "domain-confirmed", "refreshed_deeds": [deed_uid["value"]]}),
        ),
    )
    page.add_init_script(
        """window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
 callback({success: true, result: {id: 'FAKE-WORKSITE-TX'}});
 }};"""
    )
    page.goto(base_url)
    wait_for_board(page)

    rows = page.locator("#plot-tbody > tr.plot-row")
    modal = page.locator("#worksite-modal-overlay")
    for index in range(rows.count()):
        row = rows.nth(index)
        if row.locator("xpath=following-sibling::tr[1]").evaluate("node => node.style.display") == "none":
            row.click()
        button = page.locator("tr.expand-row").nth(index).locator("button.worksite-change-btn")
        if button.count() == 0:
            continue
        if not button.is_visible():
            continue
        button.click()
        modal.wait_for(state="visible", timeout=5_000)
        options = modal.locator(".worksite-tile-option")
        if options.count() == 0:
            modal.locator("[data-worksite-action='close']").first.click()
            continue
        deed_uid["value"] = modal.get_attribute("data-deed-uid")
        art_style = options.first.locator(".worksite-tile-art").get_attribute("style") or ""
        assert "productionBuildingSquare/" in art_style
        # app.js is loaded as an ES module (`<script type="module">`), so its
        # top-level `let BOARD` is module-scoped and unreachable from
        # page.evaluate() -- fetch the same board data the page itself
        # rendered from, straight from the API the page called.
        with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
            board = json.loads(response.read())
        fresh_row["value"] = next(
            (plot for plot in board.get("plots", []) if plot.get("deed_uid") == deed_uid["value"]), None
        )
        option = options.first
        selected_label["value"] = option.locator(".worksite-tile-name").inner_text()
        option.click()
        modal.locator(".worksite-modal-confirm").click()
        modal.locator("button[data-worksite-action='broadcast']").wait_for(timeout=30_000)
        assert modal.locator("details.op-review-ops").count() == 1
        assert modal.locator("details.op-review-ops").evaluate("element => element.open") is False
        modal.locator("button[data-worksite-action='broadcast']").click()
        verdict = modal.locator("[data-worksite-verdict]")
        verdict.wait_for(state="visible", timeout=30_000)
        assert page.locator("#worksite-banner").count() == 0
        assert modal.locator("[data-worksite-verdict]").count() == 1
        return page, modal, selected_label["value"] or ""
    pytest.fail("no live worksite-change button offered a selectable worksite")


def test_worksite_confirmation_copy_has_no_forbidden_phrases(live_browser) -> None:
    """Injected defect: render `Worksite changed` in the live verdict; expected red assertion: the verdict text contains the forbidden phrase."""
    page, modal, _ = _confirmed_worksite(live_browser)
    text = modal.inner_text()
    for phrase in ("Worksite changed", "Quarry active"):
        assert phrase not in text, f"forbidden confirmation phrase found in rendered copy: {phrase!r}"
    page.close()


def test_worksite_confirmation_copy_never_a_bare_success_or_done(live_browser) -> None:
    """Injected defect: set the live verdict title to bare `Done`; expected red assertion: its title is not one of the forbidden bare labels."""
    page, modal, _ = _confirmed_worksite(live_browser)
    for element in (modal.locator(".worksite-verdict-title"), modal.locator(".worksite-verdict-detail")):
        assert element.inner_text() not in {"Success", "Done"}
    page.close()


def test_worksite_confirmation_no_longer_auto_closes_the_modal(live_browser) -> None:
    """Injected defect: restore the 1.2-second modal close timer; expected red assertion: the verdict modal is no longer visible after the timer."""
    page, modal, _ = _confirmed_worksite(live_browser)
    settle(page)
    assert modal.is_visible()
    assert modal.locator("[data-worksite-verdict]").is_visible()
    page.close()


def test_worksite_confirmed_copy_names_the_build_not_a_finished_state(live_browser) -> None:
    """Injected defect: replace the live detail with `Worksite changed`; expected red assertion: the rendered detail no longer names the building work in progress."""
    page, modal, selected_label = _confirmed_worksite(live_browser)
    detail = modal.locator(".worksite-verdict-detail").inner_text()
    assert "is now building" in detail
    assert selected_label in detail
    assert "active" not in detail.lower()
    page.close()
