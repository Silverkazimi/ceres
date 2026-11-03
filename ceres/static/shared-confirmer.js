// the ONE confirmation module. Every signed action Ceres starts is confirmed here, from the chain and the
// game, never from the wallet answer alone (the ack is only a fast way to learn the transaction id).
// Owns, alone in ceres/static (G1, tests/ceres/test_ir9545_guards.py): every confirmation schedule, poller, wallet
// silence deadline and settlement wait. Flows hand in a one-shot `check` (their domain read: receipt, amounts) and
// render the state they are given: pending (honest wording), confirmed, refused (with the game's error), never-sent.
// Nothing here gives up and there is no manual control: a fast schedule, then spaced reads, then a slow read every five
// minutes for as long as the action is unresolved; an immediate read on page load, visibilitychange (visible) and
// pageshow. Absence is final only on the server's proof (own history covering the signing moment at expiry, or Hive
// reporting the known transaction absent past its lifetime, both judged by the server's clock).
// Fix round 1 (, reviews astra-review.md F1-F10 and aletheia-verdict.md)
// an ACTION (account + exact operations) has numbered ATTEMPTS; a retry after "never sent" is a fresh pending attempt
// (F3); transitions are monotonic: a proven transaction id beats an inferred "never sent", a final game verdict is
// never overwritten by pending (F3)
// one storage key per action, merged monotonically, synchronised across tabs (storage event + BroadcastChannel); a
// failed write keeps the row in memory and is retried (F6)
// every request (finder, verdict, inclusion) is bounded and aborted (F4); known-id reads never go dormant (F4/F7)
// the own-history finder follows the SERVER's schedule and budget (/api/confirm/find: at most six reads in the ten
// minutes after the wallet answered, then the one expiry read, all on the server's clock, counted per attempt on the
// server so two tabs share one budget) (F6/F7, )
// per-op verdicts are never manufactured from an aggregate refusal (F5).
// Fix round 2 (astra-review-r2 P0-1, P1-3, P1-4, P1-6; aletheia-verdict-r2 MEDIUM-1, LOW-2)
// wallet SILENCE is never an answer: `markSilent` starts the finder but the request stays OPEN; only a wallet answer
// that ends the request (`closed`) lets the server ever prove absence, and the server times it by its own clock
// the attempt's begin is announced to the server by a beacon as the wallet opens (no fetch, nothing awaited, )
// late wallet proof is bound to the attempt that asked for it: an old attempt's answer never touches a newer one
// a cached final verdict never suppresses the flow's domain receipt read (reload-before-screen, another tab)
// a failed write falls back to sessionStorage and is flushed on pagehide (quota + close)
// a flow read that timed out is aborted and its late result is ignored.
const PREFIX = 'ceres:confirm:v2:';
const INDEX_KEY = 'ceres:confirm:v2-index';
const LEGACY_KEY = 'ceres:confirm:v1';
// Game/by-id reads: the swap schedule (/, the maintainer's reference, IN-0708) for 70 s, then every 10 s to
// 3 min, every 30 s to 10 min, every 60 s to Hive's longest lifetime, then every 5 min for as long as it takes.
export const CONFIRM_FAST_MS = [0, 2000, 4000, 8000, 12000, 18000, 26000, 36000, 50000, 70000];
export const CHECK_BOUND_MS = 30000;          // one read (the harvest verifier's former per-request bound, )
export const WALLET_SILENCE_MS = 120000;     // the wallet has not answered at all: start the own-history finder
export const NEVER_SENT_AFTER_MS = 3900 * 1000; // Hive's longest expiry plus grace (display and schedule only; the server decides)
export const SLOW_READ_MS = 5 * 60 * 1000;   // past the lifetime: known-id recovery keeps reading on this slow schedule
const WINDOW_MS = 10 * 60 * 1000;
const FINDER_RETRY_MS = 30000;               // the server could not be reached: ask it again (it keeps the budget)
// IN-0705 (manager decision 2026-11-03, Q1 option 1): an action signed WITHOUT a returned id that the
// ten-minute finder did not see gets ONE more read of the account's own operations at the transaction's Hive expiry
// (the server decides when, from its own clock, and pages back over the signer's own history to the signing moment).
export const CONFIRM_EXPIRY_RECHECK = true;
export const STATES = Object.freeze(['never-sent', 'pending', 'confirmed', 'refused']);
const STATE_SET = new Set(STATES);
const FINAL_STATES = new Set(['confirmed', 'refused']);

// Clock seam (tests freeze it; production uses the real one). Every timer in this module goes through it.
const clock = {
  now: () => Date.now(),
  // unref: in Node (tests) a waiting confirmation never keeps the process alive; browsers return a number.
  sleep: ms => new Promise(resolve => { const handle = setTimeout(resolve, ms); handle?.unref?.(); }),
};
export function __setConfirmerClock(next) { Object.assign(clock, next); }

// Persistence: one key per action, a memory copy that is authoritative when storage fails (F6).
const mem = new Map();          // id -> row (the newest this page knows)
const dirty = new Set();        // ids whose last write failed; retried
let retryArmed = false;
const live = new Map();         // finder tasks by `${id}#${attempt}`
const waiters = new Map();
const finderWakes = new Map();
let serverSkewMs = 0;           // server clock minus this browser's clock, learned from /api/confirm answers

export function __resetConfirmer() {
  mem.clear(); dirty.clear(); live.clear(); finderWakes.forEach(fn => fn()); finderWakes.clear();
  waiters.forEach(w => { w.stopped = true; w.wakeFn?.(); }); waiters.clear();
}

function confirmStorage() { try { return globalThis.localStorage || null; } catch (_) { return null; } }
function fallbackStorage() { try { return globalThis.sessionStorage || null; } catch (_) { return null; } }
function readStored(store, id) {
  try { const raw = store?.getItem(keyFor(id)); const row = raw ? JSON.parse(raw) : null; return row?.id === id ? row : null; } catch (_) { return null; }
}
function hashId(text) {
  let a = 0x811c9dc5, b = 0x9e3779b1;
  for (let i = 0; i < text.length; i += 1) { const c = text.charCodeAt(i); a = Math.imul(a ^ c, 16777619); b = Math.imul(b ^ c, 2246822519); }
  return (a >>> 0).toString(16).padStart(8, '0') + (b >>> 0).toString(16).padStart(8, '0');
}
function keyFor(id) { return PREFIX + hashId(id); }
function identity(account, operations) { return JSON.stringify([String(account).toLowerCase(), operations]); }
const RANK = { pending: 0, 'never-sent': 1, refused: 2, confirmed: 2 };
function mergePerOp(a, b) {
  if (!Array.isArray(a)) return b; if (!Array.isArray(b)) return a;
  return a.map((op, index) => { const other = b[index]; if (!other) return op; return (RANK[other.state] || 0) > (RANK[op.state] || 0) ? other : op; });
}
// Two copies of one action (two tabs, memory vs storage) merge monotonically: the higher attempt wins outright; within one
// attempt a final verdict beats pending, a proven transaction id beats an inferred "never sent" (F3).
function merge(a, b) {
  if (!a) return b || null; if (!b) return a;
  if ((a.attempt || 1) !== (b.attempt || 1)) return (a.attempt || 1) > (b.attempt || 1) ? a : b;
  const base = (a.rev || 0) >= (b.rev || 0) ? a : b;
  const other = base === a ? b : a;
  const out = { ...other, ...base, rev: Math.max(a.rev || 0, b.rev || 0) };
  out.txId = base.txId || other.txId || null;
  const states = [a, b].map(row => row.state);
  if (states.some(state => FINAL_STATES.has(state))) out.state = FINAL_STATES.has(base.state) ? base.state : other.state;
  else if (states.includes('never-sent')) {
    const absent = base.state === 'never-sent' ? base : other;
    const reopened = base.state === 'pending' && (base.reopens || 0) > (other.reopens || 0);
    // Only the chain's verdict on this very id may say "never sent" while an id is known; an inference never can.
    const byChain = absent.neverSentBy === 'chain' && absent.txId && absent.txId === out.txId;
    out.state = reopened || (out.txId && !byChain) ? 'pending' : 'never-sent';
  } else out.state = 'pending';
  out.reopens = Math.max(a.reopens || 0, b.reopens || 0);
  out.perOp = mergePerOp(base.perOp, other.perOp);
  if (out.state === 'pending') out.perOp = (out.perOp || []).map(op => op.state === 'never-sent' ? { index: op.index, state: 'pending' } : op);
  out.answeredAt = [a.answeredAt, b.answeredAt].filter(Boolean).sort()[0] || null;
  return out;
}
function loadRow(id) {
  // The durable copy, the sessionStorage fallback (a write that failed for quota, P1-6) and memory merge monotonically.
  const stored = merge(readStored(confirmStorage(), id), readStored(fallbackStorage(), id));
  const row = merge(stored, mem.get(id) || null);
  if (row) mem.set(id, row);
  return row;
}
function writeIndex(ids) {
  const store = confirmStorage(); if (!store) return;
  let list = [];
  try { list = JSON.parse(store.getItem(INDEX_KEY) || '[]'); if (!Array.isArray(list)) list = []; } catch (_) { list = []; }
  const next = [...new Set([...list, ...ids])];
  if (next.length !== list.length) store.setItem(INDEX_KEY, JSON.stringify(next));
}
function writeDurable(row) {
  const store = confirmStorage();
  if (!store) throw Error('No durable storage');
  store.setItem(keyFor(row.id), JSON.stringify(row));
  writeIndex([row.id]);
}
// Free space for an unresolved row: finished rows (confirmed, refused, never sent) older than an hour go first.
function pruneFinished() {
  const store = confirmStorage(); if (!store) return;
  for (const row of allRows()) {
    if (row.state === 'pending' || clock.now() - Date.parse(row.updatedAt || row.since || '') < 3600000) continue;
    try { store.removeItem?.(keyFor(row.id)); } catch (_) {}
  }
}
function persist(row) {
  try { writeDurable(row); }
  catch (_) {
    try { pruneFinished(); writeDurable(row); }
    catch (__) {
      // P1-6: quota and then the tab closes. The row also goes to sessionStorage (restored by a reload of this tab) and
      // stays dirty: retried every 5 s, on every later write, on resume and on pagehide.
      try { fallbackStorage()?.setItem(keyFor(row.id), JSON.stringify(row)); } catch (___) {}
      dirty.add(row.id);
      armRetry();
      return false;
    }
  }
  dirty.delete(row.id);
  try { fallbackStorage()?.removeItem?.(keyFor(row.id)); } catch (_) {}
  return true;
}
function armRetry() {
  if (retryArmed) return;
  retryArmed = true;
  clock.sleep(5000).then(() => { retryArmed = false; flushDirty(); });
}
function flushDirty() { for (const id of [...dirty]) { const row = mem.get(id); if (row) persist(row); else dirty.delete(id); } }
let channel = null;
try { channel = globalThis.BroadcastChannel ? new globalThis.BroadcastChannel('ceres-confirm') : null; channel?.unref?.(); } catch (_) { channel = null; }   // unref: Node only (tests)
function saveRow(row) {
  const stored = loadRow(row.id);
  // The patch is the newest copy (its rev is above every copy seen), so its fields apply; the merge keeps every proof.
  const next = { ...row, rev: Math.max(row.rev || 0, stored?.rev || 0) + 1, updatedAt: new Date(clock.now()).toISOString() };
  const final = merge(stored, next);
  mem.set(final.id, final);
  persist(final);
  if (dirty.size) flushDirty();
  try { channel?.postMessage({ id: final.id }); } catch (_) {}
  publish(final);
  return final;
}
function allIds() {
  const ids = new Set(mem.keys());
  const store = confirmStorage();
  try { for (const id of JSON.parse(store?.getItem(INDEX_KEY) || '[]')) ids.add(id); } catch (_) {}
  for (const source of [store, fallbackStorage()]) {
    try {
      const count = Number(source?.length || 0);
      for (let index = 0; index < count; index += 1) {
        const key = source.key(index);
        if (key?.startsWith(PREFIX)) { try { const row = JSON.parse(source.getItem(key)); if (row?.id) ids.add(row.id); } catch (_) {} }
      }
    } catch (_) {}
  }
  return ids;
}
function allRows() { return [...allIds()].map(loadRow).filter(Boolean); }
function migrateLegacy() {
  const store = confirmStorage(); if (!store) return;
  try {
    const rows = JSON.parse(store.getItem(LEGACY_KEY) || 'null');
    if (!Array.isArray(rows)) return;
    for (const row of rows) if (row?.id && !loadRow(row.id)) { const next = { attempt: 1, attemptId: randomId(), rev: 0, ...row }; mem.set(next.id, next); persist(next); }
    store.removeItem?.(LEGACY_KEY);
  } catch (_) {}
}
function pruneOld() {
  const store = confirmStorage();
  for (const row of allRows()) {
    if (row.state === 'pending') continue;
    if (clock.now() - Date.parse(row.updatedAt || row.since || '') < 2 * 86400000) continue;
    mem.delete(row.id);
    try { store?.removeItem?.(keyFor(row.id)); } catch (_) {}
  }
}

// Subscribers are notified directly (no reliance on DOM event plumbing); the window event is for outside observers.
const subscribers = new Set();
function publish(row) {
  for (const fn of [...subscribers]) { try { fn(row); } catch (_) {} }
  try { globalThis.window?.dispatchEvent(new CustomEvent('ceres:confirm-state', { detail: row })); } catch (_) {}
}
function randomId() {
  try { const bytes = new Uint8Array(8); globalThis.crypto.getRandomValues(bytes); return [...bytes].map(b => b.toString(16).padStart(2, '0')).join(''); }
  catch (_) { return (clock.now().toString(16) + Math.random().toString(16).slice(2)).slice(0, 16); }
}
function freshAttempt(account, operations, previous, detail = {}) {
  return { id: identity(account, operations), account: String(account).toLowerCase(), operations,
    attempt: (previous?.attempt || 0) + 1, attemptId: randomId(), rev: previous?.rev || 0,
    since: new Date(clock.now()).toISOString(), state: 'pending', txId: null, answeredAt: null, neverSentBy: null,
    error: '', message: '', perOp: operations.map((_, index) => ({ index, state: 'pending' })), ...detail };
}

export function confirmationState(account, operations) { return loadRow(identity(account, operations)); }
// The gate starts an attempt right before the wallet is asked. A finished action (never sent, confirmed or refused) that
// is signed again gets a fresh attempt with its own id, budget and verdict; an unresolved one is the same attempt.
export function beginAttempt(account, operations, { expiration = null } = {}) {
  const current = confirmationState(account, operations);
  if (current && current.state === 'pending') return current;
  // No request here: the next wallet opens at once (). The server learns the attempt from the finder's first read
  // (answered + elapsed duration) or the inclusion probe (answered_ms_ago), on its own clock.
  return saveRow(freshAttempt(account, operations, current, expiration ? { expiration } : {}));
}
// Make sure a record exists (a flow confirming an action it did not sign in this page); never starts a new attempt.
export function trackSigning(account, operations) {
  return confirmationState(account, operations) || saveRow(freshAttempt(account, operations, null));
}
// Late proof from an OLD attempt (P1-3): kept on that attempt's own record, never attached to the current attempt.
function notePriorAttempt(row, attempt, fact) {
  const prior = Array.isArray(row.priorAttempts) ? row.priorAttempts : [];
  const existing = prior.find(item => item.attempt === attempt) || { attempt };
  const next = { ...existing, ...fact };
  return saveRow({ ...row, priorAttempts: [...prior.filter(item => item.attempt !== attempt), next].slice(-8) });
}
// The gate notes the wallet's ANSWER: the confirm schedule runs from that moment. `closed` = the answer ended the wallet
// request (it can no longer be signed later): only then may the server ever prove absence (P0-1). `attempt` binds the
// answer to the attempt that asked (P1-3). A wallet answer for an attempt that own history had found absent reopens it.
export function markAnswered(account, operations, { attempt = null, closed = false } = {}) {
  const row = confirmationState(account, operations);
  if (!row) return;
  if (attempt != null && (row.attempt || 1) !== attempt) { notePriorAttempt(row, attempt, { answered: true }); return; }
  const now = new Date(clock.now()).toISOString();
  if (row.state === 'never-sent' && row.neverSentBy === 'history') {
    saveRow({ ...row, state: 'pending', neverSentBy: null, error: '', reanchor: true, reopens: (row.reopens || 0) + 1,
      answeredAt: now, closedAt: closed ? now : null, perOp: row.operations.map((_, index) => ({ index, state: 'pending' })) });
    findSignedTransaction(row.account, row.operations).catch(() => {});   // the finder carries `reanchor` to the server
    return;
  }
  if (!row.answeredAt || (closed && !row.closedAt)) saveRow({ ...row, answeredAt: row.answeredAt || now, ...(closed && !row.closedAt ? { closedAt: now } : {}) });
  if (closed && !row.closedAt) beacon(row, { event: 'answered', answered: true, closed: true });   // the server times the closure
}
// The wallet has said nothing (P0-1): the finder starts, but the request stays OPEN -- it may still be approved later, so
// no amount of silence ever lets anything be declared "never sent".
export function markSilent(account, operations, { attempt = null } = {}) {
  const row = confirmationState(account, operations);
  if (!row || (attempt != null && (row.attempt || 1) !== attempt) || row.silentAt) return;
  saveRow({ ...row, silentAt: new Date(clock.now()).toISOString() });
}
// The attempt's begin, sent as the wallet opens (P0-1/LOW-2: the server's own clock gives the oldest edge of any later
// absence proof). A beacon, not a fetch: nothing is awaited and nothing reads before the next wallet (). Without a
// beacon the server simply never proves absence for this attempt (honest pending), never a guess.
export function announceBegin(account, operations) {
  const row = confirmationState(account, operations);
  return row ? beacon(row, { event: 'begin' }) : false;
}
function beacon(row, fields) {
  try {
    const send = globalThis.navigator?.sendBeacon;
    if (typeof send !== 'function') return false;
    const body = JSON.stringify({ operations: row.operations, attempt: row.attemptId, ...fields });
    const payload = typeof Blob === 'function' ? new Blob([body], { type: 'application/json' }) : body;
    return send.call(globalThis.navigator, `/api/confirm/intent?player=${encodeURIComponent(row.account)}`, payload) === true;
  } catch (_) { return false; }
}
// The gate records which admission instance belongs to this attempt (: a late reply of an old request must never
// touch a newer instance of the same identity).
export function linkAdmission(account, operations, instanceId) {
  const row = confirmationState(account, operations);
  if (row && row.admissionInstanceId !== instanceId) saveRow({ ...row, admissionInstanceId: instanceId });
}
export function markConfirmation(account, operations, state, detail = {}) {
  if (!STATE_SET.has(state)) throw Error('Invalid confirmation state');
  const row = confirmationState(account, operations);
  if (!row) return null;
  if (FINAL_STATES.has(row.state) && state !== row.state) return row;    // a final game verdict is never overwritten
  if (state === 'never-sent' && row.txId && detail.by !== 'chain') return row;   // a proven id beats an inferred "never sent" (F3)
  const perOp = state === 'never-sent' ? row.operations.map((_, index) => ({ index, state, error: detail.error || '' })) : undefined;
  return saveRow({ ...row, state, ...(perOp ? { perOp } : {}), ...(state === 'never-sent' ? { neverSentBy: detail.by || 'wallet' } : {}), ...detail });
}
export function subscribeConfirmation(account, operations, listener) {
  const id = identity(account, operations);
  const handler = row => { if (row?.id === id) listener(row); };
  subscribers.add(handler);
  const row = loadRow(id);
  if (row) listener(row);
  return () => subscribers.delete(handler);
}
// Every row, every change (the UI consumer for results restored after a reload, the gate's admission reconcile).
export function subscribeAll(listener) {
  subscribers.add(listener);
  return () => subscribers.delete(listener);
}
export function allConfirmations() { return allRows(); }
export function waitForConfirmation(account, operations) {
  const current = confirmationState(account, operations);
  if (current && current.state !== 'pending') return Promise.resolve(current);
  return new Promise(resolve => {
    const unsubscribe = subscribeConfirmation(account, operations, row => {
      if (row?.state && row.state !== 'pending') { unsubscribe(); resolve(row); }
    });
  });
}

// Bounded requests (F4): every fetch is aborted at its bound; a hung request never blocks recovery.
async function boundedJson(url, body, { bound = CHECK_BOUND_MS, signal = null } = {}) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const abort = () => controller?.abort();
  signal?.addEventListener?.('abort', abort);
  const from = clock.now();
  const timeout = clock.sleep(bound).then(() => {
    if (clock.now() - from < bound - 1) return new Promise(() => {});   // a clock that did not move never times out
    abort(); throw Object.assign(Error('Request timed out'), { timedOut: true });
  });
  try {
    const response = await Promise.race([fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), ...(controller ? { signal: controller.signal } : {}) }), timeout]);
    const json = await Promise.race([response.json().catch(() => null), timeout]);
    if (Number.isFinite(json?.server_now)) serverSkewMs = json.server_now - clock.now();
    return { status: response.status, ok: response.ok, body: json };
  } finally { signal?.removeEventListener?.('abort', abort); }
}
function serverIso(localMs) { return new Date(localMs + serverSkewMs).toISOString(); }

// The one scheduler.
export function nextCheckOffset(previous, schedule = 'game') {
  if (previous == null) return 0;
  if (schedule === 'inclusion' && previous < 65000) return Math.floor(previous / 1000) * 1000 + 1000;
  const fast = CONFIRM_FAST_MS.find(offset => offset > previous);
  if (fast != null) return fast;
  if (previous < 180000) return Math.min(180000, previous + 10000);
  if (previous < WINDOW_MS) return Math.min(WINDOW_MS, previous + 30000);
  if (previous < NEVER_SENT_AFTER_MS) return Math.min(NEVER_SENT_AFTER_MS, previous + 60000);
  return previous + SLOW_READ_MS;   // never dormant: a slow read for as long as the action is unresolved (F4/F7)
}
// Hive inclusion (the signed queue's dependency order): once a second for 65 s, as the queue pump did before .
export const INCLUSION_FAST_MS = Array.from({ length: 66 }, (_, index) => index * 1000);

// waitThrough({ key, check, onUpdate, since, txId }) runs `check({ signal })` on the shared schedule until it answers a
// final state. A thrown, hung or malformed answer is "pending, temporarily unavailable" and never stops later reads
// (history shape 4); a read that exceeds its bound is aborted. With a txId, reads past the fast window also ask Hive
// (bounded) whether the transaction is included; the server answers "absent" only past its lifetime by its own clock.
export function waitThrough({ key, check, onUpdate, since, txId, account, schedule = 'game', startedAt = null, intent = null } = {}) {
  if (!key || typeof check !== 'function') throw Error('waitThrough needs a key and a check');
  let waiter = waiters.get(key);
  if (waiter) {
    if (check && !check.generic) waiter.check = check;
    if (onUpdate) waiter.listeners.add(onUpdate);
    if (txId) waiter.txId = txId;
    if (intent) waiter.intent = intent;
    if (waiter.last && onUpdate) { try { onUpdate(waiter.last); } catch (_) {} }
    return waiter.handle;
  }
  let resolveDone;
  const done = new Promise(resolve => { resolveDone = resolve; });
  waiter = { key, check, listeners: new Set(onUpdate ? [onUpdate] : []), startedAt: Math.min(clock.now(), startedAt ?? clock.now()),
    since: Date.parse(since || '') || clock.now(), txId, account, schedule, intent, stopped: false, wakeFn: null, woken: false,
    last: null, lastChainAt: null, frozen: 0 };
  waiter.handle = { done, wake: () => wakeWaiter(waiter), stop: () => { waiter.stopped = true; waiter.wakeFn?.(); waiters.delete(key); } };
  waiters.set(key, waiter);
  const emit = answer => { waiter.last = answer; waiter.listeners.forEach(fn => { try { fn(answer); } catch (_) {} }); };
  (async () => {
    let offset = null;
    while (!waiter.stopped) {
      offset = nextCheckOffset(offset, waiter.schedule);
      const wait = waiter.startedAt + offset - clock.now();
      // A clock that never moves (a test whose timers fire at once) would spin: after 50 such reads the waiter waits for
      // a wake. A real clock always advances across a sleep, so a browser never reaches this branch.
      if (waiter.frozen > 50 && !waiter.woken) await new Promise(resolve => { waiter.wakeFn = resolve; });
      else if (wait > 0 && !waiter.woken) {
        const woke = await Promise.race([clock.sleep(wait).then(() => false), new Promise(resolve => { waiter.wakeFn = () => resolve(true); })]);
        waiter.frozen = !woke && clock.now() < waiter.startedAt + offset - 1 ? waiter.frozen + 1 : 0;
      }
      waiter.wakeFn = null;
      if (waiter.stopped) return;
      if (waiter.woken) { waiter.woken = false; offset = Math.max(offset, clock.now() - waiter.startedAt); }
      let answer;
      const controller = typeof AbortController === 'function' ? new AbortController() : null;
      const boundFrom = clock.now();
      const bound = clock.sleep(CHECK_BOUND_MS).then(() => (clock.now() - boundFrom >= CHECK_BOUND_MS - 1
        ? { state: 'pending', unavailable: true, message: 'The game did not answer in time. Ceres keeps checking; nothing is sent again.' }
        : new Promise(() => {})));   // a clock that did not move (instant test timers) never times a read out
      try { answer = await Promise.race([waiter.check({ signal: controller?.signal }), bound]); }
      catch (error) { answer = { state: 'pending', unavailable: true, message: 'The game could not be read just now. Ceres keeps checking; nothing is sent again.' }; }
      if (answer?.unavailable) controller?.abort();   // a hung read is abandoned, never awaited again (F4)
      if (!answer || !STATE_SET.has(answer.state)) answer = { ...(answer || {}), state: 'pending' };
      const elapsed = clock.now() - waiter.startedAt;
      if (answer.state === 'pending' && waiter.txId && elapsed >= WINDOW_MS
        && (waiter.lastChainAt == null || clock.now() - waiter.lastChainAt >= SLOW_READ_MS || clock.now() - waiter.since >= NEVER_SENT_AFTER_MS && waiter.chainAnswer !== true)) {
        waiter.lastChainAt = clock.now();
        const chain = await chainInclusion(waiter.account, waiter.txId, waiter.since, waiter.intent, clock.now() - waiter.since);
        // An unavailable lookup (null) is retried on the next read; it never ends the wait (F4).
        if (chain === false) answer = { ...answer, state: 'never-sent', by: 'chain', error: 'Hive has no record of this transaction and it is past its lifetime. It was not sent.' };
        else if (chain === true) { waiter.chainAnswer = true; answer = { ...answer, onChain: true, message: answer.message || 'On Hive; the game has not answered yet. Ceres keeps checking; nothing is sent again.' }; }
      }
      emit(answer);
      if (answer.state !== 'pending') { waiters.delete(key); resolveDone(answer); return; }
      while (nextCheckOffset(offset, waiter.schedule) <= clock.now() - waiter.startedAt) offset = nextCheckOffset(offset, waiter.schedule);
    }
  })();
  return waiter.handle;
}
function wakeWaiter(waiter) { waiter.woken = true; waiter.frozen = 0; waiter.wakeFn?.(); }
async function chainInclusion(account, txId, sinceMs, intent, answeredMsAgo) {
  if (!account || !txId) return null;
  try {
    // The server ages the attempt by its own ledger, else by this duration (a duration is not shifted by a wrong clock).
    const answer = await boundedJson(`/api/op-inclusion?player=${encodeURIComponent(account)}`,
      { tx_id: txId, created_at: serverIso(sinceMs), ...(intent ? { intent } : {}), answered_ms_ago: Math.max(0, Math.round(answeredMsAgo || 0)) });
    const body = answer.ok ? answer.body : null;
    return body?.included === true ? true : body?.included === false ? false : null;
  } catch (_) { return null; }
}

// Generic per-op game verdict for a known transaction id (`<tx>`, `<tx>-1`, ...), used when no flow check is live
// (for example after a reload).
function genericVerdict(id) {
  const check = async ({ signal } = {}) => {
    const current = loadRow(id);
    if (!current?.txId) return { state: 'pending' };
    if (current.state !== 'pending') return { state: current.state, perOp: current.perOp };
    const answer = await boundedJson(`/api/confirm/verdict?player=${encodeURIComponent(current.account)}`,
      { tx_id: current.txId, operations: current.operations, attempt: current.attemptId }, { signal });
    if (!answer.ok) throw Error(`Game lookup ${answer.status}`);
    const body = answer.body;
    if (body?.tx_id !== current.txId || !Array.isArray(body.operations) || body.operations.length !== current.operations.length) throw Error('Incomplete game verdict');
    const perOp = body.operations.map((op, index) => {
      const previous = current.perOp?.[index];
      if (op.index !== index || !['pending', 'confirmed', 'refused'].includes(op.state)) return previous || { index, state: 'pending' };
      return previous?.state === 'confirmed' || previous?.state === 'refused' ? previous : op;
    });
    const drift = body.operations.some(op => op.drift);
    const state = perOp.every(op => op.state === 'confirmed') ? 'confirmed'
      : perOp.every(op => op.state !== 'pending') && perOp.some(op => op.state === 'refused') ? 'refused' : 'pending';
    return { state, perOp, drift, message: state !== 'pending' ? '' : drift
      ? 'The game answered in a shape Ceres does not recognise yet; it keeps checking and nothing is sent again.'
      : 'The game is still processing this transaction; Ceres keeps checking.' };
  };
  check.generic = true;
  return check;
}
// Apply one answer to the stored row of the attempt it belongs to (never to a newer attempt). Per-op states come only
// from per-op evidence: an aggregate refusal is never fanned out over operations that may have succeeded (F5).
function applyAnswer(id, attempt, answer) {
  const row = loadRow(id);
  if (!row || (row.attempt || 1) !== attempt) return null;
  const patch = { state: answer.state, message: answer.message || answer.error || '' };
  if (answer.state === 'never-sent') patch.neverSentBy = answer.by || 'chain';
  if (Array.isArray(answer.perOp) && answer.perOp.length === row.operations.length) patch.perOp = mergePerOp(row.perOp, answer.perOp);
  else if (answer.state === 'confirmed' || answer.state === 'never-sent') patch.perOp = (row.perOp || []).map(op => FINAL_STATES.has(op.state) ? op : { ...op, state: answer.state });
  else if (answer.state === 'refused') patch.perOpUnknown = true;   // which operation was refused is not known here
  if (answer.error) patch.error = answer.error;
  return saveRow({ ...row, ...patch });
}
function rowWaiter(row, check, onUpdate) {
  const attempt = row.attempt || 1;
  // Anchored at the wallet's answer (as the pre-fix swap schedule was), not at the moment an id was found.
  return waitThrough({ key: `${row.id}#${attempt}`, check: check || genericVerdict(row.id), since: row.answeredAt || row.since, txId: row.txId,
    account: row.account, intent: row.attemptId, startedAt: Date.parse(row.answeredAt || '') || null,
    onUpdate: answer => { applyAnswer(row.id, attempt, answer); onUpdate?.(answer); } });
}
// A proven transaction id for the CURRENT attempt. Monotonic (F3): a final verdict keeps its id; an attempt inferred
// "never sent" from history is reopened by a proven id; the first id of an attempt is kept.
export function attachTransaction(account, operations, txId, { attempt = null } = {}) {
  if (!/^[a-f0-9]{40}$/i.test(txId || '')) throw Error('Invalid transaction id');
  const id = txId.toLowerCase();
  const row = trackSigning(account, operations);
  if (attempt != null && (row.attempt || 1) !== attempt) return notePriorAttempt(row, attempt, { txId: id });
  let next = row;
  if (FINAL_STATES.has(row.state)) next = row;
  else if (row.state === 'never-sent') {
    if (row.neverSentBy !== 'chain' || row.txId !== id) next = saveRow({ ...row, state: 'pending', txId: row.txId && row.neverSentBy === 'chain' ? id : (row.txId || id),
      neverSentBy: null, error: '', reopens: (row.reopens || 0) + 1, perOp: row.operations.map((_, index) => ({ index, state: 'pending' })) });
  } else if (!row.txId) next = saveRow({ ...row, txId: id });
  waiters.forEach(waiter => wakeWaiter(waiter));   // a flow's own wait (e.g. the swap card) reads at once with the id
  return loadRow(row.id) || next;
}

// confirmAction: the one call a signing flow makes after the gate returns (or rejects ambiguously).
// check(txId, { signal }) -> { state, perOp?, ...domain result } one read; the flow's receipt/amount endpoint
// onUpdate(answer) every answer, for the automatic state line
// Resolves with the final answer: { state: 'confirmed' | 'refused' | 'never-sent', ...domain result, txId }.
// Without a transaction id it first learns it (late wallet answer or the bounded own-history finder, ).
export function confirmAction({ account, operations, txId = null, check, onUpdate } = {}) {
  let row = trackSigning(account, operations);
  if (txId) row = attachTransaction(account, operations, txId);
  const id = row.id;
  const flowCheck = check ? Object.assign(async (options = {}) => {
    const current = loadRow(id);
    if (!current?.txId) return { state: 'pending', message: 'Waiting for the transaction id from the wallet or your own latest Hive operations.' };
    const answer = await check(current.txId, options);
    // P1-6: a read the scheduler already abandoned (timed out, aborted) is stale; its late answer decides nothing.
    if (options.signal?.aborted) return { state: 'pending', stale: true };
    return answer;
  }, {}) : null;
  return new Promise(resolve => {
    let started = false;
    const finish = answer => resolve({ ...answer, txId: loadRow(id)?.txId || null });
    const start = current => {
      if (started) return; started = true;
      rowWaiter(current, flowCheck, answer => onUpdate?.({ ...answer, txId: loadRow(id)?.txId || null })).done.then(finish);
    };
    const current = loadRow(id) || row;
    if (FINAL_STATES.has(current.state) && flowCheck && current.txId) {
      // P1-4: the final verdict is already known (another tab, the orphan verifier, before this screen opened), but the
      // flow still needs its DOMAIN receipt (amounts, refreshed deeds, bcx): read it once more; the verdict stays final.
      started = true;
      waitThrough({ key: `${id}#${current.attempt || 1}#receipt`, check: flowCheck, since: current.answeredAt || current.since,
        startedAt: Date.parse(current.answeredAt || '') || null,
        onUpdate: answer => onUpdate?.({ ...answer, state: answer.state === 'pending' ? 'pending' : loadRow(id)?.state || current.state, txId: loadRow(id)?.txId || null }) })
        .done.then(answer => finish({ ...answer, state: loadRow(id)?.state || current.state, perOp: answer.perOp || loadRow(id)?.perOp }));
      return;
    }
    if (FINAL_STATES.has(current.state)) { const answer = { state: current.state, perOp: current.perOp, error: current.error || '' }; onUpdate?.(answer); finish(answer); return; }
    if (current.state === 'never-sent') { const answer = { state: 'never-sent', error: current.error || '' }; onUpdate?.(answer); finish(answer); return; }
    if (current.txId) { start(current); return; }
    onUpdate?.({ state: 'pending', txId: null, message: 'Signed in the wallet; finding the transaction on Hive. Nothing will be sent again.' });
    const stop = subscribeConfirmation(account, operations, next => {
      if (started) return;
      if (next?.state === 'never-sent' && !next.txId) { stop(); const answer = { state: 'never-sent', error: next.error || '' }; onUpdate?.(answer); finish(answer); return; }
      if (next?.txId) { stop(); start(next); }
    });
    findSignedTransaction(account, operations).catch(() => {});
  });
}

// A genuine second step (pool settlement, the next routing step) waits through the same scheduler.
export function waitForStep({ key, check, onUpdate }) {
  return waitThrough({ key: `step:${key}`, check, onUpdate }).done;
}

// The wallet has not answered at all: hand the request to the own-history finder (the gate calls this; G1 keeps
// every other deadline out of the flows).
export function afterWalletSilence(callback, ms = WALLET_SILENCE_MS) {
  let cancelled = false;
  clock.sleep(ms).then(() => { if (!cancelled) callback(); });  // unref'd like every confirmer timer
  return () => { cancelled = true; };
}

// Own-history finder (: own account, that exact action; the SERVER keeps the schedule and the budget).
// api/confirm/find answers one of: found (tx_id) | wait (next_in_s: the next read the server allows) | absent (the
// expiry read proved it, covering the signing moment and fresh to the expiry) | unproven (next_in_s: try again later)
// | unmatchable (the operations carry no nonce: never matched in history). The server counts the reads per attempt, so
// two tabs or a reload never add reads; this loop only sleeps for as long as the server says.
const UNPROVEN = 'Your latest Hive operations do not show this action yet. Nothing will be sent again; Ceres reads them once more when the transaction expires (about an hour after signing) and then tells you if it was sent.';
const UNCOVERED = 'Ceres could not yet read back to the moment you signed, so it cannot prove whether this action was sent. Nothing will be sent again; Ceres keeps reading.';
const OPEN_REQUEST = 'The wallet never answered this request, so it may still be approved there. Ceres keeps looking in your own Hive operations and tells you when it appears; nothing will be sent again.';
const NEWEST_EDGE = 'Ceres can prove this was not sent once your account shows a newer Hive operation. Nothing will be sent again; Ceres keeps checking.';
const UNMATCHABLE = 'This action carries no reference Ceres can find in your Hive history. Nothing will be sent again.';
async function finderSleep(key, ms) {
  await Promise.race([clock.sleep(Math.max(0, ms)), new Promise(resolve => finderWakes.set(key, resolve))]);
  finderWakes.delete(key);
}
export async function findSignedTransaction(account, operations) {
  const row = trackSigning(account, operations);
  if (row.txId) return row.txId;
  if (row.state !== 'pending') return null;
  const attempt = row.attempt || 1;
  const key = `${row.id}#${attempt}`;
  if (live.has(key)) return live.get(key);
  const task = (async () => {
    for (;;) {
      const current = loadRow(row.id);
      if (!current || (current.attempt || 1) !== attempt || current.state !== 'pending') return current?.txId || null;
      if (current.txId) return current.txId;
      let answer = null;
      try {
        const response = await boundedJson(`/api/confirm/find?player=${encodeURIComponent(current.account)}`, {
          operations: current.operations, attempt: current.attemptId, since: serverIso(Date.parse(current.since)),
          answered: Boolean(current.answeredAt), closed: Boolean(current.closedAt),
          ...(current.reanchor ? { reanchor: true } : {}),
        });
        answer = response.status === 422 ? { status: 'unmatchable' } : response.ok ? response.body : null;
      } catch (_) { answer = null; }
      let now = loadRow(row.id);
      if (!now || (now.attempt || 1) !== attempt || now.state !== 'pending') return now?.txId || null;
      // Whether the own-history read itself failed (: screens say so; a failed read is never evidence).
      if (Boolean(now.findUnavailable) !== !answer || (now.reanchor && answer)) now = saveRow({ ...now, findUnavailable: !answer, ...(answer ? { reanchor: false } : {}) });
      if (answer?.status === 'found' && /^[a-f0-9]{40}$/i.test(answer.tx_id || '')) {
        const attached = attachTransaction(now.account, now.operations, answer.tx_id);
        // F9: an orphan (left by a previous page) has no flow to read it: its verifier starts at once. An in-page flow
        // reads with its own check (confirmAction subscribed and started it when the id was attached).
        if (attached.orphan && !waiters.has(`${attached.id}#${attempt}`) && attached.state === 'pending') rowWaiter(attached);
        return answer.tx_id.toLowerCase();
      }
      if (answer?.status === 'absent') {
        markConfirmation(now.account, now.operations, 'never-sent', { by: 'history',
          error: 'Your own Hive operations show no such action, and its transaction has expired. It was not sent.' });
        return null;
      }
      if (answer?.status === 'unmatchable') { saveRow({ ...now, message: UNMATCHABLE }); return null; }
      const delayS = Number(answer?.next_in_s);
      // The line changes only when the wait is long (the expiry read) or the proof could not be completed yet.
      const message = answer?.status === 'unproven' ? (answer.reason === 'open' ? OPEN_REQUEST : answer.reason === 'newest-edge' ? NEWEST_EDGE : UNCOVERED)
        : answer?.status === 'wait' && delayS > WINDOW_MS / 1000 ? UNPROVEN : null;
      if (message && now.message !== message) saveRow({ ...now, message });
      await finderSleep(key, Number.isFinite(delayS) && delayS >= 0 ? delayS * 1000 : FINDER_RETRY_MS);
    }
  })().finally(() => { live.delete(key); });
  live.set(key, task);
  return task;
}

// Page load, visibilitychange (visible), pageshow and a write in another tab: read at once; hidden-tab timer throttling
// cannot delay a result. Rows a previous page left behind are orphans: their verifier starts here, and the UI consumer
// (confirm-notices.js) shows their result.
export function resumeConfirmations({ before = Infinity } = {}) {
  flushDirty();
  waiters.forEach(waiter => wakeWaiter(waiter));
  finderWakes.forEach(wake => wake());
  for (const row of allRows()) {
    if (row.state !== 'pending' || !(Date.parse(row.since) < before)) continue;
    const key = `${row.id}#${row.attempt || 1}`;
    if (before !== Infinity && !row.orphan) saveRow({ ...row, orphan: true });
    if (row.txId) { if (!waiters.has(key)) rowWaiter(row); continue; }
    if (!live.has(key)) findSignedTransaction(row.account, row.operations).catch(() => {});
  }
}
function onForeignChange(id) {
  const row = loadRow(id);
  if (!row) return;
  publish(row);
  const waiter = waiters.get(`${row.id}#${row.attempt || 1}`);
  if (waiter) wakeWaiter(waiter);
  finderWakes.get(`${row.id}#${row.attempt || 1}`)?.();
}
if (globalThis.window?.addEventListener) {
  window.addEventListener('visibilitychange', () => { if (globalThis.document?.visibilityState === 'visible') resumeConfirmations(); });
  window.addEventListener('pageshow', () => resumeConfirmations());
  window.addEventListener('pagehide', () => flushDirty());   // P1-6: last chance to make a dirty row durable
  // Another tab wrote a row (F6): merge it, tell subscribers, wake its reader.
  window.addEventListener('storage', event => {
    if (!event?.key?.startsWith?.(PREFIX) || !event.newValue) return;
    try { const row = JSON.parse(event.newValue); if (row?.id) { mem.set(row.id, merge(mem.get(row.id) || null, row)); onForeignChange(row.id); } } catch (_) {}
  });
  if (channel) channel.onmessage = event => { if (event?.data?.id) onForeignChange(event.data.id); };
  migrateLegacy();
  pruneOld();
  // Page load: only rows a previous page left behind (a request signing in this page is not an orphan).
  const loadedAt = clock.now();
  queueMicrotask(() => resumeConfirmations({ before: loadedAt }));
}
