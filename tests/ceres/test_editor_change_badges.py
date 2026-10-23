"""Rendered-behaviour guard for the instant tile-change badges (ceres v0.7.130).

The separate `.editor-facts` line is gone: the plot-facts strip was dropped (the plot row
above shows the same figures) and only the instant staged-change badges survive, right-aligned
on the worker group's controls line (`.slot-group-controls > .editor-change-badges`,
editor.js renderSlotEditor: "badges now render inside the worker group's controls line").
These guards follow the badges to that line.

Mutation-tested: deleting the `renderChangeBadges(...)` call from renderSlotEditor()
makes test_staging_a_tile_change_renders_badges_in_the_facts_row fail on the badge
count assertion. A string-match test could not detect that, which is why this drives
a real browser instead -- the tooltip-arrow bug regressed twice under source-grep
tests that stayed green.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page

from .conftest import settle, spawn_live_ceres


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _expanded_editor(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(base_url)
    # The plot count is not fixed -- it changes whenever a plot is traded, so waiting
    # on a literal count makes this fail on an unrelated trade rather than on a defect.
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0",
        timeout=30_000,
    )
    page.evaluate(
        """() => {
            const rows = [...document.querySelectorAll('#plot-tbody > tr.plot-row')];
            (rows.find(r => r.textContent.includes('/5')) || rows[0]).click();
        }"""
    )
    # The band is rendered first in a `preparing` state; tiles, controls and the footer
    # exist once it reports `ready`.
    page.wait_for_selector(".editor-band[data-plot-state='ready']", timeout=30_000)
    return page


BADGES = ".slot-group[data-group-id='worker'] > .slot-group-controls > .editor-change-badges .editor-change-badge"


def _stage_a_removal(page: Page) -> bool:
    staged = page.evaluate(
        """() => {
            const t = document.querySelector('.slot-editor-body .slot-tile:not(.is-empty) [data-slot-action]');
            if (!t) return false;
            t.click();
            return true;
        }"""
    )
    if staged:
        settle(page)
    return bool(staged)


def test_staging_a_tile_change_renders_badges_in_the_facts_row(
    live_browser: tuple[Browser, str],
) -> None:
    page = _expanded_editor(live_browser)
    assert page.evaluate("document.querySelectorAll('.editor-change-badge').length") == 0

    if not _stage_a_removal(page):
        pytest.skip("no occupied worker tile with a stageable action on the opened plot")

    # Instant: no reload, no refresh call, no broadcast -- the badges must be present
    # on the very next render after the stage mutation.
    badges = page.evaluate(
        "sel => [...document.querySelectorAll(sel)].map(e => e.textContent.trim())", BADGES
    )
    assert badges, "staging a tile change rendered no badge on the worker controls line"
    assert any("Base" in b for b in badges), badges


def test_badges_never_wrap_the_facts_row_or_overflow_full_hd(
    live_browser: tuple[Browser, str],
) -> None:
    page = _expanded_editor(live_browser)
    if not _stage_a_removal(page):
        pytest.skip("no occupied worker tile with a stageable action on the opened plot")

    # The line carrying the badges must stay single-line and fit the browser's content viewport.
    assert page.locator(BADGES).count() > 0, "staging a tile change rendered no badge"
    height = page.evaluate(
        "Math.round(document.querySelector(\".slot-group[data-group-id='worker'] > .slot-group-controls\").getBoundingClientRect().height)"
    )
    assert height < 40, f"badge line wrapped to {height}px"
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_facts_row_stays_above_the_worker_tiles(live_browser: tuple[Browser, str]) -> None:
    """Mutation-tested: moving the footer strip before the tiles or restoring body labels makes this fail."""
    page = _expanded_editor(live_browser)
    order = page.evaluate(
        "[...document.querySelector('.editor-main').children].map(e => e.className.split(' ')[0])"
    )
    assert order.index("slot-editor-footer") > order.index("slot-editor-body"), order
    # The badge line (the worker group's controls line) sits above the worker tiles.
    group_order = page.evaluate(
        "[...document.querySelector(\".slot-editor-body .slot-group[data-group-id='worker']\").children].map(e => e.className.split(' ')[0])"
    )
    assert group_order.index("slot-group-controls") < group_order.index("slot-grid"), group_order
    assert page.evaluate(
        "document.querySelectorAll('.slot-editor-body .slot-tile-cell .slot-tile-pp').length"
    ) == 0, "rejected body-level PP labels are still rendered"
    assert page.evaluate(
        "document.querySelectorAll('.slot-editor-footer .editor-base-pp, .slot-editor-footer .editor-base-pp-strip').length"
    ) == 0, "rejected footer Base PP figures are still rendered"


def test_worker_tiles_carry_no_level_and_no_inline_pp(live_browser: tuple[Browser, str]) -> None:
    """Level is gone entirely; PP moved out of the caption to a label under the tile."""
    page = _expanded_editor(live_browser)
    assert page.evaluate("document.querySelectorAll('.slot-editor-body .slot-pp').length") == 0
    assert page.evaluate(
        "[...document.querySelectorAll('.slot-editor-body .slot-meta')]"
        ".filter(e => /Lv\\d/.test(e.textContent)).length"
    ) == 0
