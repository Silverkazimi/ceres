""": an old SPS claim never resurfaces; the claim intent lives only in page memory (, )."""
from ceres import routes_wallet as wallet

from .vm_context import STATIC, run_in_module_context


def test_unknown_transaction_is_not_found_not_pending():
    assert wallet.claim_result(None, "player0005", "a" * 16, "b" * 40) == {"status": "not_found"}


def test_legacy_saved_claim_is_removed_silently_and_nothing_is_stored():
    body_js = """
const store = new Map([['ceres:wallet-claim:pending:player0005', JSON.stringify({ nonce: '0'.repeat(16), total: '1.000', status: 'pending', tx_id: 'a'.repeat(40) })]]);
let writes = 0;
globalThis.localStorage = { getItem: k => store.get(k) ?? null, setItem: () => { writes += 1; }, removeItem: k => store.delete(k) };
if (loadClaimPending('player0005') !== null) throw new Error('an old saved claim must not come back');
if (store.size !== 0) throw new Error('the old saved claim must be removed');
if (writes !== 0) throw new Error('nothing may be written to storage');
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-claim.js")


def test_unconfirmed_known_claim_keeps_retrying_same_id():
    """ (behaviour, replaces the 4-try loop's timer count): a known claim the game has not answered keeps being read
    by the SAME id on the shared confirmer's schedule (frozen here), shows the automatic "still checking" line, raises no
    error, reloads no balance and writes no claim intent to storage (; only the confirmer's own row may persist)."""
    body_js = """
__setConfirmerClock(Object.fromEntries([['sleep', async () => undefined]]));  // the shared schedule, frozen
const store = new Map();
globalThis.localStorage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => { if (!String(k).startsWith('ceres:confirm:')) throw new Error('no claim storage writes: ' + k); store.set(k, v); }, removeItem: k => store.delete(k) };
const D = { player: 'player0005', generation: 0, error: '', signing: false, busy: false,
  claim: { busy: false, review: null, pending: { nonce: 'n'.repeat(16), total: '1', status: 'pending', tx_id: 'a'.repeat(40) }, checking: false } };
let reloads = 0;
(async () => {
  await claimAction(D, 'verify', () => {}, () => 'player0005', async () => { reloads += 1; });
  for (let i = 0; i < 500 && globalThis.__reads.length < 6; i += 1) await new Promise(resolve => setTimeout(resolve, 0));
  if (globalThis.__reads.length < 6) throw new Error('the known claim stopped being read after ' + globalThis.__reads.length + ' reads');
  if (globalThis.__reads.some(id => id !== 'a'.repeat(40))) throw new Error('a different id was read: ' + globalThis.__reads);
  if (D.claim.pending?.tx_id !== 'a'.repeat(40) || D.claim.pending?.phase !== 'retried' || D.claim.pending?.status !== 'pending') throw new Error('known claim must keep checking the same id: ' + JSON.stringify(D.claim.pending));
  if (D.error) throw new Error('no error text: ' + D.error);
  if (reloads !== 0) throw new Error('unconfirmed claim must not reload a balance');
  console.log('IR9545_CLAIM_KEEPS_READING');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
"""
    extra_context_js = """
context.__reads = [];
context.fetch = (url, options) => { context.__reads.push(JSON.parse(options.body).tx_id); return Promise.resolve({ ok: true, json: async () => ({ status: 'not_found' }) }); };
context.window.fetch = context.fetch;
"""
    result = run_in_module_context(body_js, entry=STATIC / "wallet-claim.js", extra_context_js=extra_context_js)
    assert "IR9545_CLAIM_KEEPS_READING" in result.stdout   # a pending promise exits 0 too; the marker proves the checks ran


def test_confirmation_after_a_not_found_poll_still_confirms():
    body_js = """
__setConfirmerClock(Object.fromEntries([['sleep', async () => undefined]]));  // the shared schedule, frozen
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const D = { player: 'player0005', generation: 0, error: '', signing: false, busy: false,
  claim: { busy: false, review: null, pending: { nonce: 'n'.repeat(16), total: '1', status: 'pending', tx_id: 'a'.repeat(40) }, checking: false } };
(async () => {
  await claimAction(D, 'verify', () => {}, () => 'player0005', async () => { throw new Error('must not reload on confirm'); });
  for (let i = 0; i < 500 && D.claim.pending?.status !== 'confirmed'; i += 1) await new Promise(resolve => setTimeout(resolve, 0));
  if (D.claim.pending?.status !== 'confirmed') throw new Error('expected confirmed, got ' + JSON.stringify(D.claim.pending));
  console.log('IR9545_CLAIM_CONFIRMED');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
"""
    extra_context_js = """
let calls = 0;
context.fetch = () => { calls += 1; return Promise.resolve({ ok: true, json: async () => (calls === 1 ? { status: 'not_found' } : { status: 'confirmed', total: '1', claimed: [] }) }); };
context.window.fetch = context.fetch;
"""
    result = run_in_module_context(body_js, entry=STATIC / "wallet-claim.js", extra_context_js=extra_context_js)
    assert "IR9545_CLAIM_CONFIRMED" in result.stdout


def test_pending_signature_caption_shows_only_while_signing():
    source = (STATIC / "regional-dec.js").read_text(encoding="utf-8")
    assert "D.tab === 'SPS' ? (D.signing && !!D.claim.pending)" in source
