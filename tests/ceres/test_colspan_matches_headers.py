""" ( lows batch): the various `colSpan = PLOT_TABLE_COLUMN_COUNT`
assignments used for full-width error/spacer rows must always equal the actual
number of `<th>` cells in the live table header -- not just match the constant's
current literal value (test_material_columns.py already checks that). This
guard re-derives the header count directly from index.html so a future header
add/remove that forgets to bump state.js::PLOT_TABLE_COLUMN_COUNT is caught here.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
ROWS = (STATIC / "rows.js").read_text(encoding="utf-8")
TABLE = (STATIC / "table.js").read_text(encoding="utf-8")
STATE = (STATIC / "state.js").read_text(encoding="utf-8")


def _header_cell_count() -> int:
    match = re.search(r'<thead[^>]*id="table-head-sticky"[^>]*>.*?</thead>', HTML, re.S)
    assert match, "could not find #table-head-sticky thead"
    ths = re.findall(r'<th\b[^>]*data-key="[^"]+"[^>]*>.*?</th>|<th\b[^>]*col-w-count[^>]*>.*?</th>', match.group(0), re.S)   # + the keyless counting header ()
    return len(ths)


def _declared_column_count() -> int:
    match = re.search(r'export const PLOT_TABLE_COLUMN_COUNT = (\d+);', STATE)
    assert match, "PLOT_TABLE_COLUMN_COUNT declaration not found in state.js"
    return int(match.group(1))


def test_declared_column_count_matches_live_header_cell_count():
    assert _declared_column_count() == _header_cell_count()


def test_every_colspan_assignment_uses_the_shared_constant():
    # No full-width row anywhere hardcodes a numeric colSpan -- every one goes
    # through PLOT_TABLE_COLUMN_COUNT so it tracks header changes automatically.
    for src, label in ((ROWS, "rows.js"), (TABLE, "table.js")):
        for match in re.finditer(r'\.colSpan\s*=\s*([^;]+);', src):
            rhs = match.group(1).strip()
            assert rhs == "PLOT_TABLE_COLUMN_COUNT", (
                f"{label} sets colSpan from {rhs!r} instead of the shared constant"
            )
        assert "PLOT_TABLE_COLUMN_COUNT" in src, f"{label} does not import/use PLOT_TABLE_COLUMN_COUNT"
