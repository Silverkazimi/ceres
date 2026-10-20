"""Overflow guards across effective viewport and root-font conditions ().

's column-header follow-up used to leave 1536/1280 px and a 20px root
font as strict xfails. sizes the table by CSS with fluid type and moves
the card-view switch to em (board width x 16 / root font <= 1279), so every
case now has a definite expectation and none is an xfail: in the table view
nothing may pass the window edge, scroll sideways or overflow the clipping
table scroller -- with the board, an opened plot, the picker and the checkout;
in the card view (1536 and 1366 at a 20px root) the cards must fit.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres


GUTTER = 15


def _table_view(width: int, root_font: int) -> bool:
    return (width - GUTTER) * 16 / root_font > 1279


CASES = [(w, f) for f in (16, 20) for w in (1920, 1745, 1536, 1366)]
TABLE_CASES = [pytest.param(w, f, id=f"{w}px-{f}px") for w, f in CASES if _table_view(w, f)]
CARD_CASES = [pytest.param(w, f, id=f"{w}px-{f}px") for w, f in CASES if not _table_view(w, f)]


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _audit(page: Page) -> dict:
    return page.evaluate(
        """() => {
 const clientWidth = document.documentElement.clientWidth;
 const over = [];
 const scrollers = [];
 const vendored = (el) => !!el.closest('.theme-picker, .theme-picker-dock');
 document.querySelectorAll('*').forEach((el) => {
 if (vendored(el)) return;
 const r = el.getBoundingClientRect();
 if (r.width > 0 && Math.round(r.right) > clientWidth + 1) {
 over.push({ cls: (el.className || el.tagName).toString().slice(0, 60),
 right: Math.round(r.right), width: Math.round(r.width) });
 }
 if (el.scrollWidth > el.clientWidth + 1) {
 const ov = getComputedStyle(el).overflowX;
 if (ov === 'auto' || ov === 'scroll') {
 scrollers.push({ cls: (el.className || el.tagName).toString().slice(0, 60),
 scrollWidth: el.scrollWidth, clientWidth: el.clientWidth });
 }
 }
 });
 return { clientWidth, pageScrollWidth: document.documentElement.scrollWidth,
 over: over.slice(0, 8), scrollers: scrollers.slice(0, 8),
 tableOverflow: (() => { const s = document.querySelector('#plot-table-scroll');
 return s && getComputedStyle(s).display !== 'none' ? s.scrollWidth - s.clientWidth : 0; })() };
 }"""
    )


def _assert_fits(page: Page, state: str) -> None:
    audit = _audit(page)
    assert audit["pageScrollWidth"] <= audit["clientWidth"], f"{state}: page is {audit}"
    assert not audit["over"], f"{state}: elements past client width -> {audit['over']}"
    assert not audit["scrollers"], f"{state}: horizontal scrollers -> {audit['scrollers']}"
    assert audit["tableOverflow"] <= 1, f"{state}: the table is wider than its clipping scroller -> {audit}"


def _board(live_browser: tuple[Browser, str], width: int, root_font: int) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": width, "height": 1080})
    page.add_init_script(
        f"""document.addEventListener('DOMContentLoaded', () => {{
 document.documentElement.style.setProperty('font-size', '{root_font}px', 'important');
 const actual = getComputedStyle(document.documentElement).fontSize;
 if (actual !== '{root_font}px') throw new Error(`root font self-check: expected {root_font}px, got ${{actual}}`);
 window.dispatchEvent(new Event('resize'));
 }})"""
    )
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    page.wait_for_function(
        "document.querySelector('#plot-tbody > tr.plot-row') !== null"
        " || document.querySelector('#plot-card-list .plot-card') !== null", timeout=30_000
    )
    page.wait_for_timeout(500)
    assert page.evaluate("() => getComputedStyle(document.documentElement).fontSize") == f"{root_font}px"
    return page


@pytest.mark.parametrize("width,root_font", TABLE_CASES)
def test_widest_states_never_exceed_full_hd(
    live_browser: tuple[Browser, str], width: int, root_font: int,
) -> None:
    """Injection: a wide slot editor or picker must be reported past clientWidth."""
    page = _board(live_browser, width, root_font)
    _assert_fits(page, "board")
    page.evaluate(
        """() => {
 const rows = [...document.querySelectorAll('#plot-tbody > tr.plot-row')];
 (rows.find(r => r.textContent.includes('5/5')) || rows.find(r => r.textContent.includes('/5')) || rows[0]).querySelector('td').click(); // a plot opens from its cells
 }"""
    )
    page.wait_for_selector(".editor-band", timeout=30_000)  # the facts line is gone; the opened plot is the band
    _assert_fits(page, "plot expanded")
    opened = page.evaluate(
        """() => {
 const tile = document.querySelector('.slot-editor-body .slot-tile.is-empty') ||
 document.querySelector('.slot-editor-body .slot-tile');
 if (!tile) return false;
 tile.click();
 return true;
 }"""
    )
    if opened:
        page.wait_for_timeout(2000)
        _assert_fits(page, "picker open")
    page.close()


@pytest.mark.parametrize("width,root_font", TABLE_CASES)
def test_every_plot_row_fits_full_hd(
    live_browser: tuple[Browser, str], width: int, root_font: int,
) -> None:
    """A single long plot name or badge run must not widen the table."""
    page = _board(live_browser, width, root_font)
    widest = page.evaluate(
        """() => {
 const clientWidth = document.documentElement.clientWidth;
 let right = 0, which = '';
 document.querySelectorAll('#plot-tbody > tr.plot-row').forEach((row) => {
 const edge = Math.round(row.getBoundingClientRect().right);
 if (edge > right) { right = edge; which = row.textContent.trim().slice(0, 40); }
 });
 return {right, clientWidth, which};
 }"""
    )
    assert widest["right"] <= widest["clientWidth"] + 1, widest
    page.close()


@pytest.mark.parametrize("width,root_font", TABLE_CASES)
def test_checkout_open_fits_full_hd(
    live_browser: tuple[Browser, str], width: int, root_font: int,
) -> None:
    page = _board(live_browser, width, root_font)
    page.evaluate(
        """() => {
 const rows = [...document.querySelectorAll('#plot-tbody > tr.plot-row')];
 (rows.find(r => r.textContent.includes('/5')) || rows[0]).querySelector('td').click(); // a plot opens from its cells
 }"""
    )
    page.wait_for_selector(".editor-band", timeout=30_000)  # the facts line is gone; the opened plot is the band
    staged = page.evaluate(
        """() => {
 const action = document.querySelector('.slot-editor-body .slot-tile:not(.is-empty) [data-slot-action]');
 if (!action) return false;
 action.click();
 return true;
 }"""
    )
    if not staged:
        pytest.skip("no stageable occupied tile on the opened plot")
    page.wait_for_timeout(1500)
    page.evaluate("() => document.querySelector('[data-checkout-action=\"toggle\"]')?.click()")
    page.wait_for_timeout(1500)
    _assert_fits(page, "checkout open with a staged change")
    page.close()


@pytest.mark.parametrize("width,root_font", CARD_CASES)
def test_card_view_cases_fit(
    live_browser: tuple[Browser, str], width: int, root_font: int,
) -> None:
    """A larger text size that no longer fits the table moves to the card view
 (em switch) -- and the cards fit the window with no side scrolling."""
    page = _board(live_browser, width, root_font)
    got = page.evaluate(
        """() => ({phone: document.documentElement.classList.contains('ceres-lw-le-1279'),
 cards: document.querySelectorAll('#plot-card-list .plot-card').length})"""
    )
    assert got["phone"] and got["cards"] > 0, got
    _assert_fits(page, "card view")
    page.close()
