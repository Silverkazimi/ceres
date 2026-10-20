"""the pool settlement is read by itself on a
schedule; past the limit the card is red with a clear line; a confirmed-but-pending swap past the limit fails the same way."""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
ENTRY = STATIC / "wallet-lp.js"

FLOW = r"""
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, 0, ...rest);
const symbol = 'SPS';
const wallet = { account: { DEC: '100', SPS: '600' }, swap: { symbols: ['SPS'], directions: ['swap_sell'], signing_enabled: true, rates: {}, fees: {}, two_step_sell: ['SPS'] }, regions: [] };
const ctx = { player: 'alice', account: () => 'alice', drawer: () => null, wallet: () => wallet, render: () => {}, setSigning: () => {}, reload: async () => {} };
const st = swapState('alice', symbol);
"""


def _run(js):
    run_in_module_context("(async () => {\n" + FLOW + js + "\n})().catch(error => { console.error(error && error.stack || error); process.exit(1); });", entry=ENTRY)


def test_settlement_is_read_by_itself_until_the_pool_has_paid():
    _run(r"""
let reads = 0;
globalThis.fetch = async (url) => {
 if (String(url).includes('/settle')) { reads += 1; return { ok: true, status: 200, json: async () => reads < 3 ? { status: 'settling' } : { status: 'settled', received: [{ symbol: 'DEC', amount: '4817.193' }], bridge_tx_id: 'b'.repeat(40) } }; }
 throw Error('unexpected ' + url);
};
Object.assign(st, { stage: 'settling', receipt: { kind: 'swap_sell', pay: 'SPS', receive: 'DEC', input: 600, output: null, nonce: 'ab'.repeat(8), tx_id: 'a'.repeat(40), payload: {}, funding_tx: 'c'.repeat(40), funding_at: new Date().toISOString() } });
resumeSwapChecks(ctx);
for (let i = 0; i < 50 && st.stage !== 'done'; i++) await new Promise(r => realSetTimeout(r, 5));
if (st.stage !== 'done' || reads !== 3 || st.receipt.output !== 4817.193) throw Error('loop did not settle: ' + st.stage + ' reads=' + reads);
""")


def test_five_minutes_without_settlement_fail_red_and_check_again_resumes():
    _run(r"""
let reads = 0;
globalThis.fetch = async (url) => { reads += 1; return { ok: true, status: 200, json: async () => ({ status: 'settling' }) }; };
Object.assign(st, { stage: 'settling', receipt: { kind: 'swap_sell', pay: 'SPS', receive: 'DEC', input: 600, output: null, nonce: 'ab'.repeat(8), tx_id: 'a'.repeat(40), payload: {}, funding_tx: 'c'.repeat(40), funding_at: new Date(Date.now() - 61 * 60000).toISOString() } });
resumeSwapChecks(ctx);
for (let i = 0; i < 20 && !st.failed; i++) await new Promise(r => realSetTimeout(r, 5));
if (st.failed !== 'settle' || reads !== 0) throw Error('expected a red failure without a read, got ' + st.failed + ' reads=' + reads);
const html = swapHtml(symbol, wallet, 'alice');
if (!html.includes('is-failed') || !html.includes('Not settled within 5 minutes') || !html.includes('role="alert"') || !html.includes('Check again')) throw Error('failure is not loud: ' + html);
await swapAction(ctx, 'settle', { closest: () => ({ dataset: { lpSymbol: 'SPS' } }) });
if (reads < 1) throw Error('Check again did not read the pool');
""")


def test_a_pending_confirmation_past_three_minutes_stays_amber():
    _run(r"""
globalThis.fetch = async (url) => ({ ok: true, status: 200, json: async () => ({ status: 'pending' }) });
Object.assign(st, { stage: 'confirming', receipt: { kind: 'swap_sell', pay: 'SPS', receive: 'DEC', input: 600, output: 5, nonce: 'ab'.repeat(8), tx_id: 'a'.repeat(40), payload: {}, signed_at: new Date(Date.now() - 61 * 60000).toISOString() } });
resumeSwapChecks(ctx);
for (let i = 0; i < 50 && !st.failed; i++) await new Promise(r => realSetTimeout(r, 5));
if (st.failed !== 'confirm' || st.checking) throw Error('expected a stopped pending confirmation, got ' + st.failed);
const html = swapHtml(symbol, wallet, 'alice');
if (!html.includes('is-warn') || !html.includes('Still pending') || !html.includes('Resume checking')) throw Error('pending state is not clear: ' + html);
""")
