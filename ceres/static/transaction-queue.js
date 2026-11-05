import { requestWalletSignature, walletCanSignTransaction } from './wallet.js';
import { announceBegin, beginAttempt, confirmationState, markAnswered, waitThrough } from './shared-confirmer.js';

const QUEUE_PREFIX = 'ceres:signed-queue:v1:';
// the Hive nodes Ceres reads from, in order; kept identical to ceres/hive_rpc.py HIVE_RPC_NODES (a test
// compares them) and allowed in security_headers.py CONNECT_SRC. Broadcasts and signing-head reads stay on the first
// node only; failover is for the inclusion probe and the lag reads.
export const HIVE_RPC_NODES = ['https://api.hive.blog', 'https://api.openhive.network', 'https://api.deathwing.me'];
const QUEUE_RPC = HIVE_RPC_NODES[0];
const TERMINAL_QUEUE_STATES = new Set(['included', 'rejected', 'expired', 'cancelled', 'blocked']);
const QUEUE_PUMPS = new Map();
const QUEUE_SIGN_TIMEOUT = 120000;
export const SIGNED_QUEUE_EXPIRY_MS = 10 * 60 * 1000;
const QUEUE_HEAD_CACHE_MS = 30000;
const queueHeadCache = new Map();
const queueSleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
const cloneQueueValue = (value) => JSON.parse(JSON.stringify(value));
const canonicalQueueValue = (value) => JSON.stringify(value, function (_key, item) {
  return item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item;
});
function queueAccount(account) {
  const value = String(account || '').toLowerCase();
  if (!/^[a-z][a-z0-9.-]{2,15}$/.test(value)) throw new Error('Invalid transaction account.');
  return value;
}
function queueLock(name, callback) {
  if (!globalThis.navigator?.locks?.request) throw new Error('Safe transaction coordination requires browser Web Locks.');
  return navigator.locks.request(name, { mode: 'exclusive' }, callback);
}
export function signedQueueRows(account) {
  const data = JSON.parse(localStorage.getItem(QUEUE_PREFIX + queueAccount(account)) || '[]');
  if (!Array.isArray(data) || data.some(row => !row || !/^[0-9a-f]{40}$/.test(row.txId) || row.account !== queueAccount(account))) {
    throw new Error('The saved transaction queue could not be read. Restore site storage before signing.');
  }
  return data;
}
async function updateQueueRow(account, txId, update) {
  return queueLock(`ceres:queue-journal:${account}`, () => {
    const rows = signedQueueRows(account);
    const index = rows.findIndex(row => row.txId === txId);
    const previous = index < 0 ? null : rows[index];
    const next = typeof update === 'function' ? update(previous) : { ...previous, ...update };
    if (!next) return previous;
    next.updatedAt = new Date().toISOString();
    if (index < 0) rows.push(next); else rows[index] = next;
    // Never discard an unresolved or signed transaction to make storage room.
    const cutoff = Date.now() - 30 * 86400000;
    localStorage.setItem(QUEUE_PREFIX + account, JSON.stringify(rows.filter(row => !TERMINAL_QUEUE_STATES.has(row.state) || Date.parse(row.updatedAt) >= cutoff)));
    // waiters learn of every row change at once (no polling loop, no deadline).
    try { globalThis.window?.dispatchEvent?.(new CustomEvent('ceres:queue-row', { detail: { account, txId: next.txId, state: next.state } })); } catch (_) {}
    return next;
  });
}
export async function queueRpc(method, params = [], node = QUEUE_RPC) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(node, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }), signal: controller.signal, credentials: 'omit' });
    if (!response.ok) throw new Error('Hive connection unavailable.');
    const data = await response.json();
    if (data.error) {
      const error = new Error('Hive rejected the request.');
      error.rpcError = data.error;
      throw error;
    }
    if (!Object.prototype.hasOwnProperty.call(data, 'result')) throw new Error('Hive returned an incomplete response.');
    return data.result;
  } finally { clearTimeout(timer); }
}
// a read with node failover. A node that cannot be reached (or answers without a usable response) is skipped
// for the next one; a node that answers with a JSON-RPC error has spoken, so that error is returned as is.
export async function hiveReadRpc(method, params = [], nodes = HIVE_RPC_NODES) {
  let lastError = new Error('Hive connection unavailable.');
  for (const node of nodes) {
    try { return await queueRpc(method, params, node); }
    catch (error) { if (error?.rpcError) throw error; lastError = error; }
  }
  throw lastError;
}
// transaction_status_api.find_transaction status -> what Ceres may say about it. Only `expired_irreversible` proves the
// transaction can never be included; `expired_reversible` may still be undone by a fork, so it stays "still checking".
export function findTransactionChainStatus(status) {
  switch (status) {
    case 'within_reversible_block': case 'within_irreversible_block': return 'in_block';
    case 'within_mempool': return 'pending';
    case 'expired_irreversible': return 'dropped';
    case 'expired_reversible': return 'expiring';
    default: return 'unknown';   // 'unknown', 'too_old' or anything new: inconclusive
  }
}
// A fresh review mints new operation nonces (fides_land/harvest.py nonce()), so the redo is accepted while this row's own
// operations keep their claim and stay refused.
export const QUEUE_DROPPED_MESSAGE = 'Not included: Hive reports this transaction expired. Start the harvest again to send a new one; Ceres never resends it.';
export const QUEUE_DROPPED_MESSAGE_OTHER = 'Not included: Hive reports this transaction expired. Review the action again to send a new one; Ceres never resends it.';
const droppedMessage = row => (row.runKind === 'harvest' || row.context?.harvestAttemptId ? QUEUE_DROPPED_MESSAGE : QUEUE_DROPPED_MESSAGE_OTHER);
export function reviewedActiveLockOperation(account, operations, review) {
  if (operations?.length !== 1 || !review || !['sm_lock_assets', 'sm_unlock_assets'].includes(review.id)) return false;
  const [kind, body] = operations[0] || [];
  if (kind !== 'custom_json' || body?.id !== review.id || body.json !== review.json
    || canonicalQueueValue(body.required_auths) !== canonicalQueueValue([account])
    || canonicalQueueValue(body.required_posting_auths) !== '[]') return false;
  let payload;
  try { payload = JSON.parse(body.json); } catch (_) { return false; }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return false;
  const keys = Object.keys(payload).sort().join(',');
  const expected = body.id === 'sm_lock_assets' ? 'app,asset_ids,lock_days,n,type' : 'app,asset_ids,n,type';
  return keys === expected && payload.type === 'item'
    && payload.app === currentCeresAppIdentity()
    && Array.isArray(payload.asset_ids) && payload.asset_ids.length > 0 && payload.asset_ids.length <= 100
    && new Set(payload.asset_ids).size === payload.asset_ids.length
    && payload.asset_ids.every(uid => /^I-[0-9]+-[A-Za-z0-9]+$/.test(uid))
    && /^[0-9a-f-]{36}$/i.test(payload.n || '')
    && (body.id !== 'sm_lock_assets' || payload.lock_days === 3);
}

export function currentCeresAppIdentity() {
  const version = document.getElementById('board-version')?.textContent?.trim().match(/^v?(\d+\.\d+\.\d+)$/)?.[1];
  if (!version) throw new Error('The loaded Ceres version is unavailable.');
  return `ceres/${version}`;
}
function validateQueueOperations(account, operations, authority, reviewedActiveLock = null, maxOpsPerTx = 4) {
  if (!['Posting', 'Active'].includes(authority) || !Array.isArray(operations) || !operations.length || operations.length > maxOpsPerTx) throw new Error('Unsupported transaction shape.');
  for (const operation of operations) {
    const [kind, body] = operation || [];
    if (kind !== 'custom_json' || !body || Object.keys(body).sort().join(',') !== 'id,json,required_auths,required_posting_auths'
      || typeof body.id !== 'string' || new TextEncoder().encode(body.id).length > 32 || typeof body.json !== 'string'
      || new TextEncoder().encode(body.json).length > 8192) throw new Error('Unsupported custom operation.');
    const payload = JSON.parse(body.json);
    if (!payload || typeof payload !== 'object' || !(payload.n || payload.params?.n)) throw new Error('A reviewed operation nonce is required.');
    const auth = authority === 'Posting' ? body.required_posting_auths : body.required_auths;
    const other = authority === 'Posting' ? body.required_auths : body.required_posting_auths;
    if (canonicalQueueValue(auth) !== canonicalQueueValue([account]) || canonicalQueueValue(other) !== '[]') throw new Error('The transaction authority does not match the reviewed account.');
    if (authority === 'Active' && !reviewedActiveLockOperation(account, operations, reviewedActiveLock)
      && !(operations.length === 1 && body.id === 'sm_sps' && payload.action === 'token_transfer'
      && payload.params?.token === 'SPS' && /^(lpsid|lpdid):[1-9][0-9]*$/.test(payload.params.memo || '')
      && Number.isFinite(payload.params.qty) && payload.params.qty > 0 && typeof payload.params.to === 'string' && typeof payload.params.n === 'string')) {
      throw new Error('Active authority is only available for reviewed SPS funding or deed lock operations.');
    }
  }
}
// Hive transaction id hashes the unsigned binary transaction (custom_json opcode 18).
export function unsignedTransactionBytes(transaction) {
  const bytes = [];
  const uint = (number, size) => { for (let i = 0; i < size; i++) bytes.push((number / 2 ** (8 * i)) & 255); };
  const variable = (number) => { do { let byte = number & 127; number >>>= 7; if (number) byte |= 128; bytes.push(byte); } while (number); };
  const string = (value) => { const encoded = new TextEncoder().encode(value); variable(encoded.length); bytes.push(...encoded); };
  const vector = (values) => { variable(values.length); values.forEach(string); };
  uint(transaction.ref_block_num, 2); uint(transaction.ref_block_prefix, 4);
  uint(Date.parse(transaction.expiration + 'Z') / 1000, 4);
  variable(transaction.operations.length);
  for (const [kind, body] of transaction.operations) {
    if (kind !== 'custom_json') throw new Error('Unsupported transaction serialization.');
    variable(18); vector(body.required_auths); vector(body.required_posting_auths); string(body.id); string(body.json);
  }
  if (transaction.extensions.length) throw new Error('Unsupported transaction extensions.');
  variable(0);
  return new Uint8Array(bytes);
}
export async function queueTransactionId(transaction) {
  const hash = await crypto.subtle.digest('SHA-256', unsignedTransactionBytes(transaction));
  return Array.from(new Uint8Array(hash).slice(0, 20), byte => byte.toString(16).padStart(2, '0')).join('');
}
function queueHeadIsFresh(props) {
  return Number.isInteger(props?.head_block_number) && /^[0-9a-f]{40}$/.test(props.head_block_id || '')
    && Number.isFinite(Date.parse(props.time + 'Z')) && Math.abs(Date.now() - Date.parse(props.time + 'Z')) <= 60000;
}
export async function prepareQueueBatch(account, operationGroups, authority = 'Posting', metadata = {}) {
  account = queueAccount(account);
  if (!walletCanSignTransaction(account)) return null;
  const maxOpsPerTx = Number.isInteger(metadata?.maxOpsPerTx) && metadata.maxOpsPerTx > 0
    ? metadata.maxOpsPerTx : 4;
  operationGroups.forEach(operations => validateQueueOperations(account, operations, authority, metadata?.reviewedActiveLock, maxOpsPerTx));
  const runId = typeof metadata?.runId === 'string' && metadata.runId ? metadata.runId : null;
  const now = Date.now();
  for (const [cachedRunId, value] of queueHeadCache) {
    if (now - value.cachedAt >= QUEUE_HEAD_CACHE_MS) queueHeadCache.delete(cachedRunId);
  }
  const cached = runId ? queueHeadCache.get(runId) : null;
  let props = metadata?.headProperties || (cached && now - cached.cachedAt >= 0 && now - cached.cachedAt < QUEUE_HEAD_CACHE_MS && queueHeadIsFresh(cached.props) ? cached.props : null);
  if (metadata?.headProperties && !queueHeadIsFresh(props)) throw new Error('Counted Hive head is stale. Check this routing bundle before signing.');
  if (!props) {
    props = await queueRpc('condenser_api.get_dynamic_global_properties');
    if (!queueHeadIsFresh(props)) throw new Error('Hive head information is stale. Refresh before signing.');
    if (runId) queueHeadCache.set(runId, { props, cachedAt: Date.now() });
  }
  const prefixBytes = props.head_block_id.slice(8, 16).match(/../g).map(byte => parseInt(byte, 16));
  const prefix = prefixBytes.reduce((sum, byte, index) => sum + byte * 2 ** (8 * index), 0);
  const batchId = crypto.randomUUID();
  const runKind = ['plan', 'harvest'].includes(metadata?.runKind) ? metadata.runKind : null;
  const prepared = [];
  for (const operations of operationGroups) {
    const transaction = { ref_block_num: props.head_block_number & 65535, ref_block_prefix: prefix,
      expiration: new Date(Date.parse(props.time + 'Z') + SIGNED_QUEUE_EXPIRY_MS).toISOString().slice(0, 19),
      operations: cloneQueueValue(operations), extensions: [] };
    prepared.push({ account, authority, maxOpsPerTx, transaction, txId: await queueTransactionId(transaction), batchId, ...(metadata?.reviewedActiveLock ? { reviewedActiveLock: cloneQueueValue(metadata.reviewedActiveLock) } : {}), ...(runId ? { runId } : {}), ...(runKind ? { runKind } : {}),
      dependsOn: prepared.at(-1)?.txId || null });
  }
  return prepared;
}
function queueError(message, row, unsent = false) {
  const error = new Error(message);
  error.txId = row?.txId;
  error.broadcasted = !unsent;
  error.ambiguousBroadcast = !unsent;
  error.retryable = false;
  return error;
}
function queueExpired(row) { return Date.parse(row.transaction.expiration + 'Z') <= Date.now() + 5000; }
function validateSignedEnvelope(prepared, signed) {
  if (!signed || typeof signed !== 'object') throw new Error('The wallet returned no signed transaction.');
  const { signatures, ...unsigned } = signed;
  if (canonicalQueueValue(unsigned) !== canonicalQueueValue(prepared.transaction)
    || !Array.isArray(signatures) || !signatures.length || signatures.length > 5
    || signatures.some(signature => !/^[0-9a-f]{130}$/i.test(signature))) throw new Error('The wallet returned a transaction different from the reviewed request. Nothing was sent.');
  if (queueExpired(prepared)) throw new Error('This signature expired before submission. Review again; nothing was sent.');
}
function queueOperationIdentities(account, operations) {
  return operations.map(([type, body]) => {
    const payload = JSON.parse(body.json);
    return JSON.stringify([account, type, body.id, String(payload.n || payload.params?.n)]);
  });
}
export function withQueueSigningLock(account, callback) {
  return queueLock(`ceres:sign:${queueAccount(account)}`, callback);
}
export function hasSignedQueueClaim(account, operations) {
  account = queueAccount(account);
  const identities = new Set(queueOperationIdentities(account, operations));
  return signedQueueRows(account).some(row => !['cancelled', 'expired', 'rejected', 'blocked'].includes(row.state)
    && queueOperationIdentities(account, row.transaction.operations).some(identity => identities.has(identity)));
}
export async function signQueuedTransaction(prepared) {
  const { account, txId } = prepared;
  return withQueueSigningLock(account, async () => {
    const existingRows = signedQueueRows(account);
    const identities = new Set(queueOperationIdentities(account, prepared.transaction.operations));
    const oldClaims = JSON.parse(localStorage.getItem(`ceres:broadcast-claims:v1:${encodeURIComponent(account)}`) || '[]');
    if (!Array.isArray(oldClaims)) throw queueError('Safe broadcast tracking is unavailable.', prepared, true);
    const duplicate = existingRows.some(row => row.txId === txId || (!['cancelled', 'expired', 'rejected', 'blocked'].includes(row.state)
      && queueOperationIdentities(account, row.transaction.operations).some(identity => identities.has(identity))))
      || oldClaims.some(identity => identities.has(identity));
    if (duplicate) throw queueError('This operation already has a transaction record. Open Receipts to recover it; do not sign it again.', prepared);
    validateQueueOperations(account, prepared.transaction.operations, prepared.authority, prepared.reviewedActiveLock, prepared.maxOpsPerTx);
    if (await queueTransactionId(prepared.transaction) !== txId || queueExpired(prepared)) throw queueError('The prepared transaction changed or expired. Review again.', prepared, true);
    await updateQueueRow(account, txId, { ...cloneQueueValue(prepared), state: 'signing', createdAt: new Date().toISOString() });
    let signed;
    try {
      let attempt = confirmationState(account, prepared.transaction.operations);
      if (!attempt?.beginToken) {
        attempt = beginAttempt(account, prepared.transaction.operations, { expiration: prepared.transaction.expiration });
        if (!await announceBegin(account, prepared.transaction.operations)) throw new Error('The signing attempt could not be recorded. Nothing was sent.');
      }
      const begun = confirmationState(account, prepared.transaction.operations);
      await updateQueueRow(account, txId, { attemptId: attempt.attemptId, beginToken: begun?.beginToken || null,
        actionId: begun?.actionId || null });
      signed = await new Promise((resolve, reject) => {
        let finished = false;
        const timer = setTimeout(() => { finished = true; reject(new Error('The signing request timed out. No transaction was broadcast; any late signature will be discarded.')); }, QUEUE_SIGN_TIMEOUT);
        try {
          requestWalletSignature(account, cloneQueueValue(prepared.transaction), prepared.authority, response => {
            if (finished) return;
            finished = true; clearTimeout(timer);
            try { markAnswered(account, prepared.transaction.operations, { attempt: attempt.attempt, closed: true }); } catch (_) {}
            if (response?.error || response?.success === false) { reject(new Error('The wallet did not approve this signature. Nothing was sent.')); return; }
            if (response?.data?.key && String(response.data.key).toLowerCase() !== prepared.authority.toLowerCase()) { reject(new Error('The wallet used a different signing authority. Nothing was sent.')); return; }
            resolve(response?.result);
          });
        } catch (error) { finished = true; clearTimeout(timer); reject(error); }
      });
      validateSignedEnvelope(prepared, signed);
      await updateQueueRow(account, txId, { state: 'queued', signedTransaction: cloneQueueValue(signed), message: 'Signed — queued for broadcast.' });
    } catch (error) {
      await updateQueueRow(account, txId, { state: 'cancelled', message: error.message, signedTransaction: null });
      throw queueError(error.message, prepared, true);
    }
    resumeSignedQueue(account);
    const submitted = waitForQueuedSubmission(account, txId);
    submitted.catch(() => {});
    return { txId, blockNum: null, queued: true, submitted };
  });
}
// find_transaction first (it tells "still in the mempool" and "expired" apart from "not seen"); inclusion is
// still only accepted from get_transaction with a matching hash. A node without transaction_status_api falls back to the
// get_transaction probe alone. Whatever the answer, nothing here can authorize a second send.
export async function probeQueueInclusion(row, rpc = hiveReadRpc) {
  let chain = 'unknown';
  try {
    const found = await rpc('transaction_status_api.find_transaction', { transaction_id: row.txId, expiration: row.transaction?.expiration });
    chain = findTransactionChainStatus(found?.status);
  } catch (_) { chain = 'unsupported'; }
  if (chain === 'pending') return { blockNum: null, chain };
  try {
    const tx = await rpc('condenser_api.get_transaction', [row.txId]);
    if (Number.isInteger(tx?.block_num) && tx.block_num > 0 && await queueTransactionId(tx) === row.txId) return { blockNum: tx.block_num, chain: 'in_block' };
  } catch (_) { /* Absence or lookup failure cannot authorize a second send. */ }
  // A node that says "in a block" without get_transaction proof stays unresolved; so does "dropped" before expiry.
  return { blockNum: null, chain: chain === 'in_block' || (chain === 'dropped' && !queueExpired(row)) ? 'unknown' : chain };
}
async function processSignedQueue(account) {
  return queueLock(`ceres:broadcast:${account}`, async () => {
    const deadline = Date.now() + 65000;
    const inspected = new Set();
    while (Date.now() < deadline) {
      const rows = signedQueueRows(account);
      const row = rows.find(item => !inspected.has(item.txId) && ['queued', 'submitting', 'submitted', 'unknown', 'rate_limited'].includes(item.state));
      if (!row) return;
      const dependency = row.dependsOn && rows.find(item => item.txId === row.dependsOn);
      if (row.dependsOn && dependency?.state !== 'included') {
        if (!dependency || TERMINAL_QUEUE_STATES.has(dependency.state)) {
          await updateQueueRow(account, row.txId, { state: 'blocked', signedTransaction: null, message: 'Not sent: an earlier transaction in this batch did not complete.' });
          continue;
        }
        if (queueExpired(row) && !row.submissionAttempted) {
          await updateQueueRow(account, row.txId, { state: 'expired', signedTransaction: null, message: 'Not sent: it expired while an earlier transaction in this batch was still unresolved.' });
          continue;
        }
        inspected.add(row.txId); continue;
      }
      if (['submitting', 'submitted', 'unknown'].includes(row.state)) {
        // inclusion is read by the shared confirmer (its schedule, its resume on load/visible/pageshow); the
        // pump only acts on what it reports. A dependent transaction is submitted the moment its parent is included.
        watchQueueInclusion(account, row);
        inspected.add(row.txId); continue;
      }
      if (queueExpired(row)) {
        await updateQueueRow(account, row.txId, { state: 'expired', signedTransaction: null, message: 'Expired before submission. Review and sign again.' });
        continue;
      }
      if (row.state === 'rate_limited') {
        let props;
        try { props = await queueRpc('condenser_api.get_dynamic_global_properties'); } catch (_) { return; }
        if (!Number.isInteger(props?.head_block_number) || props.head_block_number <= row.retryAfterBlock) { await queueSleep(500); continue; }
      }
      let head;
      try { head = await queueRpc('condenser_api.get_dynamic_global_properties'); } catch (_) { return; }
      if (!Number.isInteger(head?.head_block_number)) return;
      try { validateSignedEnvelope(row, row.signedTransaction); }
      catch (_) { await updateQueueRow(account, row.txId, { state: 'rejected', signedTransaction: null, message: 'Saved signature does not match the reviewed transaction. Nothing was sent.' }); continue; }
      // Persist the handoff BEFORE making the network request: a crash is then ambiguous, never a retry.
      await updateQueueRow(account, row.txId, { state: 'submitting', submissionAttempted: true,
        message: 'Submitting signed transaction to Hive.' });
      try {
        await queueRpc('condenser_api.broadcast_transaction', [row.signedTransaction]);
        await updateQueueRow(account, row.txId, { state: 'submitted', message: 'Submitted to Hive — awaiting inclusion.' });
        watchQueueInclusion(account, row);
      } catch (error) {
        const detail = JSON.stringify(error.rpcError || {});
        if (/HIVE_CUSTOM_OP_BLOCK_LIMIT|limit_custom_op_count/.test(detail)) {
          await updateQueueRow(account, row.txId, { state: 'rate_limited', retryAfterBlock: head.head_block_number, message: 'Signed — waiting for the next Hive block.' });
        } else {
          // Generic RPC errors (including duplicate transaction) are not proof of nonacceptance.
          await updateQueueRow(account, row.txId, { state: 'unknown', message: 'Sent to Hive; Ceres is confirming it. Nothing is broadcast again.' });
          watchQueueInclusion(account, row);
        }
      }
    }
  });
}
// one inclusion waiter per submitted transaction, run by the shared confirmer. Included -> the row is
// 'included' and the pump resumes (dependent rows go out at once). Dropped/expired is shown as a status only; Hive's
// final absence past the lifetime makes the row 'expired' (never sent). Nothing here can authorize a second send.
const INCLUSION_WATCH = new Set();
function watchQueueInclusion(account, row) {
  if (INCLUSION_WATCH.has(row.txId)) return;
  INCLUSION_WATCH.add(row.txId);
  const handle = waitThrough({ key: `inclusion:${row.txId}`, schedule: 'inclusion', account, txId: row.txId,
    since: row.createdAt, intent: row.attemptId || null,
    beginToken: () => signedQueueRows(account).find(item => item.txId === row.txId)?.beginToken || null,
    check: async () => {
      const current = signedQueueRows(account).find(item => item.txId === row.txId);
      if (!current || current.state === 'included') return { state: 'confirmed' };
      if (TERMINAL_QUEUE_STATES.has(current.state)) return { state: 'never-sent' };
      const { blockNum, chain } = await probeQueueInclusion(current);
      if (blockNum) {
        await updateQueueRow(account, current.txId, { state: 'included', blockNum, chainStatus: 'in_block', signedTransaction: null, message: 'Included in Hive; game result still requires verification.' });
        return { state: 'confirmed', blockNum };
      }
      const dropped = chain === 'dropped' || current.chainStatus === 'dropped';
      await updateQueueRow(account, current.txId, { chainStatus: dropped ? 'dropped' : chain,
        message: dropped ? droppedMessage(current) : 'Sent to Hive; Ceres is confirming it. Nothing is broadcast again.' });
      if (dropped && current.chainStatus !== 'dropped') {
        try { globalThis.window?.dispatchEvent?.(new CustomEvent('ceres:queue-status', { detail: { account, txId: current.txId, chainStatus: 'dropped', message: droppedMessage(current) } })); } catch (_) {}
      }
      return { state: 'pending' };
    } });
  handle.done.then(async answer => {
    INCLUSION_WATCH.delete(row.txId);
    if (answer.state === 'never-sent') {
      const current = signedQueueRows(account).find(item => item.txId === row.txId);
      if (current && !TERMINAL_QUEUE_STATES.has(current.state)) {
        await updateQueueRow(account, row.txId, { state: 'expired', provenAbsent: true, signedTransaction: null, message: 'Hive has no record of this transaction and it is past its lifetime. It was not sent.' });
      }
    }
    resumeSignedQueue(account);
  });
}
export function resumeSignedQueue(account) {
  account = queueAccount(account);
  if (QUEUE_PUMPS.has(account)) return QUEUE_PUMPS.get(account);
  const pump = processSignedQueue(account).catch(() => {}).finally(() => {
    QUEUE_PUMPS.delete(account);
    try {
      // Submission pacing only (the next Hive block for a rate-limited row, or a row that became sendable while the
      // pump was busy). Confirmation is never re-armed here: the inclusion waiters own it.
      const rows = signedQueueRows(account);
      if (rows.some(row => (row.state === 'rate_limited' || (row.state === 'queued' && !row.dependsOn)) && !queueExpired(row))) {
        setTimeout(() => resumeSignedQueue(account), 1000);
      }
    } catch (_) {}
  });
  QUEUE_PUMPS.set(account, pump);
  return pump;
}
export async function releaseSignedQueueDependency(account, txId, expectedParentTxId) {
  account = queueAccount(account);
  if (!/^[0-9a-f]{40}$/.test(txId || '') || !/^[0-9a-f]{40}$/.test(expectedParentTxId || '')) {
    throw new Error('Invalid queued transaction dependency.');
  }
  const result = await queueLock(`ceres:queue-journal:${account}`, () => {
    const rows = signedQueueRows(account);
    const childIndex = rows.findIndex(row => row.txId === txId);
    const child = rows[childIndex];
    const parent = rows.find(row => row.txId === expectedParentTxId);
    if (child?.state === 'expired' && child.releaseOutcome === 'not_sent'
      && child.releasedDependency === expectedParentTxId && !child.signedTransaction && !child.submissionAttempted) {
      return { txId, dependsOn: expectedParentTxId, state: child.state, notSent: true };
    }
    if (!child || child.state !== 'queued' || child.submissionAttempted
      || child.dependsOn !== expectedParentTxId) throw new Error('Only an unattempted dependent transaction can be released.');
    if (!parent || parent.state !== 'unknown' || !queueExpired(parent)) {
      throw new Error('The earlier transaction is not an expired unresolved submission.');
    }
    const expired = queueExpired(child);
    const next = { ...child, dependsOn: null, updatedAt: new Date().toISOString(),
      ...(expired
        ? { state: 'expired', signedTransaction: null, releaseOutcome: 'not_sent',
          releasedDependency: expectedParentTxId,
          message: 'Expired behind an unresolved earlier transaction. Nothing was sent.' }
        : { state: 'queued', message: 'Dependency released. Signed transaction queued for its first submission.' }) };
    rows[childIndex] = next;
    localStorage.setItem(QUEUE_PREFIX + account, JSON.stringify(rows));
    return { txId, dependsOn: expectedParentTxId, state: next.state, notSent: expired };
  });
  if (!result.notSent) resumeSignedQueue(account);
  return result;
}
// resolves when Hive has the transaction handed over (submitted/included), or when the submission answer was
// ambiguous (state 'unknown': the id is known, so the shared confirmer decides from the chain). Rejects only when the
// transaction provably never left this browser. Event driven: no polling loop and no deadline.
export function waitForQueuedSubmission(account, txId) {
  return new Promise((resolve, reject) => {
    const decide = () => {
      const row = signedQueueRows(account).find(item => item.txId === txId);
      if (!row) { reject(queueError('Saved transaction unavailable. Nothing was sent again.', { txId })); return true; }
      if (['submitted', 'included'].includes(row.state)) { resolve({ txId, blockNum: row.blockNum || null }); return true; }
      if (row.state === 'unknown') { resolve({ txId, blockNum: null, submissionUncertain: true }); return true; }
      if (['rejected', 'expired', 'cancelled', 'blocked'].includes(row.state)) { reject(queueError(row.message, row, !row.submissionAttempted || row.provenAbsent === true)); return true; }
      return false;
    };
    if (decide()) return;
    const listener = event => {
      if (event?.detail?.txId !== txId) return;
      if (decide()) globalThis.window?.removeEventListener?.('ceres:queue-row', listener);
    };
    globalThis.window?.addEventListener?.('ceres:queue-row', listener);
  });
}
// fix round 1 (F6): another tab's pump writes the queue; its rows reach this tab's waits as the same event.
globalThis.window?.addEventListener?.('storage', event => {
  if (!event?.key?.startsWith?.(QUEUE_PREFIX) || event.newValue == null) return;
  const account = event.key.slice(QUEUE_PREFIX.length);
  let rows = [];
  try { rows = JSON.parse(event.newValue) || []; } catch (_) { return; }
  for (const row of Array.isArray(rows) ? rows : []) {
    try { globalThis.window.dispatchEvent(new CustomEvent('ceres:queue-row', { detail: { account, txId: row.txId, state: row.state } })); } catch (_) {}
  }
});
export function initializeSignedQueue(account) {
  if (!account) return;
  account = queueAccount(account);
  navigator.locks?.request(`ceres:sign:${account}`, { ifAvailable: true }, async lock => {
    if (!lock) return;
    for (const row of signedQueueRows(account).filter(item => item.state === 'signing')) {
      await updateQueueRow(account, row.txId, { state: 'cancelled', signedTransaction: null,
        message: 'The signing page closed before the signature was saved. Nothing was broadcast; review again.' });
    }
  }).catch(() => {});
  resumeSignedQueue(account);
}

// a row's harvest evidence is posted once per state it reaches, not every 15 s for as long as the browser keeps
// the row. The last state posted successfully is remembered per transaction in the browser.
const SYNCED_KEY = account => `ceres:queue-evidence-synced:${account}`;
function syncedStates(account) {
  try { const value = JSON.parse(localStorage.getItem(SYNCED_KEY(account)) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; }
  catch (_) { return {}; }
}

export async function syncQueueHarvestEvidence(account) {
  const synced = syncedStates(account);
  const live = new Set();
  let changed = false;
  for (const row of signedQueueRows(account)) {
    const attemptId = row.context?.harvestAttemptId;
    if (!attemptId || row.state === 'signing') continue;
    live.add(row.txId);
    const state = ['submitted', 'included'].includes(row.state) ? 'submitted'
      : ['expired', 'cancelled', 'blocked', 'rejected'].includes(row.state) ? 'failed' : 'signed_queued';
    if (synced[row.txId] === state) continue;
    try {
      const response = await fetch(`/api/harvest/attempt?player=${encodeURIComponent(account)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ attempt_id: attemptId, tx_id: row.txId, run_id: row.runId || null, state }) });
      if (response?.ok) { synced[row.txId] = state; changed = true; }
    } catch (_) { /* Queue evidence remains durable locally for a later sync. */ }
  }
  for (const txId of Object.keys(synced)) if (!live.has(txId)) { delete synced[txId]; changed = true; }
  if (changed) { try { localStorage.setItem(SYNCED_KEY(account), JSON.stringify(synced)); } catch (_) {} }
}
