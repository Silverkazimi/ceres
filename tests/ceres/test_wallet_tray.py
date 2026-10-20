""" (maintainer, verbatim): "Make the top of the wallet mirror exactly the icon
only slide-out of Iris. Use each resource icon and a click open the connected modal
and slide it in after click with the selected icon." The single-glyph #wallet-edge-tab
becomes #wallet-picker: an icon-only tray of every WALLET_TABS resource (mirroring the
Iris theme picker's own tray/toggle shape under new class names, never redefining
Iris's own `.theme-*` classes) whose toggle shows the currently selected resource and
whose tray options each open the wallet drawer directly on that resource. The drawer's
own tab row (wallet-resources.js walletTabsHtml) becomes icon-only too. See
library/ceres-edge-tabs.md §v5 and library/ceres-wallet.md.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
INDEX_HTML = STATIC / "index.html"
CERES_CSS = STATIC / "ceres.css"
REGIONAL_DEC_JS = STATIC / "regional-dec.js"
WALLET_RESOURCES_JS = STATIC / "wallet-resources.js"


def _picker_html() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(r'<div id="wallet-picker"[^\n]*', html)
    assert match, "could not find #wallet-picker markup"
    return match.group(0)


def test_picker_markup_structure():
    markup = _picker_html()
    assert 'class="edge-picker"' in markup
    assert 'data-open="false"' in markup
    # Pre-landing review fix: the toggle's aria-controls now targets the TRAY it
    # opens/closes (it no longer opens the drawer directly), so the tray needs its own id.
    assert '<div id="wallet-picker-tray" class="edge-tray" role="tablist" aria-label="Wallet resource"></div>' in markup
    toggle = re.search(r'<button[^>]*id="wallet-edge-tab"[^>]*>', markup)
    assert toggle, "toggle button not found"
    tag = toggle.group(0)
    assert 'class="edge-toggle"' in tag
    assert 'aria-haspopup="true"' in tag
    assert 'aria-expanded="false"' in tag
    assert 'aria-controls="wallet-picker-tray"' in tag
    assert 'aria-controls="regional-dec-drawer"' not in tag
    assert 'data-tip="Wallet"' in tag
    assert 'aria-label="Wallet"' in tag
    assert not re.search(r'\btitle="[^"]+"', tag)


def test_css_never_redefines_the_iris_theme_classes():
    css = CERES_CSS.read_text(encoding="utf-8")
    for selector in (".theme-picker", ".theme-tray", ".theme-opt", ".theme-toggle"):
        assert f"{selector} {{" not in css, f"{selector} must not be redefined in ceres.css (Iris consumer protocol)"


def test_edge_picker_css_values_replicate_iris():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".edge-picker {" in css
    picker_rule = css.split(".edge-picker {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in picker_rule
    # ("slightly smaller in height"): the wallet picker is 40px; the Iris picker itself stays 48px (vendored, untouched).
    assert "height: 40px;" in picker_rule
    assert "height: 48px;" not in picker_rule
    # (maintainer, verbatim: "the wallet slider is missing the background being
    # larger top and bottom... mirror iris"): `.edge-toggle` shrank to a fixed 32px
    # (below), so the picker's own closed-state content would otherwise be narrower
    # than `--edge-w` -- `min-inline-size` restores the width match without
    # constraining the open-state tray (frozen, ).
    assert "min-inline-size: var(--edge-w);" in picker_rule
    # (maintainer, verbatim: "But all icons and borders in the slider are
    # unaligned"): padding/radius read from Iris's own LIVE measurement now
    # (--edge-pad-left/--edge-pad-right/--edge-radius-outer) -- fallback defaults
    # match the previous hardcoded values.
    assert "padding: 0 var(--edge-pad-right, 0px) 0 var(--edge-pad-left, 4px);" in picker_rule
    assert "border-radius: var(--edge-radius-outer, 5px 0 0 5px);" in picker_rule
    assert "color-mix(in srgb, var(--shadow) 40%, transparent)" in picker_rule
    assert "box-shadow:" not in picker_rule
    assert "backdrop-filter: saturate(120%) blur(2px);" in picker_rule
    assert "overflow: hidden;" in picker_rule
    assert "cursor: pointer;" in picker_rule
    assert '.edge-picker[data-open="true"] {' in css
    open_rule = css.split('.edge-picker[data-open="true"] {', 1)[1].split("}", 1)[0]
    assert "color-mix(in srgb, var(--shadow) 82%, transparent)" in open_rule
    assert "saturate(130%) blur(4px)" in open_rule
    assert ".edge-picker:hover, .edge-picker:focus-visible { box-shadow" not in css
    assert ".edge-tray {" in css
    tray_rule = css.split(".edge-tray {", 1)[1].split("}", 1)[0]
    assert "max-width: 0;" in tray_rule
    assert "overflow: hidden;" in tray_rule
    assert '.edge-picker[data-open="true"] .edge-tray { max-width: 20rem; }' in css
    assert ".edge-opt {" in css
    opt_rule = css.split(".edge-opt {", 1)[1].split("}", 1)[0]
    # ("slightly smaller in height"): 34px tray options, shorter than the 40px toggle.
    assert "height: 34px;" in opt_rule
    assert "opacity: .4;" in opt_rule
    assert ".edge-opt:hover { opacity: .8; }" in css
    # Pre-landing review fix, deliberate departure from Iris: Iris's own
    # `.theme-opt--active` HIDES the selected option (it always shows the selection
    # separately as the toggle glyph instead). The Wallet picker keeps every resource
    # reachable from the tray at all times, so the selected one is marked by full
    # opacity instead of removed.
    # Pre-landing review fix: display/align/justify moved to the BASE .edge-opt rule
    # (every option centres its icon identically, selected or not) instead of only
    # the active variant.
    assert ".edge-opt {" in css
    base_opt_rule = css.split(".edge-opt {", 1)[1].split("}", 1)[0]
    assert "display: inline-flex;" in base_opt_rule
    assert "align-items: center;" in base_opt_rule
    assert "justify-content: center;" in base_opt_rule
    assert ".edge-opt--active {" in css
    active_rule = css.split(".edge-opt--active {", 1)[1].split("}", 1)[0]
    assert "opacity: 1;" in active_rule
    assert "background: color-mix(in srgb, var(--primary) 22%, transparent);" in active_rule
    assert "border-radius: 5px;" in active_rule
    assert "border-radius: 5px 0 0 5px;" not in active_rule
    assert "box-shadow: 0 8px 22px color-mix(in srgb, var(--shadow) 35%, transparent), inset 0 1px 0 color-mix(in srgb, var(--hi) 6%, transparent);" in active_rule
    # (maintainer, verbatim: "bug: the resource slide-out double the selected
    # resource. iris handles that better") REVERSES the "keep it visible" decision
    # above: the selected option now hides in the tray too, same as Iris's own
    # `.theme-tray .theme-opt--active { display: none; }` (canonical.css) -- it stays
    # reachable via the toggle itself (). The bare `.edge-opt--active` rule
    # above is kept (unused while hidden) only so a future reuse of the class outside
    # the tray is not surprised by its absence.
    assert ".edge-tray .edge-opt--active { display: none; }" in css
    assert ".edge-toggle {" in css
    toggle_rule = css.split(".edge-toggle {", 1)[1].split("}", 1)[0]
    # (maintainer, verbatim: "the wallet slider is missing the background
    # being larger top and bottom... mirror iris" / "dotn touch" the tray): the toggle
    # no longer stretches to fill the picker's own remaining width (the old
    # `calc(var(--edge-w) - 4px)`/`block-size: 40px`, the SAME size as the picker
    # itself, leaving no glass frame) -- fixed 32x32, proportionally mirroring Iris's
    # own 48px-pill/40px-toggle ratio for this project's shorter, filter-row-sized
    # 40px pill. instead gives the clickable inner chip the same measured
    # `--edge-w` geometry as the flag/Iris handle, using the live left inset so it
    # remains inside the unchanged outer wallet pill.
    assert "inline-size: var(--edge-chip-w, 32px);" in toggle_rule
    assert "block-size: min(var(--edge-chip-h, 32px), calc(40px - 2 * var(--edge-pad-left, 4px)));" in toggle_rule
    assert "flex: 0 0 var(--edge-w);" in toggle_rule
    assert "margin-inline-start: calc(-1 * var(--edge-pad-left, 4px));" in toggle_rule
    assert "inline-size: calc(var(--edge-w) - 4px);" not in toggle_rule
    assert "block-size: 40px;" not in toggle_rule
    # Pre-landing review fix: replicates canonical.css's `.theme-toggle` chip look
    # (background/radius/box-shadow), not only its sizing. (maintainer, verbatim
    # "the background for the selected resource is too large"): this chip now hugs
    # exactly the 32px toggle box above instead of the old full-pill-width one
    # resolved as a direct consequence of the toggle's own shrink, no separate rule.
    assert "background: color-mix(in srgb, var(--primary) 22%, transparent);" in toggle_rule
    # radius/shadow read from Iris's own live measurement now
    # (--edge-radius-inner/--edge-chip-shadow) -- fallback defaults match the
    # previous hardcoded values exactly.
    assert "border-radius: var(--edge-radius-inner, 5px 0 0 5px);" in toggle_rule
    assert "box-shadow: var(--edge-chip-shadow, 0 8px 22px color-mix(in srgb, var(--shadow) 35%, transparent), inset 0 1px 0 color-mix(in srgb, var(--hi) 6%, transparent));" in toggle_rule
    # icon shrinks with the toggle, 20px -> 18px (within the spec's 16-18px band).
    assert ".edge-toggle img { inline-size: 18px; block-size: 18px; object-fit: contain; }" in css
    # 18px, down from 22px, to match the tray's own smaller 34px option height.
    # froze this -- still 18px, untouched by the toggle shrink.
    assert ".edge-opt img { inline-size: 18px; block-size: 18px; object-fit: contain; }" in css
    assert "@media (prefers-reduced-motion: reduce) {" in css and ".edge-tray { transition: none; }" in css


def test_edge_w_is_measured_at_runtime_not_hardcoded():
    # the Iris picker's
    # closed width is measured at runtime and published as --edge-w so .badge-bar and
    # the wallet toggle always match its real rendered width.
    # (maintainer, verbatim, superseding the old toggleWidth+4 guess: "But all
    # icons and borders in the slider are unaligned"): --edge-w is now the PILL's
    # own real closed rect width (toggle width plus the pill's own live padding
    # and borders) -- along with a full set of sibling geometry vars (--edge-chip
    # edge-pad-left, --edge-pad-right, --edge-radius-outer, --edge-radius-inner
    # edge-chip-shadow), all read straight from Iris's live rect/computed style.
    # (): gating on the `is-open` class published mid-animation
    # widths, so the gate is now a settled-width comparison instead -- the
    # measured closed width only gets published once it matches the picker's
    # own current layout width within half a pixel; otherwise the previous good
    # values are left alone.
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    fn_match = re.search(r"function centerThemePicker\(\) \{.*?\n\}\n", app, re.S)
    assert fn_match, "centerThemePicker source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "const toggle = document.querySelector('.theme-picker .theme-toggle');" in body
    assert "const toggleRect = toggle?.getBoundingClientRect();" in body
    assert "if (toggleRect && toggleRect.width > 0) {" in body
    assert "const pickerCs = getComputedStyle(picker);" in body
    assert "const toggleCs = getComputedStyle(toggle);" in body
    assert "if (Math.abs(pickerLayout.width - closedPickerWidth) <= 0.5) {" in body
    assert "root.setProperty('--edge-w', `${Math.round(closedPickerWidth)}px`);" in body
    assert "root.setProperty('--edge-chip-w', `${Math.round(toggleRect.width)}px`);" in body
    assert "root.setProperty('--edge-chip-h', `${Math.round(toggleRect.height)}px`);" in body
    assert "root.setProperty('--edge-pad-left', pickerCs.paddingLeft);" in body
    assert "root.setProperty('--edge-pad-right', `${Math.max(0, Math.round(pickerLayout.right - toggleRect.right))}px`);" in body
    assert "root.setProperty('--edge-radius-outer', pickerCs.borderRadius);" in body
    assert "root.setProperty('--edge-radius-inner', toggleCs.borderRadius);" in body
    assert "root.setProperty('--edge-glow'" not in body
    assert "root.setProperty('--edge-chip-shadow', toggleCs.boxShadow);" in body
    # The old toggleWidth+4 formula must not be what gets published any more.
    assert "Math.round(toggleWidth) + 4" not in body


def test_badge_bar_resting_state_has_no_ambient_box_shadow():
    # the
    # ambient box-shadow read as a long dark stripe on the full-table-height bar (see
    # ceres.css's own comment on this). No border-left/border-inline-start was ever set
    # on .badge-bar or .badge-bar-head ( reinstated the head, see
    # test_edge_stack.py for its own geometry/style guard).
    css = CERES_CSS.read_text(encoding="utf-8")
    resting_match = re.search(r"(?:^|\n)\.badge-bar \{\n border: 0;.*?\n\}\n", css, re.S)
    assert resting_match, "badge-bar resting-state chrome rule not found in its expected shape"
    assert "box-shadow" not in resting_match.group(0)
    geometry_match = re.search(r"(?:^|\n)\.badge-bar \{\n position: absolute;.*?\n\}\n", css, re.S)
    assert geometry_match, "badge-bar geometry rule not found in its expected shape"
    head_match = re.search(r"(?:^|\n)\.badge-bar-head \{.*?\n\}\n", css, re.S)
    assert head_match, "badge-bar-head rule not found in its expected shape"
    for block in (resting_match.group(0), geometry_match.group(0), head_match.group(0)):
        assert "border-left" not in block
        assert "border-inline-start" not in block
    css_rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert not re.search(r"\.badge-bar-head:hover\s*\{", css_rules)
    assert ".badge-bar-head:focus-visible { outline: 2px solid var(--accent);" in css


def test_wallet_tabs_icons_css_values():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".wallet-tabs--icons {" in css
    row_rule = css.split(".wallet-tabs--icons {", 1)[1].split("}", 1)[0]
    assert "border-bottom: 0;" in row_rule
    assert "color-mix(in srgb, var(--shadow) 40%, transparent)" in row_rule
    assert ".wallet-tab--icon {" in css
    tab_rule = css.split(".wallet-tab--icon {", 1)[1].split("}", 1)[0]
    assert "opacity: .4;" in tab_rule
    assert ".wallet-tab--icon:hover { opacity: .8; }" in css
    # The active DRAWER tab must stay visible (still reachable to switch) and now also
    # carries the Iris chip, same as .edge-opt--active.
    assert '.wallet-tab--icon[aria-selected="true"] {' in css
    active_tab_rule = css.split('.wallet-tab--icon[aria-selected="true"] {', 1)[1].split("}", 1)[0]
    assert "opacity: 1;" in active_tab_rule
    assert "background: color-mix(in srgb, var(--primary) 22%, transparent);" in active_tab_rule
    # Pre-landing review fix: symmetric 5px (this tab sits mid-row), not .edge-toggle's 5px 0 0 5px.
    assert "border-radius: 5px;" in active_tab_rule
    assert "border-radius: 5px 0 0 5px;" not in active_tab_rule
    assert "box-shadow: 0 8px 22px color-mix(in srgb, var(--shadow) 35%, transparent), inset 0 1px 0 color-mix(in srgb, var(--hi) 6%, transparent);" in active_tab_rule
    assert '.wallet-tab--icon[disabled] { opacity: .2; cursor: default; }' in css
    assert ".wallet-tab--icon img { inline-size: 22px; block-size: 22px; object-fit: contain; }" in css


def test_walletTabsHtml_is_icon_only_with_ceres_tooltips():
    js = WALLET_RESOURCES_JS.read_text(encoding="utf-8")
    assert "class=\"ceres-tabs wallet-tabs wallet-tabs--icons\" role=\"tablist\"" in js
    assert "class=\"wallet-tab wallet-tab--icon\" role=\"tab\"" in js
    assert "ceres-btn ceres-btn--secondary wallet-tab" not in js
    assert "data-tip=\"${esc(tokenLabel(symbol))}\"" in js
    assert "aria-label=\"${esc(tokenLabel(symbol))}\"" in js
    # No text label span any more -- just the icon.
    assert "<span>${esc(tokenLabel(symbol))}</span>" not in js


def test_regional_dec_persists_selection_and_fills_the_tray():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    assert "const WALLET_SELECTION_KEY = 'ceres:wallet:resource';" in js
    assert "export function setWalletSelection(symbol) {" in js
    assert "function getWalletSelection() {" in js
    # Reads/writes wrapped in try/catch (a private window or blocked site data can throw).
    selection_fn = js.split("function getWalletSelection() {", 1)[1].split("\n}\n", 1)[0]
    assert "try {" in selection_fn and "catch (_)" in selection_fn
    set_fn = js.split("export function setWalletSelection(symbol) {", 1)[1].split("\n}\n", 1)[0]
    assert "try {" in set_fn and "catch (_)" in set_fn
    assert "localStorage.setItem(WALLET_SELECTION_KEY, symbol)" in set_fn
    # The tray is filled with one .edge-opt per WALLET_TABS symbol, carrying a Ceres tooltip
    # plus the chevron prepended ahead of them (a non-resource option, no data-symbol).
    assert "walletTray.innerHTML = chevronHtml + WALLET_TABS.map(symbol =>" in js
    assert 'class="edge-opt edge-tray-chevron"' in js
    assert 'class="edge-opt" role="tab" data-symbol="${symbol}"' in js
    assert "data-tip=\"${esc(tokenLabel(symbol))}\"" in js


def test_open_wallet_drawer_has_a_symbol_parameter_before_pending_overrides():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"export async function openWalletDrawer\(symbol\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "openWalletDrawer source shape changed -- update this test's slice"
    body = fn_match.group(0)
    tab_line_idx = body.index("D.tab = WALLET_TABS.includes(symbol) ? symbol : getWalletSelection();")
    pending_override_idx = body.index("if (D.pending) D.tab = 'DEC'; else if (D.claim.pending) D.tab = 'SPS';")
    assert tab_line_idx < pending_override_idx, "the symbol argument must be set BEFORE the pending-receipt/claim overrides"


def test_tray_option_click_selects_focuses_then_opens_the_drawer_on_that_symbol():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    listener_match = re.search(r"walletTray\.addEventListener\('click', \(event\) => \{(.*?)\n \}\);", js, re.S)
    assert listener_match, "wallet tray click listener not found in its expected shape"
    body = listener_match.group(1)
    option_body = body.split("const opt = event.target.closest('.edge-opt');", 1)[1]
    select_idx = option_body.index("setWalletSelection(opt.dataset.symbol);")
    focus_idx = option_body.index("walletToggleBtn.focus({ preventScroll: true });")
    open_idx = option_body.index("openWalletDrawer(opt.dataset.symbol);")
    assert "closeWalletPicker();" not in option_body
    assert select_idx < focus_idx < open_idx, "must select, focus the stable toggle, then open the drawer"


def test_toggle_opens_and_closes_the_tray_and_wallet_together():
    """the edge handle and wallet drawer share one open/closed target.
 aria-expanded is set centrally in setWalletSurfaceTarget() (the one place that
 also writes walletPicker.dataset.open and drawer.dataset.walletMotion) rather
 than duplicated inline in toggleWalletPicker() itself -- every path this
 function takes (close(), closeWalletPicker(), openWalletDrawer()) routes
 through that single target-state writer."""
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    assert "walletToggleBtn.addEventListener('click', () => toggleWalletPicker());" in js
    assert "function toggleWalletPicker() {" in js
    toggle_fn = js.split("function toggleWalletPicker() {", 1)[1].split("\n}\n", 1)[0]
    assert "walletPicker.dataset.open" in toggle_fn
    fn_match = re.search(r"function setWalletSurfaceTarget\(open\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "setWalletSurfaceTarget source shape changed -- update this test's slice"
    assert "aria-expanded" in fn_match.group(0)
    open_branch = toggle_fn.split("if (open) {", 1)[1].split("return;", 1)[0]
    assert "close();" in open_branch
    assert "closeWalletPicker();" in open_branch
    assert "openWalletDrawer(getWalletSelection());" not in open_branch
    assert toggle_fn.count("openWalletDrawer") == 1
    closed_branch = toggle_fn.rsplit("\n }\n", 1)[1]
    assert "openWalletDrawer(getWalletSelection());" in closed_branch


def test_outside_click_and_escape_close_the_tray():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    assert "function closeWalletPicker() {" in js
    assert "!walletPicker.contains(event.target)" in js
    assert "event.key === 'Escape'" in js


def test_toggle_expanded_state_is_scoped_to_the_tray_not_the_drawer():
    # Pre-landing review fix: the toggle's own aria-expanded tracks the TRAY only
    # (toggleWalletPicker/closeWalletPicker set it directly); setWalletTriggersExpanded
    # the drawer's own open/closed sync -- must not also touch it, since the toggle
    # no longer carries aria-controls="regional-dec-drawer".
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function setWalletTriggersExpanded\(open\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "setWalletTriggersExpanded source shape changed -- update this test's slice"
    assert "aria-controls=\"regional-dec-drawer\"" in fn_match.group(0)
    assert "wallet-edge-tab" not in fn_match.group(0)


def test_act_tab_handler_persists_the_selection_too():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    assert "D.tab = tab; setWalletSelection(tab); render(); return;" in js


def test_node_check_touched_js_files():
    for path in (REGIONAL_DEC_JS, WALLET_RESOURCES_JS):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"


def test_selected_resource_carries_the_iris_chip_everywhere():
    """the exact chip canonical.css's `.theme-toggle` uses
 (`color-mix(in srgb, var(--primary) 22%, transparent)` plus its box-shadow) must
 mark the selected resource on all three surfaces: the edge toggle (already, since
 /pre-landing fix 4), the active tray option, and the active drawer tab.
 Pre-landing review fix: only the TOGGLE keeps the toggle's own asymmetric radius
 (`5px 0 0 5px`, matching its edge-flush position) -- the mid-tray option and the
 mid-row drawer tab get a symmetric `5px` instead. Non-selected options stay flat
 at .4 opacity (`.edge-opt`/`.wallet-tab--icon` base rules, unchanged)."""
    css = CERES_CSS.read_text(encoding="utf-8")
    chip_bg = "background: color-mix(in srgb, var(--primary) 22%, transparent);"
    chip_shadow = "box-shadow: 0 8px 22px color-mix(in srgb, var(--shadow) 35%, transparent), inset 0 1px 0 color-mix(in srgb, var(--hi) 6%, transparent);"
    toggle_rule = css.split(".edge-toggle {", 1)[1].split("}", 1)[0]
    active_opt_rule = css.split(".edge-opt--active {", 1)[1].split("}", 1)[0]
    active_tab_rule = css.split('.wallet-tab--icon[aria-selected="true"] {', 1)[1].split("}", 1)[0]
    assert chip_bg in toggle_rule
    assert chip_bg in active_opt_rule
    assert chip_bg in active_tab_rule
    # only `.edge-toggle`'s own box-shadow/radius now read from Iris's
    # live measurement (--edge-chip-shadow/--edge-radius-inner) -- the tray/drawer
    # active-option chips (out of 's scope, wallet handles only) keep the
    # literal, hand-typed value unchanged.
    chip_shadow_value = chip_shadow[len("box-shadow "):-1]  # strip the property name and trailing
    assert f"box-shadow: var(--edge-chip-shadow, {chip_shadow_value});" in toggle_rule
    assert chip_shadow in active_opt_rule
    assert chip_shadow in active_tab_rule
    assert "border-radius: var(--edge-radius-inner, 5px 0 0 5px);" in toggle_rule
    for rule in (active_opt_rule, active_tab_rule):
        assert "border-radius: 5px;" in rule
        assert "border-radius: 5px 0 0 5px;" not in rule
    # Non-selected stays flat.
    opt_rule = css.split(".edge-opt {", 1)[1].split("}", 1)[0]
    tab_rule = css.split(".wallet-tab--icon {", 1)[1].split("}", 1)[0]
    for rule in (opt_rule, tab_rule):
        assert "opacity: .4;" in rule
        assert chip_bg not in rule


def test_edge_tabs_is_a_direct_child_of_body_not_controls_row():
    """Pre-landing review fix: `.controls-row` is `overflow-x: auto` on desktop (its
 filter controls can outgrow the viewport), which clipped `#edge-tabs` and scrolled
 it sideways with the row's own content when it lived inside that row. It moved to
 a direct child of `<body>`, right after `.controls-row` closes, so it is no longer
 inside any scrolling/clipping box."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    controls_row_match = re.search(r'<div class="controls-row">.*?\n</div>\n', html, re.S)
    assert controls_row_match, "could not find .controls-row markup"
    assert 'id="edge-tabs"' not in controls_row_match.group(0)
    after_controls_row = html[controls_row_match.end():]
    edge_tabs_match = re.search(r'<div id="edge-tabs" class="edge-tabs">', after_controls_row)
    assert edge_tabs_match, "#edge-tabs must immediately follow .controls-row in the DOM"
    # Nothing else opens a container between the row's close and #edge-tabs.
    between = after_controls_row[:edge_tabs_match.start()]
    assert "<div" not in between and "<main" not in between


def test_body_carries_the_absolute_positioning_containing_block():
    css = CERES_CSS.read_text(encoding="utf-8")
    body_rule = css.split("body {", 1)[1].split("}", 1)[0]
    assert "position: relative;" in body_rule


def test_edge_tabs_recomputed_on_resize_and_after_first_board_render():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "window.addEventListener('resize', scheduleCenterThemePicker);" in app
    # Pre-landing review fix: #edge-tabs's own top now depends on .controls-row's real
    # content (filter option counts, the Clear-all button's visibility, etc.), which
    # only settles once the board's first render actually lands -- init() awaits
    # loadBoard() internally, so chaining off its own promise recomputes once more
    # right after that first paint.
    assert "const initDone = init();" in app
    assert "initDone.then(scheduleCenterThemePicker);" in app


def test_edge_tabs_recomputed_on_layout_shifts_above_the_controls_row():
    """Pre-landing review fix, round 3: notices/totals above the filter row change
 height on every render() (a new notice line, the totals strip filling in) without
 firing `resize`, which shifted `.controls-row`'s own position and left
 `#edge-tabs`'s `top` stale. The existing ResizeObserver that watches
 `.controls-row` (for `syncControlsDividers()`) is reused to also recompute
 `#edge-tabs`, and now also watches `#board-notices`/`.totals-strip`; table.js's
 render() dispatches `ceres:table-layout`, caught here as a third trigger."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "const onControlsAreaResize = () => { syncControlsDividers(); scheduleCenterThemePicker(); };" in app
    assert "const controlsAreaObserver = new ResizeObserver(onControlsAreaResize);" in app
    assert "if (row) controlsAreaObserver.observe(row);" in app
    assert "controlsAreaObserver.observe(document.getElementById('board-notices'));" in app
    assert "controlsAreaObserver.observe(document.querySelector('.totals-strip'));" in app
    assert "window.addEventListener('ceres:table-layout', scheduleCenterThemePicker);" in app
    table_js = (STATIC / "table.js").read_text(encoding="utf-8")
    render_match = re.search(r"export function render\(isNewDataset\) \{.*?\n\}\n", table_js, re.S)
    assert render_match, "render() source shape changed -- update this test's slice"
    assert "no second `ceres:table-layout` here" in render_match.group(0)  # review fix: render() relies on renderVirtualWindow(true)'s own notify


def _rule_block(css: str, selector_open: str) -> str:
    return css.split(selector_open, 1)[1].split("\n}\n", 1)[0]


def test_edge_tray_and_edge_opt_are_byte_identical_to_master():
    """ freezes `.edge-tray`/`.edge-opt` (the base rule,
 not `.edge-opt--active`, which explicitly asked to change -- see
 test_edge_picker_css_values_replicate_iris) -- this diffs their exact text
 against master, so any future accidental edit to the tray/option's own layout,
 chip, spacing or transition fails loudly instead of silently drifting."""
    master_css = subprocess.run(
        ["git", "show", "master:ceres/static/ceres.css"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    current_css = CERES_CSS.read_text(encoding="utf-8")
    for selector_open in (".edge-tray {", ".edge-opt {"):
        assert _rule_block(current_css, selector_open) == _rule_block(master_css, selector_open), (
            f"{selector_open} drifted from master -- froze it"
        )
    # .edge-picker[data-open="true"] .edge-tray { max-width: 20rem; } and .edge-opt:hover
    # are the tray/option's own one-liners -- frozen the same way.
    for one_liner in (
        '.edge-picker[data-open="true"] .edge-tray { max-width: 20rem; }',
        ".edge-opt:hover { opacity: .8; }",
    ):
        assert one_liner in master_css and one_liner in current_css
