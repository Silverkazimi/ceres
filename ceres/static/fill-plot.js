// ( Fill plot, ): a modal in the
// Harvest / Changes style that asks the server for the best fill of ONE plot from the account's ready assets, shows
// the proposal in full, and stages it only on Accept -- through the ordinary stage (stageForSlot), so Undo, badges
// kept plans and Changes all apply as for any other staged move. Cancel changes nothing.
import { BOARD, adoptLatestCapture, playerQueryAppend } from './board.js';
import { candidateDelta, signedPp } from './candidates.js';
import { renderCheckoutSurface } from './checkout.js';
import { editorGroups, editorOccupant, renderPreviewSatellite, renderPreviewTile, stagePreviewState } from './editor.js';
import { esc, formatPlotLabel, num } from './format.js';
import { territoryCellHtml } from './icons.js';
import { EXPANDED_DATA_LOADED, loadPlotDetail, updateMountedRow } from './rows.js';
import { CERES_STAGE, mutateEditorSlot, stageForSlot, stagedWorksiteBuild, stageMovePayload, stageToast } from './staging.js';
import { removeCheckoutStage } from './checkout.js';
import { holdTooltipsUntilPointerMoves, tipAttrs } from './tooltip.js';

const X_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const SWITCHES = [
  ['land_cards', 'Land cards'], ['workers', 'Workers'], ['runis', 'Runis'], ['totems', 'Totems'], ['titles', 'Titles'], ['power_cores', 'Power Cores'],
];
// the rules of the search on their own line (three aligned lines: Use / Also use / Rules).
const RULES = [
  ['keep_current', 'Keep current cards'],
  ['skip_staged', 'Skip staged cards'], // on by default
  ['terrain_bonus_only', 'Terrain bonus only'],
];
// the second row -- off by default, each one a real switch.
const ALSO = [
  ['allow_cooling', 'Cooling cards', 'Cards in their land cooldown: staged red, they wait for it.'],
  ['allow_staked', 'Cards on other plots', 'Staged orange: their unstake there signs now, the stake here waits for the cooldown.'],
  ['allow_survival', 'Survival cards', 'Cards dead from Survival: staged red until they are back.'],
]; // no "Rented cards" switch -- Fill never uses rented-in cards
const M = { open: false, deedUid: null, settings: null, proposal: null, loading: false, error: null, token: 0, returnFocus: null, excluded: new Map() }; // excluded: slot key -> the move the maintainer clicked away ()

function defaults() {
  return { land_cards: true, workers: true, runis: true, totems: true, titles: true, power_cores: true, keep_current: false, skip_staged: true, terrain_bonus_only: false, min_gain: 0, allow_cooling: false, allow_staked: false, allow_survival: false };
}
function overlay() {
  let el = document.getElementById('fill-plot-overlay');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'fill-plot-overlay';
  el.className = 'harvest-overlay';
  el.hidden = true;
  el.innerHTML = '<div class="harvest-panel changes-panel fill-panel" role="dialog" aria-modal="true" aria-labelledby="fill-plot-title"></div>';
  el.addEventListener('click', (event) => {
    if (event.target === el) { closeFillPlotModal({ returnFocus: false }); return; }
    const slot = event.target.closest('[data-fp-slot]');
    if (slot) { toggleExcluded(slot.dataset.fpSlot); return; }
    const action = event.target.closest('[data-fp]');
    if (!action) return;
    if (action.dataset.fp === 'close') closeFillPlotModal({ returnFocus: false });
    else if (action.dataset.fp === 'accept') acceptProposal();
    else if (action.dataset.fp === 'retry') requestProposal();
  });
  el.addEventListener('change', (event) => {
    const number = event.target.closest('[data-fp-number]');
    if (number) { M.settings[number.dataset.fpNumber] = Math.max(0, Number(number.value) || 0); requestProposal(); return; }
    const box = event.target.closest('[data-fp-setting]');
    if (!box) return;
    M.settings[box.dataset.fpSetting] = box.checked;
    requestProposal();
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && M.open) { event.preventDefault(); closeFillPlotModal(); } });
  el.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    const focusable = [...el.querySelectorAll('button:not([disabled]), input:not([disabled])')];
    if (!focusable.length) return;
    const first = focusable[0]; const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.body.appendChild(el);
  return el;
}

export function openFillPlotModal(row) {
  if (!row) return;
  const el = overlay();
  M.returnFocus = document.activeElement;
  M.open = true; M.deedUid = row.deed_uid; M.settings = defaults(); M.proposal = null; M.error = null; M.excluded = new Map();
  el.hidden = false;
  render();
  el.querySelector('.harvest-x')?.focus();
  requestProposal();
  // the tiles come from the plot's own detail (workers, items); without it every tile read "Empty".
  if (!EXPANDED_DATA_LOADED.has(row.deed_uid)) {
    const deedUid = row.deed_uid;
    loadPlotDetail(row).then(() => { if (M.open && M.deedUid === deedUid) render(); }).catch(() => {});
  }
}
// focus goes back to the Fill plot button only after Escape (keyboard); after a click it would open the
// button's tooltip and leave it standing.
export function closeFillPlotModal({ returnFocus = true } = {}) {
  const el = document.getElementById('fill-plot-overlay');
  if (el) el.hidden = true;
  M.open = false; M.token += 1; M.proposal = null;
  const target = returnFocus && M.returnFocus?.isConnected ? M.returnFocus : null;
  if (target) target.focus?.(); else document.activeElement?.blur?.();
  holdTooltipsUntilPointerMoves();
}

function plannedMoves() {
  return CERES_STAGE.filter((entry) => entry.deed_uid === M.deedUid && !entry.txId && !entry._synthetic).map(stageMovePayload);
}
function stagedWorksiteTarget() {
  return stagedWorksiteBuild(M.deedUid)?.target || null;
}
// every asset staged as incoming anywhere else in the browser.
function stagedElsewhereRefs() {
  if (!M.settings?.skip_staged) return []; // otherwise the proposal may move an unsigned stage here on Accept
  return [...new Set(CERES_STAGE.filter((entry) => entry.deed_uid !== M.deedUid && !entry._synthetic && entry.action !== 'unstake' && entry.asset_ref).map((entry) => String(entry.asset_ref)))];
}

async function requestProposal() {
  const token = ++M.token;
  M.loading = true; M.error = null; M.proposal = null; M.priced = null;
  render();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    let response; let payload;
    try {
      response = await fetch(playerQueryAppend(`/api/plots/${encodeURIComponent(M.deedUid)}/fill-proposal`), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capture_generated_at: BOARD?.generated_at || null, settings: M.settings, planned_moves: plannedMoves(), staged_worksite: stagedWorksiteTarget(),
          excluded_asset_refs: [...M.excluded.values()].map((move) => String(move.asset_ref)), reserved_asset_refs: stagedElsewhereRefs(),
          hold_slots: [...M.excluded.keys()].map((key) => ({ group_id: key.split('|')[0], slot_index: Number(key.split('|')[1]) || 0 })) }),
      });
      payload = await response.json().catch(() => ({}));
    } catch (error) {
      if (token !== M.token) return;
      M.loading = false; M.error = 'The proposal request did not answer.'; render(); return;
    }
    if (token !== M.token) return;
    if (response.status === 409 && payload?.detail?.code === 'capture_superseded') { await adoptLatestCapture(); continue; } // idiom
    if (!response.ok) {
      const detail = payload?.detail;
      M.loading = false; M.error = typeof detail === 'string' ? detail : detail?.message || `The server refused the request (HTTP ${response.status}).`;
      render(); return;
    }
    M.loading = false; M.proposal = payload; render(); return;
  }
  if (token === M.token) { M.loading = false; M.error = 'The board kept changing; try again.'; render(); }
}

// A proposed item from the server carries no rarity; a totem's is its name ("Rare Totem"), which the totem art needs.
function candidateAsset(candidate, groupId) {
  const c = { ...(candidate || {}) };
  if (groupId === 'totem' && !c.rarity) c.rarity = String(c.name || '').split(' ')[0].toLowerCase();
  return c;
}
// Base PP with the terrain bonus already added, as a badge under the tile (the plot view's own style).
// the after-cap figure, like the plot view -- a card clipped by the plot's Base PP cap shows its clipped
// figure in red and says so.
function basePpBadge(asset, afterCapByRef = {}) {
  if (!asset) return '<span class="editor-base-pp-cell" aria-hidden="true"></span>';
  const base = Number(asset.base_pp);
  const terrain = Number(asset.terrain_boost) || 0;
  if (!Number.isFinite(base)) return '<span class="editor-base-pp-cell" aria-hidden="true"></span>';
  const afterCap = Number(afterCapByRef[String(asset.asset_ref)] ?? asset.base_pp_after_cap);
  const clipped = Number.isFinite(afterCap) && afterCap < base - 1e-6;
  const counted = clipped ? afterCap : base;
  const value = counted * (1 + terrain);
  const terrainText = terrain ? `${terrain > 0 ? '+' : '−'}${Math.round(Math.abs(terrain) * 100)}% terrain` : 'neutral terrain';
  const tip = tipAttrs({ title: 'Boostable PP', body: `${num(base)} Base PP · ${terrainText}${clipped ? ` · the plot's cap takes ${num(base - afterCap)}` : ''}. What this plot's boosts multiply.` });
  const cls = clipped ? ' is-cap-clipped' : terrain > 0 ? ' is-terrain-bonus' : terrain < 0 ? ' is-terrain-penalised' : '';
  return `<span class="editor-base-pp-cell is-filled${cls}"${tip}><b>${esc(num(value))}</b></span>`;
}

// a click on a proposed tile leaves that card out (its slot stays empty, the totals are re-priced by the
// server); a click on the dimmed tile brings it back.
// leaving a card out never re-runs the search -- the remaining proposal is re-priced as it is.
function toggleExcluded(key) {
  if (M.excluded.has(key)) M.excluded.delete(key);
  else {
    const move = (M.proposal?.moves || []).find((item) => `${item.group_id}|${item.group_id === 'worker' ? item.slot_index : 0}` === key);
    if (!move) return;
    M.excluded.set(key, move);
  }
  repriceRemaining();
}
function keptMoves() {
  return (M.proposal?.moves || []).filter((move) => !M.excluded.has(`${move.group_id}|${move.group_id === 'worker' ? move.slot_index : 0}`));
}
async function repriceRemaining() {
  const token = ++M.token;
  M.pricing = true; render();
  const moves = keptMoves().map((move) => ({ group_id: move.group_id, slot_index: move.slot_index, action: move.action, asset_ref: move.asset_ref, evict_asset_ref: move.evict_asset_ref || null,
    base_pp: (move.candidate || {}).base_pp ?? null, splinter: (move.candidate || {}).splinter ?? null,
    source_deed_uid: (move.candidate || {}).source_deed_uid ?? null, name: move.name || (move.candidate || {}).name || null }));
  try {
    const response = await fetch(playerQueryAppend(`/api/plots/${encodeURIComponent(M.deedUid)}/fill-proposal`), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ capture_generated_at: BOARD?.generated_at || null, planned_moves: plannedMoves(), price_moves: moves,
        staged_worksite: stagedWorksiteTarget() }),
    });
    const payload = await response.json().catch(() => ({}));
    if (token !== M.token) return;
    if (response.ok) M.priced = payload; else M.error = typeof payload?.detail === 'string' ? payload.detail : 'The re-pricing did not answer.';
  } catch (error) { if (token === M.token) M.error = 'The re-pricing did not answer.'; }
  if (token === M.token) { M.pricing = false; render(); }
}

function plotBadge(row) {
  const { cls } = territoryCellHtml(row?.territory, row?.deed_uid);
  return `<span class="ch-plot-badge ${cls}" style="cursor:default">${esc(formatPlotLabel(row?.plot || M.deedUid))}</span>`;
}
function ppClass(value) { return Number.isFinite(value) ? (value > 0 ? ' is-up' : value < 0 ? ' is-down' : '') : ''; }
function whereText(move) {
  const where = String(move.group_id).replace('_', ' ');
  return move.group_id === 'worker' ? `${where} slot ${Number(move.slot_index) + 1}` : where;
}

// the proposal as the plot's own tile row -- workers in a row, the items small at the side; proposed tiles
// pulse green like a staged change, everything else stays as it is on the board.
function movesHtml(row, proposal) {
  const groups = editorGroups(row);
  const byKey = new Map(proposal.moves.map((move) => [`${move.group_id}|${move.group_id === 'worker' ? move.slot_index : 0}`, move]));
  // the highlight is the card's stage state (green / orange / red), from the same server cooldown the board reads.
  const stateOf = (move) => (move ? stagePreviewState((move.candidate || {}).cooldown, (move.candidate || {}).source_deed_uid || null) : false);
  const occupantAt = (group, index) => {
    const key = `${group.id}|${index}`;
    const excluded = M.excluded.get(key) || null;
    const move = byKey.get(key) || null;
    const shown = move || excluded;
    return { move, excluded, occupant: shown ? editorOccupant(candidateAsset(shown.candidate, group.id), group.id) : (group.slots[index] || null) };
  };
  // A proposed tile is clickable (leave it out); an excluded one too (bring it back). Wrapped, never a tile action.
  const clickable = (html, key, excluded) => `<span class="fp-slot${excluded ? ' is-fp-excluded' : ''}" data-fp-slot="${esc(key)}" role="button" tabindex="0"${tipAttrs(excluded ? { title: 'Left out', body: 'Click to put it back into the proposal.' } : { title: 'Proposed', body: 'Click to leave it out.' })}>${html}</span>`;
  const tile = (group, index) => {
    const { move, excluded, occupant } = occupantAt(group, index);
    const html = renderPreviewTile(group, occupant, { highlight: excluded ? false : stateOf(move), tip: !(move || excluded) }); // one tooltip per slot
    return move || excluded ? clickable(html, `${group.id}|${index}`, Boolean(excluded)) : html;
  };
  const workers = groups.find((group) => group.id === 'worker');
  const capacity = Math.max(workers?.capacity || 0, proposal.worker_capacity || 0);
  const workerTiles = Array.from({ length: capacity }, (_, index) => tile(workers, index)).join('');
  const ppBadges = Array.from({ length: capacity }, (_, index) => { const at = occupantAt(workers, index); return basePpBadge(at.excluded ? null : at.occupant, proposal.after_cap || {}); }).join('');
  // the item tiles are the plot view's own -- size, style, captions, the divider, the Power slot with its
  // satellite -- and their badges say what the plot view's say (a Runi's Base PP, an item's boost).
  const badge = (figure, tip) => figure
    ? `<span class="editor-base-pp-cell is-filled"${tipAttrs(tip)}><b>${esc(figure)}</b></span>`
    : '<span class="editor-base-pp-cell" aria-hidden="true"></span>';
  const pct = (value) => (Number.isFinite(Number(value)) ? `+${num(Number(value) * 100)}%` : '');
  const runi = groups.find((item) => item.id === 'runi');
  const core = groups.find((item) => item.id === 'power_core');
  const runiAt = runi ? occupantAt(runi, 0) : { move: null, occupant: null };
  const coreAt = core ? occupantAt(core, 0) : { move: null, occupant: null };
  // an empty item tile says when the account has none of that asset to stake.
  const avail = proposal.item_available || {};
  const emptyLabelFor = (id, label, fallback) => (avail[id] === 0 ? `${label} unavailable` : fallback);
  // the plot view's own rule -- the Runi is the large tile with the Power Core as its satellite; with no Runi
  // (live or proposed) the Power Core takes the large tile and the Runi slot becomes the satellite.
  const runiLarge = Boolean(runiAt.occupant) || !coreAt.occupant;
  const large = runiLarge ? { group: runi, at: runiAt, key: 'runi|0', label: emptyLabelFor('runi', 'Runi', 'Power slot · Add Runi') } : { group: core, at: coreAt, key: 'power_core|0', label: emptyLabelFor('power_core', 'Power Core', 'Power slot · Add Power Core') };
  const sat = runiLarge ? { group: core, at: coreAt, key: 'power_core|0', unavailable: avail.power_core === 0 } : { group: runi, at: runiAt, key: 'runi|0', unavailable: avail.runi === 0 };
  const largeHtml = renderPreviewTile(large.group, large.at.occupant, { highlight: large.at.excluded ? false : stateOf(large.at.move), emptyLabel: large.label, tip: !(large.at.move || large.at.excluded) });
  const satHtml = !runiLarge && !sat.at.occupant ? '' : renderPreviewSatellite(sat.group, sat.at.occupant, { highlight: sat.at.excluded ? false : stateOf(sat.at.move), unavailable: sat.unavailable, tip: !(sat.at.move || sat.at.excluded) }); // no empty Runi bubble over a Power Core
  const powerTile = `<section class="slot-power" aria-label="Power slot"><div class="slot-power-stage"><div class="slot-power-large">${large.at.move || large.at.excluded ? clickable(largeHtml, large.key, Boolean(large.at.excluded)) : largeHtml}</div>${sat.at.move || sat.at.excluded ? clickable(satHtml, sat.key, Boolean(sat.at.excluded)) : satHtml}</div></section>`;
  const powerBadge = runiAt.occupant && Number.isFinite(Number(runiAt.occupant.base_pp))
    ? badge(num(Number(runiAt.occupant.base_pp)), { title: `Runi Base PP ${num(Number(runiAt.occupant.base_pp))}`, body: 'Counts on top of the plot cap.' })
    : badge('', null);
  const itemTile = (id) => {
    const group = groups.find((item) => item.id === id);
    if (!group) return { tile: '', badge: '' };
    const { move, excluded, occupant } = occupantAt(group, 0);
    const boost = occupant && !excluded ? pct(occupant.boost) : '';
    const scope = id === 'title' ? 'every worker' : 'this plot';
    const html = renderPreviewTile(group, occupant, { highlight: excluded ? false : stateOf(move), emptyLabel: emptyLabelFor(id, group.label, 'Empty'), tip: !(move || excluded) });
    return { tile: move || excluded ? clickable(html, `${id}|0`, Boolean(excluded)) : html,
      badge: badge(boost, occupant ? { title: occupant.name, body: `${boost} production for ${scope}` } : null) };
  };
  const totem = itemTile('totem'); const title = itemTile('title');
  const itemTiles = `${powerTile}${totem.tile}${title.tile}`;
  const itemBadges = `${powerBadge}${totem.badge}${title.badge}`;
  const empty = !proposal.moves.length
    ? `<p class="hm-dim">Nothing to change. ${proposal.empty_slots_left ? 'No ready card would raise this plot\'s Total PP with the current switches.' : 'This plot is already as full as your ready cards allow.'}</p>` : '';
  const worksitePrompt = proposal.worksite_prompt ? `<p class="hm-dim">${esc(proposal.worksite_prompt)}</p>` : '';
  return `<div class="hm-card"><div class="fp-tiles slot-grid">${workerTiles}<span class="slot-grid-divider" aria-hidden="true"></span>${itemTiles}</div>`
    + `<div class="fp-badge-row editor-base-pp-row" aria-label="Per-card Boostable PP">${ppBadges}<span class="editor-base-pp-divider" aria-hidden="true"></span>${itemBadges}</div>${worksitePrompt}${empty}</div>`;
}

function render() {
  const el = document.getElementById('fill-plot-overlay');
  if (!el || !M.open) return;
  const panel = el.querySelector('.fill-panel');
  const row = BOARD?.plots.find((plot) => plot.deed_uid === M.deedUid);
  const sub = [row?.terrain, row?.worksite_type].filter(Boolean).map(esc).join(' · ');
  const focused = document.activeElement?.dataset?.fpSetting;
  // everything on one row -- the switches, then the not-yet-available ones; the explanations are tooltips.
  const tips = { keep_current: { title: 'Keep current cards', body: 'Only empty slots are filled; no card on the plot is replaced.' },
    skip_staged: { title: 'Skip staged cards', body: 'A card already staged for any plot (in Changes) is not proposed.' },
    terrain_bonus_only: { title: 'Terrain bonus only', body: 'Only workers whose splinter this plot boosts are proposed; neutral ones are left out.' } };
  const switch_ = ([key, label]) => `<label class="hm-check"${tipAttrs(tips[key] || { title: label, body: 'Off: no card of this kind in the proposal; one already on the plot may be replaced.' })}><input type="checkbox" data-fp-setting="${key}"${M.settings[key] ? ' checked' : ''}> ${esc(label)}</label>`;
  const switches = SWITCHES.map(switch_).join('');
  const rules = RULES.map(switch_).join('');
  const also = ALSO.map(([key, label, body]) => `<label class="hm-check"${tipAttrs({ title: label, body })}><input type="checkbox" data-fp-setting="${key}"${M.settings[key] ? ' checked' : ''}> ${esc(label)}</label>`).join('');
  // the minimum gain a slot must bring (PP) -- below it the slot is left as it is.
  const minGain = `<label class="hm-check fp-min"${tipAttrs({ title: 'Min gain (PP)', body: 'A slot is only changed when it gains at least this much Total PP.' })}>Min gain <input type="number" min="0" step="1000" data-fp-number="min_gain" value="${esc(String(M.settings.min_gain || 0))}"></label>`;
  const settingsCard = `<div class="hm-card fp-settings"><div class="hm-checks fp-row" data-fp-span="2"><span class="hm-lbl">Use</span>${switches}</div><div class="hm-checks fp-row" data-fp-span="4"><span class="hm-lbl">Also use</span>${also}</div><div class="hm-checks fp-row" data-fp-span="3"><span class="hm-lbl">Rules</span>${rules}${minGain}</div></div>`;
  let body;
  let note = '';
  let accept = '';
  if (M.loading) {
    body = '<div class="hm-card" aria-busy="true"><p class="hm-dim"><span class="spinner" aria-hidden="true"></span> Searching your ready cards and items…</p></div>';
    note = 'Searching…';
  } else if (M.error) {
    body = `<div class="hm-card"><p class="hm-dim">${esc(M.error)} <button type="button" class="hm-btn is-ghost ch-btn ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-fp="retry">Try again</button></p></div>`;
    note = 'No proposal.';
  } else if (M.proposal) {
    const p = { ...M.proposal, ...(M.priced || {}) }; // figures of the kept set when something is left out
    // the plot's figures after the proposal -- Base PP, Boostable PP, Total PP -- and the gain.
    const figure = (label, value, tip, cls = '') => `<div${tipAttrs(tip)}><span class="hm-lbl">${esc(label)}</span><b class="${cls}">${esc(Number.isFinite(value) ? num(value) : '—')}</b></div>`;
    // the four figures as one strip at the top of the tiles card; the search note goes to the footer.
    // Base PP is always shown against the plot's cap -- at the cap is the goal, over it is a loss and reads red.
    const cap = Number(p.base_cap) || 100000;
    const over = Number.isFinite(p.base_after) && p.base_after > cap + 1e-6;
    const atCap = !over && Number.isFinite(p.base_after) && Math.abs(p.base_after - cap) < 1e-6;
    const capBody = over ? `Over the cap by ${num(p.base_after - cap)}: that much Base PP counts for nothing.` : atCap ? 'Exactly at the plot\'s Base PP cap — the most that can count.' : `${num(cap - p.base_after)} still fits under the plot\'s Base PP cap.`;
    // what the plots a card is taken from would lose, each priced as one removal by the server, and the net.
    const donors = (p.donors || []).filter((d) => d.names?.length);
    const donorLoss = donors.reduce((sum, d) => sum + (d.known ? Number(d.harvest_pp_delta) || 0 : 0), 0);
    const unknownDonor = donors.some((d) => !d.known);
    const donorBadge = (d) => { const drow = BOARD?.plots.find((plot) => plot.deed_uid === d.deed_uid); const { cls } = territoryCellHtml(drow?.territory, d.deed_uid); return `<span class="ch-plot-badge ${cls}" style="cursor:default">${esc(formatPlotLabel(drow?.plot || d.plot || d.deed_uid))}</span>`; };
    const donorsHtml = donors.length
      ? `<div class="fp-donors"><span class="hm-lbl">Other plots lose</span>${donors.map((d) => `<span class="fp-donor">${donorBadge(d)} <b class="ch-status is-down">${esc(d.known ? signedPp(d.harvest_pp_delta) : '—')} PP</b> <small class="hm-dim">${esc(d.names.join(', '))}</small></span>`).join('')}`
        + `<span class="fp-donor fp-net"><span class="hm-lbl">Net across plots</span><b class="ch-status${ppClass(p.gain + donorLoss)}">${esc(unknownDonor ? '—' : signedPp(p.gain + donorLoss))} PP</b></span></div>` : '';
    const totals = `<div class="fp-totals">`
      + `<div${tipAttrs({ title: 'Base PP against the cap', body: capBody })}><span class="hm-lbl">Base PP</span><b class="${over ? 'ch-status is-down' : ''}">${esc(num(p.base_after))}</b><small class="hm-dim">of ${esc(num(cap))} cap</small></div>`
      + figure('Boostable PP', p.boostable_after, { title: 'Boostable PP', body: 'What the boosts multiply: Base PP after the cap, then the terrain bonus. Terrain is applied after the cap, so this can stand above it.' })
      + figure('Total PP', p.proposed_total, { title: 'Total PP', body: `After the proposal · now ${num(p.planned_total)}${p.planned_total !== p.current_total ? ' with your staged changes' : ''}.` })
      + `<div><span class="hm-lbl">Gain</span><b class="ch-status${ppClass(p.gain)}">${esc(signedPp(p.gain))} PP</b></div></div>${donorsHtml}`;
    body = movesHtml(row, p).replace('<div class="hm-card">', `<div class="hm-card fp-result">${totals}`);
    const n = keptMoves().length;
    const left = M.excluded.size ? ` · ${M.excluded.size} left out` : '';
    const search = `Best found from ${num(p.search.pool_seen)} ready candidates${p.empty_slots_left ? ` · ${p.empty_slots_left} slot${p.empty_slots_left === 1 ? ' stays' : 's stay'} empty${p.cap_reached ? ' (Base PP cap reached)' : Number.isFinite(p.cap_room) && p.empty_slots_left ? ` (only ${num(p.cap_room)} Base PP left under the cap)` : ''}` : ''}.`;
    note = M.pricing ? 'Re-pricing…' : n ? `${n} change${n === 1 ? '' : 's'} staged on Accept, not signed${left} · ${search}` : `Nothing to accept${left} · ${search}`;
    accept = `<button type="button" class="hm-btn is-primary ceres-btn ceres-btn--primary" data-control-density="comfortable" data-fp="accept"${n && !M.pricing ? '' : ' disabled'}>Accept ${n ? `${n} change${n === 1 ? '' : 's'}` : ''}</button>`;
  } else body = '';
  const scroll = panel.querySelector('.hm-body')?.scrollTop || 0;
  panel.innerHTML = `<header class="hm-headr"><div><h2 id="fill-plot-title">Fill plot ${plotBadge(row)}</h2><p>${sub}</p></div><button type="button" class="harvest-x ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" data-fp="close" aria-label="Close">${X_ICON}</button></header>`
    + `<div class="hm-body">${settingsCard}${body}</div>`
    + `<footer class="hm-foot"><span class="hm-note">${esc(note)}</span><span class="hm-btns"><button type="button" class="hm-btn is-ghost ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-fp="close">Cancel</button>${accept}</span></footer>`;
  const newBody = panel.querySelector('.hm-body');
  if (newBody) newBody.scrollTop = scroll;
  if (focused) panel.querySelector(`[data-fp-setting="${focused}"]`)?.focus();
}

// Accept: stage every proposed move through the ordinary stage, exactly as the picker does for one card.
async function acceptProposal() {
  const proposal = M.proposal;
  const row = BOARD?.plots.find((plot) => plot.deed_uid === M.deedUid);
  const kept = keptMoves();
  if (!proposal || !row || !kept.length) return;
  if (BOARD?.generated_at !== proposal.generated_at) { M.error = 'The board changed since this proposal; it was refreshed.'; requestProposal(); return; }
  // (plan phase 3): a card already staged for another plot is MOVED here -- its old staged entry is undone
  // first (that plot's tile comes back), so the card is never staged twice.
  for (const move of kept) {
    const elsewhere = CERES_STAGE.find((entry) => entry.deed_uid !== row.deed_uid && !entry._synthetic && !entry.txId
      && entry.action !== 'unstake' && String(entry.asset_ref) === String(move.asset_ref));
    if (elsewhere) {
      await removeCheckoutStage(elsewhere);
      if (CERES_STAGE.includes(elsewhere)) { M.error = 'A staged card could not be moved from its earlier plot. Try again.'; render(); return; }
    }
  }
  let staged = 0;
  kept.forEach((move) => {
    const group = editorGroups(row).find((item) => item.id === move.group_id);
    if (!group) return;
    const candidate = candidateAsset(move.candidate, group.id);
    const incoming = editorOccupant(candidate, group.id);
    const outgoing = group.slots[move.slot_index] || null;
    const action = outgoing ? 'replace' : 'stake';
    if (stageForSlot(row.deed_uid, group.id, move.slot_index, action, incoming.asset_ref, candidateDelta(candidate), outgoing?.asset_ref || null, incoming.name,
      candidate.source_deed_uid || null, candidate.base_pp_delta, candidate.cooldown, { ...incoming, plot_impact: candidate.plot_impact }) === false) return;
    mutateEditorSlot(row, group, move.slot_index, incoming);
    staged += 1;
  });
  updateMountedRow(row.deed_uid);
  renderCheckoutSurface();
  closeFillPlotModal({ returnFocus: false });
  stageToast(`Staged ${staged} change${staged === 1 ? '' : 's'} on plot ${formatPlotLabel(row.plot)} — review and sign them in Changes.`);
}
