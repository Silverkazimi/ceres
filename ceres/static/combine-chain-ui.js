import { buildPlanCombine, verifyPlanCombine, resolvePlanMismatch } from './combine-chain-sign.js';
import { chainReturn, chainStageBackSource } from './combine-chain.js';
// plan-mode views and the existing stage/confirmation adapters.
import { BOARD, CURRENT_PLAYER, playerQueryAppend, noticeMessage } from './board.js';
import { activeChainInputs, chainPlotLosses, createChain, deriveChain, reconcileChainCopies, selectChainLevel } from './combine-chain.js';
import { activeChainPlans, chainPlans, mintTransactionRunId, saveChain, stagePlansReady } from './stage-plans.js';
import { openCombinePlanDialog, closeCombineDialog } from './combine-dialog.js';
import { planPlacementTileHtml, plotBadgeHtml, renderCheckoutSurface, removeCheckoutStage } from './checkout.js';
import { editorGroups, editorCardImageHtml, editorOccupant } from './editor.js';
import { candidateQuery } from './candidates.js';
import { loadPlotDetail, updateMountedRow } from './rows.js';
import { CERES_STAGE, applyStageToRow, stageForSlot, stageToast, removalImpact, applyRemovalImpactToStage, mutateEditorSlot } from './staging.js';
import { COMMIT_ICON } from './button-icons.js';
import { FILL_TRAY_ICON, HOURGLASS_IMG } from './icons.js';
import { S } from './state.js';
import { esc, num, formatPlotLabel, humanizeInstant } from './format.js';
import { bindCountdowns, countdownText } from './countdown.js';
import { tipAttrs } from './tooltip.js';

let UPGRADE_WINDOW = null;
let upgradeRequest = 0;
let chainRefreshSerial = 0;
const CHAIN_REFRESHES = new Map();
// a plan card's step list shows this many rows before "Show N more"; `${id}:${step}` keys of lists opened in full.
const CHAIN_ROWS = 5;
const CHAIN_EXPANDED = new Set();

function upgradeUid(card) { return String(card?.uid || card?.card_uid || card?.asset_ref || ''); }
function chainPlotLabel(source) {
  const row = BOARD?.plots?.find((item) => item.deed_uid === source?.deed_uid);
  return row ? `Plot ${formatPlotLabel(row.plot)}` : 'Source plot';
}
function chainSourceLabel(input) {
  return input.source ? `${chainPlotLabel(input.source)}${Number.isInteger(input.source.slot_index) ? `, slot ${input.source.slot_index + 1}` : ''}` : input.kind === 'free' ? 'Free copy' : 'Cooling copy';
}
function chainProblemDetail(plan, detail) {
  let text = String(detail || 'The game supplied no refusal reason.');
  for (const input of plan.inputs) text = text.split(input.uid).join('this copy');
  return text.replace(/\bC\d?-[A-Za-z0-9-]+\b/g, 'this copy');
}
function chainTimeHtml(iso, now = Date.now()) {
  return iso ? `${esc(humanizeInstant(iso)?.absolute)} · <span data-countdown-until="${esc(iso)}">${esc(countdownText(iso, now))}</span>` : 'the end time is being read from the game';
}
async function chainPost(route, body) {
  const response = await fetch(playerQueryAppend(`/api/combine-chain/${route}`), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!response.ok) throw new Error('The game facts could not be checked. Reopen the upgrade plan and try again.');
  return response.json();
}

export function upgradeActionHtml(card, index = 'occupant', named = false) {
  if (Number(card?.upgrade?.usable_copies) < 2 || !card?.upgrade || card.upgrade.eligible === false) return '';
  return `<button type="button" class="candidate-upgrade ceres-btn ceres-btn--secondary" data-control-density="compact" data-upgrade-action="open" data-upgrade-index="${esc(index)}"${tipAttrs({ title: 'Upgrade this worker', body: 'Combine it with your other copies. Copies on other plots are unstaked too; the new card can go on any plot.' })}>${named ? `Upgrade ${esc(card.name || 'worker')}` : 'Upgrade'}</button>`;
}

export async function loadOccupantUpgrade(picker) {
  if (picker.groupId !== 'worker' || !picker.currentOccupant || picker.mobile) return;
  try {
    const data = await chainPost('inputs', { uid: upgradeUid(picker.currentOccupant) });
    if (S.CERES_PICKER !== picker) return;
    const anchor = data.copies.find((copy) => copy.uid === upgradeUid(picker.currentOccupant) || (picker.currentOccupant.asset_ref && copy.asset_ref === picker.currentOccupant.asset_ref));
    picker.currentOccupant.upgrade = { eligible: anchor?.eligible === true, usable_copies: data.copies.filter((copy) => copy.eligible).length };
    updateMountedRow(picker.deedUid);
  } catch (error) {
    // invalidate stale eligibility; diagnostics stay off the rendered surface.
    if (S.CERES_PICKER !== picker) return;
    delete picker.currentOccupant.upgrade;
    console.warn('Occupant upgrade lookup failed', error);
    updateMountedRow(picker.deedUid);
  }
}

export async function openUpgradeWindow(card, origin) {
  const serial = ++upgradeRequest;
  const player = CURRENT_PLAYER;
  UPGRADE_WINDOW = null;
  openCombinePlanDialog(`Upgrade — ${card.name || 'worker'}`, '<span class="spinner spinner-sm"></span> Reading your copies…');
  try {
    const data = await chainPost('inputs', { uid: upgradeUid(card) });
    if (serial !== upgradeRequest || CURRENT_PLAYER !== player || document.getElementById('combine-dialog-overlay')?.hidden) return;
    const anchor = data.copies.find((copy) => copy.uid === upgradeUid(card) || (card.asset_ref && copy.asset_ref === card.asset_ref));
    if (!anchor?.eligible) throw new Error('This worker cannot be used in an upgrade plan.');
    UPGRADE_WINDOW = { card, origin, player, copies: data.copies, anchor: anchor.uid, thresholds: data.thresholds || [], selected: [anchor.uid], target: 'manual', expected: null, loading: false, error: '', losses: [], stageBack: true };
    renderUpgradeWindow();
  } catch (error) {
    if (serial === upgradeRequest && CURRENT_PLAYER === player) openCombinePlanDialog(`Upgrade — ${card.name || 'worker'}`, `<p class="chain-problem">${esc(error.message)}</p>`);
  }
}

export function upgradeWindowHtml(view) {
  const selected = view.copies.filter((copy) => view.selected.includes(copy.uid));
  const total = selected.reduce((sum, copy) => sum + Number(copy.bcx), 0);
  const expected = view.expected;
  const currentLevel = Number(view.card.level || 1);
  const available = view.copies.filter((copy) => copy.eligible).reduce((sum, copy) => sum + Number(copy.bcx), 0);
  const levels = view.thresholds.map((cc, index) => index + 1 > currentLevel ? `<button type="button" class="ceres-btn ceres-btn--choice${view.target === index + 1 ? ' is-active' : ''}" data-control-density="compact" data-upgrade-action="level" data-level="${index + 1}" aria-pressed="${view.target === index + 1}"${cc > available ? ` disabled${tipAttrs({ title: `You own ${num(cc - available)} CC too few for Level ${index + 1}.` })}` : ''}><span class="ceres-btn__check" aria-hidden="true"></span>Level ${index + 1}${index === view.thresholds.length - 1 ? ' · max' : ''}</button>` : '').join('');
  const next = view.thresholds.findIndex((cc) => cc > total);
  const summary = `${selected.length} cards ticked, total CC ${num(total)}.${view.loading ? ' Checking result…' : ''}`;
  const extra = expected?.base_pp_capped ? `Over the maximum: CC ${num(total)} (max ${num(expected.bcx_max)}); base PP stops at ${num(expected.base_pp)}. Combining more copies adds no production.` : next >= 0 ? `Level ${next + 1} needs ${num(view.thresholds[next] - total)} more CC.` : '';
  const rows = view.copies.map((copy, index) => {
    const ticked = view.selected.includes(copy.uid);
    const anchor = copy.uid === view.anchor;
    const survivor = expected ? expected.survivor_uid === copy.uid : anchor;
    const tone = !copy.eligible ? 'unusable' : !ticked ? 'muted' : survivor ? 'survivor' : 'consumed';
    const state = !copy.eligible ? `Can't combine: ${(copy.reasons || []).map((reason) => reason.replace(/Card ID/g, 'card details')).join('; ')}` : !ticked ? 'Not in this plan' : survivor ? `Stays · becomes Lv ${expected?.level || currentLevel}` : 'Used up';
    const location = copy.kind === 'staked' ? `${chainSourceLabel(copy)} · Unstake · 3-day cooldown` : copy.kind === 'cooling' ? `Cooling · ready ${copy.ready_at ? humanizeInstant(copy.ready_at)?.absolute : 'after the game reports its end time'}` : 'Free';
    return `<div class="chain-copy is-${tone}"><label class="chain-check ceres-btn ceres-btn--choice" data-control-density="compact"><span class="ceres-btn__check" aria-hidden="true"></span><input type="checkbox" data-upgrade-copy="${index}"${ticked ? ' checked' : ''}${anchor || !copy.eligible ? ' disabled' : ''} aria-label="${esc(`${view.card.name} · CC ${copy.bcx}${anchor ? ' · This worker' : ''}`)}"></label><span><strong>${esc(view.card.name)}</strong> · CC ${num(copy.bcx)}${anchor ? ' · This worker' : ''}<small class="${copy.kind !== 'free' ? 'chain-warn' : ''}">${esc(location)}</small></span><span class="chain-copy-outcome">${esc(state)}</span></div>`;
  }).join('');
  return `<div class="chain-editor"><div class="chain-hero">${editorCardImageHtml(view.card)}<div><h4>${esc(view.card.name)}</h4><p>Level ${esc(currentLevel)} → ${esc(expected?.level || '—')} · CC ${num(view.card.bcx)} → ${expected ? num(expected.bcx) : '—'}</p><p>Base PP ${num(view.card.base_pp ?? view.card.land_base_pp)} → ${expected ? num(expected.base_pp) : '—'} (expected)</p><small>The game keeps the copy with the most CC.</small></div></div><div class="chain-levels">Target level: ${levels}<button type="button" class="ceres-btn ceres-btn--choice${view.target === 'manual' ? ' is-active' : ''}" data-control-density="compact" data-upgrade-action="manual" aria-pressed="${view.target === 'manual'}"><span class="ceres-btn__check" aria-hidden="true"></span>Manual</button></div><p class="chain-summary" role="status">${esc(summary)} <span class="chain-warn">${esc(extra)}</span></p><div class="chain-copies">${rows}</div>${plotLossWarningHtml(selected, view.card.name, view.losses)}${stageBackHtml(view)}<details class="chain-editor-how"><summary>How the upgrade works</summary>${chainStepsHtml(['Unstake every selected copy on land with Sign in Changes.', 'Each copy cools until its own game end time. The last one sets the ready time.', 'Combine the selected copies into one card after all cooldowns end.', 'Place the upgraded worker on a plot after the combine.'], ['waiting', 'waiting', 'waiting', 'waiting'])}</details>${view.error ? `<p class="chain-problem" role="alert">${esc(view.error)}</p>` : ''}<footer class="chain-footer"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-upgrade-action="close">Cancel</button><button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" data-upgrade-action="stage"${!expected || selected.length < 2 || view.loading ? ' disabled' : ''}>Stage the upgrade plan</button></footer></div>`;
}

function stageBackHtml(view) {
  const anchor = view.copies.find((copy) => copy.uid === view.anchor);
  if (anchor?.kind !== 'staked') return '';
  return `<label class="chain-stage-back ceres-btn ceres-btn--choice" data-control-density="compact"><span class="ceres-btn__check" aria-hidden="true"></span><input type="checkbox" data-upgrade-stage-back${view.stageBack ? ' checked' : ''}><span>Stage the upgraded card back in this worker's slot${anchor.source ? ` (${esc(chainSourceLabel(anchor))})` : ''} after the combine</span></label>`;
}

export function plotLossWarningHtml(copies, name, impacts = []) {
  const plots = chainPlotLosses(copies);
  if (!plots.length) return '';
  const rows = plots.map(({ deed_uid, inputs }) => {
    const impact = impacts.find((item) => item.deed_uid === deed_uid)?.impact;
    const pp = impact?.removed?.reduce((sum, item) => sum + Number(item.own_base_pp || 0), 0);
    return `<li>${esc(chainPlotLabel(inputs[0].source))} loses ${inputs.length > 1 ? `${inputs.length} copies of ` : ''}${esc(name)}${pp ? ` (−${num(pp)} Base PP)` : ''} until you stake ${inputs.length > 1 ? 'other workers' : 'another worker'} there.${(impact?.notices || []).map((notice) => ` ${esc(noticeMessage(notice))}`).join('')}</li>`;
  }).join('');
  return `<aside class="chain-loss"><strong>${plots.length === 2 ? 'Two plots lose' : plots.length === 1 ? 'One plot loses' : `${plots.length} plots lose`} a worker for about 3 days</strong><ul>${rows}</ul></aside>`;
}

function renderUpgradeWindow() {
  if (!UPGRADE_WINDOW || document.getElementById('combine-dialog-overlay')?.hidden) return;
  const focusedCopy = document.activeElement?.dataset?.upgradeCopy;
  const focusedLevel = document.activeElement?.dataset?.level;
  document.getElementById('combine-dialog-title').textContent = `Upgrade — ${UPGRADE_WINDOW.card.name}`;
  document.getElementById('combine-dialog-body').innerHTML = upgradeWindowHtml(UPGRADE_WINDOW);
  if (focusedCopy !== undefined) document.querySelector(`[data-upgrade-copy="${focusedCopy}"]`)?.focus();
  else if (focusedLevel !== undefined) document.querySelector(`[data-upgrade-action="level"][data-level="${focusedLevel}"]`)?.focus();
}

async function updateUpgradeSelection() {
  const view = UPGRADE_WINDOW;
  const serial = ++upgradeRequest;
  view.expected = null;
  view.error = '';
  view.losses = [];
  view.loading = view.selected.length >= 2;
  renderUpgradeWindow();
  if (!view.loading) return;
  try {
    const result = await chainPost('expected-card', { uids: view.selected });
    if (serial !== upgradeRequest || view !== UPGRADE_WINDOW || CURRENT_PLAYER !== view.player) return;
    view.expected = result.expected;
    const selected = view.copies.filter((copy) => view.selected.includes(copy.uid));
    const losses = [];
    for (const loss of chainPlotLosses(selected)) {
      const row = BOARD?.plots?.find((item) => item.deed_uid === loss.deed_uid);
      if (row) losses.push({ deed_uid: loss.deed_uid, impact: await removalImpact(row, loss.inputs.map((copy) => copy.asset_ref || copy.uid)) });
    }
    if (serial === upgradeRequest) view.losses = losses;
  } catch (error) { if (serial === upgradeRequest) { view.error = error.message; view.expected = null; } }
  if (serial === upgradeRequest && view === UPGRADE_WINDOW) { view.loading = false; renderUpgradeWindow(); }
}

export function chainStepsHtml(details, states = ['active', 'waiting', 'waiting', 'waiting']) {
  const removeIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 5L19 19M19 5L5 19"/></svg>';
  const icons = [removeIcon, HOURGLASS_IMG, COMMIT_ICON, FILL_TRAY_ICON];
  const stateNames = { done: 'Done', active: 'In progress', waiting: 'Not started', problem: 'Failed or refused' };
  return `<div class="chain-steps">${['Unstake', 'Cooldown', 'Combine', 'Place'].map((name, index) => {
    const state = states[index] || 'waiting';
    const open = state === 'active' || state === 'problem';
    return `<section class="chain-step chain-step-${index + 1} is-${state}"><details class="chain-step-details"${open ? ' open' : ''}><summary><span class="chain-step-number">${state === 'done' ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m5 12 4 4 10-10"/></svg>' : index + 1}</span><span class="chain-step-icon" aria-hidden="true">${icons[index]}</span><span class="chain-step-name">${name}</span><span class="chain-step-state">${stateNames[state] || stateNames.waiting}</span></summary><div class="chain-step-content">${details[index]}</div></details></section>`;
  }).join('')}</div>`;
}

export function chainCardHtml(id, plan, now = Date.now()) {
  if (plan.state === 'cancelled') {
    const pending = plan.inputs.filter((input) => input.left_out_choice === 'put_back' && !input.return_staged_at);
    return `<article class="hm-card chain-card"><h3>${esc(plan.card.name)} · Return requested</h3><p>Waiting for the unstake confirmation and a free source slot. Each return will appear as an ordinary staged stake.</p>${pending.map((input) => `<p>CC ${num(input.cc)} · ${esc(chainSourceLabel(input))} · ${chainTimeHtml(input.ready_at, now)}</p>`).join('')}<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-upgrade-action="keep-off" data-chain-id="${esc(id)}">Leave off land</button></article>`;
  }
  const derived = deriveChain(plan, now);
  const inputs = activeChainInputs(plan);
  const signed = !!plan.combine || ['combine_signed', 'combined', 'done'].includes(plan.state);
  // ( showed on copies whose unstake is already signed and confirmed): only an unsigned copy offers it.
  const leave = (input) => signed || input.unstake?.tx_id ? '' : `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-upgrade-action="leave" data-chain-id="${esc(id)}" data-chain-input="${plan.inputs.indexOf(input)}">Leave out</button>`;
  const row = (input, text, action = true) => `<div class="chain-input"><span>CC ${num(input.cc)} · ${esc(chainSourceLabel(input))}<small>${text}</small></span>${action ? leave(input) : ''}</div>`;
  // identical free copies (same CC) and cooling copies with the same CC that end in the
  // same minute become one row -- "12 free copies · CC 1 each"; its one "Leave out all" leaves out every copy of the group.
  // Staked copies never group (each is a signed unstake with its own plot and slot) and the upgraded worker stays its own row.
  const anchorUid = plan.origin?.asset_uid;
  const groups = (list) => {
    const byKey = new Map();
    for (const input of list) {
      const key = input.kind === 'staked' || input.uid === anchorUid ? input.uid : `${input.kind}:${input.cc}:${input.kind === 'cooling' ? Math.floor((Date.parse(input.ready_at) || 0) / 60000) : ''}`;
      if (!byKey.has(key)) byKey.set(key, []);
      byKey.get(key).push(input);
    }
    return [...byKey.values()];
  };
  const leaveAll = (group) => signed ? '' : `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-upgrade-action="leave-group" data-chain-id="${esc(id)}" data-chain-inputs="${group.map((input) => plan.inputs.indexOf(input)).join(' ')}">Leave out all</button>`;
  const groupRow = (group, text, action = true) => group.length === 1 ? row(group[0], text, action)
    : `<div class="chain-input"><span>${group.length} ${group[0].kind} copies · CC ${num(group[0].cc)} each<small>${text}</small></span>${action ? leaveAll(group) : ''}</div>`;
  // After grouping, a list longer than CHAIN_ROWS shows its first rows and one "Show N more" that opens it in place.
  const shown = (rows, step) => rows.length > CHAIN_ROWS && !CHAIN_EXPANDED.has(`${id}:${step}`)
    ? rows.slice(0, CHAIN_ROWS).join('') + `<div class="chain-input"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-upgrade-action="show-more" data-chain-id="${esc(id)}" data-chain-step="${step}">Show ${rows.length - CHAIN_ROWS} more</button></div>`
    : rows.join('');
  const staked = inputs.filter((input) => input.kind === 'staked');
  const unstake = shown(staked.map((input) => row(input, input.problem?.code === 'unstake_refused' ? `Refused by the game: ${esc(chainProblemDetail(plan, input.problem.detail))} <button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-upgrade-action="retry" data-chain-id="${esc(id)}" data-chain-input="${plan.inputs.indexOf(input)}">Try again</button>` : input.unstake?.confirmed_at ? `Done · ${esc(humanizeInstant(input.unstake.confirmed_at)?.absolute)}` : input.unstake?.tx_id ? 'Signed · checking with the game' : 'Sign it with Sign below')), 1) || 'No unstakes needed.';
  const timed = inputs.filter((input) => input.kind !== 'free');
  // one large live timer -- spinner and time -- with
  // the ready time under it; each copy shows only its own countdown, its date in the tooltip.
  const running = !signed && derived.ready_at && Date.parse(derived.ready_at) > now;
  const cooldownHead = signed || (derived.ready_at && !running) ? '<p>All cooldowns ended.</p>'
    : derived.state === 'planned' ? '<p>Starts when every unstake is signed.</p>'
    : running ? `<div class="chain-timer" role="timer"${tipAttrs({ title: 'Combine possible in', body: `When the last copy's cooldown ends: ${humanizeInstant(derived.ready_at)?.absolute}.` })}><span class="spinner" aria-hidden="true"></span><b data-countdown-until="${esc(derived.ready_at)}" data-countdown-ended="now">${esc(countdownText(derived.ready_at, now))}</b></div><p class="chain-timer-when">Ready ${esc(humanizeInstant(derived.ready_at)?.absolute)}</p>`
    : `<p>${timed.length ? 'The end time is being read from the game.' : 'All copies are free.'}</p>`;
  const copyTime = (iso) => `<span${tipAttrs({ title: 'Cooldown ends', body: humanizeInstant(iso)?.absolute })}>${Date.parse(iso) > now ? `<span data-countdown-until="${esc(iso)}" data-countdown-compact data-countdown-ended="ended">${esc(countdownText(iso, now, { compact: true }))}</span>` : 'ended'}</span>`;
  const cooldown = cooldownHead + shown(groups(timed).map((group) => groupRow(group, group[0].ready_at ? copyTime(group[0].ready_at) : group[0].unstake?.tx_id ? 'The end time is being read from the game.' : 'Waiting for the unstake.', group[0].kind === 'cooling')), 2);
  // step 3 lists every copy that
  // goes in -- the upgraded worker first, then the other staked, cooling and free copies -- each with its state in plain
  // words, and closes with the result as CC / max CC and level. The max CC is the one the server put into the expected card.
  const copyState = (input) => input.kind === 'free' ? 'Free in the collection'
    : input.kind === 'cooling' ? (input.ready_at ? Date.parse(input.ready_at) > now ? `Cooling until ${esc(humanizeInstant(input.ready_at)?.absolute)}` : 'Cooldown ended' : 'Cooling · the end time is being read from the game')
    : input.unstake?.confirmed_at ? (input.ready_at ? Date.parse(input.ready_at) > now ? `Unstaked, cooling until ${esc(humanizeInstant(input.ready_at)?.absolute)}` : 'Unstaked, cooldown ended' : 'Unstaked · the end time is being read from the game')
    : input.unstake?.tx_id ? 'Staked, unstake signed' : 'Staked, unstake not signed yet';
  const combineInputs = [...inputs].sort((a, b) => (a.uid === anchorUid ? 0 : 1) - (b.uid === anchorUid ? 0 : 1));
  const combineRows = shown([...groups(combineInputs).map((group) => groupRow(group, `${group[0].uid === anchorUid ? 'The worker being upgraded · ' : ''}${copyState(group[0])}`, group[0].kind === 'free')),
    ...plan.inputs.filter(input => input.status === 'unavailable').map(input => row(input, 'Unavailable · leave out to review the remaining copies', false))], 3);
  const expected = plan.expected?.bcx ? plan.expected : null;
  const result = expected ? `<p class="chain-result">Result: <b>${num(expected.bcx)}${expected.bcx_max != null ? ` / ${num(expected.bcx_max)}` : ''} CC → Level ${esc(expected.level ?? '—')}</b>${expected.base_pp_capped ? ' · above the max CC, base PP stops rising' : ''}</p>` : '';
  let combine = `<p>${inputs.length} cards into one · ${derived.state === 'ready' ? 'Ready to combine' : 'waits for the last cooldown'}<span class="chain-phone"> · Combine from a computer</span></p>${combineRows}${result}`;
  const states = [staked.some((input) => input.problem) ? 'problem' : staked.every((input) => input.unstake?.confirmed_at) ? 'done' : 'active', derived.state === 'ready' ? 'done' : derived.state === 'planned' ? 'waiting' : 'active', derived.state === 'ready' ? 'active' : 'waiting', 'waiting'];
  if (plan.problem?.code === 'combine_refused') states[2] = 'problem';  // a combine refused before signing marks the Combine step red
  const placement = CERES_STAGE.find(entry => entry.chain_id === id && entry.action !== 'unstake');
  const back = placement && chainReturn(plan, placement.deed_uid, placement.slot_index);
  let place = placement ? `${back ? 'Returns upgraded to' : 'On'} ${chainPlotLabel({ deed_uid: placement.deed_uid })}, slot ${placement.slot_index + 1}${back && !back.sameSlot ? ` (was slot ${back.slot})` : ''} · signable after the combine` : 'Not placed yet · choose a plot in the worker picker.';
  if (signed) {
    states[0] = states[1] = 'done';
    states[2] = plan.state === 'combine_signed' ? 'active' : plan.problem ? 'problem' : 'done';
    states[3] = plan.problem ? 'problem' : plan.state === 'done' ? 'done' : 'active';
    combine = (plan.state === 'combine_signed' ? '<p>Combine signed · checking with the game. Do not combine these copies again.</p>' : '<p>Combine confirmed.</p>') + combineRows + result;
    if (plan.state === 'combine_signed' || plan.problem?.code === 'combine_mismatch') combine += `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-upgrade-action="check-combine" data-chain-id="${esc(id)}">Check again</button>`;
    if (placement) place = place.replace('signable after the combine', plan.problem ? 'placement on hold' : placement.cooldown?.ready ? 'ready to stake · next Sign' : 'waiting for the board read');
  } else if (derived.state === 'ready' || plan.problem?.code === 'combine_refused') {
    combine += `<button type="button" class="ceres-btn ceres-btn--secondary chain-desktop" data-control-density="compact" data-upgrade-action="review" data-chain-id="${esc(id)}">Review and combine</button>`;
  }
  const old = plan.card;
  let problem = derived.problem ? `<p class="chain-problem" role="status">${derived.problem.code === 'not_enough_cards' ? 'Not enough cards: at least two copies are needed. Cancel the plan or keep the remaining copies off land.' : derived.problem.code === 'unstake_refused' ? 'The game refused an unstake. Try again or leave that copy out.' : 'A copy is unavailable and has been left out. The expected result uses the remaining copies.'}</p>` : '';
  if (plan.problem?.code === 'combine_refused') problem = `<p class="chain-problem">${esc(chainProblemDetail(plan, plan.problem.detail || 'The combine was not sent or was refused. Review the plan before trying again.'))}</p>`;
  if (plan.problem?.code === 'combine_uncertain') problem = '<p class="chain-problem">The combine is not confirmed yet. These copies remain held while its transaction is checked.</p>';
  if (plan.problem?.code === 'combine_mismatch') problem = `<p class="chain-problem">The combine result does not match the plan: expected CC ${num(plan.expected.bcx)} and base PP ${num(plan.expected.base_pp, 3)}, the game shows CC ${num(plan.result?.bcx)} and base PP ${num(plan.result?.base_pp, 3)}. Your staged placement is on hold.</p><div class="chain-footer"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-upgrade-action="use-result" data-chain-id="${esc(id)}">Use this card anyway</button><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-control-case="destructive" data-upgrade-action="cancel-placement" data-chain-id="${esc(id)}">Cancel the placement</button></div>`;
  return `<article class="hm-card chain-card"><div class="chain-card-head"><div class="chain-hero">${editorCardImageHtml(old)}<div><h3>${esc(old.name)}</h3><p>Level ${esc(old.level || '—')} → ${esc(expected?.level || '—')} (expected) · CC ${num(old.bcx)} → ${expected ? num(expected.bcx) : '—'}</p><p>Base PP ${num(old.base_pp ?? old.land_base_pp)} → ${expected ? num(expected.base_pp) : '—'}${expected?.base_pp_capped ? ' (max)' : ''}</p><small>${inputs.length} cards · ${staked.length} from land · ${esc(derived.state.replaceAll('_', ' '))}</small></div></div>${plan.problem?.code === 'combine_mismatch' ? '' : `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-control-case="destructive" data-upgrade-action="${signed ? 'finish' : 'cancel'}" data-chain-id="${esc(id)}"${plan.state === 'combine_signed' ? ' disabled' : ''}>${signed ? 'Finish plan' : 'Cancel plan'}</button>`}</div>${problem}${chainStepsHtml([unstake, cooldown, combine, placeHtml(id, plan, place)], states)}</article>`;
}

// the planned tile
// and its box sit inside the Place step, beside its text.
// the plot named in the
// Place text is the board's own plot badge (click goes to the plot).
function placeHtml(id, plan, place) {
  const tile = planPlacementTileHtml(id, plan);
  const placement = CERES_STAGE.find((entry) => entry.chain_id === id && entry.action !== 'unstake');
  const deed = placement?.deed_uid || chainStageBackSource(plan)?.deed_uid;
  const row = deed && BOARD?.plots?.find((item) => item.deed_uid === deed);
  const text = row ? esc(place).replace(esc(chainPlotLabel({ deed_uid: deed })), plotBadgeHtml(row, deed)) : esc(place);
  return tile ? `<div class="chain-place">${tile}<p>${text}</p></div>` : text;
}

export function upgradePlansHtml() {
  const plans = activeChainPlans();
  return plans.length ? `<section class="chain-plans"><h3>Upgrade plans</h3>${plans.map(([id, plan]) => chainCardHtml(id, plan)).join('')}</section>` : '';
}

async function stageUpgradePlan() {
  const view = UPGRADE_WINDOW;
  if (!view?.expected || view.loading || !stagePlansReady()) return;
  view.loading = true;
  renderUpgradeWindow();
  try {
    const copies = view.copies.filter((copy) => view.selected.includes(copy.uid));
    if (activeChainPlans().some(([, plan]) => activeChainInputs(plan).some((input) => view.selected.includes(input.uid)))) throw new Error('A selected copy already belongs to an upgrade plan.');
    const prepared = [];
    for (const loss of chainPlotLosses(copies)) {
      await loadPlotDetail(BOARD.plots.find((item) => item.deed_uid === loss.deed_uid));
      if (CURRENT_PLAYER !== view.player || UPGRADE_WINDOW !== view) return;
      const row = BOARD.plots.find((item) => item.deed_uid === loss.deed_uid);
      const group = editorGroups(row).find((item) => item.id === 'worker');
      for (const copy of loss.inputs) {
        const index = group.slots.findIndex((asset) => asset && (upgradeUid(asset) === copy.uid || asset.asset_ref === copy.asset_ref));
        if (index < 0 || CERES_STAGE.some((entry) => entry.deed_uid === loss.deed_uid && entry.group_id === 'worker' && entry.slot_index === index)) throw new Error('A source slot changed or already has a staged change. Reopen the plan.');
        copy.source = { deed_uid: loss.deed_uid, slot_index: index };
        copy.asset = group.slots[index];
        copy.asset_ref = copy.asset.asset_ref;
        prepared.push({ copy, row, group, index });
      }
    }
    const expected = (await chainPost('expected-card', { uids: view.selected })).expected;
    const impacts = [];
    for (const loss of chainPlotLosses(copies)) {
      const row = BOARD.plots.find((item) => item.deed_uid === loss.deed_uid);
      impacts.push({ deed_uid: loss.deed_uid, impact: await removalImpact(row, loss.inputs.map((copy) => copy.asset_ref)) });
    }
    if (CURRENT_PLAYER !== view.player || UPGRADE_WINDOW !== view) return;
    if (document.getElementById('combine-dialog-overlay')?.hidden) return;
    for (const { copy, row, index } of prepared) {
      const currentRow = BOARD.plots.find((item) => item.deed_uid === row.deed_uid);
      const occupant = editorGroups(currentRow).find((item) => item.id === 'worker')?.slots[index];
      if (occupant?.asset_ref !== copy.asset_ref || CERES_STAGE.some((entry) => entry.deed_uid === row.deed_uid && entry.group_id === 'worker' && entry.slot_index === index)) throw new Error('A source slot changed. Reopen the plan.');
    }
    const id = mintTransactionRunId().replace(/^run-/, 'cc-');
    const anchorStaked = prepared.some(({ copy }) => copy.uid === view.anchor);
    const plan = createChain({ id, origin: { ...view.origin, asset_uid: view.anchor, stage_back: anchorStaked && view.stageBack !== false }, card: view.card, copies, expected });
    saveChain(id, plan);
    prepared.forEach(({ copy, row, group, index }) => {
      stageForSlot(row.deed_uid, 'worker', index, 'unstake', copy.asset_ref, 0, null, view.card.name, null, null, null, copy.asset);
      const entry = CERES_STAGE.find((item) => item.deed_uid === row.deed_uid && item.group_id === 'worker' && item.slot_index === index);
      entry.chain_id = id;
      entry.chain_input_uid = copy.uid;
      mutateEditorSlot(row, group, index, null);
    });
    impacts.forEach(({ deed_uid, impact }) => { applyRemovalImpactToStage(deed_uid, impact); updateMountedRow(deed_uid); });
    saveChain(id, plan);
    closeCombineDialog();
    UPGRADE_WINDOW = null;
    renderCheckoutSurface();
    stageToast(prepared.length ? 'Upgrade plan staged. Sign the unstake in Changes.' : 'Upgrade plan staged. Review it in Changes.');
  } catch (error) { view.error = error.message; view.loading = false; renderUpgradeWindow(); }
}

async function refreshChain(id, live = false) {
  const plan = chainPlans()[id];
  if (!plan || ['combine_signed', 'combined'].includes(plan.state) || plan.state === 'done' || (plan.state === 'cancelled' && !plan.inputs.some((input) => input.left_out_choice === 'put_back' && !input.return_staged_at))) return;
  const player = CURRENT_PLAYER;
  const serial = chainRefreshSerial;
  const requestId = (CHAIN_REFRESHES.get(id) || 0) + 1;
  CHAIN_REFRESHES.set(id, requestId);
  const anchor = activeChainInputs(plan)[0]?.uid || plan.inputs.find((input) => input.left_out_choice === 'put_back')?.uid;
  if (!anchor) return;
  try {
    const data = await chainPost('inputs', { uid: anchor, plan_uids: plan.inputs.map((input) => input.uid), ...(live ? { live: true } : {}) });
    if (CURRENT_PLAYER !== player || serial !== chainRefreshSerial || CHAIN_REFRESHES.get(id) !== requestId || chainPlans()[id] !== plan) return;
    const refreshed = JSON.parse(JSON.stringify(plan));
    reconcileChainCopies(refreshed, data.copies, data.observed_at);
    for (const input of refreshed.inputs.filter((item) => item.left_out_choice === 'put_back')) {
      const copy = data.copies.find((item) => item.uid === input.uid);
      if (copy?.eligible && copy.kind !== 'staked') input.ready_at = copy.ready_at || data.observed_at;
    }
    const inputs = activeChainInputs(refreshed);
    // The projection route uses the board snapshot; end times above may be fresher.
    try { refreshed.expected = inputs.length >= 2 ? (await chainPost('expected-card', { uids: inputs.map((input) => input.uid) })).expected : {}; }
    catch (_) { refreshed.expected = {}; }
    if (CURRENT_PLAYER !== player || serial !== chainRefreshSerial || CHAIN_REFRESHES.get(id) !== requestId || chainPlans()[id] !== plan) return;
    saveChain(id, refreshed, { refresh: true });
    // leave-outs and later live ends also update the card already placed.
    const placement = CERES_STAGE.find(entry => entry.chain_id === id && entry._asset?.planned);
    if (placement) {
      const expected = refreshed.expected || {};
      Object.assign(placement._asset, { level: expected.level ?? null, bcx: expected.bcx ?? null,
        base_pp: expected.base_pp ?? null, ready_at: deriveChain(refreshed).ready_at });
      const target = BOARD?.plots.find(row => row.deed_uid === placement.deed_uid);
      if (target) { applyStageToRow(target, { force: true }); updateMountedRow(target.deed_uid); }
    }
    renderCheckoutSurface();
  } catch (_) { /* Keep the plan; unknown is never ready. Retry on the next board read. */ }
}

function confirmChainRemoval(plan, inputs, cancel) {
  return new Promise((resolve) => {
    const signed = inputs.filter((input) => input.unstake?.tx_id);
    const overlay = document.createElement('div');
    overlay.className = 'slot-hazard-overlay chain-confirm-overlay';
    const prior = document.activeElement;
    const finish = (choice) => { overlay.remove(); prior?.focus?.(); resolve(choice); };
    overlay.innerHTML = `<div class="slot-hazard-panel" role="dialog" aria-modal="true" aria-labelledby="chain-cancel-title"><header><h3 id="chain-cancel-title">${cancel ? 'Cancel upgrade plan?' : inputs.length > 1 ? `Leave ${inputs.length} copies of ${esc(plan.card.name)} (CC ${num(inputs[0].cc)} each) out of the plan?` : `Leave ${esc(plan.card.name)} (CC ${num(inputs[0].cc)}) out of the plan?`}</h3></header><div class="slot-hazard-body"><p>${signed.length ? 'These copies are already signed to leave land. Cancelling does not reverse the unstake.' : 'The copies and their unsigned unstakes will be removed from this plan.'}</p>${signed.map((input) => `<p>${esc(chainSourceLabel(input))} · ${chainTimeHtml(input.ready_at)}</p>`).join('')}${!cancel && activeChainInputs(plan).length <= 2 ? '<p>This leaves one card, so the plan ends. Choose what happens to every signed copy.</p>' : ''}</div><footer class="chain-footer"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-chain-choice="stay">Keep it in</button>${signed.length ? '<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-case="destructive" data-chain-choice="put_back">Leave out, put it back when ready</button>' : ''}<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-case="destructive" data-chain-choice="keep_off">${signed.length ? 'Leave out, keep it off land' : cancel ? 'Cancel plan' : 'Leave out'}</button></footer></div>`;
    overlay.addEventListener('click', (event) => { const choice = event.target.closest('[data-chain-choice]'); if (choice) finish(choice.dataset.chainChoice); else if (event.target === overlay) finish('stay'); });
    overlay.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish('stay'); return; }
      if (event.key !== 'Tab') return;
      const buttons = [...overlay.querySelectorAll('button')];
      if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0].focus(); }
    });
    document.body.appendChild(overlay);
    overlay.querySelector('button').focus();
  });
}

async function leaveUpgradeInputs(id, index = null) {
  const plan = chainPlans()[id];
  if (!plan || plan.combine || ['combine_signed', 'combined', 'done'].includes(plan.state)) return;
  // `index` may be a list -- a grouped row leaves out every copy of its group at once. The plan is cancelled when
  // fewer than two active copies would remain, exactly as for one copy.
  const chosen = index === null ? [] : [].concat(index).map((item) => plan.inputs[item]).filter(Boolean);
  if (index !== null && !chosen.length) return;
  const leaving = chosen.filter((input) => input.status === 'active').length;
  const cancel = index === null || (leaving > 0 && activeChainInputs(plan).length - leaving < 2);
  const inputs = cancel ? activeChainInputs(plan) : chosen;
  const player = CURRENT_PLAYER;
  const choice = await confirmChainRemoval(plan, inputs, cancel);
  if (choice === 'stay' || player !== CURRENT_PLAYER || chainPlans()[id] !== plan) return;
  for (const input of inputs) {
    const entry = CERES_STAGE.find((item) => item.chain_id === id && item.chain_input_uid === input.uid);
    if (entry && !entry.txId) { await removeCheckoutStage(entry); if (CERES_STAGE.includes(entry)) return; }
    input.status = 'left_out';
    input.left_out_choice = choice;
    input.updated_at = new Date().toISOString();
  }
  if (cancel) {
    const placement = CERES_STAGE.find(entry => entry.chain_id === id && entry.action !== 'unstake');
    if (placement) await removeCheckoutStage(placement);
    plan.state = 'cancelled';
  }
  saveChain(id, plan);
  await materializeChainReturns(id);
  if (!cancel) await refreshChain(id);
  renderCheckoutSurface();
}

export async function handleUpgradeAction(event) {
  const stageBack = event.target.closest('[data-upgrade-stage-back]');
  if (stageBack && UPGRADE_WINDOW) { UPGRADE_WINDOW.stageBack = stageBack.checked; return true; }
  const toggle = event.target.closest('[data-upgrade-copy]');
  if (toggle && UPGRADE_WINDOW) {
    const copy = UPGRADE_WINDOW.copies[Number(toggle.dataset.upgradeCopy)];
    if (copy?.eligible && copy.uid !== UPGRADE_WINDOW.anchor) {
      UPGRADE_WINDOW.selected = toggle.checked ? [...new Set([...UPGRADE_WINDOW.selected, copy.uid])] : UPGRADE_WINDOW.selected.filter((uid) => uid !== copy.uid);
      UPGRADE_WINDOW.target = 'manual';
      await updateUpgradeSelection();
    }
    return true;
  }
  const button = event.target.closest('[data-upgrade-action]');
  if (!button) return false;
  event.preventDefault();
  event.stopPropagation();
  const action = button.dataset.upgradeAction;
  if (action === 'open') {
    const picker = S.CERES_PICKER;
    const card = button.dataset.upgradeIndex === 'occupant' ? picker?.currentOccupant : picker?.candidates[Number(button.dataset.upgradeIndex)];
    if (card) await openUpgradeWindow(card, { kind: button.dataset.upgradeIndex === 'occupant' ? 'tile' : 'picker', deed_uid: picker.deedUid, slot_index: picker.slotIndex });
  } else if (action === 'close') { ++upgradeRequest; UPGRADE_WINDOW = null; closeCombineDialog(); }
  else if (action === 'review') await openPlanReview(button.dataset.chainId);
  else if (action === 'check-combine') await verifyPlanCombine(button.dataset.chainId);
  else if (action === 'use-result' || action === 'cancel-placement') await resolvePlanMismatch(button.dataset.chainId, action === 'use-result');
  else if (action === 'finish') {
    const plan = chainPlans()[button.dataset.chainId];
    if (plan?.state === 'combined' && !CERES_STAGE.some(entry => entry.chain_id === plan.id && entry.txId)) {
      for (const entry of [...CERES_STAGE].filter(entry => entry.chain_id === plan.id && entry.action !== 'unstake')) await removeCheckoutStage(entry);
      saveChain(plan.id, { ...plan, state: 'done' }); renderCheckoutSurface();
    }
  }
  else if (action === 'stage') await stageUpgradePlan();
  else if (action === 'level' && UPGRADE_WINDOW) {
    UPGRADE_WINDOW.target = Number(button.dataset.level);
    UPGRADE_WINDOW.selected = selectChainLevel(UPGRADE_WINDOW.copies, UPGRADE_WINDOW.anchor, UPGRADE_WINDOW.thresholds[UPGRADE_WINDOW.target - 1]);
    await updateUpgradeSelection();
  } else if (action === 'manual' && UPGRADE_WINDOW) { UPGRADE_WINDOW.target = 'manual'; renderUpgradeWindow(); }
  else if (action === 'cancel' || action === 'leave') await leaveUpgradeInputs(button.dataset.chainId, action === 'leave' ? Number(button.dataset.chainInput) : null);
  else if (action === 'leave-group') await leaveUpgradeInputs(button.dataset.chainId, String(button.dataset.chainInputs || '').split(' ').filter(Boolean).map(Number));
  else if (action === 'show-more') { CHAIN_EXPANDED.add(`${button.dataset.chainId}:${button.dataset.chainStep}`); renderCheckoutSurface(); }
  else if (action === 'keep-off') {
    const plan = chainPlans()[button.dataset.chainId];
    plan?.inputs.forEach((input) => { if (input.left_out_choice === 'put_back' && !input.return_staged_at) input.left_out_choice = 'keep_off'; });
    if (plan) { saveChain(button.dataset.chainId, plan); renderCheckoutSurface(); }
  } else if (action === 'retry') {
    const plan = chainPlans()[button.dataset.chainId];
    const input = plan?.inputs[Number(button.dataset.chainInput)];
    const entry = CERES_STAGE.find((item) => item.chain_id === button.dataset.chainId && item.chain_input_uid === input?.uid);
    if (entry && !entry.txId) { delete entry.refused_at; delete entry.refusal_reason; input.problem = null; saveChain(button.dataset.chainId, plan); renderCheckoutSurface(); }
  }
  return true;
}

window.addEventListener('ceres:chain-unstake-confirmed', (event) => { for (const id of event.detail.ids) refreshChain(id, true).then(() => materializeChainReturns(id)); });
window.addEventListener('ceres:chain-input-left-out', (event) => { Promise.resolve().then(() => refreshChain(event.detail.id)); });
window.addEventListener('ceres:chain-plans-restored', () => { if (stagePlansReady()) for (const id of Object.keys(chainPlans())) refreshChain(id).then(() => materializeChainReturns(id)); });
window.addEventListener('ceres:player-changed', () => { ++chainRefreshSerial; ++upgradeRequest; UPGRADE_WINDOW = null; CHAIN_EXPANDED.clear(); });

window.addEventListener('ceres:chain-countdown-ended', () => renderCheckoutSurface());

// D-7: a return request survives while its signed unstake is in flight.
async function materializeChainReturns(id) {
  const plan = chainPlans()[id];
  if (!plan || !stagePlansReady()) return;
  let changed = false;
  for (const input of plan.inputs) {
    if (input.left_out_choice !== 'put_back' || input.return_staged_at || !input.unstake?.confirmed_at || !input.source) continue;
    const row = BOARD?.plots?.find((item) => item.deed_uid === input.source.deed_uid);
    if (!row || CERES_STAGE.some((entry) => entry.deed_uid === input.source.deed_uid && entry.group_id === 'worker' && entry.slot_index === input.source.slot_index)) continue;
    const occupant = editorGroups(row).find((group) => group.id === 'worker')?.slots[input.source.slot_index];
    if (occupant && upgradeUid(occupant) !== input.uid) continue;
    if (stageForSlot(input.source.deed_uid, 'worker', input.source.slot_index, 'stake', input.asset_ref || input.uid, null, null, plan.card.name, null, null,
      { ready: !!input.ready_at && Date.parse(input.ready_at) <= Date.now(), ready_at: input.ready_at, state: 'cooling' }, input.asset) === false) continue;
    applyStageToRow(row);
    updateMountedRow(row.deed_uid);
    input.return_staged_at = new Date().toISOString();
    changed = true;
  }
  changed = await stageBackPlannedCard(id, plan) || changed;
  if (changed) { saveChain(id, plan); renderCheckoutSurface(); }
}

// once the clicked worker's unstake is confirmed, its slot gets the planned card, priced by the picker's own
// read; its stake signs after the combine. Done once: an undo in Changes is respected, and a card placed by hand wins.
async function stageBackPlannedCard(id, plan) {
  const source = chainStageBackSource(plan);
  const anchor = source && activeChainInputs(plan).find((input) => input.uid === plan.origin.asset_uid);
  if (!anchor?.unstake?.confirmed_at) return false;
  const done = () => { plan.origin = { ...plan.origin, stage_back_at: new Date().toISOString() }; return true; };
  if (CERES_STAGE.some((entry) => entry.asset_ref === `planned:${id}`)) return done();
  const row = BOARD?.plots?.find((item) => item.deed_uid === source.deed_uid);
  if (!row || CERES_STAGE.some((entry) => entry.deed_uid === source.deed_uid && entry.group_id === 'worker' && entry.slot_index === source.slot_index)) return false;
  if (editorGroups(row).find((group) => group.id === 'worker')?.slots[source.slot_index]) return false;
  let candidate = null;
  const player = CURRENT_PLAYER, serial = chainRefreshSerial;
  try {
    const picker = { groupId: 'worker', deedUid: source.deed_uid, slotIndex: source.slot_index, sort: 'construction_pp', filters: { bucket: 'all' } };
    const response = await fetch(playerQueryAppend(candidateQuery(picker, 0)));
    if (response.ok) candidate = ((await response.json()).candidates || []).find((item) => item.planned && item.chain_id === id) || null;
  } catch (error) { /* staged unpriced; the board prices it on the next change */ }
  if (chainPlans()[id] !== plan || CERES_STAGE.some((entry) => entry.asset_ref === `planned:${id}`)) return false;
  // (audit #6): the read above waited; a card the maintainer placed meanwhile (or another account / capture) wins -- re-check the
  // account, the target's staged entry and its live occupant before placing anything.
  if (CURRENT_PLAYER !== player || serial !== chainRefreshSerial) return false;
  if (CERES_STAGE.some((entry) => entry.deed_uid === source.deed_uid && entry.group_id === 'worker' && entry.slot_index === source.slot_index)) return false;
  const liveRow = BOARD?.plots?.find((item) => item.deed_uid === source.deed_uid);
  if (!liveRow || editorGroups(liveRow).find((group) => group.id === 'worker')?.slots[source.slot_index]) return false;
  const incoming = editorOccupant(candidate || { ...plan.card, level: plan.expected?.level, base_pp: plan.expected?.base_pp, planned: true, chain_id: id, asset_ref: `planned:${id}` }, 'worker');
  if (stageForSlot(source.deed_uid, 'worker', source.slot_index, 'stake', incoming.asset_ref, null, null, incoming.name, null, candidate?.base_pp_delta || null,
    { ready: false, state: 'planned', ready_at: null, reason: 'after the combine' }, { ...incoming, plot_impact: candidate?.plot_impact }) === false) return false;
  applyStageToRow(liveRow);
  updateMountedRow(liveRow.deed_uid);
  return done();
}


// a read-only ledger; the main Sign button owns the warning and wallet.
export function planReviewHtml(plan) {
  const expected = plan.expected;
  return `<div class="chain-editor"><div class="chain-hero">${editorCardImageHtml(plan.card)}<div><h4>${esc(plan.card.name)} · Review</h4><p>Level ${num(expected.level)} · CC ${num(expected.bcx)} · Base PP ${num(expected.base_pp)}</p><p>Locked to your upgrade plan. Sign it in Changes.</p></div></div><div class="chain-copies">${activeChainInputs(plan).map(input => `<div class="chain-copy is-${input.uid === expected.survivor_uid ? 'survivor' : 'consumed'}"><strong>${esc(plan.card.name)}</strong><span>CC ${num(input.cc)} · ${input.uid === expected.survivor_uid ? 'Kept' : 'Used up'}</span></div>`).join('')}</div><footer class="chain-footer"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-upgrade-action="close">Close</button></footer></div>`;
}

async function openPlanReview(id) {
  const plan = chainPlans()[id];
  if (!plan || plan.combine) return;
  ++upgradeRequest; UPGRADE_WINDOW = null;
  openCombinePlanDialog(`Review — ${plan.card.name}`, planReviewHtml(plan));
  const player = CURRENT_PLAYER;
  try {
    const reviewed = { ...plan, problem: null };
    await buildPlanCombine(reviewed);
    if (player === CURRENT_PLAYER && chainPlans()[id] === plan) saveChain(id, reviewed);
  } catch (_) { /* The fixed ledger stays visible; a failed read never authorizes signing. */ }
  renderCheckoutSurface();
}
