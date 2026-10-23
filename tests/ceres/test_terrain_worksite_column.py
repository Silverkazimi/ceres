"""Regression coverage for separate Terrain and Worksite plot-table columns.

census row 34: the (a) node-exec test (`test_pre_merge_cells_render_terrain_background_input_and_worksite_icon`)
is converted () to a real-browser test reading the main plot table's own
`td.terrain-cell`/`td.resource-cell` cells (`plotRowCellsHtml()`, app.js:8144-8163)
for a real board row -- no slicing/re-execution needed, `terrainCellHtml()`/
`resourceIconHtml()` render straight into the mounted table row.

 (row 34, (b) half): `test_worksite_cell_is_icon_only_and_terrain_keeps_its_own_cell`
is now converted too -- see its own docstring for what moved to a `vm_context`
whole-module symbol check versus what was dropped as redundant with the
rendered test above. This file no longer reads `ceres/static/app.js` by
source position (`test_plot_table_header_order_keeps_terrain_and_worksite_separate`
and `test_plot_and_terrain_background_rules_remain_in_css` read `index.html`/
`ceres.css`, outside the ratchet's `*.js` glob)."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres
from .vm_context import run_in_module_context


INDEX_HTML = Path(__file__).parents[2] / "ceres" / "static" / "index.html"


def _slice(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end, source.index(start))]


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def test_plot_table_header_order_keeps_terrain_and_worksite_separate() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    head = _slice(html, 'id="table-head-sticky"', "</thead>")  # #plot-table's own sticky thead
    keys = [line.split('data-key="')[1].split('"')[0] for line in head.splitlines() if "data-key=" in line]
    assert keys[:5] == ["plot", "rarity_rank", "plot_status_rank", "terrain", "resource"]

    body = _slice(html, '<table class="plot-table" id="plot-table">', "</colgroup>")
    cols = re.findall(r"<col[ >]", body)
    # one <col> per header column: Totem and Title are independent columns again and the
    # lock and plot-count columns were added (index.html), 20 in all -- was 17 with the
    # merged Totem/Title cell.
    assert len(cols) == len(re.findall(r"<th[ >]", head)) == 20


def test_worksite_cell_is_icon_only_and_terrain_keeps_its_own_cell() -> None:
    """ (row 34, (b) half): converted from source-substring
    checks to a whole-module `vm_context` load, for the one claim below with no
    rendered form -- `terrainWorksiteCellHtml` (the pre-split merged-cell
    builder this feature replaced) must not exist as a module-scope symbol.
    The other former substring claims are dropped as redundant, not just
    retargeted: `'class="resource-cell"'`/`'class="${terrainCell.cls}"'`/
    `"resourceIconHtml(row.resource)"` in `plotRowCellsHtml()` and the
    icon-only shape of `resourceIconHtml()` (no `<span>`) are implementation
    spellings of exactly what `test_pre_merge_cells_render_terrain_background_input_and_worksite_icon`
    below already proves by rendering a real board row -- a real
    `td.terrain-cell`/`td.resource-cell` pair with an icon and zero `<span>`
    children. `"worksite-label" not in source` is covered the same way: that
    class could only appear via the label-span shape the rendered sibling's
    own named injection (a `<span>${label}</span>` reintroduced into
    `resourceIconHtml()`) demonstrates it would catch."""
    run_in_module_context(
        "if (typeof terrainWorksiteCellHtml !== 'undefined') { "
        "throw new Error('terrainWorksiteCellHtml (the retired merged-cell builder) must not exist as a module-scope symbol'); }"
    )


def test_pre_merge_cells_render_terrain_background_input_and_worksite_icon(live_browser: tuple[Browser, str]) -> None:
    r"""Named injection, run and watched red, then reverted and watched green: in
    `ceres/static/app.js`'s `resourceIconHtml()`, the icon-only return
    `return \`<img class="resource-icon" src="${ASSET_BASE}/${file}" alt="${label}"${tipAttrs(label)}>\`;`
    was changed to append a visible label span,
    `return \`<img class="resource-icon" src="${ASSET_BASE}/${file}" alt="${label}"${tipAttrs(label)}><span>${label}</span>\`;`.
    Red: the real board row's `td.resource-cell` gained a `<span>` child --
    `assert 0 == 1` on the "no <span> in the resource cell" assertion. Restoring
    the line made this test pass again (whole-file run: 4 passed).

    Reads a REAL plot row already mounted in the main table (no route mocking --
    terrain/resource are static per-plot board-summary fields, not detail-fetch
    only) rather than re-slicing and re-executing the two render functions in a
    sandboxed script."""
    browser, base_url = live_browser
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    row = next((p for p in board["plots"] if p.get("terrain") and p.get("resource")), None)
    if row is None:
        pytest.skip("maintainer snapshot no longer has a real plot with both terrain and resource set")
    terrain_key = row["terrain"].lower()
    resource_key = row["resource"].lower()

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    tr = page.locator(f"tr.plot-row[data-deed-uid='{row['deed_uid']}']")
    tr.wait_for(timeout=30_000)

    terrain_cell = tr.locator(f"td.terrain-cell.terrain-{terrain_key}")
    assert terrain_cell.count() == 1, f"terrain cell missing expected class terrain-{terrain_key}"
    terrain_img = terrain_cell.locator("img.terrain-emblem")
    assert f"terrain-emblem-{terrain_key}.svg" in (terrain_img.get_attribute("src") or "")
    assert (terrain_img.get_attribute("data-tip") or terrain_img.get_attribute("title") or terrain_cell.inner_text())

    resource_cell = tr.locator("td.resource-cell")
    resource_img = resource_cell.locator("img.resource-icon")
    assert resource_img.count() == 1
    assert resource_key.replace(" ", "") in (resource_img.get_attribute("src") or "").lower()
    assert resource_cell.locator("span").count() == 0, "resource cell must stay icon-only, no visible label span"
    page.close()


def test_plot_and_terrain_background_rules_remain_in_css() -> None:
    css = (INDEX_HTML.parent / "ceres.css").read_text(encoding="utf-8")
    assert "td.plot-cell.territory-broken-lands       { background: var(--territory-broken-lands);" in css
    assert "td.terrain-cell.terrain-plains    { background: var(--terrain-plains);" in css
