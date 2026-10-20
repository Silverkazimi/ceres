"""Browser rendering guards for bulk-derived bonus identity (row 6,  migration).

Was a Node harness that sliced `bonusProvenanceNote`/`fmtTotem`/`fmtTitle`/
`bonusResolutionHtml` out of `app.js` by source position and hand-stubbed their
callees (`totemIconHtml`, `titleIconHtml`, `titleRarityGem`, `rarityGemHtml`).
Retired per  (source-string slicing is banned) and campaign §3.1 batch C
row 6. Replaced with a route-mocked live board: `/api/board` is served a real
129-plot snapshot with exactly one plot's `bonus.totem`/`bonus.title` swapped
for each scenario's shape (`ceres/board.py::_bonus_for_plot`'s own output
shape, by deed), and the assertion reads the rendered `.totem-title-cell` slot
icon's real `data-tip` attribute -- the same text `tipAttrs()`
(app.js:532) puts there for `initTooltips()` to show on hover, not a
source-string search.

Named injection (/, not performed here -- independent verification only):
in `ceres/static/app.js`'s `bonusProvenanceNote()` (~line 2559), change
`bonus.resolution !== 'derived'` to `bonus.resolution !== 'nonexistent'`. Expected
red: `test_known_bulk_totem_renders_name_and_derived_provenance` and
`test_known_bulk_title_renders_class_and_derived_provenance` both lose
"derived from this plot's bulk boost" from their `data-tip` text (the note
never fires). Second named injection: in `bonusResolutionHtml()` (~line 2564),
add a branch `if (bonus.resolution === 'resolved') return '<span class="bonus-resolution bonus-resolved" data-tip="Loaded from the staked asset.">Loaded</span>';`
before the `conflict` check. Expected red:
`test_resolved_asset_renders_no_pill_and_no_provenance_note` finds
`bonus-resolution` and `Loaded` in the `.totem-title-cell` HTML it asserts absent.
"""

from __future__ import annotations

import copy
import json
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


def _board_with_bonus(base_url: str, *, totem: dict | None = None, title: dict | None = None) -> tuple[dict, str]:
    board = _real_board(base_url)
    if not board["plots"]:
        raise AssertionError("live board returned no plots to build a bonus fixture from")
    row = copy.deepcopy(board["plots"][0])
    row["bonus"] = dict(row.get("bonus") or {}, totem=totem, title=title)
    board["plots"][0] = row
    return board, row["deed_uid"]


def _render_cell(page, base_url: str, board: dict, deed_uid: str, cell_selector: str) -> tuple[str, str]:
    """Serve `board` for `/api/board`, load the page, and return
    `(data-tip, outerHTML)` of the named plot row's cell."""
    page.route(
        "**/api/board*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(board)),
    )
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000,
    )
    cell = page.locator(f"tr.plot-row[data-deed-uid='{urllib.parse.quote(deed_uid, safe='')}'] {cell_selector}").first
    cell.wait_for(timeout=30_000)
    icon = cell.locator("[data-tip]").first
    tip = icon.get_attribute("data-tip") or ""
    html = cell.evaluate("(el) => el.outerHTML")
    return tip, html


def test_known_bulk_totem_renders_name_and_derived_provenance(live_browser: tuple[Browser, str]) -> None:
    # Owner ruling 2026-08-30: the "Derived" pill was noise -- the provenance
    # note now folds into the icon's own tooltip (data-tip) instead of a
    # second adjacent element, so this reads the tooltip text, not a pill.
    browser, base_url = live_browser
    totem = {
        "name": "Common Totem Land", "rarity": "common", "boost": 0.1,
        "asset_ref": None, "uid": None, "resolution": "derived", "source": "staking_facts",
    }
    board, deed_uid = _board_with_bonus(base_url, totem=totem)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    tip, html = _render_cell(page, base_url, board, deed_uid, ".totem-title-cell .totem-slot")
    page.close()
    assert "Common Totem Land" in tip
    assert "bonus-resolution" not in html
    assert ">?<" not in html


def test_known_bulk_title_renders_class_and_derived_provenance(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    title = {
        "name": "Legendary Title", "rarity": "legendary", "boost": 0.5,
        "asset_ref": None, "uid": None, "resolution": "derived", "source": "staking_facts",
    }
    board, deed_uid = _board_with_bonus(base_url, title=title)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    tip, html = _render_cell(page, base_url, board, deed_uid, ".totem-title-cell .title-slot")
    page.close()
    assert "Legendary Title" in tip
    assert "bonus-resolution" not in html
    assert ">?<" not in html


def test_unknown_nonzero_boost_is_named_not_question_mark(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    title = {
        "name": "Title present — identity unknown", "rarity": None, "boost": 0.75,
        "asset_ref": None, "uid": None, "resolution": "derived", "source": "staking_facts",
    }
    board, deed_uid = _board_with_bonus(base_url, title=title)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    tip, html = _render_cell(page, base_url, board, deed_uid, ".totem-title-cell .title-slot")
    page.close()
    assert "Title present — identity unknown" in tip
    assert "Unknown" in html
    assert ">?<" not in html


def test_conflicting_asset_renders_visible_warning(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    totem = {
        "name": "Epic Totem", "rarity": "epic", "boost": 0.5,
        "asset_ref": "epic-totem-asset", "uid": "u1", "resolution": "conflict",
        "source": "bonus_assets", "conflict": {"bulk_boost": 0.25, "asset_boost": 0.5},
    }
    board, deed_uid = _board_with_bonus(base_url, totem=totem)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _tip, html = _render_cell(page, base_url, board, deed_uid, ".totem-title-cell .totem-slot")
    page.close()
    assert "Conflict" in html
    assert "data-tip" in html
    assert ">?<" not in html


def test_resolved_asset_renders_no_pill_and_no_provenance_note(live_browser: tuple[Browser, str]) -> None:
    """Owner ruling (2026-08-30): `resolved` ("Loaded") is the NORMAL case and
    must say nothing about provenance anywhere -- no visible pill (the old
    `.bonus-resolution.bonus-resolved` span, now dead CSS) and no note folded
    into the icon's tooltip either (that treatment is `derived`-only). See
    module docstring for this test's named injection."""
    browser, base_url = live_browser
    totem = {
        "name": "Common Totem Land", "rarity": "common", "boost": 0.1,
        "asset_ref": "common-totem-asset", "uid": "u2", "resolution": "resolved", "source": "bonus_assets",
    }
    board, deed_uid = _board_with_bonus(base_url, totem=totem)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    tip, html = _render_cell(page, base_url, board, deed_uid, ".totem-title-cell .totem-slot")
    page.close()
    assert "bonus-resolution" not in html
    assert "Loaded" not in html
    assert "derived from" not in tip
