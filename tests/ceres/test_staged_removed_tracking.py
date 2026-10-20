""": a staged change that leaves the store (undone, signed, done) is remembered as removed, so the other device's copy is not brought back by the sync."""
from pathlib import Path
import subprocess


def test_a_plan_that_leaves_the_store_is_tombstoned_and_a_restaged_one_is_not():
    source = (Path(__file__).parents[2] / "ceres/static/stage-plans.js").read_text()
    fn = source[source.index("export function persistStagePlans() {"):source.index("export function droppedStagePlans")].replace("export ", "")
    # include the real imported compactor and lock coordinator in this legacy sliced harness.
    account_source = (Path(__file__).parents[2] / "ceres/static/account-space.js").read_text()
    helper = account_source[account_source.index("export function compactChains"):account_source.index("// Staged changes (plans")].replace("export ", "")
    fn = helper + fn
    script = """
const assert = require('node:assert/strict');
Object.defineProperty(globalThis, 'navigator', { value: { locks: { request: async (_name, callback) => callback() } }, configurable: true }); //  B03: ordinary writes participate too.
const store = {}; globalThis.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
const LISTENERS = new Set(); const CERES_STAGE = []; const CERES_STAGED_WORKSITES = new Map();
const PLANS = { open: true, account: 'alice', revision: 0, pending: new Map(), pendingWorksites: new Map(), dropped: [], lastSig: null, warned: new Set(), removed: {}, known: new Set(), migrateKey: null };
const storeKey = a => 'k:' + a; const planKey = e => `${e.deed_uid}|${e.group_id}|${e.slot_index}`; const planFromEntry = e => ({ ...e, created_at: e.created_at });
const warnOnce = c => { throw new Error('warn ' + c); };
""" + fn + """
CERES_STAGE.push({ deed_uid: 'd', group_id: 'g', slot_index: 0, created_at: '2026-10-01T00:00:00.000Z' });
persistStagePlans();
assert.deepEqual(JSON.parse(store['k:alice']).removed, {});
CERES_STAGE.length = 0;
persistStagePlans();
const gone = JSON.parse(store['k:alice']);
assert.ok(gone.removed['d|g|0'] && !gone.plans['d|g|0']);
CERES_STAGE.push({ deed_uid: 'd', group_id: 'g', slot_index: 0, created_at: new Date(Date.now() + 5000).toISOString() });
persistStagePlans();
const back = JSON.parse(store['k:alice']);
assert.ok(back.plans['d|g|0'] && !back.removed['d|g|0']);
console.log('ok');
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_stale_tab_merges_with_what_the_other_tab_saved_instead_of_skipping_or_overwriting():
    root = Path(__file__).parents[2] / "ceres/static"
    source = (root / "stage-plans.js").read_text()
    merge = (root / "account-space.js").read_text()
    helper = merge[merge.index("export function compactChains"):merge.index("// Staged changes (plans")]
    merge = (helper + merge[merge.index("export function mergeStaged"):merge.index("export function stagedDigest")]).replace("export ", "")
    fn = source[source.index("export function persistStagePlans() {"):source.index("export function droppedStagePlans")].replace("export ", "")
    script = """
const assert = require('node:assert/strict');
Object.defineProperty(globalThis, 'navigator', { value: { locks: { request: async (_name, callback) => callback() } }, configurable: true }); //  B03: ordinary writes participate too.
const store = {}; globalThis.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
const LISTENERS = new Set(); const CERES_STAGE = []; const CERES_STAGED_WORKSITES = new Map(); let restored = 0; const restoreStagePlans = () => { restored += 1; };
const PLANS = { open: true, account: 'alice', revision: 0, pending: new Map(), pendingWorksites: new Map(), dropped: [], lastSig: null, warned: new Set(), removed: {}, known: new Set(), migrateKey: null };
const storeKey = a => 'k:' + a; const planKey = e => `${e.deed_uid}|${e.group_id}|${e.slot_index}`; const planFromEntry = e => ({ ...e });
const warnOnce = c => { throw new Error('warn ' + c); };
""" + merge + fn + """
store['k:alice'] = JSON.stringify({ version: 1, revision: 5, plans: { 'other|g|1': { created_at: '2026-10-01T00:00:00.000Z' } }, worksites: {}, removed: {} });
CERES_STAGE.push({ deed_uid: 'mine', group_id: 'g', slot_index: 0, created_at: '2026-10-01T00:00:00.000Z' });
persistStagePlans();
const out = JSON.parse(store['k:alice']);
assert.equal(out.revision, 6);
assert.deepEqual(Object.keys(out.plans).sort(), ['mine|g|0', 'other|g|1']);
assert.equal(PLANS.revision, 6);
setTimeout(() => { assert.equal(restored, 1); console.log('ok'); }, 0);
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_expired_in_flight_record_is_archived_and_an_unrestorable_worksite_plan_is_kept():
    root = Path(__file__).parents[2] / "ceres/static"
    ops = (root / "operations.js").read_text()
    read_fn = ops[ops.index("function readInFlightRecords() {"):ops.index("function writeInFlightRecord")]
    plans = (root / "stage-plans.js").read_text()
    arch = plans[plans.index("function archiveWorksitePlan"):plans.index("async function restoreWorksitePlans")]
    script = """
const assert = require('node:assert/strict');
Object.defineProperty(globalThis, 'navigator', { value: { locks: { request: async (_name, callback) => callback() } }, configurable: true }); //  B03: ordinary writes participate too.
const store = {}; globalThis.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
const IN_FLIGHT_RECORD_TTL_MS = 24 * 3600 * 1000; const inFlightStoreKey = () => 'k'; const inFlightStoreFailure = () => { throw new Error('store'); };
const PLANS = { account: 'alice' };
""" + read_fn + arch + """
const old = new Date(Date.now() - 3 * 24 * 3600 * 1000).toISOString();
store.k = JSON.stringify({ version: 1, records: { aa: { txId: 'aa', operations: [1], broadcast_at: old }, bb: { txId: 'bb', operations: [1], broadcast_at: new Date().toISOString() } } });
const out = readInFlightRecords();
assert.equal(out.records.length, 1); assert.equal(out.expired.length, 1);
assert.ok(JSON.parse(store['k:expired']).aa, 'the expired record was destroyed');
archiveWorksitePlan('d1', { target: 'x' }, 'plot_gone');
assert.equal(JSON.parse(store['ceres:stage-plan-unrestored:v1:alice'])[0].plan.target, 'x');
console.log('ok');
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_staged_sync_never_re_triggers_itself():
    """the production server  polled /api/me/staged every two seconds: the sync's own store write fired the change listener, which scheduled another sync."""
    js = (Path(__file__).parents[2] / "ceres/static/staged-sync.js").read_text()
    assert "!running && !stagedSavedHere(name)" in js      # only a REAL change of this device's staged changes starts a push
    assert "if (!stagedSavedHere(name)) schedule();" in js  # and a change queued during a sync only counts if it is real
