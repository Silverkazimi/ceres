""" worker-tile tags sit in one top-left row; a tile wears at most one full-width band, by priority."""

from __future__ import annotations

from pathlib import Path

from .vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"

_BAND_PRIORITY = r"""
const B = (k) => `<span class="slot-wait-badge is-${k}">${k}</span>`;
const order = ['failed', 'removal', 'unpowered', 'waiting', 'moving', 'planned', 'ready'];
if (JSON.stringify(TILE_BAND_PRIORITY) !== JSON.stringify(order)) process.exit(10);
// every state present: the highest wins, exactly one band
const all = Object.fromEntries(order.map((k) => [k, B(k)]));
for (let i = 0; i < order.length; i += 1) {
 const states = Object.fromEntries(order.slice(i).map((k) => [k, B(k)]));
 const out = tileBand(states);
 if (out !== B(order[i])) process.exit(20 + i);
 if ((out.match(/slot-wait-badge/g) || []).length !== 1) process.exit(30 + i);
}
if (tileBand({ unpowered: B('unpowered'), waiting: B('waiting'), planned: B('planned') }) !== B('unpowered')) process.exit(40);
if (tileBand({ removal: B('removal'), unpowered: B('unpowered') }) !== B('removal')) process.exit(41);
if (tileBand({ waiting: '', planned: B('planned') }) !== B('planned')) process.exit(42);
if (tileBand({}) !== '' || tileBand({ waiting: '' }) !== '') process.exit(43);
// the tag row: one container, empty parts dropped, nothing when there are no tags
if (tileTagsHtml('', '') !== '') process.exit(50);
const row = tileTagsHtml('<span class="slot-kin-badges"><span class="slot-kin-badge">a</span></span>', '', '<span class="slot-flag slot-flag-cooldown">c</span>');
if (!row.startsWith('<div class="slot-tags">') || (row.match(/slot-tags/g) || []).length !== 1 || !row.includes('slot-flag-cooldown')) process.exit(51);
"""


def test_one_band_by_priority_and_one_tag_row() -> None:
    run_in_module_context(_BAND_PRIORITY)


def test_tile_markup_has_one_band_slot_and_the_tag_row() -> None:
    editor = (STATIC / "editor.js").read_text(encoding="utf-8")
    tile = editor[editor.index("const tile = `<div class=\"slot-tile ${shapeClass}"):][:2600]
    assert "${band}" in tile and "${tags}" in tile
    for gone in ("${waitBadge}", "${coolingFlag}", "${unpoweredFlag}", "${plannedReturnChip("):
        assert gone not in tile  # every band and tag goes through tileBand() / tileTagsHtml()
    assert "slot-flag-unpowered" not in editor  # unpowered is the "Not powered" band now (Echo's .tc-badge--unpowered)
    assert 'class="slot-wait-badge is-unpowered"' in editor and "Not powered" in editor
    assert "tileBand({ removal" in editor and "tileBand({ moving" in editor
    assert "const tags = tileTagsHtml(kinBadges, coolingFlag);" in editor


def test_tag_row_css_uses_name_tail_without_satellite_offset() -> None:
    css = (STATIC / "badge-face.css").read_text(encoding="utf-8")
    assert '@import url("badge-face.css");' in (STATIC / "badge-colours.css").read_text(encoding="utf-8").split("\n", 3)[1]
    row = css[css.index("html:not(.bf-off) .slot-tile .slot-tags {"):]
    row = row[:row.index("}")]
    for decl in ("position: absolute;", "bottom: 4px;", "left: 4px;",
                 "right: 4px;", "flex-flow: row nowrap;", "gap: 3px;"):
        assert decl in row
    assert ".slot-tile .slot-tags .slot-kin-badges { display: contents; }" in css
    assert "has(> .slot-tile-cell > .slot-energized-satellite) > .slot-tile-cell > .slot-tile .slot-tags" not in css
    assert ".mobile-plot-sheet .slot-tile .slot-tags { left: 3px; right: 3px; bottom: 2px;" in (STATIC / "mobile-plot.css").read_text(encoding="utf-8")


def test_toil_and_kin_tag_keeps_full_wording_as_accessible_name() -> None:
    badges = (STATIC / "badges.js").read_text(encoding="utf-8")
    assert "short: paired ? (rate || 'Paired') : 'Solo'" in badges
    assert 'role="img" aria-label="${esc(pill.text)}"' in badges and 'aria-hidden="true">${esc(pill.short || pill.text)}' in badges


def test_badge_overlay_labels_carry_no_deed_uid() -> None:
    overlay = (STATIC / "badge-overlay.js").read_text(encoding="utf-8")
    assert "badges for plot ${deedUid}" not in overlay
    assert "plotAriaName(deedUid)" in overlay
