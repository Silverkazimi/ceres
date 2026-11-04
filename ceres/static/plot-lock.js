import { esc, formatPlotLabel } from './format.js';
import { tipAttrs } from './tooltip.js';
import { prepareSigningBatch, requestSignatureThroughGate } from './broadcast-gate.js';
import { confirmAction, confirmationState, waitThrough } from './shared-confirmer.js';
import { walletCanSignTransaction } from './wallet.js';
import { currentCeresAppIdentity } from './transaction-queue.js';
import { playerQueryAppend } from './board.js';

const LOCK_PLAN_PREFIX = 'ceres:plot-lock-plan:v1:';
export const STAGED_PLOT_LOCKS = new Map();
let planAccount = null;
const reviews = new Map();
const deedId = (value) => /^I-[0-9]+-[A-Za-z0-9]+$/.test(value || '');
// a signed lock/unlock is read by id (never a history scan) through the shared confirmer: accepted ->
// it keeps waiting for the live deed state; refused -> the entry goes and the game's reason is shown; never sent (Hive
// reports it absent past its lifetime) -> "not sent" with a dismiss. No 25-second "not found", no Check again.
const verifying = new Set();

export function plotLockState(row) {
  const days = row?.lock_days;
  const date = row?.unlock_date;
  const hasDays = Number.isInteger(Number(days)) && Number(days) > 0;
  const unlocksAt = typeof date === 'string' && Number.isFinite(Date.parse(date)) ? Date.parse(date) : null;
  const locked = unlocksAt === null ? hasDays : unlocksAt > Date.now();
  return { known: !!row && Object.hasOwn(row, 'lock_days') && Object.hasOwn(row, 'unlock_date'), locked,
    unlocking: unlocksAt !== null && unlocksAt > Date.now(), days: hasDays ? Number(days) : null, unlocksAt };
}

export function plotLockSortState(row) {
  const staged = STAGED_PLOT_LOCKS.get(row?.deed_uid);
  // Sort by what the icon shows: a staged change shows its target, a signed one (txId) the current state.
  if (!staged?.txId && (staged?.action === 'lock' || staged?.action === 'unlock')) {
    return { known: true, locked: staged.action === 'lock' };
  }
  const state = plotLockState(row);
  return { known: state.known, locked: state.locked };
}

// (audit A09/A10): a plan is only ever removed by the player's Undo or by the chain's own proof. Entries this page cannot read are carried through every
// save unchanged, a record that cannot be parsed is moved aside (never overwritten), and a plan another tab added is adopted instead of being erased by this tab's save.
const OPAQUE_PLANS = [];
const REMOVED_HERE = new Set();
function dropPlan(uid) { REMOVED_HERE.add(uid); STAGED_PLOT_LOCKS.delete(uid); }
function readablePlan(entry) {
  return {
    deed_uid: entry.deed_uid, action: entry.action,
    baselineDays: Number.isInteger(entry.baselineDays) && entry.baselineDays > 0 ? entry.baselineDays : null,
    baselineUnlockDate: typeof entry.baselineUnlockDate === 'string' && entry.baselineUnlockDate.length <= 100 ? entry.baselineUnlockDate : null,
    lockDays: 3,
    ...(typeof entry.txId === 'string' && /^[0-9a-f]{40}$/.test(entry.txId) ? { txId: entry.txId } : {}),
    ...(Number.isFinite(entry.signedAt) && entry.signedAt > 0 ? { signedAt: entry.signedAt } : {}),
    ...(typeof entry.actionId === 'string' && /^[a-f0-9]{32}$/.test(entry.actionId) ? { actionId: entry.actionId } : {}),
    ...(entry.accepted === true ? { accepted: true } : {}),
    ...(entry.notFound === true ? { notFound: true } : {}),
    // fix round 3: the signed lock operation is kept across a reload, so the shared confirmer finds the attempt
    // it belongs to (its recorded begin and closure); without it a dropped lock could never be proven "never sent".
    ...(lockOperations(entry.operations) ? { operations: entry.operations } : {}),
  };
}
function lockOperations(operations) {
  if (!Array.isArray(operations) || operations.length !== 1) return false;
  const [kind, body] = operations[0] || [];
  return kind === 'custom_json' && ['sm_lock_assets', 'sm_unlock_assets'].includes(body?.id) && typeof body.json === 'string' && body.json.length <= 4000;
}
function adoptOtherTabPlans() {
  try {
    const stored = JSON.parse(localStorage.getItem(LOCK_PLAN_PREFIX + planAccount) || '[]');
    if (Array.isArray(stored)) for (const entry of stored) {
      if (deedId(entry?.deed_uid) && ['lock', 'unlock'].includes(entry.action) && !STAGED_PLOT_LOCKS.has(entry.deed_uid) && !REMOVED_HERE.has(entry.deed_uid)) STAGED_PLOT_LOCKS.set(entry.deed_uid, readablePlan(entry));
    }
  } catch (_) { /* an unreadable record is handled where it is loaded */ }
}

function savePlans() {
  if (!planAccount) return;
  adoptOtherTabPlans();
  localStorage.setItem(LOCK_PLAN_PREFIX + planAccount, JSON.stringify([...STAGED_PLOT_LOCKS.values(), ...OPAQUE_PLANS]));
  window.dispatchEvent(new CustomEvent('ceres:plot-lock-stage'));
}

export function reconcilePlotLockPlans(board) {
  const account = String(board?.player || '').toLowerCase();
  if (!account) return;
  if (account !== planAccount) {
    planAccount = account;
    STAGED_PLOT_LOCKS.clear();
    OPAQUE_PLANS.length = 0;
    REMOVED_HERE.clear();
    let raw = null;
    try {
      raw = localStorage.getItem(LOCK_PLAN_PREFIX + account);
      const saved = JSON.parse(raw || '[]');
      if (!Array.isArray(saved)) throw new Error('not a list');
      for (const entry of saved) {
        if (deedId(entry?.deed_uid) && ['lock', 'unlock'].includes(entry.action)) STAGED_PLOT_LOCKS.set(entry.deed_uid, readablePlan(entry));
        else if (entry !== null && entry !== undefined) OPAQUE_PLANS.push(entry);   // not understood by this version: kept as it is
      }
    } catch (_) {
      STAGED_PLOT_LOCKS.clear();
      try { if (raw) localStorage.setItem(`${LOCK_PLAN_PREFIX}${account}:corrupt:${new Date().toISOString()}`, raw); } catch (__) { /* nothing more can be done */ }
    }
  }
  const rows = new Map((board.plots || []).map(row => [row.deed_uid, row]));
  for (const [uid, entry] of STAGED_PLOT_LOCKS) {
    const row = rows.get(uid);
    const state = plotLockState(row);
    // fix round 2 (astra-r2 shape 5): a SIGNED entry is settled only by its operation's verdict, never by the
    // board: until the game has accepted it, the board looking "done" (or the deed missing) removes nothing.
    if (entry.txId && !entry.accepted) { delete entry.blockedReason; continue; }
    if (entry.txId && row && state.known && ((entry.action === 'lock' && state.locked)
      || (entry.action === 'unlock' && row.unlock_date !== entry.baselineUnlockDate))) {
      dropPlan(uid);
      continue;
    }
    if (row && !state.known) { entry.blockedReason = 'The live deed lock state is not known yet. Refresh before signing.'; continue; }   // unknown facts never delete a plan
    if (!row || (entry.action === 'lock') !== !state.locked) {
      if (!entry.txId || (row && state.known && ((entry.action === 'lock') === state.locked))) dropPlan(uid);
      else entry.blockedReason = 'The live deed lock state changed. Refresh before signing.';
    } else if (state.days !== entry.baselineDays || row.unlock_date !== entry.baselineUnlockDate) {
      entry.blockedReason = 'The live deed lock period changed. Undo and review again.';
    } else delete entry.blockedReason;
  }
  try { savePlans(); } catch (_) { /* Persistence failure is reported before staging. */ }
  // a signed entry still without a game verdict (e.g. after a reload) is looked up again by its id.
  for (const [uid, entry] of STAGED_PLOT_LOCKS) {
    if (entry.txId && !entry.accepted && !entry.notFound) queueMicrotask(() => { verifySignedPlotLock(uid).catch(() => {}); });
  }
}

function announce(uid, status, message = '') {
  window.dispatchEvent(new CustomEvent('ceres:plot-lock-verdict', { detail: { uid, status, message } }));
}

// one read for the shared confirmer. `fetchImpl` is injectable for tests; the clock is the confirmer's seam.
export async function verifySignedPlotLock(uid, { fetchImpl = (...args) => fetch(...args) } = {}) {
  const start = STAGED_PLOT_LOCKS.get(uid);
  if (!start?.txId || start.accepted || start.notFound || verifying.has(uid)) return null;
  verifying.add(uid);
  const txId = start.txId;
  const action = start.action;
  const same = () => { const entry = STAGED_PLOT_LOCKS.get(uid); return entry && entry.txId === txId ? entry : null; };
  // fix round 2 (astra-r2 shape 5): the verdict is the OPERATION's (the game's record of this transaction), read
  // even when the plan has left this page meanwhile; a missing plan is never "confirmed".
  const check = async ({ signal } = {}) => {
    const response = await fetchImpl(playerQueryAppend('/api/plot-lock/verify'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tx_id: txId, deed_uid: uid, action }), ...(signal ? { signal } : {}) });
    if (!response.ok) throw Error(`plot-lock verify ${response.status}`);
    const result = await response.json();
    if (result?.status === 'failed') return { state: 'refused', result };
    if (result?.status === 'accepted') return { state: 'confirmed', result };
    return { state: 'pending' };
  };
  try {
    const signedAt = Number.isFinite(start.signedAt) ? new Date(start.signedAt).toISOString() : undefined;
    const answer = start.operations
      ? await confirmAction({ account: planAccount, operations: start.operations, txId, check: (_tx, options) => check(options) })
      : await waitThrough({ key: `plot-lock:${uid}:${txId}`, account: planAccount, txId, since: signedAt, check }).done;
    const entry = same();
    if (!entry || answer.gone) return 'gone';
    if (answer.state === 'refused') {
      dropPlan(uid); reviews.delete(uid);
      try { savePlans(); } catch (_) { /* the in-memory state is already correct */ }
      announce(uid, 'failed', String(answer.result?.message || 'The game refused this deed lock change.'));
      return 'failed';
    }
    if (answer.state === 'confirmed') {
      entry.accepted = true;
      try { savePlans(); } catch (_) { /* in memory */ }
      announce(uid, 'accepted');
      return 'accepted';
    }
    entry.notFound = true;   // never sent: Hive has no record past its lifetime
    try { savePlans(); } catch (_) { /* in memory */ }
    announce(uid, 'not_found');
    return 'not_found';
  } finally { verifying.delete(uid); }
}

// a signed lock the game has no record of is dismissed; nothing else is.
export function dismissPlotLock(uid) {
  const entry = STAGED_PLOT_LOCKS.get(uid);
  if (!entry?.notFound) return false;
  dropPlan(uid); reviews.delete(uid);
  try { savePlans(); } catch (_) { /* in memory */ }
  return true;
}

// Plain words for a signed entry's state, shared by the Changes surfaces.
export function signedPlotLockText(entry) {
  if (!entry?.txId) return '';
  if (entry.notFound) return 'Not sent — Hive has no record of it; your deed did not change.';
  if (entry.accepted) return 'Accepted — waiting for the live deed state';
  return 'Signed — Ceres is confirming it with the game; nothing is signed again.';
}

export function togglePlotLock(row) {
  if (!planAccount || !deedId(row?.deed_uid)) return false;
  const state = plotLockState(row);
  if (!state.known) return false;
  const current = STAGED_PLOT_LOCKS.get(row.deed_uid);
  if (current?.txId) return false;
  if (!current && state.unlocking) return false;
  if (current) dropPlan(row.deed_uid);
  else STAGED_PLOT_LOCKS.set(row.deed_uid, {
    deed_uid: row.deed_uid, action: state.locked ? 'unlock' : 'lock', baselineDays: state.days,
    baselineUnlockDate: row.unlock_date ?? null, lockDays: 3,
  });
  reviews.delete(row.deed_uid);
  try { savePlans(); } catch (_) {
    if (current) STAGED_PLOT_LOCKS.set(row.deed_uid, current); else STAGED_PLOT_LOCKS.delete(row.deed_uid);
    return false;
  }
  return true;
}

export function clearPlotLockPlans() {
  for (const [uid, entry] of STAGED_PLOT_LOCKS) if (!entry.txId) { dropPlan(uid); reviews.delete(uid); }
  savePlans();
}

export function plotLockSvg(locked) {
  const shackle = locked
    ? 'M8 10V7a4 4 0 0 1 8 0v3'
    : 'M8 10V6.8a4 4 0 0 0-7.1-2.5L.3 5';
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M6 10h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z"/><path d="${shackle}"/><path d="M12 15v2"/></svg>`;
}

export function initializePlotLockHeaderIcon() {
  const slot = document.querySelector('th[data-key="lock_state"] .plot-lock-icon');
  if (slot) slot.innerHTML = plotLockSvg(true);
}

export function plotLockIconHtml(row, { button = false } = {}) {
  const state = plotLockState(row);
  const entry = STAGED_PLOT_LOCKS.get(row.deed_uid);
  const isStaged = !!entry && !entry.txId;
  const label = isStaged ? (entry.action === 'lock' ? 'Lock staged' : 'Unlock staged')
    : entry?.notFound ? 'Signed lock change not found — dismiss it in Changes'
    : !state.known ? 'Lock state unavailable' : state.locked ? 'Locked' : 'Unlocked';
  const targetLocked = isStaged ? entry.action === 'lock' : state.locked;
  const period = !isStaged && state.locked && state.unlocksAt !== null
    ? ` · unlocks ${new Date(state.unlocksAt).toLocaleString()}` : '';
  const tip = tipAttrs({ title: `${label}${period}`, body: isStaged ? 'Click to undo' : '', peer: 'plot_metrics' });
  const icon = plotLockSvg(targetLocked);
 // (maintainer): in the board cell a change that is staged but not signed yet shows the TARGET padlock -- closed for a
  // staged lock, open for a staged unlock -- in warn orange, pulsing like the badge clock (shared pulse timeline). Once signed
  // (entry.txId) isStaged is false and the cell shows the current state again: the orange pulse is gone.
  if (!button && isStaged) return `<span class="plot-lock-icon is-staged-lock" role="img" aria-label="${esc(label)}"${tip}><span class="plot-lock-staged">${icon}</span></span>`;
  if (!button) return `<span class="plot-lock-icon" role="img" aria-label="${esc(label)}${esc(period)}"${tip}><span class="${state.known ? targetLocked ? 'is-locked' : 'is-unlocked' : ''}">${icon}</span></span>`;
  return `<button type="button" class="plot-lock-button ceres-btn ceres-btn--secondary${isStaged ? ' is-staged' : ''}" data-control-density="compact" data-plot-lock-action="toggle" data-deed-uid="${esc(row.deed_uid)}" aria-label="${esc(isStaged ? `${label}; click to undo` : state.unlocking ? 'Unlocking' : state.locked ? 'Stage unlock' : 'Stage lock')} for plot ${esc(formatPlotLabel(row.plot))}"${!state.known || !!entry?.txId || (state.unlocking && !entry) ? ' disabled' : ''}${tip}>${icon}</button>`;
}

export function plotLockReview(uid) { return reviews.get(uid) || null; }
export function invalidatePlotLockReview(uid) { reviews.delete(uid); }

export async function preparePlotLockReview(row) {
  if (!walletCanSignTransaction(planAccount)) throw new Error('Locking needs Hive Keychain (Active key)');
  const entry = STAGED_PLOT_LOCKS.get(row?.deed_uid);
  const state = plotLockState(row);
  if (!entry || entry.txId || entry.blockedReason || !state.known || state.unlocking || (entry.action === 'lock') !== !state.locked) throw new Error('The live deed state no longer matches this staged change.');
  const id = entry.action === 'lock' ? 'sm_lock_assets' : 'sm_unlock_assets';
  const payload = entry.action === 'lock'
    ? { type: 'item', lock_days: 3, asset_ids: [row.deed_uid], app: currentCeresAppIdentity(), n: crypto.randomUUID() }
    : { type: 'item', asset_ids: [row.deed_uid], app: currentCeresAppIdentity(), n: crypto.randomUUID() };
  const operation = ['custom_json', { required_auths: [planAccount], required_posting_auths: [], id, json: JSON.stringify(payload) }];
  const operations = [operation];
  const witness = { id, json: operation[1].json };
  const prepared = (await prepareSigningBatch(planAccount, [operations], 'Active', { reviewedActiveLock: witness }))[0];
  const review = { account: planAccount, uid: row.deed_uid, plot: formatPlotLabel(row.plot), action: entry.action,
    lockDays: entry.lockDays, baselineDays: entry.baselineDays, baselineUnlockDate: entry.baselineUnlockDate,
    operations, prepared, witness };
  reviews.set(row.deed_uid, review);
  return review;
}

export async function signPlotLockReview(row) {
  if (!walletCanSignTransaction(planAccount)) throw new Error('Locking needs Hive Keychain (Active key)');
  const entry = STAGED_PLOT_LOCKS.get(row?.deed_uid);
  const previous = reviews.get(row?.deed_uid);
  const actionId = entry?.actionId || (previous?.action === entry?.action && previous?.baselineDays === entry?.baselineDays
    ? confirmationState(planAccount, previous.operations)?.actionId || null : null);
  // A review and its signed envelope are consumed as a pair. A retry gets a new UUID.
  reviews.delete(row?.deed_uid);
  const review = await preparePlotLockReview(row);
  if (!entry || !review || entry.txId || entry.blockedReason || review.account !== planAccount || review.action !== entry.action
    || review.lockDays !== entry.lockDays || review.baselineDays !== entry.baselineDays || review.baselineUnlockDate !== entry.baselineUnlockDate
    || JSON.stringify(review.prepared.transaction.operations) !== JSON.stringify(review.operations)) throw new Error('The prepared lock operation changed. Try signing again.');
  const current = plotLockState(row);
  if (!current.known || current.days !== entry.baselineDays || row.unlock_date !== entry.baselineUnlockDate) throw new Error('The live deed lock state changed. Refresh before signing.');
  reviews.delete(row.deed_uid);
  let result;
  try {
    result = await requestSignatureThroughGate(planAccount, review.operations, null, 'Active', review.prepared,
      { reviewedActiveLock: review.witness, actionId });
  } catch (error) {
    entry.actionId = confirmationState(planAccount, review.operations)?.actionId || actionId || null;
    savePlans();
    throw error;
  }
  entry.actionId = confirmationState(planAccount, review.operations)?.actionId || actionId || null;
  entry.txId = result.txId;
  entry.operations = review.operations;   // the shared confirmer's identity for this lock
  entry.signedAt = Date.now();
  savePlans();
  queueMicrotask(() => { verifySignedPlotLock(row.deed_uid).catch(() => {}); });
  return result;
}
