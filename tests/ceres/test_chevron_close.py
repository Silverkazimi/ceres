""": plot/picker Close buttons are icon-only red chevron-up buttons on a
semi-transparent ground (maintainer ruling 2026-09-19), not the ceres-btn family."""
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_icon_defined_once_and_reused():
    icons = (STATIC / "icons.js").read_text()
    assert 'export const CHEVRON_CLOSE_ICON' in icons
    assert 'viewBox="0 0 16 16"' in icons
    assert 'stroke="currentColor"' in icons
    assert 'stroke-width="2"' in icons
    assert 'aria-hidden="true"' in icons.split("CHEVRON_CLOSE_ICON")[1].split(";")[0]
    # No Unicode arrow glyph snuck into the icon definition.
    for glyph in ("↑", "▲", "⌃"):
        assert glyph not in icons.split("CHEVRON_CLOSE_ICON")[1].split(";")[0]


def test_editor_close_button_is_icon_only_chevron():
    editor = (STATIC / "editor.js").read_text()
    assert "CHEVRON_CLOSE_ICON" in editor
    assert 'class="eos-close btn-close chevron-close"' in editor
    assert 'aria-label="Close plot ' in editor
    assert 'data-tip="Close"' in editor
    assert '"eos-close btn-close ceres-btn ceres-btn--secondary"' not in editor
    assert ">Close</button>" not in editor


def test_editor_error_fallback_close_button_is_icon_only_chevron():
    picker_actions = (STATIC / "picker-actions.js").read_text()
    fallback = picker_actions[picker_actions.index("export function workerSubtable"):]
    assert "CHEVRON_CLOSE_ICON" in fallback
    assert 'class="eos-close chevron-close"' in fallback
    assert 'aria-label="Close plot ' in fallback
    assert 'data-tip="Close"' in fallback
    assert '"eos-close ceres-btn ceres-btn--secondary"' not in fallback
    assert ">Close</button>" not in fallback


def test_picker_close_button_is_icon_only_chevron():
    candidates = (STATIC / "candidates.js").read_text()
    assert "CHEVRON_CLOSE_ICON" in candidates
    assert 'class="picker-close chevron-close"' in candidates
    assert 'aria-label="Close picker"' in candidates
    assert 'data-tip="Close"' in candidates
    assert '"picker-close ceres-btn ceres-btn--secondary"' not in candidates
    assert 'data-picker-action="close">Close</button>' not in candidates


def test_chevron_close_css_uses_danger_token_and_semi_transparent_ground():
    css = (STATIC / "ceres.css").read_text()
    assert ".chevron-close {" in css
    block = css[css.index(".chevron-close {"):css.index(".chevron-close {") + 400]
    assert "color: var(--danger)" in block
    assert "color-mix(in srgb, var(--bg) 55%, transparent)" in block
    assert "border: 0" in block
    assert "padding: 0" in block
    assert ".chevron-close:hover, .chevron-close:focus-visible {" in css
    assert "color-mix(in srgb, var(--danger) 18%, color-mix(in srgb, var(--bg) 55%, transparent))" in css
    assert ".chevron-close:focus-visible {" in css
    assert ".chevron-close svg {" in css


def test_chevron_close_buttons_carry_no_stale_density_attribute():
    for name in ("editor.js", "candidates.js", "picker-actions.js"):
        source = (STATIC / name).read_text()
        assert 'chevron-close" data-control-density="compact"' not in source
