""": a HiveAuth transaction request the relay never confirms is sent once more on a fresh worker.

The relay answers every sign_req with sign_wait within about a second, so silence for 8 s means the request went into a stale
socket. Ceres then resends it ONCE on a fresh worker (fresh websocket), never after sign_wait (that could sign it twice), and
refuses requests too large for the relay before sending them. No real time passes: the 8 s and 110 s timers are captured.
"""

from tests.ceres.test_ir7070_hiveauth_session import WALLET, WORKER_SOURCE, fake_hiveauth_context, worker_runtime_source
from tests.ceres.vm_context import run_in_module_context


ACK_TIMER = r"""
const __ir9525SetTimeout = context.setTimeout;
context.__ackTimers = [];
context.setTimeout = (fn, ms) => {
  if (ms === 8000) { context.__ackTimers.push(fn); return 9525; }
  if (ms === 110000) { context.__hiveAuthWatchdog = fn; return 7070; }
  return __ir9525SetTimeout(fn, ms);
};
"""

START = r"""
__workerBroadcast = 'wait';
const operations = [['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: '{"n":"ir9525"}' }]];
const answers = [];
const first = __workers.at(-1);
if (!first || !first.messages.some(message => message.action === 'restore')) throw Error('saved session was not restored');
requestWalletBroadcast('alice', operations, answer => answers.push(answer));
const sent = first.messages.find(message => message.action === 'broadcast');
if (!sent) throw Error('broadcast not posted to the worker');
if (__ackTimers.length !== 1) throw Error('no relay acknowledgement timer for the broadcast');
const broadcasts = () => __workers.flatMap(worker => worker.messages.filter(message => message.action === 'broadcast'));
"""


def run(body: str) -> None:
    run_in_module_context(
        "(async () => {\n" + START + body + "\n})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });",
        entry=WALLET,
        extra_context_js=fake_hiveauth_context() + ACK_TIMER,
    )


def test_sign_wait_sets_relay_confirmed_state():
    run(r"""
if (walletRelayConfirmed('alice')) throw Error('relay confirmed before any sign_wait');
first.onmessage({ data: { id: sent.id, type: 'sign-wait' } });
if (!walletRelayConfirmed('alice')) throw Error('sign_wait did not set the relay-confirmed state');
if (walletSigningStatus('alice') !== 'The HiveAuth relay confirmed the request. Switch to the Hive Keychain app and confirm the transaction.') throw Error('status: ' + walletSigningStatus('alice'));
const wait = walletWaitHtml('alice', 'Posting', { since: Date.now() });
if (!wait.includes('class="wallet-relay-confirmed"') || !wait.includes('Relay confirmed: the request reached HiveAuth.')) throw Error('wait box has no relay line: ' + wait);
first.onmessage({ data: { id: sent.id, type: 'signed', txId: 'a'.repeat(40) } });
if (answers.length !== 1 || !answers[0].success || answers[0].result.id !== 'a'.repeat(40)) throw Error('signed answer lost: ' + JSON.stringify(answers));
if (walletRelayConfirmed('alice')) throw Error('relay-confirmed state outlived the request');
""")


def test_no_ack_resends_exactly_once_on_a_fresh_worker():
    run(r"""
__ackTimers[0]();
const fresh = __workers.at(-1);
if (fresh === first || !first.terminated) throw Error('no fresh worker, or the stale one kept running');
const order = fresh.messages.map(message => message.action);
if (JSON.stringify(order) !== JSON.stringify(['restore', 'broadcast'])) throw Error('fresh worker got ' + JSON.stringify(order));
const restore = fresh.messages[0];
if (restore.credentials.token !== 'session-token' || restore.credentials.key !== 'shared-auth-key') throw Error('fresh worker not restored with the saved session');
const resent = fresh.messages[1];
if (resent.id !== sent.id || JSON.stringify(resent.operations) !== JSON.stringify(operations) || resent.authority !== 'Posting') throw Error('resend differs from the request');
if (!walletReady('alice')) throw Error('resend dropped the HiveAuth session');
// A second silence (or the same timer firing again) must not send a third time.
__ackTimers[0]();
for (const timer of __ackTimers.slice(1)) timer();
if (broadcasts().length !== 2 || __workers.at(-1) !== fresh) throw Error('more than one resend: ' + broadcasts().length);
// The stale worker can no longer answer for this request.
first.onmessage({ data: { id: sent.id, type: 'signed', txId: 'b'.repeat(40) } });
if (answers.length) throw Error('the terminated worker answered the request');
fresh.onmessage({ data: { id: sent.id, type: 'sign-wait' } });
if (!walletRelayConfirmed('alice')) throw Error('fresh worker sign_wait not shown');
fresh.onmessage({ data: { id: sent.id, type: 'signed', txId: 'c'.repeat(40) } });
if (answers.length !== 1 || answers[0].result.id !== 'c'.repeat(40)) throw Error('resent request answer lost: ' + JSON.stringify(answers));
""")


def test_no_resend_after_sign_wait():
    run(r"""
first.onmessage({ data: { id: sent.id, type: 'sign-wait' } });
__ackTimers[0]();
if (__workers.at(-1) !== first || first.terminated || broadcasts().length !== 1) throw Error('a request the relay confirmed was resent');
// The watchdog still reports it as uncertain (never as a failure that invites signing again).
__hiveAuthWatchdog();
if (answers.length !== 1 || !answers[0].ambiguousBroadcast || !answers[0].stillChecking) throw Error('confirmed request lost its uncertain outcome: ' + JSON.stringify(answers));
if (broadcasts().length !== 1) throw Error('watchdog resent the request');
""")


def test_no_resend_after_the_request_ended():
    run(r"""
first.onmessage({ data: { id: sent.id, type: 'declined', reason: 'declined' } });
__ackTimers[0]();
if (__workers.at(-1) !== first || broadcasts().length !== 1) throw Error('a finished request was resent');
if (answers.length !== 1 || !answers[0].declined) throw Error('decline lost');
""")


def test_wallet_shows_the_too_large_refusal_as_not_sent():
    run(r"""
first.onmessage({ data: { id: sent.id, type: 'not-sent', reason: 'too-large' } });
if (answers.length !== 1 || !answers[0].notSent || answers[0].ambiguousBroadcast) throw Error('too-large not a definite not-sent: ' + JSON.stringify(answers));
if (answers[0].error !== 'This request is too large for HiveAuth. It was not sent. Sign it with Hive Keychain or in smaller parts.') throw Error('message: ' + answers[0].error);
__ackTimers[0]();
if (broadcasts().length !== 1) throw Error('a refused request was resent');
""")


def test_worker_refuses_an_oversized_request_before_sending():
    run_in_module_context(
        r"""
(async () => {
  await self.onmessage({ data: { id: 'restore', action: 'restore', account: 'alice', credentials: {
    account: 'alice', username: 'alice', token: 'token', key: 'key', expire: Date.now() + 60000,
  } } });
  const big = [['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: 'x'.repeat(40001) }]];
  await self.onmessage({ data: { id: 'big', action: 'broadcast', account: 'alice', operations: big, authority: 'Posting' } });
  const refused = __workerMessages.find(message => message.id === 'big');
  if (refused?.type !== 'not-sent' || refused.reason !== 'too-large') throw Error('oversized request not refused: ' + JSON.stringify(__workerMessages));
  if (__calls.length) throw Error('oversized request reached the relay');
  const small = [['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: 'x'.repeat(30000) }]];
  await self.onmessage({ data: { id: 'small', action: 'broadcast', account: 'alice', operations: small, authority: 'Posting' } });
  if (!__workerMessages.some(message => message.id === 'small' && message.type === 'signed') || __calls.length !== 1) throw Error('a request under the limit was refused');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=WORKER_SOURCE,
        source=worker_runtime_source(),
        extra_context_js=r"""
context.self = context;
context.__workerMessages = [];
context.__calls = [];
context.postMessage = message => context.__workerMessages.push(message);
context.__HAS = {
  setOptions() {}, traceOff() {}, authenticate() {},
  async broadcast(auth, authority, operations, onWait) { context.__calls.push(authority); onWait(); return { data: 'a'.repeat(40) }; },
};
context.__qrcode = () => ({ addData() {}, make() {}, createSvgTag() { return ''; } });
""",
    )


def test_bundle_carries_the_size_guard():
    bundle = (WORKER_SOURCE.parents[2] / "ceres/static/vendor/hiveauth-worker.js").read_text()
    assert '"too-large"' in bundle and "4e4" in bundle and "TextEncoder" in bundle
