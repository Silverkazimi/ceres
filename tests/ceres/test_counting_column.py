""": a counting column (last: right of the locks) numbers every LISTED plot, its header shows the total, it is not sortable, and the header lock matches the row locks."""
import re
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
ROWS = (STATIC / "rows.js").read_text(encoding="utf-8")
TABLE = (STATIC / "table.js").read_text(encoding="utf-8")
CSS = (STATIC / "ceres.css").read_text(encoding="utf-8")


def test_the_counting_header_is_the_last_cell_has_no_sort_key_and_shows_the_total():
    thead = re.search(r'<thead[^>]*id="table-head-sticky"[^>]*>.*?</thead>', HTML, re.S).group(0)
    ths = re.findall(r"<th\b[^>]*>.*?</th>", thead, re.S)
    assert "col-w-count" in ths[-1] and "data-key" not in ths[-1].split(">", 1)[0] and 'class="count-total"' in ths[-1]
    assert "lock_state" in ths[-2]                                                            # directly right of the locks
    assert '<col class="col-w-lock"><col class="col-w-count">' in HTML


def test_every_row_shows_its_position_in_the_listed_rows_and_the_header_total_follows_the_filters():
    assert 'class="count-cell col-w-count">${Number.isInteger(rowIndex) ? rowIndex + 1 : \'\'}</td>' in ROWS
    assert "tr.dataset.rowIndex = String(rowIndex)" in ROWS                                   # a re-rendered row keeps its number
    assert "plotRowCellsHtml(row, Number.isInteger(listedIndex) ? listedIndex : null)" in ROWS
    assert "listedTotal.textContent = String(CURRENT_ROWS.length)" in TABLE                   # CURRENT_ROWS = the filtered and sorted list


def test_header_and_row_numbers_share_one_right_edge_and_the_header_lock_matches_the_row_locks():
    assert "table.plot-table :is(th.col-w-count, td.count-cell) { text-align: end;" in CSS
    assert "color: var(--muted)" not in CSS[CSS.index("table.plot-table :is(th.col-w-count, td.count-cell)"):CSS.index("table.plot-table td.count-cell { color: var(--muted); }")]   # the header cell takes the global header colour
    assert "html:not(.ceres-lw-le-1279) table.plot-table thead tr th.col-w-count { padding-inline-end: calc(var(--table-end-reserve)" in CSS   # same end padding as the cells
    assert 'table.plot-table thead th[data-key="lock_state"] .plot-lock-icon svg { inline-size: 1.1rem; block-size: 1.1rem; display: block; stroke-width: 2.6; }' in CSS
    assert "table.plot-table td.lock-cell .plot-lock-icon svg { inline-size: 1.1rem; block-size: 1.1rem; }" in CSS                             # the row lock size it matches
