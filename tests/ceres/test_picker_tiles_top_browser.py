""": in a real Chromium, offline on the recorded `board-basic` set, opening a plot's
worker picker docks the plot's slot tiles (highest painted point, Power Core sockets included) on the line under the
sticky header -- on the desktop board at 1536x864, and in the phone plot sheet (its own scroller, under the sheet head)
at 390x844. Opening the picker again on the already-docked plot moves nothing, and closing it puts the view back where
the user was. Nothing is staged or signed. CI only (public check/* run); do not run on the laptop:
flock -w 900 /tmp/er-browser.lock nice -n 15 python3 -m pytest -q tests/ceres/test_picker_tiles_top_browser.py"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from .conftest import settle
from .test_picker_scroll_browser import PLAYER, SET, _recorded_plots, _settle

pytestmark = [pytest.mark.browser, pytest.mark.skipif(not (SET / "fixture-manifest.json").is_file(), reason="board-basic set not recorded")]

TOLERANCE = 3  # px

MEASURE = r"""(uid) => {
  const phone = !!document.querySelector('#mobile-plot-sheet:not([hidden])');
  const host = phone ? document.getElementById('mobile-plot-sheet-body') : document.querySelector(`tr.expand-row[data-deed-uid="${CSS.escape(uid)}"]`);
  let tiles = null;
  for (const n of host.querySelectorAll('.editor-band .slot-tile, .editor-band .power-satellite')) {
    const r = n.getBoundingClientRect();
    if (r.height > 0 && r.width > 0 && (tiles === null || r.top < tiles)) tiles = r.top;
  }
  const line = phone ? host.getBoundingClientRect().top
    : Math.max(0, ...['header.board-header', '#table-head-sticky'].map((s) => document.querySelector(s)?.getBoundingClientRect().bottom || 0));
  return { phone, tiles, line, scroll: phone ? host.scrollTop : scrollY, picker: !!host.querySelector('.inline-picker[data-picker="1"]'), pad: phone ? host.style.paddingBottom : document.body.style.minHeight };
}"""


def _tile_selector(uid: str, phone: bool) -> str:
    scope = "#mobile-plot-sheet-body" if phone else f'tr.expand-row[data-deed-uid="{uid}"]'
    return (f'{scope} [data-group-id="worker"][data-slot-action="add"], {scope} [data-group-id="worker"][data-slot-action="replace"]')


def _run(width: int, height: int) -> dict:
    playwright = pytest.importorskip("playwright.sync_api")
    from tests.ceres.fixture_live import install_routes, spawn_fixture_ceres

    phone = width < 700
    with spawn_fixture_ceres(SET) as base, playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            aborted = install_routes(page, SET)
            page.route("**/api/plots/*/candidates*", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"candidates": [], "total": 0})))
            page.goto(f"{base}/?player={PLAYER}", timeout=15_000)
            page.wait_for_function("() => window.__ceresReady && window.__ceresReady.ready === true", timeout=120_000)
            recorded = _recorded_plots()
            # desktop lists plots as tr.plot-row; the phone board lists them as article.plot-card (no table rows at all)
            row_sel = "#plot-card-list .plot-card[data-deed-uid]" if phone else "#plot-tbody > tr.plot-row"
            if phone:
                listed = page.eval_on_selector_all(row_sel, "els => els.map((e) => e.dataset.deedUid)")
                uids = [u for u in listed if u in recorded]
            else:  # desktop keeps the filled-slot cell filter of the test
                listed = page.eval_on_selector_all(row_sel, "els => els.map((e) => e.dataset.deedUid)")
                uids = [u for u in page.eval_on_selector_all(row_sel, "els => els.filter((e) => [...e.children].some((td) => "
                                                             "/^\\s*\\d+\\/[1-9]\\d*(\\+R)?\\s*$/.test(td.textContent))).map((e) => e.dataset.deedUid)")
                        if u in recorded]
            facts = page.evaluate("""() => ({rows: document.querySelectorAll('#plot-tbody > tr.plot-row').length,
              cards: document.querySelectorAll('#plot-card-list .plot-card').length, ready: !!window.__ceresReady})""")
            assert len(uids) >= 3, {"selector": row_sel, "page": facts, "listed": len(listed), "recorded": len(recorded), "usable": uids}
            uid = uids[len(uids) // 2]
            row = page.locator(f'{row_sel}[data-deed-uid="{uid}"]' if phone else f'tr.plot-row[data-deed-uid="{uid}"]')
            row.scroll_into_view_if_needed()
            row.click()
            tile = _tile_selector(uid, phone)
            try:
                page.wait_for_selector(tile, state="attached", timeout=60_000)
            except Exception as exc:  # name what the page does hold, not just the timeout
                dom = page.evaluate("""() => ({sheetHidden: document.querySelector('#mobile-plot-sheet')?.hidden ?? null,
                  bands: document.querySelectorAll('.editor-band').length, tiles: document.querySelectorAll('.slot-tile').length,
                  workerSlots: [...document.querySelectorAll('[data-group-id="worker"]')].map((n) => n.dataset.slotAction),
                  bodyHtml: (document.getElementById('mobile-plot-sheet-body')?.innerHTML || '').slice(0, 300)})""")
                raise AssertionError(f"no worker tile for {uid} with {tile!r}: {dom}") from exc
            _settle(page)
            before = page.evaluate(MEASURE, uid)
            page.evaluate("(sel) => document.querySelector(sel).click()", tile)
            page.wait_for_selector('.inline-picker[data-picker="1"]', state="attached", timeout=30_000)
            _settle(page)
            settle(page)
            opened = page.evaluate(MEASURE, uid)
            # the open tile pressed again closes; pressing it once more re-opens on the plot that was docked before
            page.evaluate("(sel) => document.querySelector(sel).click()", tile)
            page.wait_for_function("!document.querySelector('.inline-picker[data-picker=\"1\"]')", timeout=10_000)
            _settle(page)
            settle(page)
            closed = page.evaluate(MEASURE, uid)
            page.evaluate("(sel) => document.querySelector(sel).click()", tile)
            page.wait_for_selector('.inline-picker[data-picker="1"]', state="attached", timeout=30_000)
            _settle(page)
            settle(page)
            reopened = page.evaluate(MEASURE, uid)
            return {"before": before, "opened": opened, "closed": closed, "reopened": reopened, "errors": errors, "aborted": aborted}
        finally:
            browser.close()


@pytest.mark.parametrize("width,height", [(1536, 864), (390, 844)])
def test_picker_docks_the_slot_tiles_at_the_top(width, height):
    got = _run(width, height)
    assert got["errors"] == [] and got["aborted"] == []
    phone = width < 700
    for name in ("opened", "reopened"):
        m = got[name]
        assert m["phone"] is phone and m["picker"] is True, (name, m)
        assert m["tiles"] is not None, (name, m)
        assert abs(m["tiles"] - m["line"]) <= TOLERANCE, (name, m)  # the tiles' top is on the line under the header
    # re-open on a docked plot does not move it
    assert abs(got["reopened"]["scroll"] - got["opened"]["scroll"]) <= 1, got
    # close puts the view back where the user was
    assert got["closed"]["picker"] is False
    assert abs(got["closed"]["scroll"] - got["before"]["scroll"]) <= 1, got
    if phone:
        assert got["closed"]["pad"] == "", got["closed"]  # the picker's room is let go
