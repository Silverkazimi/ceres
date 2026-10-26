import { BOARD, CURRENT_PLAYER } from './board.js';
import { esc } from './format.js';
import { decorateResourceMentions } from './icons.js';

const preferences = new Map();
const HIVEAUTH_SESSION_KEY = 'ceres:hiveauth-session:v1:';
const SIGNING_REJECTED = 'Signing rejected in the Keychain app';
const storedExpiryTimers = new Map();
let session = null;
let pending = null;
const lateBroadcasts = new Map();
let expiryTimer = null;
let requestView = null;
let notice = '';
let controlsId = 0;
const controlStates = new Map();

function controlState(id) {
  if (!controlStates.has(id)) controlStates.set(id, { expanded: false, drawerUpward: false });
  return controlStates.get(id);
}

// the modal panel itself clips (`.harvest-panel { overflow: hidden }`, for its rounded
// corners), so the drawer's clip boundary is that panel's own box, not the viewport -- a footer
// can sit well inside a shorter-than-viewport panel and still have no room to spare.
function clipBounds(el) {
  const bounds = { top: 0, bottom: window.innerHeight, left: 0, right: window.innerWidth };
  if (typeof getComputedStyle !== 'function') return bounds;
  for (let parent = el.parentElement; parent; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    const rect = parent.getBoundingClientRect();
    if (style.overflowY !== 'visible') {
      bounds.top = Math.max(bounds.top, rect.top);
      bounds.bottom = Math.min(bounds.bottom, rect.bottom);
    }
    if (style.overflowX !== 'visible') {
      bounds.left = Math.max(bounds.left, rect.left);
      bounds.right = Math.min(bounds.right, rect.right);
    }
  }
  return bounds;
}

// The drawer used to only ever live in a standalone full-width row, so it always had room to
// grow downward. Now that it sits in a modal footer, it must sometimes open upward instead
// measured against the toggle button and the drawer's own content height so it clears the
// panel's edge exactly the way any anchored popover would.
function computeDrawerUpward(toggle, body) {
  if (!toggle || !body) return false;
  const rect = toggle.getBoundingClientRect();
  const bounds = clipBounds(toggle);
  const needed = body.scrollHeight + 16;
  const below = Math.max(0, bounds.bottom - rect.bottom - 8);
  const above = Math.max(0, rect.top - bounds.top - 8);
  return needed > below && above > below;
}

// Center the drawer on its toggle, then clamp it to the nearest clipping boundary relative to
// `.wallet-controls` (its own positioned ancestor there).
function anchorDrawerHorizontally(el, drawer) {
  if (!el.closest('.wallet-sign-pair')) {
    drawer.style.insetInlineStart = '';
    drawer.style.insetInlineEnd = '';
    drawer.style.removeProperty('--wallet-drawer-max-inline-size');
    drawer.style.removeProperty('--wallet-drawer-anchor-x');
    drawer.style.removeProperty('--wallet-drawer-max-block-size');
    return;
  }
  const anchorRect = el.getBoundingClientRect();
  const toggleRect = el.querySelector('[data-wallet-toggle]')?.getBoundingClientRect() || anchorRect;
  const bounds = clipBounds(el);
  el.style.setProperty('--wallet-drawer-max-inline-size', `${Math.max(0, bounds.right - bounds.left - 16)}px`);
  const width = drawer.offsetWidth || 240;
  let left = (anchorRect.width - width) / 2;
  const boundsLeft = anchorRect.left + left;
  if (boundsLeft < bounds.left + 8) left += bounds.left + 8 - boundsLeft;
  const boundsRight = anchorRect.left + left + width;
  if (boundsRight > bounds.right - 8) left -= boundsRight - (bounds.right - 8);
  drawer.style.insetInlineEnd = 'auto';
  drawer.style.insetInlineStart = `${Math.round(left)}px`;
  const drawerLeft = anchorRect.left + left;
  const anchorX = Math.min(width - 12, Math.max(12, toggleRect.left + (toggleRect.width / 2) - drawerLeft));
  drawer.style.setProperty('--wallet-drawer-anchor-x', `${Math.round(anchorX)}px`);
}

function layoutDrawer(el) {
  const state = controlState(el.dataset.walletControlsId);
  const toggle = el.querySelector('[data-wallet-toggle]');
  const drawer = el.querySelector('.wallet-drawer');
  const body = drawer?.querySelector('.wallet-drawer-body');
  // a connect request opens every drawer of the account (`connectRequest` in walletControlsHtml) without setting `state.expanded`, and a
  // caller that gives no control id gets a fresh, closed state on every render. The drawer's own `is-open` class is the truth, so the
  // clamp always runs for a drawer the player can see (the QR and the "Open Keychain app" button were drawn off the left edge).
  if ((!state.expanded && !drawer?.classList.contains('is-open')) || !toggle || !drawer || !body) return;
  const bounds = clipBounds(toggle);
  const rect = toggle.getBoundingClientRect();
  const below = Math.max(0, bounds.bottom - rect.bottom - 8);
  const above = Math.max(0, rect.top - bounds.top - 8);
  state.drawerUpward = computeDrawerUpward(toggle, body);
  const available = state.drawerUpward ? above : below;
  drawer.style.setProperty('--wallet-drawer-max-block-size', `${Math.round(available)}px`);
  el.classList.toggle('is-upward', state.drawerUpward);
  anchorDrawerHorizontally(el, drawer);
}

export function layoutOpenWalletDrawers(root = document) {
  root.querySelectorAll('.wallet-controls').forEach(layoutDrawer);
}

function closeDrawer(el) {
  const state = controlState(el.dataset.walletControlsId);
  const toggle = el.querySelector('[data-wallet-toggle]');
  const drawer = el.querySelector('.wallet-drawer');
  if (!state.expanded || !drawer) return false;
  state.expanded = false;
  toggle?.setAttribute('aria-expanded', 'false');
  drawer.classList.remove('is-open');
  drawer.inert = true;
  // The drawer is still visible while its grid row closes, so retain the measured upward
  // anchor until that transition finishes instead of briefly applying the below-toggle default.
  const settleClosedDrawer = (event) => {
    if (event.target !== drawer || event.propertyName !== 'grid-template-rows' || state.expanded) return;
    drawer.removeEventListener('transitionend', settleClosedDrawer);
    state.drawerUpward = false;
    el.classList.remove('is-upward');
  };
  drawer.addEventListener('transitionend', settleClosedDrawer);
  return true;
}

export function walletAccount() { return String(CURRENT_PLAYER || BOARD?.player || '').toLowerCase(); }
export function keychainAvailable() { return typeof window.hive_keychain?.requestBroadcast === 'function'; }
function hiveAuthAvailable() { return window.isSecureContext && typeof Worker === 'function' && typeof WebSocket === 'function'; }
function hiveAuthSessionKey(account) { return `${HIVEAUTH_SESSION_KEY}${encodeURIComponent(String(account || '').toLowerCase())}`; }
function clearStoredSession(account) {
  if (!account) return;
  clearTimeout(storedExpiryTimers.get(account));
  storedExpiryTimers.delete(account);
  try { localStorage.removeItem(hiveAuthSessionKey(account)); } catch (_) {}
}
function scheduleStoredExpiry(account, expire) {
  clearTimeout(storedExpiryTimers.get(account));
  const timer = setTimeout(() => {
    storedExpiryTimers.delete(account);
    let current = null;
    try { current = JSON.parse(localStorage.getItem(hiveAuthSessionKey(account)) || 'null'); } catch (_) {}
    if (!current || !Number.isFinite(current.expire) || current.expire <= Date.now()) clearStoredSession(account);
    else scheduleStoredExpiry(account, current.expire);
  }, Math.min(Math.max(0, expire - Date.now()), 2147483647));
  storedExpiryTimers.set(account, timer);
}
function storedSession(account) {
  try {
    const saved = JSON.parse(localStorage.getItem(hiveAuthSessionKey(account)) || 'null');
    if (!saved || saved.account !== account || saved.username !== account
      || typeof saved.token !== 'string' || !saved.token || typeof saved.key !== 'string' || !saved.key
      || !Number.isFinite(saved.expire) || saved.expire <= Date.now()) {
      clearStoredSession(account);
      return null;
    }
    scheduleStoredExpiry(account, saved.expire);
    return { account, username: account, token: saved.token, key: saved.key, expire: saved.expire };
  } catch (_) {
    clearStoredSession(account);
    return null;
  }
}
function storeSession(account, credentials) {
  if (!credentials || credentials.account !== account || credentials.username !== account
    || typeof credentials.token !== 'string' || !credentials.token
    || typeof credentials.key !== 'string' || !credentials.key
    || !Number.isFinite(credentials.expire) || credentials.expire <= Date.now()) return false;
  try {
    localStorage.setItem(hiveAuthSessionKey(account), JSON.stringify({ account, username: account,
      token: credentials.token, key: credentials.key, expire: credentials.expire }));
    scheduleStoredExpiry(account, credentials.expire);
    return true;
  } catch (_) { return false; }
}
function walletPreference(account) {
  const key = String(account || '').toLowerCase();
  if (preferences.has(key)) return preferences.get(key);
  try {
    const kind = localStorage.getItem(`ceres.wallet-provider:${key}`);
    if (kind === 'keychain' || kind === 'hiveauth') { preferences.set(key, kind); return kind; }
  } catch (_) {}
  return null;
}
function rememberWallet(account, kind) {
  if (kind !== 'keychain' && kind !== 'hiveauth') return;
  const key = String(account || '').toLowerCase();
  preferences.set(key, kind);
  try { localStorage.setItem(`ceres.wallet-provider:${key}`, kind); } catch (_) {}
}
export function walletKind(account = walletAccount()) { return walletPreference(account) || (keychainAvailable() ? 'keychain' : 'hiveauth'); }
// the account menu chooses a provider without opening the transaction wallet drawer.
export function accountWalletChoices() { return { keychain: keychainAvailable(), hiveauth: hiveAuthAvailable() }; }
export function selectAccountWallet(account, kind) { if (kind === 'keychain' || kind === 'hiveauth') { rememberWallet(account, kind); notify(); } }
export function accountWalletRequest(account) {
  if (requestView?.account !== account || !pending || !['connect', 'challenge'].includes(pending.action)) return null;
  return { status: requestView.status, link: requestView.link || '', svg: requestView.svg || '', action: pending.action };
}
export function cancelAccountWalletRequest(account) {
  if (pending?.account !== account || !['connect', 'challenge'].includes(pending.action)) return;
  finishPending({ type: 'cancelled' });
  closeDialog();
  notify();
}
export function pairAccountWalletAgain(account) {
  if (pending?.account !== account || pending.action !== 'challenge' || !pending.retryWithQr) return;
  const request = pending;
  pending = null;
  clearTimeout(request.timer);
  restartChallengeWithQr(request);
}
export function walletName(account = walletAccount()) { return walletKind(account) === 'keychain' ? 'Keychain' : 'HiveAuth'; }
// ( in tiny letters must say to switch to the Keychain app): a HiveAuth request waits in the
// wallet app, and the player has to go there. The text is shown large and orange (`.is-action`).
const SWITCH_TX = 'Switch to the Hive Keychain app and confirm the transaction.';
const SWITCH_AUTH = 'Switch to the Hive Keychain app and confirm the authentication.';
const actionClass = (text) => (text === SWITCH_TX || text === SWITCH_AUTH ? ' is-action' : '');
export function walletSigningStatus(account = walletAccount()) {
  if (requestView?.account === account && requestView.signing) return requestView.status;
  return walletKind(account) === 'hiveauth' ? SWITCH_TX : 'Waiting for wallet approval';
}
export function clearStaleWalletRequest(account) {
  if (requestView?.account !== account || requestView.signing || !requestView.failed) return false;
  closeWalletDialog();
  return true;
}
export function walletReady(account = walletAccount()) {
  return walletKind(account) === 'keychain' ? keychainAvailable() : Boolean(session?.account === account && session.expire > Date.now());
}
function notify() {
  if (pending?.action === 'connect' && pending.account !== walletAccount()) { finishPending({ type: 'cancelled' }); return; }
  const controls = [...document.querySelectorAll('.wallet-controls')];
  const active = document.activeElement;
  const index = controls.indexOf(active?.closest('.wallet-controls'));
  const action = ['data-wallet-toggle', 'data-wallet-select', 'data-wallet-disconnect', 'data-wallet-close'].find((name) => active?.hasAttribute(name));
  const value = action ? active.getAttribute(action) : '';
  const expandedControlIds = new Set(controls.filter((el) => controlState(el.dataset.walletControlsId).expanded).map((el) => el.dataset.walletControlsId));
  controls.forEach((el) => {
    el.outerHTML = walletControlsHtml(el.dataset.walletAccount, el.dataset.walletControlsId);
  });
  document.querySelectorAll('.wallet-controls').forEach(layoutDrawer);
  window.dispatchEvent(new CustomEvent('ceres:wallet-changed'));
  document.querySelectorAll('.wallet-controls').forEach((el) => {
    const state = controlState(el.dataset.walletControlsId);
    if (expandedControlIds.has(el.dataset.walletControlsId)) {
      state.expanded = true;
      const toggle = el.querySelector('[data-wallet-toggle]');
      const drawer = el.querySelector('.wallet-drawer');
      toggle?.setAttribute('aria-expanded', 'true');
      drawer?.classList.add('is-open');
      if (drawer) drawer.inert = false;
    }
    layoutDrawer(el);
  });
  if (index >= 0 && action) {
    const host = document.querySelectorAll('.wallet-controls')[index];
    const next = host?.querySelector(`[${action}="${value}"]:not(:disabled)`) || host?.querySelector('[data-wallet-close], [data-wallet-toggle]');
    next?.focus({ preventScroll: true });
  }
}
function disposeSession(clearStored = true) {
  const account = session?.account;
  session?.worker.terminate();
  session = null;
  lateBroadcasts.clear();
  clearTimeout(expiryTimer);
  expiryTimer = null;
  if (clearStored) clearStoredSession(account);
}
function scheduleExpiry(expire) {
  clearTimeout(expiryTimer);
  expiryTimer = setTimeout(expireSession, Math.min(expire - Date.now(), 2147483647));
}
function expireSession() {
  if (!session) return;
  const remaining = session.expire - Date.now();
  if (remaining > 0) scheduleExpiry(session.expire);
  else if (!pending && lateBroadcasts.size === 0) disposeSession();
  notify();
}
function closeDialog() { requestView = null; }
function showDialog(title, account, signing = false, controlId = null) {
  notice = '';
  requestView = { title, account, signing, status: signing ? SWITCH_TX : 'Connecting…' };
  if (controlId) controlState(controlId).expanded = true;
}
function closeWalletDialog() {
  if (pending?.action === 'broadcast') {
    document.querySelectorAll('.wallet-controls').forEach((el) => {
      if (el.dataset.walletAccount === pending.account) controlState(el.dataset.walletControlsId).expanded = false;
    });
    notify();
    return;
  }
  if (pending?.action === 'connect') finishPending({ type: 'cancelled' });
  closeDialog();
  notify();
}
function connectionFailure(reason) {
  return {
    expired: 'The HiveAuth approval request expired. Connect again for a new QR code.',
    declined: 'The wallet declined the connection request.',
    'invalid-session': 'The wallet response did not contain a valid session. Connect again.',
    transport: 'The HiveAuth connection was interrupted. Connect again.',
    'wallet-error': 'The wallet could not complete the connection request.',
  }[reason] || 'HiveAuth could not confirm the connection. Connect again.';
}
function finishPending(message) {
  const request = pending;
  if (!request) return;
  pending = null;
  clearTimeout(request.timer);
  if (request.action === 'broadcast' && message.type === 'uncertain' && message.reason === 'watchdog') {
    lateBroadcasts.set(request.id, request);
    session?.worker.postMessage({ id: request.id, action: 'release' });
  }
  if (request.action === 'connect') {
    if (message.type === 'connected' && session && message.account === request.account && request.account === walletAccount() && Number.isFinite(message.expire) && message.expire > Date.now()) {
      session.expire = message.expire;
      storeSession(request.account, message.credentials);
      document.querySelectorAll('.wallet-controls').forEach((el) => {
        if (el.dataset.walletAccount === request.account) controlState(el.dataset.walletControlsId).expanded = false;
      });
      scheduleExpiry(message.expire);
      closeDialog();
      // a sign-in that rode on this connection gets its signature from the SAME approval.
      if (request.callback) {
        if (message.signature) request.callback({ success: true, result: message.signature });
        else if (request.message) {   // approved without a signature: ask over the approved session instead of failing a valid sign-in
          try { requestWalletSignChallenge(request.account, request.message, request.callback, { retryWithQr: request.retryWithQr }); } catch (error) { request.callback({ success: false, error: String(error?.message || error) }); }
        } else request.callback({ success: false, error: 'HiveAuth did not return a signature.' });
      }
    } else {
      if (request.callback) request.callback({ success: false, declined: message.reason === 'declined', error: message.type === 'cancelled' ? 'cancelled' : connectionFailure(message.reason) });
      // cancelling a re-authentication of a stored session keeps that session (nothing proved it invalid).
      const keepStored = message.type === 'cancelled' && request.reauth;
      disposeSession(!keepStored);
      if (keepStored && request.account === walletAccount()) restoreHiveAuth(request.account);
      if (message.type !== 'cancelled') requestView = { title: 'HiveAuth', account: request.account, status: connectionFailure(message.reason), failed: true, retry: message.reason === 'expired' };
      else closeDialog();
    }
  } else if (request.action === 'challenge') {   // a working saved session signs without reconnecting
    if (request.retryWithQr && (message.type === 'challenge-failed' || message.type === 'declined' || (message.type === 'not-sent' && message.reason !== 'busy'))) {
      restartChallengeWithQr(request);
      return;
    }
    closeDialog();
    if (message.type === 'challenged' && message.signature) request.callback({ success: true, result: message.signature });
    else if (message.type === 'declined') request.callback({ success: false, declined: true, error: 'declined' });
    else request.callback({ success: false, error: message.type === 'not-sent' ? 'HiveAuth session expired before signing.' : 'HiveAuth did not return a signature.' });
  } else {
    closeDialog();
    if (message.type === 'signed') {
      request.callback({ success: true, data: { key: request.authority || 'Posting' }, result: { id: message.txId } });
    } else if (message.type === 'declined') {
      request.callback({ success: false, declined: true, error: SIGNING_REJECTED });
    } else if (message.type === 'not-sent') {
      request.callback({ success: false, notSent: true, error: message.reason === 'busy'
        ? 'The wallet is still handling the earlier request. This request was not sent.'
        : 'HiveAuth session expired before signing.' });
    } else {
      const stillChecking = message.reason === 'watchdog' || message.reason === 'expired';
      // an uncertain answer keeps the session (the claim still blocks re-signing this request, S3).
      // Only the wallet's own "not connected through HAS" answer proves the session is gone.
      const invalidSession = message.reason === 'invalid-session';
      if (invalidSession) disposeSession();
      request.callback({ success: false, ambiguousBroadcast: true, stillChecking,
        error: stillChecking ? `Ceres is still checking whether the wallet sent this (${message.reason}${Number.isFinite(message.waited) ? `, ${message.waited} s${message.reached ? '' : ', request never confirmed'}` : ''})`
          : invalidSession ? 'The Keychain app no longer has this HiveAuth session. Connect HiveAuth again.'
          : 'HiveAuth did not return a verified transaction id. The request may have been broadcast.' });
    }
  }
  if (session?.expire && session.expire <= Date.now() && lateBroadcasts.size === 0) disposeSession();
  notify();
}
function restartChallengeWithQr(request) {
  disposeSession();
  connectHiveAuth(request.account, null, request.message, request.callback, request.retryWithQr);
  if (!pending) request.callback({ success: false, error: 'HiveAuth could not start.' });
}
function startRequest(action, account, operations, callback, authority = 'Posting', credentials = null, retryWithQr = false) {
  if (pending) throw new Error('Finish the current wallet request first.');
  const id = crypto.randomUUID();
  const timer = setTimeout(() => finishPending({ type: action === 'connect' ? 'connect-failed' : action === 'challenge' ? 'challenge-failed' : 'uncertain', reason: action === 'connect' ? 'expired' : 'watchdog' }), 110000);
  pending = { id, action, account, callback, timer, authority, message: action === 'connect' || action === 'challenge' ? operations : undefined, reauth: Boolean(credentials), retryWithQr };
  try { session.worker.postMessage({ id, action, account, operations: action === 'connect' || action === 'challenge' ? undefined : operations, message: action === 'connect' || action === 'challenge' ? operations : undefined, authority, ...(action === 'connect' && credentials ? { credentials } : {}) }); }
  catch (error) {
    clearTimeout(timer);
    pending = null;
    disposeSession();
    throw error;
  }
  notify();
}
function createHiveAuthWorker(account, expire = 0) {
    const workerUrl = new URL('./vendor/hiveauth-worker.js', import.meta.url);
    workerUrl.search = new URL(import.meta.url).search;
    const worker = new Worker(workerUrl);
    session = { account, worker, expire };
    worker.onmessage = ({ data }) => {
      if (session?.worker !== worker) return;
      if (data.type === 'restored') {
        if (data.account !== account || !Number.isFinite(data.expire) || data.expire <= Date.now()) { disposeSession(); notify(); return; }
        session.expire = data.expire;
        scheduleExpiry(data.expire);
        notify();
        return;
      }
      if (data.type === 'restore-failed') { disposeSession(); notify(); return; }
      if (!pending || data.id !== pending.id) {
        const late = lateBroadcasts.get(data.id);
        if (!late) return;
        if (data.type === 'signed') {
          lateBroadcasts.delete(data.id);
          late.callback({ success: true, lateSigned: true, data: { key: late.authority || 'Posting' }, result: { id: data.txId } });
        } else if (data.type !== 'sign-wait') lateBroadcasts.delete(data.id);
        if (!pending && lateBroadcasts.size === 0 && session?.expire && session.expire <= Date.now()) disposeSession();
        return;
      }
      if (pending.action === 'connect' && account !== walletAccount()) { finishPending({ type: 'cancelled' }); closeDialog(); return; }
      if (data.type === 'auth-wait') {
        requestView = { title: 'HiveAuth', account, signing: pending.action === 'broadcast', status: pending.action === 'broadcast' ? SWITCH_TX : SWITCH_AUTH, link: data.link, svg: data.svg };
        notify();
      } else if (data.type !== 'sign-wait') finishPending(data);
    };
    worker.onerror = () => {
      if (session?.worker !== worker) return;
      if (pending) {
        finishPending({ type: pending.action === 'connect' ? 'connect-failed' : pending.action === 'challenge' ? 'challenge-failed' : 'uncertain', reason: 'transport' });
      } else {
        disposeSession();
        closeDialog();
        notice = connectionFailure('transport');
        notify();
      }
    };
    return worker;
}
function restoreHiveAuth(account) {
  const credentials = storedSession(account);
  if (!credentials || !hiveAuthAvailable()) return;
  try {
    const worker = createHiveAuthWorker(account, credentials.expire);
    worker.postMessage({ id: crypto.randomUUID(), action: 'restore', account, credentials });
    scheduleExpiry(credentials.expire);
  } catch (_) { disposeSession(); }
}
function connectHiveAuth(account, controlId = null, message = null, callback = null, retryWithQr = false) {
  if (pending || !account || account !== walletAccount()) return;
  if (!hiveAuthAvailable()) { notice = 'HiveAuth requires a secure browser connection.'; notify(); return; }
  // a stored, unexpired session is re-authenticated with its own token and key (Keychain ignores a blank auth_req for an account it already holds).
  const credentials = storedSession(account);
  disposeSession(!credentials);
  showDialog('Connect HiveAuth', account, false, controlId);
  try {
    createHiveAuthWorker(account);
    startRequest('connect', account, message, callback, 'Posting', credentials, retryWithQr);
  } catch (_) {
    disposeSession();
    requestView = { title: 'HiveAuth', account, status: connectionFailure('transport'), failed: true };
    notify();
  }
}

function walletLogo(kind) {
  const src = new URL(`./vendor/wallet/${kind}.svg`, import.meta.url).href;
  return `<img class="wallet-logo" src="${esc(src)}" alt="" aria-hidden="true">`;
}
// the wallet whose name and logo a sign-in label shows -- the remembered choice, else Keychain (the default), whatever the browser offers.
export function walletLabelKind(account = walletAccount()) { return walletPreference(account) || 'keychain'; }
export function walletLogoHtml(kind) { return walletLogo(kind); }
export function walletMethodLabelHtml(kind) { return `<span class="wallet-method">${kind === 'keychain' ? 'Keychain' : 'HiveAuth'}${walletLogo(kind)}</span>`; }
export function walletMethodHtml(account = walletAccount()) {
  return `<span class="wallet-method">${esc(walletName(account))}${walletLogo(walletKind(account))}</span>`;
}
// the waiting line
// while a signature is open, with the wallet's own logo and where to approve it.
// `options.since` (ms, when the wallet was asked) adds the time this wallet request
// stays open in Ceres and what happens when it runs out. The lifetimes are Ceres' own waits: broadcast-gate.js
// KEYCHAIN_RESPONSE_TIMEOUT_MS (120 s, Keychain extension) and the HiveAuth watchdog in startRequest below (110 s). Past them the
// outcome is unknown and Ceres asks the game; nothing is signed again. walletClockTick() keeps the clock running.
export const WALLET_REQUEST_MS = Object.freeze({ keychain: 120000, hiveauth: 110000 });
export const WALLET_EXPIRY_TEXT = 'If the time runs out, Ceres asks the game whether it went through. Do not sign it again.';
export function walletClockText(since, ttl, now = Date.now()) {
  const left = Math.max(0, Math.ceil((Number(since) + Number(ttl) - now) / 1000));
  return left > 0 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} left` : 'Time is up';
}
export function walletWaitHtml(account = walletAccount(), authority = 'Posting', options = {}) {
  const kind = walletKind(account);
  const key = authority === 'Active' ? ' (Active key)' : '';
  const text = kind === 'hiveauth' ? `Check the Keychain app on your phone now and approve the request${key}.` : `Approve the request in the Keychain extension now${key}.`;
  const since = Number(options.since);
  if (!Number.isFinite(since) || since <= 0) return `<p class="wallet-wait is-${kind}" role="status"><span class="spinner" aria-hidden="true"></span>${walletLogo(kind)}<span>${esc(text)}</span></p>`;
  const ttl = WALLET_REQUEST_MS[kind] || WALLET_REQUEST_MS.keychain;
  return `<div class="wallet-wait is-${kind} has-clock" role="status"><span class="spinner" aria-hidden="true"></span>${walletLogo(kind)}<span class="wallet-wait-text"><span>${esc(text)} </span><small>${esc(WALLET_EXPIRY_TEXT)}</small></span><b class="wallet-wait-clock" data-wallet-clock data-since="${since}" data-ttl="${ttl}" aria-live="off">${esc(walletClockText(since, ttl))}</b></div>`;
}
// Updates every running wallet clock under `root` in place (no re-render, so focus and buttons stay put).
export function walletClockTick(root = typeof document !== 'undefined' ? document : null, now = Date.now()) {
  for (const el of root?.querySelectorAll?.('[data-wallet-clock]') || []) el.textContent = walletClockText(el.dataset.since, el.dataset.ttl, now);
}
export function walletSignLabelHtml(prefix = 'Sign with', account = walletAccount()) {
  return `<span class="wallet-sign-label">${decorateResourceMentions(esc(prefix))} ${walletMethodHtml(account)}</span>`;
}
const secondary = 'hm-btn ceres-btn ceres-btn--secondary';
const choice = 'hm-btn ceres-btn ceres-btn--choice';
const X_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
export function walletControlsHtml(account = walletAccount(), controlId = null, options = {}) {
  if (!account) return '';
  const kind = walletKind(account), ready = walletReady(account);
  const stateId = controlId || String(++controlsId);
  const state = controlState(stateId);
  const connectRequest = requestView?.account === account && !requestView.signing;
  // an open connect request shows every drawer of this account while it lasts, but must not leave
  // drawers the maintainer never opened stuck open after it ends ().
  const expanded = state.expanded || connectRequest;
  const busy = pending?.action === 'broadcast';
  const id = `wallet-panel-${stateId}`;
  const remembered = Boolean(walletPreference(account));
  const compactRequest = options.compactRequest === true;
  const selected = (name) => kind === name && (remembered || ready);
  const check = '<span class="ceres-btn__check" aria-hidden="true"></span>';
  const close = requestView?.account === account && compactRequest ? `<button type="button" class="harvest-x ceres-btn ceres-btn--secondary wallet-request-close" data-control-density="comfortable" data-control-mode="icon" data-control-case="destructive" data-wallet-close aria-label="${pending?.action === 'connect' ? 'Cancel HiveAuth request' : 'Close HiveAuth request'}">${X_ICON}</button>` : '';
  const connection = kind === 'hiveauth' && session?.account === account && session.expire > Date.now() ? `<button type="button" class="${secondary} wallet-disconnect" data-control-density="touch" data-control-mode="icon" data-control-case="destructive" data-wallet-disconnect data-tip="Disconnect HiveAuth" aria-label="Disconnect HiveAuth"${busy ? ' disabled' : ''}><span class="wallet-disconnect-mark" aria-hidden="true">${walletLogo('hiveauth')}</span></button>` : '';
  const pairAgain = pending?.account === account && pending.action === 'challenge' && pending.retryWithQr ? `<button type="button" class="${secondary}" data-control-density="touch" data-wallet-pair-again>Pair again with a QR code</button>` : '';
  // while a HiveAuth connect request is shown, the box is about that request only; the Hive Keychain choice is not part of it (the close button cancels).
  const providers = connectRequest && !compactRequest ? '' : `<div class="wallet-providers">${connectRequest ? '' : `<button type="button" class="${choice}" data-control-density="touch" data-wallet-select="keychain" aria-pressed="${selected('keychain')}"${busy ? ' disabled' : ''}>${check}${walletLogo('keychain')}<span>Hive Keychain</span></button>`}${connectRequest ? '' : `<button type="button" class="${choice}" data-control-density="touch" data-wallet-select="hiveauth" aria-pressed="${selected('hiveauth')}"${busy ? ' disabled' : ''}>${check}${walletLogo('hiveauth')}<span>${kind === 'hiveauth' && ready ? 'HiveAuth' : 'Connect HiveAuth'}</span></button>`}${connection}${close}</div>`;
  const request = requestView?.account === account ? compactRequest
    ? `<div class="wallet-request${requestView.link ? ' is-pairing' : ''}${requestView.failed ? ' is-failed' : ''}" data-wallet-request>${requestView.signing ? '' : `<div class="wallet-status${actionClass(requestView.status)}" role="status" aria-live="polite">${pending ? '<span class="spinner" aria-hidden="true"></span>' : ''}${esc(requestView.status)}</div>`}${requestView.link ? `<div class="wallet-pairing" data-wallet-pairing><div class="wallet-qr" role="img" aria-label="HiveAuth connection QR code">${requestView.svg}</div><a class="${secondary}" data-control-density="touch" data-wallet-open-keychain rel="noreferrer" href="${esc(requestView.link)}" aria-label="Open the Hive Keychain app on this device to approve HiveAuth">${walletLogo('keychain')}<span>Open Keychain app to approve</span></a></div>` : ''}${pairAgain}${requestView.retry ? `<button type="button" class="${secondary}" data-control-density="touch" data-wallet-retry>Retry HiveAuth</button>` : ''}</div>`
    : `<div class="wallet-request${requestView.link ? ' is-pairing' : ''}" data-wallet-request><div class="wallet-request-head">${requestView.signing ? '' : `<div class="wallet-status${actionClass(requestView.status)}" role="status" aria-live="polite">${pending ? '<span class="spinner" aria-hidden="true"></span>' : ''}${esc(requestView.status)}</div>`}<button type="button" class="harvest-x ceres-btn ceres-btn--secondary wallet-request-close" data-control-density="comfortable" data-control-mode="icon" data-control-case="destructive" data-wallet-close aria-label="${pending?.action === 'connect' ? 'Cancel HiveAuth request' : 'Close HiveAuth request'}">${X_ICON}</button></div>${requestView.link ? `<div class="wallet-pairing" data-wallet-pairing><div class="wallet-qr" role="img" aria-label="HiveAuth connection QR code">${requestView.svg}</div><a class="${secondary}" data-control-density="touch" data-wallet-open-keychain rel="noreferrer" href="${esc(requestView.link)}" aria-label="Open the Hive Keychain app on this device to approve HiveAuth">${walletLogo('keychain')}<span>Open Keychain app to approve</span></a></div>` : ''}${pairAgain}${requestView.retry ? `<button type="button" class="${secondary}" data-control-density="touch" data-wallet-retry>Retry HiveAuth</button>` : ''}</div>` : '';
  // ( on closing): the direction must survive the close
  // dropping `is-upward` together with `is-open` moved the still-collapsing chooser from above the button to below it.
  return `<section class="wallet-controls${state.drawerUpward ? ' is-upward' : ''}" data-wallet-account="${esc(account)}" data-wallet-controls-id="${esc(stateId)}"><button type="button" class="${secondary} wallet-toggle" data-control-density="touch" data-wallet-toggle aria-expanded="${expanded}" aria-controls="${id}" aria-label="${ready ? esc(walletName(account)) + ' connected. Wallet options' : remembered ? esc(walletName(account)) + ' selected. Wallet options' : 'Choose wallet'}"><span class="wallet-indicator${ready ? ' is-ready' : ''}" aria-hidden="true"></span>${ready || remembered ? walletLogo(kind) : ''}<span>${ready || remembered ? esc(walletName(account)) : 'Wallet'}</span><span class="wallet-chevron ceres-chevron" aria-hidden="true"></span></button><div class="wallet-drawer${expanded ? ' is-open' : ''}" data-wallet-drawer data-wallet-drawer-anchor data-wallet-drawer-arrow id="${id}"${expanded ? '' : ' inert'}><div class="wallet-drawer-body" data-wallet-drawer-content>${providers}${notice ? `<p class="wallet-status" role="status">${esc(notice)}</p>` : ''}${request}</div></div></section>`;
}

export function walletCanSignTransaction(account) {
  return walletKind(account) === 'keychain' && typeof window.hive_keychain?.requestSignTx === 'function';
}

export function requestWalletSignature(account, transaction, authority, callback) {
  if (walletAccount() !== account) throw new Error('The selected player changed. Nothing was sent.');
  if (!walletCanSignTransaction(account) || !walletReady(account)) throw new Error('This wallet does not support separate transaction signing.');
  if (!['Posting', 'Active'].includes(authority)) throw new Error('Unsupported signing authority.');
  window.hive_keychain.requestSignTx(account, transaction, authority, callback);
}

export function requestWalletBroadcast(account, operations, callback, authority = 'Posting') {
  if (!['Posting', 'Active'].includes(authority)) throw new Error('Unsupported signing authority.');
  if (pending) throw new Error('Finish the current wallet request first.');
  if (!walletReady(account)) throw new Error(`${walletName(account)} is not connected for this account.`);
  if (walletKind(account) === 'keychain') {
    window.hive_keychain.requestBroadcast(account, operations, authority, callback);
    return;
  }
  // the SPS sale's pool transfer needs the Active key; HiveAuth carries `key_type` active to the Keychain app (library/hiveauth.md Q4).
  showDialog('HiveAuth', account, true);
  startRequest('broadcast', account, operations, callback, authority);
}

// the optional sign-in asks the wallet to sign ONE text (Posting key, no transaction). Same answer shape for both wallets: { success, result: signature } or
// { success: false, declined }. HiveAuth does it in ONE step: the text rides on the connection request, so one QR scan and one confirmation both pair the wallet and sign.
export function walletCanSignLogin(account = walletAccount()) {
  return walletKind(account) === 'keychain' ? keychainAvailable() && typeof window.hive_keychain?.requestSignBuffer === 'function' : hiveAuthAvailable();
}
export function requestWalletSignChallenge(account, message, callback, { retryWithQr = false } = {}) {
  if (pending) throw new Error('Finish the current wallet request first.');
  if (walletKind(account) === 'keychain') {
    if (!keychainAvailable()) throw new Error('Keychain is not available.');
    window.hive_keychain.requestSignBuffer(account, message, 'Posting', callback);
    return;
  }
  if (session?.account === account && session.expire > Date.now()) {   // already approved: never reconnect (a new QR would drop this valid session)
    showDialog('HiveAuth', account, true);
    requestView.status = SWITCH_AUTH;
    try { startRequest('challenge', account, message, callback, 'Posting', null, retryWithQr); } catch (error) { closeDialog(); throw error; }
    return;
  }
  const controls = document.querySelector('.wallet-controls');
  if (retryWithQr) connectHiveAuth(account, controls?.dataset.walletControlsId || null, message, callback, true);
  else connectHiveAuth(account, controls?.dataset.walletControlsId || null, message, callback);
  if (!pending) callback({ success: false, error: 'HiveAuth could not start.' });   // connectHiveAuth refused (not the selected account, or no secure context)
}

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-wallet-toggle], [data-wallet-select], [data-wallet-disconnect], [data-wallet-close], [data-wallet-retry], [data-wallet-pair-again]');
  if (!button || button.disabled) return;
  const controls = button.closest('.wallet-controls');
  if (!controls || controls.dataset.walletAccount !== walletAccount()) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  if (button.hasAttribute('data-wallet-toggle')) {
    const state = controlState(controls.dataset.walletControlsId);
    const drawer = controls.querySelector('.wallet-drawer');
    if (state.expanded) closeDrawer(controls);
    else {
      state.expanded = true;
      button.setAttribute('aria-expanded', 'true');
      drawer.classList.add('is-open');
      drawer.inert = false;
      layoutDrawer(controls);
    }
    return;
  }
  if (button.hasAttribute('data-wallet-close')) { closeWalletDialog(); return; }
  if (button.hasAttribute('data-wallet-pair-again')) { pairAccountWalletAgain(walletAccount()); return; }
  if (button.hasAttribute('data-wallet-retry')) { connectHiveAuth(walletAccount(), controls.dataset.walletControlsId); return; }
  notice = '';
  if (button.hasAttribute('data-wallet-disconnect')) { disposeSession(); notify(); return; }
  const kind = button.dataset.walletSelect;
  if (kind !== 'keychain' && kind !== 'hiveauth') return;
  if (pending?.action === 'broadcast') return;
  if (pending?.action === 'connect' && kind === 'keychain') finishPending({ type: 'cancelled' });
  rememberWallet(walletAccount(), kind);
  if (kind === 'keychain' && !keychainAvailable()) {
    notice = 'Hive Keychain is unavailable in this browser.';
    notify();
    return;
  }
  if (kind === 'hiveauth' && !walletReady()) connectHiveAuth(walletAccount(), controls.dataset.walletControlsId);
  else { closeDialog(); notify(); }
}, true);

document.addEventListener('keydown', (event) => {
  const controls = event.target.closest('.wallet-controls');
  if (event.key !== 'Escape' || !controls || !controlState(controls.dataset.walletControlsId).expanded) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  if (pending?.action === 'connect') finishPending({ type: 'cancelled' });
  closeDrawer(controls);
  notify();
  controls.querySelector('.wallet-toggle')?.focus();
}, true);

document.addEventListener('click', (event) => {
  if (event.target.closest('.wallet-controls')) return;
  let changed = false;
  document.querySelectorAll('.wallet-controls').forEach((el) => {
    const state = controlState(el.dataset.walletControlsId);
    if (!state.expanded) return;
    changed = closeDrawer(el) || changed;
  });
  if (changed) window.dispatchEvent(new CustomEvent('ceres:wallet-closed'));
}, true);

window.addEventListener('focus', notify);
document.addEventListener('visibilitychange', () => { if (!document.hidden) notify(); });
window.addEventListener('storage', (event) => {
  if (session?.account && event.key === hiveAuthSessionKey(session.account) && event.newValue === null) {
    disposeSession(false);
    notify();
  }
});
window.addEventListener('ceres:player-changed', () => {
  if (pending) { notify(); return; }
  const account = walletAccount();
  if (session?.account !== account) disposeSession(false);
  if (!session) restoreHiveAuth(account);
  notify();
});

restoreHiveAuth(walletAccount());
