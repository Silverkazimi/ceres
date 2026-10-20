"""..: a swap whose wallet returned no transaction id is found and confirmed by itself; no paste prompt,
no "Check again"; a Stop control; HiveAuth may send the SPS sale's Active pool transfer; the wallet wait names the app."""
import json
from pathlib import Path

from ceres import routes_wallet_lp as lp_routes, wallet_lp_operations as operations
from tests.ceres.vm_context import read_entry_source, run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
ENTRY = STATIC / "wallet-lp.js"
SOURCE = read_entry_source(ENTRY).replace("await requestBroadcastThroughGate(ctx.player, fresh.operations", "await globalThis.__gate(ctx.player, fresh.operations").replace("|| !walletReady(ctx.player)) return;", "|| false) return;")

FLOW = r"""
const calls = [];
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, 0, ...rest);
const symbol = 'WOOD';
const st = swapState('alice', symbol);
Object.assign(st, { direction: 'sell', amount: '10', quoted: '10', region: 'PR-AAA-1',
  quote: { kind: 'swap_sell', input: 10, output: 5, minimum: 4.9, fee_percent: '1', pool_cost: 0.1 } });
const wallet = { account: { DEC: '100' }, swap: { symbols: ['WOOD'], directions: ['swap_sell', 'swap_buy'], signing_enabled: true, rates: {}, fees: {} },
  regions: [{ region_uid: 'PR-AAA-1', balances: { WOOD: '50' } }] };
const target = { closest: () => ({ dataset: { lpSymbol: 'WOOD' } }) };
const ctx = { player: 'alice', account: () => 'alice', drawer: () => null, wallet: () => wallet, render: () => {}, setSigning: () => {}, reload: async () => {} };
"""


def _run(js):
    body = "(async () => {\n" + FLOW + js + "\n})().catch(error => { console.error(error && error.stack || error); process.exit(1); });"
    run_in_module_context(body, entry=ENTRY, source=SOURCE)


def test_no_transaction_id_is_found_in_history_then_confirmed():
    _run(r"""
let finds = 0;
globalThis.fetch = async (url, init) => {
  const path = String(url); calls.push(path);
  if (path.includes('/find')) { finds += 1; return { ok: true, status: 200, json: async () => finds < 3 ? { status: 'not_found', covered: true } : { status: 'found', tx_id: 'd'.repeat(40) } }; }
  if (path.includes('/verify')) {
    const body = JSON.parse(init.body);
    if (body.tx_id !== 'd'.repeat(40)) throw Error('verify without the found id');
    return { ok: true, status: 200, json: async () => ({ status: 'confirmed', game_result: { dec_amount: '5' } }) };
  }
  return { ok: true, status: 200, json: async () => ({ operations: [['custom_json', { id: 'x', json: '{}' }]], nonce: 'ab'.repeat(8), payload: {}, quote: { input: 10, output: 5 } }) };
};
globalThis.__gate = async () => { const e = new Error('HiveAuth gave no id'); e.broadcasted = undefined; throw e; };
await swapAction(ctx, 'swap', target);
for (let i = 0; i < 200 && st.stage !== 'done'; i++) await new Promise(r => realSetTimeout(r, 2));
if (st.stage !== 'done') throw Error('expected done, got ' + st.stage);
if (st.receipt.tx_id !== 'd'.repeat(40) || finds !== 3) throw Error('history lookup did not supply the id');
if (calls.some(path => path.includes('/verify') && false)) throw Error('unreachable');
""")


def test_stop_ends_the_automatic_check_and_shows_resume():
    _run(r"""
globalThis.fetch = async (url) => {
  const path = String(url);
  if (path.includes('/find')) { st.stopped || swapAction(ctx, 'stop', target); return { ok: true, status: 200, json: async () => ({ status: 'not_found', covered: true }) }; }
  return { ok: true, status: 200, json: async () => ({ operations: [['custom_json', { id: 'x', json: '{}' }]], nonce: 'ab'.repeat(8), payload: {}, quote: { input: 10, output: 5 } }) };
};
globalThis.__gate = async () => { const e = new Error('no answer'); e.broadcasted = undefined; throw e; };
await swapAction(ctx, 'swap', target);
for (let i = 0; i < 200 && (st.checking || st.stopped !== true); i++) await new Promise(r => realSetTimeout(r, 2));
if (st.stage !== 'confirming' || st.checking || st.stopped !== true) throw Error('stop did not end the check: ' + st.stage + ' ' + st.checking);
const html = swapHtml(symbol, wallet, 'alice');
if (!html.includes('Resume checking') || html.includes('Check again') || html.includes('transaction id')) throw Error('stopped card: ' + html);
if (!html.includes('is-waiting')) throw Error('the open swap box must carry the warning colour');
""")


def test_source_has_no_paste_prompt_and_hiveauth_can_send_active():
    lp = ENTRY.read_text(encoding="utf-8")
    assert "prompt(" not in lp and "enter-id" not in lp and "'Check again', 'primary', 'touch'));" not in lp
    wallet = (STATIC / "wallet.js").read_text(encoding="utf-8")
    assert "HiveAuth signing is limited to Posting authority" not in wallet
    assert "Check the Keychain app on your phone now" in wallet and "walletLogo(kind)" in wallet
    assert "walletWaitHtml(player, 'Active')" in lp and "st.fundError" in lp


def test_history_lookup_matches_only_this_players_land_operation_with_the_nonce():
    payload = operations.sell("alice", "PR-CEF-65", "GRAIN", 2, 1, 2.5)["payload"]
    op = lambda body, tx: [1, {"trx_id": tx, "op": ["custom_json", body]}]
    mine = {"id": "sm_land_operation", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": ["alice"]}
    rows = [op({**mine, "required_posting_auths": ["bob"]}, "a" * 40),
            op({**mine, "id": "sm_other"}, "b" * 40),
            op({**mine, "json": json.dumps({**payload, "n": "f" * 16})}, "c" * 40),
            op(mine, "D" * 40)]
    assert lp_routes.find_in_history(rows, "alice", payload["n"]) == "d" * 40
    assert lp_routes.find_in_history(rows[:3], "alice", payload["n"]) is None


def test_unsent_swap_stays_pending_until_expiry_and_late_broadcast_is_adopted_before_another_swap():
    # a history miss before Hive expiry cannot release the pending wallet intent.
    _run(r"""
let finds = 0; let previews = 0; let gates = 0; let late = false;
globalThis.fetch = async (url, init) => {
  const path = String(url); calls.push(path);
  if (path.includes('/find')) { finds += 1; return { ok: true, status: 200, json: async () => late ? { status: 'found', tx_id: 'e'.repeat(40) } : { status: 'not_found', covered: true } }; }
  if (path.includes('/verify')) return { ok: true, status: 200, json: async () => ({ status: 'confirmed', game_result: { dec_amount: '5' } }) };
  previews += 1;
  return { ok: true, status: 200, json: async () => ({ operations: [['custom_json', { id: 'x', json: '{}' }]], nonce: 'ab'.repeat(8), payload: {}, quote: { input: 10, output: 5 } }) };
};
globalThis.__gate = async () => { gates += 1; const e = new Error('no answer'); e.broadcasted = undefined; throw e; };
const realNow = Date.now; let offset = 0; Date.now = () => realNow() + offset;
await swapAction(ctx, 'swap', target);  // the click returns at once; the mock clock advances six minutes, below Hive expiry plus grace
await new Promise(r => realSetTimeout(r, 20));
offset = 6 * 60000;
for (let i = 0; i < 60; i++) { await new Promise(r => realSetTimeout(r, 2)); if (st.stage !== 'confirming') throw Error('released before Hive expiry: ' + st.stage); }
late = true; offset = 0;  // the wallet broadcast late; the next action adopts it before any new signature
if (st.stage !== 'confirming') throw Error('expected the unresolved intent to remain blocked, got ' + st.stage + ' ' + st.lpNote);
const stored = JSON.parse(localStorage.getItem('ceres:wallet-lp-intent:v1:alice:WOOD') || 'null');
if (stored?.stage !== 'confirming' || stored.receipt?.nonce !== 'ab'.repeat(8)) throw Error('the pending intent was not durably retained');
// A second click cannot start while this stored intent is unresolved; the background check adopts the late broadcast.
await swapAction(ctx, 'swap', target);
await swapAction(ctx, 'verify', target);  // resume if the zero-delay VM reached the defensive round cap
for (let i = 0; i < 200 && st.stage !== 'done'; i++) await new Promise(r => realSetTimeout(r, 2));
if (gates !== 1) throw Error('a second swap was requested from the wallet: ' + gates);
if (st.stage !== 'done' || st.receipt.tx_id !== 'e'.repeat(40)) throw Error('the late broadcast was not adopted: ' + st.stage);
if (localStorage.getItem('ceres:wallet-lp-intent:v1:alice:WOOD')) throw Error('the confirmed intent stayed stored');
Date.now = realNow;
""")


# an unknown history read never releases a swap and never opens the wallet
PREVIEW = r"""
const previewJson = { operations: [['custom_json', { id: 'x', json: '{}' }]], nonce: 'ab'.repeat(8), payload: {}, quote: { input: 10, output: 5 } };
const freshQuote = () => Object.assign(st, { direction: 'sell', amount: '10', quoted: '10', quote: { kind: 'swap_sell', input: 10, output: 5, minimum: 4.9, fee_percent: '1', pool_cost: 0.1 } });
const realNow = Date.now; let offset = 0; Date.now = () => realNow() + offset;
const tick = () => new Promise(r => realSetTimeout(r, 2));
const watchKey = 'ceres:wallet-lp-watch:v1:alice:WOOD';
const seedWatch = () => localStorage.setItem(watchKey, JSON.stringify([{ at: new Date().toISOString(), receipt: { kind: 'swap_sell', pay: 'WOOD', receive: 'DEC', region: 'PR-AAA-1', input: 10, output: 5, nonce: 'cd'.repeat(8), payload: {}, tx_id: null } }]));
"""


def test_failing_history_read_keeps_the_swap_open_and_blocks_a_second_swap():
    # (a) /find throws for 6 simulated minutes: the swap stays confirming, never edit, and a new click never reaches the wallet
    _run(PREVIEW + r"""
let gates = 0; let previews = 0; let finds = 0;
globalThis.fetch = async (url) => {
  const path = String(url);
  if (path.includes('/find')) { finds += 1; throw new Error('network down'); }
  if (path.includes('/verify')) throw Error('verify without an id');
  previews += 1; return { ok: true, status: 200, json: async () => previewJson };
};
globalThis.__gate = async () => { gates += 1; const e = new Error('no answer'); e.broadcasted = undefined; throw e; };
await swapAction(ctx, 'swap', target);
await new Promise(r => realSetTimeout(r, 20));
offset = 6 * 60000;
const before = finds;
for (let i = 0; i < 60; i++) { await tick(); if (st.stage !== 'confirming') throw Error('released on a failed read: ' + st.stage + ' ' + st.lpNote); }
if (finds <= before) throw Error('Ceres stopped reading the history');
// The VM maps every source timer to zero, so the defensive round cap may stop the loop during this assertion window.
if (st.historyUnreadable !== true) throw Error('expected the unreadable flag');
const html = swapHtml(symbol, wallet, 'alice');
if (!html.includes('Ceres has not confirmed what happened to this request yet. It stays pending; do not swap again.')) throw Error('no pending line: ' + html);
if (html.includes('Check again')) throw Error('no Check again button');
freshQuote(); st.stage = 'confirming';
await swapAction(ctx, 'swap', target);
if (gates !== 1 || previews !== 1) throw Error('a second swap reached the wallet: gates ' + gates + ' previews ' + previews);
Date.now = realNow; process.exit(0);
""")


def test_successful_not_found_before_expiry_keeps_the_amber_intent_open():
    # (b) a successful history miss before the wallet request expires is not proof it was not sent.
    _run(PREVIEW + r"""
let gates = 0; let failing = true;
globalThis.fetch = async (url) => {
  const path = String(url);
  if (path.includes('/find')) { if (failing) return { ok: false, status: 502, json: async () => ({ error: 'bad gateway' }) }; return { ok: true, status: 200, json: async () => ({ status: 'not_found', covered: true }) }; }
  return { ok: true, status: 200, json: async () => previewJson };
};
globalThis.__gate = async () => { gates += 1; const e = new Error('no answer'); e.broadcasted = undefined; throw e; };
await swapAction(ctx, 'swap', target);
await new Promise(r => realSetTimeout(r, 20));
offset = 6 * 60000;
for (let i = 0; i < 30; i++) await tick();
if (st.stage !== 'confirming') throw Error('a 502 released the swap');
failing = false;
for (let i = 0; i < 400 && st.stage !== 'edit'; i++) await tick();
if (st.stage !== 'confirming' || st.lpNoteTone === 'danger') throw Error('history miss released the still-live request: ' + st.stage + ' ' + st.lpNote);
if (!swapHtml(symbol, wallet, 'alice').includes('wallet-swap-note is-warn')) throw Error('pending state is not amber');
Date.now = realNow;
""")


def test_watch_list_with_failing_read_refuses_to_open_the_wallet():
    # (c) a watched intent whose lookup fails: signIntent refuses, the wallet is never opened, the clear line is shown
    _run(PREVIEW + r"""
seedWatch();
let gates = 1; let previews = 0; let finds = 0; let ok = false;
globalThis.fetch = async (url) => {
  const path = String(url);
  if (path.includes('/find')) { finds += 1; if (!ok) throw new Error('network down'); return { ok: true, status: 200, json: async () => ({ status: 'not_found', covered: true }) }; }
  if (path.includes('/verify')) return { ok: true, status: 200, json: async () => ({ status: 'confirmed', game_result: { dec_amount: '5' } }) };
  previews += 1; return { ok: true, status: 200, json: async () => previewJson };
};
globalThis.__gate = async () => { gates += 1; return { txId: 'f'.repeat(40) }; };
freshQuote();
await swapAction(ctx, 'swap', target);
if (finds !== 1 || previews !== 0 || gates !== 1) throw Error('the wallet path was entered on an unknown: finds ' + finds + ' previews ' + previews + ' gates ' + gates);
if (st.stage !== 'edit' || st.lpNote !== HISTORY_UNREADABLE_NOTE) throw Error('no clear line on refusal: ' + st.lpNote);
if (!swapHtml(symbol, wallet, 'alice').includes('wallet-swap-note is-warn')) throw Error('the line is not shown on the card');
if (!localStorage.getItem(watchKey)) throw Error('the watch entry was dropped on a failed read');
ok = true;  // absence alone cannot clear the retained wallet intent before expiry
await swapAction(ctx, 'swap', target);
if (previews !== 0 || gates !== 1) throw Error('a history miss allowed another wallet request: previews ' + previews + ' gates ' + gates);
if (st.stage !== 'edit' || !localStorage.getItem(watchKey)) throw Error('the unresolved intent was not retained');
Date.now = realNow; process.exit(0);
""")


def test_watch_list_with_found_adopts_and_never_opens_the_wallet():
    # (d) a watched intent found in the history is adopted; no second wallet request
    _run(PREVIEW + r"""
seedWatch();
let gates = 0; let previews = 0;
globalThis.fetch = async (url) => {
  const path = String(url);
  if (path.includes('/find')) return { ok: true, status: 200, json: async () => ({ status: 'found', tx_id: 'e'.repeat(40) }) };
  if (path.includes('/verify')) return { ok: true, status: 200, json: async () => ({ status: 'confirmed', game_result: { dec_amount: '5' } }) };
  previews += 1; return { ok: true, status: 200, json: async () => previewJson };
};
globalThis.__gate = async () => { gates += 1; return { txId: 'f'.repeat(40) }; };
freshQuote();
await swapAction(ctx, 'swap', target);
for (let i = 0; i < 200 && st.stage !== 'done'; i++) await tick();
if (gates !== 0 || previews !== 0) throw Error('a second swap was requested: gates ' + gates + ' previews ' + previews);
if (st.stage !== 'done' || st.receipt.tx_id !== 'e'.repeat(40) || st.receipt.nonce !== 'cd'.repeat(8)) throw Error('not adopted: ' + st.stage);
if (localStorage.getItem(watchKey)) throw Error('the adopted intent stayed on the watch list');
Date.now = realNow;
""")


def test_expired_confirmation_does_not_loop_and_resume_reads_the_game():
    # audit finding 2: a restored confirming swap past 3 minutes reads once, turns red, and rendering never restarts it; Resume reads again
    _run(PREVIEW + r"""
let verifies = 0; let renders = 0;
globalThis.fetch = async (url) => {
  const path = String(url);
  if (path.includes('/verify')) { verifies += 1; return { ok: true, status: 200, json: async () => ({ status: 'pending' }) }; }
  return { ok: true, status: 200, json: async () => ({ status: 'not_found', covered: true }) };
};
const old = new Date(realNow() - 4 * 60000).toISOString();
Object.assign(st, { stage: 'confirming', receipt: { kind: 'swap_sell', pay: 'WOOD', receive: 'DEC', input: 10, output: 5, nonce: 'ab'.repeat(8), payload: {}, tx_id: 'd'.repeat(40), signed_at: old } });
ctx.render = () => { renders += 1; if (renders < 500) Promise.resolve().then(() => resumeSwapChecks(ctx)); };
resumeSwapChecks(ctx);
for (let i = 0; i < 100 && st.failed !== 'confirm'; i++) await tick();
for (let i = 0; i < 20; i++) await tick();
if (st.failed !== 'confirm' || st.checking) throw Error('expected a red, stopped confirmation: ' + st.failed);
if (verifies !== 1) throw Error('expected exactly one read before the limit, got ' + verifies);
if (renders > 20) throw Error('render loop: ' + renders + ' renders');
const resume = swapAction(ctx, 'verify', target);
for (let i = 0; i < 5 && verifies < 2; i++) await tick();
if (verifies < 2) throw Error('Resume did not read the game');
if (st.receipt.signed_at !== old) throw Error('Resume changed the original signing time');
st.stopped = true; st.wake?.(); await resume;
Date.now = realNow; process.exit(0);
""")
