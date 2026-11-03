// one explicit, page-owned statement of what is on screen, so the
// rig and the browser tests wait on facts instead of DOM guesses. `window.__ceresReady` is a frozen snapshot replaced on
// every change; `ceres:ready` (window event, detail = the same snapshot) fires with each change.
// page 'board' | 'cards'
// board 'none' nothing drawn yet (loading, start page)
// 'plot' only the plot named by ?plot= is drawn (plot-first phase; board-wide values not shown yet)
// 'full' the whole board is drawn (totals, filters, ranks are board-wide values)
// 'failed' the read failed; the notice says why
// workers true when no OPEN plot is still loading its workers (every open plot is loaded or shows its error)
// plot label of the plot the address opened, once its editor band is drawn with its workers; else null
// capture 'running' | 'settled' | 'failed' -- the page load's live capture, including its self-heal tail
// market null (not the Market) | 'loading' | 'settled' (the Market grid is filled, no skeletons)
// ready the page is done: board 'full' + workers + capture settled (+ plot when the address named one)
// or on /cards: the view is drawn and, in Market mode, market 'settled'
// at ms since navigation start at which each state was first reached (boardPlot, boardFull, plot, workers
// settled, market)
// the state lives on window, not in this module, so a second instance of the module (same file loaded under
// another URL -- a stale cached copy, or a test that serves the whole graph as one app.js and then dynamic-imports the
// real files) joins the page's one state instead of resetting it to 'none'/'running'. With modulepreload covering the
// whole graph (1.1.2) such a second instance evaluates within milliseconds of the board's 'full' and wiped it.
const STATE = window.__ceresReadyState || (window.__ceresReadyState = {
  version: 1,
  page: 'board',
  board: 'none',
  workers: true,
  plot: null,
  plotWanted: null,
  capture: 'running',
  market: null,
  view: false,
  ready: false,
  at: {},
});

function now() {
  return Math.round(typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now());
}

function computeReady(state) {
  if (state.page === 'cards') return state.view === true && (state.market === null || state.market === 'settled');
  if (state.board === 'failed') return true;
  return state.board === 'full' && state.workers === true && state.capture !== 'running'
    && (!state.plotWanted || state.plot === state.plotWanted || state.plotMissing === true);
}

function publish() {
  STATE.ready = computeReady(STATE);
  if (STATE.ready && STATE.at.ready === undefined) STATE.at.ready = now();
  const snapshot = Object.freeze({ ...STATE, at: Object.freeze({ ...STATE.at }) });
  window.__ceresReady = snapshot;
  try { window.dispatchEvent(new CustomEvent('ceres:ready', { detail: snapshot })); } catch (_) { /* no CustomEvent: the object still stands */ }
  return snapshot;
}

const STAMPS = { board: { plot: 'boardPlot', full: 'boardFull' }, capture: { settled: 'settled' }, market: { settled: 'market' } };

export function setReady(patch) {
  let changed = false;
  for (const [key, value] of Object.entries(patch || {})) {
    if (STATE[key] === value) continue;
    STATE[key] = value;
    changed = true;
    const stamp = STAMPS[key]?.[value];
    if (stamp && STATE.at[stamp] === undefined) STATE.at[stamp] = now();
    if (key === 'plot' && value && STATE.at.plot === undefined) STATE.at.plot = now();
    if (key === 'workers' && value === true && STATE.board !== 'none' && STATE.at.workers === undefined) STATE.at.workers = now();
  }
  return changed || !window.__ceresReady ? publish() : window.__ceresReady;
}

export function readyState() {
  return window.__ceresReady || publish();
}

if (!window.__ceresReady) publish(); // a second instance never re-announces; the first one already did
