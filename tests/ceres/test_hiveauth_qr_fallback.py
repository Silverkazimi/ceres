""": saved HiveAuth sign-in must recover to a fresh QR without a live server."""

import pytest

from tests.ceres.test_account_menu import run


SETUP = r"""
selectAccountWallet('alice', 'hiveauth');
const baseFetch = fetch;
fetch = (url, init) => String(url).startsWith('/api/me/')
  ? Promise.resolve({ status: 404, json: async () => ({}), headers: { get() { return null; } } })
  : baseFetch(url, init);
window.fetch = fetch;
localStorage.setItem('ceres:hiveauth-session:v1:alice', JSON.stringify({
  account: 'alice', username: 'alice', token: 'saved-token', key: 'saved-key', expire: Date.now() + 60000,
}));
const oldWorker = createHiveAuthWorker('alice', Date.now() + 60000);
mountAccountMenu();
__button.click({ stopPropagation() {} });
const click = action => __panel.click({
  target: { closest: selector => selector === '[data-account]' ? { dataset: { account: action }, disabled: false } : null },
  stopPropagation() {},
});
click('player0014');
await new Promise(resolve => setTimeout(resolve, 20));
const challenge = oldWorker.messages.find(message => message.action === 'challenge');
if (!challenge || oldWorker.messages.some(message => message.action === 'connect')) throw Error('saved session did not select challenge');
if (!__panel.innerHTML.includes('Confirm in the Hive Keychain app') || __panel.innerHTML.includes('wallet-qr')) throw Error('saved session did not reproduce no-QR wait');
if (!walletControlsHtml('alice').includes('data-wallet-pair-again')) throw Error('shared wallet request has no QR recovery action');
"""


def test_saved_session_wait_offers_pair_again_and_new_qr():
    run(SETUP + r"""
if (!__panel.innerHTML.includes('data-account="pair-again"') || !__panel.innerHTML.includes('Pair again with a QR code')) throw Error('no recovery action while the old phone pairing is silent');
click('pair-again');
const newWorker = __workers.at(-1);
const connect = newWorker?.messages.find(message => message.action === 'connect');
if (newWorker === oldWorker || !oldWorker.terminated || !connect || connect.message !== 'login text' || connect.credentials) throw Error('pair again did not start a fresh login QR request');
if (localStorage.getItem('ceres:hiveauth-session:v1:alice')) throw Error('pair again kept obsolete saved pairing');
newWorker.onmessage({ data: { id: connect.id, type: 'auth-wait', link: 'has://new-pairing', svg: '<svg></svg>' } });
if (!__panel.innerHTML.includes('HiveAuth connection QR code') || !__panel.innerHTML.includes('has://new-pairing')) throw Error('fresh QR missing after pair again');
const expire = Date.now() + 60000;
newWorker.onmessage({ data: { id: connect.id, type: 'connected', account: 'alice', expire,
  credentials: { account: 'alice', username: 'alice', token: 'new-token', key: 'new-key', expire }, signature: 'a'.repeat(130) } });
await new Promise(resolve => setTimeout(resolve, 20));
if (!hasSession('alice') || !__panel.innerHTML.includes('Signed in as alice')) throw Error('fresh QR approval did not complete the original sign-in');
""")


@pytest.mark.parametrize("response", ["challenge-failed", "declined", "not-sent"])
def test_rejected_challenge_automatically_starts_fresh_qr(response):
    run(SETUP + r"""
oldWorker.onmessage({ data: { id: challenge.id, type: RESPONSE } });
const newWorker = __workers.at(-1);
const connect = newWorker?.messages.find(message => message.action === 'connect');
if (newWorker === oldWorker || !oldWorker.terminated || !connect || connect.message !== 'login text' || connect.credentials) throw Error('failed challenge did not restart sign-in with fresh pairing');
newWorker.onmessage({ data: { id: connect.id, type: 'auth-wait', link: 'has://after-error', svg: '<svg></svg>' } });
if (!__panel.innerHTML.includes('HiveAuth connection QR code')) throw Error('automatic fallback QR missing');
click('cancel');
""".replace("RESPONSE", repr(response)))
