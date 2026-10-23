""" (version B, ceres-table-redesign plan §3/§5, maintainer ): three sortable
material-input columns (Wood/hr, Stone/hr, Iron/hr) after Food/hr. String/regex-only DOM
guard (no browser) -- the live-render check belongs to a dedicated verification queue.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .vm_context import run_in_module_context

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "ceres.css").read_text(encoding="utf-8")
ROWS = (STATIC / "rows.js").read_text(encoding="utf-8")
TABLE = (STATIC / "table.js").read_text(encoding="utf-8")
BOARD = (STATIC / "board.js").read_text(encoding="utf-8")
BADGES = (STATIC / "badges.js").read_text(encoding="utf-8")
STATE = (STATIC / "state.js").read_text(encoding="utf-8")


def _thead():
    match = re.search(r'<thead[^>]*id="table-head-sticky"[^>]*>.*?</thead>', HTML, re.S)
    assert match, "could not find #table-head-sticky thead"
    return match.group(0)


def test_seventeen_headings_in_order_after_food():
    thead = _thead()
    # <th>-scoped, not a raw data-key search: an explanatory HTML comment above the Total PP
    # header also contains the literal string `data-key="harvest_pp"`.
    ths = re.findall(r'<th\b[^>]*>.*?</th>', thead, re.S)
    assert len(ths) == 20   # + the listed-plot counter ()
    keys = re.findall(r'data-key="([^"]+)"', thead)
    i = keys.index("food_per_hour")
    assert keys[i + 1:i + 4] == ["wood_cost_per_hour", "stone_cost_per_hour", "iron_cost_per_hour"]
    assert keys[i + 4] == "worker_count"
    assert keys.index("base_pp") < keys.index("totem_boost") < keys.index("multiplier")
    assert keys[-2:] == ["dec_needed", "lock_state"]   # the counting column after Lock has no sort key ()


def test_material_headers_are_sortable_and_labelled():
    thead = _thead()
    for key, label in (("wood_cost_per_hour", "Wood/hr"), ("stone_cost_per_hour", "Stone/hr"), ("iron_cost_per_hour", "Iron/hr")):
        th_match = re.search(rf'<th\b[^>]*data-key="{key}"[^>]*>.*?</th>', thead, re.S)
        assert th_match, f"missing sortable {key} header"
        assert label in th_match.group(0)
        assert '<span class="sort-chevron" aria-hidden="true"></span>' in th_match.group(0)


def test_colgroup_has_seventeen_cols_with_width_tier_classes():
    colgroup_match = re.search(r'<table class="plot-table" id="plot-table">\s*<colgroup>(.*?)</colgroup>', HTML, re.S)
    assert colgroup_match
    body = colgroup_match.group(1)
    assert len(re.findall(r"<col\b", body)) == 20
    assert 'col-w-wood' in body and 'col-w-stone' in body and 'col-w-iron' in body
    assert body.rstrip().endswith('<col class="col-w-dec"><col class="col-w-lock"><col class="col-w-count">')   # the counting column is last ()


def test_row_cells_render_seventeen_td_with_material_cells():
    fn_match = re.search(r"function plotRowCellsHtml\(row(?:, rowIndex = null)?\) \{.*?\n\}", ROWS, re.S)
    assert fn_match, "could not find plotRowCellsHtml()"
    body = fn_match.group(0)
    assert len(re.findall(r"<td\b", body)) == 20
    assert "materialCostCell(row, 'WOOD')" in body
    assert "materialCostCell(row, 'STONE')" in body
    assert "materialCostCell(row, 'IRON')" in body
    assert 'col-w-wood"' in body and 'col-w-stone"' in body and 'col-w-iron"' in body
    assert '<td class="lock-cell col-w-lock">${plotLockIconHtml(row)}</td>\n    <td class="count-cell col-w-count">${Number.isInteger(rowIndex) ? rowIndex + 1 : \'\'}</td>`;' in body


def test_colspan_uses_the_shared_column_count_constant():
    assert "export const PLOT_TABLE_COLUMN_COUNT = 20;" in STATE
    assert ROWS.count("td.colSpan = PLOT_TABLE_COLUMN_COUNT;") == 1
    assert TABLE.count("colSpan = PLOT_TABLE_COLUMN_COUNT;") == 2  # error row + virtual spacer row
    assert "colSpan = 15" not in ROWS and "colSpan = 15" not in TABLE


def test_material_columns_are_numeric_sortable_with_null_last():
    for key in ("wood_cost_per_hour", "stone_cost_per_hour", "iron_cost_per_hour"):
        assert f"S.SORT_KEY === '{key}'" in BOARD


def test_width_tier_hides_material_columns_below_1760_only():
    # Owner decision / plan §"1280px could either cut them out or remeasure"
    # MEASURED (maintainer review): the frozen sizes overflow the clipping scroller
    # with the three columns visible from 1440 up to ~1650 -- hidden through 1679
    # visible from 1680 up. A dedicated tier (index.html), not the reused-but-unrelated
    # ceres-lw-le-1659 tier.
    # review fix: the inline sort chevron's extra header width pushed this
    # reveal point out from 1680 to 1760 -- MEASURED headless with the chevron change
    # live. Tier renamed ceres-lw-le-1679 -> ceres-lw-le-1759 to match.
    rule = re.search(r"html\.ceres-lw-le-1759 \.col-w-wood,\s*\n\s*html\.ceres-lw-le-1759 \.col-w-stone,\s*\n\s*html\.ceres-lw-le-1759 \.col-w-iron \{ display: none; \}", CSS)
    assert rule, "expected a ceres-lw-le-1759 hide rule for .col-w-wood/.col-w-stone/.col-w-iron"
    HTML_SRC = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "root.classList.toggle('ceres-lw-le-1759',e<=1759);" in HTML_SRC
    # No width rule was added on the identity-hook classes themselves ( guard).
    assert re.search(r"\.col-w-(wood|stone|iron)\s*\{[^}]*width\s*:", CSS) is None


def test_material_cell_reuses_food_hr_shape_and_never_renders_zero_for_unknown():
    fn_match = re.search(r"export function materialCostCell\(row, symbol\) \{.*?\n\}", BADGES, re.S)
    assert fn_match, "could not find materialCostCell()"
    body = fn_match.group(0)
    assert "value === null || value === undefined" in body
    assert "'—'" in body
    assert "tooltipIconPath('resource'" in body
    # Fixed 2 decimals (plan §2: "0.00" means applicable and measured zero), NOT the shared
    # num() helper, which strips a whole number's trailing zeros (num(0, 2) -> "0").
    assert "toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })" in body


def test_material_cell_icon_uses_the_shared_clamp_not_the_14px_dec_icon():
    # Review fix: NOT .dec-stake-icon (fixed 14px) -- .material-cost-icon joins the
    # shared table icon clamp so it reads at the same size as every other table icon.
    fn_match = re.search(r"export function materialCostCell\(row, symbol\) \{.*?\n\}", BADGES, re.S)
    assert fn_match
    body = fn_match.group(0)
    assert 'class="material-cost-icon"' in body
    assert 'class="dec-stake-icon"' not in body
    clamp_rule = re.search(
        r"table\.plot-table td\.col-w-wood \.material-cost-icon,\s*\n"
        r"table\.plot-table td\.col-w-stone \.material-cost-icon,\s*\n"
        r"table\.plot-table td\.col-w-iron \.material-cost-icon",
        CSS,
    )
    assert clamp_rule, "expected .material-cost-icon in the  shared table icon clamp"


def test_a_measured_zero_renders_as_0_00_not_a_dash():
    _JS = r"""
    // `applicable` is the ordinary case -- a real recipe entry with a genuine
    // nonzero measured cost -- kept distinct from `zeroPp`, the zero-EFFECTIVE-PP case
    // (a real recipe entry whose measured cost happens to be exactly 0.0, e.g. base_pp == 0).
    // Both render "0.00"-shaped output (fixed 2 decimals), but they are not the same input.
    const applicable = materialCostCell({wood_cost_per_hour: 12.34}, 'WOOD');
    const zeroPp = materialCostCell({wood_cost_per_hour: 0.0}, 'WOOD');
    const missing = materialCostCell({wood_cost_per_hour: null}, 'WOOD');
    const absent = materialCostCell({}, 'WOOD');
    console.log('__CELL__' + JSON.stringify({
      applicableHtml: applicable.html, zeroPpHtml: zeroPp.html,
      missingHtml: missing.html, absentHtml: absent.html,
    }));
    """
    result = run_in_module_context(_JS)
    out = result.stdout.split('__CELL__', 1)
    assert len(out) == 2, result.stdout
    records = json.loads(out[1].splitlines()[0])
    assert records["applicableHtml"].startswith("12.34")
    assert records["zeroPpHtml"].startswith("0.00")
    assert records["missingHtml"] == "—"
    assert records["absentHtml"] == "—"


def test_material_columns_sort_nulls_last_in_both_directions():
    # Review fix (): 5,900 of 6,300 plots have no material input for a given column
    # ascending must start with the smallest REAL value, not a wall of dash rows. Unlike
    # every other numeric column, null sorts last in BOTH ascending and descending.
    _JS = r"""
    S.SORT_KEY = 'wood_cost_per_hour';
    const rows = [
      {deed_uid: 'a', wood_cost_per_hour: null},
      {deed_uid: 'b', wood_cost_per_hour: 5.0},
      {deed_uid: 'c', wood_cost_per_hour: null},
      {deed_uid: 'd', wood_cost_per_hour: 1.0},
    ];
    S.SORT_DIR = 1;
    const asc = sortRows(rows).map(r => r.deed_uid);
    S.SORT_DIR = -1;
    const desc = sortRows(rows).map(r => r.deed_uid);
    console.log('__SORT__' + JSON.stringify({asc, desc}));
    """
    result = run_in_module_context(_JS)
    out = result.stdout.split('__SORT__', 1)
    assert len(out) == 2, result.stdout
    records = json.loads(out[1].splitlines()[0])
    assert records["asc"] == ["d", "b", "a", "c"]
    assert records["desc"] == ["b", "d", "a", "c"]
