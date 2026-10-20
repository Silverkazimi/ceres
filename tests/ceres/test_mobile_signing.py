"""/: leaving for the wallet app must not delete a routing run; the wallet drawer (QR) is clamped inside the viewport."""
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_pagehide_never_discards_a_run_that_is_being_signed():
    source = (STATIC / "routing-run.js").read_text()
    handler = source[source.index("const onLeave"):source.index("window.addEventListener('pagehide'")]
    assert "signing()" in handler and "event?.persisted" in handler
    assert "S.busy || Object.values(S.record?.bundles" in source
    assert "['claimed', 'attached', 'checking', 'outcome_unknown']" in source[source.index("const signing"):source.index("const onLeave")]


def test_account_menu_places_its_inline_pairing_without_a_transaction_drawer():
    menu = (STATIC / "account-menu.js").read_text()
    assert "walletControlsHtml" not in menu and "layoutOpenWalletDrawers" not in menu
    assert "accountWalletRequest(account)" in menu and "panel.innerHTML = accountMenuHtml();\n  place();" in menu
    css = (STATIC / "ceres.css").read_text()
    assert ".account-menu {" in css and "max-block-size: calc(100vh - 5rem)" in css
    assert ".acct-pairing .wallet-qr svg { max-inline-size: 100%; block-size: auto; }" in css


def test_routing_window_tells_the_player_the_transaction_waits_in_the_wallet_app():
    window = (STATIC / "routing-window.js").read_text()
    assert "state !== 'claimed'" in window and "walletLabelKind(player) !== 'hiveauth'" in window
    assert "Switch to the Hive Keychain app and confirm the transaction." in window
    assert "${walletWaitHtml(state, player, error, busy)}${terminal}" in window
    assert ".r3-wallet-wait" in (STATIC / "ceres.css").read_text()


def test_hiveauth_waiting_text_tells_the_player_to_switch_to_the_keychain_app_in_large_orange_letters():
    wallet = (STATIC / "wallet.js").read_text()
    assert "Switch to the Hive Keychain app and confirm the authentication." in wallet
    assert "Switch to the Hive Keychain app and confirm the transaction." in wallet
    assert wallet.count("wallet-status${actionClass(requestView.status)}") == 2
    assert "status: 'Waiting for wallet approval'" not in wallet
    css = (STATIC / "ceres.css").read_text()
    rule = css[css.index(".wallet-status.is-action"):].split("}")[0]
    assert "var(--warn)" in rule and "font-size:1.25rem" in rule


def test_transaction_wallet_drawers_still_clamp_after_account_menu_simplification():
    """: the account panel uses inline pairing; transaction footers keep their clamped wallet drawer."""
    wallet = (STATIC / "wallet.js").read_text()
    guard = wallet[wallet.index("function layoutDrawer"):wallet.index("export function layoutOpenWalletDrawers")]
    assert "drawer?.classList.contains('is-open')" in guard
    assert "walletControlsHtml(account, 'account-menu')" not in (STATIC / "account-menu.js").read_text()
    assert "walletControlsHtml(player, 'routing-window')" in (STATIC / "routing-window.js").read_text()
    assert "walletControlsHtml(player, 'harvest-routing')" in (STATIC / "harvest-routing.js").read_text()


def test_receipt_shows_one_ledger_and_centred_tiles_and_routing_can_find_a_lost_signature():
    """ """
    results = (STATIC / "harvest-results.js").read_text()
    assert "members.length === 1 && isRegion && ledger" in results
    css = (STATIC / "mobile-plot.css").read_text()
    assert ".harvest-receipt-ledger .hm-batch-badge .hm-batch-number { grid-row: 1; grid-column: 2; align-self: center" in css
    assert '[data-resource="research"] { grid-column: auto; }' in css and ".r3-destination > strong { font-size: 1rem" in css
    window = (STATIC / "routing-window.js").read_text()
    assert "(current.querySelector('.r3-wallet-wait')?.innerHTML ?? '')" in window and 'data-r3="find"' in window
    assert "['attached', 'checking', 'outcome_unknown'].includes(state) ? `<span>Metered routing checks" in window
    assert "find: findSigned" in (STATIC / "routing-run.js").read_text()
    from ceres import routing_runs_v2 as runs
    op = ["custom_json", {"id": "sm_x", "json": '{"n":"a"}', "required_auths": [], "required_posting_auths": ["p"]}]
    row = [1, {"trx_id": "b" * 40, "op": ["custom_json", {"id": "sm_x", "json": '{"n": "a"}', "required_auths": [], "required_posting_auths": ["p"]}]}]
    assert runs._history_transaction([row], [op]) == "b" * 40 and runs._history_transaction([], [op]) is None
