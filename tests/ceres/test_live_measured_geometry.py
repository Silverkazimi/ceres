""" (maintainer, verbatim): "But all icons and borders in the slider are
unaligned". Measured state: Iris pill 32px wide with a 28px toggle, while the
Wallet pill/flag were 36px wide with 32px chips (`--edge-w` used to be
`toggleWidth + 4`, a guess, not a direct measurement). Every handle now takes
its outer box (width, padding, radius, glow) and inner chip (size, radius,
shadow) from Iris's own LIVE rects/computed style, published as CSS custom
properties every handle consumes. See library/ceres-edge-tabs.md.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
APP_JS = STATIC / "app.js"
CERES_CSS = STATIC / "ceres.css"


def _rule(css: str, selector_open: str) -> str:
    return css.split(selector_open, 1)[1].split("\n}\n", 1)[0]


def test_center_theme_picker_measures_and_publishes_the_full_geometry_set():
    js = APP_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function centerThemePicker\(\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "centerThemePicker source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "const toggle = document.querySelector('.theme-picker .theme-toggle');" in body
    assert "const toggleRect = toggle?.getBoundingClientRect();" in body
    assert "if (toggleRect && toggleRect.width > 0 && !picker.classList.contains('is-open')) {" in body
    for prop in (
        "--edge-w", "--edge-chip-w", "--edge-chip-h", "--edge-chip-font", "--edge-pad-left", "--edge-pad-right",
        "--edge-radius-outer", "--edge-radius-inner", "--edge-chip-shadow",
    ):
        assert f"root.setProperty('{prop}'" in body


def test_edge_picker_is_border_box_so_edge_w_means_the_whole_pill():
    """ measured bug: `min-inline-size`/`inline-size` on a `content-box`
 element apply to the content area only, excluding padding -- with the
 picker's own 4px left padding, `min-inline-size: var(--edge-w)` produced a
 TOTAL width 4px wider than Iris's own real pill. `#badge-flag` (a real
 `<button>`) already got `box-sizing: border-box` for free from the
 browser's own UA default stylesheet (form controls default to
 border-box); `.edge-picker` (a plain `<div>`) does not, and needs it
 declared explicitly."""
    css = CERES_CSS.read_text(encoding="utf-8")
    picker_rule = _rule(css, ".edge-picker {")
    assert "box-sizing: border-box;" in picker_rule
    head_rule = _rule(css, ".badge-bar-head {")
    assert "box-sizing: border-box;" in head_rule


def test_all_three_handles_consume_the_same_geometry_vars():
    css = CERES_CSS.read_text(encoding="utf-8")
    picker_rule = _rule(css, ".edge-picker {")
    head_rule = _rule(css, ".badge-bar-head {")
    toggle_rule = _rule(css, ".edge-toggle {")
    before_rule = _rule(css, ".badge-bar-head::before {")

    # Outer boxes: same width token, same padding vars, same radius/glow vars.
    assert "min-inline-size: var(--edge-w);" in picker_rule
    assert "inline-size: var(--edge-w, 36px);" in head_rule
    for rule in (picker_rule, head_rule):
        assert "var(--edge-pad-left, 4px)" in rule
        assert "var(--edge-pad-right, 0px)" in rule
        assert "border-radius: var(--edge-radius-outer, 5px 0 0 5px);" in rule
        assert "box-shadow:" not in rule

    # Inner chips: same size token, same radius/shadow vars. : capped at
    # `min(var(--edge-chip, 32px), 32px)`, not a bare var() -- the Wallet
    # pill's own fixed 40px height only has room for a 32px chip with 's
    # framing intact; a chip that tracks Iris's real toggle size WITHOUT this
    # ceiling would reopen 's "chip too large" bug if Iris's real
    # toggle ever measures larger than 32px.
    assert "inline-size: var(--edge-chip-w, 32px);" in toggle_rule
    assert "block-size: min(var(--edge-chip-h, 32px), calc(40px - 2 * var(--edge-pad-left, 4px)));" in toggle_rule
    # `.badge-bar-head::before` dropped its explicit `inline-size`
    # it now spans the content box exactly (`left`/`right` insets both track
    # `--edge-pad-left`/`--edge-pad-right`), so it always coincides with the
    # now-centred glyph instead of tracking `--edge-chip-w` independently.
    assert "inline-size:" not in before_rule
    assert "left: var(--edge-pad-left, 4px);" in before_rule
    assert "block-size: min(var(--edge-chip-h, 32px), calc(var(--badge-flag-h, var(--ceres-thead-height, 2.75rem)) - 2 * var(--edge-pad-left, 4px)));" in before_rule
    for rule in (toggle_rule, before_rule):
        assert "var(--edge-radius-inner, 5px 0 0 5px)" in rule or "border-radius: var(--edge-radius-inner, 5px 0 0 5px);" in rule


def test_badge_bar_own_strip_width_is_unaffected_by():
    """The bar's own vertical-strip body keeps its dedicated, deliberately
 thinner `--badge-bar-w` () -- only widened the FLAG's own
 outer box to match the Wallet/Iris handles, not the strip itself."""
    css = CERES_CSS.read_text(encoding="utf-8")
    bar_rule = next(
        (m.group(1) for m in re.finditer(r"(?:^|\n)\.badge-bar \{(.*?)\n\}\n", css, re.S) if "position: absolute;" in m.group(1)),
        None,
    )
    assert bar_rule, "standalone .badge-bar geometry rule not found"
    assert "inline-size: var(--badge-bar-w);" in bar_rule
    assert "--edge-w" not in bar_rule


def test_node_check_touched_js_file():
    result = subprocess.run(["node", "--check", str(APP_JS)], capture_output=True, text=True)
    assert result.returncode == 0, f"{APP_JS}: {result.stderr}"
