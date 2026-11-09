""" fix round 2, client behaviour (astra-review-r2.md P0-1, P1-3, P1-4, P1-6, P1-7; aletheia-verdict-r2.md).

Each test runs the REAL module(s) -- broadcast gate, shared confirmer, the named flow module -- in the replay suite's
deterministic fake world (virtual clock, fake Keychain/chain/game, no network, no browser). The fake own-history server
follows the round-2 server contract (absence only for a CLOSED wallet request whose begin the server heard first; one
budget per action). Each test failed on the round-1 tree (snapshot before-r2, with these same test files) and passes now.
"""
from __future__ import annotations

import json

from tests.ceres.test_replay import STATIC, SWAP, SWAP_SOURCE_PATCH, _run
from tests.ceres.test_round1_flows import OPS, run, run_source
from tests.ceres.vm_context import read_entry_source, run_in_module_context


# P0-1: silence is never an answer
def test_p0_1_a_silent_wallet_keeps_its_claim_for_ever_and_is_never_signed_twice():
    """Astra r2 , as amended by the maintainer's IN-0888 (fix round 3): the wallet never answers. For the transaction's
    whole lifetime (here 55 minutes in) the same request is refused as a duplicate (its claim held) and the row is not
    "never sent". (After the lifetime it ends "never asked": test_round3_flows.py::test_m1_*.)"""
    out = run(f"""
const ops = {OPS('slnt', 1)};
__world.scenario = {{ wallet: 'silent', neverSent: true }};
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(55 * 60 * 1000);
let again = null;
__world.scenario = {{ lag: 0 }};
BroadcastGate.broadcast('alice', ops).then(() => {{ again = 'sent-twice'; }}, e => {{ again = e.duplicateBroadcast ? 'duplicate-refused' : 'refused:' + e.message; }});
await __advance(5000);
__mark({{ state: again, row: confirmationState('alice', ops)?.state, wallet: __world.walletCalls }});
""")
    assert out["row"] == "pending" and out["state"] == "duplicate-refused" and out["wallet"] == 1, out


def test_p0_1_the_gate_tells_the_server_when_the_request_began_and_whether_the_wallet_closed_it():
    """Fix round 3 (astra-review-r3 N1) changes the first half: the begin is RECORDED by the server before the wallet
    opens (one awaited /api/confirm/intent, nothing else,  otherwise unchanged), and its token is presented with
    the finder's reads. The finder says `closed` only for an answer that ended the request: Keychain's error answer yes,
    HiveAuth's uncertain answer ('expired', the request may still be approved) no."""
    out = run(f"""
const finds = [];
__world.route = async (path, body) => {{ if (path === '/api/confirm/find') finds.push({{ rely: JSON.stringify(body.operations).includes('rely'), closed: body.closed === true, answered: body.answered === true, token: Boolean(body.begin_token) }}); return null; }};
const fetchesBeforeWallet = [];
const keychain = window.hive_keychain.requestBroadcast;
window.hive_keychain.requestBroadcast = (...args) => {{ fetchesBeforeWallet.push(__world.fetches.slice()); return keychain(...args); }};
const lost = {OPS('lost', 1)};
__world.scenario = {{ wallet: 'lost', neverSent: true }};
BroadcastGate.broadcast('alice', lost).catch(() => {{}});
await __advance(8000);
const lostFinds = finds.splice(0);
const relay = {OPS('rely', 1)};
__world.scenario = {{ wallet: 'expired', neverSent: true }};
BroadcastGate.broadcast('alice', relay).catch(() => {{}});
await __advance(70000);
__mark({{ beacons: __world.beacons || 0, lost: lostFinds, relay: finds.filter(f => f.rely), before: fetchesBeforeWallet }});
""")
    assert out["beacons"] >= 1, out                                     # the closing answer of the 'lost' request
    assert out["lost"] and all(f["closed"] and f["token"] for f in out["lost"]), out
    assert out["relay"] and not any(f["closed"] for f in out["relay"]), out
    assert out["before"][0] == ["/api/confirm/intent"], out             # the begin, and only the begin, before the wallet


# P1-3: late proof is bound to its attempt
def test_p1_3_an_old_attempts_late_answer_never_touches_the_new_attempt():
    """Astra r2  (helper level, as Astra's probe): attempt 1 ended, attempt 2 started; attempt 1's late wallet answer
    and id arrive. Attempt 2 must keep no id and stay unanswered; the old proof is kept on attempt 1's record. Before:
    LATE_OLD_CALLBACK -- attempt 2 inherited attempt 1's id."""
    out = run(f"""
const ops = {OPS('late', 1)};
beginAttempt('alice', ops);
markConfirmation('alice', ops, 'never-sent', {{ error: 'declined' }});
const second = beginAttempt('alice', ops);
markAnswered('alice', ops, {{ attempt: 1, closed: true }});
attachTransaction('alice', ops, 'bb'.repeat(20), {{ attempt: 1 }});
const row = confirmationState('alice', ops);
__mark({{ attempt: row.attempt, txId: row.txId, answeredAt: row.answeredAt, prior: row.priorAttempts || null, second: second.attempt }});
""")
    assert out["attempt"] == 2 and out["txId"] is None and out["answeredAt"] is None, out
    assert out["prior"] and out["prior"][0]["attempt"] == 1 and out["prior"][0]["txId"] == "bb" * 20, out


# P1-4: a cached final verdict never suppresses the domain receipt
def test_p1_4_reload_before_screen_the_flow_still_gets_its_domain_receipt():
    """Astra r2  / FINAL_THEN_DOMAIN: the orphan verifier (or generic verdict) finished the action before the flow's
    screen opened. confirmAction must still run the flow's receipt read once and return its result. Before: checks 0,
    result undefined (checkout stored an undefined receipt; combine threw reading bcx)."""
    out = run(f"""
const ops = {OPS('rcpt', 1)};
trackSigning('alice', ops);
attachTransaction('alice', ops, 'cd'.repeat(20));
markConfirmation('alice', ops, 'confirmed', {{}});
let checks = 0, answer = null;
confirmAction({{ account: 'alice', operations: ops, check: async () => {{ checks += 1; return {{ state: 'confirmed', result: {{ bcx: 7 }} }}; }} }}).then(a => {{ answer = a; }});
await __advance(5000);
__mark({{ checks, state: answer?.state, bcx: answer?.result?.bcx ?? null }});
""")
    assert out["checks"] >= 1 and out["state"] == "confirmed" and out["bcx"] == 7, out


def test_p1_4_another_tab_finished_it_the_receipt_is_still_read_and_the_verdict_stays_final():
    """Cross-tab final: another tab's write (storage event) made the row refused before this flow asked. The flow's
    receipt read runs, and its pending answer can never turn the final verdict back. Before: no read, no result."""
    out = run(f"""
const ops = {OPS('xtab', 1)};
const row = trackSigning('alice', ops);
const other = {{ ...row, txId: 'ef'.repeat(20), state: 'refused', error: 'Game said no', rev: (row.rev || 0) + 5,
  perOp: [{{ index: 0, state: 'refused', error: 'Game said no' }}] }};
localStorage.setItem(keyFor(row.id), JSON.stringify(other));   // the other tab's write
for (const fn of (__listeners.storage || [])) fn({{ key: keyFor(row.id), newValue: JSON.stringify(other) }});
let checks = 0, answer = null;
confirmAction({{ account: 'alice', operations: ops, check: async () => {{ checks += 1; return {{ state: 'refused', result: {{ message: 'Game said no' }} }}; }} }}).then(a => {{ answer = a; }});
await __advance(5000);
__mark({{ checks, state: answer?.state, message: answer?.result?.message ?? null, row: confirmationState('alice', ops)?.state }});
""")
    assert out["checks"] >= 1 and out["state"] == "refused" and out["message"] == "Game said no" and out["row"] == "refused", out


def test_p1_4_a_mixed_result_notice_says_what_went_through():
    """Astra r2  / shape 9: op 0 confirmed, op 1 refused. The restored notice must not say only 'refused'.
    Before: 'The game refused your land action (2 operations) ...'."""
    entry = STATIC / "confirm-notices.js"
    result = run_in_module_context(r"""
const row = { orphan: true, state: 'refused', operations: [['custom_json', { id: 'sm_land_operation' }], ['custom_json', { id: 'sm_land_operation' }]],
  perOp: [{ index: 0, state: 'confirmed' }, { index: 1, state: 'refused', error: 'op 1 rejected' }] };
console.log('IR9545-RESULT ' + JSON.stringify({ text: confirmNoticeText(row) }));
""", entry=entry, check=False)
    line = next(l for l in result.stdout.splitlines() if l.startswith("IR9545-RESULT "))
    text = json.loads(line.split(" ", 1)[1])["text"]
    assert "1 confirmed" in text and "1 refused" in text and "op 1 rejected" in text, text


def test_p1_4_a_result_that_became_final_before_the_notices_loaded_is_still_shown():
    """A row a previous page left (orphan) became final before confirm-notices.js initialised (the orphan verifier ran
    first). Its result must still be shown once. Before: only a pending->final change seen by this module was shown."""
    entry = STATIC / "confirm-notices.js"
    source = read_entry_source(entry)
    result = run_in_module_context(r"""
const line = confirmNoticeFor({ id: 'x', attempt: 1, orphan: true, state: 'confirmed', updatedAt: new Date().toISOString(),
  operations: [['custom_json', { id: 'sm_claim_rewards' }]] });
const again = confirmNoticeFor({ id: 'x', attempt: 1, orphan: true, state: 'confirmed', updatedAt: new Date().toISOString(),
  operations: [['custom_json', { id: 'sm_claim_rewards' }]] });
console.log('IR9545-RESULT ' + JSON.stringify({ shown: Boolean(line), again: Boolean(again) }));
""", entry=entry, source=source, check=False)
    line = next(l for l in result.stdout.splitlines() if l.startswith("IR9545-RESULT "))
    out = json.loads(line.split(" ", 1)[1])
    assert out == {"shown": True, "again": False}, out


# P1-6: durable fallback, cancellation
def test_p1_6_quota_then_close_the_row_survives_in_session_storage():
    """Astra r2 #6: localStorage refuses every write (quota) after approval, then the tab reloads (memory gone). The
    row must still be there with its id. Before: memory only -- lost on close."""
    out = run(f"""
const ops = {OPS('quot', 1)};
const session = new Map();
globalThis.sessionStorage = window.sessionStorage = {{ getItem: k => session.has(k) ? session.get(k) : null, setItem: (k, v) => session.set(k, String(v)),
  removeItem: k => session.delete(k), key: i => [...session.keys()][i] ?? null, get length() {{ return session.size; }} }};
const realSet = localStorage.setItem.bind(localStorage);
localStorage.setItem = () => {{ throw Object.assign(Error('QuotaExceededError'), {{ name: 'QuotaExceededError' }}); }};
trackSigning('alice', ops);
attachTransaction('alice', ops, 'aa'.repeat(20));
__resetConfirmer();   // the tab closes: the memory copy is gone
const restored = confirmationState('alice', ops);
__mark({{ txId: restored?.txId || null, sessionRows: session.size }});
""")
    assert out["txId"] == "aa" * 20 and out["sessionRows"] >= 1, out


def test_p1_6_the_harvest_receipt_read_is_cancellable():
    """Astra r2 #8: the harvest flow's /api/harvest/verify read gets the scheduler's AbortSignal, so a timed-out read is
    aborted and its late answer cannot rewrite the receipt. Before: the read ignored the signal."""
    from tests.ceres.test_replay import _harvest_source
    from tests.ceres.test_round1_flows import HARVEST_BODY
    entry, source = _harvest_source()
    body = HARVEST_BODY.replace("SCENARIO", json.dumps({"lag": 5}))
    body = body.replace("postJson = async (url, body) => {", "let signals = 0; postJson = async (url, body, options) => { if (url.endsWith('/verify') && options?.signal) signals += 1;")
    body = body.replace("AFTER", "")
    body = body.replace("__mark({ phase: M.run?.phase || null,", "__mark({ signals, phase: M.run?.phase || null,")
    out = run_source(body, entry, source)
    assert out["phase"] == "success" and out["signals"] >= 1, out


# P1-7: deed lock by verdict; swap refusals shown; legacy receipts never browser-aged
def test_p1_7_a_signed_deed_lock_is_never_settled_by_the_board():
    """Astra r2 shape 5: a signed lock (tx id, no game verdict yet); the board already shows the deed locked. The plan
    must stay until the operation's verdict. Before: the board match dropped it (treated as confirmed)."""
    plot_lock = STATIC / "plot-lock.js"
    result = run_in_module_context(r"""
const KEY = 'ceres:plot-lock-plan:v1:alice';
const signed = { deed_uid: 'I-1-aaaaaaaa', action: 'lock', baselineDays: null, baselineUnlockDate: null, lockDays: 3, txId: 'ab'.repeat(20) };
localStorage.setItem(KEY, JSON.stringify([signed]));
const fetches = [];
globalThis.fetch = window.fetch = async (url) => { fetches.push(String(url)); return { ok: true, json: async () => ({ status: 'pending' }) }; };
reconcilePlotLockPlans({ player: 'alice', plots: [{ deed_uid: 'I-1-aaaaaaaa', plot: '1-1-1', lock_days: 3, unlock_date: null }] });
const kept = STAGED_PLOT_LOCKS.has('I-1-aaaaaaaa');
const gone = { player: 'alice', plots: [] };
reconcilePlotLockPlans(gone);
console.log('IR9545-RESULT ' + JSON.stringify({ kept, keptWithoutRow: STAGED_PLOT_LOCKS.has('I-1-aaaaaaaa') }));
process.exit(0);
""", entry=plot_lock, check=False)
    line = next((l for l in result.stdout.splitlines() if l.startswith("IR9545-RESULT ")), None)
    assert line, (result.stdout[-800:], result.stderr[-1500:])
    out = json.loads(line.split(" ", 1)[1])
    assert out == {"kept": True, "keptWithoutRow": True}, out


def test_p1_7_a_refused_swap_is_shown_with_the_games_reason():
    """Astra r2 shape 6: the game refuses a (non-transfer) swap. The card shows the refusal and its reason. Before: back
    to the edit card with the receipt and the reason erased."""
    entry = STATIC / "wallet-lp.js"
    source = read_entry_source(entry).replace(*SWAP_SOURCE_PATCH)
    body = SWAP.replace("SCENARIO", json.dumps({"lag": 5, "refuse": 0})).replace("LIMIT", "200000").replace("STEP", "5000")
    out = _run(body, entry, source)
    assert out["stage"] == "refused" and out["refusal"] == "Game refused op 0" and out["receipt"], out


def test_p1_7_a_legacy_swap_intent_is_never_declared_not_sent_by_the_browsers_clock():
    """Astra r2 P1-7 (wallet-lp.js:505): an intent saved before  (no operations) whose nonce lookup says
    'not_found, covered' past 65 minutes of browser time. It stays honestly unresolved. Before: 'never-sent' (the card
    reopened for a new swap while the old one may still have been sent)."""
    entry = STATIC / "wallet-lp.js"
    source = read_entry_source(entry).replace(*SWAP_SOURCE_PATCH)
    body = r"""
(async () => {
__world.scenario = { neverSent: true };
const symbol = 'WOOD';
const st = swapState('alice', symbol);
const ctx = { player: 'alice', account: () => 'alice', drawer: () => null, wallet: () => ({}), render: () => {}, setSigning: () => {}, reload: async () => {} };
const asked = new Date(__now() - 2 * 3600 * 1000).toISOString();
Object.assign(st, { stage: 'confirming', receipt: { kind: 'swap_sell', nonce: 'cd'.repeat(8), asked_at: asked, signed_at: asked, pay: 'WOOD', receive: 'DEC', input: 10 } });
__world.gameFetch = async (path) => path === '/api/wallet/lp/find' ? __world.reply({ status: 'not_found', covered: true }) : null;
resumeSwapChecks(ctx);
await __advance(600000);
console.log('IR9545-RESULT ' + JSON.stringify({ stage: st.stage, note: st.lpNote || '' }));
process.exit(0);
})().catch((error) => { console.log('IR9545-RESULT ' + JSON.stringify({ stage: 'error:' + (error && error.message) })); process.exit(0); });
"""
    out = _run(body, entry, source)
    assert out["stage"] == "confirming", out


# Item 11: every reachable signer's operations carry a nonce the finder can match
def test_item11_every_nonce_generator_matches_the_finders_nonce_rule():
    """Open item 'operations without a nonce': every Ceres operation builder (library/ceres-signing-options.md) takes
    its nonce from one of these generators; each output must satisfy the server's exact-match rule."""
    import re
    import secrets
    import uuid
    from ceres.routes_confirm import NONCE
    from fides_cards import ops as card_ops
    from fides_land import harvest, ops as land_ops
    samples = [land_ops._nonce(), card_ops._nonce(), harvest.nonce(), secrets.token_hex(8), str(uuid.uuid4())]
    assert all(NONCE.fullmatch(sample) for sample in samples), samples
    # The only browser-built operation (deed lock) uses crypto.randomUUID(): the UUID shape above.
    source = (STATIC / "plot-lock.js").read_text()
    assert re.search(r"n: crypto\.randomUUID\(\)", source)
