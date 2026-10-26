import { activeChainInputs, chainPickerVisible, chainReturn, chainPlacementBlocked, deriveChain } from './combine-chain.js';
import { chainPlans, pendingIncomingRefElsewhere } from './stage-plans.js';
import { upgradeActionHtml } from './combine-chain-ui.js';
import { abilityRowText, abilityTipModel, bloodlineOutranked, foilBadgeHtml, workerSubType } from './badges.js';
import { CB_ABILITY_LABELS, CB_ANY_ABILITY, cbAbilityCodes, cbList } from './card-browser-model.js';
import { BOARD, catalogNotice, noticeMessage } from './board.js';
import { tokenIcon, tokenLabel } from './harvest-state.js';
import { candidateCombineInfo, combineTipModel } from './combine.js';
import { editorCardImageHtml, editorSlotLabel } from './editor.js';
import { cardFoilValue, editionDisplayLabel, esc, formatPlotLabel, humanizeInstant, num, terrainValue } from './format.js';
import { CHEVRON_CLOSE_ICON, FILL_TRAY_ICON, abilityIconHtml, cardSplinters, cardSplinterTipHtml, rarityGemHtml, setIconOnlyHtml, splinterIconHtml, titleIconHtml, titleRarityGem, tooltipIconPath, totemIconHtml } from './icons.js';
import { terrainAffinityHtml } from './terrainAffinity.js';
import { countdownText } from './countdown.js';
import { stageMovePayload, stagedReleasedItemRefs, stagedEntriesForRow, stagedIncomingAssetRefs, stagedWorksiteBuild } from './staging.js';
import { plotBadgeHtml } from './checkout.js';
import { ASSET_BASE, S } from './state.js';
import { tipAttrs, tipIcon } from './tooltip.js';
import { combineEstimates, isValidEstimate, normalizeEstimate } from './estimates.js';

export { combineEstimates, isValidEstimate, normalizeEstimate };

export const WORKER_UPGRADE_OPTIONS = new Map();
export function workerUpgradeKey(row, worker) {
  return `${row?.deed_uid || ''}:${worker?.asset_ref || ''}`;
}

export function workerUpgradeOptionsFor(row, worker) {
  return WORKER_UPGRADE_OPTIONS.get(workerUpgradeKey(row, worker)) || null;
}

export function workerUpgradeIsAvailable(worker, options) {
  if (!worker || !options || options.eligible !== true || Number(options.combinable_count) < 1) return false;
  const bcx = Number(worker.bcx);
  const bcxMax = worker.bcx_max === null || worker.bcx_max === undefined ? null : Number(worker.bcx_max);
  return !(Number.isFinite(bcx) && Number.isFinite(bcxMax) && bcx >= bcxMax);
}

function appendStagedPlan(params, deedUid) {
  const entries = typeof stagedEntriesForRow === 'function'
    ? stagedEntriesForRow(deedUid).filter((entry) => !entry.txId && !entry._synthetic && !entry._asset?.planned)
    : [];
  if (entries.length) params.set('staged_moves', JSON.stringify(entries.map(stageMovePayload)));
  const worksite = typeof stagedWorksiteBuild === 'function' ? stagedWorksiteBuild(deedUid) : null;
  if (worksite?.target) params.set('staged_worksite', String(worksite.target));
}

function workerUpgradeCopyCount(row, candidate) {
  if (!candidate) return 0;
  const identity = [candidate.card_detail_id, candidate.edition, cardFoilValue(candidate)]
    .map((value) => String(value ?? '')).join('|');
  let count = 0;
  for (const worker of row?.workers || []) {
    const options = workerUpgradeOptionsFor(row, worker);
    if (!options || !workerUpgradeIsAvailable(worker, options)) continue;
    const copies = (options.other_copies || []).filter((copy) => {
      const copyIdentity = [copy.card_detail_id, copy.edition, cardFoilValue(copy)]
        .map((value) => String(value ?? '')).join('|');
      return copyIdentity === identity && copy.eligible === true;
    });
    count = Math.max(count, copies.length);
  }
  return count;
}

export function pickerEditionOptionsHtml(picker) {
  return '<option value="">Any edition</option>' + (picker.availableEditions || [])
    .map((id) => `<option value="${id}"${String(picker.filters.edition) === String(id) ? ' selected' : ''}>${esc(editionDisplayLabel(id))}</option>`).join('');
}

export function candidateQuery(picker, offset) {
  const workerLike = picker.groupId === 'worker' || picker.groupId === 'runi';
  // Totem/Title are ITEMS (`stake.items[]`), a different asset class from worker
  // cards -- they carry no Boostable/Terrain/Plot change, so none of the
  // Worker filter bar's fields (splinter/edition/min_pp/bucket/bloodline) or the
  // old Worker-shaped presets apply. Item plan: Phase 4, plan §8.
  const itemLike = picker.groupId === 'totem' || picker.groupId === 'title';
  const params = new URLSearchParams({
    group_id: picker.groupId,
    limit: '25',
    offset: String(offset),
    sort: workerLike ? (picker.sort || 'construction_pp') : itemLike ? (picker.sort || 'boost') : 'net_gain',
    slot: String(picker.slotIndex),
  });
  stagedReleasedItemRefs(picker.groupId, picker.deedUid).forEach((ref) => params.append('released_asset_ref', ref));
  // reserve the inputs even before the first signature; only then expose the result.
  if (picker.groupId === 'worker') {
    const summaries = [];
    for (const [id, plan] of Object.entries(chainPlans())) {
      if (['cancelled', 'done'].includes(plan.state)) continue;
      activeChainInputs(plan).forEach(input => params.append('reserved_asset_ref', input.asset_ref || input.uid));
      if (chainPickerVisible(plan) && !stagedIncomingAssetRefs().has(`planned:${id}`)) summaries.push({ id, card: plan.card, expected: plan.expected, ready_at: deriveChain(plan).ready_at });
    }
    if (summaries.length) params.set('planned_cards', JSON.stringify(summaries));
  }
  const filters = picker.filters;
  if (filters.q) params.set('q', filters.q);
  if (filters.rarity) params.set('rarity', filters.rarity);
  if (itemLike) {
    // No splinter/edition/min_pp/bucket/preset: those describe worker cards only.
    const reserved = typeof stagedIncomingAssetRefs === 'function' ? stagedIncomingAssetRefs() : new Set();
    reserved.forEach((assetRef) => params.append('reserved_asset_ref', assetRef));
    appendStagedPlan(params, picker.deedUid);
    return `/api/plots/${encodeURIComponent(picker.deedUid)}/candidates?${params}`;
  }
  if (filters.splinter) params.set('splinter', filters.splinter);
  if (filters.edition) params.set('edition', filters.edition);
  if (filters.min_pp) params.set('min_pp', filters.min_pp);
  if (filters.max_pp) params.set('max_pp', filters.max_pp);
  if (workerLike && filters.land_only) params.set('land_only', 'true');
  if (workerLike) cbList(filters.ability).forEach((code) => params.append('ability', code));
  params.set('bucket', filters.bucket || 'all');
  if (workerLike && filters.ready_now === false) params.set('include_cooling', 'true');
  if (workerLike) params.set('include_rented_in', 'true');
  if (workerLike && filters.include_on_land) params.set('include_on_land', 'true'); // staked-elsewhere cards hidden by default
  if (picker.groupId === 'worker' && filters.include_terrain_penalised) params.set('include_terrain_penalised', 'true');
  if (picker.groupId === 'worker' && filters.bloodline_pairs && picker.bloodlineSubTypes?.length) { // no holder, no filter
    (picker.bloodlineSubTypes || ['__no_matching_bloodline__']).forEach((subType) => params.append('bloodline_sub_type', subType));
  }
  if (!workerLike && picker.preset === 'ability') params.set('edition', '19');
  if (!workerLike && (picker.preset === 'cooldown_free' || picker.preset === 'everything')) params.set('include_cooling', 'true');
  if (!workerLike && picker.preset === 'everything') params.set('include_rented_in', 'true');
  // Worker/Runi pickers no longer show a preset bar (item 2 of the redesign
  // see plan §7): the Boostable / Name chips set `sort` directly. Do
  // NOT also send a `preset` param here -- the server's `preset` handling
  // (ceres/routes_candidates.py -> ceres/candidates.py:688) unconditionally
  // overrides `sort` with the preset's own fixed ordering whenever `preset`
  // is truthy, which silently clobbered the Name chip's `sort=name` back to
  // `sort=construction_pp` and made both chips return identical order.
  if (!workerLike) {
    const legacyPreset = ({ base_pp: 'highest_pp', cooldown_free: 'cooldown_free', everything: 'everything' })[picker.preset] || 'net_gain_default';
    params.set('preset', legacyPreset);
  }
  const reserved = typeof stagedIncomingAssetRefs === 'function' ? stagedIncomingAssetRefs() : new Set();
  reserved.forEach((assetRef) => params.append('reserved_asset_ref', assetRef));
  appendStagedPlan(params, picker.deedUid);
  return `/api/plots/${encodeURIComponent(picker.deedUid)}/candidates?${params}`;
}

function candidateEvaluation(candidate, key, legacyKey) {
  const evaluation = candidate?.[key];
  if (evaluation && typeof evaluation === 'object') {
    const status = ['point', 'range', 'unknown', 'not_applicable'].includes(evaluation.status) ? evaluation.status : 'unknown';
    const value = evaluation.value === null || evaluation.value === undefined ? null : Number(evaluation.value);
    const lowValue = evaluation.low ?? evaluation.min;
    const highValue = evaluation.high ?? evaluation.max;
    const low = lowValue === null || lowValue === undefined ? null : Number(lowValue);
    const high = highValue === null || highValue === undefined ? null : Number(highValue);
    return {
      status,
      value: Number.isFinite(value) ? value : null,
      low: Number.isFinite(low) ? low : null,
      high: Number.isFinite(high) ? high : null,
      reason: evaluation.reason || null,
    };
  }
  const legacy = legacyKey ? candidate?.[legacyKey] : null;
  const value = legacy === null || legacy === undefined ? null : Number(legacy);
  return Number.isFinite(value)
    ? { status: 'point', value, low: value, high: value, reason: null }
    : { status: 'unknown', value: null, low: null, high: null, reason: 'not_calculated' };
}

export function candidateDelta(candidate, picker) {
  const impact = candidateEvaluation(candidate, 'plot_impact', 'net_gain');
  return impact.status === 'point' ? impact.value : null;
}

export function signedPp(value) {
  if (!Number.isFinite(value)) return '—';
  if (value > 0) return `+${num(value)}`;
  if (value < 0) return `−${num(Math.abs(value))}`;
  return '0';
}

function candidateReason(reason) {
  return ({
    not_calculated: 'Not calculated',
    item_scoring_not_applicable: 'Not applicable to this asset',
  })[reason] || (reason ? String(reason).replaceAll('_', ' ') : 'Not calculated');
}

// Compact badge text for one Base-PP-stage estimate -- the checkout's per-move and
// per-plot rows use this alongside the existing signed Total-PP figure.
export function estimateBadge(estimate) {
  if (estimate.status === 'point') return signedPp(estimate.value);
  if (estimate.status === 'range') return `${signedPp(estimate.low)}…${signedPp(estimate.high)}`;
  if (estimate.status === 'not_applicable') return 'n/a';
  return '—';
}

function evaluationText(evaluation, label, signed = false) {
  if (evaluation.status === 'point' && evaluation.value !== null) {
    const value = signed ? signedPp(evaluation.value) : num(evaluation.value);
    return { text: `${label} ${value} PP`, value, reason: '' };
  }
  if (evaluation.status === 'range' && evaluation.low !== null && evaluation.high !== null) {
    return { text: `${label} approximately ${num(evaluation.low)} to ${num(evaluation.high)} PP`, value: `≈ ${num(evaluation.low)}–${num(evaluation.high)}`, reason: candidateReason(evaluation.reason) };
  }
  const reason = candidateReason(evaluation.reason);
  return { text: `${label} not calculated: ${reason}`, value: '—', reason };
}

// Breakdown keys stay unchanged because staging and server ledgers consume them.
function renderCandidateBreakdown(candidate) {
  const breakdown = candidate.net_gain_breakdown || {};
  const labels = {
    own_harvest_delta: 'Incoming worker change',
    evicted_loss: 'Current worker removed',
    plot_residual_delta: 'Other workers on this plot',
    donor_plot_delta: 'Source plot after move',
    precondition_delta: 'Power-state change',
  };
  const parts = [];
  Object.entries(labels).forEach(([key, label]) => {
    const value = breakdown[key] === null || breakdown[key] === undefined ? null : Number(breakdown[key]);
    if (Number.isFinite(value)) parts.push(`${label} ${signedPp(value)}`);
  });
  return parts.join(' · ');
}

const PICKER_GROUP_META = {
  worker: ['🃏', 'Worker cards'], runi: ['🃏', 'Runi cards'], totem: ['🔮', 'Totems'],
  title: ['🏷️', 'Titles'], power_core: ['⚡', 'Power Cores'],
};
const PICKER_VIEW_ICON = '<svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true"><path class="f" d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/><path class="d" d="M10 6.5h4M6.5 10v4M17.5 10v4M10 17.5h4"/><path class="a h" d="M12 2v20"/></svg>';

export function candidateStableKey(candidate, index = 0) {
  const identity = candidate?.asset_ref || candidate?.uid || candidate?.card_uid
    || [candidate?.card_detail_id, candidate?.edition, candidate?.name].filter((value) => value !== undefined && value !== null).join(':');
  return String(identity || `candidate-${index}`);
}

function candidateDomId(key) {
  return `candidate-detail-${encodeURIComponent(String(key)).replace(/%/g, '-')}`;
}

export function plannedPlacementReason(candidate, picker = S.CERES_PICKER) {
  if (!candidate?.planned || !picker) return '';
  return Object.values(chainPlans()).filter(plan => !['cancelled', 'done'].includes(plan.state))
    .map(plan => chainPlacementBlocked(plan, picker.deedUid, picker.slotIndex)).find(Boolean) || '';
}

function plannedCandidateHtml(candidate, picker) {
  const back = chainReturn(chainPlans()[candidate.chain_id], picker.deedUid, picker.slotIndex);
  const capped = candidate.base_pp_capped ? ` Over the maximum: this card keeps CC ${num(candidate.bcx)}, but its base PP stays at the level-${candidate.level} maximum of ${num(candidate.base_pp)}. Combining more copies adds no production.` : '';
  // the plot and slot the upgraded worker came from.
  const plan = chainPlans()[candidate.chain_id];
  const from = activeChainInputs(plan || {}).find((input) => input.uid === plan?.origin?.asset_uid && input.source)?.source;
  const fromRow = from ? (BOARD?.plots || []).find((plot) => plot.deed_uid === from.deed_uid) : null;
  const fromHtml = fromRow ? `<span class="planned-origin"${tipAttrs({ title: 'Original plot', body: `${candidate.name} was staked on plot ${formatPlotLabel(fromRow.plot)}, slot ${from.slot_index + 1}.` })}>${plotBadgeHtml(fromRow, fromRow.deed_uid, { static: true })}<span>slot ${from.slot_index + 1}</span></span>` : '';
  return `<span class="planned-card-copy"${tipAttrs({ title: 'Planned card · not yet combined', body: 'Place this expected card on any plot. Its stake can be signed after the combine.', foot: `CC ${num(candidate.bcx)}${candidate.base_pp_capped ? ` (max ${num(candidate.bcx_max)})` : ''} · Level ${candidate.level} · Base PP ${num(candidate.base_pp)}.${capped}` })}><span class="candidate-tag is-planned">Planned</span><span>Not yet combined</span>${fromHtml}${back ? `<span class="planned-return">${esc(back.picker)}</span>` : ''}</span>`;
}

function plannedStakeButton(candidate, index, picker, replacing) {
  const reason = plannedPlacementReason(candidate, picker);
  return `<button type="button" class="candidate-primary ceres-btn ceres-btn--secondary" data-control-density="compact" data-picker-action="choose" data-candidate-index="${index}"${reason ? ` disabled${tipAttrs({ title: reason })}` : ''}>${replacing ? 'Replace' : 'Stake here'}</button>`;
}

export function candidateStatusModel(candidate) {
  const cooldown = candidate.cooldown || {};
  if (candidate.planned) return { tone: 'warn', icon: '', label: 'Planned', detail: 'Not yet combined', countdownUntil: candidate.ready_at, selectable: !plannedPlacementReason(candidate) };
  if (cooldown.state === 'staked') {
    const plot = candidate.source_plot || null;
    return { tone: 'info', icon: '◆', label: `On land${plot ? ` · plot ${plot}` : ''}`, countdownUntil: null, detail: 'Moving it here costs a 72 h cooldown', selectable: true, onLand: true };
  }
  if (cooldown.state === 'survival_locked') {
    const bracket = candidate.survival_bracket ? `Bracket ${candidate.survival_bracket} · ` : '';
    return { tone: 'danger', icon: '✕', label: 'Dead in Survival', countdownUntil: cooldown.ready_at || null, detail: `${bracket}may revive earlier at the season reset`, selectable: false };
  }
  if (cooldown.state === 'cooling') {
    // with a known source plot the detail line is "Unstaked from" + the shared plot
    // badge (candidateStatusHtml), so `detail` carries only the no-plot fallback.
    return { tone: 'warn', icon: '◷', label: 'Cooling', countdownUntil: cooldown.ready_at || null, detail: candidate.cooling_from_plot ? '' : 'Unstaked; planning preview remains available', selectable: true, coolingFromPlot: candidate.cooling_from_plot || null };
  }
  if (cooldown.state === 'unknown') {
    return { tone: 'warn', icon: '?', label: 'Cooldown unknown', countdownUntil: null, detail: 'Stake class not recognised', selectable: true };
  }
  if (cooldown.state === 'unverified') {
    return { tone: 'warn', icon: '?', label: 'Cooldown unverified', countdownUntil: null, detail: 'No published duration; restake timing not promised', selectable: true };
  }
  return { tone: 'quiet', icon: '', label: 'Idle · ready', countdownUntil: null, detail: '', selectable: true };
}

function onLandBadgeHtml(candidate, { compact = false } = {}) {
  const row = (BOARD?.plots || []).find((plot) =>
    (candidate.source_deed_uid && plot.deed_uid === candidate.source_deed_uid)
    || (candidate.source_plot && String(plot.plot) === String(candidate.source_plot))) || null;
  const deedUid = row?.deed_uid || candidate.source_deed_uid || null;
  const plotLabel = row?.plot || candidate.source_plot || null;
  const badge = plotLabel ? plotBadgeHtml(row || { plot: plotLabel }, deedUid || plotLabel, { static: !deedUid }) : '';
  const tip = tipAttrs({ title: 'On land', body: `Staked on ${plotLabel ? `plot ${plotLabel}` : 'another plot'}${row?.territory ? ` (${row.territory})` : ''}. Moving it here costs a 72 h cooldown.`, peer: 'candidate_cooldown' });
  return `<span class="candidate-status-onland candidate-source-plot"${tip}>${compact ? '' : '<span class="candidate-status-part">On land</span>'}${badge}</span>`;
}

// (his words: "fix the same column for cooling workers … also use the region badge
// there"): the cooling detail line is "Unstaked from" + the same static plot badge the
// On-land cell draws; the board row (by plot number) supplies the territory colour.
function unstakedFromBadgeHtml(plotLabel) {
  const row = (BOARD?.plots || []).find((plot) => String(plot.plot) === String(plotLabel)) || null;
  const badge = plotBadgeHtml(row || { plot: plotLabel }, row?.deed_uid || plotLabel, { static: true });
  return `<span class="candidate-status-detail candidate-status-unstaked"><span class="candidate-status-part">Unstaked from</span>${badge}</span>`;
}

function candidateStatusHtml(candidate, picker = S.CERES_PICKER) {
  if (candidate.planned) return { label: 'Planned', selectable: !plannedPlacementReason(candidate, picker), html: plannedCandidateHtml(candidate, picker) };
  const status = candidateStatusModel(candidate);
  const when = status.countdownUntil ? humanizeInstant(status.countdownUntil) : null;
  // the cooling pill is ONE line -- a compact countdown ("2d 18h", minutes only under
  // a day) and no wrapping (.is-oneline, cooldowns are <= 72 h); other pills still wrap between
  // pieces, never inside a countdown.
  const countdown = status.countdownUntil
    ? `<span class="candidate-status-part">· ${status.tone === 'danger' ? 'revives in' : 'ready in'} <span class="candidate-countdown" data-countdown-until="${esc(status.countdownUntil)}" data-countdown-compact>${esc(countdownText(status.countdownUntil, Date.now(), { compact: true }) || when?.relative || '')}</span></span>` : '';
  const tip = when?.absolute ? tipAttrs({ title: status.label, body: `Reported ready time: ${when.absolute}.`, peer: 'candidate_cooldown' }) : '';
  const parts = String(status.label).split(' · ').map((part, i) => `<span class="candidate-status-part">${i ? '· ' : ''}${esc(part)}</span>`).join('');
  const badge = status.onLand ? onLandBadgeHtml(candidate) : `<span class="candidate-status-badge is-${status.tone}${countdown && status.tone === 'warn' ? ' is-oneline' : ''}"${tip}>${status.icon ? `<span class="candidate-status-icon" aria-hidden="true">${status.icon}</span>` : ''}${parts}${countdown}</span>`;
  const rentedIn = candidate.rental?.direction === 'in';
  const workerLike = picker?.groupId === 'worker' || picker?.groupId === 'runi';
  const formattedRentalExpiry = rentedIn ? humanizeInstant(candidate.rental.expires_at)?.absolute || candidate.rental.expires_at || 'unknown' : '';
  const rentalDetail = rentedIn
    ? workerLike
      ? `Rented · ends ${formattedRentalExpiry}`
      : `Rented in · expires ${candidate.rental.expires_at || 'unknown'}${candidate.rental.notice?.message ? ` · ${candidate.rental.notice.message}` : ''}`
    : '';
  const details = [status.detail, rentalDetail].filter(Boolean);
  const unstaked = status.coolingFromPlot ? unstakedFromBadgeHtml(status.coolingFromPlot) : '';
  return { ...status, html: `${badge}${unstaked}${details.map((detail) => `<span class="candidate-status-detail">${esc(detail)}</span>`).join('')}` };
}

// Short, factual fragment describing WHY a staged asset isn't ready -- feeds the
// {reason} placeholder of notices.py's `checkout_cooldown_blocked` template. Reads
// `cooldown.state`/`cooldown.ready_at` exactly as candidateStatusModel() above does; never
// invents a verdict `cooldown.ready` didn't already give.
export function cooldownReasonFragment(cooldown) {
  if (!cooldown) return 'cooldown evidence unavailable';
  if (cooldown.state === 'staked') return 'currently staked on another plot -- unstake it and wait out its cooldown before restaking it here';
  if (cooldown.state === 'survival_locked') {
    const when = humanizeInstant(cooldown.ready_at);
    return when?.absolute
      ? `dead from Survival mode until ${when.absolute}, or sooner at the season reset`
      : 'dead from Survival mode';
  }
  if (cooldown.state === 'cooling') {
    const when = humanizeInstant(cooldown.ready_at);
    return when ? `cooling until ${when.absolute} (${when.relative})` : (cooldown.reason || 'cooling down');
  }
  if (cooldown.state === 'unknown') return 'stake class not recognised';
  if (cooldown.state === 'unverified') return 'no published cooldown duration for this stake class';
  return cooldown.reason || 'cooldown evidence unavailable';
}

function candidateCardImageHtml(candidate) {
  const image = editorCardImageHtml(candidate);
  return image ? `<span class="candidate-art">${image}</span>` : '';
}

// the land-ability tooltip belongs to the ability area only (never the whole row)
// and it is the only tooltip there -- the icons inside carry none of their own.
function candidateAbilitiesHtml(candidate) {
  const abilities = Array.isArray(candidate.land_abilities) ? candidate.land_abilities : [];
  if (!abilities.length) return '';
  const models = abilities.map((entry) => ({ entry, model: abilityTipModel(entry) }));
  // Every ability stays visible; mobile uses two balanced rows when there are more than four.
  const shown = models.map(({ model }) => `<span class="candidate-icon-box">${model.icon ? `<img class="ability-icon" src="${ASSET_BASE}/${esc(model.icon)}" alt="${esc(model.title || 'Ability')}">` : ''}</span>`).join('');
  const tip = tipAttrs({ title: candidate.name || 'Unknown card', items: models.map(({ entry, model }) => ({ icon: model.icon, name: model.title, effect: abilityRowText(entry), group: 'Abilities' })), peer: 'candidate_row' });
  return `<span class="candidate-abilities candidate-icon-row" style="--mobile-ability-columns:${Math.ceil(abilities.length / 2)}"${tip}>${shown}</span>`;
}

export function bloodlinePairBadgeHtml(candidate, picker) {
  if (picker?.groupId !== 'worker' || !picker.filters?.bloodline_pairs) return '';
  const bloodline = workerSubType(candidate);
  if (!bloodline || !picker.bloodlineSubTypes?.includes(bloodline)) return '';
  const icon = abilityTipModel(['BLOODLINE', 0, bloodline]).icon;
  return `<span class="candidate-bloodline-pair">${icon ? `<img class="ability-icon" src="${ASSET_BASE}/${esc(icon)}" alt="Toil and Kin">` : ''}<span>Bloodline Pair: ${esc(bloodline)}</span></span>`;
}

export function bloodlineRelationHtml(candidate, picker, row) {
  // title and Bloodline on the first line, the pairing on the second.
  const plate = (tone, icon, head, sub) => `<span class="candidate-bloodline ${tone}"><span class="candidate-bloodline-head">${icon}<span>${head}</span></span>${sub ? `<span class="candidate-bloodline-sub">${sub}</span>` : ''}</span>`;
  const outgoingRef = picker?.mode === 'replace' ? picker.currentOccupant?.asset_ref : null;
  const workers = (Array.isArray(row?.workers) ? row.workers : [])
    .filter((worker) => !outgoingRef || String(worker.asset_ref) !== String(outgoingRef));
  const holderEntry = (candidate.land_abilities || []).find((entry) => Array.isArray(entry) && entry[0] === 'BLOODLINE' && entry[2]);
  if (holderEntry) {
    const required = String(holderEntry[2]);
    const companion = workers.find((worker) => workerSubType(worker) === required);
    return plate(companion ? 'is-paired' : 'is-solo', abilityIconHtml(holderEntry, 'candidate_bloodline'),
      `Toil and Kin · Bloodline: ${esc(required)}`,
      companion ? `Pairs with ${esc(companion.name || required)} on this plot${bloodlineOutranked(holderEntry, workers) ? ' · not stacking, a larger Toil and Kin applies' : ''}` : `No ${esc(required)} on this plot yet`);
  }
  const subType = candidate.sub_type ? String(candidate.sub_type) : '';
  if (!subType) return '';
  let match = null;
  workers.some((holder) => {
    const entry = (holder.land_abilities || []).find((ability) => Array.isArray(ability) && ability[0] === 'BLOODLINE' && String(ability[2]) === subType);
    if (!entry) return false;
    match = { holder, entry };
    return true;
  });
  if (match) {
    return plate('is-paired', abilityIconHtml(match.entry, 'candidate_bloodline'), `Bloodline: ${esc(subType)}`,
      `Pairs with ${esc(match.holder.name || 'the holder')}'s Toil and Kin`);
  }
  return `<span class="candidate-bloodline"><span class="candidate-bloodline-head"><span>Bloodline: ${esc(subType)}</span></span></span>`;
}

// item 10: the whole move, in one extended tooltip -- what the number is, then
// each part the server priced, then what it is not.
function plotChangeTipAttrs(candidate, impact, impactText) {
  // the figure is the
  // whole move across the account -- what THIS plot gains, minus what the plot the card leaves loses. The
  // server's own/residual split is internal; this plot's change is shown as one number.
  const breakdown = candidate.net_gain_breakdown || {};
  const n = (key) => (breakdown[key] === null || breakdown[key] === undefined ? null : Number(breakdown[key]));
  const hasHarvest = Number.isFinite(n('resulting_harvest')) && Number.isFinite(n('current_harvest'));
  const here = hasHarvest ? n('resulting_harvest') - n('current_harvest')
    : ['own_harvest_delta', 'evicted_loss', 'plot_residual_delta', 'precondition_delta'].reduce((sum, key) => sum + (Number.isFinite(n(key)) ? n(key) : 0), 0);
  const donor = n('donor_plot_delta');
  const source = candidate.source_plot ? ` (plot ${candidate.source_plot})` : '';
  const items = [];
  if (Number.isFinite(here)) items.push({ name: 'This plot gains', effect: `${signedPp(here)} PP`, group: 'Made up of' });
  if (Number.isFinite(n('evicted_loss')) && n('evicted_loss') !== 0) items.push({ name: '  incl. worker it replaces', effect: `${signedPp(n('evicted_loss'))} PP`, group: 'Made up of' });
  if (Number.isFinite(donor) && donor !== 0) items.push({ name: `Plot it leaves${source}`, effect: `${signedPp(donor)} PP`, group: 'Made up of' });
  const title = impact.status === 'point' ? `Total PP change ${signedPp(impact.value)} PP` : 'Total PP change';
  const moving = Number.isFinite(donor) && donor !== 0;
  const body = impact.status === 'point'
    ? (moving ? `This card is staked on plot ${candidate.source_plot || 'another plot'}. Moving it here changes both plots: the total is what this plot gains minus what that plot loses.` : "How this plot's PP changes if you stake this card here.")
    : impact.status === 'range'
      ? `Between ${impactText.value} PP: ${impactText.reason}.`
      : `Not calculated: ${impactText.reason}.`;
  return tipAttrs({ title, body, items, foot: 'Harvest PP after boosts, whole account. Base PP and Boostable describe the card alone.', peer: 'candidate_metric' });
}

// harvest change per hour, priced like Total PP change -- what this plot gains in ITS resource, minus
// what the plot the card leaves loses in ITS resource (two resources when the plots differ). Rate = the plot's
// own harvest per PP (Harvest/hr ÷ Total PP); a plot producing nothing yet uses the typical rate of plots
// making the same resource.
function harvestRatePerPp(plot) {
  if (!plot) return null;
  const pp = Number(plot.harvest_pp);
  const perHour = Number(plot.work_per_hour);
  if (pp > 0 && Number.isFinite(perHour)) return perHour / pp;
  const rates = (BOARD?.plots || [])
    .filter((p) => p.resource === plot.resource && Number(p.harvest_pp) > 0 && Number(p.work_per_hour) > 0)
    .map((p) => Number(p.work_per_hour) / Number(p.harvest_pp)).sort((a, b) => a - b);
  return rates.length ? rates[Math.floor(rates.length / 2)] : null;
}
function fmtHarvest(value) {
  const abs = Math.abs(value);
  const digits = abs >= 100 ? 0 : abs >= 1 ? 2 : abs >= 0.01 ? 3 : 5;
  return `${value > 0 ? '+' : value < 0 ? '−' : ''}${abs.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}
function candidateHarvestChange(candidate, picker) {
  const breakdown = candidate.net_gain_breakdown;
  const target = picker.row || (BOARD?.plots || []).find((p) => p.deed_uid === picker.deedUid);
  if (!breakdown || !target?.resource) return null;
  const n = (key) => (breakdown[key] === null || breakdown[key] === undefined ? null : Number(breakdown[key]));
  const herePp = Number.isFinite(n('resulting_harvest')) && Number.isFinite(n('current_harvest')) ? n('resulting_harvest') - n('current_harvest') : null;
  const hereRate = harvestRatePerPp(target);
  if (!Number.isFinite(herePp) || hereRate === null) return null;
  const parts = [{ resource: String(target.resource).toUpperCase(), value: herePp * hereRate, where: 'This plot' }];
  const donorPp = n('donor_plot_delta');
  if (Number.isFinite(donorPp) && donorPp !== 0) {
    const donor = (BOARD?.plots || []).find((p) => String(p.plot) === String(candidate.source_plot));
    const donorRate = harvestRatePerPp(donor);
    if (!donor?.resource || donorRate === null) return null;
    parts.push({ resource: String(donor.resource).toUpperCase(), value: donorPp * donorRate, where: `Plot it leaves (plot ${candidate.source_plot})` });
  }
  const byResource = new Map();
  parts.forEach((p) => byResource.set(p.resource, (byResource.get(p.resource) || 0) + p.value));
  return { parts, totals: [...byResource.entries()] };
}
function harvestChangeHtml(candidate, picker) {
  const change = candidateHarvestChange(candidate, picker);
  if (!change) return `<span class="candidate-metric candidate-harvest-change"${tipAttrs({ title: 'Harvest change', body: 'Not calculated for this card.', peer: 'candidate_metric' })}><strong>—</strong><small>Harvest /hr</small></span>`;
  const line = ([resource, value]) => {
    const icon = tokenIcon(resource);
    const sign = value > 0 ? ' class="is-r117-positive"' : value < 0 ? ' class="is-r117-negative"' : '';
    return `<strong${sign}>${esc(fmtHarvest(value))}${icon ? `<img class="cand-res-ic" src="${esc(icon)}" alt="${esc(tokenLabel(resource))}">` : ''}</strong>`;
  };
  const items = change.parts.map((p) => ({ icon: tooltipIconPath('resource', p.resource.toLowerCase()), name: p.where, effect: `${fmtHarvest(p.value)} ${tokenLabel(p.resource).toLowerCase()}/hr`, group: 'Made up of' }));
  const title = `Harvest change ${change.totals.map(([r, v]) => `${fmtHarvest(v)} ${tokenLabel(r).toLowerCase()}`).join(' · ')} per hour`;
  const what = change.parts.length > 1
    ? 'What this plot starts producing, minus what the plot the card leaves stops producing, each in its own resource.'
    : 'What this plot starts producing per hour with this card.';
  return `<span class="candidate-metric candidate-harvest-change${change.totals.length > 1 ? ' is-two' : ''}"${tipAttrs({ title, items, foot: `${what} Before tax (the game keeps 10% at harvest); estimated from each plot's current harvest per PP.`, peer: 'candidate_metric' })}>${change.totals.map(line).join('')}<small>Harvest /hr</small></span>`;
}

function itemMoveSourceHtml(candidate) {
  if (!candidate.source_deed_uid) return '';
  const source = BOARD?.plots?.find((row) => row.deed_uid === candidate.source_deed_uid);
  return `<span class="candidate-meta">Move from ${plotBadgeHtml(source, candidate.source_deed_uid, { static: true })} · staged removal</span>`;
}

function itemCopyAvailable(candidate, picker) {
  if (!candidate?.asset_ref) return false;
  const reserved = typeof stagedIncomingAssetRefs === 'function' ? stagedIncomingAssetRefs() : new Set();
  return !reserved.has(String(candidate.asset_ref))
    && (typeof pendingIncomingRefElsewhere !== 'function'
      || !pendingIncomingRefElsewhere(candidate.asset_ref, picker.deedUid, picker.groupId, picker.slotIndex));
}

function renderCandidateRow(candidate, index, picker, replacing) {
  const itemClass = ['totem', 'title', 'power_core'].includes(picker.groupId);
  const totemRarity = candidate.rarity || String(candidate.name || '').split(' ')[0];
  if (itemClass) {
    // Totem/Title are ITEMS -- they carry a flat production boost, never a
    // Boostable/Terrain/Plot-change figure (those are `not_applicable` for
    // items server-side, ceres/candidates.py::serialize_candidate). The row shows
    // identity plus one numeric Boost column; no fake net-PP delta or gain/loss
    // rail ( Phase 4, plan §8).
    const isTotem = picker.groupId === 'totem';
    const isTitle = picker.groupId === 'title';
    const leading = isTotem
      // Gate document #79/#80/#106: no tooltip on a picker item row -- name, tier and
      // boost are all printed on the row itself.
      ? totemIconHtml(totemRarity, null)
      : isTitle ? titleIconHtml(null, candidate.name) : index + 1;
    const boostValue = candidate.boost === null || candidate.boost === undefined ? null : Number(candidate.boost);
    const boostText = Number.isFinite(boostValue) ? `+${num(boostValue * 100)}%` : '—';
    const tierLabel = totemRarity ? `${String(totemRarity).charAt(0).toUpperCase()}${String(totemRarity).slice(1).toLowerCase()}` : null;
    const tierHtml = isTotem
      ? (tierLabel ? `<span class="candidate-item-tier">${esc(tierLabel)}</span>` : '')
      : isTitle ? (rarityGemHtml(titleRarityGem(boostValue) || candidate.rarity) || '') : '';
    const effectHtml = candidate.effect ? `<span class="candidate-meta">${esc(candidate.effect)}</span>` : '';
    return `<button class="candidate-row candidate-row-item" data-picker-action="choose" data-candidate-index="${index}"><span class="candidate-rank">${leading}</span><span class="candidate-copy"><span class="candidate-name-line"><strong class="candidate-name">${esc(candidate.name || 'Unknown asset')}</strong>${foilBadgeHtml(candidate, 'candidate-tag is-foil')}${tierHtml}</span>${effectHtml}${itemMoveSourceHtml(candidate)}</span><span class="candidate-boost"><strong>${boostText}</strong></span></button>`;
  }
  const delta = candidateDelta(candidate, picker);
  const construction = candidateEvaluation(candidate, 'construction_pp', null);
  const impact = candidateEvaluation(candidate, 'plot_impact', 'net_gain');
  const constructionText = evaluationText(construction, 'Boostable');
  const impactText = evaluationText(impact, 'Total PP change', true);
  const terrain = terrainValue(candidate.terrain_boost);
  const terrainText = terrain !== null ? `${signedPp(terrain * 100)}%` : '—';
  const terrainSignClass = terrain !== null && terrain > 0 ? ' is-r117-positive' : terrain !== null && terrain < 0 ? ' is-r117-negative' : '';
  const terrainFigure = `<strong${terrainSignClass ? ` class="${terrainSignClass.trim()}"` : ''}>${terrainText}</strong>`;
  const detail = renderCandidateBreakdown(candidate);
  const foilChip = foilBadgeHtml(candidate, 'candidate-tag is-foil');
  const iconBox = (html) => (html ? `<span class="candidate-icon-box">${html}</span>` : '');
  const identity = `<span class="candidate-identity"><span class="candidate-icon-row">${cardSplinters(candidate).map(value => iconBox(splinterIconHtml(value))).join('')}${iconBox(rarityGemHtml(candidate.rarity))}${iconBox(setIconOnlyHtml(candidate.card_set, candidate.edition))}</span>${foilChip}<span>Lv ${esc(candidate.level ?? '—')}</span><span>${esc(editionDisplayLabel(candidate.edition))}</span></span>`;
  const status = candidateStatusHtml(candidate);
  const upgradeCount = workerUpgradeCopyCount(picker.row || null, candidate);
  const upgradeChip = upgradeCount > 0 ? `<span class="candidate-upgrade">${upgradeCount} combinable ${upgradeCount === 1 ? 'copy' : 'copies'}</span>` : '';
  const classification = impact.status === 'range' ? 'is-range' : impact.status !== 'point' ? 'is-unknown' : delta > 0 ? 'is-gain' : delta < 0 ? 'is-loss' : 'is-neutral';
  const notReady = ['cooling', 'survival_locked'].includes(candidate.cooldown?.state) ? ' is-not-ready' : '';
  const impactSign = impact.status === 'point' && impact.value > 0 ? ' class="is-positive"' : impact.status === 'point' && impact.value < 0 ? ' class="is-negative"' : '';
  const targetTerrain = picker.targetTerrain || 'this plot';
  const terrainTip = terrain !== null
    ? `${terrain < 0 ? 'Reduces' : 'Boosts'} ${candidate.splinter}'s production by ${terrainText} on ${targetTerrain}`
    : `Terrain effect isn't known for ${targetTerrain}`;
  const basePp = candidate.base_pp === null || candidate.base_pp === undefined ? '—' : num(candidate.base_pp);
  const accessible = `${candidate.name || 'Unknown asset'}. ${status.label}. Base PP ${basePp}. ${constructionText.text}. ${terrainTip}. ${impactText.text}. ${detail}.`;
  const rowAction = status.selectable ? ' data-picker-action="choose"' : '';
  const rowDisabled = status.selectable ? ' tabindex="0"' : ' aria-disabled="true"';
  const primary = candidate.planned ? plannedStakeButton(candidate, index, picker, replacing) : status.selectable ? `<button type="button" class="candidate-primary ceres-btn ceres-btn--secondary" data-control-density="compact" data-picker-action="choose" data-candidate-index="${index}">${replacing ? 'Replace' : 'Stake here'}</button>` : '';
  const combine = picker.groupId === 'worker' ? candidateCombineActionHtml(candidate, index) : '';
  const actionsHtml = `<span class="candidate-actions">${primary}${upgradeChip}${picker.groupId === 'worker' && picker.filters?.show_upgrade ? upgradeActionHtml(candidate, index) : ''}${combine}</span>`;
  const identityBlock = `<span class="candidate-card-details"><strong class="candidate-name">${esc(candidate.name || 'Unknown asset')}</strong>${identity}<span class="candidate-traits">${candidateAbilitiesHtml(candidate)}${bloodlinePairBadgeHtml(candidate, picker) || bloodlineRelationHtml(candidate, picker, picker.row || null)}</span></span>`;
  // item 11: splinter on terrain, then the effect in one short line.
  const terrainTipModel = terrain !== null
    ? { title: `${cardSplinterTipHtml(candidate)} on ${tipIcon(tooltipIconPath('terrain', targetTerrain))}${targetTerrain}`,
      body: terrain > 0 ? `Terrain bonus ${terrainText} production` : terrain < 0 ? `Terrain penalty ${terrainText} production` : 'No terrain effect', peer: 'candidate_terrain' }
    : { title: 'Terrain effect not reported', body: `No modifier is known for ${targetTerrain}.`, peer: 'candidate_terrain' };
  const metrics = `<span class="candidate-metrics"><span class="candidate-metric candidate-base"><strong>${basePp}</strong><small>Base PP</small></span><span class="candidate-metric candidate-construction"${tipAttrs({ title: `Boostable ${constructionText.value} PP`, body: "This card's Base PP after this plot's terrain, before harvest boosts.", peer: 'candidate_metric' })}><strong>${constructionText.value}</strong><small>Boostable</small>${constructionText.reason ? `<em>${esc(constructionText.reason)}</em>` : ''}</span><span class="candidate-metric candidate-terrain"${tipAttrs(terrainTipModel)}>${terrainFigure}<small>Terrain</small></span><span class="candidate-metric candidate-plot-change"${plotChangeTipAttrs(candidate, impact, impactText)}><strong${impactSign}>${impactText.value}</strong><small>Total PP change</small>${impactText.reason ? `<em>${esc(impactText.reason)}</em>` : ''}</span>${harvestChangeHtml(candidate, picker)}</span>`;
  // cards replace the placeholders in place (no fade from 0 -- that was the blink); only the art fades.
  const entering = '';
  return `<div class="candidate-row candidate-card ${candidate.planned ? 'is-planned ' : ''}${classification}${notReady}${entering}"${rowAction} data-candidate-index="${index}" style="--ci:${Math.min(index, 10)}" role="button"${rowDisabled} aria-label="${esc(accessible)}">${candidate.planned ? candidateGridArtHtml(candidate) : candidateCardImageHtml(candidate)}${identityBlock}<span class="candidate-status">${status.html}</span>${metrics}${actionsHtml}</div>`;
}

function candidateReadinessOverlayHtml(candidate) {
  const status = candidateStatusModel(candidate);
  if (!candidate.planned && !['cooling', 'survival_locked'].includes(candidate.cooldown?.state)) return '';
  const when = status.countdownUntil ? humanizeInstant(status.countdownUntil) : null;
  const countdown = status.countdownUntil
    ? `<span class="candidate-countdown" data-countdown-until="${esc(status.countdownUntil)}" data-countdown-compact>${esc(countdownText(status.countdownUntil, Date.now(), { compact: true }) || when?.relative || '')}</span>`
    : `<span class="candidate-countdown">${candidate.planned ? 'about 3 days' : 'Unknown'}</span>`;
  return `<span class="candidate-readiness-overlay ${candidate.planned ? 'is-warn' : 'is-danger'}" role="status"><span>Ready in</span><span class="candidate-readiness-badge">${countdown}</span></span>`;
}

function candidateGridArtHtml(candidate) {
  const art = candidateCardImageHtml(candidate);
  if (!art) return '';
  return art.replace(/<\/span>$/, `${candidateReadinessOverlayHtml(candidate)}${foilBadgeHtml(candidate, 'candidate-grid-art-foil')}</span>`);
}

function gridIdentityHtml(candidate) {
  const iconBox = (html) => (html ? `<span class="candidate-icon-box">${html}</span>` : '');
  const icons = `<span class="candidate-icon-row">${cardSplinters(candidate).map(value => iconBox(splinterIconHtml(value))).join('')}${iconBox(rarityGemHtml(candidate.rarity))}${iconBox(setIconOnlyHtml(candidate.card_set, candidate.edition, editionDisplayLabel(candidate.edition)))}</span>`;
  return `<span class="candidate-grid-identity"><strong class="candidate-name">${esc(candidate.name || 'Unknown asset')}</strong><span class="candidate-grid-icon-level">${icons}<span>Lv ${esc(candidate.level ?? '—')}</span></span>${candidateAbilitiesHtml(candidate)}</span>`;
}

function candidateGridDetailHtml(candidate, accessible, index, key) {
  return `<div id="${candidateDomId(key)}" class="candidate-grid-detail" data-detail-key="${esc(key)}" role="dialog" aria-label="Details for ${esc(candidate.name || 'candidate')}" aria-hidden="true"><p>${esc(accessible)}</p><button type="button" class="candidate-detail-pin ceres-btn ceres-btn--secondary" data-control-density="compact" data-picker-action="pin-details" data-candidate-index="${index}" aria-pressed="false">Pin details</button></div>`;
}

function renderCandidateGridCard(candidate, index, picker, replacing) {
  const key = candidateStableKey(candidate, index);
  const selected = picker.selectedKey === key;
  const itemClass = ['totem', 'title', 'power_core'].includes(picker.groupId);
  const selectable = candidateStatusModel(candidate).selectable && (!itemClass || itemCopyAvailable(candidate, picker));
  const detailsButton = `<button type="button" class="candidate-detail-toggle ceres-btn ceres-btn--secondary" data-control-density="compact" data-picker-action="details" data-candidate-index="${index}" aria-expanded="false" aria-controls="${candidateDomId(key)}">Details</button>`;
  if (itemClass) {
    const isTotem = picker.groupId === 'totem';
    const totemRarity = candidate.rarity || String(candidate.name || '').split(' ')[0];
    const leading = isTotem ? totemIconHtml(totemRarity, null) : titleIconHtml(null, candidate.name);
    const boostValue = candidate.boost === null || candidate.boost === undefined ? null : Number(candidate.boost);
    const boostText = Number.isFinite(boostValue) ? `+${num(boostValue * 100)}%` : '—';
    const tierLabel = totemRarity ? `${String(totemRarity).charAt(0).toUpperCase()}${String(totemRarity).slice(1).toLowerCase()}` : null;
    const tierHtml = isTotem ? (tierLabel ? `<span class="candidate-item-tier">${esc(tierLabel)}</span>` : '') : (rarityGemHtml(titleRarityGem(boostValue) || candidate.rarity) || '');
    const effectHtml = candidate.effect ? `<span class="candidate-meta">${esc(candidate.effect)}</span>` : '';
    const accessible = `${candidate.name || 'Unknown asset'}. Production boost ${boostText}. ${candidate.effect || ''}`;
    const primary = selectable ? `<button type="button" class="candidate-primary ceres-btn ceres-btn--secondary" data-control-density="compact" data-picker-action="choose" data-candidate-index="${index}">${replacing ? 'Replace' : 'Stake here'}</button>` : '';
    const face = `<span class="candidate-grid-item-face"><span class="candidate-rank">${leading}</span><span class="candidate-copy"><span class="candidate-name-line"><strong class="candidate-name">${esc(candidate.name || 'Unknown asset')}</strong>${foilBadgeHtml(candidate, 'candidate-tag is-foil')}${tierHtml}</span>${effectHtml}${itemMoveSourceHtml(candidate)}</span><span class="candidate-boost"><strong>${boostText}</strong><small>Production boost</small></span></span>`;
    return `<article class="candidate-card candidate-grid-card candidate-grid-item${selected ? ' is-selected' : ''}" data-candidate-index="${index}" data-candidate-key="${esc(key)}" data-picker-group="${esc(picker.groupId)}"><button type="button" class="candidate-grid-select" data-picker-action="select" data-candidate-index="${index}" tabindex="${selected || (!picker.selectedKey && index === 0) ? '0' : '-1'}" aria-pressed="${selected ? 'true' : 'false'}" aria-label="${esc(`${accessible} Select this card.`)}">${face}</button><div class="candidate-grid-tools">${detailsButton}${primary}</div>${candidateGridDetailHtml(candidate, accessible, index, key)}</article>`;
  }
  const status = candidateStatusModel(candidate);
  const construction = candidateEvaluation(candidate, 'construction_pp', null);
  const constructionText = evaluationText(construction, 'Boostable');
  const terrain = terrainValue(candidate.terrain_boost);
  const basePp = candidate.base_pp === null || candidate.base_pp === undefined ? '—' : num(candidate.base_pp);
  const boostState = terrain !== null && terrain > 0 ? ' is-positive' : terrain !== null && terrain < 0 ? ' is-negative' : terrain === null ? ' is-unknown' : '';
  const label = `${candidate.name || 'Unknown asset'}. ${status.label}. Base PP ${basePp}. ${constructionText.text}.`;
  const disabled = status.selectable ? '' : ` disabled${tipAttrs({ title: plannedPlacementReason(candidate, picker) || status.label })}`;
  const metrics = `<span class="candidate-grid-metrics"><span class="candidate-grid-metric candidate-grid-base"><strong>${basePp}</strong><small>Base PP</small></span><span class="candidate-grid-metric candidate-grid-boostable${boostState}"><strong>${constructionText.value}</strong><small>Boostable</small></span></span>`;
  const combine = picker.groupId === 'worker' && candidateCombineInfo(candidate) ? candidateCombineActionHtml(candidate, index) : '';
  const face = `<span class="candidate-grid-face">${candidateGridArtHtml(candidate)}${gridIdentityHtml(candidate)}${candidate.planned ? plannedCandidateHtml(candidate, picker) : ''}${bloodlinePairBadgeHtml(candidate, picker)}${metrics}</span>`;
  const stage = `<button type="button" class="candidate-grid-select candidate-grid-stage" data-picker-action="choose" data-candidate-index="${index}"${disabled} aria-label="${esc(`${label} ${replacing ? 'Replace' : 'Stake'}.`)}">${face}</button>`;
  const arriving = picker.cardsShownAt && performance.now() - picker.cardsShownAt < 1000 ? ' is-grid-arriving' : '';
  return `<article class="candidate-card candidate-grid-card${candidate.planned ? ' is-planned' : ''}${selected ? ' is-selected' : ''}${status.selectable ? '' : ' is-not-ready'}${arriving}" data-candidate-index="${index}" data-candidate-key="${esc(key)}" data-picker-group="${esc(picker.groupId)}" style="--ci:${Math.min(index, 10)}">${stage}${status.onLand ? onLandBadgeHtml(candidate, { compact: true }) : ''}<div class="candidate-grid-tools">${candidate.planned ? plannedStakeButton(candidate, index, picker, replacing) : picker.filters?.show_upgrade ? upgradeActionHtml(candidate, index) : ''}${combine}</div></article>`;
}

function itemBoostSortKey(candidate) {
  const rawBoost = candidate?.boost;
  const boost = rawBoost === null || rawBoost === undefined || rawBoost === '' ? NaN : Number(rawBoost);
  const known = Number.isFinite(boost);
  return {
    known,
    boost: known ? boost : Number.NEGATIVE_INFINITY,
    name: String(candidate?.name || '').toLocaleLowerCase(),
    ref: String(candidate?.asset_ref || candidate?.uid || ''),
  };
}

function renderItemRarityGridCards(picker, replacing) {
  const isTitle = picker.groupId === 'title';
  const rarities = isTitle ? ['rare', 'epic', 'legendary'] : ['common', 'rare', 'epic', 'legendary'];
  const orderedRarities = [...rarities].sort((left, right) => {
    const a = itemBoostSortKey(picker.rarityGroups?.[left]?.candidate);
    const b = itemBoostSortKey(picker.rarityGroups?.[right]?.candidate);
    if (a.known !== b.known) return a.known ? -1 : 1;
    if (a.boost !== b.boost) return b.boost - a.boost;
    return rarities.indexOf(right) - rarities.indexOf(left);
  });
  const assetLabel = isTitle ? 'Title' : 'Totem';
  const reserved = typeof stagedIncomingAssetRefs === 'function' ? stagedIncomingAssetRefs() : new Set();
  const tierOf = (item) => String(item?.rarity || item?.name?.split(' ')[0] || '').toLowerCase();
  const isFree = (item) => item?.asset_ref && !reserved.has(String(item.asset_ref))
    && (typeof pendingIncomingRefElsewhere !== 'function' || !pendingIncomingRefElsewhere(item.asset_ref, picker.deedUid, picker.groupId, picker.slotIndex));
  return orderedRarities.map((rarity) => {
    const group = picker.rarityGroups?.[rarity] || { count: 0, candidate: null };
    const copies = [...new Map([group.candidate, ...(picker.candidates || []).filter((item) => tierOf(item) === rarity)]
      .filter((item) => item?.asset_ref)
      .map((item) => [String(item.asset_ref), item])).values()];
    const count = Number(group.count || 0);
    const candidate = copies.find(isFree) || (count > copies.length ? group.candidate : null);
    const freeCount = Math.max(0, count - copies.filter((item) => !isFree(item)).length);
    const disabled = !candidate || count < 1;
    const countLabel = freeCount === count ? `${count} available` : `${freeCount} available`;
    const label = `${rarity.charAt(0).toUpperCase()}${rarity.slice(1)} ${assetLabel}`;
    const icon = isTitle ? rarityGemHtml(rarity) : totemIconHtml(rarity, null);
    return `<button type="button" class="candidate-grid-card candidate-grid-rarity is-${picker.groupId}${disabled ? ' is-unavailable' : ''}" data-picker-action="choose" data-picker-rarity="${rarity}"${disabled ? ' disabled aria-disabled="true"' : ''} aria-label="${esc(`${label}, ${countLabel}, ${replacing ? 'replace' : 'stake'}`)}"><span class="candidate-grid-rarity-icon">${icon}</span><strong>${esc(label)}</strong><span class="candidate-grid-rarity-count">${countLabel}</span></button>`;
  }).join('');
}

function pickerEmptyHtml(picker) {
  const excluded = picker.excludedCounts || {};
  const noticeFor = (code) => (picker.notices || []).find((notice) => notice.code === code) || catalogNotice(code, { count: excluded[code] || 0 });
  if (picker.candidates.length) return '';
  if (picker.filters?.bloodline_pairs && !picker.bloodlineSubTypes?.length) {
    return '<p class="picker-empty">No Bloodline holder on this plot to match a companion against.</p>';
  }
  if (picker.candidates.length) return '';
  if (Number(excluded.capacity) > 0) {
    return `<p class="picker-empty">${esc(noticeMessage(noticeFor('capacity')))}</p>`;
  }
  if (Number(excluded.negative_net_gain) > 0) {
    return `<p class="picker-empty">${esc(noticeMessage(noticeFor('negative_net_gain')))}</p>`;
  }
  if (Number(excluded.terrain_penalty) > 0) {
    return `<p class="picker-empty">${esc(noticeMessage(noticeFor('terrain_penalty')))}</p>`;
  }
  if (picker.groupId === 'runi') return '<p class="picker-empty picker-availability" role="status">No Runi match these filters.</p>';
  return '<p class="picker-empty">No candidates match these filters.</p>';
}

export function pickerHiddenSummary(picker) {
  const excluded = picker.excludedCounts || {};
  const chips = [];
  const penalised = Number(excluded.terrain_penalty || 0);
  if (penalised > 0) {
    chips.push(`<button type="button" class="picker-hidden-chip ceres-btn ceres-btn--secondary" data-control-density="compact" data-picker-action="show-hidden-penalised"${tipAttrs({ title: 'Show penalised cards', body: "Cards whose splinter loses production on this terrain are hidden. Click to include them.", peer: 'picker_hidden' })}>${num(penalised)} penalised hidden</button>`);
  }
  const dead = Number(excluded.survival_locked || 0);
  const cooling = Number(excluded.cooling || 0);
  if (dead + cooling > 0) {
    const breakdown = [dead ? `${num(dead)} dead in Survival` : '', cooling ? `${num(cooling)} cooling` : ''].filter(Boolean).join(' · ');
    chips.push(`<button type="button" class="picker-hidden-chip ceres-btn ceres-btn--secondary" data-control-density="compact" data-picker-action="show-hidden-not-ready"${tipAttrs({ title: 'Show cards that are not ready', body: 'Cooling and dead-in-Survival cards are hidden by Ready only. Click to show them.', peer: 'picker_hidden' })}>${num(dead + cooling)} not ready hidden (${breakdown})</button>`);
  }
  const onLand = Number(excluded.on_land || 0);
  if (onLand > 0) {
    chips.push(`<button type="button" class="picker-hidden-chip ceres-btn ceres-btn--secondary" data-control-density="compact" data-picker-action="show-hidden-on-land"${tipAttrs({ title: 'Show cards already on land', body: 'Cards staked on another plot are hidden until On land is on. Moving one here costs a 72 h cooldown. Click to show them.', peer: 'picker_hidden' })}>${num(onLand)} on land hidden</button>`);
  }
  const unavailableKeys = [['delegated', 'delegated'], ['rented', 'rented'], ['soulkeep', 'Soulkeep'], ['market_listed', 'listed'], ['unlock_locked', 'unlock-locked']];
  const unavailable = unavailableKeys.reduce((sum, [key]) => sum + Number(excluded[key] || 0), 0);
  if (unavailable > 0) {
    const breakdown = unavailableKeys.filter(([key]) => Number(excluded[key] || 0) > 0)
      .map(([key, label]) => `${num(Number(excluded[key]))} ${label}`).join(' · ');
    chips.push(`<span class="picker-hidden-chip is-static"${tipAttrs({ title: `${num(unavailable)} unavailable`, body: breakdown, peer: 'picker_hidden' })}>${num(unavailable)} unavailable</span>`);
  }
  return chips.join('');
}

// Door 3's trailing action -- rendered by renderCandidateRow() inside the worker
// row's own `.candidate-actions` column (2026-08-30: no longer a sibling of the
// row; the row became a <div> so a real <button> can live inside it). Absent-not
// disabled when nothing is combinable (a plain dash, matching the other two
// doors' rule).
function candidateCombineActionHtml(candidate, index) {
  const info = candidateCombineInfo(candidate);
  if (!info) return '<span class="picker-combine-dash" aria-hidden="true">—</span>';
  const label = `Combine idle duplicates of ${candidate.name || 'this card'} (${info.count} eligible ${info.count === 1 ? 'copy' : 'copies'})`;
  return `<button type="button" class="picker-combine ceres-btn ceres-btn--secondary" data-control-density="compact" data-picker-action="combine" data-candidate-index="${index}"${tipAttrs(combineTipModel(info.count, 'candidate_combine'))}>Combine (${info.count})</button>`;
}

const PICKER_GRID_PLACEHOLDER_COUNT = 7;
function pickerLoadingRowsHtml(picker, startIndex) {
  if (startIndex !== 0) return '';
  if (picker.groupId === 'runi') return '<p class="picker-empty picker-availability" role="status">Checking available Runi…</p>';
  if (picker.source === 'card_pool_refreshing') {
    return '<p class="picker-empty" role="status">Refreshing your cards…</p>';
  }
  const status = '<span class="sr-only" role="status">Loading candidates…</span>';
  if (picker.viewMode !== 'grid') {
    return `${status}${'<div class="candidate-row candidate-card is-ghost" aria-hidden="true"><span class="candidate-art"><span class="spinner"></span></span><span class="cand-ghost-body"></span></div>'.repeat(3)}`;
  }
  const ghostIdentity = '<span class="candidate-grid-identity cand-ghost-body"><strong class="candidate-name">&nbsp;</strong><span class="candidate-grid-icon-level"><span class="candidate-icon-row"><span class="candidate-icon-box"></span><span class="candidate-icon-box"></span><span class="candidate-icon-box"></span></span><span>&nbsp;</span></span><span class="candidate-abilities candidate-icon-row"><span class="candidate-icon-box"></span></span></span>';
  const ghostMetrics = '<span class="candidate-grid-metrics"><span class="candidate-grid-metric"><strong>&nbsp;</strong><small>&nbsp;</small></span><span class="candidate-grid-metric"><strong>&nbsp;</strong><small>&nbsp;</small></span></span>';
  const ghost = `<article class="candidate-card candidate-grid-card is-ghost" data-picker-group="${esc(picker.groupId)}" aria-hidden="true"><span class="candidate-grid-select candidate-grid-stage"><span class="candidate-grid-face"><span class="candidate-art"><span class="spinner"></span></span>${ghostIdentity}${ghostMetrics}</span></span><span class="candidate-grid-tools"></span></article>`;
  return `${status}${ghost.repeat(PICKER_GRID_PLACEHOLDER_COUNT)}`;
}

export function pickerRegionHtml(picker, replacing, row, { startIndex = 0 } = {}) {
  // The Plan/Combine actions used to be built HERE, as siblings of
  // renderCandidateRow()'s <button> (buttons cannot nest, so this was the
  // only place to put them) -- they now live inside the row itself as a
  // real trailing grid column, since a worker row is a <div> and can hold
  // them. See renderCandidateRow()'s own comment for the why.
  // the cards fade in (staggered) once, when the list first shows -- like the plot opening's tiles.
  const refreshingCards = picker.source === 'card_pool_refreshing';
  if (refreshingCards && !picker.loading && !picker.cardPoolRetryTimer) {
    picker.cardPoolRetryTimer = setTimeout(async () => {
      picker.cardPoolRetryTimer = null;
      if (S.CERES_PICKER !== picker || picker.loading) return;
      const { fetchPickerPage } = await import('./picker-actions.js');
      if (S.CERES_PICKER === picker) fetchPickerPage(true);
    }, 2000);
  }
  if (!picker.loading && !refreshingCards && !picker.error && picker.candidates.length && !picker.cardsShownAt) picker.cardsShownAt = performance.now();
  const itemRarityGrid = picker.viewMode === 'grid' && ['totem', 'title'].includes(picker.groupId);
  const selectedAbilities = cbList(picker.filters?.ability);
  const visible = picker.candidates.map((candidate, index) => ({ candidate, index })).filter(({ candidate }) =>
    !selectedAbilities.length || (selectedAbilities.includes(CB_ANY_ABILITY) ? cbAbilityCodes(candidate).length > 0
      : cbAbilityCodes(candidate).some((code) => selectedAbilities.includes(code))));
  const candidateRows = itemRarityGrid && startIndex === 0
    ? renderItemRarityGridCards(picker, replacing)
    : visible.filter(({ index }) => index >= startIndex).map(({ candidate, index }) => picker.viewMode === 'grid'
      ? renderCandidateGridCard(candidate, index, picker, replacing)
      : renderCandidateRow(candidate, index, picker, replacing)).join('');
  const rows = picker.loading || refreshingCards
    // no text line that the cards then push away -- the list opens with card-shaped placeholders
    // (empty art slot + the official spinner), which the real cards replace in place.
    ? pickerLoadingRowsHtml(picker, startIndex)
    : picker.error
      ? `<p class="picker-error">${esc(picker.error)}</p>`
      : (startIndex === 0 && !itemRarityGrid && !visible.length && selectedAbilities.length
        ? `<p class="picker-empty">No loaded candidates have the chosen ability.${picker.total > picker.candidates.length ? ' Load more to check the remaining candidates.' : ''}</p>`
        : startIndex === 0 && !itemRarityGrid ? pickerEmptyHtml(picker) : '') + candidateRows;
  const total = picker.total === null ? '—' : num(picker.total);
  const shown = picker.candidates.length;
  // `picker.offset` already equals `shown` after every page, so the old
  // `offset + shown` test counted the list twice and stopped at 50; and no row cap.
  const hasMore = !itemRarityGrid && picker.total !== null && shown < picker.total;
  const more = picker.loading && shown > 0
    ? '<span class="picker-more picker-loading-more" role="status">Loading…</span>'
    : hasMore ? '<button type="button" class="picker-chip picker-more ceres-btn ceres-btn--choice" data-control-density="compact" data-picker-action="more"><span class="ceres-btn__check" aria-hidden="true"></span>Load 25 more</button>' : '';
  const count = refreshingCards ? '' : itemRarityGrid ? `<span class="picker-count">${picker.groupId === 'title' ? 'Three' : 'Four'} rarity tiers</span>` : selectedAbilities.length
    ? `<span class="picker-count">${visible.length} matching in ${shown} loaded · ${total} candidates</span>`
    : `<span class="picker-count">Showing ${shown} of ${total} matching</span>`;
  return {
    rows,
    footer: `${pickerHiddenSummary(picker)}${count}${more}`,
  };
}

// which of the picker's OWN filter-row dimensions (name search, Rarity
// Edition, Min/Max Base PP, Ready only, On land, Land Cards, Bloodline pairs, and
// the Splinter dropdown for Runi/legacy pickers only) is off its neutral value
// drives whether "Clear filters" shows. Deliberately excludes Sort and, for the
// worker picker, the splinter-icon terrain-affinity badges/remembered groups
// (/ are frozen, approved behaviour -- not part of this reset).
export function pickerFiltersActive(picker) {
  if (!picker) return false;
  const f = picker.filters || {};
  if (String(f.q || '').trim()) return true;
  if (f.rarity) return true;
  if (cbList(f.ability).length) return true;
  if (f.edition) return true;
  if (String(f.min_pp || '').trim()) return true;
  if (String(f.max_pp || '').trim()) return true;
  if (f.ready_now) return true;
  if (f.include_on_land) return true;
  if (f.land_only) return true;
  if (f.bloodline_pairs) return true;
  if (picker.groupId !== 'worker' && f.splinter) return true;
  return false;
}

export function renderInlineCandidatePicker(row) {
  const picker = S.CERES_PICKER;
  if (!picker || picker.deedUid !== row.deed_uid) return '';
  const replacing = picker.mode === 'replace' && picker.currentOccupant;
  const workerLike = picker.groupId === 'worker' || picker.groupId === 'runi';
  // Totem/Title are ITEMS (`stake.items[]`) -- a flat production-boost ladder, not
  // a scored plot-fit candidate, so they get their own sort chips (Production
  // boost/Name). Power Core never reaches this picker at all (one-click flow
  // stagePowerCoreOneClick above), so every reachable group is one of the two here.
  const itemLike = picker.groupId === 'totem' || picker.groupId === 'title';
  // every filter control carries a tooltip.
  const tip = (title, body) => tipAttrs({ title, body, peer: 'picker_filters' });
  const sortTips = {
    construction_pp: ['Sort by Production', 'Highest PP after terrain first.'],
    boost: ['Sort by production boost', 'Highest boost first.'],
    name: ['Sort by name', 'A to Z.'],
  };
  const chips = (workerLike ? [['construction_pp', 'Production'], ['name', 'Name']] : [['boost', 'Production boost'], ['name', 'Name']])
    .map(([id, label]) => `<button type="button" class="picker-chip${picker.sort === id ? ' is-active' : ''} ceres-btn ceres-btn--choice" data-control-density="compact" data-picker-action="sort" data-sort="${id}"${tip(...sortTips[id])}><span class="ceres-btn__check" aria-hidden="true"></span>${label}</button>`).join('');
  // one header bar. The old explanation sentence lives in the Plot change tooltip.
  const occupantPp = replacing ? Number(picker.currentOccupant.current_harvest_pp) : NaN;
  const action = replacing ? `Replace ${picker.currentOccupant.name || 'worker'}${Number.isFinite(occupantPp) ? ` (${num(occupantPp)} PP)` : ''}` : 'Add';
  const filter = picker.filters;
  const [groupIcon, groupTitle] = PICKER_GROUP_META[picker.groupId] || ['🎴', picker.groupLabel];
  const group = { id: picker.groupId, label: picker.groupLabel };
  const context = `${action} · ${row.terrain || 'Plot'} · ${row.worksite_type || 'plot'} · ${editorSlotLabel(group, picker.slotIndex)}`;
  const mobileContext = picker.groupId === 'worker' ? `${replacing ? `Replace ${picker.currentOccupant.name || 'worker'}` : 'Add worker'} · Worker slot ${Number(picker.slotIndex) + 1}` : context;
  const region = pickerRegionHtml(picker, replacing, row);
  const editionOptions = pickerEditionOptionsHtml(picker);
  // Toggle pills keep the checkbox (and its data-picker-filter name) so handlePickerFilter()
  // is unchanged; the box is visually hidden and the pill shows its state.
  const toggle = (key, label, on, body) => `<label class="picker-toggle ceres-btn ceres-btn--choice" data-control-density="compact"${tip(label, body)}><span class="ceres-btn__check" aria-hidden="true"></span><input type="checkbox" data-picker-filter="${key}"${on ? ' checked' : ''}><span>${label}</span></label>`;
  const isWorker = picker.groupId === 'worker';
  // (his words: "Apply the same filter technique to our worker picker
  // module, where we have lotas of filters as well" -- the board filter row's own
  // technique, Variant A): no outside caption on any field -- each keeps its
  // accessible name as a `.sr-only` span inside the wrapping <label> (native-select
  // no-JS case) and as the customSelect trigger's own `aria-label` (read from the
  // rendered <select>'s own `aria-label` attribute by initPickerFilterDropdowns() in
  // picker-actions.js, same mechanism board.js's initFilterDropdowns() uses). Every
  // drop-down's first option/closed state reads "Any <category>"; a chosen rarity or
  // splinter still shows its icon + name (customSelect's existing decorateOption).
  const nameField = `<label class="picker-filter-field"${tip('Search by name', 'Matches any part of the card name.')}><span class="sr-only">Name</span><input type="text" class="ceres-wide-input" data-picker-filter="q" value="${esc(filter.q)}" placeholder="Search name…" aria-label="Search by name"></label>`;
  const workerNameField = `<label class="board-filter-field picker-filter-field"${tip('Search by name', 'Matches any part of the card name.')}><span class="sr-only">Name</span><input type="text" class="ceres-wide-input" data-picker-filter="q" value="${esc(filter.q)}" placeholder="Search name…" aria-label="Search by name"></label>`;
  const rarityField = (label, values) => `<label class="picker-filter-field"${tip(label, `Show only one ${label.toLowerCase()}.`)}><span class="sr-only">${esc(label)}</span><select data-picker-filter="rarity" aria-label="${esc(label)} filter"><option value="">Any ${label.toLowerCase()}</option>${values.map((value) => `<option value="${value}"${filter.rarity === value ? ' selected' : ''}>${esc(value.charAt(0).toUpperCase() + value.slice(1))}</option>`).join('')}</select></label>`;
  // Worker: the plot's splinter icons are the Splinter filter (maintainer, §9); Runi keeps the dropdown.
  const splinterSelect = isWorker ? '' : `<label class="picker-filter-field"${tip('Splinter', 'Show only one splinter.')}><span class="sr-only">Splinter</span><select data-picker-filter="splinter" aria-label="Splinter filter"><option value="">Any splinter</option>${['fire', 'water', 'earth', 'life', 'death', 'dragon', 'neutral'].map((value) => `<option value="${value}"${filter.splinter === value ? ' selected' : ''}>${esc(value.charAt(0).toUpperCase() + value.slice(1))}</option>`).join('')}</select></label>`;
  const abilityField = `<label class="picker-filter-field"${tip('Ability', 'Match any selected Land ability among loaded candidates.')}><span class="sr-only">Ability</span><select data-picker-filter="ability" aria-label="Ability filter" multiple><option value="">All</option><option value="${CB_ANY_ABILITY}"${cbList(filter.ability).includes(CB_ANY_ABILITY) ? ' selected' : ''}>Has an ability</option>${Object.entries(CB_ABILITY_LABELS).map(([code, label]) => `<option value="${code}"${cbList(filter.ability).includes(code) ? ' selected' : ''}>${esc(label)}</option>`).join('')}</select></label>`;
  // no Source filter; the list always shows idle and staked cards.
  const workerFields = isWorker
    ? `${workerNameField}<label class="board-filter-field picker-filter-field"${tip('Min Base PP', 'Only cards with at least this Base PP.')}><span class="sr-only">Min Base PP</span><input type="text" class="ceres-wide-input" data-picker-filter="min_pp" inputmode="decimal" value="${esc(filter.min_pp)}" placeholder="Min PP" aria-label="Min Base PP"></label><label class="board-filter-field picker-filter-field"${tip('Max Base PP', "Only cards with at most this Base PP -- for filling the plot's Base PP cap precisely.")}><span class="sr-only">Max Base PP</span><input type="text" class="ceres-wide-input" data-picker-filter="max_pp" inputmode="decimal" value="${esc(filter.max_pp || '')}" placeholder="Max PP" aria-label="Max Base PP"></label><span class="controls-sep" aria-hidden="true"></span>${rarityField('Rarity', ['common', 'rare', 'epic', 'legendary'])}<label class="picker-filter-field"${tip('Edition', 'Show only one edition.')}><span class="sr-only">Edition</span><select data-picker-filter="edition" aria-label="Edition filter">${editionOptions}</select></label>${abilityField}<span class="controls-sep" aria-hidden="true"></span>${toggle('ready_now', 'Ready only', filter.ready_now === true, "Only cards that can be staked right now -- no cooldown running, not dead in Survival. Cards already on land are hidden unless 'On land' is on.")}${toggle('include_on_land', 'On land', filter.include_on_land === true, 'Also show cards already staked on another plot. Moving one here costs a 72 h cooldown.')}${toggle('land_only', 'Land Cards', filter.land_only === true, 'Only cards with land abilities: the Land set and Visions of Verico.')}${S.CERES_PICKER?.bloodlineSubTypes?.length ? toggle('bloodline_pairs', 'Bloodline pairs', filter.bloodline_pairs, 'Only cards that pair with a Toil and Kin holder on this plot.') : ''}`
    : `${rarityField('Rarity', ['common', 'rare', 'epic', 'legendary'])}${splinterSelect}<label class="picker-filter-field"${tip('Edition', 'Show only one edition.')}><span class="sr-only">Edition</span><select data-picker-filter="edition" aria-label="Edition filter">${editionOptions}</select></label>${nameField}<label class="picker-filter-field"${tip('Min Base PP', 'Only cards with at least this Base PP.')}><span class="sr-only">Min Base PP</span><input type="text" class="ceres-wide-input" data-picker-filter="min_pp" inputmode="decimal" value="${esc(filter.min_pp)}" placeholder="Min PP" aria-label="Min Base PP"></label><label class="picker-filter-field"${tip('Max Base PP', "Only cards with at most this Base PP -- for filling the plot's Base PP cap precisely.")}><span class="sr-only">Max Base PP</span><input type="text" class="ceres-wide-input" data-picker-filter="max_pp" inputmode="decimal" value="${esc(filter.max_pp || '')}" placeholder="Max PP" aria-label="Max Base PP"></label>${toggle('ready_now', 'Ready only', filter.ready_now === true, "Only cards that can be staked right now -- no cooldown running, not dead in Survival. Cards already on land are hidden unless 'On land' is on.")}${toggle('include_on_land', 'On land', filter.include_on_land === true, 'Also show cards already staked on another plot. Moving one here costs a 72 h cooldown.')}`;
  const legacyFields = `${nameField}${rarityField('Rarity', ['common', 'rare', 'epic', 'legendary'])}<label class="picker-filter-field"><span class="sr-only">Splinter</span><select data-picker-filter="splinter" aria-label="Splinter filter"><option value="">Any splinter</option>${['fire', 'water', 'earth', 'life', 'death', 'dragon', 'neutral'].map((value) => `<option value="${value}"${filter.splinter === value ? ' selected' : ''}>${value}</option>`).join('')}</select></label><label class="picker-filter-field"><span class="sr-only">Edition</span><input type="text" class="ceres-wide-input" data-picker-filter="edition" inputmode="numeric" value="${esc(filter.edition)}" placeholder="Any edition" aria-label="Edition"></label><label class="picker-filter-field"><span class="sr-only">Min PP</span><input type="text" class="ceres-wide-input" data-picker-filter="min_pp" inputmode="decimal" value="${esc(filter.min_pp)}" placeholder="Min PP" aria-label="Min PP"></label>`;
  // Totem tier ladder is Common/Rare/Epic/Legendary; Title's is Rare/Epic/Legendary
  // only (no Common title exists -- the internal rules's title-boost ladder). Different
  // asset classes, different value lists -- never merge them.
  const itemFields = `${nameField}${rarityField(picker.groupId === 'totem' ? 'Tier' : 'Rarity', picker.groupId === 'totem' ? ['common', 'rare', 'epic', 'legendary'] : ['rare', 'epic', 'legendary'])}`;
  // worker/Runi cards caption every metric themselves -- no column header row.
  const columns = itemLike && picker.viewMode !== 'grid' ? `<div class="candidate-columns is-item-columns" aria-hidden="true"><span></span><span>${picker.groupId === 'totem' ? 'Totem' : 'Title'}</span><span>Production boost</span></div>` : '';
  const affinity = isWorker ? `<span class="candidate-worker-affinity">${terrainAffinityHtml(row, 'picker', filter.splinter)}</span>` : '';
  const fields = workerLike ? workerFields : itemLike ? itemFields : legacyFields;
  // item 2: splinters, fields, pills and sort all share the one filter row; the
  // title line above holds only the title and Close.
  // the sort chips sit in the title line before Close; the filter row keeps one line.
  // a quiet "Clear filters" button, visible only while a filter is active
  // (picker-actions.js's syncPickerFilterChrome() toggles `hidden` after every
  // filter change); resets the fields above -- never Sort, never the worker's
  // splinter-icon badges/remembered terrain groups (/, frozen).
  const clearFiltersHtml = `<button type="button" class="picker-clear-filters ceres-btn ceres-btn--secondary" data-control-density="compact" data-picker-action="clear-picker-filters" aria-label="Clear picker filters"${pickerFiltersActive(picker) ? '' : ' hidden'}>Clear filters</button>`;
  const nextView = picker.viewMode === 'grid' ? 'list' : 'grid';
  const viewAction = nextView === 'grid' ? 'Use card grid view' : 'Use list view';
  const viewSwitchHtml = `<button type="button" class="picker-view-switch hdr-action ceres-btn ceres-btn--choice" data-control-density="compact" data-control-mode="icon" data-picker-action="toggle-view" aria-pressed="${picker.viewMode === 'grid' ? 'true' : 'false'}" ${tip(viewAction, nextView === 'grid' ? 'Show workers and assets as cards.' : 'Show workers and assets as a list.')}><span class="ceres-btn__check" aria-hidden="true"></span>${PICKER_VIEW_ICON}<span class="sr-only">Card grid view</span></button>`;
  // show actions without filtering the cards themselves.
  const upgradeToggleHtml = isWorker ? `<button type="button" class="picker-toggle ceres-btn ceres-btn--choice${filter.show_upgrade ? ' is-active' : ''}" data-control-density="compact" data-picker-action="toggle-upgrade" aria-pressed="${filter.show_upgrade === true}"${tip('Upgrade buttons', 'Show an Upgrade button under every card that can be upgraded.')}><span class="ceres-btn__check" aria-hidden="true"></span>Upgrade</button>` : '';
  const filtersHtml = `<div class="picker-filters picker-bar-filters">${affinity}${isWorker ? '<span class="controls-sep" aria-hidden="true"></span>' : ''}${fields}${clearFiltersHtml}${viewSwitchHtml}${upgradeToggleHtml}</div>`;
  const sortHtml = `<div class="picker-sort-chips" role="group" aria-label="Sort">${chips}</div>`;
  // fill a plot without closing the picker -- after a card is staged the picker moves to the next empty slot.
  // the switch also shows on the Runi, Totem and Title pickers the fill run opens.
  const keepOpenHtml = isWorker || picker.chain ? `<label class="picker-toggle picker-keep-open fill-action ceres-btn ceres-btn--choice" data-control-density="compact" data-control-case="signal"${tip('Fill next slot', 'Keep this picker open and move to the next empty slot after each card, skipping categories with nothing to add.')}><span class="ceres-btn__check" aria-hidden="true"></span><input type="checkbox" data-picker-filter="keep_open"${filter.keep_open || picker.chain ? ' checked' : ''}>${FILL_TRAY_ICON}<span>Fill next slot</span></label>` : '';
  const candidateListAttrs = picker.viewMode === 'grid' ? ' class="candidate-list is-card-grid" data-picker-view="grid"' : ' class="candidate-list"';
  return `<div class="candidate-visible-slice"><section class="inline-picker${picker.openedAt && performance.now() - picker.openedAt < 250 ? ' is-opening' : ''}" data-picker="1" data-picker-group="${esc(picker.groupId)}"><header class="picker-bar"><div class="picker-bar-title"><span class="picker-group-icon" aria-hidden="true">${groupIcon}</span><span class="picker-group-title">${esc(groupTitle)}</span><span class="picker-context" data-mobile-context="${esc(mobileContext)}">${esc(context)}</span>${replacing && !picker.mobile && picker.groupId === 'worker' ? upgradeActionHtml(picker.currentOccupant, 'occupant', true) : ''}</div><div class="picker-bar-end">${keepOpenHtml}${sortHtml}<span class="picker-bar-sep" aria-hidden="true"></span><button type="button" class="picker-close chevron-close" data-picker-action="close" aria-label="Close picker" data-tip="Close">${CHEVRON_CLOSE_ICON}</button></div>${filtersHtml}</header>${columns}<div${candidateListAttrs}>${region.rows}</div><footer class="picker-footer">${region.footer}</footer></section></div>`;
}
