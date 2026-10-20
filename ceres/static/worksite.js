import { balancesChanged } from './balances-bus.js';
import { COMMIT_ICON } from './button-icons.js';
import { walletSignLabelHtml, walletReady, walletName, walletControlsHtml } from './wallet.js';
import { retryBackgroundLayers, worksiteTileArtPath } from './art.js';
import { requestBroadcastThroughGate, requestSignatureThroughGate } from './broadcast-gate.js';
import { BOARD, BOARD_GENERATION, CURRENT_PLAYER, catalogNotice, noticeMessage, playerQueryAppend, renderNotices } from './board.js';
import { esc, fmtSpsPerPp, formatPlotLabel, num } from './format.js';
import { refreshPlotHarvest } from './harvest-state.js';
import { decorateResourceMentions, resourceIconHtml, territoryCellHtml, tooltipIconPath } from './icons.js';
import { operationSignButtonHtml, renderOperationReview, setConfirmationBannerState } from './operations.js';
import { handleSlotEditorClick } from './picker-actions.js';
import { plotPoweredAfterStage, stagedWorkersForPlot } from './hazard.js';
import { refreshBoardTotals, updateMountedRow } from './rows.js';
import { applyStageToRow, reconcileCapPrediction, stageToast, stagedWorksiteBuild, stageWorksiteBuild, stagedEntriesForRow } from './staging.js';
import { loadSpsRate, spsRateNow } from './sps-rate.js';
import { ASSET_BASE, S } from './state.js';
import { CURRENT_ROWS } from './table.js';
import { tipAttrs, tipIcon } from './tooltip.js';

// (v0.9.352): the worksite modal is a house modal -- `.harvest-overlay` > `.harvest-panel`
// (the Changes/Fill pattern): a header with the plot badge, a scrolling body of `.hm-card`s (the
// tiles, then ONE "Signing" card with the compact operation review), and a fixed footer whose note
// carries the state and whose primary button signs. Picking a tile builds the preview at once (one
// step, like the Changes modal). Signing closes the modal the moment Keychain accepts and the
// transaction pill reports from there (/). The keyboard contract is unchanged: Escape
// closes, Tab wraps within the panel, focus returns to the trigger on close. Broadcasts go through
// `requestBroadcastThroughGate` resolves after submission; Keychain signatures are queued separately.
let WORKSITE_MODAL_RETURN_FOCUS = null;
// The currently-selected (non-current) tile's target, or null -- reset on every open.
export let WORKSITE_MODAL_SELECTED = null;
let WORKSITE_MODAL_TC = false; // the "build instantly" opt-in; reset on every open, never sticky
let WORKSITE_PREVIEW_TOKEN = 0; // a preview answer for an older pick, opt-in or session is dropped
let WORKSITE_VERIFY_TIMER = null;
const WORKSITE_VERIFY_MAX_ATTEMPTS = 6;
const WORKSITE_VERIFY_INTERVAL_MS = 2000;
// One title verb per body state (R-23 §3.7).
const WORKSITE_MODAL_TITLE = { choose: 'Change worksite', refused: 'Cannot change worksite', completion: 'Complete worksite' };
const X_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';

function keychainReady() {
  return walletReady();
}

function worksiteModalRow(overlay) {
  return BOARD?.plots?.find((plot) => plot.deed_uid === overlay?.dataset.deedUid) || null;
}

function ensureWorksiteModalOverlay() {
  if (document.getElementById('worksite-modal-overlay')) return;
  const overlay = document.createElement('div');
  overlay.id = 'worksite-modal-overlay';
  overlay.className = 'harvest-overlay worksite-overlay';
  overlay.hidden = true;
  // The skeleton is persistent; only the body, the title/subtitle and the footer's note and sign
  // button are re-rendered per open. `.worksite-modal-panel`/`.worksite-modal-body` stay as the
  // guard suites' handles.
  overlay.innerHTML = `<div class="harvest-panel changes-panel worksite-panel worksite-modal-panel" role="dialog" aria-modal="true" aria-labelledby="worksite-modal-title">`
    + `<header class="hm-headr"><div><h2 id="worksite-modal-title">Change worksite</h2><p class="worksite-modal-sub"></p></div><button type="button" class="harvest-x ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" data-worksite-action="close" aria-label="Close">${X_ICON}</button></header>`
    + '<div class="hm-body"><div class="worksite-modal-body"></div></div>'
    + '<footer class="hm-foot"><span class="hm-note" data-worksite-note></span><span class="hm-btns"><span class="worksite-modal-sign" data-worksite-sign></span></span></footer></div>';
  overlay.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closeWorksiteModal(); return; }
    if (event.key !== 'Tab') return;
    const focusable = [...overlay.querySelectorAll('button:not([disabled]), input:not([disabled])')];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  overlay.addEventListener('click', (event) => { if (event.target === overlay) closeWorksiteModal(); });
  // Created lazily (on first open), never eagerly: an eager second permanent `[role=dialog]`
  // node trips the "at most one dialog node" guard. Click delegation is wired here, once.
  overlay.addEventListener('click', handleSlotEditorClick);
  // The Time Crystal opt-in changes the operation itself, so a preview already built is rebuilt.
  overlay.addEventListener('change', (event) => {
    if (!(event.target instanceof HTMLInputElement) || !event.target.closest('.worksite-modal-tc')) return;
    WORKSITE_MODAL_TC = event.target.checked;
    const row = worksiteModalRow(overlay);
    if (row && WORKSITE_MODAL_SELECTED) requestWorksitePreview(row, WORKSITE_MODAL_SELECTED, WORKSITE_MODAL_TC ? TC_SWAP_COST : 0);
  });
  document.body.appendChild(overlay);
}

// A tile's hero rate plus its sub-line -- the element the whole modal exists
// to serve (plan §5.2). Takes the whole TILE, not just `tile.rate`: the
// sub-line differs for the current tile (its number is read straight off
// `staking_facts.total_work_per_hour`, so it says "measured now" rather than
// showing a second projected figure).
// SPS's hero is its `projected_harvest_pp` -- the plot's projected MINING PP
// not a hole. That figure is exact, dstb-corrected (an `occupied` plot doubles
// it, same 2x trap as every other tile) and is the ONE quantity that actually
// determines the plot's pool share, so showing it is not a fabricated
// point-estimate the way a made-up SPS/hr number would be; only the true
// unknown (SPS/hr itself) stays an explicit null, carried in the sub-line and
// the `worksite_swap_sps_pool_share` advisory below the tile. This also means
// SPS needs no special-cased layout -- one hero+sub-line shape, one exception
// expressed as a label, never a different tile shape.
// Every non-current, non-SPS tile's sub-line is its own `projected_harvest_pp`
// the stage-two figure the hero rate is derived from. Printing it is what
// makes the tile read as arithmetic instead of an oracle, and it is also what
// makes a dstb-doubled/halved hero number legible next to the bonus-gained
// bonus-lost note worksiteTileHtml() attaches below the tile.
// projected SPS/hr = the tile's projected mining PP x the current pool rate
// (sps-rate.js). Null until a rate is known -- the tile then keeps its pool-share face.
function spsTileHourly(tile) {
  const rate = tile?.rate;
  const spsRate = rate?.kind === 'pool_share' ? spsRateNow() : null;
  const harvestPp = rate?.projected_harvest_pp;
  if (!spsRate || harvestPp === null || harvestPp === undefined || !Number.isFinite(Number(harvestPp))) return null;
  return Number(harvestPp) * spsRate.rate;
}

function spsRateTipBody(spsRate) {
  const where = spsRate.source === 'own' ? `your ${spsRate.plots === 1 ? 'SPS plot' : `${spsRate.plots} SPS plots`}` : "another player's SPS plots";
  return `Estimate for this plot at today's shared-pool rate: ${fmtSpsPerPp(spsRate.rate)} SPS per PP per hour, measured on ${where}. The rate changes during the day as players start, stop or change SPS plots.`;
}

// a never-cleared plot's tiles start its FIRST clear (PP-scaled, no 24 h, no Time Crystal shortcut).
function isFirstClear(row) {
  return Array.isArray(row?.swap_targets) && row.swap_targets.some((tile) => tile.first_clear);
}

export function keptStagedWorksiteTarget(row) {
  const target = stagedWorksiteBuild(row?.deed_uid)?.target || null;
  return target && (row?.swap_targets || []).some((tile) => tile.target === target && !tile.is_current) ? target : null;
}

// (his ruling: "we can stage ANYTHING so we can stage a worksite without a power
// core, we simply cannot sign it ... we can offer a simple power staging but we dont have
// to force an instant signing"): whether this plot WOULD pass `swap_eligibility` once its
// staged assets apply. H1 requires power plus a worker for existing-worksite changes; a
// first clear requires power and permits zero workers with a warning. Never a claim that it is eligible NOW. `row.swap_eligibility.eligible === false`
// is the live refusal; this only decides whether the modal offers "Stage this build"
// instead of that refusal. The server re-derives the first-clear transaction from live facts
// plus the submitted staged overlay at checkout (checkout.js `confirmCheckoutWithWorksites`).
export function worksiteEligibleAfterStage(row) {
  if (!row) return false;
  if (!plotPoweredAfterStage(row)) return false;
  if (isFirstClear(row)) return true; // a first clear needs power only; it progresses once workers are seated
  const liveWorkers = Number(row.capacity?.worker_count) > 0;
  return liveWorkers || stagedWorkersForPlot(row);
}

// What the plot still lacks, live or staged, before a staged build can be signed ().
export function worksiteMissingForSigning(row) {
  const missing = [];
  if (!plotPoweredAfterStage(row)) missing.push('power');
  if (!isFirstClear(row) && !(Number(row?.capacity?.worker_count) > 0 || stagedWorkersForPlot(row))) missing.push('a worker');
  return missing;
}

// True while the plot is not yet live-eligible (H1). (maintainer: "it still does not work!!! The power core is
// added. And the button of worksites still doesnt let me select a worksite and stage it."; his ruling : "We
// can stage ANYTHING"): staging the build is always allowed then. First clears can combine
// staged power/workers; existing changes still wait for the fresh live eligibility read.
export function worksiteStageOnly(row) {
  if (isFirstClear(row) && stagedEntriesForRow(row.deed_uid).length) return true;
  return !!(row?.swap_eligibility && row.swap_eligibility.eligible === false);
}

function clearDurationText(hours) {
  return hours < 48 ? `${Math.ceil(hours)} h` : `${num(hours / 24, 1)} days`;
}

function firstClearSubText(tile) {
  const req = Number(tile.clear_pp_required);
  const need = Number.isFinite(req) && req > 0 ? `${num(req / 1e6, 2)}M PP to clear` : 'first clear';
  const hours = Number(tile.time_hours);
  return Number.isFinite(hours) && hours > 0 ? `${need} · about ${clearDurationText(hours)}` : `${need} · no workers, no progress`;
}

function firstClearNoteHtml(row) {
  const refusal = row.swap_eligibility && row.swap_eligibility.eligible === false ? renderNotices([row.swap_eligibility.notice], 'op-state-notice') : '';
  return `<p class="ws-first-clear-note">First clear: the plot is cleared with the workers' base PP before the worksite produces.</p>${refusal}`;
}

function worksiteRateHtml(tile) {
  const rate = tile?.rate;
  if (!rate) return '';
  const harvestPp = rate.projected_harvest_pp;
  const isSps = rate.kind === 'pool_share';
  // with a measured pool rate the SPS tile shows SPS/hr like every other tile.
  const spsHourly = spsTileHourly(tile);
  const hero = spsHourly !== null
    ? num(spsHourly, Math.abs(spsHourly) < 1 ? 4 : 2)
    : isSps
    ? (harvestPp === null || harvestPp === undefined ? '—' : num(harvestPp, 0))
    : (rate.value === null || rate.value === undefined ? '—' : num(rate.value, Math.abs(rate.value) < 1 ? 4 : 2));
  const unit = spsHourly !== null ? 'SPS/hr' : isSps ? 'PP mining SPS' : (rate.unit || '');
  let sub = '';
  if (tile.is_current) sub = 'measured now';
  else if (spsHourly !== null) sub = `${num(harvestPp, 0)} Total PP · estimate`;
  else if (isSps) sub = 'pool share — no fixed rate';
  else if (harvestPp !== null && harvestPp !== undefined) sub = `${num(harvestPp, 0)} Total PP`;
  if (tile.first_clear) sub = firstClearSubText(tile);
  return `<div class="worksite-tile-rate">${hero} <span class="worksite-tile-rate-unit">${esc(unit)}</span></div>${sub ? `<div class="worksite-tile-rate-sub">${esc(sub)}</div>` : ''}`;
}

// Official worksite BUILDING art for one tile. The modal uses the verified public
// CDN's original 2829x1080 canvas because the locally normalised asset set is
// deployment-only and gitignored; the canvas keeps the game's left registration.
function worksiteTileArtHtml(target, terrain, overlay = '') {
  const path = worksiteTileArtPath(target, terrain);
  if (!path) return `<div class="worksite-tile-art is-missing">${overlay}</div>`;
  return `<div class="worksite-tile-art" data-bg-retry="${esc(path)}" style="background-image: url('${path}');">${overlay}</div>`;
}

// the +100% land-type bonus pill, on the art of every tile that gets it here
// (`tile.land_bonus` is the plot's status, set server-side by the game's own rule).
function worksiteLandBonusHtml(tile) {
  const pills = [landBonusPill(tile), abilityBonusPill(tile)].filter(Boolean);
  return pills.length ? `<span class="worksite-tile-pills">${pills.join('')}</span>` : '';
}

// a worker's resource-match ability (e.g. Undying Miner's Bountiful Aura) that
// this worksite would switch on, or keeps on, for the whole plot.
function abilityBonusPill(tile) {
  const bonus = tile.ability_bonus;
  if (!bonus || !(bonus.rate > 0)) return '';
  const pct = `+${Math.round(bonus.rate * 100)}%`;
  const tip = {
    title: 'Worker ability bonus',
    body: `${bonus.holder} on this plot ${tile.is_current ? 'boosts' : 'would boost'} ${tile.worksite_name} production by ${pct} for every worker. Already counted in the rate below.`,
  };
  return `<span class="worksite-tile-land-bonus is-ability"${tipAttrs(tip)}>${pct} ${esc(bonus.holder)}</span>`;
}

function landBonusPill(tile) {
  if (!tile.land_bonus) return '';
  const status = tile.land_bonus.charAt(0).toUpperCase() + tile.land_bonus.slice(1);
  const tip = {
    title: 'Land-type bonus',
    body: `${status} plot: ${tile.worksite_name} ${tile.is_current ? 'gets' : 'would get'} +100% production here. Already counted in the rate below.`,
  };
  // the game's own deed-overview boost icon (hammer.svg) marks the land-type bonus.
  return `<span class="worksite-tile-land-bonus"${tipAttrs(tip)}><img class="worksite-tile-pill-icon" src="${ASSET_BASE}/land/hammer.svg" alt="" aria-hidden="true">+100% ${esc(status)}</span>`;
}

// Mirrors `ceres/worksite_swap.py`'s TC_COST -- the flat cost to convert an
// EXISTING worksite. Used only by the footer's standalone TC line (every tile
// already carries its own `tc_cost` from the server); kept as one named constant
// rather than a literal `50` so the two can't silently drift.
export const TC_SWAP_COST = 50;

// Factual flavour copy naming what each worksite actually does -- not
// fabricated game lore (this repo is bound for public release and lifting a
// third party's marketing prose would be a needless licensing question for a
// line of decoration), just the internal rules's own primary-sourced facts about each
// resource, written out rather than a claim about numbers -- the numbers stay
// in the rate block, which is the actual decision input.
// Lives in the tile's HOVER TOOLTIP (worksiteTileTooltipText() below), not
// on the tile face -- the modal must fit an entire plot's worksite grid on
// one FullHD screen with no scrolling, and a 3-4 sentence paragraph per tile
// was the largest single contributor to needing one.
// Gate document #109-#115 (): each tile says only what its own face does not
// the rate, the one fact that sets it apart, and where it is native -- with the icon of
// every resource and terrain it names (). Lines are built lazily so the icon paths
// resolve at render time.
const resIcon = (resource) => tipIcon(tooltipIconPath('resource', resource));
const terrainList = (terrains) => `Native: ${terrains.map((terrain) => `${tipIcon(tooltipIconPath('terrain', terrain))}${terrain}`).join(' · ')}`;
const WORKSITE_FLAVOUR = {
  grain: { lines: () => [`Rate 1 ${resIcon('grain')}Grain/hour per 50 PP`, 'Every worker eats Grain — run out and harvesting stalls.', terrainList(['Plains', 'River', 'Bog', 'Lake'])] },
  wood: { lines: () => [`Rate 1 ${resIcon('wood')}Wood/hour per 200 PP`, terrainList(['Swamp', 'Forest', 'Tundra', 'Jungle'])] },
  stone: { lines: () => [`Rate 1 ${resIcon('stone')}Stone/hour per 500 PP`, terrainList(['Desert', 'Canyon', 'Hills'])] },
  iron: { lines: () => [`Rate 1 ${resIcon('iron')}Iron/hour per 2,000 PP`, terrainList(['Mountain', 'Badlands', 'Caldera'])] },
  research: { lines: () => [`The only worksite that finds Totem Fragments`, 'Any terrain · eats all four base resources'] },
  sps: { lines: () => [`A share of the monthly ${resIcon('sps')}SPS pool, split by network mining PP — not a fixed rate`, 'Any terrain'] },
  aura: { lines: () => [`${resIcon('aura')}Aura for Artisan crafting`, 'Any terrain'] },
};

const statusIcon = (status) => tipIcon(tooltipIconPath('type', String(status || '').toLowerCase()));
const WORKSITE_EXTRA_LINES = {
  bonusGained: ({ status }) => `+100% ${statusIcon(status)}${status} land bonus, already in the rate`,
  bonusLost: ({ status, current_worksite }) => `Leaving ${current_worksite} loses its +100% ${statusIcon(status)}${status} land bonus`,
  permitUnknown: 'Needs an Aura Lab Permit — your balance is unknown',
  permitZero: (value) => `Needs an Aura Lab Permit — you hold ${value}`,
  permitHeld: (value) => `Needs an Aura Lab Permit — you hold ${value}`,
};

// The "Needs" row specifically: the resource ICONS `resourceIconHtml()` draws
// for the board's compact Worksite column, not the plain lowercase text join
// this used to be -- a name join reads as a wall of text once a tile needs
// all four base resources (Research/Shard Mine/Aura Lab), and this is
// exactly the row the maintainer flagged as needing real icons. Each icon still
// carries its own tooltip (from resourceIconHtml) so the row stays legible
// even without colour, and the surrounding <b> keeps right-alignment.
// each input icon explains what it costs -- the published rate and what that
// comes to on THIS plot. Wood/Stone/Iron: 0.005/0.002/0.0005 per PP per hour (Research
// Shard Mine; Aura Lab has the same inputs by the Aura Lab FAQ's own grouping). Grain
// the workers' food, 1 per 100 Base PP per hour, boosts excluded -- the same for every
// worksite, so the plot's own food cost is the figure.
const INPUT_RATE_PER_PP = { wood: 0.005, stone: 0.002, iron: 0.0005 };
function needsCostTip(resource, tile, row) {
  const label = resource.charAt(0).toUpperCase() + resource.slice(1);
  const icon = tooltipIconPath('resource', resource);
  if (resource === 'grain') {
    const food = Number(row?.grain_cost_per_hour);
    const onPlot = Number.isFinite(food) ? ` · ${num(food, food < 10 ? 2 : 0)} Grain/hr on this plot` : '';
    return { title: 'Grain cost', icon, body: `Workers eat 1 ${tipIcon(icon)}Grain per 100 Base PP per hour (boosts don't count)${onPlot}. Paid at each harvest.` };
  }
  const rate = INPUT_RATE_PER_PP[resource];
  if (!rate) return { title: `${label} cost`, icon, body: `Consumed every hour while this worksite harvests.` };
  const pp = Number(tile.is_current ? row?.harvest_pp : tile.rate?.projected_harvest_pp);
  const onPlot = Number.isFinite(pp) && pp > 0 ? ` · ${num(pp * rate, pp * rate < 10 ? 2 : 0)} ${label}/hr on this plot (${num(pp, 0)} Total PP)` : '';
  return { title: `${label} cost`, icon, body: `${rate} ${tipIcon(icon)}${label} per Total PP per hour${onPlot}. Paid at each harvest.` };
}

function worksiteTileNeedsRow(needsInputs, tile, row) {
  const icons = (needsInputs || []).map((r) => resourceIconHtml(r, null, needsCostTip(String(r).toLowerCase(), tile, row))).join('');
  return `<div class="worksite-tile-data-row"${tipAttrs({ title: 'Needs', body: 'Resources this worksite eats every hour to keep harvesting.', peer: 'worksite_needs' })}><span>Needs</span><b class="worksite-tile-needs-icons">${icons}</b></div>`;
}

// Everything that used to sit ON the tile face as its own block -- flavour
// paragraph, the SPS pool-share advisory, the deed_status_token_boost delta
// note, and the Aura permit note -- folded into ONE hover tooltip (point 5/7
// worksite-modal rework). Kept compact and non-duplicative with the confirm
// step (worksiteReviewHtml()): the auto-harvest warning and the 24h build
// duration live there, only once a target is actually picked, never here.
function worksiteTileTooltipText(tile) {
  const flavour = WORKSITE_FLAVOUR[tile.target];
  const parts = flavour ? [...flavour.lines()] : [];
  // Server-formatted, per-tile: whether swapping here gains or costs the
  // plot's own deed_status_token_boost land-type bonus (board.py attaches this
  // only when `dstb_delta` is non-zero, so it's the exception, not a line on
  // every tile) -- explains a hero number that jumped or dropped for a reason
  // other than the resource's own conversion rate.
  if (tile.dstb_notice?.code === 'worksite_swap_bonus_gained') {
    parts.push(WORKSITE_EXTRA_LINES.bonusGained(tile.dstb_notice.context || {}));
  } else if (tile.dstb_notice?.code === 'worksite_swap_bonus_lost') {
    parts.push(WORKSITE_EXTRA_LINES.bonusLost(tile.dstb_notice.context || {}));
  } else if (tile.dstb_notice) {
    parts.push(noticeMessage(tile.dstb_notice));
  }
  // Current tile: no permit line even for Aura -- it's already staffed, so
  // the permit question is moot (§3.2/§3.6).
  if (!tile.is_current && tile.requires_permit) parts.push(worksitePermitLineText(BOARD?.permit_balance));
  return { title: tile.worksite_name, items: parts.filter(Boolean).map((effect) => ({ name: '', effect })), peer: 'worksite_details' };
}

function worksiteTileHtml(tile, terrain, row = null) {
  const classes = ['worksite-tile'];
  if (tile.is_current) classes.push('is-current');
  const rows = [worksiteTileNeedsRow(tile.needs_inputs, tile, row)];
  const rateTip = tile.is_current
    ? { title: 'Production rate', body: 'Measured from the game right now, on this plot.', peer: 'worksite_rate' }
    : spsTileHourly(tile) !== null
    ? { title: 'Production if built here', body: spsRateTipBody(spsRateNow()), peer: 'worksite_rate' }
    : { title: 'Production if built here', body: 'Projected for this plot · land bonus included when it applies.', peer: 'worksite_rate' };
  const tileTip = worksiteTileTooltipText(tile);
  const inner = `${tile.is_current ? '<div class="worksite-tile-badge">Current</div>' : '<div class="worksite-tile-check" aria-hidden="true">✓</div>'}
    <div class="worksite-tile-name">${esc(tile.worksite_name)}</div>
    ${worksiteTileArtHtml(tile.target, terrain, worksiteLandBonusHtml(tile))}
    <div class="worksite-tile-rate-block"${tipAttrs(rateTip)}>${worksiteRateHtml(tile)}</div>
    <div class="worksite-tile-data">${rows.join('')}</div>`;
  // The current tile is the BASELINE, not an option -- rendered as a
  // non-interactive div (`aria-disabled`, no click handler), never a disabled
  // <button>, per the plan's §5.9 distinction between "not an option" and
  // "unavailable option". Every OTHER tile is a real, keyboard-reachable
  // <button> ( worksite-modal follow-up, maintainer: "Selecting another
  // worksite is not possible, zero clickability") -- clicking or
  // Enter/Space-ing it selects that target and enables Confirm; it never
  // broadcasts anything itself (Phase 4 owns the actual op). The tooltip
  // attributes go on this same outer element (never a wrapping div) so
  // hover/focus and click/select share one target, and so `[data-tip]`'s
  // closest()-based delegation still resolves the RATE block's own, more
  // specific tooltip first for anyone hovering that inner area.
  if (tile.is_current) {
    return `<div class="${classes.join(' ')}" data-worksite-target="${esc(tile.target)}" aria-disabled="true"${tipAttrs(tileTip)}>${inner}</div>`;
  }
  classes.push('worksite-tile-option');
  return `<button type="button" class="${classes.join(' ')}" data-worksite-action="select-tile" data-worksite-target="${esc(tile.target)}" aria-pressed="false"${tipAttrs(tileTip)}>${inner}</button>`;
}

function worksiteModalState(row) {
  if (row.worksite_completion) return 'completion';
  // a plot that would pass H1 once its own staged power/worker moves confirm stays
  // "choose" -- tiles are clickable and the footer offers to stage the build, never the
  // dead-end refusal (the old "redundant pathway": tiles you could click but no sign button).
  if (row.swap_eligibility && row.swap_eligibility.eligible === false && !worksiteStageOnly(row)) return 'refused';
  return 'choose';
}

function worksitePlotBadgeHtml(row) {
  const { cls } = territoryCellHtml(row.territory, row.deed_uid);
  return `<span class="ch-plot-badge ${cls}" style="cursor:default">${esc(formatPlotLabel(row.plot))}</span>`;
}

function worksiteSubtitleText(row) {
  const worksite = String(row.worksite_type || '').trim();
  return [row.terrain, worksite || 'No worksite'].filter(Boolean).join(' · ');
}

function worksiteModalBodyHtml(row) {
  const completion = row.worksite_completion;
  if (completion) {
    const notice = catalogNotice('worksite_completion_ready', { worksite: completion.to_label, grain_cost: completion.grain_cost });
    return `<div class="hm-card"><section class="worksite-completion"><div class="worksite-completion-art">${worksiteTileArtHtml(completion.to, row.terrain)}</div><p class="worksite-completion-lead">${decorateResourceMentions(esc(noticeMessage(notice)))}</p></section></div>${worksiteSigningCardHtml(row)}`;
  }
  const eligibility = row.swap_eligibility;
  const stageOnly = worksiteStageOnly(row);
  if (eligibility && eligibility.eligible === false && !isFirstClear(row) && !stageOnly) {
    // H1 -- the refusal renders IN PLACE OF the tile row, never a dead greyed-out control.
    return `<div class="hm-card worksite-modal-refusal">${renderNotices([eligibility.notice], 'op-state-notice')}</div>`;
  }
  const tiles = (row.swap_targets || []).map((tile) => worksiteTileHtml(tile, row.terrain, row)).join('');
  const tail = stageOnly ? '' : isFirstClear(row) ? firstClearNoteHtml(row) : worksiteTcLineHtml();
  const signingHtml = !WORKSITE_MODAL_SELECTED ? ''
    : stageOnly ? worksiteStageNoticeHtml(row)
    : worksiteSigningCardHtml(row);
  // while staging is possible the red "no power ... then try again" refusal is not shown -- the staging card
  // (worksiteStageNoticeHtml) explains the Power Core way instead.
  return `<div class="hm-card ws-tiles"><div class="worksite-tile-grid">${tiles}</div>${tail}</div>${signingHtml}`;
}

// The "build instantly" opt-in, under the tiles. Disabled ONLY when the balance is known and
// insufficient -- an unknown balance stays enabled with a "balance: unknown" note.
function worksiteTcLineHtml() {
  const balance = BOARD?.tc_balance;
  const short = !!(balance && balance.value !== null && balance.value !== undefined && balance.value < TC_SWAP_COST);
  return `<label class="hm-check worksite-modal-tc${short ? ' is-off' : ''}"${tipAttrs({ title: 'Build instantly', body: 'Skips the 24-hour wait using Time Crystals, spent only if you sign.', peer: 'worksite_controls' })}><input type="checkbox" aria-label="Build instantly with Time Crystals"${WORKSITE_MODAL_TC ? ' checked' : ''}${short ? ' disabled' : ''}><span class="worksite-modal-tc-text">${esc(worksiteTcLineText(balance))}</span></label>`;
}

// ONE "Signing" card, like the Changes modal's -- the compact operation review without a
// button of its own (the footer signs), the server's notices (the auto-harvest warning, the Aura
// permit) as notice lines inside it, and the exact Hive operations behind a closed disclosure.
function worksiteSigningCardHtml(row) {
  const review = S.WORKSITE_MODAL_PREVIEW;
  const reviewHtml = renderOperationReview({ deed_uid: row.deed_uid }, review, { compact: true, footerSign: true, worksiteCompletion: Boolean(row.worksite_completion) });
  const noticeList = Array.isArray(review?.payload?.notices)
    ? review.payload.notices.filter((notice) => !row.worksite_completion || notice?.code !== 'worksite_completion_ready') : [];
  const notices = Array.isArray(noticeList) && noticeList.length ? `<div class="ws-notices">${renderNotices(noticeList, 'ws-notice')}</div>` : '';
  const marker = '<details class="op-c-details">';
  const body = notices && reviewHtml.includes(marker) ? reviewHtml.replace(marker, `${notices}${marker}`) : `${reviewHtml}${notices}`;
  return `<div class="hm-card ws-signing"><h3 class="hm-sec-h">Signing</h3>${body}</div>`;
}

// Existing-worksite changes still wait for live power/workers. first clears instead
// preview their staged Power Core/workers together with construction as one transaction.
function worksiteStageNoticeHtml(row) {
  const target = (row.swap_targets || []).find((tile) => tile.target === WORKSITE_MODAL_SELECTED);
  const name = target?.worksite_name || 'This worksite';
  // the card says
  // what the plot still needs and what each footer button does now (/).
  const missing = worksiteMissingForSigning(row);
  let lines;
  if (isFirstClear(row)) {
    lines = missing.length
      ? [`This plot has no ${missing.join(' and ')} yet.`,
        'Stage a Power Core first — use the Power Core button in the plot’s harvest box.',
        `You can already stage ${name} now; Changes keeps it until the Power Core can sign with it.`]
      : [`${name} is ready to sign with this plot’s staged changes.`,
        'Stage change adds it to Changes; the Power Core, workers, and first worksite will be one transaction.',
        'Signing sends one request for that one transaction.'];
    if (!(Number(row?.capacity?.worker_count) > 0 || stagedWorkersForPlot(row))) {
      lines.push('The build will not progress until workers are staked.');
    }
  } else {
    lines = missing.length
      ? [`This plot has no ${missing.join(' and ')} yet.`,
        missing.includes('power') ? 'Stage a Power Core first — use the Power Core button in the plot’s harvest box.' : 'Stage a worker on the plot first.',
        `You can already stage ${name} now: it waits in Changes and signs after the Power Core.`]
      : [`${name} can be built once your staged Power Core is on chain.`,
        'Stage change adds it to Changes — it signs later with your other staged changes: the Power Core first, then this build.',
        'Signing applies the Power Core first, then this build once the chain has confirmed it.'];
  }
  return `<div class="hm-card ws-signing"><h3 class="hm-sec-h">How this build signs</h3><p class="ws-stage-note">${lines.map((line) => esc(line)).join(' ')}</p></div>`;
}

function worksiteCostText(row, payload) {
  if (payload.kind === 'worksite_completion') {
    const grain = Number(row.worksite_completion?.grain_cost);
    return Number.isFinite(grain) ? `pays ${num(grain, grain < 10 ? 2 : 0)} Grain` : 'pays the Grain cost';
  }
  if (payload.first_clear) return 'no cost, first clear (time depends on base PP)';
  return payload.time_crystals ? `${TC_SWAP_COST} Time Crystals, starts immediately` : 'no cost, 24-hour build';
}

// The footer note: one line for the state the modal is in.
function worksiteFooterNote(row, review) {
  const state = worksiteModalState(row);
  if (state === 'refused') return 'This worksite cannot be changed right now';
  if (state === 'choose' && !WORKSITE_MODAL_SELECTED) return 'Select a worksite';
  if (worksiteStageOnly(row)) {
    return worksiteEligibleAfterStage(row)
      ? isFirstClear(row)
        ? 'Stage it for later, or sign now: Power Core, workers, and first worksite use one transaction.'
        : 'Stage it for later, or sign now: the staged Power Core first, then this build.'
      : `Stage it for later -- it can be signed once this plot has ${worksiteMissingForSigning(row).join(' and ') || 'power'}.`;
  }
  if (!review || review.loading) return 'Preparing the transaction…';
  if (review.error) return review.error;
  if (!keychainReady()) return 'Wallet not connected.';
  const chunks = review.payload?.chunks || [];
  const ops = chunks.reduce((sum, chunk) => sum + (Array.isArray(chunk.operations) ? chunk.operations.length : 0), 0);
  return `${ops} operation${ops === 1 ? '' : 's'} in ${chunks.length} transaction${chunks.length === 1 ? '' : 's'} · ${worksiteCostText(row, review.payload)}`;
}

// The footer's sign button is operationSignButtonHtml() in the modal's global button style
// with the worksite click contract: 'broadcast'
// signs, a failed preview's "Try again" rebuilds it ('confirm' / 'complete').
// a stage-only pick gets its own plain button (`data-worksite-action="stage"`)
// there is no preview to arm it from, and it never touches Keychain.
// (maintainer: "there is only a close button. We need a stage change and sign with keychain button. Sign does it
// immediately, stage puts it up with all other staged changes for later."): a worksite choice always shows BOTH
// buttons -- "Stage change" (queued in Changes, signed later with everything else) and "Sign with Keychain" (now).
// Both wait for a picked worksite; Sign stays off while the plot cannot sign yet (no power or worker on chain).
function worksiteStageButtonHtml(picked, firstClear = false) {
  // a switched-off
  // button uses aria-disabled, not `disabled`, so its tooltip still shows on hover; the click handler ignores it.
  const body = firstClear
    ? 'Adds this build to Changes. Its Power Core, workers, and construction will sign in one transaction.'
    : 'Adds this build to Changes. It signs later together with your other staged changes, after any staged power and workers are confirmed.';
  return `<button type="button" class="hm-btn is-secondary worksite-modal-stage ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-worksite-action="stage"${picked ? '' : ' aria-disabled="true"'}${tipAttrs({ title: 'Stage change', body: picked ? body : 'First click one of the worksites above, then stage it.', peer: 'worksite_controls' })}>Stage change</button>`;
}

function worksiteSignButtonHtml(row, review) {
  const state = worksiteModalState(row);
  if (state === 'refused') return '';
  const picked = !!WORKSITE_MODAL_SELECTED;
  const signPair = sign => `<span class="wallet-sign-pair">${walletControlsHtml()}${sign}</span>`;
  if (worksiteStageOnly(row)) {
    // First clears combine staged Power Core/workers and construction. Existing changes
    // retain their live-state, sequential signing path.
    if (worksiteEligibleAfterStage(row)) {
      return `${worksiteStageButtonHtml(picked, isFirstClear(row))}${signPair(`<button type="button" class="hm-btn is-primary worksite-modal-confirm ceres-btn ceres-btn--primary" data-control-density="comfortable" data-worksite-action="stage-and-sign"${picked && walletReady() ? '' : ' aria-disabled="true"'}${tipAttrs({ title: `Sign with ${walletName()}`, body: picked ? 'Signs this first clear now: Power Core, workers, and worksite construction in one transaction.' : 'First click one of the worksites above, then sign it.', peer: 'worksite_controls' })} data-control-case="commit">${COMMIT_ICON}${walletSignLabelHtml()}</button>`)}`;
    }
    const missing = worksiteMissingForSigning(row);
    const why = `This plot cannot sign a build yet -- it has no power or no worker on chain${missing.length ? ` (still needs ${missing.join(' and ')})` : ''}. Stage it instead; it signs after them.`;
    return `${worksiteStageButtonHtml(picked, isFirstClear(row))}${signPair(`<button type="button" class="hm-btn is-primary worksite-modal-confirm ceres-btn ceres-btn--primary" data-control-density="comfortable" aria-disabled="true"${tipAttrs({ title: `Sign with ${walletName()}`, body: why, peer: 'worksite_controls' })}>${walletSignLabelHtml()}</button>`)}`;
  }
  if (state === 'choose') {
    // Live-eligible plot: both ways are open once a worksite is picked.
    const signHtml = operationSignButtonHtml({ deed_uid: row.deed_uid }, picked ? review : null, { surface: 'worksite' });
    return `${worksiteStageButtonHtml(picked, isFirstClear(row))}${signPair(signHtml)}`;
  }
  const armed = state === 'completion' || !!WORKSITE_MODAL_SELECTED;
  return signPair(operationSignButtonHtml({ deed_uid: row.deed_uid }, armed ? review : null, { surface: state === 'completion' ? 'worksite-completion' : 'worksite' }));
}

// signs a staged worksite build headlessly (no modal open) once the checkout's own
// signing step has decided it is ready -- built and verified through the SAME preview
// allowlist/Keychain gate the modal itself uses (`requestWorksitePreview`
// `requestBroadcastThroughGate`, `verifyWorksiteBroadcast`); never a client-built op.
// Returns `{ok:true}` once the signature is durably queued (the transaction pill reports
// confirmation from there on, exactly like the modal's own path -- `verifyWorksiteBroadcast`
// is fired the same way), or `{ok:false, reason}` when it could not even be sent, so the
// caller (checkout.js) leaves the build staged with that reason -- nothing is lost.
export async function prepareStagedWorksiteBuild(row, target, timeCrystals = 0) {
  await requestWorksitePreview(row, target, timeCrystals);
  const preview = S.WORKSITE_MODAL_PREVIEW;
  if (!preview?.payload) throw new Error(preview?.error || 'Could not build the worksite transaction.');
  const chunk = preview.payload.chunks[0];
  chunk.whatText = worksiteWhatText(row, preview.payload);
  return { row, account: preview.payload.account, chunk };
}

export async function signStagedWorksiteBuild(row, target, timeCrystals = 0, prepared = null, envelope = null) {
  if (!keychainReady()) return { ok: false, reason: 'Wallet not connected.' };
  try {
    const plan = prepared || await prepareStagedWorksiteBuild(row, target, timeCrystals);
    const { chunk } = plan;
    const broadcast = await requestSignatureThroughGate(plan.account, chunk.operations, () => {}, 'Posting', envelope);
    broadcast.submitted.then(result => {
      chunk.inclusionBlock = result.blockNum;
      return verifyWorksiteBroadcast(row.deed_uid, chunk, result.txId);
    }).catch(error => {
      const refused = error.broadcasted === false;
      return reportWorksiteVerdict(row.deed_uid, chunk, broadcast.txId, {
        tone: refused ? 'danger' : 'caution',
        title: refused ? 'Worksite transaction not sent' : 'Worksite transaction pending',
        detail: error.message,
        phase: refused ? 'refused' : 'pending',
        state: refused ? 'is-failed' : 'is-pending',
        retry: false,
      });
    });
    return { ok: true, txId: broadcast.txId, queued: broadcast.queued };
  } catch (error) {
    if (error?.inclusionUnobserved || error?.ambiguousBroadcast) {
      return { ok: false, reason: error.message || 'Broadcast inclusion was not observed -- check the transaction pill before retrying.' };
    }
    return { ok: false, reason: String(error) };
  }
}

// writes the pick into CERES_STAGED_WORKSITES and closes the modal -- no server
// call, no Keychain. The real op is built and signed later, at the checkout's own signing
// step, against live facts (checkout.js `confirmCheckoutWithWorksites`).
export function stageThisWorksiteBuild(row, target) {
  const overlay = document.getElementById('worksite-modal-overlay');
  const tile = (row.swap_targets || []).find((item) => item.target === target);
  const tcChecked = !!overlay?.querySelector('.worksite-modal-tc input')?.checked;
  const staged = stageWorksiteBuild(row.deed_uid, {
    target,
    targetLabel: tile?.worksite_name || target,
    timeCrystals: isFirstClear(row) ? 0 : (tcChecked ? TC_SWAP_COST : 0),
    firstClear: isFirstClear(row),
  });
  if (!staged) {
    stageToast('This worksite build is already in flight. Wait for confirmation before changing it.', true);
    return false;
  }
  stageToast(`Staged: build ${tile?.worksite_name || target} -- ${isFirstClear(row) ? 'it signs with the staged Power Core and workers in one transaction' : 'it signs once the staged power/workers confirm'}. See Changes.`);
  closeWorksiteModal();
  updateMountedRow(row.deed_uid);
  return true;
}

function renderWorksiteFooter(overlay, row = worksiteModalRow(overlay)) {
  if (!overlay || !row) return;
  const review = S.WORKSITE_MODAL_PREVIEW;
  const note = overlay.querySelector('[data-worksite-note]');
  if (note) {
    note.innerHTML = decorateResourceMentions(esc(worksiteFooterNote(row, review)));  // icon after the resource name
    note.className = `hm-note${review?.error ? ' is-danger' : ''}`;
  }
  const sign = overlay.querySelector('[data-worksite-sign]');
  if (sign) sign.innerHTML = worksiteSignButtonHtml(row, review);
}

// The Time Crystal footer line -- "zero time crystals" (maintainer's report) was never
// a display bug in the number itself, it was that no balance was ever wired up at
// all: the checkbox's label was a hardcoded "(read-only preview)" string with no
// balance anywhere in it. `tcBalance` is `BOARD.tc_balance` (`{value, ...}` or
// null, fetched server-side during capture -- see `ceres/capture.py`), NEVER
// `row.time_crystal_value`, which is a per-deed valuation field, not the player's
// wallet balance. An honest "unknown" beats a silent zero when the fetch hasn't
// run yet or failed (same rule as everywhere else in this modal).
function worksiteTcLineText(tcBalance) {
  const cost = TC_SWAP_COST;
  if (!tcBalance || tcBalance.value === null || tcBalance.value === undefined) {
    return `Build instantly with Time Crystals — ${cost} TC. Balance: unknown.`;
  }
  const value = num(tcBalance.value, 2);
  if (tcBalance.value < cost) {
    return `Build instantly with Time Crystals — ${cost} TC. Balance: ${value} — not enough for ${cost} TC.`;
  }
  return `Build instantly with Time Crystals — ${cost} TC. Balance: ${value}.`;
}

// The Aura Lab tile's Permit note -- `permitBalance` is `BOARD.permit_balance`
// (`{value, ...}` or null, fetched server-side during capture alongside
// `tc_balance` -- see `ceres/capture.py::_permit_balance`). `PERMIT.AL` is
// account-bound and required to build or change into an Aura Lab; an honest
// "unknown" beats a silent zero when the fetch hasn't run yet or failed (same
// rule as `worksiteTcLineText` above).
function worksitePermitLineText(permitBalance) {
  if (!permitBalance || permitBalance.value === null || permitBalance.value === undefined) {
    return WORKSITE_EXTRA_LINES.permitUnknown;
  }
  const value = num(permitBalance.value, 0);
  if (permitBalance.value < 1) {
    return WORKSITE_EXTRA_LINES.permitZero(value);
  }
  return WORKSITE_EXTRA_LINES.permitHeld(value);
}

// Visually select a tile (button element, not the current-worksite div) and re-render the
// footer for it. Re-run on every click so re-selecting a different tile moves the selected
// state rather than accumulating it.
export function selectWorksiteTile(overlay, target) {
  WORKSITE_MODAL_SELECTED = target;
  overlay.querySelectorAll('.worksite-tile-option').forEach((el) => {
    const selected = el.dataset.worksiteTarget === target;
    el.classList.toggle('is-selected', selected);
    el.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
  renderWorksiteFooter(overlay);
}

// the harvest box's Feed-workers button (a finished build) -- it must not silently no-op
// when the board's own captured `worksite_completion` is absent, which happens whenever the
// countdown reached zero while THIS page was already open. A live per-plot read (: never a
// cached/stale fact) decides whether the game is actually ready before the modal opens.
export async function openWorksiteFeedModal(deedUid) {
  const row = BOARD?.plots?.find((plot) => plot.deed_uid === deedUid);
  if (row) return openWorksiteModal(row);
}

export async function openWorksiteModal(row) {
  if (!row) return;
  const isConstruction = row.is_construction || row.harvest?.state === 'construction' || row.pending_change?.kind === 'worksite_change';
  if (!row.worksite_completion && isConstruction) {
    try {
      const data = await refreshPlotHarvest(row.deed_uid, { force: true });
      const completion = data?.projection?.worksite_completion;
      if (data?.projection?.source === 'fresh' && completion) row.worksite_completion = completion;
    } catch (_) {
      return;
    }
  }
  if ((!Array.isArray(row.swap_targets) || row.swap_targets.length === 0) && !row.worksite_completion) return;
  ensureWorksiteModalOverlay();
  const overlay = document.getElementById('worksite-modal-overlay');
  WORKSITE_MODAL_RETURN_FOCUS = document.activeElement;
  overlay.dataset.deedUid = row.deed_uid;
  // Nothing is inherited from a previous modal session; the kept staged choice is account plan state.
  S.WORKSITE_MODAL_PREVIEW = null;
  S.WORKSITE_MODAL_LAST_VERIFY = null;
  WORKSITE_MODAL_SELECTED = keptStagedWorksiteTarget(row);
  WORKSITE_MODAL_TC = false;
  WORKSITE_PREVIEW_TOKEN += 1;
  const state = worksiteModalState(row);
  const panel = overlay.querySelector('.worksite-modal-panel');
  if (panel) panel.dataset.worksiteState = state;
  overlay.querySelector('#worksite-modal-title').innerHTML = `${esc(isFirstClear(row) ? 'Choose worksite' : WORKSITE_MODAL_TITLE[state])} ${worksitePlotBadgeHtml(row)}`;
  overlay.querySelector('.worksite-modal-sub').textContent = worksiteSubtitleText(row);
  overlay.querySelector('.worksite-modal-body').innerHTML = worksiteModalBodyHtml(row);
  retryBackgroundLayers(overlay);
  // the SPS tile fills in its SPS/hr once the pool rate arrives; never blocks the open.
  const spsTile = (row.swap_targets || []).find((tile) => tile.rate?.kind === 'pool_share');
  if (spsTile) {
    const shown = spsTileHourly(spsTile);
    loadSpsRate().then(() => { if (spsTileHourly(spsTile) !== shown) renderWorksiteModalBody(row); });
  }
  selectWorksiteTile(overlay, WORKSITE_MODAL_SELECTED);
  overlay.hidden = false;
  // One step: a ready worksite's Grain payment is prepared at once; the footer button pays and signs.
  if (state === 'completion') requestWorksiteCompletionPreview(row);
  const focusTarget = state === 'choose' ? overlay.querySelector('.worksite-tile-option') : overlay.querySelector('.harvest-x');
  focusTarget?.focus();
}

export function closeWorksiteModal() {
  const overlay = document.getElementById('worksite-modal-overlay');
  if (overlay) overlay.hidden = true;
  WORKSITE_MODAL_SELECTED = null;
  WORKSITE_MODAL_TC = false;
  WORKSITE_PREVIEW_TOKEN += 1; // a preview still in flight for this session is dropped on arrival
  S.WORKSITE_MODAL_PREVIEW = null;
  if (WORKSITE_MODAL_RETURN_FOCUS && typeof WORKSITE_MODAL_RETURN_FOCUS.focus === 'function') WORKSITE_MODAL_RETURN_FOCUS.focus();
  WORKSITE_MODAL_RETURN_FOCUS = null;
}

export function worksiteModalIsOpen() {
  const overlay = document.getElementById('worksite-modal-overlay');
  return !!overlay && !overlay.hidden;
}

// Re-renders the modal body from the CURRENT WORKSITE_MODAL_PREVIEW state and re-applies the
// tile selection (which a raw `.innerHTML =` replacement would otherwise wipe) plus the footer.
// No-op if the modal isn't open for this plot (a stale async response after Close/Escape).
export function renderWorksiteModalBody(row) {
  const overlay = document.getElementById('worksite-modal-overlay');
  if (!overlay || overlay.hidden || overlay.dataset.deedUid !== row.deed_uid) return;
  if (overlay.querySelector('.worksite-modal-panel')?.dataset.worksiteState === 'cancel') {
    renderWorksiteCancelBody(row);
    return;
  }
  const scroller = overlay.querySelector('.hm-body');
  const scrollTop = scroller?.scrollTop || 0;
  overlay.querySelector('.worksite-modal-body').innerHTML = worksiteModalBodyHtml(row);
  retryBackgroundLayers(overlay);
  if (scroller) scroller.scrollTop = scrollTop;
  selectWorksiteTile(overlay, WORKSITE_MODAL_SELECTED);
}

// Builds the real op preview ( Phase 4) -- POSTs the selected target (and TC opt-in) to
// the backend, which re-derives eligibility/legality server-side and either returns the exact
// Hive operation or refuses with a reason. Never builds the op client-side.
export async function requestWorksitePreview(row, target, timeCrystals, action = null) {
  const token = ++WORKSITE_PREVIEW_TOKEN;
  S.WORKSITE_MODAL_PREVIEW = { loading: true, error: null, payload: null };
  renderWorksiteModalBody(row);
  let next;
  try {
    next = { loading: false, error: null, payload: await fetchWorksitePreview(row.deed_uid, action ? { action } : { target, time_crystals: timeCrystals }) };
  } catch (error) {
    next = { loading: false, error: String(error), payload: null };
  }
  if (token !== WORKSITE_PREVIEW_TOKEN) return; // a newer pick, opt-in change or close superseded this answer
  S.WORKSITE_MODAL_PREVIEW = next;
  renderWorksiteModalBody(row);
}

// The one request both the worksite window and the Changes window send; the server builds the op.
async function fetchWorksitePreview(deedUid, body) {
  const response = await fetch(playerQueryAppend(`/api/plots/${encodeURIComponent(deedUid)}/worksite-preview`), {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message || `HTTP ${response.status}`);
  }
  return payload;
}

const WORKSITE_COMPLETION_BODY = Object.freeze({ action: 'complete' });

export async function requestWorksiteCompletionPreview(row) {
  return requestWorksitePreview(row, null, 0, WORKSITE_COMPLETION_BODY.action);
}

// "Feed the workers" from the Changes window -- the SAME request and server builder as
// the worksite window's completion preview above, without touching that window's state. Returns
// the plan shape the Changes signer already uses for staged builds (`prepareStagedWorksiteBuild`).
export async function prepareWorksiteCompletion(row) {
  const payload = await fetchWorksitePreview(row.deed_uid, { ...WORKSITE_COMPLETION_BODY });
  const chunk = payload?.chunks?.[0];
  if (!chunk?.operations?.length) throw new Error('Could not build the Feed the workers transaction.');
  chunk.whatText = worksiteWhatText(row, payload);
  return { row, account: payload.account, chunk, grainCost: payload.grain_cost ?? null, payload };
}

function renderWorksiteCancelBody(row) {
  const overlay = document.getElementById('worksite-modal-overlay');
  if (!overlay || overlay.hidden || overlay.dataset.deedUid !== row.deed_uid) return;
  const preview = S.WORKSITE_MODAL_PREVIEW;
  const payload = preview?.payload;
  const detail = preview?.error ? `<p class="hm-note is-danger">${esc(preview.error)}</p>`
    : payload ? `<details class="hm-ops" open><summary>Exact Hive operation · Posting key</summary><pre>${esc(JSON.stringify(payload.operations.map(([name, body]) => [name, { ...body, json: JSON.parse(body.json) }]), null, 2))}</pre></details>`
      : '<p class="hm-note">Checking the running construction…</p>';
  overlay.querySelector('.worksite-modal-body').innerHTML = `<div class="hm-card"><h3 class="hm-sec-h">Cancel construction</h3><p>This stops the running construction on plot ${esc(formatPlotLabel(row.plot))}. Confirm to ask your Posting wallet to sign.</p>${detail}</div>`;
  overlay.querySelector('[data-worksite-sign]').innerHTML = `<span class="wallet-sign-pair">${walletControlsHtml()}<button type="button" class="hm-btn ceres-btn ceres-btn--primary" data-control-density="comfortable" data-worksite-action="cancel-broadcast"${payload && keychainReady() ? '' : ' disabled'}>Confirm cancel construction</button></span>`;
}

export function openWorksiteCancelDialog(row) {
  if (!row) return;
  ensureWorksiteModalOverlay();
  const overlay = document.getElementById('worksite-modal-overlay');
  WORKSITE_MODAL_RETURN_FOCUS = document.activeElement;
  overlay.dataset.deedUid = row.deed_uid;
  overlay.querySelector('.worksite-modal-panel').dataset.worksiteState = 'cancel';
  overlay.querySelector('#worksite-modal-title').innerHTML = `Cancel construction ${worksitePlotBadgeHtml(row)}`;
  overlay.querySelector('.worksite-modal-sub').textContent = 'Review the operation before signing';
  S.WORKSITE_MODAL_PREVIEW = null;
  S.WORKSITE_MODAL_LAST_VERIFY = null;
  const note = overlay.querySelector('[data-worksite-note]');
  if (note) { note.textContent = ''; note.className = 'hm-note'; }
  WORKSITE_PREVIEW_TOKEN += 1;
  overlay.hidden = false;
  renderWorksiteCancelBody(row);
  requestWorksitePreview(row, null, 0, 'cancel');
  overlay.querySelector('.harvest-x')?.focus();
}

// A verdict while the modal is still open (waiting for Keychain, a broadcast that never
// started, a plot gone from the board) is the footer note; `null` restores the state text.
// `{tone: 'info'|'success'|'danger'|'caution', title, detail}`.
export function renderWorksiteVerdict(overlay, verdict) {
  if (!overlay) return;
  if (!verdict) { renderWorksiteFooter(overlay); return; }
  const note = overlay.querySelector('[data-worksite-note]');
  if (!note) return;
  const tone = ['info', 'success', 'danger', 'caution'].includes(verdict.tone) ? verdict.tone : 'caution';
  note.className = `hm-note is-${tone}`;
  note.textContent = verdict.detail ? `${verdict.title} — ${verdict.detail}` : verdict.title;
}

// once a broadcast exists the modal is closed and every verdict is the transaction
// pill's (the Changes modal's rule, /) -- "Check again" is the pill's own action.
function reportWorksiteVerdict(deedUid, chunk, txId, verdict) {
  const row = BOARD?.plots?.find((plot) => plot.deed_uid === deedUid);
  const retry = verdict.retry ? () => verifyWorksiteBroadcast(deedUid, chunk, txId) : null;
  const title = chunk?.cancelConstruction && verdict.tone === 'info'
    ? (txId ? 'Awaiting confirmation' : 'Transaction sent') : verdict.title;
  setConfirmationBannerState(title, chunk?.cancelConstruction && verdict.tone === 'info' ? '' : verdict.detail || '', verdict.tone, {
    txId: txId || null,
    phase: verdict.phase,
    state: verdict.state,
    family: 'worksite',
    deedUids: [deedUid],
    items: [],
    where: row ? `Plot ${formatPlotLabel(row.plot)}` : null,
    what: chunk?.whatText || null,
    action: retry ? { id: 'retry_verification', label: 'Check again' } : null,
    retry,
  });
}

// Maps an /api/op-verify result's `state` to the verdict the player actually
// needs to see. Only `domain-mismatch` is a genuine failure (danger) -- every
// other non-confirmed state (`chain_verified`, a lookup failure, or any state
// this client doesn't recognise yet) means "not observable yet", not "it
// failed", so all of those fall through to the same neutral in-progress
// treatment with a "Check again" button. A worksite change verified moments
// after broadcast may legitimately not show in game state yet; that is
// expected, not an error.
// `freshRow` is only meaningful for `domain-confirmed` -- the board row as
// refreshed by `refreshWorksitePlot()` moments earlier, carrying the fresh
// `pending_change` the copy below is built from. Every other state ignores it.
// BINDING COPY RULE ( Phase 0, plan §4.3): "confirmed" means the game
// accepted the change and started the 24-hour build -- it does NOT mean the
// worksite is running. Forbidden: "Worksite changed", "Quarry active", any
// bare "Success"/"Done" (guarded by test_worksite_confirmation_copy.py).
function worksiteGameRefusalReason(result) {
  const notice = result?.notices?.find((item) => item?.code === 'verification_game_refused');
  return notice?.reason || result?.game?.evidence?.reason || result?.game?.evidence?.error || 'The game refused this operation.';
}

function worksiteVerdictForState(state, freshRow, refusalReason = null) {
  if (state === 'domain-confirmed') {
    return { tone: 'success', title: 'Transaction confirmed', detail: worksiteConfirmedDetailText(freshRow), phase: 'confirmed', state: 'is-confirmed', retry: false, terminal: true };
  }
  if (state === 'domain-mismatch') {
    return { tone: 'danger', title: 'Game state mismatch', detail: 'The game does not reflect this change.', phase: 'mismatch', state: 'is-failed', retry: true, terminal: true };
  }
  if (state === 'game-refused') {
    return { tone: 'danger', title: 'Game refused', detail: refusalReason || 'The game refused this operation.', phase: 'refused', state: 'is-failed', retry: false, terminal: true };
  }
  return { tone: 'info', title: 'Awaiting confirmation', detail: 'The game has not shown this change yet.', phase: 'pending', state: 'is-pending', retry: true, terminal: false };
}

function worksiteConfirmedDetailText(freshRow) {
  const change = freshRow?.pending_change;
  if (!change) return 'The game accepted this change; finish time unavailable.';
  if (change.stalled) return 'The 24-hour build is paused because this plot has no PP.';
  return 'The 24-hour build has started.';
}

export async function refreshConfirmedPlot(deedUid) {
  const generation = BOARD_GENERATION;
  const player = CURRENT_PLAYER;
  const currentRow = BOARD?.plots?.find((p) => p.deed_uid === deedUid);
  try {
    const resp = await fetch(playerQueryAppend(`/api/plots/${encodeURIComponent(deedUid)}`), { cache: 'no-store' });
    if (!resp.ok) return;
    const updated = await resp.json();
    const idx = BOARD?.plots?.findIndex((p) => p.deed_uid === deedUid) ?? -1;
    // The response is fire-and-forget; abandon it if a newer board load or row
    // replacement has made this request's view obsolete.
    if (generation !== BOARD_GENERATION || player !== CURRENT_PLAYER || idx === -1 || BOARD.plots[idx] !== currentRow) return;
    reconcileCapPrediction(deedUid, updated);
    BOARD.plots[idx] = updated;
    // (maintainer: "bug: staged workers with cooldown must remain in their tiles. they are only n the changes menu after
    // something was signed"): the fresh server row carries no staged moves; write the still-staged ones (e.g. a waiting stake
    // set aside from the signed batch) back onto it, as rows.js loadPlotDetail does, so their tiles keep them.
    applyStageToRow(updated);
    const cidx = CURRENT_ROWS.findIndex((p) => p.deed_uid === deedUid);
    if (cidx !== -1) CURRENT_ROWS[cidx] = updated;
    updateMountedRow(deedUid);
    // Item 9 (worker-table rebuild): the row itself is now current, but the
    // portfolio-wide totals strip (Base PP/Harvest PP/DEC needed) is not derived
    // from any single row -- it needs its own refresh, never left stale.
    refreshBoardTotals(generation, player);
  } catch (error) { /* the confirmed state remains authoritative if the row read fails */ }
}

// A stale callback (a Keychain response or a client-side timeout firing after the modal has
// since been closed and reopened for a DIFFERENT plot) must never touch a newer modal session.
function worksiteModalStillOpenFor(overlay, deedUid) {
  return !!overlay && !overlay.hidden && overlay.dataset.deedUid === deedUid;
}

export { KEYCHAIN_RESPONSE_TIMEOUT_MS } from './broadcast-gate.js';

function worksiteWhatText(row, payload) {
  if (payload.kind === 'worksite_cancel') return `Cancel construction on ${formatPlotLabel(row.plot)}`;
  if (payload.kind === 'worksite_completion') return `Complete ${row.worksite_completion?.to_label || row.pending_change?.to_label || 'the worksite'}`;
  const tile = (row.swap_targets || []).find((item) => item.target === payload.target);
  return tile ? `Change to ${tile.worksite_name}` : 'Change worksite';
}

// The actual broadcast -- one op, one Keychain click, `requestBroadcast` only (never the
// sign-only Keychain call). The modal closes the moment Keychain accepts; the pill reports
// from there. Once a transaction id exists, retries only verify that transaction.
export function confirmWorksiteBroadcast(row, broadcastBtn) {
  const preview = S.WORKSITE_MODAL_PREVIEW;
  const overlay = document.getElementById('worksite-modal-overlay');
  const deedUid = row.deed_uid;
  if (!preview?.payload || !keychainReady()) {
    stageToast('Connect a wallet before signing.', true);
    return;
  }
  const chunk = preview.payload.chunks[0];
  chunk.cancelConstruction = preview.payload.kind === 'worksite_cancel';
  chunk.whatText = worksiteWhatText(row, preview.payload);
  if (broadcastBtn) broadcastBtn.disabled = true;
  renderWorksiteVerdict(overlay, { tone: 'info', title: 'Waiting for wallet approval…', detail: '' });
  const closeIfOpen = () => { if (worksiteModalStillOpenFor(overlay, deedUid)) closeWorksiteModal(); };
  requestBroadcastThroughGate(preview.payload.account, chunk.operations, (response) => {
    const txId = response?.result?.id || response?.result?.tx_id || response?.tx_id;
    if (response?.success && txId) {
      closeIfOpen();
      reportWorksiteVerdict(deedUid, chunk, txId, { tone: 'info', title: 'Transaction sent', detail: 'Waiting for confirmation.', phase: 'accepted', state: 'is-sent' });
    }
  }).then(({ txId }) => {
    closeIfOpen();
    verifyWorksiteBroadcast(deedUid, chunk, txId);
  }).catch((error) => {
    if (error?.inclusionUnobserved || error?.ambiguousBroadcast) {
      if (error.txId) S.WORKSITE_MODAL_LAST_VERIFY = { deedUid, chunk, txId: error.txId };
      closeIfOpen();
      if (error.txId) {
        verifyWorksiteBroadcast(deedUid, chunk, error.txId);
      } else {
        reportWorksiteVerdict(deedUid, chunk, null, { tone: 'caution', title: 'Wallet result unavailable', detail: error.message, phase: 'could-not-verify', state: 'is-unresolved', retry: false });
      }
      return;
    }
    // The broadcast never started (rejected or failed in Keychain): the modal stays and its footer says why.
    if (worksiteModalStillOpenFor(overlay, deedUid)) renderWorksiteVerdict(overlay, { tone: 'danger', title: 'Broadcast did not start', detail: String(error) });
    if (error?.retryable && broadcastBtn) broadcastBtn.disabled = false;
  });
}

// Domain-verifies the broadcast (never trusts the tx id alone) and refreshes the affected row
// only after domain confirmation. Pending results retry a bounded number of times, then leave
// the pill's manual "Check again" available.
export async function verifyWorksiteBroadcast(deedUid, chunk, txId) {
  clearTimeout(WORKSITE_VERIFY_TIMER);
  WORKSITE_VERIFY_TIMER = null;
  chunk.verificationAttempt = (chunk.verificationAttempt || 0) + 1;
  S.WORKSITE_MODAL_LAST_VERIFY = { deedUid, chunk, txId };
  const scheduleRecheck = () => {
    if (chunk.verificationAttempt >= WORKSITE_VERIFY_MAX_ATTEMPTS) return;
    WORKSITE_VERIFY_TIMER = setTimeout(() => {
      WORKSITE_VERIFY_TIMER = null;
      if (S.WORKSITE_MODAL_LAST_VERIFY?.deedUid === deedUid && S.WORKSITE_MODAL_LAST_VERIFY?.txId === txId) {
        verifyWorksiteBroadcast(deedUid, chunk, txId);
      }
    }, WORKSITE_VERIFY_INTERVAL_MS);
  };
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(playerQueryAppend('/api/op-verify'), {
      signal: controller.signal,
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tx_id: txId, operations: chunk.operations, verification_attempt: chunk.verificationAttempt }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `verification HTTP ${response.status}`);
    if (result.state === 'domain-confirmed') {
      balancesChanged({ player: CURRENT_PLAYER || BOARD?.player, assets: ['DEC', 'GRAIN', 'WOOD', 'STONE', 'IRON', 'AURA', 'RESEARCH'], id: `worksite:${txId}` });
      // Phase 0 -- the approved copy (plan §4.3) names the plot and the exact ready
      // time, both of which only exist on the FRESH row, so the refresh is awaited first.
      for (const refreshedDeed of result.refreshed_deeds || []) await refreshConfirmedPlot(refreshedDeed);
      const freshRow = BOARD.plots.find((p) => p.deed_uid === deedUid);
      const verdict = (result.refresh_errors || []).length
        ? { tone: 'caution', title: 'Transaction confirmed', detail: noticeMessage(catalogNotice('verification_refresh_failed')), phase: 'confirmed-partial', state: 'is-confirmed-partial', retry: false, terminal: true }
        : worksiteVerdictForState(result.state, freshRow);
      const completed = chunk.operations?.some((operation) => {
        try { return JSON.parse(operation[1]?.json || '{}').op === 'update_worksite'; } catch { return false; }
      });
      if (completed || chunk.cancelConstruction) await refreshPlotHarvest(deedUid, { force: true });
      if (completed && !(result.refresh_errors || []).length) {
        verdict.title = 'Workers fed — worksite complete';
        verdict.detail = `${freshRow?.worksite_type || 'The worksite'} is now active${freshRow ? ` on ${formatPlotLabel(freshRow.plot)}` : ''}.`;
      }
      if (chunk.cancelConstruction && !(result.refresh_errors || []).length) {
        verdict.title = 'Transaction confirmed';
        verdict.detail = '';
      }
      reportWorksiteVerdict(deedUid, chunk, txId, verdict);
      return;
    }
    if (result.state === 'game-refused') {
      reportWorksiteVerdict(deedUid, chunk, txId, worksiteVerdictForState(result.state, null, worksiteGameRefusalReason(result)));
      return;
    }
    reportWorksiteVerdict(deedUid, chunk, txId, worksiteVerdictForState(result.state, null));
    if (result.state !== 'domain-mismatch') scheduleRecheck();
  } catch (error) {
    reportWorksiteVerdict(deedUid, chunk, txId, { tone: 'caution', title: 'Could not verify', detail: 'The game result could not be checked.', phase: 'could-not-verify', state: 'is-unresolved', retry: true });
    scheduleRecheck();
  } finally {
    clearTimeout(deadline);
  }
}

export function worksiteChangeButtonHtml(row) {
  if ((!Array.isArray(row.swap_targets) || row.swap_targets.length === 0) && !row.worksite_completion) return '';
  // name the plot's actual worksite ("Change Grain Farm"); a plot with no worksite
  // name keeps the catalogue wording.
  const worksiteName = String(row.worksite_type || '').trim();
  const label = worksiteName ? `Change ${worksiteName}` : isFirstClear(row) ? 'Choose worksite' : noticeMessage(catalogNotice('worksite_swap_open'));
  // No tipAttrs() here -- the hover tooltip would just restate the button's
  // own visible label. aria-label stays for the non-hover accessibility path.
  return `<button type="button" class="worksite-change-btn ceres-btn ceres-btn--secondary" data-control-density="compact" data-slot-action="open-worksite-modal" data-deed-uid="${esc(row.deed_uid)}" aria-label="${esc(label)}">${esc(label)}</button>`;
}

window.addEventListener('ceres:wallet-changed', () => {
  const el = document.getElementById('worksite-modal-overlay');
  if (!el || el.hidden) return;
  if (el.querySelector('.worksite-modal-panel')?.dataset.worksiteState === 'cancel') {
    const row = worksiteModalRow(el);
    if (row) renderWorksiteCancelBody(row);
  } else renderWorksiteFooter(el);
});
