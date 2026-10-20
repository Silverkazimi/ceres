// phase 2 (maintainer-accepted stage planner, staged
// changes are the maintainer's plans -- kept per account in this browser (K1) until undone or signed, and re-checked against
// the page load's own live capture before they are drawn (: game facts never come from the store).
import { BOARD, CURRENT_PLAYER, adoptLatestCapture, catalogNotice, noticeMessage, playerQueryAppend } from './board.js';
import { isRuniWorker } from './format.js';
import { mergeStaged, compactChains, withStagedAccountLock } from './account-space.js';
import { batchCheckoutRenders, renderCheckoutSurface } from './checkout.js';
import { EXPANDED_DATA_LOADED, updateMountedRow } from './rows.js';
import { CERES_STAGE, CERES_STAGED_WORKSITES, applyRemovalImpactToStage, applyStageToRow, cloneStageAsset, refreshStageImpactForRow, removalImpact, restoreStagedMove, stageForSlot, stageToast } from './staging.js';

import { deriveChain, migrateChainNumbers, validChainPlan } from './combine-chain.js';

const LISTENERS = new Set();
export function mintTransactionRunId(now = new Date()) {
  const minute = now.toISOString().slice(0, 16);
  const hash = (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`).replace(/-/g, '').slice(0, 8);
  return `run-${minute}-${hash}`;
}
export function onPlansChanged(fn) { LISTENERS.add(fn); }
export function dropReasonText(reason, detail = null) {
  // the live check names the one cause (listed, rented, delegated, locked); show that, not all three.
  // (audit #8, ): the server's detail is never shown as text -- it is read as a code with a validated parameter
  // and every sentence is Ceres's own; anything unrecognised gets the local generic sentence.
  if (reason === 'card_unavailable' && typeof detail === 'string') {
    if (/^listed\b/.test(detail)) return 'this card is listed on the market now';
    if (/^rented\b/.test(detail)) return 'this card is rented out now';
    const delegated = /^delegated to ([a-z][a-z0-9.-]{2,15})$/.exec(detail);
    if (delegated) return `this card is delegated to ${delegated[1]} now`;
    if (/^delegated\b/.test(detail)) return 'this card is delegated now';
    const locked = /^locked until (\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})?)?)$/.exec(detail);
    if (locked) return `this card is locked until ${locked[1]}`;
    if (/^locked\b/.test(detail)) return 'this card is locked now';
  }
  return DROP_TEXT[reason] || 'this change cannot be applied to the live board now';
}
export function clearDroppedPlans() { if (PLANS.dropped.length) { PLANS.dropped = []; PLANS.lastSig = null; } } // closing the modal clears the list
export function dismissDroppedPlan(index) { if (index >= 0 && index < PLANS.dropped.length) { PLANS.dropped.splice(index, 1); PLANS.lastSig = null; persistStagePlans(); } }

const PLANS = { open: false, ready: false, account: null, restoreId: null, revision: 0, pending: new Map(), pendingWorksites: new Map(), chains: {}, dropped: [], lastSig: null, warned: new Set(), removed: {}, known: new Set() };
const DROP_TEXT = {
  card_gone: 'this card or item is no longer in your collection and is not on this plot',
  card_unavailable: 'this card cannot be used now (listed, delegated, rented, or locked)',
  plot_gone: 'this plot is no longer yours',
  slot_taken: 'a different card is in this slot now',
  evict_gone: 'the card this plan would replace is no longer in this slot',
  card_moved: 'this card is staked on a different plot now',
  duplicate_staged: 'this card or item was staged on another plot or slot too',
  invalid_plan: 'the saved plan could not be read',
  worksite_unavailable: 'this worksite build is not available on the live plot now',
};

const WORKSITE_RETRY_DELAYS_MS = [250, 750];
let boardRevealRetryQueued = false;

function schedulePendingWorksiteRetry() {
  if (!PLANS.pendingWorksites.size) return;
  if (!PLANS.ready) {
    boardRevealRetryQueued = true;
    return;
  }
  boardRevealRetryQueued = false;
  queueMicrotask(() => {
    if (PLANS.pendingWorksites.size && PLANS.ready) restoreStagePlans();
  });
}

window.addEventListener('ceres:board-revealed', schedulePendingWorksiteRetry);
window.addEventListener('ceres:board-capture-settled', schedulePendingWorksiteRetry);

export function planKey(entry) { return `${entry.deed_uid}|${entry.group_id}|${entry.slot_index}`; }
function storeKey(account) { return account ? `ceres:stage-plan:v1:${account}` : null; }
function warnOnce(code) {
  if (PLANS.warned.has(code)) return;
  PLANS.warned.add(code);
  stageToast(noticeMessage(catalogNotice(code, {})), true);
}

function readStore(account) {
  const key = storeKey(account);
  const empty = { version: 1, revision: 0, plans: {}, worksites: {}, chains: {}, dropped: [] };
  if (!key) return empty;
  let raw = null;
  try { raw = localStorage.getItem(key); } catch (error) { warnOnce('stage_plans_store_unavailable'); return empty; }
  if (!raw) return empty;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || typeof parsed.plans !== 'object') throw new Error('malformed');
    return { ...empty, ...parsed };
  } catch (error) {
    // B03: retain corrupt bytes; an unlocked reader must never delete a newer reservation.
    try { localStorage.setItem(`${key}:corrupt:${new Date().toISOString()}`, raw); } catch (_) { /* keep going */ }
    warnOnce('stage_plans_corrupt');
    return empty;
  }
}

// Guard 1 (plan §F.1): closed from the start of a board load or player switch until the new account's plans are
// loaded, so the load's own stage wipe is never written over the kept plans.
let restoreSerial = 0;
export function closePlanStore() { restoreSerial += 1; PLANS.open = false; PLANS.ready = false; }
export function stagePlansReady() { return PLANS.ready; }
export function pendingWorksitePlanCount(deedUid = null) { return deedUid ? Number(PLANS.pendingWorksites.has(deedUid)) : PLANS.pendingWorksites.size; }
export function pendingStagePlanCount() { return PLANS.pending.size + PLANS.pendingWorksites.size; }
export function pendingIncomingRefElsewhere(ref, deedUid, groupId, slotIndex) {
  if (!ref) return false;
  return [...PLANS.pending.values()].some((plan) => plan.action !== 'unstake' && String(plan.asset_ref) === String(ref)
    && (plan.deed_uid !== deedUid || plan.group_id !== groupId || String(plan.slot_index) !== String(slotIndex)));
}
export function discardPendingStagePlans() {
  const account = String(CURRENT_PLAYER || BOARD?.player || '').toLowerCase();
  if (!account || PLANS.account !== account || PLANS.restoreId !== restoreSerial) return false;
  restoreSerial += 1;
  PLANS.restoreId = restoreSerial;
  PLANS.pending.clear();
  PLANS.pendingWorksites.clear();
  PLANS.open = true;
  PLANS.ready = true;
  return true;
}

function planFromEntry(entry) {
  if (!entry._plan_created_at) entry._plan_created_at = new Date().toISOString();
  return {
    deed_uid: entry.deed_uid, group_id: entry.group_id, slot_index: entry.slot_index, action: entry.action,
    asset_ref: entry.asset_ref || null, evict_asset_ref: entry.evict_asset_ref || null,
    source_deed_uid: entry._source_deed_uid || null, name: entry._asset_name || null,
    asset: cloneStageAsset(entry._asset), previous_asset: cloneStageAsset(entry._previous_asset),
    origin: entry._plan_origin || 'staged', unstake_tx: entry._unstake_tx || null, created_at: entry._plan_created_at,
    chain_id: entry.chain_id || null, chain_input_uid: entry.chain_input_uid || null,
    combine_verified_at: entry.combine_verified_at || null,
    base: entry.base || null, // the Base PP estimate survives a reload
    // phase 3: a refusal survives reload -- it can only set a move aside, never authorize one.
    refused_at: entry.refused_at || null, refusal_reason: entry.refusal_reason || null,
  };
}

// One snapshot write at the choke point every stage, undo and refusal passes through (renderCheckoutSurface).
// B03: every ordinary writer joins the reservation lock. Capture the account
// before waiting; the locked writer has no await and cannot re-enter this coordinator.
export function persistStagePlans() {
  const account = PLANS.account;
  return withStagedAccountLock(account, locked => {
    if (!locked) throw new Error('Safe staged-plan locking is unavailable.');
    if (account !== PLANS.account) return;
    persistStagePlansLocked();
  }).catch(() => { warnOnce('stage_plans_store_unavailable'); });
}
function persistStagePlansLocked() {
  LISTENERS.forEach((fn) => { try { fn(); } catch (error) { /* a listener never blocks a write */ } }); // phase 5: the Planner follows
  if (!PLANS.open || !PLANS.account) return;
  const key = storeKey(PLANS.account);
  const plans = {};
  PLANS.pending.forEach((plan, planId) => { plans[planId] = plan; });
  CERES_STAGE.forEach((entry) => {
    if (entry.txId || entry._synthetic || !entry.deed_uid) return; // signed moves live in the in-flight store
    plans[planKey(entry)] = planFromEntry(entry);
  });
  const worksites = Object.fromEntries(PLANS.pendingWorksites);
  CERES_STAGED_WORKSITES.forEach((entry, deedUid) => {
    delete worksites[deedUid];
    if (!entry?.target || entry.txId) return;
    worksites[deedUid] = {
      deed_uid: deedUid, target: entry.target, targetLabel: entry.targetLabel || entry.target,
      timeCrystals: entry.timeCrystals === 50 ? 50 : 0, firstClear: entry.firstClear === true,
      created_at: entry.created_at || new Date(entry.stagedAt || Date.now()).toISOString(),
    };
  });
  const dropped = []; // dropped plans are not kept (they were sticky across reloads until dismissed)
  // A plan that leaves the store (undone, signed, done on the chain) is remembered as removed, so another device's copy of it is not brought back when staged changes are synced.
  const now = new Date().toISOString();
  const present = new Set([...Object.keys(plans), ...Object.keys(worksites).map((id) => `w:${id}`)]);
  const removed = { ...PLANS.removed };
  PLANS.known.forEach((id) => { if (!present.has(id)) removed[id] = now; });
  present.forEach((id) => { const plan = id.startsWith('w:') ? worksites[id.slice(2)] : plans[id]; if (removed[id] && !(Date.parse(plan?.created_at) > Date.parse(removed[id]))) return; delete removed[id]; });
  PLANS.chains = compactChains(PLANS.chains);
  const sig = JSON.stringify([plans, worksites, PLANS.chains, dropped, Object.keys(removed).sort()]);
  if (sig === PLANS.lastSig) return;
  try {
    const stored = JSON.parse(localStorage.getItem(key) || 'null');
    if (stored && (Number(stored.revision) > PLANS.revision || Object.entries(stored.chains || {}).some(([id, plan]) =>
      (plan.combine && plan.combine.nonce !== PLANS.chains[id]?.combine?.nonce && plan.combine.nonce !== PLANS.chains[id]?.cleared_combine_nonce)
      || (plan.state === 'cancelled' && PLANS.chains[id]?.state !== 'cancelled')))) {
      // (audit A12): another tab wrote meanwhile. This tab's edits are MERGED with what that tab saved (never skipped, never over-written), and the page then
      // adopts what the other tab staged.
      const merged = mergeStaged({ plans, worksites, chains: PLANS.chains, removed }, stored);
      const mergedRevision = Number(stored.revision) + 1;
      localStorage.setItem(key, JSON.stringify({ ...merged, version: 1, revision: mergedRevision, account: PLANS.account, dropped }));
      PLANS.revision = mergedRevision;
      PLANS.removed = merged.removed;
      PLANS.chains = merged.chains;
      PLANS.known = new Set([...Object.keys(merged.plans), ...Object.keys(merged.worksites).map((id) => `w:${id}`)]);
      PLANS.lastSig = null;
      if (Object.keys(merged.plans).length !== Object.keys(plans).length || Object.keys(merged.worksites).length !== Object.keys(worksites).length) queueMicrotask(() => restoreStagePlans());
      return;
    }
    const revision = PLANS.revision + 1;
    localStorage.setItem(key, JSON.stringify({ version: 1, revision, account: PLANS.account, plans, worksites, chains: PLANS.chains, dropped, removed }));
    PLANS.revision = revision;
    PLANS.removed = removed;
    PLANS.known = present;
    PLANS.lastSig = sig;
    if (PLANS.migrateKey) { localStorage.removeItem(PLANS.migrateKey); PLANS.migrateKey = null; }
  } catch (error) {
    warnOnce('stage_plans_store_unavailable');
  }
}

export function droppedStagePlans() { return PLANS.dropped.slice(); }

function keptPlanIsFulfilled(plan, verdict) {
  if (verdict?.verdict === 'done') return true;
  if (!['stake', 'replace'].includes(plan?.action)) return false;
  const row = BOARD?.plots?.find((plot) => String(plot.deed_uid) === String(plan.deed_uid));
  if (!row) return false;
  let occupant = null;
  if (plan.group_id === 'worker') {
    const liveSlot = Number(plan.slot_index) + 1;
    occupant = Array.isArray(row.workers) ? row.workers.find((worker) => Number(worker.slot) === liveSlot) : null;
  } else if (plan.group_id === 'runi') {
    occupant = Array.isArray(row.workers) ? row.workers.find((worker) => isRuniWorker(worker)) : null;
  } else {
    occupant = row.bonus?.[plan.group_id] || null;
  }
  const liveRef = occupant?.asset_ref || occupant?.uid || occupant?.card_uid || occupant?.item_uid;
  return liveRef != null && String(liveRef) === String(plan.asset_ref);
}

function keptPlanVerdict(plan, verdict) {
  const liveSource = verdict?.verdict === 'ok' ? verdict.location?.deed_uid : null;
  if (liveSource && String(liveSource) !== String(plan.source_deed_uid || '')) {
    return { ...verdict, verdict: 'drop', reason: 'card_moved' };
  }
  return verdict;
}

function stageFromPlan(plan, verdict) {
  if (keptPlanIsFulfilled(plan, verdict)) return null;
  const cooldown = plan.action === 'unstake' ? null : (verdict.cooldown || null);
  const fallbackName = verdict.live?.name || plan.name || plan.asset_ref;
  const asset = plan.asset
    ? { ...plan.asset, cooldown }
    : plan.action === 'unstake' ? null : { asset_ref: plan.asset_ref, uid: plan.asset_ref, name: fallbackName, cooldown };
  const liveSource = verdict.location?.deed_uid && verdict.location.deed_uid !== plan.deed_uid ? verdict.location.deed_uid : null;
  if (stageForSlot(plan.deed_uid, plan.group_id, plan.slot_index, plan.action, plan.asset_ref, null, plan.evict_asset_ref || null,
    fallbackName, liveSource, plan.base || null, cooldown, asset) === false) return null; // base restored
  const entry = CERES_STAGE.find((item) => planKey(item) === planKey(plan) && !item.txId);
  if (!entry) return;
  entry.chain_id = plan.chain_id || null;
  entry.chain_input_uid = plan.chain_input_uid || null;
  entry.combine_verified_at = plan.combine_verified_at || null;
  entry._plan_origin = plan.origin || 'staged';
  entry._plan_created_at = plan.created_at || entry._plan_created_at;
  if (plan.unstake_tx) entry._unstake_tx = plan.unstake_tx;
  if (plan.refused_at) { entry.refused_at = plan.refused_at; entry.refusal_reason = plan.refusal_reason || null; }
  if (verdict.verdict === 'drop') {
    entry._plan_invalid_reason = verdict.reason || 'invalid_plan';
    entry._plan_invalid_detail = verdict.detail || null;
  }
  if (!entry._previous_asset) entry._previous_asset = cloneStageAsset(plan.action === 'unstake' ? (plan.previous_asset || plan.asset) : plan.previous_asset);
}

async function checkPlans(plans) {
  const account = CURRENT_PLAYER || BOARD?.player;
  const current = () => account === (CURRENT_PLAYER || BOARD?.player);
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (!current()) throw new Error('the account changed');
    const response = await fetch(playerQueryAppend('/api/stage-plans/check'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plans, capture_generated_at: BOARD?.generated_at || null }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!current()) throw new Error('the account changed');
    if (response.status === 409 && payload?.detail?.code === 'capture_superseded') { await adoptLatestCapture(); continue; } // idiom
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const verdicts = payload.verdicts || [];
    const missingDeeds = [...new Set(verdicts.filter((v) => v.verdict === 'needs_assets').map((v) => v.deed_uid))];
    if (!missingDeeds.length) return verdicts;
    // plots missing details are read three at a time instead of one after another.
    for (let index = 0; index < missingDeeds.length; index += 3) {
      if (!current()) throw new Error('the account changed');
      const reads = await Promise.all(missingDeeds.slice(index, index + 3).map((deedUid) =>
        fetch(playerQueryAppend(`/api/plots/${encodeURIComponent(deedUid)}/expand`), { method: 'POST' })));
      const failed = reads.find((expanded) => !expanded.ok);
      if (failed) throw new Error(`Plot details HTTP ${failed.status}`);
    }
    if (!current()) throw new Error('the account changed');
    await adoptLatestCapture();
  }
  throw new Error('the board kept changing or plot details remain unavailable');
}

// phase 4: the route checks at most 200 plans per request; larger sets go in serial chunks, and the verdicts
// are used only when every chunk was checked against the same capture (else the whole pass is repeated).
async function checkAllPlans(request) {
  for (let pass = 0; pass < 3; pass += 1) {
    const stamp = BOARD?.generated_at;
    const verdicts = [];
    for (let index = 0; index < request.length; index += 200) verdicts.push(...await checkPlans(request.slice(index, index + 200)));
    if (BOARD?.generated_at === stamp) return verdicts;
  }
  throw new Error('the board kept changing');
}

async function fetchWorksiteDetailWithRetry(deedUid, isCurrent) {
  for (let attempt = 0; attempt <= WORKSITE_RETRY_DELAYS_MS.length; attempt += 1) {
    if (!isCurrent()) return null;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(playerQueryAppend('/api/plots/' + encodeURIComponent(deedUid)), { cache: 'no-store', signal: controller.signal });
      if (response.ok) return await response.json().catch(() => null);
      if (![409, 503].includes(response.status)) return null;
    } catch (error) {
      // Network failures and the per-read timeout are transient during board finishing.
    } finally {
      clearTimeout(timeout);
    }
    if (attempt < WORKSITE_RETRY_DELAYS_MS.length) {
      await new Promise((resolve) => setTimeout(resolve, WORKSITE_RETRY_DELAYS_MS[attempt]));
    }
  }
  return null;
}

// (audit A11): a worksite build that cannot be restored is told once, but its plan is kept (newest 50) so nothing the player staged is destroyed silently.
function archiveWorksitePlan(deedUid, plan, reason) {
  try {
    const key = `ceres:stage-plan-unrestored:v1:${PLANS.account}`;
    const kept = JSON.parse(localStorage.getItem(key) || '[]');
    const list = Array.isArray(kept) ? kept : [];
    list.push({ deed_uid: deedUid, reason, at: new Date().toISOString(), plan: plan ?? null });
    localStorage.setItem(key, JSON.stringify(list.slice(-50)));
  } catch (error) { /* the notice still tells the player */ }
}

async function restoreWorksitePlans(stored, isCurrent) {
  let restored = 0;
  let fulfilled = 0;
  for (const [deedUid, plan] of Object.entries(stored || {})) {
    if (!isCurrent()) return null;
    if (CERES_STAGED_WORKSITES.has(deedUid)) { PLANS.pendingWorksites.delete(deedUid); continue; }
    const row = BOARD?.plots?.find((plot) => String(plot.deed_uid) === String(deedUid));
    if (!plan || typeof plan.target !== 'string') {
      PLANS.pendingWorksites.delete(deedUid);
      archiveWorksitePlan(deedUid, plan, 'invalid_plan');
      PLANS.dropped.push({ deed_uid: deedUid, name: 'Worksite build', reason: 'invalid_plan' });
      continue;
    }
    if (!row) {
      PLANS.pendingWorksites.delete(deedUid);
      archiveWorksitePlan(deedUid, plan, 'plot_gone');
      PLANS.dropped.push({ ...plan, deed_uid: deedUid, name: plan.targetLabel || plan.target, reason: 'plot_gone' });
      continue;
    }
    // Lean board rows omit swap targets; only a successful full read can invalidate a saved build.
    const detail = await fetchWorksiteDetailWithRetry(deedUid, isCurrent);
    if (!isCurrent()) return null;
    if (CERES_STAGED_WORKSITES.has(deedUid)) { PLANS.pendingWorksites.delete(deedUid); continue; }
    if (!detail || String(detail.deed_uid) !== String(deedUid)) continue;
    const projectType = String(detail.worksite_facts?.project_type || '');
    const expectedConstruction = 'worksite_' + plan.target + '_construction';
    const pendingTarget = String(detail.pending_change?.to || detail.pending_change?.target || '').toLowerCase();
    if (projectType === expectedConstruction || pendingTarget === String(plan.target).toLowerCase()) {
      PLANS.pendingWorksites.delete(deedUid);
      fulfilled += 1;
      continue;
    }
    if (!Array.isArray(detail.swap_targets)) continue;
    const tile = detail.swap_targets.find((candidate) => candidate.target === plan.target);
    PLANS.pendingWorksites.delete(deedUid);
    if (tile?.is_current) { fulfilled += 1; continue; }
    if (!tile || projectType.endsWith('_construction') || (!!plan.firstClear !== !!tile.first_clear)) {
      archiveWorksitePlan(deedUid, plan, 'worksite_unavailable');
      PLANS.dropped.push({ ...plan, deed_uid: deedUid, name: plan.targetLabel || plan.target, reason: 'worksite_unavailable' });
      continue;
    }
    CERES_STAGED_WORKSITES.set(deedUid, {
      target: plan.target,
      targetLabel: tile.worksite_name || plan.targetLabel || plan.target,
      timeCrystals: tile.first_clear ? 0 : (plan.timeCrystals === 50 ? 50 : 0),
      firstClear: !!tile.first_clear,
      blockedReason: null,
      stagedAt: Date.parse(plan.created_at) || Date.now(),
      created_at: plan.created_at || new Date().toISOString(),
    });
    restored += 1;
  }
  return { restored, fulfilled };
}

// Plan §F.3: runs after the table is revealed, so it never delays first paint.
export async function restoreStagePlans() {
  const account = String(CURRENT_PLAYER || BOARD?.player || '').toLowerCase();
  if (!account) return;
  PLANS.ready = false;
  PLANS.open = false;
  const restoreId = ++restoreSerial;
  // Quiet board refreshes preserve this account's plan session; explicit reload/switch closes it.
  const isCurrent = () => restoreId === restoreSerial
    && String(CURRENT_PLAYER || BOARD?.player || '').toLowerCase() === account;
  const rawStore = readStore(account);
  const rawChains = rawStore?.chains && typeof rawStore.chains === 'object' && !Array.isArray(rawStore.chains) ? rawStore.chains : {};
  const malformedStored = new Set(Object.entries(rawChains)
    .filter(([, plan]) => !validChainPlan(migrateChainNumbers(JSON.parse(JSON.stringify(plan ?? null)))))
    .map(([id]) => id));
  const store = mergeStaged(rawStore, {}); // collapse duplicate refs even in a single device's old store
  PLANS.account = account;
  PLANS.restoreId = restoreId;
  PLANS.migrateKey = null; // (audit A14): a migration of another account never carries over
  PLANS.revision = Number(store.revision) || 0;
  PLANS.removed = store.removed && typeof store.removed === 'object' ? { ...store.removed } : {};
  PLANS.chains = store.chains && typeof store.chains === 'object' && !Array.isArray(store.chains) ? { ...store.chains } : {};
  // A03: never render malformed records, or overwrite their unresolved evidence.
  Object.values(PLANS.chains).forEach(migrateChainNumbers); // B01: migrate display facts only.
  const malformedChains = malformedStored.size > 0 || Object.values(PLANS.chains).some(plan => !validChainPlan(plan));
  PLANS.chains = Object.fromEntries(Object.entries(PLANS.chains).filter(([id, plan]) => !malformedStored.has(id) && validChainPlan(plan)));
  if (malformedChains) { PLANS.open = false; PLANS.ready = false; stageToast('A saved upgrade plan is malformed. Signing is held until it can be recovered.', true); return; }
  PLANS.known = new Set([...Object.keys(store.plans || {}), ...Object.keys(store.worksites || {}).map((id) => `w:${id}`)]);
  PLANS.pending = new Map();
  PLANS.pendingWorksites = new Map(Object.entries(store.worksites || {}));
  PLANS.dropped = []; // a dropped plan is told once, on the load that dropped it -- never carried over
  PLANS.lastSig = null;
  PLANS.warned = new Set();
  const inStage = new Set(CERES_STAGE.map(planKey)); // the in-flight store wins (restoreInFlightRecords ran first)
  Object.entries(store.plans || {}).forEach(([planId, plan]) => { if (plan && !inStage.has(planId)) PLANS.pending.set(planId, plan); });
  // phase 4: planned restakes saved by become plans; the old key goes after the first successful write.
  const restakeKey = `ceres:planned-restake:v1:${account}`;
  try {
    const restakes = JSON.parse(localStorage.getItem(restakeKey) || '{}') || {};
    let skipped = 0; // (audit A13): the old record is only removed when EVERY entry of it was carried over
    Object.values(restakes).forEach((item) => {
      if (!item?.target_deed_uid || !item.asset_ref) { skipped += 1; return; }
      const plan = { deed_uid: item.target_deed_uid, group_id: item.group_id || 'worker', slot_index: item.slot_index, action: 'stake',
        asset_ref: item.asset_ref, evict_asset_ref: null, source_deed_uid: item.source_deed_uid || null, name: item.name || null, asset: null, previous_asset: null,
        origin: 'restake', unstake_tx: item.unstake_tx || null, created_at: item.saved_at || new Date().toISOString() };
      if (!inStage.has(planKey(plan)) && !PLANS.pending.has(planKey(plan))) PLANS.pending.set(planKey(plan), plan);
      else if (PLANS.pending.get(planKey(plan))?.asset_ref !== plan.asset_ref) skipped += 1; // the slot already holds another plan: the old entry stays where it is
    });
    if (Object.keys(restakes).length && !skipped) PLANS.migrateKey = restakeKey;
  } catch (error) { /* an unreadable old record stays where it is */ }
  const uniquePending = mergeStaged({ plans: Object.fromEntries(PLANS.pending), removed: PLANS.removed }, {});
  if (PLANS.migrateKey && uniquePending.plans && Object.keys(uniquePending.plans).length !== PLANS.pending.size) PLANS.migrateKey = null;
  PLANS.pending = new Map(Object.entries(uniquePending.plans));
  PLANS.removed = uniquePending.removed;
  // In-flight entries were restored first. A saved copy of their ref in another
  // slot is not another reservation; keep the newer intent and tombstone the other.
  for (const [planId, plan] of PLANS.pending) {
    if (plan.action === 'unstake' || !plan.asset_ref) continue;
    const earlier = CERES_STAGE.find((entry) => entry.action !== 'unstake' && String(entry.asset_ref) === String(plan.asset_ref));
    if (!earlier) continue;
    const at = Date.parse(plan.created_at) || 0;
    const inFlightAt = Date.parse(earlier._plan_created_at) || 0;
    if (at <= inFlightAt || earlier.txId) {
      PLANS.pending.delete(planId);
      PLANS.removed[planId] = new Date().toISOString();
      if (plan.origin === 'restake') PLANS.migrateKey = null;
    } else {
      restoreStagedMove(earlier);
      CERES_STAGE.splice(CERES_STAGE.indexOf(earlier), 1);
      PLANS.removed[planKey(earlier)] = new Date().toISOString();
      refreshStageImpactForRow(earlier.deed_uid);
      updateMountedRow(earlier.deed_uid);
    }
  }
  renderCheckoutSurface(); // every saved change counts from the first frame, marked as being checked
  const worksiteResult = await restoreWorksitePlans(store.worksites, isCurrent);
  if (!worksiteResult || !isCurrent()) return;
  const restoredWorksites = worksiteResult.restored;
  let fulfilled = worksiteResult.fulfilled;
  if (restoredWorksites) [...CERES_STAGED_WORKSITES.keys()].forEach((deedUid) => updateMountedRow(deedUid));
  PLANS.open = true; // the next write equals the store plus anything staged since, so nothing is lost
  if (!PLANS.pending.size) {
    PLANS.ready = true;
    window.dispatchEvent(new CustomEvent('ceres:chain-plans-restored'));
    persistStagePlans();
    renderCheckoutSurface();
    if (PLANS.dropped.length) stageToast(PLANS.dropped.map((item) => noticeMessage(catalogNotice('stage_plan_dropped', { name: item.name || 'A plan', reason: dropReasonText(item.reason) }))).join(' '), true);
    else if (restoredWorksites) stageToast(noticeMessage(catalogNotice('stage_plans_restored', { count: restoredWorksites })));
    else if (fulfilled) stageToast(`${fulfilled} staged change${fulfilled === 1 ? ' was' : 's were'} already done — removed`);
    if (boardRevealRetryQueued) schedulePendingWorksiteRetry();
    return;
  }
  // the expected card does not exist in the collection yet. Restore its kept
  // placement locally with the same blocked cooldown; never ask the card-existence check.
  // staged in one batch; each touched plot is then refreshed once.
  const plannedDeeds = new Set();
  batchCheckoutRenders(() => {
    for (const [key, plan] of PLANS.pending) {
      if (plan.action === 'unstake' || !plan.asset?.planned || !plan.chain_id) continue;
      const chain = PLANS.chains[plan.chain_id];
      if (!chain || chain.state === 'cancelled') { PLANS.pending.delete(key); continue; }
      stageFromPlan(plan, { verdict: 'ok', cooldown: { ready: false, state: 'planned', ready_at: null, reason: 'after the combine' } });
      PLANS.pending.delete(key);
      plannedDeeds.add(plan.deed_uid);
    }
  });
  if (plannedDeeds.size) {
    const plotsByDeed = new Map((BOARD?.plots || []).map((row) => [row.deed_uid, row]));
    for (const deedUid of plannedDeeds) {
      const row = plotsByDeed.get(deedUid);
      if (row) { applyStageToRow(row, { force: true }); updateMountedRow(deedUid); }
    }
  }
  const request = [...PLANS.pending.entries()].map(([planId, plan]) => ({
    key: planId, deed_uid: plan.deed_uid, group_id: plan.group_id, slot_index: plan.slot_index,
    action: plan.action, asset_ref: plan.asset_ref, evict_asset_ref: plan.evict_asset_ref || null,
  }));
  let verdicts;
  try {
    verdicts = await checkAllPlans(request);
  } catch (error) {
    if (isCurrent() && PLANS.account === account) {
      PLANS.ready = true;
      renderCheckoutSurface();
      if (PLANS.pending.size) stageToast(noticeMessage(catalogNotice('stage_plans_unchecked', { count: PLANS.pending.size })), true);
    }
    return; // kept, not shown; checked again on the next load
  }
  if (!isCurrent() || PLANS.account !== account || !PLANS.open) return; // the player changed meanwhile
  let restored = restoredWorksites;
  const invalid = [];
  const touched = new Set();
  // every verdict is applied first, in one batch; the Changes surface renders once afterwards (below).
  batchCheckoutRenders(() => verdicts.forEach((liveVerdict) => {
    const plan = PLANS.pending.get(liveVerdict.key);
    if (!plan) return;
    PLANS.pending.delete(liveVerdict.key);
    const verdict = keptPlanVerdict(plan, liveVerdict);
    if (keptPlanIsFulfilled(plan, verdict)) { fulfilled += 1; return; }
    if (verdict.verdict === 'ok') {
      if (verdict.action === 'stake' && plan.action === 'replace') { plan.action = 'stake'; plan.evict_asset_ref = null; } // healed
      stageFromPlan(plan, verdict); restored += 1; touched.add(plan.deed_uid); return;
    }
    if (verdict.verdict === 'drop') {
      stageFromPlan(plan, verdict);
      invalid.push(plan);
      restored += 1;
      touched.add(plan.deed_uid);
    }
  }));
  PLANS.ready = true;
  window.dispatchEvent(new CustomEvent('ceres:chain-plans-restored'));
  persistStagePlans();
  renderCheckoutSurface();
  if (invalid.length) {
    stageToast(`${invalid.length} kept plan ${invalid.length === 1 ? 'is' : 'are'} no longer valid. Open Changes to review and Undo ${invalid.length === 1 ? 'it' : 'them'}.`, true);
  } else if (fulfilled) {
    stageToast(`${fulfilled} staged change${fulfilled === 1 ? ' was' : 's were'} already done — removed`);
  } else if (restored) {
    stageToast(noticeMessage(catalogNotice('stage_plans_restored', { count: restored })));
  }
  if (boardRevealRetryQueued) schedulePendingWorksiteRetry();
  // the changes are ready and listed first
  // each removal's live price fills in afterwards, so the button never drops its count while prices load.
  // each touched plot is refreshed once. The Changes surface re-renders when a price lands (prices fill in
  // one by one), and plot refreshes without a price share ONE render at the end, not one per plot. Stage and board
  // are re-read per plot: the maintainer may stage or undo, and a newer capture may arrive, while a price is awaited.
  let unrendered = false;
  for (const deedUid of touched) {
    if (!isCurrent()) return;
    const entries = CERES_STAGE.filter((entry) => entry.deed_uid === deedUid && !entry.txId && !entry._plan_invalid_reason);
    const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
    let priced = false;
    if (row && entries.length && entries.every((entry) => entry.action === 'unstake')) {
      try { applyRemovalImpactToStage(deedUid, await removalImpact(row, entries.map((entry) => String(entry.asset_ref)))); priced = true; } catch (error) { /* priced on the next change */ }
    }
    if (!isCurrent()) return;
    if (row && EXPANDED_DATA_LOADED.has(deedUid)) { applyStageToRow(row, { force: true }); updateMountedRow(deedUid); unrendered = true; }
    if (priced) { renderCheckoutSurface(); unrendered = false; }
  }
  if (unrendered) renderCheckoutSurface();
  persistStagePlans();
}

// the kept-plan document is the only maintainer of upgrade plans.
export function chainPlans() { return PLANS.chains; }
export function activeChainPlans() {
  if (PLANS.account !== String(CURRENT_PLAYER || BOARD?.player || '').toLowerCase()) return [];
  return Object.entries(PLANS.chains).filter(([, plan]) => !['cancelled', 'done'].includes(plan.state) || plan.inputs.some((input) => input.left_out_choice === 'put_back' && !input.return_staged_at));
}
// A16: wallet evidence belongs to the captured account and nonce, not the UI.
export async function completeChainAttempt(account, id, nonce, txId) {
  if (!txId) return;
  return withStagedAccountLock(account, locked => {
    if (!locked) throw new Error('Safe staged-plan locking is unavailable.');
    const key = storeKey(account);
    const stored = JSON.parse(localStorage.getItem(key) || 'null');
    const plan = stored?.chains?.[id];
    if (!plan?.combine || plan.combine.nonce !== nonce) return;
    plan.combine = { ...plan.combine, tx_id: txId };
    stored.revision = Number(stored.revision || 0) + 1;
    localStorage.setItem(key, JSON.stringify(stored));
    if (PLANS.account === account && CURRENT_PLAYER === account) {
      PLANS.chains[id] = plan;
      PLANS.revision = stored.revision;
      PLANS.lastSig = null;
    }
  });
}

export function saveChain(id, plan, { refresh = false, locked = false } = {}) {
  if (!PLANS.open || !PLANS.ready || PLANS.account !== String(CURRENT_PLAYER || BOARD?.player || '').toLowerCase()) throw new Error('Wait for the saved plans to finish loading.');
  if (!PLANS.chains[id] && activeChainPlans().length >= 50) throw new Error('Keep at most 50 active upgrade plans.');
  PLANS.chains[id] = { ...plan, ...deriveChain(plan), updated_at: refresh ? plan.updated_at : new Date().toISOString() };
  return locked ? persistStagePlansLocked() : persistStagePlans();
}
export function recordChainUnstake(entry, phase, txId, at = new Date().toISOString()) {
  const plan = PLANS.chains[entry.chain_id];
  if (plan?.state === 'combined' && entry.action !== 'unstake' && phase === 'confirmed' && entry.asset_ref === plan.result?.uid) {
    saveChain(entry.chain_id, { ...plan, state: 'done' });
    return;
  }
  const input = plan?.inputs.find((item) => item.uid === entry.chain_input_uid);
  if (!input) return;
  if (phase === 'refused') {
    input.problem = { code: 'unstake_refused', at, detail: entry.refusal_reason || null };
    input.refused_unstake_tx_id = txId;
    if (input.left_out_choice === 'put_back') input.return_staged_at = at;
    input.unstake = null;
    input.ready_at = null;
  } else {
    input.unstake = { ...input.unstake, tx_id: txId, signed_at: (input.unstake?.tx_id === txId ? input.unstake.signed_at : null) || entry.broadcast_at || at,
      confirmed_at: phase === 'confirmed' ? at : input.unstake?.tx_id === txId ? input.unstake.confirmed_at || null : null };
    input.problem = null;
  }
  input.updated_at = at;
  Object.assign(plan, deriveChain(plan), { updated_at: at });
  persistStagePlans();
}
export function leaveChainUnstake(entry) {
  const plan = PLANS.chains[entry.chain_id];
  const input = plan?.inputs.find((item) => item.uid === entry.chain_input_uid);
  if (!input || input.unstake?.tx_id) return;
  input.status = 'left_out';
  input.updated_at = new Date().toISOString();
  Object.assign(plan, deriveChain(plan), { updated_at: input.updated_at });
  window.dispatchEvent(new CustomEvent('ceres:chain-input-left-out', { detail: { id: entry.chain_id } }));
}
