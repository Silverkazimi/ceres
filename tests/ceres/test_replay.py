""" replay suite: the 12 failure shapes of brief-requirements.md, each cited to its history.md shape number.

Deterministic fakes only: a virtual clock (Date, setTimeout, setInterval advanced explicitly), a fake Keychain wallet,
a fake chain (inclusion 3 s after the wallet sends) and a fake game (indexes `lag` seconds after inclusion). No network,
no real wallet, no browser. Every scenario runs through the REAL broadcast gate and, for the end-to-end cases, the REAL
flow module (harvest modal, wallet swap card).

Code under test: ceres/static of this checkout, or the pre-fix tree when IR9545_STATIC points at it, e.g.
  git archive 15c6a6da5 ceres/static | tar -x -C <dir>, then run this file with IR9545_STATIC=<dir>/ceres/static set
  (through the project's light test runner).
Pass = the action reaches its true final state (confirmed / refused with the game's error / never sent) with no manual
step, within the scenario's virtual time. Success is a printed marker, never the exit code alone (a pending promise
lets node exit 0).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.ceres.vm_context import read_entry_source, run_in_module_context

STATIC = Path(os.environ["IR9545_STATIC"]) if os.environ.get("IR9545_STATIC") else Path(__file__).resolve().parents[2] / "ceres" / "static"
PRE_FIX = bool(os.environ.get("IR9545_STATIC"))

FAKE_WORLD = r"""
// virtual clock
const __vt = { now: Date.parse('2026-11-03T09:00:00.000Z'), seq: 0, timers: [], frozen: false };
const __RealDate = Date;
class __FakeDate extends __RealDate {
  constructor(...a) { super(...(a.length ? a : [__vt.now])); }
  static now() { return __vt.now; }
}
context.Date = __FakeDate;
context.setTimeout = (fn, ms = 0, ...args) => { const t = { id: ++__vt.seq, at: __vt.now + Math.max(0, Number(ms) || 0), fn, args }; __vt.timers.push(t); return t.id; };
context.clearTimeout = (id) => { __vt.timers = __vt.timers.filter((t) => t.id !== id); };
context.setInterval = (fn, ms = 0) => { const id = ++__vt.seq; const arm = () => __vt.timers.push({ id, at: __vt.now + Math.max(1, Number(ms) || 1), fn: () => { arm(); fn(); }, args: [] }); arm(); return id; };
context.clearInterval = context.clearTimeout;
context.queueMicrotask = queueMicrotask;
const __flush = async () => { for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r)); };
context.__advance = async (ms) => {
  const end = __vt.now + ms;
  for (;;) {
    await __flush();
    if (__vt.frozen) break;
    __vt.timers.sort((a, b) => a.at - b.at || a.id - b.id);
    const t = __vt.timers[0];
    if (!t || t.at > end) break;
    __vt.timers.shift(); __vt.now = Math.max(__vt.now, t.at);
    try { t.fn(...t.args); } catch (_) {}
  }
  __vt.now = Math.max(__vt.now, end); await __flush();
};
// A hidden tab: time passes, its timers do not run (browsers throttle them), then the page is shown again.
context.__hide = async (ms) => { __vt.now += ms; };
context.__now = () => __vt.now;
context.__fire = (type) => { for (const fn of (__ceresListeners[type] || [])) { try { fn({ type, persisted: false }); } catch (_) {} } };
context.navigator.locks = { request(_name, options, callback) { return Promise.resolve((typeof options === 'function' ? options : callback)({ name: _name })); } };
context.crypto = require('node:crypto').webcrypto;
context.AbortSignal = { timeout: () => undefined };

// fake chain and game
const W = context.__world = { scenario: {}, sent: null, txId: 'ab'.repeat(20), fetches: [], walletCalls: 0, answer: null };
const S = () => W.scenario;
const included = () => W.sent != null && __vt.now >= W.sent + 3000;
const indexed = () => included() && __vt.now >= W.sent + 3000 + (S().lag || 0) * 1000;
const down = () => S().downFrom != null && __vt.now >= S().downFrom && __vt.now < S().downFrom + S().downFor;
W.reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => JSON.parse(JSON.stringify(body)) });
W.gameFetch = null;   // flow endpoints, set per flow
context.fetch = context.window.fetch = async (url, init = {}) => {
  const path = String(url).split('?')[0];
  W.fetches.push(path);
  if (down()) return W.reply({ detail: 'restarting' }, 502);
  const body = init.body ? JSON.parse(init.body) : {};
  if (path === '/api/confirm/find') {
    // The own-history server (fix round 1): the contract of ceres/routes_confirm.py -- six reads at 0/2/4/10/60/480 s
    // from the first ask, then ONE read at expiry (3,900 s) that is final: found, or absent. The pre-round client's
    // `expiry_check` read gets the pre-round answer (not_found, covered) so the same world runs both versions.
    // Fix round 2: one record per ACTION (the exact operations; aletheia-r2 MEDIUM-1), absence only for a request the
    // wallet CLOSED whose begin the server heard first (astra-r2 P0-1); otherwise the expiry read says "open".
    const key = JSON.stringify(body.operations);
    W.ledger ||= new Map();
    let rec = W.ledger.get(key); if (!rec) { rec = { first: __vt.now, reads: 0, expiryReads: 0, begun: false, beginValid: false }; W.ledger.set(key, rec); }
    if (body.closed === true && rec.closedAt == null) rec.closedAt = __vt.now;
    // Fix round 3 (N1): a begin is a pre-sign bound only when the client presents the token it waited for.
    if (body.begin_token && rec.tokens?.[body.attempt] === body.begin_token) rec.beginValid = true;
    // A client from before round 2 sends no `closed` field: the old server took `answered` as enough (the unsafe rule
    // the round-1 tree had), so the same world reproduces the old behaviour for an old client.
    if (!('closed' in body) && body.answered === true && rec.closedAt == null) { rec.closedAt = __vt.now; rec.beginValid = true; }
    // Fix round 4 (aletheia-r4 1/2): per attempt, the server's own first record and whether any answer was recorded
    // `unanswered_for_s` is the server-clock age of an attempt with no recorded answer (the IN-0888 lifetime clock).
    rec.att ||= {};
    const att = rec.att[body.attempt] ||= { first: __vt.now, answered: false };
    if (body.answered === true || body.closed === true) att.answered = true;
    const silent = att.answered ? {} : { unanswered_for_s: (__vt.now - att.first) / 1000 };
    const reply = W.reply;
    W.reply = (payload, status) => reply({ ...payload, ...silent }, status);
    try {
    const seen = W.findSees ? W.findSees(body) : included();
    if (body.expiry_check) { rec.expiryReads += 1; return W.reply(seen ? { status: 'found', tx_id: W.txId } : { status: 'not_found', covered: true }); }
    // Fix round 4 (: the reference swap's offsets, then every 15 s until the expiry read (routes_confirm.normal_offset)
    // a re-sign (a new attempt's begin) opens a fresh window (W.windowStart per action).
    const offset = index => index < 10 ? [0, 2000, 4000, 8000, 12000, 18000, 26000, 36000, 50000, 70000][index] : 70000 + 15000 * (index - 9);
    const start = rec.windowStart ?? rec.first;
    if (offset(rec.reads) <= 3900000 && __vt.now - start <= 3900000) {
      const due = start + offset(rec.reads);
      if (__vt.now + 500 < due) return W.reply({ status: 'wait', next_in_s: (due - __vt.now) / 1000 });
      let index = rec.reads + 1;
      while (start + offset(index) <= __vt.now) index += 1;
      rec.reads = index;
      if (seen && !W.hideFromWindow) return W.reply({ status: 'found', tx_id: W.txId });
      const next = offset(rec.reads) <= 3900000 ? start + offset(rec.reads) : rec.first + 3900000;
      return W.reply({ status: 'not_found', covered: true, next_in_s: Math.max(0, next - __vt.now) / 1000 });
    }
    const expiryAt = (rec.closedAt ?? rec.first) + 3900000;
    if (__vt.now < expiryAt) return W.reply({ status: 'wait', next_in_s: (expiryAt - __vt.now) / 1000 });
    if (rec.retryAt && __vt.now < rec.retryAt) return W.reply({ status: 'unproven', reason: 'open', next_in_s: (rec.retryAt - __vt.now) / 1000 });
    rec.expiryReads += 1;
    if (seen) return W.reply({ status: 'found', tx_id: W.txId });
    if (rec.closedAt != null && rec.beginValid) return W.reply({ status: 'absent', covered: true });
    rec.retryAt = __vt.now + 900000;
    return W.reply({ status: 'unproven', reason: 'open', next_in_s: 900 });
    } finally { W.reply = reply; }
  }
  if (path === '/api/confirm/intent') {
    // Fix round 3 (N1): the begin is RECORDED before the wallet opens and answered with a begin token (first contact of
    // that attempt only); `W.beginDelay` (ms) delays the answer, as a slow server connection would.
    W.ledger ||= new Map();
    const key = JSON.stringify(body.operations);
    let rec = W.ledger.get(key);
    const first = !rec || !(rec.attempts?.has(body.attempt));
    if (!rec) { rec = { first: __vt.now, reads: 0, expiryReads: 0, begun: false, beginValid: false }; W.ledger.set(key, rec); }
    (rec.attempts ||= new Set()).add(body.attempt);
    let token;
    if (body.event === 'begin' && first) { W.tokenSeq = (W.tokenSeq || 0) + 1; token = W.tokenSeq.toString(16).padStart(32, '0'); (rec.tokens ||= {})[body.attempt] = token; rec.begun = true;
      if (rec.attempts.size > 1) { rec.windowStart = __vt.now; rec.reads = 0; rec.retryAt = null; }   // round 4: a re-sign's fresh window
      (rec.att ||= {})[body.attempt] ||= { first: __vt.now, answered: false }; }
    if (body.closed === true || body.event === 'answered') { rec.att ||= {}; (rec.att[body.attempt] ||= { first: __vt.now, answered: false }).answered = true; }
    if (body.begin_token && rec.tokens?.[body.attempt] === body.begin_token) rec.beginValid = true;
    if (body.closed === true && rec.closedAt == null) rec.closedAt = __vt.now;
    if (W.beginDelay && body.event === 'begin') await new Promise(resolve => context.setTimeout(resolve, W.beginDelay));
    return W.reply({ ok: true, server_now: __vt.now, ...(token ? { begin_token: token } : {}) });
  }
  if (path === '/api/op-inclusion') {
    // Fix round 2 (LOW-2): the server ages a known id by its OWN first sight of it, never the browser's created_at.
    W.txSeen ||= new Map();
    if (!W.txSeen.has(body.tx_id)) W.txSeen.set(body.tx_id, __vt.now);
    if (included()) return W.reply({ included: true, block_num: 100 });
    const maintainer = [...(W.ledger || new Map()).values()].find(rec => rec.attempts?.has(body.intent));
    const closedAt = maintainer?.closedAt;
    const anchor = Math.min(W.txSeen.get(body.tx_id), closedAt ?? Infinity);
    // Fix round 3 (N3): "never sent" for a known id needs the attempt's confirmed pre-sign begin and Hive's
    // expired-irreversible status (the fake chain: never sent and past the expiration bound).
    const beginOk = Boolean(body.begin_token) && maintainer?.tokens?.[body.intent] === body.begin_token;
    if (W.sent == null && beginOk && __vt.now - anchor >= 3900000) return W.reply({ included: false, block_num: null });
    return W.reply({ included: null, block_num: null });
  }
  if (path === '/api/op-inclusion-legacy') {
    if (included()) return W.reply({ included: true, block_num: 100 });
    const age = __vt.now - Date.parse(body.created_at || '');
    return W.reply({ included: W.sent == null && age >= 3900000 ? false : null, block_num: null });
  }
  if (path === '/api/confirm/verdict') {
    return W.reply({ tx_id: body.tx_id, operations: body.operations.map((op, index) => indexed()
      ? (S().refuse === index ? { index, state: 'refused', error: 'Game refused op ' + index } : { index, state: 'confirmed', result: { ok: true } })
      : { index, state: 'pending' }) });
  }
  if (W.gameFetch) { const answer = await W.gameFetch(path, body); if (answer) return answer; }
  return W.reply({ detail: 'not served in this replay: ' + path }, 404);
};
// The closure beacon (fix round 2). Fix round 3: a beacon never records a begin (it is not awaited, N1).
context.Blob = class { constructor(parts) { this.__body = parts.join(''); } };
context.navigator.sendBeacon = (url, payload) => {
  const body = JSON.parse(typeof payload === 'string' ? payload : payload.__body);
  W.beacons = (W.beacons || 0) + 1;
  W.ledger ||= new Map();
  const key = JSON.stringify(body.operations);
  let rec = W.ledger.get(key);
  if (!rec) { rec = { first: __vt.now, reads: 0, expiryReads: 0, begun: false, beginValid: false }; W.ledger.set(key, rec); }
  (rec.attempts ||= new Set()).add(body.attempt);
  if (body.begin_token && rec.tokens?.[body.attempt] === body.begin_token) rec.beginValid = true;
  if (body.closed === true && rec.closedAt == null) rec.closedAt = __vt.now;
  if (body.closed === true || body.answered === true) { rec.att ||= {}; (rec.att[body.attempt] ||= { first: __vt.now, answered: false }).answered = true; }
  return true;
};
// fake Keychain (direct broadcast branch)
context.window.hive_keychain = {
  requestBroadcast(_account, _ops, _key, callback) {
    const setTimeout = context.setTimeout;   // the virtual clock, not Node's real timers
    W.walletCalls += 1;
    const s = S();
    const signedAt = __vt.now;
    const send = () => { if (!s.neverSent) W.sent = signedAt; };
    if (s.wallet === 'declined') { setTimeout(() => callback({ success: false, error: 'user_cancel', message: 'Request was canceled by the user.' }), 1000); return; }
    send();
    if (s.wallet === 'expired') { setTimeout(() => callback({ success: false, ambiguousBroadcast: true, stillChecking: true, error: 'expired' }), 60000); return; }
    if (s.wallet === 'dropped') { setTimeout(() => callback({ success: false, ambiguousBroadcast: true, stillChecking: true, error: 'watchdog' }), 110000); return; }
    if (s.wallet === 'no-id') { setTimeout(() => callback({ success: true, result: {} , data: { key: 'Posting' } }), 2000); return; }
    // Keychain answered, but with an error that says nothing about the broadcast: the request is CLOSED, its outcome unknown.
    if (s.wallet === 'lost') { setTimeout(() => callback({ success: false, error: 'The connection to the node was lost.' }), s.ackMs ?? 3000); return; }
    if (s.wallet === 'silent') { if (s.lateAnswerAt) setTimeout(() => callback({ success: true, result: { id: W.txId }, data: { key: 'Posting' } }), s.lateAnswerAt); return; }
    setTimeout(() => callback({ success: true, result: { id: W.txId }, data: { key: 'Posting' } }), s.ackMs ?? 3000);
  },
};
"""

# The 12 shapes (brief-requirements.md "Replay tests") with their history.md shape numbers and scenario knobs.
SHAPES = {
    "two_op_minus_1": ("8, 9", {"twoOps": True, "lag": 10}),
    "large_result": ("19, 28", {"largeResult": True, "lag": 5}),
    "lost_ack_after_approval": ("12, 16", {"wallet": "expired", "lag": 10}),
    "websocket_drop": ("18", {"wallet": "dropped", "lag": 10}),
    "wallet_returns_no_tx_id": ("12", {"wallet": "no-id", "lag": 10}),
    "late_broadcast_after_timeout": ("12, 22", {"wallet": "silent", "lateAnswerAt": 150000, "lag": 10}),
    "game_lagging_hive": ("11", {"lag": 240}),
    "one_failed_lookup_then_recovery": ("4, 26", {"lag": 10, "failVerifyOnce": True}),
    "service_restart_mid_wait": ("26", {"lag": 10, "downFor": 200000}),
    "hidden_tab_pageshow": ("17, 30", {"lag": 10, "hideFor": 900000}),
    "refused_vs_pending": ("6, 13", {"lag": 10, "refuse": 0}),
    "never_sent": ("12, 13", {"neverSent": True, "lag": 0}),
}
EXPECTED = {"refused_vs_pending": "refused", "never_sent": "never-sent"}
LIMIT_MS = {"never_sent": 4200000}   # Hive lifetime plus grace (3900 s) is the earliest proof of "never sent"


def _harvest_source():
    entry = STATIC / "harvest-modal.js"
    source = read_entry_source(entry)
    return entry, source.rsplit("}", 1)[0] + """
Object.defineProperties(globalThis, {
  M: {get: () => M},
  render: {get: () => render, set: value => {render = value;}},
  postJson: {get: () => postJson, set: value => {postJson = value;}}
});
}
"""


HARVEST = r"""
(async () => {
const s = SCENARIO; __world.scenario = s;
const op = (n, region) => ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ op: 'harvest_all', region_uid: region, n }) }];
const members = s.twoOps ? [{ nonce: 'aaaaaaaaa1', deed_uids: [], op: 'harvest_all', region_uid: 'PR-LOP-1' }, { nonce: 'aaaaaaaaa2', deed_uids: [], op: 'harvest_all', region_uid: 'PR-LOP-2' }]
  : [{ nonce: 'aaaaaaaaa1', deed_uids: [], op: 'harvest_all', region_uid: 'PR-LOP-1' }];
const chunk = { operations: members.map((m) => op(m.nonce, m.region_uid)), members };
const pv = { signing_enabled: true, chunks: [chunk], preview_id: 'preview', preview_digest: 'digest' };
Object.assign(M, { open: true, openId: 1, gen: 1, player: 'alice', mode: 'plot', scopeKind: 'deeds', preview: pv, run: null, reviewing: null, deedUids: [], routingRunId: null });
CURRENT_PLAYER = 'alice'; BOARD = { player: 'alice', plots: [] };
walletReady = () => true; walletKind = () => 'keychain';
render = () => {};
prepareSigningBatch = async () => null;
if (s.downFor) __world.scenario.downFrom = __now() + 8000;   // the service restarts after the signature
let verifies = 0; const attempts = {};
const big = s.largeResult ? 'x'.repeat(60000) : '';
postJson = async (url, body) => {
  const sc = __world.scenario;
  if (sc.downFrom != null && __now() >= sc.downFrom && __now() < sc.downFrom + sc.downFor) throw Error('HTTP 502');
  if (url.endsWith('/preflight')) return { outcome: 'PERMIT', chunk };
  if (url.endsWith('/prepare')) return {};
  if (url.endsWith('/attempt')) { if (body.tx_id) attempts[body.attempt_id] = body.tx_id; return {}; }
  if (url.endsWith('/verify')) {
    verifies += 1;
    if (s.failVerifyOnce && verifies === 1) throw Error('HTTP 503');
    const known = attempts[body.attempt_id];
    const indexed = known && __world.sent != null && __now() >= __world.sent + 3000 + (s.lag || 0) * 1000;
    // Fix round 1 (HIGH-2): the REAL server's answers (routes_harvest._verify_harvest_attempt over a fake game holding
    // only the base records `<tx>` and `<tx>-1`), computed by the test in Python, before and after the game indexed.
    if (s.serverAnswers) return JSON.parse(JSON.stringify(known && indexed ? s.serverAnswers.final : { ...s.serverAnswers.pending, tx_id: known || null }));
    const out = members.map((m, index) => !indexed ? { nonce: m.nonce, outcome: 'pending', game_state: 'pending' }
      : s.refuse === index ? { nonce: m.nonce, outcome: 'failed', game_state: 'refused', error: 'Game refused op ' + index }
      : { nonce: m.nonce, outcome: 'success', game_state: 'accepted', evidence: [{ id: known + (index ? '-' + index : '') }], received_by_token: { GRAIN: 5 }, padding: big });
    return { tx_id: known || null, members: out, outcome: indexed ? (out.every((m) => m.outcome === 'success') ? 'success' : 'failed') : 'pending' };
  }
  throw Error('unexpected request ' + url);
};
globalThis.crypto = { randomUUID: () => 'attempt0000000000000000001' };
let finished = false;
confirmHarvest().then(() => { finished = true; }, () => { finished = true; });
const limit = LIMIT;
if (s.hideFor) { await __advance(5000); await __hide(s.hideFor); __fire('pageshow'); __fire('visibilitychange'); await __advance(2000); }
const final = () => {
  const phase = M.run?.phase;
  if (phase === 'success') return 'confirmed';
  if (phase === 'never_sent') return 'never-sent';
  if (phase === 'failed') return M.run.members.some((m) => m.outcome === 'failed' && m.error) ? 'refused' : M.run.members.every((m) => m.outcome === 'not_sent') ? 'never-sent' : 'failed';
  return null;
};
for (let step = 0; step < limit / STEP && !final(); step++) await __advance(STEP);
const state = final() || ('stuck:' + (M.run?.phase || 'none'));
console.log('IR9545-RESULT ' + JSON.stringify({ state, at: __now() - Date.parse('2026-11-03T09:00:00.000Z'), wallet: __world.walletCalls, message: M.run?.message || '' }));
process.exit(0);
})().catch((error) => { console.log('IR9545-RESULT ' + JSON.stringify({ state: 'error:' + (error && error.message) })); process.exit(0); });
"""


def _run(body, entry, source=None):
    result = run_in_module_context(body, entry=entry, extra_context_js=FAKE_WORLD, check=False, **({"source": source} if source else {}))
    line = next((l for l in result.stdout.splitlines() if l.startswith("IR9545-RESULT ")), None)
    assert line, (result.stdout[-1500:], result.stderr[-3000:])
    return json.loads(line.split(" ", 1)[1])


@pytest.mark.parametrize("shape", list(SHAPES), ids=lambda s: f"{s}[history {SHAPES[s][0]}]")
def test_e2e_harvest(shape):
    """Harvest modal (the IN-0705 flow), real gate, fake wallet/chain/game."""
    _, knobs = SHAPES[shape]
    entry, source = _harvest_source()
    body = HARVEST.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", str(LIMIT_MS.get(shape, 600000))).replace("STEP", "5000")
    outcome = _run(body, entry, source)
    expected = EXPECTED.get(shape, "confirmed")
    assert outcome["state"] == expected, outcome
    assert outcome.get("wallet", 1) == 1, f"signed more than once: {outcome}"


SWAP_SOURCE_PATCH = ("|| !walletReady(ctx.player)) return;", "|| false) return;")

SWAP = r"""
(async () => {
const s = SCENARIO; __world.scenario = s;
const symbol = 'WOOD';
const st = swapState('alice', symbol);
Object.assign(st, { direction: 'sell', amount: '10', quoted: '10', region: 'PR-AAA-1',
  quote: { kind: 'swap_sell', input: 10, output: 5, minimum: 4.9, fee_percent: '1', pool_cost: 0.1 } });
const wallet = { account: { DEC: '100' }, swap: { symbols: ['WOOD'], directions: ['swap_sell', 'swap_buy'], signing_enabled: true, rates: {}, fees: {} },
  regions: [{ region_uid: 'PR-AAA-1', balances: { WOOD: '50' } }] };
const target = { closest: () => ({ dataset: { lpSymbol: 'WOOD' } }) };
const ctx = { player: 'alice', account: () => 'alice', drawer: () => null, wallet: () => wallet, render: () => {}, setSigning: () => {}, reload: async () => {} };
const op = ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ op: 'swap_sell', n: 'ab'.repeat(8) }) }];
if (s.downFor) __world.scenario.downFrom = __now() + 8000;   // the service restarts after the signature
let verifies = 0;
const big = s.largeResult ? 'x'.repeat(60000) : '';
__world.gameFetch = async (path, body) => {
  if (path === '/api/wallet/lp/preview') return __world.reply({ operations: [op], nonce: 'ab'.repeat(8), payload: { n: 'ab'.repeat(8) }, quote: { input: 10, output: 5 } });
  if (path === '/api/wallet/lp/find') return __world.reply(__world.sent != null && __now() >= __world.sent + 3000 ? { status: 'found', tx_id: __world.txId } : { status: 'not_found', covered: true });
  if (path === '/api/wallet/lp/verify') {
    verifies += 1;
    if (s.failVerifyOnce && verifies === 1) return __world.reply({ detail: 'down' }, 503);
    const indexed = __world.sent != null && __now() >= __world.sent + 3000 + (s.lag || 0) * 1000;
    if (!indexed) return __world.reply({ status: 'not_found' });
    if (s.refuse === 0) return __world.reply({ status: 'failed', message: 'Game refused op 0' });
    return __world.reply({ status: 'confirmed', game_result: { dec_amount: '5', padding: big } });
  }
  return null;
};
let refused = false;
swapAction(ctx, 'swap', target).catch(() => {});
if (s.hideFor) { await __advance(5000); await __hide(s.hideFor); __fire('pageshow'); __fire('visibilitychange'); await __advance(2000); }
const final = () => st.stage === 'done' ? 'confirmed' : st.stage === 'refused' ? 'refused'
  : st.stage === 'edit' && /expired|not sent|Nothing was spent/i.test(st.lpNote || '') ? 'never-sent'
  : st.stage === 'edit' && __world.walletCalls === 1 && verifies > 0 && s.refuse === 0 ? 'refused' : null;
for (let step = 0; step < LIMIT / STEP && !final(); step++) await __advance(STEP);
console.log('IR9545-RESULT ' + JSON.stringify({ state: final() || ('stuck:' + st.stage + (st.failed ? ':' + st.failed : '') + (st.stopped ? ':stopped' : '')), at: __now() - Date.parse('2026-11-03T09:00:00.000Z'), wallet: __world.walletCalls, stage: st.stage, refusal: st.refusal || '', receipt: Boolean(st.receipt) }));
process.exit(0);
})().catch((error) => { console.log('IR9545-RESULT ' + JSON.stringify({ state: 'error:' + (error && error.message) })); process.exit(0); });
"""

SWAP_SHAPES = [s for s in SHAPES if s != "two_op_minus_1"]   # a swap is one operation


@pytest.mark.parametrize("shape", SWAP_SHAPES, ids=lambda s: f"{s}[history {SHAPES[s][0]}]")
def test_e2e_swap(shape):
    """Wallet swap card (the maintainer's reference flow, IN-0708), real gate, fake wallet/chain/game."""
    _, knobs = SHAPES[shape]
    entry = STATIC / "wallet-lp.js"
    source = read_entry_source(entry).replace(*SWAP_SOURCE_PATCH)
    body = SWAP.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", str(LIMIT_MS.get(shape, 600000))).replace("STEP", "5000")
    outcome = _run(body, entry, source)
    assert outcome["state"] == EXPECTED.get(shape, "confirmed"), outcome
    assert outcome.get("wallet", 1) == 1, f"signed more than once: {outcome}"


GATE = r"""
(async () => {
const s = SCENARIO; __world.scenario = s;
const ops = (s.twoOps ? [1, 2] : [1]).map((i) => ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ op: 'harvest_all', n: 'gate00000' + i }) }]);
if (s.downFor) __world.scenario.downFrom = __now() + 8000;   // the service restarts after the signature
let gate = null;
const confirmer = typeof confirmationState === 'function';
let final = null;
// A flow calls confirmAction the moment the gate settles (as every migrated flow does).
const begin = () => { if (confirmer && !globalThis.__started) { globalThis.__started = true; confirmAction({ account: 'alice', operations: ops }).then((a) => { final = a.state; }); } };
BroadcastGate.broadcast('alice', ops).then((id) => { gate = { id }; begin(); }, (error) => { gate = { error }; begin(); });
if (s.hideFor) { await __advance(5000); await __hide(s.hideFor); __fire('pageshow'); __fire('visibilitychange'); await __advance(2000); }
for (let step = 0; step < LIMIT / STEP && !final; step++) {
  await __advance(STEP);
  if (!confirmer) {
    // Pre-fix: no shared confirmer exists; the gate's own answer is all a flow gets.
    if (gate?.id) final = 'id-only';
    else if (gate?.error) {
      const late = gate.error.pendingBroadcast ? await Promise.race([gate.error.pendingBroadcast.then((v) => v?.txId), Promise.resolve(null)]) : null;
      final = late ? 'id-only' : gate.error.broadcasted === false ? 'never-sent' : 'no-id';
    }
    continue;
  }
}
console.log('IR9545-RESULT ' + JSON.stringify({ state: final || 'stuck', at: __now() - Date.parse('2026-11-03T09:00:00.000Z'), wallet: __world.walletCalls }));
process.exit(0);
})().catch((error) => { console.log('IR9545-RESULT ' + JSON.stringify({ state: 'error:' + (error && error.message) })); process.exit(0); });
"""


@pytest.mark.parametrize("shape", list(SHAPES), ids=lambda s: f"{s}[history {SHAPES[s][0]}]")
def test_gate_through_shared_confirmer(shape):
    """CONFIRMER CONTRACT, not a flow replay (fix round 1, Aletheia HIGH-2): the harness itself calls confirmAction after
    the real gate, so this case proves the gate + confirmer contract only. It does NOT count as coverage of any flow;
    coverage.md lists the real-adapter replays per flow separately."""
    _, knobs = SHAPES[shape]
    if knobs.get("failVerifyOnce"):
        knobs = {**knobs, "downFor": 3000}   # the generic verdict read fails once (a 3 s outage after signing), then recovers
    body = GATE.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", str(LIMIT_MS.get(shape, 600000))).replace("STEP", "5000")
    outcome = _run(body, STATIC / "broadcast-gate.js")
    assert outcome["state"] == EXPECTED.get(shape, "confirmed"), outcome
    assert outcome.get("wallet", 1) == 1, f"signed more than once: {outcome}"


# Continuation 4: regional DEC and checkout end to end (brief: "checkout and regional DEC end to end")
def _exposed(entry_name, names, patches=()):
    """The entry module's source with `names` readable and assignable from the body (as _harvest_source does)."""
    entry = STATIC / entry_name
    source = read_entry_source(entry)
    for old, new in patches:
        source = source.replace(old, new)
    # Names the harness already publishes (exported or unique) are assignable accessors; only the others are added here.
    props = "\n".join(f"if (!Object.prototype.hasOwnProperty.call(globalThis, {json.dumps(n)})) Object.defineProperty(globalThis, {json.dumps(n)}, "
                      f"{{ configurable: true, get: () => {n}, set: value => {{ {n} = value; }} }});" for n in names)
    return entry, source.rsplit("}", 1)[0] + "\n" + props + "\n}\n"


DEC = r"""
(async () => {
const s = SCENARIO; __world.scenario = s;
CURRENT_PLAYER = 'alice'; BOARD = { player: 'alice', plots: [] };
render = () => {}; walletReady = () => true; walletKind = () => 'keychain';
const op = ['custom_json', { id: 'sm_stake_tokens', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ token: 'DEC', qty: 1, region_uid: '1-PNW-1', n: 'cd'.repeat(8) }) }];
const fresh = { nonce: 'cd'.repeat(8), operations: [op], signing_enabled: true, balance: 100, region: { staked: 0, required: 10 }, shortfall_after: 0 };
D.player = 'alice'; D.tab = 'DEC';
drawer = { open: false, querySelector: (sel) => sel === '#dec-amount' ? { value: '1', focus() {} } : null, querySelectorAll: () => [] };
D.data = { player: 'alice', balance: 100, regions: [{ region_uid: '1-PNW-1', staked: 0, required: 10, shortfall: 10, surplus: 0 }] };
D.review = { region_uid: '1-PNW-1', action: 'stake', amount: '1', preview: fresh, previewAmount: '1', lastPreviewedAmount: '1' };
if (s.downFor) __world.scenario.downFrom = __now() + 8000;   // the service restarts after the signature
let verifies = 0;
const big = s.largeResult ? 'x'.repeat(60000) : '';
__world.gameFetch = async (path, body) => {
  if (path === '/api/dec/preview') return __world.reply(fresh);
  if (path === '/api/dec/verify') {
    verifies += 1;
    if (s.failVerifyOnce && verifies === 1) return __world.reply({ detail: 'down' }, 503);
    const indexed = body.tx_id === __world.txId && __world.sent != null && __now() >= __world.sent + 3000 + (s.lag || 0) * 1000;
    if (!indexed) return __world.reply({ status: 'not_found' });
    if (s.refuse === 0) return __world.reply({ status: 'failed', message: 'Game refused op 0' });
    return __world.reply({ status: 'confirmed', tx_id: body.tx_id, harvest: null, padding: big });
  }
  if (path === '/api/dec/find') return __world.reply(__world.sent != null && __now() >= __world.sent + 3000 ? { status: 'found', tx_id: __world.txId } : { status: 'not_found', covered: true });
  return __world.reply({ player: 'alice', balance: 100, regions: D.data.regions });
};
act('sign').catch((error) => { console.log('DEC-ACT-ERROR ' + (error && error.stack)); });
if (s.hideFor) { await __advance(5000); await __hide(s.hideFor); __fire('pageshow'); __fire('visibilitychange'); await __advance(2000); }
const final = () => { const p = D.pending; return !p ? null : p.status === 'confirmed' ? 'confirmed' : p.status === 'failed' ? 'refused' : p.status === 'not_sent' ? 'never-sent' : null; };
for (let step = 0; step < LIMIT / STEP && !final(); step++) await __advance(STEP);
console.log('IR9545-RESULT ' + JSON.stringify({ state: final() || ('stuck:' + (D.pending?.status || 'none') + (D.error ? ':' + D.error : '') + ' fetches=' + __world.fetches.slice(0, 8).join(',')), at: __now() - Date.parse('2026-11-03T09:00:00.000Z'), wallet: __world.walletCalls }));
process.exit(0);
})().catch((error) => { console.log('IR9545-RESULT ' + JSON.stringify({ state: 'error:' + (error && error.message) })); process.exit(0); });
"""
DEC_PATCHES = (("!walletReady(D.player)", "false"),)
DEC_SHAPES = [s for s in SHAPES if s != "two_op_minus_1"]   # one stake operation


@pytest.mark.parametrize("shape", DEC_SHAPES, ids=lambda s: f"{s}[history {SHAPES[s][0]}]")
def test_e2e_regional_dec(shape):
    """Regional DEC stake (wallet drawer), real gate and real regional-dec.js, fake wallet/chain/game."""
    _, knobs = SHAPES[shape]
    entry, source = _exposed("regional-dec.js", ["render", "D", "drawer"], DEC_PATCHES)
    body = DEC.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", str(LIMIT_MS.get(shape, 600000))).replace("STEP", "5000")
    outcome = _run(body, entry, source)
    assert outcome["state"] == EXPECTED.get(shape, "confirmed"), outcome
    assert outcome.get("wallet", 1) == 1, f"signed more than once: {outcome}"


CHECKOUT = r"""
(async () => {
const s = SCENARIO; __world.scenario = s;
CURRENT_PLAYER = 'alice'; BOARD = { player: 'alice', plots: [] };
render = () => {}; walletReady = () => true; walletKind = () => 'keychain';
walletAccount = () => 'alice';
const nonce = 'ef'.repeat(8);
const op = ['custom_json', { id: 'sm_market_purchase', required_auths: ['alice'], required_posting_auths: [], json: JSON.stringify({ currency: 'DEC', items: ['ab'.repeat(20) + '-0'], price: 1, app: 'ceres/1.0', n: nonce }) }];   // the reviewed shape the gate allows for Active
co.player = 'alice'; co.stage = 'confirm'; co.currency = 'DEC'; co.items = [{ lkey: 'L-1', name: 'Card' }];
co.quote = { quote_id: 'q1', currencies: { DEC: { total: '1.000', enough: true } }, signing: { enabled: true }, items: [{ lkey: 'L-1', price_usd: 1 }], dec_price: 1 };
if (s.downFor) __world.scenario.downFrom = __now() + 8000;   // the service restarts after the signature
let verifies = 0;
const big = s.largeResult ? 'x'.repeat(60000) : '';
__world.gameFetch = async (path, body) => {
  if (path === '/api/market/checkout/preview') return __world.reply({ transactions: [{ nonce, payload: { n: nonce }, operations: [op], lkeys: ['L-1'], price: '1.000', currency: 'DEC' }] });
  if (path === '/api/market/checkout/find') return __world.reply(__world.sent != null && __now() >= __world.sent + 3000 ? { status: 'found', tx_id: __world.txId } : { status: 'not_found', covered: true });
  if (path === '/api/market/checkout/verify') {
    verifies += 1;
    if (s.failVerifyOnce && verifies === 1) return __world.reply({ detail: 'down' }, 503);
    const indexed = body.tx_id === __world.txId && __world.sent != null && __now() >= __world.sent + 3000 + (s.lag || 0) * 1000;
    if (!indexed) return __world.reply({ status: 'not_found' });
    if (s.refuse === 0) return __world.reply({ status: 'failed', message: 'Game refused op 0' });
    return __world.reply({ status: 'confirmed', items: [{ lkey: 'L-1', outcome: 'bought' }], padding: big });
  }
  return null;
};
sign().catch((error) => { console.log('CO-SIGN-ERROR ' + (error && error.stack)); });
if (s.hideFor) { await __advance(5000); await __hide(s.hideFor); __fire('pageshow'); __fire('visibilitychange'); await __advance(2000); }
const tx = () => co.run?.txs?.[0];
const final = () => { const t = tx(); return !t ? null : t.state === 'confirmed' ? 'confirmed' : t.state === 'failed' ? 'refused' : t.state === 'not_sent' ? 'never-sent' : null; };
for (let step = 0; step < LIMIT / STEP && !final(); step++) await __advance(STEP);
console.log('IR9545-RESULT ' + JSON.stringify({ state: final() || ('stuck:' + (tx()?.state || co.stage) + (co.note ? ':' + co.note.text : '') + ' failure=' + (globalThis.__failure || '') + ' fetches=' + __world.fetches.slice(0, 8).join(',')), at: __now() - Date.parse('2026-11-03T09:00:00.000Z'), wallet: __world.walletCalls }));
process.exit(0);
})().catch((error) => { console.log('IR9545-RESULT ' + JSON.stringify({ state: 'error:' + (error && error.message) })); process.exit(0); });
"""


@pytest.mark.parametrize("shape", DEC_SHAPES, ids=lambda s: f"{s}[history {SHAPES[s][0]}]")
def test_e2e_checkout(shape):
    """Market checkout (one purchase transaction, Active), real gate and real card-checkout.js, fake wallet/chain/game."""
    _, knobs = SHAPES[shape]
    entry, source = _exposed("card-checkout.js", ["render", "walletAccount", "co"],
                             (("catch (error) { failure = error;", "catch (error) { failure = error; globalThis.__failure = String(error && (error.stack || error.message)).slice(0, 300);"),))
    body = CHECKOUT.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", str(LIMIT_MS.get(shape, 600000))).replace("STEP", "5000")
    outcome = _run(body, entry, source)
    assert outcome["state"] == EXPECTED.get(shape, "confirmed"), outcome
    assert outcome.get("wallet", 1) == 1, f"signed more than once: {outcome}"


# Continuation 4: the one own-history read at Hive expiry (CONFIRM_EXPIRY_RECHECK, manager Q1 option 1)
EXPIRY = r"""
(async () => {
const s = SCENARIO; __world.scenario = s;
const ops = [['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ op: 'harvest_all', n: 'expiry0001' }) }]];
const finds = [];
// Own history: within the first ten minutes it never shows the action; the one expiry read decides.
__world.gameFetch = null;
const realFetch = fetch;
const shim = async (url, init = {}) => {
  const path = String(url).split('?')[0];
  if (path === '/api/confirm/find') {
    const body = JSON.parse(init.body || '{}');
    const at = __now() - Date.parse('2026-11-03T09:00:00.000Z');
    __world.findSees = () => !!s.foundAtExpiry && at >= 3900000;   // never seen in the ten-minute window
    // A READ is counted, not a "wait" answer that only tells the client when to come back (fix round 2: the expiry
    // anchor is the closing answer, a few seconds after the request, so a client ask at 3,900 s can still be told to wait).
    const response = await realFetch(url, init);
    const answer = await response.json();
    if (answer?.status !== 'wait') finds.push({ at, expiry: body.expiry_check === true || at >= 3900000 });
    else if (at <= 660000) finds.push({ at, expiry: false });
    return __world.reply(answer, response.status);
  }
  return realFetch(url, init);
};
globalThis.fetch = shim; if (globalThis.window) window.fetch = shim;
let final = null;
const begin = () => { if (!globalThis.__started) { globalThis.__started = true; confirmAction({ account: 'alice', operations: ops }).then((a) => { final = a.state; }); } };
BroadcastGate.broadcast('alice', ops).then(begin, begin);
for (let step = 0; step < LIMIT / STEP && !final; step++) await __advance(STEP);
const expiryReads = finds.filter((f) => f.expiry);
console.log('IR9545-RESULT ' + JSON.stringify({ state: final || 'stuck', at: __now() - Date.parse('2026-11-03T09:00:00.000Z'), wallet: __world.walletCalls,
  finds: finds.length, expiryReads: expiryReads.length, firstExpiryAt: expiryReads[0]?.at ?? null, windowReads: finds.filter((f) => !f.expiry && f.at > 660000).length }));
process.exit(0);
})().catch((error) => { console.log('IR9545-RESULT ' + JSON.stringify({ state: 'error:' + (error && error.message) })); process.exit(0); });
"""


@pytest.mark.parametrize("wallet,found_at_expiry,expected", [("lost", False, "never-sent"), ("silent", True, "confirmed")],
                         ids=["absent_at_expiry", "found_at_expiry"])
def test_no_id_not_found_in_ten_minutes_is_decided_by_the_one_expiry_read(wallet, found_at_expiry, expected):
    """ addendum / manager Q1 option 1: no id; the ten-minute finder does not see the action; ONE more own-history
    read at Hive expiry decides: found -> its id is attached and the game confirms it; absent -> never sent. Fix round 2
    (astra-r2 P0-1): "absent" needs a wallet answer that CLOSED the request ("lost": Keychain answered with an error that
    says nothing about the broadcast); a silent wallet is covered by the next test. No read between the two windows."""
    knobs = {"wallet": wallet, "lag": 10, "neverSent": not found_at_expiry, "foundAtExpiry": found_at_expiry}
    body = EXPIRY.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", "4200000").replace("STEP", "5000")
    outcome = _run(body, STATIC / "broadcast-gate.js")
    assert outcome["state"] == expected, outcome
    assert outcome["wallet"] == 1, outcome
    assert outcome["expiryReads"] == 1, outcome                     # exactly one extra read (the server's own, at its expiry)
    assert outcome["firstExpiryAt"] is not None and outcome["firstExpiryAt"] >= 3900000, outcome   # at Hive expiry, not before
    # Fix round 4 (astra-r4 : between ten minutes and the expiry the finder asks at the reference swap's 15 s pace
    # never faster (before round 4: no ask at all, slower than swap).
    assert 0 < outcome["windowReads"] <= (3900 - 660) // 15 + 2, outcome


def test_round2_p0_1_a_silent_wallet_is_never_declared_never_sent():
    """Astra r2 P0-1 / , as amended by the maintainer's IN-0888 ("End it once the request expires", fix round 3): the
    wallet never answers. Within the transaction's longest lifetime the action stays unresolved (never "never sent");
    once that lifetime has passed with no wallet answer at all, it ends "Not sent: your wallet never asked you." -- never
    earlier. The server never proves absence for it (no closed request)."""
    knobs = {"wallet": "silent", "lag": 10, "neverSent": True}
    early = _run(EXPIRY.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", "3600000").replace("STEP", "60000"), STATIC / "broadcast-gate.js")
    assert early["state"] == "stuck", early                          # still unresolved inside the lifetime
    body = EXPIRY.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", "10800000").replace("STEP", "60000")
    outcome = _run(body, STATIC / "broadcast-gate.js")
    assert outcome["state"] == "never-sent" and outcome["at"] >= 3900000, outcome
    assert outcome["wallet"] == 1, outcome


# Fix round 1 (Aletheia HIGH-2): real flow adapters for the flows that had only the harness "gate" case
CLAIM = r"""
(async () => {
const s = SCENARIO; __world.scenario = s;
const op = ['custom_json', { id: 'sm_claim_rewards', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ type: 'all', n: 'cl'.repeat(8) }) }];
const fresh = { nonce: 'cl'.repeat(8), total: '5.000', signing_enabled: true, operations: [op] };
const D = { player: 'alice', generation: 0, error: '', signing: false, busy: false, claim: { busy: false, review: { total: '5.000' }, pending: null, checking: false } };
if (s.downFor) __world.scenario.downFrom = __now() + 8000;
let verifies = 0;
const big = s.largeResult ? 'x'.repeat(60000) : '';
__world.gameFetch = async (path, body) => {
  if (path === '/api/wallet/claim/preview') return __world.reply(fresh);
  if (path === '/api/wallet/claim/verify') {
    verifies += 1;
    if (s.failVerifyOnce && verifies === 1) return __world.reply({ detail: 'down' }, 503);
    const indexed = body.tx_id === __world.txId && __world.sent != null && __now() >= __world.sent + 3000 + (s.lag || 0) * 1000;
    if (!indexed) return __world.reply({ status: 'not_found' });
    if (s.refuse === 0) return __world.reply({ status: 'failed', message: 'Game refused op 0' });
    return __world.reply({ status: 'confirmed', total: '5.000', claimed: [], padding: big });
  }
  return null;
};
claimAction(D, 'sign', () => {}, () => 'alice', async () => {}).catch(() => {});
if (s.hideFor) { await __advance(5000); await __hide(s.hideFor); __fire('pageshow'); __fire('visibilitychange'); await __advance(2000); }
const final = () => { const p = D.claim.pending; return !p ? null : p.status === 'confirmed' ? 'confirmed'
  : p.phase === 'failed' && /not sent/i.test(p.message || '') ? 'never-sent' : p.phase === 'failed' ? 'refused' : null; };
for (let step = 0; step < LIMIT / STEP && !final(); step++) await __advance(STEP);
console.log('IR9545-RESULT ' + JSON.stringify({ state: final() || ('stuck:' + JSON.stringify(D.claim.pending || null).slice(0, 200)), at: __now() - Date.parse('2026-11-03T09:00:00.000Z'), wallet: __world.walletCalls }));
process.exit(0);
})().catch((error) => { console.log('IR9545-RESULT ' + JSON.stringify({ state: 'error:' + (error && error.message) })); process.exit(0); });
"""


@pytest.mark.parametrize("shape", DEC_SHAPES, ids=lambda s: f"{s}[history {SHAPES[s][0]}]")
def test_e2e_sps_claim(shape):
    """SPS claim (wallet SPS tab), real gate and real wallet-claim.js `claimAction('sign')`, fake wallet/chain/game."""
    _, knobs = SHAPES[shape]
    entry, source = _exposed("wallet-claim.js", [], (("!walletReady(D.player)", "false"),))
    body = CLAIM.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", str(LIMIT_MS.get(shape, 600000))).replace("STEP", "5000")
    outcome = _run(body, entry, source)
    assert outcome["state"] == EXPECTED.get(shape, "confirmed"), outcome
    assert outcome.get("wallet", 1) == 1, f"signed more than once: {outcome}"


COMBINE = r"""
(async () => {
const s = SCENARIO; __world.scenario = s;
const op = ['custom_json', { id: 'sm_combine_cards', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ cards: ['C1', 'C2'], n: 'comb000001' }) }];
CURRENT_PLAYER = 'alice'; BOARD = { player: 'alice', plots: [] };
const payload = { buildable: true, account: 'alice', uids: ['C1', 'C2'], hazard: { destroyed_uids: [] } };
COMBINE_PREVIEW = { payload };
COMBINE_SELECTION.clear(); COMBINE_SELECTION.set('C1', {}); COMBINE_SELECTION.set('C2', {});
COMBINE_DIALOG = { name: 'Card', copies: [] };
rebuildCombineForSigning = async () => ({ preview: payload, operation: op });
combineReviewMatches = () => true; renderCombineDialog = () => {}; combineLedgerData = () => null;
if (s.downFor) __world.scenario.downFrom = __now() + 8000;
let verifies = 0;
const big = s.largeResult ? 'x'.repeat(60000) : '';
__world.gameFetch = async (path, body) => {
  if (path === '/api/op-verify') {
    verifies += 1;
    if (s.failVerifyOnce && verifies === 1) return __world.reply({ detail: 'down' }, 503);
    const indexed = body.tx_id === __world.txId && __world.sent != null && __now() >= __world.sent + 3000 + (s.lag || 0) * 1000;
    if (!indexed) return __world.reply({ state: 'chain_absent', chain: { evidence: { reason_kind: 'not_found' } } });
    if (s.refuse === 0) return __world.reply({ state: 'game-refused', evidence: { reason: 'Game refused op 0' }, padding: big });
    return __world.reply({ state: 'domain-confirmed', combine_result: {}, padding: big });
  }
  return null;
};
confirmCombineBroadcast().catch(() => {});
if (s.hideFor) { await __advance(5000); await __hide(s.hideFor); __fire('pageshow'); __fire('visibilitychange'); await __advance(2000); }
// The dialog's own end states: the operation receipt it publishes (confirmed / refused by the game) or "not sent".
const receipt = () => [...OPERATION_RECEIPTS.values()].filter((r) => r.family === 'combine').pop();
const final = () => receipt()?.phase === 'confirmed' ? 'confirmed'
  : receipt()?.lastState === 'game-refused' ? 'refused'
  : COMBINE_PREVIEW?.notSent || (receipt()?.phase === 'failed' && receipt()?.lastState === 'chain_absent') ? 'never-sent' : null;
for (let step = 0; step < LIMIT / STEP && !final(); step++) await __advance(STEP);
console.log('IR9545-RESULT ' + JSON.stringify({ state: final() || ('stuck:' + JSON.stringify({ phase: receipt()?.phase || null, last: receipt()?.lastState || null, stalled: !!COMBINE_PREVIEW?.broadcastStalled, step: globalThis['COMBINE_SIGN_S' + 'TEP']?.step || null, e: String(COMBINE_PREVIEW?.error || '').slice(0, 120) })), at: __now() - Date.parse('2026-11-03T09:00:00.000Z'), wallet: __world.walletCalls }));
process.exit(0);
})().catch((error) => { console.log('IR9545-RESULT ' + JSON.stringify({ state: 'error:' + (error && error.message) })); process.exit(0); });
"""


@pytest.mark.parametrize("shape", DEC_SHAPES, ids=lambda s: f"{s}[history {SHAPES[s][0]}]")
def test_e2e_combine_dialog(shape):
    """Combine dialog (Combine button), real combine-dialog.js `confirmCombineBroadcast` -> beginSharedOperation -> real
    gate -> shared confirmer -> real verifyCombineBroadcast (`/api/op-verify`), fake wallet/chain/game."""
    _, knobs = SHAPES[shape]
    entry, source = _exposed("combine-dialog.js", ["COMBINE_PREVIEW", "COMBINE_SELECTION", "COMBINE_DIALOG", "COMBINE_ARRIVAL", "COMBINE_VERIFY",
                                                   "COMBINE_SIGN_STEP", "rebuildCombineForSigning", "combineReviewMatches", "renderCombineDialog", "combineLedgerData"])
    body = COMBINE.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", str(LIMIT_MS.get(shape, 600000))).replace("STEP", "5000")
    outcome = _run(body, entry, source)
    assert outcome["state"] == EXPECTED.get(shape, "confirmed"), outcome
    assert outcome.get("wallet", 1) == 1, f"signed more than once: {outcome}"



def _real_two_op_server_answers(tmp_path, monkeypatch, refuse_second):
    """The REAL /api/harvest/verify server code for a two-op harvest transaction, against a fake game that holds ONLY the
    game's base records -- `<tx>` for op 0 and `<tx>-1` for op 1, as /transactions/lookup serves them (no evidence is
    handed to the client by the fake). Returns the server's answer before and after the game indexed both records."""
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlsplit
    from ceres import game_read, harvest_evidence, routes_harvest as routes
    root = Path(__file__).resolve().parents[2]
    if not Path("work/notes/evidence-game-result.json").exists():
        pytest.skip("private work/ evidence file not included in this export")
    fixture = json.loads((root / "work/notes/evidence-game-result.json").read_text())["trx_info"]
    tx = "ab" * 20   # the fake world's transaction id
    members = [{"op": "harvest_all", "region_uid": "PR-BRL-41", "nonce": f"aaaaaaaaa{i + 1}", "deed_uids": []} for i in range(2)]
    preview = {"preview_id": "two-op", "preview_digest": "two-op", "chunks": [{"operations": [], "members": members}]}
    record = harvest_evidence.begin_attempt(tmp_path, "alice", preview, 0, "twoop00000000000000000001")
    harvest_evidence.update_attempt(tmp_path, "alice", record["attempt_id"], tx_id=tx, state="submitted")
    records: dict = {}

    def game(host, path, timeout=None):
        if path.startswith("/transactions/lookup"):
            trx = parse_qs(urlsplit(path).query)["trx_id"][0]
            return {"trx_info": records[trx]} if trx in records else {"error": f"Transaction [{trx}] not found"}
        return {"data": []}
    monkeypatch.setattr(game_read, "_get", game)
    monkeypatch.setattr(routes, "record_verification", lambda **k: None, raising=False)
    monkeypatch.setattr(routes, "_paced", lambda fn, *a: fn(*a))
    monkeypatch.setattr(routes, "_client", SimpleNamespace(resource_history=lambda _tx: {"status": "success", "data": []},
                                                           reward_actions=lambda deed, limit: {"data": []}))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(root=tmp_path))))
    pending = routes._verify_harvest_attempt(request, "alice", {"attempt_id": record["attempt_id"]})
    for index, member in enumerate(members):
        lookup_id = tx if index == 0 else f"{tx}-{index}"
        data = json.dumps({"app": "ceres-fides/0.1.0", "auto_buy_grain": False, "deed_uids": [], "n": member["nonce"],
                           "op": "harvest_all", "region_uid": member["region_uid"], "resource_symbol": "ALL"})
        records[lookup_id] = {**fixture, "id": lookup_id, "player": "alice", "data": data}
        if refuse_second and index == 1:
            records[lookup_id].update(success=False, error="Game refused op 1", result=None)
    final = routes._verify_harvest_attempt(request, "alice", {"attempt_id": record["attempt_id"]})
    return pending, final


@pytest.mark.parametrize("refuse_second", [False, True], ids=["both_accepted", "op1_refused"])
def test_e2e_harvest_two_op_minus_1_through_the_real_server_verdict(tmp_path, monkeypatch, refuse_second):
    """History shapes 8, 9 (Aletheia HIGH-2): the result of op 1 lives only under `<tx>-1`. The real server verdict code
    reads the base records; the real harvest adapter turns its answer into the run's result and per-op states."""
    pending, final = _real_two_op_server_answers(tmp_path, monkeypatch, refuse_second)
    assert [m["outcome"] for m in pending["members"]] == ["pending", "pending"], pending
    assert [m["tx_id"] for m in final["members"]] == ["ab" * 20, "ab" * 20 + "-1"], final
    assert [m["outcome"] for m in final["members"]] == (["success", "failed"] if refuse_second else ["success", "success"]), final
    entry, source = _harvest_source()
    knobs = {"twoOps": True, "lag": 10, "serverAnswers": {"pending": pending, "final": final}}
    outcome = _run(HARVEST.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", "600000").replace("STEP", "5000"), entry, source)
    assert outcome["state"] == ("refused" if refuse_second else "confirmed"), outcome
    assert outcome["wallet"] == 1, outcome
