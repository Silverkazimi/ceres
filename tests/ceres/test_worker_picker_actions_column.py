"""Guards the worker picker's actions column structure.

Root cause was structural: the candidate row used to be a `<button>`, and HTML
forbids nesting a button inside a button, so `pickerRegionHtml` (app.js) had to
emit Plan/Combine as SIBLINGS of the row in a wrapper `.candidate-action-row`
div -- landing outside the row's own box, which is why row widths disagreed and
the actions hung off to the side. `renderCandidateRow` now emits worker rows as
`<div role="button" tabindex="0">` with the actions living INSIDE the row as a
real trailing `.candidate-actions` grid column.

Retired the Node-extraction half of this file per  (campaign §3.1 batch C
row 41, census row 41): `test_worker_row_actions_render_inside_the_rows_own_container`
used to `_between()`-slice the real `renderCandidateRow` + `candidateCombineActionHtml`
out of `app.js` and run them in a bare Node sandbox with hand-stubbed helpers --
already the best-modelled bucket-(a) test in the census (real `html.parser` DOM
nesting, not a substring grep), just pointed at the wrong runtime. It now opens a
real worker candidate picker through `spawn_live_ceres` (route-mocked plot detail,
candidates, and idle-cards responses so one candidate is genuinely combinable --
`IDLE_CARDS_CACHE` populated via a real `/api/idle-cards` mock sharing the
candidate's `combineGroupKey()` -- and one is not) and reads the real rendered
DOM's ancestor chain with Playwright's own `evaluate`, which is strictly stronger
than the offline `html.parser` walk it replaces: this is the browser's real layout
engine parsing the browser's real output, not a second parser approximating it.

`test_injection_the_old_sibling_shape_is_caught_by_the_nesting_parser` below reads
no source file at all (a hardcoded literal reproduction of the retired sibling
shape) -- it was never a text-reader in 's sense and is kept unchanged: a
permanent regression fixture proving the nesting check actually distinguishes
"inside the row" from "next to it", not a real product read.

Named injection (/, not performed here -- independent verification only):
revert `renderCandidateRow`'s live worker-row return statement (`ceres/static/app.js`,
~line 4325, the `<${tag} class="candidate-row ...">...${actionsHtml}</${tag}>` line)
to the pre-fix shape (`<button class="candidate-row">...` with no `actionsHtml`
appended). Expected red:
`test_worker_row_actions_render_inside_the_rows_own_container` fails its
`assert parser.actions_seen` ("no .candidate-actions found at all").
"""

from __future__ import annotations

import copy
import json
import re
import urllib.parse
import urllib.request

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _real_board(base_url: str) -> dict:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _real_detail_row(base_url: str) -> dict:
    board = _real_board(base_url)
    for plot in board["plots"]:
        if plot.get("assets_loaded") and (plot.get("capacity") or {}).get("worker_count", 0) >= 1:
            deed_uid = plot["deed_uid"]
            url = f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}"
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
    raise AssertionError("no live plot with a real worker to build a detail-row template from")


def test_worker_row_actions_render_inside_the_rows_own_container(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    row["idle_cards_loaded"] = True
    row["workers"] = [w for w in row["workers"] if int(w.get("slot") or 0) != 1]
    deed_uid = row["deed_uid"]

    combinable = {
        "name": "Combinable Worker", "asset_ref": "combo-card-1", "uid": "combo-card-1",
        "card_detail_id": 40001, "edition": 3, "foil": 0, "level": 1, "base_pp": 5000,
        "terrain_boost": 0, "splinter": "Fire", "rarity": "Common", "card_set": "chaos",
        "construction_pp": {"status": "point", "value": 5000, "low": 5000, "high": 5000, "reason": None},
        "plot_impact": {"status": "point", "value": 100, "low": 100, "high": 100, "reason": None},
        "net_gain": 100, "net_gain_breakdown": {},
    }
    solo = {
        "name": "Solo Worker", "asset_ref": "solo-card-1", "uid": "solo-card-1",
        "card_detail_id": 40002, "edition": 3, "foil": 0, "level": 1, "base_pp": 4000,
        "terrain_boost": 0, "splinter": "Water", "rarity": "Common", "card_set": "chaos",
        "construction_pp": {"status": "point", "value": 4000, "low": 4000, "high": 4000, "reason": None},
        "plot_impact": {"status": "point", "value": 50, "low": 50, "high": 50, "reason": None},
        "net_gain": 50, "net_gain_breakdown": {},
    }
    # Two idle copies of `combinable`'s printing (same card_detail_id/edition/foil
    # matched by `combineGroupKey()`), both with a resolved BCX ceiling and neither
    # maxed nor blocked -- exactly `candidateCombineInfo()`'s (app.js:6979) real
    # eligibility contract, not a stubbed return value. `solo`'s printing has no
    # idle entry at all, so it stays non-combinable (the real dash).
    idle_cards = [
        {"uid": "combo-card-1", "card_detail_id": 40001, "edition": 3, "foil": 0, "bcx": 2, "bcx_max": 5, "available": True},
        {"uid": "combo-card-2", "card_detail_id": 40001, "edition": 3, "foil": 0, "bcx": 1, "bcx_max": 5, "available": True},
    ]

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(
        re.compile(rf"/api/plots/{re.escape(urllib.parse.quote(deed_uid, safe=''))}(/expand)?(\?|$)"),  # GET detail or POST expand
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"candidates": [combinable, solo], "total": 2, "source": "test-fixture", "excluded_counts": {}, "notices": []},
        )),
    )
    page.route(
        "**/api/idle-cards*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"cards": idle_cards})),
    )
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    empty_slot = page.locator(".slot-editor .slot-group[data-group-id='worker'] .slot-tile[data-slot-action='add']").first
    empty_slot.wait_for(timeout=30_000)
    empty_slot.click()
    page.locator(".candidate-row").first.wait_for(timeout=30_000)

    def actions_nested_inside_row(row_locator) -> bool:
        """Real DOM ancestor-chain check via the browser's own layout engine --
        strictly stronger than the offline `html.parser` walk this replaces."""
        return row_locator.evaluate(
            "(rowEl) => { const actions = rowEl.querySelector('.candidate-actions'); "
            "return !!actions && rowEl.contains(actions) && actions !== rowEl; }"
        )

    combinable_row = page.locator(".candidate-row", has_text="Combinable Worker").first
    combinable_row.wait_for(timeout=30_000)
    assert actions_nested_inside_row(combinable_row)
    combinable_html = combinable_row.evaluate("(el) => el.outerHTML")
    assert "picker-combine" in combinable_html
    assert "candidate-action-row" not in combinable_html, "the old sibling wrapper must be gone"

    solo_row = page.locator(".candidate-row", has_text="Solo Worker").first
    solo_row.wait_for(timeout=30_000)
    assert actions_nested_inside_row(solo_row)
    solo_html = solo_row.evaluate("(el) => el.outerHTML")
    assert "picker-combine-dash" in solo_html, "a non-combinable row must still show the dash, in the same column"
    assert "candidate-action-row" not in solo_html
    page.close()


def test_injection_the_old_sibling_shape_is_caught_by_the_nesting_parser() -> None:
    """Literal reproduction of the pre-2026-08-30 shape (pickerRegionHtml used to
    wrap `renderCandidateRow`'s <button class="candidate-row"> output and a
    sibling actions span in one `.candidate-action-row` div, since a <button>
    cannot contain another <button>). Confirms the parser used above actually
    tells 'inside the row' apart from 'next to it', rather than passing on any
    HTML that merely contains both class names somewhere. Reads no source file --
    not a text-reader in 's sense, unchanged by this migration."""
    from html.parser import HTMLParser

    class _NestingParser(HTMLParser):
        def __init__(self, row_class: str, actions_class: str) -> None:
            super().__init__()
            self.row_class = row_class
            self.actions_class = actions_class
            self.stack: list[str] = []
            self.row_depth: int | None = None
            self.actions_nested_inside_row = False
            self.actions_seen = False

        def _classes(self, attrs) -> str:
            for name, value in attrs:
                if name == "class":
                    return value or ""
            return ""

        def handle_starttag(self, tag, attrs):
            classes = self._classes(attrs)
            self.stack.append(tag)
            if self.row_depth is None and self.row_class in classes.split():
                self.row_depth = len(self.stack)
            if self.actions_class in classes.split():
                self.actions_seen = True
                if self.row_depth is not None and len(self.stack) > self.row_depth:
                    self.actions_nested_inside_row = True

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            self.stack.pop()

        def handle_endtag(self, tag):
            if self.stack and self.stack[-1] == tag:
                depth = len(self.stack)
                self.stack.pop()
                if self.row_depth is not None and depth == self.row_depth:
                    self.row_depth = None

    def actions_nested_inside_row(html: str) -> bool:
        parser = _NestingParser("candidate-row", "candidate-actions")
        parser.feed(html)
        assert parser.actions_seen, "no .candidate-actions found at all -- fixture is broken, not the assertion"
        return parser.actions_nested_inside_row

    old_shape = (
        '<div class="candidate-action-row">'
        '<button class="candidate-row is-gain" data-picker-action="choose" data-candidate-index="0">'
        '<span class="candidate-rank">1</span><span class="candidate-copy">Test Worker</span>'
        '<span class="candidate-metrics"></span>'
        '</button>'
        '<span class="candidate-actions">'
        '<button type="button" class="picker-plan" data-picker-action="plan" data-candidate-index="0">Planning</button>'
        '<button type="button" class="picker-combine" data-picker-action="combine" data-candidate-index="0">Combine (2)</button>'
        '</span>'
        '</div>'
    )
    assert not actions_nested_inside_row(old_shape), (
        "guard is void -- it passed on the literal pre-fix sibling shape"
    )
