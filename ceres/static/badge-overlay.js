// The badge rail and its individual slide-outs follow the mounted plot rows. Open state
// lives in memory only; newly mounted rows start closed and the flag derives its next
// action from the rows currently open.
import { BOARD, BOARD_GENERATION } from './board.js';
import { formatPlotLabel } from './format.js';
import { badgeCodesFor, badgeHtml, badgeRosterLoaded, badgeStatusGlyph, badgeStripHtml, badgeStripStatus, cooldownChangeEntries, unsignedChangeCount } from './badges.js';

let panel = null;
let bar = null;
let scroller = null;
let flag = null; // `#badge-flag` -- a standalone `position: fixed` button now, no longer nested inside `#badge-bar`
// (maintainer, verbatim: "flag button appears, then disappears behind the
// loadig animation, then reappears with the full board. Stop that flashing")
// app.js's own reveal gate (`maybeRevealEdgeHandles()`) needs to know the
// bar's ROWS (not just its own top/height, which app.js computes itself) have
// been laid out at least once before it is safe to show the bar/flag -- this
// module owns that fact, so it dispatches it once, the same DOM-event
// hand-off pattern board.js's own `ceres:board-revealed` already uses.
let barRowsSyncedOnce = false;

// (maintainer): the flag opens an Iris/wallet-style tray of status icons. Picking one keeps only that status's
// slide-outs out and locks its icon on the button in place of the flag; the flag then joins the tray and shows all.
const STATUS_FILTERS = [
  ['clean', 'Show plots without issues'], ['warning', 'Show plots with warnings'],
  ['critical', 'Show critical plots'], ['pending', 'Show plots with pending changes'],
];
let tray = null;
let statusFilter = null;
let flagIconHtml = '';

const openRows = new Set();
let focusReturnTarget = null;
const pendingTransitions = new Set();
const transitionTimers = new Map();
let reducedMotion = false;

function isRowOpen(deedUid) {
  return openRows.has(deedUid);
}

export function nextGroupState(mountedUids, openRowsIn) {
  const next = new Set(openRowsIn);
  const targetOpen = !mountedUids.length || !mountedUids.every((uid) => next.has(uid));
  mountedUids.forEach((uid) => targetOpen ? next.add(uid) : next.delete(uid));
  return next;
}

function mountedDeedUids() {
  const tbody = document.getElementById('plot-tbody');
  if (!tbody) return [];
  return Array.from(tbody.querySelectorAll('tr.plot-row[data-deed-uid]'), (tr) => tr.dataset.deedUid).filter(Boolean);
}

function rowControl(deedUid) {
  return bar?.querySelector(`.badge-bar-row[data-deed-uid="${CSS.escape(deedUid)}"]`);
}

function updateRowExpanded(deedUid) {
  const control = rowControl(deedUid);
  if (!control) return;
  const open = openRows.has(deedUid);
  control.setAttribute('aria-expanded', open ? 'true' : 'false');
  control.setAttribute('aria-label', `${open ? 'Hide' : 'Show'} badges for ${plotAriaName(deedUid)}. ${control.dataset.statusLabel || 'Badge status unavailable'}`);
}

function updateGroupExpanded() {
  const mounted = mountedDeedUids();
  const allOpen = mounted.length > 0 && mounted.every((uid) => openRows.has(uid));
  bar?.setAttribute('aria-expanded', allOpen ? 'true' : 'false');
  bar?.classList.toggle('is-group-open', allOpen);  // the open slide-out keeps the column glowing
  flag?.setAttribute('aria-expanded', allOpen ? 'true' : 'false');
  if (flag) {
    const label = openRows.size ? 'Close all badge slide-outs' : 'Show badges by status';  // what a click on the flag does
    flag.setAttribute('aria-label', label);
    flag.setAttribute('data-tip', label);
    flag.dataset.state = allOpen ? 'used' : 'unused';
  }
}

function settleTransition(deedUid) {
  pendingTransitions.delete(deedUid);
  clearTimeout(transitionTimers.get(deedUid));
  transitionTimers.delete(deedUid);
  updateRowExpanded(deedUid);
  updateGroupExpanded();
  if (!pendingTransitions.size && openRows.size === 0 && focusReturnTarget?.isConnected) {
    focusReturnTarget.focus({ preventScroll: true });
    focusReturnTarget = null;
  }
}

function markTransitionPending(deedUid) {
  if (reducedMotion) {
    updateRowExpanded(deedUid);
    return;
  }
  pendingTransitions.add(deedUid);
  clearTimeout(transitionTimers.get(deedUid));
  transitionTimers.set(deedUid, setTimeout(() => settleTransition(deedUid), 550));
}

function setRowOpen(deedUid, open) {
  if (open) openRows.add(deedUid); else openRows.delete(deedUid);
  syncBadgeOverlay();
}

function rowKind(deedUid) {
  const row = rowForDeedUid(deedUid);
  return row ? badgeStripStatus(row).kind : null;
}

function renderTray() {
  if (!tray) return;
  const option = (key, label, icon) => `<button type="button" class="badge-filter-opt" role="menuitem" data-filter="${key}" aria-label="${label}" data-tip="${label}">${icon}</button>`;
  const options = STATUS_FILTERS.map(([key, label]) => option(key, label, badgeStatusGlyph(key)).replace('class="badge-filter-opt"', key === statusFilter ? 'class="badge-filter-opt is-active" aria-current="true"' : 'class="badge-filter-opt"'));
  if (statusFilter) options.push(option('all', 'Show badges for all visible plots', flagIconHtml));
  tray.innerHTML = options.join('');
}

function setTrayOpen(open) {
  if (!tray) return;
  if (open) renderTray();
  tray.dataset.open = open ? 'true' : 'false';
  flag?.classList.toggle('is-tray-open', open);
}

function applyStatusFilter(key) {
  statusFilter = key === 'all' ? null : key;
  if (flag) {
    flag.innerHTML = statusFilter ? badgeStatusGlyph(statusFilter) : flagIconHtml;
    flag.dataset.filter = statusFilter || '';
  }
  openRows.clear();
  mountedDeedUids().forEach((uid) => { if (!statusFilter || rowKind(uid) === statusFilter) openRows.add(uid); });
  setTrayOpen(false);
  syncBadgeOverlay();
}

function toggleGroup() {
  const next = nextGroupState(mountedDeedUids(), openRows);
  openRows.clear();
  next.forEach((uid) => openRows.add(uid));
  syncBadgeOverlay();
}

function closeAll(returnFocus = true) {
  if (returnFocus && openRows.size) focusReturnTarget ||= flag;
  setTrayOpen(false);  // closing keeps the chosen status on the button (like Iris keeps its theme); the flag option resets it
  openRows.clear();
  syncBadgeOverlay();
  if (!pendingTransitions.size && returnFocus && focusReturnTarget?.isConnected) {
    focusReturnTarget.focus({ preventScroll: true });
    focusReturnTarget = null;
  }
}

export function initBadgeOverlay() {
  bar = document.getElementById('badge-bar');
  scroller = document.getElementById('plot-table-scroll');
  flag = document.getElementById('badge-flag'); // separate element now, positioned by app.js's updateBadgeFlagPosition()
  tray = document.getElementById('badge-filter-tray');
  flagIconHtml = flag ? flag.innerHTML : '';
  if (!bar || !scroller) return;
  panel = document.createElement('aside');
  panel.id = 'badge-overlay';
  panel.className = 'badge-overlay';
  panel.hidden = false;
  panel.setAttribute('aria-label', 'Badges');
  scroller.appendChild(panel);
  reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  bar.setAttribute('role', 'group');
  bar.removeAttribute('tabindex');
  bar.removeAttribute('aria-expanded');
  bar.removeAttribute('aria-controls');
  bar.setAttribute('aria-label', 'Plot badge controls');
  if (flag) {
    flag.setAttribute('aria-expanded', 'false');
    flag.setAttribute('aria-controls', 'badge-overlay');
    flag.dataset.state = 'unused';
    flag.setAttribute('aria-label', 'Show badges for all visible plots');
    flag.setAttribute('data-tip', 'Show badges for all visible plots');
  }
  bar.addEventListener('click', (event) => {
    const barRow = event.target.closest('.badge-bar-row');
    if (barRow && barRow.dataset.deedUid) {
      focusReturnTarget = barRow;
      setRowOpen(barRow.dataset.deedUid, !openRows.has(barRow.dataset.deedUid));
    }
  });
  bar.addEventListener('keydown', (event) => {
    if (!['Enter', ' ', 'Spacebar'].includes(event.key)) return;
    const barRow = event.target.closest('.badge-bar-row');
    if (!barRow?.dataset.deedUid) return;
    event.preventDefault();
    focusReturnTarget = barRow;
    setRowOpen(barRow.dataset.deedUid, !openRows.has(barRow.dataset.deedUid));
  });
  if (flag) {
    flag.setAttribute('aria-haspopup', 'menu');
    flag.addEventListener('click', () => {
      focusReturnTarget = flag;
      // desktop opens the status tray; where the tray is not shown (phone), the flag keeps its one-click toggle.
      // with slide-outs open, one click on the flag closes them all
      // (and resets the filter); with none open it opens the status tray.
      if (tray && getComputedStyle(tray).display !== 'none') {
        if (openRows.size && tray.dataset.open !== 'true') closeAll(false);
        else setTrayOpen(tray.dataset.open !== 'true');
      } else toggleGroup();
    });
    tray?.addEventListener('click', (event) => {
      const option = event.target.closest('.badge-filter-opt');
      if (option) applyStatusFilter(option.dataset.filter);
    });
    // (restores ): hovering or focusing the flag gives the column the same glow a hover does
    // (ceres.css `.badge-bar.is-flag-hover`). A class on the bar, not a `:has()` selector, so only the bar restyles.
    const setFlagHover = (on) => { if (bar) bar.classList.toggle('is-flag-hover', on); };
    flag.addEventListener('pointerenter', () => setFlagHover(true));
    flag.addEventListener('pointerleave', () => setFlagHover(false));
    flag.addEventListener('focus', () => setFlagHover(flag.matches(':focus-visible')));
    flag.addEventListener('blur', () => setFlagHover(false));
  }
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && tray?.dataset.open === 'true') { event.preventDefault(); setTrayOpen(false); flag?.focus({ preventScroll: true }); return; }
    if (event.key !== 'Escape' || openRows.size === 0) return;
    event.preventDefault();
    closeAll(true);
  });
  // (maintainer: "plot badges that are slid-out should stay out until the flag slider button or the plots badge slider
  // button is clicked again"): a click elsewhere on the page closes only the status tray (a menu), never the slide-outs.
  document.addEventListener('pointerdown', (event) => {
    if (tray?.dataset.open === 'true' && !tray.contains(event.target) && !flag?.contains(event.target)) setTrayOpen(false);
  });
  panel.addEventListener('transitionend', (event) => {
    if (event.propertyName !== 'transform') return;
    const overlayRow = event.target.closest('.badge-overlay-row[data-deed-uid]');
    if (!overlayRow) return;
    const targetX = openRows.has(overlayRow.dataset.deedUid) ? 0 : overlayRow.getBoundingClientRect().width;
    const currentX = new DOMMatrixReadOnly(getComputedStyle(overlayRow).transform).m41;
    if (Math.abs(targetX - currentX) <= 1) settleTransition(overlayRow.dataset.deedUid);
  });
  // table.js dispatches this after any change to which rows are mounted or how tall
  // they are (renderVirtualWindow/measureMountedRowHeights/onTableScroll) -- neither
  // the bar nor the overlay ever re-measures the table itself, they only read the
  // already-rendered mounted <tr> rects each time that happens. `event.detail.reason`
  // ('scroll' | 'layout', table.js::notifyTableLayout()) lets syncBadgeOverlay() skip
  // the whole pass on a scroll frame that changed nothing -- see its own comment.
  window.addEventListener('ceres:table-layout', (event) => syncBadgeOverlay(event?.detail?.reason));
  window.addEventListener('resize', () => syncBadgeOverlay());
  // once a minute, and only while slide-outs are open and the page is visible, refresh their countdowns.
  // Rows whose signature did not change are skipped, so this costs nothing when no countdown is shown.
  setInterval(() => { if (openRows.size && document.visibilityState === 'visible') syncBadgeOverlay(); }, 60000);
  syncBadgeOverlay();
}

// labels name the plot by its label, never the deed UID.
function plotAriaName(deedUid) {
  const plot = rowForDeedUid(deedUid)?.plot;
  return plot ? `plot ${formatPlotLabel(plot)}` : 'this plot';
}

function rowForDeedUid(deedUid) {
  return BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
}

// The bar and slide-outs follow only the currently mounted rows. Reads are batched
// before writes so a layout pass never interleaves with rectangle measurement.
export function syncBadgeOverlay(reason) {
  const tbody = document.getElementById('plot-tbody');
  if (!tbody || !scroller || !bar) return;
  if (openRows.size === 0 && reason === 'scroll') return;

  // Read phase.
  const scrollerRect = scroller.getBoundingClientRect(); // V
  const mounted = [];
  tbody.querySelectorAll('tr.plot-row[data-deed-uid]').forEach((tr) => {
    const deedUid = tr.dataset.deedUid;
    if (!deedUid) return;
    // Read straight off the mounted <tr>'s own rendered box -- this is what stays
    // exactly aligned through virtualization remounts and expand/collapse height
    // changes without either the bar or the overlay duplicating table.js's own
    // row-offset bookkeeping. An expanded plot's own <tr> height is unaffected by its
    // sibling expand-row, so this is deliberately the plot row's height only, never
    // the opened detail's.
    const trRect = tr.getBoundingClientRect(); // V
    mounted.push({
      deedUid,
      top: `${trRect.top - scrollerRect.top + 1}px`,
      height: `${Math.max(0, trRect.height - 2)}px`,
      // Slide-outs keep the established 3px vertical inset within their plot row.
      overlayTop: `${trRect.top - scrollerRect.top + 3}px`,
      overlayHeight: `${Math.max(0, trRect.height - 6)}px`,
      row: rowForDeedUid(deedUid),
      tr,
    });
  });

  // Write phase.
  const mountedIds = new Set();
  const pendingOpen = [];
  for (const { deedUid, top, height, overlayTop, overlayHeight, row, tr } of mounted) {
    mountedIds.add(deedUid);

    // The bar row -- exactly what the old .badge-strip-cell <td> held.
    let barRow = bar.querySelector(`.badge-bar-row[data-deed-uid="${CSS.escape(deedUid)}"]`);
    const isFirstMountThisSession = !barRow; // V (bar rows are always created, unconditionally, the first time a plot mounts)
    if (isFirstMountThisSession) {
      barRow = document.createElement('div');
      barRow.className = 'badge-bar-row';
      barRow.dataset.deedUid = deedUid;
      barRow.setAttribute('role', 'button');
      barRow.setAttribute('tabindex', '0');
      barRow.setAttribute('aria-controls', `badge-overlay-row-${encodeURIComponent(deedUid)}`);
      barRow.setAttribute('aria-expanded', 'false');
      bar.appendChild(barRow);
    }
    if (barRow.style.top !== top) barRow.style.top = top;
    if (barRow.style.height !== height) barRow.style.height = height;
    // a row that scrolls into view follows the active status filter.
    if (isFirstMountThisSession && statusFilter && row && badgeStripStatus(row).kind === statusFilter) openRows.add(deedUid);
    const rowOpen = isRowOpen(deedUid);
    const barSig = !row ? 'missing' : [badgeRosterLoaded(row) ? 'loaded' : 'loading', badgeCodesFor(row).join(','), BOARD_GENERATION].join('|');
    if (barRow.dataset.sig !== barSig) {
      barRow.dataset.sig = barSig;
      barRow.innerHTML = `<span class="badge-bar-content">${row ? badgeStripHtml(row) : ''}</span>`;
      const label = badgeStripStatus(row || {}).label;
      barRow.dataset.statusLabel = label;
      barRow.dataset.pending = row && badgeStripStatus(row).kind === 'pending' ? 'true' : 'false';
      barRow.setAttribute('aria-label', `Show badges for ${plotAriaName(deedUid)}. ${label}`);
    }
    barRow.classList.toggle('is-open', rowOpen);
    // (maintainer): the row pulses gold exactly while the clock does. Decided here, on every sync, from the same status the
    // clock uses -- not once when the row was built, which missed a change staged afterwards.
    tr.classList.toggle('row-pending', barRow.dataset.pending === 'true');

    // each plot row is its own slide-out now -- only create/render one when
    // this row is actually open (or was open before and still needs its `is-open`
    // class/position kept current while its close transition plays; see the removal
    // pass below for when a row's element finally goes away). A row that was never
    // opened, and isn't opening now, gets no DOM element at all.
    const open = rowOpen;
    let overlayRow = panel.querySelector(`.badge-overlay-row[data-deed-uid="${CSS.escape(deedUid)}"]`);
    const overlayRowJustCreated = !overlayRow;
    if (overlayRowJustCreated) {
      overlayRow = document.createElement('div');
      overlayRow.className = 'badge-overlay-row';
      overlayRow.dataset.deedUid = deedUid;
      overlayRow.id = `badge-overlay-row-${encodeURIComponent(deedUid)}`;
      panel.appendChild(overlayRow);
    }
    if (overlayRow.style.top !== overlayTop) overlayRow.style.top = overlayTop;
    if (overlayRow.style.height !== overlayHeight) overlayRow.style.height = overlayHeight;
    // Pre-landing review fix: a freshly created element has no PRIOR rendered frame to
    // transition from -- adding `.is-open` in the same tick it's created/appended skips
    // straight to the open state instead of sliding. Forcing one synchronous layout
    // read here gives the browser a real "closed" frame to animate away from, the same
    // trick the old whole-panel `setOpen()` used (`void panel.offsetWidth`) before this
    // rewrite moved the open/close state onto each row.
    // (maintainer, verbatim: "It cannot be opened for 10 seconds, then it opens and
    // then it is closes and opens again as fast as I would expect it to without delay")
    // forcing that layout HERE, once per new row and interleaved with this loop's own
    // writes, cost one full-document layout per row -- measured 10.9 s of
    // syncBadgeOverlay self-time for 129 rows on the first flag click, which is why only
    // the FIRST open was slow. New rows are now queued and opened together after a single
    // forced layout below the loop; they still get their closed frame to slide from.
    const wasOpen = overlayRow.classList.contains('is-open');
    if (wasOpen !== open) {
      markTransitionPending(deedUid);
      if (overlayRowJustCreated && open) pendingOpen.push(overlayRow);
      else overlayRow.classList.toggle('is-open', open);
    }
    if (!open) continue; // stale content while closed is fine -- nothing shows it
    // Perf: only touch innerHTML when this row's badge content actually changed
    // syncBadgeOverlay() runs on every scroll frame/table-layout event, and re-parsing
    // the same HTML for every mounted row on each of those is wasted work. The
    // signature is cheap to compute (no DOM) and captures everything the branches
    // below render from: missing-row, loaded state, the exact ranked code list, the
    // maintainer's own unsigned/cooldown staged-plan counts (badgeHtml() renders both via
    // badgeChips()), the worksite build's ready/stalled state, and the region DEC
    // efficiency badge -- none of those flow through badgeCodesFor()'s code list, so
    // without them here the overlay row would keep stale text after any of them
    // changes. BOARD_GENERATION is included so every overlay row is forced to
    // re-render on a fresh board load (loadBoard() bumps it, quiet or not).
    const sig = !row ? 'missing' : [
      badgeRosterLoaded(row) ? 'loaded' : 'loading',
      badgeCodesFor(row).join(','),
      unsignedChangeCount(row.deed_uid),
      cooldownChangeEntries(row.deed_uid).length,
      row.pending_change?.ready_at ?? '',
      row.pending_change?.stalled ?? '',
      row.region_efficiency ?? '',
      BOARD_GENERATION,
      // rows showing a ready time or a waiting change re-render each minute, so "(in 3 hours)" never goes stale.
      (row.pending_change?.ready_at || cooldownChangeEntries(row.deed_uid).length) ? Math.floor(Date.now() / 60000) : '',
    ].join('|');
    if (overlayRow.dataset.sig !== sig) {
      overlayRow.dataset.sig = sig;
      if (!row) {
        overlayRow.textContent = 'Loading';
        overlayRow.classList.add('is-loading');
      } else {
        // The rail glyph and slide-out share badgeChips() through badgeHtml().
        const html = badgeHtml(row);
        if (html) {
          overlayRow.classList.remove('is-loading');
          overlayRow.innerHTML = html;
        } else if (!badgeRosterLoaded(row)) {
          overlayRow.classList.add('is-loading');
          overlayRow.textContent = 'Loading';
        } else {
          overlayRow.classList.remove('is-loading');
          overlayRow.innerHTML = '<span class="badge-overlay-none">No issues</span>';
        }
      }
    }
  }
  if (pendingOpen.length) {
    void panel.offsetWidth; // V: ONE layout
    for (const overlayRow of pendingOpen) overlayRow.classList.add('is-open');
  }
  bar.querySelectorAll('.badge-bar-row').forEach((barRow) => {
    const deedUid = barRow.dataset.deedUid;
    if (!mountedIds.has(deedUid)) {
      openRows.delete(deedUid);
      if (focusReturnTarget === barRow) focusReturnTarget = flag;
      settleTransition(deedUid);
      barRow.remove();
    }
  });
  // a row's own element is removed only once it is no longer mounted (as
  // before) -- NOT the moment it closes, so a mounted-but-closing row keeps its
  // element (and its CSS transition + delayed visibility) to actually slide out
  // instead of vanishing instantly. Each row is now `inline-size: max-content` on its
  // own (ceres.css), so there is no shared panel width left to measure (superseding
  // the old measurement pass, which existed only because the whole panel used
  // to share one width across every row).
  if (panel) panel.querySelectorAll('.badge-overlay-row').forEach((overlayRow) => {
    if (!mountedIds.has(overlayRow.dataset.deedUid)) overlayRow.remove();
  });

  updateGroupExpanded();
  if (!barRowsSyncedOnce) {
    barRowsSyncedOnce = true;
    window.dispatchEvent(new CustomEvent('ceres:badge-bar-synced'));
  }
}
