from pathlib import Path

from tests.ceres.vm_context import run_in_module_context


ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "ceres" / "static" / "broadcast-gate.js"
OPERATIONS = ROOT / "ceres" / "static" / "operations.js"


def test_timeout_keeps_one_request_and_recovers_late_callback() -> None:
    run_in_module_context(
        r"""
(async () => {
const timers = [];
setTimeout = (callback, delay) => {
 const timer = { callback, delay, cleared: false };
 timers.push(timer);
 return timer;
};
clearTimeout = (timer) => { timer.cleared = true; };

let callback;
let requestCount = 0;
let inclusionCount = 0;
window.hive_keychain = {
 requestBroadcast(_account, _operations, _key, handler) {
 requestCount += 1;
 callback = handler;
 },
};
fetch = async (url, init) => {
 inclusionCount += 1;
 if (!String(url).startsWith('/api/op-inclusion?player=alice')) throw new Error(`wrong inclusion URL: ${url}`);
 if (JSON.parse(init.body).tx_id !== 'late-tx') throw new Error('wrong inclusion transaction');
 return { ok: true, status: 200, json: async () => ({ included: true, block_num: 321 }) };
};

const operations = [['custom_json', { id: 'sm_stake_change', json: JSON.stringify({ n: 'nonce-ir5890', deed_uid: 'D-1' }) }]];
const first = BroadcastGate.broadcast('Alice', operations).then(
 () => { throw new Error('the timeout must enter pending verification'); },
 (error) => error,
);
await Promise.resolve();
if (requestCount !== 1 || typeof callback !== 'function') throw new Error('Keychain was not called exactly once');
timers.find((timer) => timer.delay === KEYCHAIN_RESPONSE_TIMEOUT_MS).callback();
const timeout = await first;
if (!timeout.keychainTimedOut || !timeout.ambiguousBroadcast || timeout.broadcasted !== true || timeout.retryable !== false) {
 throw new Error('timeout was not classified as an ambiguous pending broadcast');
}

callback({ success: true, result: { id: 'late-tx' }, data: { key: 'Posting' } });
const late = await timeout.pendingBroadcast;
if (late.txId !== 'late-tx' || late.blockNum !== null) throw new Error('late callback did not retain broadcast evidence');
if (requestCount !== 1 || inclusionCount !== 0) throw new Error('late completion resent or blocked on inclusion');

const duplicate = await BroadcastGate.broadcast('alice', [['custom_json', {
 id: 'sm_stake_change',
 json: JSON.stringify({ n: 'nonce-ir5890', deed_uid: 'D-CHANGED' }),
}]]).then(
 () => null,
 (error) => error,
);
if (!duplicate?.duplicateBroadcast || duplicate.retryable !== false) throw new Error('same account/nonce was not refused');
if (requestCount !== 1) throw new Error('same account/nonce reached requestBroadcast twice');
})().then(() => process.exit(0), (error) => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js="context.navigator.locks = { request(_name, _options, callback) { return callback(); } };",
    )


def test_five_uncertain_rows_recover_by_probe_and_wallet_is_reached() -> None:
    run_in_module_context(
        r"""
(async () => {
const operation = nonce => ['custom_json', {
 id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'],
 json: JSON.stringify({ n: nonce }),
}];
const rows = Array.from({ length: 5 }, (_, index) => {
 const id = JSON.stringify(['alice', 'custom_json', 'sm_land_operation', `wedged-${index}`]);
 return { id, claimIds: [id], count: 1, txId: null, createdAt: new Date().toISOString() };
});
localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify(rows.map(row => row.id)));
localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify(rows));
let probes = 0;
fetch = async () => { probes += 1; return { ok: true, json: async () => ({ included: false, block_num: null }) }; };
const recovered = await recoverPendingBroadcastRequests('alice', 1);
if (recovered.blocked || recovered.released !== 5 || recovered.remaining !== 0 || probes !== 5) throw Error('five-row recovery did not clear the proven-absent requests');
let walletCalls = 0;
window.hive_keychain = { requestBroadcast(_account, _operations, _key, callback) {
 walletCalls += 1;
 callback({ success: true, result: { id: 'recovered-tx' }, data: { key: 'Posting' } });
} };
await BroadcastGate.broadcast('alice', [operation('fresh-after-recovery')]);
if (walletCalls !== 1) throw Error('wallet was not reached after recovery');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js="context.navigator.locks = { request(_name, _options, callback) { return callback(); } };",
    )


def test_old_rows_keep_their_claims_but_stop_blocking_new_signing() -> None:
    """age never releases a claim, but it does free the budget.

 Keeping a request's claim is what stops the same signed envelope being sent twice.
 Keeping it in the per-block budget forever is what bricked the maintainer's account: a
 request nobody can look up would otherwise refuse every later signing attempt for
 good. After the window in which Hive would certainly have included it, it stops
 counting against the budget and the claims stay.
    """
    run_in_module_context(
        r"""
(async () => {
const operation = nonce => ['custom_json', {
 id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'],
 json: JSON.stringify({ n: nonce }),
}];
const rows = Array.from({ length: 5 }, (_, index) => {
 const id = JSON.stringify(['alice', 'custom_json', 'sm_land_operation', `expired-${index}`]);
 return { id, claimIds: [id], count: 1, txId: null, createdAt: new Date(Date.now() - 600001).toISOString() };
});
localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify(rows.map(row => row.id)));
localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify(rows));
fetch = async () => ({ ok: true, json: async () => ({ included: null, block_num: null }) });
let walletCalls = 0;
window.hive_keychain = { requestBroadcast(_account, _operations, _key, callback) {
 walletCalls += 1;
 callback({ success: true, result: { id: 'fresh-tx' }, data: { key: 'Posting' } });
} };
const blocked = await BroadcastGate.broadcast('alice', [operation('fresh')]).catch(error => error);
if (walletCalls !== 1 || blocked?.pendingWalletRequests) throw Error(': an unjudgeable old request still blocks a new signing attempt');
const claims = JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice'));
if (!rows.every(row => claims.includes(row.id))) throw Error('age released exact-envelope claims');
const kept = JSON.parse(localStorage.getItem('ceres:pending-custom-ops:v1:alice'));
if (!rows.every(row => kept.some(entry => entry.id === row.id))) throw Error('age deleted admission rows');
if (kept.filter(entry => rows.some(row => row.id === entry.id)).every(entry => entry.budgetActive)) throw Error(': no unjudgeable row was retired from the budget');;
const duplicate = await BroadcastGate.broadcast('alice', [operation('expired-0')]).catch(error => error);
if (!duplicate.duplicateBroadcast || walletCalls !== 1) throw Error('the exact earlier envelope must still be refused as a replay');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js="context.navigator.locks = { request(_name, _options, callback) { return callback(); } };",
    )


def test_one_inconclusive_row_refuses_exact_replay_below_budget_limit() -> None:
    run_in_module_context(
        r"""
(async () => {
const operation = ['custom_json', {
 id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'],
 json: JSON.stringify({ n: 'one-row-replay' }),
}];
const id = JSON.stringify(['alice', 'custom_json', 'sm_land_operation', 'one-row-replay']);
localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify([id]));
localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify([{ id, claimIds: [id], count: 1, txId: null, createdAt: new Date().toISOString() }]));
fetch = async () => ({ ok: true, json: async () => ({ included: null, block_num: null }) });
let walletCalls = 0;
window.hive_keychain = { requestBroadcast() { walletCalls += 1; } };
const duplicate = await BroadcastGate.broadcast('alice', [operation]).catch(error => error);
if (!duplicate.duplicateBroadcast || duplicate.retryable !== false || walletCalls !== 0) throw Error('below-limit exact replay was not refused before the wallet');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js="context.navigator.locks = { request(_name, _options, callback) { return callback(); } };",
    )


def test_inconclusive_recovery_still_blocks_without_rebroadcast() -> None:
    run_in_module_context(
        r"""
(async () => {
const nativeSetTimeout = setTimeout; // run the one-second budget checks at once
setTimeout = (callback, delay) => delay === 1000 ? Promise.resolve().then(callback) : nativeSetTimeout(callback, delay);
const operation = nonce => ['custom_json', {
 id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'],
 json: JSON.stringify({ n: nonce }),
}];
const rows = Array.from({ length: 5 }, (_, index) => {
 const id = JSON.stringify(['alice', 'custom_json', 'sm_land_operation', `unclear-${index}`]);
 return { id, claimIds: [id], count: 1, txId: null, createdAt: new Date().toISOString() };
});
localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify(rows.map(row => row.id)));
localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify(rows));
fetch = async () => ({ ok: false, json: async () => ({}) });
const recovered = await recoverPendingBroadcastRequests('alice', 1);
if (!recovered.blocked || recovered.pending !== 5 || recovered.remaining !== 5) throw Error('inconclusive recovery was not retained');
let walletCalls = 0;
window.hive_keychain = { requestBroadcast() { walletCalls += 1; } };
const blocked = await BroadcastGate.broadcast('alice', [operation('must-not-send')]).catch(error => error);
if (!blocked.pendingWalletRequests || !blocked.message.includes('Ceres keeps checking')) throw Error(': the refusal must say Ceres checks it itself, with no action for the maintainer');
if (walletCalls !== 0) throw Error('inconclusive recovery rebroadcast a request');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js="context.navigator.locks = { request(_name, _options, callback) { return callback(); } };",
    )


def test_two_staged_transactions_sign_in_sequence_before_modal_closes() -> None:
    run_in_module_context(
        r"""
(async () => {
const target = { scope: 'portfolio' };
const review = {
 payload: { account: 'alice', chunks: [{ operations: [] }, { operations: [] }] },
 stagedSnapshot: [{ deed_uid: 'D-1' }, { deed_uid: 'D-2' }],
};
CERES_PREVIEWS.set(previewKey(target), review);
S.CERES_CHECKOUT_OPEN = true;
walletReady = () => true;
freshReviewForSigning = async () => review;
renderConfirmationBanner = () => {};
setVerificationNotice = () => {};
markBroadcastedChunk = () => {};
prepareSigningBatch = async () => null;
verifyBroadcast = async () => {};
let walletCalls = 0;
requestSignatureThroughGate = async (_account, _operations, callback) => {
 walletCalls += 1;
 callback({ success: true, result: { id: `tx-${walletCalls}` } });
 if (walletCalls === 1 && !S.CERES_CHECKOUT_OPEN) throw Error('modal closed before the second staged transaction');
 return { txId: `tx-${walletCalls}`, blockNum: null, submitted: Promise.resolve() };
};
await confirmOperationBroadcast(target);
if (walletCalls !== 2 || S.CERES_CHECKOUT_OPEN) throw Error('two staged transactions did not complete in sequence');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=OPERATIONS,
    )


def test_blocked_second_transaction_stays_staged_with_visible_recovery() -> None:
    run_in_module_context(
        r"""
(async () => {
const target = { scope: 'portfolio' };
const staged = [{ deed_uid: 'D-1' }, { deed_uid: 'D-2' }];
const review = { payload: { account: 'alice', chunks: [{ operations: [] }, { operations: [] }] }, stagedSnapshot: staged };
CERES_PREVIEWS.set(previewKey(target), review);
S.CERES_CHECKOUT_OPEN = true;
walletReady = () => true;
freshReviewForSigning = async () => review;
renderConfirmationBanner = () => {};
setVerificationNotice = () => {};
markBroadcastedChunk = () => {};
prepareSigningBatch = async () => null;
verifyBroadcast = async () => {};
let walletCalls = 0;
requestSignatureThroughGate = async (_account, _operations, callback) => {
 walletCalls += 1;
 if (walletCalls === 1) {
 callback({ success: true, result: { id: 'tx-first' } });
 return { txId: 'tx-first', blockNum: null, submitted: Promise.resolve() };
 }
 const error = Error('An earlier wallet request is still being checked with Hive. This transaction was not sent. Ceres keeps checking and this clears itself.');
 Object.assign(error, { broadcasted: false, retryable: true, pendingWalletRequests: 5, requiredCustomOperations: 1 });
 throw error;
};
await confirmOperationBroadcast(target);
const blockedControl = operationSignButtonHtml(target, { ...review, pendingWalletBlock: { message: 'Ceres is checking', count: 5, requiredCount: 1, checking: true } }, { surface: 'changes' });
if (walletCalls !== 2 || !S.CERES_CHECKOUT_OPEN) throw Error('blocked second transaction closed the modal');
if (review.stagedSnapshot !== staged || review.stagedSnapshot.length !== 2) throw Error('blocked second transaction was dropped from staging');
if (blockedControl.includes('Check pending requests') || blockedControl.includes('recover-pending') || !blockedControl.includes('disabled')) throw Error(': a blocked transaction must render a disabled Sign, never a recovery control the maintainer has to press');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=OPERATIONS,
    )


def test_timeout_uses_existing_check_again_state_without_reenabling_sign() -> None:
    gate = GATE.read_text(encoding="utf-8")
    operations = OPERATIONS.read_text(encoding="utf-8")

    assert gate.count("requestWalletBroadcast(account, operations") == 1
    assert "error.keychainTimedOut = true;" in gate
    assert "error.ambiguousBroadcast = true;" in gate
    assert "error.broadcasted = true;" in gate
    assert "error.retryable = false;" in gate
    assert "claimBroadcastRequest(account, operations)" in gate
    assert "error.pendingBroadcast" in operations
    assert "review.broadcastPending = true;" in operations
    assert "review.broadcasting || review.freshnessCheck || review.broadcastPending" in operations
    assert "action: { id: 'retry_verification', label: 'Check again' }" in operations
    assert "if (error?.retryable && broadcastButton)" in operations

def test_retry_reaches_the_wallet_after_a_request_the_chain_never_saw() -> None:
    """his Changes signing died here.

 A request that Hive says never arrived must free its identity, not only its budget
 slot. Releasing the slot alone left the identity claimed, so the next press of Sign
 was refused in the browser as a duplicate and the wallet was never contacted.
    """
    run_in_module_context(
        r"""
(async () => {
const account = 'alice';
const operations = [['custom_json', { id: 'sm_land_operation', json: JSON.stringify({ n: 'nonce-1' }), required_posting_auths: [account] }]];
let reachedWallet = 0;
requestWalletBroadcast = async () => { reachedWallet += 1; return { txId: null }; };
globalThis.fetch = async () => ({ ok: true, json: async () => ({ included: false, block_num: null }) });
const claims = claimBroadcastRequest(account, operations);
const row = { id: claims[0], claimIds: claims, count: 1, txId: null, instanceId: 'i-1', budgetActive: true, createdAt: new Date(Date.now() - 3600000).toISOString() };
saveCustomOps(account, [row]);
if (!resolveAdmission(account, row, false, { releaseClaim: true })) throw Error('the proven-absent request was not resolved');
const stored = JSON.parse(globalThis.localStorage.getItem('ceres:broadcast-claims:v1:' + account) || '[]');
if (stored.length !== 0) throw Error(': the identity stayed claimed, so a retry can never reach the wallet');
claimBroadcastRequest(account, operations);
if (reachedWallet !== 0) throw Error('nothing should have been sent in this test');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
    )


def test_unjudgeable_old_request_stops_blocking_but_keeps_its_claim() -> None:
    """his account was held hostage by a request nobody can look up.

 An operation whose payload carries no nonce has no checkable identity, so the chain
 probe answers "unknown" forever. Once such a request is older than the window in
 which Hive would certainly have included it, it must stop counting against the
 per-block budget -- while keeping its claim, so that exact envelope can never be
 sent twice.
    """
    run_in_module_context(
        r"""
(async () => {
const account = 'alice';
globalThis.fetch = async () => ({ ok: true, json: async () => ({ included: null, block_num: null }) });
const stale = { id: 'stuck', claimIds: ['stuck'], count: 4, txId: null, instanceId: 'i-old',
 budgetActive: true, createdAt: new Date(Date.now() - 3600000).toISOString() };
saveCustomOps(account, [stale]);
globalThis.localStorage.setItem('ceres:broadcast-claims:v1:' + account, JSON.stringify(['stuck']));
let reached = 0;
window.hive_keychain = { requestBroadcast(_a, _o, _k, callback) { reached += 1; callback({ success: true, result: { id: 'tx-new' }, data: { key: 'Posting' } }); } };
const operations = [['custom_json', { id: 'sm_land_operation', json: JSON.stringify({ n: 'fresh-nonce' }), required_posting_auths: [account] }]];
await BroadcastGate.broadcast(account, operations);
if (reached !== 1) throw Error(': signing is still blocked by a request the chain cannot judge');
const claims = JSON.parse(globalThis.localStorage.getItem('ceres:broadcast-claims:v1:' + account) || '[]');
if (!claims.includes('stuck')) throw Error(': the stuck request lost its replay protection');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js="context.navigator.locks = { request(_name, _options, callback) { return callback(); } };",
    )
