// the OPTIONAL Posting-key login and the player's own server space (root routing setup, later staged changes).
// Nothing here is required: browsing, routing and staging work locally without it. The player chooses "Save across devices"; the wallet signs ONE harmless text
// (not a transaction, nothing is paid or broadcast); the server answers a memory-only session token that lives only in this page (a full refresh = sign in again).
import { requestWalletSignChallenge, walletCanSignLogin } from './wallet.js';

const sessions = new Map();   // account -> bearer token
const sessionWallets = new Map();   // account -> 'keychain' | 'hiveauth', the wallet that signed this login
// a page refresh keeps the login. The token also lives in sessionStorage (this tab only; gone when the tab closes, never in localStorage), with the moment it
// was issued, and is dropped once the server's own maximum has passed. The server still decides: any 401 ends it. A restored token is checked once against the server.
const SESSION_STORE = account => `ceres:space-session:v1:${account}`;
const SESSION_STORE_PREFIX = 'ceres:space-session:v1:';
const DEFAULT_MAX_MS = 12 * 3600 * 1000;
function rememberSession(account, token, maxSeconds, wallet = '') {
  sessions.set(account, token);
  if (wallet === 'keychain' || wallet === 'hiveauth') sessionWallets.set(account, wallet);
  try { sessionStorage.setItem(SESSION_STORE(account), JSON.stringify({ token, wallet: sessionWallets.get(account) || '', expires: Date.now() + (Number.isFinite(maxSeconds) && maxSeconds > 0 ? maxSeconds * 1000 : DEFAULT_MAX_MS) })); } catch (_) { /* blocked: this page still has it */ }
}
function dropSession(account) {
  sessions.delete(account);
  sessionWallets.delete(account);
  try { sessionStorage.removeItem(SESSION_STORE(account)); } catch (_) { /* nothing stored */ }
}
const restored = [];
try {
  for (let i = sessionStorage.length - 1; i >= 0; i--) {
    const key = sessionStorage.key(i);
    if (!key || !key.startsWith(SESSION_STORE_PREFIX)) continue;
    const account = key.slice(SESSION_STORE_PREFIX.length);
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(key)); } catch (_) { saved = null; }
    if (saved && typeof saved.token === 'string' && saved.token && Number.isFinite(saved.expires) && saved.expires > Date.now()) { sessions.set(account, saved.token); if (saved.wallet === 'keychain' || saved.wallet === 'hiveauth') sessionWallets.set(account, saved.wallet); restored.push(account); }
    else sessionStorage.removeItem(key);
  }
} catch (_) { /* no sessionStorage: every refresh signs in again, as before */ }

const hex = bytes => [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
async function sha256Hex(text) { return hex(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))); }

export const hasSession = account => sessions.has(String(account).toLowerCase());
export const sessionWallet = account => sessionWallets.get(String(account).toLowerCase()) || '';
export function spaceAvailable(account) {   // the selected wallet can sign the login text (Keychain, or HiveAuth: it pairs and signs in one step)
  return walletCanSignLogin(account);
}

async function call(method, path, { account, body, headers = {} } = {}) {
  const token = account ? sessions.get(String(account).toLowerCase()) : null;
  const response = await fetch(path, { method, headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}), ...headers },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  let data = null;
  try { data = await response.json(); } catch (_) { data = null; }
  if (response.status === 401 && account) dropSession(String(account).toLowerCase());   // the session ended: sign in again
  return { status: response.status, data, etag: response.headers?.get?.('ETag') || null };
}

function walletSign(account, message) {   // the selected wallet signs the text; both wallets answer the same way
  return new Promise((resolve, reject) => {
    try {
      requestWalletSignChallenge(account, message, (answer) => {
        const signature = answer?.result;
        if (answer?.success && typeof signature === 'string' && /^[0-9a-f]{130}$/i.test(signature)) resolve(signature);
        else reject(new Error('declined'));
      });
    } catch (error) { reject(error); }
  });
}

// Sign in: challenge -> wallet signs the exact text -> verify. Returns { ok } or { ok: false, reason }.
export async function player0013(account, { sign = walletSign, shouldContinue = () => true, wallet = '' } = {}) {
  account = String(account).toLowerCase();
  try {
    const verifier = hex(crypto.getRandomValues(new Uint8Array(32)));
    const binding = await sha256Hex(verifier);
    const challenge = await call('POST', '/api/auth/challenge', { body: { account, binding, purpose: 'login' } });
    if (challenge.status !== 200 || typeof challenge.data?.message !== 'string') return { ok: false, reason: challenge.status === 429 ? 'busy' : 'unavailable' };
    if (!shouldContinue()) return { ok: false, reason: 'declined' };   // Cancel before the wallet request starts
    const signature = await sign(account, challenge.data.message);
    if (!shouldContinue()) return { ok: false, reason: 'declined' };
    const verified = await call('POST', '/api/auth/verify', { body: { challenge_id: challenge.data.challenge_id, verifier, signatures: [signature] } });
    if (verified.status !== 200 || typeof verified.data?.token !== 'string' || verified.data.account !== account) {
      return { ok: false, reason: verified.status === 401 ? 'refused' : verified.status === 503 ? 'unavailable' : 'failed' };
    }
    if (!shouldContinue()) return { ok: false, reason: 'declined' };   // verification may finish after Cancel
    rememberSession(account, verified.data.token, Number(verified.data.max_seconds), wallet);
    return { ok: true };
  } catch (error) { return { ok: false, reason: error?.message === 'declined' ? 'declined' : 'failed' }; }
}

export async function signOut(account) {
  account = String(account).toLowerCase();
  const token = sessions.get(account);
  if (token) { try { await call('POST', '/api/auth/logout', { account }); } catch (_) { /* the session ends with the page anyway */ } }
  dropSession(account);
}

// GET the player's saved document: { exists:false } | { exists:true, document, etag } | { error }.
export async function loadDocument(account, kind) {
  const reply = await call('GET', `/api/me/${kind}`, { account });
  if (reply.status === 401) return { error: 'signed-out' };
  if (reply.status !== 200) return { error: 'unavailable' };
  return reply.data?.exists === false ? { exists: false } : { exists: true, document: reply.data, etag: reply.etag };
}

// PUT a whole document; `etag` is the version that was edited (null = create). A stale save is refused with the current document and NEVER overwrites.
export async function saveDocument(account, kind, payload, etag) {
  const reply = await call('PUT', `/api/me/${kind}`, { account, body: { payload }, headers: etag ? { 'If-Match': etag } : { 'If-None-Match': '*' } });
  if (reply.status === 200) return { ok: true, document: reply.data, etag: reply.etag };
  if (reply.status === 412) return { conflict: true, current: reply.data?.current || null };
  return { error: reply.status === 401 ? 'signed-out' : 'unavailable' };
}

// Lossless merge of this device's root routing setup and the saved one: every route of either side stays (matched by id), except routes the player
// explicitly removed (tombstones, from either side); unknown fields of both are kept; the protected-DEC floor takes the larger (more protective) value.
export function mergeRouting(local, saved) {
  const a = local && typeof local === 'object' ? local : {}, b = saved && typeof saved === 'object' ? saved : {};
  const dead = new Map();
  for (const side of [a, b]) for (const tomb of Array.isArray(side.deleted) ? side.deleted : []) if (tomb && typeof tomb.id === 'string') dead.set(tomb.id, tomb);
  const seen = new Set(), rules = [];
  for (const rule of [...(Array.isArray(b.rules) ? b.rules : []), ...(Array.isArray(a.rules) ? a.rules : [])]) {
    const id = rule && typeof rule === 'object' && !Array.isArray(rule) ? rule.id : undefined;
    if (id !== undefined && (dead.has(id) || seen.has(id))) continue;
    if (id !== undefined) seen.add(id);
    rules.push(rule);
  }
  const floor = [a.protected_wallet_dec, b.protected_wallet_dec].map(Number).filter(Number.isFinite);
  return { ...b, ...a, rules, deleted: [...dead.values()].slice(-5000), ...(floor.length ? { protected_wallet_dec: String(Math.max(...floor)) } : {}) };
}

// Bring this device and the saved space together (both directions, nothing lost), returning the merged root. `read`/`write` are this device's root setup.
export async function syncRouting(account, { read, write }) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const remote = await loadDocument(account, 'routing');
    if (remote.error) return { ok: false, reason: remote.error };
    const merged = mergeRouting(read(), remote.exists ? remote.document.payload : null);
    const saved = await saveDocument(account, 'routing', merged, remote.exists ? remote.etag : null);
    if (saved.ok) { write(merged); markSynced(account, merged); return { ok: true, count: merged.rules.length }; }
    if (!saved.conflict) return { ok: false, reason: saved.error || 'unavailable' };
  }
  return { ok: false, reason: 'busy' };
}

// A11: keep recent terminal detail, retain tiny permanent id tombstones.
export function compactChains(chains = {}) {
  const terminal = Object.entries(chains).filter(([, plan]) => ['done', 'cancelled'].includes(plan?.state)
    && !plan.inputs?.some(input => input.left_out_choice === 'put_back' && !input.return_staged_at))
    .sort((a, b) => String(b[1].updated_at).localeCompare(String(a[1].updated_at)));
  const out = { ...chains };
  for (const [id, plan] of terminal.slice(50)) out[id] = { version: 2, id, state: plan.state, created_at: plan.created_at, updated_at: plan.updated_at, tombstone: true, inputs: [], terminal_nonce: plan.combine?.nonce || plan.terminal_nonce || null };
  return out;
}

// A01/A02: serialize durable reservations and sync commits per account.
// Callbacks must perform their read/modify/write synchronously. Without Web Locks
// callers refuse durable writes and irreversible admission when locking is unavailable.
export async function withStagedAccountLock(account, callback) {
  if (globalThis.navigator?.locks?.request) return navigator.locks.request(`ceres:stage-plan:${String(account).toLowerCase()}`, () => callback(true));
  return callback(false);
}

// Staged changes (plans and worksite builds): the union of both sides by plan key, minus what either side removed. A removal is a tombstone with its time; a plan
// created AFTER its removal (staged again) wins. Duplicate incoming refs keep their newest plan; unknown fields of both sides are kept.
export function mergeStaged(local, saved) {
  const a = local && typeof local === 'object' ? local : {}, b = saved && typeof saved === 'object' ? saved : {};
  const when = value => { const t = Date.parse(value); return Number.isFinite(t) ? t : 0; };
  const removed = {};
  for (const side of [a, b]) for (const [id, at] of Object.entries(side.removed && typeof side.removed === 'object' && !Array.isArray(side.removed) ? side.removed : {})) if (when(at) >= when(removed[id])) removed[id] = at;
  const pick = field => {
    const out = {};
    for (const side of [b, a]) for (const [id, item] of Object.entries(side[field] && typeof side[field] === 'object' && !Array.isArray(side[field]) ? side[field] : {})) {
      const key = field === 'worksites' ? `w:${id}` : id;
      if (removed[key] !== undefined && when(removed[key]) >= when(item?.created_at)) continue;
      if (!out[id] || when(item?.created_at) >= when(out[id]?.created_at)) out[id] = item;
    }
    return out;
  };
  // merge plans and their inputs independently. A cancelled plan is
  // a tombstone unless a newer edit explicitly replaces it.
  const ranks = { planned: 0, cooling: 1, ready: 2, combine_signed: 3, combined: 4, done: 5, cancelled: 6 };
  const inputRanks = { active: 0, left_out: 1, unavailable: 1 };
  const choose = (left, right, rank) => {
    if (!left) return right;
    if (!right) return left;
    // A09: cancellation is terminal for this id, regardless of clocks.
    if (right.state === 'cancelled') return right;
    if (left.state === 'cancelled') return left;
    const difference = (rank[right.state || right.status] ?? 0) - (rank[left.state || left.status] ?? 0);
    return difference > 0 || (difference === 0 && when(right.updated_at) > when(left.updated_at)) ? right : left;
  };
  const chains = {};
  for (const id of new Set([...Object.keys(b.chains || {}), ...Object.keys(a.chains || {})])) {
    const localPlan = a.chains?.[id], savedPlan = b.chains?.[id];
    if (localPlan?.tombstone || savedPlan?.tombstone) {
      // B04: terminal identity dominates earlier states of the same attempt.
      const tomb = savedPlan?.tombstone ? savedPlan : localPlan;
      const unresolved = [savedPlan, localPlan].find(plan => plan?.combine && ['combine_signed', 'combined'].includes(plan.state)
        && plan.combine.nonce !== tomb.terminal_nonce);
      chains[id] = tomb;
      if (unresolved) {
        const attemptId = `${id}:attempt:${unresolved.combine.nonce}`;
        chains[attemptId] = { ...unresolved, id: attemptId };
      }
      continue;
    }
    // cancellation or input edits cannot erase an irreversible pending combine.
    const cleared = (older, newer) => older?.combine?.nonce && newer?.cleared_combine_nonce === older.combine.nonce
      && when(newer.updated_at) >= when(older.updated_at);
    const saved = cleared(savedPlan, localPlan) ? null : savedPlan;
    const local = cleared(localPlan, savedPlan) ? null : localPlan;
    const irreversible = plan => plan?.combine && ['combine_signed', 'combined', 'done'].includes(plan.state);
    const conflict = irreversible(saved) && irreversible(local) && saved.combine.nonce !== local.combine.nonce;
    // A01: the durable attempt cannot be replaced by a newer nonce.
    const winner = conflict ? saved : irreversible(saved) && !irreversible(local) ? saved : irreversible(local) && !irreversible(saved) ? local : choose(saved, local, ranks);
    if (!winner) continue;
    // B04: retain distinct irreversible attempts, never discard their evidence.
    if (conflict) {
      const attemptId = `${id}:attempt:${local.combine.nonce}`;
      chains[attemptId] = { ...local, id: attemptId };
    }
    const inputs = new Map();
    for (const side of [savedPlan, localPlan]) for (const input of side?.inputs || []) {
      if (!input?.uid) continue;
      const previous = inputs.get(input.uid);
      // Input rows have no updated_at of their own; use their plan's edit time
      // for equal-rank choices while retaining any independently signed unstake.
      const chosen = choose(previous, { ...input, updated_at: input.updated_at || side.updated_at }, inputRanks);
      // a known game refusal must not resurrect an older device's signed record.
      const signed = [previous?.unstake, input.unstake].filter(value => value?.tx_id && value.tx_id !== chosen.refused_unstake_tx_id)
        .sort((x, y) => when(y.signed_at) - when(x.signed_at))[0];
      const confirmation = [previous?.unstake, input.unstake].filter(value => value?.tx_id === signed?.tx_id && value?.confirmed_at)
        .sort((x, y) => when(y.confirmed_at) - when(x.confirmed_at))[0];
      inputs.set(input.uid, { ...chosen, unstake: signed ? { ...signed, confirmed_at: confirmation?.confirmed_at || signed.confirmed_at || null } : chosen.unstake || null });
    }
    chains[id] = { ...winner, inputs: irreversible(winner) ? winner.inputs : [...inputs.values()].map(({ updated_at, ...input }) => input) };
    // A02/A16: learning the id is monotonic, even with equal edit stamps.
    if (winner.combine) {
      const known = [saved, local].find(plan => plan?.combine?.nonce === winner.combine.nonce && plan.combine.tx_id);
      if (known) chains[id].combine = { ...winner.combine, tx_id: known.combine.tx_id };
    }
  }
  const plans = pick('plans');
  const byRef = new Map();
  for (const [id, plan] of Object.entries(plans).sort((x, y) => when(y[1]?.created_at) - when(x[1]?.created_at) || x[0].localeCompare(y[0]))) {
    if (plan?.action === 'unstake' || !plan?.asset_ref) continue;
    const ref = String(plan.asset_ref);
    if (!byRef.has(ref)) { byRef.set(ref, id); continue; }
    delete plans[id];
    removed[id] = new Date(Math.max(Date.now(), when(plan.created_at) + 1)).toISOString();
  }
  const cutoff = Date.now() - 30 * 24 * 3600 * 1000;
  const keep = Object.entries(removed).filter(([, at]) => when(at) > cutoff).sort((x, y) => when(x[1]) - when(y[1])).slice(-2000);
  return { ...b, ...a, version: 1, plans, worksites: pick('worksites'), chains: compactChains(chains), removed: Object.fromEntries(keep) };
}
export function stagedDigest(store) {
  const data = store && typeof store === 'object' ? store : {};
  const text = canonical({ plans: data.plans || {}, worksites: data.worksites || {}, chains: data.chains || {}, removed: data.removed || {} });
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index++) { hash ^= text.charCodeAt(index); hash = Math.imul(hash, 0x01000193) >>> 0; }
  return `${text.length}:${hash.toString(16)}`;
}
const STAGED_SYNCED_KEY = account => `ceres:staged-synced:v1:${String(account).toLowerCase()}`;
export function stagedSyncedDigest(account) { try { return localStorage.getItem(STAGED_SYNCED_KEY(account)); } catch (_) { return null; } }

// Bring this device's staged changes and the saved ones together (both directions). `read()` is this device's store, `write(merged)` replaces it (only called
// when the merge differs). Returns { ok, changed } where `changed` says this device gained something.
export async function syncStaged(account, { read, write }) {
  let adopted = false;
  for (let attempt = 0; attempt < 3; attempt++) {
    const remote = await loadDocument(account, 'staged');
    if (remote.error) return { ok: false, reason: remote.error };
    const local = read();
    const merged = mergeStaged(local, remote.exists ? remote.document.payload : null);
    const same = stagedDigest(merged) === stagedDigest(remote.exists ? remote.document.payload : null);
    const saved = same ? { ok: true } : await saveDocument(account, 'staged', merged, remote.exists ? remote.etag : null);
    if (saved.ok) {
      // A02: the PUT may have awaited a wallet reservation in another tab.
      const committed = await withStagedAccountLock(account, locked => {
        if (!locked) throw new Error('Safe staged-plan locking is unavailable.');
        const latest = read();
        const next = mergeStaged(merged, latest);
        const changed = stagedDigest(next) !== stagedDigest(latest);
        if (changed) write(next);
        return { changed, retry: stagedDigest(next) !== stagedDigest(merged) || Number(latest.revision || 0) !== Number(local.revision || 0) };
      });
      adopted ||= committed.changed;
      if (committed.retry) continue;
      try { localStorage.setItem(STAGED_SYNCED_KEY(account), stagedDigest(merged)); } catch (_) { /* the status just shows this device */ }
      return { ok: true, changed: adopted };
    }
    if (!saved.conflict) return { ok: false, reason: saved.error || 'unavailable' };
  }
  return { ok: false, reason: 'busy' };
}

// (W3b): delete the saved space or the cached data. The wallet signs a text made for exactly this deletion (the server writes the wording); a login session is
// not enough. Returns { ok, ...report } or { ok: false, reason }.
export async function deleteSaved(account, scope, { sign = walletSign } = {}) {
  account = String(account).toLowerCase();
  try {
    const verifier = hex(crypto.getRandomValues(new Uint8Array(32)));
    const binding = await sha256Hex(verifier);
    const challenge = await call('POST', '/api/auth/challenge', { body: { account, binding, purpose: 'delete-all', scope } });
    if (challenge.status !== 200 || typeof challenge.data?.message !== 'string') return { ok: false, reason: challenge.status === 429 ? 'busy' : 'unavailable' };
    const signature = await sign(account, challenge.data.message);
    const done = await call('POST', '/api/me/delete', { body: { challenge_id: challenge.data.challenge_id, verifier, signatures: [signature], scope } });
    if (done.status !== 200) return { ok: false, reason: done.status === 401 ? 'refused' : 'unavailable' };
    dropSession(account);   // the server ended its sessions; so does this page
    if (scope === 'space') { try { localStorage.removeItem(`ceres:space-synced:v1:${account}`); localStorage.removeItem(`ceres:staged-synced:v1:${account}`); } catch (_) { /* nothing to forget */ } }
    return { ok: true, ...done.data };
  } catch (error) { return { ok: false, reason: error?.message === 'declined' ? 'declined' : 'failed' }; }
}

const ROOT_KEY = account => `ceres:harvest-routing:v1:${String(account).toLowerCase()}`;
function readRootLocal(account) {
  try {
    const raw = localStorage.getItem(ROOT_KEY(account));
    if (raw === null) return { ok: true, data: {} };
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? { ok: true, data: parsed } : { ok: false, data: {} };
  } catch (_) { return { ok: false, data: {} }; }
}

// The one place that signs in AND brings this device's root routing setup together with the saved copy (both directions, nothing lost). An unreadable
// local record is never overwritten. Announces `ceres:space-changed` so an open routing screen adopts the merged routes.
export async function connect(account, { shouldContinue = () => true, wallet = '' } = {}) {
  account = String(account).toLowerCase();
  const signed = hasSession(account) ? { ok: true } : await player0013(account, { shouldContinue, wallet });
  if (!signed.ok) return signed;
  if (!shouldContinue()) return { ok: false, reason: 'declined' };   // avoid syncing after Cancel
  const local = readRootLocal(account);
  const result = await syncRouting(account, { read: () => local.data, write: root => { if (local.ok) { try { localStorage.setItem(ROOT_KEY(account), JSON.stringify(root)); } catch (_) { /* blocked: the saved copy is correct */ } } } });
  window.dispatchEvent(new CustomEvent('ceres:space-changed', { detail: { account } }));
  return result.ok ? { ok: true, count: result.count } : { ok: false, reason: result.reason };
}

export async function disconnect(account) {
  await signOut(account);
  window.dispatchEvent(new CustomEvent('ceres:space-changed', { detail: { account: String(account).toLowerCase() } }));
}

// What "saved across devices" means: the routes on THIS device equal the saved copy as last synced. The digest covers the EFFECTIVE routes (a removed route's
// tombstone is left out) and the protected-DEC floor, so adding a route and removing it again lands back on the synced state.
const SYNCED_KEY = account => `ceres:space-synced:v1:${String(account).toLowerCase()}`;
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
export function rootDigest(root) {
  const data = root && typeof root === 'object' ? root : {};
  const dead = new Set((Array.isArray(data.deleted) ? data.deleted : []).map(tomb => tomb?.id));
  const rules = (Array.isArray(data.rules) ? data.rules : []).filter(rule => !(rule && typeof rule === 'object' && dead.has(rule.id)));
  const text = canonical({ rules: rules.map(canonical).sort(), floor: Number.isFinite(Number(data.protected_wallet_dec)) ? String(Number(data.protected_wallet_dec)) : '0' });
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index++) { hash ^= text.charCodeAt(index); hash = Math.imul(hash, 0x01000193) >>> 0; }
  return `${text.length}:${hash.toString(16)}`;
}
function markSynced(account, root) { try { localStorage.setItem(SYNCED_KEY(account), rootDigest(root)); } catch (_) { /* storage blocked: the status just shows this device */ } }
export function isSynced(account, root) { try { return localStorage.getItem(SYNCED_KEY(account)) === rootDigest(root); } catch (_) { return false; } }

// after a refresh, ask the server once whether each restored login is still valid (a 401 drops it), then tell the screens.
if (restored.length && typeof fetch === 'function') {
  Promise.all(restored.map(account => call('GET', '/api/auth/session', { account }).catch(() => null))).then(() => {
    try { window.dispatchEvent(new CustomEvent('ceres:space-changed', { detail: { account: restored[0] } })); } catch (_) { /* no window */ }
  });
}
