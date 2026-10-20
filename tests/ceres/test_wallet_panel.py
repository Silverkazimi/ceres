""" (maintainer, verbatim): "redesign the wallet to basically fit under der slide
out and serve is icon picker or a disconnected modal below it with the selected
wallet. add a slide in chevron to the left. Open the selected resource when slided
in and clicked again."

Agreed reading (told to him, no objection): the edge tray (#wallet-picker) is the
ONLY icon picker; the wallet becomes a floating panel anchored under the tray on
desktop, showing the selected resource; a chevron at the tray's left end slides the
tray in/out; clicking the selected icon (tray slid in) opens that resource's wallet;
clicking another icon in the tray selects it and opens its wallet (unchanged).

See library/ceres-wallet.md (v0.9.681 section) and library/ceres-edge-tabs.md §v7
for the full writeup. Companion files updated by this ticket:
tests/ceres/test_wallet_tray.py (toggle semantics, tray markup).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .conftest import spawn_live_ceres
from .vm_context import read_entry_source, run_in_module_context

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
APP_JS = STATIC / "app.js"
CERES_CSS = STATIC / "ceres.css"
REGIONAL_DEC_JS = STATIC / "regional-dec.js"
WALLET_JS = STATIC / "wallet.js"
WALLET_CSS = STATIC / "wallet.css"


def _center_theme_picker_body() -> str:
    app = APP_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function centerThemePicker\(\) \{.*?\n\}\n", app, re.S)
    assert fn_match, "centerThemePicker source shape changed -- update this test's slice"
    return fn_match.group(0)


def test_tray_chevron_is_prepended_and_is_not_a_resource_option():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    assert 'const chevronHtml = `<button type="button" class="edge-opt edge-tray-chevron" data-tip="Slide in" aria-label="Slide the tray in">' in js
    assert "walletTray.innerHTML = chevronHtml + WALLET_TABS.map(symbol =>" in js
    # The chevron carries no data-symbol -- it never becomes .edge-opt--active
    # (updateWalletTrayActive() compares opt.dataset.symbol === symbol, always false
    # for it) and the tray click handler's `!opt.dataset.symbol` guard rejects it as
    # a selection.
    assert "data-symbol=\"${symbol}\"" not in re.search(r"const chevronHtml = `(.*?)`;", js, re.S).group(1)


def test_chevron_click_only_closes_the_tray():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    listener_match = re.search(r"walletTray\.addEventListener\('click', \(event\) => \{(.*?)\n \}\);", js, re.S)
    assert listener_match, "wallet tray click listener not found in its expected shape"
    body = listener_match.group(1)
    chevron_guard = body.split("const opt = event.target.closest('.edge-opt');", 1)[0]
    assert "event.target.closest('.edge-tray-chevron')" in chevron_guard
    assert "closeWalletPicker();" in chevron_guard
    assert "openWalletDrawer" not in chevron_guard
    assert "return;" in chevron_guard
    # A non-chevron, non-option click (event.target outside any .edge-opt) is a no-op.
    assert "if (!opt || !opt.dataset.symbol) return;" in body


def test_chevron_icon_reuses_the_shared_disclosure_mask_pointing_right():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".edge-tray-chevron .ceres-chevron { transform: rotate(-90deg); }" in css
    # No bespoke mask/gradient of its own -- it rides .ceres-chevron's existing
    # ceres-chevron-art mask (buttons.css), only the rotation is new.
    assert "--ceres-chevron-art" not in css.split(".edge-tray-chevron", 1)[1].split("\n\n", 1)[0]


def test_isDesktopPanel_uses_the_shared_board_tier_helper():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    assert "import { isMobileBoardTier } from './table.js';" in js
    assert "function isDesktopPanel() { return !isMobileBoardTier(); }" in js


def test_desktop_render_drops_header_and_tab_row_mobile_keeps_them():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    render_match = re.search(r"function render\(force = false\) \{.*?\n\}\n", js, re.S)
    assert render_match, "render() source shape changed -- update this test's slice"
    body = render_match.group(0)
    assert "const desktop = isDesktopPanel();" in body
    assert "const headerHtml = desktop ? '' : `<header class=\"dec-drawer-header\">" in body
    assert "const tabsHtml = desktop ? '' : walletTabsHtml(D.tab, tabsBusy, tabsAllowed);" in body
    assert "${headerHtml}${tabsHtml}${captionHtml}<div class=\"dec-drawer-content\">" in body


def test_desktop_caption_names_the_resource_and_flags_a_forced_pending_tab():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    render_match = re.search(r"function render\(force = false\) \{.*?\n\}\n", js, re.S)
    body = render_match.group(0)
    assert "const forcedPending = D.tab === 'DEC' ? D.pending?.status === 'pending' : D.tab === 'SPS' ? (D.signing && !!D.claim.pending) : false;" in body
    assert 'class="wallet-caption" id="dec-title"' in body
    assert "${esc(D.tab === 'DEC' || D.tab === 'SPS' ? D.tab : tokenLabel(D.tab))}" in body  # DEC/SPS stay upper-case
    assert "wallet-caption-pending" in body
    assert "forcedPending ? '<span class=\"wallet-caption-pending\">Pending signature</span>' : ''" in body


def test_confirmed_dec_receipt_never_keeps_pending_signature_in_its_caption():
    """the desktop caption's terminal predicate must match the receipt body."""
    body_js = """
document.documentElement.classList = { contains() { return false; } };
const panel = { innerHTML: '' };
drawer = { open: true, querySelector(selector) { return selector === '.dec-drawer-panel' ? panel : null; } };
Object.assign(D, {
 tab: 'DEC', player: 'player0005', data: { regions: [] }, wallet: null, rewards: null,
 walletBusy: false, review: null, busy: false, signing: false, checking: false,
 closing: false, error: '', message: '', claim: { review: null, pending: null, busy: false, checking: false },
 pending: {
 status: 'confirmed', region_uid: 'PR-BRL-41', action: 'stake', amount: 8,
 tx_id: 'a'.repeat(40),
 harvest: { state: 'recorded', members: [{ outcome: 'success', region_uid: 'PR-BRL-41', received_by_token: {}, costs_by_token: {} }] },
 },
});
render();
if (!panel.innerHTML.includes('DEC change confirmed') || !panel.innerHTML.includes('Harvest complete')) {
 throw new Error('the confirmed DEC receipt body was not rendered: ' + panel.innerHTML);
}
if (panel.innerHTML.includes('Pending signature')) {
 throw new Error('Pending signature survived beside confirmed receipt content: ' + panel.innerHTML);
}
"""
    fixed_source = read_entry_source(REGIONAL_DEC_JS)
    # changed only the SPS branch (caption only while signing); the DEC predicate under test is unchanged.
    fixed_predicate = "const forcedPending = D.tab === 'DEC' ? D.pending?.status === 'pending' : D.tab === 'SPS' ? (D.signing && !!D.claim.pending) : false;"
    old_predicate = "const forcedPending = D.tab === 'DEC' ? !!D.pending : D.tab === 'SPS' ? (D.signing && !!D.claim.pending) : false;"
    assert fixed_predicate in fixed_source, "counterfactual injection requires the fixed DEC predicate"
    counterfactual = run_in_module_context(
        body_js,
        entry=REGIONAL_DEC_JS,
        source=fixed_source.replace(fixed_predicate, old_predicate),
        check=False,
    )
    assert counterfactual.returncode != 0, "unmodified predicate must reproduce the contradictory caption"
    assert "Pending signature survived beside confirmed receipt content" in counterfactual.stderr
    run_in_module_context(body_js, entry=REGIONAL_DEC_JS, source=fixed_source)


def test_desktop_panel_is_shown_non_modal_mobile_stays_a_real_modal():
    """the desktop branch now also tracks `alreadyOpen` so a re-selection
 while the panel is already showing does not replay its open motion -- the panel
 and the edge tray handle must always move together. The one-line shape
 became this three-statement block; the functional contract (desktop: non-modal
 show + positionDesktopPanel(), mobile: showModal()) is unchanged and still
 checked here."""
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    open_match = re.search(r"if \(isDesktopPanel\(\)\) \{(.*?)\n \} else drawer\.showModal\(\);", js, re.S)
    assert open_match, "openWalletDrawer's desktop/mobile branch not found in its expected shape"
    body = open_match.group(1)
    assert "drawer.show();" in body
    assert "positionDesktopPanel();" in body


def test_click_outside_and_escape_close_the_desktop_panel():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    # A non-modal dialog (drawer.show()) gets neither native backdrop-click-to-close
    # nor native Escape-to-close -- both are wired explicitly, scoped to desktop and
    # to the drawer actually being open.
    click_match = re.search(r"document\.addEventListener\('click', \(event\) => \{\s*if \(!drawer\.open \|\| !isDesktopPanel\(\)\) return;(.*?)\n \}\);", js, re.S)
    assert click_match, "desktop panel outside-click listener not found in its expected shape"
    body = click_match.group(1)
    assert "panel?.contains(event.target)" in body
    assert "walletPicker?.contains(event.target)" in body
    assert "close();" in body
    assert "document.addEventListener('keydown', (event) => {\n if (event.key === 'Escape' && drawer.open && isDesktopPanel()) close();\n });" in js


def test_desktop_panel_click_uses_pointerdown_and_ignores_detached_targets():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    assert "let desktopPanelPointerDownInside = false;" in js
    assert "document.addEventListener('pointerdown', (event) => {" in js
    assert "desktopPanelPointerDownInside = !!(panel?.contains(event.target) || walletPicker?.contains(event.target));" in js
    assert "targetConnected: event.target?.isConnected !== false" in js
    run_in_module_context(
        """
const detachedByRender = desktopPanelClickShouldClose({
 pointerDownInside: true, targetConnected: false, panelContains: false, pickerContains: false,
});
if (detachedByRender) throw new Error('a synchronously detached panel click must not close the panel');
if (desktopPanelClickShouldClose({
 pointerDownInside: false, targetConnected: false, panelContains: false, pickerContains: false,
})) throw new Error('a disconnected click target must be ignored');
if (!desktopPanelClickShouldClose({
 pointerDownInside: false, targetConnected: true, panelContains: false, pickerContains: false,
})) throw new Error('a connected outside click must close the panel');
""",
        entry=STATIC / "regional-dec.js",
    )


def test_desktop_panel_css_is_only_a_pre_js_fallback_no_edge_strip_vars():
    """Team-lead correction (2026-09-19): #wallet-picker's own position is not a
 fixed, CSS-var-derivable constant (a later version moves it onto the filter row,
 where it scrolls) -- the panel's real top/right/max-height are plain inline
 styles `regional-dec.js::positionDesktopPanel()` sets from the picker's own live
 rect (see test_position_desktop_panel_* below), never a `--edge-right`/
 `--edge-strip-bottom` custom property written by app.js."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html:not(.ceres-lw-le-1279) .dec-drawer {" in css
    panel_dialog_rule = css.split("html:not(.ceres-lw-le-1279) .dec-drawer {", 1)[1].split("}", 1)[0]
    assert "--edge-right" not in panel_dialog_rule
    assert "--edge-strip-bottom" not in panel_dialog_rule
    assert "width: min(34rem, calc(100vw - 2rem));" in panel_dialog_rule
    assert "max-width: none;" in panel_dialog_rule
    assert "--edge-right" not in css
    assert "--edge-strip-bottom" not in css
    assert "html:not(.ceres-lw-le-1279) .dec-drawer-panel {" in css
    panel_rule = css.split("html:not(.ceres-lw-le-1279) .dec-drawer-panel {", 1)[1].split("}", 1)[0]
    assert "background: color-mix(in srgb, var(--shadow) 82%, transparent);" in panel_rule
    assert "border-radius: 5px;" in panel_rule
    assert 'html:not(.ceres-lw-le-1279)[data-theme="light"] .dec-drawer-panel' in css
    assert 'html:not(.ceres-lw-le-1279)[data-theme="wood"] .dec-drawer-panel {' in css
    light_wood_rule = css.split('html:not(.ceres-lw-le-1279)[data-theme="wood"] .dec-drawer-panel {', 1)[1].split("}", 1)[0]
    assert "color-mix(in srgb, var(--bg2) 92%, transparent)" in light_wood_rule


def test_desktop_panel_backdrop_is_transparent_and_scoped_never_global():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "html:not(.ceres-lw-le-1279) .dec-drawer-backdrop { display: none; }" in css
    # Never a bare, unscoped redefinition that would also hide the mobile modal's
    # own backdrop click-to-close target.
    assert re.search(r"(?:^|\n)\.dec-drawer-backdrop \{ display: none; \}", css) is None


def test_wallet_caption_css_present():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".wallet-caption {" in css
    assert ".wallet-caption-pending {" in css


def test_wallet_content_uses_the_shared_themed_scrollbar_contract():
    # () replaced the per-selector :is(...) list with one global scrollbar rule; the
    # wallet content is covered by it as long as it scrolls and does not override it.
    css = CERES_CSS.read_text(encoding="utf-8")
    shared = "where(*), html"
    global_rule = css.split(f"{shared} {{", 1)[1].split("}", 1)[0]
    assert "scrollbar-width: thin;" in global_rule
    assert "scrollbar-color: var(--border-bright) transparent;" in global_rule
    assert "where(*)::-webkit-scrollbar, html::-webkit-scrollbar { width: 0.5rem; height: 0.5rem; }" in css
    assert "where(*)::-webkit-scrollbar-track, html::-webkit-scrollbar-track { background: transparent; }" in css
    assert "where(*)::-webkit-scrollbar-thumb, html::-webkit-scrollbar-thumb {" in css
    wallet_rule = css.split(".dec-drawer-content {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in wallet_rule
    assert ".dec-drawer-content::-webkit-scrollbar" not in css
    assert not re.search(r"\.dec-drawer-content[^{]*\{[^}]*scrollbar-(?:width|color)", css)


def test_app_js_centerThemePicker_is_unchanged_by_this_ticket():
    """Team-lead correction: app.js is not touched by at all any more --
 positioning moved entirely into regional-dec.js's own positionDesktopPanel().
 later removed the `.style.right` write this test used to check for --
 #edge-tabs is plain CSS `right: 0` now (see test_edge_stack.py)."""
    body = _center_theme_picker_body()
    assert "--edge-right" not in body
    assert "--edge-strip-bottom" not in body
    assert "edgeTabs.style.right" not in body


def test_position_desktop_panel_reads_the_pickers_own_live_rect():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function positionDesktopPanel\(\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "positionDesktopPanel source shape changed -- update this test's slice"
    body = fn_match.group(0)
    # the guard is now two statements -- a plain not-open return, then a
    # not-desktop branch that clears leftover inline positioning before returning
    # (test_lows_batch.py::test_position_desktop_panel_clears_styles_on_a_stay_open_desktop_to_mobile_resize).
    assert "if (!drawer || !drawer.open) return;" in body
    assert "if (!isDesktopPanel()) {" in body
    assert "document.getElementById('wallet-picker')" in body
    assert "picker.getBoundingClientRect();" in body
    assert "document.querySelector('.controls-row')" in body
    assert "filterRow?.getBoundingClientRect();" in body
    assert "document.documentElement.getBoundingClientRect().right;" in body
    assert "const top = Math.round(filterRect?.bottom ?? rect.bottom);" in body
    assert "const gap = 8;" not in body
    assert "minVisible" not in body
    assert "drawer.style.top = `${top}px`;" in body
    assert "drawer.style.right = `${right}px`;" in body
    assert "drawer.style.maxHeight = `calc(100vh - ${top}px - 1rem)`;" in body
    # No leftover --edge-right/--edge-strip-bottom var reads or writes.
    assert "--edge-right" not in body
    assert "--edge-strip-bottom" not in body


def test_position_desktop_panel_is_called_on_open_and_kept_live_on_resize_scroll_layout():
    js = REGIONAL_DEC_JS.read_text(encoding="utf-8")
    open_match = re.search(r"if \(isDesktopPanel\(\)\) \{(.*?)\n \} else drawer\.showModal\(\);", js, re.S)
    assert open_match, "openWalletDrawer's desktop/mobile branch not found in its expected shape"
    body = open_match.group(1)
    assert "drawer.show();" in body
    assert "positionDesktopPanel();" in body
    assert "window.addEventListener('resize', positionDesktopPanel);" in js
    assert "window.addEventListener('scroll', positionDesktopPanel, true);" in js
    assert "window.addEventListener('ceres:table-layout', positionDesktopPanel);" in js


def test_desktop_panel_z_order_stays_between_header_and_table_head():
    css = CERES_CSS.read_text(encoding="utf-8")
    panel_rule = css.split("html:not(.ceres-lw-le-1279) .dec-drawer {", 1)[1].split("}", 1)[0]
    assert "z-index: 39;" in panel_rule
    thead_rule = css.split("thead.table-head-sticky {", 1)[1].split("}", 1)[0]
    assert "z-index: 9;" in thead_rule


def test_node_check_touched_js_files():
    for path in (REGIONAL_DEC_JS, APP_JS, WALLET_JS):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"


def test_wallet_switcher_state_is_per_surface_and_connection_pending_keeps_keychain_escape():
    js = WALLET_JS.read_text(encoding="utf-8")
    css = WALLET_CSS.read_text(encoding="utf-8")
    assert "const controlStates = new Map();" in js
    assert "walletControlsHtml(el.dataset.walletAccount, el.dataset.walletControlsId)" in js
    assert "state = controlState(controls.dataset.walletControlsId)" in js
    assert "const expandedControlIds = new Set(" in js
    assert "expandedControlIds.has(el.dataset.walletControlsId)" in js
    assert "connectHiveAuth(walletAccount(), controls.dataset.walletControlsId)" in js
    assert "pending?.action === 'broadcast'" in js
    assert "if (pending?.action === 'connect' && kind === 'keychain') finishPending({ type: 'cancelled' });" in js
    assert "document.querySelectorAll('.wallet-controls').forEach(layoutDrawer);" in js
    assert "max-block-size: var(--wallet-drawer-max-block-size, calc(100dvh - 1rem));" in css


def _install_wallet_test_controls(page, *, clipped: bool = False):
    page.evaluate(
        """(clipped) => {
 document.querySelectorAll('.wallet-controls, [data-wallet-test-panel]').forEach((el) => el.remove());
 const control = id => `<span class="wallet-sign-pair" data-wallet-test="${id}"><section class="wallet-controls" data-wallet-account="player0005" data-wallet-controls-id="fixture-${id}">
 <button type="button" class="hm-btn ceres-btn ceres-btn--secondary wallet-toggle" data-wallet-toggle aria-expanded="false" aria-controls="fixture-panel-${id}">Wallet</button>
 <div class="wallet-drawer" id="fixture-panel-${id}" inert><div class="wallet-drawer-body"><div class="wallet-providers">
 <button type="button" class="hm-btn ceres-btn ceres-btn--choice" data-wallet-select="keychain" aria-pressed="false">Hive Keychain</button>
 <button type="button" class="hm-btn ceres-btn ceres-btn--choice" data-wallet-select="hiveauth" aria-pressed="false">Connect HiveAuth</button>
 </div></div></div></section></span>`;
 if (clipped) {
 document.body.insertAdjacentHTML('beforeend', `<div data-wallet-test-panel class="harvest-panel" style="position:fixed;left:40px;bottom:20px;width:420px;height:220px;min-width:0;max-width:none;max-height:none;overflow:hidden;"><div style="position:absolute;left:150px;bottom:8px;">${control('clip')}</div></div>`);
 } else {
 document.body.insertAdjacentHTML('beforeend', `<div data-wallet-test-panel style="position:fixed;left:40px;top:calc(var(--ceres-header-height, 80px) + 20px);display:flex;align-items:flex-start;gap:24rem;">${control('a')}${control('b')}</div>`);
 }
 }""",
        clipped,
    )


def test_wallet_switcher_live_surfaces_and_hiveauth_escape():
    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page(viewport={"width": 1200, "height": 730})
        page.add_init_script(
            """() => {
 window.hive_keychain = { requestBroadcast() {} };
 window.Worker = class {
 postMessage(message) {
 if (message.action === 'connect') setTimeout(() => this.onmessage?.({ data: {
 id: message.id, type: 'auth-wait', link: '#hiveauth',
 svg: '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180"></svg>'
 } }), 0);
 }
 terminate() {}
 };
 }"""
        )
        page.goto(f"{base_url}/?player=player0005", wait_until="domcontentloaded")
        _install_wallet_test_controls(page)
        fixture_hit = page.evaluate(
            """() => {
 const button = document.querySelector('[data-wallet-test="a"] [data-wallet-toggle]');
 const rect = button.getBoundingClientRect();
 const top = document.elementFromPoint((rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2);
 const header = document.querySelector('header.board-header')?.getBoundingClientRect();
 return {
 buttonTop: rect.top,
 headerBottom: header?.bottom,
 targetOrDescendant: top === button || button.contains(top),
 };
 }"""
        )
        assert fixture_hit["buttonTop"] > fixture_hit["headerBottom"] and fixture_hit["targetOrDescendant"], fixture_hit
        page.locator('[data-wallet-test="a"] [data-wallet-toggle]').click()
        before = page.evaluate(
            """() => [...document.querySelectorAll('[data-wallet-test]')].map((el) => ({
 id: el.dataset.walletTest,
 expanded: el.querySelector('[data-wallet-toggle]')?.getAttribute('aria-expanded'),
 }))"""
        )
        page.locator('[data-wallet-test="a"] [data-wallet-select="hiveauth"]').click()
        page.locator('[data-wallet-test="a"] .wallet-qr').wait_for(timeout=5000)
        pending = page.evaluate(
            """() => ({
 keychainDisabled: document.querySelector('[data-wallet-test="a"] [data-wallet-select="keychain"]')?.disabled,
 requestVisible: Boolean(document.querySelector('[data-wallet-test="a"] .wallet-request')),
 })"""
        )
        assert before == [{"id": "a", "expanded": "true"}, {"id": "b", "expanded": "false"}], before
        assert pending == {"keychainDisabled": False, "requestVisible": True}, pending
        page.locator('[data-wallet-test="a"] [data-wallet-select="keychain"]').click()
        after = page.evaluate(
            """() => ({
 provider: localStorage.getItem('ceres.wallet-provider:player0005'),
 requestVisible: Boolean(document.querySelector('[data-wallet-test="a"] .wallet-request')),
 expanded: [...document.querySelectorAll('[data-wallet-test]')].map((el) =>
 el.querySelector('[data-wallet-toggle]')?.getAttribute('aria-expanded')),
 })"""
        )
        assert after == {"provider": "keychain", "requestVisible": False, "expanded": ["true", "false"]}, after
        page.locator('[data-wallet-test="b"] [data-wallet-toggle]').click()
        both_open = page.evaluate(
            """() => [...document.querySelectorAll('[data-wallet-test]')].map((el) =>
 el.querySelector('[data-wallet-toggle]')?.getAttribute('aria-expanded'))"""
        )
        assert both_open == ["true", "true"], both_open
        page.mouse.click(5, 5)
        page.wait_for_function(
            """() => [...document.querySelectorAll('[data-wallet-test]')].every((el) =>
 el.querySelector('[data-wallet-toggle]')?.getAttribute('aria-expanded') === 'false')"""
        )
        page.close()


def test_wallet_hiveauth_drawer_stays_inside_clipping_surface_live():
    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page(viewport={"width": 1200, "height": 730})
        page.add_init_script(
            """() => {
 window.hive_keychain = { requestBroadcast() {} };
 window.Worker = class {
 postMessage(message) {
 if (message.action === 'connect') setTimeout(() => this.onmessage?.({ data: {
 id: message.id, type: 'auth-wait', link: '#hiveauth',
 svg: '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220"></svg>'
 } }), 0);
 }
 terminate() {}
 };
 }"""
        )
        page.goto(f"{base_url}/?player=player0005", wait_until="domcontentloaded")
        _install_wallet_test_controls(page, clipped=True)
        page.locator('[data-wallet-test="clip"] [data-wallet-toggle]').click()
        page.locator('[data-wallet-test="clip"] [data-wallet-select="hiveauth"]').click()
        page.locator('[data-wallet-test="clip"] .wallet-qr').wait_for(timeout=5000)
        geometry = page.evaluate(
            """() => {
 const panel = document.querySelector('[data-wallet-test-panel]');
 const drawer = document.querySelector('[data-wallet-test="clip"] .wallet-drawer');
 const p = panel.getBoundingClientRect();
 const d = drawer.getBoundingClientRect();
 return {
 upward: document.querySelector('[data-wallet-test="clip"] .wallet-controls').classList.contains('is-upward'),
 inside: d.top >= p.top && d.bottom <= p.bottom && d.left >= p.left && d.right <= p.right,
 panel: { top: p.top, bottom: p.bottom, left: p.left, right: p.right },
 drawer: { top: d.top, bottom: d.bottom, left: d.left, right: d.right },
 };
 }"""
        )
        assert geometry["upward"] is True, geometry
        assert geometry["inside"] is True, geometry
        page.mouse.click(5, 5)
        page.wait_for_function(
            "document.querySelector('[data-wallet-test=clip] [data-wallet-toggle]')?.getAttribute('aria-expanded') === 'false'"
        )
        page.close()


def test_real_harvest_wallet_drawer_survives_hiveauth_rerender_live():
    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page(viewport={"width": 1536, "height": 900})
        page.add_init_script(
            """() => {
 window.hive_keychain = { requestBroadcast() {} };
 window.Worker = class {
 postMessage(message) {
 if (message.action === 'connect') setTimeout(() => this.onmessage?.({ data: {
 id: message.id, type: 'auth-wait', link: '#hiveauth',
 svg: '<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"180\" height=\"180\"></svg>'
 } }), 0);
 }
 terminate() {}
 };
 }"""
        )
        page.goto(f"{base_url}/?player=player0005", wait_until="domcontentloaded")
        page.wait_for_function("document.querySelectorAll('[data-deed-uid]').length > 0", timeout=30000)
        page.locator('#harvest-btn').click()
        panel = page.locator('#harvest-modal-overlay .harvest-panel')
        panel.wait_for(timeout=10000)
        toggle = panel.locator('[data-wallet-toggle]')
        toggle.click()
        panel.locator('.wallet-drawer.is-open').wait_for(timeout=5000)
        page.wait_for_timeout(500)
        before = page.evaluate(
            """() => {
 const button = document.querySelector('#harvest-modal-overlay [data-wallet-select="keychain"]');
 const r = button.getBoundingClientRect();
 const x = (r.left + r.right) / 2;
 const y = (r.top + r.bottom) / 2;
 const raw = document.elementFromPoint(x, y);
 return {x, y, raw: raw?.tagName || null, closest: raw?.closest('[data-wallet-select="keychain"]') === button, drawerOpen: document.querySelector('#harvest-modal-overlay .wallet-drawer')?.classList.contains('is-open')};
 }"""
        )
        assert before["drawerOpen"] is True and before["closest"] is True, before
        panel.locator('[data-wallet-select="hiveauth"]').click()
        panel.locator('.wallet-qr').wait_for(state="attached", timeout=5000)
        page.wait_for_timeout(500)
        after = page.evaluate(
            """() => {
 const button = document.querySelector('#harvest-modal-overlay [data-wallet-select="keychain"]');
 const r = button.getBoundingClientRect();
 const x = (r.left + r.right) / 2;
 const y = (r.top + r.bottom) / 2;
 const raw = document.elementFromPoint(x, y);
 return {x, y, raw: raw?.tagName || null, closest: raw?.closest('[data-wallet-select="keychain"]') === button, drawerOpen: document.querySelector('#harvest-modal-overlay .wallet-drawer')?.classList.contains('is-open')};
 }"""
        )
        assert after["drawerOpen"] is True and after["closest"] is True, after
        page.close()


def test_desktop_wallet_drawer_stays_open_after_stake_click():
    """a button inside the desktop drawer must not trigger picker close."""
    dec_payload = {
        "player": "player0005",
        "regions": [{
            "region_uid": "PR-PNW-1",
            "staked": 100,
            "required": 50,
            "in_use": 0,
            "surplus": 50,
            "shortfall": 0,
        }],
        "balance": 1000,
        "signing_enabled": False,
    }
    wallet_payload = {
        "player": "player0005",
        "account": None,
        "positions": [],
        "regions": [],
        "liquidity": [],
        "staked_dec": {"overall": "100", "by_region": []},
        "issues": [],
        "resources": [],
    }
    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page(viewport={"width": 1536, "height": 730})
        page.route("**/api/dec?*", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(dec_payload)
        ))
        page.route("**/api/wallet?*", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(wallet_payload)
        ))
        page.route("**/api/wallet/rewards?*", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"player": "player0005"})
        ))
        page.goto(f"{base_url}/?player=player0005", wait_until="domcontentloaded")
        page.locator("#wallet-edge-tab").click()
        page.wait_for_function("document.querySelector('#regional-dec-drawer')?.open === true", timeout=15_000)
        page.wait_for_function("document.querySelector('.dec-drawer-content .dec-status') === null", timeout=15_000)
        assert "EVP" not in page.locator("#regional-dec-drawer .dec-drawer-content").inner_text()
        before = page.evaluate(
            """() => {
 const drawer = document.querySelector('#regional-dec-drawer');
 return {
 open: drawer?.open,
 pickerOpen: document.querySelector('#wallet-picker')?.dataset.open,
 };
 }"""
        )
        page.locator('#regional-dec-drawer button[data-dec="stake"]:not([disabled])').first.click()
        page.wait_for_timeout(1500)
        after = page.evaluate(
            """() => {
 const drawer = document.querySelector('#regional-dec-drawer');
 return {
 open: drawer?.open,
 pickerOpen: document.querySelector('#wallet-picker')?.dataset.open,
 };
 }"""
        )
        assert before["open"] is True and after["open"] is True, {"before": before, "after": after}
        assert before["pickerOpen"] == "true" and after["pickerOpen"] == "true", {"before": before, "after": after}
        page.close()
