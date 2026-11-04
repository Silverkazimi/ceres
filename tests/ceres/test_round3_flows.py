""" fix round 3, client behaviour (astra-review-r3.md N1, N3, N4; aletheia-verdict-r3.md L3).

Each test runs the REAL module(s) -- broadcast gate, shared confirmer and the named flow module -- in the replay suite's
deterministic fake world (virtual clock, fake Keychain/chain/game, fake server following the round-3 contract: a begin is
recorded and answered with a token BEFORE the wallet opens). No network, no browser. Each failed on the round-2 tree
(snapshot before-r3) unless marked as a control.
"""
from __future__ import annotations

import json

from tests.ceres.test_replay import HARVEST, STATIC, _harvest_source, _run
from tests.ceres.test_round1_flows import OPS, SETTLE, run, run_source
from tests.ceres.vm_context import read_entry_source, run_in_module_context


# N1: the wallet opens only after the server recorded the begin (bounded)
def test_n1_the_wallet_opens_only_after_the_server_recorded_the_begin_and_its_token_is_presented():
    """Astra r3 N1: the begin must reach the server BEFORE the wallet is called. The begin answer arrives 800 ms later;
    the wallet opens after it, and every later finder read presents the begin token. Before: the wallet opened first,
    the begin went out afterwards as an unawaited beacon, no token existed."""
    out = run(f"""
const ops = {OPS('ordr', 1)};
const order = [];
__world.beginDelay = 800;
__world.route = async (path, body) => {{ if (path === '/api/confirm/intent' && body.event === 'begin') order.push('begin-asked'); if (path === '/api/confirm/find') order.push(body.begin_token ? 'find+token' : 'find'); return null; }};
const keychain = window.hive_keychain.requestBroadcast;
window.hive_keychain.requestBroadcast = (...args) => {{ order.push('wallet@' + (__now() - start)); return keychain(...args); }};
const start = __now();
__world.scenario = {{ wallet: 'no-id', lag: 0 }};
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(6000);
__mark({{ order, token: Boolean(confirmationState('alice', ops)?.beginToken) }});
""")
    assert out["order"][0] == "begin-asked" and out["order"][1].startswith("wallet@"), out
    assert 800 <= int(out["order"][1].split("@")[1]) < 2500, out
    assert out["token"] and "find+token" in out["order"] and "find" not in out["order"], out


def test_n1_a_begin_answer_after_the_bound_is_ignored_and_absence_stays_unprovable():
    """The server's answer takes longer than BEGIN_BOUND_MS: the wallet opens at the bound, the late token is never
    kept (the wallet may already have signed before the server recorded anything), so the finder presents none. The
    action whose wallet then closes without an id and was never sent stays pending for good. Before: the beacon's
    receipt time became the oldest edge and the action was declared never sent."""
    out = run(f"""
const ops = {OPS('late', 1)};
let walletAt = null;
__world.beginDelay = 10000;
const keychain = window.hive_keychain.requestBroadcast;
window.hive_keychain.requestBroadcast = (...args) => {{ walletAt = __now() - start; return keychain(...args); }};
const tokens = [];
__world.route = async (path, body) => {{ if (path === '/api/confirm/find') tokens.push(body.begin_token || null); return null; }};
const start = __now();
__world.scenario = {{ wallet: 'lost', neverSent: true }};
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(3 * 3600 * 1000);
const row = confirmationState('alice', ops);
__mark({{ walletAt, token: row?.beginToken || null, state: row?.state, presented: tokens.filter(Boolean).length, finds: tokens.length }});
""")
    assert out["walletAt"] is not None and 2500 <= out["walletAt"] < 3500, out
    assert out["token"] is None and out["presented"] == 0 and out["finds"] >= 1, out
    assert out["state"] == "pending", out


def test_n1_control_a_begin_recorded_in_time_still_proves_a_closed_unsent_request():
    """Control: the same never-sent request with the begin recorded in time is proven never sent at its expiry."""
    out = run(f"""
const ops = {OPS('ctrl', 1)};
__world.scenario = {{ wallet: 'lost', neverSent: true }};
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(2 * 3600 * 1000);
__mark({{ state: confirmationState('alice', ops)?.state }});
""")
    assert out["state"] == "never-sent", out


# N3: once Hive showed the id included, a later "absent" never makes it never-sent
def test_n3_a_known_id_once_reported_included_is_never_turned_never_sent():
    """Astra r3 N3 (client half): the inclusion probe once answered included=true (the game is still behind); a later
    probe (another node) answers included=false. The action stays pending. Before: the later 'false' made it never-sent
    and released its claim."""
    out = run(f"""
const ops = {OPS('incl', 1)};
let probes = 0;
__world.route = async (path) => {{
  if (path === '/api/op-inclusion') {{ probes += 1; return __world.reply({{ included: probes === 1 ? true : false, block_num: probes === 1 ? 5 : null }}); }}
  if (path === '/api/confirm/verdict') return __world.reply({{ detail: 'game down' }}, 503);
  return null;
}};
trackSigning('alice', ops);
markAnswered('alice', ops, {{ closed: true }});
let answer = null;
confirmAction({{ account: 'alice', operations: ops, txId: 'ce'.repeat(20) }}).then(a => {{ answer = a; }});
await __advance(3 * 3600 * 1000);
__mark({{ probes, answer: answer?.state || null, row: confirmationState('alice', ops)?.state }});
""")
    assert out["probes"] >= 2 and out["answer"] is None and out["row"] == "pending", out


# N4: settlement / checkout / harvest attach reads carry the scheduler's signal
def test_n4_a_hung_pool_settlement_read_is_aborted_and_the_next_read_runs():
    """Astra r3 N4: the first SPS pool settlement read hangs. After the read bound the scheduler aborts it; `busy` is
    cleared at once and the following reads really read. Before: the hung fetch kept busy=true for ever, every later
    check returned pending without reading."""
    from tests.ceres.test_round1_flows import _swap_source
    entry, source = _swap_source()
    body = SETTLE.replace("SCENARIO", json.dumps({"sent": True})).replace("LIMIT", "200000")
    body = body.replace("""__world.gameFetch = async (path, body) => {""", """let settleCalls = 0, aborted = 0;
__world.route = async (path, body, init) => {
  if (path !== '/api/wallet/lp/settle') return null;
  settleCalls += 1;
  if (settleCalls === 1) return new Promise((_, reject) => init?.signal?.addEventListener('abort', () => { aborted += 1; reject(Error('aborted')); }));
  return __world.reply({ status: 'settling', next_cursor: 9000 });
};
__world.gameFetch = async (path, body) => {""")
    body = body.replace("__mark({ stage: st.stage,", "__mark({ settleCalls, aborted, busy: Boolean(st.busy), stage: st.stage,")
    out = run_source(body, entry, source)
    assert out["aborted"] == 1 and out["settleCalls"] >= 3 and not out["busy"], out


def test_n4_the_checkout_verify_read_carries_the_schedulers_signal():
    """Astra r3 N4: checkout's verify read is aborted with the scheduler (its late answer was already ignored; now
    the request itself is cancelled). Before: post() sent no signal."""
    out = _checkout_signal()
    assert out["signals"] >= 1 and out["aborted"] >= 1, out


def _checkout_signal():
    from tests.ceres.test_replay import CHECKOUT, _exposed
    entry, source = _exposed("card-checkout.js", ["render", "walletAccount", "co"], ())
    body = CHECKOUT.replace("SCENARIO", json.dumps({"lag": 0})).replace("LIMIT", "200000").replace("STEP", "1000")
    hook = """
let __verifies = 0, __signals = 0, __aborted = 0;
const __checkoutFetch = globalThis.fetch;
globalThis.fetch = window.fetch = async (url, init = {}) => {
  if (String(url).startsWith('/api/market/checkout/verify')) {
    __verifies += 1;
    if (init.signal) __signals += 1;
    if (__verifies === 1) return new Promise((_, reject) => init.signal?.addEventListener('abort', () => { __aborted += 1; reject(Error('aborted')); }));
  }
  return __checkoutFetch(url, init);
};
"""
    body = body.replace("(async () => {", "(async () => {" + hook, 1)
    body = body.replace("console.log('IR9545-RESULT ' + JSON.stringify({", "console.log('IR9545-RESULT ' + JSON.stringify({ signals: __signals, aborted: __aborted, verifies: __verifies,", 1)
    return _run(body, entry, source)


def test_n4_the_harvest_attach_read_carries_the_schedulers_signal():
    """Astra r3 N4: the harvest's attach request (`/api/harvest/attempt` with the id) runs under the scheduler's signal;
    a hung attach is aborted and the harvest still confirms. Before: the attach ignored the signal (left hanging)."""
    entry, source = _harvest_source()
    body = HARVEST.replace("SCENARIO", json.dumps({"lag": 0})).replace("LIMIT", "300000").replace("STEP", "1000")
    body = body.replace("postJson = async (url, body) => {", """let __attachSignals = 0, __attachAborted = 0, __attaches = 0;
postJson = async (url, body, options = {}) => {
  if (url.endsWith('/attempt') && body.tx_id) {
    __attaches += 1;
    // The attaches made right at the wallet's answer fail here, so the confirm read's own attach must do it.
    if (!options.signal) throw Error('HTTP 503');
    __attachSignals += 1;
    if (__attachSignals === 1) return new Promise((_, reject) => options.signal.addEventListener('abort', () => { __attachAborted += 1; reject(Error('aborted')); }));
  }""", 1)
    body = body.replace("wallet: __world.walletCalls,", "wallet: __world.walletCalls, attachSignals: __attachSignals, attachAborted: __attachAborted,", 1)
    out = _run(body, entry, source)
    assert out["state"] == "confirmed" and out["attachSignals"] >= 1 and out["attachAborted"] == 1, out


# L3: the first load after the upgrade does not replay old results
HASH_ID = r"""
function __hashId(text) {
  let a = 0x811c9dc5, b = 0x9e3779b1;
  for (let i = 0; i < text.length; i += 1) { const c = text.charCodeAt(i); a = Math.imul(a ^ c, 16777619); b = Math.imul(b ^ c, 2246822519); }
  return (a >>> 0).toString(16).padStart(8, '0') + (b >>> 0).toString(16).padStart(8, '0');
}
const __oldRow = { id: JSON.stringify(['alice', [['custom_json', { id: 'sm_claim_rewards' }]]]), account: 'alice', attempt: 1, attemptId: 'a1a1a1a1a1a1a1a1',
  operations: [['custom_json', { id: 'sm_claim_rewards' }]], orphan: true, state: 'confirmed', txId: 'ab'.repeat(20), rev: 3,
  since: new Date(Date.now() - 3 * 3600000).toISOString(), updatedAt: new Date(Date.now() - 2 * 3600000).toISOString(), perOp: [{ index: 0, state: 'confirmed' }] };
__ceresLocalStorage.setItem('ceres:confirm:v2-index', JSON.stringify([__oldRow.id]));
__ceresLocalStorage.setItem('ceres:confirm:v2:' + __hashId(__oldRow.id), JSON.stringify(__oldRow));
"""


def test_l3_the_first_load_after_the_upgrade_shows_no_old_results():
    """Aletheia r3 L3: a browser with finished orphan rows from before (no shown-list yet) loads the new module. No old
    notice appears; they are remembered as shown. A result that becomes final from now on is still shown (control).
    Before: up to three old notices on the first load."""
    entry = STATIC / "confirm-notices.js"
    result = run_in_module_context(r"""
const before = confirmNoticeLines.length;
const shown = JSON.parse(localStorage.getItem('ceres:confirm-notices:shown') || 'null');
const fresh = confirmNoticeFor({ id: 'fresh', attempt: 1, orphan: true, state: 'confirmed', updatedAt: new Date().toISOString(),
  operations: [['custom_json', { id: 'sm_claim_rewards' }]] });
console.log('IR9545-RESULT ' + JSON.stringify({ before, shown, fresh: Boolean(fresh) }));
""", entry=entry, check=False, extra_context_js=HASH_ID)
    line = next((l for l in result.stdout.splitlines() if l.startswith("IR9545-RESULT ")), None)
    assert line, (result.stdout[-1500:], result.stderr[-3000:])
    out = json.loads(line.split(" ", 1)[1])
    assert out["before"] == 0 and out["shown"] and len(out["shown"]) == 1 and out["fresh"], out


# M1 / maintainer IN-0888 "End it once the request expires"
NEVER_ASKED = "Not sent: your wallet never asked you. Starting again is safe."
EXPIRED_HIVEAUTH = """
const keychain = window.hive_keychain.requestBroadcast;
window.hive_keychain.requestBroadcast = (account, operations, key, callback) => {
  if (__world.scenario.hiveAuthExpires) {
    __world.walletCalls += 1;
    if (__world.scenario.sentAnyway) __world.sent = __now();
    // HiveAuth's own request expiry, reported by the worker (reason 'expired'), as wallet.js passes it on.
    setTimeout(() => callback({ success: false, ambiguousBroadcast: true, stillChecking: true, walletExpired: true, error: 'expired' }), 60000);
    return;
  }
  return keychain(account, operations, key, callback);
};
"""


def test_m1_hiveauth_reports_its_request_expired_unsigned_the_action_ends_and_its_claim_is_released():
    """IN-0888: HiveAuth reports its own request expired unsigned. The action ends with the maintainer's exact text, its claim
    is released (the same operations may be signed again: the wallet is asked a second time), nothing is resent."""
    out = run(f"""
{EXPIRED_HIVEAUTH}
const ops = {OPS('hexp', 1)};
__world.scenario = {{ hiveAuthExpires: true, neverSent: true }};
let pendingOutcome = null;
BroadcastGate.broadcast('alice', ops).catch(e => {{ e?.pendingBroadcast?.catch(x => {{ pendingOutcome = x.message; }}); }});
await __advance(70000);
const row = confirmationState('alice', ops);
__world.scenario = {{ lag: 0 }};
let again = null;
BroadcastGate.broadcast('alice', ops).then(() => {{ again = 'sent'; }}, e => {{ again = e.duplicateBroadcast ? 'duplicate' : 'error:' + e.message; }});
await __advance(10000);
__mark({{ state: row?.state, error: row?.error, by: row?.neverSentBy, pendingOutcome, again, wallet: __world.walletCalls }});
""")
    assert out["state"] == "never-sent" and out["error"] == NEVER_ASKED and out["by"] == "wallet-expired", out
    assert out["pendingOutcome"] == NEVER_ASKED, out
    assert out["again"] == "sent" and out["wallet"] == 2, out


def test_m1_a_later_chain_hit_for_that_attempt_still_wins():
    """IN-0888 guard: HiveAuth said 'expired', but the app had signed and the transaction is on Hive. The ending is
    shown, the finder keeps looking for that attempt, and the server's expiry read finds it: the action reopens and is
    confirmed by the game."""
    out = run(f"""
{EXPIRED_HIVEAUTH}
const ops = {OPS('hhit', 1)};
__world.scenario = {{ hiveAuthExpires: true, sentAnyway: true, lag: 10 }};
__world.hideFromWindow = true;
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(70000);
const first = confirmationState('alice', ops)?.state;
await __advance(2 * 3600 * 1000);
const row = confirmationState('alice', ops);
__mark({{ first, state: row?.state, txId: row?.txId, wallet: __world.walletCalls }});
""")
    assert out["first"] == "never-sent", out
    assert out["txId"] == "ab" * 20 and out["state"] == "confirmed", out


def test_m1_a_wallet_that_never_answers_ends_only_after_the_transactions_lifetime():
    """IN-0888 for Keychain: no wallet answer at all. Pending (claim held) through the transaction's longest lifetime;
    after it, the maintainer's exact text and the claim released. The server never proved absence (no closed request)."""
    out = run(f"""
const ops = {OPS('kexp', 1)};
__world.scenario = {{ wallet: 'silent', neverSent: true }};
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(60 * 60 * 1000);
const inside = confirmationState('alice', ops)?.state;
await __advance(30 * 60 * 1000);
const row = confirmationState('alice', ops);
const claims = JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice') || '[]');
__mark({{ inside, state: row?.state, error: row?.error, claims: claims.length }});
""")
    assert out["inside"] == "pending", out
    assert out["state"] == "never-sent" and out["error"] == NEVER_ASKED and out["claims"] == 0, out



# M2 / maintainer IN-0889 "One warning line"
def test_m2_storage_full_shows_one_warning_line_until_a_durable_write_succeeds():
    """IN-0889: localStorage refuses the write, the row is kept in sessionStorage: the notice surface shows exactly one
    warning line with the maintainer's text; when storage accepts the row again the line goes. Nothing goes to the server."""
    entry = STATIC / "confirm-notices.js"
    result = run_in_module_context(r"""
(async () => {
const fetches = [];
globalThis.fetch = async (url) => { fetches.push(String(url)); return { ok: false, status: 404, json: async () => null }; };
const realSet = localStorage.setItem;
let full = true;
localStorage.setItem = (key, value) => { if (full && String(key).startsWith('ceres:confirm:v2')) throw Error('QuotaExceededError'); return realSet(key, value); };
const ops = [['custom_json', { id: 'sm_claim_rewards', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ n: 'aaaaaaaaaa' }) }]];
trackSigning('alice', ops);
const during = confirmNoticesHtml(confirmStorageNotice() ? [confirmStorageNotice()] : []);
const lines = confirmStorageNotice() ? 1 : 0;
full = false;
flushDirty();
const after = confirmStorageNotice();
console.log('IR9545-RESULT ' + JSON.stringify({ during, lines, after, session: Boolean(sessionStorage.getItem('ceres:confirm:v2:' + hashId(confirmationState('alice', ops).id)) === null), fetches }));
})();
""", entry=entry, check=False)
    line = next((l for l in result.stdout.splitlines() if l.startswith("IR9545-RESULT ")), None)
    assert line, (result.stdout[-1500:], result.stderr[-3000:])
    out = json.loads(line.split(" ", 1)[1])
    assert out["lines"] == 1 and "This browser&#39;s storage is full" not in out["during"], out
    assert "This browser's storage is full: keep this tab open until the action is confirmed" in out["during"], out
    assert out["after"] is None and out["fetches"] == [], out
