""": badges leave the table's own sizing -- a fixed strip replaces the resizing
Badges column, and an overlay panel (badge-overlay.js) shows the full per-row badge
list aligned with the table while scrolling. See library/ceres-badge-overlay.md and
work/notes/PLAN-S13840-badge-overlay-2026-09-19.md for the maintainer-approved design.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .vm_context import run_in_module_context

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
INDEX_HTML = STATIC / "index.html"
ROWS_JS = STATIC / "rows.js"
TABLE_JS = STATIC / "table.js"
BADGES_JS = STATIC / "badges.js"
APP_JS = STATIC / "app.js"
CERES_CSS = STATIC / "ceres.css"
BADGE_OVERLAY_JS = STATIC / "badge-overlay.js"

# Plot, Rarity, Type, Terrain, Worksite, Total PP, Boostable, Base PP, the combined
# Totem/Title cell (with two sort controls), Boost, Harvest/hr, Food/hr, Wood/hr
# Stone/hr, Iron/hr, Workers, DEC stake. amendment: the badge strip is no
# longer a table column at all (see #badge-bar in index.html/ceres-badge-overlay.md
# v3) -- the table now ends at DEC stake, no trailing header.
EXPECTED_KEY_ORDER = [
    "plot", "rarity_rank", "plot_status_rank", "terrain", "resource",
    "harvest_pp", "construction_pp", "base_pp", "totem_boost", "title_boost",
    "multiplier", "work_per_hour", "food_per_hour",
    # three material-input columns after Food/hr (see test_material_columns.py).
    "wood_cost_per_hour", "stone_cost_per_hour", "iron_cost_per_hour",
    "worker_count", "dec_needed",
    # the plot-lock column is the last board column.
    "lock_state",
    # the listed-plot counter is the last column, not sortable (no data-key; named here by its class).
    "listed_count",
]


def _thead_row_html() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(r'<thead class="table-head-sticky[^>]*>.*?<tr>(.*?)</tr>\s*</thead>', html, re.S)
    assert match, "could not find #plot-table thead row"
    return match.group(1)


def test_thead_column_order_matches_the_plan():
    row = _thead_row_html()
    ths = re.findall(r"<th\b[^>]*>.*?</th>", row, re.S)
    keys = []
    for th in ths:
        key_match = re.search(r'data-key="([^"]+)"', th.split(">", 1)[0])
        if key_match:
            keys.append(key_match.group(1))
        elif "col-w-count" in th.split(">", 1)[0]:
            keys.append("listed_count")
        else:
            keys.extend(re.findall(r'<button\b[^>]*data-key="([^"]+)"', th) or [None])
    assert keys == EXPECTED_KEY_ORDER, keys
    # amendment: the badge strip is no longer a table column at all (moved to
    # the standalone #badge-bar, see test_edge_tabs.py) -- the thead has no
    # trailing header/glyph any more and every <th> carries a real sort key.
    assert 'id="badge-overlay-toggle"' not in row
    assert "badge-strip-head" not in row
    assert None not in keys
    assert "totem_boost" in row and "title_boost" in row


def test_plot_row_cells_html_emits_cells_in_the_same_order():
    rows = ROWS_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function plotRowCellsHtml\(row(?:, rowIndex = null)?\) \{.*?\n\}\n", rows, re.S)
    assert fn_match, "plotRowCellsHtml source shape changed -- update this test's slice"
    body = fn_match.group(0)
    rendered = body.split("return `", 1)[1]
    # Field-identifying substrings in the order they must appear as <td> content.
    markers = [
        "formatPlotLabel(row.plot)", "plotRarityCellHtml(row)", "typeMarkHtml(",
        "terrainCell.html", "worksiteCellHtml(row)", "num(row.harvest_pp", "num(row.construction_pp", "fmtBasePp(row)",
        '<td class="totem-cell">', '<td class="title-cell"',
        "row.multiplier", "fmtWorkPerHour(row)", "foodCell.html", "row.capacity.display",
        "decStake.html", 'class="lock-cell col-w-lock"', 'class="count-cell col-w-count"',
    ]
    positions = [rendered.index(marker) for marker in markers]
    assert positions == sorted(positions), "plotRowCellsHtml cell order drifted from the plan"
    assert 'class="totem-cell"' in body
    assert 'class="title-cell"' in body
    # amendment: the table has no badge column any more -- its content moved to
    # #badge-bar (badge-overlay.js::syncBadgeOverlay).
    assert "badge-strip-cell" not in body
    assert "badgeStripHtml(row)" not in body
    assert "badgeHtml(row)" not in body, "the table cell must never render a badge list of its own"


def test_refresh_path_updates_the_bar_row_and_syncs_the_overlay():
    rows = ROWS_JS.read_text(encoding="utf-8")
    # amendment: refreshChangeBadges() now targets #badge-bar's own per-row
    # element (only present while that deed's <tr> is mounted), not a table <td>.
    assert "document.querySelector(`#badge-bar .badge-bar-row" in rows
    assert "cell.innerHTML = badgeStripHtml(row);" in rows
    assert "syncBadgeOverlay()" in rows
    assert "import { syncBadgeOverlay } from './badge-overlay.js';" in rows


def test_mobile_card_projection_is_untouched():
    rows = ROWS_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"export function plotCardHtml\(row, rowIndex\) \{.*?\n\}\n", rows, re.S)
    assert fn_match, "plotCardHtml source shape changed -- update this test's slice"
    assert "badgeHtml(row)" in fn_match.group(0)


def test_app_wires_the_overlay_and_table_dispatches_layout_events():
    app = APP_JS.read_text(encoding="utf-8")
    assert "import { initBadgeOverlay } from './badge-overlay.js';" in app
    assert "ready(initBadgeOverlay);" in app
    table = TABLE_JS.read_text(encoding="utf-8")
    # amendment (pre-landing review fix): the event now carries a reason
    # ('scroll' | 'layout') so badge-overlay.js can skip a no-op scroll frame.
    assert "new CustomEvent('ceres:table-layout', { detail: { reason } })" in table
    assert "notifyTableLayout();" in table


def test_plot_table_scroll_is_the_overlay_containing_block():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "  container: ceres-table / inline-size;\n  position: relative; /* : containing block for #badge-overlay */\n}" in css


def test_badge_overlay_module_exports_init_and_sync():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "export function initBadgeOverlay()" in js
    # amendment (pre-landing review fix): takes an optional `reason` so a
    # scroll-only frame can be skipped while the panel is closed.
    assert "export function syncBadgeOverlay(reason)" in js
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "window.addEventListener('ceres:table-layout'" in js
    # Item 1 (pre-landing review, second pass): a row carrying real server badge codes
    # renders them immediately, loaded or not; badgeRosterLoaded() (the honest per-row
    # `assets_loaded` signal) is consulted only when the code list is empty, never the
    # client-only `_badge_roster_loaded` flag an unexpanded row never sets.
    assert "import { badgeCodesFor, badgeHtml, badgeRosterLoaded, badgeStatusGlyph, badgeStripHtml, badgeStripStatus, cooldownChangeEntries, unsignedChangeCount } from './badges.js';" in js
    assert "!badgeRosterLoaded(row)" in js
    assert "_badge_roster_loaded" not in js
    assert "assets_not_loaded" not in js
    # Item 2 (pre-landing review): the per-row signature also covers the maintainer's own
    # staged-plan counts and the region DEC badge, neither of which flows through
    # badgeCodesFor()'s code list.
    assert "unsignedChangeCount(row.deed_uid)" in js
    assert "cooldownChangeEntries(row.deed_uid).length" in js
    assert "row.region_efficiency" in js
    assert "BOARD_GENERATION" in js
    # Item 3 (pre-landing review, perf): innerHTML is reassigned only when the row's
    # badge content actually changed, via a cheap per-row signature.
    assert "overlayRow.dataset.sig" in js
    assert "if (overlayRow.dataset.sig !== sig) {" in js
    # the shared-panel width measurement is gone -- each row now
    # sizes itself via plain CSS (max-content), nothing to measure any more.
    assert "scrollWidth" not in js


def test_measure_mounted_row_heights_only_measures_tr_elements():
    table = TABLE_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function measureMountedRowHeights\(\) \{.*?\n\}\n", table, re.S)
    assert fn_match, "measureMountedRowHeights source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "badge-overlay" not in body
    assert "nodes[0]?.getBoundingClientRect()" in body


def test_strip_dots_and_wrapping_css_guards():
    css = CERES_CSS.read_text(encoding="utf-8")
    # amendment: the fixed-width indicator is #badge-bar now, not a table cell
    # (see test_edge_tabs.py for its own geometry guards).
    assert ".badge-bar {" in css
    assert "inline-size: 2.25rem;" in css
    assert ".badge-status-glyph.is-critical" in css
    assert ".badge-status-glyph.is-warning" in css
    assert ".badge-status-glyph.is-pending" in css
    assert ".badge-status-glyph.is-loading" in css
    assert ".badge-status-glyph.is-clean" in css
    assert ".badge-overlay {" in css
    assert ".badge-overlay-row {" in css
    # each row is now its own slide-out and stays pointer-events: none in
    # BOTH states -- only its badge chips re-enable hit-testing while open (see
    # test_row_slideouts.py for the full per-row CSS guards).
    assert "pointer-events: none;" in css.split(".badge-overlay-row {", 1)[1].split("}", 1)[0]
    assert ".badge-overlay-row.is-open .badge-row > *, .badge-overlay-row.is-open [data-tip] { pointer-events: auto; }" in css


def test_strip_severities_pure_function_ordering_and_dedup():
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
const pending = badgeStripStatus({deed_uid:'a', assets_loaded:true, badges:['over_cap'], pending_change:{stalled:false}, capacity:{}, workers:[], bonus:{}});
const critical = badgeStripStatus({deed_uid:'b', assets_loaded:true, badges:['over_cap'], capacity:{}, workers:[], bonus:{}});
const warning = badgeStripStatus({deed_uid:'c', assets_loaded:true, badges:['underfilled'], capacity:{worker_count:5, open_slots:2, card_capacity:5}, workers:[], bonus:{}});
const loading = badgeStripStatus({deed_uid:'d', assets_loaded:false, badges:[], capacity:{}, workers:[], bonus:{}});
const clean = badgeStripStatus({deed_uid:'e', assets_loaded:true, badges:[], capacity:{}, workers:[], bonus:{}});
assert([pending.kind, critical.kind, warning.kind, loading.kind, clean.kind].join(',') === 'pending,critical,warning,loading,clean', 'status kinds follow the priority order');
""",
        entry=BADGES_JS,
    )

def test_badge_strip_html_states_via_source_guard():
    js = BADGES_JS.read_text(encoding="utf-8")
    assert "export function badgeStripHtml(row) {" in js
    assert "const status = badgeStripStatus(row);" in js
    assert "badgeStatusGlyph(status.kind)" in js
    assert 'aria-label="${esc(status.label)}"' in js
    for kind in ("pending", "critical", "warning", "loading", "clean"):
        assert f'is-{kind}' in js
    assert "<i class=\"badge-dot" not in js

def test_badge_strip_html_states_in_a_vm() -> None:
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
const pending = badgeStripHtml({deed_uid:'p', assets_loaded:true, badges:['over_cap'], pending_change:{stalled:false}, capacity:{}, workers:[], bonus:{}});
const critical = badgeStripHtml({deed_uid:'c', assets_loaded:true, badges:['over_cap'], capacity:{}, workers:[], bonus:{}});
const warning = badgeStripHtml({deed_uid:'w', assets_loaded:true, badges:['underfilled'], capacity:{worker_count:5, open_slots:2, card_capacity:5}, workers:[], bonus:{}});
const loading = badgeStripHtml({deed_uid:'l', assets_loaded:false, badges:[], capacity:{}, workers:[], bonus:{}});
const clean = badgeStripHtml({deed_uid:'n', assets_loaded:true, badges:[], capacity:{}, workers:[], bonus:{}});
for (const [html, state] of [[pending,'pending'],[critical,'critical'],[warning,'warning'],[loading,'loading'],[clean,'clean']]) {
  assert(html.includes(`badge-status-glyph is-${state}`), `${state} status glyph expected`);
  assert(html.includes('aria-label='), `${state} status must be accessible`);
  assert(!html.includes('badge-dot'), 'status must use one glyph, not dots');
}
""",
        entry=BADGES_JS,
    )