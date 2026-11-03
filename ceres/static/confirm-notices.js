// fix round 1 (F9): the visible result of a signed action that a previous page left unresolved. The shared
// confirmer finds and verifies such an orphan by itself after a reload; no flow screen is open for it any more, so this
// consumer of the confirmer's state shows its final outcome in one plain line (confirmed, refused with the game's
// reason, or not sent). It reads nothing and decides nothing; it only shows what the confirmer proved.
import { allConfirmations, subscribeAll } from './shared-confirmer.js';

const CONFIRM_NOTICE_LABELS = {
  sm_land_operation: 'land action', sm_combine_cards: 'combine', sm_stake_change: 'card change',
  sm_dec_powerup_region: 'DEC stake', sm_dec_powerdown_region: 'DEC unstake', sm_claim_rewards: 'SPS claim',
  sm_lock_assets: 'deed lock', sm_unlock_assets: 'deed unlock', sm_market_purchase: 'card purchase', sm_sps: 'SPS transfer',
};

export function confirmNoticeText(row) {
  const id = row?.operations?.[0]?.[1]?.id;
  const label = CONFIRM_NOTICE_LABELS[id] || 'signed action';
  const count = row?.operations?.length > 1 ? ` (${row.operations.length} operations)` : '';
  if (row?.state === 'confirmed') return `Your ${label}${count} signed before the page reloaded is confirmed by the game.`;
  if (row?.state === 'refused') {
    const reasons = [...new Set((row.perOp || []).map(op => op.error).filter(Boolean))];
    const why = reasons.length ? `: ${reasons.join(' · ')}` : row.error ? `: ${row.error}` : '.';
    // fix round 2 (astra-r2 , shape 9): a mixed result says what went through, never "refused" alone.
    const ok = (row.perOp || []).filter(op => op.state === 'confirmed').length;
    const refused = (row.perOp || []).filter(op => op.state === 'refused').length;
    if (ok && refused) return `Part of your ${label}${count} signed before the page reloaded went through: ${ok} confirmed by the game, ${refused} refused${why}`;
    return `The game refused your ${label}${count} signed before the page reloaded${why}`;
  }
  if (row?.state === 'never-sent') return `Your ${label}${count} signed before the page reloaded was not sent. Nothing changed.`;
  return '';
}

const confirmNoticeSeen = new Map();
const confirmNoticeLines = [];

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
  box.innerHTML = confirmNoticesHtml();
  box.hidden = !confirmNoticeLines.length;
}

// fix round 2 (astra-r2 : a result is shown ONCE, also when it became final while no page showed it (another
// tab, the orphan verifier before this module loaded). Shown keys are remembered per browser.
const SHOWN_KEY = 'ceres:confirm-notices:shown';
function shownKeys() { try { const list = JSON.parse(globalThis.localStorage?.getItem(SHOWN_KEY) || '[]'); return Array.isArray(list) ? list : []; } catch (_) { return []; } }
function rememberShown(key) { try { globalThis.localStorage?.setItem(SHOWN_KEY, JSON.stringify([...shownKeys().filter(item => item !== key), key].slice(-200))); } catch (_) { /* shown in this page */ } }
const NOTICE_MAX_AGE_MS = 24 * 3600 * 1000;

export function confirmNoticeFor(row) {
  if (!row?.orphan || !row.id) return null;
  const key = `${row.id}#${row.attempt || 1}`;
  confirmNoticeSeen.set(key, row.state);
  if (row.state === 'pending' || shownKeys().includes(key) || confirmNoticeLines.some(line => line.key === key)) return null;
  if (Date.now() - Date.parse(row.updatedAt || row.since || '') > NOTICE_MAX_AGE_MS) return null;
  const text = confirmNoticeText(row);
  if (!text) return null;
  rememberShown(key);
  const line = { key, text, tone: row.state === 'confirmed' ? 'ok' : row.state === 'refused' ? 'danger' : 'warn' };
  confirmNoticeLines.push(line);
  if (confirmNoticeLines.length > 3) confirmNoticeLines.shift();
  drawConfirmNotices();
  return line;
}

try { allConfirmations().forEach(row => { if (row.orphan) confirmNoticeFor(row); }); } catch (_) {}
subscribeAll(confirmNoticeFor);
