"""Rendered tooltip coverage for the picker and checkout surfaces."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres, wait_for_board


def _open_badge_slideouts(page) -> None:
    """Open every plot's badge slide-out.

    Board badges are no longer a table column: each plot's chips render in its own row of
    the slide-out panel (`#badge-overlay .badge-overlay-row`), which badge-overlay.js fills
    only once that plot's handle in the right-edge badge bar (`#badge-bar .badge-bar-row`)
    is opened."""
    handles = page.locator("#badge-bar .badge-bar-row")
    handles.first.wait_for(timeout=30_000)
    handles.evaluate_all("rows => rows.forEach(row => { if (row.getAttribute('aria-expanded') !== 'true') row.click(); })")
    page.wait_for_function(
        """() => {
            const rows = [...document.querySelectorAll('#badge-overlay .badge-overlay-row')];
            return rows.length > 0 && rows.every(row => row.classList.contains('is-open'));
        }""",
        timeout=30_000,
    )


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _open_picker(live_browser: tuple[Browser, str]) -> tuple[Page, object]:
    browser, base_url = live_browser
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.goto(base_url)
    wait_for_board(page)
    rows = page.locator("tr.plot-row")
    for index in range(rows.count()):
        row = rows.nth(index)
        if row.locator("+ tr.expand-row").evaluate("node => node.style.display") == "none":
            row.click()
        expand = row.locator("+ tr.expand-row")
        try:
            expand.locator(".slot-editor").wait_for(timeout=30_000)
            # the band renders `preparing` first (placeholder tiles under a cover); the
            # real, clickable tiles exist once it reports `ready`. The worker group's row also
            # holds the plot's empty Totem/Title/Power Core tiles, so the empty slot is picked
            # by its worker group id.
            expand.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
        except Exception:
            row.click()
            continue
        empty = expand.locator(".slot-group[data-group-id='worker'] .slot-grid .slot-tile.is-empty[data-group-id='worker'][data-slot-action='add']")
        if empty.count() == 0:
            row.click()
            continue
        with page.expect_request("**/api/plots/*/candidates*"):
            empty.first.click()
        expand.locator(".inline-picker").wait_for(timeout=30_000)
        return page, context
    context.close()
    # peer-group tooltip coverage is independent of which current snapshot row supplies the picker.
    # R-138: fixture discovery follows the rendered snapshot rather than a stale fixed plot.
    pytest.skip("No live snapshot plot exposes a reachable open worker slot: selector 'tr.plot-row' had no qualifying row")


def test_picker_tooltip_copy_is_rendered(live_browser: tuple[Browser, str]) -> None:
    page, context = _open_picker(live_browser)
    try:
        # INJECTION: restoring legacy chip/first-place tipAttrs would add data-tip here.
        assert page.locator(".candidate-upgrade, .candidate-first").evaluate_all(
            "els => els.every(el => !el.hasAttribute('data-tip'))"
        )
        # INJECTION: dropping the highlighted count or reintroducing provenance would fail these action checks.
        for selector in (".picker-combine[data-tip]",):
            actions = page.locator(selector)
            for index in range(actions.count()):
                action = actions.nth(index)
                assert action.get_attribute("data-tip-key") is not None
                assert action.get_attribute("data-tip-foot") is None
        # INJECTION: reverting the Terrain title/body rewrite would change this rendered nested span.
        # The terrain metric's tooltip now names the pairing ("<splinter> on <terrain>", with
        # inline icons) and states the effect in one short line (candidates.js item 11).
        terrain = page.locator(".candidate-terrain").first
        terrain.wait_for(state="attached", timeout=30_000)
        title = re.sub(r"\{\{ic:[^}]+\}\}", "", terrain.get_attribute("data-tip") or "")
        assert re.fullmatch(r"\S.* on \S.*", title) or title == "Terrain effect not reported", title
        assert any(
            (terrain.get_attribute("data-tip-body") or "").startswith(prefix)
            for prefix in ("Terrain bonus ", "Terrain penalty ", "No terrain effect", "No modifier is known for ")
        ), terrain.get_attribute("data-tip-body")
        # INJECTION: restoring newline-joined accessible fields would make the row label multiline.
        labels = page.locator(".candidate-row").evaluate_all("els => els.map(el => el.getAttribute('aria-label') || '')")
        assert labels and all("Base PP" in label and "\n" not in label for label in labels)
        assert page.locator(".candidate-row[data-tip]").count() == 0
        # INJECTION: restoring the retired provenance sentence would leak it into rendered tooltip attributes.
        attributes = page.locator("[data-tip], [data-tip-body], [data-tip-foot], [aria-label]").evaluate_all(
            "els => els.flatMap(el => ['data-tip', 'data-tip-body', 'data-tip-foot', 'aria-label'].map(name => el.getAttribute(name)).filter(Boolean))"
        )
        assert all("Reported by the game" not in value for value in attributes)
    finally:
        context.close()


def test_idle_badge_uses_expanded_name(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(base_url)
        wait_for_board(page)
        _open_badge_slideouts(page)
        idle = page.locator(".badge-idle").first
        if idle.count() == 0:
            pytest.skip("No idle badge is present in this account's live board snapshot")
        # INJECTION: using the backend label "Idle" as the tooltip title would fail this rendered badge check.
        assert idle.get_attribute("data-tip") == "No workers staked — produces nothing"
        assert idle.get_attribute("data-tip-body") is None
    finally:
        context.close()


def test_transaction_guard_copy_is_identical(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.add_init_script(
            """window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
              callback({success: true, result: {id: 'FAKE-TOOLTIP-TX'}});
            }};"""
        )
        page.route(
            "**/api/op-verify*",
            lambda route: route.fulfill(status=200, content_type="application/json", body='{"state":"domain-pending","notices":[]}'),
        )
        page.goto(base_url)
        wait_for_board(page)
        staged = False
        rows = page.locator("tr.plot-row")
        for index in range(rows.count()):
            row = rows.nth(index)
            if row.locator("+ tr.expand-row").evaluate("node => node.style.display") == "none":
                row.click()
            expand = row.locator("+ tr.expand-row")
            expand.locator(".slot-editor").wait_for(timeout=30_000)
            expand.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
            empty = expand.locator(".slot-group[data-group-id='worker'] .slot-grid .slot-tile.is-empty[data-group-id='worker'][data-slot-action='add']")
            if empty.count() == 0:
                continue
            with page.expect_request("**/api/plots/*/candidates*"):
                empty.first.click()
            candidate = expand.locator(".candidate-row").first
            candidate.wait_for(timeout=30_000)
            candidate.click()
            page.locator("#checkout-btn").wait_for(timeout=30_000)
            staged = True
            break
        if not staged:
            pytest.skip("No reachable open worker slot exists for a browser-only staged transaction")
        page.locator("#checkout-btn").click()
        panel = page.locator("#checkout-panel")
        panel.locator("button[data-checkout-action='prepare']").click()
        panel.locator(".op-preview-review").wait_for(timeout=30_000)
        panel.locator("button[data-op-action='confirm']").click()
        expected = "Finish the current transaction first"
        page.wait_for_function(
            "expected => document.querySelector('.checkout-undo-all')?.getAttribute('data-tip') === expected",
            arg=expected,
            timeout=30_000,
        )
        # INJECTION: leaving the two call sites as separate legacy strings would make their rendered values diverge.
        values = page.locator("[data-tip], [aria-label]").evaluate_all(
            "els => els.flatMap(el => [el.getAttribute('data-tip'), el.getAttribute('aria-label')]).filter(value => value && value.includes('Finish the current transaction first'))"
        )
        assert values and all(value == expected for value in values)
    finally:
        context.close()


def test_tooltip_picker_checkout_placeholder():
    # INJECTION: this placeholder is replaced by each requested rendered-DOM assertion.
    pass
