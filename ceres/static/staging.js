import { chainPlans, pendingIncomingRefElsewhere } from './stage-plans.js';
import { chainPlacementBlocked } from './combine-chain.js';
import { BOARD, BOARD_GENERATION, CURRENT_PLAYER, noticeMessage, playerQueryAppend } from './board.js';
import { isValidEstimate, normalizeEstimate } from './candidates.js';
import { renderCheckoutSurface } from './checkout.js';
import { annotateEditorBloodlines, editorAssetImage, editorGroups } from './editor.js';
import { CERES_PREVIEWS } from './operations.js';
import { fetchPickerPage } from './picker-actions.js';
import { updateMountedRow } from './rows.js';
import { S } from './state.js';
import { releasePendingBroadcastRequests } from './broadcast-gate.js';

// Column order: Slot · SPL · Name · Rarity · Edition · Foil · Lvl · CC · Base PP ·
// Terrain% · DEC · Abilities · (sole) -- the owner, 2026-08-21 board-polish
// batch 15, item 1: splinter moved before Name (renamed header "SPL", both header text
// and icon centred, matching item 2's Rarity treatment -- both are icon-only columns
// in this sub-table just as Rarity already is in the main table). "CC/CC max" header
// shortened to "CC" (item 3) -- cell content (current/max, e.g. "115/400") unchanged.
// Worker-column-polish pass (2026-08-21, same day): Edition is now icon-only, text
// moved into the tooltip via setIconOnlyHtml() -- same treatment as the main table's
// Type/Totem/Title columns (typeMarkHtml() et al.), centred to match SPL/Rarity. SPL's
// icon renders larger here (splinterIconHtml(..., true) -> .splinter-icon-lg, ceres.css)
// worker-only, the idle-cards table's own splinter icons are untouched. Harvest PP
// column REMOVED (the owner: "remove harvestPP") -- the sub-table already shows Base PP
// and Terrain%, from which Harvest PP is derivable, and the plot row's own Harvest PP
// total (row.harvest_pp, unaffected -- see plotRowCellsHtml()) is the aggregate that
// actually matters for portfolio decisions.
export const CERES_STAGE = [];
// A staged worksite stays separate from card moves in memory. keeps it through the
// account plan store; includes first clears in op_preview's combined transaction.
export const CERES_STAGED_WORKSITES = new Map();

export function stageWorksiteBuild(deedUid, entry) {
  if (CERES_STAGED_WORKSITES.get(deedUid)?.txId) return false;
  CERES_STAGED_WORKSITES.set(deedUid, { ...entry, blockedReason: null, stagedAt: Date.now() });
  CERES_PREVIEWS.delete(deedUid);
  CERES_PREVIEWS.delete(CERES_PORTFOLIO_KEY);
  renderCheckoutSurface();
  return true;
}

function pendingPreviewGroups(review) {
  return (review?.payload?.chunks || []).map((chunk) => chunk.operations).filter((operations) => Array.isArray(operations) && operations.length);
}

export async function releasePendingStageBroadcasts(reviews = [...CERES_PREVIEWS.values()], builds = [...CERES_STAGED_WORKSITES.values()]) {
  for (const review of reviews) {
    const groups = pendingPreviewGroups(review);
    if (groups.length && !await releasePendingBroadcastRequests(review.payload.account || CURRENT_PLAYER, groups)) return false;
  }
  for (const build of builds) {
    if (Array.isArray(build?.pendingOperations)
        && !await releasePendingBroadcastRequests(build.pendingAccount || CURRENT_PLAYER, [build.pendingOperations])) return false;
  }
  return true;
}

export async function unstageWorksiteBuild(deedUid) {
  if (CERES_STAGED_WORKSITES.get(deedUid)?.txId) return false;
  const build = CERES_STAGED_WORKSITES.get(deedUid);
  const reviews = [CERES_PREVIEWS.get(deedUid), CERES_PREVIEWS.get(CERES_PORTFOLIO_KEY)].filter(Boolean);
  if (!await releasePendingStageBroadcasts(reviews, build ? [build] : [])) return false;
  CERES_STAGED_WORKSITES.delete(deedUid);
  CERES_PREVIEWS.delete(deedUid);
  CERES_PREVIEWS.delete(CERES_PORTFOLIO_KEY);
  renderCheckoutSurface();
  return true;
}

// published for art.js / harvest-state.js, which must not import this module (it closed an import cycle).
globalThis.ceresStagedWorksiteBuild = (deedUid) => CERES_STAGED_WORKSITES.get(deedUid) || null;
export function stagedWorksiteBuild(deedUid) {
  return CERES_STAGED_WORKSITES.get(deedUid) || null;
}

export function hasStagedWorksiteBuilds() {
  return CERES_STAGED_WORKSITES.size > 0;
}

// Recorded when the signing order (staging.js §) reaches this build and finds it
// still not sendable -- the reason is shown in the checkout row; nothing is undone.
export function setWorksiteBuildBlocked(deedUid, reason) {
  const entry = CERES_STAGED_WORKSITES.get(deedUid);
  if (entry) entry.blockedReason = reason || null;
}

export const CERES_STAGE_IMPACTS = new Map();
const CERES_STAGE_IMPACT_QUEUED = new Set();
const CERES_STAGE_IMPACT_REVISIONS = new Map();
let CERES_TOAST_TIMER = null;
export const CERES_PORTFOLIO_KEY = '__portfolio__';
export const CERES_REMOVAL_PENDING = new Set();
export const CERES_CAP_PREDICTIONS = new Map();
export const CERES_CAP_SAMPLES = [];
export const CERES_CAP_MISMATCHES = [];
const CERES_CAP_DERIVATION_NOTICES = new Map();
const CAP_RECONCILE_TOLERANCE_PP = 0.01;
let capAccount = null;
const CAP_STORE_PREFIX = 'ceres:cap-predictions:';
function loadCapPredictions() {
  if (capAccount === CURRENT_PLAYER) return;
  capAccount = CURRENT_PLAYER;
  CERES_CAP_PREDICTIONS.clear();
  CERES_CAP_DERIVATION_NOTICES.clear();
  CERES_CAP_MISMATCHES.length = 0;
  CERES_CAP_SAMPLES.length = 0;
  // ("all storage of current board data is strictly forbidden"): predicted cap numbers are derived
  // board numbers, so they live in this page session's memory only. Q5: the copy earlier versions
  // kept in localStorage is removed silently.
  try {
    for (let i = localStorage.length - 1; i >= 0; i -= 1) {
      const name = localStorage.key(i);
      if (name && name.startsWith(CAP_STORE_PREFIX)) localStorage.removeItem(name);
    }
  } catch (_) { /* storage unavailable: nothing to remove */ }
}

export function capNoticesForRow(deedUid) {
  loadCapPredictions();
  return CERES_CAP_MISMATCHES.filter((notice) => notice.context?.deed_uid === deedUid)
    .concat(CERES_CAP_DERIVATION_NOTICES.get(deedUid) || [])
    .concat(CERES_CAP_PREDICTIONS.get(deedUid)?.notices || []);
}

export function recordCapDerivationFailure(deedUid, plot, reason) {
  loadCapPredictions();
  CERES_CAP_PREDICTIONS.delete(deedUid);
  CERES_CAP_DERIVATION_NOTICES.set(deedUid, [{ code: 'cap_derivation_failed', context: { deed_uid: deedUid, plot, reason } }]);
}

export function recordCapPrediction(deedUid, prediction) {
  loadCapPredictions();
  CERES_CAP_DERIVATION_NOTICES.delete(deedUid);
  CERES_CAP_PREDICTIONS.set(deedUid, { ...prediction, recordedAt: Date.now() });
}

export function clearCapDerivationState(deedUid) {
  loadCapPredictions();
  CERES_CAP_DERIVATION_NOTICES.delete(deedUid);
  CERES_CAP_PREDICTIONS.delete(deedUid);
}

export function reconcileCapPrediction(deedUid, updated) {
  loadCapPredictions();
  const prediction = CERES_CAP_PREDICTIONS.get(deedUid);
  if (!prediction || !Array.isArray(prediction.slots)) return;
  const workers = Array.isArray(updated?.workers) ? updated.workers : [];
  let compared = 0;
  let mismatch = false;
  prediction.slots.forEach((expected) => {
    const served = workers.find((worker) => String(worker.asset_ref) === String(expected.asset_ref)
      && Number(worker.slot) === Number(expected.slot));
    const servedAfter = Number(served?.base_pp_after_cap);
    if (!served || !Number.isFinite(servedAfter)) return;
    compared += 1;
    const distance = Math.abs(servedAfter - expected.predicted_after_cap);
    if (distance <= CAP_RECONCILE_TOLERANCE_PP) return;
    mismatch = true;
    const notice = {
      code: 'cap_prediction_mismatch',
      context: {
        deed_uid: deedUid, plot: updated.plot || prediction.plot, slot: expected.slot,
        card: served.name || expected.asset_ref, predicted: expected.predicted_after_cap, served: servedAfter, allocated: servedAfter,
      },
    };
    CERES_CAP_MISMATCHES.push(notice);
    console.error('[ceres:cap-mismatch]', JSON.stringify(notice.context));
  });
  if (compared === prediction.slots.length && !mismatch) {
    CERES_CAP_SAMPLES.push({ ...prediction, confirmed_at: new Date().toISOString() });
  }
  if (compared !== prediction.slots.length) {
    return;
  }
  CERES_CAP_PREDICTIONS.delete(deedUid);
}

export function stagedRemovalRefs(deedUid, exclude = null) {
  return CERES_STAGE.filter((entry) => entry !== exclude && entry.deed_uid === deedUid && entry.action === 'unstake')
    .map((entry) => entry.asset_ref).filter(Boolean).map(String);
}

export function stagedReleasedItemRefs(groupId, targetDeedUid) {
  const reserved = stagedIncomingAssetRefs();
  return CERES_STAGE.filter((entry) => entry.group_id === groupId
    && ['totem', 'title', 'power_core'].includes(entry.group_id)
    && ['unstake', 'replace'].includes(entry.action) && entry.deed_uid !== targetDeedUid
    && !entry.txId && !entry._plan_invalid_reason && !entry.refused_at
    && !reserved.has(String(entry.action === 'replace' ? entry.evict_asset_ref : entry.asset_ref)))
    .map((entry) => String(entry.action === 'replace' ? entry.evict_asset_ref : entry.asset_ref)).filter((ref) => ref && ref !== 'null');
}

export function stagedIncomingAssetRefs(exclude = null) {
  return new Set(CERES_STAGE
    .filter((entry) => entry !== exclude && (entry.action === 'stake' || entry.action === 'replace'))
    .map((entry) => entry.asset_ref).filter(Boolean).map(String));
}

export function sameRefSet(left, right) {
  const a = new Set(left.map(String));
  const b = new Set(right.map(String));
  return a.size === b.size && [...a].every((ref) => b.has(ref));
}

export function deedHasBothDirectionsStaged(deedUid) {
  let hasRemoval = false;
  let hasIncoming = false;
  CERES_STAGE.forEach((entry) => {
    if (entry.deed_uid !== deedUid) return;
    if (entry.action === 'unstake') hasRemoval = true;
    if (entry.action === 'stake' || entry.action === 'replace') hasIncoming = true;
  });
  return hasRemoval && hasIncoming;
}

export function cloneStageAsset(asset) {
  if (!asset) return null;
  return {
    ...asset,
    land_abilities: Array.isArray(asset.land_abilities) ? asset.land_abilities.slice() : [],
    sole_holder_for: Array.isArray(asset.sole_holder_for) ? asset.sole_holder_for.slice() : [],
  };
}

// an item asset kept from before v0.9.330 carries `image: null`; the icon comes from its name/rarity.
function withItemImage(asset, groupId) {
  if (!asset || asset.image || !['totem', 'title', 'power_core'].includes(groupId)) return asset;
  const image = editorAssetImage(asset, groupId);
  return image ? { ...asset, image, image_candidates: [image] } : asset;
}

export function stageForSlot(deedUid, groupId, slotIndex, action, assetRef, delta = 0, evictAssetRef = null, assetName = null, sourceDeedUid = null, baseEstimate = null, cooldown = null, asset = null) {
  if (action !== 'unstake' && assetRef && (CERES_STAGE.some(entry => entry.action !== 'unstake' && String(entry.asset_ref) === String(assetRef)
    && (entry.deed_uid !== deedUid || entry.group_id !== groupId || String(entry.slot_index) !== String(slotIndex)))
    || pendingIncomingRefElsewhere(assetRef, deedUid, groupId, slotIndex))) {
    stageToast('This card or item is already staged on another plot or slot. Move it from the earlier change first.', true);
    return false;
  }
  // reject a stale click as well as disabling the picker control.
  if (asset?.planned && (Object.values(chainPlans()).some(plan => !['cancelled', 'done'].includes(plan.state) && chainPlacementBlocked(plan, deedUid, slotIndex))
    || CERES_STAGE.some(entry => entry.asset_ref === assetRef && entry.action !== 'unstake' && (entry.deed_uid !== deedUid || entry.slot_index !== slotIndex)))) return false;
  const index = CERES_STAGE.findIndex((entry) => entry.deed_uid === deedUid && entry.group_id === groupId && entry.slot_index === slotIndex);
  const existing = index === -1 ? null : CERES_STAGE[index];
  // a card put into a slot whose own card is already staged for removal (e.g. via
  // "Find a matching … holder" after unstaging a Toil and Kin holder) REPLACES that card.
  // As a plain `stake` it overwrote the removal, so the server priced a card going into a
  // slot still held by the old one -- the stage impact failed and no change badges showed.
  if (existing?.action === 'unstake' && action === 'stake' && existing.asset_ref && existing.asset_ref !== assetRef) {
    action = 'replace';
    evictAssetRef = existing.asset_ref;
  }
  // replacing a card that is itself only STAGED here (unsigned) replaces the plan, not a live card
  // the new entry keeps the slot's real action and evict (the live card, or none). Staged as a `replace` evicting
  // the staged card, the server refused the stage impact ("staged asset is not present on deed" -- the change
  // badges vanished) and the reload check dropped the plan ("the card it replaced has left the slot").
  if (existing && !existing.txId && existing.action !== 'unstake' && action === 'replace' && evictAssetRef && evictAssetRef === existing.asset_ref) {
    action = existing.action;
    evictAssetRef = existing.evict_asset_ref || null;
  }
  const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
  const group = row ? editorGroups(row).find((item) => item.id === groupId) : null;
  const currentAsset = group?.slots[slotIndex] || null;
  const numericDelta = delta === null || delta === undefined ? null : Number(delta);
  const deltaPriced = Number.isFinite(numericDelta);
  const entry = {
    deed_uid: deedUid, group_id: groupId, slot_index: slotIndex, action,
    asset_ref: assetRef || null, evict_asset_ref: evictAssetRef || null,
    delta: asset?.planned ? null : deltaPriced ? numericDelta : null,
    delta_status: !asset?.planned && deltaPriced ? 'calculated' : 'not_calculated',
    delta_reason: deltaPriced ? null : asset?.plot_impact?.reason || 'plot_impact_unknown',
    // Raw Base PP estimate for this move -- see the internal rules's two-stage
    // chain. `delta` above stays the Total-PP (harvest-stage) figure; never collapsed.
    // a pure unstake with no priced estimate yet is NEVER given a
    // fabricated `{status:'point', value:0}` placeholder -- `normalizeEstimate(null)`
    // does exactly that, and every Base PP surface (`combineEstimates` over
    // `staged.map(e => e.base)` in editor.js) treated it as a real, confidently-zero
    // removal, so a staged unstake always read `+0` and the net always looked ≥ 0
    // `stagePricedRemovals()` (below) is the one caller that stages a bare unstake
    // and it always prices it via `removalImpact()` + `applyRemovalImpactToStage()`
    // BEFORE this function's own `renderCheckoutSurface()` call ever paints -- so in
    // the normal flow this placeholder is created and overwritten in the same tick.
    // This `not_applicable` (never `point`/0) is the fallback for any OTHER caller
    // `combineEstimates` excludes `not_applicable` entries from the sum entirely
    // (candidates.js:174-176), so an unpriced removal simply contributes nothing
    // the pre-stage figure keeps showing, exactly like R-78 requires -- instead of
    // asserting a wrong zero. It is not a displayed "not calculated" string (R-78
    // forbids that); it is a numeric no-op until `applyRemovalImpactToStage`
    // overwrites it with the real negative `own_base_pp`.
    base: asset?.planned ? { status: 'not_applicable', value: null, reason: 'after the combine' } : action === 'unstake' && !isValidEstimate(baseEstimate)
      ? { status: 'not_applicable', value: null, low: null, high: null, reason: 'removal_pricing_pending' }
      : normalizeEstimate(baseEstimate),
    _asset_name: assetName || existing?._asset_name || currentAsset?.name || null,
    _asset: cloneStageAsset(withItemImage(asset, groupId)),
    _previous_asset: existing ? existing._previous_asset : cloneStageAsset(currentAsset),
    _source_deed_uid: sourceDeedUid || null,
    // Server-computed readiness (candidates.py::cooldown_state) for the INCOMING asset
    // null for a pure unstake, since removing an already-staked card is never cooldown
    // gated itself. Read-only display data; the checkout's Prepare-transaction gate below
    // reads `.ready` straight off this, never re-deriving it (see editorOccupant's comment).
    chain_id: asset?.planned ? asset.chain_id : null,
    cooldown: asset?.planned ? { ready: false, state: 'planned', ready_at: null, reason: 'after the combine' } : cooldown || null,
    txId: existing?.txId || null,
    broadcast_at: existing?.broadcast_at || null,
  };
  if (index === -1) CERES_STAGE.push(entry);
  else CERES_STAGE[index] = entry;
  CERES_PREVIEWS.delete(deedUid);
  CERES_PREVIEWS.delete(CERES_PORTFOLIO_KEY);
  if (action !== 'unstake' && !asset?.planned) refreshStageImpactForRow(deedUid);
  renderCheckoutSurface();
}

function clearStageForGroup(deedUid, groupId) {
  for (let i = CERES_STAGE.length - 1; i >= 0; i--) {
    if (CERES_STAGE[i].deed_uid === deedUid && CERES_STAGE[i].group_id === groupId) CERES_STAGE.splice(i, 1);
  }
  CERES_PREVIEWS.delete(deedUid);
  CERES_PREVIEWS.delete(CERES_PORTFOLIO_KEY);
  refreshStageImpactForRow(deedUid);
  renderCheckoutSurface();
  if (S.CERES_PICKER) fetchPickerPage(true);
}

export function stagedEntriesForRow(deedUid) {
  return CERES_STAGE.filter((entry) => entry.deed_uid === deedUid);
}

export function stageImpactForRow(deedUid) {
  return CERES_STAGE_IMPACTS.get(deedUid) || null;
}

export function stageDeltaForRow(deedUid) {
  // Every displayed PP figure is checked against the server batch result, never against another figure on this screen.
  const entries = stagedEntriesForRow(deedUid).filter((entry) => !entry._plan_invalid_reason && !entry._asset?.planned);
  if (!entries.length) return 0;
  const impact = stageImpactForRow(deedUid);
  return impact?.status === 'point' && Number.isFinite(impact.harvest_pp_delta) ? impact.harvest_pp_delta : null;
}

function validateStageImpact(payload, deedUid, entries) {
  if (!payload || payload.deed_uid !== deedUid || !Number.isFinite(payload.current_harvest)
      || !Number.isFinite(payload.resulting_harvest) || !Number.isFinite(payload.harvest_pp_delta)
      || !Array.isArray(payload.per_move)) throw new Error('Stage impact response was malformed.');
  if (payload.per_move.length !== entries.length || payload.per_move.some((move) => !Number.isFinite(move.attributed_delta))) {
    throw new Error('Stage impact response did not cover every staged move.');
  }
  const refs = entries.map((entry) => String(entry.asset_ref));
  const returnedRefs = payload.per_move.map((move) => String(move.asset_ref));
  if (refs.some((ref) => !returnedRefs.includes(ref))) throw new Error('Stage impact response did not match the staged assets.');
  const attributed = payload.per_move.reduce((sum, move) => sum + move.attributed_delta, 0);
  if (Math.abs(attributed - payload.harvest_pp_delta) > 1e-6) throw new Error('Stage impact attribution did not reconcile.');
  return { ...payload, status: 'point', reason: null };
}

export function refreshStageImpactForRow(deedUid) {
  CERES_STAGE_IMPACT_REVISIONS.set(deedUid, (CERES_STAGE_IMPACT_REVISIONS.get(deedUid) || 0) + 1);
  CERES_STAGE_IMPACTS.delete(deedUid);
  if (CERES_STAGE_IMPACT_QUEUED.has(deedUid)) return;
  CERES_STAGE_IMPACT_QUEUED.add(deedUid);
  Promise.resolve().then(async () => {
    CERES_STAGE_IMPACT_QUEUED.delete(deedUid);
    const revision = CERES_STAGE_IMPACT_REVISIONS.get(deedUid);
    const entries = stagedEntriesForRow(deedUid).filter((entry) => !entry._plan_invalid_reason && !entry._asset?.planned);
    if (!entries.length) {
      renderCheckoutSurface();
      return;
    }
    const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
    if (!row) return;
    try {
      const response = await fetch(playerQueryAppend(`/api/plots/${encodeURIComponent(deedUid)}/stage-impact`), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ moves: entries.map(stageMovePayload) }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `Stage impact request failed: HTTP ${response.status}`);
      if (revision !== CERES_STAGE_IMPACT_REVISIONS.get(deedUid)) {
        refreshStageImpactForRow(deedUid);
        return;
      }
      CERES_STAGE_IMPACTS.set(deedUid, validateStageImpact(payload, deedUid, entries));
    } catch (error) {
      if (revision === CERES_STAGE_IMPACT_REVISIONS.get(deedUid)) {
        CERES_STAGE_IMPACTS.set(deedUid, { status: 'unknown', reason: 'stage_impact_unavailable', detail: String(error) });
      }
    }
    renderCheckoutSurface();
    updateMountedRow(deedUid);
  });
}

export function stageMovePayload(entry) {
  // `base` and `cooldown` are display-only (see stageForSlot) -- never part of the move
  // contract the server executes against; the server re-derives cooldown readiness from
  // its own live snapshot at preview time regardless of what the client saw at stage time.
  // _worksite_parked_reason is page state written after each preview; sending it made the Changes window's batch
  // look changed and request a second, identical preview (each one now a live read, ).
  const { _asset_name, _asset, _previous_asset, _source_deed_uid, _notices, _restake, _synthetic, _unstake_tx, _plan_invalid_reason, _plan_invalid_detail, _worksite_parked_reason, base, cooldown, combine_verified_at, txId, broadcast_at, delta_status, delta_reason, chain_id, chain_input_uid, ...move } = entry;
  return move;
}

export function restoreStagedMove(entry) {
  if (entry?._plan_invalid_reason) return;
  const row = BOARD?.plots.find((plot) => plot.deed_uid === entry.deed_uid);
  const group = row ? editorGroups(row).find((item) => item.id === entry.group_id) : null;
  if (!row || !group) return;
  const previous = cloneStageAsset(entry._previous_asset);
  // Runi rides `row.workers`/`row.capacity.runi_staked`, not `row.bonus.runi` (same
  // reasoning as mutateEditorSlot() above) -- routing it through the generic
  // `row.bonus[group.id] = previous` branch below would leave `row.workers` still
  // missing slot 0 and `row.capacity.runi_staked` still `false`, so the restored tile
  // would render as a plain empty "Add Runi" slot instead of bringing the card back.
  if (group.id === 'worker' || group.id === 'runi') {
    mutateEditorSlot(row, group, entry.slot_index, previous);
  } else {
    row.bonus = row.bonus || {};
    row.bonus[group.id] = previous;
  }
}

export function stageToast(message, error = false) {
  let toast = document.getElementById('ceres-stage-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'ceres-stage-toast';
    toast.className = 'stage-toast';
    toast.setAttribute('role', 'status');
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.toggle('is-error', error);
  toast.hidden = false;
  clearTimeout(CERES_TOAST_TIMER);
  CERES_TOAST_TIMER = setTimeout(() => { toast.hidden = true; }, 4500);
}

export function stagedPowerCoreRefs() {
  return new Set(CERES_STAGE
    .filter((entry) => entry.group_id === 'power_core' && (entry.action === 'stake' || entry.action === 'replace'))
    .map((entry) => String(entry.asset_ref)));
}

export function mutateEditorSlot(row, group, index, occupant) {
  if (group.id === 'worker') {
    const current = Array.isArray(row.workers) ? row.workers : [];
    const slot = index + 1;
    const without = current.filter((worker) => Number(worker.slot) !== slot);
    if (occupant) without.push({ ...occupant, harvest_pp: occupant.current_harvest_pp ?? occupant.harvest_pp, land_abilities: occupant.land_abilities || [], sole_holder_for: occupant.sole_holder_for || [], slot });
    row.workers = without.sort((a, b) => Number(a.slot || 99) - Number(b.slot || 99));
    annotateEditorBloodlines(row);
    const counts = new Map();
    row.workers.forEach((worker) => (worker.land_abilities || []).forEach((entry) => {
      const code = Array.isArray(entry) ? entry[0] : entry;
      counts.set(code, (counts.get(code) || 0) + 1);
    }));
    row.workers.forEach((worker) => {
      worker.sole_holder_for = (worker.land_abilities || []).map((entry) => Array.isArray(entry) ? entry[0] : entry)
        .filter((code) => counts.get(code) === 1);
    });
  } else if (group.id === 'runi') {
    // The Runi rides in `row.workers` on reserved slot 0, not `row.bonus` -- power-source
    // detection everywhere else (`solePowerSource`, `plotPoweredAfterStage`, the RUNI
    // badge) reads the boolean `row.capacity.runi_staked`, so that is what must flip here
    // never `row.bonus.runi` (which the item groups use for a full asset object).
    const current = Array.isArray(row.workers) ? row.workers : [];
    const without = current.filter((worker) => Number(worker.slot) !== 0);
    if (occupant) without.push({ ...occupant, slot: 0 });
    row.workers = without.sort((a, b) => Number(a.slot || 99) - Number(b.slot || 99));
    row.capacity = row.capacity || {};
    row.capacity.runi_staked = !!occupant;
  } else {
    // the table row's Totem / Title cells show the LIVE state; only the tiles show the staged one.
    if (!row._live_bonus) row._live_bonus = { ...(row.bonus || {}) };
    row.bonus = row.bonus || {};
    row.bonus[group.id] = occupant;
  }
}

// phase 2 / : a row freshly read from the server (a plot detail load, a quiet re-read) carries none of
// the staged moves, so its tiles would show the server's cards. Every unsigned staged move is replayed onto it once
// the live occupant becomes the undo target. Idempotent: a slot already showing the staged state is left alone.
const STAGE_REPLAYED = new WeakSet();
export function applyStageToRow(row, { force = false } = {}) {
  if (!row || (!force && STAGE_REPLAYED.has(row))) return;
  STAGE_REPLAYED.add(row);
  const entries = CERES_STAGE.filter((entry) => entry.deed_uid === row.deed_uid && !entry.txId && !entry._synthetic && !entry._plan_invalid_reason);
  if (!entries.length) return;
  const groups = editorGroups(row);
  entries.forEach((entry) => {
    const group = groups.find((item) => item.id === entry.group_id);
    if (!group) return;
    const live = group.slots?.[entry.slot_index] || null;
    if (entry.action === 'unstake') {
      if (!live || String(live.asset_ref) !== String(entry.asset_ref)) return; // already empty, or another card now
      entry._previous_asset = cloneStageAsset(live);
      mutateEditorSlot(row, group, entry.slot_index, null);
      return;
    }
    if (live && String(live.asset_ref) === String(entry.asset_ref)) return; // already shows the staged card
    if (!entry._asset) return;
    entry._previous_asset = cloneStageAsset(live);
    mutateEditorSlot(row, group, entry.slot_index, cloneStageAsset(entry._asset));
  });
}

function validateRemovalImpact(payload, assetRefs, deedUid) {
  if (!payload || !Array.isArray(payload.removed)) throw new Error('Removal impact response was malformed. Nothing was staged.');
  if (payload.deed_uid !== deedUid || typeof payload.before_powered !== 'boolean' || typeof payload.after_powered !== 'boolean') {
    throw new Error('Removal impact response did not match the requested plot context. Nothing was staged.');
  }
  const expected = new Set(assetRefs.map(String));
  const actual = new Set(payload.removed.map((removed) => String(removed?.asset_ref)));
  if (payload.removed.length !== expected.size || actual.size !== expected.size || ![...expected].every((ref) => actual.has(ref))) {
    throw new Error('Removal impact response did not cover the exact requested assets. Nothing was staged.');
  }
  if (payload.removed.some((removed) => typeof removed.own_harvest_pp !== 'number' || !Number.isFinite(removed.own_harvest_pp))) {
    throw new Error('Removal impact response contained an invalid asset PP value. Nothing was staged.');
  }
  // `own_construction_pp` is the stage-one (Base PP) counterpart of `own_harvest_pp` above
  // null for items (Totem/Title/Power Core carry no construction PP of their own), a finite
  // number for cards. Never both missing and non-null.
  if (payload.removed.some((removed) => removed.own_construction_pp !== null && (typeof removed.own_construction_pp !== 'number' || !Number.isFinite(removed.own_construction_pp)))) {
    throw new Error('Removal impact response contained an invalid Base PP value. Nothing was staged.');
  }
  if (payload.removed.some((removed) => removed.own_base_pp !== null && (typeof removed.own_base_pp !== 'number' || !Number.isFinite(removed.own_base_pp)))) {
    throw new Error('Removal impact response contained an invalid Base PP value. Nothing was staged.');
  }
  for (const term of ['plot_residual_delta', 'plot_ability_delta', 'harvest_pp_delta']) {
    if (typeof payload[term] !== 'number' || !Number.isFinite(payload[term])) {
      throw new Error('Removal impact response contained an invalid plot delta. Nothing was staged.');
    }
  }
  if (!isValidEstimate(payload.own_construction_pp_delta)) {
    throw new Error('Removal impact response contained an invalid Base PP delta. Nothing was staged.');
  }
  if (!isValidEstimate(payload.own_base_pp_delta)) {
    throw new Error('Removal impact response contained an invalid Base PP value. Nothing was staged.');
  }
  return payload;
}

export async function removalImpact(row, assetRefs) {
  if (!row) throw new Error('The plot is no longer available. Nothing was staged.');
  const response = await fetch(playerQueryAppend(`/api/plots/${encodeURIComponent(row.deed_uid)}/removal-impact`), {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ asset_refs: assetRefs }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Removal impact request failed: HTTP ${response.status}`);
  return validateRemovalImpact(payload, assetRefs, row.deed_uid);
}

export function removalBoardContext(row) {
  return { generation: BOARD_GENERATION, player: CURRENT_PLAYER, row };
}

export function currentRemovalRow(deedUid, context) {
  const currentRow = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
  return BOARD_GENERATION === context.generation && CURRENT_PLAYER === context.player && currentRow === context.row
    ? currentRow : null;
}

export function applyRemovalImpactToStage(deedUid, impact) {
  const removedByRef = new Map((impact.removed || []).map((removed) => [String(removed.asset_ref), removed]));
  const pricedEntries = CERES_STAGE.filter((entry) => entry.deed_uid === deedUid && entry.action === 'unstake' && removedByRef.has(String(entry.asset_ref)));
  pricedEntries.forEach((entry) => {
    entry.delta = -Number(removedByRef.get(String(entry.asset_ref))?.own_harvest_pp || 0);
    entry.delta_status = 'calculated';
    entry.delta_reason = null;
  });
  if (pricedEntries.length) pricedEntries[0].delta += Number(impact.plot_residual_delta || 0);
  const attributed = pricedEntries.map((entry) => ({
    slot_index: entry.slot_index, group_id: entry.group_id, asset_ref: entry.asset_ref, attributed_delta: entry.delta,
  }));
  // carry the plot's current and resulting harvest, so the production badge (/h)
  // follows the removal's PP change. Left null, the badge read it as 0 and claimed the plot's
  // whole output lost (e.g. -11,687 /h on 065-09-083 whatever the removal cost).
  const plotRow = (typeof BOARD !== 'undefined' && BOARD?.plots) ? BOARD.plots.find((plot) => plot.deed_uid === deedUid) : null;
  const currentHarvest = Number(plotRow?.harvest_pp);
  const harvestDelta = Number(impact.harvest_pp_delta);
  const knownNow = Number.isFinite(currentHarvest) && currentHarvest > 0 && Number.isFinite(harvestDelta);
  CERES_STAGE_IMPACTS.set(deedUid, {
    status: 'point', current_harvest: knownNow ? currentHarvest : null, resulting_harvest: knownNow ? currentHarvest + harvestDelta : null,
    harvest_pp_delta: harvestDelta, per_move: attributed, notices: impact.notices || [],
  });
  // Raw Base PP cost of each removed asset. Unlike the harvest-stage
  // residual above, raw Base PP has no plot-wide broadcast (the internal rules's two-stage
  // chain), so each removed card's own raw Base PP is charged to ITS OWN entry
  // no shared residual to concentrate on the first entry. Items (`own_base_pp`
  // null server-side) carry no Base PP of their own.
  pricedEntries.forEach((entry) => {
    const ownBase = removedByRef.get(String(entry.asset_ref))?.own_base_pp;
    entry.base = ownBase === null || ownBase === undefined
      ? { status: 'not_applicable', value: null, low: null, high: null, reason: 'item_pp_not_applicable' }
      : { status: 'point', value: -Number(ownBase), low: -Number(ownBase), high: -Number(ownBase), reason: null };
  });
  pricedEntries.forEach((entry) => { entry._notices = []; });
  if (pricedEntries.length) pricedEntries[0]._notices = Array.isArray(impact.notices) ? impact.notices : [];
}

// Keep responsive removal notices on the staged entries so undo restores the
// pre-action state and its notices disappear together.
function applyRemovalNoticesToStage(deedUid, notices) {
  const entries = CERES_STAGE.filter((entry) => entry.deed_uid === deedUid && entry.action === 'unstake');
  entries.forEach((entry) => { entry._notices = []; });
  if (entries.length) entries[0]._notices = Array.isArray(notices) ? notices : [];
  return Array.isArray(notices) ? notices : [];
}

export async function stagePricedRemovals(row, specs, successMessage) {
  const deedUid = row.deed_uid;
  if (CERES_REMOVAL_PENDING.has(deedUid)) {
    stageToast('A removal price update is already in progress for this plot. Try again when it finishes.', true);
    return false;
  }
  CERES_REMOVAL_PENDING.add(deedUid);
  try {
    const boardContext = removalBoardContext(row);
    const existingRefs = stagedRemovalRefs(deedUid);
    const assetRefs = [...new Set(existingRefs.concat(specs.map((spec) => spec.occupant.asset_ref).filter(Boolean).map(String)))];
    let impact;
    try {
      impact = await removalImpact(row, assetRefs);
    } catch (error) {
      // requirement 3 -- pricing genuinely failed. Never fall through
      // to a fabricated `+0` (that is the exact defect this ticket fixes); nothing is
      // staged, the pre-stage figures stay on screen untouched, and the R-78 defect
      // path (`cap_derivation_failed`, already used for a failed cap-allocation derivation
      // in editor.js:874) fires here too so the failure is visible in the plot's own
      // notices, not just a toast that can be missed.
      recordCapDerivationFailure(deedUid, row.plot, `removal pricing request failed: ${error}`);
      updateMountedRow(deedUid);
      stageToast(`Could not price removal safely: ${error}`, true);
      return false;
    }
    const currentRow = currentRemovalRow(deedUid, boardContext);
    if (!currentRow) {
      stageToast('The board or player changed while pricing removals. Nothing was staged; try again.', true);
      return false;
    }
    const slotsUnchanged = specs.every((spec) => {
      const currentGroup = editorGroups(currentRow).find((group) => group.id === spec.group.id);
      return String(currentGroup?.slots[spec.index]?.asset_ref) === String(spec.occupant.asset_ref);
    });
    if (!slotsUnchanged || !sameRefSet(stagedRemovalRefs(deedUid), existingRefs)) {
      stageToast('The plot changed while pricing removals. Nothing was staged; try again.', true);
      return false;
    }
    specs.forEach((spec) => {
      stageForSlot(deedUid, spec.group.id, spec.index, 'unstake', spec.occupant.asset_ref, 0, null, spec.occupant.name, null, null, null, spec.occupant);
    });
    applyRemovalImpactToStage(deedUid, impact);
    // The server computed these against the pre-removal roster.
    const notices = applyRemovalNoticesToStage(deedUid, impact.notices);
    specs.forEach((spec) => mutateEditorSlot(currentRow, spec.group, spec.index, null));
    renderCheckoutSurface();
    if (notices.length) {
      stageToast(notices.map(noticeMessage).join(' '));
    } else {
      stageToast(successMessage);
    }
    updateMountedRow(currentRow.deed_uid);
    return true;
  } finally {
    CERES_REMOVAL_PENDING.delete(deedUid);
  }
}
