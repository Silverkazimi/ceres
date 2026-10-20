import { esc, formatPlotLabel } from './format.js';
import { tipAttrs } from './tooltip.js';
import { prepareSigningBatch, requestSignatureThroughGate } from './broadcast-gate.js';
import { walletCanSignTransaction } from './wallet.js';
import { currentCeresAppIdentity } from './transaction-queue.js';
import { playerQueryAppend } from './board.js';

const LOCK_PLAN_PREFIX = 'ceres:plot-lock-plan:v1:';
export const STAGED_PLOT_LOCKS = new Map();
let planAccount = null;
const reviews = new Map();
const deedId = (value) => /^I-[0-9]+-[A-Za-z0-9]+$/.test(value || '');
// a signed lock/unlock used to wait for
// the board's deed state alone, so a lock the game refused left the button disabled forever. Its transaction is now
// read by id (never a history scan) on a fixed schedule after the signature: accepted -> it keeps waiting for the live
// deed state; refused -> the entry goes and the game's reason is shown; unknown once the claims window (, 25 s)
// has passed -> "not found" with a dismiss; no answer at all -> Check again.
export const LOCK_VERIFY_SCHEDULE_MS = [3000, 8000, 15000, 30000, 60000];
export const LOCK_NOT_FOUND_AFTER_MS = 25000;
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
    ...(entry.accepted === true ? { accepted: true } : {}),
    ...(entry.notFound === true ? { notFound: true } : {}),
  };
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

// one bounded run of by-id checks for a signed lock. Dependencies are injectable for tests.
export async function verifySignedPlotLock(uid, { fetchImpl = (...args) => fetch(...args), sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), now = () => Date.now() } = {}) {
  const start = STAGED_PLOT_LOCKS.get(uid);
  if (!start?.txId || start.accepted || start.notFound || verifying.has(uid)) return null;
  verifying.add(uid);
  const txId = start.txId;
  const signedAt = Number.isFinite(start.signedAt) ? start.signedAt : now();
  const same = () => { const entry = STAGED_PLOT_LOCKS.get(uid); return entry && entry.txId === txId ? entry : null; };
  let last = null;
  try {
    for (const offset of LOCK_VERIFY_SCHEDULE_MS) {
      const wait = signedAt + offset - now();
      if (wait > 0) await sleep(wait);
      const entry = same();
      if (!entry) return 'gone';  // the board confirmed it (or it was dismissed) meanwhile
      let result;
      try {
        const response = await fetchImpl(playerQueryAppend('/api/plot-lock/verify'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tx_id: txId, deed_uid: uid, action: entry.action }) });
        if (!response.ok) { last = 'error'; continue; }
        result = await response.json();
      } catch (_) { last = 'error'; continue; }
      last = result?.status || 'error';
      if (last === 'failed') {
        dropPlan(uid); reviews.delete(uid);
        try { savePlans(); } catch (_) { /* the in-memory state is already correct */ }
        announce(uid, 'failed', String(result.message || 'The game refused this deed lock change.'));
        return 'failed';
      }
      if (last === 'accepted') {
        entry.accepted = true; delete entry.checkPaused;
        try { savePlans(); } catch (_) { /* in memory */ }
        announce(uid, 'accepted');
        return 'accepted';
      }
      if (last === 'not_found' && now() - signedAt >= LOCK_NOT_FOUND_AFTER_MS) break;
    }
    const entry = same();
    if (!entry) return 'gone';
    if (last === 'not_found') {
      entry.notFound = true; delete entry.checkPaused;
      try { savePlans(); } catch (_) { /* in memory */ }
      announce(uid, 'not_found');
      return 'not_found';
    }
    entry.checkPaused = true;  // no answer from the game at all: say so, with Check again
    announce(uid, 'paused');
    return 'paused';
  } finally { verifying.delete(uid); }
}

// "Check again" on a signed lock whose checks got no answer; restarts the bounded schedule from now.
export function recheckPlotLock(uid) {
  const entry = STAGED_PLOT_LOCKS.get(uid);
  if (!entry?.txId || entry.accepted) return false;
  delete entry.checkPaused; delete entry.notFound; entry.signedAt = Date.now();
  try { savePlans(); } catch (_) { /* in memory */ }
  verifySignedPlotLock(uid).catch(() => {});
  return true;
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
  if (entry.notFound) return 'Transaction not found — the game has no record of it; your deed did not change.';
  if (entry.accepted) return 'Accepted — waiting for the live deed state';
  if (entry.checkPaused) return 'No answer from the game yet. Check again reads it once more; nothing is signed again.';
  return 'Signed — checking with the game…';
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
  const review = reviews.get(row?.deed_uid) || await preparePlotLockReview(row);
  if (!entry || !review || entry.txId || entry.blockedReason || review.account !== planAccount || review.action !== entry.action
    || review.lockDays !== entry.lockDays || review.baselineDays !== entry.baselineDays || review.baselineUnlockDate !== entry.baselineUnlockDate
    || JSON.stringify(review.prepared.transaction.operations) !== JSON.stringify(review.operations)) throw new Error('The prepared lock operation changed. Try signing again.');
  const current = plotLockState(row);
  if (!current.known || current.days !== entry.baselineDays || row.unlock_date !== entry.baselineUnlockDate) throw new Error('The live deed lock state changed. Refresh before signing.');
  reviews.delete(row.deed_uid);
  const result = await requestSignatureThroughGate(planAccount, review.operations, null, 'Active', review.prepared,
    { reviewedActiveLock: review.witness });
  entry.txId = result.txId;
  entry.signedAt = Date.now();
  savePlans();
  queueMicrotask(() => { verifySignedPlotLock(row.deed_uid).catch(() => {}); });
  return result;
}
