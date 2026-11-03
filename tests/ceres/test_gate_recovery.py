"""The gate recovers a lost HiveAuth/Keychain ack without another signature."""
from pathlib import Path
import os

from tests.ceres.vm_context import run_in_module_context


GATE = Path(os.environ["IR9545_GATE"]) if "IR9545_GATE" in os.environ else Path(__file__).parents[2] / "ceres" / "static" / "broadcast-gate.js"


def test_lost_ack_recovers_by_exact_ops_then_delivers_id_to_flow():
    result = run_in_module_context(
        r"""
(async () => {
const operations = [['custom_json', { id: 'sm_claim_rewards', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ app: 'ceres/test', n: 'a'.repeat(16) }) }]];
let walletCalls = 0, finderCalls = 0, answer;
window.hive_keychain = { requestBroadcast(_account, _ops, _key, callback) { walletCalls++; answer = callback; } };
fetch = async (url, init) => {
  if (!String(url).startsWith('/api/confirm/find?player=alice')) throw Error('unexpected request ' + url);
  finderCalls++;
  const body = JSON.parse(init.body);
  if (JSON.stringify(body.operations) !== JSON.stringify(operations)) throw Error('finder did not receive the exact ops');
  return { ok: true, status: 200, json: async () => ({ status: 'found', tx_id: 'b'.repeat(40) }) };
};
const receipt = BroadcastGate.broadcast('alice', operations).then(() => null, error => error);
await Promise.resolve();
if (walletCalls !== 1 || !answer) throw Error('one wallet request required');
// history.md shapes 12 and 16: the relay window expired after approval, no tx id.
answer({ success: false, ambiguousBroadcast: true, stillChecking: true, error: 'expired' });
const handoff = await receipt;
if (!handoff || handoff.broadcasted !== true || handoff.retryable !== false || !handoff.confirmPending) throw Error('lost ack was not handed to the shared confirmer');
const late = await handoff.pendingBroadcast;
if (late.txId !== 'b'.repeat(40) || finderCalls !== 1 || walletCalls !== 1) throw Error('lost ack was not recovered exactly once');
if (typeof confirmationState !== 'function' || confirmationState('alice', operations).txId !== 'b'.repeat(40)) throw Error('pending entry lost its tx id');
console.log('IR9545-OK');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        check=False,
        extra_context_js="""
context.navigator.locks = { request(_name, _options, callback) { return callback(); } };
const frozenNow = Date.parse('2026-11-03T09:00:00.000Z');
context.Date = class FrozenDate extends Date {
  constructor(...args) { super(...(args.length ? args : [frozenNow])); }
  static now() { return frozenNow; }
};
""",
    )
    # A pending promise exits node with 0, so success is the printed marker, never the exit code alone.
    assert result.returncode == 0 and "IR9545-OK" in result.stdout, result.stderr[-2000:]
