""" (version A, ceres-table-redesign plan §2/§5, maintainer ): visual-system-
only redesign of the plot table -- font, row rhythm, header gradient/contrast, sort
state, zebra/hover/divider, icon sizing, alignment. Source guards cover the visual contract;
the Totem/Title cell also has a whole-module VM render check. (version B) added
three sortable material-input columns; keeps Totem and Title as separate ordinary
columns, and every other Version A token assertion is untouched.
"""
from __future__ import annotations

import copy
import json
import re
import urllib.request
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Browser

from .conftest import settle, spawn_live_ceres
from .vm_context import run_in_module_context

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
INDEX_HTML = STATIC / "index.html"
APP_JS = STATIC / "app.js"
TABLE_JS = STATIC / "table.js"
CERES_CSS = STATIC / "ceres.css"
ROWS_JS = STATIC / "rows.js"

CSS = CERES_CSS.read_text(encoding="utf-8")
HTML = INDEX_HTML.read_text(encoding="utf-8")
APP = APP_JS.read_text(encoding="utf-8")
TABLE = TABLE_JS.read_text(encoding="utf-8")
ROWS = ROWS_JS.read_text(encoding="utf-8")


def test_body_font_clamp_is_exact():
    assert "font-size: clamp(.875rem, .75rem + .15625cqi, .9375rem); line-height: 1.2;" in CSS


def test_row_block_size_and_padding():
    # Pre-landing fix: `min-block-size` is inert on a table cell in Chromium (measured
    # 33.8px against a 36px target) -- `block-size`, scoped to the real data row's own
    # td (never the virtual-spacer/expand-row td), forces it.
    assert "table.plot-table tbody tr.plot-row td { block-size: 2.25rem; box-sizing: border-box;" in CSS
    assert "table.plot-table tbody tr.virtual-spacer td { padding: 0; border: 0; }" in CSS
    assert "padding: .375rem clamp(.25rem, .3125cqi, .375rem);" in CSS


def test_header_block_size_and_type():
    # Same Chromium `min-block-size` fix, applied to the header th.
    th_rule = re.search(r"table\.plot-table th \{(.*?)\n\}", CSS, re.S)
    assert th_rule, "could not find table.plot-table th rule"
    assert "block-size: 2.75rem; box-sizing: border-box;" in th_rule.group(1)
    assert re.search(r"(?<!min-)block-size: 2.75rem;", th_rule.group(1))
    assert "\n min-block-size" not in th_rule.group(1)
    assert "font-weight: 700; line-height: 1.1; letter-spacing: .025em;" in CSS


def test_header_gradient_every_theme():
    gradient = (
        "linear-gradient(180deg, color-mix(in srgb, var(--primary) 65%, var(--heading) 35%), "
        "color-mix(in srgb, var(--primary) 50%, var(--heading) 50%))"
    )
    # ( for the Badges flag) ended the
    # .badge-bar-head/table-header gradient parity established -- the head
    # glyph now shares the Iris strip recipe instead (see test_edge_stack.py).
    # The gradient itself stays universal (not gated behind [data-theme="light"]).
    assert gradient in CSS
    assert '[data-theme="light"] table.plot-table th' not in CSS
    assert '[data-theme="light"] .badge-bar-head' not in CSS


def test_header_divider_and_highlight():
    assert "border-bottom: .125rem solid var(--border-bright);" in CSS
    assert "box-shadow: inset 0 1px 0 color-mix(in srgb, var(--hi) 10%, transparent);" in CSS


def test_zebra_and_hover_recipes():
    assert "background: color-mix(in srgb, var(--bg2) 72%, var(--bg));" in CSS
    assert "background: color-mix(in srgb, var(--primary) 8%, var(--bg3));" in CSS
    assert "box-shadow: inset .1875rem 0 0 var(--accent);" in CSS
    assert "table.plot-table tbody tr.plot-row:focus-within" in CSS


def test_unified_icon_clamp_replaces_old_formulas():
    icon_clamp = "clamp(1.25rem, 1rem + .3125cqi, 1.375rem)"
    assert CSS.count(icon_clamp) >= 2  # icon-size rule + sort-chevron reuse would differ; icon rule alone repeats width/height
    assert "max(1.05rem, 0.563rem + 0.624cqi)" not in CSS  # the old title-cell formula is gone


def test_icon_clamp_includes_rarity_gem_excludes_tax_art():
    clamp_rule = re.search(r"table\.plot-table td\.resource-cell[^{]*\{[^}]*width: clamp\(1\.25rem[^}]*\}", CSS, re.S)
    assert clamp_rule, "could not find the shared icon clamp rule"
    selector_block = clamp_rule.group(0)
    # The Rarity column's own gem (not just the title-cell's rarity-gem override).
    assert "table.plot-table td.rarity-cell .rarity-gem" in selector_block
    # Pre-landing fix: 's approved, frozen 1.7rem Keep/Castle art ()
    # must not be pulled down to the shared clamp by this rule's specificity.
    assert "table.plot-table td.resource-cell .resource-icon:not(.is-tax)" in selector_block


def test_no_fixed_column_widths():
    # table-layout stays auto/no hardcoded ch/px/rem widths on .col-w-* -- the guard
    # this test extends (test_scaling_guard.py) already exercises this live
    # here we only assert the static declaration itself was not reintroduced.
    assert "table-layout: auto; width: 100%;" in CSS
    assert re.search(r"\.col-w-[a-z]+\s*\{[^}]*width\s*:", CSS) is None


def test_sort_chevron_markup_and_css():
    # Every data-key header in the thead carries a chevron slot.
    thead_match = re.search(r'<thead[^>]*id="table-head-sticky"[^>]*>.*?</thead>', HTML, re.S)
    assert thead_match, "could not find #table-head-sticky thead"
    thead_html = thead_match.group(0)
    ths = re.findall(r"<th\b[^>]*>.*?</th>", thead_html, re.S)
    assert len(ths) == 19
    controls = re.findall(r'<(?:th|button)\b[^>]*\bdata-key="[^"]+"', thead_html)
    assert len(controls) == 19
    for th in ths:
        assert 'sort-chevron' in th
    assert "th[data-key] .sort-chevron" in CSS
    assert "--ceres-chevron-art" in CSS  # reuses the existing shared chevron mask, no new icon family
    assert 'table.plot-table .table-sort-control[aria-pressed="true"] .sort-chevron' in CSS
    assert "table.plot-table th[data-key] .sort-chevron" in CSS
    chevron_rule = CSS.split("table.plot-table th[data-key] .sort-chevron", 1)[1].split("}", 1)[0]
    assert "inline-size: .6rem; block-size: .6rem;" in chevron_rule
    # (2026-09-20) legitimately introduced its OWN, unrelated
    # `+ .75rem)` (the `--table-end-reserve` DEC-clearance formula) -- this guard
    # only cares that the chevron's own old padding formula (pre-shrink) is gone.
    assert "padding-inline-end: calc(clamp(.25rem, .3125cqi, .375rem) + .75rem);" not in chevron_rule
    assert "inline-size: .7rem; block-size: .7rem;" not in chevron_rule
    # (maintainer: chevron must sit right after the header text, not at the far
    # edge of the cell): the chevron is in normal flow now, not an absolutely
    # positioned end-of-cell slot.
    assert "position: static" in chevron_rule
    assert "display: inline-block" in chevron_rule
    assert "margin-inline-start: .3rem" in chevron_rule
    assert "position: absolute" not in chevron_rule
    assert "inset-inline-end" not in chevron_rule
    assert "padding-inline-end: calc(clamp(.25rem, .3125cqi, .375rem) + .6rem);" not in chevron_rule


def test_aria_sort_maintained_in_js():
    assert "export function syncSortAriaState()" in TABLE
    assert "setAttribute('aria-sort'" in TABLE
    assert 'aria-sort="none"' in HTML
    assert 'table-sort-control' not in HTML
    assert "syncSortAriaState" in APP  # imported/called from the existing click handler
    assert "syncSortAriaState()" in APP


def test_aria_sort_plot_direction_is_inverted_like_sortbyplot():
    # board.js::sortByPlot inverts SORT_DIR for the 'plot' key only
    # (`dir = SORT_DIR === 1 ? -1 : 1`) -- every other column's comparator in
    # sortRows() reads SORT_DIR directly. syncSortAriaState() must mirror both.
    board_js = (STATIC / "board.js").read_text(encoding="utf-8")
    assert "const dir = S.SORT_DIR === 1 ? -1 : 1;" in board_js
    sync_fn = re.search(r"export function syncSortAriaState\(\) \{.*?\n\}", TABLE, re.S)
    assert sync_fn, "could not find syncSortAriaState()"
    body = sync_fn.group(0)
    assert "key === 'plot' ? S.SORT_DIR === -1 : S.SORT_DIR === 1" in body


def test_badge_bar_head_shares_header_treatment():
    # `.badge-bar-head` is reinstated
    # ('s measured follow-up had retired it in favour of a standalone
    # `.badge-flag` handle, itself now gone -- see test_edge_stack.py), but it
    # no longer shares the table header's own gradient -- it uses the shared Iris
    # strip recipe instead.
    badge_head = re.search(r"\.badge-bar-head\s*\{[^}]*\}", CSS, re.S)
    assert badge_head, "could not find .badge-bar-head rule"
    assert "linear-gradient(180deg" not in badge_head.group(0)
    # (maintainer: "give it two chips, the dark one in the current boundary
    # sizes ... exactly like the iris and wallet behave") supersedes 's
    # plain transparent stop -- the flag sits on the table header band, so the
    # SAME translucent recipe let that band shimmer through where Iris/Wallet
    # sit over plain page background and read solid. `--bg3` is opaque and
    # measured to match the Wallet pill's own live outer pixel.
    assert "color-mix(in srgb, var(--shadow) 40%, var(--bg3))" in badge_head.group(0)
    assert "color-mix(in srgb, var(--shadow) 40%, transparent)" not in badge_head.group(0)
    assert not re.search(r"\.badge-flag\s*\{", CSS), "the retired .badge-flag rule still exists"


def test_column_count_and_markup_unchanged():
    # (version B) added three material-input columns; keeps Totem
    # and Title separate, leaving 18 physical cells and sort keys. The expand-row colSpan
    # follows PLOT_TABLE_COLUMN_COUNT in state.js and is covered directly in
    # test_resource_inputs_per_hour.py rather than duplicated here.
    cols = re.findall(r"<col\b[^>]*>", HTML)
    # colgroup for #plot-table only (mobile/other tables aren't this markup).
    colgroup_match = re.search(r'<table class="plot-table" id="plot-table">\s*<colgroup>(.*?)</colgroup>', HTML, re.S)
    assert colgroup_match
    assert len(re.findall(r"<col\b", colgroup_match.group(1))) == 19


def test_totem_and_title_are_separate_fluid_columns_between_base_pp_and_boost():
    thead = re.search(r'<thead[^>]*id="table-head-sticky"[^>]*>.*?</thead>', HTML, re.S).group(0)
    keys = re.findall(r'<(?:th|button)\b[^>]*\bdata-key="([^"]+)"', thead)
    assert keys.index("base_pp") < keys.index("totem_boost") < keys.index("title_boost") < keys.index("multiplier")
    assert 'class="totem-cell"' in ROWS
    assert 'class="title-cell"${titleTipAttrs(row)}' in ROWS
    assert 'class="col-w-totem"' in HTML
    assert 'class="col-w-title"' in HTML
    assert 'totem-title' not in HTML + ROWS + CSS
    width_rule = re.search(
        r"table\.plot-table col\.col-w-totem,\n" r"table\.plot-table col\.col-w-title,\n" r"table\.plot-table th\[data-key=\"totem_boost\"\],\n" r"table\.plot-table th\[data-key=\"title_boost\"\],\n" r"table\.plot-table td\.totem-cell,\n" r"table\.plot-table td\.title-cell \{(.*?)\n\}",
        CSS,
        re.S,
    )
    assert width_rule, "could not find the independent Totem/Title width rule"
    assert "  min-width: 4rem;" in width_rule.group(1)


def test_totem_and_title_reuse_the_shared_centering_rule():
    center_rule = re.search(
        r"table\.plot-table th\.col-center,\n" r"table\.plot-table td\.plot-cell,\n" r"table\.plot-table td\.rarity-cell,\n" r"table\.plot-table td\.resource-cell,\n" r"table\.plot-table td\.type-cell,\n" r"table\.plot-table td\.totem-cell,\n" r"table\.plot-table td\.title-cell \{(.*?)\n\}",
        CSS,
        re.S,
    )
    assert center_rule, "Totem/Title centring must use the shared table rule"
    assert "text-align: center;" in center_rule.group(1)
    assert "display: flex" not in center_rule.group(0)
    assert ".totem-icon { width: 1.3rem; height: 1.3rem; vertical-align: middle; margin-right: 0; }" in CSS


def test_title_cell_is_the_only_shared_tooltip_trigger():
    assert 'titleTipAttrs(row)' in ROWS
    assert 'class="title-cell"${titleTipAttrs(row)}' in ROWS
    assert '.title-slot' not in ROWS
    assert 'titleIconHtml(null, title.name)' in (STATIC / "format.js").read_text(encoding="utf-8")
    assert 'rarityGemHtml(gemTier, {})' in (STATIC / "format.js").read_text(encoding="utf-8")
    assert 'data-tip' not in (STATIC / "format.js").read_text(encoding="utf-8").split('export function fmtTitle', 1)[1].split('function fmtDecEfficiency', 1)[0]


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def test_separate_totem_title_cells_keep_exact_row_height_and_tooltips(live_browser: tuple[Browser, str]):
    browser, base_url = live_browser
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:  # noqa: S310
        board = json.loads(response.read().decode("utf-8"))
    assert len(board["plots"]) >= 2, "live board needs two plots for the row-height comparison"
    plain = copy.deepcopy(board["plots"][0])
    totem_conflict = copy.deepcopy(plain)
    title_conflict = copy.deepcopy(plain)
    unmatched = copy.deepcopy(plain)
    generic_derived = copy.deepcopy(plain)
    without_bonus = copy.deepcopy(board["plots"][1])
    totem_conflict["deed_uid"] = f"{plain['deed_uid']}-totem-conflict"
    title_conflict["deed_uid"] = f"{plain['deed_uid']}-title-conflict"
    unmatched["deed_uid"] = f"{plain['deed_uid']}-unmatched-tier"
    generic_derived["deed_uid"] = f"{plain['deed_uid']}-generic-derived"
    plain["bonus"] = plain["_live_bonus"] = {
        "totem": {"name": "Common Totem", "rarity": "common", "boost": 0.1},
        "title": {"name": "The Dawnbringer", "rarity": "legendary", "boost": 0.5},
    }
    totem_conflict["bonus"] = totem_conflict["_live_bonus"] = {
        "totem": {
            "name": "Epic Totem", "rarity": "epic", "boost": 0.5,
            "resolution": "conflict", "conflict": {"bulk_boost": 0.25, "asset_boost": 0.5},
        },
        "title": {"name": "The Dawnbringer", "rarity": "legendary", "boost": 0.5},
    }
    title_conflict["bonus"] = title_conflict["_live_bonus"] = {
        "totem": {"name": "Common Totem", "rarity": "common", "boost": 0.1},
        "title": {
            "name": "The Dawnbringer", "rarity": "legendary", "boost": 0.5,
            "resolution": "conflict", "conflict": {"bulk_boost": 0.25, "asset_boost": 0.5},
        },
    }
    unmatched["bonus"] = unmatched["_live_bonus"] = {
        "totem": {"name": "Common Totem", "rarity": "common", "boost": 0.1},
        "title": {"name": "The Unmatched", "rarity": "rare", "boost": 0.37},
    }
    generic_derived["bonus"] = generic_derived["_live_bonus"] = {
        "totem": {"name": "Common Totem", "rarity": "common", "boost": 0.1},
        "title": {"name": "Epic Title", "rarity": "epic", "boost": 0.25, "resolution": "derived"},
    }
    without_bonus["bonus"] = without_bonus["_live_bonus"] = {
        "totem": {"name": "Common Totem", "rarity": "common", "boost": 0.1},
        "title": None,
    }
    board["plots"][:6] = [plain, totem_conflict, title_conflict, unmatched, generic_derived, without_bonus]

    context = browser.new_context(viewport={"width": 1536, "height": 864}, device_scale_factor=4)
    page = context.new_page()
    page.route(
        "**/api/board*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(board)),
    )
    try:
        page.goto(f"{base_url}/?player=sample01", wait_until="domcontentloaded")
        page.wait_for_selector("tr.plot-row", state="attached", timeout=30_000)
        page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 1", timeout=30_000)
        page.evaluate("document.documentElement.setAttribute('data-theme', 'alchemical')")
        geometry = page.evaluate(
            """([plainId, totemConflictId, titleConflictId, unmatchedId, genericId, withoutId]) => {
 const row = id => document.querySelector(`tr.plot-row[data-deed-uid="${CSS.escape(id)}"]`);
 const rows = [row(plainId), row(totemConflictId), row(titleConflictId), row(unmatchedId), row(genericId)];
 const empty = row(withoutId);
 const box = rect => ({left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height});
 const centeredGroup = (cell, nodes) => {
 const cellBox = cell.getBoundingClientRect();
 const rects = nodes.map(node => node.getBoundingClientRect());
 const left = Math.min(...rects.map(rect => rect.left));
 const right = Math.max(...rects.map(rect => rect.right));
 return {
 box: {left, right, top: Math.min(...rects.map(rect => rect.top)), bottom: Math.max(...rects.map(rect => rect.bottom)), width: right - left, height: Math.max(...rects.map(rect => rect.bottom)) - Math.min(...rects.map(rect => rect.top))},
 leftGap: left - cellBox.left,
 rightGap: cellBox.right - right,
 centerOffset: (left + right) / 2 - (cellBox.left + cellBox.right) / 2,
 };
 };
 const measure = currentRow => {
 const totemCell = currentRow.querySelector('.totem-cell');
 const titleCell = currentRow.querySelector('.title-cell');
 const totem = currentRow.querySelector('.totem-icon');
 const title = currentRow.querySelector('.title-icon');
 const gem = currentRow.querySelector('.title-cell .rarity-gem');
 const markers = [...currentRow.querySelectorAll('.bonus-conflict')];
 const tooltip = marker => marker.getAttribute('title') || marker.getAttribute('data-tip') || '';
 return {
 totemCell: box(totemCell.getBoundingClientRect()),
 titleCell: box(titleCell.getBoundingClientRect()),
 height: currentRow.getBoundingClientRect().height,
 totemGroup: totem ? centeredGroup(totemCell, [totem]) : null,
 titleGroup: title && gem ? centeredGroup(titleCell, [title, gem]) : null,
 titleCellTips: currentRow.querySelectorAll('.title-cell[data-tip]').length,
 titleIconTips: currentRow.querySelectorAll('.title-cell .title-icon[data-tip]').length,
 rarityIconTips: currentRow.querySelectorAll('.title-cell .rarity-gem[data-tip]').length,
 totemInsideCell: totem.getBoundingClientRect().left >= totemCell.getBoundingClientRect().left && totem.getBoundingClientRect().right <= totemCell.getBoundingClientRect().right,
 titleInsideCell: Boolean(title && gem && title.getBoundingClientRect().left >= titleCell.getBoundingClientRect().left && title.getBoundingClientRect().right <= titleCell.getBoundingClientRect().right && gem.getBoundingClientRect().left >= titleCell.getBoundingClientRect().left && gem.getBoundingClientRect().right <= titleCell.getBoundingClientRect().right),
 markers: markers.map(marker => ({
 rightInsideCell: (() => {
 const cell = marker.closest('.totem-cell, .title-cell').getBoundingClientRect();
 return marker.getBoundingClientRect().right <= cell.right;
 })(),
 unclipped: marker.scrollWidth <= marker.clientWidth,
 width: marker.getBoundingClientRect().width,
 height: marker.getBoundingClientRect().height,
 background: getComputedStyle(marker).backgroundColor,
 glyph: getComputedStyle(marker, '::after').content,
 text: marker.textContent.trim(),
 tooltip: tooltip(marker),
 })),
 };
 };
 return {
 rows: rows.map(measure),
 emptyHeight: empty.getBoundingClientRect().height,
 emptyTotemCell: box(empty.querySelector('.totem-cell').getBoundingClientRect()),
 emptyTitleCell: box(empty.querySelector('.title-cell').getBoundingClientRect()),
 headers: ['totem_boost', 'title_boost'].map(key => {
 const th = document.querySelector(`#table-head-sticky th[data-key="${key}"]`);
 const labelRange = document.createRange();
 labelRange.selectNode(th.firstChild);
 const label = labelRange.getBoundingClientRect();
 const chevron = th.querySelector('.sort-chevron').getBoundingClientRect();
 const groupLeft = Math.min(label.left, chevron.left);
 const groupRight = Math.max(label.right, chevron.right);
 const cell = th.getBoundingClientRect();
 return {
 key,
 cell: box(cell),
 label: box(label),
 group: {left: groupLeft, right: groupRight, top: Math.min(label.top, chevron.top), bottom: Math.max(label.bottom, chevron.bottom), width: groupRight - groupLeft, height: Math.max(label.bottom, chevron.bottom) - Math.min(label.top, chevron.top)},
 leftGap: groupLeft - cell.left,
 rightGap: cell.right - groupRight,
 groupCenterOffset: (groupLeft + groupRight) / 2 - (cell.left + cell.right) / 2,
 labelCenterOffset: (label.left + label.right) / 2 - (cell.left + cell.right) / 2,
 };
 }),
 columnWidths: [...document.querySelectorAll('#plot-tbody > tr.plot-row:first-child > td')].map(cell => cell.getBoundingClientRect().width),
 separateShape: {
 headers: [...document.querySelectorAll('#table-head-sticky th')].filter(th => ['totem_boost', 'title_boost'].includes(th.dataset.key)).length,
 cells: empty.cells.length,
 sharedWrapperCount: document.querySelectorAll('.totem-title-pair, .totem-title-slot').length,
 },
 };
 }""",
                [plain["deed_uid"], totem_conflict["deed_uid"], title_conflict["deed_uid"], unmatched["deed_uid"], generic_derived["deed_uid"], without_bonus["deed_uid"]],
            )
        title_icon = page.locator(
                f'tr.plot-row[data-deed-uid="{generic_derived["deed_uid"]}"] .title-icon'
            )
        rarity_icon = page.locator(
                f'tr.plot-row[data-deed-uid="{generic_derived["deed_uid"]}"] .title-cell .rarity-gem'
            )
        title_icon.hover()
        settle(page)
        page.wait_for_selector("#ceres-tooltip:not([hidden])")
        title_tip = page.evaluate(
                """() => {
 const tip = document.querySelector('#ceres-tooltip');
 window.__s15270Tooltip = tip;
 return {
 count: document.querySelectorAll('#ceres-tooltip').length,
 html: tip.querySelector('.tip-inner').innerHTML,
 text: tip.textContent.trim(),
 icons: [...tip.querySelectorAll('img')].map(icon => icon.getAttribute('src')),
 };
 }"""
            )
        rarity_icon.hover()
        settle(page)
        page.wait_for_selector("#ceres-tooltip:not([hidden])")
        rarity_tip = page.evaluate(
                """() => {
 const tip = document.querySelector('#ceres-tooltip');
 return {
 sameElement: tip === window.__s15270Tooltip,
 count: document.querySelectorAll('#ceres-tooltip').length,
 html: tip.querySelector('.tip-inner').innerHTML,
 text: tip.textContent.trim(),
 icons: [...tip.querySelectorAll('img')].map(icon => icon.getAttribute('src')),
 };
 }"""
            )
        screenshot = Image.open(BytesIO(page.screenshot())).convert("RGB")
        def bright_right_padding(cell):
            x0 = int((cell["right"] - 5) * 4)
            x1 = int(cell["right"] * 4) + 1
            y0 = int(cell["top"] * 4)
            y1 = int(cell["bottom"] * 4)
            return sum(
                1
                for y in range(y0, min(y1, screenshot.height))
                for x in range(x0, min(x1, screenshot.width))
                if sum(screenshot.getpixel((x, y))) > 600
            )
        for row in geometry["rows"]:
            row["brightTotemRightPadding"] = bright_right_padding(row["totemCell"])
            row["brightTitleRightPadding"] = bright_right_padding(row["titleCell"])
        geometry["emptyTotemBrightRightPadding"] = bright_right_padding(geometry["emptyTotemCell"])
        geometry["emptyTitleBrightRightPadding"] = bright_right_padding(geometry["emptyTitleCell"])
        tooltip = {"title": title_tip, "rarity": rarity_tip}
    finally:
        page.close()
        context.close()

    rows = geometry["rows"]
    def assert_centered(group):
        assert abs(group["leftGap"] - group["rightGap"]) * 4 <= 1.0
        assert abs(group["centerOffset"]) * 4 <= 1.0

    assert all(row["height"] == 36.0 for row in rows)
    assert geometry["emptyHeight"] == 36.0
    assert all(row["height"] == geometry["emptyHeight"] for row in rows)
    for row in rows:
        assert row["totemGroup"] is not None
        assert_centered(row["totemGroup"])
        if row["titleGroup"] is not None:
            assert_centered(row["titleGroup"])
    assert any(row["titleGroup"] is not None for row in rows)
    for header in geometry["headers"]:
        assert abs(header["leftGap"] - header["rightGap"]) * 4 <= 1.0
        assert abs(header["groupCenterOffset"]) * 4 <= 1.0
    assert all(row["totemInsideCell"] for row in rows)
    assert all(row["titleInsideCell"] for row in rows if row["titleGroup"] is not None)
    assert rows[4]["titleCellTips"] == 1
    assert all(row["titleIconTips"] == row["rarityIconTips"] == 0 for row in rows)
    assert all(row["brightTotemRightPadding"] == 0 for row in rows)
    assert all(row["brightTitleRightPadding"] == 0 for row in rows)
    assert geometry["emptyTotemBrightRightPadding"] == 0
    assert geometry["emptyTitleBrightRightPadding"] == 0
    assert geometry["separateShape"] == {"headers": 2, "cells": 19, "sharedWrapperCount": 0}
    assert rows[0]["markers"] == []
    assert [len(row["markers"]) for row in rows[1:]] == [1, 1, 0, 0]
    markers = [marker for row in rows for marker in row["markers"]]
    assert all(marker["rightInsideCell"] and marker["unclipped"] for marker in markers)
    assert all(marker["width"] == marker["height"] == 14.0 for marker in markers)
    assert all(marker["background"] not in ("rgba(0, 0, 0, 0)", "transparent") for marker in markers)
    assert all(marker["glyph"] == '"!"' for marker in markers)
    assert all(marker["tooltip"] for marker in markers)
    assert any(marker["text"] == "Conflict" and "Boost mismatch" in marker["tooltip"] for marker in markers)
    assert rows[3]["markers"] == []
    assert len(geometry["columnWidths"]) == 19
    assert geometry["columnWidths"][8] > 0 and geometry["columnWidths"][9] > 0

    print(
        "measurements "
        + json.dumps(
            {
                "rows": [
                    {
                        "height": row["height"],
                        "totemCell": row["totemCell"],
                        "totemGroup": row["totemGroup"],
                        "titleCell": row["titleCell"],
                        "titleGroup": row["titleGroup"],
                        "brightRight5": {"totem": row["brightTotemRightPadding"], "title": row["brightTitleRightPadding"]},
                    }
                    for row in rows
                ],
                "noTitleRow": {
                    "height": geometry["emptyHeight"],
                    "totemCell": geometry["emptyTotemCell"],
                    "titleCell": geometry["emptyTitleCell"],
                    "brightRight5": {"totem": geometry["emptyTotemBrightRightPadding"], "title": geometry["emptyTitleBrightRightPadding"]},
                },
                "headers": geometry["headers"],
                "columnWidths": geometry["columnWidths"],
            },
            sort_keys=True,
        )
    )

    title_tip = tooltip["title"]
    rarity_tip = tooltip["rarity"]
    assert title_tip["count"] == rarity_tip["count"] == 1
    assert rarity_tip["sameElement"]
    assert title_tip["html"] == rarity_tip["html"]
    assert title_tip["text"] == rarity_tip["text"]
    assert "Epic Title" in title_tip["text"] and "Epic" in title_tip["text"]
    assert title_tip["icons"] == rarity_tip["icons"]
    assert any("titles/title-proven.svg" in icon for icon in title_tip["icons"])
    assert any("rarity/gem-epic.svg" in icon for icon in title_tip["icons"])
    assert "+25%" in title_tip["text"]
    assert "Read from plot data" not in title_tip["text"]


def test_vm_render_keeps_totem_then_title_at_separate_indexes_for_every_row():
    run_in_module_context(r"""
const base = {
 plot: '065-09-083', deed_uid: 'vm-plot', territory: 'Praetoria', terrain: 'Plains',
 resource: 'Grain', worksite_type: 'Farm', plot_status: 'occupied', magic_type: null,
 harvest_pp: 10, construction_pp: 20, base_pp: 30, base_pp_after_cap: 30,
 multiplier: 1, work_per_hour: 2, food_per_hour: 0, input_costs_per_hour: {},
 capacity: { display: '3' }, dec_needed: 0, bonus: { totem: null, title: null },
};
const rows = [
 base,
 { ...base, bonus: { totem: { rarity: 'common', boost: 0.1, name: 'Totem' }, title: null } },
 { ...base, bonus: { totem: null, title: { name: 'Title', boost: 0.1 } } },
 { ...base, bonus: { totem: { rarity: 'common', boost: 0.1, name: 'Totem' }, title: { name: 'Title', boost: 0.1 } } },
];
const rendered = rows.map(row => plotRowCellsHtml(row));
const totemIndexes = rendered.map(html => [...html.matchAll(/<td\b/g)].findIndex(match => html.slice(match.index).startsWith('<td class="totem-cell"')));
const titleIndexes = rendered.map(html => [...html.matchAll(/<td\b/g)].findIndex(match => html.slice(match.index).startsWith('<td class="title-cell"')));
if (totemIndexes.some(index => index !== 8) || titleIndexes.some(index => index !== 9)) throw new Error(`Totem/Title indexes drifted: ${totemIndexes}/${titleIndexes}`);
if (rendered.some(html => (html.match(/<td\b/g) || []).length !== 19)) throw new Error('every row must render 19 cells');
if (rendered.some(html => /totem-title-pair|totem-title-slot/.test(html))) throw new Error('paired Totem/Title wrapper survived');
""")


def test_title_icon_and_rarity_gem_render_adjacent_with_no_gap(live_browser: tuple[Browser, str]):
    """assert the real rendered
 rects touch, on his own 1536px width -- a CSS property proves nothing about
 whether two icons in different elements actually read as one pair."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1536, "height": 900})
    try:
        page.goto(f"{base_url}/?player=sample01", wait_until="domcontentloaded")
        page.wait_for_selector("tr.plot-row td.title-cell .title-icon", timeout=30_000)
        gap = page.evaluate(
            """() => {
 const rows = [...document.querySelectorAll('tr.plot-row')];
 for (const row of rows) {
 const titleIcon = row.querySelector('td.title-cell .title-icon');
 const rarityGem = row.querySelector('td.title-cell .rarity-gem');
 if (titleIcon && rarityGem) {
 const t = titleIcon.getBoundingClientRect();
 const r = rarityGem.getBoundingClientRect();
 return r.left - t.right;
 }
 }
 return null;
 }"""
        )
        assert gap is not None, "live board needs at least one row with both a title icon and a rarity gem"
        assert gap <= 0.5, f"title icon and rarity gem must touch, measured gap {gap}px"
    finally:
        page.close()
