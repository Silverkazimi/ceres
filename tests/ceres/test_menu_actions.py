""" step2c shared actions and explicit copy selection contract."""

from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"


def test_existing_owned_menu_uses_one_eligibility_read_and_initially_empty_selection():
    cart = (STATIC / "card-cart.js").read_text(encoding="utf-8")
    actions = (STATIC / "card-actions-menu.js").read_text(encoding="utf-8")
    assert "export async function cartOpenOwnedMenu" in cart
    assert "sharedCardActionsHtml(copies, selectedIndexes || new Set(), actionEligibility, actionNote)" in cart
    assert "selectedIndexes: new Set()" in cart
    assert "readCardActionEligibility(ownedCopies, player)" in cart
    assert "method: 'POST'" in actions
    assert "asset_refs: refs, staged_plans: boardStagedPlans(player)" in actions
    assert "copy.asset_ref" in actions


def test_copy_selection_and_shared_bar_contract():
    cart = (STATIC / "card-cart.js").read_text(encoding="utf-8")
    actions = (STATIC / "card-actions-menu.js").read_text(encoding="utf-8")
    for required in ('type="checkbox"', "data-copy-index", "selectedIndexes.add(index)", "selectedIndexes.delete(index)"):
        assert required in cart
    assert "sharedCardActionsHtml" in cart
    assert "Select a copy first" in actions
    assert "Select exactly one staked copy" in actions
    assert "Select one copy to stake" in actions
    assert "result.reason" in actions
    assert "for (const copy of selected)" in actions
    assert "!result.available" in actions
    assert """const greyed = state.allowed ? '' : ' aria-disabled="true"';""" in actions
    assert "uid" not in actions.lower()
    assert "data-cm-action" not in actions
    assert "fetch(`/api/cards/eligibility${query}`" in actions
    assert "fetch(`/api/" not in actions.replace("fetch(`/api/cards/eligibility${query}`", "")


def test_action_buttons_are_icon_only_shared_controls_with_styled_tooltips():
    actions = (STATIC / "card-actions-menu.js").read_text(encoding="utf-8")
    css = (STATIC / "card-cart.css").read_text(encoding="utf-8")
    for label in ("Stake", "Unstake", "Go to plot", "Delegate", "Recall delegation", "Return card", "Transfer", "Burn for DEC"):
        assert label in actions
    assert "data-control-density=\"compact\"" in actions
    assert "data-control-mode=\"icon\"" in actions
    assert "tipAttrs({ title: state.allowed ? label : `${label}: ${state.reason}` })" in actions
    assert "cm-copy-action-panel" in css
    assert "white-space: nowrap" in css
    assert "cm-copy-action-reasons" not in css


def test_every_action_has_its_own_registry_icon():
    """Round 2: eight actions, eight distinct glyphs from the button icon registry, none shared."""
    import re

    actions = (STATIC / "card-actions-menu.js").read_text(encoding="utf-8")
    registry = (STATIC / "button-icons.js").read_text(encoding="utf-8")
    block = actions[actions.index("export const CARD_ACTIONS = ["):]
    block = block[: block.index("];")]
    entries = re.findall(r"\['([a-z_]+)', '([^']+)', CARD_ACTION_ICONS\['([a-z-]+)'\]\]", block)
    assert [key for key, _, _ in entries] == [
        "stake", "unstake", "plot_link", "delegate", "recall_delegation", "return_card", "transfer", "burn",
    ]
    assert block.count("CARD_ACTION_ICONS[") == len(entries)  # no action without a registry icon
    names = [name for _, _, name in entries]
    assert len(set(names)) == len(names), "two actions share one icon"
    reg = registry[registry.index("export const CARD_ACTION_ICONS = {"):]
    reg = reg[: reg.index("};")]
    glyphs = dict(re.findall(r'"([a-z-]+)": LINE\(\'(.*?)\'\)', reg))
    assert set(names) <= set(glyphs), "an action names an icon the registry lacks"
    bodies = [glyphs[name] for name in names]
    assert len(set(bodies)) == len(bodies), "two registry icons draw the same glyph"
    assert 'viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"' in registry
    assert "COMMIT_ICON" not in actions and "RETURN_ICON" not in actions


def test_phone_owned_rows_keep_their_checkbox_column():
    """Round 2: at phone widths the checkbox has its own column; Lv, CC and PP follow it."""
    css = (STATIC / "card-cart.css").read_text(encoding="utf-8")
    prefix = "html.ceres-lw-le-1279 .card-browser .cm-dialog-panel.is-narrow .cm-copy"
    assert f"{prefix}-table > .cm-copy.cm-owned-copy {{ grid-template-columns: 1.25rem repeat(3, minmax(0, 1fr)); }}" in css
    for cell, column in (("select", "1"), ("lv", "2"), ("cc", "3"), ("pp", "4")):
        assert f"{prefix}.cm-owned-copy .cm-c-{cell} {{ grid-column: {column}; grid-row: 1;" in css


def test_owned_menu_uses_tile_art_helper_and_placeholder_without_new_source():
    browser = (STATIC / "card-browser.js").read_text(encoding="utf-8")
    cart = (STATIC / "card-cart.js").read_text(encoding="utf-8")
    art = (STATIC / "card-browser-art.js").read_text(encoding="utf-8")
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert "const image = cardArtImageHtml(card);" in browser
    assert "${cardArtImageHtml(m.row)}" in cart
    assert 'class="cm-menu-art cm-owned-art"' in cart
    assert 'data-cb-art="${esc(JSON.stringify(art))}"' in art
    assert "cardArtPlaceholderHtml()" in art
    assert "onCardArtError" in browser
    assert 'cb-art-missing-text">No image</span>' in art
    assert "https://d36mxiodymuqjm.cloudfront.net" in art
    assert "https://" not in cart
    assert ".cm-owned-art > img { display: block; inline-size: 100%; block-size: 100%;" in css
    assert ".cm-owned-art { block-size: 8.42rem;" in css
    assert ".cm-owned-art { inline-size: 4.5rem; block-size: 6.31rem; }" in css
    assert ".cm-dialog-panel.is-narrow .cm-menu-art { inline-size: 4.5rem; }" in css
    assert "const CB_TILE_MENU = true;" in browser


def test_shared_art_helper_keeps_beta_source_and_tile_fallback():
    output = run_in_module_context("""
const beta = {name: 'Lyanna Natura', edition: 1, card_set: 'beta', foil: 0};
const image = cardArtImageHtml(beta);
const missing = cardArtImageHtml({name: 'Unknown', edition: 999, card_set: 'unknown'});
console.log(JSON.stringify({image, missing}));
""", entry=STATIC / "card-browser-art.js").stdout
    import json

    result = json.loads(output.strip())
    assert 'https://d36mxiodymuqjm.cloudfront.net/cards_beta/Lyanna%20Natura.jpg' in result['image']
    assert 'data-cb-art=' in result['image']
    assert result['missing'] == '<span class="cb-art-missing-text">No image</span>'
