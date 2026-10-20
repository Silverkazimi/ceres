""": a page refresh keeps the optional login (tab-only sessionStorage, capped by the server's maximum, checked once, a 401 ends it)."""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

ACCOUNT_SPACE = Path(__file__).parents[2] / "ceres" / "static" / "account-space.js"

PRESET = r"""
const __store = new Map();
context.sessionStorage = { get length() { return __store.size; }, key: i => [...__store.keys()][i] ?? null,
  getItem: k => (__store.has(k) ? __store.get(k) : null), setItem: (k, v) => __store.set(k, String(v)), removeItem: k => __store.delete(k) };
context.__store = __store;
context.__calls = [];
context.__valid = true;
context.fetch = async (url, init = {}) => {
  context.__calls.push({ url: String(url), auth: (init.headers || {}).Authorization });
  const status = String(url) === '/api/auth/session' && !context.__valid ? 401 : 200;
  return { status, ok: status < 400, json: async () => ({ account: 'alice' }), headers: { get: () => null } };
};
globalThis.fetch = context.fetch;
"""


def run(body, expires="Date.now() + 3600000", valid=True, wallet=""):
    run_in_module_context(
        "(async () => {\n" + body + "\n})().then(() => process.exit(0), error => { console.error(error && error.stack || error); process.exit(1); });",
        entry=ACCOUNT_SPACE,
        extra_context_js=PRESET + f"\ncontext.__valid = {str(valid).lower()};\n__store.set('ceres:space-session:v1:alice', JSON.stringify({{ token: 't'.repeat(43), wallet: '{wallet}', expires: {expires} }}));\n",
    )


def test_a_valid_stored_login_is_restored_and_checked_once():
    run(r"""
if (!hasSession('alice')) throw Error('the login was not kept over the refresh');
await new Promise(resolve => setTimeout(resolve, 20));
const checks = __calls.filter(call => call.url === '/api/auth/session');
if (checks.length !== 1 || checks[0].auth !== 'Bearer ' + 't'.repeat(43)) throw Error('not checked once: ' + JSON.stringify(__calls));
if (!hasSession('alice')) throw Error('a valid login was dropped');
""")


def test_a_login_the_server_no_longer_accepts_is_dropped_everywhere():
    run(r"""
await new Promise(resolve => setTimeout(resolve, 20));
if (hasSession('alice') || __store.has('ceres:space-session:v1:alice')) throw Error('a rejected login stayed');
""", valid=False)


def test_a_login_past_its_maximum_is_never_restored():
    run(r"""
if (hasSession('alice') || __store.has('ceres:space-session:v1:alice')) throw Error('an expired login came back');
""", expires="Date.now() - 1")


def test_sign_out_removes_the_stored_login():
    run(r"""
await signOut('alice');
if (hasSession('alice') || __store.has('ceres:space-session:v1:alice')) throw Error('sign-out left the login behind');
""")


def test_choosing_hiveauth_in_the_account_menu_only_selects_it_so_sign_in_is_one_scan():
    wallet = (ACCOUNT_SPACE.parent / "wallet.js").read_text()
    menu = (ACCOUNT_SPACE.parent / "account-menu.js").read_text()
    assert "selectAccountWallet(walletAccount(), wallet.dataset.accountWallet)" in menu
    assert "walletControlsHtml" not in menu
    assert "connectHiveAuth(account, controls?.dataset.walletControlsId || null, message, callback)" in wallet


def test_the_wallet_of_the_login_is_kept_with_it():
    run(r"""
await new Promise(resolve => setTimeout(resolve, 20));
if (sessionWallet('alice') !== 'hiveauth') throw Error('the wallet of the restored login was lost');
await signOut('alice');
if (sessionWallet('alice') !== '') throw Error('sign-out kept the wallet');
""", wallet="hiveauth")
