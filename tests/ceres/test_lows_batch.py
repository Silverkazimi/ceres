""" ( lows batch, post-release /682 findings, aletheia-0-9-681-682):

1. regional-dec.js close() must clear the inline top/right/maxHeight positionDesktopPanel()
   wrote, so a desktop->mobile resize before the next open never inherits a stale desktop
   position on the full-height mobile drawer. positionDesktopPanel() itself must also clear
   them when a resize crosses into the mobile tier WHILE the panel stays open (not closed).
2. ceres/board.py's lean `GET /api/board` row drops `input_costs_per_hour` (no frontend read)
   -- executed here via project_board(lean=True/False) against a real fixture plot with a
   resource_recipe, plus a source guard on `_LEAN_DEAD_KEYS`.
3. Stale "15 columns"/"1440" comments in index.html and state.js corrected to
   18/PLOT_TABLE_COLUMN_COUNT and 1680/-1679.
4. harvest.py's duplicate-symbol recipe fix is covered by
   tests/ceres/test_resource_inputs_per_hour.py::test_duplicate_symbol_recipe_entries_keep_their_own_per_entry_value
   (board.py's per-symbol SUM vs. harvest.py's per-entry rows).
5. tests/ceres/test_material_columns.py's zeroPp case fix is covered there directly.
"""
from __future__ import annotations

import re
from pathlib import Path

from ceres.board import project_board
from tests.ceres.conftest import build_details_by_id, build_report

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
REGIONAL_DEC_JS = (STATIC / "regional-dec.js").read_text(encoding="utf-8")
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
STATE_JS = (STATIC / "state.js").read_text(encoding="utf-8")
BOARD_PY = (ROOT / "ceres" / "board.py").read_text(encoding="utf-8")


def _close_fn():
    match = re.search(r"async function close\(\) \{.*?\n\}\n", REGIONAL_DEC_JS, re.S)
    assert match, "close() source shape changed -- update this test's slice"
    return match.group(0)


def test_close_clears_the_inline_desktop_panel_styles():
    body = _close_fn()
    assert "drawer.style.top = '';" in body
    assert "drawer.style.right = '';" in body
    assert "drawer.style.maxHeight = '';" in body
    # The clear happens after drawer.close(), not before (so it doesn't affect the closing
    # animation, which reads the panel's current transform, not these dialog-level styles).
    close_call = body.index("drawer.close();")
    top_clear = body.index("drawer.style.top = '';")
    assert close_call < top_clear


def test_position_desktop_panel_clears_styles_on_a_stay_open_desktop_to_mobile_resize():
    # review fix: a resize that crosses into the mobile tier WHILE the panel stays
    # open (never closed) still runs positionDesktopPanel() via the 'resize' listener
    # it must clear the styles itself in that case, not only leave it to close(). This is
    # a DIFFERENT path from close(): here the drawer is still open when we get here.
    fn_match = re.search(r"function positionDesktopPanel\(\) \{.*?\n\}\n", REGIONAL_DEC_JS, re.S)
    assert fn_match, "positionDesktopPanel source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "if (!drawer || !drawer.open) return;" in body
    assert "if (!isDesktopPanel()) {" in body
    not_desktop_branch = body[body.index("if (!isDesktopPanel()) {"):]
    assert "drawer.style.top = '';" in not_desktop_branch
    assert "drawer.style.right = '';" in not_desktop_branch
    assert "drawer.style.maxHeight = '';" in not_desktop_branch
    # The clear-and-return happens BEFORE any read off #wallet-picker's live rect (that
    # element/positioning math is desktop-only and must never run on the mobile tier).
    assert not_desktop_branch.index("return;") < not_desktop_branch.index("getElementById('wallet-picker')")


def test_lean_board_row_drops_input_costs_per_hour_source_guard():
    match = re.search(r'_LEAN_DEAD_KEYS = \(([^)]*)\)', BOARD_PY)
    assert match, "_LEAN_DEAD_KEYS declaration not found"
    assert '"input_costs_per_hour"' in match.group(1)


def test_lean_board_row_omits_input_costs_per_hour_dict_but_keeps_the_flat_keys():
    """Executed, not just a source guard: build a real board row (fixture plot 0 has a
    genuine resource_recipe and total_base_pp_after_cap -- see test_resource_inputs_per_hour.py
    for the underlying formula) and check what `project_board` actually returns for both
    `lean=True` (GET /api/board) and `lean=False` (GET /api/plots/{deed_uid})."""
    report = build_report()
    deed_uid = report["plots"][0]["deed_uid"]
    details = build_details_by_id()

    lean_row = next(r for r in project_board(report, details, lean=True)["plots"] if r["deed_uid"] == deed_uid)
    full_row = next(r for r in project_board(report, details, lean=False)["plots"] if r["deed_uid"] == deed_uid)

    # The fixture plot's own recipe (WOOD/STONE/IRON) makes wood_cost_per_hour a real
    # number, not None/absent -- otherwise this test would pass vacuously.
    assert full_row.get("wood_cost_per_hour") is not None

    assert "input_costs_per_hour" not in lean_row
    assert lean_row.get("wood_cost_per_hour") == full_row.get("wood_cost_per_hour")
    assert lean_row.get("stone_cost_per_hour") == full_row.get("stone_cost_per_hour")
    assert lean_row.get("iron_cost_per_hour") == full_row.get("iron_cost_per_hour")

    assert "input_costs_per_hour" in full_row
    assert full_row["input_costs_per_hour"] == {
        "WOOD": full_row["wood_cost_per_hour"],
        "STONE": full_row["stone_cost_per_hour"],
        "IRON": full_row["iron_cost_per_hour"],
    }


def test_index_html_column_count_comment_matches_the_table():
    # the table comment names the real column count: PLOT_TABLE_COLUMN_COUNT and the <th> cells of the plot table
    count = int(re.search(r"export const PLOT_TABLE_COLUMN_COUNT = (\d+);", STATE_JS).group(1))
    table = INDEX_HTML[INDEX_HTML.index('<table class="plot-table"'):]
    head = re.sub(r"", "", table[:table.index("</thead>")], flags=re.S)
    assert len(re.findall(r"<th\b", head)) == count
    stated = re.findall(r"\ball (\d+) columns\b", INDEX_HTML)
    assert stated == [str(count)], stated
    assert "back to 15 columns" not in INDEX_HTML and "back to 17 columns" not in INDEX_HTML


def test_index_html_material_column_breakpoint_comment_says_1760():
    # review fix raised this from 1680 to 1760 (inline sort chevron's extra
    # header width pushed the material-columns overflow point out).
    assert "hide below 1440" not in INDEX_HTML
    assert "hide below 1760" in INDEX_HTML


def test_state_js_column_count_comment_names_the_1759_tier():
    assert "ceres-lw-le-1439" not in STATE_JS
    assert "ceres-lw-le-1759" in STATE_JS
