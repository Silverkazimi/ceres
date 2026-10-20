"""an expanded plot's details/picker must paint fully over the badges bar
(and any of its own open row slide-outs) instead of being squeezed by the table's own
end reserve for that bar.
See ceres/static/ceres.css's own comment (next to `table.plot-table tbody
tr.expand-row td`) for the full design.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
CERES_CSS = STATIC / "ceres.css"


def _rule_body(css: str, selector_open: str) -> str:
    return css.split(selector_open, 1)[1].split("}", 1)[0]


def _numeric_z_index(rule_body: str) -> int:
    match = re.search(r"z-index:\s*(-?\d+)\s*;", rule_body)
    assert match, f"no numeric z-index found in rule: {rule_body!r}"
    return int(match.group(1))


def _badge_bar_positioning_rule(css: str) -> str:
    """`.badge-bar {` appears more than once ( split the shared resting-state
 chrome from `.edge-picker` into its OWN standalone `.badge-bar { border: 0; ... }`
 rule) -- this returns the block that carries `position: absolute` (the geometry
 rule this ticket's ordering rules touch). z-fix round 2: this rule
 carries NO numeric z-index at all (`z-index: auto`) -- its own `backdrop-filter`
 (the thing that used to force a stacking context regardless of z-index) moved to
 `.badge-bar::before` instead, so `.badge-bar` itself never forces one, and its
 real children (.badge-bar-head/.badge-bar-row) compete directly against the real
 thead/expand-row td in the scroller's own shared context."""
    for match in re.finditer(r"(?:^|\n)\.badge-bar \{(.*?)\n\}\n", css, re.S):
        if "position: absolute;" in match.group(1):
            return match.group(1)
    raise AssertionError("no .badge-bar geometry rule found")


def test_table_end_reserve_is_one_shared_custom_property():
    """`.table-scroll` no longer applies the reserve as its own end padding
 (the table now runs under the bar) -- the property still exists as the single
 source of truth, now consumed by the last column's own padding instead (see
 test_edge_tabs.py::test_last_column_carries_the_reserve_gap)."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html:not(.ceres-lw-le-1279) .table-scroll {" in css
    reserve_rule = _rule_body(css, "html:not(.ceres-lw-le-1279) .table-scroll {")
    # the
    # reserve now tracks the bar's own (narrower) `--badge-bar-w` plus the measured
    # `--page-gutter` (the bar's containing block no longer coincides with the
    # table's own, since it moved out to `<body>`), not `--edge-w`.
    assert "table-end-reserve: calc(var(--badge-bar-w) + var(--page-gutter, 0px) + .75rem);" in reserve_rule
    assert "padding-inline-end: var(--table-end-reserve);" not in reserve_rule
    # No stray hardcoded duplicate of the reserve formula left un-tokenised elsewhere.
    assert "padding-inline-end: calc(var(--edge-w)" not in css


def test_expand_row_td_stacks_above_the_bar_and_overlay():
    css = CERES_CSS.read_text(encoding="utf-8")
    # z-fix round 2 restored this to its ORIGINAL value (8) -- round 1's
    # `11` was a disclosed trade-off needed only because `.badge-bar` itself had
    # been given a real z-index of 10 at the time; that need is gone now that the
    # bar's own children carry their own real z-index values instead.
    assert "html:not(.ceres-lw-le-1279) table.plot-table tbody tr.expand-row td { position: relative; z-index: 8; }" in css
    assert "z-index: auto;" in _badge_bar_positioning_rule(css)
    row_rule = _rule_body(css, ".badge-bar-row {")
    assert "z-index: 7;" in row_rule
    overlay_rule = _rule_body(css, ".badge-overlay {")
    assert "z-index: 5;" in overlay_rule


def test_expand_body_grows_full_bleed_over_the_reserve_and_is_not_reclamped():
    """the parent `td` now already spans the table's full width (the
 `.table-scroll` end reserve that used to narrow it is gone), so `.slot-editor`
 only needs plain `inline-size: 100%` to cover the bar -- no widening past its
 parent cell needed any more. `max-inline-size: none` stays so this rule does not
 depend on cascade order with the unconditional `.slot-editor { max-inline-size:
 100% }` rule elsewhere in this file."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html:not(.ceres-lw-le-1279) table.plot-table tbody tr.expand-row td > .slot-editor {" in css
    rule = _rule_body(css, "html:not(.ceres-lw-le-1279) table.plot-table tbody tr.expand-row td > .slot-editor {")
    assert "inline-size: 100%;" in rule
    assert "max-inline-size: none;" in rule
    assert "box-sizing: border-box;" in rule
    assert "background: var(--bg2);" in rule
    # The bare `.slot-editor` rule elsewhere must still exist (untouched by this
    # override for every OTHER context) and must still clamp to 100% there.
    assert ".slot-editor { inline-size: 100%; max-inline-size: 100%; min-inline-size: 0; }" in css


def test_no_change_to_the_plot_row_itself():
    """Owner's spec item 3: only the EXPANSION covers the bar -- the plot row (the
 header the picker opens from) must stay visible above it, unchanged."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "table.plot-table tbody tr.plot-row { z-index" not in css
    assert "tr.plot-row td { position: relative; z-index: 8" not in css


def test_full_z_index_stack_order_is_correct():
    """ z-fix, ROUND 2. Round 1's diagnosis was correct -- `.badge-bar` carries
 `backdrop-filter`, which forces its own stacking context REGARDLESS of z-index,
 so nothing nested inside it could ever escape that forced context to out-rank a
 sibling context like the real thead, proven empirically (forcing the head's own
 z-index to 99999 inline changed nothing; giving `#badge-bar` itself a real,
 non-auto z-index fixed the hit-test instantly) -- but round 1's FIX (raise the
 whole bar's own z-index above the thead) created a genuine cycle with 's
 two existing requirements (bar > thead, expand-row td > bar, thead > expand-row
 td cannot all hold with flat z-index values) and was accepted as a disclosed
 trade-off for one release.
 ROUND 2 removes the cycle at the SOURCE instead: `background`/`backdrop-filter`
 (the actual forcing culprit) moved off `.badge-bar` onto a dedicated
 `.badge-bar::before` pseudo-element -- `.badge-bar` itself now carries NEITHER,
 so it no longer forces a stacking context at all, and its real children
 (`.badge-bar-head`/`.badge-bar-row`) now compete DIRECTLY against the real
 thead/expand-row td, each with its own REAL z-index. Full chain, no cycle:
 head (10) > thead (9) > expand-row td (8) > row (7) > glass (6) > overlay (5).
 `tr.expand-row td` is back to its ORIGINAL value (8) -- the "real thead
 never disappears under an expanded plot" guarantee is fully restored, no
 trade-off left."""
    css = CERES_CSS.read_text(encoding="utf-8")
    head_rule = _rule_body(css, ".badge-bar-head {")
    thead_rule = _rule_body(css, "thead.table-head-sticky {")
    expand_rule = _rule_body(css, "html:not(.ceres-lw-le-1279) table.plot-table tbody tr.expand-row td {")
    row_rule = _rule_body(css, ".badge-bar-row {")
    before_rule_match = re.search(r"(?:^|\n)\.badge-bar::before \{(.*?)\n\}\n", css, re.S)
    assert before_rule_match, "standalone .badge-bar::before rule not found"
    before_rule = before_rule_match.group(1)
    overlay_rule = _rule_body(css, ".badge-overlay {")
    bar_rule = _badge_bar_positioning_rule(css)

    head_z = _numeric_z_index(head_rule)
    thead_z = _numeric_z_index(thead_rule)
    expand_z = _numeric_z_index(expand_rule)
    row_z = _numeric_z_index(row_rule)
    glass_z = _numeric_z_index(before_rule)
    overlay_z = _numeric_z_index(overlay_rule)

    assert head_z == 10
    assert thead_z == 9
    assert expand_z == 8
    assert row_z == 7
    assert glass_z == 6
    assert overlay_z == 5
    # The full chain, no cycle.
    assert head_z > thead_z > expand_z > row_z > glass_z > overlay_z
    # .badge-bar itself carries no numeric z-index at all (its own glass moved to
    # before, so it never forces a stacking context in the first place).
    assert "z-index: auto;" in bar_rule
