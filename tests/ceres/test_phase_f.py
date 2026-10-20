""" Phase F guards for mobile staging, Changes, and Keychain guidance."""

from __future__ import annotations

import re

from .vm_context import STATIC, run_in_module_context


HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
ROWS_JS = (STATIC / "rows.js").read_text(encoding="utf-8")
PICKER_JS = (STATIC / "picker-actions.js").read_text(encoding="utf-8")
CANDIDATES_JS = (STATIC / "candidates.js").read_text(encoding="utf-8")
CHECKOUT_JS = (STATIC / "checkout.js").read_text(encoding="utf-8")
STAGE_NAV_JS = (STATIC / "stage-navigation.js").read_text(encoding="utf-8")
CSS = (STATIC / "ceres.css").read_text(encoding="utf-8")


def test_mobile_changes_button_reuses_the_checkout_surface_and_model() -> None:
    button = re.search(r'<button[^>]*id="mobile-changes-btn"[^>]*>', HTML)
    assert button
    assert 'data-checkout-action="toggle"' in button.group(0)
    assert 'aria-controls="checkout-panel"' in button.group(0)
    assert 'id="mobile-changes-count"' in HTML
    assert "actions?.addEventListener('click', handleSlotEditorClick);" in APP_JS
    # (v0.9.626) parks unready first-clear builds; buildChangesModel now takes a wrapped setAside.
    assert "const model = buildChangesModel(CERES_STAGE, (entry) => stageSetAside(entry)" in CHECKOUT_JS
    assert "mobileButton.disabled = !visible;" in CHECKOUT_JS
    assert "mobileCount.textContent = String(state?.count || 0);" in CHECKOUT_JS
    assert "el.innerHTML = '<div class=\"harvest-panel changes-panel\" id=\"checkout-panel\"" in CHECKOUT_JS


def test_mobile_plot_sheet_uses_the_existing_staging_handler_and_forces_grid() -> None:
    assert "addEventListener('click', handleSlotEditorClick)" in APP_JS
    assert "addEventListener('input', handlePickerFilter)" in APP_JS
    assert "addEventListener('change', handlePickerFilter)" in APP_JS
    assert "viewMode: mobile ? 'grid' : readPickerViewPreference()" in PICKER_JS
    assert "const pickerElement = pickerElementFor(deedUid);" in PICKER_JS
    assert "if (!picker.mobile && typeof scrollPlotAfterSlotAdd === 'function')" in PICKER_JS
    assert 'data-picker-action="choose" data-candidate-index="${index}"' in CANDIDATES_JS
    assert 'data-picker-action="choose" data-picker-rarity="${rarity}"' in CANDIDATES_JS
    constrained = ROWS_JS.split("function constrainMobileEditorActions(root) {", 1)[1].split("\n}", 1)[0]
    assert "'[data-slot-action]'" not in constrained
    for desktop_only in ('[data-combine-action]', '[data-slot-action="combine-plot"]', '[data-slot-action="combine-tile"]'):
        assert desktop_only in constrained
    # v0.9.505 removed the mobile combining notice text entirely (controls are removed silently, no message).
    assert "initPickerFilterDropdowns(body)" in ROWS_JS


def test_missing_keychain_guidance_replaces_the_dead_mobile_sign_button_in_vm() -> None:
    # v0.9.511 ("Add account-bound HiveAuth alternative to Ceres signing", maintainer decision
    # 2026-09-16: Keychain app browser + explicit HiveAuth alternative) replaced the old
    # copy-link-only Keychain guidance with the shared wallet chooser (walletControlsHtml)
    # offering both providers regardless of board tier; it renders only once an account is known.
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
document.documentElement.classList = {contains(name) { return name === 'ceres-lw-le-1279'; }};
assert(mobileKeychainGuidanceHtml() === '', 'no account known yet -> no wallet controls to show');
BOARD = {player: 'testaccount'};
const guidance = mobileKeychainGuidanceHtml();
assert(guidance.includes('data-wallet-select="keychain"'), 'guidance must offer Hive Keychain');
assert(guidance.includes('data-wallet-select="hiveauth"'), 'guidance must offer the HiveAuth alternative');
""",
        entry=STATIC / "checkout.js",
    )
    assert "const mobileKeychainMissing = mobileBoardTier() && !keychainReady;" in CHECKOUT_JS
    note_line = next(line for line in CHECKOUT_JS.splitlines() if line.lstrip().startswith("const note ="))
    assert ": mobileKeychainMissing ? 'Wallet not connected.'" in note_line  # after the upgrade-plan-only branch
    assert "navigator.clipboard.writeText(window.location.href)" in PICKER_JS


def test_changes_links_route_back_to_the_mobile_plot_sheet() -> None:
    assert "if (mobileNavigationActive())" in STAGE_NAV_JS
    assert "openMobilePlotSheet(deedUid, card)" in STAGE_NAV_JS
    assert "ceresMobilePlotSheet: true" in STAGE_NAV_JS
    assert "find(plotFrame(deedUid))" in STAGE_NAV_JS


def test_phase_f_css_is_mobile_tier_scoped_and_keeps_scale_guards_flat() -> None:
    block = CSS.split("/*  PHASE F MOBILE STAGING START */", 1)[1].split(
        "/*  PHASE F MOBILE STAGING END */", 1
    )[0]
    selectors = re.findall(r"([^{}]+)\{", re.sub(r"/\*.*?\*/", "", block, flags=re.S))
    assert selectors
    for selector_group in selectors:
        for selector in selector_group.split(","):
            assert selector.strip().startswith("html.ceres-lw-le-1919.ceres-lw-le-1279"), selector
    assert "@media" not in block
    assert not re.search(r"(?<![-A-Za-z])\d+(?:\.\d+)?(?:vw|vh)\b", block)
    assert "#changes-overlay > .changes-panel" in block
    assert "candidate-list.is-card-grid" in block
    assert ".picker-combine" in block
