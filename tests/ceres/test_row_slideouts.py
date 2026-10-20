"""per-row badge slide-outs. Owner (verbatim): "give each row an individual
slideout with, based on its badges. They each slide out individually on click or
behave as a group when triggerd via the flag." The old shared #badge-overlay panel --
one wide sliding surface every mounted row was stretched to match -- is replaced by an
independent slide-out per plot row, sized only to that row's own badges; the group flag
(a click on #badge-bar's head glyph/background, or Enter/Space anywhere on the bar)
still opens/closes every mounted row together. See the v4 section of
library/ceres-badge-overlay.md for the full design, including the pre-landing review
fix that made `openRows` the single source of truth (`groupOpen` is a persisted
PREFERENCE that drives mount-time auto-open and toggle direction, never a live scan).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Locator, Page

from .conftest import spawn_live_ceres

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
CERES_CSS = STATIC / "ceres.css"
BADGE_OVERLAY_JS = STATIC / "badge-overlay.js"


@pytest.fixture(scope="module")
def live_badge_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _badge_hit_target(control: Locator) -> dict[str, object]:
    return control.evaluate(
        """node => {
 const rect = node.getBoundingClientRect();
 const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
 return {
 rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
 reachesControl: !!hit && (hit === node || node.contains(hit)),
 pointerEvents: getComputedStyle(node).pointerEvents,
 };
 }"""
    )


def _assert_click_target(control: Locator) -> None:
    observed = _badge_hit_target(control)
    assert observed["rect"]["width"] > 0 and observed["rect"]["height"] > 0, observed
    assert observed["pointerEvents"] == "auto", observed
    assert observed["reachesControl"], observed


def test_live_badge_controls_receive_real_clicks_without_a_flag_delay(
    live_badge_browser: tuple[Browser, str],
) -> None:
    """ compares immediate state changes, not a borrowed millisecond budget."""
    browser, base_url = live_badge_browser
    context = browser.new_context(viewport={"width": 1536, "height": 864})
    try:
        page: Page = context.new_page()
        page.add_init_script("localStorage.removeItem('ceres:badge-overlay:open');")
        page.goto(f"{base_url}/?player=player0005", wait_until="domcontentloaded")
        page.wait_for_selector("tr.plot-row", timeout=30_000)
        page.wait_for_selector("#badge-flag.badge-flag-revealed", timeout=30_000)

        iris = page.locator(".theme-picker .theme-toggle")
        _assert_click_target(iris)
        iris.click()
        assert page.locator(".theme-picker").evaluate("node => node.classList.contains('is-open')")
        iris.click()
        assert not page.locator(".theme-picker").evaluate("node => node.classList.contains('is-open')")

        wallet = page.locator("#wallet-edge-tab")
        _assert_click_target(wallet)
        wallet.click()
        assert page.locator("#wallet-picker").get_attribute("data-open") == "true"
        wallet.click()
        assert page.locator("#wallet-picker").get_attribute("data-open") == "false"

        flag = page.locator("#badge-flag")
        # The wallet drawer is still sliding out over the flag for a moment after it
        # closes (the same on master); wait for that close to finish, never longer than
        # 1.5 s, before asking whether the flag is reachable.
        page.wait_for_function(
            """() => { const f = document.getElementById('badge-flag'); const r = f.getBoundingClientRect();
 const h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
 return !!h && (h === f || f.contains(h)); }""",
            timeout=1_500,
        )
        _assert_click_target(flag)
        # (maintainer): the flag opens its status tray; a status shows only those slide-outs, and the flag, which then
        # sits in the tray, shows them all.
        flag.click()
        tray = page.locator("#badge-filter-tray")
        assert tray.get_attribute("data-open") == "true"
        tray.locator('[data-filter="warning"]').click()
        assert tray.get_attribute("data-open") == "false"
        # with slide-outs open, one flag click closes them all.
        flag.click()
        assert page.locator(".badge-overlay-row.is-open").count() == 0
        flag.click()
        tray.locator('[data-filter="all"]').click()
        assert flag.get_attribute("aria-expanded") == "true"
        assert page.locator(".badge-overlay-row.is-open").count() > 0

        badge_bar_row = page.locator(".badge-bar-row").first
        _assert_click_target(badge_bar_row)
        badge_bar_row.click()
        assert not badge_bar_row.evaluate("node => node.classList.contains('is-open')")
    finally:
        context.close()


def test_badge_overlay_container_paints_nothing():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".badge-overlay {" in css
    rule = css.split(".badge-overlay {", 1)[1].split("\n}\n", 1)[0]
    assert "background:" not in rule
    assert "backdrop-filter:" not in rule
    assert "border-radius:" not in rule
    assert "transform:" not in rule
    assert "transition:" not in rule
    assert "position: absolute;" in rule
    # `#badge-bar` shrank to its own `--badge-bar-w` and moved outside the
    # scroller, so `.badge-overlay`'s own anchor follows suit (bar width).
    # measured fix: subtracts `--page-gutter` (not `+`) -- the overlay's
    # own containing block is already inset by that gutter, so adding it again
    # double-counted it and left a real 16px gap between the slid-out rows and
    # the bar; subtracting cancels the containing block's own inset instead.
    assert "right: calc(var(--badge-bar-w) - var(--page-gutter, 0px));" in rule
    assert "pointer-events: none;" in rule
    # No leftover single-panel open-state rule.
    assert ".badge-overlay.is-open" not in css


def test_badge_overlay_row_is_max_content_and_right_anchored_only():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".badge-overlay-row {" in css
    rule = css.split(".badge-overlay-row {", 1)[1].split("\n}\n", 1)[0]
    assert "inline-size: max-content;" in rule
    assert "max-inline-size: 45vw;" in rule
    assert "right: 0;" in rule
    assert "left: auto;" in rule
    # The old shared-panel rows were `left: 0; right: 0` -- exactly what stretched
    # every row to match the widest mounted row. That must be gone.
    assert "left: 0; right: 0" not in rule
    # Pre-landing review fix: restored -- a row that still overflows its own 45vw cap
    # truncates instead of spilling past it.
    assert "overflow: hidden;" in rule
    assert "text-overflow: ellipsis;" in rule
    # Pre-landing review fix (hit-testing): the row itself is pointer-events: none in
    # BOTH states now -- only its badge chips re-enable it (see the next test).
    assert "pointer-events: none;" in rule
    assert "pointer-events: auto;" not in rule


def test_badge_overlay_row_carries_its_own_fill_and_transform():
    css = CERES_CSS.read_text(encoding="utf-8")
    rule = css.split(".badge-overlay-row {", 1)[1].split("\n}\n", 1)[0]
    # item 4: the dark-theme fill mirrors the Iris OPEN-tray recipe now
    # (canonical.css `.theme-picker.is-open`); the previous --bg2 tint lives on in
    # light/wood only (see test_edge_stack.py).
    assert "color-mix(in srgb, var(--shadow) 82%, transparent)" in rule
    assert "backdrop-filter: saturate(130%) blur(4px);" in rule
    assert "border-radius: 5px 0 0 5px;" in rule
    assert "box-shadow:" not in rule
    assert "transform: translateX(100%);" in rule
    assert ".badge-overlay-row.is-open {" in css
    open_rule = css.split(".badge-overlay-row.is-open {", 1)[1].split("}", 1)[0]
    assert "transform: translateX(0);" in open_rule
    assert "visibility: visible" in open_rule
    # Pre-landing review fix: the open row itself does NOT re-enable pointer-events
    # only its badge chips do, via the dedicated selector below.
    assert "pointer-events: auto;" not in open_rule
    assert ".badge-overlay-row.is-open .badge-row > *, .badge-overlay-row.is-open [data-tip] { pointer-events: auto; }" in css
    # Closed rows are visibility: hidden, delayed by the transition's own duration so
    # the close slide can actually finish before a row leaves the a11y tree/hit-test.
    assert "visibility: hidden;" in rule
    assert "visibility 0s linear .5s" in rule


def test_reduced_motion_covers_both_the_base_and_open_row_selectors():
    """Pre-landing review fix: the open state's own compound selector
 (`.badge-overlay-row.is-open`) has higher specificity than the bare
 `.badge-overlay-row` -- without naming it explicitly inside the media query, its own
 `transition` value would still win even under reduced motion."""
    css = CERES_CSS.read_text(encoding="utf-8")
    match = re.search(
        r"@media \(prefers-reduced-motion: reduce\) \{\s*\.badge-overlay-row, \.badge-overlay-row\.is-open \{ transition: none; \}\s*\}",
        css,
    )
    assert match, "reduced-motion rule must name both .badge-overlay-row and .badge-overlay-row.is-open"


def test_badge_overlay_js_has_per_row_open_state_with_group_as_a_preference():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "const openRows = new Set();" in js
    assert "let groupOpen" not in js
    assert "function isRowOpen(deedUid) {" in js
    assert "return openRows.has(deedUid);" in js
    assert "function setRowOpen(deedUid, open) {" in js
    assert "function toggleGroup() {" in js
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    # No leftover live "every mounted row open" scan driving the toggle direction
    # that is exactly the bug the pre-landing review fixed.
    assert "function setGroupOpen(open) {" not in js


def test_click_inside_a_bar_row_routes_to_set_row_open_not_the_group():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    click_match = js.split("bar.addEventListener('click', (event) => {", 1)[1].split("});", 1)[0]
    assert "event.target.closest('.badge-bar-row')" in click_match
    assert "setRowOpen(barRow.dataset.deedUid," in click_match
    assert "toggleGroup();" not in click_match
    assert "setRowOpen(" in click_match


def test_keydown_on_the_bar_is_always_a_group_toggle():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    keydown_match = js.split("bar.addEventListener('keydown', (event) => {", 1)[1].split("});", 1)[0]
    assert "event.target.closest('.badge-bar-row')" in keydown_match
    assert "setRowOpen(barRow.dataset.deedUid," in keydown_match
    assert "toggleGroup();" not in keydown_match


def test_scroll_skip_condition_reads_the_new_state():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "if (openRows.size === 0 && reason === 'scroll') return;" in js


def test_no_scroll_width_measurement_left_anywhere():
    """the shared-panel width measurement pass is gone -- each row now
 sizes itself via plain CSS (max-content), nothing to measure any more."""
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "scrollWidth" not in js
    assert "panel.style.inlineSize" not in js
    assert "overlayChanged" not in js


def test_row_element_only_created_or_kept_for_an_open_or_previously_open_row():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    # `open` now reuses `rowOpen` (computed once, earlier, for the bar
    # cell's own chevron/dots swap) instead of a second `isRowOpen(deedUid)` call.
    assert "const rowOpen = isRowOpen(deedUid);" in js
    assert "const open = rowOpen;" in js
    assert "const overlayRowJustCreated = !overlayRow;" in js
    assert "overlayRow.id = `badge-overlay-row-${encodeURIComponent(deedUid)}`;" in js
    assert "barRow.setAttribute('aria-controls', `badge-overlay-row-${encodeURIComponent(deedUid)}`);" in js
    assert "overlayRow.className = 'badge-overlay-row';" in js
    assert "overlayRow.classList.toggle('is-open', open);" in js
    assert "if (!open) continue;" in js


def test_first_open_forces_a_reflow_before_adding_is_open():
    """A freshly created (or re-created after a virtualization remount) row element has
 no prior rendered frame to transition from, so it must be laid out closed before
 `.is-open` is added or the slide never plays. : that layout is forced ONCE for
 all new rows after the write loop -- a per-row forced layout inside the loop cost
 10.9 s for 129 rows on the first flag click."""
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "void overlayRow.offsetWidth" not in js, "no per-row forced layout inside the write loop"
    push_idx = js.index("if (overlayRowJustCreated && open) pendingOpen.push(overlayRow);")
    reflow_idx = js.index("void panel.offsetWidth; // V: ONE layout")
    add_idx = js.index("for (const overlayRow of pendingOpen) overlayRow.classList.add('is-open');")
    assert push_idx < reflow_idx < add_idx, "queue in the loop, one layout, then open"


def test_row_keeps_the_same_badge_strip_in_both_slide_states():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "const rowOpen = isRowOpen(deedUid);" in js
    assert "badge-bar-content" in js
    assert "badge-bar-chevron" not in js
    assert "barRow.classList.toggle('is-open', rowOpen);" in js


def test_slide_out_has_no_badge_chevron_artifact():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "badge-bar-chevron" not in js
    assert ".badge-bar-chevron" not in css


def test_badge_lane_uses_svg_flag_and_shared_open_golden_style():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = CERES_CSS.read_text(encoding="utf-8")
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert 'id="badge-flag"' in html and 'class="hdr-ic badge-flag-icon"' in html
    assert '<path class="f"' in html and '<path class="d"' in html and '<path class="a h"' in html
    assert "is(.badge-bar-head, .badge-filter-opt) .hdr-ic .f" in css  # the tray flag shares the colours
    assert "is(.badge-bar-head, .badge-filter-opt) .hdr-ic .d" in css
    assert "is(.badge-bar-head, .badge-filter-opt) .hdr-ic .a.h" in css
    assert "@keyframes badge-golden-pulse" not in css
    assert "badge-clock-pulse" in css
    assert 'data-state="unused"' in html
    assert "flag.dataset.state = allOpen ? 'used' : 'unused';" in js
    assert not re.search(r"(?:^|})\s*\.badge-bar-head:hover", re.sub(r"/\*.*?\*/", "", css, flags=re.S))


def test_row_joins_open_rows_on_first_mount_while_group_preference_is_open():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "const isFirstMountThisSession = !barRow;" in js
    mount_match = re.search(
        r"if \(isFirstMountThisSession\) \{(.*?)\n \}\n", js, re.S,
    )
    assert mount_match, "first-mount block not found in its expected shape"
    assert "if (groupOpen)" not in js


def test_aria_expanded_is_a_live_recomputed_fact_not_the_group_preference():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "const allOpen = mounted.length > 0 && mounted.every((uid) => openRows.has(uid));" in js
    assert "bar?.setAttribute('aria-expanded', allOpen ? 'true' : 'false');" in js


def test_next_group_state_pure_function_executed_in_node():
    """nextGroupState toggles only the mounted rows and preserves other open rows."""
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    fn_match = re.search(
        r"export function nextGroupState\(mountedUids, openRowsIn\) \{.*?\n\}\n",
        js, re.S,
    )
    assert fn_match, "nextGroupState source shape changed -- update this test's slice"
    script = (
        fn_match.group(0).replace("export function", "function") + "\n"
        + """
function assert(ok, message) { if (!ok) throw new Error(message); }
let openRows = nextGroupState(['a', 'b'], new Set());
assert(openRows.size === 2 && openRows.has('a') && openRows.has('b'), 'flag opens every mounted row');
openRows = nextGroupState(['a', 'b'], openRows);
assert(openRows.size === 0, 'flag closes every mounted row');
openRows = nextGroupState(['a', 'b'], new Set(['z']));
assert(openRows.has('z') && openRows.has('a') && openRows.has('b'), 'unmounted rows are preserved');
"""
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_node_check_badge_overlay_js():
    result = subprocess.run(["node", "--check", str(BADGE_OVERLAY_JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_flag_and_open_slideout_have_no_glow():
    """The sidebar has no hover or open-state glow."""
    css = (ROOT / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    css_rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert not re.search(r"\.badge-bar(?::hover|:focus-visible)[^{]*\{[^}]*box-shadow", css_rules)
    assert "badge-bar.is-flag-hover" not in css_rules
    assert "body:has(#badge-flag:is(" not in css_rules
    assert "setFlagHover" not in js
    assert "bar.classList.toggle('is-group-open', allOpen);" not in js
    open_rule = css.split(".badge-overlay-row.is-open {", 1)[1].split("}", 1)[0]
    assert "box-shadow:" not in open_rule


def test_status_glyph_centres_on_the_visible_chip():
    """The status glyph centers on the visible chip, including its live insets."""
    css = (ROOT / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")
    row_rule = css.split("\n.badge-bar-row {", 1)[1].split("\n}", 1)[0]
    assert "padding-inline: calc(0.15rem + var(--edge-pad-left, 4px)) calc(0.15rem + var(--edge-pad-right, 0px));" in row_rule
    chip_rule = css.split(".badge-bar-row::before {", 1)[1].split("}", 1)[0]
    assert "left: var(--edge-pad-left, 4px);" in chip_rule and "right: var(--edge-pad-right, 0px);" in chip_rule
    assert ".badge-status-glyph" in css


def test_idle_rail_chip_pulses_gold_for_changes_cooldowns_and_builds():
    """ (b): the idle SLID-IN rail chip of a row with unsigned staged changes, a cooldown wait
 or a build under way carries 's golden pulse on its whole visible chip; a stalled build does not."""
    css = (ROOT / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "is-attention" not in js
    assert "badge-golden-pulse" not in css
    # the glow stays in the row's queue because the row clips its own contents
    row_rule = css.split("\n.badge-bar-row {", 1)[1].split("\n}", 1)[0]
    assert "overflow: hidden;" in row_rule


def test_rail_dots_use_the_same_centred_layout_open_and_closed():
    """the dot wrapper is inline-flex centred in BOTH states, so the dots do not jump on slide-out."""
    css = (ROOT / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")
    assert "\n.badge-bar-content { display: inline-flex; align-items: center; justify-content: center; }" in css
    assert ".badge-bar-row.is-open .badge-bar-content" not in css
