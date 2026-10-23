import { unmountPlotPresentation } from './eos.js';
import { BOARD, matchesFilters, renderNotices, sortRows } from './board.js';
import { prefetchIdleCardsCache } from './combine.js';
import { EXPANDED_HEIGHTS, EXPANDED_ROWS, renderRow, renderTotalsStrip, attachMountedPlot, handoffPlotContent, plotCardHtml } from './rows.js';
// render() resets S.IDLE_CARDS_CACHE ( reland: dropping this import broke ).
import { PLOT_TABLE_COLUMN_COUNT, S } from './state.js';

// Measured (never hardcoded) sticky-header offset for the main table's `th`
// (board-polish batch 19, item 16 -- see ceres.css's `.table-scroll`
// `table.plot-table#plot-table th` comments for why this exists: with
// `.table-scroll` no longer vertically scrolling, `th`'s sticky containing
// block is the viewport again and must clear `header.board-header`'s own
// sticky band). Sets `--ceres-header-height` on `:root` from
// `header.board-header`'s real `getBoundingClientRect().height` -- a stale
// hardcoded constant (`3.4rem`) is literally what caused , so this
// must stay a live measurement, re-run on anything that can change the
// header's rendered height: window resize, AND a ResizeObserver on the
// header itself (covers content-driven height changes with no viewport
// resize at all, e.g. `.totals-strip` re-wrapping after a player switch
// changes its text length).
// maintains `aria-sort` on headers from the current S.SORT_KEY/S.SORT_DIR
// SORT_DIR === 1 is ascending
// 1 (the default, state.js) is descending, matching sortRows()'s own comparator in
// board.js. Called from render() below (the one place both the initial
// restoreSortChoice() load and every later sort/filter render pass through) and from
// app.js's sort click handler, so the header reflects the current state immediately.
// `.sort-chevron` (ceres.css) reads this attribute, not a class, so there is one
// source of truth for the visible indicator.
export function syncSortAriaState() {
  document.querySelectorAll('#plot-table thead th[data-key]').forEach((th) => {
    const key = th.dataset.key;
    const active = key === S.SORT_KEY;
    if (!active) { th.setAttribute('aria-sort', 'none'); return; }
    // Pre-landing fix: board.js::sortByPlot inverts SORT_DIR for the 'plot' key
    // specifically (`dir = SORT_DIR === 1 ? -1 : 1`, comment: "SORT_DIR starts at
    // 1 -> ascending") -- every other column's own comparator in sortRows() uses
    // SORT_DIR directly (SORT_DIR === 1 is ascending there). Mirror both mappings
    // here so the chevron/aria-sort never disagrees with the actual row order.
    const ascending = key === 'plot' ? S.SORT_DIR === -1 : S.SORT_DIR === 1;
    th.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
  });
}

export function updateHeaderHeightVar() {
  const header = document.querySelector('header.board-header');
  if (!header) return;
  const h = header.getBoundingClientRect().height; // V
  if (h > 0) document.documentElement.style.setProperty('--ceres-header-height', `${h}px`); // V
  // (maintainer: "the wallet and flag slider are still not sticky in their
  // respective rows") made `.controls-row` (the filter row) sticky too, at the
  // same `--ceres-header-height` offset the thead used to use alone -- MEASURED
  // that made the real sticky thead's own column-label row stick UNDER the now
  // permanently-stuck filter row once both were stuck simultaneously (the filter
  // row visually painted over the whole header row, `z-index: 30` vs the thead's
  // `9`), hiding the plot table's column headers entirely. `--ceres-header-row
  // height` is the combined offset (header + filter row, both real measured
  // heights, no hardcoded guess) `thead.table-head-sticky` now sticks to instead
  // (ceres.css) -- `.controls-row` itself keeps using the bare `--ceres-header
  // height` (it only needs to clear the header, not itself). `.controls-row` is
  // 0-height / not rendered on the mobile tier (its own sticky rule is desktop
  // only), so this naturally collapses back to just the header height there
  // where `#table-head-sticky` is hidden anyway and never reads this var.
  const controlsRow = document.querySelector('.controls-row');
  const controlsRowHeight = controlsRow?.getBoundingClientRect().height; // V
  if (h > 0) {
    const combined = h + (controlsRowHeight > 0 ? controlsRowHeight : 0);
    document.documentElement.style.setProperty('--ceres-header-row-height', `${combined}px`); // V
  }
  // (badge-bar amendment). `--ceres-thead-height` drives `.badge-bar-head`'s
  // own `block-size` (ceres.css) -- reinstated by after 's measured
  // follow-up had briefly retired it -- so the glyph's own box matches the real
  // sticky thead's height exactly, centring it on the header band by construction.
  // Measured here, next to the header height above, rather than guessed as a CSS
  // constant, for the same reason `--ceres-header-height` itself is measured and not
  // hardcoded.
  const thead = document.getElementById('table-head-sticky');
  const theadHeight = thead?.getBoundingClientRect().height; // V
  if (theadHeight > 0) document.documentElement.style.setProperty('--ceres-thead-height', `${theadHeight}px`); // V
}

// Wrapped badges make plot rows unequal. Measure mounted rows in one batch and
// use cumulative heights for virtual spacers; never change column widths.
export function remeasureRowHeight() {
  if (isMobileBoardTier() || !MOUNTED_NODES.size) return;
  const anchor = captureTableAnchor();
  if (measureMountedRowHeights()) {
    updateVirtualSpacers();
    restoreTableAnchor(anchor);
    renderVirtualWindow(false, anchor);
    // pre-landing fix: a ResizeObserver-driven remeasure (a mounted row's own
    // content height changed -- e.g. its badge chips wrapped) never touches which rows
    // are mounted, so renderVirtualWindow()'s own notifyTableLayout() call doesn't fire
    // for it. Without this, the badge overlay kept stale row heights/positions until an
    // unrelated scroll or window resize happened to force a resync.
    notifyTableLayout();
  }
}

// CURRENT_ROWS is the complete filtered/sorted view. Only its DOM projection is windowed.
export let CURRENT_ROWS = [];
let CURRENT_INDEX_BY_UID = new Map();
let ROW_HEIGHT = null;
const MEASURED_ROW_HEIGHTS = new Map();
let ROW_OFFSETS = [0];
let ROW_MEASUREMENT_WIDTH = null;
let ROW_MEASUREMENT_FONT = null;
const ROW_RESIZE_OBSERVER = typeof ResizeObserver === 'function'
  ? new ResizeObserver(() => remeasureRowHeight()) : null;
const ROW_HEIGHT_FALLBACK = 32;
const VIRTUAL_BUFFER_ROWS = 12;
const VIRTUALIZE_THRESHOLD = 400;
// Row window is quantized to blocks of this size so a re-window (mount/unmount +
// full repaint) happens once per block scrolled rather than once per frame -- at
// 50 rows, measured on a 6,303-row account: 3.3fps -> 30fps. 1 (no quantization)
// was the prior default and is the regression case a test must reproduce; 150 was
// measured WORSE than 50 (bigger remounts per block), so 50 is not a rounded
// guess, it is the measured optimum of the sizes tried.
const VIRTUAL_WINDOW_BLOCK = 50;
let MOUNTED_START = -1;
let MOUNTED_END = -1;
const MOUNTED_NODES = new Map();
let FRONT_SPACER = null;
let BACK_SPACER = null;
let SCROLL_RAF = null;
let LAST_BOARD_PRESENTATION_TIER = null;
let MOBILE_CARD_RENDER_TOKEN = 0;
let MOBILE_CARD_RENDER_HANDLE = null;
const MOBILE_CARD_CHUNK_SIZE = 60;

// badge-overlay.js's own rows stay aligned with the table purely by reading
// each mounted <tr>'s rendered rect after any layout change -- it never duplicates
// table.js's row-offset bookkeeping. This is the one signal it listens for.
// amendment (pre-landing review fix, per-frame scroll cost): `reason` tells
// badge-overlay.js WHY layout changed. `onTableScroll()`'s own rAF callback below is
// the one call site that fires on every scroll frame of a virtualized board, even on
// the (common) frames where the mounted window did not actually shift -- and a mounted
// <tr>'s offset from `#plot-table-scroll` is INVARIANT under pure page scroll (both
// move together, since `.table-scroll` no longer scrolls internally -- ), so
// that frame's own dispatch carries reason 'scroll'. Every other call site (mount
// unmount, row-height remeasure, expand/collapse) is a genuine layout change and keeps
// the default 'layout'.
function notifyTableLayout(reason = 'layout') {
  if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    window.dispatchEvent(new CustomEvent('ceres:table-layout', { detail: { reason } }));
  }
}

export function isMobileBoardTier() {
  return document.documentElement.classList.contains('ceres-lw-le-1279');
}

export function maybeRenderOnBoardPresentationChange() {
  const next = isMobileBoardTier() ? 'cards' : 'table';
  if (LAST_BOARD_PRESENTATION_TIER === null) {
    LAST_BOARD_PRESENTATION_TIER = next;
    return false;
  }
  if (next === LAST_BOARD_PRESENTATION_TIER) return false;
  LAST_BOARD_PRESENTATION_TIER = next;
  if (BOARD) render();
  return true;
}

// `isNewDataset` (v0.4.13 filter-reflow fix, S1/S3): true only when the underlying
// BOARD.plots array itself just changed (board load, player switch, refresh, full
// capture -- the sole caller is loadBoard()). Every other render() caller (text
// select filters, staged toggle, sort) is re-filtering
// re-sorting the SAME dataset and passes nothing, defaulting to false. Threaded
// into renderTable() for two purposes: (S1) whether to reset window scroll -- see
// the window.scrollTo() call below; (S3) whether the row-height and opened-plot
// height caches are reset.
export function render(isNewDataset) {
  if (!BOARD) return;
  syncSortAriaState();
  const boardNotices = document.getElementById('board-notices');
  if (boardNotices) boardNotices.innerHTML = renderNotices(BOARD.notices);


  // Template factored into renderTotalsStrip() (item 9 of the worker-table
  // rebuild) so refreshBoardTotals() -- called after a confirmed stake/unstake
  // changes a plot -- can repaint this strip from a fresh BOARD.totals without
  // duplicating the template. See that function's own comment for why totals
  // are re-fetched rather than summed client-side.
  renderTotalsStrip();

  // CURRENT_ROWS is recomputed HERE (filter+sort over the full dataset, once)
  // renderTable() below mounts it -- see the big comment above CURRENT_ROWS'S OWN
  // declaration for the background-progressive-fill design (batch 19, item 13).
  CURRENT_ROWS = sortRows(BOARD.plots.filter(matchesFilters));
  // `.table-scroll` no longer scrolls vertically (board-polish batch 19, item 16)
  // the PAGE does, so "start at the top" on a fresh board load/player switch
  // means scrolling the window, not the (now content-height) table-scroll div.
  // v0.4.13 filter-reflow fix (S1): this must NOT fire on a plain filter/sort
  // render -- the owner: "when using any filter, the page bounces". Gated on
  // isNewDataset so it only runs for loadBoard()'s own render(true) call (board
  // load, player switch, refresh, full capture), never for the filter/sort
  // listeners wired in init(), which all call render() with no argument.
  if (isNewDataset) window.scrollTo(0, 0);
  // one measured line per dataset render so a rig's console capture shows what the
  // window costs (rows in CURRENT_ROWS, rows actually mounted, windowed or eager).
  const hasPerf = typeof performance !== 'undefined' && typeof performance.mark === 'function';
  if (hasPerf) performance.mark('ceres-board-render-start');
  const renderStart = hasPerf ? performance.now() : Date.now();
  renderTable(isNewDataset);
  if (hasPerf) {
    performance.mark('ceres-board-render-end');
    try { performance.measure('ceres-board-render', 'ceres-board-render-start', 'ceres-board-render-end'); } catch (_) { /* older engines */ }
  }
  if (isNewDataset) {
    const elapsed = (hasPerf ? performance.now() : Date.now()) - renderStart;
    const windowed = CURRENT_ROWS.length > VIRTUALIZE_THRESHOLD;
    console.info(`ceres: board render ${CURRENT_ROWS.length} rows in ${elapsed.toFixed(1)} ms (windowed: ${windowed ? 'yes' : 'no'}, mounted: ${MOUNTED_NODES.size})`);
  }

  // Idle cards ( board-fix batch, item 3 follow-on): GET /api/board no longer
  // embeds the idle_cards array at all -- measured live against player0005's
  // fully-captured account, 2026-08-21: it was 89% of the ENTIRE board payload
  // (3.04 MB of 3.4 MB), bigger by far than every plot row's own `workers` field
  // combined. The "Idle cards"/"Unassigned bonus assets" sections that used to
  // render IDLE_CARDS_CACHE
  // are gone, but every combine door still reads the cache directly, so it is
  // reset and refetched here exactly as before.
  S.IDLE_CARDS_CACHE = null;
  prefetchIdleCardsCache();
  // Review fix: no second `ceres:table-layout` here -- renderVirtualWindow(true) already
  // notifies with reason 'layout' on every render, and app.js recentres the picker on that.
}

function mountRowAt(tbody, index, beforeNode) {
  const row = CURRENT_ROWS[index];
  try {
    const [tr, expand] = renderRow(row, index);
    tbody.insertBefore(tr, beforeNode);
    tbody.insertBefore(expand, beforeNode);
    MOUNTED_NODES.set(index, [tr, expand]);
    ROW_RESIZE_OBSERVER?.observe(tr);
    attachMountedPlot(row, expand);
    return tr;
  } catch (e) {
    const errTr = document.createElement('tr');
    errTr.className = 'render-error-row';
    const errTd = document.createElement('td');
    errTd.colSpan = PLOT_TABLE_COLUMN_COUNT;
    errTd.textContent = `Render error for plot ${row && row.plot}: ${e.message}`;
    errTr.appendChild(errTd);
    tbody.insertBefore(errTr, beforeNode);
    MOUNTED_NODES.set(index, [errTr, null]);
    return errTr;
  }
}

function unmountRowAt(tbody, index) {
  const nodes = MOUNTED_NODES.get(index);
  if (!nodes) return;
  ROW_RESIZE_OBSERVER?.unobserve(nodes[0]);
  unmountPlotPresentation(nodes[0]?.dataset.deedUid);
  nodes.forEach((node) => node && node.parentNode === tbody && tbody.removeChild(node));
  MOUNTED_NODES.delete(index);
}

function expandedExtraBefore(index) {
  let extra = 0;
  for (const [uid, height] of EXPANDED_HEIGHTS) {
    const rowIndex = CURRENT_INDEX_BY_UID.get(uid);
    if (EXPANDED_ROWS.has(uid) && rowIndex !== undefined && rowIndex < index) extra += height;
  }
  return extra;
}

function rebuildRowOffsets() {
  ROW_OFFSETS = [0];
  for (const row of CURRENT_ROWS) {
    ROW_OFFSETS.push(ROW_OFFSETS[ROW_OFFSETS.length - 1]
      + (MEASURED_ROW_HEIGHTS.get(row.deed_uid) || ROW_HEIGHT || ROW_HEIGHT_FALLBACK));
  }
}

function measureMountedRowHeights() {
  const table = document.getElementById('plot-table');
  const width = table?.getBoundingClientRect().width || 0; // V
  const font = table ? getComputedStyle(table).fontSize : '';
  let changed = width !== ROW_MEASUREMENT_WIDTH || font !== ROW_MEASUREMENT_FONT;
  if (changed) {
    MEASURED_ROW_HEIGHTS.clear();
    ROW_HEIGHT = null;
    ROW_MEASUREMENT_WIDTH = width;
    ROW_MEASUREMENT_FONT = font;
  }
  for (const [index, nodes] of MOUNTED_NODES) {
    const uid = CURRENT_ROWS[index]?.deed_uid;
    const height = nodes[0]?.getBoundingClientRect().height || 0; // V
    if (!uid || height <= 0) continue;
    if (ROW_HEIGHT === null) ROW_HEIGHT = height;
    if (Math.abs((MEASURED_ROW_HEIGHTS.get(uid) || 0) - height) > 0.25) {
      MEASURED_ROW_HEIGHTS.set(uid, height);
      changed = true;
    }
  }
  if (changed) rebuildRowOffsets();
  return changed;
}

function rowOffset(index) {
  return (ROW_OFFSETS[index] ?? index * (ROW_HEIGHT || ROW_HEIGHT_FALLBACK)) + expandedExtraBefore(index);
}

function totalTableHeight() {
  return rowOffset(CURRENT_ROWS.length);
}

function indexAtOffset(offset) {
  const target = Math.max(0, offset);
  let low = 0;
  let high = CURRENT_ROWS.length;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (rowOffset(mid + 1) <= target) low = mid + 1;
    else high = mid;
  }
  return Math.min(CURRENT_ROWS.length, low);
}

function virtualSpacerRow(height) {
  const tr = document.createElement('tr');
  tr.className = 'virtual-spacer';
  tr.setAttribute('aria-hidden', 'true');
  const td = document.createElement('td');
  td.colSpan = PLOT_TABLE_COLUMN_COUNT;
  td.style.height = `${Math.max(0, height)}px`;
  td.style.padding = '0';
  td.style.border = 'none';
  tr.appendChild(td);
  return tr;
}

function updateVirtualSpacers() {
  const tbody = document.getElementById('plot-tbody');
  if (!tbody || MOUNTED_START < 0) return;
  const frontHeight = rowOffset(MOUNTED_START);
  const backHeight = totalTableHeight() - rowOffset(MOUNTED_END);
  if (MOUNTED_START > 0) {
    if (!FRONT_SPACER) {
      FRONT_SPACER = virtualSpacerRow(frontHeight);
      const first = MOUNTED_NODES.get(MOUNTED_START)?.[0] || tbody.firstChild;
      tbody.insertBefore(FRONT_SPACER, first);
    } else FRONT_SPACER.firstElementChild.style.height = `${frontHeight}px`;
  } else if (FRONT_SPACER) {
    tbody.removeChild(FRONT_SPACER);
    FRONT_SPACER = null;
  }
  if (MOUNTED_END < CURRENT_ROWS.length) {
    if (!BACK_SPACER) {
      BACK_SPACER = virtualSpacerRow(backHeight);
      tbody.appendChild(BACK_SPACER);
    } else BACK_SPACER.firstElementChild.style.height = `${Math.max(0, backHeight)}px`;
  } else if (BACK_SPACER) {
    tbody.removeChild(BACK_SPACER);
    BACK_SPACER = null;
  }
}

function tableWindow() {
  const table = document.getElementById('plot-table');
  const total = CURRENT_ROWS.length;
  if (!table || !total) return [0, 0];
  if (total <= VIRTUALIZE_THRESHOLD) return [0, total];
  const tableTop = table.getBoundingClientRect().top; // V
  const viewportStart = -tableTop; // V
  const viewportEnd = (window.innerHeight || 600) - tableTop; // V
  const rawStart = Math.max(0, indexAtOffset(viewportStart) - VIRTUAL_BUFFER_ROWS);
  const rawEnd = Math.min(total, indexAtOffset(viewportEnd) + VIRTUAL_BUFFER_ROWS + 1);
  // Quantize to VIRTUAL_WINDOW_BLOCK-row boundaries -- start rounds DOWN, end rounds
  // UP -- so the mounted window only changes once every N rows scrolled instead of
  // on every frame. Without this, renderVirtualWindow() below re-fires its
  // mount/unmount + repaint path on nearly every scroll frame (see VIRTUAL_WINDOW_BLOCK
  // comment for the measured cost).
  const start = Math.max(0, Math.floor(rawStart / VIRTUAL_WINDOW_BLOCK) * VIRTUAL_WINDOW_BLOCK);
  const end = Math.min(total, Math.ceil(rawEnd / VIRTUAL_WINDOW_BLOCK) * VIRTUAL_WINDOW_BLOCK);
  return [start, Math.max(start, end)];
}

let viewportEpoch = 0;
let viewportLease = null;
export function plotViewportLeaseIsCurrent(lease) { return viewportLease === lease && lease?.viewportEpoch === viewportEpoch; }
export function cancelPlotViewportLease(lease = null) {
  if (lease && lease !== viewportLease) return;
  const previous = viewportLease;
  viewportLease = null;
  viewportEpoch++;
  previous?.cancel?.();
}
export function acquirePlotViewportLease(owner, cancel) {
  cancelPlotViewportLease();
  viewportLease = { ...owner, viewportEpoch, cancel };
  return viewportLease;
}
export function releasePlotViewportLease(lease) {
  if (!plotViewportLeaseIsCurrent(lease)) return;
  viewportLease = null;
  viewportEpoch++;
}
function disposeMountedPlots() {
  ROW_RESIZE_OBSERVER?.disconnect();
  for (const nodes of MOUNTED_NODES.values()) {
    const uid = nodes[0]?.dataset.deedUid;
    if (uid) {
      const row = BOARD?.plots.find((plot) => plot.deed_uid === uid);
      handoffPlotContent(row || { deed_uid: uid }, nodes[1]?.querySelector('.editor-band'), null);
      unmountPlotPresentation(uid, { parked: true });
    }
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('wheel', () => cancelPlotViewportLease(), { passive: true });
  window.addEventListener('touchstart', () => cancelPlotViewportLease(), { passive: true });
  window.addEventListener('resize', () => cancelPlotViewportLease());
  document.addEventListener('keydown', (event) => {
    if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) cancelPlotViewportLease();
  }, true);
  document.addEventListener('pointerdown', (event) => {
    if (event.target === document.documentElement || event.clientX >= document.documentElement.clientWidth || event.target.closest('.slot-editor, .candidate-picker')) cancelPlotViewportLease(); // V
  }, true);
}

export function captureTableAnchor() {
  for (const [index, nodes] of MOUNTED_NODES) {
    const row = nodes[0];
    if (!row || !row.classList.contains('plot-row')) continue;
    const rect = row.getBoundingClientRect(); // V
    if (rect.bottom >= 0) return { uid: CURRENT_ROWS[index]?.deed_uid, top: rect.top, viewportEpoch };
  }
  return null;
}

function restoreTableAnchor(anchor) {
  if (!anchor?.uid || viewportLease || anchor.viewportEpoch !== viewportEpoch) return;
  const row = document.querySelector(`tr.plot-row[data-deed-uid="${CSS.escape(anchor.uid)}"]`);
  if (!row) return;
  const delta = row.getBoundingClientRect().top - anchor.top; // V
  if (Math.abs(delta) > 0.5) window.scrollBy(0, delta);
}

// (maintainer ruling "Top under the header, everywhere"): ONE rule for every scroll that acts on a plot -- its
// row sits directly under the sticky header -- and ONE function. A user's click animates it (300 ms ease-out under
// the viewport lease, so any wheel, key or touch cancels it); a programmatic follow-up (staging, a jump) is instant.
// Callers: the plot open (eos.js centreReservedPlot), the picker (picker-actions.js), scrollPlotAfterSlotAdd and
// revealPlotRow below.
export function plotHeaderLine() {
  return Math.max(0, ...['header.board-header', '#table-head-sticky'].map((selector) => document.querySelector(selector)?.getBoundingClientRect().bottom || 0)); // V
}
export function plotRowScrollGoal(deedUid) {
  const row = document.querySelector(`tr.plot-row[data-deed-uid="${CSS.escape(deedUid)}"]`);
  const index = CURRENT_INDEX_BY_UID.get(deedUid);
  const table = document.getElementById('plot-table');
  const top = row ? row.getBoundingClientRect().top : index !== undefined && table ? table.getBoundingClientRect().top + rowOffset(index) : null; // V
  return top === null ? null : Math.max(0, window.scrollY + top - plotHeaderLine()); // V
}
export function scrollPlotUnderHeader(deedUid, { animate = false, owner = null, onDone = null } = {}) {
  const done = () => { onDone?.(); };
  const goal = () => plotRowScrollGoal(deedUid);
  const first = goal();
  if (first === null) { done(); return; }
  const reduced = !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if (!animate || reduced || Math.abs(first - window.scrollY) < 2) { // V
    cancelPlotViewportLease();
    if (Math.abs(first - window.scrollY) > 0.5) window.scrollTo({ top: first, behavior: 'instant' }); // V
    done();
    return;
  }
  const from = window.scrollY; // V
  const lease = acquirePlotViewportLease(owner || { deedUid }, done); // cancelled by the user's own input -> done
  const start = performance.now();
  const step = (now) => {
    if (!plotViewportLeaseIsCurrent(lease)) return;
    const t = Math.min(1, (now - start) / 300);
    const eased = 1 - Math.pow(1 - t, 3);
    const target = goal(); // re-read each frame: the plot is still growing while it opens
    if (target !== null) window.scrollTo({ top: from + (target - from) * eased, behavior: 'instant' });
    if (t < 1) requestAnimationFrame(step);
    else { releasePlotViewportLease(lease); done(); }
  };
  requestAnimationFrame(step);
}

export function scrollPlotAfterSlotAdd(deedUid) {
  cancelPlotViewportLease();
  const epoch = viewportEpoch;
  requestAnimationFrame(() => {
    if (epoch !== viewportEpoch || viewportLease) return;
    scrollPlotUnderHeader(deedUid);
  });
}

// bring one plot's row under the header and mount its window block (never the whole table).
// False when the board's filters hide the plot.
export function revealPlotRow(deedUid) {
  if (CURRENT_INDEX_BY_UID.get(deedUid) === undefined || !document.getElementById('plot-table')) return false;
  scrollPlotUnderHeader(deedUid);
  if (CURRENT_ROWS.length > VIRTUALIZE_THRESHOLD) { renderVirtualWindow(false, null); scrollPlotUnderHeader(deedUid); }
  return true;
}

// the one door for code that needs a plot's `tr.plot-row` element. A row inside the mounted window is
// returned as it is; a row outside it (the table is windowed above VIRTUALIZE_THRESHOLD) is brought under the
// header and its window block mounted synchronously, so the element exists before this returns. Null when the
// filters hide the plot -- the caller then mutates the data only, and the window draws it when it gets there.
export function mountedPlotRow(deedUid) {
  const index = CURRENT_INDEX_BY_UID.get(deedUid);
  return index === undefined ? null : (MOUNTED_NODES.get(index)?.[0] || null);
}
export function ensureRowMounted(deedUid) {
  const mounted = mountedPlotRow(deedUid);
  if (mounted) return mounted;
  if (!revealPlotRow(deedUid)) return null;
  return mountedPlotRow(deedUid);
}

// updateMountedRow() rewrites an already-mounted row's <tr>/expand-row content
// in place (e.g. toggling an expand row open/closed, or a loaded-detail
// refresh) -- that can change the expand row's height, which the virtual
// spacers must account for immediately, not just on the next scroll tick.
// Mirrors the height-correction + spacer + anchor-restore sequence
// renderVirtualWindow() already runs after a real window change.
export function refreshVirtualLayout(deedUid, anchor) {
  const index = CURRENT_INDEX_BY_UID.get(deedUid);
  if (index !== undefined) {
    const nodes = MOUNTED_NODES.get(index);
    const expand = nodes && nodes[1];
    if (expand && EXPANDED_ROWS.has(deedUid) && expand.style.display !== 'none') {
      const height = expand.getBoundingClientRect().height; // V
      if (height > 0) EXPANDED_HEIGHTS.set(deedUid, height);
    } else {
      EXPANDED_HEIGHTS.delete(deedUid);
    }
  }
  updateVirtualSpacers();
  restoreTableAnchor(anchor);
  notifyTableLayout();
}

function mountedExpandedHeightChanges() {
  let changed = false;
  for (const [index, nodes] of MOUNTED_NODES) {
    const uid = CURRENT_ROWS[index]?.deed_uid;
    const expand = nodes[1];
    if (!uid || !expand || !EXPANDED_ROWS.has(uid) || expand.style.display === 'none') continue;
    const height = expand.getBoundingClientRect().height; // V
    if (height > 0 && Math.abs((EXPANDED_HEIGHTS.get(uid) || 0) - height) > 0.5) {
      EXPANDED_HEIGHTS.set(uid, height);
      changed = true;
    }
  }
  return changed;
}

export function onTableScroll() {
  if (isMobileBoardTier()) return;
  // Below VIRTUALIZE_THRESHOLD, tableWindow() always returns [0, total]
  // there is never a window change to react to. Bailing here (measured
  // regression, player0005 59.5fps -> 15.0fps) skips captureTableAnchor()'s
  // per-node getBoundingClientRect() scan, which otherwise runs on every
  // scroll frame for a plot count that will never actually re-window.
  if (CURRENT_ROWS.length <= VIRTUALIZE_THRESHOLD) return;
  if (SCROLL_RAF !== null) return;
  const queuedEpoch = viewportEpoch;
  SCROLL_RAF = requestAnimationFrame(() => {
    SCROLL_RAF = null;
    renderVirtualWindow(false, queuedEpoch === viewportEpoch ? captureTableAnchor() : null);
    notifyTableLayout('scroll');
  });
}

function renderVirtualWindow(force = false, anchor = null) {
  if (isMobileBoardTier()) return false;
  const tbody = document.getElementById('plot-tbody');
  if (!tbody) return false;
  const [start, end] = tableWindow();
  if (!force && start === MOUNTED_START && end === MOUNTED_END) return false;
  const total = CURRENT_ROWS.length;
  if (!total) {
    disposeMountedPlots();
    tbody.replaceChildren();
    MOUNTED_START = 0;
    MOUNTED_END = 0;
    MOUNTED_NODES.clear();
    FRONT_SPACER = null;
    BACK_SPACER = null;
    notifyTableLayout();
    return true;
  }

  const overlaps = MOUNTED_START >= 0 && start < MOUNTED_END && end > MOUNTED_START;
  if (!overlaps) {
    disposeMountedPlots();
    tbody.replaceChildren();
    MOUNTED_NODES.clear();
    FRONT_SPACER = start > 0 ? virtualSpacerRow(rowOffset(start)) : null;
    if (FRONT_SPACER) tbody.appendChild(FRONT_SPACER);
    for (let index = start; index < end; index++) mountRowAt(tbody, index, null);
    BACK_SPACER = end < total ? virtualSpacerRow(totalTableHeight() - rowOffset(end)) : null;
    if (BACK_SPACER) tbody.appendChild(BACK_SPACER);
  } else {
    for (let index = MOUNTED_START; index < start; index++) unmountRowAt(tbody, index);
    for (let index = end; index < MOUNTED_END; index++) unmountRowAt(tbody, index);
    if (start < MOUNTED_START) {
      const anchorNode = MOUNTED_NODES.get(MOUNTED_START)?.[0] || FRONT_SPACER?.nextSibling || tbody.firstChild;
      for (let index = start; index < MOUNTED_START; index++) mountRowAt(tbody, index, anchorNode);
    }
    if (end > MOUNTED_END) {
      for (let index = MOUNTED_END; index < end; index++) mountRowAt(tbody, index, BACK_SPACER);
    }
  }
  MOUNTED_START = start;
  MOUNTED_END = end;
  const rowsChanged = measureMountedRowHeights();
  const expandedChanged = mountedExpandedHeightChanges();
  updateVirtualSpacers();
  if (rowsChanged || expandedChanged) renderVirtualWindow(false, anchor);
  restoreTableAnchor(anchor);
  notifyTableLayout();
  return true;
}

// requestIdleCallback/requestAnimationFrame scheduling primitive (board-polish
// batch 19, item 15) -- extracted out of renderTable()/scheduleBackgroundFill()
// so any progressive background fill can reuse the exact same
// RIC-with-RAF-fallback mechanics instead of a second copy. What's NOT
// shared: the per-row mount logic and the FILL_INDEX/FILL_TOKEN bookkeeping
// stay separate per table (mountRangeInto() mounts a <tr> PLUS its
// expand-row sibling with a click handler)
// only the scheduling wrapper generalises cleanly.
function scheduleIdle(runChunk) {
  if (typeof requestIdleCallback === 'function') return requestIdleCallback(runChunk, { timeout: 200 });
  return requestAnimationFrame(() => runChunk(null));
}
function cancelIdle(handle) {
  if (handle === null) return;
  if (typeof cancelIdleCallback === 'function') cancelIdleCallback(handle);
  else cancelAnimationFrame(handle);
}

function cancelMobileCardRender() {
  MOBILE_CARD_RENDER_TOKEN += 1;
  cancelIdle(MOBILE_CARD_RENDER_HANDLE);
  MOBILE_CARD_RENDER_HANDLE = null;
}

// Cards consume CURRENT_ROWS directly, after render() has applied the board's one
// filter/sort pipeline. Chunking changes only when DOM nodes arrive; it never changes
// their source order or asks for a second dataset.
export function renderMobileCards() {
  const host = document.getElementById('plot-card-list');
  if (!host) return;
  cancelMobileCardRender();
  const token = MOBILE_CARD_RENDER_TOKEN;
  host.replaceChildren();
  if (!CURRENT_ROWS.length) {
    host.innerHTML = '<p class="plot-card-empty">No plots match these filters.</p>';
    host.removeAttribute('aria-busy');
    return;
  }
  host.setAttribute('aria-busy', 'true');
  let index = 0;
  const appendChunk = () => {
    if (token !== MOBILE_CARD_RENDER_TOKEN || !isMobileBoardTier()) return;
    const end = Math.min(index + MOBILE_CARD_CHUNK_SIZE, CURRENT_ROWS.length);
    let html = '';
    const count = end - index;
    for (; index < end; index += 1) html += plotCardHtml(CURRENT_ROWS[index], index);
    host.insertAdjacentHTML('beforeend', `<div class="plot-card-group" style="--card-group-rows: ${count}; --card-group-tablet-rows: ${Math.ceil(count / 2)}">${html}</div>`);
    if (index < CURRENT_ROWS.length) MOBILE_CARD_RENDER_HANDLE = scheduleIdle(appendChunk);
    else {
      MOBILE_CARD_RENDER_HANDLE = null;
      host.removeAttribute('aria-busy');
    }
  };
  appendChunk();
}

// Background-progressive render of #plot-tbody (board-polish batch 19, item 13
// see the big comment above CURRENT_ROWS for the full design and why this
// replaces the prior virtualized/windowed table). Mounts the first screenful
// synchronously, then -- only if more rows remain -- background-fills the rest in
// idle-time chunks that never unmount anything and never react to scroll.
function renderTable(isNewDataset) {
  cancelPlotViewportLease();
  disposeMountedPlots();
  if (isNewDataset) {
    ROW_HEIGHT = null;
    MEASURED_ROW_HEIGHTS.clear();
    EXPANDED_HEIGHTS.clear();
  }
  CURRENT_INDEX_BY_UID = new Map(CURRENT_ROWS.map((row, index) => [row.deed_uid, index]));
  const listedTotal = document.querySelector('#table-head-sticky .count-total');   // the counting column's header shows how many plots are listed
  if (listedTotal) listedTotal.textContent = String(CURRENT_ROWS.length);
  rebuildRowOffsets();
  MOUNTED_START = -1;
  MOUNTED_END = -1;
  MOUNTED_NODES.clear();
  FRONT_SPACER = null;
  BACK_SPACER = null;
  const tbody = document.getElementById('plot-tbody');
  LAST_BOARD_PRESENTATION_TIER = isMobileBoardTier() ? 'cards' : 'table';
  if (isMobileBoardTier()) {
    tbody?.replaceChildren();
    renderMobileCards();
    return;
  }
  if (!tbody) return;
  cancelMobileCardRender();
  const cardHost = document.getElementById('plot-card-list');
  cardHost?.replaceChildren();
  cardHost?.removeAttribute('aria-busy');
  renderVirtualWindow(true);
}
