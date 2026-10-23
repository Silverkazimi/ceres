""" (maintainer, verbatim, about the three right-edge handles -- the Iris theme
picker, the Wallet picker and the Badges flag): "They are neither aligned in style
nor vertically." His decision (questionnaire, 2026-09-19): "One column, equal gaps"
-- the Wallet sits directly under the Iris pill and the Badges flag directly under
the Wallet, all 8px apart, same width and left edge, reading as one control strip;
the Wallet leaves the filter-row centre (supersedes that part of ).

Measured follow-up (maintainer's real-board verdict, 2026-09-19, on the FIRST attempt at
this ticket): FAIL -- the column existed only at scroll 0; the Iris->Wallet gap was
off by 10px; the Wallet->flag gap was wrong; the pills' right edge was 15px off the
bar's real edge. Redesigned (fixed strip, `#badge-flag` standalone handle,
`--edge-stack-top`), then a second independent re-check found the gutter itself
disagreed with a fixed element's real containing block whenever a scrollbar was
present -- fixed too (`viewportRight`, not `clientWidth`).

** SUPERSEDES THE STACK ENTIRELY** (maintainer ruling, after seeing the 
build, verbatim): "resource and flag slider must sit centered in the filter row and
table header respectively." The fixed, stacked-under-Iris column (Wallet directly
under Iris, Badges flag directly under Wallet) is GONE. This file now covers the
 mechanism instead:
- `#edge-tabs` (holding ONLY the Wallet picker now) is `position: absolute` again
  (document coordinates), centred on `.controls-row`'s own vertical midpoint --
  restoring the pre- (-era) mechanism.
- The Badges flag glyph is back INSIDE `#badge-bar` as `.badge-bar-head`
  (`position: sticky`, following the real table header band), not a standalone
  fixed handle -- `#badge-flag` and `--edge-stack-top` are retired again.
- `.badge-bar` itself is `top: 0` / `block-size: 100%` again (the  shift and
  its `badge-overlay.js` row-position compensation are both reverted).
- Width/right-edge alignment (the  second follow-up's `viewportRight`-based
  gutter, measured off `#badge-bar`'s own live rect) is KEPT unchanged -- 
  did not touch that part.
- The  chevron-on-open and the exact-Iris-tray-shape work on
  `.badge-overlay-row` are KEPT unchanged --  only moved WHERE the Wallet
  picker and the Badges flag sit, not their look or the row slide-out behaviour.

See library/ceres-edge-tabs.md v8 ("Centred on target, not stacked ()") for
the full geometry writeup. Companion coverage: test_edge_tabs.py (updated
guards for the formulas/rules this ticket changed), test_wallet_tray.py, and
test_table_redesign.py.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
APP_JS = STATIC / "app.js"
CERES_CSS = STATIC / "ceres.css"
BADGE_OVERLAY_JS = STATIC / "badge-overlay.js"
INDEX_HTML = STATIC / "index.html"


def _center_theme_picker_body() -> str:
    app = APP_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function centerThemePicker\(\) \{.*?\n\}\n", app, re.S)
    assert fn_match, "centerThemePicker source shape changed -- update this test's slice"
    return fn_match.group(0)


def test_edge_tabs_centres_on_controls_row_again():
    body = _center_theme_picker_body()
    # (maintainer: "the wallet and flag slider are still not sticky in their
    # respective rows") moved the actual positioning OUT of centerThemePicker() into
    # its own updateEdgeTabsPosition() (app.js, same split updateBadgeFlagPosition()
    # already has) -- see test_edge_tabs.py's
    # test_ir6980_edge_tabs_tracks_the_sticky_controls_row_with_no_extra_frame_lag for
    # the full formula/coordinate-space guard. centerThemePicker() itself just calls it.
    assert "updateEdgeTabsPosition();" in body
    assert "pickerLayout.bottom + 8" not in body
    # edge-stack-top and the standalone #badge-flag handle are both retired again.
    assert "--edge-stack-top" not in body
    assert "getElementById('badge-flag')" not in body


def test_edge_tabs_right_gutter_still_measured_off_the_bar_itself():
    """
    SUPERSEDES the  gutter mechanism for the Iris dock/#edge-tabs/#scroll-top
    -- all three are plain CSS `right: 0` now (no JS gutter write at all). The
    gutter concept survives only for `#badge-bar` (moved out to `<body>`, needs a
    `--page-gutter` compensation against its own `position: absolute` containing
    block) -- see test_ir6730_edge_flush.py for that coverage."""
    body = _center_theme_picker_body()
    assert "dock.style.right" not in body
    assert "edgeTabs.style.right" not in body
    assert "scrollTopBtn.style.right" not in body
    assert not re.search(r"^\s*const \w+ = .*\bclientWidth\b", body, re.M)
    # The right edge is the document element's own live rect (a fixed element's
    # real containing block), read once into `viewport` and reused.
    assert "const viewportRect = document.documentElement.getBoundingClientRect();" in body
    assert "right: viewportRect.right" in body
    assert "const viewportRight = viewport.right;" in body
    assert "const edge = Math.max(0, viewportRight - pickerLayout.right);" in body


def test_edge_tab_geometry_css_guards():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".edge-tabs {" in css
    tabs_rule = css.split(".edge-tabs {", 1)[1].split("}", 1)[0]
    # `position: fixed` again -- `.controls-row` is now the sticky element
    # (ceres.css, that rule's own comment); #edge-tabs tracks its live rect instead
    # of holding a one-time document-coordinate top. See test_edge_tabs.py.
    assert "position: fixed;" in tabs_rule
    assert "position: absolute;" not in tabs_rule
    assert "gap: 0.4rem;" in tabs_rule
    assert "gap: 8px;" not in tabs_rule


def test_badge_flag_is_fully_retired():
    """ SUPERSEDES this: `#badge-flag` is reinstated, as a standalone
    `position: fixed` element (not nested inside `#badge-bar` any more, and not
    `position: sticky`) -- see test_badge_bar_head_is_reinstated_sticky_on_the_real_header
    below (now covering the  shape) and test_ir6730_edge_flush.py."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="badge-flag"' in html
    css = CERES_CSS.read_text(encoding="utf-8")
    assert re.search(r"\.badge-bar-head\s*\{", css)
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "getElementById('badge-flag')" in js


def test_badge_bar_head_is_reinstated_sticky_on_the_real_header():
    """: `#badge-flag` (`.badge-bar-head`) is a standalone
    `position: fixed; right: 0` button again -- not nested inside `#badge-bar`, and
    not `position: sticky` (a sticky element's containing block was the scroller,
    which no longer reaches the true page edge) -- its `top`/`block-size` are
    JS-written from `#table-head-sticky`'s own live rect (app.js
    updateBadgeFlagPosition()) instead of the static `--ceres-header-height`/
    `--ceres-thead-height` vars this test used to check for."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert '<button type="button" id="badge-flag" class="badge-bar-head"' in html
    css = CERES_CSS.read_text(encoding="utf-8")
    head_rule_match = re.search(r"(?:^|\n)\.badge-bar-head \{(.*?)\n\}\n", css, re.S)
    assert head_rule_match, "standalone .badge-bar-head rule not found"
    head_rule = head_rule_match.group(1)
    assert "position: fixed; right: 0;" in head_rule
    assert "z-index: 10;" in head_rule
    # (maintainer, verbatim: "the inner and outer border of the slider
    # background for iris, wallet and flag must be aligned"): the flag's own
    # OUTER pill widened from `--badge-bar-w` to the Wallet pill's own width
    # token (the bar's own vertical-strip body keeps `--badge-bar-w` -- this
    # widening is scoped to the flag/head only). (maintainer, verbatim
    # "But all icons and borders in the slider are unaligned") then made
    # `--edge-w` itself the pill's own REAL live-measured width directly (no
    # longer a `toggleWidth + 4` guess), so a plain `inline-size` here matches
    # the Wallet pill exactly -- the old `min-inline-size: max(..., 36px)`
    # safety net is gone, no longer needed.
    assert "inline-size: var(--edge-w, 36px);" in head_rule
    assert "min-inline-size: max(var(--edge-w), 36px);" not in head_rule
    assert "inline-size: var(--badge-bar-w);" not in head_rule
    # (maintainer amendment: "the flag must use the IDENTICAL recipe as the
    # wallet pill, top to bottom") superseded 's opaque `--bg` variant
    # back to the SAME translucent shadow tint the Wallet's `.edge-picker` uses.
    # (maintainer: table header shimmers through the flag) supersedes that for
    # the OUTER pill only -- same 40% `--shadow` mix, opaque `--bg3` stop instead
    # of `transparent`, measured to match the Wallet pill's own live pixel.
    assert "background: color-mix(in srgb, var(--shadow) 40%, var(--bg3));" in head_rule
    assert "background: color-mix(in srgb, var(--shadow) 40%, transparent);" not in head_rule
    # radius/glow read from Iris's own live measurement now.
    assert "box-shadow:" not in head_rule
    assert "border-radius: var(--edge-radius-outer, 5px 0 0 5px);" in head_rule
    assert "linear-gradient" not in head_rule
    js = APP_JS.read_text(encoding="utf-8")
    assert "function updateBadgeFlagPosition() {" in js
    assert "getElementById('table-head-sticky')" in js
    # The flag's top is derived from the thead's own live rect (whole pixels); the
    # published inset is the measured distance between the two.
    assert "const flagTop = Math.round(theadRect.top) + 2;" in js
    assert "document.documentElement.style.setProperty('--badge-flag-inset', `${flagTop - theadRect.top}px`);" in js
    assert "document.documentElement.style.setProperty('--badge-flag-h', `${flagHeight}px`);" in js
    assert "flag.style.top = `${flagTop}px`;" in js
    assert "flag.style.height = `${flagHeight}px`;" not in js
    assert "block-size: var(--badge-flag-h, calc(var(--ceres-thead-height, 2.75rem) - 2 * var(--badge-flag-inset, 4px)));" in head_rule
    assert "function initBadgeFlag() {" in js
    assert "initBadgeFlag(); // " in js


def test_badge_bar_toggles_the_group_on_head_or_background_click():
    """ restores the pre- wiring -- the head glyph is decorative
    (aria-hidden) and falls into the bar's own "anything but a row" click branch."""
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "bar.addEventListener('click', (event) => {" in js
    assert "const barRow = event.target.closest('.badge-bar-row');" in js
    assert "toggleGroup();" in js
    assert "bar.addEventListener('keydown'" in js
    assert "flag?.addEventListener" not in js


def test_badge_bar_geometry_reverted_top_0_block_size_100():
    """: `#badge-bar` moved out to a direct child of `<body>` -- its `top`/
    `block-size` are now JS-written from the scroller's own live rect (app.js
    centerThemePicker(), so it starts/ends exactly where the table itself does),
    not the static `top: 0` / `block-size: 100%` this test used to check for
    (those remain only as the pre-JS fallback via the `--badge-bar-top`/
    `--badge-bar-h` custom properties' own default values)."""
    css = CERES_CSS.read_text(encoding="utf-8")
    bar_rule = next(
        (m.group(1) for m in re.finditer(r"(?:^|\n)\.badge-bar \{(.*?)\n\}\n", css, re.S) if "position: absolute;" in m.group(1)),
        None,
    )
    assert bar_rule, "standalone .badge-bar geometry rule not found"
    assert "top: var(--badge-bar-top, 0px);" in bar_rule
    # measured fix: `#badge-bar` moved OUT of `main#board-main` (whose own
    # 8px padding was the real source of the old gutter) to `<body>` (no padding of
    # its own) -- plain `right: 0` is already flush with the true page edge there
    # a `-1 * --page-gutter` compensation measured off the SCROLLER's rect (still
    # `main`-padded) overshot the true edge by that same 8px.
    assert "right: 0;" in bar_rule
    assert "right: calc(-1 * var(--page-gutter, 0px));" not in bar_rule
    assert "inline-size: var(--badge-bar-w);" in bar_rule
    # "Not the height, width"): `--badge-bar-w` now equals `--edge-w` (a live
    # tracking token, not 's fixed 1.5rem/24px), so the bar is exactly as
    # wide as the Iris/Wallet/flag pills and grows with them.
    assert "--badge-bar-w: var(--edge-w, 2rem);" in css
    # (maintainer amendment: "All must be same width, the single row badges
    # as well"): `.badge-bar-row` (the per-row dot/chevron cell) already spans
    # the bar's own full box (`left: 0; right: 0`, no narrower inset).
    row_rule = next(
        (m.group(1) for m in re.finditer(r"(?:^|\n)\.badge-bar-row \{(.*?)\n\}\n", css, re.S)),
        None,
    )
    assert row_rule, ".badge-bar-row rule not found"
    assert "left: 0; right: 0;" in row_rule
    assert "justify-content: center;" in row_rule
    assert not re.search(r"(?:^|;)\s*border-radius\s*:", row_rule)  # maintainer 2026-09-28: no per-row chip; badges sit on the column's dark glass
    # the row is the visible chip, using #badge-flag's opaque recipe
    # the child flagged strip is only the dot wrapper.
    assert not re.search(r"(?:^|;)\s*background\s*:", row_rule)  # maintainer 2026-09-28: no per-row chip; badges sit on the column's dark glass
    # measured fix: review found a real 16px gap between the slid-out
    # `.badge-overlay-row`s and the bar (row right edge 1473 vs bar left 1489
    # at 1536px) -- `.badge-overlay`'s own containing block (`#plot-table
    # scroll`) is already inset by `--page-gutter`, so ADDING it again in this
    # `right` formula double-counted it. Subtracting cancels that inset
    # instead, landing the rows flush against the bar (headless-verified 0px
    # gap at 1536/DPR1.25, 1920, and a forced 40px Iris toggle font-size).
    overlay_rule_match = re.search(r"(?:^|\n)\.badge-overlay \{(.*?)\n\}\n", css, re.S)
    assert overlay_rule_match, ".badge-overlay rule not found"
    overlay_rule = overlay_rule_match.group(1)
    assert "right: calc(var(--badge-bar-w) - var(--page-gutter, 0px));" in overlay_rule
    assert "right: calc(var(--badge-bar-w) + var(--page-gutter, 0px));" not in overlay_rule
    js = APP_JS.read_text(encoding="utf-8")
    assert "badgeBarEl.style.top = `${barDocTop}px`;" in js
    assert "badgeBarEl.style.height = `${scrollerRect.height}px`;" in js
    assert "documentElement.style.setProperty('--page-gutter'" in js
    badge_js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "top: `${trRect.top - scrollerRect.top + 1}px`," in badge_js
    assert "height: `${Math.max(0, trRect.height - 2)}px`," in badge_js
    # z-fix round 2: .badge-bar carries no numeric z-index (its glass fill
    # the actual stacking-context-forcing culprit, moved to .badge-bar::before).
    assert "z-index: auto;" in bar_rule
    before_rule_match = re.search(r"(?:^|\n)\.badge-bar::before \{(.*?)\n\}\n", css, re.S)
    assert before_rule_match, "standalone .badge-bar::before rule not found"
    before_rule = before_rule_match.group(1)
    assert "content: '';" in before_rule
    assert "position: absolute; inset: 0;" in before_rule
    assert "z-index: 6;" in before_rule
    assert "pointer-events: none;" in before_rule
    assert "backdrop-filter:" in before_rule


def test_badge_overlay_js_row_position_compensation_reverted():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    # The `barTop`/`theadHeightPx` compensation is gone -- `.badge-bar-row` and
    # `.badge-overlay-row` share the same plain `<tr>`-rect-relative `top` again
    # ( later gave the OVERLAY row its own separate, INSET top/height
    # see test_badge_overlay_row_inset.py -- but the bar row's own value
    # is still the plain, uninset one asserted here).
    assert "theadHeightPx" not in js
    assert "barTop" not in js
    assert "for (const { deedUid, top, height, overlayTop, overlayHeight, row, tr } of mounted) {" in js
    assert "if (barRow.style.top !== top) barRow.style.top = top;" in js


def test_badge_bar_and_overlay_row_share_the_iris_tint_in_dark_themes():
    """/ fill colours, unaffected by 's mechanism changes --
     z-fix round 2 moved the fill from `.badge-bar` onto `.badge-bar::before`
    (a pseudo-element, since `background`/`backdrop-filter` together are what forced
    the stacking context bug), but the actual colours/values are identical."""
    css = CERES_CSS.read_text(encoding="utf-8")
    resting_rule_match = re.search(r"(?:^|\n)\.badge-bar \{\n  border: 0;.*?\n\}\n", css, re.S)
    assert resting_rule_match, "badge-bar resting-state chrome rule not found"
    assert "background:" not in resting_rule_match.group(0)
    before_rule_match = re.search(r"(?:^|\n)\.badge-bar::before \{(.*?)\n\}\n", css, re.S)
    assert before_rule_match, "standalone .badge-bar::before rule not found"
    assert "color-mix(in srgb, var(--shadow) 40%, transparent)" in before_rule_match.group(1)
    # 2026-09-28 (maintainer): the column keeps this glass while slide-outs are out -- no open-state fill.
    assert '.badge-bar[aria-expanded="true"]::before' not in css
    row_rule = css.split(".badge-overlay-row {", 1)[1].split("\n}\n", 1)[0]
    assert "color-mix(in srgb, var(--shadow) 82%, transparent)" in row_rule
    assert "background-color .5s cubic-bezier(.4, .8, .4, 1)" in row_rule


def test_light_and_wood_keep_a_single_bg2_contrast_fill_no_compounding():
    """ item 5, unaffected by 's mechanism changes -- the light/wood
    override now targets `.badge-bar::before` (see the test above) instead of
    `.badge-bar` directly, same reasoning."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert '[data-theme="light"] .badge-bar::before, [data-theme="wood"] .badge-bar::before {' in css
    assert '[data-theme="light"] .badge-bar-row, [data-theme="wood"] .badge-bar-row {' not in css
    assert '[data-theme="light"] .badge-overlay-row, [data-theme="wood"] .badge-overlay-row {' in css
    overlay_fill_rule = css.split('[data-theme="light"] .badge-overlay-row, [data-theme="wood"] .badge-overlay-row {', 1)[1].split("}", 1)[0]
    assert "color-mix(in srgb, var(--bg2) 92%, transparent)" in overlay_fill_rule


def test_open_bar_row_keeps_its_badge_strip_with_no_chevron():
    """.1
    supersedes 's chevron: an open row keeps its own strip and only gains `is-open`."""
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "const rowOpen = isRowOpen(deedUid);" in js
    assert "badge-bar-chevron" not in js
    assert "barRow.classList.toggle('is-open', rowOpen);" in js
    assert '<span class="badge-bar-content">${row ? badgeStripHtml(row) : \'\'}</span>' in js
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".badge-bar-chevron {" not in css


def test_badge_overlay_row_matches_the_iris_open_tray_shape_in_every_theme():
    """Kept unchanged from  --  did not touch this."""
    css = CERES_CSS.read_text(encoding="utf-8")
    row_rule = css.split(".badge-overlay-row {", 1)[1].split("\n}\n", 1)[0]
    assert "background: color-mix(in srgb, var(--shadow) 82%, transparent);" in row_rule
    assert "backdrop-filter: saturate(130%) blur(4px);" in row_rule
    assert "border-radius: 5px 0 0 5px;" in row_rule
    assert "box-shadow:" not in row_rule
    assert "padding: 0 8px;" in row_rule
    assert "padding-inline: 0.6rem;" not in row_rule


def test_no_border_left_or_inset_stripe_on_any_of_the_handles():
    css = CERES_CSS.read_text(encoding="utf-8")
    for selector, pattern in (
        (".edge-picker", r"(?:^|\n)\.edge-picker \{(.*?)\n\}\n"),
        (".edge-toggle", r"(?:^|\n)\.edge-toggle \{(.*?)\n\}\n"),
        (".badge-bar (resting)", r"(?:^|\n)\.badge-bar \{\n  border: 0;(.*?)\n\}\n"),
        (".badge-bar-head", r"(?:^|\n)\.badge-bar-head \{(.*?)\n\}\n"),
    ):
        match = re.search(pattern, css, re.S)
        assert match, f"{selector} rule not found in its expected shape"
        block = match.group(1)
        assert "border-left" not in block, f"{selector} carries a border-left"
        assert "border-inline-start" not in block, f"{selector} carries a border-inline-start"
        # A LEFT stripe would be a non-zero x-offset inset shadow (`inset Npx ...`
        # N != 0). `.edge-toggle` legitimately keeps its own `inset 0 1px 0 ...` top
        # highlight (canonical.css `.theme-toggle` parity) -- that is a TOP line, not
        # a left one, so it is not what the maintainer's screenshot showed.
        assert not re.search(r"inset\s+-?[1-9]\w*px", block), f"{selector} carries a left-offset inset line"


def test_node_check_touched_js_files():
    for path in (APP_JS, BADGE_OVERLAY_JS):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"


# (maintainer, verbatim: "add a scroll up button to the bottom right in the iris
# style") -- a fourth standalone fixed handle, same build. See
# library/ceres-edge-tabs.md v8 for the full writeup.


def test_scroll_top_markup():
    html = INDEX_HTML.read_text(encoding="utf-8")
    btn_match = re.search(r'<button[^>]*id="scroll-top"[^>]*></button>', html)
    assert btn_match, "#scroll-top button not found in index.html"
    tag = btn_match.group(0)
    assert 'class="edge-toggle scroll-top"' in tag
    assert 'data-tip="Back to top"' in tag
    assert 'aria-label="Back to top"' in tag


def test_scroll_top_css_is_the_iris_pill_recipe_not_the_toggle_chip():
    css = CERES_CSS.read_text(encoding="utf-8")
    rule_match = re.search(r"(?:^|\n)\.scroll-top \{(.*?)\n\}\n", css, re.S)
    assert rule_match, "standalone .scroll-top rule not found"
    rule = rule_match.group(1)
    assert "position: fixed;" in rule
    assert "bottom: env(safe-area-inset-bottom, 0px);" in rule
    assert "border-radius: 5px 5px 0 0;" in rule
    # Same width as the rest of the strip; the maintainer's "iris style" (shadow-40% glass
    # + primary glow -- .edge-picker's own resting-state values), NOT .edge-toggle's
    # own primary-tint chip look it would otherwise inherit from the shared class.
    assert "inline-size: var(--edge-w);" in rule
    assert "z-index: 40;" in rule
    assert "background: color-mix(in srgb, var(--shadow) 40%, transparent);" in rule
    assert "box-shadow: 0 0 18px -6px var(--primary);" in rule
    assert "color-mix(in srgb, var(--primary) 22%" not in rule
    # Hidden by default; a scroll listener (app.js) toggles .is-visible.
    assert "opacity: 0;" in rule
    assert "pointer-events: none;" in rule
    assert ".scroll-top.is-visible { opacity: 1; pointer-events: auto; }" in css
    assert ".scroll-top:hover, .scroll-top:focus-visible {" in css
    # Several reduced-motion blocks exist (some single-line); the override must be in one of
    # the multi-line ones.
    reduced_blocks = [m.group(1) for m in re.finditer(r"@media \(prefers-reduced-motion: reduce\) \{\n(.*?)\n\}\n", css, re.S)]
    assert reduced_blocks, "prefers-reduced-motion block not found"
    assert any(".scroll-top { transition: none; }" in block for block in reduced_blocks)
    # The chevron glyph reuses the shared mask, rotated to point up (native direction
    # is down).
    assert ".scroll-top::before {" in css
    before_rule = css.split(".scroll-top::before {", 1)[1].split("}", 1)[0]
    assert "--ceres-chevron-art" in before_rule
    assert "rotate(180deg)" in before_rule


def test_scroll_top_sits_one_handle_width_left_of_the_thinner_bar():
    """ measured finding (2026-09-20) offset the button one handle-width
    left of the strip so it never covered the bar's bottom two rows. A first pass
    of  briefly put it back at plain `right: 0` (reading the maintainer's "all
    three sliders" as including the scroll-top button) -- team-lead correction:
    the maintainer's "all three sliders" are the Iris dock, the wallet picker and the
    badges flag/bar; `#scroll-top` keeps its own offset, now tracking the bar's
    new, thinner `--badge-bar-w` (24px) instead of the old, wider `--edge-w`
    (48px) the bar itself used to be."""
    css = CERES_CSS.read_text(encoding="utf-8")
    rule_match = re.search(r"(?:^|\n)\.scroll-top \{(.*?)\n\}\n", css, re.S)
    assert rule_match, "standalone .scroll-top rule not found"
    assert "right: calc(var(--badge-bar-w) + 8px);" in rule_match.group(1)
    assert "right: 0;" not in rule_match.group(1)
    assert "right: calc(var(--edge-w) + 8px);" not in rule_match.group(1)


def test_scroll_top_visibility_threshold_and_reduced_motion_click_handler():
    js = APP_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function initScrollTop\(\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "initScrollTop source shape changed -- update this test's slice"
    body = fn_match.group(0)
    # Passive listener, rAF-throttled -- at most one DOM write per paint.
    assert "window.addEventListener('scroll', () => {" in body
    assert "{ passive: true }" in body
    assert "requestAnimationFrame(update)" in body
    assert "window.scrollY > 400" in body
    assert "window.matchMedia('(prefers-reduced-motion: reduce)').matches" in body
    assert "window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });" in body
    assert "initScrollTop();" in js


def test_scroll_top_clears_mobile_action_bar_and_hides_on_start_no_land_pages():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html.ceres-lw-le-1279 #scroll-top { display: none; }" in css
    assert "html.ceres-lw-le-1279 #scroll-top { bottom: calc(4.5rem + env(safe-area-inset-bottom)); }" not in css
    mobile_bar = re.search(r"html\.ceres-lw-le-1919\.ceres-lw-le-1279 \.mobile-action-bar \{(.*?)\s*\}", css, re.S)
    assert mobile_bar and "inset: auto 0 0;" in mobile_bar.group(1)
    assert "z-index: 20;" in mobile_bar.group(1)
    no_land_rule = re.search(r"body\.no-land [^\n]*\{ display: none !important; \}", css)
    assert no_land_rule and "#scroll-top" in no_land_rule.group(0)
    start_page_rule = re.search(r"body\.start-page [^\n]*\n[^\n]*\{ display: none !important; \}", css)
    assert start_page_rule and "#scroll-top" in start_page_rule.group(0)
