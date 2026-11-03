""": a board read whose capture status stops advancing tells the player so, calmly, and keeps trying.

The loader polls /api/capture/status for up to 30 minutes. When the game API stalls, the status keeps coming back
unchanged and the page showed "Reading your plots…" with no word for up to half an hour. After
CAPTURE_STATUS_STALL_NOTICE_MS without any change in the status's progress fields the loader text gains one plain
sentence; the next status that changes a progress field removes it. The 30-minute ceiling and the poll cadence stay.

No real time passes: Date.now and setTimeout are replaced by one fake clock, and every fake long-poll status answer
advances that clock by the `wait` the page asked for (what the server's long-poll does while the capture is busy).
"""

from .vm_context import run_in_module_context

NOTICE = "The game server is slow or not answering. Ceres keeps trying."

CLOCK = r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
const __realSetTimeout = setTimeout;
let __clock = 1_000_000;
let __timerId = 0;
let __timers = [];
Date.now = () => __clock;
setTimeout = (fn, ms = 0) => { const id = ++__timerId; __timers.push({ id, due: __clock + Math.max(0, Number(ms) || 0), fn }); return id; };
clearTimeout = (id) => { __timers = __timers.filter((timer) => timer.id !== id); };
requestAnimationFrame = (fn) => setTimeout(() => fn(__clock), 16);
cancelAnimationFrame = (id) => clearTimeout(id);
function advance(ms) {
  const until = __clock + ms;
  for (;;) {
    __timers.sort((a, b) => a.due - b.due);
    const next = __timers[0];
    if (!next || next.due > until) break;
    __timers.shift();
    __clock = Math.max(__clock, next.due);
    next.fn();
  }
  __clock = until;
}
const waitOf = (url) => Number((/[?&]wait=(\d+)/.exec(String(url)) || [])[1] || 0);
"""

# loadBoard() end to end: the text node the player reads is the assertion target, not a helper's return value.
DOM = r"""
function element() {
  return {
    dataset: {}, hidden: true, value: '', textContent: '', listeners: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    style: { setProperty() {}, removeProperty() {} },
    addEventListener() {}, removeEventListener() {}, remove() {}, setAttribute() {}, removeAttribute() {},
    getAttribute() { return null; }, appendChild() {}, prepend() {}, replaceChildren() {}, focus() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, cloneNode() { return null; },
    closest() { return null; }, insertAdjacentHTML() {}, getBoundingClientRect() { return { top: 0, height: 0, width: 0 }; },
  };
}
const elements = new Map();
document.getElementById = (id) => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
const loadingText = element();
document.querySelector = (selector) => selector === '#board-loading .board-loading-text' ? loadingText : null;
document.querySelectorAll = () => [];
window.scrollTo = () => {};
"""

WITH_PLAYER = (
    "window.location.search = '?player=player0005';"
    " context.history = {replaceState() {}};"
    " document.documentElement.classList = {add() {}, remove() {}, toggle() {}, contains() { return false; }};"
)


def run(body: str) -> None:
    script = (
        CLOCK + DOM + "(async () => {\n" + body + "\n})().then(() => process.exit(0), "
        "(error) => { console.error(error); process.exit(1); });\n"
        "__realSetTimeout(() => { console.error(' test did not finish'); process.exit(1); }, 20000);"
    )
    result = run_in_module_context(script, extra_context_js=WITH_PLAYER, check=False)
    assert result.returncode == 0, result.stderr[-4000:]


# A fake upstream: `statusAt(elapsedMs)` gives the capture status at that moment of the read. Each status request
# long-polls for its `wait` (the clock moves by it) before answering; once `stopAfterMs` has passed, the request never
# answers, so the read stays parked and the test reads what the page showed along the way.
LOAD_BOARD = r"""
const seen = [];
let readStartedAt = null;
let parked = false;
let lastAnswerAt = null;
const ceiling = 30 * 60 * 1000;
fetch = async (url) => {
  const path = String(url).split('?')[0];
  if (path === '/api/capture/refresh') { readStartedAt = Date.now(); return { ok: true, status: 202 }; }
  if (path === '/api/capture/status') {
    // what the page shows for the previous answer, read when it asks again (or parks)
    if (lastAnswerAt !== null) seen.push({ at: lastAnswerAt, text: loadingText.textContent });
    if (Date.now() - readStartedAt >= stopAfterMs) { parked = true; return new Promise(() => {}); }
    advance(waitOf(url));
    lastAnswerAt = Date.now() - readStartedAt;
    const status = statusAt(lastAnswerAt);
    return { ok: true, json: async () => status };
  }
  throw new Error('unexpected request: ' + url);
};
loadBoard();
for (let spins = 0; !parked && spins < 100000; spins += 1) await new Promise((resolve) => __realSetTimeout(resolve, 0));
assert(parked, 'the read never reached the end of the fake timeline');
assert(seen.length > 10, 'too few status answers were shown: ' + seen.length);
"""

NOTICE_JS = "const NOTICE = " + repr(NOTICE) + ";\n"


def test_notice_appears_after_the_threshold_of_no_progress_and_not_before():
    run(NOTICE_JS + r"""
const stopAfterMs = 150000;
const statusAt = () => ({ state: 'running', phase: 'plots', total_deeds: 129, estimated_seconds: 5, board_ready: false });
""" + LOAD_BOARD + r"""
assert(CAPTURE_STATUS_STALL_NOTICE_MS === 90000, 'threshold: ' + CAPTURE_STATUS_STALL_NOTICE_MS);
const before = seen.filter((entry) => entry.at < CAPTURE_STATUS_STALL_NOTICE_MS);
const after = seen.filter((entry) => entry.at >= CAPTURE_STATUS_STALL_NOTICE_MS);
assert(before.length > 5 && after.length > 5, 'timeline too short: ' + JSON.stringify(seen.map((e) => e.at)));
for (const entry of before) assert(!entry.text.includes(NOTICE), 'notice shown too early at ' + entry.at + ' ms: ' + entry.text);
for (const entry of before) assert(entry.text === 'Reading your plots… · 129 plots · about 5 s', 'normal text changed: ' + entry.text);
for (const entry of after) assert(entry.text === 'Reading your plots… · 129 plots · ' + NOTICE, 'stall text at ' + entry.at + ' ms: ' + entry.text);
// It appears with the first status at or past the threshold: at most one long poll (2 s) late.
assert(after[0].at - CAPTURE_STATUS_STALL_NOTICE_MS <= 2000, 'notice came late: ' + after[0].at);
assert(!/[0-9a-f]{8,}|deed_uid/i.test(after[0].text), 'no UIDs in the loader text');
""")


def test_notice_clears_when_the_status_advances_and_returns_on_a_new_stall():
    run(NOTICE_JS + r"""
const stopAfterMs = 300000;
const statusAt = (elapsed) => ({
  state: 'running', total_deeds: 129, estimated_seconds: 5, board_ready: false,
  phase: elapsed < 100000 ? 'plots' : 'balances',
});
""" + LOAD_BOARD + r"""
const stalled = seen.filter((entry) => entry.at >= 90000 && entry.at < 100000);
const resumed = seen.filter((entry) => entry.at >= 100000 && entry.at < 190000);
const again = seen.filter((entry) => entry.at >= 192000);
assert(stalled.length && stalled.every((entry) => entry.text.includes(NOTICE)), 'no notice during the first stall');
assert(resumed.length && resumed.every((entry) => !entry.text.includes(NOTICE)), 'notice stayed after progress: ' + JSON.stringify(resumed.slice(0, 2)));
assert(resumed[0].text === 'Reading your balances… · 129 plots · about 5 s', 'resumed text: ' + resumed[0].text);
assert(again.length && again.every((entry) => entry.text.includes(NOTICE)), 'no notice on the second stall');
""")


def test_moving_plot_count_is_progress_even_when_slow():
    # Progress is a change in the status's progress fields, not the passing of time: a full read that moves its
    # N of M every 30 s never shows the notice, however long it takes.
    run(NOTICE_JS + r"""
const stopAfterMs = 400000;
const statusAt = (elapsed) => ({ state: 'running', phase: 'plots', fetched_deeds: Math.floor(elapsed / 30000), total_deeds: 400, board_ready: false });
""" + LOAD_BOARD + r"""
for (const entry of seen) assert(!entry.text.includes(NOTICE), 'notice on a moving read at ' + entry.at + ': ' + entry.text);
assert(seen.at(-1).text.startsWith('Reading your plots… 13 of 400'), 'count text: ' + seen.at(-1).text);
""")


def test_thirty_minute_ceiling_and_cadence_are_unchanged():
    run(r"""
assert(CAPTURE_STATUS_MAX_WAIT_MS === 30 * 60 * 1000, 'ceiling: ' + CAPTURE_STATUS_MAX_WAIT_MS);
const waits = [];
let startedAt = null;
fetch = async (url) => {
  const path = String(url).split('?')[0];
  if (path === '/api/capture/refresh') { startedAt = Date.now(); return { ok: true, status: 202 }; }
  if (path === '/api/capture/status') {
    const wait = waitOf(url);
    waits.push(wait);
    advance(wait);
    return { ok: true, json: async () => ({ state: 'running', phase: 'plots', board_ready: false }) };
  }
  throw new Error('unexpected request: ' + url);
};
const flags = [];
const result = await readBoard((status, info) => flags.push(info?.stalled === true));
const elapsed = Date.now() - startedAt;
assert(!result.ok && String(result.error?.message) === 'read did not settle within 30 minutes', 'result: ' + JSON.stringify(result));
assert(elapsed >= 30 * 60 * 1000 && elapsed < 30 * 60 * 1000 + 2000, 'gave up at ' + elapsed + ' ms');
// Cadence: 10 fast polls, 1 s until the first minute, then 2 s (), as before.
assert(waits.slice(0, 10).every((w) => w === 500), 'fast polls: ' + waits.slice(0, 10));
assert(waits[10] === 1000 && waits.at(-1) === 2000, 'mid/slow polls: ' + waits[10] + ' ' + waits.at(-1));
// The stalled read is still polling the whole half hour, and is flagged from the threshold on.
assert(flags.length === waits.length && flags.at(-1) === true && flags[0] === false, 'stall flags: ' + flags.length);
""")
