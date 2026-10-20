""": the table must run under the badges bar instead of stopping short of it. His screenshot
showed an empty dark column between the DEC stake column and the bar (the old
`.table-scroll` end reserve) where the table's rows, zebra stripes and header stopped
short. This ticket removes that scroller-level reserve and moves the DEC gap
(/) into the last column's own end padding, so the row background,
zebra striping and border continue under the bar while the DEC numbers keep their
exact prior position. 's expand-row full-bleed override is simplified to
match, since the parent `td` no longer needs widening past its own cell.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
CERES_CSS = STATIC / "ceres.css"


def _rule_body(css: str, selector_open: str) -> str:
    return css.split(selector_open, 1)[1].split("}", 1)[0]


def test_table_scroll_no_longer_reserves_end_padding():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html:not(.ceres-lw-le-1279) .table-scroll {" in css
    rule = _rule_body(css, "html:not(.ceres-lw-le-1279) .table-scroll {")
    assert "padding-inline-end" not in rule
    # The shared custom property itself must still exist -- the last column
    # (below) is now its only consumer. (maintainer, verbatim, high: "all
    # three sliders must sit on the furthest right edge... the entire slide
    # bar must be thinner") changed the token expression from
    # `calc(var(--edge-w) + 1.25rem)` (this test's own STALE value, unfixed
    # since ) to `calc(var(--badge-bar-w) + var(--page-gutter, 0px) +
    # .75rem)` -- the bar shrank to its own dedicated, narrower width
    # (`--badge-bar-w`, not the handles' `--edge-w`) and moved to the true
    # page edge (`--page-gutter`, the gap the reserve must now also span).
    assert "--table-end-reserve: calc(var(--badge-bar-w) + var(--page-gutter, 0px) + .75rem);" in rule


def test_desktop_plot_table_cancels_both_board_gutters():
    css = CERES_CSS.read_text(encoding="utf-8")
    selector = "html:not(.ceres-lw-le-1279) #plot-table-scroll {"
    assert selector in css
    rule = _rule_body(css, selector)
    assert "margin-inline: -0.5rem;" in rule
    assert "inline-size: calc(100% + 1rem);" in rule
    assert "max-width: none;" in rule
    assert "padding-inline-end" not in rule
    table_selector = "html:not(.ceres-lw-le-1279) #plot-table-scroll > table.plot-table {"
    assert table_selector in css
    table_rule = _rule_body(css, table_selector)
    assert "margin-inline: -0.5px;" in table_rule
    assert "inline-size: calc(100% + 8.5px);" in table_rule


def test_last_column_padding_rule_exists_for_th_and_plot_row_td():
    """Separate `th`/`td` rules, not a combined selector. : the chevron moved
    into normal flow after the label text, so it no longer needs its own reserved
    end-of-cell slot -- the th half's formula now matches the td half's exactly
    (both are just `--table-end-reserve` plus the column's own fluid padding)."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html:not(.ceres-lw-le-1279) table.plot-table thead th:last-child {" in css
    th_rule = css.split(
        "html:not(.ceres-lw-le-1279) table.plot-table thead th:last-child {", 1
    )[1].split("}", 1)[0]
    assert (
        "padding-inline-end: calc(var(--table-end-reserve) + clamp(.25rem, .3125cqi, .375rem));"
        in th_rule
    )
    assert "+ .6rem" not in th_rule
    assert "html:not(.ceres-lw-le-1279) table.plot-table tbody tr.plot-row td:last-child {" in css
    td_rule = css.split(
        "html:not(.ceres-lw-le-1279) table.plot-table tbody tr.plot-row td:last-child {",
        1,
    )[1].split("}", 1)[0]
    assert (
        "padding-inline-end: calc(var(--table-end-reserve) + clamp(.25rem, .3125cqi, .375rem));"
        in td_rule
    )


def test_last_column_rule_scoped_away_from_spacer_and_expand_rows():
    """The gap belongs only to real header cells and real body rows -- it must not
    reach `tr.virtual-spacer` (zero-padding spacer rows) or `tr.expand-row` (the
    full-bleed detail cell, which already spans the whole width on its own terms)."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "tr.virtual-spacer td:last-child" not in css
    assert "tr.expand-row td:last-child" not in css


def test_expand_row_slot_editor_is_plain_full_width():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html:not(.ceres-lw-le-1279) table.plot-table tbody tr.expand-row td > .slot-editor {" in css
    rule = _rule_body(
        css,
        "html:not(.ceres-lw-le-1279) table.plot-table tbody tr.expand-row td > .slot-editor {",
    )
    assert "inline-size: 100%;" in rule
    assert "calc(100% + var(--table-end-reserve))" not in rule
    assert "max-inline-size: none;" in rule


def test_last_column_chevron_has_no_inset_override():
    """: the chevron is in normal flow, not absolutely positioned, so the
    last column no longer needs its own inset override to carry the reserve --
    it sits after the label text like every other column's chevron."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "thead th:last-child .sort-chevron" not in css
