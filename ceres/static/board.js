import { adoptRowPresentations, invalidateRowPresentations, refreshBoardTotals } from './rows.js';
import { onBalancesChanged } from './balances-bus.js';
import { badgeFilterOptions, rowHasBadge } from './badges.js';
import { playerAvatarLayers } from './avatar.js';
import { WORKER_UPGRADE_OPTIONS } from './candidates.js';
import { renderCheckoutSurface } from './checkout.js';
import { initCustomSelect, selectValues, setSelectValues, syncCustomSelect } from './customSelect.js';
import { esc } from './format.js';
import { decorateResourceMentions, rarityOptionIconHtml, resourceIconHtml, terrainOptionIconHtml, territorySlug, typeIconOnlyHtml, splinterIconHtml } from './icons.js';
import { CERES_PREVIEWS, restoreInFlightRecords } from './operations.js';
import { EXPANDED_DATA_LOADED, EXPANDED_HEIGHTS, EXPANDED_ROWS, EXPAND_ERRORS, loadPlotDetail, syncCloseAll } from './rows.js';
import { setReady } from './ceres-ready.js';
import { CERES_STAGE, CERES_STAGED_WORKSITES } from './staging.js';
import { S } from './state.js';
import { render, revealPlotRow } from './table.js';
import { closePlanStore, restoreStagePlans } from './stage-plans.js';
import { plotLockSortState, reconcilePlotLockPlans } from './plot-lock.js';

// Current player is initialized during module evaluation.
// Ceres never sits on a URL without ?player=. The URL wins; without one, the last player
// this browser loaded (remembered below) is written into the URL before anything loads; only with
// no remembered player at all does the server's default decide, and syncPlayerInUrl() then writes it.
const PLAYER_MEMORY_KEY = 'ceres:lastPlayer';
const BOARD_LOAD_SESSION_KEY = 'ceres:boardLoadSession';
function newBoardLoadSessionId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
function readBoardLoadSession() {
  try {
    const stored = JSON.parse(sessionStorage.getItem(BOARD_LOAD_SESSION_KEY) || 'null');
    if (stored && typeof stored.id === 'string' && stored.id.length > 0 && stored.id.length <= 128
        && Number.isSafeInteger(stored.generation) && stored.generation >= 0
        && stored.generation < Number.MAX_SAFE_INTEGER) return stored;
  } catch (_) { /* a blocked or corrupt session store gets a page-local identity */ }
  return { id: newBoardLoadSessionId(), generation: 0 };
}
function persistBoardLoadSession() {
  try {
    sessionStorage.setItem(BOARD_LOAD_SESSION_KEY, JSON.stringify({ id: BOARD_LOAD_SESSION_ID, generation: BOARD_GENERATION }));
  } catch (_) { /* ordering still holds for this page lifetime */ }
}
// Hive account names are lowercase -- "player0002" was sent as typed and the server refused it
// ("invalid player"), which the page showed as "The latest board data could not be loaded".
function normalizePlayer(name) {
  return String(name || '').trim().toLowerCase() || null;
}
function rememberPlayer(name) {
  try { if (name) localStorage.setItem(PLAYER_MEMORY_KEY, name); } catch (_) { /* storage blocked: URL still carries it */ }
}
function syncPlayerInUrl(name) {
  if (!name) return;
  const url = new URL(location.href);
  if (url.searchParams.get('player') !== name) {
    url.searchParams.set('player', name);
    history.replaceState(null, '', url);
  }
  rememberPlayer(name);
}
// this replaces 's fill-in -- no ?player= means the start page; the remembered player is only offered there.
export let CURRENT_PLAYER = normalizePlayer(new URLSearchParams(location.search).get('player')) || null;
syncPlayerInUrl(CURRENT_PLAYER);
const initialBoardLoadSession = readBoardLoadSession();
const BOARD_LOAD_SESSION_ID = initialBoardLoadSession.id;
export let BOARD_GENERATION = initialBoardLoadSession.generation;
persistBoardLoadSession();
let boardLoadController = null;
// The board's numeric DEC stake summary is rendered from /api/board. Region plaques
// themselves carry labels; wallet-resources.js renders their balance figures and the
// wallet listener refreshes those. Keep the board summary current after a verdict.
onBalancesChanged(({ assets }) => {
  if (CURRENT_PLAYER && !boardLoadController && assets.some(asset => asset === 'DEC' || asset === 'CARDS')) {
    refreshBoardTotals(BOARD_GENERATION, CURRENT_PLAYER);
  }
}, { player: () => CURRENT_PLAYER });

export const SPINNER_SHOW_DELAY_MS = 200;
export const SPINNER_MIN_VISIBLE_MS = 300;
// (2026-09-14): the poll used to give up after 240 × 250 ms = 60 s and read the
// board anyway -- on a 4357-plot account (player0006) the first read took minutes, no
// snapshot existed yet, and the page showed "could not be loaded" while the server was
// still working. The wait is now bounded by the capture's STATE (running → idle/failed)
// with a 30-minute safety ceiling only against a server that never settles.
// (2026-09-14): the old 250 ms × 20 / 500 ms / 1 s cadence made 37 status polls on
// a whale and 18 on a 461-plot account. The poll is now 500 ms for the first 5 s (a small
// account finishes there), then 1 s until the first minute, then 2 s -- the reveal lands at
// most 1 s late early on and at most 2 s late on a long read. When the status body carries
// an `estimated_seconds` above 10 s the fast tier is skipped and polling starts at 1 s: no
// reason to ask twice a second for a read the server itself says takes a minute.
export const CAPTURE_STATUS_FAST_POLL_INTERVAL_MS = 500;
export const CAPTURE_STATUS_MID_POLL_INTERVAL_MS = 1000;
export const CAPTURE_STATUS_SLOW_POLL_INTERVAL_MS = 2000;
export const CAPTURE_STATUS_FAST_POLL_ATTEMPTS = 10;
export const CAPTURE_STATUS_MID_POLL_UNTIL_MS = 60000;
export const CAPTURE_STATUS_SKIP_FAST_POLL_ABOVE_SECONDS = 10;
export const CAPTURE_STATUS_MAX_WAIT_MS = 30 * 60 * 1000;
// (2026-09-20, review of v0.9.687): defense-in-depth floor for the `wait=`
// long-poll below -- if a status round trip comes back in under this many ms, the
// loop sleeps out the REMAINDER of that attempt's own cadence before polling again
// so a server that ignores `wait` (or returns early for a reason the client did not
// expect) can never turn this into a busy-spin loop. The regression this guards
// `waitForCaptureToSettle()` kept looping on `state === 'running'`, but the server's
// long-poll returned instantly once `board_ready` turned true while the self-heal
// tail was still `running` -- ~80 status requests/second measured until the tail
// finished. Fixed at the root by `wait_for=settled` (server ignores board_ready for
// the settle loop); this floor is the belt to that braces.
export const CAPTURE_STATUS_FLOOR_MS = 250;

// `fadeMs` (board panel only) turns the hide into a soft handover -- class `is-done`
// first (the CSS fades the panel and fills the seal to 1), the `hidden` attribute after
// the fade. A show() in between cancels the pending hide and strips `is-done` again.
// v0.9.372: `holdBeforeFadeMs` (a function) lets the fade wait for the station cascade to
// finish. v0.9.376: the function owns the clamp (see harvestFinishHoldMs), so the finish
// always takes about a second. Reduced motion skips hold and fade alike.
function createSpinnerGate(getEl, { fadeMs = 0, onHidden = null, holdBeforeFadeMs = null } = {}) {
  let active = 0;
  let showTimer = null;
  let hideTimer = null;
  let shownAt = 0;
  const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const hideNow = () => {
    const target = getEl();
    if (!target) return;
    const finish = () => { target.hidden = true; target.classList.remove('is-done'); if (onHidden) onHidden(); };
    if (fadeMs > 0 && !reducedMotion()) {
      const fade = () => {
        target.classList.add('is-done');
        hideTimer = setTimeout(() => { hideTimer = null; finish(); }, fadeMs);
      };
      const hold = holdBeforeFadeMs ? Math.max(0, holdBeforeFadeMs()) : 0;
      if (hold > 0) hideTimer = setTimeout(() => { hideTimer = null; fade(); }, hold);
      else fade();
    } else {
      finish();
    }
  };
  return {
    show() {
      active += 1;
      if (active > 1 || showTimer) return;
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
      const el = getEl();
      if (el) el.classList.remove('is-done');
      if (el && !el.hidden) return;
      showTimer = setTimeout(() => {
        showTimer = null;
        if (active <= 0) return;
        const target = getEl();
        if (target) target.hidden = false;
        shownAt = Date.now();
      }, SPINNER_SHOW_DELAY_MS);
    },
    hide() {
      active = Math.max(0, active - 1);
      if (active > 0) return;
      if (showTimer) { clearTimeout(showTimer); showTimer = null; if (onHidden) onHidden(); return; }
      const el = getEl();
      if (!el || el.hidden) { if (onHidden) onHidden(); return; }
      if (hideTimer) return;
      const remaining = SPINNER_MIN_VISIBLE_MS - (Date.now() - shownAt);
      if (remaining > 0) {
        hideTimer = setTimeout(() => { hideTimer = null; hideNow(); }, remaining);
      } else {
        hideNow();
      }
    },
  };
}
const boardSpinnerGate = createSpinnerGate(() => document.getElementById('board-loading'), {
  fadeMs: 300,
  onHidden: () => stopHarvestFill(),
  holdBeforeFadeMs: () => harvestFinishHoldMs(HARVEST_FADE_MS), // v0.9.376: a finishing moment of about a second
});
const HARVEST_FADE_MS = 300;
// (v0.9.376), his words on v0.9.375: "reserve a moment to finish the final animation".
// v0.9.381, his words on v0.9.378: "Moving in circles is the loading animation, which is
// followed by a concluding flash up animation, where all six fire at the same time and then
// reveal the board." The finish on the RENDERED signal (markHarvestRendered): the fill sweeps
// to 1 (400 ms, CSS `is-ready`) while all six bubbles flash up AT ONCE and stay lit; the panel
// holds for that concluding flash (HARVEST_FINAL_FLASH_MS) and then fades (300 ms), so
// rendered → hidden is HARVEST_FINISH_MIN_MS to HARVEST_FINISH_MAX_MS (≈ 0.7–0.9 s).
const HARVEST_FINISH_MIN_MS = 700;
const HARVEST_FINISH_MAX_MS = 900;
const HARVEST_FINAL_FLASH_MS = 500;
const HARVEST_SWEEP_MS = 400;
function harvestFinishHoldMs(fadeMs) {
  const sinceReady = harvestReadyAt ? Math.max(0, Date.now() - harvestReadyAt) : 0;
  const wanted = Math.max(harvestCascadeRemainingMs(), HARVEST_SWEEP_MS - sinceReady, HARVEST_FINISH_MIN_MS - fadeMs - sinceReady);
  return Math.max(0, Math.min(HARVEST_FINISH_MAX_MS - fadeMs - sinceReady, wanted));
}

// Capture phases are milestones, not elapsed-time percentages. An early usable
// snapshot skips directly to board transfer; only the rendered board completes it.
let harvestFillReady = false;
let harvestReadyAt = 0;
let harvestFillMax = 0;
function advanceHarvestFill(value) {
  const host = document.getElementById('board-loading');
  if (!host || harvestFillReady) return;
  harvestFillMax = Math.max(harvestFillMax, value);
  host.style.setProperty('--harvest-fill', String(harvestFillMax));
}
// v0.9.381, his words on v0.9.378: "The flashing resource bubbles never flash for all six.
// Not even for awhale, after first load has been done. Instead, make them always flash up
// while the bubble move around. never tied to progress." The bubbles no longer read the
// fill: measured milestones only draw the seal.
function startHarvestFill() {
  stopHarvestFill();
  harvestFillReady = false;
  harvestReadyAt = 0;
  harvestFillMax = 0;
  const host = document.getElementById('board-loading');
  if (host) {
    host.classList.remove('is-ready');
    host.style.setProperty('--harvest-fill', '0');
    for (const node of host.querySelectorAll('.harvest-node')) node.classList.remove('is-lit', 'is-pulsing');
  }
  startHarvestCycle();
}
function stopHarvestFill() {
  stopHarvestCycle();
  clearHarvestPulses();
}
// v0.9.381: while the panel is up the six bubbles run a continuous clockwise flash-up
// one at a time, 12 → 2 → 4 → 6 → 8 → 10 o'clock (data-node 1..6), HARVEST_CYCLE_STEP_MS
// apart with a short pause per lap -- independent of fill, phase and estimate. Nothing
// stays lit until the board is rendered (markHarvestRendered). Reduced motion: no cycle.
const HARVEST_CYCLE_STEP_MS = 160;
const HARVEST_CYCLE_LAP_PAUSE_MS = 420;
let harvestCycleTimer = null;
let harvestCyclePos = 0; // the bubble that flashed last (1..6), 0 before the first
function startHarvestCycle() {
  stopHarvestCycle();
  harvestCyclePos = 0;
  if (harvestReducedMotion()) return;
  harvestCycleTimer = setTimeout(harvestCycleStep, HARVEST_CYCLE_STEP_MS);
}
function harvestCycleStep() {
  harvestCycleTimer = null;
  const host = document.getElementById('board-loading');
  if (!host || harvestFillReady) return;
  harvestCyclePos = (harvestCyclePos % 6) + 1;
  flashHarvestNode(host, harvestCyclePos, false);
  const wait = harvestCyclePos === 6 ? HARVEST_CYCLE_STEP_MS + HARVEST_CYCLE_LAP_PAUSE_MS : HARVEST_CYCLE_STEP_MS;
  harvestCycleTimer = setTimeout(harvestCycleStep, wait);
}
function stopHarvestCycle() {
  if (harvestCycleTimer) { clearTimeout(harvestCycleTimer); harvestCycleTimer = null; }
}
function harvestReducedMotion() {
  return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}
// One flash of bubble n (the 600 ms pulse + spoke dash); `lit` keeps it bright afterwards.
function flashHarvestNode(host, n, lit) {
  const node = host.querySelector(`.harvest-node[data-node="${n}"]`);
  if (!node) return;
  if (lit) node.classList.add('is-lit');
  node.classList.remove('is-pulsing');
  void node.offsetWidth; // restart the animation if the class was just removed
  node.classList.add('is-pulsing');
  const done = () => { clearTimeout(fallback); node.classList.remove('is-pulsing'); };
  const fallback = setTimeout(done, HARVEST_PULSE_FALLBACK_MS);
  node.addEventListener('animationend', done, { once: true });
}
// v0.9.381: the ready-AND-RENDERED signal -- called from revealBoardTable() after the first
// render(true), not on the status's board_ready. Stops the cycle where it is and fires the
// concluding flash-up: all six at the same time, each landing lit, together with the fill
// sweep to 1; the gate's hold (harvestFinishHoldMs) covers that flash, then the fade.
function markHarvestRendered() {
  const host = document.getElementById('board-loading');
  if (!host || harvestFillReady) return;
  harvestFillReady = true;
  harvestReadyAt = Date.now();
  stopHarvestCycle();
  host.classList.add('is-ready');
  host.style.setProperty('--harvest-fill', '1');
  // Start the sweep before subsequent synchronous board work consumes its hold.
  const fill = host.querySelector('.harvest-fill');
  if (fill) void getComputedStyle(fill).transform;
  if (harvestReducedMotion()) {
    for (const node of host.querySelectorAll('.harvest-node')) node.classList.add('is-lit');
    return;
  }
  clearHarvestPulses();
  for (let n = 1; n <= 6; n += 1) flashHarvestNode(host, n, true);
  harvestPulseEndsAt = Date.now() + HARVEST_FINAL_FLASH_MS;
}
// the concluding flash's end time, for the handover hold (harvestFinishHoldMs).
// v0.9.381: no per-station queue any more -- the cycle has its own timer and the
// concluding flash fires all six at once (markHarvestRendered).
const HARVEST_PULSE_FALLBACK_MS = 800; // removes is-pulsing if animationend never fires
let harvestPulseEndsAt = 0;
function clearHarvestPulses() { harvestPulseEndsAt = 0; }
// Milliseconds until the concluding flash has finished (0 when nothing is running).
function harvestCascadeRemainingMs() {
  return Math.max(0, harvestPulseEndsAt - Date.now());
}
function setPlayerLoading(loading) {
  const button = document.getElementById('player-go-btn');
  const spinner = document.getElementById('player-loading');
  if (spinner) spinner.hidden = !loading;
  button?.setAttribute('aria-busy', String(loading));
  button?.setAttribute('aria-label', loading ? 'Switch player. Loading profile.' : 'Switch player');
}

// R-149, his ruling: "do 1" -- report the capture's real PHASE rather than a
// fabricated fraction. The default board read is a QUICK, bulk-only capture, so
// fetched/total stay null throughout it and there is genuinely no N of M to show.
// These names mirror the server's own sequential steps (ceres/capture.py `_set_phase`)
// each published as it BEGINS, so the text never claims a step that has not started.
const CAPTURE_PHASE_TEXT = {
  card_details: 'Reading card details…',
  plots: 'Reading your plots…',
  balances: 'Reading your balances…',
  merging: 'Merging with your board…',
  cards: 'Reading your cards…',
  gaps: 'Filling the gaps…',
};

function boardLoadingMessage(status) {
  // A replacement can wait briefly while the superseded worker exits and is reaped.
  if (status?.state === 'queued') return 'Replacing the previous board read…';
  const fetched = status?.fetched_deeds;
  const total = status?.total_deeds;
  // A full capture DOES fan out per deed and therefore has a real count; prefer it.
  if (Number.isInteger(fetched) && fetched >= 0 && Number.isInteger(total) && total >= 0) {
    return `Reading your plots… ${fetched} of ${total}`;
  }
  // plot count and a measured-rate estimate for every account (his ruling: 'interesting for all users, not just whales'); no estimate is shown until this server has measured one read (). v0.9.363 (his ruling: 'the seconds ticker ... is faulty and incorrect. remove it and keep it vague'): no elapsed-seconds ticker and no 'taking longer than usual' -- the text stays vague.
  const fmtInt = new Intl.NumberFormat('en-US').format;
  const fmtDuration = (seconds) => seconds < 60
    ? `${Math.ceil(seconds / 5) * 5} s`
    : `${Math.ceil(seconds / 30) / 2} min`;
  let message = CAPTURE_PHASE_TEXT[status?.phase] || 'Reading your plots…';
  if (Number.isInteger(total) && total > 0) message += ` · ${fmtInt(total)} plots`;
  const est = status?.estimated_seconds;
  const hasEstimate = Number.isFinite(est) && est > 0;
  if (hasEstimate) message += ` · about ${fmtDuration(est)}`;
  return message;
}

function setBoardLoadingMessage(message, status) {
  const text = document.querySelector('#board-loading .board-loading-text');
  if (text) text.textContent = message;
  // R-148, his request: "Can we should the loading of the plots with some sort of
  // progress? Is that possible with our current approach". It is, and nothing needs
  // estimating -- boardLoadingMessage() above already reads a MEASURED fetched/total
  // off the capture status. The only thing missing was that the figure never left the
  // text. It is published here as a 0..1 custom property so the loading art can draw it.
  // TWO HONESTY RULES, because a progress indicator that lies is worse than none
  // * the arc only ever shows what has been COUNTED -- it never animates ahead of the
  // measurement toward a guess
  // * before a total is known there is no fraction, so the element is marked
  // indeterminate rather than being given a 0 that would read as "nothing done".
  const host = document.getElementById('board-loading');
  if (!host) return;
  const fetched = status?.fetched_deeds;
  const total = status?.total_deeds;
  if (Number.isInteger(fetched) && Number.isInteger(total) && total > 0) {
    host.style.setProperty('--harvest-progress', String(Math.max(0, Math.min(1, fetched / total))));
    host.dataset.progress = 'known';
  } else {
    host.style.removeProperty('--harvest-progress');
    host.dataset.progress = 'indeterminate';
  }
  const phaseFill = { card_details: 0.06, plots: 0.12, cards: 0.58, balances: 0.78, merging: 0.86, gaps: 0.92 };
  if (status?.state !== 'queued') advanceHarvestFill(phaseFill[status?.phase] || 0);
  if (Number.isInteger(fetched) && Number.isInteger(total) && total > 0 && status?.phase === 'plots') {
    advanceHarvestFill(0.12 + 0.44 * Math.max(0, Math.min(1, fetched / total)));
  }
  if (status?.board_ready === true) advanceHarvestFill(0.92);
}

export function captureStatusPollInterval(attempt, elapsedMs, estimatedSeconds = null) {
  // a server estimate above 10 s skips the fast tier -- mid cadence from the first poll.
  const longRead = Number.isFinite(estimatedSeconds) && estimatedSeconds > CAPTURE_STATUS_SKIP_FAST_POLL_ABOVE_SECONDS;
  if (!longRead && attempt < CAPTURE_STATUS_FAST_POLL_ATTEMPTS) return CAPTURE_STATUS_FAST_POLL_INTERVAL_MS;
  if (elapsedMs < CAPTURE_STATUS_MID_POLL_UNTIL_MS) return CAPTURE_STATUS_MID_POLL_INTERVAL_MS;
  return CAPTURE_STATUS_SLOW_POLL_INTERVAL_MS;
}

// the filter bar (`.controls-row`) hides and reveals with the table -- it must not
// show behind the loading animation. Only the non-quiet load path calls hideBoardTable().
function hideBoardTable() {
  document.getElementById('table-head-sticky')?.classList.add('board-content-hidden');
  document.getElementById('plot-table-scroll')?.classList.add('board-content-hidden');
  document.getElementById('plot-card-list')?.classList.add('board-content-hidden');
  document.getElementById('mobile-action-bar')?.classList.add('board-content-hidden');
  document.querySelector('.controls-row')?.classList.add('board-content-hidden');
}
function revealBoardTable(ready = true) {
  if (ready) markHarvestRendered();
  document.getElementById('table-head-sticky')?.classList.remove('board-content-hidden');
  document.getElementById('plot-table-scroll')?.classList.remove('board-content-hidden');
  document.getElementById('plot-card-list')?.classList.remove('board-content-hidden');
  document.getElementById('mobile-action-bar')?.classList.remove('board-content-hidden');
  document.querySelector('.controls-row')?.classList.remove('board-content-hidden');
  // (maintainer, verbatim: "the wallet slider must not be visible during the
  // animation at page load"): app.js's own initBadgeFlag()-adjacent wiring (see
  // that file) listens for this to reveal `#edge-tabs` -- board.js cannot import
  // app.js directly (app.js already imports FROM board.js, a circular import)
  // so a DOM event is the hand-off, same pattern `ceres:player-changed` already
  // uses below. Fired on every call (both the "quiet"/`ready:false` and the
  // normal `ready:true` paths, and the reduced-quiet paths above) -- the wallet
  // pill should appear whenever the board itself does, not only on a full render.
  window.dispatchEvent(new CustomEvent('ceres:board-revealed'));
}
export let BOARD = null;
export let NOTICE_CATALOG = {};

// a page opened with ?plot=<label> reads and draws THAT plot first
// (`/api/board?plots=<label>`, the same publication cut to one row, `partial: true`), opens it, and draws the full board
// as soon as it is in. Everything board-wide -- the filter bar, the problem count, the listed-plot counter, the badge
// bar and the edge tabs -- stays hidden or blank until the full board is drawn, so no number is ever read from the one
// row. The opened plot keeps its place on screen across the full-board draw and the capture's settle re-read
// (PLOT_ANCHOR) until the reader scrolls, clicks or types. stage-navigation.js owns the label and the open.
let PLOT_FIRST_HOOKS = null;
let PLOT_ANCHOR = null;
export function setPlotFirstHooks(hooks) { PLOT_FIRST_HOOKS = hooks ? { ...hooks, used: false } : null; }
function releasePlotAnchor() { PLOT_ANCHOR = null; }
['pointerdown', 'wheel', 'keydown', 'touchstart'].forEach((type) => window.addEventListener(type, releasePlotAnchor, { capture: true, passive: true }));
function plotRowTop(deedUid) {
  if (!deedUid) return null;
  const row = document.querySelector(`tr.plot-row[data-deed-uid="${CSS.escape(deedUid)}"]`);
  return row ? row.getBoundingClientRect().top : null;
}
// The ETag of a filtered read is `"<token>;plots=<digest>"`; both reads compare on the publication token alone.
function boardTokenOf(resp) {
  const etag = resp?.headers?.get?.('ETag') || '';
  return etag.replace(/^W\//, '').replace(/"/g, '').split(';')[0] || null;
}
const PLOT_FIRST_STYLE_ID = 'ceres-plot-first-style';
function setPlotFirstPhase(on) {
  document.documentElement.classList.toggle('ceres-plot-first', on);
  if (!on || document.getElementById(PLOT_FIRST_STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = PLOT_FIRST_STYLE_ID;
  // The listed-plot counter and each row's position are board-wide numbers: blank, not "1", until the full board.
  // The page is given room to scroll so the plot can sit under the header exactly where it stays afterwards.
  style.textContent = 'html.ceres-plot-first .count-cell, html.ceres-plot-first #table-head-sticky .count-total { visibility: hidden; }'
    + ' html.ceres-plot-first body { min-height: 300vh; }';
  document.head.appendChild(style);
}
// ready signal: `workers` is true when no open plot is still loading. Recomputed wherever a state changes and
// on a short timer (rows.js lands details without an event), so a reader of window.__ceresReady never sees a stale true.
export function syncWorkersReady() {
  const loading = [...EXPANDED_ROWS].some((uid) => !EXPANDED_DATA_LOADED.has(uid) && !EXPAND_ERRORS.has(uid));
  return setReady({ workers: !loading });
}
const WORKERS_READY_TIMER = setInterval(syncWorkersReady, 100);
WORKERS_READY_TIMER?.unref?.(); // a module test in node must still exit

async function drawPlotFirst(hooks, signal, generation) {
  let resp;
  try {
    resp = await fetch(playerQueryAppend(`/api/board?plots=${encodeURIComponent(hooks.label)}`), { signal });
  } catch (_) { return null; }
  if (signal.aborted || generation !== BOARD_GENERATION || !resp.ok) return null; // 404: the full board's path says so
  let board;
  try { board = await resp.json(); } catch (_) { return null; }
  if (signal.aborted || generation !== BOARD_GENERATION) return null;
  const row = Array.isArray(board?.plots) ? board.plots[0] : null;
  if (!row || board.partial !== true) return null;
  BOARD = board;
  EXPANDED_DATA_LOADED.clear();
  NOTICE_CATALOG = BOARD.notice_catalog || NOTICE_CATALOG;
  resetStagingForFreshBoard();
  CURRENT_PLAYER = BOARD.player || CURRENT_PLAYER;
  syncPlayerInUrl(CURRENT_PLAYER);
  setPlotFirstPhase(true);
  updateNoLandState();
  render(true);
  document.getElementById('table-head-sticky')?.classList.remove('board-content-hidden');
  document.getElementById('plot-table-scroll')?.classList.remove('board-content-hidden');
  document.getElementById('plot-card-list')?.classList.remove('board-content-hidden');
  markHarvestRendered();
  setPlayerLoading(false); // the spinner gate is released here, so the account button's busy state goes with it
  // the row sits under the header line from the first frame. Twice: the first scroll lets the sticky header
  // stick (its line moves up), the second puts the row under the stuck line -- where the full board will hold it.
  revealPlotRow(row.deed_uid);
  revealPlotRow(row.deed_uid);
  PLOT_ANCHOR = row.deed_uid;
  setReady({ board: 'plot' });
  try { hooks.drawn?.(row); } catch (_) { /* the full board's path still opens it */ }
  return { deedUid: row.deed_uid, token: boardTokenOf(resp) };
}

// The resets every fresh (non-quiet) board read makes before its first draw.
// (4-space body: test_board_r111_guards.py's removal injections anchor on these exact lines.)
function resetStagingForFreshBoard() {
    CERES_STAGE.length = 0;
    CERES_STAGED_WORKSITES.clear();
    S.CERES_PICKER = null;
    CERES_PREVIEWS.clear();
  // the same player's full reload keeps an open Changes window open (it says it is checking, then lists the
  // restored changes); only a switch to another player closes it.
  if (String(CURRENT_PLAYER || '').toLowerCase() !== String(BOARD.player || '').toLowerCase()) S.CERES_CHECKOUT_OPEN = false;
  renderCheckoutSurface();
}
function playerQuery() {
  return CURRENT_PLAYER ? `?player=${encodeURIComponent(CURRENT_PLAYER)}` : '';
}

export function playerQueryAppend(url) {
  if (!CURRENT_PLAYER) return url;
  return url + (url.includes('?') ? '&' : '?') + `player=${encodeURIComponent(CURRENT_PLAYER)}`;
}

export function noticeMessage(notice) {
  if (!notice) return '';
  const message = notice.message || NOTICE_CATALOG[notice.code]?.message;
  if (!message) return `Unknown safety notice: ${notice.code || 'unclassified'}`;
  const context = { ...(notice.context || {}) };
  if (notice.reason !== undefined) context.reason = notice.reason;
  return String(message).replace(/\{([a-z_]+)\}/gi, (whole, key) => context[key] === undefined ? whole : String(context[key]));
}

export function catalogNotice(code, context = {}) {
  const spec = NOTICE_CATALOG[code] || {};
  return { code, kind: spec.kind, severity: spec.severity, blocking: spec.blocking, message: spec.message, context };
}

function renderNotice(notice, extraClass = '') {
  if (!notice) return '';
  const role = notice.blocking && notice.blocking !== 'none' ? 'alert' : (notice.severity === 'caution' ? 'status' : 'status');
  const reason = notice.reason || notice.evidence?.reason;
  const action = notice.action?.id === 'open_bloodline_pairs'
    ? `<button type="button" class="notice-action ceres-btn ceres-btn--secondary" data-control-density="compact" data-notice-action="open-bloodline-pairs" data-sub-type="${esc(notice.action.sub_type || notice.context?.sub_type || '')}">${esc(notice.action.label || 'Find a matching companion')}</button>`
    // defect 3 -- the checkout's verification notices (verification_pending
    // verification_pending_escalated, verification_could_not_verify, and any
    // unrecognised-state fallback -- see ceres/notices.py::verification_notice) carry
    // this action so "Not confirmed yet" copy is paired with something that actually
    // re-checks, mirroring the worksite modal's own "Check again" button. The click
    // handler looks the target chunk/review up from `CHECKOUT_VERIFY_TARGETS`, keyed by
    // the containing `.op-state` element.
    : notice.action?.id === 'retry_verification'
      ? `<button type="button" class="notice-action ceres-btn ceres-btn--secondary" data-control-density="compact" data-notice-action="retry-verification">${esc(notice.action.label || 'Check again')}</button>` : '';
  return `<div class="ceres-notice ceres-notice-${esc(notice.kind || 'advisory')} ceres-notice-${esc(notice.severity || 'info')} ${extraClass}" data-notice-code="${esc(notice.code || 'unknown_notice')}" data-deed-uid="${esc(notice.context?.deed_uid || '')}" role="${role}" aria-live="${role === 'alert' ? 'assertive' : 'polite'}"><span class="notice-message">${decorateResourceMentions(esc(noticeMessage(notice)))}</span>${reason ? `<small class="notice-reason">${esc(reason)}</small>` : ''}${action}</div>`;
}

export function renderNotices(notices, extraClass = '') {
  return dedupNotices(notices).map((notice) => renderNotice(notice, extraClass)).join('');
}

// the same notice (code + message) is drawn once, however often the page appended it -- two failed board
// reads during a deploy restart stacked two "board read failed" rows.
export function dedupNotices(notices) {
  const seen = new Set();
  return (Array.isArray(notices) ? notices : []).filter((notice) => {
    const key = `${notice?.code || ''}|${notice?.message || ''}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function appendBoardNotice(notice) {
  if (!BOARD || !notice) return;
  BOARD.notices = dedupNotices([...(BOARD.notices || []), notice]);
}

// Custom dropdowns (board-polish batch 9, item 8) -- vendored
// customSelect.js wraps each native <select> with a styled trigger + list
// while leaving the native element live, so every `addEventListener('change'
// ...)` below and matchesFilters()'s own `.value` reads are untouched. Filter
// options carry the board's own visual language via `decorateOption` (a
// Ceres-local addition to the vendored file, see its header comment)
// Terrain gets its terrain-colour background fill + emblem, Rarity its gem
// Worksite its resource icon, Type its plot-type mark. The Sort control is
// also wrapped for chrome consistency but gets no icon decoration (its
// values are metric names, not board-visual categories).
// every filter select below is multi-select
// (`multiple: true`); a row matches a filter when it matches ANY chosen value, and the filters still AND together
// (matchesFilters). Sort stays single-select.
export function initFilterDropdowns() {
  // Look 2: `ariaLabel` gives each compact trigger its own category name for a
  // screen reader now that the visible outer captions are gone (index.html's `.sr-only`
  // spans keep the same names for the native <select> itself/no-JS case).
  // Region options wear the region's territory colour; the closed trigger only tints its text (the Plot column's own
  // territory-* tokens); the territory comes from the option's data-territory set by populateFilterOptions().
  // Look 2 (maintainer: "Just use the region number, remove the name in the closed region.
  // Still show the name in the dropdown."): `decorateTrigger` overrides only the closed
  // trigger's label to the region number; the option list (decorateOption below) keeps
  // number + name untouched, and colourRegionControl() still owns the whole-field territory
  // colour independently of this label override.
  initCustomSelect(document.getElementById('region-filter'), {
    ariaLabel: 'Region filter',
    multiple: true,
    decorateOption: (v) => { const slug = territorySlug(regionTerritory(v)); return slug ? { className: `csel-option-territory territory-${slug}` } : null; },
    decorateTrigger: (v) => (v ? { label: v.padStart(3, '0') } : null),
  });
  document.getElementById('region-filter').addEventListener('change', () => { saveRegionChoice(); colourRegionControl(); });
  initCustomSelect(document.getElementById('rarity-filter'), {
    ariaLabel: 'Rarity filter',
    multiple: true,
    decorateOption: (v) => (v ? { icon: rarityOptionIconHtml(v) } : null),
  });
  initCustomSelect(document.getElementById('terrain-filter'), {
    ariaLabel: 'Terrain filter',
    multiple: true,
    decorateOption: (v) => (v ? { icon: terrainOptionIconHtml(v), className: `csel-option-terrain terrain-${v.toLowerCase()}` } : null),
  });
  initCustomSelect(document.getElementById('resource-filter'), {
    ariaLabel: 'Worksite filter',
    multiple: true,
    decorateOption: (v) => (v ? { icon: resourceIconHtml(v) } : null),
  });
  initCustomSelect(document.getElementById('status-filter'), {
    ariaLabel: 'Type filter',
    multiple: true,
    decorateOption: (v) => (v ? { icon: v.startsWith('magical:') ? `${typeIconOnlyHtml('magical')}${splinterIconHtml(v.split(':')[1], false)}` : typeIconOnlyHtml(v) } : null),
  });
  initCustomSelect(document.getElementById('badge-filter'), {
    ariaLabel: 'Badges filter',
    multiple: true,
    decorateOption: (v) => {
      const option = badgeFilterOptions().find((entry) => entry.key === v);
      return option ? { className: `badge-option-group-${option.group}` } : null;
    },
  });
  document.getElementById('badge-filter').addEventListener('change', () => { saveBadgeChoice(); colourBadgeFilterControl(); });
}

// the chosen values of one board filter select ([] = Any).
export function boardFilterValues(id) { return selectValues(document.getElementById(id)); }
// a kept filter choice is stored as before when it is one value (a plain string, so a store written by
// an older Ceres and one written now read the same), and as a JSON array only when several are chosen.
export function encodeFilterChoice(values) {
  const list = (values || []).filter(Boolean).map(String);
  return list.length > 1 ? JSON.stringify(list) : (list[0] || '');
}
export function decodeFilterChoice(raw) {
  const text = String(raw ?? '');
  if (text.startsWith('[')) {
    try { const list = JSON.parse(text); if (Array.isArray(list)) return list.filter((v) => v != null && v !== '').map(String); } catch (_) { /* fall through: treat as one value */ }
  }
  return text ? [text] : [];
}

// Look 3
// the row's own eight filter dimensions -- text search, the six category selects and
// the Staged toggle. Sort and the Iris theme dock are deliberately excluded
// Sort is not a filter, and the theme picker is not board state.
const BOARD_FILTER_SELECT_IDS = ['region-filter', 'rarity-filter', 'terrain-filter', 'resource-filter', 'status-filter', 'badge-filter'];
const BOARD_FILTER_TOGGLE_IDS = ['staged-only'];

// True while any of the eight dimensions above is not at its neutral value
// drives whether "Clear all" shows at all.
export function boardFiltersActive() {
  if (document.getElementById('text-filter')?.value.trim()) return true;
  if (BOARD_FILTER_SELECT_IDS.some((id) => document.getElementById(id)?.value)) return true;
  if (BOARD_FILTER_TOGGLE_IDS.some((id) => document.getElementById(id)?.checked)) return true;
  return false;
}

export function boardFilterCount() {
  let count = document.getElementById('text-filter')?.value.trim() ? 1 : 0;
  count += BOARD_FILTER_SELECT_IDS.filter((id) => document.getElementById(id)?.value).length;
  count += BOARD_FILTER_TOGGLE_IDS.filter((id) => document.getElementById(id)?.checked).length;
  return count;
}

// Shows/hides #clear-board-filters to match current filter state. Called from app.js's
// own delegated `.controls-row` input/change listener (same pattern as syncCloseAll's)
// after any real edit, and once here after a fresh board load restores the kept Region
// choice () -- a programmatic `.value =` never fires 'change' on its own, so that
// restore would otherwise leave the button's visibility stale.
export function syncBoardFilterChrome() {
  const btn = document.getElementById('clear-board-filters');
  if (btn) btn.hidden = !boardFiltersActive();
  const count = boardFilterCount();
  const mobileCount = document.getElementById('mobile-filters-count');
  if (mobileCount) {
    mobileCount.textContent = String(count);
    mobileCount.hidden = count === 0;
  }
  const mobileFilters = document.getElementById('mobile-filters-btn');
  if (mobileFilters) {
    mobileFilters.classList.toggle('is-active', count > 0);
    mobileFilters.setAttribute('aria-label', count ? `Filters, ${count} active` : 'Filters');
  }
}

// Look 3: resets text search, six
// selects and Staged toggle to neutral by setting each control's OWN value/checked state
// and dispatching its OWN normal input/change event -- never a private reset path
// so every existing listener (app.js's per-field render() calls, this file's Region
// save+recolour, customSelect's own trigger resync, rows.js's bulk-button sync) reacts
// exactly as if the maintainer had cleared each control by hand, in one pass, ending in one
// final same-dataset render. Sort and the theme dock are untouched.
export function clearBoardFilters() {
  const text = document.getElementById('text-filter');
  if (text && text.value) {
    text.value = '';
    text.dispatchEvent(new Event('input', { bubbles: true }));
  }
  for (const id of BOARD_FILTER_SELECT_IDS) {
    const sel = document.getElementById(id);
    if (sel && sel.value) {
      sel.value = ''; // region-filter's own 'change' listener re-saves+recolours to Any
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }
  for (const id of BOARD_FILTER_TOGGLE_IDS) {
    const box = document.getElementById(id);
    if (box && box.checked) {
      box.checked = false;
      box.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }
  syncBoardFilterChrome();
}

// Re-sync the custom dropdowns after populateFilterOptions()
// add real <option> elements to the native selects at runtime (board load)
// the native select stays the source of truth, but the custom list only
// reflects it once told to re-render (syncCustomSelect === _rebuildOptions).
function syncFilterDropdowns() {
  ['region-filter', 'rarity-filter', 'terrain-filter', 'resource-filter', 'status-filter', 'badge-filter']
    .forEach((id) => syncCustomSelect(document.getElementById(id)));
  colourRegionControl();
  colourBadgeFilterControl();
}

// (maintainer: "Add a true region filter into the filter row, right before rarity. Show only owned regions and
// use the correct territory colors"): the region is the first segment of the plot id ("65-3-104" -> "65"); the
// option list is rebuilt from the loaded account's plots on every board load, labelled like the plot ids (065)
// with the game's region name (the territory only colours it). The chosen region is kept per account like the sort choice () -- a view
// preference, never game data ( untouched).
export function plotRegion(plot) { return String(plot ?? '').split('-')[0]; }
function regionTerritory(region) {
  const opt = region ? document.querySelector(`#region-filter option[value="${region}"]`) : null;
  return opt?.dataset.territory || null;
}
function regionStoreKey(account) { return account ? `ceres:region:v1:${String(account).toLowerCase()}` : null; }
function saveRegionChoice() {
  const key = regionStoreKey(CURRENT_PLAYER || BOARD?.player);
  if (!key) return;
  try { localStorage.setItem(key, encodeFilterChoice(boardFilterValues('region-filter'))); } catch (error) { /* a view preference is never worth an error */ }
}
function restoreRegionChoice() {
  const key = regionStoreKey(CURRENT_PLAYER || BOARD?.player);
  if (!key) return [];
  try { return decodeFilterChoice(localStorage.getItem(key)); } catch (error) { return []; }
}
function populateRegionOptions() {
  const sel = document.getElementById('region-filter');
  if (!sel) return;
  // The stored choice is read once per account; later loads of the same account (interim -> final board, reload)
  // keep whatever the control shows, so an explicit "any" is never overridden by the store.
  const account = String(CURRENT_PLAYER || BOARD?.player || '').toLowerCase();
  const wanted = sel.dataset.account === account ? selectValues(sel) : restoreRegionChoice(); // a list of regions
  sel.dataset.account = account;
  const territories = new Map();
  const names = new Map();
  for (const p of BOARD.plots) {
    const r = plotRegion(p.plot);
    if (!r) continue;
    if (!territories.has(r)) territories.set(r, p.territory || '');
    if (!names.has(r) && p.region_name) names.set(r, String(p.region_name));
  }
  sel.innerHTML = '';
  const any = document.createElement('option');
  any.value = '';
  any.textContent = 'Any region'; // Look 2 (maintainer: category-specific "Any" for every filter, closed and as the first option)
  sel.appendChild(any);
  for (const r of [...territories.keys()].sort((a, b) => Number(a) - Number(b))) {
    const opt = document.createElement('option');
    opt.value = r;
    opt.dataset.territory = territories.get(r);
    // fix: the label is the
    // region number and the game's own region name; the territory only decides the colour.
    opt.textContent = names.get(r) ? `${r.padStart(3, '0')} ${names.get(r)}` : r.padStart(3, '0');
    sel.appendChild(opt);
  }
  setSelectValues(sel, wanted.filter((r) => territories.has(r))); // keep the regions that still exist, else any
}
// fix: only the selected region INSIDE the closed control is styled (the chrome stays identical to
// Rarity/Terrain, the label is plain); "any" is plain. (maintainer: "use the correct background and text color
// AFTER a region was selected"): the value becomes a chip wearing the same territory classes as its list row
// same fill, same text colour -- instead of coloured text on the field.
function colourRegionControl() {
  const sel = document.getElementById('region-filter');
  const el = sel?._csel?.trigger;
  if (!el) return;
  // several regions fill the field only when they all share one territory; otherwise it stays plain.
  const slugs = new Set(selectValues(sel).map((r) => territorySlug(regionTerritory(r))));
  const slug = slugs.size === 1 ? [...slugs][0] : null;
  // (maintainer: "Use the same style but not as a badge. Simply color the entire background and text the same
  // way, for the selected region."): the whole field wears the territory fill and text colour; no chip inside.
  [...el.classList].filter((name) => name.startsWith('territory-')).forEach((name) => el.classList.remove(name));
  el.classList.toggle('csel-option-territory', !!slug);
  el.classList.toggle('region-filled', !!slug);
  if (slug) el.classList.add(`territory-${slug}`);
}

function badgeStoreKey() {
  const account = String(CURRENT_PLAYER || BOARD?.player || '').toLowerCase();
  return account ? `ceres:badge:v1:${account}` : null;
}
function saveBadgeChoice() {
  const key = badgeStoreKey();
  if (!key) return;
  try { localStorage.setItem(key, encodeFilterChoice(boardFilterValues('badge-filter'))); } catch (_) { /* View preferences may be unavailable. */ }
}
function boardFilterLabel(value) {
  if (String(value).startsWith('magical:')) {
    const element = String(value).split(':')[1];
    return `Magical ${element.charAt(0).toUpperCase()}${element.slice(1)}`;
  }
  const acronyms = { sps: 'SPS', dec: 'DEC', runi: 'RUNI', pp: 'PP', vov: 'VoV', dd: 'DD', bib: 'BiB' };
  return String(value).replace(/[A-Za-z]+/g, (word, offset) => acronyms[word.toLowerCase()] || (offset === 0 ? word.charAt(0).toUpperCase() + word.slice(1).toLowerCase() : word.toLowerCase()));
}

function populateBadgeOptions() {
  const select = document.getElementById('badge-filter');
  const account = String(CURRENT_PLAYER || BOARD?.player || '').toLowerCase();
  let wanted = select.dataset.account === account ? selectValues(select) : []; // a list of badge keys
  if (select.dataset.account !== account) {
    try { wanted = decodeFilterChoice(localStorage.getItem(badgeStoreKey())); } catch (_) { /* Keep the neutral choice. */ }
  }
  wanted = wanted.map((key) => (key === 'sole_ability_holder' ? 'land_ability_card' : key));
  select.dataset.account = account;
  select.replaceChildren();
  const any = document.createElement('option');
  any.value = ''; any.textContent = 'Any badge'; select.appendChild(any);
  for (const { key, label, group } of badgeFilterOptions()) {
    const option = document.createElement('option');
    option.value = key; option.textContent = boardFilterLabel(label); option.dataset.group = group;
    select.appendChild(option);
  }
  setSelectValues(select, wanted); // unknown keys drop out; none left -> Any badge
}

function colourBadgeFilterControl() {
  const sel = document.getElementById('badge-filter');
  const el = sel?._csel?.trigger;
  if (!el) return;
  // several badges fill the field only when they share one group.
  const groups = new Set([...(sel.selectedOptions || [])].filter((o) => o.value).map((o) => o.dataset.group || ''));
  const group = groups.size === 1 ? [...groups][0] : '';
  [...el.classList].filter((name) => name.startsWith('badge-option-group-')).forEach((name) => el.classList.remove(name));
  el.classList.toggle('badge-filled', !!group);
  if (group) el.classList.add(`badge-option-group-${group}`);
}

// (maintainer: "dont show filters and table header in that case when no plots exist. Instead show or
// loading animation as a still image"): a player who owns no land gets the loading scene, frozen, with one line.
function updateNoLandState() {
  const empty = !!BOARD && Array.isArray(BOARD.plots) && BOARD.plots.length === 0
    && !(BOARD.notices || []).some((notice) => notice?.code === 'capture_failed');
  document.body.classList.toggle('no-land', empty);
  let state = document.getElementById('no-land-state');
  if (!empty) { state?.remove(); return; }
  if (!state) {
    state = document.createElement('section');
    state.id = 'no-land-state';
    state.setAttribute('role', 'status');
    const stage = document.querySelector('#board-loading .harvest-stage')?.cloneNode(true);
    if (stage) { stage.classList.add('is-still'); state.appendChild(stage); }
    const text = document.createElement('p');
    text.className = 'no-land-text';
    state.appendChild(text);
    document.getElementById('board-main')?.prepend(state);
  }
  state.querySelector('.no-land-text').textContent = `${CURRENT_PLAYER || 'This player'} owns no land plots.`;
}

// the start page -- no header, the Ceres logo large in the top left, one field to open a
// player's board. Nothing is read from the game until a player is chosen.
export function showStartPage() {
  document.documentElement.classList.add('start-page');
  document.body.classList.add('start-page');
  if (document.getElementById('start-page')) return;
  const page = document.createElement('section');
  page.id = 'start-page';
  page.setAttribute('aria-labelledby', 'start-page-title');
  const stage = document.querySelector('#board-loading .harvest-stage')?.cloneNode(true);
  if (stage) { stage.classList.add('is-still'); page.appendChild(stage); }
  page.insertAdjacentHTML('beforeend', `<h1 id="start-page-title" class="start-title">Your Land, at a glance</h1>
    <p class="start-lead">Enter a Splinterlands account to open its plots, workers and harvest.</p>
    <img class="start-logo" src="/static/assets/logos/ceres-logo-alchemical-1080.webp" width="1080" height="720" alt="Ceres">
    <form class="start-form" novalidate><input type="text" class="ceres-wide-input start-input" data-control-density="comfortable" name="player" placeholder="Account name" autocomplete="off" spellcheck="false" aria-label="Account name" required><button type="submit" class="start-go ceres-btn ceres-btn--primary" data-control-density="comfortable">Open board</button></form>`);
  page.querySelector('.start-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const name = page.querySelector('.start-input').value;
    if (normalizePlayer(name)) switchPlayer(name);
  });
  document.getElementById('board-main')?.prepend(page);
  page.querySelector('.start-input')?.focus();
}

function hideStartPage() {
  document.documentElement.classList.remove('start-page');
  document.body.classList.remove('start-page');
  document.getElementById('start-page')?.remove();
}

// (maintainer: "it should be as tall as we can justify, left of the name field. Maybe slightly overlap?
// Something cool looking."; header only): the account's Splinterlands avatar as a medallion beside the account field.
// the medallion shows the account's avatar-builder character (avatar.js), layered from the game's own
// artwork -- never the legacy /players/avatar image, which is a RUNI. Browser-only; Ceres adds no route and caches
// nothing. No saved avatar or any failure shows nothing; a late answer for a previous account is ignored.
export function syncPlayerAvatar(account) {
  const box = document.getElementById('player-avatar');
  if (!box) return;
  const name = String(account || '').trim().toLowerCase();
  if (!name) { box.hidden = true; box.replaceChildren(); delete box.dataset.account; delete box.dataset.refreshState; box.removeAttribute('aria-busy'); return; }
  if (box.dataset.account === name) return;
  delete box.dataset.refreshState;
  box.removeAttribute('aria-busy');
  const status = document.getElementById('player-refresh-status');
  if (status) { status.hidden = true; status.textContent = ''; delete status.dataset.state; }
  // Keep a successfully drawn avatar visible while a fresh read is in flight.
  if (!box.querySelector('img')) { box.hidden = true; box.replaceChildren(); }
  box.dataset.account = name;
  const attempt = async (round) => {
    try {
      const urls = await playerAvatarLayers(name);
      if (box.dataset.account !== name) return;
      if (!urls?.length) throw new Error('No avatar layers');
      const images = urls.map((url) => { const img = new Image(); img.alt = ''; img.decoding = 'async'; img.referrerPolicy = 'no-referrer'; img.src = url; return img; });
      const decoded = await Promise.allSettled(images.map((img) => img.decode()));
      if (box.dataset.account !== name) return;
      const loaded = images.filter((_, index) => decoded[index].status === 'fulfilled');
      if (!loaded.length) throw new Error('Avatar images unavailable');
      const spinner = document.createElement('span');
      spinner.className = 'spinner avatar-refresh-spinner';
      spinner.setAttribute('aria-hidden', 'true');
      box.replaceChildren(...loaded, spinner);
      box.hidden = false;
    } catch (_) {
      if (box.dataset.account !== name) return;
      if (round < 2) setTimeout(() => { if (box.dataset.account === name) attempt(round + 1); }, [250, 750][round]);
      else if (!box.querySelector('img')) box.hidden = true;
    }
  };
  attempt(0);
}

let avatarRefreshPromise = null;
let avatarRefreshTimer = null;

export function refreshBoardFromAvatar() {
  const box = document.getElementById('player-avatar');
  if (!box || !CURRENT_PLAYER || avatarRefreshPromise) return avatarRefreshPromise;
  const status = document.getElementById('player-refresh-status');
  const account = CURRENT_PLAYER;
  const finishStatus = (message, state, duration = 2400) => {
    if (box.dataset.account !== account) return;
    box.dataset.refreshState = state;
    box.setAttribute('aria-busy', state === 'loading' ? 'true' : 'false');
    box.setAttribute('aria-label', message || 'Refresh board');
    if (status) { status.textContent = message; status.hidden = !message; status.dataset.state = state; }
    clearTimeout(avatarRefreshTimer);
    if (message && duration > 0) avatarRefreshTimer = setTimeout(() => {
      if (box.dataset.account === account && status) { status.hidden = true; status.textContent = ''; }
      if (box.dataset.account === account) { delete box.dataset.refreshState; box.setAttribute('aria-label', 'Refresh board'); }
    }, duration);
  };
  finishStatus('Refreshing board…', 'loading', 0);
  avatarRefreshPromise = (async () => {
    const ok = await loadBoard({ quiet: true });
    if (box.dataset.account !== account) return false;
    if (ok) { finishStatus('✓ Updated just now', 'success'); return true; }
    finishStatus('Board refresh failed. Try again.', 'error', 4200);
    return false;
  })().catch(() => {
    if (box.dataset.account === account) finishStatus('Board refresh failed. Try again.', 'error', 4200);
    return false;
  }).finally(() => { avatarRefreshPromise = null; });
  return avatarRefreshPromise;
}


export async function loadBoard(opts = {}) {
  // `quiet` is the re-read after the self-heal settles. The table the reader
  // is looking at stays up, nothing is hidden or spun, and the reader's own state (stage
  // picker, checkout) is kept. Everything else -- the single board fetch, the single
  // detail-trust invalidation, the render -- is the same code path as a first load.
  const quiet = opts.quiet === true;
  let spinnerHeld = false;
  boardLoadController?.abort();
  const controller = new AbortController();
  const { signal } = controller;
  boardLoadController = controller;
  if (!quiet) {
    invalidateRowPresentations();
    // (his ruling: "All plots should load closed"): a fresh read -- first load, the
    // reload control, a player switch -- forgets every open plot. Only the quiet re-read
    // during the same page life keeps the reader's open rows ().
    EXPANDED_ROWS.clear();
    EXPANDED_HEIGHTS.clear();
    syncCloseAll();
  }
  const generation = ++BOARD_GENERATION;
  persistBoardLoadSession();
  if (!quiet) setReady({ board: 'none', capture: 'running' });
  // a quiet re-read keeps the open plots, so they must follow the generation
  // bump -- otherwise every one of them fails `owns()` from here on and never reaches
  // `dataReady` (his screenshot: "Still loading workers…" over five empty tiles, forever).
  if (quiet) adoptRowPresentations(generation);
  if (!quiet) {
    closePlanStore(); // phase 2: nothing is written over the kept plans until they are restored
    hideStartPage();
    hideBoardTable();
    startHarvestFill();
    syncPlayerAvatar(CURRENT_PLAYER); // starts alongside the board read, never waits for it
    setBoardLoadingMessage('Reading your plots…');
    boardSpinnerGate.show();
    spinnerHeld = true;
    setPlayerLoading(true);
  }
  try {
    // a caller that has just watched a capture finish must NOT start another
    // one. The post-combine path polls the server's own capture to completion and then
    // only needs the finished snapshot rendered; calling readBoard() there POSTs
    // api/capture/refresh and sweeps the game's API a second time for data that is
    // already on disk. `fromCompletedCapture` is the display-only entry: same render
    // same generation guard, same failure surface, no second read. Every other caller
    // first load, player switch, the reload control -- is untouched and still live.
    const readOnce = () => readBoard((status) => {
      if (!signal.aborted && generation === BOARD_GENERATION) {
        setBoardLoadingMessage(boardLoadingMessage(status), status);
      }
    }, signal, generation);
    let read = opts.fromCompletedCapture === true ? { ok: true } : await readOnce();
    // (critical: "The board read did not finish. Reload to try again."): a read that dies because the server restarted (a deploy, an auto-reload) or the
    // connection dropped is retried ONCE, automatically, instead of asking the player to reload. A read the player aborted, or a second failure, is shown as before.
    if (!read.ok && opts.fromCompletedCapture !== true && read.error?.name !== 'AbortError' && !signal.aborted && generation === BOARD_GENERATION) {
      if (!quiet) setBoardLoadingMessage('Reconnecting…');
      try { await abortableDelay(3000, signal); } catch (_) { /* aborted: the caller moved on */ }
      if (!signal.aborted && generation === BOARD_GENERATION) read = await readOnce();
    }
    if (generation !== BOARD_GENERATION) return false;
    if (!read.ok) {
      // the board is drawn only from live data. A read that failed or never
      // settled draws NO previous snapshot -- the notice says what happened.
      if (!quiet) setReady({ board: 'failed', capture: 'failed' });
      if (!quiet) {
        BOARD = { player: CURRENT_PLAYER, plots: [], totals: {}, notices: [{ ...catalogNotice('capture_failed'), message: 'The board read did not finish. Reload to try again.' }], sortable: [], notice_catalog: NOTICE_CATALOG };
        updateNoLandState();
        render(true); revealBoardTable(false);
      }
      return false;
    }
    if (!quiet) advanceHarvestFill(0.92);
    // with ?plot=<label> the one plot is read and drawn first; the full board is requested at the same moment.
    const plotFirst = !quiet && opts.fromCompletedCapture !== true && PLOT_FIRST_HOOKS?.label && !PLOT_FIRST_HOOKS.used ? PLOT_FIRST_HOOKS : null;
    if (plotFirst) plotFirst.used = true;
    const fullRequest = fetch(`/api/board${playerQuery()}`, { signal });
    let first = null;
    if (plotFirst) {
      fullRequest.catch(() => {}); // a failure is handled where it is awaited below
      first = await drawPlotFirst(plotFirst, signal, generation);
      if (first && spinnerHeld) {
        boardSpinnerGate.hide();
        spinnerHeld = false;
      }
    }
    const resp = await fullRequest;
    if (generation !== BOARD_GENERATION) return false;
    if (resp.status === 503) {
      if (first) setPlotFirstPhase(false);
      if (!quiet) setReady({ board: 'failed' });
      if (!quiet) {
        BOARD = { player: CURRENT_PLAYER, plots: [], totals: {}, notices: [{ ...catalogNotice('capture_failed'), message: 'The latest board data could not be loaded.' }], sortable: [], notice_catalog: NOTICE_CATALOG };
        updateNoLandState(); // before render -- body.no-land hides the table and its columns measured 0 px
        render(true); revealBoardTable(false);
      }
      return false;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const board = await resp.json();
    if (signal.aborted || generation !== BOARD_GENERATION) return false;
    // the plot already on screen keeps its row object across the full-board draw (and, while it is anchored
    // across the settle re-read): an in-flight detail still lands on it, a loaded detail is not dropped and re-read.
    // Same publication token only for the plot-first hand-over; the settle re-read keeps a loaded detail (it came from
    // the plot's own live read) and re-reads nothing for it.
    const keepUid = first ? first.deedUid : (quiet ? PLOT_ANCHOR : null);
    const keptRow = keepUid ? BOARD?.plots?.find((plot) => plot.deed_uid === keepUid) : null;
    const keepDetail = !!keptRow && (first ? (first.token !== null && first.token === boardTokenOf(resp)) : EXPANDED_DATA_LOADED.has(keepUid));
    const keepLoaded = keepDetail && EXPANDED_DATA_LOADED.has(keepUid);
    if (keepDetail) {
      const index = board.plots.findIndex((plot) => plot.deed_uid === keepUid);
      if (index !== -1) board.plots[index] = keptRow;
    }
    const anchorTop = keepUid ? plotRowTop(keepUid) : null;
    BOARD = board;
    reconcilePlotLockPlans(BOARD);
    if (!quiet) advanceHarvestFill(0.97);
    if (generation !== BOARD_GENERATION) return false;
    // LOCAL INVARIANT: replacing BOARD invalidates every per-row detail-trust flag because lean rows contain no workers.
    // (: the one kept row above is the same object, so its flag stays true to it.)
    EXPANDED_DATA_LOADED.clear();
    if (keepLoaded) EXPANDED_DATA_LOADED.add(keepUid);
    NOTICE_CATALOG = BOARD.notice_catalog || NOTICE_CATALOG;
    if (!quiet && !first) resetStagingForFreshBoard();
    // The server's own player (its --player default, when CURRENT_PLAYER was never
    // set) becomes the client's notion of "current" too, so the input field and every
    // subsequent fetch agree with what actually loaded.
    CURRENT_PLAYER = BOARD.player || CURRENT_PLAYER;
    syncPlayerAvatar(CURRENT_PLAYER); // a board opened without ?player= learns its account only here
    syncPlayerInUrl(CURRENT_PLAYER);
    restoreInFlightRecords();
    WORKER_UPGRADE_OPTIONS.clear();
    const input = document.getElementById('player-input');
    if (input && document.activeElement !== input) input.value = CURRENT_PLAYER || '';
    populateFilterOptions();
    restoreSortChoice(); // Retain the saved column-header sorting.
    syncFilterDropdowns();
    syncBoardFilterChrome(); // Look 3: a restored Region choice never fires 'change' on its own
    updateNoLandState(); // must run BEFORE render -- a stale body.no-land (after a no-land player) hid the
    // table while the (since removed, ) column lock measured it: every column at 0 px.
    if (first) setPlotFirstPhase(false);
    render(true); // v0.4.13: this IS a dataset change -- see render()'s own comment
    // same task as the render, so no frame shows the board scrolled to the top under the opened plot.
    if (keepUid && (first || PLOT_ANCHOR === keepUid)) {
      try { PLOT_FIRST_HOOKS?.anchor?.(keepUid, anchorTop); } catch (_) { /* the plot is still drawn; only its place may move */ }
    }
    revealBoardTable();
    setReady({ board: 'full' });
    syncWorkersReady();
    if (!quiet) restoreStagePlans(); // phase 2: after first paint, never before
    // an unresolved earlier wallet request resolves itself from here on, with no
    // action from the maintainer. A page load is enough to clear an account that was stuck.
    if (!quiet) {
      import('./broadcast-gate.js')
        .then(({ startPendingBroadcastAutoResolve }) => startPendingBroadcastAutoResolve(CURRENT_PLAYER || BOARD?.player))
        .catch(() => {});
    }
    for (const deedUid of EXPANDED_ROWS) {
      if (generation !== BOARD_GENERATION) break;
      if (EXPANDED_DATA_LOADED.has(deedUid)) continue; // a kept, loaded plot is not read again
      const row = BOARD.plots.find((plot) => plot.deed_uid === deedUid);
      if (row) loadPlotDetail(row, generation);
    }
    if (read.boardReadyEarly) {
      // the table above was rendered from the snapshot the moment it was on disk
      // while the `gaps` self-heal batch was still running. Wait for that batch and read
      // the board once more -- the same load path, quietly -- so healed rows appear without
      // a second capture and without hiding the table the reader is already looking at.
      // Skipped if the reader has started staging or picking in the meantime.
      // the veil must go NOW, with the table -- `finally` below only runs after the
      // tail, which left the finished board visible under a translucent "Reading your
      // cards…" for up to 22 s on a whale account (his question, investigation 7).
      if (spinnerHeld) {
        boardSpinnerGate.hide();
        spinnerHeld = false;
        setPlayerLoading(false);
      }
      const settle = await waitForCaptureToSettle(signal);
      if (generation !== BOARD_GENERATION) return false;
      if (!settle.settled) { releasePlotAnchor(); setReady({ capture: 'failed' }); }
      // The first reveal can precede plan restoration; this signal is the hand-off after the live capture is final.
      if (settle.settled) window.dispatchEvent(new CustomEvent('ceres:board-capture-settled'));
      // since v0.9.360 the settle phase only runs the 8 retries, so the snapshot
      // usually does not change -- and the quiet re-read fetched the FULL board again
      // (14.57 MB, 25.7 s on a whale). The snapshot token remembered at the interim render
      // is compared with the one the final status reports: unchanged → no reload at all
      // changed → the same quiet reload as before. The guards stay.
      const tokenUnchanged = read.snapshotToken != null && settle.token != null && settle.token === read.snapshotToken;
      if (settle.settled && !tokenUnchanged && !S.CERES_PICKER && CERES_STAGE.length === 0 && !S.CERES_CHECKOUT_OPEN) {
        const reread = await loadBoard({ fromCompletedCapture: true, quiet: true });
        if (reread) settleReadySignal(true);
        return reread;
      }
      if (settle.settled) settleReadySignal(true);
      return true;
    }
    if (!quiet) settleReadySignal(true);
    return true;
  } catch (err) {
    if (signal.aborted || err?.name === 'AbortError') return false;
    if (!quiet && document.documentElement.classList.contains('ceres-plot-first')) {
      // the full board never came; the plot stays, the board-wide chrome stays hidden, the notice says why.
      releasePlotAnchor();
      setReady({ board: 'failed' });
    }
    if (BOARD) {
      appendBoardNotice(catalogNotice('capture_failed')); // once, not one row per failed read
      render();
    }
    return false;
  } finally {
    const ownsLoad = boardLoadController === controller && generation === BOARD_GENERATION;
    if (boardLoadController === controller) boardLoadController = null;
    // Every show() owns one gate reference, including a load aborted by its successor.
    if (spinnerHeld) boardSpinnerGate.hide();
    if (!quiet && ownsLoad) setPlayerLoading(false);
  }
}

// the page load's capture (and its re-read, if any) is done: the opened plot is no longer held in place.
function settleReadySignal(settled) {
  releasePlotAnchor();
  syncWorkersReady();
  setReady({ capture: settled ? 'settled' : 'failed' });
}

// settle any running capture, then re-read /api/board quietly (the lean board
// no new capture; stage, picker, checkout and previews are kept). Used when a stake
// preview finds the server holds a newer capture than the page's board.
export async function adoptLatestCapture() {
  const player = CURRENT_PLAYER;
  const generation = BOARD_GENERATION;
  await waitForCaptureToSettle();
  if (player !== CURRENT_PLAYER || generation !== BOARD_GENERATION) return BOARD?.generated_at || null;
  await loadBoard({ fromCompletedCapture: true, quiet: true });
  return BOARD?.generated_at || null;
}

// a fetch() that throws (server restart
// dropped connection) is retried for ~31.5 s with backoff before the caller sees the
// failure; non-2xx responses are never retried. Shared by the board read here and by
// rows.js::loadPlotDetail. `onRetry(attempt)` runs before each wait; returning false
// stops the loop and rethrows the original error (stale generation, closed row).
export const NETWORK_RETRY_DELAYS_MS = [500, 1000, 2000, 3000, 5000, 5000, 5000, 5000, 5000];
function abortableDelay(ms, signal) {
  if (!signal) return new Promise((resolve) => setTimeout(resolve, ms));
  if (signal.aborted) return Promise.reject(signal.reason || new DOMException('The request was aborted.', 'AbortError'));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
      reject(signal.reason || new DOMException('The request was aborted.', 'AbortError'));
    };
    signal.addEventListener('abort', onAbort, { once: true });
  });
}
export async function fetchWithNetworkRetry(url, init, { onRetry = null } = {}) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await fetch(url, init);
    } catch (error) {
      if (error?.name === 'AbortError' || init?.signal?.aborted || attempt >= NETWORK_RETRY_DELAYS_MS.length) throw error;
      if (onRetry && onRetry(attempt) === false) throw error;
      await abortableDelay(NETWORK_RETRY_DELAYS_MS[attempt], init?.signal);
      if (onRetry && onRetry(attempt + 1, true) === false) throw error;
    }
  }
}

export async function readBoard(onStatus, signal = undefined, loadGeneration = undefined) {
  let response;
  try {
    const refreshPath = loadGeneration === undefined
      ? '/api/capture/refresh'
      : `/api/capture/refresh?load_id=${encodeURIComponent(BOARD_LOAD_SESSION_ID)}&load_generation=${loadGeneration}`;
    response = await fetchWithNetworkRetry(playerQueryAppend(refreshPath), { method: 'POST', signal });
  } catch (error) {
    return { ok: false, error };
  }
  if (!response.ok && response.status !== 409) {
    return { ok: false, error: new Error(`HTTP ${response.status}`) };
  }
  const startedAt = Date.now();
  let estimatedSeconds = null; // the server's own estimate steers the poll cadence
  for (let attempt = 0; Date.now() - startedAt < CAPTURE_STATUS_MAX_WAIT_MS; attempt += 1) {
    try {
      // `wait` makes the server long-poll for up to this attempt's cadence
      // the request itself returns the moment the capture stops being busy, instead of
      // this loop sleeping the full interval AFTER a status read that already saw it
      // finish. No client-side setTimeout between attempts any more.
      const waitMs = captureStatusPollInterval(attempt, Date.now() - startedAt, estimatedSeconds);
      const requestStartedAt = Date.now();
      // wait_for defaults to "board_ready" server-side -- the early-render exit below.
      const statusResponse = await fetchWithNetworkRetry(playerQueryAppend(`/api/capture/status?wait=${waitMs}`), { signal });
      if (!statusResponse.ok) return { ok: false, error: new Error(`HTTP ${statusResponse.status}`) };
      const status = await statusResponse.json();
      if (signal?.aborted) return { ok: false, error: new DOMException('The board load was aborted.', 'AbortError') };
      onStatus?.(status);
      if (Number.isFinite(status.estimated_seconds)) estimatedSeconds = status.estimated_seconds;
      if (status.state === 'running' || status.state === 'queued') {
        // `queued` polls like `running` while the superseded worker is being reaped.
        // the merged snapshot is on disk before the self-heal batch runs. Render
        // on it now; loadBoard() re-reads once the run has actually settled.
        // the snapshot token of this interim render is remembered so the settle
        // phase can tell whether the board changed at all.
        if (status.board_ready === true) return { ok: true, boardReadyEarly: true, snapshotToken: status.latest_snapshot?.token ?? null };
        const elapsed = Date.now() - requestStartedAt;
        if (elapsed < CAPTURE_STATUS_FLOOR_MS) await abortableDelay(waitMs - elapsed, signal);
        continue;
      }
      return status.state === 'failed'
        ? { ok: false, error: status.error || 'read failed' }
        : { ok: true };
    } catch (error) {
      return { ok: false, error };
    }
  }
  return { ok: false, error: new Error('read did not settle within 30 minutes') };
}

// after an early render, wait quietly for the capture run to settle. Resolves
// `{ settled, token }` -- `settled` true when it finished cleanly, false on failure, timeout
// or a transport error; `token` () is the final status's `latest_snapshot.token`, so
// the caller can skip the quiet reload when the snapshot did not change.
// the only sign of the self-heal tail behind an unveiled board -- one muted line
// written from the status poll the page already makes; cleared when the capture settles.
// same cadence as readBoard(), steered by the server's estimate.
async function waitForCaptureToSettle(signal = undefined) {
  const startedAt = Date.now();
  let estimatedSeconds = null;
  for (let attempt = 0; Date.now() - startedAt < CAPTURE_STATUS_MAX_WAIT_MS; attempt += 1) {
    try {
      // same long-poll as readBoard(), but `wait_for=settled` -- this
      // loop's own exit condition is `state !== 'running'`, never `board_ready`, so the
      // server must ignore board_ready here too (see `_capture_status_busy` server-side)
      // or an interim publish turns every call instant and this becomes a busy spin.
      const waitMs = captureStatusPollInterval(attempt, Date.now() - startedAt, estimatedSeconds);
      const requestStartedAt = Date.now();
      const statusResponse = await fetch(playerQueryAppend(`/api/capture/status?wait=${waitMs}&wait_for=settled`), { signal });
      if (!statusResponse.ok) return { settled: false, token: null };
      const status = await statusResponse.json();
      if (signal?.aborted) return { settled: false, token: null };
      if (Number.isFinite(status.estimated_seconds)) estimatedSeconds = status.estimated_seconds;
      if (status.state !== 'running') return { settled: status.state !== 'failed', token: status.latest_snapshot?.token ?? null };
      const elapsed = Date.now() - requestStartedAt;
      if (elapsed < CAPTURE_STATUS_FLOOR_MS) await abortableDelay(waitMs - elapsed, signal);
    } catch (error) {
      return { settled: false, token: null };
    }
  }
  return { settled: false, token: null };
}

export async function switchPlayer(name) {
  name = normalizePlayer(name);
  const input = document.getElementById('player-input');
  if (input && name && input.value !== name) input.value = name;
  if (!name || name === CURRENT_PLAYER) return;
  closePlanStore(); // phase 2: the old account's plans are never written under the new one
  CURRENT_PLAYER = name;
  window.dispatchEvent(new CustomEvent('ceres:player-changed', { detail: { player: name } }));
  syncPlayerInUrl(name); // URL + remembered player
  await loadBoard();
}

// the last sort choice, per account, in this browser -- a view preference, never game data ( untouched).
function sortStoreKey(account) { return account ? `ceres:sort:v1:${String(account).toLowerCase()}` : null; }
export function saveSortChoice() {
  const key = sortStoreKey(CURRENT_PLAYER || BOARD?.player);
  if (!key) return;
  try { localStorage.setItem(key, JSON.stringify({ key: S.SORT_KEY, dir: S.SORT_DIR })); } catch (error) { /* a view preference is never worth an error */ }
}
function restoreSortChoice() {
  const key = sortStoreKey(CURRENT_PLAYER || BOARD?.player);
  if (!key) return;
  let stored = null;
  try { stored = JSON.parse(localStorage.getItem(key) || 'null'); } catch (error) { stored = null; }
  if (!stored || typeof stored.key !== 'string') return;
  if (stored.key !== 'plot' && !BOARD?.sortable?.includes(stored.key)) return; // a column this board does not offer
  S.SORT_KEY = stored.key;
  S.SORT_DIR = stored.dir === 1 ? 1 : -1;
}

// the Rarity filter lists "any" then the tiers highest first, whatever
// order the plots arrived in and however many accounts have been loaded into this select.
const RARITY_FILTER_ORDER = ['mythic', 'legendary', 'epic', 'rare', 'common'];
function orderRarityOptions() {
  const sel = document.getElementById('rarity-filter');
  const rank = (o) => (o.value === '' ? -1 : (RARITY_FILTER_ORDER.indexOf(o.value.toLowerCase()) + 1 || RARITY_FILTER_ORDER.length + 1));
  const opts = Array.from(sel.options).sort((a, b) => rank(a) - rank(b));
  const values = selectValues(sel); // keep every chosen rarity across the reorder
  for (const o of opts) sel.appendChild(o);
  setSelectValues(sel, values);
}

function populateFilterOptions() {
  const fill = (id, values) => {
    const sel = document.getElementById(id);
    const existing = new Set(Array.from(sel.options).map((o) => o.value));
    for (const v of values) {
      if (v && !existing.has(v)) {
        const opt = document.createElement('option');
        opt.value = v;
        opt.textContent = boardFilterLabel(v);
        sel.appendChild(opt);
        existing.add(v);
      }
    }
  };
  populateRegionOptions(); // rebuilt from this account's plots, never accumulated across accounts
  fill('rarity-filter', new Set(BOARD.plots.map((p) => p.rarity)));
  orderRarityOptions();
  fill('terrain-filter', new Set(BOARD.plots.map((p) => p.terrain)));
  fill('resource-filter', new Set(BOARD.plots.map((p) => p.resource)));
  fill('status-filter', new Set(BOARD.plots.map((p) => p.plot_status)));
  fill('status-filter', ['fire', 'water', 'earth', 'life', 'death', 'dragon'].map((element) => `magical:${element}`));
  populateBadgeOptions();
}

export function matchesFilters(row) {
  if (S.CERES_NAV_INCLUDE && row.deed_uid === S.CERES_NAV_INCLUDE) return true; // a jumped-to plot outside the filters
  const text = document.getElementById('text-filter').value.trim().toLowerCase();
  if (text) {
    const workers = Array.isArray(row.workers) ? row.workers : [];
    const names = workers.length ? workers.map((worker) => worker.name) : (row.worker_names || []);
    const hay = `${row.plot} ${names.join(' ')}`.toLowerCase();
    if (!hay.includes(text) && !plotIdMatches(row.plot, text)) return false;
  }
  // each filter is a list of chosen values
  // a row passes a filter when it matches ANY of them (none chosen = Any), and every filter must pass.
  const passes = (id, test) => { const values = boardFilterValues(id); return !values.length || values.some(test); };
  if (!passes('region-filter', (region) => plotRegion(row.plot) === region)) return false;
  if (!passes('rarity-filter', (rarity) => row.rarity === rarity)) return false;
  if (!passes('terrain-filter', (terrain) => row.terrain === terrain)) return false;
  if (!passes('resource-filter', (resource) => row.resource === resource)) return false;
  if (!passes('status-filter', (status) => (status.startsWith('magical:')
    ? row.plot_status === 'magical' && String(row.magic_type || '').toLowerCase() === status.split(':')[1]
    : row.plot_status === status))) return false;
  if (!passes('badge-filter', (badge) => rowHasBadge(row, badge))) return false;
  if (document.getElementById('staged-only')?.checked && !plotHasStagedChange(row.deed_uid)) return false;
  return true;
}
// a typed id token is
// compared per dash-separated segment as integers, prefix-matching the row's region-tract-plot sequence
// so leading zeros never matter. Examples against row.plot "41-5-94"
// "10" -> false, "41" / "041" / "0041" -> true, "41-5" / "041-05" -> true, "41-5-94" / "041-05-094" -> true
// "41-6" -> false, "041-05-0" -> false (plot 0 != 94), "" / "abc" -> false (name matching handles text).
// a trailing dash while still typing ("041-") counts like "041" -- measured "41-" 22 rows (substring)
// vs "041-" 0 rows before this.
export function plotIdMatches(plot, text) {
  if (!/^\d+(-\d+)*-?$/.test(text)) return false;
  const typed = text.replace(/-$/, '').split('-').map(Number);
  const own = String(plot ?? '').split('-').map(Number);
  if (typed.length > own.length) return false;
  return typed.every((n, i) => n === own[i]);
}
// a plot with any staged change -- incoming (ready or waiting) or a card of its own moving out.
export function plotHasStagedChange(deedUid) {
  return CERES_STAGE.some((entry) => !entry._synthetic && (entry.deed_uid === deedUid || entry._source_deed_uid === deedUid));
}
export function stagedPlotCount() {
  const deeds = new Set();
  CERES_STAGE.forEach((entry) => { if (entry._synthetic) return; deeds.add(entry.deed_uid); if (entry._source_deed_uid) deeds.add(entry._source_deed_uid); });
  if (!deeds.size) return 0;
  const onBoard = new Set((BOARD?.plots || []).map((plot) => plot.deed_uid)); // one index, not a plot scan per deed
  return [...deeds].filter((deed) => onBoard.has(deed)).length;
}

// Descending comparator for tie-break keys below -- FIXED direction, independent of
// SORT_DIR/the primary column's own toggle (Rarity sort, batch 13 below): clicking
// Rarity twice to reverse it to ascending should still show the highest producers
// first WITHIN each rarity tier, not lowest-first: the owner asked for "Harvest PP as
// secondary" descending, not "secondary, flipping with the primary direction."
// Honest nulls sort last either way (-Infinity), never coerced to 0.
function cmpDescFixed(x, y) {
  if (x === null || x === undefined) x = -Infinity;
  if (y === null || y === undefined) y = -Infinity;
  if (x < y) return 1;
  if (x > y) return -1;
  return 0;
}

export function sortRows(rows) {
  if (S.SORT_KEY === 'plot') return sortByPlot(rows);
  return [...rows].sort((a, b) => {
    if (S.SORT_KEY === 'lock_state') {
      const av = plotLockSortState(a), bv = plotLockSortState(b);
      if (av.known !== bv.known) return av.known ? -1 : 1;
      if (av.locked !== bv.locked) return (Number(av.locked) - Number(bv.locked)) * S.SORT_DIR;
      return 0;
    }
    if (S.SORT_KEY === 'totem_boost' || S.SORT_KEY === 'title_boost') {
      const kind = S.SORT_KEY === 'totem_boost' ? 'totem' : 'title';
      const value = (row) => {
        const bonus = row._live_bonus || row.bonus;
        if (!bonus || !Object.hasOwn(bonus, kind)) return null;
        const item = bonus[kind];
        if (item === null) return 0;
        const raw = item?.boost;
        return raw !== null && raw !== undefined && raw !== '' && Number.isFinite(Number(raw)) ? Number(raw) : null;
      };
      const av = value(a), bv = value(b);
      if (av === null && bv !== null) return 1;
      if (bv === null && av !== null) return -1;
      if (av !== bv) return (av - bv) * S.SORT_DIR;
      return String(a.plot).localeCompare(String(b.plot), undefined, { numeric: true });
    }
    // review fix: the three material-input columns are mostly dash rows (5,900 of
    // 6,300 plots have no such recipe) -- unlike every other numeric column here, a null
    // must sort LAST in BOTH directions (ascending starts at the smallest REAL value, not
    // a wall of dashes), never flip to first under descending like the generic -Infinity
    // fallback below would. Early-returns, same shape as the totem/title branch above.
    if (S.SORT_KEY === 'wood_cost_per_hour' || S.SORT_KEY === 'stone_cost_per_hour' || S.SORT_KEY === 'iron_cost_per_hour') {
      const av = a[S.SORT_KEY], bv = b[S.SORT_KEY];
      const aNull = av === null || av === undefined;
      const bNull = bv === null || bv === undefined;
      if (aNull && bNull) return 0;
      if (aNull) return 1;
      if (bNull) return -1;
      if (av < bv) return -1 * S.SORT_DIR;
      if (av > bv) return 1 * S.SORT_DIR;
      return 0;
    }
    let av = a[S.SORT_KEY];
    let bv = b[S.SORT_KEY];
    if (S.SORT_KEY === 'dec_needed') { av = a.dec.needed; bv = b.dec.needed; }
    if (S.SORT_KEY === 'food_per_hour') { av = a.grain_cost_per_hour; bv = b.grain_cost_per_hour; }
    if (S.SORT_KEY === 'dec_shortfall') { av = a.dec.shortfall; bv = b.dec.shortfall; }
    if (S.SORT_KEY === 'open_slots') { av = a.capacity.open_slots; bv = b.capacity.open_slots; }
    if (S.SORT_KEY === 'worker_count') { av = a.capacity.worker_count; bv = b.capacity.worker_count; }
    if (av === null || av === undefined) av = -Infinity;
    if (bv === null || bv === undefined) bv = -Infinity;
    if (av < bv) return -1 * S.SORT_DIR;
    if (av > bv) return 1 * S.SORT_DIR;
    // Rarity sort only: three-level sort (the owner, 2026-08-21 batch 13: "sorting by
    // rarity should put Harvest PP as secondary and Type as tertiary sorting
    // criteria"). Rarity alone leaves large ties -- 130 plots span only four rarity
    // values, so a plain rarity sort produces four undifferentiated blocks in
    // arbitrary (insertion) order; these two extra keys make it readable. Scoped to
    // THIS column deliberately -- every other column keeps its existing single-key
    // behaviour; multi-level sorting was not requested for them.
    // 2. Harvest PP, descending (`a.harvest_pp`/`b.harvest_pp` -- raw, full
    // precision values, same field the Harvest PP column itself sorts by).
    // 3. Type, via the existing `plot_status_rank` field (Kingdom=3 > Occupied=2 >
    // Magical=1 > Natural=0 -- already computed in ceres/board.py). Kingdom
    // (Keep/Castle) now ranks HIGHEST, per the owner's explicit ruling
    // ("the Keep/Castle type is the highest") made once player0001's account
    // surfaced real kingdom plots -- supersedes the earlier UNCONFIRMED -1
    // placeholder from board-polish batch 13.
    if (S.SORT_KEY === 'rarity_rank') {
      const byHarvest = cmpDescFixed(a.harvest_pp, b.harvest_pp);
      if (byHarvest !== 0) return byHarvest;
      return cmpDescFixed(a.plot_status_rank, b.plot_status_rank);
    }
    return 0;
  });
}

// Plot-column sort: the owner confirmed (2026-08-21) "sorting by the 'Plot' column
// aka territory region" -- a literal plot-ID sort would never tie (all 130 plot IDs
// are unique) so the tie-breaks below would be dead code; territory/region grouping
// is what actually makes the sort meaningful. Groups by territory (alphabetical
// no canonical territory order exists anywhere else in this codebase), then region
// within territory (alphabetical -- keeps Pristine Northwest's two regions
// PR-PNW-1 and PR-PNW-8, grouped and ordered rather than interleaved), then the
// three tie-breaks the owner specified: Harvest PP descending, Rarity, Type -- reusing
// the same fixed-direction `cmpDescFixed` helper the Rarity sort above already uses
// so a click on Plot always shows the biggest producers/highest rarity first within
// each territory/region regardless of SORT_DIR.
// the plot id
// "65-9-83" is region-tract-plot; sort by those numbers. First choice ascending; the header click flips it.
function plotNumbers(row) {
  const parts = String(row.plot ?? '').split('-').map((part) => Number.parseInt(part, 10));
  return [0, 1, 2].map((i) => (Number.isFinite(parts[i]) ? parts[i] : Number.POSITIVE_INFINITY));
}
function sortByPlot(rows) {
  const dir = S.SORT_DIR === 1 ? -1 : 1; // SORT_DIR starts at -1 -> ascending
  return [...rows].sort((a, b) => {
    const pa = plotNumbers(a);
    const pb = plotNumbers(b);
    for (let i = 0; i < 3; i += 1) if (pa[i] !== pb[i]) return (pa[i] < pb[i] ? -1 : 1) * dir;
    return 0;
  });
}
