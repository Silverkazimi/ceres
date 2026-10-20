// phase 1 ("Exact plot-and-tile
// navigation"): every change row in the Changes panel opens its plot and highlights the exact tile. It never simulates a
// tile click (that could open a picker or undo a removal) -- it scrolls the row into the mounted window, opens the plot
// through the ordinary path, marks the tile until the next pointer press or Escape.
import { BOARD, setPlotFirstHooks, syncWorkersReady } from './board.js';
import { setReady } from './ceres-ready.js';
import { renderCheckoutSurface } from './checkout.js';
import { finishPlotReveal, getPlotPresentation } from './eos.js';
import { esc, formatPlotLabel } from './format.js';
import { EXPANDED_DATA_LOADED, EXPAND_ERRORS, closeMobilePlotSheet, ensurePlotOpen, mobilePlotSheetIsOpen, openMobilePlotSheet } from './rows.js';
import { stageToast } from './staging.js';
import { S } from './state.js';
import { cancelPlotViewportLease, render, revealPlotRow } from './table.js';

let NAV = 0; // a newer navigation supersedes an older one still waiting
let OBSERVER = null;

function expandRow(deedUid) { return document.querySelector(`tr.expand-row[data-deed-uid="${CSS.escape(deedUid)}"]`); }
function mobileNavigationActive() { return document.documentElement.classList?.contains('ceres-lw-le-1279') === true; }
function plotFrame(deedUid) {
  const sheet = document.getElementById('mobile-plot-sheet');
  if (mobileNavigationActive() && !sheet?.hidden && sheet?.dataset.deedUid === deedUid) return document.getElementById('mobile-plot-sheet-body');
  return expandRow(deedUid);
}
function nextFrame() { return new Promise((resolve) => requestAnimationFrame(() => resolve())); }
async function waitFor(test, token, ms) {
  const end = performance.now() + ms;
  while (performance.now() < end) {
    if (token !== NAV) return null;
    const value = test();
    if (value) return value;
    await nextFrame();
  }
  return null;
}

function clearMark() {
  OBSERVER?.disconnect();
  OBSERVER = null;
  document.querySelectorAll('.is-nav-target').forEach((node) => node.classList.remove('is-nav-target'));
}
document.addEventListener('pointerdown', clearMark, true);
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') clearMark(); });

// A plot redraw (stage change, art load) rebuilds its tiles, so the mark is put back on the new node.
function mark(deedUid, find) {
  const body = mobileNavigationActive() ? document.getElementById('mobile-plot-sheet-body') : document.getElementById('plot-tbody'); // stable across surface redraws
  if (!body) return;
  const apply = () => { const node = find(plotFrame(deedUid)); if (node && !node.classList.contains('is-nav-target')) node.classList.add('is-nav-target'); };
  apply();
  OBSERVER = new MutationObserver(apply);
  OBSERVER.observe(body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
}

// phase 4: a jump to a plot the board's filters hide shows THAT one plot as an exception, never resets the
// filters; any filter edit, Restore view or a later jump ends it.
function endFilterException(redraw = true) {
  document.getElementById('nav-include-notice')?.remove();
  if (!S.CERES_NAV_INCLUDE) return;
  S.CERES_NAV_INCLUDE = null;
  if (redraw) render();
}
function showFilterException(deedUid, label) {
  S.CERES_NAV_INCLUDE = deedUid;
  render();
  const notice = document.createElement('div');
  notice.id = 'nav-include-notice';
  notice.className = 'nav-include-notice';
  notice.setAttribute('role', 'status');
  notice.innerHTML = `Showing plot ${esc(label)}, a planned plot outside your filters · <button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact">Restore view</button>`;
  notice.querySelector('button').addEventListener('click', () => endFilterException());
  document.body.appendChild(notice);
}
const FILTER_IDS = new Set(['text-filter', 'region-filter', 'rarity-filter', 'terrain-filter', 'resource-filter', 'status-filter', 'badge-filter', 'staged-only']);
['input', 'change'].forEach((type) => document.addEventListener(type, (event) => {
  if (FILTER_IDS.has(event.target?.id)) endFilterException(false); // the filter's own render follows
}, true));

// ONE final scroll, always the same target -- the plot's row directly under the sticky header, whether a
// card or the plot was clicked and whether the plot was open or not. The opened plot fills the space below it.
function settle(deedUid) {
  cancelPlotViewportLease();
  if (mobileNavigationActive()) { document.getElementById('mobile-plot-sheet-body')?.scrollTo?.({ top: 0, behavior: 'instant' }); window.scrollTo({ top: 0, behavior: 'instant' }); return; }
  revealPlotRow(deedUid);
}

// All slots are mounted in one row, so navigation can target the tile directly.
async function locate(deedUid, find, token) {
  await waitFor(() => plotFrame(deedUid)?.querySelector('.editor-band:not([data-eos-detail-scaffold]) .slot-group'), token, 3000);
  return find(plotFrame(deedUid));
}

function slotFinder(groupId, slotIndex) {
  const sel = `[data-group-id="${CSS.escape(String(groupId))}"][data-slot-index="${CSS.escape(String(slotIndex))}"]`;
  return (frame) => frame?.querySelector(`.slot-tile${sel}, .power-satellite${sel}`) || null;
}
function assetFinder(assetRef) {
  return (frame) => frame?.querySelector(`[data-reveal-group="worker-${CSS.escape(String(assetRef))}"] .slot-tile`) || null;
}

// { deedUid, groupId, slotIndex } opens that slot; { deedUid, assetRef } finds the card on that plot (an orange move's
// source); { deedUid } alone opens the plot.
export async function revealStagedChange({ deedUid, groupId = null, slotIndex = null, assetRef = null }) {
  const token = ++NAV;
  clearMark();
  const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
  if (!row) { stageToast('That plot is no longer on this board.', true); return false; }
  const label = formatPlotLabel(row.plot || deedUid);
  if (mobileNavigationActive()) {
    if (S.CERES_CHECKOUT_OPEN) { S.CERES_CHECKOUT_OPEN = false; renderCheckoutSurface(); }
    const alreadyOpen = mobilePlotSheetIsOpen();
    const card = document.querySelector(`.plot-card[data-deed-uid="${CSS.escape(deedUid)}"]`);
    const samePlot = alreadyOpen && document.getElementById('mobile-plot-sheet')?.dataset.deedUid === deedUid;
    if (alreadyOpen && !samePlot) closeMobilePlotSheet({ restoreFocus: false });
    if (!samePlot && !openMobilePlotSheet(deedUid, card)) { stageToast(`Plot ${label} could not be shown.`, true); return false; }
    if (!alreadyOpen) history.pushState({ ...(history.state || {}), ceresMobilePlotSheet: true }, '');
    await waitFor(() => EXPANDED_DATA_LOADED.has(deedUid) || EXPAND_ERRORS.has(deedUid), token, 15000);
    if (token !== NAV) return false;
    if (!EXPANDED_DATA_LOADED.has(deedUid)) { stageToast(`Plot ${label} could not be loaded. Try again.`, true); return false; }
    const find = assetRef != null ? assetFinder(assetRef) : groupId != null && slotIndex != null ? slotFinder(groupId, slotIndex) : null;
    const node = find ? await locate(deedUid, find, token) : null;
    if (token !== NAV) return false;
    settle(deedUid);
    node?.scrollIntoView?.({ block: 'center', behavior: 'instant' });
    if (node) { mark(deedUid, find); return true; }
    if (find) stageToast(assetRef != null ? `That card is no longer on plot ${label}.` : `That slot is not shown on plot ${label}.`, true);
    return !find;
  }
  if (S.CERES_NAV_INCLUDE && S.CERES_NAV_INCLUDE !== deedUid) endFilterException();
  if (!revealPlotRow(deedUid)) {
    showFilterException(deedUid, label);
    if (!revealPlotRow(deedUid)) { stageToast(`Plot ${label} could not be shown.`, true); return false; }
  }
  if (S.CERES_CHECKOUT_OPEN) { S.CERES_CHECKOUT_OPEN = false; renderCheckoutSurface(); }
  await ensurePlotOpen(deedUid);
  await waitFor(() => EXPANDED_DATA_LOADED.has(deedUid) || EXPAND_ERRORS.has(deedUid), token, 15000);
  if (token !== NAV) return false;
  if (!EXPANDED_DATA_LOADED.has(deedUid)) { stageToast(`Plot ${label} could not be loaded. Try again.`, true); return false; }
  cancelPlotViewportLease();
  finishPlotReveal(getPlotPresentation(deedUid));
  const find = assetRef != null ? assetFinder(assetRef) : groupId != null && slotIndex != null ? slotFinder(groupId, slotIndex) : null;
  const node = find ? await locate(deedUid, find, token) : null;
  if (token !== NAV) return false;
  settle(deedUid);
  if (node) { mark(deedUid, find); return true; }
  if (find) stageToast(assetRef != null ? `That card is no longer on plot ${label}.` : `That slot is not shown on plot ${label}.`, true);
  return false;
}

// the Cards page links a staked card's region badge to /?plot=<plot label>. That plot opens the same way a
// Changes badge opens it; the parameter is then cleared, so a reload does not reopen it.
// board.js reads and draws that ONE plot first (`drawn`), before the
// rest of the board, and calls `anchor` in the same task as the full-board draw so the open plot keeps its place. Only
// when the plot-first read could not draw it (an unknown label, a failed read) does the full board's reveal open it.
export function openPlotFromAddress() {
  const wanted = new URLSearchParams(location.search).get('plot');
  if (!wanted) return;
  const wantedLabel = formatPlotLabel(wanted);
  setReady({ plotWanted: wantedLabel });
  let handled = false;
  const clearParam = () => { const url = new URL(location.href); url.searchParams.delete('plot'); history.replaceState(history.state, '', url); };
  const open = (row) => {
    if (handled) return;
    handled = true;
    clearParam();
    const label = formatPlotLabel(row.plot || row.deed_uid);
    revealStagedChange({ deedUid: row.deed_uid }).then(() => announcePlotOpen(row.deed_uid, label)).catch(() => {});
  };
  setPlotFirstHooks({ label: wanted, drawn: open, anchor: anchorPlot });
  window.addEventListener('ceres:board-revealed', () => {
    if (handled) return;
    const row = (BOARD?.plots || []).find((plot) => formatPlotLabel(plot.plot) === wantedLabel || String(plot.plot) === wanted);
    if (row) { open(row); return; }
    handled = true;
    clearParam();
    setReady({ plotMissing: true });
    stageToast(`Plot ${wanted} is not on this board.`, true);
  }, { once: true });
}

// ready signal: `plot` names the address's plot once its editor band is drawn with its workers.
async function announcePlotOpen(deedUid, label) {
  const token = NAV;
  const band = await waitFor(() => EXPANDED_DATA_LOADED.has(deedUid)
    && plotFrame(deedUid)?.querySelector('.editor-band:not([data-eos-detail-scaffold]) .slot-group'), token, 15000);
  if (!band) return;
  syncWorkersReady();
  setReady({ plot: label });
}

// The full board (or the settle re-read) was just drawn: the plot comes back to the place it had, in the same task.
function anchorPlot(deedUid, previousTop) {
  if (mobileNavigationActive()) return;
  if (!revealPlotRow(deedUid)) {
    const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
    showFilterException(deedUid, formatPlotLabel(row?.plot || deedUid));
    if (!revealPlotRow(deedUid)) return;
  }
  if (previousTop === null || previousTop === undefined) return;
  const row = document.querySelector(`tr.plot-row[data-deed-uid="${CSS.escape(deedUid)}"]`);
  const delta = row ? row.getBoundingClientRect().top - previousTop : 0;
  if (Math.abs(delta) > 0.5) window.scrollBy({ top: delta, behavior: 'instant' });
}
