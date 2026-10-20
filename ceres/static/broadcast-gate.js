import { requestWalletBroadcast, walletCanSignTransaction } from './wallet.js';
import { prepareQueueBatch, signQueuedTransaction, withQueueSigningLock, hasSignedQueueClaim, reviewedActiveLockOperation, SIGNED_QUEUE_EXPIRY_MS } from './transaction-queue.js';

export const KEYCHAIN_RESPONSE_TIMEOUT_MS = 120000;

const BROADCAST_CLAIMS_KEY = 'ceres:broadcast-claims:v1:';
const BROADCAST_CLAIMS = new Set();

function lockName(account) {
  return `ceres:broadcast:${String(account || '').toLowerCase()}`;
}

function withAccountLock(account, callback) {
  const name = lockName(account);
  if (globalThis.navigator?.locks?.request) {
    return globalThis.navigator.locks.request(name, { mode: 'exclusive' }, callback);
  }
  const error = new Error('This browser cannot safely coordinate account broadcasts across tabs. Use a browser with Web Locks support.');
  error.gateUnavailable = true;
  error.retryable = true;
  return Promise.reject(error);
}

function broadcastRequestIdentities(account, operations) {
  const player = String(account || '').toLowerCase();
  const identities = [...new Set((operations || []).map((operation) => {
    const [type, body] = Array.isArray(operation) ? operation : [];
    let nonce;
    try { nonce = JSON.parse(body?.json)?.n; } catch (_) { /* exact operation is the fallback identity */ }
    return nonce === undefined || nonce === null
      ? JSON.stringify([player, operation])
      : JSON.stringify([player, type, body?.id || '', String(nonce)]);
  }))];
  return identities.length ? identities : [JSON.stringify([player, operations])];
}

function broadcastClaimsKey(account) {
  return `${BROADCAST_CLAIMS_KEY}${encodeURIComponent(String(account || '').toLowerCase())}`;
}

function storedBroadcastClaims(account) {
  try {
    if (!globalThis.localStorage) return null;
    const stored = JSON.parse(globalThis.localStorage.getItem(broadcastClaimsKey(account)) || '[]');
    return Array.isArray(stored) && stored.every((item) => typeof item === 'string') ? stored : null;
  } catch (_) {
    return null;
  }
}

function writeBroadcastClaims(account, claims) {
  try {
    globalThis.localStorage.setItem(broadcastClaimsKey(account), JSON.stringify(claims));
    return true;
  } catch (_) {
    return false;
  }
}

function claimStorageError() {
  const error = new Error('Safe broadcast tracking is unavailable in this browser. Enable site storage, then restage before signing.');
  error.gateUnavailable = true;
  error.broadcasted = false;
  error.retryable = false;
  return error;
}

function duplicateBroadcastError() {
  const error = new Error('Check again, never a second broadcast — this exact signed request was already handed to a wallet. Restage to create a new request and nonce.');
  error.duplicateBroadcast = true;
  error.ambiguousBroadcast = true;
  error.broadcasted = true;
  error.retryable = false;
  return error;
}

function claimBroadcastRequest(account, operations) {
  const identities = broadcastRequestIdentities(account, operations);
  const stored = storedBroadcastClaims(account);
  if (!stored) throw claimStorageError();
  if (identities.some((identity) => BROADCAST_CLAIMS.has(identity) || stored.includes(identity))) throw duplicateBroadcastError();
  identities.forEach((identity) => BROADCAST_CLAIMS.add(identity));
  if (!writeBroadcastClaims(account, stored.concat(identities))) {
    identities.forEach((identity) => BROADCAST_CLAIMS.delete(identity));
    throw claimStorageError();
  }
  return identities;
}

function releaseBroadcastRequest(account, identities) {
  identities.forEach((identity) => BROADCAST_CLAIMS.delete(identity));
  const released = new Set(identities);
  const stored = storedBroadcastClaims(account);
  if (stored && !writeBroadcastClaims(account, stored.filter((item) => !released.has(item)))) throw claimStorageError();
  dispatchBroadcastRequestReleased(account, released);
}

function dispatchBroadcastRequestReleased(account, identities) {
  try {
    window.dispatchEvent(new CustomEvent('ceres:broadcast-request-released', {
      detail: { account: String(account || '').toLowerCase(), identities: [...identities] },
    }));
  } catch (_) { /* no window in tests */ }
}

const ADMISSION_KEY = 'ceres:pending-custom-ops:v1:';
const CUSTOM_OP_LIMIT = 5;
let admissionSequence = 0;

function newAdmissionInstanceId() {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return uuid;
  if (globalThis.crypto?.getRandomValues) {
    const words = new Uint32Array(4);
    globalThis.crypto.getRandomValues(words);
    return [...words].map((word) => word.toString(16).padStart(8, '0')).join('');
  }
  admissionSequence += 1;
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${admissionSequence.toString(36)}`;
}

function admissionKey(account) { return ADMISSION_KEY + encodeURIComponent(String(account).toLowerCase()); }
function pendingCustomOps(account) {
  try {
    let rows = JSON.parse(globalThis.localStorage.getItem(admissionKey(account)) || '[]');
    if (!Array.isArray(rows) || rows.some((row) => !row || typeof row.id !== 'string'
      || !Number.isInteger(row.count) || row.count < 1 || row.count > CUSTOM_OP_LIMIT
      || (row.txId !== null && typeof row.txId !== 'string')
      || (row.claimIds !== undefined && (!Array.isArray(row.claimIds) || !row.claimIds.length
        || row.claimIds.some((identity) => typeof identity !== 'string') || !row.claimIds.includes(row.id)))
      || (row.instanceId !== undefined && (typeof row.instanceId !== 'string' || !row.instanceId))
      || (row.budgetActive !== undefined && typeof row.budgetActive !== 'boolean')
      || (row.createdAt !== undefined && (typeof row.createdAt !== 'string' || !Number.isFinite(Date.parse(row.createdAt)))))) throw new Error();
    const now = Date.now();
    let changed = false;
    const storedClaims = rows.some((row) => row.claimIds === undefined) ? storedBroadcastClaims(account) : null;
    rows = rows.map((row) => {
      const migrated = { ...row };
      if (migrated.createdAt === undefined) {
        const legacyTimestamp = [migrated.timestamp, migrated.created_at].find((value) => typeof value === 'string' && Number.isFinite(Date.parse(value)));
        migrated.createdAt = legacyTimestamp || new Date(now - SIGNED_QUEUE_EXPIRY_MS).toISOString();
        changed = true;
      }
      if (migrated.claimIds === undefined) {
        const claimIndex = storedClaims?.indexOf(row.id) ?? -1;
        migrated.claimIds = claimIndex >= 0 ? storedClaims.slice(claimIndex, claimIndex + row.count) : [row.id];
        changed = true;
      }
      if (migrated.instanceId === undefined) {
        migrated.instanceId = newAdmissionInstanceId();
        changed = true;
      }
      if (migrated.budgetActive === undefined) {
        migrated.budgetActive = true;
        changed = true;
      }
      return migrated;
    });
    if (changed) saveCustomOps(account, rows);
    return rows;
  } catch (_) { throw claimStorageError(); }
}
function activeAdmissions(account) {
  return pendingCustomOps(account).filter((row) => row.budgetActive);
}
function saveCustomOps(account, rows) {
  try {
    globalThis.localStorage.setItem(admissionKey(account), JSON.stringify(rows));
  }
  catch (_) { throw claimStorageError(); }
}
function recoverStoredAdmissions() {
  try {
    const keys = [];
    for (let index = 0; index < Number(globalThis.localStorage?.length || 0); index += 1) {
      const key = globalThis.localStorage.key(index);
      if (key?.startsWith(ADMISSION_KEY)) keys.push(key);
    }
    keys.forEach((key) => {
      try { pendingCustomOps(decodeURIComponent(key.slice(ADMISSION_KEY.length))); } catch (_) { /* Account access will surface storage errors. */ }
    });
  } catch (_) { /* Storage availability is checked before signing. */ }
}
recoverStoredAdmissions();
function customOpCount(operations) {
  return operations.filter(([type]) => ['custom_json', 'custom', 'custom_binary'].includes(type)).length;
}
function pendingWalletRequestsStopped(rows, requiredCount) {
  const count = rows.length;
  const error = new Error(`${count === 1 ? 'An earlier wallet request is' : `${count} earlier wallet requests are`} still being checked with Hive. This transaction was not sent. Ceres keeps checking and this clears itself.`);
  error.broadcasted = false;
  error.retryable = true;
  error.pendingWalletRequests = count;
  error.pendingWalletOperations = rows.reduce((sum, row) => sum + row.count, 0);
  error.requiredCustomOperations = requiredCount;
  error.recoveryAction = 'check-pending-wallet-requests';
  return error;
}
async function probeAdmission(account, row) {
  // A10: no-id recovery cannot discover transactions through history.
  if (!row.txId) return { included: null, block_num: null };
  let timer;
  try {
    return await Promise.race([
      fetch(`/api/op-inclusion?player=${encodeURIComponent(String(account).toLowerCase())}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tx_id: row.txId, claim_ids: row.claimIds, created_at: row.createdAt }),
      }).then((response) => response.ok ? response.json() : null),
      new Promise((resolve) => { timer = setTimeout(() => resolve(null), 5000); }),
    ]);
  } catch (_) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function resolveAdmission(account, row, included, { releaseClaim = false } = {}) {
  const rows = pendingCustomOps(account);
  const current = rows.find((candidate) => candidate.id === row.id && candidate.instanceId === row.instanceId);
  if (!current || current.txId !== row.txId || current.budgetActive !== row.budgetActive) return false;
  if (included === false && current.txId === null) {
    // the chain has said this request never arrived, so its identity must be
    // released, not merely announced. Marking the row inactive and firing the event
    // alone left the identity claimed forever, so the next press of Sign was refused in
    // the browser as a duplicate and the wallet was never called. The Changes path is
    // the only one that crosses this budget, which is why harvest kept working.
    saveCustomOps(account, rows.map((candidate) => candidate.id === row.id && candidate.instanceId === row.instanceId
      ? { ...candidate, budgetActive: false } : candidate));
    if (releaseClaim) releaseBroadcastRequest(account, current.claimIds);
    else dispatchBroadcastRequestReleased(account, current.claimIds);
    return true;
  }
  saveCustomOps(account, rows.filter((candidate) => candidate.id !== row.id || candidate.instanceId !== row.instanceId));
  if (included === false) releaseBroadcastRequest(account, current.claimIds);
  return true;
}

async function releaseProvenAdmissions(account) {
  const rows = activeAdmissions(account);
  if (!rows.length) return;
  const inclusions = await Promise.all(rows.map((row) => probeAdmission(account, row)));
  rows.forEach((row, index) => {
    const inclusion = inclusions[index];
    if (inclusion?.included === false) resolveAdmission(account, row, false, { releaseClaim: true });
    else if (inclusion?.included === true) resolveAdmission(account, row, true);
  });
}

function releaseAdmission(account, row) {
  return resolveAdmission(account, row, false);
}

export async function recoverPendingBroadcastRequests(account, requiredCount = 1) {
  const rows = activeAdmissions(account);
  let released = 0;
  let included = 0;
  let pending = 0;
  const inclusions = await Promise.all(rows.map((row) => probeAdmission(account, row)));
  rows.forEach((row, index) => {
    const inclusion = inclusions[index];
    if (inclusion?.included === false && resolveAdmission(account, row, false, { releaseClaim: true })) released += 1;
    else if (inclusion?.included === true && resolveAdmission(account, row, true)) included += 1;
    else pending += 1;
  });
  const remainingRows = activeAdmissions(account);
  const remainingOperations = remainingRows.reduce((sum, row) => sum + row.count, 0);
  // carry the server's own "ask again in N seconds" back to the surface, so it
  // can show a real wait instead of an open-ended promise that it clears itself.
  const waits = inclusions.map((inclusion) => Number(inclusion?.retry_after)).filter((value) => Number.isFinite(value) && value > 0);
  return {
    released, included, pending, remaining: remainingRows.length, remainingOperations,
    retryAfter: waits.length ? Math.min(...waits) : null,
    blocked: remainingOperations + requiredCount > CUSTOM_OP_LIMIT,
  };
}

// Ceres resolves pending wallet requests itself. The maintainer never inspects the
// chain and never presses a recovery control: his only action is to sign. This runs the
// same bounded inclusion probe automatically -- on load, when a signing surface opens
// and on a slow repeat while anything is still unresolved. It fails closed: only a
// definitive not-included verdict releases a request, so a slow or unreachable node can
// never be mistaken for "never sent".
const AUTO_RESOLVE_INTERVAL_MS = 45000;
let autoResolveTimer = null;
let autoResolveAccount = null;
let autoResolveRunning = false;

function dispatchPendingRequestsResolved(account, result) {
  try {
    window.dispatchEvent(new CustomEvent('ceres:pending-requests-resolved', {
      detail: { account: String(account || '').toLowerCase(), ...result },
    }));
  } catch (_) { /* no window in tests */ }
}

export async function resolvePendingBroadcastRequestsNow(account, requiredCount = 1) {
  if (!account || autoResolveRunning) return null;
  if (!activeAdmissions(account).length) return null;
  autoResolveRunning = true;
  try {
    const result = await recoverPendingBroadcastRequests(account, requiredCount);
    if (result && (result.released || result.included)) dispatchPendingRequestsResolved(account, result);
    return result;
  } catch (_) {
    return null;
  } finally {
    autoResolveRunning = false;
  }
}

export function stopPendingBroadcastAutoResolve() {
  if (autoResolveTimer) clearInterval(autoResolveTimer);
  autoResolveTimer = null;
  autoResolveAccount = null;
}

export function startPendingBroadcastAutoResolve(account) {
  const name = String(account || '').toLowerCase();
  if (!name) return;
  if (autoResolveTimer && autoResolveAccount === name) {
    resolvePendingBroadcastRequestsNow(name).catch(() => {});
    return;
  }
  stopPendingBroadcastAutoResolve();
  autoResolveAccount = name;
  resolvePendingBroadcastRequestsNow(name).catch(() => {});
  try {
    autoResolveTimer = setInterval(() => {
      if (!activeAdmissions(name).length) { stopPendingBroadcastAutoResolve(); return; }
      resolvePendingBroadcastRequestsNow(name).catch(() => {});
    }, AUTO_RESOLVE_INTERVAL_MS);
  } catch (_) { /* no timers in tests */ }
}

export async function releasePendingBroadcastRequests(account, operationGroups) {
  const groups = (operationGroups || []).filter((operations) => Array.isArray(operations) && operations.length);
  const requestedIds = new Set(groups.flatMap((operations) => broadcastRequestIdentities(account, operations)));
  const rows = activeAdmissions(account).filter((row) => requestedIds.has(row.id));
  let releasedAll = true;
  for (const row of rows) {
    const inclusion = await probeAdmission(account, row);
    if (inclusion?.included === false) releasedAll = releaseAdmission(account, row) && releasedAll;
    else if (inclusion?.included === true) releasedAll = resolveAdmission(account, row, true) && releasedAll;
    else releasedAll = false;
  }
  return releasedAll;
}

// a request the chain can never judge must not hold the account hostage.
// Some requests cannot be looked up at all -- an operation whose payload carries no
// nonce has no checkable identity -- so the probe answers "unknown" forever and every
// later signing attempt was refused before the wallet was called. Once a request is
// older than the window in which Hive would certainly have included it, it stops
// counting against the per-block budget. Its claim is KEPT, so that exact signed
// envelope can still never be sent twice; each new preview mints a fresh nonce, so the
// maintainer's staged changes are untouched and he never has to undo or restage anything.
const BUDGET_RETIREMENT_MS = 10 * 60 * 1000; // same window as the signed-queue expiry

function retireFromBudget(account, row) {
  const age = Date.now() - Date.parse(row.createdAt || 0);
  if (!Number.isFinite(age) || age < BUDGET_RETIREMENT_MS) return false;
  const rows = pendingCustomOps(account);
  const current = rows.find((candidate) => candidate.id === row.id && candidate.instanceId === row.instanceId);
  if (!current || !current.budgetActive) return false;
  saveCustomOps(account, rows.map((candidate) => candidate.id === row.id && candidate.instanceId === row.instanceId
    ? { ...candidate, budgetActive: false } : candidate));
  return true;
}

// the next
// chunk of a batch waits for the previous one to be proven on chain -- one check a second for up to 30 s (a Hive block is
// 3 s, plus indexing), for requests with or without a transaction ID. Only a definitive verdict frees the budget, so a
// slow node still never counts as "never sent" and nothing is ever broadcast twice.
const BUDGET_WAIT_ATTEMPTS = 30;
const BUDGET_WAIT_STEP_MS = 1000;
async function waitForCustomOpBudget(account, count) {
  for (let attempt = 0; attempt < BUDGET_WAIT_ATTEMPTS; attempt += 1) {
    const rows = activeAdmissions(account);
    if (rows.reduce((sum, row) => sum + row.count, count) <= CUSTOM_OP_LIMIT) return;
    const first = rows[0];
    const inclusion = await probeAdmission(account, first);
    if (inclusion?.included === false) {
      if (first.txId) resolveAdmission(account, first, false, { releaseClaim: true });
      else releaseAdmission(account, first);
      continue;
    }
    if (inclusion?.included === true && (!first.txId || (Number.isInteger(inclusion.block_num) && inclusion.block_num > 0))) {
      // Re-read: a late wallet callback may have updated an outstanding record.
      resolveAdmission(account, first, true);
      continue;
    }
    if (retireFromBudget(account, first)) continue; // unjudgeable and long past its window
    if (attempt < BUDGET_WAIT_ATTEMPTS - 1) await new Promise((resolve) => setTimeout(resolve, BUDGET_WAIT_STEP_MS));
  }
  const rows = activeAdmissions(account);
  if (rows.reduce((sum, row) => sum + row.count, count) > CUSTOM_OP_LIMIT) throw pendingWalletRequestsStopped(rows, count);
}

function walletFailure(response) {
  if (response?.signingRejected || response?.declined) {
    const error = new Error('Signing rejected in the Keychain app');
    error.signingRejected = true;
    error.declined = true;
    error.retryable = true;
    error.broadcasted = false;
    error.ambiguousBroadcast = false;
    return error;
  }
  if (response?.stillChecking) {
    const error = new Error('Ceres is still checking whether the wallet sent this');
    error.retryable = false;
    error.broadcasted = true;
    error.ambiguousBroadcast = true;
    error.stillChecking = true;
    return error;
  }
  if (response?.notSent) {
    const error = new Error(response.error || 'The wallet did not receive this request.');
    error.retryable = true;
    error.broadcasted = false;
    error.ambiguousBroadcast = false;
    return error;
  }
  const raw = JSON.stringify(response?.error || response || {});
  const blockLimit = /HIVE_CUSTOM_OP_BLOCK_LIMIT|limit_custom_op_count|already submitted .*custom json operation/i.test(raw);
  const rejected = blockLimit || /assert_exception|Assert Exception/.test(raw);
  const cancelled = /cancel|denied|reject.*user|user.*reject/i.test(raw);
  const error = new Error(blockLimit
    ? 'Hive rejected this signed transaction because the account reached its per-block operation limit. This batch was not accepted; earlier batches may already have completed. Refresh the harvest before signing the remaining plots.'
    : rejected ? 'Hive rejected the signed transaction. This batch was not accepted; check the wallet for the rejection reason.'
      : cancelled ? 'The wallet request was cancelled. This transaction was not sent.'
        : 'The wallet did not say whether it sent this request. Ceres will not offer it again until it has been checked.');
  // S10 / L2-01: only an OUTCOME the wallet or Hive stated (a refusal, a cancel, a chain rejection, an explicit "not sent") counts as
  // not sent. A null answer or an unclassified failure is unknown: the claim stays, the request is not offered for signing again.
  // A duplicate-transaction assertion means an identical request may already be on chain: that is the opposite of "not sent".
  const duplicate = /duplicate|already (?:been )?(?:applied|included|processed)/i.test(raw);
  const known = (blockLimit || rejected || cancelled) && !duplicate;
  const ambiguous = Boolean(response?.ambiguousBroadcast) || !known;
  error.broadcastRejected = rejected;
  error.retryable = !ambiguous;
  error.broadcasted = ambiguous;
  error.ambiguousBroadcast = ambiguous;
  return error;
}

function announceSigned(account, txId) {
  try { window.dispatchEvent(new CustomEvent('ceres:tx-signed', { detail: { txId, account } })); } catch (_) { /* no window in tests */ }
}

function requestBroadcastPromise(account, operations, onResponse, authority) {
  const requestIdentities = claimBroadcastRequest(account, operations);
  const count = customOpCount(operations);
  const admissionId = requestIdentities[0];
  const admissionInstanceId = newAdmissionInstanceId();
  if (count) {
    try { saveCustomOps(account, [...pendingCustomOps(account), {
      id: admissionId, instanceId: admissionInstanceId, claimIds: requestIdentities,
      count, txId: null, budgetActive: true, createdAt: new Date().toISOString(),
    }]); }
    catch (error) { releaseBroadcastRequest(account, requestIdentities); throw error; }
  }
  const updateAdmission = (txId, remove = false) => {
    if (!count) return false;
    const rows = pendingCustomOps(account);
    const found = rows.some((row) => row.id === admissionId && row.instanceId === admissionInstanceId && row.txId === null);
    if (!found) return false;
    saveCustomOps(account, remove
      ? rows.filter((row) => row.id !== admissionId || row.instanceId !== admissionInstanceId)
      : rows.map((row) => row.id === admissionId && row.instanceId === admissionInstanceId && row.txId === null ? { ...row, txId } : row));
    return found;
  };
  const requestError = (error) => {
    error.broadcastRequestId = admissionId;
    error.broadcastRequestIds = requestIdentities;
    return error;
  };
  return new Promise((resolve, reject) => {
    let timedOut = false;
    let resolvePendingBroadcast;
    let rejectPendingBroadcast;
    const pendingBroadcast = new Promise((pendingResolve, pendingReject) => {
      resolvePendingBroadcast = pendingResolve;
      rejectPendingBroadcast = pendingReject;
    });
    pendingBroadcast.catch(() => {});
    const timeoutId = setTimeout(() => {
      timedOut = true;
      const error = new Error('Check again, never a second broadcast — The wallet has not returned a result, so this request remains pending verification.');
      error.keychainTimedOut = true;
      error.ambiguousBroadcast = true;
      error.broadcasted = true;
      error.retryable = false;
      error.pendingBroadcast = pendingBroadcast;
      reject(requestError(error));
    }, KEYCHAIN_RESPONSE_TIMEOUT_MS);
    try {
      requestWalletBroadcast(account, operations, (response) => {
        clearTimeout(timeoutId);
        try { onResponse?.(response, { timedOut }); } catch (_) { /* Receipt rendering must not undo a wallet broadcast. */ }
        if (!response || !response.success) {
          const error = walletFailure(response);
          if (!error.ambiguousBroadcast) {
            try { updateAdmission(null, true); } catch (_) { /* Retain conservative budget on storage failure. */ }
          }
          if (!timedOut && !error.ambiguousBroadcast) releaseBroadcastRequest(account, requestIdentities);
          if (timedOut) rejectPendingBroadcast(requestError(error));
          else reject(requestError(error));
          return;
        }
        const signedKey = response.data?.key;
        if (signedKey && String(signedKey).toLowerCase() !== authority.toLowerCase()) {
          const error = new Error(`Wallet signed with ${signedKey}, not ${authority} — refused.`);
          error.tierMismatch = true;
          error.keyTier = signedKey;
          error.broadcasted = true;
          error.retryable = false;
          error.ambiguousBroadcast = true;
          if (timedOut) rejectPendingBroadcast(requestError(error));
          else reject(requestError(error));
          return;
        }
        const txId = response.result?.id || response.result?.tx_id || response.tx_id;
        if (!txId) {
          const error = new Error('Check again, never a second broadcast — The wallet accepted the request but returned no transaction id.');
          error.ambiguousBroadcast = true;
          error.broadcasted = true;
          if (timedOut) rejectPendingBroadcast(requestError(error));
          else reject(requestError(error));
          return;
        }
        let admissionUpdated = false;
        try { admissionUpdated = updateAdmission(txId); } catch (_) { /* Preserve the receipt even if storage becomes unavailable. */ }
        announceSigned(account, txId);
        if (admissionUpdated && response.lateSigned === true) dispatchBroadcastRequestReleased(account, requestIdentities);
        if (timedOut) {
          if (admissionUpdated && response.lateSigned !== true) dispatchBroadcastRequestReleased(account, requestIdentities);
          resolvePendingBroadcast({ txId, blockNum: null });
          return;
        }
        resolve(txId);
      }, authority);
    } catch (error) {
      clearTimeout(timeoutId);
      const failure = error instanceof Error ? error : new Error(String(error));
      failure.retryable = true;
      failure.broadcasted = false;
      releaseBroadcastRequest(account, requestIdentities);
      try { updateAdmission(null, true); } catch (_) { /* Keep conservative budget. */ }
      reject(requestError(failure));
    }
  });
}

// ( -- Ceres signs the market purchase with Keychain / HiveAuth, Active key): the one other
// reviewed Active operation. Exactly one sm_market_purchase built by the checkout (ceres/market_purchase.py): the official
// field order plus Ceres' app and nonce, DEC or CREDITS, 1-150 distinct listing ids, a positive price, this account only.
export function reviewedMarketPurchase(account, operations) {
  if (!Array.isArray(operations) || operations.length !== 1) return false;
  const [type, body] = operations[0] || [];
  if (type !== 'custom_json' || body?.id !== 'sm_market_purchase' || body.required_auths?.length !== 1
    || body.required_auths[0] !== account || body.required_posting_auths?.length !== 0) return false;
  let payload;
  try { payload = JSON.parse(body.json); } catch (_) { return false; }
  const items = payload?.items;
  return JSON.stringify(Object.keys(payload || {})) === '["currency","items","price","app","n"]'
    && (payload.currency === 'DEC' || payload.currency === 'CREDITS')
    && Array.isArray(items) && items.length >= 1 && items.length <= 150 && new Set(items).size === items.length
    && items.every((id) => typeof id === 'string' && /^[0-9a-f]{40}-[0-9]{1,4}$/.test(id))
    && typeof payload.price === 'number' && Number.isFinite(payload.price) && payload.price > 0
    && typeof payload.app === 'string' && payload.app.startsWith('ceres/') && /^[a-f0-9]{16}$/.test(payload.n || '');
}

export const BroadcastGate = {
  broadcast(account, operations, onResponse, authority = 'Posting', metadata = {}) {
    if (!['Posting', 'Active'].includes(authority)) return Promise.reject(new Error('Unsupported signing authority.'));
    if (authority === 'Active') {
      const valid = reviewedActiveLockOperation(account, operations, metadata?.reviewedActiveLock) || reviewedMarketPurchase(account, operations) || (operations?.length === 1 && operations.every(([type, body]) => {
        let payload;
        try { payload = JSON.parse(body.json); } catch (_) { return false; }
        return type === 'custom_json' && body.id === 'sm_sps' && payload.action === 'token_transfer'
          && payload.params?.token === 'SPS' && /^(lpsid|lpdid):[1-9][0-9]*$/.test(payload.params.memo || '')
          && Number.isFinite(payload.params.qty) && payload.params.qty > 0
          && typeof payload.params.to === 'string' && typeof payload.params.n === 'string'
          && body.required_auths?.length === 1
          && body.required_auths[0] === account && body.required_posting_auths?.length === 0;
      }));
      if (!valid) return Promise.reject(new Error('Active authority is only available for reviewed SPS funding, deed lock or market purchase operations.'));
    }
    return withQueueSigningLock(account, () => withAccountLock(account, async () => {
      // the inclusion check runs only when it can change the outcome -- an unresolved earlier request holds
      // one of THESE operations' identities, or this request would exceed the custom-op budget ( recovery).
      // Otherwise the next wallet opens at once, with no validation read in between ().
      const identities = new Set(broadcastRequestIdentities(account, operations));
      const pending = activeAdmissions(account);
      if (pending.some((row) => (row.claimIds || [row.id]).some((id) => identities.has(id)))
        || pending.reduce((sum, row) => sum + row.count, customOpCount(operations)) > CUSTOM_OP_LIMIT) await releaseProvenAdmissions(account);
      if (hasSignedQueueClaim(account, operations)) throw duplicateBroadcastError();
      const count = customOpCount(operations);
      if (count > CUSTOM_OP_LIMIT) {
        const error = new Error('This transaction exceeds Hive’s five custom-operation limit. Nothing was sent.');
        error.broadcasted = false;
        throw error;
      }
      if (count && activeAdmissions(account).reduce((sum, row) => sum + row.count, count) > CUSTOM_OP_LIMIT) {
        await waitForCustomOpBudget(account, count);
      }
      const txId = await requestBroadcastPromise(account, operations, onResponse, authority);
      return { txId, blockNum: null };
    }));
  },
};

export async function prepareSigningBatch(account, operationGroups, authority = 'Posting', metadata = {}) {
  return prepareQueueBatch(account, operationGroups, authority, metadata);
}

export async function requestSignatureThroughGate(account, operations, onResponse, authority = 'Posting', prepared = null, metadata = {}) {
  // `direct` asks the wallet to sign AND send in one request (Keychain requestBroadcast), as the game does for
  // the Active SPS transfer of a pool sale; Keychain showed no prompt for a sign-only Active request there.
  if (!walletCanSignTransaction(account) || metadata?.direct === true) {
    const broadcast = await BroadcastGate.broadcast(account, operations, onResponse, authority, metadata);
    return { ...broadcast, queued: false, submitted: Promise.resolve(broadcast) };
  }
  try {
    const envelope = prepared || (await prepareQueueBatch(account, [operations], authority, metadata))[0];
    if (envelope.account !== String(account).toLowerCase() || envelope.authority !== authority
      || JSON.stringify(envelope.transaction.operations) !== JSON.stringify(operations)) throw new Error('Prepared transaction does not match this review.');
    const queued = await signQueuedTransaction(envelope);
    queued.submitted.then(result => {
      try { onResponse?.({ success: true, result: { id: result.txId }, data: { key: authority } }); } catch (_) {}
      announceSigned(account, result.txId);
    }).catch(() => {});
    return queued;
  } catch (error) {
    if (error.broadcasted === undefined) { error.broadcasted = false; error.retryable = false; }
    throw error;
  }
}

export async function requestBroadcastThroughGate(account, operations, onResponse, authority = 'Posting', metadata = {}) {
  const queued = await requestSignatureThroughGate(account, operations, onResponse, authority, null, metadata);
  return queued.submitted;
}
