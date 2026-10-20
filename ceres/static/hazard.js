import { esc, num, formatPlotLabel } from './format.js';
import { chainReturn } from './combine-chain.js';
import { BOARD } from './board.js';
import { CERES_STAGE } from './staging.js';

let HAZARD_RETURN_FOCUS = null;

// Player switching (2026-08-21): CURRENT_PLAYER is null until the user types a name
// or the URL names one -- null means "let the server apply its own --player default"
// (config.player, normally player0005), never a hardcoded client-side default. Every
// board/capture fetch below appends `?player=` only when CURRENT_PLAYER is set, and
// the URL's own `?player=` query param is kept in sync so a reload or a shared link
// lands on the same player -- no discovery/scanning, just whatever name is typed.

// `is_powered = power_core OR energized OR runi`, 0 exceptions across 6,903 measured
// plots (the internal rules) -- so a plot's SOLE source (power_core or runi) is whichever one of
// those two is currently staked while the other two are absent.
export function isSoleSource(row, groupId) {
  const facts = row.staking_facts || row;
  const hasCore = facts.is_power_core_staked === true;
  const hasEnergized = facts.is_energized === true;
  const hasRuni = facts.is_runi_staked === true || row.capacity?.runi_staked === true;
  if (groupId === 'power_core') return hasCore && !hasEnergized && !hasRuni;
  if (groupId === 'runi') return hasRuni && !hasEnergized && !hasCore;
  return false;
}

function solePowerSource(row) {
  return isSoleSource(row, 'power_core');
}

// Staged (un)stakes of EITHER independently-sufficient source (power_core or runi) can
// change whether a plot is powered -- both are checked, not just power_core, now that
// Runi is stageable. Inlined per-group rather than a shared helper so this stays
// self-contained (tests/ceres/test_job2_staged_undo.py extracts this function's source
// verbatim and runs it in isolation via node).
export function plotPoweredAfterStage(row) {
  const facts = row.staking_facts || row;
  const workersChanged = CERES_STAGE.some((entry) => entry.deed_uid === row.deed_uid && entry.group_id === 'worker');
  const hasEnergized = workersChanged
    ? (row.workers || []).some((worker) => (worker?.land_abilities || []).some(
      (ability) => (Array.isArray(ability) ? ability[0] : ability) === 'ENERGIZED',
    ))
    : facts.is_energized === true;
  if (hasEnergized) return true;
  const coreStage = CERES_STAGE.find((entry) => entry.deed_uid === row.deed_uid && entry.group_id === 'power_core');
  const runiStage = CERES_STAGE.find((entry) => entry.deed_uid === row.deed_uid && entry.group_id === 'runi');
  const hasCore = coreStage
    ? (coreStage.action === 'stake' || coreStage.action === 'replace')
    : facts.is_power_core_staked === true;
  const hasRuni = runiStage
    ? (runiStage.action === 'stake' || runiStage.action === 'replace')
    : (facts.is_runi_staked === true || row.capacity?.runi_staked === true);
  return hasCore || hasRuni;
}

// exported for worksite.js's staged-build eligibility check (whether the plot would
// have a seated worker once its own staged stakes/replaces confirm).
export function stagedWorkersForPlot(row) {
  return CERES_STAGE.some((entry) => entry.deed_uid === row.deed_uid && entry.group_id === 'worker'
    && (entry.action === 'stake' || entry.action === 'replace'));
}

export function ensureHazardOverlay() {
  if (document.getElementById('slot-hazard-overlay')) return;
  const overlay = document.createElement('div');
  overlay.id = 'slot-hazard-overlay';
  overlay.className = 'slot-hazard-overlay';
  overlay.hidden = true;
  overlay.innerHTML = '<div class="slot-hazard-panel" role="dialog" aria-modal="true" aria-labelledby="slot-hazard-title" aria-describedby="slot-hazard-body"><header><h3 id="slot-hazard-title">⚠ Unstaking the sole power source</h3><button type="button" class="hazard-close ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" data-hazard-action="close" aria-label="Close">×</button></header><div class="slot-hazard-body"></div><footer><button type="button" class="slot-bulk ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hazard-action="close">Cancel</button><button type="button" class="slot-review hazard-confirm ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-case="destructive" data-hazard-action="confirm">Unstake anyway</button></footer></div>';
  overlay.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closeHazard(); return; }
    if (event.key !== 'Tab') return;
    const focusable = [...overlay.querySelectorAll('button:not([disabled])')];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  // Backdrop click closes instantly (maintainer, 2026-08-30) -- only fires when the
  // click landed on the overlay element itself (the dimmed backdrop); a click
  // on the panel never bubbles up with the overlay as `event.target`, so this
  // can never fire from inside the panel.
  overlay.addEventListener('click', (event) => { if (event.target === overlay) closeHazard(); });
  document.body.appendChild(overlay);
}

export function openHazard(row, group, index) {
  ensureHazardOverlay();
  const overlay = document.getElementById('slot-hazard-overlay');
  const body = overlay.querySelector('.slot-hazard-body');
  const workers = row.workers?.filter((worker) => Number(worker.slot) > 0).length || 0;
  HAZARD_RETURN_FOCUS = document.activeElement;
  overlay.dataset.deedUid = row.deed_uid;
  overlay.dataset.groupId = group.id;
  overlay.dataset.slotIndex = String(index);
  delete overlay.dataset.combine;
  overlay.querySelector('#slot-hazard-title').textContent = '⚠ Unstaking the sole power source';
  overlay.querySelector('[data-hazard-action="confirm"]').textContent = 'Unstake anyway';
  overlay.querySelector('[data-hazard-action="confirm"]').setAttribute('aria-label', 'Unstake anyway');
  const assetLabel = group.id === 'runi' ? 'Runi' : 'Power Core';
  const cooldownNote = group.id === 'runi' ? ', with no cooldown either way' : ', possibly into the 72-hour cooldown';
  body.innerHTML = `<p>This ${assetLabel} is the plot’s sole proven power source, with ${workers} seated worker${workers === 1 ? '' : 's'}.</p><div class="hazard-unobserved"><strong>The outcome for the seated workers has never been observed.</strong> No consequence is asserted. The known possibilities are:</div><ul><li>Force-unstake${cooldownNote}</li><li>Workers remain stranded and idle</li><li>The game refuses the unstake</li></ul>`;
  overlay.hidden = false;
  overlay.querySelector('.hazard-close')?.focus();
}

export function closeHazard() {
  const overlay = document.getElementById('slot-hazard-overlay');
  if (overlay) { overlay.hidden = true; delete overlay.dataset.combine; }
  if (HAZARD_RETURN_FOCUS && typeof HAZARD_RETURN_FOCUS.focus === 'function') HAZARD_RETURN_FOCUS.focus();
  HAZARD_RETURN_FOCUS = null;
}

// use the shared hazard shell; never render its UID-bearing warning string.
export function confirmCombineWarning(reviews) {
  return new Promise(resolve => {
    const prior = document.activeElement;
    const overlay = document.createElement('div');
    overlay.className = 'slot-hazard-overlay chain-confirm-overlay';
    const finish = choice => { overlay.remove(); prior?.focus?.(); resolve(choice); };
    overlay.innerHTML = `<div class="slot-hazard-panel" role="dialog" aria-modal="true" aria-labelledby="chain-warning-title"><header><h3 id="chain-warning-title">One of these cannot be undone</h3></header><div class="slot-hazard-body">${combineWarningHtml(reviews)}</div><footer class="chain-footer"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-chain-sign="cancel">Cancel</button><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-chain-sign="without">Sign without the combine</button><button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" data-control-case="destructive" data-chain-sign="all">I understand, sign everything</button></footer></div>`;
    overlay.addEventListener('click', event => { const choice = event.target.closest('[data-chain-sign]'); if (choice) finish(choice.dataset.chainSign); else if (event.target === overlay) finish('cancel'); });
    overlay.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish('cancel'); }
      if (event.key !== 'Tab') return;
      const buttons = [...overlay.querySelectorAll('button')];
      if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0].focus(); }
    });
    document.body.appendChild(overlay);
    overlay.querySelector('button').focus();
  });
}

export function combineWarningHtml(reviews) {
  return reviews.map(({ plan, build }) => {
    const hazard = build.preview.hazard;
    const destroyed = new Set(hazard.destroyed_uids);
    const rows = hazard.rows.filter(row => destroyed.has(row.card_id));
    const placement = CERES_STAGE.find(entry => entry.chain_id === plan.id && entry.action !== 'unstake');
    const back = placement && chainReturn(plan, placement.deed_uid, placement.slot_index);
    const plot = placement && BOARD?.plots?.find(row => row.deed_uid === placement.deed_uid);
    return `<section><p>The combine uses up ${rows.length} cards. They are gone after it is signed:</p><div class="chain-copies">${rows.map(row => `<div class="chain-copy is-consumed"><strong>${esc(plan.card.name)}</strong><span>CC ${num(row.bcx)} · used up</span></div>`).join('')}</div><p><strong>You get: ${esc(plan.card.name)} · level ${num(plan.expected.level)} · CC ${num(plan.expected.bcx)} · base PP ${num(plan.expected.base_pp)}</strong></p><p>Kept: the copy with CC ${num(hazard.survivor.bcx)}</p>${placement ? `<p>${esc(back?.label || 'Placed')} · ${plot ? `Plot ${esc(formatPlotLabel(plot.plot))}` : 'target plot'}, slot ${esc(placement.slot_index + 1)} · next Sign, after confirmation.</p>` : ''}</section>`;
  }).join('');
}
