import { renderCheckoutSurface } from './checkout.js';
import { appendRich, appendItems, editorGroups, mobileOccupantDetails, mobileWorkerIdentity, renderPreviewTile, stakedCardSlots } from './editor.js';
import { beginLakeScene, endLakeScene, editorSceneLayers, loadImageWithRetry, tilePlateLayers, worksiteArtGeometry } from './art.js';
import { badgeHtml, badgeStripHtml, badgeStripStatus, foodPerHourCell, materialCostCell, stageBadgeKeysByDeed } from './badges.js';
import { syncBadgeOverlay } from './badge-overlay.js';
import { BOARD, BOARD_GENERATION, CURRENT_PLAYER, CAPTURE_STATUS_MAX_WAIT_MS, captureStatusPollInterval, fetchWithNetworkRetry, playerQueryAppend, stagedPlotCount } from './board.js';
import { WORKER_UPGRADE_OPTIONS, workerUpgradeIsAvailable, workerUpgradeKey } from './candidates.js';
import { basePpTipAttrs, boostablePpTipAttrs, boostTipAttrs, decStakeCell, esc, fmtBasePp, fmtTitle, fmtTotem, fmtWorkPerHour, formatPlotLabel, num, ppAttrs, titleTipAttrs, totalPpTipAttrs } from './format.js';
import { plotRarityCellHtml, plotTypeTipAttrs, resourceIconHtml, terrainCellHtml, territoryCellHtml, territorySlug, tooltipIconPath, typeMarkHtml } from './icons.js';
import { closeMobilePickerSelection, initPickerFilterDropdowns, updatePickerRegion, workerSubtable } from './picker-actions.js';
import { ASSET_BASE, PLOT_TABLE_COLUMN_COUNT, S } from './state.js';
import { worksiteCellHtml } from './ws-build.js';
import { CERES_STAGE, applyStageToRow, reconcileCapPrediction } from './staging.js';
import { CURRENT_ROWS, ensureRowMounted, scrollPlotUnderHeader, captureTableAnchor, refreshVirtualLayout, acquirePlotViewportLease, cancelPlotViewportLease, releasePlotViewportLease, plotViewportLeaseIsCurrent, render as renderTable } from './table.js';
import { PLOT_UNREACHABLE_ERROR, setPlotReconnecting, setPlotFinishing, configureEos, plotGeometryProfile, beginPlotOpening, getPlotPresentation, ensurePlotPresentation, beforePlotContentReplace, parkPlotPresentation, mountPlotPresentation, centreReservedPlot, closePlotPresentation, invalidatePlotPresentations, adoptPlotPresentations, reconcilePlotHandoff, spliceDecodedArt } from './eos.js';
import { availableTableWidth } from './scale.js';
import { plotLockIconHtml } from './plot-lock.js';

export function renderRow(row, rowIndex) {
  const session = getPlotPresentation(row.deed_uid);
  const previousBand = session?.mountedFrame || session?.lastRealBand;
  beforePlotContentReplace(row.deed_uid);
  const tr = document.createElement('tr');
  // Zebra striping ( board-fix batch, Iris tokens only -- see .plot-row.row-alt
  // in ceres.css). Class-driven from the FILTERED/SORTED row index passed in by the
  // caller, never a raw CSS nth-child on <tr> -- this table's expand-row siblings
  // stay in the DOM (display:none) even while collapsed, so nth-child parity would
  // silently shift for every row after the first one ever expanded.
  // (maintainer: the pulsing clock is far right and can be missed): a row whose status is the pending clock also pulses
  // a subtle gold across the whole row, in step with the clock.
  tr.className = 'plot-row' + (rowIndex % 2 === 1 ? ' row-alt' : '') + (badgeStripStatus(row).kind === 'pending' ? ' row-pending' : '');
  // data-deed-uid ( board-fix batch): lets updateMountedRow() find this exact
  // row's DOM later without a linear scan, and lets the virtualization window
  // recompute confirm a row is (or isn't) currently mounted.
  tr.dataset.deedUid = row.deed_uid;
  tr.dataset.rowIndex = String(rowIndex);   // the position in the listed rows (the counting column), kept for re-renders of this row
  tr.innerHTML = plotRowCellsHtml(row, rowIndex);
  const expand = document.createElement('tr');
  // fix: a remount keeps the slow-open flag (the spinner's only switch) from the session.
  expand.className = 'expand-row' + (session?.slowOpen ? ' is-slow-open' : '');
  expand.dataset.deedUid = row.deed_uid;
  expand.style.display = EXPANDED_ROWS.has(row.deed_uid) ? '' : 'none';
  const td = document.createElement('td');
  td.colSpan = PLOT_TABLE_COLUMN_COUNT;
  td.innerHTML = workerSubtable(row);
  const nextBand = td.querySelector('.editor-band');
  const handoff = handoffPlotContent(row, previousBand, nextBand);
  if (nextBand && handoff && handoff !== nextBand) nextBand.replaceWith(handoff);
  // Dropdown-unification batch: only the currently-open picker's row (if
  // any) actually contains `[data-picker-filter]` selects -- see
  // initPickerFilterDropdowns()'s own comment for why this must re-run on
  // every mount rather than once at boot. Guarded so the (cheap but
  // pointless) querySelectorAll doesn't run for every one of hundreds of
  // rows on every renderTable().
  if (S.CERES_PICKER && S.CERES_PICKER.deedUid === row.deed_uid) initPickerFilterDropdowns(td);
  expand.appendChild(td);
  tr.addEventListener('click', (event) => {
    if (event.target.closest('.slot-editor')) return;
    toggleRow(BOARD?.plots.find((plot) => plot.deed_uid === row.deed_uid) || row);
  });
  return [tr, expand];
}

export function handoffPlotContent(row, previousBand, nextBand, { readiness = false } = {}) {
  const session = getPlotPresentation(row.deed_uid);
  if (!session) return nextBand;
  if (!nextBand) {
    parkPlotPresentation(row.deed_uid);
    return nextBand;
  }
  spliceDecodedArt(row.deed_uid, previousBand, nextBand);
  const hasRealBand = !!previousBand?.querySelector('[data-reveal-group]') && !previousBand.hasAttribute('data-eos-detail-scaffold');
  const hasFreshDetail = !!nextBand.querySelector('[data-reveal-group]') && !nextBand.hasAttribute('data-eos-detail-scaffold');
  if (hasRealBand && !hasFreshDetail && session.terminalReplacement && !readiness) {
    session.quietRetained = true;
    session.workersReleased = true;
    session.cardsSettled = true;
    session.phase = 'ready';
    return previousBand;
  }
  session.quietRetained = false;
  session.lastRealBand = null;
  return reconcilePlotHandoff(row.deed_uid, previousBand, nextBand);
}

// Lazy per-plot asset loading (2026-08-21 bulk-first redesign, revised
// board-fix batch for the lean list payload -- see the note above toggleRow()
// below). Expanding a row that hasn't been loaded yet triggers ONE request and
// merges the result into BOARD in place -- the row never needs re-fetching again
// after that. EXPANDED_ROWS survives a re-render so the row stays open across it.
// TDZ-audited (see the module-level rule at the top of this file): safe declared
// here, below ready() -- all are only ever read from render()/renderTable()
// toggleRow()/workerSubtable()/updateMountedRow(), none of which run during
// init()'s synchronous prefix (render() only runs after loadBoard()'s `await`
// resolves; toggleRow() only runs from a later click handler).
export const EXPANDED_ROWS = new Set();
export const LOADING_PLOTS = new Set();
// Tracks which deed_uids have had their FULL per-plot detail (workers included)
// fetched into BOARD.plots THIS session ( board-fix batch). Distinct from the
// server-side `row.assets_loaded` flag, which now means only "the server already
// has this deed's assets cached on disk" -- since GET /api/board's list response no
// longer embeds `workers` at all (payload-trim fix, item 3), `assets_loaded: true`
// on a freshly-loaded row does NOT mean `row.workers` is populated; it only tells
// toggleRow() which endpoint can serve the detail (GET, no network call, vs. POST
// .../expand, a real fetch). EXPANDED_DATA_LOADED is the one true "is row.workers
// trustworthy right now" signal, read by workerSubtable() below.
export const EXPANDED_DATA_LOADED = new Set();
// deed_uid -> message, for a row whose expand fetch failed after the automatic
// retry (see toggleRow()). Rendered inline by workerSubtable() with a Retry
// link, replacing the old alert() (2026-08-21, `work/ceres-plot-expand-bug.md`)
// a transient network blip (e.g. the auto-reload watcher restarting the
// server mid-request) is exactly the kind of failure an alert() is the wrong
// affordance for: it's disruptive and gives no way to recover without
// re-clicking the row closed and open again.
export const EXPAND_ERRORS = new Map();
const DETAIL_REQUESTS = new Map();
const WORKER_UPGRADE_REQUESTS = new Map();
const WORKER_UPGRADE_CONCURRENCY = 2;
let requestSerial = 0;

configureEos({
  generation: () => BOARD_GENERATION,
  player: () => CURRENT_PLAYER,
  row: (uid) => BOARD?.plots.find((row) => row.deed_uid === uid), geometry: geometrySnapshot,
  acquireLease: acquirePlotViewportLease, cancelLease: cancelPlotViewportLease,
  releaseLease: releasePlotViewportLease, leaseIsCurrent: plotViewportLeaseIsCurrent,
  redraw: updateMountedRow,
  refreshHeight: (uid) => refreshVirtualLayout(uid, captureTableAnchor()),
  close: (uid) => { abortWorkerUpgradeOptions(uid); closePlotPresentation(uid); EXPANDED_ROWS.delete(uid); updateMountedRow(uid); },
});

// root fix: the opened-plot geometry reads the SAME authoritative
// width as the table (scale.js availableTableWidth() -- the live scroller, or
// the viewport minus board padding when the table is hidden). The old
// dual-probe clamp read `scroller.clientWidth || 0` and the FIRST `.plot-row`'s
// offsetWidth -- under a tier where the table is display:none both probes
// returned 0, the tile pager floored at pageSize 1 and pushed every tile
// (including Title) onto later pages while hundreds of px sat free.
function geometrySnapshot() {
  return { width: availableTableWidth(),
    font: parseFloat(getComputedStyle(document.documentElement).fontSize) || 16 };
}
export function attachMountedPlot(row, expand) {
  if (!EXPANDED_ROWS.has(row.deed_uid)) return;
  const frame = expand?.querySelector('.editor-band');
  if (!frame) return;
  mountPlotPresentation(row, frame, { dataReady: EXPANDED_DATA_LOADED.has(row.deed_uid), error: frame.dataset.eosRenderError || EXPAND_ERRORS.get(row.deed_uid) });
}
export function presentationForRow(row) {
  const existing = getPlotPresentation(row.deed_uid);
  if (existing) return existing;
  const { width, font } = geometrySnapshot();
  return ensurePlotPresentation(row, width, font);
}
export function invalidateRowPresentations() {
  closeMobilePlotSheet({ restoreFocus: false });
  invalidatePlotPresentations();
  DETAIL_REQUESTS.clear();
  LOADING_PLOTS.clear();
  EXPAND_ERRORS.clear();
  WORKER_UPGRADE_REQUESTS.forEach((request, deedUid) => abortWorkerUpgradeOptions(deedUid));
  WORKER_UPGRADE_REQUESTS.clear();
}

// the quiet re-read's counterpart to the above -- nothing is closed, the open
// plots follow the board to its new generation so their detail reload can re-mount them.
export function adoptRowPresentations(generation) {
  adoptPlotPresentations(generation);
}


export const EXPANDED_HEIGHTS = new Map();
// Builds just the <td> cells for a plot row ( board-fix batch -- split out of
// renderRow() so updateMountedRow() below can refresh an ALREADY-MOUNTED row's
// content in place without tearing down and recreating its <tr>, which is what
// full re-render did before and is the same freeze-causing pattern at whale scale
// applied to a single row).
export function plotRowCellsHtml(row, rowIndex = null) {
  const terrainCell = terrainCellHtml(row.terrain);
  const decStake = decStakeCell(row);
  const foodCell = foodPerHourCell(row);
  const woodCell = materialCostCell(row, 'WOOD');
  const stoneCell = materialCostCell(row, 'STONE');
  const ironCell = materialCostCell(row, 'IRON');
  const totemHtml = fmtTotem(row);
  const titleHtml = fmtTitle(row);
  const typeTip = plotTypeTipAttrs(row);
  const territoryCell = territoryCellHtml(row.territory, row.deed_uid);
  // amendment: the badge strip is no longer a table column. Its content
  // (badgeStripHtml()) now renders into #badge-bar's own per-row elements
  // (badge-overlay.js::syncBadgeOverlay()), kept aligned with these <tr>s the same
  // way #badge-overlay's rows already were.
  return `
    <td class="${territoryCell.cls}"${territoryCell.tip}>${esc(formatPlotLabel(row.plot))}</td>
    <td class="rarity-cell">${plotRarityCellHtml(row)}</td>
    <td class="type-cell"${typeTip}>${typeMarkHtml(row.plot_status, row.magic_type, row.worksite_type, Boolean(typeTip))}</td>
    <td class="${terrainCell.cls}">${terrainCell.html}</td>
    <td class="resource-cell">${worksiteCellHtml(row)}</td>
    <td class="col-num"${ppAttrs(row.harvest_pp)}${totalPpTipAttrs(row)}>${num(row.harvest_pp, 0)}</td>
    <td class="col-num"${ppAttrs(row.construction_pp)}${boostablePpTipAttrs(row)}>${num(row.construction_pp, 0)}</td>
    <td class="col-num"${ppAttrs(row.base_pp_after_cap ?? row.base_pp)}${basePpTipAttrs(row)}>${fmtBasePp(row)}</td>
    <td class="totem-cell">${totemHtml}</td>
    <td class="title-cell"${titleTipAttrs(row)}>${titleHtml}</td>
    <td class="col-num"${boostTipAttrs(row)}>${row.multiplier !== null ? row.multiplier.toFixed(2) + 'x' : '—'}</td>
    <td class="col-num">${fmtWorkPerHour(row)}</td>
    <td class="col-num"${foodCell.tip}>${foodCell.html}</td>
    <td class="col-num col-w-wood"${woodCell.tip}>${woodCell.html}</td>
    <td class="col-num col-w-stone"${stoneCell.tip}>${stoneCell.html}</td>
    <td class="col-num col-w-iron"${ironCell.tip}>${ironCell.html}</td>
    <td class="col-num">${row.capacity.display}</td>
    <td class="col-num"${decStake.tip}>${decStake.html}</td>
    <td class="lock-cell col-w-lock">${plotLockIconHtml(row)}</td>
    <td class="count-cell col-w-count">${Number.isInteger(rowIndex) ? rowIndex + 1 : ''}</td>`;
}

// Phase D: the mobile card is a second projection of the same row object
// not a second model. Every value that overlaps the desktop table calls the exact
// helper used by plotRowCellsHtml() above; badgeHtml() also preserves its canonical
// staged/property/problem ordering.
// (maintainer: "The mainboard must be brighter, more colorful, full of artwork, more compact, more plots at once
// visible, more game-like"; "Terrain and worksite image is a must"; badges "could be cool as an opt-in overlay"): the
// phone board is a list of art cards -- the plot's terrain with its worksite building standing in it, a rarity-coloured
// frame, and on the art the plot plaque, the rarity gem set in a corner piece ("Remove terrain, type and lock icons"; "integrated into the top
// right corner"), and Harvest/hr and Total PP ("move the harvest and total pp on top of the artwork and a bit smaller with supporting
// shadow"). Badges sit in an overlay shown only when
// the viewer switches them on.
function plotTileArtStyle(row) {
  // A production building's full scene sits on its own terrain, the pair moved so the building is centred on the card.
  const plate = tilePlateLayers(row);
  if (plate) return esc([`--tile-terrain: url('${plate.terrain}')`, `--tile-plate: url('${plate.plate}')`,
    `--p-cx: ${plate.cx}`, `--p-base: ${plate.base}`, `--p-top: ${plate.top}`].join('; '));
  const layers = editorSceneLayers(row);
  const terrain = layers.terrain?.replace('/web/land/', '/thumbs/land/');
  const building = layers.worksiteFallback || layers.worksite;
  const g = building ? worksiteArtGeometry(row, { fallback: Boolean(layers.worksiteFallback) }) : null;
  const vars = [`--tile-terrain: ${terrain ? `url('${terrain}')` : 'none'}`];
  if (building && g) vars.push(`--tile-building: url('${building}')`, `--b-cx: ${g.cx}`, `--b-base: ${g.base}`, `--b-top: ${g.t}`,
    `--b-l: ${g.l}`, `--b-r: ${g.r}`, `--b-ratio: ${g.ratio}`);
  return esc(vars.join('; '));
}

export function plotCardHtml(row, rowIndex) {
  const badges = badgeHtml(row);
  const territory = territorySlug(row.territory);
  const type = [row.plot_status, String(row.plot_status).toLowerCase() === 'magical' ? row.magic_type : null].filter(Boolean).join(' · ');
  const rarity = String(row.rarity || 'unknown').toLowerCase().replace(/[^a-z]/g, '');
  const marks = [row.rarity || 'Unknown', type || 'Unknown', row.terrain, row.worksite_type].filter(Boolean).join(' · ');
  return `<article class="plot-card plot-tile rarity-${esc(rarity)}${rowIndex % 2 === 1 ? ' row-alt' : ''}" data-deed-uid="${esc(row.deed_uid)}" data-row-index="${rowIndex}" role="button" tabindex="0" aria-haspopup="dialog" aria-label="Open plot ${esc(formatPlotLabel(row.plot))}">
    <div class="plot-tile-art" style="${plotTileArtStyle(row)}">
      <span class="plot-tile-scene" aria-hidden="true"></span><span class="plot-tile-building" aria-hidden="true"></span>
      <strong class="ch-plot-badge plot-tile-id${territory ? ` territory-${territory}` : ''}">${esc(formatPlotLabel(row.plot))}</strong>
      <span class="plot-card-classification plot-tile-marks" role="img" aria-label="${esc(marks)}">${plotRarityCellHtml(row)}</span>
      ${plotStatsHtml(row)}
      ${badges ? `<div class="plot-card-badges plot-tile-badges">${badges}</div>` : ''}
    </div>
  </article>`;
}

// Both mobile surfaces read the same row and the same formatters as the desktop
// cells. This is presentation reuse only; none of these figures is recomputed.
export function plotStatsHtml(row) {
  return `<dl class="plot-card-stats">
    <div><dt>Harvest/hr</dt><dd>${fmtWorkPerHour(row)}</dd></div>
    <div><dt>Total PP</dt><dd>${num(row.harvest_pp, 0)}</dd></div>
  </dl>`;
}

let MOBILE_PLOT_UID = null;
let MOBILE_PLOT_SCROLL_Y = 0;
let MOBILE_PLOT_RETURN_TARGET = null;
let MOBILE_PLOT_ADDED_EXPANSION = false;

export function mobilePlotSheetIsOpen() {
  return MOBILE_PLOT_UID !== null;
}

function constrainMobileEditorActions(root) {
  // optional card Upgrade actions remain; the phone title entry stays absent.
  root.querySelectorAll('[data-combine-action], [data-slot-action="combine-plot"], [data-slot-action="combine-tile"], [data-picker-action="combine"], .picker-bar-title .candidate-upgrade, span.candidate-upgrade, .picker-combine-dash').forEach((control) => control.remove());
  root.querySelectorAll('.candidate-grid-tools:empty').forEach((tools) => tools.remove());
}

let MOBILE_NAME_OBSERVER = null;

// Base PP and Total PP side by side, the same figures and
// tooltips as the desktop Base PP and Total PP cells.
function mobileTotalPpHtml(row) {
  return `<div class="mobile-plot-total-pp"><dl class="mobile-plot-pp-stat"${basePpTipAttrs(row)}><dt>Base PP</dt><dd>${fmtBasePp(row)}</dd></dl><dl class="mobile-plot-pp-stat"${totalPpTipAttrs(row)}><dt>Total PP</dt><dd>${num(row.harvest_pp, 0)}</dd></dl></div>`;
}

// (table redesign version C): the compact "Resource flow" block in the phone
// plot detail sheet, after the existing Harvest/hr and Total PP stats. Inputs only
// Food/hr (always shown, maintainer decision 4), Wood/Stone/Iron only when this worksite's
// recipe actually consumes them -- never a produced-resource line, since that would
// just repeat the Harvest/hr band immediately above it verbatim (review finding
// landing ). Every line is consumption, so every line is signed with the same
// leading minus and reuses the desktop `materialCostCell()`'s exact formatter/icon
// (badges.js): Food/hr renders with the same 20.8px `.material-cost-icon` as Wood
// Stone/Iron (not the 14px `.dec-stake-icon` `foodPerHourCell()` uses for the desktop
// column -- that mismatch was already rejected once for and must not recur
// here). A missing recipe or missing facts renders "unavailable", never "0". At most
// four lines can ever exist (Food + Wood + Stone + Iron) so there is no cap to enforce
// and no line is ever dropped -- Iron must never be truncated on a plot that consumes
// all four. No Net/hr scalar (maintainer decision 5). Never rendered on the card face
// (plotStatsHtml()/plotCardHtml() above are untouched) -- detail sheet only.
function mobileResourceFlowHtml(row) {
  const grainIconPath = tooltipIconPath('resource', 'grain');
  const grainIcon = grainIconPath ? `<img class="material-cost-icon" src="${ASSET_BASE}/${grainIconPath}" alt="" aria-hidden="true">` : '';
  const grainCost = row?.grain_cost_per_hour;
  const lines = [{
    label: 'Food/hr',
    value: grainCost === null || grainCost === undefined ? 'unavailable' : `−${num(grainCost, 2)}${grainIcon}`,
  }];
  [['WOOD', 'Wood/hr'], ['STONE', 'Stone/hr'], ['IRON', 'Iron/hr']].forEach(([symbol, label]) => {
    const cell = materialCostCell(row, symbol);
    if (cell.html !== '—') lines.push({ label, value: `−${cell.html}` });
  });
  const rows = lines.map((line) => `<div><dt>${esc(line.label)}</dt><dd>${line.value}</dd></div>`).join('');
  return `<section class="mobile-plot-resource-flow"><p class="mobile-plot-resource-flow-title">Resource flow</p><dl class="mobile-plot-resource-flow-rows">${rows}</dl></section>`;
}

function decorateMobileWorkerTiles(body) {
  const grid = body.querySelector('.slot-group[data-group-id="worker"] > .slot-grid');
  if (!grid) return;
  grid.querySelectorAll('.slot-grid-divider').forEach((node) => node.remove());
  const ppRow = body.querySelector('.editor-base-pp-row');
  [...grid.children].forEach((cell) => {
    const tile = cell.querySelector?.('.slot-tile');
    if (!tile) return;
    const groupId = cell.querySelector('.slot-power') ? 'runi' : tile.dataset.groupId;
    const slotIndex = groupId === 'runi' ? '0' : tile.dataset.slotIndex;
    if (groupId === 'worker' && tile.classList.contains('is-empty')) {
      const label = document.createElement('span');
      label.className = 'mobile-worker-slot-label';
      label.innerHTML = `<strong>Worker slot ${Number(slotIndex) + 1}</strong>`;
      cell.append(label);
    }
    const badge = ppRow?.querySelector(`.editor-base-pp-cell.is-filled[data-group-id="${groupId}"][data-slot-index="${slotIndex}"]`);
    if (badge) {
      const wrap = document.createElement('span');
      wrap.className = 'mobile-boostable-pp';
      wrap.append(badge, Object.assign(document.createElement('small'), { textContent: groupId === 'worker' ? 'Boostable PP' : groupId === 'runi' ? 'Base PP' : 'Boost' }));
      cell.append(wrap);
    }
  });
}

function decorateMobileSlotActions(body) {
  const pendingFor = (target) => CERES_STAGE.find((entry) => entry.deed_uid === target.dataset.deedUid
    && entry.group_id === target.dataset.groupId && String(entry.slot_index) === target.dataset.slotIndex);
  const addAction = (target, host, original, suffix = '') => {
    if (!host || target.disabled) return;
    const pending = pendingFor(target);
    if (pending?.txId) return;
    const undo = Boolean(pending) || ['restore', 'undo-move'].includes(target.dataset.slotAction);
    const button = original || document.createElement('button');
    button.type = 'button';
    button.className = 'mobile-slot-action ceres-btn ceres-btn--secondary';
    if (!original) Object.assign(button.dataset, target.dataset);
    button.dataset.slotAction = original ? 'remove'
      : target.dataset.slotAction === 'add-power-core' ? 'restore' : target.dataset.slotAction;
    for (const attribute of [...button.attributes]) {
      if (attribute.name.startsWith('data-tip')) button.removeAttribute(attribute.name);
    }
    const name = target.querySelector('.slot-name')?.textContent || suffix || target.dataset.groupId;
    button.textContent = `${undo ? 'Undo' : 'Remove'}${suffix ? ` ${suffix}` : ''}`;
    button.setAttribute('aria-label', `${undo ? 'Undo staged change for' : 'Stage removal of'} ${name}`);
    button.setAttribute('data-control-density', 'touch');
    button.hidden = true;
    host.append(button);
  };
  for (const tile of body.querySelectorAll('.slot-tile')) {
    const remove = tile.querySelector('.slot-remove');
    if (!remove && !['restore', 'undo-move'].includes(tile.dataset.slotAction)) continue;
    const host = tile.closest('.slot-tile-cell, .asset-slot, .slot-power');
    addAction(tile, host, remove);
  }
  for (const satellite of body.querySelectorAll('.power-satellite[data-slot-action]')) {
    const pending = pendingFor(satellite);
    if (satellite.dataset.slotAction !== 'remove' && !(pending?.action === 'unstake' && satellite.dataset.slotAction === 'add-power-core')) continue;
    addAction(satellite, satellite.closest('.slot-power'), null, satellite.dataset.groupId === 'power_core' ? 'core' : 'Runi');
  }
  // The surviving power source can replace the removed Runi/Core tile entirely.
  const power = body.querySelector('.slot-power');
  if (!power) return;
  const deedUid = power.querySelector('[data-deed-uid]')?.dataset.deedUid;
  for (const entry of CERES_STAGE) {
    if (entry.deed_uid !== deedUid || entry.action !== 'unstake' || entry._plan_invalid_reason
      || !['runi', 'power_core'].includes(entry.group_id)
      || power.querySelector(`.mobile-slot-action[data-group-id="${entry.group_id}"]`)) continue;
    const target = document.createElement('button');
    Object.assign(target.dataset, { slotAction: 'restore', deedUid, groupId: entry.group_id, slotIndex: String(entry.slot_index) });
    addAction(target, power, null, entry.group_id === 'runi' ? 'Runi' : 'core');
  }
}

function decorateMobileStagedChanges(body, worksite) {
  const badges = body.querySelector('.editor-change-badges');
  if (!badges) return;
  const section = document.createElement('section');
  section.className = 'mobile-staged-changes';
  section.setAttribute('aria-label', 'Staged changes');
  const heading = document.createElement('h3');
  heading.textContent = 'Staged changes';
  section.append(heading, badges);
  worksite.after(section);
}

function decorateMobileHarvestBar(row, body, worksite) {
  const footer = body.querySelector('.slot-editor-footer');
  const harvestBand = worksite.querySelector('.mobile-plot-harvest-band');
  if (!footer || !harvestBand) return;
  footer.classList.add('mobile-plot-actions');
  harvestBand.appendChild(footer);
}

function decorateMobileDetailTargets(body) {
  for (const tile of body.querySelectorAll('.slot-tile[data-occupied="1"], .slot-tile.is-pending-removal, .power-satellite.is-filled[data-slot-action]')) {
    if (tile.disabled) continue;
    for (const attr of [...tile.attributes]) if (attr.name.startsWith('data-tip')) tile.removeAttribute(attr.name);
    tile.dataset.slotAction = 'mobile-details';
    tile.dataset.actionable = '1';
    tile.tabIndex = 0;
    tile.setAttribute('role', 'button');
  }
}

function appendMobileSlotDetails(region, body, detail) {
  const row = BOARD?.plots.find((plot) => plot.deed_uid === detail.deedUid);
  const group = row && editorGroups(row).find((item) => item.id === detail.groupId);
  if (!group) return;
  const entry = CERES_STAGE.find((item) => item.deed_uid === detail.deedUid && item.group_id === detail.groupId && item.slot_index === Number(detail.slotIndex));
  const occupant = group.slots[Number(detail.slotIndex)] || entry?._previous_asset;
  const selector = `[data-group-id="${CSS.escape(detail.groupId)}"][data-slot-index="${Number(detail.slotIndex)}"]`;
  let source = body.querySelector(`.slot-tile${selector}`);
  if (!source && occupant) {
    const template = document.createElement('template');
    template.innerHTML = renderPreviewTile(group, occupant, { tip: false });
    source = template.content.firstElementChild;
  }
  const panel = document.createElement('section');
  panel.className = 'mobile-slot-details';
  const top = document.createElement('div');
  top.className = 'mobile-slot-details-top';
  if (source) {
    const art = source.cloneNode(true);
    art.querySelectorAll('.slot-remove, .picker-focus-ring').forEach((node) => node.remove());
    for (const node of [art, ...art.querySelectorAll('*')]) {
      for (const attr of [...node.attributes]) if (attr.name.startsWith('data-slot') || attr.name.startsWith('data-tip') || attr.name === 'id') node.removeAttribute(attr.name);
      node.removeAttribute('tabindex');
    }
    art.classList.remove('is-picker-dimmed', 'is-picker-focus', 'is-view-only', 'is-art-pending', 'is-caption-pending');
    art.setAttribute('role', 'img');
    art.setAttribute('aria-label', occupant?.name || group.label);
    top.append(art);
  }
  const content = document.createElement('div');
  content.className = 'mobile-slot-details-content';
  const heading = document.createElement('h3');
  heading.textContent = occupant?.name || group.label;
  content.append(heading);
  if (occupant) {
    const model = mobileOccupantDetails(occupant, group, Number(detail.slotIndex), row);
    if (group.id === 'worker') {
      const identity = mobileWorkerIdentity(occupant, detail.slotIndex);
      for (const [key, value] of [['slot', identity.slot], ['traits', identity.traits], ['edition', identity.edition]]) {
        const line = document.createElement('p');
        line.className = `mobile-detail-${key}`;
        appendRich(line, value);
        content.append(line);
      }
      const finish = document.createElement('p');
      finish.className = 'mobile-detail-level-foil';
      const level = document.createElement('span');
      level.className = 'mobile-detail-level';
      level.textContent = `Level ${identity.level}`;
      finish.append(level);
      const foil = document.createElement('span');
      foil.innerHTML = identity.foil;
      finish.append(foil);
      content.append(finish);
    } else {
      const identity = document.createElement('p');
      appendRich(identity, model.title);
      content.append(identity);
    }
    const metrics = document.createElement('div');
    metrics.className = 'mobile-slot-detail-metrics';
    if (model.items) appendItems(metrics, model.items);
    if (model.body || model.key) {
      const description = document.createElement('p');
      appendRich(description, model.body || model.key);
      metrics.append(description);
    }
    metrics.dataset.terrain = Number(occupant.terrain_boost) > 0 ? 'bonus' : Number(occupant.terrain_boost) < 0 ? 'penalty' : 'neutral';
    for (const label of metrics.querySelectorAll('.tip-item-name')) if (label.textContent === 'Boostable') label.nextElementSibling.classList.add('mobile-detail-boostable');
    panel.append(metrics);
  }
  top.append(content);
  panel.prepend(top);
  const actions = document.createElement('div');
  actions.className = 'mobile-slot-details-actions';
  for (const original of body.querySelectorAll(`.mobile-slot-action${selector}`)) {
    const action = original.cloneNode(true);
    action.hidden = false;
    actions.append(action);
  }
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'ceres-btn ceres-btn--secondary';
  close.textContent = 'Back to plot';
  Object.assign(close.dataset, { slotAction: 'mobile-details-close', deedUid: detail.deedUid });
  actions.append(close);
  panel.append(actions);
  region.append(panel);
}

function arrangeMobilePicker(sheet, body, worksite) {
  const wasFocused = sheet.classList.contains('is-picker-focused');
  const picker = body.querySelector('.candidate-visible-slice');
  const detail = S.MOBILE_SLOT_DETAIL?.deedUid === MOBILE_PLOT_UID ? S.MOBILE_SLOT_DETAIL : null;
  const focused = Boolean(picker || detail);
  if (focused) {
    const region = document.createElement('section');
    region.className = 'mobile-picker-focus-region';
    region.setAttribute('aria-label', 'Slot details and selection');
    if (detail) appendMobileSlotDetails(region, body, detail);
    if (picker) region.appendChild(picker);
    worksite.after(region);
    const context = region.querySelector('.picker-context');
    if (context) context.textContent = context.dataset.mobileContext;
    const filters = region.querySelector('.picker-filters');
    if (filters) {
      const sort = region.querySelector('.picker-sort-chips');
      if (sort) filters.prepend(sort);
      const disclosure = document.createElement('details');
      disclosure.className = 'mobile-picker-filters';
      disclosure.innerHTML = '<summary>Filters</summary>';
      disclosure.open = Boolean(S.CERES_PICKER?.mobileFiltersOpen);
      filters.replaceWith(disclosure);
      disclosure.append(filters);
      disclosure.addEventListener('toggle', () => {
        if (S.CERES_PICKER?.deedUid === MOBILE_PLOT_UID) S.CERES_PICKER.mobileFiltersOpen = disclosure.open;
      });
    }
    if (S.CERES_PICKER?.groupId === 'worker') {
      region.querySelectorAll('.picker-keep-open, .picker-close').forEach((button) => button.remove());
      const end = region.querySelector('.picker-bar-end');
      if (end && !end.children.length) end.remove();
    }
    body.scrollTop = 0;
    window.scrollTo(0, 0); // the phone sheet scrolls with the page
  }
  const editor = body.querySelector('.slot-editor');
  if (editor) editor.inert = focused;
  sheet.classList.toggle('is-picker-focused', wasFocused);
  requestAnimationFrame(() => {
    if (!sheet.hidden && worksite.isConnected && sheet.dataset.deedUid === MOBILE_PLOT_UID) sheet.classList.toggle('is-picker-focused', focused);
  });
}

function fitMobileTileNames(body) {
  const measure = document.createElement('canvas').getContext('2d');
  for (const name of body.querySelectorAll('.slot-name, .candidate-name, .candidate-grid-metric strong')) {
    name.style.fontSize = '';
    name.style.width = '';
    name.style.transform = '';
    let size = parseFloat(getComputedStyle(name).fontSize);
    const overflows = () => name.scrollHeight > name.clientHeight + 1 || name.scrollWidth > name.clientWidth + 1;
    while (size > 10 && overflows()) {
      size = Math.max(10, size - 0.25);
      name.style.fontSize = `${size}px`;
    }
    if (name.matches('.candidate-grid-metric strong')) continue;
    // Keep two full lines legible on phone rows; condense only when needed.
    if (!measure || !name.clientWidth) continue;
    measure.font = getComputedStyle(name).font;
    const oneLineWidth = Math.ceil(measure.measureText(name.textContent).width / name.clientWidth * 100) + 5;
    for (let width = 105; width <= oneLineWidth && overflows(); width += 5) {
      name.style.width = `${width}%`;
      name.style.transform = `scaleX(${100 / width})`;
    }
  }
}

function renderMobilePlotSheet(row) {
  const sheet = document.getElementById('mobile-plot-sheet');
  const body = document.getElementById('mobile-plot-sheet-body');
  if (!sheet || !body || MOBILE_PLOT_UID !== row.deed_uid) return false;
  MOBILE_NAME_OBSERVER?.disconnect();
  MOBILE_NAME_OBSERVER = null;
  // the plot number
  // is the board card's territory plate; territory, terrain and worksite are chips under it. (maintainer: "remove the
  // region number (keep the badge)"): no region chip -- the plate already carries the region.
  const title = document.getElementById('mobile-plot-sheet-title');
  const territory = territorySlug(row.territory);
  title.textContent = formatPlotLabel(row.plot);
  title.className = `ch-plot-badge mobile-plot-sheet-plate${territory ? ` territory-${territory}` : ''}`;
  title.setAttribute?.('aria-label', `Plot ${formatPlotLabel(row.plot)}`);
  document.getElementById('mobile-plot-sheet-subtitle').innerHTML = [row.territory, row.terrain, row.worksite_type]
    .filter(Boolean).map((part) => `<span class="mobile-plot-sheet-chip">${esc(part)}</span>`).join('');
  const back = document.getElementById('mobile-plot-sheet-close');
  back?.classList.add('ceres-btn', 'ceres-btn--secondary', 'mobile-plot-back');
  back?.setAttribute('data-control-density', 'touch');
  body.innerHTML = `<section class="mobile-plot-worksite rarity-${esc(String(row.rarity || 'unknown').toLowerCase().replace(/[^a-z]/g, ''))}"><span class="plot-tile-marks mobile-plot-rarity-corner">${plotRarityCellHtml(row)}</span><div class="mobile-plot-harvest-band"><span class="mobile-plot-rate"><small>Harvest/hr</small><strong>${fmtWorkPerHour(row)}</strong></span></div></section>${workerSubtable(row, { mobile: true })}${mobileTotalPpHtml(row)}${mobileResourceFlowHtml(row)}`;
  constrainMobileEditorActions(body);
  if (S.CERES_PICKER?.deedUid === row.deed_uid) initPickerFilterDropdowns(body);
  const band = body.querySelector('.editor-band');
  if (band) {
    const ready = EXPANDED_DATA_LOADED.has(row.deed_uid);
    const error = EXPAND_ERRORS.get(row.deed_uid);
    band.dataset.plotState = error ? 'error' : ready ? 'ready' : 'preparing';
    band.classList.toggle('is-slow-open', LOADING_PLOTS.has(row.deed_uid));
    band.setAttribute('aria-busy', String(!ready && !error));
    band.querySelectorAll('.eos-scenic-veil, .eos-worker-cover').forEach((node) => { node.hidden = true; });
    const status = band.querySelector('.eos-status-text');
    if (status) status.textContent = error || (ready ? '' : 'Loading plot…');
    const retry = band.querySelector('.expand-retry-link');
    if (retry) retry.hidden = !error;
    const scene = band.querySelector('.editor-scene');
    const worksite = body.querySelector('.mobile-plot-worksite');
    if (scene && worksite) {
      const layers = editorSceneLayers(row);
      scene.style.setProperty('--scene-terrain', layers.terrain ? `url('${layers.terrain}')` : 'none');
      const setWorksiteLayer = (url, fallback = false) => {
        scene.style.setProperty('--scene-worksite', url ? `url('${url}')` : 'none');
        const geometry = worksiteArtGeometry(row, { fallback });
        const art = scene.querySelector?.('.editor-worksite-art');
        if (!art || !geometry) return;
        art.style.setProperty('--art-cx', geometry.cx);
        art.style.setProperty('--art-base', geometry.base);
        art.style.setProperty('--art-h', `${geometry.hRem}rem`);
        art.style.setProperty('--art-l', geometry.l);
        art.style.setProperty('--art-t', geometry.t);
        art.style.setProperty('--art-r', geometry.r);
        art.style.setProperty('--art-ratio', geometry.ratio);
      };
      const resolvedWorksite = scene.style.getPropertyValue?.('--scene-worksite');
      if (!resolvedWorksite || resolvedWorksite === 'none') {
        setWorksiteLayer(layers.worksiteFallback || layers.worksite, Boolean(layers.worksiteFallback));
        if (layers.worksiteFallback && layers.worksite) {
          loadImageWithRetry(layers.worksite).then((url) => {
            if (MOBILE_PLOT_UID === row.deed_uid) setWorksiteLayer(url);
          }).catch(() => {});
        }
      }
      // (maintainer: "Make sure the worksite and terrain artworks fit properly together on single plot view. On the
      // board it already works well"): the same aligned pair as the board card -- terrain and the building's full scene in
      // one frame, the building's measured centre on the picture's centre.
      const plate = tilePlateLayers(row);
      scene.classList.toggle?.('has-plate', Boolean(plate));
      if (plate) {
        scene.style.setProperty('--tile-terrain', `url('${plate.terrain}')`);
        scene.style.setProperty('--tile-plate', `url('${plate.plate}')`);
        scene.style.setProperty('--p-cx', plate.cx);
        scene.style.setProperty('--p-base', plate.base);
        scene.style.setProperty('--p-top', plate.top);
        if (!scene.querySelector?.(':scope > .sheet-plate')) scene.insertAdjacentHTML?.('afterbegin', '<span class="sheet-plate" aria-hidden="true"></span>');
      }
      scene.classList.add('mobile-plot-worksite-art');
      worksite.prepend(scene);
    }
    const affinity = band.querySelector('.terrain-affinity[data-terrain-affinity="editor"]');
    if (affinity && worksite) {
      affinity.classList.add('mobile-plot-affinity');
      worksite.appendChild(affinity);
    }
    if (worksite) {
      decorateMobileWorkerTiles(body);
      decorateMobileSlotActions(body);
      decorateMobileHarvestBar(row, body, worksite);
      decorateMobileStagedChanges(body, worksite);
      decorateMobileDetailTargets(body);
      arrangeMobilePicker(sheet, body, worksite);
      if (typeof ResizeObserver === 'function') {
        let previousWidth = -1;
        MOBILE_NAME_OBSERVER = new ResizeObserver(([entry]) => {
          if (entry.contentRect.width === previousWidth) return;
          previousWidth = entry.contentRect.width;
          requestAnimationFrame(() => { if (MOBILE_PLOT_UID === row.deed_uid) fitMobileTileNames(body); });
        });
        MOBILE_NAME_OBSERVER.observe(body);
      }
      document.fonts.ready.then(() => requestAnimationFrame(() => {
        if (MOBILE_PLOT_UID === row.deed_uid) fitMobileTileNames(body);
      }));
    }
  }
  return true;
}

export function openMobilePlotSheet(deedUid, returnTarget = null) {
  if (!document.documentElement.classList.contains('ceres-lw-le-1279')) return false;
  const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
  const sheet = document.getElementById('mobile-plot-sheet');
  if (!row || !sheet) return false;
  const newOpening = MOBILE_PLOT_UID !== deedUid;
  if (newOpening && MOBILE_PLOT_UID !== null) closeMobilePlotSheet({ restoreFocus: false });
  if (newOpening) beginLakeScene(row, { mobile: true });
  if (S.CERES_PICKER) closeMobilePickerSelection(S.CERES_PICKER.deedUid, { render: false });
  S.MOBILE_SLOT_DETAIL = null;
  sheet.classList.remove('is-picker-focused');
  MOBILE_PLOT_UID = deedUid;
  const wasHidden = sheet.hidden !== false; // closed unless explicitly shown
  if (wasHidden) MOBILE_PLOT_SCROLL_Y = window.scrollY || 0; // V->L; : a re-render of an open sheet keeps the saved board position
  MOBILE_PLOT_RETURN_TARGET = returnTarget;
  if (newOpening) MOBILE_PLOT_ADDED_EXPANSION = !EXPANDED_ROWS.has(deedUid);
  EXPANDED_ROWS.add(deedUid);
  sheet.dataset.deedUid = deedUid;
  sheet.hidden = false;
  document.body.classList.add('mobile-plot-sheet-open');
  if (wasHidden) window.scrollTo(0, 0); // the sheet is part of the page; the board position is restored on close
  renderMobilePlotSheet(row);
  document.getElementById('mobile-plot-sheet-close')?.focus();
  if (!EXPANDED_DATA_LOADED.has(deedUid) && !LOADING_PLOTS.has(deedUid)) loadPlotDetail(row);
  return true;
}

export function closeMobilePlotPicker() {
  return MOBILE_PLOT_UID !== null && closeMobilePickerSelection(MOBILE_PLOT_UID);
}

export function closeMobilePlotSheet({ restoreFocus = true } = {}) {
  if (MOBILE_PLOT_UID === null) return false;
  MOBILE_NAME_OBSERVER?.disconnect();
  MOBILE_NAME_OBSERVER = null;
  const deedUid = MOBILE_PLOT_UID;
  endLakeScene(deedUid, { mobile: true });
  closeMobilePickerSelection(deedUid, { render: false });
  if (MOBILE_PLOT_ADDED_EXPANSION) EXPANDED_ROWS.delete(deedUid);
  S.MOBILE_SLOT_DETAIL = null;
  MOBILE_PLOT_UID = null;
  MOBILE_PLOT_ADDED_EXPANSION = false;
  const sheet = document.getElementById('mobile-plot-sheet');
  if (sheet) { sheet.hidden = true; sheet.classList.remove('is-picker-focused'); sheet.removeAttribute('data-deed-uid'); }
  document.getElementById('mobile-plot-sheet-body')?.replaceChildren();
  document.body.classList.remove('mobile-plot-sheet-open');
  window.scrollTo(0, MOBILE_PLOT_SCROLL_Y);
  if (restoreFocus) MOBILE_PLOT_RETURN_TARGET?.focus?.();
  MOBILE_PLOT_RETURN_TARGET = null;
  return true;
}

// State remains authoritative in BOARD/EXPANDED_ROWS even when a row is outside
// the current window; DOM work is limited to a currently mounted pair.
// patch a mounted plot in place instead of replacing it. Rebuilding the row and band
// from scratch on every staged change swapped ~370 elements (every button, name and picture)
// for identical copies, so the whole plot flickered. Matching nodes are kept and only their
// changed attributes and text are touched; a picture already showing the same card is left
// exactly as it is. Nodes that get listeners attached on mount (the plot's close and page
// buttons) and the inline picker (its dropdowns are re-initialised after every redraw) are
// always swapped fresh, so no listener is ever added twice.
const MORPH_REPLACE_SELECTOR = '.eos-close, .inline-picker, select[data-picker-filter]';
const MORPH_KEYS = ['id', 'data-deed-uid', 'data-group-id', 'data-slot-index', 'data-slot-action', 'data-reveal-group'];
function morphSameImage(live, next) {
  const identity = (image) => `${image.dataset.cardImageCandidates || image.getAttribute('src') || ''}|${image.dataset.pickKey || ''}`;
  return identity(live) === identity(next) && !!(live.currentSrc || live.getAttribute('src'));
}
function morphCompatible(live, next) {
  if (live === next || live.nodeType !== next.nodeType) return false;
  if (live.nodeType !== Node.ELEMENT_NODE) return live.nodeType === Node.TEXT_NODE || live.nodeType === Node.COMMENT_NODE;
  if (live.tagName !== next.tagName) return false;
  // The main class names what the element IS (a Base PP row vs a stage summary); pairing
  // by tag alone made one inserted footer item shift every later sibling into a rebuild.
  if ((live.classList[0] || '') !== (next.classList[0] || '')) return false;
  if (live.matches(MORPH_REPLACE_SELECTOR) || next.matches(MORPH_REPLACE_SELECTOR)) return false;
  if (MORPH_KEYS.some((key) => live.getAttribute(key) !== next.getAttribute(key))) return false;
  if (live.tagName === 'IMG' && (live.dataset.cardImageCandidates || '') !== (next.dataset.cardImageCandidates || '')) return false;
  return true;
}
function morphNode(live, next) {
  if (live.nodeType !== Node.ELEMENT_NODE) {
    if (live.nodeValue !== next.nodeValue) live.nodeValue = next.nodeValue;
    return;
  }
  if (live.tagName === 'IMG' && morphSameImage(live, next)) return;
  for (const { name } of [...live.attributes]) if (!next.hasAttribute(name)) live.removeAttribute(name);
  for (const { name, value } of [...next.attributes]) if (live.getAttribute(name) !== value) live.setAttribute(name, value);
  morphChildren(live, next);
}
export function morphChildren(liveParent, nextParent) {
  const nextNodes = [...nextParent.childNodes];
  let liveChild = liveParent.firstChild;
  for (const next of nextNodes) {
    if (liveChild === next) { liveChild = liveChild.nextSibling; continue; }
    // One-step lookahead: a node removed from the middle is dropped rather than making
    // every node after it look different.
    if (liveChild && !morphCompatible(liveChild, next) && liveChild.nextSibling && morphCompatible(liveChild.nextSibling, next)) {
      const gone = liveChild;
      liveChild = liveChild.nextSibling;
      gone.remove();
    }
    if (liveChild && morphCompatible(liveChild, next)) {
      const kept = liveChild;
      liveChild = liveChild.nextSibling;
      morphNode(kept, next);
    } else {
      liveParent.insertBefore(next, liveChild);
    }
  }
  while (liveChild) {
    const gone = liveChild;
    liveChild = liveChild.nextSibling;
    gone.remove();
  }
}

// "Close all" in the filter bar closes every opened plot with the exact same steps as a
// single plot's Close; the button is disabled while no plot is open.
export function alignCloseAll() {
  // onto the plots' Close column (0.8rem inside the plot row's right edge).
  // "Open all" sits in the same place, aligned the same way.
  const closes = [...document.querySelectorAll('.editor-band > .eos-close')];
  ['close-all-plots', 'open-all-plots'].forEach((id) => {
    const button = document.getElementById(id);
    // Align to the plots' own Close buttons (when none is open, the filter bar's right edge is the place).
    if (!button || button.hidden) return;
    button.style.marginRight = '0px';
    // with no plot open, the same spot -- 0.8rem inside the table's right edge, where a plot's Close sits.
    const table = document.getElementById('plot-table');
    // viewport rects and the rem inset are one coordinate space (no page zoom).
    const target = (closes.length ? Math.max(...closes.map((c) => c.getBoundingClientRect().right))
      : table ? table.getBoundingClientRect().right
        - 0.8 * parseFloat(getComputedStyle(document.documentElement).fontSize) : null);
    if (target === null) return;
    // keep the collapse control 3.5rem left of the wallet slider overlay.
    const alignedTarget = target - (id === 'close-all-plots' ? 3.5 * parseFloat(getComputedStyle(document.documentElement).fontSize) : 0);
    button.style.marginRight = `${Math.round((button.getBoundingClientRect().right - alignedTarget) * 10) / 10}px`;
  });
}

// the plots the board shows right now (after the filters) that are not open.
function unopenedVisiblePlots() {
  return (CURRENT_ROWS || []).map((row) => row.deed_uid).filter((uid) => !EXPANDED_ROWS.has(uid));
}

export function syncCloseAll() {
  // only shown with 2+ open plots; aligned in the same task it is unhidden, so it never jumps.
  // (maintainer: "Add an Open All button at the same position ... Show it only when the staged filter is ticked.
  // Replace it for the Close All after it has been used"): with the Staged filter on, "Open all" while one of
  // the shown plots is still closed; once all are open it gives way to "Close all" (even for a single plot).
  const button = document.getElementById('close-all-plots');
  const openAll = document.getElementById('open-all-plots');
  if (!button) return;
  const stagedOn = document.getElementById('staged-only')?.checked === true && (CURRENT_ROWS || []).length > 0;
  const showOpen = stagedOn && unopenedVisiblePlots().length > 0;
  if (openAll) {
    if (showOpen && openAll.hidden) { openAll.hidden = false; alignCloseAll(); }
    else if (!showOpen) openAll.hidden = true;
  }
  const show = !showOpen && (EXPANDED_ROWS.size >= 2 || stagedOn);
  // Align at once, and once more after the new plot's reveal has settled (its Close may still be moving).
  if (show && button.hidden) { button.hidden = false; alignCloseAll(); setTimeout(alignCloseAll, 450); }
  else if (!show) button.hidden = true;
}

// (maintainer: "Scroll the first plot to the top, open it (unless it is already open), open the next but
// stay at the top plot, open the remaining ones in the background outside of the visible field"): the first shown
// plot lands under the header (its own opening scroll, or a plain scroll when it is open already); every further
// plot opens below it without touching the viewport, one after the other.
let OPEN_ALL_RUNNING = false;
export async function openAllVisiblePlots() {
  if (OPEN_ALL_RUNNING) return;
  OPEN_ALL_RUNNING = true;
  const openAll = document.getElementById('open-all-plots');
  if (openAll) openAll.disabled = true;
  try {
    const uids = (CURRENT_ROWS || []).map((row) => row.deed_uid);
    if (!uids.length) return;
    const first = uids[0];
    if (EXPANDED_ROWS.has(first)) scrollPlotUnderHeader(first);
    else await ensurePlotOpen(first);
    for (const uid of uids.slice(1)) if (!EXPANDED_ROWS.has(uid)) await ensurePlotOpen(uid, { keepViewport: true });
  } finally {
    OPEN_ALL_RUNNING = false;
    if (openAll) openAll.disabled = false;
    syncCloseAll();
  }
}

export function closeAllPlots() {
  [...EXPANDED_ROWS].forEach((uid) => {
    abortWorkerUpgradeOptions(uid);
    closePlotPresentation(uid);
    EXPANDED_ROWS.delete(uid);
    updateMountedRow(uid);
  });
  syncCloseAll();
}

// the "X Changes" badge follows the staged list. Only a plot whose unsigned count changed has its Badges
// cell redrawn -- the plot itself (and an open editor) is never touched.
const CHANGE_BADGE_COUNTS = new Map();
let STAGED_PLOT_COUNT = 0;
let BADGE_FILTER_RENDER_QUEUED = false;
function queueBadgeFilterRender() {
  if (BADGE_FILTER_RENDER_QUEUED) return;
  BADGE_FILTER_RENDER_QUEUED = true;
  queueMicrotask(() => {
    BADGE_FILTER_RENDER_QUEUED = false;
    renderTable();
  });
}
export function refreshChangeBadges() {
  if (!BOARD?.plots) return;
  // the Staged filter's count, and a re-filter when the set changes while the filter is on.
  const stagedCount = stagedPlotCount();
  const countEl = document.getElementById('staged-only-count');
  if (countEl) countEl.textContent = String(stagedCount);
  let refilter = Boolean(document.getElementById('badge-filter')?.value);
  if (stagedCount !== STAGED_PLOT_COUNT) {
    STAGED_PLOT_COUNT = stagedCount;
    if (document.getElementById('staged-only')?.checked) refilter = true;
  }
  if (refilter) queueBadgeFilterRender();
  // one pass over the stage and one plot index per refresh, instead of two stage scans per plot.
  const keys = stageBadgeKeysByDeed();
  const plotsByDeed = new Map(BOARD.plots.map((row) => [row.deed_uid, row]));
  const deeds = new Set(CHANGE_BADGE_COUNTS.keys());
  keys.forEach((key, deedUid) => { if (key !== '0|0' && plotsByDeed.has(deedUid)) deeds.add(deedUid); });
  for (const deedUid of deeds) {
    const key = keys.get(deedUid) || '0|0'; // phase 3: both badges
    if ((CHANGE_BADGE_COUNTS.get(deedUid) || '0|0') === key) continue;
    if (key !== '0|0') CHANGE_BADGE_COUNTS.set(deedUid, key); else CHANGE_BADGE_COUNTS.delete(deedUid);
    const row = plotsByDeed.get(deedUid);
    // amendment: the strip is no longer a table cell -- it is #badge-bar's own
    // per-row element (only present while this deed's <tr> is mounted, same universe
    // as before).
    const cell = document.querySelector(`#badge-bar .badge-bar-row[data-deed-uid="${CSS.escape(deedUid)}"]`);
    if (row && cell) cell.innerHTML = badgeStripHtml(row);
  }
  if (deeds.size) syncBadgeOverlay();
}

export function updateMountedRow(deedUid, { reserveOnly = false, readiness = false } = {}) {
  queueMicrotask(syncCloseAll); // every open/close path passes through here
  const row = BOARD?.plots.find((p) => p.deed_uid === deedUid);
  if (!row) return false;
  if (MOBILE_PLOT_UID === deedUid && renderMobilePlotSheet(row)) return true;
  const tbody = document.getElementById('plot-tbody');
  if (!tbody) return false;
  const tr = tbody.querySelector(`tr.plot-row[data-deed-uid="${deedUid}"]`);
  if (!tr) return false;
  const scroller = document.getElementById('plot-table-scroll');
  const savedLeft = scroller?.scrollLeft || 0;
  const anchor = captureTableAnchor();
  const previousBand = tr.nextElementSibling?.querySelector('.editor-band');
  beforePlotContentReplace(deedUid, { readiness });
  const cellsShell = document.createElement('template');
  const listedIndex = Number(tr.dataset.rowIndex);
  cellsShell.innerHTML = `<table><tbody><tr>${plotRowCellsHtml(row, Number.isInteger(listedIndex) ? listedIndex : null)}</tr></tbody></table>`;
  const nextCells = cellsShell.content.querySelector('tr');
  if (nextCells) morphChildren(tr, nextCells); else tr.innerHTML = plotRowCellsHtml(row, Number.isInteger(listedIndex) ? listedIndex : null);
  const expand = tr.nextElementSibling;
  if (expand && expand.classList.contains('expand-row')) {
    expand.style.display = EXPANDED_ROWS.has(deedUid) ? '' : 'none';
    const td = expand.querySelector('td');
    if (td) {
      const shell = document.createElement('template');
      shell.innerHTML = workerSubtable(row);
      const nextBand = shell.content.querySelector('.editor-band');
      const handoff = handoffPlotContent(row, previousBand, nextBand, { readiness });
      if (nextBand && handoff && handoff !== nextBand) nextBand.replaceWith(handoff);
      morphChildren(td, shell.content); // patch in place, no whole-plot flicker
      if (!reserveOnly) attachMountedPlot(row, expand);
      // Dropdown-unification batch -- see initPickerFilterDropdowns()'s own
      // comment. This is the OTHER call site that rebuilds the picker's
      // filter-bar DOM from scratch (openPicker()/preset+sort clicks/staging
      // actions all funnel through updateMountedRow()); fetchPickerPage()'s
      // fast path (updatePickerRegion()) only ever touches `.candidate-list`
      // `.picker-footer`, never this <select> markup, so it needs no re-init.
      if (S.CERES_PICKER && S.CERES_PICKER.deedUid === deedUid) initPickerFilterDropdowns(td);
    }
  }
  refreshVirtualLayout(deedUid, anchor);
  if (scroller) scroller.scrollLeft = savedLeft;
  return true;
}

// Lean list payload ( board-fix batch, item 3): GET /api/board no longer
// embeds `workers` in any row -- see ceres/board.py's `_plot_row(lean=...)`. So
// expanding a row now ALWAYS needs one fetch on first open (EXPANDED_DATA_LOADED
// tracks "already done this session"), never zero -- but WHICH endpoint depends on
// whether the server needs to do real work: `row.assets_loaded` true means the
// server already has this deed's assets cached on disk, so a cheap read-only GET
// (project_single_plot(), no network I/O) suffices; false means a real per-deed
// fetch is needed, the existing POST .../expand path (network + on-disk merge).
// open a plot without a click; idempotent (an open or loading plot is left as it is).
export async function ensurePlotOpen(deedUid, { keepViewport = false } = {}) {
  const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
  if (!row) return false;
  if (!EXPANDED_ROWS.has(deedUid)) await toggleRow(row, { keepViewport });
  return true;
}

// `keepViewport` (): open without the landing scroll -- the page keeps its place (Open all opens the
// plots below the first one in the background).
async function toggleRow(row, { keepViewport = false } = {}) {
  const scroller = document.getElementById('plot-table-scroll');
  const savedLeft = scroller?.scrollLeft || 0;
  const restoreScroll = () => requestAnimationFrame(() => {
    if (scroller && scroller.scrollLeft !== savedLeft) scroller.scrollLeft = savedLeft;
  });
  if (EXPANDED_ROWS.has(row.deed_uid)) {
    abortWorkerUpgradeOptions(row.deed_uid);
    closePlotPresentation(row.deed_uid);
    EXPANDED_ROWS.delete(row.deed_uid);
    updateMountedRow(row.deed_uid);
    restoreScroll();
    return;
  }
  WORKER_UPGRADE_REQUESTS.forEach((request, deedUid) => {
    if (deedUid !== row.deed_uid) abortWorkerUpgradeOptions(deedUid);
  });
  // an open that lands the plot under the header needs its row in the DOM -- on a windowed table (a jump
  // from Changes, a deep open) the row may sit outside the mounted window; this mounts it first. A background
  // open (keepViewport) leaves the window alone: EXPANDED_ROWS keeps the plot open for when the window reaches it.
  if (!keepViewport) ensureRowMounted(row.deed_uid);
  const { width, font } = geometrySnapshot();
  const session = beginPlotOpening(row, width, font);
  EXPANDED_ROWS.add(row.deed_uid);
  updateMountedRow(row.deed_uid, { reserveOnly: true });
  const reservedGeometry = geometrySnapshot();
  if (reservedGeometry.width !== width || reservedGeometry.font !== font) {
    session.geometryProfile = plotGeometryProfile(reservedGeometry.width, reservedGeometry.font, stakedCardSlots(row));
    updateMountedRow(row.deed_uid, { reserveOnly: true });
  }
  const expand = document.querySelector(`tr.expand-row[data-deed-uid="${CSS.escape(row.deed_uid)}"]`);
  const frame = expand?.querySelector('.editor-band');
  if (keepViewport) { if (session.scrollLease) releasePlotViewportLease(session.scrollLease); session.scrollLease = null; }
  else if (frame) centreReservedPlot(session, frame);
  attachMountedPlot(row, expand);
  if (EXPANDED_DATA_LOADED.has(row.deed_uid) || LOADING_PLOTS.has(row.deed_uid)) {
    restoreScroll();
    return;
  }
  await loadPlotDetail(row);
  restoreScroll();
}

// while this plot's picker is open, the upgrade check only changes the picker's footer status
// refresh that instead of redrawing the whole plot (the redraw rebuilt the open picker ~0.6s after opening).
function refreshPickerFooterOnly(row) {
  if (S.CERES_PICKER?.deedUid !== row.deed_uid) return false;
  return updatePickerRegion(row.deed_uid, { footerOnly: true });
}

export function workerUpgradeOptionsState(row) {
  const request = WORKER_UPGRADE_REQUESTS.get(row?.deed_uid);
  return { loading: !!request?.loading, error: request?.error || null };
}

export function abortWorkerUpgradeOptions(deedUid) {
  const request = WORKER_UPGRADE_REQUESTS.get(deedUid);
  if (!request) return;
  request.aborted = true;
  request.runId = (request.runId || 0) + 1;
  request.loading = false;
  request.controller?.abort();
}

function workerUpgradeRowIsReady(row, generation) {
  if (!row || generation !== BOARD_GENERATION || !EXPANDED_ROWS.has(row.deed_uid)
    || !EXPANDED_DATA_LOADED.has(row.deed_uid)) return false;
  const frame = document.querySelector(`tr.expand-row[data-deed-uid="${CSS.escape(row.deed_uid)}"] .editor-band`);
  return frame?.dataset.plotState === 'ready' || frame?.dataset.plotState === 'degraded';
}

export function requestWorkerUpgradeOptions(row, generation = BOARD_GENERATION) {
  if (!workerUpgradeRowIsReady(row, generation)) return null;
  let request = WORKER_UPGRADE_REQUESTS.get(row.deed_uid);
  if (request?.row !== row || request.generation !== generation) {
    if (request) abortWorkerUpgradeOptions(row.deed_uid);
    request = { row, generation, controller: null, loading: false, aborted: false, runId: 0, error: null, promise: null, results: new Map() };
    WORKER_UPGRADE_REQUESTS.set(row.deed_uid, request);
  }
  if (request.loading || request.promise && !request.aborted) return request.promise;
  request.controller = new AbortController();
  request.loading = true;
  request.aborted = false;
  request.runId = (request.runId || 0) + 1;
  const runId = request.runId;
  request.error = null;
  const current = () => generation === BOARD_GENERATION
    && WORKER_UPGRADE_REQUESTS.get(row.deed_uid) === request
    && request.runId === runId
    && !request.aborted
    && BOARD?.plots.find((plot) => plot.deed_uid === row.deed_uid) === row;
  if (!refreshPickerFooterOnly(row)) updateMountedRow(row.deed_uid);
  const workers = Array.isArray(row.workers) ? row.workers.filter((worker) => worker?.asset_ref) : [];
  request.promise = (async () => {
    let next = 0;
    let active = 0;
    await new Promise((resolve) => {
      const pump = () => {
        if (request.aborted || request.runId !== runId) { resolve(); return; }
        if (next >= workers.length && active === 0) { resolve(); return; }
        while (active < WORKER_UPGRADE_CONCURRENCY && next < workers.length) {
          const worker = workers[next++];
          const key = workerUpgradeKey(row, worker);
          if (request.results.has(key) || WORKER_UPGRADE_OPTIONS.has(key)) continue;
          active++;
          (async () => {
            try {
              const endpoint = `/api/plots/${encodeURIComponent(row.deed_uid)}/workers/${encodeURIComponent(worker.asset_ref)}/upgrade-options`;
              const response = await fetch(playerQueryAppend(endpoint), { signal: request.controller.signal });
              const body = await response.json().catch(() => ({}));
              if (!current()) return;
              const result = response.ok ? body : { eligible: false, reasons: body.detail?.reasons || [] };
              request.results.set(key, result);
              WORKER_UPGRADE_OPTIONS.set(key, result);
            } catch (error) {
              if (!current() || error?.name === 'AbortError') return;
              const result = { eligible: false, reasons: [{ reason: 'upgrade options could not be verified' }] };
              request.results.set(key, result);
              WORKER_UPGRADE_OPTIONS.set(key, result);
              request.error = result.reasons[0].reason;
            } finally {
              active--;
              pump();
            }
          })();
        }
        if (next >= workers.length && active === 0) resolve();
      };
      pump();
    });
    if (current()) {
      request.loading = false;
      // A worker that became upgradable adds a button to its tile and copy counts to picker cards
      // only then is the whole plot redrawn.
      const upgradable = workers.some((worker) => workerUpgradeIsAvailable(worker, WORKER_UPGRADE_OPTIONS.get(workerUpgradeKey(row, worker))));
      if (upgradable || !refreshPickerFooterOnly(row)) updateMountedRow(row.deed_uid);
    }
  })();
  return request.promise;
}

// Fetch logic factored out of toggleRow() (2026-08-21, `work/ceres-plot-expand-bug.md`)
// so the Retry link in workerSubtable()'s error state (EXPAND_ERRORS) can re-run
// exactly the same fetch, not a re-implementation of it.
// fix: the one switch for the tile spinner -- a class on the stable expand row (survives
// updateMountedRow's morph) mirrored on the session (survives a renderRow remount).
function markSlowOpen(deedUid, on) {
  const session = getPlotPresentation(deedUid);
  if (session) session.slowOpen = on;
  document.querySelector(`tr.expand-row[data-deed-uid="${CSS.escape(deedUid)}"]`)?.classList.toggle('is-slow-open', on);
}

// poll /api/capture/status at the cadence until the publication token differs
// from the interim one or the run is no longer running; resolves either way (the caller retries once).
export async function waitForBoardToFinish(stillCurrent) {
  const startedAt = Date.now();
  let interimToken;
  let estimatedSeconds = null;
  for (let attempt = 0; Date.now() - startedAt < CAPTURE_STATUS_MAX_WAIT_MS; attempt += 1) {
    if (!stillCurrent()) return;
    let status;
    try {
      const response = await fetch(playerQueryAppend('/api/capture/status'));
      if (!response.ok) return;
      status = await response.json();
    } catch (error) {
      return;
    }
    if (Number.isFinite(status.estimated_seconds)) estimatedSeconds = status.estimated_seconds;
    const token = status.latest_snapshot?.token ?? null;
    if (interimToken === undefined) interimToken = token;
    if (status.state !== 'running' && status.state !== 'queued') return;
    if (token !== interimToken || status.interim === false) return;
    await new Promise((resolve) => setTimeout(resolve, captureStatusPollInterval(attempt, Date.now() - startedAt, estimatedSeconds)));
  }
}

export async function loadPlotDetail(row, generation = BOARD_GENERATION) {
  const deedUid = row.deed_uid;
  const existing = DETAIL_REQUESTS.get(deedUid);
  if (existing?.generation === generation) return existing.promise;
  const owner = { generation, player: CURRENT_PLAYER || BOARD?.player || null, requestId: ++requestSerial, row, promise: null };
  DETAIL_REQUESTS.set(deedUid, owner);
  const samePlayer = () => owner.player === CURRENT_PLAYER && (!BOARD?.player || BOARD.player === owner.player);
  const current = () => generation === BOARD_GENERATION && samePlayer()
    && DETAIL_REQUESTS.get(deedUid) === owner;
  const latestEligibleRow = () => {
    if (!samePlayer() || !EXPANDED_ROWS.has(deedUid) || EXPANDED_DATA_LOADED.has(deedUid)) return null;
    const latest = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
    if (!latest) return null;
    return latest;
  };
  const handoffRow = () => {
    if (!current()) return null;
    const latest = latestEligibleRow();
    if (!latest) return null;
    owner.row = latest;
    return latest;
  };
  LOADING_PLOTS.add(deedUid);
  EXPAND_ERRORS.delete(deedUid);
  const session = getPlotPresentation(deedUid);
  if (session?.error) session.animateAllowed = false;
  if (!session || session.error) updateMountedRow(deedUid);
  // fix: the tile spinner is switched on by THIS timer, never by a CSS delay -- only if
  // the detail is still pending 400 ms after the request started (a fast open never shows it)
  // and the class sits on the stable expand row so re-renders neither restart nor flash it.
  owner.slowTimer = setTimeout(() => {
    owner.slowTimer = null;
    if (current() && EXPANDED_ROWS.has(deedUid) && LOADING_PLOTS.has(deedUid) && !EXPANDED_DATA_LOADED.has(deedUid)) markSlowOpen(deedUid, true);
  }, 400);
  owner.promise = (async () => {
  try {
    const endpoint = row.assets_loaded
      ? `/api/plots/${encodeURIComponent(row.deed_uid)}`
      : `/api/plots/${encodeURIComponent(row.deed_uid)}/expand`;
    const method = row.assets_loaded ? 'GET' : 'POST';
    let resp;
    // a network-layer failure (fetch()
    // itself throwing -- the local auto-reload restart, a deploy, a dropped
    // connection) is retried for ~31.5 s (board.js::NETWORK_RETRY_DELAYS_MS) while the
    // tile says "Reconnecting to Ceres…". Only after that does the tile show "Could not
    // reach Ceres" with a Try again button. Non-2xx responses are never retried here; an
    // abort or a stale row/generation stops the loop silently.
    let reconnecting = false;
    // a 409 `board_finishing` means the board on screen is the interim artifact
    // (bulk read published, final publish still landing). Keep the spinner, say
    // "Finishing the read…", wait for the status token to change (or the run to settle) at
    // the cadence, then retry ONCE -- never the previous read's snapshot.
    let finishingRetried = false;
    for (;;) {
      try {
        resp = await fetchWithNetworkRetry(playerQueryAppend(endpoint), { method }, {
          onRetry: () => {
            if (!current()) return false;
            if (!reconnecting) { reconnecting = true; setPlotReconnecting(deedUid, true); }
            return true;
          },
        });
      } catch (networkErr) {
        if (reconnecting) setPlotReconnecting(deedUid, false);
        if (!current() || networkErr?.name === 'AbortError') return;
        if (current()) EXPAND_ERRORS.set(row.deed_uid, PLOT_UNREACHABLE_ERROR);
        return;
      }
      if (reconnecting) setPlotReconnecting(deedUid, false);
      if (resp.status !== 409 || finishingRetried) break;
      const refusal = await resp.clone().json().catch(() => ({}));
      if (refusal?.reason !== 'board_finishing') break;
      finishingRetried = true;
      setPlotFinishing(deedUid, true);
      try {
        await waitForBoardToFinish(current);
      } finally {
        setPlotFinishing(deedUid, false);
      }
      if (!current()) return;
    }
    if (resp.ok) {
      let updated = await resp.json();
      let targetRow = row;
      let idx = BOARD?.plots.findIndex((p) => p.deed_uid === row.deed_uid) ?? -1;
      if (!current() || idx === -1 || BOARD.plots[idx] !== row) {
        targetRow = handoffRow();
        if (!targetRow) return;
        idx = BOARD.plots.findIndex((p) => p.deed_uid === deedUid);
      }
      if (!current() || idx === -1 || BOARD.plots[idx] !== targetRow) return;
      // the plain GET is
      // chosen because the BOARD row says this plot is loaded, but the server's detail can still be the un-loaded one
      // every worker without a slot, totem/title named from their boost ("Epic Totem Land", "Rare Title"). Drawing
      // it put a random worker in the Runi tile and emptied every worker slot. Checked only AFTER the handoff above
      // so a generation bump still hands off exactly as before: a detail that is not loaded falls through to the full
      // load once, and if that is not loaded either it is refused, never drawn.
      if (updated?.assets_loaded !== true && method === 'GET') {
        let expanded = null;
        try {
          expanded = await fetchWithNetworkRetry(playerQueryAppend(`/api/plots/${encodeURIComponent(deedUid)}/expand`), { method: 'POST' });
        } catch (networkErr) {
          if (networkErr?.name === 'AbortError') return;
        }
        if (expanded?.ok) updated = await expanded.json();
        idx = BOARD?.plots.findIndex((p) => p.deed_uid === deedUid) ?? -1;
        if (!current() || idx === -1 || BOARD.plots[idx] !== targetRow) return;
      }
      if (updated?.assets_loaded !== true) {
        EXPAND_ERRORS.set(deedUid, "This plot's workers did not load. Try again.");
        return;
      }
      reconcileCapPrediction(deedUid, updated);
      updated._badge_roster_loaded = true;
      if (idx !== -1) BOARD.plots[idx] = updated;
      applyStageToRow(updated); // phase 2 / : staged moves survive a fresh row
      // CURRENT_ROWS is a SNAPSHOT of the filtered/sorted array taken at the last
      // render() -- it holds the same object REFERENCES BOARD.plots held at that
      // moment, not a live view. Replacing BOARD.plots[idx] above does not update
      // CURRENT_ROWS's own reference at the matching position -- without this patch
      // scrolling the row out of view and back would remount it from the outdated
      // pre-fetch object (no workers, no bonus identity) even though BOARD.plots
      // itself is correct. Keep both in sync explicitly.
      const cidx = CURRENT_ROWS.findIndex((p) => p.deed_uid === deedUid);
      if (cidx !== -1) CURRENT_ROWS[cidx] = updated;
      EXPANDED_DATA_LOADED.add(deedUid);
    } else {
      const body = await resp.json().catch(() => ({}));
      if (current()) EXPAND_ERRORS.set(row.deed_uid, body.detail || `HTTP ${resp.status}`);
    }
  } catch (err) {
    // Inline in-row error + Retry link (workerSubtable()), not alert() -- an
    // alert() is the wrong affordance for a transient blip: it's disruptive and
    // gives no way to recover without closing and re-opening the row.
    if (current()) EXPAND_ERRORS.set(row.deed_uid, String(err));
  } finally {
    if (owner.slowTimer) { clearTimeout(owner.slowTimer); owner.slowTimer = null; }
    if (DETAIL_REQUESTS.get(deedUid) !== owner) return;
    const successor = latestEligibleRow();
    if (!EXPANDED_DATA_LOADED.has(deedUid) && successor
      && (successor !== row || generation !== BOARD_GENERATION)) {
      DETAIL_REQUESTS.delete(deedUid);
      LOADING_PLOTS.delete(deedUid);
      loadPlotDetail(successor, BOARD_GENERATION);
      return;
    }
    DETAIL_REQUESTS.delete(deedUid);
    LOADING_PLOTS.delete(deedUid);
    markSlowOpen(deedUid, false); // fix: detail landed or failed -- the spinner switch goes off before the redraw
    if (BOARD && document.getElementById('badge-filter')?.value) {
      renderTable();
      // The filtered board rebuild does not redraw the separate mobile sheet.
      if (MOBILE_PLOT_UID === deedUid) updateMountedRow(deedUid, { readiness: true });
      queueMicrotask(syncCloseAll);
    } else if (BOARD) updateMountedRow(deedUid, { readiness: true });
    if (S.CERES_CHECKOUT_OPEN) renderCheckoutSurface();
  }
  })();
  return owner.promise;
}

// Portfolio-wide totals (Base PP, Harvest PP, DEC needed, idle/plot counts)
// server-computed in `ceres/board.py` (dec_committed/dec_shortfall/portfolio_dec_
// efficiency/etc. all fold into the few figures actually shown), never re-summed
// from BOARD.plots here: this project's own convention is one source of truth per
// number, never a second hand-maintained copy that can drift (same reasoning as
// notices.py's BADGE_ORDER). refreshBoardTotals() below is what keeps this fresh
// after a confirmed broadcast without a full loadBoard().
export function renderTotalsStrip() {
  const totals = BOARD?.totals;
  const strip = document.getElementById('totals-strip');
  if (!totals || !strip) return;
  // "Portfolio PP/DEC" removed from the header ( board-fix batch, the owner
  // does not want that figure there) -- it was the metric whose denominator
  // (totals.portfolio_dec_efficiency, derived from the untrustworthy
  // total_dec_staked field flagged in docs/ceres.md's DEC-metric-rules section) we
  // could not fully stand behind, so removing it also removes a number this board
  // was showing with a permanent asterisk. `totals.portfolio_dec_efficiency`
  // `_excluded_plots` stay in the `/api/board` payload (still contract-tested
  // `tests/ceres/test_metrics_dec.py`) -- this is a display-only removal, not a
  // payload change. The per-plot PP/DEC column (fmtDecEfficiency) is unaffected.
  strip.innerHTML = `
    <span>Base PP: <strong>${num(totals.base_pp)}</strong></span>
    <span>Total PP: <strong>${num(totals.harvest_pp)}</strong></span>
    <span>DEC stake: <strong>${num(totals.dec_needed)}</strong></span>
    <span>Idle plots: <strong>${totals.plots_idle}</strong></span>
    <span>Plots: <strong>${totals.plots}</strong></span>`;
}

// Item 9 of the worker-table rebuild (maintainer's spec): "100% of page data updated
// instantly when a worker tile or asset tile is changed" -- named explicitly
// "plot totals... must re-render". refreshConfirmedPlot() already patched the one
// changed row in place, but never touched this portfolio-wide strip, so Base PP
// Harvest PP/DEC needed/Idle plots went stale after every confirmed stake or
// unstake until the next full page load (the same gap class as /).
// `GET /api/board` triggers no capture (the internal rules) -- this is a cheap cached read
// not a network fetch against the game -- so re-fetching it for authoritative
// totals is correct AND cheap, not a re-derivation of board.py's own math.
export async function refreshBoardTotals(generation = BOARD_GENERATION, player = CURRENT_PLAYER) {
  try {
    const response = await fetch(playerQueryAppend('/api/board'));
    if (!response.ok) return;
    const payload = await response.json();
    if (generation !== BOARD_GENERATION || player !== CURRENT_PLAYER || !BOARD) return;
    BOARD.totals = payload.totals;
    renderTotalsStrip();
  } catch (_) {
    // The previous totals remain on screen if this fetch fails -- never blank
    // never a fabricated number.
  }
}
