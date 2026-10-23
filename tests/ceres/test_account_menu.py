""": the optional login stays in one account panel; transaction wallet controls stay separate."""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

MENU = Path(__file__).parents[2] / "ceres" / "static" / "account-menu.js"

CONTEXT = r"""
window.isSecureContext = true;
context.crypto = require('node:crypto').webcrypto;
context.WebSocket = class {};
context.Worker = class {
  constructor() { this.messages = []; context.__workers.push(this); }
  postMessage(message) { this.messages.push(message); }
  terminate() { this.terminated = true; }
};
context.__workers = [];
context.__button = {
  dataset: {}, classList: { toggle() {} }, style: {}, setAttribute() {}, removeAttribute() {},
  getBoundingClientRect() { return { left: 100, width: 40, bottom: 50 }; },
  contains() { return false; }, addEventListener(type, fn) { this[type] = fn; },
};
context.__panel = {
  dataset: {}, hidden: true, offsetWidth: 330, innerHTML: '',
  style: { setProperty() {} }, classList: { toggle() {} }, setAttribute() {},
  contains() { return false; }, addEventListener(type, fn) { this[type] = fn; },
};
document.getElementById = id => id === 'account-menu-btn' ? context.__button : null;
document.createElement = () => context.__panel;
window.dispatchEvent = event => { for (const fn of __ceresListeners[event.type] || []) fn(event); return true; };
context.fetch = async (url, init = {}) => {
  if (url === '/api/auth/challenge') return { status: 200, json: async () => ({ message: 'login text', challenge_id: 'challenge' }), headers: { get() { return null; } } };
  if (url === '/api/auth/verify') return { status: 200, json: async () => ({ account: 'alice', token: 'token' }), headers: { get() { return null; } } };
  throw Error('unexpected fetch: ' + url);
};
context.window.fetch = context.fetch;
"""


def run(body: str, context: str = CONTEXT):
    result = run_in_module_context(
        "(async () => {\nCURRENT_PLAYER = 'alice';\n" + body + "\n})().then(() => process.exit(0), error => { console.error(error && error.stack || error); process.exit(1); });",
        entry=MENU, extra_context_js=context, check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_first_hiveauth_sign_in_click_keeps_menu_open_and_shows_inline_qr():
    run(r"""
mountAccountMenu();
__button.click({ stopPropagation() {} });
selectAccountWallet('alice', 'hiveauth');
let stopped = false;
__panel.click({ target: { closest: selector => selector === '[data-account]' ? { dataset: { account: 'player0014' }, disabled: false } : null }, stopPropagation() { stopped = true; } });
if (!stopped) for (const fn of __ceresListeners['doc:click'] || []) fn({ target: { closest() { return null; } } });
await new Promise(resolve => setTimeout(resolve, 20));
const worker = __workers.at(-1);
const sent = worker?.messages.find(message => message.action === 'connect');
if (!sent || sent.message !== 'login text') throw Error('sign-in did not start one connect with the challenge');
worker.onmessage({ data: { id: sent.id, type: 'auth-wait', link: 'has://auth', svg: '<svg viewBox="0 0 10 10"></svg>' } });
if (__panel.hidden || !__panel.innerHTML.includes('HiveAuth connection QR code') || !__panel.innerHTML.includes('has://auth')) throw Error('first click did not keep inline QR visible');
if (!__panel.innerHTML.includes('Scan with the Hive Keychain app and confirm') || !__panel.innerHTML.includes('data-account="cancel"')) throw Error('waiting instructions or Cancel missing');
state.attempt++; cancelAccountWalletRequest('alice');
""")


def test_live_hiveauth_session_requests_challenge_without_qr():
    run(r"""
selectAccountWallet('alice', 'hiveauth');
const worker = new Worker('mock');
session = { account: 'alice', worker, expire: Date.now() + 60000 };
state.busy = true; state.loginKind = 'hiveauth';
requestWalletSignChallenge('alice', 'login text', () => {});
const sent = worker.messages.find(message => message.action === 'challenge');
if (!sent || worker.messages.some(message => message.action === 'connect')) throw Error('live session was not reused');
const html = accountMenuHtml('alice');
if (html.includes('wallet-qr') || !html.includes('Confirm in the Hive Keychain app')) throw Error('challenge should wait without QR');
cancelAccountWalletRequest('alice');
if (pending || session?.worker !== worker || worker.terminated) throw Error('cancelling a challenge must keep the approved session');
""")


def test_account_controls_have_no_transaction_wallet_drawer():
    run(r"""
window.hive_keychain = { requestBroadcast() {}, requestSignBuffer() {} };
selectAccountWallet('alice', 'keychain');
let html = accountMenuHtml('alice');
if (!html.includes('data-account-wallet="keychain"') || !html.includes('data-account-wallet="hiveauth"')) throw Error('wallet choices missing');
if (html.includes('wallet-toggle') || html.includes('wallet-drawer') || html.includes('Connect HiveAuth') || html.includes('wallet-controls')) throw Error('transaction wallet drawer leaked into the account menu');
""")


def test_signed_in_delete_options_are_folded_and_require_confirmation():
    run(r"""
await player0014('alice', { sign: async () => 'a'.repeat(130) });
html = accountMenuHtml('alice');
if (!html.includes('Signed in as alice') || !html.includes('data-account="signout"')) throw Error('signed-in state missing');
if (!html.includes('Saved data options') || html.includes('data-account="delete-space"')) throw Error('delete options are not folded');
// Sign out and Saved data options share one row, and Sign out wears the wallet logo of the current login
const row = html.match(/<div class="acct-actions">(.*?)<\/div>/s);
if (!row || !row[1].includes('data-account="signout"') || !row[1].includes('data-account="manage"')) throw Error('Sign out and Saved data options are not on one row');
if (!/data-account="signout"[^>]*>\s*<img class="wallet-logo"/.test(html)) throw Error('Sign out does not show the wallet logo');
mountAccountMenu();
__button.click({ stopPropagation() {} });
const click = action => __panel.click({ target: { closest: selector => selector === '[data-account]' ? { dataset: { account: action } } : null }, stopPropagation() {} });
click('manage');
html = __panel.innerHTML;
if (!html.includes('data-account="delete-space"') || !html.includes('data-account="delete-cache"')) throw Error('folded deletion options missing');
click('delete-space');
html = __panel.innerHTML;
if (!html.includes('This permanently deletes your saved routing setup') || !html.includes('data-account="delete-confirm"')) throw Error('delete confirmation missing');
click('delete-cancel');
if (state.confirm || __panel.innerHTML.includes('data-account="delete-confirm"')) throw Error('Cancel did not leave confirmation');
click('delete-cache');
if (!__panel.innerHTML.includes('This deletes the game data Ceres cached') || !__panel.innerHTML.includes('data-account="delete-confirm"')) throw Error('cache delete confirmation missing');
""")


def test_keychain_sign_in_click_waits_for_extension_and_offers_no_cancel():   # the extension popup cannot be withdrawn from the page
    run(r"""
let approve;
window.hive_keychain = { requestBroadcast() {}, requestSignBuffer(account, message, authority, callback) { approve = callback; } };
selectAccountWallet('alice', 'keychain');
mountAccountMenu();
__button.click({ stopPropagation() {} });
const click = action => __panel.click({ target: { closest: selector => selector === '[data-account]' ? { dataset: { account: action } } : null }, stopPropagation() {} });
click('player0014');
await new Promise(resolve => setTimeout(resolve, 20));
if (!approve || __panel.hidden || !__panel.innerHTML.includes('Confirm in the Keychain extension')) throw Error('Keychain approval was not shown inline');
if (__panel.innerHTML.includes('wallet-qr')) throw Error('Keychain must not show a QR');
if (__panel.innerHTML.includes('data-account="cancel"')) throw Error('Keychain must not offer a Cancel that cannot withdraw the extension popup');
""")


def test_cancel_hiveauth_pairing_terminates_worker_and_ignores_late_response():
    run(r"""
selectAccountWallet('alice', 'hiveauth');
mountAccountMenu();
__button.click({ stopPropagation() {} });
const click = action => __panel.click({ target: { closest: selector => selector === '[data-account]' ? { dataset: { account: action } } : null }, stopPropagation() {} });
click('player0014');
await new Promise(resolve => setTimeout(resolve, 20));
const worker = __workers.at(-1);
const sent = worker.messages.find(message => message.action === 'connect');
click('cancel');
if (!worker.terminated || pending || accountWalletRequest('alice')) throw Error('pairing survived cancellation');
worker.onmessage({ data: { id: sent.id, type: 'connected', account: 'alice', expire: Date.now() + 60000, signature: 'a'.repeat(130) } });
await new Promise(resolve => setTimeout(resolve, 10));
if (hasSession('alice') || state.busy || __panel.hidden) throw Error('late HiveAuth approval bypassed Cancel');
""")


def test_cancel_during_server_verification_does_not_save_session():
    run(r"""
let finishVerify;
const originalFetch = fetch;
fetch = (url, init) => url === '/api/auth/verify'
  ? new Promise(resolve => { finishVerify = resolve; }) : originalFetch(url, init);
const attempt = ++state.attempt;
const signing = player0014('alice', { sign: async () => 'a'.repeat(130), shouldContinue: () => state.attempt === attempt });
for (let i = 0; i < 20 && !finishVerify; i++) await new Promise(resolve => setTimeout(resolve, 1));
if (!finishVerify) throw Error('verification did not begin');
++state.attempt;
finishVerify({ status: 200, json: async () => ({ account: 'alice', token: 'token' }), headers: { get() { return null; } } });
const result = await signing;
if (result.ok || hasSession('alice')) throw Error('late verification saved a cancelled login');
""")
