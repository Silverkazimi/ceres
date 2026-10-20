// §2.6: one timer for every countdown in the open worker picker.
// A countdown is `<span data-countdown-until="<iso>">`; each tick rewrites its text only.
// The tick re-queries the DOM, so a picker that was redrawn wholesale (updateMountedRow)
// keeps ticking without being re-bound, and the timer stops by itself once no countdown
// is on the page. When a target reaches zero, `onExpire` runs once for that instant so
// the server (the single readiness source) re-classifies the card.

let TIMER = null;
let ON_EXPIRE = null;
const EXPIRED = new Set();
const SELECTOR = '.inline-picker [data-countdown-until], #checkout-panel [data-countdown-until], .slot-wait-badge [data-countdown-until], .dec-drawer [data-countdown-until]';
// a waiting badge on a board tile keeps ticking when the picker closes.
const PERSISTENT = '.slot-wait-badge [data-countdown-until], .dec-drawer [data-countdown-until]';  // the wallet's claim timer

// `compact` drops the minutes once days are shown ("2d 18h"), so the picker's
// cooling pill stays on one line; under a day it is unchanged ("18h 58m").
export function formatCountdown(ms, { compact = false } = {}) {
  if (!Number.isFinite(ms) || ms <= 0) return 'now';
  const total = Math.floor(ms / 1000);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (n) => String(n).padStart(2, '0');
  if (total >= 3600) return days > 0 ? `${days}d ${pad(hours)}h${compact ? '' : ` ${pad(minutes)}m`}` : `${hours}h ${pad(minutes)}m`;
  return `${pad(minutes)}m ${pad(seconds)}s`;
}

export function countdownText(iso, now = Date.now(), opts = {}) {
  const until = Date.parse(iso);
  return Number.isFinite(until) ? formatCountdown(until - now, opts) : '';
}

function tick() {
  const nodes = typeof document === 'undefined' ? [] : document.querySelectorAll(SELECTOR);
  if (!nodes.length) { unbindCountdowns(); return; }
  const now = Date.now();
  let expired = false;
  nodes.forEach((node) => {
    const iso = node.dataset.countdownUntil;
    const until = Date.parse(iso);
    if (!Number.isFinite(until)) return;
    const text = until <= now && node.dataset.countdownEnded ? node.dataset.countdownEnded : formatCountdown(until - now, { compact: 'countdownCompact' in node.dataset });
    if (node.textContent !== text) node.textContent = text;
    if (until <= now && !EXPIRED.has(iso)) { EXPIRED.add(iso); if (node.closest('.inline-picker')) expired = true; else if (node.closest('#checkout-panel')) window.dispatchEvent(new CustomEvent('ceres:chain-countdown-ended')); else if (node.closest('.dec-drawer')) window.dispatchEvent(new CustomEvent('ceres:wallet-countdown-ended')); }
  });
  if (expired && typeof ON_EXPIRE === 'function') ON_EXPIRE();
}

export function bindCountdowns(onExpire) {
  if (onExpire) ON_EXPIRE = onExpire;
  if (TIMER !== null) return;
  if (typeof document === 'undefined' || !document.querySelector(SELECTOR)) return;
  TIMER = setInterval(tick, 1000);
}

export function unbindCountdowns() {
  if (TIMER !== null && typeof document !== 'undefined' && document.querySelector(PERSISTENT)) return;
  if (TIMER !== null) clearInterval(TIMER);
  TIMER = null;
}
