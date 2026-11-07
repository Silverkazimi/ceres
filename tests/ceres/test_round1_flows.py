""" fix round 1, client behaviour (astra-review.md F1-F10, aletheia-verdict.md HIGH-1, LOW-7).

Each test runs the REAL module(s) under test -- the broadcast gate, the shared confirmer and the flow adapter named in its
docstring -- in the replay suite's deterministic fake world (virtual clock, fake Keychain, fake chain and game, no
network, no browser). The fake own-history server emulates the server contract of ceres/routes_confirm.py (six reads at
0/2/4/10/60/480 s, then one read at expiry); the server itself is tested in test_round1_server.py. Every test
records a marker line; a pending promise never passes. Run against the pre-round snapshot to see "before"
(build-report.md "Fix round 1").
"""
from __future__ import annotations

import json

import pytest

from tests.ceres.test_replay import FAKE_WORLD, STATIC, _exposed, _harvest_source, _run
from tests.ceres.vm_context import read_entry_source

ROUND1_WORLD = FAKE_WORLD + r"""
// ---- round 1: per-test routes in front of the replay world (whose /api/confirm/find has the server's contract) ----
const W2 = context.__world;
W2.route = null; W2.ledger = new Map(); W2.findSees = null; W2.hideFromWindow = false;
context.__require = require;
context.__listeners = __ceresListeners;
const __baseFetch = context.fetch;
context.fetch = context.window.fetch = async (url, init = {}) => {
  const path = String(url).split('?')[0];
  const body = init.body ? JSON.parse(init.body) : {};
  if (W2.route) { const routed = await W2.route(path, body, init); if (routed) { W2.fetches.push(path); return routed; } }
  return __baseFetch(url, init);
};
const __mark = (payload) => { console.log('IR9545-RESULT ' + JSON.stringify(payload)); process.exit(0); };
context.__mark = __mark;
"""

GATE = STATIC / "broadcast-gate.js"
OPS = lambda tag, count=1: json.dumps([["custom_json", {"id": "sm_land_operation", "required_auths": [], "required_posting_auths": ["alice"],  # noqa: E731
                                                          "json": json.dumps({"op": "harvest_all", "n": f"{tag}{index:04d}"[:10]})}] for index in range(count)])


def run(body, entry=GATE, source=None):
    wrapped = "(async () => {\n" + body + "\n})().catch((error) => __mark({ state: 'error:' + (error && (error.stack || error.message)) }));"
    return _run(wrapped, entry, source) if source else _run_world(wrapped, entry)


def _run_world(body, entry):
    from tests.ceres.vm_context import run_in_module_context
    result = run_in_module_context(body, entry=entry, extra_context_js=ROUND1_WORLD, check=False)
    line = next((l for l in result.stdout.splitlines() if l.startswith("IR9545-RESULT ")), None)
    assert line, (result.stdout[-1500:], result.stderr[-3000:])
    return json.loads(line.split(" ", 1)[1])


def run_source(body, entry, source):
    from tests.ceres.vm_context import run_in_module_context
    wrapped = "(async () => {\n" + body + "\n})().catch((error) => __mark({ state: 'error:' + (error && (error.stack || error.message)) }));"
    result = run_in_module_context(wrapped, entry=entry, source=source, extra_context_js=ROUND1_WORLD, check=False)
    line = next((l for l in result.stdout.splitlines() if l.startswith("IR9545-RESULT ")), None)
    assert line, (result.stdout[-1500:], result.stderr[-3000:])
    return json.loads(line.split(" ", 1)[1])


# F3: attempt identity
def test_f3_a_retry_after_never_sent_is_a_fresh_attempt_that_reads_the_game():
    """Astra repro 2 / Keychain queue retry: the first attempt is declined (never sent); the same operations are signed
    again and broadcast. The retry must be read and confirmed. Before: confirmAction answered the old 'never-sent' with
    zero domain reads."""
    out = run(f"""
const ops = {OPS('retry')};
__world.scenario = {{ wallet: 'declined' }};
const declined = BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(3000);
await declined;
const first = confirmationState('alice', ops)?.state;
__world.scenario = {{ lag: 0 }};
const sent = BroadcastGate.broadcast('alice', ops);
await __advance(5000);
const {{ txId }} = await sent;
let reads = 0, final = null;
confirmAction({{ account: 'alice', operations: ops, txId, check: async () => {{ reads += 1; return {{ state: 'confirmed' }}; }} }}).then(a => {{ final = a.state; }});
await __advance(10000);
__mark({{ state: final, first, reads, wallet: __world.walletCalls }});
""")
    assert out["first"] == "never-sent", out
    assert out["state"] == "confirmed" and out["reads"] >= 1 and out["wallet"] == 2, out


def test_f3_f6_a_proven_id_beats_an_inferred_never_sent_written_later():
    """Monotonic transitions: an id proven for this attempt is never overwritten by a later inferred 'never sent' (two
    tabs, or a stale reader). Before: the later write won and confirmAction answered never-sent without reading."""
    out = run(f"""
const ops = {OPS('mono')};
trackSigning('alice', ops);
attachTransaction('alice', ops, 'cd'.repeat(20));
markConfirmation('alice', ops, 'never-sent', {{ by: 'history', error: 'stale' }});
let reads = 0, final = null;
confirmAction({{ account: 'alice', operations: ops, check: async () => {{ reads += 1; return {{ state: 'confirmed' }}; }} }}).then(a => {{ final = a.state; }});
await __advance(10000);
__mark({{ state: final, reads, row: confirmationState('alice', ops)?.state }});
""")
    assert out["state"] == "confirmed" and out["reads"] >= 1, out


def test_f3_a_late_wallet_answer_after_the_finder_never_regresses_the_harvest_receipt():
    """Astra 'Wallet id arrives after finder': the finder found the id and the harvest completed; the wallet's late
    answer with the same id arrives afterwards. The receipt must stay final. Before: phase went back to 'confirming'."""
    entry, source = _harvest_source()
    out = run_source(HARVEST_BODY.replace("SCENARIO", json.dumps({"wallet": "silent", "lateAnswerAt": 400000, "lag": 5}))
                     .replace("AFTER", "await __advance(450000);"), entry, source)
    assert out["phase"] == "success" and out["wallet"] == 1, out


# F2: admission and claim follow the proof
def test_f2_a_found_no_id_request_holding_the_whole_budget_frees_it_for_the_next_chunk():
    """Astra F2: a lost-ack request of five custom ops (the whole per-block budget) is found by the own-history finder.
    Its admission must learn the id, so the next chunk can be sent. Before: the finder attached the id first, acceptId
    skipped the admission, the next chunk waited 30 s and was refused ('earlier wallet requests')."""
    out = run(f"""
const first = {OPS('budA', 5)};
const second = {OPS('budB', 1)};
__world.scenario = {{ wallet: 'expired', lag: 0 }};
BroadcastGate.broadcast('alice', first).catch(() => {{}});
await __advance(70000);
__world.scenario = {{ lag: 0 }};
let result = null;
BroadcastGate.broadcast('alice', second).then(r => {{ result = 'sent'; }}, e => {{ result = 'refused:' + e.message; }});
await __advance(40000);
__mark({{ state: result, wallet: __world.walletCalls, firstTx: confirmationState('alice', first)?.txId || null }});
""")
    assert out["firstTx"] and out["state"] == "sent" and out["wallet"] == 2, out


def test_f2_a_proven_never_sent_request_releases_its_claim_and_budget():
    """F2: the wallet never answered and the request was never sent; the one expiry read proves it. The admission and
    the  claim are released, so the same request can be signed again. Before: the claim stayed for ever."""
    out = run(f"""
const ops = {OPS('nsA', 1)};
__world.scenario = {{ wallet: 'lost', neverSent: true }};
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(4200000);
__world.scenario = {{ lag: 0 }};
let again = null;
BroadcastGate.broadcast('alice', ops).then(() => {{ again = 'sent'; }}, e => {{ again = 'refused:' + e.message; }});
await __advance(10000);
__mark({{ state: again, wallet: __world.walletCalls, row: confirmationState('alice', ops)?.state }});
""")
    assert out["state"] == "sent" and out["wallet"] == 2, out


def test_low7_a_late_wallet_refusal_cannot_release_the_claim_while_the_proof_is_pending():
    """Aletheia LOW-7: the wallet was silent (the finder started at 120 s), then a late non-ambiguous refusal arrives.
    The claim must stay until the confirmer proves the outcome. Before: the late refusal released it and the same request
    could be signed a second time."""
    out = run(f"""
const ops = {OPS('lowS', 1)};
__world.scenario = {{ wallet: 'silent', lag: 0 }};
let late = null;
const keychain = window.hive_keychain.requestBroadcast;
window.hive_keychain.requestBroadcast = (a, o, k, cb) => {{ keychain(a, o, k, () => {{}}); late = cb; }};
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(130000);
late({{ success: false, error: 'user_cancel', message: 'Request was canceled by the user.' }});
await __advance(1000);
let again = null;
BroadcastGate.broadcast('alice', ops).then(() => {{ again = 'sent-twice'; }}, e => {{ again = e.duplicateBroadcast ? 'duplicate-refused' : 'refused:' + e.message; }});
await __advance(5000);
__mark({{ state: again, wallet: __world.walletCalls }});
""")
    assert out["state"] == "duplicate-refused" and out["wallet"] == 1, out


# HIGH-1: the proof reaches the combine dialog, plan combine and Changes
def test_high1_never_sent_settles_the_shared_operation_receipt():
    """Aletheia HIGH-1 (Changes chunks / beginSharedOperation): a no-id request that was never sent. The gate's
    pending broadcast must settle as 'never sent' (retryable) and the operation receipt must leave 'in flight'. Before:
    pendingBroadcast never settled; the receipt stayed 'in flight' with its button hidden for ever."""
    out = run(f"""
const ops = {OPS('opNS', 1)};
__world.scenario = {{ wallet: 'lost', neverSent: true }};
let error = null;
beginSharedOperation({{ account: 'alice', operations: ops, family: 'board', context: 'test' }}).catch(e => {{ error = e; }});
await __advance(125000);
let settled = 'open';
error?.pendingBroadcast?.then(() => {{ settled = 'resolved'; }}, e => {{ settled = e.neverSent && e.retryable ? 'never-sent' : 'rejected'; }});
await __advance(4100000);
const receipt = [...OPERATION_RECEIPTS.values()].pop();
__mark({{ state: settled, phase: receipt?.phase }});
""", entry=STATIC / "operations.js")
    assert out["state"] == "never-sent" and out["phase"] == "failed", out


COMBINE_DIALOG = r"""
const op = ['custom_json', { id: 'sm_combine_cards', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ cards: ['C1', 'C2'], n: 'comb000001' }) }];
const s = SCENARIO; __world.scenario = s;
CURRENT_PLAYER = 'alice';
const payload = { buildable: true, account: 'alice', uids: ['C1', 'C2'], hazard: { destroyed_uids: [] } };
COMBINE_PREVIEW = { payload };
COMBINE_SELECTION.clear(); COMBINE_SELECTION.set('C1', {}); COMBINE_SELECTION.set('C2', {});
COMBINE_DIALOG = { name: 'Card', copies: [] };
rebuildCombineForSigning = async () => ({ preview: payload, operation: op });
combineReviewMatches = () => true;
renderCombineDialog = () => {};
combineLedgerData = () => null;
const verified = [];
verifyCombineBroadcast = async (_op, txId) => { verified.push(txId); };
confirmCombineBroadcast().catch(() => {});
await __advance(LIMIT);
__mark({ stalled: !!COMBINE_PREVIEW?.broadcastStalled, notSent: !!COMBINE_PREVIEW?.notSent, verified, wallet: __world.walletCalls, error: String(COMBINE_PREVIEW?.error || '').slice(0, 300) });
"""


@pytest.mark.parametrize("case", ["found", "never_sent"])
def test_high1_the_combine_dialog_follows_the_confirmers_proof(case):
    """Aletheia HIGH-1 scenario: the wallet's answer is lost (Keychain silent / relay expired). Found -> the dialog goes on
    to the game's verdict for the found id; never sent -> the stall ends and Sign is offered again. Before: the dialog
    stayed 'stalled' with Sign disabled for ever in both cases."""
    scenario = {"wallet": "expired", "lag": 5} if case == "found" else {"wallet": "lost", "neverSent": True}  # round 2: a CLOSED request (P0-1)
    limit = 200000 if case == "found" else 4200000
    entry, source = _exposed("combine-dialog.js", ["COMBINE_PREVIEW", "COMBINE_SELECTION", "COMBINE_DIALOG", "rebuildCombineForSigning",
                                                   "combineReviewMatches", "renderCombineDialog", "combineLedgerData", "verifyCombineBroadcast"])
    out = run_source(COMBINE_DIALOG.replace("SCENARIO", json.dumps(scenario)).replace("LIMIT", str(limit)), entry, source)
    if case == "found":
        assert out["verified"] == ["ab" * 20] and not out["stalled"], out
    else:
        assert out["notSent"] and not out["stalled"] and out["verified"] == [], out
    assert out["wallet"] == 1, out


PLAN_COMBINE = r"""
const s = SCENARIO; __world.scenario = s;
CURRENT_PLAYER = 'alice';
const op = ['custom_json', { id: 'sm_combine_cards', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ cards: ['C1', 'C2'], n: 'plan000001' }) }];
const plans = { P1: { id: 'P1', state: 'ready', card: { name: 'Card' }, inputs: [], expected: {} } };
const key = 'ceres:stage-plan:v1:alice';
const persist = () => localStorage.setItem(key, JSON.stringify({ chains: plans }));
persist();
chainPlans = () => plans;
saveChain = (id, plan) => { plans[id] = plan; persist(); };
chainCanCombine = () => true; chainSignWitness = () => ({ w: 1 }); activeChainInputs = () => [];
withStagedAccountLock = async (_p, fn) => fn(true);
const build = { preview: { buildable: true, account: 'alice' }, operation: op };
buildPlanCombine = async () => build; combineReviewMatches = () => true; planBuildFacts = () => 'same';
completeChainAttempt = async (_p, id, nonce, txId) => { if (plans[id]?.combine?.nonce === nonce) { plans[id] = { ...plans[id], combine: { ...plans[id].combine, tx_id: txId } }; persist(); } };
const verified = [];
verifyPlanCombine = async (id, receipt) => { verified.push(receipt?.txId || null); };
renderCheckoutSurface = () => {};
const review = { id: 'P1', witness: JSON.stringify({ w: 1 }), build };
signPlanCombine(review).catch(() => {});
await __advance(LIMIT);
__mark({ state: plans.P1.state, problem: plans.P1.problem?.code || null, verified, wallet: __world.walletCalls });
"""


@pytest.mark.parametrize("case", ["found", "never_sent"])
def test_high1_the_plan_combine_follows_the_confirmers_proof(case):
    """Aletheia HIGH-1, plan combine (combine-chain-sign.js signPlanCombine, real gate and confirmer): found -> verified
    for the found id; never sent -> the plan is ready to combine again. Before: 'combine_uncertain' for ever."""
    scenario = {"wallet": "expired", "lag": 5} if case == "found" else {"wallet": "lost", "neverSent": True}  # round 2: a CLOSED request (P0-1)
    limit = 200000 if case == "found" else 4200000
    entry, source = _exposed("combine-chain-sign.js", ["chainPlans", "saveChain", "chainCanCombine", "chainSignWitness", "activeChainInputs",
                                                       "withStagedAccountLock", "buildPlanCombine", "combineReviewMatches", "planBuildFacts",
                                                       "completeChainAttempt", "verifyPlanCombine", "renderCheckoutSurface"])
    out = run_source(PLAN_COMBINE.replace("SCENARIO", json.dumps(scenario)).replace("LIMIT", str(limit)), entry, source)
    if case == "found":
        assert "ab" * 20 in out["verified"] and out["problem"] != "combine_uncertain", out
    else:
        assert out["state"] == "ready" and out["problem"] == "combine_refused" and out["verified"] == [], out
    assert out["wallet"] == 1, out


# F4: bounded requests, no dormancy
def test_f4_a_hung_finder_request_is_aborted_and_the_next_read_finds_the_action():
    """Astra F4: the first own-history request never answers. It must be bounded and aborted, and a later read must
    find the action. Before: the finder awaited the hung request for ever."""
    out = run(f"""
const ops = {OPS('hung', 1)};
let calls = 0;
__world.route = (path, body, init) => {{
  if (path !== '/api/confirm/find') return null;
  calls += 1;
  if (calls === 1) return new Promise((_, reject) => init.signal?.addEventListener?.('abort', () => reject(new Error('aborted'))));
  return __world.reply({{ status: 'found', tx_id: 'ef'.repeat(20) }});
}};
trackSigning('alice', ops);
findSignedTransaction('alice', ops).catch(() => {{}});
await __advance(120000);
__mark({{ state: confirmationState('alice', ops)?.txId ? 'found' : 'stuck', calls }});
""")
    assert out["state"] == "found" and out["calls"] >= 2, out


def test_f4_f7_known_id_recovery_continues_past_sixty_five_minutes():
    """Astra shape 11 / F4: the transaction is on Hive but the game lags 70 minutes. The known-id waiter must keep a slow
    schedule past Hive's lifetime and confirm when the game answers. Before: dormant at 65 min until a page event."""
    out = run(f"""
const ops = {OPS('lag70', 1)};
__world.sent = __now();
__world.route = (path, body) => path === '/api/confirm/verdict'
  ? __world.reply({{ tx_id: body.tx_id, operations: body.operations.map((_, index) => __now() >= __world.sent + 4200000 ? {{ index, state: 'confirmed' }} : {{ index, state: 'pending' }}) }})
  : null;
let final = null;
confirmAction({{ account: 'alice', operations: ops, txId: __world.txId }}).then(a => {{ final = a.state; }});
await __advance(4800000);
__mark({{ state: final }});
""")
    assert out["state"] == "confirmed", out


def test_f4_an_unavailable_final_chain_lookup_is_retried_not_treated_as_done():
    """Astra F4: no 'finalChainDone' after an unavailable lookup. A known id that was never included; the chain lookup is
    down around Hive's expiry and back later. Must end 'never sent' once the chain answers. Before: the unavailable final
    probe set finalChainDone and the waiter went dormant."""
    out = run(f"""
const ops = {OPS('chain', 1)};
const start = __now();
__world.route = (path, body) => {{
  if (path === '/api/op-inclusion') {{
    if (__now() < start + 4500000) return __world.reply({{ detail: 'down' }}, 503);
    return __world.reply({{ included: false, block_num: null }});
  }}
  if (path === '/api/confirm/verdict') return __world.reply({{ tx_id: body.tx_id, operations: body.operations.map((_, index) => ({{ index, state: 'pending' }})) }});
  return null;
}};
let final = null;
confirmAction({{ account: 'alice', operations: ops, txId: 'aa'.repeat(20) }}).then(a => {{ final = a.state; }});
await __advance(5400000);
__mark({{ state: final }});
""")
    assert out["state"] == "never-sent", out


# F5: per-op verdicts
HARVEST_BODY = r"""
const s = SCENARIO; __world.scenario = s;
const op = (n, region) => ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ op: 'harvest_all', region_uid: region, n }) }];
const allMembers = (s.twoChunks || s.twoOps) ? [{ nonce: 'aaaaaaaaa1', deed_uids: [], op: 'harvest_all', region_uid: 'PR-LOP-1' }, { nonce: 'aaaaaaaaa2', deed_uids: [], op: 'harvest_all', region_uid: 'PR-LOP-2' }]
  : [{ nonce: 'aaaaaaaaa1', deed_uids: [], op: 'harvest_all', region_uid: 'PR-LOP-1' }];
const chunks = s.twoChunks ? allMembers.map((m) => ({ operations: [op(m.nonce, m.region_uid)], members: [m] }))
  : [{ operations: allMembers.map((m) => op(m.nonce, m.region_uid)), members: allMembers }];
const pv = { signing_enabled: true, chunks, preview_id: 'preview', preview_digest: 'digest' };
Object.assign(M, { open: true, openId: 1, gen: 1, player: 'alice', mode: 'plot', scopeKind: 'deeds', preview: pv, run: null, reviewing: null, deedUids: [], routingRunId: null });
CURRENT_PLAYER = 'alice'; BOARD = { player: 'alice', plots: [] };
walletReady = () => true; walletKind = () => 'keychain';
render = () => {};
prepareSigningBatch = async () => null;
const attempts = {}; let attemptFailures = 0; let chunkOfAttempt = {}; let preflights = 0;
postJson = async (url, body) => {
  if (url.endsWith('/preflight')) { const chunk = chunks[body.chunk_index]; return { outcome: 'PERMIT', chunk }; }
  if (url.endsWith('/prepare')) { (body.attempts || []).forEach((a) => { chunkOfAttempt[a.attempt_id] = a.chunk_index; }); return {}; }
  if (url.endsWith('/attempt')) {
    if (body.tx_id && attemptFailures < (s.failAttachTimes || 0)) { attemptFailures += 1; throw Error('HTTP 503'); }
    if (body.tx_id) attempts[body.attempt_id] = body.tx_id; return {};
  }
  if (url.endsWith('/verify')) {
    const known = attempts[body.attempt_id];
    const chunk = chunks[chunkOfAttempt[body.attempt_id] ?? 0];
    const indexed = known && __world.sent != null && __now() >= __world.sent + 3000 + (s.lag || 0) * 1000;
    const out = chunk.members.map((m) => {
      const index = allMembers.indexOf(m);
      return !indexed ? { nonce: m.nonce, outcome: 'pending', game_state: 'pending' }
        : s.refuse === index ? { nonce: m.nonce, outcome: 'failed', game_state: 'refused', error: 'Game refused op ' + index }
        : { nonce: m.nonce, outcome: 'success', game_state: 'accepted', evidence: [{ id: known + (index ? '-' + index : '') }], received_by_token: { GRAIN: 5 } };
    });
    return { tx_id: known || null, members: out, outcome: indexed ? (out.every((m) => m.outcome === 'success') ? 'success' : 'failed') : 'pending' };
  }
  throw Error('unexpected request ' + url);
};
let seq = 0;
globalThis.crypto = { randomUUID: () => 'attempt' + String(++seq).padStart(17, '0') + 'x' };
confirmHarvest().catch(() => {});
for (let step = 0; step < 120 && !['success', 'failed', 'never_sent'].includes(M.run?.phase); step++) await __advance(5000);
AFTER
const shared = confirmationState('alice', chunks[0].operations);
__resetConfirmer();   // a reload: the in-memory copy is gone; the stored row alone must carry the per-op states
const reloaded = confirmationState('alice', chunks[0].operations);
__mark({ phase: M.run?.phase || null, message: M.run?.message || '', perOp: shared?.perOp?.map((p) => p.state) || null,
  reloadedPerOp: reloaded?.perOp?.map((p) => p.state) || null,
  members: M.run?.members?.map((m) => m.outcome) || null, wallet: __world.walletCalls, attached: Object.values(attempts) });
"""


def test_f5_the_harvest_adapter_keeps_op0_confirmed_when_op1_is_refused():
    """Astra repro 1 (real harvest adapter): one transaction, op 0 accepted, op 1 refused. The persisted per-op states
    must be [confirmed, refused]. Before: the aggregate refusal was fanned out: [refused, refused]."""
    entry, source = _harvest_source()
    out = run_source(HARVEST_BODY.replace("SCENARIO", json.dumps({"twoOps": True, "refuse": 1, "lag": 5})).replace("AFTER", ""), entry, source)
    assert out["perOp"] == ["confirmed", "refused"] and out["reloadedPerOp"] == ["confirmed", "refused"], out


def test_f5_a_harvest_run_with_unsigned_members_is_not_a_success():
    """Astra F5: chunk 1's wallet answer is ambiguous (found later, confirmed); chunk 2 was never signed. The run must
    not report success. Before: 'success' with an unsent member."""
    entry, source = _harvest_source()
    out = run_source(HARVEST_BODY.replace("SCENARIO", json.dumps({"twoChunks": True, "wallet": "expired", "lag": 5})).replace("AFTER", ""), entry, source)
    assert "not_sent" in out["members"] and out["phase"] != "success", out


# F9: lifecycle after a reload / restart
def test_f9_harvest_keeps_attaching_the_id_until_the_server_acknowledges_it():
    """Astra shape 26 / F9 (exact case): the wallet's answer is lost, the own-history finder discovers the id, and the
    first two saves of that id to the server fail (a restart outage spans both). It must be retried until acknowledged,
    and the harvest confirmed. Before: two saves were attempted (the read and the finder's callback); after both failed the
    id was in the run's list and never saved again; the server verify stayed pending with no id for ever."""
    entry, source = _harvest_source()
    out = run_source(HARVEST_BODY.replace("SCENARIO", json.dumps({"failAttachTimes": 2, "wallet": "expired", "lag": 5})).replace("AFTER", ""), entry, source)
    assert out["phase"] == "success" and out["attached"] == ["ab" * 20], out


TWO_PAGES = r"""
const vm = __require('node:vm');
const shared = { store: new Map(), tabs: [] };
const storage = { getItem: k => shared.store.has(k) ? shared.store.get(k) : null,
  setItem: (k, v) => { const old = shared.store.get(k) ?? null; shared.store.set(k, String(v));
    for (const tab of shared.tabs) if (tab !== current) for (const fn of tab.listeners.storage || []) fn({ key: k, oldValue: old, newValue: String(v) }); },
  removeItem: k => shared.store.delete(k), get length() { return shared.store.size; }, key: i => [...shared.store.keys()][i] ?? null };
let current = null;
function page(source) {
  const listeners = {};
  const win = { addEventListener: (t, fn) => (listeners[t] ||= []).push(fn), removeEventListener() {}, dispatchEvent: (e) => { for (const fn of listeners[e.type] || []) fn(e); return true; } };
  const ctx = { window: win, localStorage: storage, document: { visibilityState: 'visible', getElementById: () => null, createElement: () => ({ setAttribute() {}, addEventListener() {} }), body: { appendChild() {} } },
    fetch: (...a) => fetch(...a), setTimeout, clearTimeout, queueMicrotask, Date, CustomEvent, AbortController, crypto, console, JSON, Math, Promise, Error, Map, Set, Number, String, Array, Object, RegExp, encodeURIComponent };
  ctx.globalThis = ctx; win.localStorage = storage;
  const tab = { ctx, listeners };
  shared.tabs.push(tab);
  current = tab;
  vm.runInNewContext(source, ctx);
  return tab;
}
const enter = (tab) => { current = tab; return tab.ctx; };
"""


def _page_source(name):
    # Read at run time from a file: two embedded modules exceed the OS limit of one `node -e` argument (128 KiB).
    import tempfile
    handle = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    handle.write(read_entry_source(STATIC / name))
    handle.close()
    return f"__require('fs').readFileSync({json.dumps(handle.name)}, 'utf8')"


def test_f9_an_orphan_found_after_a_reload_is_verified_without_another_page_event():
    """Astra F9: page 1 signed (no id) and was closed; page 2 loads. Its load-time resume finds the id and must also
    start the verifier, so the game's verdict arrives with no further pageshow. Before: the id was attached and the
    action stayed pending until the next page event."""
    out = run(TWO_PAGES + f"""
const ops = {OPS('orph', 1)};
__world.sent = __now();
__world.route = (path, body) => path === '/api/confirm/verdict' ? __world.reply({{ tx_id: body.tx_id, operations: body.operations.map((_, index) => ({{ index, state: 'confirmed' }})) }}) : null;
const one = enter(page({_page_source('shared-confirmer.js')}));
one.trackSigning('alice', ops); one.markAnswered('alice', ops);
await __advance(1000);
const two = enter(page({_page_source('shared-confirmer.js')}));
await __advance(60000);
__mark({{ state: two.confirmationState('alice', ops)?.state }});
""")
    assert out["state"] == "confirmed", out


def test_f9_the_restored_result_is_shown_on_screen():
    """Astra F9 / shape 27: a result restored after a reload must be visible. The UI consumer of the confirmer's state
    (confirm-notices.js) shows one plain line when an orphan reaches its verdict. Before: no consumer existed."""
    out = run(TWO_PAGES + f"""
const ops = {OPS('note', 1)};
__world.sent = __now();
__world.route = (path, body) => path === '/api/confirm/verdict' ? __world.reply({{ tx_id: body.tx_id, operations: body.operations.map((_, index) => ({{ index, state: 'confirmed' }})) }}) : null;
const one = enter(page({_page_source('shared-confirmer.js')}));
one.trackSigning('alice', ops); one.markAnswered('alice', ops);
await __advance(1000);
let source = null;
try {{ source = {_page_source('confirm-notices.js') if (STATIC / 'confirm-notices.js').exists() else 'null'}; }} catch (_) {{}}
if (!source) __mark({{ state: 'no-consumer' }});
const two = enter(page(source));
await __advance(60000);
__mark({{ state: two.confirmationState('alice', ops)?.state, shown: (two.confirmNoticesHtml ? two.confirmNoticesHtml() : '') }});
""")
    assert out["state"] == "confirmed" and "confirmed by the game" in out.get("shown", ""), out


# F6: cross-tab, quota
def test_f6_another_tabs_verdict_reaches_this_tabs_subscriber():
    """Astra F6 'Two tabs': tab B shows an action; tab A confirms it. Tab B must learn the verdict (storage event).
    Before: no storage listener; tab B stayed pending."""
    out = run(TWO_PAGES + f"""
const ops = {OPS('tabs', 1)};
const a = enter(page({_page_source('shared-confirmer.js')}));
a.trackSigning('alice', ops);
const b = enter(page({_page_source('shared-confirmer.js')}));
const seen = [];
b.subscribeConfirmation('alice', ops, row => seen.push(row.state));
enter(shared.tabs[0]);
a.attachTransaction('alice', ops, 'ab'.repeat(20));
a.confirmAction({{ account: 'alice', operations: ops, check: async () => ({{ state: 'confirmed' }}) }});
await __advance(5000);
__mark({{ state: seen.includes('confirmed') ? 'seen' : 'missed', seen }});
""")
    assert out["state"] == "seen", out


def test_f6_a_storage_quota_failure_after_approval_keeps_confirming_in_memory():
    """Astra repro 3: localStorage refuses writes right after signing. Confirmation must continue (memory copy) and the
    row must be saved once storage accepts writes again. Before: confirmAction threw QuotaExceededError."""
    out = run(f"""
const ops = {OPS('quota', 1)};
trackSigning('alice', ops);
const real = localStorage.setItem.bind(localStorage);
let full = true;
localStorage.setItem = (k, v) => {{ if (full && String(k).startsWith('ceres:confirm')) {{ const e = new Error('full'); e.name = 'QuotaExceededError'; throw e; }} return real(k, v); }};
let final = null, thrown = null;
try {{ confirmAction({{ account: 'alice', operations: ops, txId: 'ab'.repeat(20), check: async () => ({{ state: 'confirmed' }}) }}).then(a => {{ final = a.state; }}); }}
catch (e) {{ thrown = e.name; }}
await __advance(3000);
full = false;
await __advance(10000);
__mark({{ state: final, thrown, memory: confirmationState('alice', ops)?.state }});
""")
    assert out["thrown"] is None and out["state"] == "confirmed" and out["memory"] == "confirmed", out


def test_f6_a_queue_submitted_by_another_tab_reaches_this_tabs_wait():
    """Astra F6: a signed queue row is submitted by another tab's pump; this tab's waitForQueuedSubmission must resolve.
    Before: it listened only to its own tab's event and waited for ever."""
    out = run(r"""
const tx = 'ab'.repeat(20);
window.dispatchEvent = (event) => { for (const fn of (__listeners[event.type] || [])) fn(event); return true; };   // a browser delivers events
const key = 'ceres:signed-queue:v1:alice';
localStorage.setItem(key, JSON.stringify([{ txId: tx, account: 'alice', state: 'signed', updatedAt: new Date().toISOString() }]));
let result = null;
waitForQueuedSubmission('alice', tx).then(r => { result = 'resolved'; }, e => { result = 'rejected'; });
await __advance(1000);
const next = JSON.stringify([{ txId: tx, account: 'alice', state: 'submitted', updatedAt: new Date().toISOString() }]);
localStorage.setItem(key, next);
for (const fn of (__listeners.storage || [])) fn({ key, newValue: next, oldValue: null });
await __advance(1000);
__mark({ state: result });
""", entry=STATIC / "transaction-queue.js")
    assert out["state"] == "resolved", out


# F1, F7, F10: the swap card
SETTLE = r"""
const s = SCENARIO; __world.scenario = s;
const fundOp = ['custom_json', { id: 'sm_sps', required_auths: ['alice'], required_posting_auths: [], json: JSON.stringify({ action: 'token_transfer', params: { to: 'player0017', qty: 5, token: 'SPS', memo: 'lpsid:7', n: 'fd'.repeat(8) } }) }];
const st = swapState('alice', 'SPS');
const signedAt = new Date(__now()).toISOString();
Object.assign(st, { stage: 'settling', receipt: { nonce: 'ab'.repeat(8), tx_id: 'ab'.repeat(20), kind: 'swap_sell', symbol: 'SPS', twoStep: true, payload: {}, signed_at: signedAt,
  fundingUnknown: true, funding_ops: [fundOp], funding_asked_at: signedAt } });
if (s.sent) __world.sent = __now();
__world.hideFromWindow = !!s.outsideLast100;
trackSigning('alice', [fundOp]);
// Fix round 2: the funding request began (beacon) and the wallet CLOSED it (P0-1); a pre-round-2 tree ignores both.
if (typeof announceBegin === 'function') await announceBegin('alice', [fundOp]);   // fix round 3: awaited before the wallet (N1)
markAnswered('alice', [fundOp], { closed: true });
const cursors = [];
__world.gameFetch = async (path, body) => {
  if (path === '/api/wallet/lp/settle') {
    if (body.cursor != null) cursors.push(body.cursor);
    const next = body.cursor == null ? 9000 : body.cursor - 100;
    return __world.reply({ status: 'settling', funding: body.funding_tx ? undefined : 'not_found', next_cursor: next });
  }
  return null;
};
const ctx = { player: 'alice', account: () => 'alice', drawer: () => null, wallet: () => ({}), render: () => {}, setSigning: () => {}, reload: async () => {} };
settleLoop(ctx, st);
let reoffered = false;
for (let i = 0; i < LIMIT / 5000; i++) { await __advance(5000); if (st.stage === 'funding') reoffered = true; }
__mark({ stage: st.stage, reoffered, funding_tx: st.receipt?.funding_tx || null, cursors, wallet: __world.walletCalls });
"""


def _swap_source():
    entry = STATIC / "wallet-lp.js"
    return entry, read_entry_source(entry)


def test_f1_a_sent_funding_transfer_beyond_the_last_100_ops_is_never_offered_again():
    """Astra F1 (P0): the SPS transfer was signed and broadcast but its wallet answer was lost; it is no longer in the
    account's latest 100 operations, so the settle route's memo search says 'not_found'. No new funding signature may be
    offered; the transfer's own proof (found at the expiry read) is attached instead. Before: past 65 min after the SWAP,
    'Send SPS to the pool' came back -- a second transfer could be signed."""
    entry, source = _swap_source()
    out = run_source(SETTLE.replace("SCENARIO", json.dumps({"sent": True, "outsideLast100": True})).replace("LIMIT", "4300000"), entry, source)
    assert not out["reoffered"] and out["funding_tx"] == "ab" * 20, out


def test_f1_a_proven_unsent_funding_transfer_is_offered_again():
    """F1: only the server's complete absence proof for the persisted funding action re-offers the transfer. (This case
    also re-offered before -- on the swap's age without proof; it is listed as already passing, not as a fix.)"""
    entry, source = _swap_source()
    out = run_source(SETTLE.replace("SCENARIO", json.dumps({"sent": False})).replace("LIMIT", "4300000"), entry, source)
    assert out["reoffered"] and out["funding_tx"] is None, out


def test_f10_the_older_settlement_pages_advance_their_own_cursor():
    """Astra F10: each read takes the newest pool page (resetting the cursor) and, after five minutes, one older page.
    The older pages must advance (9000, 8900, 8800, ...) and stop after five. Before: the same second page every time."""
    entry, source = _swap_source()
    out = run_source(SETTLE.replace("SCENARIO", json.dumps({"sent": True})).replace("LIMIT", "900000"), entry, source)
    assert out["cursors"][:5] == [9000, 8900, 8800, 8700, 8600] and len(out["cursors"]) == 5, out


def test_f7_a_lost_ack_swap_never_reads_its_own_history_outside_the_shared_budget():
    """Astra F7: a swap whose wallet answer was lost (the operations are known). Its history reads must be the shared
    finder's (server budget), never the swap route's own `/find` on every check past ten minutes. Before: about one
    `/api/wallet/lp/find` a minute after the window."""
    from tests.ceres.test_replay import SWAP, SWAP_SOURCE_PATCH
    entry = STATIC / "wallet-lp.js"
    source = read_entry_source(entry).replace(*SWAP_SOURCE_PATCH)
    body = SWAP.replace("SCENARIO", json.dumps({"wallet": "silent", "neverSent": True})).replace("LIMIT", "1800000").replace("STEP", "5000")
    body = body.replace("console.log('IR9545-RESULT ' + JSON.stringify({ state:", "console.log('IR9545-RESULT ' + JSON.stringify({ lpFinds: __world.fetches.filter((p) => p === '/api/wallet/lp/find').length, state:")
    out = run_source(body.replace("(async () => {", "", 1).rsplit("})().catch", 1)[0], entry, source)
    assert out["lpFinds"] == 0, out


CHANGES = r"""
const s = SCENARIO; __world.scenario = s;
CURRENT_PLAYER = 'alice'; BOARD = { player: 'alice', plots: [] };
const op = ['custom_json', { id: 'sm_stake_change', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ items: [], n: 'chg0000001' }) }];
const target = { scope: 'portfolio' };
const chunk = { operations: [op] };
const review = { payload: { account: 'alice', chunks: [chunk] }, stagedSnapshot: [] };
CERES_PREVIEWS.set(previewKey(target), review);
const button = { disabled: false, hidden: false, setAttribute() {} };
const stateEl = { dataset: {} };
const reviewEl = { querySelector: (sel) => sel.includes('confirm') ? button : null, querySelectorAll: () => [stateEl] };
document.querySelector = (sel) => sel.includes('op-preview-review') ? reviewEl : sel.includes('data-op-action="confirm"') ? button : null;
walletReady = () => true; freshReviewForSigning = async (_t, r) => r; renderCheckoutSurface = () => {}; prepareSigningBatch = async () => null;
const notices = []; const banners = []; const verified = [];
setVerificationNotice = (_el, notice) => { notices.push(notice?.code || null); };
setConfirmationBannerState = (title) => { banners.push(title); };
verifyBroadcast = async (_chunk, _el, txId) => { verified.push(txId); };
confirmOperationBroadcast(target).catch((e) => console.log('CHG-ERR ' + (e && e.message)));
await __advance(LIMIT);
__mark({ notices, banners, verified, disabled: button.disabled, hidden: button.hidden, wallet: __world.walletCalls });
"""


@pytest.mark.parametrize("case", ["found", "never_sent"])
def test_high1_a_changes_chunk_follows_the_confirmers_proof(case):
    """Aletheia HIGH-1, Changes chunks (operations.js confirmOperationBroadcast, real gate and confirmer): found -> the
    chunk is verified by its found id (this already worked before); never sent -> 'Not sent' and Sign is offered again.
    Before (never sent): 'Confirming' for ever with the Sign button hidden and disabled."""
    scenario = {"wallet": "expired", "lag": 5} if case == "found" else {"wallet": "lost", "neverSent": True}  # round 2: a CLOSED request (P0-1)
    limit = 200000 if case == "found" else 4200000
    entry, source = _exposed("operations.js", ["walletReady", "freshReviewForSigning", "renderCheckoutSurface", "prepareSigningBatch",
                                               "setVerificationNotice", "setConfirmationBannerState", "verifyBroadcast"])
    out = run_source(CHANGES.replace("SCENARIO", json.dumps(scenario)).replace("LIMIT", str(limit)), entry, source)
    if case == "found":
        assert out["verified"] == ["ab" * 20], out
    else:
        assert "verification_not_sent" in out["notices"] and not out["disabled"] and not out["hidden"] and out["verified"] == [], out
    assert out["wallet"] == 1, out
