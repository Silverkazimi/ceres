""": the pool settlement is read by itself on a schedule.  (IN-0706, "technically impossible to fail") replaced the
red TTL stop and its Check again with the shared confirmer: no limit ends the reads and no manual control exists."""
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


def test_an_hour_old_unsettled_swap_keeps_reading_the_pool_with_no_manual_control():
    # replaces 's red 5-minute stop + Check again: the settlement is a second step through the shared wait.
    _run(r"""
let reads = 0;
globalThis.fetch = async (url) => { reads += 1; return { ok: true, status: 200, json: async () => ({ status: 'settling' }) }; };
Object.assign(st, { stage: 'settling', receipt: { kind: 'swap_sell', pay: 'SPS', receive: 'DEC', input: 600, output: null, nonce: 'ab'.repeat(8), tx_id: 'a'.repeat(40), payload: {}, funding_tx: 'c'.repeat(40), funding_at: new Date(Date.now() - 61 * 60000).toISOString() } });
resumeSwapChecks(ctx);
for (let i = 0; i < 50 && reads < 2; i++) await new Promise(r => realSetTimeout(r, 5));
if (st.failed || st.stage !== 'settling' || reads < 2) throw Error('expected the pool to be read on, got ' + st.failed + ' reads=' + reads);
const html = swapHtml(symbol, wallet, 'alice');
if (/is-failed|Not settled within|Check again|Stop checking|Resume checking|further back/.test(html)) throw Error('red stop or manual control: ' + html);
if (!html.includes('Ceres reads the pool by itself')) throw Error('automatic line missing: ' + html);
console.log('IR9545-OK');
""")


def test_a_pending_confirmation_an_hour_old_keeps_its_automatic_amber_line():
    _run(r"""
let reads = 0;
globalThis.fetch = async (url) => { reads += 1; return { ok: true, status: 200, json: async () => ({ status: 'pending' }) }; };
Object.assign(st, { stage: 'confirming', receipt: { kind: 'swap_sell', pay: 'SPS', receive: 'DEC', input: 600, output: 5, nonce: 'ab'.repeat(8), tx_id: 'a'.repeat(40), payload: {}, signed_at: new Date(Date.now() - 61 * 60000).toISOString() } });
resumeSwapChecks(ctx);
for (let i = 0; i < 50 && reads < 2; i++) await new Promise(r => realSetTimeout(r, 5));
if (st.failed || !st.checking || reads < 2) throw Error('expected the check to keep reading, got ' + st.failed + ' reads=' + reads);
const html = swapHtml(symbol, wallet, 'alice');
if (!html.includes('is-warn') || !html.includes('Ceres keeps checking by itself') || /Still pending|Resume checking|Stop checking/.test(html)) throw Error('pending state: ' + html);
console.log('IR9545-OK');
process.exit(0);
""")
