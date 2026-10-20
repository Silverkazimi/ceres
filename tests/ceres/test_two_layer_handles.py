""" (maintainer, verbatim): "the inner and outer border of the slider
background for iris, wallet and flag must be aligned. give the flag the inner
background after it was clicked and badges are slided out. remove it when
clicked again and badges slide-in. currenly it is missing the outer
background." Every handle now shares Iris's own two-layer look: an OUTER glass
pill (`.theme-picker` recipe) and an INNER chip (`.theme-toggle` recipe). The
Wallet keeps its chip always (it shows the selected resource); the flag gets
its chip only while the badges group is open. See library/ceres-edge-tabs.md.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
CERES_CSS = STATIC / "ceres.css"
BADGE_OVERLAY_JS = STATIC / "badge-overlay.js"


def _rule(css: str, selector_open: str) -> str:
    return css.split(selector_open, 1)[1].split("\n}\n", 1)[0]


def test_flag_outer_pill_widened_to_match_wallet_iris():
    css = CERES_CSS.read_text(encoding="utf-8")
    head_rule = _rule(css, ".badge-bar-head {")
    # (maintainer, verbatim: "But all icons and borders in the slider are
    # unaligned"): --edge-w is now the pill's own REAL live-measured width
    # directly (app.js::centerThemePicker()), so a plain `inline-size` here
    # matches the Wallet pill's own `min-inline-size: var(--edge-w)` exactly
    # the `max(var(--edge-w), 36px)` safety net (needed only
    # because the OLD --edge-w derivation could under-measure) is gone.
    assert "inline-size: var(--edge-w, 36px);" in head_rule
    picker_rule = _rule(css, ".edge-picker {")
    assert "min-inline-size: var(--edge-w);" in picker_rule
    # Same padding / flush-right content placement as the picker (both now
    # read Iris's own live-measured left padding and right inset), so the
    # flag's own inner chip lands at the identical inset the Wallet toggle does.
    assert "padding: 0 var(--edge-pad-right, 0px) 0 var(--edge-pad-left, 4px);" in head_rule
    # content box == chip box now, so the glyph is centred (was
    # flex-end, which clipped it against the chip's own right edge).
    assert "justify-content: center;" in head_rule


def test_flag_inner_chip_tracks_used_and_unused_state():
    """The flag's state paint follows the mounted rows' live open state."""
    css = CERES_CSS.read_text(encoding="utf-8")
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert ".badge-bar-head::before {" in css
    before_rule = _rule(css, ".badge-bar-head::before {")
    assert "content: '';" in before_rule
    assert "position: absolute;" in before_rule
    # the chip box now spans the content box exactly (left pad to
    # right pad, no explicit inline-size), matching the glyph it's now
    # centred under (was width-only from `--edge-chip-w`, floating free of
    # the left edge while the glyph sat flush-right).
    assert "inline-size:" not in before_rule
    assert "left: var(--edge-pad-left, 4px);" in before_rule
    assert "block-size: min(var(--edge-chip-h, 32px), calc(var(--badge-flag-h, var(--ceres-thead-height, 2.75rem)) - 2 * var(--edge-pad-left, 4px)));" in before_rule
    assert "right: var(--edge-pad-right, 0px);" in before_rule
    # (maintainer): the flag's chip is the wallet/Iris toggle chip, always on.
    assert "background: color-mix(in srgb, var(--primary) 22%, transparent);" in before_rule
    assert "box-shadow: var(--edge-chip-shadow" in before_rule
    assert 'flag.dataset.state = allOpen ? \'used\' : \'unused\';' in js


def test_flag_expanded_state_does_not_add_a_pill_glow():
    """expanded state keeps the outer pill free of ambient glow;
 hover/focus-visible remains the only outer-pill glow affordance."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert '.badge-bar-head[aria-expanded="true"] {' not in css


def test_flag_aria_expanded_mirrors_the_bars_own_live_open_fact():
    """The flag and bar expose the same mounted-row open fact after each sync."""
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"export function syncBadgeOverlay\(reason\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "syncBadgeOverlay source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "bar?.setAttribute('aria-expanded', allOpen ? 'true' : 'false');" in js
    assert "flag?.setAttribute('aria-expanded', allOpen ? 'true' : 'false');" in js
    assert "flag.dataset.state = allOpen ? 'used' : 'unused';" in js


def test_wallet_toggle_chip_stays_always_visible_unlike_the_flag():
    """The Wallet keeps its own always-on chip (it shows the SELECTED resource,
 not an open/closed state) -- only changed the flag's own toggle
 behaviour, not the Wallet's."""
    css = CERES_CSS.read_text(encoding="utf-8")
    toggle_rule = _rule(css, ".edge-toggle {")
    assert "background: color-mix(in srgb, var(--primary) 22%, transparent);" in toggle_rule
    assert "aria-expanded" not in toggle_rule
