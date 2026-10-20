import { readyPlanCombines, preparePlanCombineRun, signPlanCombine } from './combine-chain-sign.js';
import { chainReturn, chainPlacementReady, chainStageBackSource } from './combine-chain.js';
import { chainPlans } from './stage-plans.js';
import { plannedReturnChip } from './editor.js';
import { upgradePlansHtml } from './combine-chain-ui.js';
import { armReadyTimer, collectReadyItems } from './ready-feed.js';
import { activeChainPlans, leaveChainUnstake } from './stage-plans.js';
import { bindCountdowns, countdownText } from './countdown.js';
import { COMMIT_ICON } from './button-icons.js';
import { walletSignLabelHtml, walletReady, walletCanSignTransaction, walletControlsHtml, walletKind } from './wallet.js';
import { BOARD, NOTICE_CATALOG, catalogNotice, noticeMessage, playerQueryAppend, renderNotices } from './board.js';
import { combineEstimates, cooldownReasonFragment, estimateBadge, signedPp } from './candidates.js';
import { editorGroups, editorOccupant, renderPreviewSatellite, renderPreviewTile } from './editor.js';
import { esc, formatPlotLabel, humanizeInstant, num } from './format.js';
import { territoryCellHtml } from './icons.js';
import { CERES_BROADCAST_ACTIVE, CERES_PREVIEWS, FINISH_TRANSACTION_FIRST, confirmOperationBroadcast, operationSignButtonHtml, renderOperationReview, requestOperationPreview, serverNoticeHtml, signingFocusHtml } from './operations.js';
import { EXPANDED_DATA_LOADED, EXPAND_ERRORS, loadPlotDetail, refreshChangeBadges, updateMountedRow } from './rows.js';
import { applyStageToRow, stageForSlot, stageMovePayload } from './staging.js';
import { CERES_PORTFOLIO_KEY, CERES_REMOVAL_PENDING, CERES_STAGE, CERES_STAGED_WORKSITES, applyRemovalImpactToStage, currentRemovalRow, deedHasBothDirectionsStaged, hasStagedWorksiteBuilds, refreshStageImpactForRow, releasePendingStageBroadcasts, removalBoardContext, removalImpact, restoreStagedMove, sameRefSet, setWorksiteBuildBlocked, stageImpactForRow, stageToast, stagedRemovalRefs, unstageWorksiteBuild } from './staging.js';
import { S } from './state.js';
import { holdTooltipsUntilPointerMoves, tipAttrs } from './tooltip.js';
import { clearDroppedPlans, discardPendingStagePlans, dropReasonText, mintTransactionRunId, pendingStagePlanCount, pendingWorksitePlanCount, persistStagePlans, stagePlansReady } from './stage-plans.js';
import { boardReadHtml, plannerCounts, plannerDroppedHtml } from './planner.js';
import { buildChangesModel } from './changes-model.js';
import { fetchPickerPage, handleSlotEditorClick } from './picker-actions.js';
import { signStagedWorksiteBuild, prepareStagedWorksiteBuild, prepareWorksiteCompletion } from './worksite.js';
import { FEED_STATE, checkFeedPreviews, constructionModel, constructionRowHtml, forgetFeedErrors, markFeedAside, markFeedSent } from './feed-changes.js';
import { prepareSigningBatch } from './broadcast-gate.js';
import { STAGED_PLOT_LOCKS, clearPlotLockPlans, plotLockSvg, signPlotLockReview, signedPlotLockText } from './plot-lock.js';

// the plot as read fresh, right before deciding whether a staged worksite build
// can be sent -- NEVER the board's own (possibly stale-by-now) row, and never staged data.
// (never draw from cache) applies here too: eligibility for the worksite op is
// decided from this live read, not from what was staged earlier in this same checkout.
async function freshRowForWorksiteSigning(deedUid) {
  try {
    const resp = await fetch(playerQueryAppend(`/api/plots/${encodeURIComponent(deedUid)}`), { cache: 'no-store' });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (error) {
    return null;
  }
}

// first clears are included in the stake preview and one Keychain transaction.
// Existing-worksite changes retain 's fresh-read, post-stake signing path below.
// Gates Undo-all / the Sign button for the FULL orchestration below, not just the card-move
// broadcast (CERES_BROADCAST_ACTIVE already covers that half; this covers the worksite-build
// tail that runs after it, when CERES_BROADCAST_ACTIVE has already dropped back to 0).
export let CERES_WORKSITE_SIGNING = false;
export function duplicateUnsignedStakeRef(entries = CERES_STAGE) {
  const seen = new Set();
  for (const entry of entries) {
    if (entry.action === 'unstake' || entry.txId || !entry.asset_ref) continue;
    const ref = String(entry.asset_ref);
    if (seen.has(ref)) return ref;
    seen.add(ref);
  }
  return null;
}
export function mintCheckoutRunId(now = new Date()) { return mintTransactionRunId(now); }

// `deedUid` scopes the whole run to ONE plot (the worksite window's Sign): only that plot's staged moves are
// signed, then only its build. Without it, the checkout's portfolio Sign runs everything, as before.
export async function confirmCheckoutWithWorksites({ deedUid = null } = {}) {
  if (duplicateUnsignedStakeRef()) return { ok: false, reason: 'The same card or item is staged to more than one plot or slot. Move or undo one change before signing.' };
  if (CERES_WORKSITE_SIGNING) return { ok: false, reason: 'Signing is in progress.' };
  let runId = null;
  const checkoutRunId = () => runId || (runId = mintCheckoutRunId());
  CERES_WORKSITE_SIGNING = true;
  try {
    const combineRun = deedUid ? { choice: 'without', reviews: [] } : await preparePlanCombineRun();
    if (combineRun.choice === 'cancel') return { ok: false, reason: 'Signing cancelled.' };
    const previewKey = deedUid || CERES_PORTFOLIO_KEY;
    const plotRow = deedUid ? BOARD?.plots?.find((plot) => plot.deed_uid === deedUid) : null;
    if (deedUid && !plotRow) return { ok: false, reason: 'This plot is no longer on the current board. Reopen it before signing.' };
    const signable = checkoutSignableEntries();
    const stagedMoveDeeds = new Set(signable.map((entry) => entry.deed_uid));
    const operationTarget = deedUid ? plotRow : { scope: 'portfolio' };
    let combinedWorksiteDeeds = new Set();
    if ((deedUid ? signable.some((entry) => entry.deed_uid === deedUid) : signable.length) || [...CERES_STAGED_WORKSITES.entries()].some(([id, build]) => build.firstClear && (!deedUid || id === deedUid))) {
      if (!CERES_PREVIEWS.get(previewKey)?.payload) await requestOperationPreview(operationTarget);
      const preview = CERES_PREVIEWS.get(previewKey);
      if (!preview?.payload) {
        const reason = preview?.error || 'Could not build the staged transaction.';
        if (deedUid) setWorksiteBuildBlocked(deedUid, reason);
        return { ok: false, reason };
      }
      combinedWorksiteDeeds = new Set(preview.payload.combined_worksite_deeds || []);
      if (preview.payload.chunks?.length) await confirmOperationBroadcast(operationTarget, { runId: checkoutRunId(), runKind: 'plan' });
      if (checkoutSignableEntries().some((entry) => !entry.txId)) return { ok: false, reason: 'The staged asset signatures did not finish. Deed locks were left unsigned.' };
      const unsignedCombined = [...combinedWorksiteDeeds].find((combinedDeedUid) => {
        const current = CERES_STAGED_WORKSITES.get(combinedDeedUid);
        return current && !current.txId;
      });
      if (unsignedCombined) {
        const current = CERES_STAGED_WORKSITES.get(unsignedCombined);
        const reason = current?.blockedReason || 'The wallet did not accept the staged transaction.';
        setWorksiteBuildBlocked(unsignedCombined, reason);
        return { ok: false, reason };
      }
    }
    if (combineRun.choice === 'all') {
      for (const review of combineRun.reviews) await signPlanCombine(review);
    }
    const deedUids = deedUid ? [deedUid] : [...CERES_STAGED_WORKSITES.keys()];
    const failures = [];
    const worksitePlans = [];
    for (const currentDeedUid of deedUids) {
      if (!CERES_STAGED_WORKSITES.has(currentDeedUid)) continue;
      if (combinedWorksiteDeeds.has(currentDeedUid)) continue;
      const build = CERES_STAGED_WORKSITES.get(currentDeedUid);
      if (build.txId || build.parkedReason) continue;
      if (build.firstClear && stagedMoveDeeds.has(currentDeedUid)) {
        const reason = CERES_PREVIEWS.get(previewKey)?.error || 'Power Core, workers, and the first worksite must be signed in one transaction.';
        setWorksiteBuildBlocked(currentDeedUid, reason);
        failures.push(reason);
        continue;
      }
      const fresh = await freshRowForWorksiteSigning(currentDeedUid);
      if (!fresh) {
        const reason = 'Could not read this plot just now -- try signing again.';
        setWorksiteBuildBlocked(currentDeedUid, reason);
        failures.push(reason);
        continue;
      }
      const idx = BOARD?.plots?.findIndex((plot) => plot.deed_uid === currentDeedUid) ?? -1;
      if (idx !== -1) BOARD.plots[idx] = fresh;
      updateMountedRow(currentDeedUid);
      if (fresh.swap_eligibility && fresh.swap_eligibility.eligible === false) {
        const reason = fresh.swap_eligibility.notice?.message || fresh.swap_eligibility.reason || 'This plot is still not eligible for a worksite change.';
        setWorksiteBuildBlocked(currentDeedUid, reason);
        failures.push(reason);
        continue;
      }
      try {
        const plan = await prepareStagedWorksiteBuild(fresh, build.target, build.timeCrystals || 0);
        worksitePlans.push({ currentDeedUid, build, fresh, plan });
      } catch (error) {
        setWorksiteBuildBlocked(currentDeedUid, error.message);
        failures.push(error.message);
      }
    }
    // each finished construction's "Feed the workers", batched with the builds. Built by the worksite
    // window's own request; one that is not ready or fails to prepare is set aside with its reason, never a veto.
    if (!deedUid) {
      for (const item of constructionModel().signable) {
        try {
          const plan = await prepareWorksiteCompletion(item.row);
          worksitePlans.push({ currentDeedUid: item.deedUid, feed: item, fresh: item.row, plan });
        } catch (error) {
          markFeedAside(item.deedUid, error instanceof Error ? error.message : error);
        }
      }
    }
    const envelopes = worksitePlans.length ? await prepareSigningBatch(worksitePlans[0].plan.account, worksitePlans.map(item => item.plan.chunk.operations), 'Posting', { runId: checkoutRunId(), runKind: 'plan' }) : null;
    for (const [index, { currentDeedUid, build, feed, fresh, plan }] of worksitePlans.entries()) {
      if (feed) {
        const result = await signStagedWorksiteBuild(fresh, null, 0, plan, envelopes?.[index]);
        if (result.ok) {
          markFeedSent(currentDeedUid, result.txId);
          stageToast(`Feed the workers (${feed.toLabel}) was sent for signing.`);
          continue;
        }
        const reason = result.reason || 'Could not sign Feed the workers.';
        markFeedAside(currentDeedUid, reason);
        failures.push(reason);
        break;
      }
      build.pendingAccount = plan.account;
      build.pendingOperations = plan.chunk.operations;
      const result = await signStagedWorksiteBuild(fresh, build.target, build.timeCrystals || 0, plan, envelopes?.[index]);
      if (result.ok) {
        await unstageWorksiteBuild(currentDeedUid);
        stageToast(`${build.targetLabel || 'The worksite'} build was sent for signing.`);
      } else {
        const reason = result.reason || 'Could not sign the worksite build.';
        setWorksiteBuildBlocked(currentDeedUid, reason);
        failures.push(reason);
        break;
      }
    }
    if (!failures.length) {
      for (const [lockUid, lock] of STAGED_PLOT_LOCKS) {
        if ((deedUid && lockUid !== deedUid) || lock.txId || lock.blockedReason) continue;
        const fresh = await freshRowForWorksiteSigning(lockUid);
        if (!fresh) { failures.push('The live deed state could not be checked.'); break; }
        try {
          await signPlotLockReview(fresh);
          updateMountedRow(lockUid);
        } catch (error) { failures.push(error instanceof Error ? error.message : String(error)); break; }
      }
    }
    return failures.length ? { ok: false, reason: failures.join(' ') } : { ok: true, reason: null };
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    if (deedUid) setWorksiteBuildBlocked(deedUid, reason);
    return { ok: false, reason };
  } finally {
    CERES_WORKSITE_SIGNING = false;
    renderCheckoutSurface();
  }
}

function worksiteBuildHtml(deedUid, build) {
  if (!build) return '';
  const label = esc(build.targetLabel || build.target);
  const status = build.parkedReason
    ? `<span class="checkout-move-status ch-status is-waiting">${esc(build.parkedReason)}</span>`
    : build.blockedReason
    ? `<span class="checkout-move-status ch-status is-bad">Blocked: ${esc(build.blockedReason)}</span>`
    : build.firstClear
    ? `<span class="checkout-move-status ch-status is-ok">Signing order: Power Core → workers + Runi → Totem + Title → Build ${label}; empty steps are omitted</span>`
    : `<span class="checkout-move-status ch-status is-ok">Signs staged assets first, then Build ${label}</span>`;
  return `<div class="ch-worksite-build-row"><span><strong>Build ${label}</strong> · ${status}</span>`
    + `<button type="button" class="hm-btn is-ghost ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-checkout-action="undo-worksite" data-deed-uid="${esc(deedUid)}"${CERES_WORKSITE_SIGNING || build.txId ? ' disabled' : ''}>Undo</button></div>`;
}

export function checkoutBlockedInFlightEntries() {
  return CERES_STAGE.filter((entry) => entry.txId && (entry.broadcast_at || entry.queued_at))
    .concat([...CERES_STAGED_WORKSITES.values()].filter((entry) => entry.txId && (entry.broadcast_at || entry.queued_at)));
}

// (maintainer: "a red staged change currently blocks signing valid staged changes next to it ... they could remain
// there with the infos we show now but being exempt from the signing and allow signing all valid (green and orange).
// Say so clearly"; orange ruling: "Sign its unstake"). A not-ready move is SET ASIDE, never a veto on the rest
// red (cooldown / Survival) is listed but not signed; orange (staked on another plot) signs only its unstake there.
export function stageSetAside(entry) {
  if (entry?._plan_invalid_reason) return 'invalid';
  if (entry?.refused_at) return 'refused'; // phase 4 (K6): a game refusal is set aside, never a veto
  if (entry?.action === 'unstake') return null;
  if (entry?._asset?.planned || (entry?.chain_id && !chainPlacementReady(chainPlans()[entry.chain_id], BOARD?.generated_at))) return 'blocked'; // verified result and newer capture only.
  if (entry?._source_deed_uid && !['totem', 'title', 'power_core'].includes(entry.group_id)) return 'elsewhere'; // from another plot -- its unstake there first, whatever its cooldown
  if (entry?.cooldown && entry.cooldown.ready === false) return 'blocked';
  if (entry?.group_id === 'worker' && waitsForPlannedPower(entry)) return 'power';
  return null;
}
// (plan: "Waiting for planned power"): a worker on a plot with no live power source, whose staged Power Core
// or Runi cannot be signed yet, waits with that batch -- staked alone it would produce nothing.
function waitsForPlannedPower(entry) {
  const row = BOARD?.plots.find((plot) => plot.deed_uid === entry.deed_uid);
  if (!row) return false;
  const facts = row.staking_facts || row;
  if (facts.is_energized === true || facts.is_power_core_staked === true || facts.is_runi_staked === true) return false;
  const power = CERES_STAGE.filter((item) => item.deed_uid === entry.deed_uid && !item._synthetic && !item.txId
    && (item.group_id === 'power_core' || item.group_id === 'runi') && item.action !== 'unstake');
  if (!power.length) return false;
  return power.every((item) => item._plan_invalid_reason || item.refused_at || (item._source_deed_uid && item.group_id !== 'power_core') || (item.cooldown && item.cooldown.ready === false));
}

export function checkoutSignableEntries() {
  const out = [];
  for (const entry of CERES_STAGE) {
    const aside = stageSetAside(entry);
    if (!aside) { out.push(entry); continue; }
    if (aside === 'elsewhere' && !entry._unstake_tx) {
      out.push({
        deed_uid: entry._source_deed_uid, group_id: entry.group_id, slot_index: null, action: 'unstake',
        asset_ref: entry.asset_ref, evict_asset_ref: null, delta: null, delta_status: 'not_calculated', delta_reason: 'plot_impact_unknown',
        base: { status: 'not_applicable', value: null, low: null, high: null, reason: 'removal_pricing_pending' },
        _asset_name: entry._asset_name || null, _asset: entry._asset || null, _previous_asset: entry._asset || null, _synthetic: true,
        // Plain data (not a reference): survives the in-flight store, so a confirmation after a reload still records it.
        _restake: { asset_ref: entry.asset_ref, name: entry._asset_name || null, target_deed_uid: entry.deed_uid, group_id: entry.group_id, slot_index: entry.slot_index, source_deed_uid: entry._source_deed_uid },
      });
    }
  }
  return out;
}

// the plot as a badge in its territory colour, like the Plot column.
// shared with the worker picker's "On land" cell; `{ static: true }` renders the
// same badge as a non-navigating span (the picker is a modal, so no board jump from inside it).
export function plotBadgeHtml(row, deedUid, { static: isStatic = false } = {}) {
  const { cls } = territoryCellHtml(row?.territory, deedUid);
  const label = checkoutPlotLabel(row, deedUid);
  if (isStatic) return `<span class="ch-plot-badge ${cls}" style="cursor:default">${esc(label)}</span>`;
  return `<button type="button" class="ch-plot-jump ch-plot-badge ceres-region-plaque ${cls}" data-control-kind="region-plaque" data-checkout-action="go" data-deed-uid="${esc(deedUid)}" aria-label="Show plot ${esc(label)} on the board">${esc(label)}</button>`;
}

function checkoutPlotLabel(row, deedUid) {
  return formatPlotLabel(row?.plot || deedUid);
}

function checkoutEntryName(entry, row) {
  if (entry._asset_name) return entry._asset_name;
  const group = row ? editorGroups(row).find((item) => item.id === entry.group_id) : null;
  return group?.slots[entry.slot_index]?.name || entry.asset_ref || 'Unknown asset';
}

function checkoutEntryAsset(entry, row) {
  if (entry._asset) return entry._asset;
  if (entry.action === 'unstake' && entry._previous_asset) return entry._previous_asset;
  const group = row ? editorGroups(row).find((item) => item.id === entry.group_id) : null;
  return group?.slots[entry.slot_index] || null;
}

// Gate document #121: never show an internal reason code; one plain sentence per code.
const STAGE_REASON_TEXT = {
  'stage impact unavailable': 'The server could not price this staged change yet.',
  'plot impact unavailable': 'The server could not price the effect on this plot yet.',
};

// Total PP green when it rises, red when it falls.
function ppClass(value) { return Number.isFinite(value) ? (value > 0 ? ' is-up' : value < 0 ? ' is-down' : '') : ''; }

function stageDeltaText(value, reason = 'stage impact unavailable') {
  return Number.isFinite(value) ? signedPp(value) : 'not calculated';
}

function stageDeltaTip(value, reason = 'stage impact unavailable') {
  if (Number.isFinite(value)) return '';
  const plain = STAGE_REASON_TEXT[String(reason || '').replaceAll('_', ' ')] || 'The server returned no Total PP for this move, so it is not counted.';
  return tipAttrs({ title: 'Total PP not calculated', body: plain, peer: 'checkout_metrics' });
}

function checkoutGroups(model, plotsById) {
  const groups = new Map();
  const planIds = new Set(activeChainPlans().map(([id]) => id));
  model.groups.forEach((allItems, deedUid) => {
    // an upgrade plan's planned card is shown in the plan card, beside its steps -- not again in its plot's list.
    const items = allItems.filter(({ entry }) => !(entry.action !== 'unstake' && entry._asset?.planned && planIds.has(entry.chain_id)));
    if (!items.length) return;
    const row = plotsById.get(deedUid);
    const impact = stageImpactForRow(deedUid);
    const delta = items.every(({ entry }) => entry.action !== 'unstake' && entry._asset?.planned) ? 0
      : impact?.status === 'point' && Number.isFinite(impact.harvest_pp_delta) ? impact.harvest_pp_delta : null;
    // Base-PP-stage (construction) total per plot -- combined separately from `delta`
    // (Total/harvest-stage) above, never blended into it. See the internal rules's two-stage chain.
    const base = combineEstimates(items.map(({ entry }) => entry.base));
    groups.set(deedUid, { row, deedUid, impact, delta, base, items: items.map((item) => ({ ...item, name: checkoutEntryName(item.entry, row) })), worksiteBuild: null, hasUndone: false });
  });
  for (const [deedUid, build] of CERES_STAGED_WORKSITES) {
    if (!groups.has(deedUid)) groups.set(deedUid, { row: plotsById.get(deedUid), deedUid, impact: null, delta: null, base: null, items: [], worksiteBuild: build, hasUndone: false });
    else groups.get(deedUid).worksiteBuild = build;
  }
  for (const item of model.constructions?.items || []) {
    if (!groups.has(item.deedUid)) groups.set(item.deedUid, { row: plotsById.get(item.deedUid), deedUid: item.deedUid, impact: null, delta: null, base: null, items: [], worksiteBuild: null, hasUndone: false });
    groups.get(item.deedUid).construction = item;
  }
  for (const [deedUid, lock] of STAGED_PLOT_LOCKS) {
    if (!groups.has(deedUid)) groups.set(deedUid, { row: plotsById.get(deedUid), deedUid, impact: null, delta: null, base: null, items: [], worksiteBuild: null, hasUndone: false });
    groups.get(deedUid).plotLock = lock;
  }
  // An undone tile is useful only while another change keeps its plot in the modal.
  for (const [key, undone] of [...CH_UNDONE]) {
    if (CERES_STAGE.some((entry) => undoneKey(entry.deed_uid, entry.group_id, entry.slot_index) === key)) { CH_UNDONE.delete(key); continue; }
    const group = groups.get(undone.deed_uid);
    if (!group) { CH_UNDONE.delete(key); continue; }
    group.hasUndone = true;
  }
  return groups;
}

function plotLockPlanHtml(group) {
  const entry = group.plotLock;
  if (!entry) return '';
  const label = entry.action === 'lock' ? 'Lock deed' : 'Unlock deed';
  const detail = entry.action === 'lock' ? 'Unlock delay: 3 days' : '';
  const tip = tipAttrs({ title: label, body: [detail, entry.blockedReason || '', entry.txId || '', entry.txId ? '' : 'Click to undo.'].filter(Boolean).join(' ') });
  const stageClass = entry.blockedReason ? 'is-stage-blocked' : 'is-stage-ready'; // same staged outline as the other change tiles
  // a signed change the game has no record of is dismissed from its tile; one with no answer is checked again.
  const signedAction = entry.notFound ? 'lock-dismiss' : entry.checkPaused ? 'lock-recheck' : '';
  const signedTip = signedAction ? tipAttrs({ title: signedPlotLockText(entry), body: entry.notFound ? 'Click to dismiss.' : 'Click to check again.' }) : tip;
  return `<span class="slot-tile ch-lock-plan ${stageClass}" ${entry.txId ? (signedAction ? `data-checkout-action="${signedAction}" role="button" tabindex="0" data-deed-uid="${esc(group.deedUid)}"` : 'data-lock-in-flight="true"') : `data-checkout-action="lock-undo" role="button" tabindex="0" data-deed-uid="${esc(group.deedUid)}"`}${entry.txId ? signedTip : tip}><span class="ch-lock-art">${plotLockSvg(entry.action === 'lock')}</span><span class="slot-caption"><strong class="slot-name">${esc(label)}</strong>${entry.action === 'lock' ? '<span>Unlock delay: 3 days</span>' : ''}</span></span>`;
}

// a waiting change counts down live to the time it can be signed (Survival included), then names that time.
function waitUntil(cooldown) {
  const until = cooldown?.ready_at;
  return ['cooling', 'survival_locked'].includes(cooldown?.state) && until && Date.parse(until) > Date.now() ? until : null;
}
function waitTimerHtml(cooldown) {
  const until = waitUntil(cooldown);
  if (!until) return '';
  const when = humanizeInstant(until)?.absolute;
  return `can be signed in <span data-countdown-until="${esc(until)}" data-countdown-ended="now">${esc(countdownText(until))}</span>${when ? ` · ${esc(when)}` : ''}${cooldown.state === 'survival_locked' ? ' (dead from Survival; sooner at the season reset)' : ''}`;
}

// one plain status per change -- what happens to it when you sign.
function statusHtml(entry, status, plotsById) {
  if (entry.action !== 'unstake' && entry._asset?.planned) return '<span class="checkout-move-status ch-status is-planned">Planned · after the combine</span>';
  if (status === 'working') return `<span class="checkout-move-status ch-status is-dim">${esc(entry.queued_at && !entry.broadcast_at ? 'Signed — queued for broadcast.' : noticeMessage(catalogNotice('verification_in_flight')))}</span>`;
  // (maintainer): no longer valid = red, the reason, and it leaves the queue when the window closes.
  if (status === 'invalid') return `<span class="checkout-move-status ch-status is-bad">No longer valid — ${esc(dropReasonText(entry._plan_invalid_reason, entry._plan_invalid_detail))}. It is removed from Changes when you close this window.</span>`;
  if (status === 'waiting' && entry._worksite_parked_reason) return `<span class="checkout-move-status ch-status is-waiting">${esc(entry._worksite_parked_reason)}</span>`;
  if (status === 'refused') return `<span class="checkout-move-status ch-status is-bad">Refused by the game: ${esc(entry.refusal_reason || 'no reason given')}. Undo it, or stage it again to retry.</span>`;
  if (status === 'orange') {
    const source = entry._source_deed_uid;
    const link = `plot ${plotBadgeHtml(plotsById.get(source), source).replace('data-checkout-action="go"', `data-checkout-action="go-source" data-asset-ref="${esc(entry.asset_ref)}"`)}`;
    return `<span class="checkout-move-status ch-status is-warn">${entry._unstake_tx ? `Unstake from ${link} sent` : `Signs the unstake from ${link} now`} · the stake here waits for its 72-hour cooldown</span>`;
  }
  // waiting = its own gold label with the time until it can be signed (the pending clock's colour).
  if (status === 'waiting') return `<span class="checkout-move-status ch-status is-waiting">Waiting · ${stageSetAside(entry) === 'power' ? 'signable once this plot\'s staged Power Core or Runi is signed' : waitTimerHtml(entry.cooldown) || (entry.cooldown?.state === 'cooling' ? `can be signed after ${esc(cooldownReasonFragment(entry.cooldown).replace(/^cooling until /, ''))}` : esc(cooldownReasonFragment(entry.cooldown)))}</span>`;
  if (entry._source_deed_uid && ['totem', 'title', 'power_core'].includes(entry.group_id)) {
    return `<span class="checkout-move-status ch-status is-ok">Move from ${plotBadgeHtml(plotsById.get(entry._source_deed_uid), entry._source_deed_uid)} · unstake then stake</span>`;
  }
  return '<span class="checkout-move-status ch-status is-ok">Ready to sign</span>';
}

// (maintainer: "we could easily fit all assets of a plot in there ... Move the stake/unstake below the tiles ...
// This would save us tons of room vertically"): one tile strip per plot -- the Fill modal's row (workers, power
// totem, title). A changed tile pulses in its stage colour with the action and its state under it; an unchanged
// tile stays live but dimmed. Clicking a changed tile undoes it; the plot badge jumps to the plot.
const STRIP_STATE = { included: 'ready', working: 'ready', orange: 'elsewhere', waiting: 'elsewhere', invalid: 'invalid', refused: 'blocked' };  // a waiting tile wears the orange outline, a refused one the red
// An undone change stays dimmed while another change keeps that plot in the modal.
// Remembered only while the modal is open -- an undone change is not a plan.
const CH_UNDONE = new Map();
const undoneKey = (deedUid, groupId, slotIndex) => `${deedUid}|${groupId}|${slotIndex}`;
export function rememberUndone(entry) {
  CH_UNDONE.set(undoneKey(entry.deed_uid, entry.group_id, entry.slot_index), { ...entry });
}
export function forgetUndone() { CH_UNDONE.clear(); }
export async function restoreUndoneChange(deedUid, groupId, slotIndex) {
  const key = undoneKey(deedUid, groupId, slotIndex);
  const entry = CH_UNDONE.get(key);
  if (!entry) return;
  // A06: a detached unstake must not be restored as an ordinary move.
  if (entry.chain_id && entry.action === 'unstake') {
    stageToast('Review the upgrade plan to add this copy again. Its linked unstake cannot be restored here.', true);
    return;
  }
  CH_UNDONE.delete(key);
  const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
  if (!row) return;
  stageForSlot(deedUid, groupId, Number(slotIndex), entry.action, entry.asset_ref, entry.delta, entry.evict_asset_ref || null,
    entry._asset_name || null, entry._source_deed_uid || null, entry.base || null, entry.cooldown || null, entry._asset || null);
  const restored = CERES_STAGE.find((item) => item.deed_uid === deedUid && item.group_id === groupId && String(item.slot_index) === String(slotIndex));
  if (restored && entry._previous_asset) restored._previous_asset = entry._previous_asset;
  if (entry.action === 'unstake') {
    try { applyRemovalImpactToStage(deedUid, await removalImpact(row, stagedRemovalRefs(deedUid))); } catch (error) { /* priced on the next change */ }
  }
  applyStageToRow(row, { force: true });
  updateMountedRow(deedUid);
  persistStagePlans();
  renderCheckoutSurface();
}
function stripOccupant(entry, groupId, row) {
  const asset = checkoutEntryAsset(entry, row);
  if (!asset) return null;
  return asset.image ? asset : editorOccupant(asset, groupId); // a kept item without its icon is rebuilt
}
// the planned card of an upgrade plan, drawn like a Changes strip tile with its two-row box, for the plan card.
export function planPlacementTileHtml(id, plan) {
  const entry = CERES_STAGE.find((item) => item.chain_id === id && item.action !== 'unstake');
  const source = entry ? { deed_uid: entry.deed_uid, slot_index: Number(entry.slot_index) } : chainStageBackSource(plan);
  const row = source && BOARD?.plots?.find((plot) => plot.deed_uid === source.deed_uid);
  const group = row ? editorGroups(row).find((item) => item.id === 'worker') : null;
  if (!group) return '';
  const asset = entry ? stripOccupant(entry, 'worker', row)
    : editorOccupant({ ...plan.card, level: plan.expected?.level, base_pp: plan.expected?.base_pp, planned: true, chain_id: id, asset_ref: `planned:${id}` }, 'worker');
  if (!asset) return '';
  const back = chainReturn(plan, source.deed_uid, source.slot_index);
  const where = `plot ${formatPlotLabel(row.plot)}, slot ${source.slot_index + 1}`;
  const tile = renderPreviewTile(group, asset, { highlight: 'planned', returnMarker: plannedReturnChip(asset, source.deed_uid, source.slot_index), tip: false });
  const ids = `data-deed-uid="${esc(source.deed_uid)}" data-group-id="worker" data-slot-index="${esc(source.slot_index)}"`;
  const tileHtml = entry
    ? `<span class="fp-slot ch-slot" data-checkout-action="strip-undo" role="button" tabindex="0" ${ids}${tipAttrs({ title: asset.name, body: `Staged on ${where}; its stake is signed after the combine. Click to undo.` })}>${tile}</span>`
    : `<span class="fp-slot ch-slot is-in-flight"${tipAttrs({ title: asset.name, body: `Staged on ${where} as soon as its unstake is confirmed.` })}>${tile}</span>`;
  // (to this plot\""): the tile alone; the Place text says where it goes.
  return `<div class="chain-place-tile ch-strip-scroll" style="--ch-n: 1"><div class="fp-tiles slot-grid ch-tiles">${tileHtml}</div></div>`;
}
function stripStateLabel(entry, status) {
  // short first row, state second.
  if (entry.action !== 'unstake' && entry._asset?.planned) return `${chainReturn(chainPlans()[entry.chain_id], entry.deed_uid, entry.slot_index)?.short || 'Planned'} · after combine`;
  if (status === 'invalid') return "Can't stage";
  const action = entry.action === 'unstake' ? 'Unstake' : entry.action === 'replace' ? 'Replace' : 'Stake';
  const state = { working: entry.queued_at && !entry.broadcast_at ? 'queued' : 'sent', refused: 'refused', orange: 'moving', waiting: 'waiting' }[status] || 'ready';
  const back = entry.action !== 'unstake' && entry.chain_id && chainReturn(chainPlans()[entry.chain_id], entry.deed_uid, entry.slot_index);
  return `${back?.short || action} · ${state}`;
}
function statusText(entry, status, plotsById) {
  return statusHtml(entry, status, plotsById).replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
}
const CHECKOUT_DETAIL_REQUESTED = new WeakSet();
function plotStripHtml(group, plotsById) {
  const row = group.row;
  if (row && !EXPANDED_DATA_LOADED.has(row.deed_uid)) {
    if (!CHECKOUT_DETAIL_REQUESTED.has(row)) {
      CHECKOUT_DETAIL_REQUESTED.add(row);
      queueMicrotask(() => loadPlotDetail(row).finally(() => {
        if (S.CERES_CHECKOUT_OPEN) renderCheckoutSurface();
      }));
    }
    const error = EXPAND_ERRORS.get(row.deed_uid);
    return error
      ? `<p role="status">Plot details unavailable. <button class="ceres-btn ceres-btn--secondary" data-slot-action="retry-detail" data-deed-uid="${esc(row.deed_uid)}">Retry</button></p>`
      // the shared spinner, centred in a slot the size of the strip it becomes, so nothing jumps when it arrives.
      : `<div class="ch-strip-scroll ch-strip-loading" style="--ch-n: ${Math.max(1, group.items.length + (group.plotLock ? 1 : 0))}" role="status" aria-label="Loading plot details"><span class="spinner" aria-hidden="true"></span></div>`;
  }
  const groups = row ? editorGroups(row) : [];
  const byKey = new Map(group.items.map((item) => [`${item.entry.group_id}|${item.entry.group_id === 'worker' ? item.entry.slot_index : 0}`, item]));
  const at = (groupId, index) => {
    const g = groups.find((item) => item.id === groupId) || null;
    const item = byKey.get(`${groupId}|${index}`) || null;
    const undone = !item ? CH_UNDONE.get(undoneKey(group.deedUid, groupId, index)) || null : null;
    const occupant = item ? stripOccupant(item.entry, groupId, row) : undone ? stripOccupant(undone, groupId, row) : (g?.slots[index] || null);
    return { g, item, undone, occupant };
  };
  // (2): an undone tile is dashed and dim; a click brings the change back.
  const wrapUndone = (html, undone) => {
    const ids = `data-deed-uid="${esc(undone.deed_uid)}" data-group-id="${esc(undone.group_id)}" data-slot-index="${esc(undone.slot_index)}"`;
    return `<span class="fp-slot ch-slot is-fp-excluded" data-checkout-action="restore-undone" role="button" tabindex="0" ${ids}${tipAttrs({ title: undone._asset_name || 'Undone', body: 'Undone. Click to bring this change back.' })}>${html}</span>`;
  };
  const wrap = (html, item) => {
    const { entry, status, name } = item;
    const ids = `data-deed-uid="${esc(entry.deed_uid)}" data-group-id="${esc(entry.group_id)}" data-slot-index="${esc(entry.slot_index)}"`;
    const inFlight = status === 'working';
    const tip = tipAttrs({ title: name, body: `${statusText(entry, status, plotsById)}${inFlight ? '' : ' Click to undo.'}` });
    return `<span class="fp-slot ch-slot${inFlight ? ' is-in-flight' : ''}" ${inFlight ? '' : 'data-checkout-action="strip-undo" role="button" tabindex="0"'} ${ids}${tip}>${html}</span>`;
  };
  const tileOpts = (item) => (item
    ? { highlight: item.entry.action !== 'unstake' && item.entry._asset?.planned ? 'planned' : STRIP_STATE[item.status] || 'ready', returnMarker: plannedReturnChip(item.entry._asset, item.entry.deed_uid, item.entry.slot_index), tip: false, extraClass: item.entry.action === 'unstake' ? 'is-pending-removal' : '' }
    : { tip: false, extraClass: 'is-ch-unchanged' });
  const tile = (groupId, index, emptyLabel) => {
    const { g, item, undone, occupant } = at(groupId, index);
    if (!g) return '';
    const html = renderPreviewTile(g, occupant, { ...(undone ? { tip: false } : tileOpts(item)), emptyLabel });
    return item ? wrap(html, item) : undone ? wrapUndone(html, undone) : html;
  };
  const cell = (item, undone = null) => {
    if (item) {
      const label = stripStateLabel(item.entry, item.status).replace(/ \(it was in slot \d+\)/, '');
      const labelHtml = item.entry.action === 'unstake' || item.entry.chain_id ? esc(label).replace(' · ', '<br>') : esc(label);
      const unstakeClass = item.entry.action === 'unstake' ? ' class="is-unstake"' : '';
      const until = waitUntil(item.entry.cooldown);
      if (item.status === 'waiting' && until) return `<span class="editor-base-pp-cell is-filled ch-state is-waiting ch-wait-timer"${tipAttrs({ title: label, body: statusText(item.entry, item.status, plotsById) })}><span class="spinner spinner-sm" aria-hidden="true"></span><b data-countdown-until="${esc(until)}" data-countdown-compact data-countdown-ended="now">${esc(countdownText(until, Date.now(), { compact: true }))}</b></span>`;
      return `<span class="editor-base-pp-cell is-filled ch-state is-${item.entry.action !== 'unstake' && item.entry._asset?.planned ? 'planned' : esc(item.status)}"${tipAttrs({ title: label, body: statusText(item.entry, item.status, plotsById) })}><b${unstakeClass}>${labelHtml}</b></span>`;
    }
    return undone
      ? `<span class="editor-base-pp-cell is-filled ch-state is-undone"${tipAttrs({ title: 'Undone', body: 'Click the tile to bring this change back.' })}><b>Undone</b></span>`
      : '<span class="editor-base-pp-cell" aria-hidden="true"></span>';
  };
  const stagedSlots = group.items.filter((item) => item.entry.group_id === 'worker').map((item) => Number(item.entry.slot_index) + 1);
  const changedWorkerSlots = [...new Set(stagedSlots)];
  const workerTiles = changedWorkerSlots.map((slot) => tile('worker', slot - 1, 'Empty')).join('');
  const workerCells = changedWorkerSlots.map((slot) => { const w = at('worker', slot - 1); return cell(w.item, w.undone); }).join('');
  // The plot view's own rule (): the Runi is the large tile with the Power Core as its satellite; with no Runi
  // the Power Core takes the large tile.
  const runiAt = at('runi', 0);
  const coreAt = at('power_core', 0);
  const runiLarge = Boolean(runiAt.occupant) || !coreAt.occupant;
  const large = runiLarge ? runiAt : coreAt;
  const sat = runiLarge ? coreAt : runiAt;
  const largeHtml = large.g ? renderPreviewTile(large.g, large.occupant, { ...(large.undone ? { tip: false } : tileOpts(large.item)), emptyLabel: runiLarge ? 'Power slot · Runi' : 'Power slot · Power Core' }) : '';
  const satHtml = sat.g && sat.occupant ? renderPreviewSatellite(sat.g, sat.occupant, { highlight: sat.item ? STRIP_STATE[sat.item.status] || 'ready' : false, tip: false, extraClass: sat.item || sat.undone ? '' : 'is-ch-unchanged' }) : '';
  const wrapAny = (html, slot) => (slot.item ? wrap(html, slot.item) : slot.undone ? wrapUndone(html, slot.undone) : html);
  const powerChanged = Boolean(large.item || large.undone || sat.item || sat.undone);
  const powerTile = powerChanged ? `<section class="slot-power" aria-label="Power slot"><div class="slot-power-stage"><div class="slot-power-large">${wrapAny(largeHtml, large)}</div>${wrapAny(satHtml, sat)}</div></section>` : '';
  const powerSlot = large.item || large.undone ? large : sat;
  const powerCell = cell(powerSlot.item, powerSlot.undone);
  const totemAt = at('totem', 0); const titleAt = at('title', 0);
  const itemTiles = `${powerTile}${totemAt.item || totemAt.undone ? tile('totem', 0, 'No totem') : ''}${titleAt.item || titleAt.undone ? tile('title', 0, 'No title') : ''}${plotLockPlanHtml(group)}`;
  const itemCells = `${powerChanged ? powerCell : ''}${totemAt.item || totemAt.undone ? cell(totemAt.item, totemAt.undone) : ''}${titleAt.item || titleAt.undone ? cell(titleAt.item, titleAt.undone) : ''}`;
  const divider = workerTiles && itemTiles ? '<span class="slot-grid-divider" aria-hidden="true"></span>' : '';
  const cellDivider = workerCells && (itemCells || group.plotLock) ? '<span class="editor-base-pp-divider" aria-hidden="true"></span>' : '';
  const lock = group.plotLock;
  const lockState = lock?.txId ? (lock.notFound ? 'Not found' : lock.accepted ? 'Accepted · waiting' : lock.checkPaused ? 'No answer' : 'Signed · checking')
    : lock?.blockedReason ? 'Blocked' : `${lock?.action === 'lock' ? 'Lock' : 'Unlock'} ready`;
  // "Unlock / ready" on two rows, aligned with "Unstake / ready" (same markup as the unstake cell).
  const lockLabelHtml = lock && !lock.txId && !lock.blockedReason ? esc(lockState).replace(' ready', '<br>ready') : esc(lockState);
  const lockCell = lock ? `<span class="editor-base-pp-cell is-filled ch-state ch-lock-state${lock.notFound ? ' is-refused' : lock.txId ? ' is-working' : lock.blockedReason ? ' is-waiting' : ' is-included'}"${tipAttrs({ title: lockState, body: lock.blockedReason || (lock.txId ? signedPlotLockText(lock) : 'Click the deed tile to undo.') })}><b class="is-unstake">${lockLabelHtml}</b></span>` : '';
  return `<div class="ch-strip-scroll"><div class="fp-tiles slot-grid ch-tiles">${workerTiles}${divider}${itemTiles}</div>`
    + `<div class="fp-badge-row editor-base-pp-row ch-cells" aria-label="Staged changes">${workerCells}${cellDivider}${itemCells}${lockCell}</div></div>`;
}

// (maintainer: "the mobile changes window has a weird vertical scroll for each plot ... the horizontal scroll is somewhat
// suboptimal ... Much can be simplified"): on the phone each plot lists its changes as rows -- picture, what changes
// its state, and an Undo button -- instead of the desktop tile strip with a state box under every tile.
const CH_SLOT_LABEL = { power_core: 'Power Core', runi: 'Runi', totem: 'Totem', title: 'Title' };
function changeSlotLabel(entry) {
  if (entry.group_id === 'worker') return `Worker slot ${Number(entry.slot_index) + 1}`;
  return CH_SLOT_LABEL[entry.group_id] || String(entry.group_id || '').replace('_', ' ');
}
function changeThumbHtml(entry, row) {
  let asset = null;
  try { asset = stripOccupant(entry, entry.group_id, row); } catch (error) { asset = null; }
  return asset?.image ? `<img src="${esc(asset.image)}" alt="" loading="lazy" decoding="async">` : '';
}
// rows follow
// the plot's own order -- worker slots 1-5, Runi, then Power Core, Totem, Title; undone rows keep their place.
const CH_GROUP_RANK = { worker: 0, runi: 10, power_core: 20, totem: 30, title: 40 };
function changeRowRank(entry) {
  const base = CH_GROUP_RANK[entry.group_id] ?? 50;
  return entry.group_id === 'worker' ? base + (Number(entry.slot_index) || 0) : base;
}
const CH_ROW_TONE = { included: 'ok', working: 'dim', orange: 'warn', waiting: 'waiting', invalid: 'bad', refused: 'bad' };
function plotListHtml(group, plotsById) {
  const row = group.row;
  const ids = (entry) => `data-deed-uid="${esc(entry.deed_uid)}" data-group-id="${esc(entry.group_id)}" data-slot-index="${esc(entry.slot_index)}"`;
  const ranked = [];
  group.items.forEach(({ entry, status, name }) => {
    const inFlight = status === 'working';
    const undo = inFlight ? '' : `<button type="button" class="ceres-btn ceres-btn--secondary ch-mrow-undo" data-control-density="compact" data-checkout-action="strip-undo" ${ids(entry)} aria-label="Undo ${esc(name)}">Undo</button>`;
    ranked.push({ rank: changeRowRank(entry), html: `<li class="ch-mrow is-${entry.action !== 'unstake' && entry._asset?.planned ? 'planned' : esc(CH_ROW_TONE[status] || 'ok')}"><span class="ch-mrow-thumb" aria-hidden="true">${changeThumbHtml(entry, row)}</span>`
      + `<span class="ch-mrow-text"><strong>${esc(name)}</strong><span class="ch-mrow-what">${esc(stripStateLabel(entry, status).split(' · ')[0])} · ${esc(changeSlotLabel(entry))}</span>${statusHtml(entry, status, plotsById)}</span>${undo}</li>` });
  });
  for (const [key, undone] of CH_UNDONE) {
    if (!key.startsWith(`${group.deedUid}|`)) continue;
    ranked.push({ rank: changeRowRank(undone), html: `<li class="ch-mrow is-undone"><span class="ch-mrow-thumb" aria-hidden="true">${changeThumbHtml(undone, row)}</span>`
      + `<span class="ch-mrow-text"><strong>${esc(undone._asset_name || 'Change')}</strong><span class="ch-mrow-what">Undone · ${esc(changeSlotLabel(undone))}</span></span>`
      + `<button type="button" class="ceres-btn ceres-btn--secondary ch-mrow-undo" data-control-density="compact" data-checkout-action="restore-undone" ${ids(undone)}>Restore</button></li>` });
  }
  const rows = ranked.sort((a, b) => a.rank - b.rank).map((item) => item.html);
  const lock = group.plotLock;
  if (lock) {
    const label = lock.action === 'lock' ? 'Lock deed' : 'Unlock deed';
    const state = lock.txId ? signedPlotLockText(lock) : lock.blockedReason || (lock.action === 'lock' ? 'Ready to sign · unlock delay: 3 days' : 'Ready to sign');
    const tone = lock.notFound ? 'bad' : lock.txId ? 'dim' : lock.blockedReason ? 'bad' : 'ok';
    // a signed change the game has no record of gets Dismiss; one with no answer gets Check again.
    const signedButton = lock.notFound ? 'lock-dismiss' : lock.checkPaused ? 'lock-recheck' : '';
    const undo = lock.txId
      ? (signedButton ? `<button type="button" class="ceres-btn ceres-btn--secondary ch-mrow-undo" data-control-density="compact" data-checkout-action="${signedButton}" data-deed-uid="${esc(group.deedUid)}">${lock.notFound ? 'Dismiss' : 'Check again'}</button>` : '')
      : `<button type="button" class="ceres-btn ceres-btn--secondary ch-mrow-undo" data-control-density="compact" data-checkout-action="lock-undo" data-deed-uid="${esc(group.deedUid)}">Undo</button>`;
    rows.push(`<li class="ch-mrow is-${tone}"><span class="ch-mrow-thumb is-icon" aria-hidden="true">${plotLockSvg(lock.action === 'lock')}</span>`
      + `<span class="ch-mrow-text"><strong>${esc(label)}</strong><span class="ch-status is-${tone}">${esc(state)}</span></span>${undo}</li>`);
  }
  return rows.length ? `<ul class="ch-mlist" aria-label="Staged changes">${rows.join('')}</ul>` : '';
}

function invalidPlanRowsHtml(group, plotsById) {
  return group.items.filter((item) => item.status === 'invalid').map(({ entry, status, name }) => {
    const ids = `data-deed-uid="${esc(entry.deed_uid)}" data-group-id="${esc(entry.group_id)}" data-slot-index="${esc(entry.slot_index)}"`;
    return `<div class="ch-worksite-build-row"><span><strong>${esc(name)}</strong> · ${statusHtml(entry, status, plotsById)}</span>`
      + `<button type="button" class="hm-btn is-ghost ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-checkout-action="strip-undo" ${ids}>Undo</button></div>`;
  }).join('');
}

// phase 2: the Changes button says how many intentions there are, or why it shows without any; null hides it.
function changesButtonState(model, extra) {
  const n = activeChainPlans().length + model.intents.length + CERES_STAGED_WORKSITES.size + STAGED_PLOT_LOCKS.size + pendingStagePlanCount() + (model.constructions?.signable.length || 0);
  if (n) return { count: n, suffix: '' };
  const building = model.constructions?.items.length || 0; // a running build still shows, as waiting
  if (building && !extra.dropped) return { count: 0, suffix: `${building} building` };
  if (extra.dropped) return { count: 0, suffix: `${extra.dropped} need${extra.dropped === 1 ? 's' : ''} attention` };
  return null; // nothing staged, nothing dropped -> no button
}
function changesDescription(model, extra) {
  const parts = [`${activeChainPlans().length + model.intents.length + CERES_STAGED_WORKSITES.size + STAGED_PLOT_LOCKS.size + (model.constructions?.signable.length || 0)} staged`];
  if (model.constructions?.items.length) parts.push(`${model.constructions.items.length} worksite construction${model.constructions.items.length === 1 ? '' : 's'}`);
  if (pendingStagePlanCount()) parts.push(pendingStagePlanCount() + ' awaiting validation');
  if (extra.dropped) parts.push(`${extra.dropped} dropped`);
  return `Changes: ${parts.join(', ')}`;
}

// the Changes panel is a modal built from the
// harvest modal's parts, created once and reused; #checkout-panel keeps its id for operations.js.
function changesOverlay() {
  let el = document.getElementById('changes-overlay');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'changes-overlay';
  el.className = 'harvest-overlay';
  el.hidden = true;
  el.innerHTML = '<div class="harvest-panel changes-panel" id="checkout-panel" role="dialog" aria-modal="true" aria-labelledby="checkout-title"></div>';
  el.addEventListener('click', handleSlotEditorClick);
  el.addEventListener('keydown', (event) => {
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[role="button"][data-checkout-action]')) {
      event.preventDefault();
      event.target.click();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [...el.querySelectorAll('button:not([disabled]), input:not([disabled]), summary, a[href]')];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.body.appendChild(el);
  return el;
}

function swapCheckoutRegion(currentRegion, nextRegion) {
  if (!currentRegion || !nextRegion) return nextRegion;
  currentRegion.innerHTML = nextRegion.innerHTML;
  return currentRegion;
}

function replaceCheckoutPanelHtml(panel, html) {
  const template = document.createElement('template');
  template.innerHTML = html;
  // These reserved boxes keep their identity while only their status text changes.
  ['.ch-signing-status', '.ch-signing-note'].forEach((selector) => {
    const currentRegion = panel.querySelector(selector);
    const nextRegion = template.content.querySelector(selector);
    if (currentRegion && nextRegion) nextRegion.replaceWith(swapCheckoutRegion(currentRegion, nextRegion));
  });
  panel.replaceChildren(template.content);
}

const CHANGES_ICON = COMMIT_ICON;
const X_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';

function mobileBoardTier() {
  return document.documentElement.classList?.contains('ceres-lw-le-1279') === true;
}

export function mobileKeychainGuidanceHtml() { return walletControlsHtml(); }

// phase 2 (maintainer ruling "Accept as proposed"): ONE Changes button (#checkout-btn) and ONE panel
// (#checkout-panel) -- every change grouped by its plot, then what needs attention, the cards not planned, the signing
// review, and a fixed footer. Hidden only when there is truly nothing to show.
// a synchronous batch of stage edits (the saved-changes restore) renders the Changes surface ONCE when it
// ends, not once per edit. Only for synchronous work: nothing can paint between the edits anyway.
let CHECKOUT_BATCH_DEPTH = 0;
let CHECKOUT_BATCH_DIRTY = false;
export function batchCheckoutRenders(fn) {
  CHECKOUT_BATCH_DEPTH += 1;
  try {
    return fn();
  } finally {
    CHECKOUT_BATCH_DEPTH -= 1;
    if (!CHECKOUT_BATCH_DEPTH && CHECKOUT_BATCH_DIRTY) { CHECKOUT_BATCH_DIRTY = false; renderCheckoutSurface(); }
  }
}

export function renderCheckoutSurface() {
  if (CHECKOUT_BATCH_DEPTH) { CHECKOUT_BATCH_DIRTY = true; return; }
  // every render replaces or clears the one local readiness deadline.
  armReadyTimer([]);
  Promise.resolve().then(() => bindCountdowns());
  refreshChangeBadges(); // every stage, undo and refusal passes through here
  persistStagePlans(); // phase 2: the kept plans follow the stage
  const button = document.getElementById('checkout-btn');
  const count = document.getElementById('checkout-count');
  if (!button || !count) return;
  const overlay = changesOverlay();
  const panel = overlay.querySelector('#checkout-panel');
  const mobileButton = document.getElementById('mobile-changes-btn');
  const mobileCount = document.getElementById('mobile-changes-count');
  const label = document.getElementById('checkout-label');
  const suffix = document.getElementById('checkout-waiting');
  if (!stagePlansReady()) {
    // with saved changes the button shows
    // "Changes N" from the first frame and answers a click; the window says it is checking until every change is
    // confirmed against the live board, then lists them. Nothing unconfirmed is ever listed or signed.
    const checking = pendingStagePlanCount() + activeChainPlans().length;
    const checkingLabel = checking ? `Checking ${checking} saved change${checking === 1 ? '' : 's'} against the live board` : 'Checking saved changes against the live board';
    button.hidden = false;
    count.hidden = !checking;
    count.textContent = String(checking);
    if (mobileCount) { mobileCount.hidden = !checking; mobileCount.textContent = String(checking); }
    if (label) { label.textContent = 'Changes'; label.hidden = !checking; }
    if (suffix) { suffix.hidden = true; suffix.textContent = ''; }
    button.classList.toggle('is-icon-only', !checking);
    button.classList.toggle('is-checking', !!checking);
    for (const el of [button, mobileButton]) { el?.classList.remove('is-changes-ready'); el?.classList.toggle('is-changes-waiting', !!checking); } // checking is a wait
    button.setAttribute('aria-label', checkingLabel);
    const open = S.CERES_CHECKOUT_OPEN;  // a click during the check always opens; the window says it is checking
    button.setAttribute('aria-expanded', String(open));
    if (mobileButton) {
      mobileButton.disabled = !checking;
      mobileButton.setAttribute('aria-disabled', String(!checking));
      mobileButton.setAttribute('aria-expanded', String(open));
      mobileButton.setAttribute('aria-label', checkingLabel);
      mobileButton.classList.toggle('is-active', !!checking);
    }
    if (!open) {
      overlay.hidden = true;
      panel.replaceChildren();
      return;
    }
    panel.innerHTML = `<div class="ch-checking" role="status"><span class="spinner" aria-hidden="true"></span><span>${checkingLabel}…</span></div>`;
    overlay.hidden = false;
    return;
  }
  button.classList.remove('is-checking'); // the check is done
  if (button.getAttribute('data-tip')?.startsWith('Checking ')) button.removeAttribute('data-tip');
  const model = buildChangesModel(CERES_STAGE, (entry) => stageSetAside(entry) || (entry._worksite_parked_reason ? "blocked" : null));
  // running constructions join the model -- a finished feed as included, a building one as waiting.
  model.constructions = constructionModel();
  model.counts.included += model.constructions.signable.length;
  model.counts.waiting += model.constructions.waiting.length;
  const extra = plannerCounts();
  const plotLabels = new Map((BOARD?.plots || []).map((row) => [row.deed_uid, formatPlotLabel(row.plot || row.deed_uid)]));
  const ready = collectReadyItems({ stage: CERES_STAGE, constructions: model.constructions.items.filter((item) => !FEED_STATE.get(item.deedUid)?.txId), plans: activeChainPlans().map(([, plan]) => plan), plotLabels });
  armReadyTimer(ready.future, () => renderCheckoutSurface());
  const state = changesButtonState(model, extra);
  const wasOpen = S.CERES_CHECKOUT_OPEN && !overlay.hidden; // an open panel keeps its empty state until closed
  const visible = Boolean(state) || wasOpen;
  button.hidden = !visible;
  // (Changes\" from the changes button if there are no changes"): the word shows
  // only with staged changes; otherwise the icon alone, plus a short state text when there is one.
  const hasChanges = activeChainPlans().length > 0 || model.intents.length > 0 || hasStagedWorksiteBuilds() || STAGED_PLOT_LOCKS.size > 0 || model.constructions.signable.length > 0;
  if (label) { label.textContent = 'Changes'; label.hidden = !hasChanges; }
  count.hidden = !state?.count;
  count.textContent = String(state?.count || 0);
  if (mobileCount) { mobileCount.hidden = !state?.count; mobileCount.textContent = String(state?.count || 0); }
  if (suffix) { suffix.hidden = !state?.suffix; suffix.textContent = state?.suffix || ''; }
  // a shown count (e.g. saved plans still being checked) needs room beside the icon, so the square icon-only shape
  // applies only when nothing but the icon is shown.
  button.classList.toggle('is-icon-only', !hasChanges && !state?.suffix && !state?.count);
  const description = changesDescription(model, extra);
  button.classList.toggle('is-ready-alert', ready.items.length > 0);
  // the whole
  // button takes the C-8 state colour, not only the count -- green while something can be signed now, orange while
  // every change still waits (cooldown, building, validation).
  const signableNow = ready.items.length > 0 || model.counts.included + model.counts.orange > 0 || CERES_STAGED_WORKSITES.size + STAGED_PLOT_LOCKS.size > 0;
  const waitingOnly = !signableNow && Boolean(state?.count || model.constructions.items.length);
  for (const el of [button, mobileButton]) {
    el?.classList.toggle('is-changes-ready', signableNow);
    el?.classList.toggle('is-changes-waiting', waitingOnly);
  }
  button.setAttribute('aria-label', `${description}${ready.items.length ? `, ${ready.items.length} ready now` : ''}`);
  button.removeAttribute('data-tip'); button.removeAttribute('data-tip-body'); button.removeAttribute('data-tip-foot');
  if (!ready.items.length) button.setAttribute('data-tip', 'Changes'); // the header shows the icon only; the tooltip keeps the word
  if (ready.items.length) {
    button.setAttribute('data-tip', `${ready.items.length} ${ready.items.length === 1 ? 'change is' : 'changes are'} ready`);
    button.setAttribute('data-tip-body', ready.items.slice(0, 3).map((item) => item.label).join('\n'));
    button.setAttribute('data-tip-foot', 'Open Changes to sign them.');
  }
  let readyStatus = document.getElementById('checkout-ready-status');
  if (!readyStatus) {
    readyStatus = document.createElement('span'); readyStatus.id = 'checkout-ready-status';
    readyStatus.className = 'sr-only'; readyStatus.setAttribute('role', 'status'); readyStatus.setAttribute('aria-live', 'polite');
    button.insertAdjacentElement('afterend', readyStatus);
  }
  const previousReadyCount = Number(readyStatus.dataset.readyCount || 0);
  if (ready.items.length > previousReadyCount) readyStatus.textContent = `${ready.items.length} ${ready.items.length === 1 ? 'change is' : 'changes are'} ready`;
  readyStatus.dataset.readyCount = String(ready.items.length);
  const open = S.CERES_CHECKOUT_OPEN && visible;
  button.setAttribute('aria-expanded', String(open));
  if (mobileButton) {
    mobileButton.disabled = !visible;
    mobileButton.setAttribute('aria-disabled', String(!visible));
    mobileButton.setAttribute('aria-expanded', String(open));
    mobileButton.classList.toggle('is-ready-alert', ready.items.length > 0);
    mobileButton.setAttribute('aria-label', `${description}${ready.items.length ? `, ${ready.items.length} ready now` : ''}`);
    mobileButton.classList.toggle('is-active', visible);
  }
  if (!open) {
    if (!visible) { S.CERES_CHECKOUT_OPEN = false; forgetUndone(); }
    if (!overlay.hidden) { forgetUndone(); clearDroppedPlans(); queueMicrotask(dropInvalidChanges); } // the dropped list is read once, then gone
    if (!overlay.hidden && panel.contains(document.activeElement)) (mobileBoardTier() ? mobileButton : button)?.focus();
    if (!overlay.hidden) holdTooltipsUntilPointerMoves();
    overlay.hidden = true;
    panel.replaceChildren();
    return;
  }
  const scrollTop = panel.querySelector('.hm-body')?.scrollTop || 0;
  const plotsById = new Map((BOARD?.plots || []).map((plot) => [plot.deed_uid, plot]));
  const focusedKey = document.activeElement?.dataset?.checkoutAction === 'toggle' && panel.contains(document.activeElement) ? '.harvest-x' : null;
  if (!activeChainPlans().length && !model.intents.length && !hasStagedWorksiteBuilds() && !STAGED_PLOT_LOCKS.size && !pendingStagePlanCount() && !model.constructions.items.length) {
    forgetUndone();
    const body = plannerDroppedHtml();
    replaceCheckoutPanelHtml(panel, `<header class="hm-headr"><div><h2 id="checkout-title">Changes</h2><p>Nothing staged.</p></div><button type="button" class="harvest-x ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" data-checkout-action="toggle" aria-label="Close">${X_ICON}</button></header>`
      + `<div class="hm-body">${body}${boardReadHtml()}</div><footer class="hm-foot"><span class="hm-note ch-signing-note" role="status" aria-live="polite"></span></footer>`);
  } else {
    replaceCheckoutPanelHtml(panel, stagedPanelHtml(model, plotsById, ready.items));
  }
  if (overlay.hidden) {
    overlay.hidden = false;
    panel.querySelector('.harvest-x')?.focus();
    forgetFeedErrors(); // each opening checks the finished feeds again
    if (model.constructions.items.some((item) => item.kind === 'feed')) queueMicrotask(() => checkFeedPreviews(() => { if (S.CERES_CHECKOUT_OPEN) renderCheckoutSurface(); }));
  }
  else if (focusedKey) panel.querySelector(focusedKey)?.focus();
  const scroller = panel.querySelector('.hm-body');
  if (scroller) scroller.scrollTop = scrollTop;
}

function readyNowHtml(items = []) {
  if (!items.length) return '';
  return `<section class="hm-card ch-ready-now"><h3 class="hm-sec-h">Ready now</h3>${items.map((item) => `<div class="ch-worksite-build-row is-ready-item"><span>${esc(item.label)}</span></div>`).join('')}</section>`;
}

function stagedPanelHtml(model, plotsById, readyItems = []) {
  const review = CERES_PREVIEWS.get(CERES_PORTFOLIO_KEY);
  if (review?.broadcasting) {
    return `<header class="hm-headr"><div><h2 id="checkout-title">Changes</h2><p>Signing run in progress</p></div></header>`
      + `<div class="hm-body ch-signing-only">${signingFocusHtml(review)}</div><footer class="hm-foot ch-signing-footer"><span class="hm-note ch-signing-note" role="status" aria-live="polite">Keep this window open while Keychain handles the requests.</span></footer>`;
  }
  const groups = checkoutGroups(model, plotsById);
  const totalDeltas = [...groups.values()].filter((group) => group.items.length).map((group) => group.delta); // undone-only plots do not count
  const totalDelta = totalDeltas.length && totalDeltas.every(Number.isFinite)
    ? totalDeltas.reduce((sum, delta) => sum + delta, 0) : null;
  // Portfolio-level Base-PP-stage total -- summed separately from `totalDelta` (Total
  // harvest-stage) below; the two are rendered side by side, never combined into one figure.
  const c = model.counts;
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const portfolioNotices = Array.isArray(review?.payload?.notices) && review.payload.notices.length
    ? review.payload.notices
    : (review?.payload?.hazards || []).map((hazard) => ({
      code: hazard.type || 'unknown_notice', kind: hazard.type === 'workers_staged_without_power' ? 'warning' : 'hazard',
      severity: hazard.type === 'workers_staged_without_power' ? 'caution' : 'danger', blocking: hazard.type === 'workers_staged_without_power' ? 'none' : 'confirm',
      message: hazard.message, reason: hazard.message, context: { deed_uid: hazard.deed_uid },
    }));
  const plotNotices = (group) => portfolioNotices.filter((notice) => notice.context?.deed_uid === group.deedUid).map((notice) => {
    const removals = group.items.filter(({ entry }) => entry.action === 'unstake');
    const runi = removals.find(({ entry }) => entry.group_id === 'runi');
    const core = removals.find(({ entry }) => entry.group_id === 'power_core');
    const energized = removals.find(({ entry }) => entry.group_id === 'worker'
      && (entry._previous_asset?.land_abilities || []).some((ability) => (Array.isArray(ability) ? ability[0] : ability) === 'ENERGIZED'));
    const source = notice.code === 'sole_power_source_removed' ? (runi || core) : notice.code === 'energized_source_removed' ? energized : null;
    const sourceName = runi && source === runi ? `Runi${core ? ' (Power Core)' : ''}`
      : source === core && source?.entry._previous_asset?.name === 'Runi' ? 'Runi (Power Core)' : source?.name;
    const named = sourceName && /^This removes/i.test(String(notice.message || ''))
      ? { ...notice, message: `Unstaking ${sourceName} removes${String(notice.message).replace(/^This removes/i, '')}` }
      : notice;
    return named.reason === named.message || source ? { ...named, reason: null } : named;
  });
  // (maintainer: "compact those infos further ... combine all unpowered worker slots together into one message ... it
  // becomes clear that this all happens only AFTER signing"): per plot, one power line and one line for every worker
  // left without power, each saying it follows from signing; plots are named by their normalized label, never a deed id.
  const compactPlotNotices = (group, notices) => {
    const plotName = formatPlotLabel(group.row?.plot || '') || 'this plot';
    const powerLoss = notices.find((notice) => ['sole_power_source_removed', 'energized_source_removed'].includes(notice.code));
    const unpowered = notices.filter((notice) => notice.code === 'worker_unpowered_inactive');
    const out = [];
    notices.forEach((notice) => {
      if (notice.code === 'worker_unpowered_inactive') return;
      if (notice.code === 'workers_staged_without_power' && powerLoss) return; // the power line already says it
      if (notice === powerLoss) {
        const who = String(notice.message || '').match(/^Unstaking (.+?) removes/)?.[1];
        const lead = who ? `After signing, unstaking ${who}` : 'After signing, this change';
        out.push({ ...notice, reason: null, message: notice.code === 'sole_power_source_removed'
          ? `${lead} leaves ${plotName} without power: every worker stops producing until the plot has a Power Core, Runi or Energized worker again.`
          : `${lead} removes ${plotName}'s Energized power: any slot no longer powered stops producing until a Power Core or another Energized worker is staked.` });
        return;
      }
      if (notice.code === 'workers_staged_without_power') {
        out.push({ ...notice, reason: null, message: `After signing, ${plotName} has workers but no worker-slot power. A Power Core or Energized worker powers all five slots; a Runi powers four.` });
        return;
      }
      out.push(notice);
    });
    if (unpowered.length) {
      const names = unpowered.map((notice) => {
        const label = String(notice.context?.worker || 'A worker').replace(/ to plot \S+/, '').replace(/ (in )?slot \d+$/, '');
        return notice.context?.slot ? `${label} (slot ${notice.context.slot})` : label;
      });
      const several = names.length > 1;
      const list = several ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}` : names[0];
      out.push({ ...unpowered[0], reason: null, message: `After signing, ${list} ${several ? 'sit in unpowered slots and produce' : 'sits in an unpowered slot and produces'} nothing until the plot has power again. ${several ? 'They stay' : 'It stays'} staked.` });
    }
    return out;
  };
  const groupHtml = [...groups.values()].map((group) => {
    const plotDelta = stageDeltaText(group.delta, group.impact?.reason);
    const plotDeltaTip = stageDeltaTip(group.delta, group.impact?.reason);
    // no "provisional" word -- a plot with moves in AND out says so in the Total's tooltip instead.
    const bothWays = deedHasBothDirectionsStaged(group.deedUid);
    const plotTotalTip = plotDeltaTip || (bothWays ? tipAttrs({ title: 'Estimate', body: 'Cards move in and out of this plot at once; the exact figure comes when the batch is priced for signing.', peer: 'checkout_metrics' }) : '');
    const changeCount = group.items.length + (group.worksiteBuild ? 1 : 0) + (group.plotLock ? 1 : 0) + (group.construction ? 1 : 0);
    const groupSummary = group.items.length
      ? `${plural(changeCount, 'change')} · Total <span class="ch-pp${ppClass(group.delta)}"${plotTotalTip}>${esc(plotDelta)} PP</span>`
      : plural(changeCount, 'change');
    const notices = compactPlotNotices(group, plotNotices(group));
    // (maintainer): "N changes · Total … PP" sits in the card's top-right corner, on the badge's line, aligned across
    // cards and fixed while the plot's details load.
    return `<div class="hm-card ch-plot"><div class="ch-plot-head"><span class="ch-plot-name">${plotBadgeHtml(group.row, group.deedUid)}</span><p class="hm-dim ch-plot-summary">${groupSummary}</p></div>`
      + `<div class="ch-plot-layout">${group.items.length || group.hasUndone || group.plotLock ? (mobileBoardTier() ? plotListHtml(group, plotsById) : plotStripHtml(group, plotsById)) : ''}`
      + `<div class="ch-plot-aside">${serverNoticeHtml(notices)}</div></div>`
      + (mobileBoardTier() ? '' : invalidPlanRowsHtml(group, plotsById))
      + worksiteBuildHtml(group.deedUid, group.worksiteBuild) + constructionRowHtml(group.construction) + '</div>';
  }).join('');
  const portfolioDelta = stageDeltaText(totalDelta);
  const portfolioDeltaTip = stageDeltaTip(totalDelta, 'one or more plot stage impacts are unavailable');
  const inFlightOnly = CERES_STAGE.length > 0 && CERES_STAGE.every((entry) => entry?.txId && (entry?.broadcast_at || entry?.queued_at));
  const inFlightWorksite = [...CERES_STAGED_WORKSITES.values()].some((entry) => entry?.txId && (entry?.broadcast_at || entry?.queued_at));
  const nothingToUndo = !CERES_STAGE.length && !CERES_STAGED_WORKSITES.size && !STAGED_PLOT_LOCKS.size && !pendingStagePlanCount(); // construction rows are not undone
  const undoAllDisabled = CERES_BROADCAST_ACTIVE > 0 || inFlightOnly || inFlightWorksite || CERES_WORKSITE_SIGNING || nothingToUndo;
  const undoAllTip = CERES_BROADCAST_ACTIVE > 0 || CERES_WORKSITE_SIGNING
    ? tipAttrs({ title: FINISH_TRANSACTION_FIRST, peer: 'checkout_blockers' })
    : inFlightOnly || inFlightWorksite ? tipAttrs({ title: noticeMessage(catalogNotice('verification_in_flight_refusal')), peer: 'checkout_blockers' }) : '';
  // on the phone it is a
  // compact button in the header; the footer keeps only the wallet and Sign.
  const phoneChanges = mobileBoardTier();
  const undoAllButton = `<button type="button" class="hm-btn is-ghost checkout-undo-all ceres-btn ceres-btn--secondary" data-control-density="${phoneChanges ? 'compact' : 'comfortable'}" data-checkout-action="undo-all"${undoAllDisabled ? ' disabled' : ''}${undoAllTip}>Undo all</button>`;
  const unplacedNoticesHtml = serverNoticeHtml(portfolioNotices.filter((notice) => !groups.has(notice.context?.deed_uid)).map((notice) => notice.reason === notice.message ? { ...notice, reason: null } : notice));
  const signable = checkoutSignableEntries();
  const readyLocks = [...STAGED_PLOT_LOCKS.values()].filter(entry => !entry.txId && !entry.blockedReason);
  const inFlightEntries = checkoutBlockedInFlightEntries();
  const inFlightNotice = inFlightEntries.length ? noticeMessage(catalogNotice('verification_in_flight_refusal')) : '';
  // a staged worksite build with no card move alongside it is still signable on its
  // own (the checkout's own signing order decides when its op can actually be sent).
  const stagedBuilds = hasStagedWorksiteBuilds();
  const readyFeeds = model.constructions?.signable.length || 0;
  const readyCombines = readyPlanCombines().length;
  const nothingSignable = !readyCombines && signable.length === 0 && !stagedBuilds && readyLocks.length === 0 && !readyFeeds;
  const onlyStandalone = signable.length === 0 && (stagedBuilds || readyLocks.length > 0 || readyFeeds > 0 || readyCombines > 0);
  const signedLocks = [...STAGED_PLOT_LOCKS.values()].filter(entry => entry.txId).length;
  const uncheckedWorksites = pendingWorksitePlanCount();
  const duplicateRef = duplicateUnsignedStakeRef();
  const prepareDisabled = nothingSignable || inFlightEntries.length > 0 || CERES_WORKSITE_SIGNING || !!duplicateRef; // K6: a refusal is set aside, not a veto
  // Gate document #125: one line per blocker, never one joined paragraph.
  const prepareBlockers = [duplicateRef ? 'The same card or item is staged twice. Move or undo one change before signing.' : '', nothingSignable ? (signedLocks ? 'The deed lock signature is awaiting the live deed state.' : 'Nothing can be signed yet: every change is waiting, no longer valid, or was refused.') : '', inFlightNotice, CERES_WORKSITE_SIGNING ? 'Signing is in progress.' : ''].filter(Boolean);
  const prepareTip = prepareDisabled ? tipAttrs({ title: 'Not ready to sign', ...(prepareBlockers.length ? { items: prepareBlockers.map((effect) => ({ name: '', effect })) } : { body: prepareBlockers.join(' ') }), peer: 'checkout_blockers' }) : '';
  // the preview is prepared automatically
  // whenever the staged batch differs from the one last previewed; the same server gates still apply.
  const stageSig = JSON.stringify({
    moves: signable.map(stageMovePayload),
    stagedWorksites: [...CERES_STAGED_WORKSITES.entries()].map(([deedUid, build]) => ({ deed_uid: deedUid, target: build.target })),
  });
  const reviewSig = review?.requestedStageSignature || null;
  if (!prepareDisabled && !onlyStandalone && CERES_BROADCAST_ACTIVE === 0 && !review?.broadcasting && !review?.freshnessCheck
      && (!review || (!review.loading && reviewSig !== stageSig))) {
    queueMicrotask(() => { if (S.CERES_CHECKOUT_OPEN && (checkoutSignableEntries().length || [...CERES_STAGED_WORKSITES.values()].some((build) => build.firstClear))) requestOperationPreview({ scope: 'portfolio' }); });
  }
  const keychainReady = walletReady();
  const mobileKeychainMissing = mobileBoardTier() && !keychainReady;
  // with no card move staged, there is no batch preview to arm the usual Sign
  // button from -- each staged build gets its own preview later, at the moment the
  // checkout's signing order actually reaches it (confirmCheckoutWithWorksites).
  const signButton = prepareDisabled
    ? `<button type="button" class="hm-btn is-primary ceres-btn ceres-btn--primary" data-control-density="comfortable" disabled${prepareTip}>${CHANGES_ICON} ${walletSignLabelHtml()}</button>`
    : onlyStandalone
    ? `<button type="button" class="hm-btn is-primary ceres-btn ceres-btn--primary" data-control-density="comfortable" data-control-case="commit" data-checkout-action="sign-worksites"${!keychainReady || (readyLocks.length > 0 && !stagedBuilds && !readyFeeds && !walletCanSignTransaction(BOARD?.player)) ? ' disabled' : ''}>${CHANGES_ICON} ${walletSignLabelHtml()}</button>`
    : operationSignButtonHtml({ scope: 'portfolio' }, review, { surface: 'changes', iconHtml: CHANGES_ICON });
  const worksiteValues = [...CERES_STAGED_WORKSITES.values()];
  const worksiteWaiting = worksiteValues.filter((build) => (build.parkedReason || build.blockedReason) && !(build.txId && (build.broadcast_at || build.queued_at))).length;
  const waiting = c.waiting + worksiteWaiting;
  const notSigned = [waiting ? `${plural(waiting, 'change')} waiting` : '', c.invalid ? `${c.invalid} no longer valid` : '', c.refused ? `${c.refused} refused` : ''].filter(Boolean).join(', ');
  const changeCount = activeChainPlans().length + model.intents.length + worksiteValues.length + STAGED_PLOT_LOCKS.size + pendingStagePlanCount() + (model.constructions?.items.length || 0);
  const summary = totalDelta !== null ? `Total <span class="ch-pp${ppClass(totalDelta)}"${portfolioDeltaTip}>${esc(portfolioDelta)} PP</span>` : '';
  const chunks = review?.payload?.chunks || [];
  const worksiteSignatures = worksiteValues.filter((build) => !build.firstClear && !build.txId && !build.parkedReason && !build.blockedReason).length;
  const postingSignatures = chunks.length + worksiteSignatures + readyFeeds + readyCombines;
  const signatureCount = postingSignatures + readyLocks.length;
  const signatureParts = [postingSignatures ? `${postingSignatures} Posting (${[readyCombines ? 'combines' : '', chunks.length ? 'card changes' : '', worksiteSignatures ? 'worksite builds' : '', readyFeeds ? 'feeding workers' : ''].filter(Boolean).join(', ')})` : '', readyLocks.length ? `${readyLocks.length} Active (deed ${readyLocks.length === 1 ? readyLocks[0].action : 'locks/unlocks'})` : ''].filter(Boolean);
  const onlyUpgradePlans = !readyCombines && activeChainPlans().length > 0 && !model.intents.length && !worksiteValues.length && !STAGED_PLOT_LOCKS.size && !model.constructions?.items.length;
  const signingSummary = onlyUpgradePlans ? `${plural(activeChainPlans().length, 'upgrade plan')} · no unstake to sign` : !review?.payload && !onlyStandalone
    ? `${plural(changeCount, 'change')} · preparing signing details…`
    : `${plural(changeCount, 'change')} · ${plural(signatureCount, 'signature')}${signatureParts.length ? `: ${signatureParts.join(', ')}` : ''}`;
  // (1): the reason a disabled Sign is disabled sits next to it, in the footer note.
  const note = onlyUpgradePlans ? 'The upgrade plan has no unstakes left to sign.' : mobileKeychainMissing ? 'Wallet not connected.'
    : uncheckedWorksites ? 'Still checking your saved plan against the live board.'
    : prepareDisabled ? prepareBlockers[0]
    : onlyStandalone ? (!keychainReady ? 'Wallet not connected.' : readyLocks.length && !walletCanSignTransaction(BOARD?.player) ? 'Deed locks need Hive Keychain (Active key).' : '')
    : review?.loading || !review ? 'Preparing the transaction…'
    : review.pendingWalletBlock ? review.pendingWalletBlock.message
    : review.error ? `Cannot sign yet: ${review.error}`
    : review.payload?.parked_worksites?.length && !chunks.length ? 'The worksite is parked until its workers and power are ready.'
    : !keychainReady ? 'Wallet not connected.'
    : readyLocks.length && !walletCanSignTransaction(BOARD?.player) ? 'Deed locks need Hive Keychain (Active key).'
    : notSigned ? `${notSigned}, not in this signature` : '';
  const reviewHtml = prepareDisabled || onlyStandalone ? '' : renderOperationReview({ scope: 'portfolio' }, review, { compact: true, footerSign: true, checkoutSummary: true });
  const lockSigningHtml = readyLocks.length ? (() => {
    const readyLockActions = new Set(readyLocks.map((entry) => entry.action));
    const lockGuidance = readyLockActions.has('lock') && readyLockActions.has('unlock')
      ? 'Deed lock changes need Hive Keychain (Active key).'
      : readyLockActions.has('unlock') ? 'Unlocking needs Hive Keychain (Active key).' : 'Locking needs Hive Keychain (Active key).';
    return `<p class="hm-dim">${walletCanSignTransaction(BOARD?.player) ? 'Keychain shows the exact operation before approval.' : lockGuidance}</p>`;
  })() : '';
  const signingCard = `<div class="hm-card ch-signing"><h3 class="hm-sec-h">Signing</h3><div class="ch-signing-status" role="status" aria-live="polite"><p class="ch-signing-summary">${esc(signingSummary)}</p>${unplacedNoticesHtml}${reviewHtml}${lockSigningHtml}</div></div>`;
  const closeButton = `<button type="button" class="harvest-x ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" data-checkout-action="toggle" aria-label="Close">${X_ICON}</button>`;
  return `<header class="hm-headr"><div><h2 id="checkout-title">Changes</h2><p>${summary}</p></div>${phoneChanges ? `<div class="ch-head-actions">${undoAllButton}${closeButton}</div>` : closeButton}</header>`
    + `<div class="hm-body">${readyNowHtml(readyItems)}${upgradePlansHtml()}${groupHtml}${plannerDroppedHtml()}${signingCard}${boardReadHtml()}</div>`
    // the wallet chooser (mobileKeychainGuidanceHtml -> walletControlsHtml) moves out of
    // its own row above Signing and into the footer, directly left of the Sign button.
    + `<footer class="hm-foot"><span class="hm-note ch-signing-note" role="status" aria-live="polite">${esc(note)}</span><span class="hm-btns">${phoneChanges ? '' : undoAllButton}<span class="wallet-sign-pair">${mobileKeychainGuidanceHtml(keychainReady)}${signButton}</span></span></footer>`;
}

export async function undoAllCheckoutStage() {
  const worksiteCount = CERES_STAGED_WORKSITES.size + pendingStagePlanCount(); // counted into the confirmation and cleared with the rest
  const unsignedLocks = [...STAGED_PLOT_LOCKS.values()].filter(entry => !entry.txId);
  const inFlightWorksite = [...CERES_STAGED_WORKSITES.values()].some((entry) => entry?.txId && (entry?.broadcast_at || entry?.queued_at));
  if ((!CERES_STAGE.length && !worksiteCount && !unsignedLocks.length) || CERES_BROADCAST_ACTIVE > 0 || CERES_WORKSITE_SIGNING || inFlightWorksite) return false;
  const entries = CERES_STAGE.filter((entry) => !(entry?.txId && (entry?.broadcast_at || entry?.queued_at)));
  if (!entries.length && !worksiteCount && !unsignedLocks.length) {
    stageToast(noticeMessage(catalogNotice('verification_in_flight_refusal')), true);
    return false;
  }
  const count = entries.length + worksiteCount + unsignedLocks.length;
  if (!confirm(`Discard all ${count} staged moves? This will undo every change in this checkout. They are also removed from your saved plan.`)) return false;
  if (!await releasePendingStageBroadcasts()) {
    stageToast('A previous wallet request is still being checked. Nothing was undone. Try again after that check finishes.', true);
    return false;
  }
  if (!discardPendingStagePlans()) return false;
  CERES_STAGED_WORKSITES.clear();
  clearPlotLockPlans();
  // LOCAL INVARIANT: every optimistic slot mutation is reversed through the single-entry restore path before stage state is cleared.
  for (let index = entries.length - 1; index >= 0; index -= 1) restoreStagedMove(entries[index]);
  entries.forEach((entry) => {
    leaveChainUnstake(entry);
    const stageIndex = CERES_STAGE.indexOf(entry);
    if (stageIndex !== -1) CERES_STAGE.splice(stageIndex, 1);
  });
  [...new Set(entries.map((entry) => entry.deed_uid))].forEach(refreshStageImpactForRow);
  CERES_PREVIEWS.clear();
  const pickerDeed = S.CERES_PICKER?.deedUid;
  S.CERES_PICKER = null;
  // phase 4: redraw only the plots the undo touched (and an open picker's), never every board plot.
  new Set([...entries.map((entry) => entry.deed_uid), ...entries.map((entry) => entry._source_deed_uid), ...unsignedLocks.map((entry) => entry.deed_uid), pickerDeed].filter(Boolean)).forEach((deedUid) => updateMountedRow(deedUid));
  S.CERES_CHECKOUT_OPEN = false; // (Undo all\""): nothing is left to show
  renderCheckoutSurface();
  stageToast(`Discarded ${count} staged move${count === 1 ? '' : 's'}.`);
  return true;
}

// (maintainer): "it will be removed automatically from the changes queue when the window is closed". One at a time
// through the normal removal (it refuses anything already signed and re-prices the plot).
async function dropInvalidChanges() {
  for (const entry of CERES_STAGE.filter((item) => item._plan_invalid_reason && !item.txId)) await removeCheckoutStage(entry);
}
export async function removeCheckoutStage(entry) {
  if (entry?.txId && (entry?.broadcast_at || entry?.queued_at)) {
    stageToast(noticeMessage(catalogNotice('verification_in_flight_refusal')), true);
    return;
  }
  const linked = ['unstake', 'replace'].includes(entry.action) && ['totem', 'title', 'power_core'].includes(entry.group_id)
    ? CERES_STAGE.filter((other) => other !== entry && other.action !== 'unstake' && other.group_id === entry.group_id
      && other.asset_ref === (entry.action === 'replace' ? entry.evict_asset_ref : entry.asset_ref) && other._source_deed_uid === entry.deed_uid) : [];
  if (linked.some((other) => other.txId)) {
    stageToast('This asset move is already signed. Wait for its result.', true); return;
  }
  const deedUid = entry.deed_uid;
  if (CERES_REMOVAL_PENDING.has(deedUid)) {
    stageToast('A removal price update is already in progress for this plot. Try again when it finishes.', true);
    return;
  }
  CERES_REMOVAL_PENDING.add(deedUid);
  try {
    if (CERES_STAGE.indexOf(entry) === -1) return;
    let remainingImpact = null;
    const remainingRefs = stagedRemovalRefs(deedUid, entry);
    const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
    const boardContext = removalBoardContext(row);
    if (entry.action === 'unstake' && remainingRefs.length) {
      try {
        remainingImpact = await removalImpact(row, remainingRefs);
      } catch (error) {
        stageToast(`Could not reprice the remaining removals safely: ${error}`, true);
        return;
      }
    }
    if (remainingImpact && !currentRemovalRow(deedUid, boardContext)) {
      stageToast('The board or player changed while repricing. Nothing was removed; try again.', true);
      return;
    }
    const currentIndex = CERES_STAGE.indexOf(entry);
    if (currentIndex === -1 || !sameRefSet(stagedRemovalRefs(deedUid, entry), remainingRefs)) {
      stageToast('The staged removals changed while repricing. Nothing was removed; try again.', true);
      return;
    }
    for (const other of linked) {
      const linkedIndex = CERES_STAGE.indexOf(other);
      if (linkedIndex === -1) continue;
      restoreStagedMove(other);
      CERES_STAGE.splice(linkedIndex, 1);
      CERES_PREVIEWS.delete(other.deed_uid);
      refreshStageImpactForRow(other.deed_uid);
      updateMountedRow(other.deed_uid);
    }
    leaveChainUnstake(entry);
    restoreStagedMove(entry);
    CERES_STAGE.splice(CERES_STAGE.indexOf(entry), 1);
    if (remainingImpact) applyRemovalImpactToStage(deedUid, remainingImpact);
    else refreshStageImpactForRow(deedUid);
    CERES_PREVIEWS.delete(deedUid);
    CERES_PREVIEWS.delete(CERES_PORTFOLIO_KEY);
    updateMountedRow(deedUid);
    if (entry._source_deed_uid) updateMountedRow(entry._source_deed_uid); // the source tile stops showing the move
    renderCheckoutSurface();
    if (S.CERES_PICKER) fetchPickerPage(true);
  } finally {
    CERES_REMOVAL_PENDING.delete(deedUid);
  }
}

// Staged incoming moves whose asset the SERVER already marked not-ready
// (candidates.py::cooldown_state -> `ready: false`). A pure unstake carries no
// `cooldown` (see stageForSlot's comment) and is never blocked by this. This is the
// single source the checkout panel and the Prepare-transaction gate both read
// neither re-derives readiness, both just filter on the field the server returned.
export function checkoutBlockedCooldownEntries() {
  return CERES_STAGE.filter((entry) => entry.action !== 'unstake' && entry.cooldown && entry.cooldown.ready === false);
}

window.addEventListener('ceres:wallet-changed', () => { if (S.CERES_CHECKOUT_OPEN) renderCheckoutSurface(); });
