""" (A-77): left a dangling `${undo}` in harvestBarHtml's quiet/no-project branch,
so every uncleared plot threw "undo is not defined" and the editor showed "Could not load workers".
This guard renders the REAL harvestBarHtml (real module, real browser) for every row of the live
board plus synthetic rows for each branch, and opens the plot the maintainer reported (065-07-085).
Network is frozen before rendering so the per-plot refreshes it schedules cost zero calls ()."""
from __future__ import annotations

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres
from . import board_basic_recording as recording

REPORTED_PLOT = recording.NEVER_BUILT.label


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _board(page, base_url: str) -> None:
    page.goto(f"{base_url}/?player=sample01", timeout=15_000)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length >= 1", timeout=60_000)


def test_harvest_bar_renders_for_every_board_row_and_each_branch(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1536, "height": 945})
    _board(page, base_url)
    result = page.evaluate(
        """async () => {
 const hs = await import('/static/harvest-state.js');
 const table = await import('/static/table.js');
 window.fetch = () => new Promise(() => {});
 const synthetic = [
 {deed_uid: 'ir8334-no-project', harvest: {state: 'no_project'}, is_powered: false},
 {deed_uid: 'ir8334-no-project-powered', harvest: {state: 'no_project'}, is_powered: true},
 {deed_uid: 'ir8334-pending', harvest: null},
 {deed_uid: 'ir8334-tax', resource: 'TAX', harvest: {state: 'tax_collection'}},
 ];
 const rows = [...table.CURRENT_ROWS, ...synthetic];
 const errors = [];
 const states = new Set();
 for (const row of rows) {
 for (const cog of [false, true]) {
 try {
 const html = hs.harvestBarHtml(row, {cog});
 const m = /class="harvest-bar([^"]*)"/.exec(html || '');
 states.add(m ? m[1].trim() || 'normal' : (html ? 'other' : 'empty'));
 } catch (error) {
 errors.push(`${row.deed_uid}: ${error && error.message}`);
 }
 }
 }
 return {count: rows.length, errors, states: [...states]};
 }"""
    )
    page.close()
    assert result["count"] > 4, result
    assert result["errors"] == [], result
    assert any("is-quiet" in state for state in result["states"]), result


def test_reported_uncleared_plot_opens(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1536, "height": 945})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _board(page, base_url)
    row = page.locator("#plot-tbody > tr.plot-row", has_text=REPORTED_PLOT).first
    if row.count() == 0:
        pytest.skip(f"{REPORTED_PLOT} is not on the live board any more")
    uid = row.get_attribute("data-deed-uid")
    row.click(timeout=10_000)
    page.wait_for_function(
        "uid => document.querySelector(`tr.expand-row[data-deed-uid=\"${uid}\"] .slot-editor, tr.expand-row[data-deed-uid=\"${uid}\"] .harvest-bar`) !== null",
        arg=uid,
        timeout=60_000,
    )
    text = page.locator(f'tr.expand-row[data-deed-uid="{uid}"]').inner_text()
    page.close()
    assert "Could not load workers" not in text
    assert not [e for e in errors if "is not defined" in e], errors
