"""IN-0267 phase 1 browser pilot. Marked for public browser CI (); never run on the laptop.

What it proves on the real board surface at 1920x1080:
- Menu > Planning enters "Planning · Plan 1" with one saved empty plan and makes zero requests: no /api call,
  no upstream/chain call, no wallet call; the live stage store is unchanged.
- IN-0315: in planning the plan is the only truth. With the
  new empty plan every row's stats and every total read 0 (no live figure), with no planning decoration.
- With one planned plot among many, only that plot asks the local evaluator (one row in one request), its
  collapsed cells show the PLANNED values from the (stubbed) evaluator answer, every other row stays exactly
  the empty-plan clean sheet, and the totals follow the plan.
- A failed evaluator answer shows "Unknown" in the changed row and in the totals, never 0 and never live.
- No horizontal overflow in planning; Menu > Live restores the live rows byte-for-byte and the live stage.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.ceres.fixture_live import install_routes, spawn_fixture_ceres
from tests.ceres.fixture_upstream_mini import SAMPLE, board_entries, write_set

playwright = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[2]

PLAN_KEY = f"ceres:board-plans:v1:{SAMPLE}"
STAGE_KEY = f"ceres:stage-plan:v1:{SAMPLE}"
PLANNED_PP = 4321

WALLET_SPY = """
window.__walletCalls = [];
const spy = (name) => (...args) => { window.__walletCalls.push(name); };
window.hive_keychain = new Proxy({}, { get: (_t, name) => (name === 'then' ? undefined : spy(String(name))) });
"""

ROWS_SNAPSHOT = """() => [...document.querySelectorAll('#plot-tbody tr.plot-row')].map((tr) => ({
  uid: tr.dataset.deedUid, html: tr.innerHTML }))"""

# in planning the sort reads the plan, so a row's listed position (the count cell) may move; the
# clean-sheet comparison is about the row's own cells, so it leaves that one position cell out.
def _without_position(html: str) -> str:
    return re.sub(r'<td class="count-cell col-w-count">\d*</td>', '', html)


TOTALS = """() => document.getElementById('totals-strip')?.textContent.replace(/\\s+/g, ' ').trim() || ''"""

ZERO_PP_ROWS = """() => [...document.querySelectorAll('#plot-tbody tr.plot-row')].filter((tr) =>
  !tr.querySelector('td.col-num[data-pp-exact="0"]')).map((tr) => tr.dataset.deedUid)"""

OVERFLOW = """() => {
  const scroller = document.getElementById('plot-table-scroll');
  const bounds = scroller?.getBoundingClientRect();
  const countText = [...document.querySelectorAll('table.plot-table :is(th.col-w-count, td.count-cell)')].map((cell) => {
    const range = document.createRange(); range.selectNodeContents(cell);
    const rect = range.getBoundingClientRect();
    return [rect.left, rect.right];
  });
  return { root: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
           table: bounds ? [bounds.left, bounds.right] : null,
           tableOverflowX: scroller ? getComputedStyle(scroller).overflowX : null, countText };
}"""


def _toggle_planning(page):
    page.click("#hdr-menu-btn")
    page.locator('#hdr-menu [data-menu-entry="planning"]').click()


def _no_overflow(page):
    sizes = page.evaluate(OVERFLOW)
    assert sizes["root"][0] <= sizes["root"][1], sizes
    if sizes["table"]:
        # deliberately clips the table's unused end reserve, so
        # scrollWidth can exceed clientWidth without any player-visible spill.
        # The last column's actual text must remain inside the table viewport.
        assert sizes["tableOverflowX"] == "clip", sizes
        assert sizes["countText"], sizes
        assert all(sizes["table"][0] <= left <= right <= sizes["table"][1]
                   for left, right in sizes["countText"]), sizes


def test_planning_entry_is_local_one_changed_row_and_live_round_trip_is_unchanged(tmp_path):
    fixture = write_set(tmp_path / "planning", board_entries())
    with spawn_fixture_ceres(fixture) as base, playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.add_init_script(WALLET_SPY)
        aborted = install_routes(page, fixture)
        evaluate_bodies: list[dict] = []
        answer = {"mode": "ok"}

        def evaluator(route):
            body = json.loads(route.request.post_data or "{}")
            evaluate_bodies.append(body)
            if answer["mode"] == "fail":
                return route.fulfill(status=500, content_type="application/json", body='{"detail":"stub failure"}')
            rows = [{"deed_uid": row["deed_uid"], "coverage": "complete", "harvest_pp": PLANNED_PP, "construction_pp": PLANNED_PP,
                     "base_pp": 1000, "base_pp_after_cap": 1000, "multiplier": 2.0, "work_per_hour": 12.5,
                     "grain_cost_per_hour": 10, "wood_cost_per_hour": None, "stone_cost_per_hour": None, "iron_cost_per_hour": None,
                     "capacity": {"display": "0/5", "worker_count": 0, "runi_staked": False, "card_capacity": 5}}
                    for row in body.get("rows", [])]
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"generated_at": body.get("capture_generated_at"), "rows": rows, "assets": {}}))

        page.route("**/api/board-plans/evaluate**", evaluator)
        page.goto(f"{base}/?player={SAMPLE}")
        page.wait_for_selector("#plot-tbody tr.plot-row")
        rows = page.locator("#plot-tbody tr.plot-row")
        assert rows.count() >= 2, "the pilot needs one changed plot among several"
        live_rows = page.evaluate(ROWS_SNAPSHOT)
        live_stage = page.evaluate(f"() => localStorage.getItem('{STAGE_KEY}')")

        # 1. Entry: one saved empty plan, label in the existing header place, zero requests of any kind.
        requests: list[str] = []
        page.on("request", lambda request: requests.append(request.url))
        aborted_before = len(aborted)
        _toggle_planning(page)
        page.wait_for_function("document.body.dataset.boardMode === 'planning'")
        assert page.locator("#hdr-place").inner_text() == "Planning · Plan 1"
        saved = page.evaluate(f"() => JSON.parse(localStorage.getItem('{PLAN_KEY}'))")
        assert len(saved["plans"]) == 1 and saved["plans"][0]["name"] == "Plan 1" and saved["plans"][0]["placements"] == []
        page.wait_for_timeout(600)  # longer than the evaluator debounce
        assert [u for u in requests if urlparse(u).path.startswith("/api/")] == []
        assert len(aborted) == aborted_before, "no upstream or chain host was contacted"
        assert page.evaluate("() => window.__walletCalls") == []
        assert page.evaluate(f"() => localStorage.getItem('{STAGE_KEY}')") == live_stage
        assert page.locator('#plot-tbody [data-row-plan-status]').count() == 0, "an empty plan decorates nothing"
        # IN-0315: a new plan is a clean sheet -- every row's PP is 0 and no row shows its live HTML; totals 0.
        assert page.evaluate(ZERO_PP_ROWS) == [], "every row's Total PP reads 0 in a new plan"
        empty_rows = {row["uid"]: row["html"] for row in page.evaluate(ROWS_SNAPSHOT)}
        assert all(empty_rows[row["uid"]] != row["html"] for row in live_rows if "data-pp-exact=\"0\"" not in row["html"]), \
            "no row with live production renders its live cells in planning"
        totals = page.evaluate(TOTALS)
        for figure in ("Base PP: 0", "Total PP: 0", "DEC stake: 0", f"Idle plots: {rows.count()}"):
            assert figure in totals, totals
        _no_overflow(page)

        # 2. One changed plot among many: a planned Totem on the first row, saved as the picker would save it.
        changed_uid = live_rows[0]["uid"]
        page.evaluate("""([key, uid]) => {
          const saved = JSON.parse(localStorage.getItem(key));
          saved.plans[0].placements = [{ deed_uid: uid, group_id: 'totem', slot_index: 0, asset_ref: 'pilot-totem',
            name: 'Legendary Totem', asset: { asset_ref: 'pilot-totem', name: 'Legendary Totem', rarity: 'legendary', boost: 1 } }];
          saved.revision += 1;
          localStorage.setItem(key, JSON.stringify(saved));
        }""", [PLAN_KEY, changed_uid])
        _toggle_planning(page)  # back to Live
        page.wait_for_function("document.body.dataset.boardMode !== 'planning'")
        requests.clear()
        _toggle_planning(page)  # planning again, now with one planned plot
        changed = page.locator(f'#plot-tbody tr.plot-row[data-deed-uid="{changed_uid}"]')
        changed.locator('[data-row-plan-status="planned"]').first.wait_for()
        assert len(evaluate_bodies) == 1, "one bounded request"
        assert [row["deed_uid"] for row in evaluate_bodies[0]["rows"]] == [changed_uid], "only the changed row is evaluated"
        assert evaluate_bodies[0]["capture_generated_at"]
        assert f"{PLANNED_PP:,}" in changed.locator("td.col-num").first.inner_text()
        assert changed.locator(".totem-cell").count() == 1
        api_calls = [urlparse(u).path for u in requests if urlparse(u).path.startswith("/api/")]
        assert set(api_calls) == {"/api/board-plans/evaluate"}, api_calls
        assert page.evaluate("() => window.__walletCalls") == []
        planning_snapshot = page.evaluate(ROWS_SNAPSHOT)
        planning_rows = {row["uid"]: row["html"] for row in planning_snapshot}
        for row in live_rows[1:]:
            assert _without_position(planning_rows[row["uid"]]) == _without_position(empty_rows[row["uid"]]), \
                "an unplanned row stays the empty-plan clean sheet"
        # the sort reads the plan -- the changed row had no planned value yet when the board sorted (its
        # answer was pending), so it is listed last by the board's null rule, never at its live position.
        assert planning_snapshot[-1]["uid"] == changed_uid, [row["uid"] for row in planning_snapshot]
        assert f"Total PP: {PLANNED_PP:,}" in page.evaluate(TOTALS), "totals follow the plan"
        assert page.locator('#plot-tbody tr.plot-row:not([data-deed-uid="%s"]) [data-row-plan-status]' % changed_uid).count() == 0
        _no_overflow(page)

        # 3. A failed answer for a new roster: Unknown, never 0 and never the live number.
        answer["mode"] = "fail"
        page.evaluate("""([key]) => {
          const saved = JSON.parse(localStorage.getItem(key));
          saved.plans[0].placements[0].asset_ref = 'pilot-totem-2';
          saved.revision += 1;
          localStorage.setItem(key, JSON.stringify(saved));
        }""", [PLAN_KEY])
        _toggle_planning(page)
        page.wait_for_function("document.body.dataset.boardMode !== 'planning'")
        _toggle_planning(page)
        changed.locator('[data-row-plan-status="unknown"]').first.wait_for()
        harvest_text = changed.locator("td.col-num").first.inner_text().strip()
        assert harvest_text == "Unknown"
        assert "Total PP: Unknown" in page.evaluate(TOTALS), "a failed row makes the total Unknown, never live"
        _no_overflow(page)

        # 4. Live round-trip: every row and the live stage exactly as before planning.
        _toggle_planning(page)
        page.wait_for_function("document.body.dataset.boardMode !== 'planning'")
        assert page.locator("#hdr-place").inner_text() == "Plots"
        assert page.evaluate(ROWS_SNAPSHOT) == live_rows
        assert page.evaluate(f"() => localStorage.getItem('{STAGE_KEY}')") == live_stage
        assert page.evaluate("() => window.__walletCalls") == []
        assert not any("wallet" in url.lower() or "keychain" in url.lower() or "hiveauth" in url.lower() for url in requests)
        browser.close()
