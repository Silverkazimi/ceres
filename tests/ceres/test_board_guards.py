""": sorting is interactive as soon as the early board is revealed."""

import json
import re
import subprocess

import pytest

from .vm_context import STATIC, read_entry_source, run_in_module_context


_EARLY_SORT_BODY = r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
function element(key) {
  return {
    dataset: {key}, hidden: true, value: '', textContent: '', listeners: {},
    classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }},
    style: {setProperty() {}, removeProperty() {}},
    addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); },
    removeEventListener() {},
    //  start page (/): loadBoard() calls hideStartPage()/showStartPage(),
    // which touch these on whatever document.getElementById(...) returns.
    remove() {}, setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    appendChild() {}, prepend() {}, replaceChildren() {}, focus() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    cloneNode() { return null; }, closest() { return null; }, insertAdjacentHTML() {},
  };
}
const headers = __HEADER_KEYS__.map(element);
const elements = new Map();
document.getElementById = (id) => {
  if (!elements.has(id)) elements.set(id, element());
  return elements.get(id);
};
document.querySelectorAll = (selector) =>
  selector === '#plot-table thead [data-key]' ? headers : [];
window.scrollTo = () => {};
initFilterDropdowns = updateHeaderHeightVar = () => {};
populateFilterOptions = populateSortOptions = syncFilterDropdowns = () => {};
renderCheckoutSurface = restoreInFlightRecords = ensureHazardOverlay = () => {};
renderTotalsStrip = prefetchIdleCardsCache = () => {};
matchesFilters = () => true;
// / fixture gap: the sandbox publishes one global per bare top-level
// declaration name, but four DIFFERENT modules each declare their own private,
// unrelated "function render(...)" (table.js's real one plus unexported local ones in
// harvest-modal.js/regional-dec.js/fill-plot.js); whichever evaluates last wins the
// global, so board.js's own bare `render(true)` call can silently invoke someone else's
// render and never touch table.js's CURRENT_ROWS. Sidestep it: recompute the same
// filter+sort table.js's real render() would (board.js's sortRows/matchesFilters are each
// declared exactly once, so those two names ARE reliably the real ones) instead of
// depending on CURRENT_ROWS being populated by whichever "render" actually ran.
const orderNow = () => sortRows(BOARD.plots.filter(matchesFilters)).map((row) => row.deed_uid).join(',');
const baseHeader = headers.find((header) => header.dataset.key === 'base_pp');
let resolveReveal, resolveTail;
const revealed = new Promise((resolve) => { resolveReveal = resolve; });
const pendingTail = new Promise((resolve) => { resolveTail = resolve; });
const originalReveal = revealBoardTable;
let listenersAtReveal;
revealBoardTable = () => {
  originalReveal();
  listenersAtReveal = headers.map((header) => header.listeners.click?.length || 0);
  resolveReveal();
};
let statusRequests = 0;
let secondInit = false;
fetch = async (url) => {
  const path = String(url).split('?')[0];
  if (path === '/api/capture/refresh') return {ok: true, status: 202};
  if (path === '/api/capture/status') {
    statusRequests += 1;
    if (secondInit) return {ok: true, json: async () => ({state: 'done'})};
    if (statusRequests > 1) return pendingTail;
    return {ok: true, json: async () => ({state: 'running', board_ready: true, phase: 'gaps'})};
  }
  if (path === '/api/board') return {ok: true, status: 200, json: async () => ({
    player: 'player0005', sortable: ['harvest_pp', 'base_pp'], notices: [], notice_catalog: {},
    plots: [
      {deed_uid: 'A', harvest_pp: 20, base_pp: 1, badges: []},
      {deed_uid: 'B', harvest_pp: 10, base_pp: 2, badges: []},
    ],
  })};
  throw new Error('unexpected request: ' + url);
};
const watchdog = setTimeout(() => { throw new Error('early-sort guard did not finish'); }, 1000);
(async () => {
  let initDone = false;
  const initializing = init().then(() => { initDone = true; });
  assert(BOARD === null, 'fixture must begin before the first board arrives');
  baseHeader.listeners.click?.[0]();
  assert(S.SORT_KEY === 'harvest_pp', 'pre-load numeric click must be ignored');
  await revealed;
  assert(headers.length === 19 && listenersAtReveal.every((count) => count === 1),
    'expected 19/19 header click listeners at reveal: ' + listenersAtReveal);
  assert(statusRequests === 2 && !initDone, 'self-heal tail must still be pending');
  assert(orderNow() === 'A,B', 'initial real render must sort by harvest PP');
  baseHeader.listeners.click[0]();
  assert(orderNow() === 'B,A', 'header click did not re-sort during the self-heal tail');
  assert(!initDone, 'sort assertions must precede tail completion');
  resolveTail({ok: true, json: async () => ({state: 'failed'})});
  await initializing;
  secondInit = true;
  await init();
  await init();
  assert(headers.every((header) => header.listeners.click?.length === 1),
    'expected exactly one click listener per header after repeated init');
})().then(() => clearTimeout(watchdog), (error) => {
  clearTimeout(watchdog);
  setTimeout(() => { throw error; }, 0);
});
"""


def _early_sort_body() -> str:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    head = html.split('id="table-head-sticky"', 1)[1].split("</thead>", 1)[0]  # #plot-table's own sticky thead
    keys = re.findall(r'<(?:th|button)\b[^>]*\bdata-key="([^"]+)"', head)
    assert len(keys) == 19  # 17 cells, with two sort controls in Totem/Title.
    return _EARLY_SORT_BODY.replace("__HEADER_KEYS__", json.dumps(keys))


# (v0.9.225, /): init() only calls loadBoard() when a player is already
# in the URL, otherwise it shows the start page and never touches the table/sort wiring this
# guard exercises. The sandbox's default location.search is '', so every run here needs a
# player in it.
_WITH_PLAYER_IN_URL = (
    "window.location.search = '?player=player0005';"
    " context.history = {replaceState() {}};"  # syncPlayerInUrl() calls history.replaceState (/)
    " document.documentElement.classList = {add() {}, remove() {}, toggle() {}, contains() { return false; }};"
)


def test_sorting_is_ready_at_reveal_while_self_heal_is_pending() -> None:
    run_in_module_context(_early_sort_body(), extra_context_js=_WITH_PLAYER_IN_URL)


def test_sort_binding_below_initial_load_injection_goes_red() -> None:
    # (v0.9.48x) removed the separate #sort-select dropdown: sorting is header-click
    # only now, bound in the querySelectorAll('#plot-table thead [data-key]') forEach block
    # below (/) -- that block is what must run before the first loadBoard().
    source = read_entry_source()
    # (table.js) added a same-selector forEach (syncSortAriaState) that is evaluated
    # before app.js in the module graph, so the literal anchor is no longer unique across the
    # whole-graph source; rindex() picks app.js's own sort-binding block (app.js is ENTRY and
    # evaluated last per read_entry_source()'s post-order DFS).
    start = source.rindex("document.querySelectorAll('#plot-table thead [data-key]').forEach((control) => {")
    end = source.index("  document.getElementById('close-all-plots')", start)
    binding = source[start:end]
    load_line = "  if (CURRENT_PLAYER) await loadBoard(); else showStartPage(); // no player in the URL -> start page, no table\n"
    # The binding block and the loadBoard() call are no longer adjacent (other init() wiring
    # close-all-plots, open-all-plots, resize listeners -- sits between them since 's rework)
    # so move the block from its own spot to directly ahead of loadBoard() instead of swapping two
    # halves of one contiguous anchor (/).
    assert source.count(binding) == 1 and source.count(load_line) == 1
    mutated = source.replace(binding, "", 1).replace(load_line, load_line + binding, 1)
    with pytest.raises(subprocess.CalledProcessError) as failure:
        run_in_module_context(_early_sort_body(), source=mutated, extra_context_js=_WITH_PLAYER_IN_URL)
    assert "expected 19/19 header click listeners at reveal" in failure.value.stderr


def test_repeated_init_sort_binding_marker_removal_goes_red() -> None:
    source = read_entry_source()
    marker = "    control.dataset.sortBound = 'true';\n"
    assert source.count(marker) == 1
    mutated = source.replace(marker, "", 1)
    with pytest.raises(subprocess.CalledProcessError) as failure:
        run_in_module_context(_early_sort_body(), source=mutated, extra_context_js=_WITH_PLAYER_IN_URL)
    assert "expected exactly one click listener per header after repeated init" in failure.value.stderr
