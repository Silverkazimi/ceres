""" in a real Chromium,
offline on the recorded `board-basic` set: opening a plot's worker picker brings the plot's row to the line under the
sticky header (within 1 px) -- also for the LAST plot, whose page is only tall enough because the picker reserves the
room (the candidate answer is empty here, so no card list makes the page taller) -- and closing it (the picker's Close,
or the open tile pressed again) scrolls back to where the user was and lets the reserved height go. Nothing is staged
or signed: the test only opens and closes pickers. Run under the shared browser lock:
flock -w 900 /tmp/er-browser.lock nice -n 15 python3 -m pytest -q tests/ceres/test_picker_scroll_browser.py"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SET = Path(__file__).resolve().parents[1] / "fixtures" / "upstream" / "board-basic"
PLAYER = "sample01"

pytestmark = [pytest.mark.browser, pytest.mark.skipif(not (SET / "fixture-manifest.json").is_file(), reason="board-basic set not recorded")]

MEASURE = r"""(uid) => {
 const row = document.querySelector(`tr.plot-row[data-deed-uid="${CSS.escape(uid)}"]`);
 const line = Math.max(0, ...['header.board-header', '#table-head-sticky'].map((s) => document.querySelector(s)?.getBoundingClientRect().bottom || 0));
 const flow = [...document.body.children].filter((e) => !['fixed', 'sticky', 'absolute'].includes(getComputedStyle(e).position) && e.getBoundingClientRect().height > 0);
 return { scrollY, line, vh: innerHeight, contentBottom: Math.max(0, ...flow.map((e) => e.getBoundingClientRect().bottom)) + scrollY, rowTop: row ? row.getBoundingClientRect().top : null, minH: document.body.style.minHeight,
 picker: !!document.querySelector('.inline-picker[data-picker="1"]'),
 maxScroll: document.documentElement.scrollHeight - innerHeight };
}"""
TILE = ('tr.expand-row[data-deed-uid="{uid}"] [data-group-id="worker"][data-slot-action="add"], '
        'tr.expand-row[data-deed-uid="{uid}"] [data-group-id="worker"][data-slot-action="replace"]')


def _recorded_plots() -> set[str]:
    """Plots whose staked assets the set recorded (the others answer "could not fetch plot assets" offline)."""
    found = set()
    for path in SET.rglob("*.json"):
        if "overlays" in path.parts:
            continue
        try:
            request = json.loads(path.read_text(encoding="utf-8")).get("request") or {}
        except (ValueError, AttributeError):
            continue
        parts = str(request.get("path") or "").split("/")
        if parts[:4] == ["", "land", "stake", "deeds"] and parts[-1] == "assets":
            found.add(parts[4])
    return found


def _settle(page) -> None:
    """Wait until the window scroll has not moved for 250 ms (the 300 ms glides are over)."""
    page.wait_for_function("""() => new Promise((done) => { let last = scrollY, still = 0;
 const tick = () => { if (Math.abs(scrollY - last) < 0.5) still += 50; else { still = 0; last = scrollY; }
 if (still >= 250) done(true); else setTimeout(tick, 50); }; tick(); })""", timeout=10_000)


def _open_plot(page, uid: str) -> None:
    row = page.locator(f'tr.plot-row[data-deed-uid="{uid}"]')
    row.scroll_into_view_if_needed()
    row.click()
    page.wait_for_selector(TILE.format(uid=uid), state="attached", timeout=60_000)
    _settle(page)


def _cycle(page, uid: str, park: str, close: str) -> dict:
    """Park the view (`park` js returning a scroll target), open the worker picker, measure, close, measure."""
    page.evaluate(f"window.scrollTo({{ top: ({park}), behavior: 'instant' }})")
    _settle(page)
    before = page.evaluate(MEASURE, uid)
    page.evaluate("(sel) => document.querySelector(sel).click()", TILE.format(uid=uid))
    page.wait_for_selector('.inline-picker[data-picker="1"]', state="attached", timeout=30_000)
    _settle(page)
    opened = page.evaluate(MEASURE, uid)
    if close == "button":
        page.evaluate("document.querySelector('.inline-picker [data-picker-action=\"close\"]').click()")
    else:  # the open tile pressed again ()
        page.evaluate("(sel) => document.querySelector(sel).click()", TILE.format(uid=uid))
    page.wait_for_function("!document.querySelector('.inline-picker[data-picker=\"1\"]')", timeout=10_000)
    _settle(page)
    page.wait_for_timeout(100)
    closed = page.evaluate(MEASURE, uid)
    return {"before": before, "opened": opened, "closed": closed}


def _run(width: int, height: int, motion: str) -> dict:
    playwright = pytest.importorskip("playwright.sync_api")
    from tests.ceres.fixture_live import install_routes, spawn_fixture_ceres

    with spawn_fixture_ceres(SET) as base, playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion=motion)
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            aborted = install_routes(page, SET)
            # an empty candidate answer: no card list makes the page taller, so only the picker's reserve can
            page.route("**/api/plots/*/candidates*", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"candidates": [], "total": 0})))
            page.goto(f"{base}/?player={PLAYER}", timeout=15_000)
            page.wait_for_function("() => window.__ceresReady && window.__ceresReady.ready === true", timeout=120_000)
            # plots with worker slots (the Workers cell reads "n/m", m > 0) whose assets the set recorded, in board order
            recorded = _recorded_plots()
            uids = [u for u in page.eval_on_selector_all("#plot-tbody > tr.plot-row", "els => els.filter((e) => [...e.children].some((td) => "
                                                         "/^\\s*\\d+\\/[1-9]\\d*(\\+R)?\\s*$/.test(td.textContent))).map((e) => e.dataset.deedUid)")
                    if u in recorded]
            assert len(uids) >= 3, uids
            # a plot from the middle, parked 300 px below the header line; closed with the picker's Close
            middle = uids[len(uids) // 2]
            _open_plot(page, middle)
            mid = _cycle(page, middle, f"scrollY + document.querySelector('tr.plot-row[data-deed-uid=\"{middle}\"]').getBoundingClientRect().top - 300 "
                                       "- Math.max(0, ...['header.board-header', '#table-head-sticky'].map((s) => document.querySelector(s)?.getBoundingClientRect().bottom || 0))",
                         "button")
            page.locator(f'tr.plot-row[data-deed-uid="{middle}"]').click()  # close that plot again
            _settle(page)
            # the board's last plot (the top plot by Total PP, with the sort turned round) with the page scrolled to its
            # very end; closed by pressing the open tile again
            last = uids[0]
            for _ in range(2):
                if page.evaluate("[...document.querySelectorAll('#plot-tbody > tr.plot-row')].at(-1).dataset.deedUid") == last:
                    break
                page.evaluate("document.querySelector('th[data-key=\"harvest_pp\"]').click()")
                page.wait_for_timeout(300)
            assert page.evaluate("[...document.querySelectorAll('#plot-tbody > tr.plot-row')].at(-1).dataset.deedUid") == last
            _open_plot(page, last)
            end = _cycle(page, last, "document.documentElement.scrollHeight", "tile")
            return {"middle": mid, "last": end, "errors": errors, "aborted": aborted}
        finally:
            browser.close()


@pytest.mark.parametrize("width,height,motion", [(1536, 864, "no-preference"), (1366, 900, "no-preference"), (1536, 864, "reduce")])
def test_picker_brings_the_plot_under_the_header_and_close_scrolls_back(width, height, motion):
    got = _run(width, height, motion)
    assert got["errors"] == [] and got["aborted"] == []
    for name in ("middle", "last"):
        c = got[name]
        assert c["opened"]["picker"] is True, (name, c)
        assert abs(c["opened"]["rowTop"] - c["opened"]["line"]) <= 1, (name, c["opened"])   # the plot's top under the header
        assert abs(c["opened"]["line"] - c["before"]["line"]) <= 1, (name, c)               # the header itself did not move
        assert c["closed"]["picker"] is False
        assert abs(c["closed"]["scrollY"] - c["before"]["scrollY"]) <= 1, (name, c)          # back where the user was
        assert abs(c["closed"]["rowTop"] - c["before"]["rowTop"]) <= 1, (name, c)
        assert c["closed"]["minH"] == "", (name, c["closed"])                                # the reserved room is let go
    # the last plot really needed the reserve: the view now reaches past the page's content, so without the reserved
    # height the browser could not have scrolled its row up to the line
    last = got["last"]
    assert last["before"]["rowTop"] - last["before"]["line"] > 50, last["before"]
    assert last["opened"]["scrollY"] + last["opened"]["vh"] > last["opened"]["contentBottom"] + 50, last["opened"]
