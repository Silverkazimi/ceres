// fix round 1 (F9): the visible result of a signed action that a previous page left unresolved. The shared
// confirmer finds and verifies such an orphan by itself after a reload; no flow screen is open for it any more, so this
// consumer of the confirmer's state shows its final outcome in one plain line (confirmed, refused with the game's
// reason, or not sent). It reads nothing and decides nothing; it only shows what the confirmer proved.
import { allConfirmations, STORAGE_FULL, subscribeAll, subscribeStorage, walletNeverAskedText } from './shared-confirmer.js';
// round 3, maintainer decisions: IN-0888 an attempt whose wallet never asked ends with WALLET_NEVER_ASKED, shown here
// word for word (also for an action signed in this page); IN-0889 one warning line while a result is kept only in this
// tab because the browser's storage is full (STORAGE_FULL).
// Round 4 (astra-review-r4 : the "never asked" line is an inference; when that attempt reopens it is withdrawn, and
// when it then turns out confirmed or refused, that result replaces it. (L3/ Old results are suppressed by the
// moment of the first upgrade load, not by a capped list.

const CONFIRM_NOTICE_LABELS = {
  sm_land_operation: 'land action', sm_combine_cards: 'combine', sm_stake_change: 'card change',
  sm_dec_powerup_region: 'DEC stake', sm_dec_powerdown_region: 'DEC unstake', sm_claim_rewards: 'SPS claim',
  sm_lock_assets: 'deed lock', sm_unlock_assets: 'deed unlock', sm_market_purchase: 'card purchase', sm_sps: 'SPS transfer',
};

export function confirmNoticeText(row, { afterNeverAsked = false } = {}) {
  const id = row?.operations?.[0]?.[1]?.id;
  const label = CONFIRM_NOTICE_LABELS[id] || 'signed action';
  const count = row?.operations?.length > 1 ? ` (${row.operations.length} operations)` : '';
  const reasons = [...new Set((row?.perOp || []).map(op => op.error).filter(Boolean))];
  const why = reasons.length ? `: ${reasons.join(' · ')}` : row?.error ? `: ${row.error}` : '.';
  if (afterNeverAsked && row?.state === 'confirmed') return `Your ${label}${count} reached Hive after all and is confirmed by the game.`;
  if (afterNeverAsked && row?.state === 'refused') return `Your ${label}${count} reached Hive after all; the game refused it${why}`;
  if (row?.state === 'confirmed') return `Your ${label}${count} signed before the page reloaded is confirmed by the game.`;
  if (row?.state === 'refused') {
    // fix round 2 (astra-r2 , shape 9): a mixed result says what went through, never "refused" alone.
    const ok = (row.perOp || []).filter(op => op.state === 'confirmed').length;
    const refused = (row.perOp || []).filter(op => op.state === 'refused').length;
    if (ok && refused) return `Part of your ${label}${count} signed before the page reloaded went through: ${ok} confirmed by the game, ${refused} refused${why}`;
    return `The game refused your ${label}${count} signed before the page reloaded${why}`;
  }
  if (row?.state === 'never-sent' && row.neverSentBy === 'wallet-expired') return row.error || walletNeverAskedText(row.operations, row.neverAskedCause);
  if (row?.state === 'never-sent') return `Your ${label}${count} signed before the page reloaded was not sent. Nothing changed.`;
  return '';
}

const confirmNoticeSeen = new Map();
const confirmNoticeLines = [];
let storageNotice = null;   // IN-0889: one line, shown while some result is kept only in this tab

function confirmNoticeRegion() {
  const doc = globalThis.document;
  if (!doc?.createElement) return null;
  let box = doc.getElementById?.('ceres-confirm-notices');
  if (!box) {
    box = doc.createElement('div');
    box.id = 'ceres-confirm-notices';
    box.className = 'stage-toast confirm-notices';
    box.setAttribute('role', 'status');
    box.setAttribute('aria-live', 'polite');
    doc.body?.appendChild?.(box);
  }
  return box;
}

// A plain status region (no control: nothing to press, IN-0706); it keeps the three latest results.
export function confirmNoticesHtml(lines = confirmNoticeLines) {
  return lines.map(line => `<p class="confirm-notice is-${line.tone}">${line.text.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])}</p>`).join('');
}

function drawConfirmNotices() {
  const box = confirmNoticeRegion();
  if (!box) return;
  const lines = storageNotice ? [storageNotice, ...confirmNoticeLines] : confirmNoticeLines;
  box.innerHTML = confirmNoticesHtml(lines);
  box.hidden = !lines.length;
}

// fix round 2 (astra-r2 : a result is shown ONCE, also when it became final while no page showed it (another
// tab, the orphan verifier before this module loaded). Shown keys are remembered per browser with the moment they were
// shown; entries older than the notice age are dropped (a row that old is never shown again anyway), so no count cap
// can make an old result reappear (round 4, /L3).
const SHOWN_KEY = 'ceres:confirm-notices:shown';
const BASELINE_KEY = 'ceres:confirm-notices:baseline';
const NOTICE_MAX_AGE_MS = 24 * 3600 * 1000;
function shownEntries() {
  try {
    const list = JSON.parse(globalThis.localStorage?.getItem(SHOWN_KEY) || '[]');
    return Array.isArray(list) ? list.map(item => (typeof item === 'string' ? { k: item, t: Date.now() } : item)).filter(item => item?.k) : [];
  } catch (_) { return []; }
}
function shownKeys() { return shownEntries().map(item => item.k); }
function rememberShown(key) {
  try {
    const now = Date.now();
    const kept = shownEntries().filter(item => item.k !== key && now - Number(item.t || 0) < NOTICE_MAX_AGE_MS + 3600000);
    globalThis.localStorage?.setItem(SHOWN_KEY, JSON.stringify([...kept, { k: key, t: now }].slice(-5000)));
  } catch (_) { /* shown in this page */ }
}
function baselineAt() { try { return Number(globalThis.localStorage?.getItem(BASELINE_KEY)) || 0; } catch (_) { return 0; } }

function dropLine(key) {
  const index = confirmNoticeLines.findIndex(line => line.key === key);
  if (index < 0) return false;
  confirmNoticeLines.splice(index, 1);
  return true;
}
function addLine(line) {
  rememberShown(line.key);
  confirmNoticeLines.push(line);
  if (confirmNoticeLines.length > 3) confirmNoticeLines.shift();
  drawConfirmNotices();
  return line;
}

export function confirmNoticeFor(row) {
  if (!row?.id) return null;
  // A detached watched attempt (round 4, is the same action: one key per action and attempt.
  const key = `${row.detachedFrom || row.id}#${row.attempt || 1}`;
  const askedKey = `${key}~never-asked~${row.neverAskedGeneration || 1}`;
  const neverAsked = row.state === 'never-sent' && row.neverSentBy === 'wallet-expired';
  const askedShown = shownKeys().some(shown => shown.startsWith(`${key}~never-asked`))
    || confirmNoticeLines.some(line => line.key.startsWith(`${key}~never-asked`));
  confirmNoticeSeen.set(key, row.state);
  const fresh = Date.now() - Date.parse(row.updatedAt || row.since || '') <= NOTICE_MAX_AGE_MS;
  if (row.orphan && Date.parse(row.updatedAt || row.since || '') < baselineAt()) return null;
  if (neverAsked) {
    if (shownKeys().includes(askedKey) || confirmNoticeLines.some(line => line.key === askedKey) || !fresh) return null;
    const text = confirmNoticeText(row);
    return text ? addLine({ key: askedKey, text, tone: 'warn' }) : null;
  }
  // the attempt reopened (a wallet answer or a chain hit) or reached a result: the "never asked" line is withdrawn.
  if (askedShown) {
    const previous = confirmNoticeLines.filter(line => line.key.startsWith(`${key}~never-asked`));
    if (previous.some(line => dropLine(line.key))) drawConfirmNotices();
  }
  if (row.state === 'pending' && dropLine(key)) drawConfirmNotices();
  if (!row.orphan && !askedShown) return null;
  if (row.state === 'pending' || shownKeys().includes(key) || confirmNoticeLines.some(line => line.key === key)) return null;
  if (!fresh) return null;
  if (row.orphan && !askedShown && Date.parse(row.updatedAt || row.since || '') < baselineAt()) return null;
  const text = confirmNoticeText(row, { afterNeverAsked: askedShown });
  if (!text) return null;
  return addLine({ key, text, tone: row.state === 'confirmed' ? 'ok' : row.state === 'refused' ? 'danger' : 'warn' });
}

// fix round 3 (aletheia-verdict-r3 L3), round 4 (: the first load after the upgrade (this browser has no
// baseline yet) records its own load time; every orphan result that was final before it is never shown, however many
// there are. Results that become final from this page on are shown as before.
const NOTICES_LOADED_AT = Date.now();
function setBaseline(rows) {
  try {
    const store = globalThis.localStorage;
    if (!store || store.getItem(BASELINE_KEY) !== null) return;
    // A browser that already used the round-3 shown-list has seen the old results through it: its baseline is 0.
    const fresh = store.getItem(SHOWN_KEY) === null;
    store.setItem(BASELINE_KEY, String(fresh ? NOTICES_LOADED_AT : 0));
    if (!fresh) return;
    const old = rows.filter(row => row?.orphan && row.id && row.state !== 'pending' && Date.parse(row.updatedAt || row.since || '') < NOTICES_LOADED_AT)
      .map(row => ({ k: `${row.detachedFrom || row.id}#${row.attempt || 1}`, t: NOTICES_LOADED_AT }));
    store.setItem(SHOWN_KEY, JSON.stringify(old.slice(-200)));   // the baseline time covers any beyond these
  } catch (_) { /* no storage: no confirmation row survives a reload either */ }
}
try { const rows = allConfirmations(); setBaseline(rows); rows.forEach(row => { if (row.orphan) confirmNoticeFor(row); }); } catch (_) {}
subscribeAll(confirmNoticeFor);
export function confirmStorageNotice() { return storageNotice; }
subscribeStorage(full => {
  const next = full ? { key: 'storage-full', text: STORAGE_FULL, tone: 'warn' } : null;
  if (Boolean(next) === Boolean(storageNotice)) return;
  storageNotice = next;
  drawConfirmNotices();
});
