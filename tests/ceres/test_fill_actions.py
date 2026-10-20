"""Source guards for 's paired fill controls."""

from pathlib import Path


ROOT = Path(__file__).parents[2]
CANDIDATES = (ROOT / "ceres/static/candidates.js").read_text(encoding="utf-8")
EDITOR = (ROOT / "ceres/static/editor.js").read_text(encoding="utf-8")
ICONS = (ROOT / "ceres/static/icons.js").read_text(encoding="utf-8")
CSS = (ROOT / "ceres/static/ceres.css").read_text(encoding="utf-8")


def test_both_fill_controls_use_the_shared_fill_tray_icon() -> None:
    assert 'export const FILL_TRAY_ICON = \'<svg class="hdr-ic fill-tray-icon"' in ICONS
    assert '<path class="f"' in ICONS
    assert '<path class="d"' in ICONS
    assert '<path class="a h"' in ICONS
    assert "${FILL_TRAY_ICON}<span>Fill plot</span>" in EDITOR
    assert "${FILL_TRAY_ICON}<span>Fill next slot</span>" in CANDIDATES


def test_fill_tooltips_use_the_approved_copy() -> None:
    assert "title: 'Fill plot'" in EDITOR
    assert "body: 'Propose the best ready cards and items for every open worker slot; nothing is staged until you accept.'" in EDITOR
    assert "tip('Fill next slot', 'Keep this picker open and move to the next empty slot after each card, skipping categories with nothing to add.')" in CANDIDATES


def test_fill_style_and_alchemical_arrow_override_are_scoped() -> None:
    styles = CSS[CSS.index("button.slot-bulk.fill-action") : CSS.index("*")]
    assert ".picker-bar-end .picker-keep-open.fill-action" in styles
    assert "background: color-mix(in srgb, var(--primary) 18%, var(--bg3))" in styles
    assert "border-color: var(--primary)" in styles
    assert "color: var(--heading)" in styles
    assert ".fill-action .hdr-ic .f { fill: color-mix(in srgb, var(--hi) 84%, var(--primary)); }" in styles
    assert ".fill-action .hdr-ic .d { fill: none; stroke: color-mix(in srgb, var(--primary) 62%, var(--shadow));" in styles
    assert ".fill-action .hdr-ic .a.h { fill: none; stroke: var(--gold);" in styles
    assert "root:not([data-theme]) .fill-action .hdr-ic .a.h" in styles
    assert '[data-theme="alchemical"] .fill-action .hdr-ic .a.h { stroke: var(--hi); }' in styles
