// card-checkout.js -- / : checkout of the Market cart, "Buy from Ceres". Opened by the cart's Checkout button
// (card-cart.js, loaded on demand). (maintainer): Ceres signs the market purchase with Keychain / HiveAuth, Active key, with
// the usual confirm and outcome checks; currency chosen at checkout ("Ask at checkout"); "allow credit purchase as well".
// (maintainer): sold listings struck through and the reduced cart re-confirmed; any price rise amber and re-confirmed, no
// currency preselected; up to 150 listings per transaction, larger carts split; two maintainer test buys of the cheapest card first.
// Flow: quote (the server re-reads every listing by id) -> currency -> "cannot be undone" confirm -> fresh server re-check and
// the exact operation(s) -> the purchase intent on disk BEFORE the wallet is asked -> one Active wallet prompt per transaction
// > the game's record per listing. An unknown outcome is never permission to buy again ( S3): its listings stay locked
// and only "Check again" is offered. Waits follow (3 min confirm, 5 min never-sent, 65 min late-broadcast watch).
// No market id, uid or seller is ever shown; the operation the wallet signs carries the listing ids, as it must.
import { balancesChanged, onBalancesChanged } from './balances-bus.js';
import { tipAttrs } from './tooltip.js';
import { CB_FOIL_LABELS } from './card-browser-model.js';
import { cartPriceHtml } from './card-cart.js';
import { walletAccount, walletClockTick, walletControlsHtml, walletReady, walletSignLabelHtml, walletWaitHtml } from './wallet.js';
import { requestBroadcastThroughGate } from './broadcast-gate.js';

export const VERIFY_SCHEDULE_MS = [0, 2000, 4000, 8000, 12000, 18000, 26000, 36000, 50000, 70000];
export const RECHECK_MS = 10000;                 // 10 s cadence after the schedule
export const CONFIRM_TTL_MS = 3 * 60 * 1000;     // the game confirms within 3 minutes, or the row turns red
export const NEVER_SENT_AFTER_MS = 3900 * 1000; // Hive expiry plus the existing inclusion grace
export const LATE_WATCH_MS = 65 * 60 * 1000;     // legacy constant; unresolved intents remain reserved until definitive evidence
export const UNDO_TEXT = 'A market purchase cannot be undone. As soon as the game accepts it, the payment leaves your account and the cards are yours.';
export const UNKNOWN_TEXT = 'Your wallet did not say whether this purchase was sent. Do not buy these listings again until Ceres has an answer.';
const MAX_ROUNDS = 400;
const DEC_ICON = '/static/assets/land/token-dec.png';
// the site's own icon, downloaded once
// (https://d36mxiodymuqjm.cloudfront.net/website/icons/icon_credits.png, manifest.json), shown after a Credits amount as DEC shows its.
const CREDITS_ICON = '/static/assets/land/token-credits.png';
export const AUTO_CLOSE_MS = 1500;               // the green summary stays this long, then the dialog closes by itself
const OPEN_STATES = ['pending', 'asked', 'unknown', 'sent', 'timeout'];
const DONE_STATES = ['confirmed', 'failed', 'not_sent', 'declined', 'skipped'];

const co = {
  player: null,
  deps: null,
  stage: 'closed',      // loading | review | confirm | signing | outcome | error | closed
  quote: null,
  currency: null,
  note: null,           // { tone: ok|warn|danger, text }
  run: null,            // { token, currency, txs: [...] } -- mirrored in localStorage while open
  names: new Map(),     // lkey -> display row (from the cart; never ids)
  test: false,
  busy: false,
  lastFocus: null,
};

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const $ = (id) => (typeof document !== 'undefined' && document.getElementById ? document.getElementById(id) : null);
function usd(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return `$${new Intl.NumberFormat('en-US', { minimumFractionDigits: n < 1 ? 3 : 2, maximumFractionDigits: n < 1 ? 3 : 2 }).format(n)}`;
}
const fixed3 = (value) => (Number.isFinite(Number(value)) ? Number(value).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 3 }) : '—');
const whole = (value) => (Number.isFinite(Number(value)) ? Math.round(Number(value)).toLocaleString('en-US') : '—');
export function amountText(currency, value) {
  return currency === 'DEC' ? `${fixed3(value)} DEC` : `${whole(value)} credits`;
}
// The amount with its currency icon after it (), escaped.
export function amountHtml(currency, value) {
  const icon = currency === 'DEC' ? DEC_ICON : CREDITS_ICON;
  return `<span class="co-amount">${esc(amountText(currency, value))}<img class="cm-dec-icon co-cur-icon" src="${icon}" alt="" width="16" height="16"></span>`;
}
// an honest elapsed clock ("0:42") for "Checking with the game…".
export function elapsedText(since, now = Date.now()) {
  const s = Math.max(0, Math.floor((now - Number(since)) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
function checkingHtml(since, what) {
  const at = Date.parse(since || '') || Date.now();
  return `<p class="cm-note co-checking" data-state="warn" role="status"><span class="spinner" aria-hidden="true"></span><span>${esc(`Checking with the game… ${what} was sent; Ceres asks the game every few seconds for up to 3 minutes.`)}</span><b class="co-clock" data-co-since="${at}">${esc(elapsedText(at))}</b></p>`;
}
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// ── storage: the open purchase ( S10 pattern of wallet-lp.js) and the late-broadcast watch list ───────────────────
const RUN_KEY = (player) => `ceres:market-buy:v1:${String(player).toLowerCase()}`;
const WATCH_KEY = (player) => `ceres:market-buy-watch:v1:${String(player).toLowerCase()}`;
export function runIsOpen(run) {
  return !!run && Array.isArray(run.txs) && (run.txs.some((tx) => OPEN_STATES.includes(tx.state) && tx.state !== 'pending')
    || (run.groups || []).some((g) => GROUP_OPEN.includes(g.state)));  // a combine still to sign or to confirm
}
// Buy & max: a maxed-card group of the cart (two or more listings of one print, one "Add maxed card" press) is bought in
// ONE transaction; once every listing of it is confirmed bought, Ceres builds the combine of exactly those cards and asks for a
// second signature (Posting). Never automatic: the maintainer presses "Sign the combine".
export const GROUP_OPEN = ['waiting', 'building', 'ready', 'review-changed', 'combine-asked', 'combine-sent', 'combine-unknown', 'combine-timeout'];
export const COMBINE_RETRY_MS = 60000;       // the cards are not in the collection yet: asked again each minute
export const COMBINE_BUILD_TTL_MS = 5 * 60 * 1000;
export const TWO_SIGNATURES = '2 signatures: buy, then combine.';
export function maxGroupsOf(items) {
  const groups = new Map();
  for (const it of items || []) {
    if (!it.group || !String(it.group).startsWith('max-')) continue;
    if (!groups.has(it.group)) groups.set(it.group, { group: it.group, name: it.name || 'Card', lkeys: [] });
    groups.get(it.group).lkeys.push(it.lkey);
  }
  return [...groups.values()].filter((g) => g.lkeys.length >= 2 && g.lkeys.length <= 150);
}
// After the purchase is confirmed: every listing of the group bought -> the combine is built; otherwise nothing is combined.
export function groupVerdict(group, tx) {
  if (!tx || tx.state !== 'confirmed') return tx && ['failed', 'not_sent', 'declined', 'skipped'].includes(tx.state) ? 'none' : 'waiting';
  const outcome = new Map((tx.result?.items || []).map((r) => [r.lkey, r.outcome]));
  const bought = group.lkeys.filter((k) => outcome.get(k) === 'bought');
  if (bought.length === group.lkeys.length) return 'building';
  return bought.length ? 'partial' : 'none';
}
export function readRun(player) {
  try { const run = JSON.parse(localStorage.getItem(RUN_KEY(player)) || 'null'); return run && Array.isArray(run.txs) ? run : null; } catch (_) { return null; }
}
// Returns false when the record could not be written: the wallet is then NOT asked.
export function writeRun(player, run) {
  try {
    const current = readRun(player);
    if (runIsOpen(current) && current.token && run && current.token !== run.token) return false;  // another tab's purchase
    if (run && (runIsOpen(run) || run.txs.some((tx) => tx.state === 'pending'))) localStorage.setItem(RUN_KEY(player), JSON.stringify({ ...run, savedAt: Date.now() }, (key, value) => (key === 'checking' || key === 'building' ? undefined : value)));
    else localStorage.removeItem(RUN_KEY(player));
    return true;
  } catch (_) { return false; }
}
export function readWatch(player) {
  try { const list = JSON.parse(localStorage.getItem(WATCH_KEY(player)) || '[]'); return Array.isArray(list) ? list.filter((w) => w?.nonce) : []; } catch (_) { return []; }
}
function watchAskedAt(item) { for (const raw of [item?.asked_at, item?.signed_at, item?.at]) { const ms = raw == null ? NaN : Date.parse(raw); if (Number.isFinite(ms)) return ms; } return null; }
function writeWatch(player, list) { try { if (list.length) localStorage.setItem(WATCH_KEY(player), JSON.stringify(list)); else localStorage.removeItem(WATCH_KEY(player)); } catch (_) { /* page memory */ } }
async function withRunLock(player, work) {
  const locks = globalThis.navigator?.locks;
  if (locks?.request) return locks.request(`ceres:market-buy:${String(player).toLowerCase()}`, async () => work());
  return work();
}
// The listings an unresolved purchase holds: the cart shows them locked and never offers them for a second purchase.
export function checkoutLockedKeys(player) {
  const run = readRun(player);
  const keys = new Set();
  for (const tx of run?.txs || []) if (['asked', 'unknown', 'sent', 'timeout'].includes(tx.state)) for (const k of tx.lkeys || []) keys.add(k);
  return keys;
}

// ── server ───────────────────────────────────────────────────────────────────────────────────────────────────────────
async function post(path, body) {
  const response = await fetch(`/api/market/checkout/${path}?player=${encodeURIComponent(co.player)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), cache: 'no-store',
  });
  let result = null;
  try { result = await response.json(); } catch (_) { result = null; }
  if (!response.ok) {
    const failure = new Error(response.status === 429 ? 'Too many checkout reads in a short time; wait a minute and try again.'
      : (result && typeof result.detail === 'string' ? result.detail : `The checkout could not be read (${response.status}).`));
    failure.status = response.status; failure.body = result;
    throw failure;
  }
  return result;
}

// By-id inclusion is read-only; the server waits through Hive's 3,900-second expiry grace.
async function expiredAndAbsent(tx) {
  if (!tx?.tx_id || !tx.asked_at) return false;
  try {
    const response = await fetch(`/api/op-inclusion?player=${encodeURIComponent(co.player)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tx_id: tx.tx_id, created_at: tx.asked_at }), cache: 'no-store',
    });
    if (!response.ok) return false;
    return (await response.json())?.included === false;
  } catch (_) { return false; }
}

// ── the quote view model (pure; tested) ──────────────────────────────────────────────────────────────────────────────
export function rowState(row) {
  if (row.status === 'ok') return 'ok';
  if (row.status === 'price_changed') return row.rise ? 'rise' : 'drop';
  if (row.status === 'own') return 'own';
  return 'sold';
}
export function currencyChoice(quote, currency) {
  const c = quote?.currencies?.[currency];
  return c ? { ...c, currency, disabled: !c.enough || !quote.signing?.enabled } : null;
}
// What must be re-confirmed: struck listings and price rises. No currency is preselected after a change ().
export function changeNotes(quote) {
  const ch = quote?.changes || {};
  const notes = [];
  if (ch.sold) notes.push({ tone: 'danger', text: `${ch.sold === 1 ? 'One listing is' : `${ch.sold} listings are`} no longer for sale and ${ch.sold === 1 ? 'is' : 'are'} struck out. The total below is for the rest.` });
  if (ch.price_rise) notes.push({ tone: 'warn', text: `${ch.price_rise === 1 ? 'One price rose' : `${ch.price_rise} prices rose`} since you added ${ch.price_rise === 1 ? 'it' : 'them'} (amber). Check the new total.` });
  if (ch.price_drop) notes.push({ tone: 'ok', text: `${ch.price_drop === 1 ? 'One price fell' : `${ch.price_drop} prices fell`} since you added ${ch.price_drop === 1 ? 'it' : 'them'}.` });
  return notes;
}

// ── dialog ───────────────────────────────────────────────────────────────────────────────────────────────────────────
function ensureOverlay() {
  let overlay = $('co-overlay');
  if (overlay) return overlay;
  overlay = document.createElement('div');
  overlay.id = 'co-overlay';
  overlay.className = 'slot-hazard-overlay cm-dialog-overlay co-overlay';
  overlay.hidden = true;
  overlay.innerHTML = '<div class="slot-hazard-panel cm-dialog-panel co-panel" role="dialog" aria-modal="true" aria-labelledby="co-title"><header><h3 id="co-title">Checkout</h3><button type="button" class="hazard-close ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" data-co-action="close" aria-label="Close">×</button></header><div class="slot-hazard-body cm-dialog-body co-body" id="co-body" aria-live="polite"></div><footer class="cm-dialog-foot co-foot" id="co-foot"></footer></div>';
  overlay.addEventListener('click', (event) => {
    // a click beside the wallet that sits over this dialog closes the wallet only, never the checkout under it.
    if (event.target === overlay) { if (!document.getElementById('regional-dec-drawer')?.open) closeCheckout(); return; }
    onClick(event);
  });
  overlay.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closeCheckout(); }
  });
  document.body.appendChild(overlay);
  window.addEventListener('resize', syncNarrow);
  window.addEventListener('ceres:wallet-changed', () => { if (co.stage === 'confirm') render(); });
  window.addEventListener('ceres:wallet-closed', () => { requoteAfterWallet(); });
  onBalancesChanged(({ assets }) => { if (assets.some(a => a === 'DEC' || a === 'CREDITS') && ['review', 'confirm', 'error'].includes(co.stage)) requoteAfterWallet(); }, { player: () => co.player });
  return overlay;
}
function syncNarrow() {
  const panel = document.querySelector?.('.co-panel');
  if (panel) panel.classList.toggle('is-narrow', panel.getBoundingClientRect().width < 640);
}
export function closeCheckout() {
  if (co.stage === 'signing') return;  // the wallet prompt is open: the dialog stays until it answers
  const overlay = $('co-overlay');
  if (overlay) overlay.hidden = true;
  co.stage = co.run && runIsOpen(co.run) ? 'outcome' : 'closed';
  if (document.body?.dataset) delete document.body.dataset.coDialog;
  co.deps?.onClose?.();
  if (co.lastFocus?.focus && document.contains?.(co.lastFocus)) co.lastFocus.focus();
}

function itemLabel(lkey) {
  const it = co.names.get(lkey);
  if (!it) return { name: 'Card', meta: '' };
  const meta = [it.level ? `Lv ${it.level}` : '', it.bcx ? `${whole(it.bcx)} CC` : '', CB_FOIL_LABELS[it.foil] || ''].filter(Boolean).join(' · ');
  return { name: it.name || 'Card', meta };
}

const STATE_CHIP = {
  ok: '',
  rise: (r) => `<span class="cm-chip is-warn co-chip"${tipAttrs({ title: 'Price rose', body: `Was ${usd(r.was_usd)} when you added it; ${usd(r.price_usd)} now.` })}>Price rose · was ${esc(usd(r.was_usd))}</span>`,
  drop: (r) => `<span class="cm-chip is-ok co-chip">Price fell · was ${esc(usd(r.was_usd))}</span>`,
  own: () => '<span class="cm-chip is-warn co-chip">Your own listing</span>',
  sold: (r) => `<span class="cm-chip is-danger co-chip">${esc(r.status === 'sold' ? 'Sold' : r.status === 'unresolved' ? 'Not found' : 'No longer on sale')}</span>`,
};
export function quoteRowsHtml(quote, decPrice) {
  return (quote.items || []).map((r) => {
    const state = rowState(r);
    const { name, meta } = itemLabel(r.lkey);
    const price = r.price_usd ?? r.seen_usd;
    const chip = typeof STATE_CHIP[state] === 'function' ? STATE_CHIP[state](r) : '';
    const reason = state === 'sold' || state === 'own' ? `<span class="co-reason">${esc(r.reason || '')}</span>` : '';
    return `<div class="co-row" data-state="${state}" role="row"><span class="co-name" role="cell"><b>${esc(name)}</b><small>${esc(meta)}</small></span><span class="co-price" role="cell">${cartPriceHtml(price, decPrice && price ? price / decPrice : null)}</span><span class="co-tags" role="cell">${chip}${reason}</span></div>`;
  }).join('');
}

function currencyButton(quote, currency) {
  const c = currencyChoice(quote, currency);
  if (!c) return '';
  const pressed = co.currency === currency;
  const why = c.enough ? '' : `<span class="co-cur-why" data-state="danger">${esc(c.reason || '')}${c.short ? ` Short by ${esc(amountText(currency, c.short))}.` : ''}</span>`;
  return `<span class="co-cur"><button type="button" class="ceres-btn ceres-btn--choice co-cur-btn" data-control-density="comfortable" data-co-action="currency" data-co-currency="${currency}" aria-pressed="${pressed}"${c.disabled ? ' disabled' : ''}><span class="ceres-btn__check" aria-hidden="true"></span><span class="co-cur-label">Pay with ${currency === 'DEC' ? 'DEC' : 'Credits'}</span><span class="co-cur-total">${amountHtml(currency, c.total)}</span></button><span class="co-cur-bal">You have ${amountHtml(currency, c.balance)}</span>${why}</span>`;
}

function notesHtml(notes) {
  return notes.map((n) => `<p class="cm-note co-note" data-state="${n.tone === 'danger' ? 'sold' : n.tone}" role="status">${esc(n.text)}</p>`).join('');
}

export function reviewHtml(quote) {
  const t = quote.totals || {};
  const notes = changeNotes(quote);
  if (co.note) notes.unshift(co.note);
  if (!quote.signing?.enabled) notes.push({ tone: 'warn', text: quote.signing?.reason || 'Buying from Ceres is not available.' });
  const txs = (t.transactions || []).length;
  const twice = maxGroupsOf(co.items).length ? `<p class="co-split co-two">${esc(`Buy & max: ${TWO_SIGNATURES} The combine is offered once every copy is confirmed bought.`)}</p>` : '';
  const split = txs > 1 ? `<p class="co-split">${esc(`${plural(t.count, 'listing', 'listings')} are bought in ${txs} purchases of up to ${quote.max_per_transaction || 150} listings each; your wallet asks ${txs} times.`)}</p>` : '';
  const test = quote.test_buy ? `<div class="co-test"><span>Owner test buy (local build): the single cheapest listing on the market.</span><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-co-action="test-buy">${co.test ? 'Cheapest card again' : 'Test buy: cheapest card'}</button></div>` : '';
  // total, currency choice and
  // Continue sit in a bar at the top of the scroll area (sticky); the listings follow below.
  return `${payBarHtml(quote)}${test}${notesHtml(notes)}${split}${twice}<section class="cm-section"><h4>${esc(plural((quote.items || []).length, 'listing', 'listings'))} <small>${esc(`${t.count || 0} can be bought`)}</small></h4><div class="co-rows" role="table">${quoteRowsHtml(quote, quote.dec_price)}</div></section>`;
}
function totalHtml(quote) {
  const c = co.currency ? currencyChoice(quote, co.currency) : null;
  return `<span class="co-pay-total">Total · ${c ? `${amountHtml(co.currency, c.total)} · ` : ''}${esc(usd(quote.totals?.usd))}</span>`;
}
export function payBarHtml(quote) {
  const t = quote.totals || {};
  const c = co.currency ? currencyChoice(quote, co.currency) : null;
  const ok = c && !c.disabled && !co.busy;
  const go = `<button type="button" class="ceres-btn ceres-btn--primary co-pay-go" data-control-density="comfortable" data-co-action="continue"${ok ? '' : ' disabled'}>${co.currency ? 'Continue' : 'Choose DEC or Credits'}</button>`;
  const choose = t.count ? `<div class="co-cur-row" role="group" aria-label="Pay with">${currencyButton(quote, 'DEC')}${currencyButton(quote, 'CREDITS')}</div>` : '';
  return `<section class="co-paybar" aria-label="Total and payment"><div class="co-pay-head">${totalHtml(quote)}${t.count ? go : ''}</div>${choose}</section>`;
}

function confirmHtml(quote) {
  const cur = co.currency;
  const c = currencyChoice(quote, cur);
  const t = quote.totals;
  const txs = (t.transactions || []).length;
  const ready = walletReady(co.player);
  const buy = `<button type="button" class="ceres-btn ceres-btn--primary co-sign" data-control-density="comfortable" data-co-action="sign"${ready && !co.busy ? '' : ' disabled'}>${walletSignLabelHtml('Buy now with', co.player)}</button>`;
  return `<section class="co-paybar co-confirm-pay" aria-label="Total and payment"><span class="co-pay-total co-sum">Buy ${esc(plural(t.count, 'listing', 'listings'))} for ${amountHtml(cur, c.total)} (${esc(usd(t.usd))})</span>
    ${txs > 1 ? `<p class="co-split">${esc(`Your wallet asks ${txs} times, once per purchase of up to ${quote.max_per_transaction || 150} listings.`)}</p>` : ''}
    <p class="co-pay-balance">You have ${amountHtml(cur, c.balance)}</p>
    <p class="co-key">Pay with ${esc(cur === 'DEC' ? 'DEC' : 'Credits')}. Your wallet asks for your <b>Active</b> key.${maxGroupsOf(co.items).length ? ` ${esc(TWO_SIGNATURES)} The combine (Posting key) is asked for only after every copy is confirmed bought.` : ''}</p>
    ${ready ? '' : `<div class="co-wallet">${walletControlsHtml(co.player, 'co-wallet')}</div>`}
    <p class="cm-note co-undo" data-state="sold" role="note">${esc(UNDO_TEXT)}</p>
    <div class="co-pay-head">${buy}</div></section>`;
}

// red only for a definite no -- the game's
// record says success:false (its text, escaped), or a listing the game's success record did not include. A wait that ran out, a
// record not found yet or one that differs is amber "Not confirmed yet" with Check again; it never permits signing again.
const OUTCOME_CHIP = {
  bought: ['ok', 'Bought'],
  not_bought: ['danger', 'Not bought'],
  pending: ['warn', 'Checking with the game…'],
  asked: ['warn', 'Waiting for your approval'],
  unknown: ['warn', 'Not confirmed yet'],
  not_sent: ['danger', 'Not sent · nothing spent'],
  declined: ['danger', 'Declined in the wallet · nothing spent'],
  skipped: ['danger', 'Not sent · nothing spent'],
  timeout: ['warn', 'Not confirmed yet'],
};
export const NOT_CONFIRMED_TEXT = 'Not confirmed yet: the game has no record of this purchase after 3 minutes. It may still arrive. Press Check again; do not buy these listings again.';
export const COMBINE_NOT_CONFIRMED_TEXT = 'Not confirmed yet: the game has no record of the combine after 3 minutes. It may still arrive. Press Check again; do not sign it again.';
// Per listing: the tx's state, refined by the game's per-listing answer once confirmed.
export function listingOutcome(tx, lkey) {
  if (tx.state === 'confirmed') return (tx.result?.items || []).find((r) => r.lkey === lkey)?.outcome || 'not_bought';
  if (tx.state === 'failed') return 'not_bought';
  if (tx.state === 'asked') return 'asked';
  if (tx.state === 'pending' || tx.state === 'sent') return 'pending';
  return tx.state;  // unknown, timeout, not_sent, declined, skipped
}
// The line of one purchase. `confirmed` with every listing bought reads "Bought 17 of 17".
export function txLineHtml(tx) {
  if (tx.state === 'confirmed') {
    const r = tx.result || {};
    const paid = r.paid_usd ? ` Paid ${usd(r.paid_usd)}${r.paid_dec ? ` (${fixed3(r.paid_dec)} DEC at the game's rate)` : ''}.` : '';
    const tone = r.fill === 'all' ? 'ok' : r.fill === 'none' ? 'sold' : 'warn';
    const text = r.fill === 'none' ? 'The game confirmed the transaction but bought none of these listings. Nothing was spent on them.'
      : `Bought ${r.bought} of ${r.requested}.${paid}${r.fill === 'all' ? '' : ' The rest were not bought.'}`;
    return `<p class="cm-note co-result" data-state="${tone}" role="status">${esc(text)}</p>`;
  }
  if (tx.state === 'failed') return `<p class="cm-note co-result" data-state="sold" role="status">The game refused this purchase: ${esc(tx.message || 'no reason given')}. Nothing was bought.</p>`;
  if (tx.state === 'unknown') return `<p class="cm-note co-result" data-state="warn" role="status">${esc(tx.message ? `Not confirmed yet: ${tx.message} Press Check again; do not buy these listings again.` : tx.historyUnreadable ? 'Ceres could not read your account history; it keeps trying. Do not buy these listings again until this clears.' : UNKNOWN_TEXT)}</p>`;
  if (tx.state === 'timeout') return `<p class="cm-note co-result" data-state="warn" role="status">${esc(NOT_CONFIRMED_TEXT)}</p>`;
  if (tx.state === 'not_sent') return '<p class="cm-note co-result" data-state="sold" role="status">The wallet transaction expired without being included. Nothing was spent.</p>';
  if (tx.state === 'declined') return '<p class="cm-note co-result" data-state="sold" role="status">Declined in the wallet. Nothing was sent.</p>';
  if (tx.state === 'skipped') return '<p class="cm-note co-result" data-state="sold" role="status">Not sent: an earlier purchase of this checkout did not go through, so Ceres stopped. Nothing was spent on these.</p>';
  // the same reminder as the wallet swap / pool / transfer (wallet.js walletWaitHtml), with the wallet request's own clock.
  if (tx.state === 'asked') return walletWaitHtml(co.player, 'Active', { since: Date.parse(tx.asked_at || '') || Date.now() });
  return checkingHtml(tx.checkFrom || tx.signed_at || tx.asked_at, 'Your purchase');
}
// everything confirmed green -- every purchase bought in full and every Buy & max combine done. Only then the dialog
// closes by itself; never on amber or red, never while anything is still open or skipped.
export function runAllGreen(run) {
  return !!run && Array.isArray(run.txs) && run.txs.length > 0 && run.txs.every((tx) => tx.state === 'confirmed' && tx.result?.fill === 'all')
    && (run.groups || []).every((g) => g.state === 'combined');
}
function summaryHtml(run) {
  if (!runAllGreen(run)) return '';
  const bought = run.txs.reduce((n, tx) => n + (tx.result?.bought || 0), 0);
  const groups = run.groups || [];
  const text = `All done: bought ${bought} of ${bought}${groups.length ? `, ${groups.length === 1 ? 'combined into one max-level card' : `combined into ${groups.length} max-level cards`}` : ''}.`;
  return `<p class="cm-note co-summary" data-state="ok" role="status">${esc(text)}</p>`;
}
export function outcomeHtml(run) {
  const allGreen = runAllGreen(run);
  const parts = run.txs.map((tx, i) => {
    const outcomes = (tx.lkeys || []).map((k) => listingOutcome(tx, k));
    const uniformBought = tx.state === 'confirmed' && outcomes.length > 0 && outcomes.every((out) => out === 'bought');
    const rows = (tx.lkeys || []).map((k, n) => {
      const out = outcomes[n];
      const [tone, label] = OUTCOME_CHIP[out] || ['warn', 'Checking…'];
      const { name, meta } = itemLabel(k);
      const price = tx.copy_prices_dec?.[k] ?? co.names.get(k)?.price_dec ?? (co.names.get(k)?.price_usd != null && co.quote?.dec_price ? co.names.get(k).price_usd / co.quote.dec_price : null);
      const priceHtml = `<span class="co-copy-dec">${price == null ? `<span class="co-amount">— DEC<img class="cm-dec-icon co-cur-icon" src="${DEC_ICON}" alt="" width="16" height="16"></span>` : amountHtml('DEC', price)}</span>`;
      const status = uniformBought || (tx.state !== 'confirmed' && new Set(outcomes).size === 1)
        ? `<span class="sr-only">${esc(label)}</span>` : `<span class="co-outcome-label is-${tone}">${esc(label)}</span>`;
      return `<div class="co-row co-outcome-row" data-state="out-${tone}" role="row"><span class="co-name" role="cell"><b>${esc(name)}</b><small>${esc(meta)}</small></span><span class="co-price" role="cell">${priceHtml}</span><span class="co-tags" role="cell">${status}</span></div>`;
    }).join('');
    const head = run.txs.length > 1 ? `<h4>Purchase ${i + 1} of ${run.txs.length} <small>${esc(plural(tx.lkeys.length, 'listing', 'listings'))} · ${amountHtml(tx.currency, tx.price)}</small></h4>` : '';
    const detail = `<div class="co-rows" role="table">${rows}</div>`;
    const list = uniformBought ? `<details class="co-outcome-detail"><summary>${esc(plural(tx.lkeys.length, 'bought copy', 'bought copies'))} · item prices</summary>${detail}</details>` : detail;
    return `<section class="cm-section co-tx" data-co-tx="${i}">${head}${allGreen ? '' : txLineHtml(tx)}${list}</section>`;
  }).join('');
  const bought = run.txs.every((tx) => tx.state === 'confirmed');
  return summaryHtml(run) + (bought ? (allGreen ? '' : groupsHtml(run)) + parts : parts + groupsHtml(run));
}

const GROUP_LINE = {
  waiting: ['warn', 'Waiting for the purchase to be confirmed; then Ceres prepares the combine.'],
  building: ['warn', 'All copies are bought. Ceres is preparing the combine (the game can take a minute to hand the cards over)…'],
  ready: ['warn', 'All copies are bought. Sign the combine to make one max-level card (Posting key).'],
  'combine-unknown': ['warn', 'Your wallet did not say whether the combine was sent. Do not combine these cards again until Ceres has an answer.'],
  'combine-timeout': ['warn', COMBINE_NOT_CONFIRMED_TEXT],
  'not-ready': ['warn', 'Not ready yet: the game had not handed every card over within 5 minutes of the purchase.'],
  combined: ['ok', 'Combined: one max-level card.'],
  'combine-failed': ['danger', 'The game refused the combine.'],
  partial: ['warn', 'Not every copy was bought, so nothing is combined.'],
  none: ['warn', 'The purchase did not go through, so nothing is combined.'],
  skipped: ['warn', 'Combine skipped. You can combine these cards later in Find combinables.'],
};
// "Combined: 115 CC, max level." -- max level when the game's CC is the maxed card's planned CC (the build's resulting_bcx).
export function combinedText(g) {
  const bcx = Number(g.bcx);
  if (g.bcx === null || g.bcx === undefined || !Number.isFinite(bcx)) return GROUP_LINE.combined[1];
  const max = g.resulting_bcx !== null && g.resulting_bcx !== undefined && bcx === Number(g.resulting_bcx);
  return `Combined: ${whole(bcx)} CC${max ? ', max level' : ''}.`;
}
export function groupLineHtml(g) {
  // the same wallet reminder as the purchase (and the wallet swap), Posting key, with its clock
  if (g.state === 'combine-asked') return walletWaitHtml(co.player, 'Posting', { since: Date.parse(g.asked_at || '') || Date.now() });
  if (g.state === 'combine-sent') return checkingHtml(g.check_from || g.signed_at || g.asked_at, 'Your combine');
  const [tone, text] = GROUP_LINE[g.state] || ['warn', 'Checking…'];
  const extra = g.state === 'partial' ? ` ${g.missing} of ${g.lkeys.length} listings were not bought. Combine what arrived in Find combinables.`
    : ['combine-failed', 'not-ready', 'combine-timeout'].includes(g.state) && g.message ? ` ${g.message}` : '';
  const spin = ['building', 'waiting'].includes(g.state) ? '<span class="spinner" aria-hidden="true"></span>' : '';
  return `<p class="cm-note co-group-line" data-state="${tone === 'danger' ? 'sold' : tone}" role="status">${spin}${esc((g.state === 'combined' ? combinedText(g) : text) + extra)}</p>`;
}
export function groupTone(g) {
  if (g.state === 'combined') return 'ok';
  return g.state === 'combine-failed' ? 'danger' : 'warn';
}
export function groupsHtml(run) {
  return (run.groups || []).map((g, i) => {
    const buttons = g.state === 'ready'
      ? `<button type="button" class="ceres-btn ceres-btn--primary co-combine-btn" data-control-density="comfortable" data-co-action="combine-sign" data-co-group="${i}">${walletSignLabelHtml('Sign the combine with', co.player)}</button><button type="button" class="ceres-btn ceres-btn--secondary co-combine-btn" data-control-density="comfortable" data-co-action="combine-skip" data-co-group="${i}">Not now</button>`
      : g.state === 'review-changed' ? `<button type="button" class="ceres-btn ceres-btn--primary co-combine-btn" data-control-density="comfortable" data-co-action="combine-confirm" data-co-group="${i}">Review the updated combine and continue</button>`
      : g.state === 'not-ready' ? `<button type="button" class="ceres-btn ceres-btn--secondary co-combine-btn" data-control-density="comfortable" data-co-action="combine-retry" data-co-group="${i}">Try again</button><button type="button" class="ceres-btn ceres-btn--secondary co-combine-btn" data-control-density="comfortable" data-co-action="combine-skip" data-co-group="${i}">Not now</button>`
      : g.state === 'combine-timeout' || g.state === 'combine-unknown' ? `${g.combine_tx ? `<button type="button" class="ceres-btn ceres-btn--primary co-combine-btn" data-control-density="comfortable" data-co-action="combine-check" data-co-group="${i}">Check again</button>` : ''}<button type="button" class="ceres-btn ceres-btn--secondary co-combine-btn" data-control-density="comfortable" data-co-action="open-combinables">Find combinables</button><button type="button" class="ceres-btn ceres-btn--secondary co-combine-btn" data-control-density="comfortable" data-co-action="combine-skip" data-co-group="${i}">Stop tracking</button>`
      : g.state === 'partial' ? '<button type="button" class="ceres-btn ceres-btn--secondary co-combine-btn" data-control-density="comfortable" data-co-action="open-combinables">Find combinables</button>' : '';
    const freshReview = g.state === 'review-changed' ? `<div class="cm-note co-group-line" data-state="warn" role="status"><p>The live combine changed. Fresh review:</p><ul>${(g.witness?.cards || []).map((card) => {
      const survivor = String(g.witness?.survivor) === String(card.uid);
      const destroyed = (g.witness?.destroyed || []).some((uid) => String(uid) === String(card.uid));
      const edition = card.edition == null ? '' : ` · Edition ${card.edition}`;
      const foil = card.foil == null ? '' : ` · ${card.foil ? 'Gold foil' : 'Regular foil'}`;
      return `<li>${esc(g.name)} · ${whole(card.bcx)} CC${esc(edition + foil)} · ${survivor ? 'survives' : destroyed ? 'destroyed' : 'included'}</li>`;
    }).join('')}</ul><p>Result: ${esc(whole(g.witness?.resulting_bcx))} CC. Review the updated result before continuing.</p></div>` : '';
    return `<section class="cm-section co-group" data-state="${groupTone(g)}"><h4>Max ${esc(g.name)} <small>${esc(plural(g.lkeys.length, 'copy', 'copies'))} · ${esc(TWO_SIGNATURES)}</small></h4>${groupLineHtml(g)}${freshReview}${buttons ? `<div class="co-group-actions">${buttons}</div>` : ''}</section>`;
  }).join('');
}

// ask the server for the combine of the bought group, again each minute while the cards are not handed over yet.
async function buildCombine(g) {
  if (g.building) return;
  g.building = true; g.state = 'building'; g.since = g.since || Date.now(); save();
  const tx = co.run.txs[g.tx];
  try {
    while (co.run && g.state === 'building') {
      let answer = null;
      try { answer = await post('combine', { tx_id: tx.tx_id, nonce: tx.nonce, expected_payload: tx.payload, lkeys: g.lkeys }); } catch (_) { answer = null; }
      if (answer?.status === 'ready' && Array.isArray(answer.operation)) { g.operation = answer.operation; g.resulting_bcx = answer.resulting_bcx ?? null; g.witness = answer.witness || null; g.state = 'ready'; break; }
      if (answer?.status === 'partial') { g.state = 'partial'; g.missing = (answer.missing || []).length; break; }
      if (answer?.status === 'failed') { g.state = 'none'; g.message = answer.message || ''; break; }  // the game refused the purchase
      if (Date.now() - g.since > COMBINE_BUILD_TTL_MS) { g.state = 'not-ready'; g.message = answer?.message || ''; break; }
      await new Promise((resolve) => setTimeout(resolve, COMBINE_RETRY_MS));
    }
  } finally { g.building = false; save(); }
}

async function signCombine(g) {
  if (g.state !== 'ready' || !g.operation || co.busy) return;
  if (walletAccount() !== co.player) return;
  co.busy = true;
  const purchase = co.run.txs[g.tx];
  let fresh;
  try { fresh = await post('combine', { tx_id: purchase.tx_id, nonce: purchase.nonce, expected_payload: purchase.payload, lkeys: g.lkeys }); }
  catch (_) { co.busy = false; g.state = 'combine-unknown'; g.message = 'The live combine could not be checked. Check again before signing.'; save(); return; }
  if (fresh?.status !== 'ready' || !Array.isArray(fresh.operation)) { co.busy = false; g.state = fresh?.status === 'failed' ? 'combine-failed' : 'not-ready'; g.message = fresh?.message || ''; save(); return; }
  const oldWitness = JSON.stringify(g.witness || null);
  const newWitness = JSON.stringify(fresh.witness || null);
  g.operation = fresh.operation; g.resulting_bcx = fresh.resulting_bcx ?? null;
  if (!g.witness || oldWitness !== newWitness) {
    g.witness = fresh.witness || null; g.state = 'review-changed'; g.message = 'The live combine changed. Review it before signing.';
    co.busy = false; save(); return;
  }
  co.busy = false;
  co.busy = true; g.state = 'combine-asked'; g.asked_at = new Date().toISOString();
  if (!writeRun(co.player, co.run)) { g.state = 'ready'; co.busy = false; co.note = { tone: 'danger', text: 'This browser could not store the combine before signing, so the wallet was not opened.' }; render(); return; }
  render();
  let failure = null;
  try {
    const result = await requestBroadcastThroughGate(co.player, [g.operation], (response) => {
      const id = response?.result?.id || response?.result?.tx_id || response?.tx_id;
      if (typeof id === 'string' && /^[a-f0-9]{40}$/i.test(id)) g.combine_tx = id.toLowerCase();
    }, 'Posting');
    if (typeof result?.txId === 'string' && /^[a-f0-9]{40}$/i.test(result.txId)) g.combine_tx = result.txId.toLowerCase();
  } catch (error) { failure = error; if (typeof error?.txId === 'string' && /^[a-f0-9]{40}$/i.test(error.txId)) g.combine_tx = error.txId.toLowerCase(); }
  co.busy = false;
  if (failure && failure.broadcasted === false && !g.combine_tx) { g.state = 'ready'; save(); return; }  // declined: nothing sent, the button stays
  g.state = g.combine_tx ? 'combine-sent' : 'combine-unknown';  // S3: no id -> unknown, never a second combine prompt
  save();
  if (g.combine_tx) verifyCombine(g);
}

// the combine's game outcome is the game's own record of the signed combine, by id (/api/market/checkout/combine-verify
// success + the merged card), like the purchase's. It used /api/op-verify, whose live collection re-read also demands the
// survivor's collection power equal the inputs' sum -- the game does not keep that, so the maintainer's finished combine
// (e72cfe27, success, 115 CC) stayed "pending" until the 3-minute clock turned it red. Only the game's success:false is red
// the 3-minute mark is amber "Not confirmed yet" with Check again ( clock), never a second combine.
async function verifyCombine(g, resumed = false) {
  if (g.checking || !g.combine_tx) return;
  g.checking = true;
  if (!g.signed_at) g.signed_at = new Date().toISOString();
  if (resumed) g.check_from = new Date().toISOString();
  const start = () => Math.max(Date.parse(g.signed_at) || 0, Date.parse(g.check_from || '') || 0);
  try {
    for (let i = 0; i < MAX_ROUNDS; i += 1) {
      if (i > 0 && Date.now() - start() > CONFIRM_TTL_MS) { g.state = 'combine-timeout'; return; }
      const wait = i < VERIFY_SCHEDULE_MS.length ? VERIFY_SCHEDULE_MS[i] - (VERIFY_SCHEDULE_MS[i - 1] || 0) : RECHECK_MS;
      if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
      let result = null;
      try { result = await post('combine-verify', { tx_id: g.combine_tx, operation: g.operation }); } catch (_) { result = null; }
      if (result?.status === 'confirmed') {
        g.state = 'combined'; g.bcx = result.bcx; g.message = '';
        balancesChanged({ player: co.player, assets: ['CARDS'], id: `combine:${g.combine_tx}` });
        save(); co.deps?.onCombined?.(g); settle(); return;
      }
      if (result?.status === 'failed') { g.state = 'combine-failed'; g.message = String(result.message || ''); return; }
      g.message = result?.status === 'mismatch' ? String(result.message || '') : '';  // shown with "Not confirmed yet"; still amber
    }
    g.state = 'combine-timeout';
  } finally { g.checking = false; save(); }
}

function footHtml() {
  const q = co.quote;
  const close = '<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-co-action="close">Close</button>';
  // on a phone the dialog covers the header's Wallet button, so the checkout carries one (CSS shows it only there).
  const wallet = `<button type="button" class="ceres-btn ceres-btn--secondary co-wallet-btn" data-control-density="comfortable" data-control-mode="icon" data-co-action="wallet" aria-controls="regional-dec-drawer" aria-label="Wallet"><img src="${DEC_ICON}" alt="" width="20" height="20"></button>`;
  if (co.stage === 'review' && q) return `${wallet}${close}`;  // total and the pay buttons are in the bar at the top
  if (co.stage === 'confirm' && q) return `${wallet}<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-co-action="back">Back</button>`;
  if (co.stage === 'outcome' && co.run) {
    const again = co.run.txs.some((tx) => ['timeout'].includes(tx.state) || (tx.stopped && OPEN_STATES.includes(tx.state)));
    const open = runIsOpen(co.run) || co.run.txs.some((tx) => tx.state === 'asked');
    const doneStyle = (co.run.groups || []).some((g) => GROUP_OPEN.includes(g.state)) ? 'secondary' : 'primary';
    return `${again ? '<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-co-action="check">Check again</button>' : ''}<button type="button" class="ceres-btn ceres-btn--${doneStyle}" data-control-density="comfortable" data-co-action="done">${open ? 'Close (keeps checking)' : 'Done'}</button>`;
  }
  if (co.stage === 'error') return `${close}<button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" data-co-action="retry">Try again</button>`;
  return close;
}

export function render() {
  const body = $('co-body');
  const foot = $('co-foot');
  const title = $('co-title');
  if (!body) return;
  if (title) title.textContent = co.stage === 'outcome' || co.stage === 'signing' ? 'Your purchase' : co.test ? 'Checkout · test buy' : co.buyMax ? 'Buy & max' : 'Checkout';
  if (co.stage === 'loading') body.innerHTML = '<div class="cb-loading" role="status"><span class="spinner" aria-hidden="true"></span><span>Checking every listing in your cart…</span></div>';
  else if (co.stage === 'error') body.innerHTML = `<p class="cm-note" data-state="sold" role="status">${esc(co.note?.text || 'The checkout could not be read.')}</p>`;
  else if (co.stage === 'review' && co.quote) body.innerHTML = reviewHtml(co.quote);
  else if (co.stage === 'confirm' && co.quote) body.innerHTML = confirmHtml(co.quote);
  else if ((co.stage === 'outcome' || co.stage === 'signing') && co.run) body.innerHTML = `${co.note ? notesHtml([co.note]) : ''}${outcomeHtml(co.run)}`;
  if (foot) foot.innerHTML = co.stage === 'signing' ? '' : footHtml();
  if ((co.stage === 'outcome' || co.stage === 'signing') && co.run) ensureTick();
  foot && (foot.hidden = !foot.innerHTML);
  syncNarrow();
}

// ── actions ──────────────────────────────────────────────────────────────────────────────────────────────────────────
async function loadQuote(items) {
  co.stage = 'loading'; co.currency = null; render();
  try {
    co.quote = await post('quote', { items: items.map((it) => ({ lkey: it.lkey, pid: it.pid, seen_usd: it.seen_usd ?? it.price_usd ?? null, ...(it.group && String(it.group).startsWith('max-') ? { group: it.group } : {}) })) });
    co.stage = 'review';
  } catch (error) {
    co.stage = 'error'; co.note = { tone: 'danger', text: error.message };
  }
  render();
}

// when the
// wallet closes over an open checkout that has not been signed, the quote is read again in place -- balances, shortfalls and
// prices -- without the loading view. The chosen currency stays while it can still pay; a confirm that no longer can returns to
// the review. Signing, an outcome or a closed dialog are never touched.
export async function requoteAfterWallet() {
  const overlay = $('co-overlay');
  if (!overlay || overlay.hidden || co.busy || co.requoting || !['review', 'confirm', 'error'].includes(co.stage) || !co.items?.length) return;
  co.requoting = true;
  try {
    const quote = await post('quote', { items: co.items.map((it) => ({ lkey: it.lkey, pid: it.pid, seen_usd: it.seen_usd ?? it.price_usd ?? null, ...(it.group && String(it.group).startsWith('max-') ? { group: it.group } : {}) })) });
    if (overlay.hidden || !['review', 'confirm', 'error'].includes(co.stage)) return;
    co.quote = quote;
    if (co.stage === 'error') { co.stage = 'review'; co.note = null; }
    if (co.currency && currencyChoice(quote, co.currency)?.disabled) { co.currency = null; if (co.stage === 'confirm') co.stage = 'review'; }
    render();
  } catch (_) { /* the shown quote stays; Sign re-checks every listing on the server anyway */ }
  finally { co.requoting = false; }
}

async function testBuy() {
  co.busy = true; render();
  try {
    const quote = await post('test-buy', {});
    const it = quote.test_item;
    if (it) co.names.set(it.lkey, it);
    co.quote = quote; co.test = true; co.currency = null; co.note = { tone: 'warn', text: 'Test buy: this replaces your cart for this checkout only. Choose DEC or Credits, then sign it yourself.' };
    co.items = it ? [it] : [];
  } catch (error) { co.note = { tone: 'danger', text: error.message }; }
  co.busy = false; render();
}

// One purchase run: reserve -> fresh server re-check -> intent on disk -> one wallet prompt per transaction -> verify.
async function sign() {
  if (co.busy || !co.quote || !co.currency) return;
  if (walletAccount() !== co.player) { co.note = { tone: 'danger', text: 'The selected player changed. Nothing was sent.' }; co.stage = 'review'; render(); return; }
  co.busy = true; render();
  const late = await adoptLate();
  if (late !== 'clear') { co.busy = false; render(); return; }
  const token = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const reserved = await withRunLock(co.player, () => {
    const open = readRun(co.player);
    if (runIsOpen(open)) return 'taken';
    co.run = { token, currency: co.currency, txs: [] };
    return 'ok';
  });
  if (reserved === 'taken') { co.run = readRun(co.player); co.stage = 'outcome'; co.busy = false; resumeAll(); render(); return; }
  let fresh;
  try {
    fresh = await post('preview', { quote_id: co.quote.quote_id, currency: co.currency, confirmed_total: currencyChoice(co.quote, co.currency).total });
  } catch (error) {
    co.run = null; co.busy = false;
    if (error.status === 409 && error.body?.quote_id) {  // sold or dearer since the confirm: show the new quote, ask again
      co.quote = error.body; co.currency = null; co.stage = 'review';
      co.note = { tone: 'warn', text: `${error.body.detail || 'The cart changed since you confirmed it.'} Nothing was signed.` };
    } else if (error.status === 410) { co.busy = false; await loadQuote(co.items); return; }
    else { co.stage = 'error'; co.note = { tone: 'danger', text: `${error.message} Nothing was signed.` }; }
    render(); return;
  }
  const txList = fresh.transactions.map((tx) => ({ nonce: tx.nonce, payload: tx.payload, operations: tx.operations, lkeys: tx.lkeys, price: tx.price, currency: tx.currency, copy_prices_dec: Object.fromEntries(tx.lkeys.map((k) => { const row = (co.quote.items || []).find((it) => it.lkey === k); return [k, row?.price_usd != null && co.quote.dec_price ? row.price_usd / co.quote.dec_price : null]; })), state: 'pending', tx_id: null }));
  // a maxed-card group whose listings are all in one transaction of this purchase is combined after it.
  const groups = maxGroupsOf(co.items).map((g) => ({ ...g, tx: txList.findIndex((tx) => g.lkeys.every((k) => tx.lkeys.includes(k))) })).filter((g) => g.tx >= 0)
    .map((g) => ({ ...g, state: 'waiting' }));
  co.run = { token, currency: co.currency, test: co.test, txs: txList, groups };
  const stored = await withRunLock(co.player, () => writeRun(co.player, co.run));
  if (!stored) {  // S10: the intent is on disk BEFORE the wallet is asked, or there is no prompt
    co.run = null; co.busy = false; co.stage = 'error';
    co.note = { tone: 'danger', text: 'This browser could not store the purchase before signing (site storage is blocked or full), so the wallet was not opened. Nothing was sent.' };
    render(); return;
  }
  co.stage = 'signing'; co.note = null; render();
  for (let i = 0; i < co.run.txs.length; i += 1) {
    const tx = co.run.txs[i];
    const signingPlayer = co.player;
    const signingRun = co.run;
    tx.state = 'asked'; tx.asked_at = new Date().toISOString();
    if (!writeRun(co.player, co.run)) { tx.state = 'pending'; markSkipped(i); break; }
    render();
    let failure = null;
    try {
      const result = await requestBroadcastThroughGate(signingPlayer, tx.operations, (response) => {
        const id = response?.result?.id || response?.result?.tx_id || response?.tx_id;
        if (typeof id === 'string' && /^[a-f0-9]{40}$/i.test(id)) {
          tx.tx_id = id.toLowerCase();
          writeRun(signingPlayer, signingRun); // late wallet callbacks persist their transaction evidence
          if (co.player === signingPlayer && co.run === signingRun) render();
        }
      }, 'Active', { direct: true });  // one "sign and send" prompt, Active, like the game
      if (typeof result?.txId === 'string' && /^[a-f0-9]{40}$/i.test(result.txId)) tx.tx_id = result.txId.toLowerCase();
    } catch (error) { failure = error; if (typeof error?.txId === 'string' && /^[a-f0-9]{40}$/i.test(error.txId)) tx.tx_id = error.txId.toLowerCase(); }
    tx.signed_at = new Date().toISOString();
    if (failure && failure.broadcasted === false && !tx.tx_id) {
      tx.state = 'declined'; markSkipped(i + 1); writeRun(co.player, co.run); break;
    }
    if (!tx.tx_id) {  // S3: no id and not provably unsent -> unknown; the rest of this checkout is not sent
      tx.state = 'unknown'; markSkipped(i + 1); writeRun(co.player, co.run); verify(tx); break;
    }
    tx.state = 'sent'; writeRun(co.player, co.run); verify(tx);
  }
  co.busy = false; co.stage = 'outcome'; render();
  settle();  // a purchase confirmed while later wallet prompts were still open
}
function markSkipped(from) {
  for (let j = from; j < co.run.txs.length; j += 1) if (co.run.txs[j].state === 'pending') co.run.txs[j].state = 'skipped';
}

// the game's record by id; with no id, the player's own history by nonce. Released as "not sent"
// only on positive evidence (a SUCCESSFUL covering history read, 3,900 seconds after the wallet was asked).
async function lookupNonce(tx) {
  try {
    const found = await post('find', { nonce: tx.nonce, ...(tx.asked_at ? { since: tx.asked_at } : {}) });
    if (found?.status === 'found') { tx.tx_id = found.tx_id; tx.historyUnreadable = false; return 'found'; }
    if (found?.status === 'not_found' && found.covered === true) { tx.historyUnreadable = false; return 'not_found'; }
  } catch (_) { /* unknown */ }
  tx.historyUnreadable = true;
  return 'unknown';
}
function save() { if (co.run) writeRun(co.player, co.run); if (!$('co-overlay')?.hidden) render(); }
// all green -> the green summary shows for AUTO_CLOSE_MS
// then the dialog closes by itself. Amber, red, an open step or a skipped combine never close it.
let closeTimer = 0;
function settle() {
  if (closeTimer || !runAllGreen(co.run)) return;
  if (!$('co-overlay')?.hidden) render();
  closeTimer = setTimeout(() => {
    closeTimer = 0;
    if (runAllGreen(co.run) && co.stage === 'outcome' && !$('co-overlay')?.hidden) closeCheckout();
  }, AUTO_CLOSE_MS);
}
// the wallet request's clock and the "Checking with the game" clock tick in place each second (no re-render).
let tickTimer = 0;
function ensureTick() {
  if (tickTimer || typeof setInterval !== 'function') return;
  tickTimer = setInterval(() => {
    const overlay = $('co-overlay');
    const live = co.run && (runIsOpen(co.run) || co.run.txs.some((tx) => tx.state === 'asked'));
    if (!live) { clearInterval(tickTimer); tickTimer = 0; return; }
    if (!overlay || overlay.hidden) return;
    walletClockTick(overlay);
    for (const el of overlay.querySelectorAll?.('[data-co-since]') || []) el.textContent = elapsedText(el.dataset.coSince);
  }, 1000);
}
function neverSent(tx) {
  tx.state = 'not_sent'; tx.historyUnreadable = false; finish(tx);
}
function finish(tx) {
  const index = co.run?.txs.indexOf(tx);
  for (const g of co.run?.groups || []) {
    if (g.tx !== index || g.state !== 'waiting') continue;
    g.state = groupVerdict(g, tx);
    if (g.state === 'partial') g.missing = g.lkeys.filter((k) => (tx.result?.items || []).find((r) => r.lkey === k)?.outcome !== 'bought').length;
    if (g.state === 'building') buildCombine(g);
  }
  save();
  if (tx.state === 'confirmed') {
    balancesChanged({ player: co.player, assets: [co.run?.currency || co.currency || 'DEC', 'CARDS'], id: `purchase:${tx.tx_id}` });
    const bought = (tx.result?.items || []).filter((r) => r.outcome === 'bought').map((r) => r.lkey);
    if (bought.length) co.deps?.onBought?.(bought);
  }
  if (co.run && !runIsOpen(co.run)) co.deps?.onSettled?.();
  settle();
}
async function verify(tx, resumed = false) {
  if (tx.checking) return;
  tx.checking = true; tx.stopped = false;
  if (resumed) tx.checkFrom = new Date().toISOString();
  const start = () => Math.max(Date.parse(tx.signed_at || tx.asked_at) || 0, Date.parse(tx.checkFrom || '') || 0);
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  try {
    for (let i = 0; i < MAX_ROUNDS; i += 1) {
      if (i > 0 && tx.tx_id && Date.now() - start() > CONFIRM_TTL_MS) { tx.state = 'timeout'; save(); return; }
      const wait = i < VERIFY_SCHEDULE_MS.length ? VERIFY_SCHEDULE_MS[i] - (VERIFY_SCHEDULE_MS[i - 1] || 0) : RECHECK_MS;
      if (wait) await pause(wait);
      if (!tx.tx_id) {
        const seen = await lookupNonce(tx);
        if (seen === 'not_found' && Date.now() - Date.parse(tx.asked_at || '') >= NEVER_SENT_AFTER_MS) { neverSent(tx); return; }
        if (seen !== 'found') { save(); continue; }
        if (tx.state === 'unknown') tx.state = 'sent';
        save();
      }
      let result;
      try { result = await post('verify', { tx_id: tx.tx_id, nonce: tx.nonce, expected_payload: tx.payload }); } catch (_) { continue; }
      if (result.status === 'confirmed') { tx.state = 'confirmed'; tx.result = result; finish(tx); return; }
      if (result.status === 'failed') { tx.state = 'failed'; tx.message = result.message; finish(tx); return; }
      if (result.status === 'mismatch') { tx.state = 'unknown'; tx.message = result.message; tx.stopped = true; save(); return; }  // the maintainer checks; Check again reads it anew
      if (result.status === 'not_found') {
        if (await expiredAndAbsent(tx)) { neverSent(tx); return; }
        tx.state = 'unknown'; save(); continue;
      }
    }
    tx.stopped = true; save();
  } finally { tx.checking = false; }
}
function resumeGroups() {
  for (const g of co.run?.groups || []) {
    g.checking = false; g.building = false;
    if (g.state === 'building') buildCombine(g);
    else if (g.state === 'combine-asked') g.state = 'combine-unknown';  // the page ended at the wallet prompt
    if (g.state === 'combine-sent' || (g.state === 'combine-unknown' && g.combine_tx)) verifyCombine(g);
  }
}
function resumeAll() {
  resumeGroups();
  for (const tx of co.run?.txs || []) {
    if (['sent', 'unknown', 'asked'].includes(tx.state)) {
      if (tx.state === 'asked') tx.state = 'unknown';  // the page ended while the wallet was asked: its outcome is unknown
      verify(tx);
    }
  }
}
// purchases the wallet never answered are looked up once before the next checkout; a late broadcast is adopted
// (shown and confirmed), never doubled. 'clear' only when every watched nonce was read successfully and is absent.
async function adoptLate() {
  const now = Date.now();
  const list = readWatch(co.player).filter((item) => { const askedAt = watchAskedAt(item); return askedAt !== null && now - askedAt < NEVER_SENT_AFTER_MS; });
  writeWatch(co.player, list);
  let unknown = false;
  for (const item of list) {
    const probe = { nonce: item.nonce, asked_at: item.asked_at };
    const seen = await lookupNonce(probe);
    if (seen === 'not_found') { unknown = true; continue; } // before Hive expiry, absence does not prove the wallet request is cancelled
    if (seen === 'unknown') { unknown = true; continue; }
    writeWatch(co.player, readWatch(co.player).filter((w) => w.nonce !== item.nonce));
    co.run = { token: `late-${item.nonce}`, currency: item.payload?.currency, txs: [{ nonce: item.nonce, payload: item.payload, lkeys: item.lkeys || [], currency: item.payload?.currency, price: item.payload?.price, state: 'sent', tx_id: probe.tx_id, asked_at: item.asked_at, signed_at: new Date().toISOString() }] };
    writeRun(co.player, co.run);
    co.stage = 'outcome';
    co.note = { tone: 'warn', text: 'Your wallet sent an earlier purchase late. Ceres is confirming it now; nothing new was signed.' };
    resumeAll();
    return 'adopted';
  }
  if (unknown) { co.note = { tone: 'warn', text: 'An earlier unanswered purchase is still pending. Ceres has not confirmed its outcome; do not buy again.' }; return 'unknown'; }
  return 'clear';
}

async function onClick(event) {
  const button = event.target.closest?.('[data-co-action]');
  if (!button || button.disabled) return;
  const action = button.dataset.coAction;
  if (action === 'close' || action === 'done') { closeCheckout(); return; }
  if (action === 'wallet') { document.getElementById('regional-dec-button')?.click(); return; }  // the page's own wallet, over this dialog
  if (action === 'currency') { co.currency = button.dataset.coCurrency; co.note = null; render(); return; }
  if (action === 'continue' && co.currency) { co.stage = 'confirm'; render(); return; }
  if (action === 'back') { co.stage = 'review'; render(); return; }
  if (action === 'sign') { await sign(); return; }
  if (action === 'retry') { await loadQuote(co.items || []); return; }
  if (action === 'test-buy') { await testBuy(); return; }
  const group = co.run?.groups?.[Number(button.dataset.coGroup)];
  if (action === 'combine-sign' && group) { await signCombine(group); return; }
  if (action === 'combine-confirm' && group) { group.state = 'ready'; save(); await signCombine(group); return; }
  if (action === 'combine-skip' && group) { group.state = 'skipped'; save(); render(); return; }
  if (action === 'combine-retry' && group) { group.state = 'building'; group.since = Date.now(); buildCombine(group); render(); return; }
  if (action === 'combine-check' && group) { group.state = 'combine-sent'; verifyCombine(group, true); render(); return; }
  if (action === 'open-combinables') { closeCheckout(); document.getElementById('cb-mode-combine')?.click(); return; }
  if (action === 'check') { for (const tx of co.run?.txs || []) if (tx.state === 'timeout' || tx.stopped) { tx.state = tx.tx_id ? 'sent' : 'unknown'; verify(tx, true); } render(); }
}

// Entry point: deps = { player, items (the cart's items), onBought(lkeys), onSettled(), onClose() }.
export async function openCheckout(deps) {
  co.deps = deps;
  co.player = String(deps.player || '').toLowerCase();
  co.items = (deps.items || []).slice();
  co.names = new Map(co.items.map((it) => [it.lkey, it]));
  co.test = false; co.note = null; co.busy = false; co.buyMax = Boolean(deps.buyMax);
  const overlay = ensureOverlay();
  if (overlay.hidden) co.lastFocus = document.activeElement;
  overlay.hidden = false;
  if (document.body?.dataset) document.body.dataset.coDialog = '1';
  // An unresolved purchase (this tab or a reload) is shown instead of a new checkout.
  const stored = co.run && runIsOpen(co.run) ? co.run : readRun(co.player);
  if (stored && (runIsOpen(stored) || stored.txs.some((tx) => tx.state === 'pending'))) {
    if (stored !== co.run) {
      co.run = stored;
      for (const tx of co.run.txs) { tx.checking = false; if (tx.state === 'pending') tx.state = 'skipped'; }  // never asked: nothing was sent
      for (const tx of co.run.txs) for (const k of tx.lkeys || []) if (!co.names.has(k)) co.names.set(k, { name: 'Card in your cart' });
      resumeAll();
    }
    co.stage = 'outcome'; render();
    overlay.querySelector('.hazard-close')?.focus();
    return;
  }
  co.run = null;
  if (!co.items.length) { co.stage = 'error'; co.note = { tone: 'danger', text: 'Your cart is empty.' }; render(); return; }
  await loadQuote(co.items);
  overlay.querySelector('.hazard-close')?.focus();
}

export const _checkoutForTests = co;
