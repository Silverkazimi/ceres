""": a Wallet edge tab, a slimmer/translucent slide-out badge panel, and
's Changes-modal tile row filling the plot group's width instead of leaving bare
space beside it. See library/ceres-edge-tabs.md and the v2/v3 sections of
library/ceres-badge-overlay.md for the full design and rationale.

Amendment (maintainer, same day, verbatim): "Make the entire right column where the current
badge group dots and the tick are, the entire slide-out. Separated from the table. Any
click on the right column will slide it out, not that icon in the table header alone.
Make the icon in the header fully connected in the design of the slider-bar." The
Badges trigger is no longer an edge tab -- it is #badge-bar, a standalone vertical bar
attached to the table's own right edge (the table's badge-strip column is gone
entirely). Only the Wallet edge tab remains in #edge-tabs.
"""
from __future__ import annotations

import re
import subprocess
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

from .vm_context import run_in_module_context

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
INDEX_HTML = STATIC / "index.html"
APP_JS = STATIC / "app.js"
TABLE_JS = STATIC / "table.js"
ROWS_JS = STATIC / "rows.js"
CERES_CSS = STATIC / "ceres.css"
BADGE_OVERLAY_JS = STATIC / "badge-overlay.js"
REGIONAL_DEC_JS = STATIC / "regional-dec.js"


@pytest.fixture(scope="module")
def _css_browser():
    with sync_playwright() as playwright:
        with closing(playwright.chromium.launch()) as browser:
            yield browser


def _edge_tabs_html() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    # #edge-tabs now wraps #wallet-picker (edge-tray + edge-toggle), which
    # itself nests a `<div class="edge-tray">...</div>` -- a naive non-greedy match to
    # the FIRST `</div>` would stop inside that nested div. #edge-tabs's own closing
    # `</div>` is the only one at zero indent ( measured follow-up added
    # `#badge-flag` as a second, one-line child; the nested ones are all on one line
    # too), so matching out to that specific `\n</div>` reaches the true outer close.
    match = re.search(r'<div id="edge-tabs" class="edge-tabs">.*?\n</div>', html, re.S)
    assert match, "could not find #edge-tabs markup"
    return match.group(0)


def _badge_bar_html() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(r'<aside id="badge-bar" class="badge-bar"[^>]*>.*?</aside>', html, re.S)
    assert match, "could not find #badge-bar markup"
    return match.group(0)


def test_edge_tabs_markup_has_only_the_wallet_picker():
    markup = _edge_tabs_html()
    assert 'id="badge-overlay-toggle"' not in markup, "Badges is no longer an edge tab (it is #badge-bar)"
    # #wallet-edge-tab is now the picker's TOGGLE, nested inside #wallet-picker
    # alongside an (initially empty, JS-filled) .edge-tray -- it mirrors the Iris theme
    # picker's own tray/toggle shape, never the Iris `.theme-*` classes themselves.
    # #wallet-picker's whole markup (tray + toggle) is authored on one line.
    picker = re.search(r'<div id="wallet-picker" class="edge-picker" data-open="false">[^\n]*', markup)
    assert picker, "wallet-picker not found inside #edge-tabs"
    picker_html = picker.group(0)
    assert '<div id="wallet-picker-tray" class="edge-tray" role="tablist" aria-label="Wallet resource"></div>' in picker_html
    wallet = re.search(r'<button[^>]*id="wallet-edge-tab"[^>]*>', picker_html)
    assert wallet, "wallet-edge-tab (the toggle) not found inside #wallet-picker"
    tag = wallet.group(0)
    assert 'class="edge-toggle"' in tag
    assert 'aria-haspopup="true"' in tag
    assert 'aria-expanded="false"' in tag
    # Pre-landing review fix (): the toggle's aria-controls now targets the
    # TRAY it opens/closes, not the drawer -- it no longer opens the drawer directly.
    assert 'aria-controls="wallet-picker-tray"' in tag
    assert 'aria-controls="regional-dec-drawer"' not in tag
    assert re.search(r'aria-label="[^"]+"', tag)
    # pre-landing review fix: a native `title` doubles the Ceres tooltip
    # the tab uses `data-tip` (tooltip.js) like every other icon on the board instead.
    assert re.search(r'data-tip="[^"]+"', tag)
    assert not re.search(r'\btitle="[^"]+"', tag)


def test_badge_bar_markup_is_a_group_with_a_separate_flag_control():
    bar = _badge_bar_html()
    assert 'role="group"' in bar
    assert 'role="button"' not in bar
    # (maintainer, verbatim, high: "all three sliders must sit on the furthest
    # right edge of the page"): `#badge-bar` moved out to a direct child of `<body>`
    # and is now an EMPTY <aside> -- its rows are populated by badge-overlay.js.
    # `#badge-flag` (the head glyph) is reinstated as its own standalone, real
    # `<button>`, a SIBLING of `#badge-bar`, not a decorative nested div any more
    # (see test_badge_flag_is_fully_retired in test_edge_stack.py).
    assert bar.strip() == '<aside id="badge-bar" class="badge-bar" role="group" aria-label="Plot badge controls"></aside>'
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="badge-flag"' in index and 'aria-controls="badge-overlay"' in index


def test_edge_tabs_holds_only_the_wallet_picker():
    markup = _edge_tabs_html()
    # the Badges flag is no longer in #edge-tabs at all -- it lives inside
    # #badge-bar as .badge-bar-head (see test_badge_bar_markup_is_a_click_anywhere_role_button).
    assert "badge-flag" not in markup
    assert 'id="wallet-picker"' in markup


def test_table_has_no_badge_column_any_more():
    html = INDEX_HTML.read_text(encoding="utf-8")
    thead_match = re.search(r'<thead class="table-head-sticky[^>]*>.*?</thead>', html, re.S)
    assert thead_match, "could not find #plot-table thead"
    thead = thead_match.group(0)
    assert 'id="badge-overlay-toggle"' not in thead
    assert "badge-strip-head" not in thead
    assert "aria-label=\"Badges\"" not in thead
    colgroup_match = re.search(r"<colgroup>.*?</colgroup>", html, re.S)
    assert colgroup_match, "could not find #plot-table colgroup"
    assert "col-w-badge-strip" not in colgroup_match.group(0)
    ths = re.findall(r"<th\b", thead)
    # One header cell per table column: the header, the colgroup and the shared column count
    # (state.js, used for the expand-row colspan) agree, so no hidden badge column remains.
    # The table has grown real columns since this guard was written (Totem and Title are
    # separate columns again, plus the lock and plot-count columns), so the count is read
    # from the product's own column constant instead of a hand-typed number.
    cols = re.findall(r"<col\b", colgroup_match.group(0))
    column_count = re.search(r"export const PLOT_TABLE_COLUMN_COUNT = (\d+);", (STATIC / "state.js").read_text(encoding="utf-8"))
    assert column_count, "state.js must declare PLOT_TABLE_COLUMN_COUNT"
    assert len(ths) == len(cols) == int(column_count.group(1)), (
        f"header cells ({len(ths)}), <col>s ({len(cols)}) and PLOT_TABLE_COLUMN_COUNT ({column_count.group(1)}) disagree"
    )


def test_regional_dec_button_stays_in_dom_for_mobile_but_hides_on_desktop():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="regional-dec-button"' in html
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html:not(.ceres-lw-le-1279) #regional-dec-button" in css
    rule = re.search(r"html:not\(\.ceres-lw-le-1279\) #regional-dec-button \{[^}]*\}", css)
    assert rule and "display: none;" in rule.group(0)


def test_edge_tabs_are_desktop_only():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html.ceres-lw-le-1279 .edge-tabs { display: none; }" in css


def test_edge_tabs_positioned_from_the_same_picker_measurement_as_the_dock():
    app = APP_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function centerThemePicker\(\) \{.*?\n\}\n", app, re.S)
    assert fn_match, "centerThemePicker source shape changed -- update this test's slice"
    body = fn_match.group(0)
    # The dock's own top is still written here, from the anchored picker measurement.
    assert "dock.style.top = `${pickerPosition.top}px`;" in body
    # the wallet's own positioning moved OUT of centerThemePicker() into
    # its own function (updateEdgeTabsPosition(), same split updateBadgeFlagPosition()
    # already has) since it now needs a per-scroll-frame update centerThemePicker()'s
    # layout-event-only triggers don't give it -- centerThemePicker() just calls it.
    assert "updateEdgeTabsPosition();" in body
    assert "getElementById('edge-tabs')" not in body


def test_edge_tabs_tracks_the_sticky_controls_row_with_no_extra_frame_lag():
    """. `.controls-row` (the filter row #edge-tabs centres
    on) is now itself `position: sticky` under the page header (same `top` var
    `thead.table-head-sticky` uses) -- 's old "wallet scrolls away with the
    row" design is superseded. `#edge-tabs` moved from `position: absolute`
    (document coordinates, `rect.top + window.scrollY`) to `position: fixed` with a
    plain viewport-relative `top` read directly off `.controls-row`'s live
    `getBoundingClientRect()`, the same pattern  already established for
    `#badge-flag` against the sticky thead -- a document-coordinate `top` would keep
    drifting past the row once it sticks, since `rect.top` stops changing while
    `scrollY` keeps climbing. `updateEdgeTabsPosition()` must therefore be called
    directly from the `scroll` listener, with no `requestAnimationFrame` hop
    in between (same measured 1-frame-lag class of bug  fixed for the flag)."""
    js = APP_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function updateEdgeTabsPosition\(\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "updateEdgeTabsPosition source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "getElementById('edge-tabs')" in body
    assert "querySelector('.controls-row')" in body
    assert "getElementById('wallet-picker')" in body
    assert "controlsRow.getBoundingClientRect();" in body
    assert "window.scrollY" not in body
    assert "edgeTabs.style.top = `${edgeTabsY}px`;" in body

    sync_match = re.search(r"function initEdgeTabsScrollSync\(\) \{.*?\n\}\n", js, re.S)
    assert sync_match, "initEdgeTabsScrollSync source shape changed -- update this test's slice"
    sync_body = sync_match.group(0)
    assert "requestAnimationFrame(" not in sync_body
    assert "updateEdgeTabsPosition();" in sync_body
    assert "{ passive: true }" in sync_body
    assert "initEdgeTabsScrollSync();" in js


def test_controls_row_is_sticky_under_the_page_header_on_desktop():
    """: `.controls-row` sticks at the same `--ceres-header-height` offset
    `thead.table-head-sticky` already uses, so the two rows always stick at the
    identical band with no gap and no overlap. Desktop only -- mobile keeps its own
    `.is-mobile-open` fixed-sheet override, untouched."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html:not(.ceres-lw-le-1279) .controls-row {" in css
    rule = css.split("html:not(.ceres-lw-le-1279) .controls-row {", 1)[1].split("}", 1)[0]
    assert "position: sticky;" in rule
    assert "top: var(--ceres-header-height, 3.4rem);" in rule
    assert "z-index: 30;" in rule


def test_edge_tab_geometry_and_z_index_css_guards():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".edge-tabs {" in css
    tabs_rule = css.split(".edge-tabs {", 1)[1].split("}", 1)[0]
    # `position: fixed` (was `absolute`, document coordinates) -- see
    # test_edge_tabs_tracks_the_sticky_controls_row_with_no_extra_frame_lag.
    assert "position: fixed;" in tabs_rule
    assert "position: absolute;" not in tabs_rule
    assert "top: 50%;" not in tabs_rule
    assert "transform: translateY(-50%);" not in tabs_rule
    assert "right: 0;" in tabs_rule
    # lowered this from 40 to 20 (below the header, then raised to 50).
    # (maintainer: "the wallet and flag slider are still not sticky in their
    # respective rows") raised it BACK to 40 once `.controls-row` (the filter row)
    # also became sticky at z-index 30 -- at 20 the row out-ranked the fixed wallet
    # pill and intercepted its clicks. 40 keeps it above its own row (30) and the
    # sticky table head (9) but below the header (50).
    assert "z-index: 40;" in tabs_rule
    assert "z-index: 20;" not in tabs_rule
    # the picker grows leftwards on open, so the column stays right-anchored.
    assert "align-items: flex-end;" in tabs_rule
    # back to the pre- gap (0.4rem) -- #edge-tabs holds only one
    # child (the Wallet picker) now, so the stacked-handle 8px gap is moot.
    assert "gap: 0.4rem;" in tabs_rule
    # `.edge-tab` (the old single-glyph tab) is gone -- the shared
    # resting-state recipe now lives on `.badge-bar` alone; the Wallet edge tab is the
    # new `.edge-picker`/`.edge-tray`/`.edge-opt`/`.edge-toggle` classes below, which
    # replicate canonical.css's `.theme-picker` family VALUES under new names (the
    # Iris consumer protocol forbids redefining those class names).
    assert ".edge-tab {" not in css
    assert ".edge-tab, .badge-bar {" not in css
    assert ".edge-picker {" in css
    picker_rule = css.split(".edge-picker {", 1)[1].split("}", 1)[0]
    # radius reads from Iris's own live measurement now
    # (--edge-radius-outer) -- fallback defaults match the previous hardcoded
    # values exactly.
    assert "border-radius: var(--edge-radius-outer, 5px 0 0 5px);" in picker_rule
    assert "color-mix(in srgb, var(--shadow) 40%, transparent)" in picker_rule
    assert "box-shadow:" not in picker_rule
    assert "backdrop-filter: saturate(120%) blur(2px);" in picker_rule
    assert "overflow: hidden;" in picker_rule
    assert '.edge-picker[data-open="true"] {' in css
    open_rule = css.split('.edge-picker[data-open="true"] {', 1)[1].split("}", 1)[0]
    assert "color-mix(in srgb, var(--shadow) 82%, transparent)" in open_rule
    # the hover/focus glow repainted the glass wallet panel (flicker); it must not return.
    assert not re.search(r"\.edge-picker:(?:hover|focus-visible)[^{]*\{[^}]*box-shadow", css)
    assert ".edge-tray {" in css
    tray_rule = css.split(".edge-tray {", 1)[1].split("}", 1)[0]
    assert "max-width: 0;" in tray_rule
    assert '.edge-picker[data-open="true"] .edge-tray { max-width: 20rem; }' in css
    assert ".edge-opt {" in css
    opt_rule = css.split(".edge-opt {", 1)[1].split("}", 1)[0]
    assert "opacity: .4;" in opt_rule
    # Pre-landing review fix: display/align/justify live on the BASE rule (every
    # option centres its icon identically), not only the active variant.
    assert "display: inline-flex;" in opt_rule
    assert "align-items: center;" in opt_rule
    assert "justify-content: center;" in opt_rule
    assert ".edge-opt:hover { opacity: .8; }" in css
    assert ".edge-opt--active {" in css
    active_rule = css.split(".edge-opt--active {", 1)[1].split("}", 1)[0]
    assert "opacity: 1;" in active_rule
    # the active option also carries the Iris chip now.
    assert "background: color-mix(in srgb, var(--primary) 22%, transparent);" in active_rule
    # Pre-landing review fix: symmetric 5px (mid-tray, surrounded on all sides), not .edge-toggle's 5px 0 0 5px.
    assert "border-radius: 5px;" in active_rule
    assert "border-radius: 5px 0 0 5px;" not in active_rule
    # (maintainer, verbatim: "bug: the resource slide-out double the selected
    # resource. iris handles that better") reverses the old "keep it visible"
    # departure from Iris above -- the selected option now hides in the tray too
    # same as canonical.css's own `.theme-tray .theme-opt--active { display: none; }`.
    assert ".edge-tray .edge-opt--active { display: none; }" in css
    assert ".edge-toggle {" in css
    toggle_rule = css.split(".edge-toggle {", 1)[1].split("}", 1)[0]
    # (maintainer, verbatim: "the wallet slider is missing the background being
    # larger top and bottom... mirror iris"): the toggle no longer stretches to
    # `calc(var(--edge-w) - 4px)`/`block-size: 40px` (the SAME size as the picker
    # itself, no room for a glass frame) -- fixed 32x32, proportionally mirroring
    # Iris's own 48px-pill/40px-toggle ratio. `.edge-picker`'s own new
    # `min-inline-size: var(--edge-w)` (above) keeps the closed pill's total width
    # matching the Iris dock/#badge-bar, same as the old formula did.
    assert "inline-size: var(--edge-chip-w, 32px);" in toggle_rule
    assert "block-size: min(var(--edge-chip-h, 32px), calc(40px - 2 * var(--edge-pad-left, 4px)));" in toggle_rule
    assert "inline-size: calc(var(--edge-w) - 4px);" not in toggle_rule
    assert "block-size: 40px;" not in toggle_rule
    assert "min-inline-size: var(--edge-w);" in picker_rule
    assert "@media (prefers-reduced-motion: reduce) {" in css
    assert ".edge-tray { transition: none; }" in css


def test_wallet_stack_paints_below_the_page_header():
    """. The wallet stack (#edge-tabs and its
    floating desktop panel, .dec-drawer) used to sit at z-index 40/39, ABOVE the
    sticky page header's z-index 10, so it painted over the header once the filter
    row scrolled under it. header.board-header is 50; the wallet stack (:
    back up to 40/39, see test_controls_row_and_wallet_z_ladder for why) is
    still below the header, and still above the sticky table head (9)."""
    css = CERES_CSS.read_text(encoding="utf-8")
    header_rule = css.split("header.board-header {", 1)[1].split("}", 1)[0]
    assert "z-index: 50;" in header_rule
    tabs_rule = css.split(".edge-tabs {", 1)[1].split("}", 1)[0]
    assert "z-index: 40;" in tabs_rule
    assert "html:not(.ceres-lw-le-1279) .dec-drawer {" in css
    drawer_rule = css.split("html:not(.ceres-lw-le-1279) .dec-drawer {", 1)[1].split("}", 1)[0]
    assert "z-index: 39;" in drawer_rule


def test_controls_row_and_wallet_z_ladder():
    """: once `.controls-row` (the filter row) became
    sticky at z-index 30, 's z-index 20 for the wallet stack was no longer
    enough -- MEASURED, the sticky row painted over the fixed wallet pill and
    intercepted its clicks (elementFromPoint on the toggle returned the row, not
    the toggle). The wallet stack moved back up to 40/39 (its pre- values),
    still below the header (50) and the Iris dock (1250), now also above the
    filter row (30) it sits inside."""
    css = CERES_CSS.read_text(encoding="utf-8")
    row_rule = css.split("html:not(.ceres-lw-le-1279) .controls-row {", 1)[1].split("}", 1)[0]
    assert "z-index: 30;" in row_rule
    tabs_rule = css.split(".edge-tabs {", 1)[1].split("}", 1)[0]
    assert "z-index: 40;" in tabs_rule
    drawer_rule = css.split("html:not(.ceres-lw-le-1279) .dec-drawer {", 1)[1].split("}", 1)[0]
    assert "z-index: 39;" in drawer_rule
    header_rule = css.split("header.board-header {", 1)[1].split("}", 1)[0]
    assert "z-index: 50;" in header_rule


def test_badge_flag_tracks_the_sticky_thead_with_no_extra_frame_lag():
    """. MEASURED: wrapping the flag's per-scroll
    position update in its own `requestAnimationFrame` (the pattern
    `scheduleCenterThemePicker()` uses elsewhere in this file) delayed the write to
    the frame AFTER the one the browser's compositor already used to paint
    `#table-head-sticky`'s new sticky position, so the fixed flag trailed the real
    thead by a steady one scroll-step through the whole approach into the sticky
    band (a Playwright measurement stepping scrollY by 10px found a consistent
    ~10px `flag.top` vs `thead.top` gap at every step, closing only once the thead
    reached its clamped `top`). Calling the position update directly inside the
    `scroll` listener -- no extra rAF hop -- removed the gap entirely (same
    measurement: 0px diff at every step). `initBadgeFlag()` must therefore call
    `updateBadgeFlagPosition()` synchronously, with no `requestAnimationFrame` in
    between."""
    js = APP_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function initBadgeFlag\(\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "initBadgeFlag source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "requestAnimationFrame(" not in body
    assert "updateBadgeFlagPosition();" in body
    assert "{ passive: true }" in body


def test_badge_bar_rule_is_a_real_top_level_cssom_rule_with_position_absolute(_css_browser):
    """Review-fail regression guard (2026-09-20,  comment-editing incident):
    a stray early `*/` inside the Z-LADDER comment above `.badge-bar` (ceres.css
    ~2309-2350) once closed that comment early, spilling the ROUND 1 history prose
    out as raw CSS tokens. A browser's CSS parser then folded that unparseable
    prose into the SELECTOR of the next rule it could reach (`.badge-bar {`),
    making the whole selector invalid and dropping the rule entirely --
    `.badge-bar` fell back to the UA default (`position: static`), losing its
    absolute right-edge positioning and its pre-reveal hidden state, while
    `_brace_depth()` (test_penalised_mark.py) stayed balanced throughout
    (the swallowed prose never itself contained a brace, so a brace-counting
    guard alone cannot catch this class of bug). Only a REAL CSS parser -- a real
    browser's CSSOM, here -- catches it: `.badge-bar` must exist as its own
    top-level rule whose declarations set `position: absolute`."""
    page = _css_browser.new_page()

    def route_local(route):
        path = urlparse(route.request.url).path
        if path == "/":
            route.fulfill(
                content_type="text/html",
                body='<!doctype html><html><head><link rel="stylesheet" href="/static/ceres.css"></head><body></body></html>',
            )
            return
        asset = (STATIC / path.removeprefix("/static/")).resolve()
        if not path.startswith("/static/") or not asset.is_relative_to(STATIC) or not asset.is_file():
            route.fulfill(status=404, body="missing fixture asset")
            return
        route.fulfill(path=str(asset))

    page.route("**/*", route_local)
    with closing(page):
        page.goto("http://ir6930-review.test/")
        page.wait_for_function(
            "() => [...document.styleSheets].some(sheet => sheet.href?.endsWith('/static/ceres.css'))"
        )
        info = page.evaluate(
            """() => {
              const sheet = [...document.styleSheets].find(item => item.href?.endsWith('/static/ceres.css'));
              const rules = [...sheet.cssRules].filter(item => item.selectorText === '.badge-bar');
              return {
                count: rules.length,
                positions: rules.map(item => item.style.position),
              };
            }"""
        )
    assert info["count"] > 0, ".badge-bar must be a top-level CSSOM rule (not merged into a broken selector)"
    assert "absolute" in info["positions"], f".badge-bar must set position: absolute somewhere in its top-level rules, found {info['positions']}"


def test_badge_bar_row_paints_the_opaque_chip_and_keeps_flagged_dots_visible(_css_browser):
    """: the row chip and its single status glyph remain visible."""
    page = _css_browser.new_page()

    def route_local(route):
        path = urlparse(route.request.url).path
        if path == "/":
            route.fulfill(
                content_type="text/html",
                body='<!doctype html><html><head><link rel="stylesheet" href="/static/ceres.css"></head><body><div class="badge-bar-row" style="height: 34px"><span class="badge-strip is-flagged"><svg class="badge-status-glyph is-critical" viewBox="0 0 24 24"></svg></span></div></body></html>',
            )
            return
        asset = (STATIC / path.removeprefix("/static/")).resolve()
        if not path.startswith("/static/") or not asset.is_relative_to(STATIC) or not asset.is_file():
            route.fulfill(status=404, body="missing fixture asset")
            return
        route.fulfill(path=str(asset))

    page.route("**/*", route_local)
    with closing(page):
        page.goto("http://s15440-badge-radius.test/")
        row = page.locator(".badge-bar-row")
        strip = page.locator(".badge-strip.is-flagged")
        glyph = page.locator(".badge-status-glyph.is-critical")
        row.wait_for()
        computed = row.evaluate(
            "el => { const s = getComputedStyle(el); return { radius: s.borderRadius, background: s.backgroundColor, width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height }; }"
        )
        inner_style = row.evaluate(
            "el => { const s = getComputedStyle(el, '::before'); return { background: s.backgroundColor, radius: s.borderRadius, left: s.left, right: s.right, blockSize: s.blockSize, top: s.top, transform: s.transform, pointerEvents: s.pointerEvents, zIndex: s.zIndex }; }"
        )
        strip_style = strip.evaluate(
            "el => { const s = getComputedStyle(el); return { display: s.display, alignItems: s.alignItems, background: s.backgroundColor, radius: s.borderRadius, position: s.position, zIndex: s.zIndex }; }"
        )
        glyph_style = glyph.evaluate(
            "el => { const s = getComputedStyle(el); return { width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height, color: s.color }; }"
        )
    assert computed["radius"] == "0px"  # maintainer 2026-09-28: no per-row chip; the column's dark glass carries the badges
    assert computed["background"] in {"transparent", "rgba(0, 0, 0, 0)"}
    assert computed["width"] > 0 and computed["height"] > 0
    assert inner_style["background"] in {"transparent", "rgba(0, 0, 0, 0)"}
    assert inner_style["radius"] == "5px 0px 0px 5px"
    assert inner_style["left"] == "4px" and inner_style["right"] == "0px"
    assert 0 < float(inner_style["blockSize"].removesuffix("px")) < computed["height"]
    assert inner_style["top"] == "17px"
    assert inner_style["transform"] != "none"
    assert inner_style["pointerEvents"] == "none"
    assert inner_style["zIndex"] == "0"
    assert strip_style == {
        "display": "flex",
        "alignItems": "center",
        "background": "rgba(0, 0, 0, 0)",
        "radius": "0px",
        "position": "static",
        "zIndex": "auto",
    }
    assert glyph_style["width"] > 0 and glyph_style["height"] > 0
    assert glyph_style["color"] not in {"transparent", "rgba(0, 0, 0, 0)"}


def test_badge_status_glyph_centers_on_the_visible_chip(_css_browser):
    """The single status glyph centers on the visible chip (the row's ::before)."""
    page = _css_browser.new_page()

    def route_local(route):
        path = urlparse(route.request.url).path
        if path == "/":
            route.fulfill(
                content_type="text/html",
                body=(
                    '<!doctype html><html><head><link rel="stylesheet" href="/static/ceres.css"></head>'
                    '<body><div class="badge-bar-row" style="position:relative; left:auto; right:auto; width:120px; height:34px">'
                    '<span class="badge-strip is-flagged"><svg class="badge-status-glyph is-critical" viewBox="0 0 24 24"></svg></span></div></body></html>'
                ),
            )
            return
        asset = (STATIC / path.removeprefix("/static/")).resolve()
        if not path.startswith("/static/") or not asset.is_relative_to(STATIC) or not asset.is_file():
            route.fulfill(status=404, body="missing fixture asset")
            return
        route.fulfill(path=str(asset))

    page.route("**/*", route_local)
    with closing(page):
        page.goto("http://s16000-status-centering.test/")
        page.locator(".badge-status-glyph.is-critical").wait_for()
        centers = page.evaluate(
            """() => {
              const row = document.querySelector('.badge-bar-row');
              const box = row.getBoundingClientRect(), before = getComputedStyle(row, '::before');
              const chip = {left: box.left + parseFloat(before.left), right: box.right - parseFloat(before.right)};
              const glyph = row.querySelector('.badge-status-glyph').getBoundingClientRect();
              return {chipCenter: (chip.left + chip.right) / 2, glyphCenter: (glyph.left + glyph.right) / 2,
                glyphWidth: glyph.width, glyphHeight: glyph.height, dots: row.querySelectorAll('.badge-dot').length};
            }"""
        )
    assert abs(centers["glyphCenter"] - centers["chipCenter"]) <= 1
    assert centers["glyphWidth"] > 0 and centers["glyphHeight"] > 0
    assert centers["dots"] == 0


def test_thead_sticks_below_the_now_sticky_filter_row():
    """ review-caught regression (own investigation, real-account run):
    once `.controls-row` also stuck at `--ceres-header-height` (the same offset
    the real thead already used), the two collided once both were stuck
    simultaneously -- MEASURED at a deep scroll on the real board (player=player0005,
    scrollY 5000): the filter row (z-index 30) painted OVER the sticky thead's own
    column-label row (z-index 9), hiding "Plot/Rarity/Type/..." entirely.
    `thead.table-head-sticky` must stick to the COMBINED header+filter-row height
    (`--ceres-header-row-height`, table.js updateHeaderHeightVar()), not the bare
    header height alone -- re-measured after the fix: filter row bottom (127px)
    exactly equals thead top (127px) at that same scroll depth, flush and
    non-overlapping."""
    css = CERES_CSS.read_text(encoding="utf-8")
    thead_rule = css.split("thead.table-head-sticky {", 1)[1].split("}", 1)[0]
    assert "top: var(--ceres-header-row-height, 6.5rem);" in thead_rule
    assert "top: var(--ceres-header-height, 3.4rem);" not in thead_rule
    js = TABLE_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"export function updateHeaderHeightVar\(\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "updateHeaderHeightVar source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "querySelector('.controls-row')" in body
    assert "--ceres-header-row-height" in body


def test_badge_bar_geometry_css_guards():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".badge-bar {" in css
    # `.badge-bar {` now appears twice -- the resting-state chrome rule
    # (border/radius/background/box-shadow/backdrop, no longer shared with a
    # `.edge-tab` selector) and, further down, this GEOMETRY rule (position/inset
    # z-index). Pick the one whose body actually holds "position: absolute" rather
    # than assuming which occurrence comes first.
    bar_rule = next(
        (m.group(1) for m in re.finditer(r"(?:^|\n)\.badge-bar \{(.*?)\n\}\n", css, re.S) if "position: absolute;" in m.group(1)),
        None,
    )
    assert bar_rule, "standalone .badge-bar geometry rule not found"
    assert "position: absolute;" in bar_rule
    # (maintainer, verbatim, high: "all three sliders must sit on the furthest
    # right edge of the page... the entire slide bar must be thinner"): `#badge-bar`
    # moved out to a direct child of `<body>` -- `main#board-main`'s own 8px
    # padding was the real (non-scrollbar) source of the old gutter, and `<body>`
    # carries none of its own, so plain `right: 0` there is already flush with the
    # true page edge. Its width shrank to the dedicated `--badge-bar-w` (24px), not
    # the handles' own wider `--edge-w`.
    assert "right: 0;" in bar_rule
    assert "right: calc(-1 * var(--page-gutter, 0px));" not in bar_rule
    assert "inline-size: var(--badge-bar-w);" in bar_rule
    # `top`/`block-size` are JS-written from the scroller's own live rect (app.js
    # centerThemePicker()) -- these CSS vars are only the pre-JS fallback.
    assert "top: var(--badge-bar-top, 0px);" in bar_rule
    assert "block-size: var(--badge-bar-h, 100%);" in bar_rule
    # z-fix ROUND 2: round 1
    # gave `.badge-bar` a real z-index (10) to out-rank the real thead AS ONE UNIT
    # which worked but created a cycle with 's own requirements. Round 2 moves
    # the bar's `backdrop-filter` (the actual stacking-context-forcing culprit) to a
    # `.badge-bar::before` pseudo-element instead, so `.badge-bar` itself carries NO
    # numeric z-index again -- its real children (.badge-bar-head/.badge-bar-row) now
    # compete DIRECTLY against the real thead/expand-row td with their own real
    # values, no cycle. See test_edge_stack.py and
    # test_expand_covers_bar.py for the full ladder.
    assert "z-index: auto;" in bar_rule
    assert "cursor: default;" in bar_rule
    # No `overflow` on .badge-bar itself.
    assert "overflow:" not in bar_rule
    css_rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert not re.search(r"\.badge-bar-head:hover\s*\{", css_rules)
    # the open-state fill mirrors the Iris tray's own open state
    # (canonical.css .theme-picker.is-open), driven off aria-expanded rather than a
    # class (badge-overlay.js::setOpen already sets it). z-fix round 2 moved
    # this fill from `.badge-bar[aria-expanded="true"]` onto
    # `.badge-bar[aria-expanded="true"]::before` (the bar's own `background`
    # `backdrop-filter` live on the pseudo-element now, see test_edge_stack.py).
    # 2026-09-28: the
    # column keeps its own glass while slide-outs are out -- no darker open-state fill any more.
    assert '.badge-bar[aria-expanded="true"]::before {' not in css
    # .badge-bar-head is reinstated -- see test_edge_stack.py for the
    # full guard on its own geometry/style.
    assert ".badge-bar-head {" in css
    assert ".badge-bar-row {" in css


def test_badge_bar_head_backdrop_filter_matches_wallet_picker():
    """ review follow-up:
    `#badge-flag` is `position: fixed` on the body, its own independent
    stacking context already -- none of `.badge-bar`'s own "backdrop-filter
    traps a sticky descendant" caveat applies, so the glass filter is safe to
    copy verbatim like every other Wallet/Iris recipe value already is."""
    css = CERES_CSS.read_text(encoding="utf-8")
    wallet_rule = css.split(".edge-picker {", 1)[1].split("\n}\n", 1)[0]
    assert "backdrop-filter: saturate(120%) blur(2px);" in wallet_rule
    head_rule = css.split(".badge-bar-head {", 1)[1].split("\n}\n", 1)[0]
    assert "backdrop-filter: saturate(120%) blur(2px);" in head_rule
    assert "-webkit-backdrop-filter: saturate(120%) blur(2px);" in head_rule


def test_edge_style_source_guards():
    css = CERES_CSS.read_text(encoding="utf-8")
    head_rule = re.search(r"(?:^|\n)\.badge-bar-head \{(.*?)\n\}\n", css, re.S).group(1)
    row_rule = re.search(r"(?:^|\n)\.badge-bar-row \{(.*?)\n\}\n", css, re.S).group(1)
    wallet_rule = re.search(r"(?:^|\n)\.edge-picker \{(.*?)\n\}\n", css, re.S).group(1)
    assert "block-size: var(--badge-flag-h, calc(var(--ceres-thead-height, 2.75rem) - 2 * var(--badge-flag-inset, 4px)));" in head_rule
    assert not re.search(r"(?:^|;)\s*border-radius\s*:", row_rule)  # maintainer 2026-09-28: no per-row chip; badges sit on the column's dark glass
    assert not re.search(r"(?:^|;)\s*background\s*:", row_rule)
    assert "box-shadow:" not in wallet_rule
    assert "box-shadow:" not in head_rule
    strip_rule = re.search(r"(?:^|\n)\.badge-strip\.is-flagged \{(.*?)\n\}\n", css, re.S).group(1)
    assert strip_rule.strip() == "display: inline-flex; align-items: center; justify-content: center;"
    assert ".edge-picker:hover, .edge-picker:focus-visible { box-shadow" not in css
    css_rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert not re.search(r"\.badge-bar-head:hover\s*\{", css_rules)
    assert ".badge-bar-head:focus-visible { outline: 2px solid var(--accent);" in css
    assert '.badge-bar-head[aria-expanded="true"] { box-shadow:' not in css
    js = APP_JS.read_text(encoding="utf-8")
    badge_js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "flag.style.height = `${flagHeight}px`;" not in js
    # (maintainer) replaces the copied wallet inset: 2px above the flag, header shadow + 2px below, whole pixels.
    assert "const flagTop = Math.round(theadRect.top) + 2;" in js
    assert "Math.round(theadRect.bottom) - 3 - flagTop" in js
    assert "top: `${trRect.top - scrollerRect.top + 1}px`," in badge_js
    assert "height: `${Math.max(0, trRect.height - 2)}px`," in badge_js


def test_table_scroll_reserves_space_for_the_bar_desktop_only():
    """: the
    scroller no longer reserves end space -- the table is 100% of the scroller
    again and runs under #badge-bar. `--table-end-reserve` ( -> 's
    DEC gap, lifted to a shared property by ) is kept, but its consumer
    moved to the last column's own padding (see
    test_last_column_carries_the_reserve_gap below)."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html:not(.ceres-lw-le-1279) .table-scroll {" in css
    rule = css.split("html:not(.ceres-lw-le-1279) .table-scroll {", 1)[1].split("}", 1)[0]
    # the bar shrank (`--edge-w` -> `--badge-bar-w`) and moved to the true
    # page edge, so the reserve now also folds in the measured `--page-gutter` gap
    # (the bar's own containing block no longer coincides with the table's), keeping
    # the DEC numbers' visible clearance from the bar the same as before.
    assert "--table-end-reserve: calc(var(--badge-bar-w) + var(--page-gutter, 0px) + .75rem);" in rule
    assert "padding-inline-end: var(--table-end-reserve);" not in rule


def test_last_column_carries_the_reserve_gap():
    """: the DEC gap moved from the scroller's own end padding into the
    last column's cell padding, so the row background/zebra/border keep running
    under the bar while the DEC value itself keeps its exact prior clearance.
    Full coverage (including the th half's chevron-preserving formula) lives in
    tests/ceres/test_table_under_bar.py; this just confirms the td rule
    exists here too, next to the reserve property it consumes."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html:not(.ceres-lw-le-1279) table.plot-table tbody tr.plot-row td:last-child {" in css
    rule = css.split(
        "html:not(.ceres-lw-le-1279) table.plot-table tbody tr.plot-row td:last-child {",
        1,
    )[1].split("}", 1)[0]
    assert "padding-inline-end: calc(var(--table-end-reserve) + clamp(.25rem, .3125cqi, .375rem));" in rule


def test_start_page_and_no_land_also_hide_edge_tabs():
    css = CERES_CSS.read_text(encoding="utf-8")
    no_land_rule = re.search(r"body\.no-land [^\n]*\{ display: none !important; \}", css)
    assert no_land_rule and "#edge-tabs" in no_land_rule.group(0)
    start_page_rule = re.search(r"body\.start-page [^\n]*\n[^\n]*\{ display: none !important; \}", css)
    assert start_page_rule and "#edge-tabs" in start_page_rule.group(0)


def test_changes_panel_power_tile_no_longer_stretches():
    """ reverted the  stretch this test used to guard -- the
    Power/Totem/Title tile is back to the base fixed `var(--slot-w)` (107x150),
    no `width: 100%` override. See test_changes_panel_tile_row_is_content_sized_again
    for the full revert + the .ch-plot content-sizing replacement."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".changes-panel .fp-tiles.slot-grid .slot-power .slot-tile { width: 100%; }" not in css


def test_badge_bar_head_height_is_measured_next_to_the_header_height():
    table = TABLE_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"export function updateHeaderHeightVar\(\) \{.*?\n\}\n", table, re.S)
    assert fn_match, "updateHeaderHeightVar source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "getElementById('table-head-sticky')" in body
    assert "--ceres-thead-height" in body
    assert "theadHeight" in body


def test_badge_overlay_container_paints_nothing_and_row_carries_the_slide_recipe():
    """: `#badge-overlay` itself is a bare positioning container now -- the old
    shared panel's fill/tint/slide moved onto each `.badge-overlay-row`, which is sized
    to its own badges only (`max-content`), not a width shared across every row."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".badge-overlay {" in css
    container_rule = css.split(".badge-overlay {", 1)[1].split("\n}\n", 1)[0]
    assert "background:" not in container_rule
    assert "backdrop-filter:" not in container_rule
    assert "transform:" not in container_rule
    # `#badge-overlay` stays a child of `#plot-table-scroll` (only
    # `#badge-bar`/`#badge-flag` moved out) -- its own `right` now anchors to
    # `#badge-bar`'s new, narrower width, not `--edge-w`.
    # measured fix: `+ var(--page-gutter, 0px)` -> `- var(--page-gutter
    # 0px)` -- this container's own containing block (#plot-table-scroll) is
    # already inset by --page-gutter, so adding it again double-counted the
    # gutter and left a real 16px gap between the slid-out rows and the bar.
    assert "right: calc(var(--badge-bar-w) - var(--page-gutter, 0px));" in container_rule
    assert ".badge-overlay-row {" in css
    row_rule = css.split(".badge-overlay-row {", 1)[1].split("\n}\n", 1)[0]
    assert "inline-size: max-content;" in row_rule
    assert "max-inline-size: 45vw;" in row_rule
    assert "right: 0;" in row_rule
    assert "left: auto;" in row_rule
    # the dark-theme fill now mirrors the Iris OPEN-tray recipe directly
    # (canonical.css .theme-picker.is-open); the --bg2 tint lives on in light/wood
    # only (see test_edge_stack.py).
    assert "color-mix(in srgb, var(--shadow) 82%, transparent)" in row_rule
    assert "backdrop-filter: saturate(130%) blur(4px);" in row_rule
    assert "transform: translateX(100%);" in row_rule
    assert "box-shadow:" not in row_rule
    assert ".badge-overlay-row.is-open {" in css
    open_rule = css.split(".badge-overlay-row.is-open {", 1)[1].split("}", 1)[0]
    assert "transform: translateX(0);" in open_rule
    # Pre-landing review fix: the reduced-motion rule must name both the base and
    # `.is-open` selectors (see test_row_slideouts.py for the dedicated guard).
    assert ".badge-overlay-row, .badge-overlay-row.is-open { transition: none; }" in css
    # Slimmer badges inside each row-slideout only -- the mobile card is untouched.
    assert ".badge-overlay-row .badge-row > * {" in css
    slim_rule = css.split(".badge-overlay-row .badge-row > * {", 1)[1].split("}", 1)[0]
    assert "padding-block: .05rem;" in slim_rule
    assert "padding-inline: .45rem;" in slim_rule
    assert "font-size: .78rem;" in slim_rule


def test_center_theme_picker_aligns_dock_and_edge_tabs_to_the_table_gutter():
    """ ("wallet slider totally displaced"), pre-landing review fix: the gutter
    read must be guarded on the scroller having a REAL (non-zero-width) box -- a
    `display: none` element's getBoundingClientRect() still returns an all-zero rect,
    and an unguarded read computed a bogus gutter that pushed the picker off-screen
    below 1280px and on the start/no-land page (both hide #plot-table-scroll)."""
    app = APP_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function centerThemePicker\(\) \{.*?\n\}\n", app, re.S)
    assert fn_match, "centerThemePicker source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "getElementById('plot-table-scroll')" in body
    assert "scrollerRect.width > 0" in body
    # the dock/#edge-tabs no longer take a JS `.style.right` write at all -- both
    # are plain CSS `right: 0` now. The scroller's live rect is still read here, but
    # only to publish `--page-gutter` for `#badge-bar` (which DOES still need a
    # JS-computed offset, since it moved outside the scroller to `<body>`).
    assert "dock.style.right" not in body
    assert "edgeTabs.style.right" not in body
    assert "documentElement.style.setProperty('--page-gutter'" in body


def test_badge_overlay_js_wires_row_controls_and_a_flag_group_toggle():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "bar.setAttribute('role', 'group');" in js
    assert "bar.removeAttribute('tabindex');" in js
    assert "bar.addEventListener('click', (event) => {" in js
    assert "event.target.closest('.badge-bar-row')" in js
    assert "setRowOpen(barRow.dataset.deedUid," in js
    assert "barRow.setAttribute('role', 'button');" in js
    assert "barRow.setAttribute('tabindex', '0');" in js
    assert "bar.addEventListener('keydown'" in js
    assert "setRowOpen(barRow.dataset.deedUid," in js
    assert "flag.addEventListener('click', () => {" in js
    assert "toggleGroup();" in js
    assert "let groupOpen" not in js
    assert "localStorage" not in js and "sessionStorage" not in js
    assert "overlayRow.id = `badge-overlay-row-${encodeURIComponent(deedUid)}`;" in js
    assert "barRow.setAttribute('aria-controls', `badge-overlay-row-${encodeURIComponent(deedUid)}`);" in js
    assert "if (!open) continue;" in js
    assert "if (overlayRowJustCreated && open) pendingOpen.push(overlayRow);" in js
    assert "void overlayRow.offsetWidth" not in js
    assert "void panel.offsetWidth; // V: ONE layout" in js
    assert "if (openRows.size === 0 && reason === 'scroll') return;" in js
    assert "mounted.length > 0 && mounted.every((uid) => openRows.has(uid));" in js
    assert "scrollWidth" not in js

def test_rows_js_refresh_targets_the_bar_row_not_a_table_cell():
    rows = ROWS_JS.read_text(encoding="utf-8")
    assert "document.querySelector(`#badge-bar .badge-bar-row" in rows
    assert "badge-strip-cell" not in rows
    fn_match = re.search(r"function plotRowCellsHtml\(row(?:, rowIndex = null)?\) \{.*?\n\}\n", rows, re.S)
    assert fn_match, "plotRowCellsHtml source shape changed -- update this test's slice"
    assert "badgeStripHtml(row)" not in fn_match.group(0)


def test_regional_dec_exports_open_wallet_drawer_and_wires_the_edge_tab():
    # the toggle (#wallet-edge-tab) now opens/closes the tray, not the drawer
    # directly -- the drawer opens from an .edge-opt click instead. See
    # test_wallet_tray.py for the full picker/tray/persistence coverage.
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    assert "export async function openWalletDrawer(symbol) {" in js
    assert "getElementById('wallet-edge-tab')" in js
    assert "walletToggleBtn.addEventListener('click', () => toggleWalletPicker());" in js
    # Pre-landing review fix: bound as an arrow wrapper, not passed directly -- a bare
    # `openWalletDrawer` handler would receive the click Event as its `symbol` arg.
    assert "trigger.addEventListener('click', () => openWalletDrawer());" in js
    assert "function setWalletTriggersExpanded(open) {" in js
    assert "querySelectorAll('[aria-controls=\"regional-dec-drawer\"]')" in js


def test_changes_modal_tile_row_is_content_sized_again():
    """ ( -- tiles stretched to 122-196px instead of their base 107x150):
    reverts 's stretch entirely. The tile row is back to the base
    `.fp-tiles.slot-grid { ... inline-size: max-content ... }` rule (no
    `.changes-panel`-scoped `inline-size: 100%` override, and no `flex: 1 1 ...`
    on its tiles/power-slot/badge-row cells). 's own intent (no empty
    space beside a short tile row) is kept a different way: `.changes-panel
    .ch-plot` (the per-plot card) sizes to its own content instead of the
    modal's full `min-width: 68rem` -- see that rule's own comment."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".changes-panel .fp-tiles.slot-grid { inline-size: 100%; }" not in css
    assert ".changes-panel .fp-tiles.slot-grid .slot-tile, .changes-panel .fp-tiles.slot-grid .slot-power { flex: 1 1 6.5rem; max-inline-size: none; }" not in css
    assert ".changes-panel .fp-badge-row { inline-size: 100%; }" not in css
    assert ".changes-panel .fp-badge-row .editor-base-pp-cell { flex: 1 1 6.5rem; max-inline-size: none; }" not in css
    # The per-plot card is capped at the modal's width. It later went back to spanning the
    # panel (`inline-size: 100%`, pinned by test_changes_layout.py: the strip now holds only
    # the changed slots and must fit the panel); the tiles inside it stay content-sized,
    # which the four `not in` checks above still guard.
    assert ".changes-panel .ch-plot { box-sizing: border-box; inline-size: 100%; min-inline-size: 0; max-inline-size: 100%; }" in css
    assert ":is(.fill-panel, .changes-panel) .fp-tiles.slot-grid {" in css and "inline-size: max-content;" in css.split(":is(.fill-panel, .changes-panel) .fp-tiles.slot-grid {", 1)[1].split("}", 1)[0]
    # The modal's own strip-table width need () is untouched.
    assert ".harvest-panel.changes-panel { width: fit-content; min-width: min(68rem, calc(var(--ceres-vw, 1vw) * 100 - 2rem)); max-width: calc(var(--ceres-vw, 1vw) * 100 - 2rem); }" in css
    # The editor band has its own sizing (--eos-slot-w etc.) and is untouched by this change.
    editor_band_rule = re.search(r"\.editor-band \.slot-grid \{[^}]*\}", css)
    assert editor_band_rule, "editor-band .slot-grid base rule missing"
    assert "flex: 1 1 6.5rem" not in editor_band_rule.group(0)


def test_overlay_open_state_pure_function_executed_in_a_vm():
    """Overlay state is not restored from local or session storage."""
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "overlayOpenState" not in js
    assert "const openRows = new Set();" in js

def test_node_check_touched_js_files():
    for path in (APP_JS, TABLE_JS, ROWS_JS, BADGE_OVERLAY_JS, REGIONAL_DEC_JS):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"
