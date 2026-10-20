// card-combinables.js -- "Find combinables", the Cards page's third mode. Read only: nothing here signs, buys or
// transfers. Owner (): "add a new feature to cards: find combinables. With it, we can a) find and combine our own
// cards and b) find a quick price for either maxing it or making it stronger cheaply, when a large CC copy is on sale but
// single copies are going up in price over a threshold to not suggest maxed". Decisions / .
// a) GET /api/cards/combinables (the stored snapshot, no market) on entering the mode: per print the own copies, blockers
// with reasons, the card-lock warning and the proposed sets (never past max CC). The Cards filters apply in the browser
// (a print is shown when any of its copies passes them). "Combine" opens the board's combine dialog right here, over this
// page (): the dialog's module is loaded on the first press, never at first paint, and the board is never drawn
// (the /?combine=<card_detail_id>-<edition>-<foil> board link still works; no Card ID ever reaches this page's screen or
// address). : the mode is the "Find combinables" pill at the end of the filter row, a view of My cards.
// b) Prices only on re-reads the list with market=1 (the going rate and which prints are
// worth a check), then prices the next 5 shown cards (GET /api/cards/combinables/upgrade, 5 at a time, never the whole
// collection, never in the background); "Check price" prices one card. "Add to cart" puts the suggested listings into the
// shop cart (card-cart.js state, group "up-...").
import { holdTooltipsUntilPointerMoves, tipAttrs } from './tooltip.js';
import { CB_FOIL_LABELS, CB_SET_LABELS, cbMatches } from './card-browser-model.js';
import { cartPriceHtml, cartRefreshOwned, cartState } from './card-cart.js';
import { setMode } from './card-market.js';
import { cartAdd, cartHasAll, cartPrintId, cartRemoveGroup, cartStore, cartTotals } from './card-cart-model.js';

export const CX_BATCH = 5;
export const CX_PAGE = 24;
const PERMANENT = new Set(['delegated_in', 'max', 'black_foil', 'arcane', 'foil']);

const cx = {
  deps: null,
  body: null,          // the last /api/cards/combinables answer
  market: false,       // that answer carries the going rate and the price-check hints
  loading: false,
  error: '',
  upgrades: new Map(), // pid -> { loading, data, error }
  shown: CX_PAGE,
  sort: 'levels',
  note: null,          // { state, text } after Add to cart
  checking: false,
  opening: '',         // the print whose Combine is loading the dialog
  held: new Set(),     // prints with a combine sent but not confirmed when the dialog closed: Combine stays closed
  refreshing: new Set(), // prints re-read after a confirmed combine
  combineError: null,  // { pid, text } when the dialog could not open or the card could not be read again
};

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function num(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return Math.round(Number(value)).toLocaleString('en-US');
}
function rate(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return `$${n.toFixed(n < 0.01 ? 4 : n < 1 ? 3 : 2)}`;
}
// the going rate is shown as PP per dollar.
function ppPerUsd(usdPerPp) {
  const n = Number(usdPerPp);
  return Number.isFinite(n) && n > 0 ? (1 / n).toLocaleString('en-US', { maximumFractionDigits: 1 / n < 10 ? 2 : 0 }) : '—';
}
const cap = (s) => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : '');
const $ = (id) => (typeof document !== 'undefined' && document.getElementById ? document.getElementById(id) : null);

export function cxMode() {
  return typeof document !== 'undefined' && document.body?.dataset?.cbMode === 'combine';
}

// ── pure helpers (tests/ceres/test_ir9226_combinables_ui.py) ─────────────────
// The prints the Cards filters let through: a print is shown when any of its own copies passes cbMatches.
export function cxAllowedPrints(cards, filters, now = Date.now()) {
  const out = new Set();
  for (const card of cards || []) {
    if (card && card.card_detail_id != null && card.edition != null && card.foil != null && cbMatches(card, filters, now)) out.add(cartPrintId(card));
  }
  return out;
}

// C-4 state of a verdict: ok = buy it (max, discount, already max); warn = stronger only to a level; danger = nothing passes.
export function cxVerdictState(kind) {
  if (kind === 'max' || kind === 'discount' || kind === 'already_max') return 'ok';
  if (kind === 'level') return 'warn';
  if (kind === 'none') return 'danger';
  return 'meta';
}

// A blocked copy's reason colour: never combinable here (delegated or rented in, already max, black foil) is danger, a
// temporary state (staked, cooling, listed, rented out, delegated out, mid-unlock, card set) is warn.
export function cxReasonState(code) {
  return PERMANENT.has(String(code || '')) ? 'danger' : 'warn';
}

export function cxSetText(set) {
  const b = set.before || {};
  const a = set.after || {};
  return `${set.count} copies → Lv ${b.level ?? '—'} to Lv ${a.level ?? '—'}, CC ${num(b.bcx)} to ${num(a.bcx)}, PP ${num(b.land_base_pp)} to ${num(a.land_base_pp)}`;
}

// The list order: the server's (levels gained, copies freed), with the likely discount copies first once prices were
// checked (), then read discount verdicts; "saving" = the biggest saving against the going rate first, unread last.
export function cxSorted(groups, sort, upgrades) {
  const verdict = (g) => upgrades?.get?.(g.print.id)?.data?.verdict || null;
  const rank = (g) => {
    if (verdict(g)?.kind === 'discount') return 0;
    if (g.upgrade_hint?.discount_likely && !verdict(g)) return 0;
    return 1;
  };
  const indexed = (groups || []).map((g, i) => ({ g, i }));
  if (sort === 'saving') {
    const saving = (g) => { const v = verdict(g); return v && Number.isFinite(Number(v.saving_usd)) && ['max', 'level', 'discount'].includes(v.kind) ? Number(v.saving_usd) : -Infinity; };
    indexed.sort((x, y) => saving(y.g) - saving(x.g) || x.i - y.i);
  } else {
    indexed.sort((x, y) => rank(x.g) - rank(y.g) || x.i - y.i);
  }
  return indexed.map((x) => x.g);
}

// The next batch to price: shown cards, in list order, not read yet, and not ruled out by the list's price hint.
export function cxNextBatch(groups, upgrades, size = CX_BATCH) {
  return (groups || []).filter((g) => !upgrades.has(g.print.id) && g.upgrade_hint?.possible !== false && g.base && !g.base.maxed)
    .slice(0, size).map((g) => g.print);
}

// The upgrade's listings into the cart as one group "up-..." (not "max-": an upgrade is not "one maxed card").
export function cxCartAdd(cart, upgrade, { now = Date.now(), rand = Math.random } = {}) {
  const listings = upgrade?.cart?.listings || [];
  const group = `up-${now.toString(36)}-${rand().toString(36).slice(2, 7)}`;
  return { group, added: cartAdd(cart, listings, { now, bundle: upgrade?.cart?.bundle || null, group }) };
}

export function cxInCart(cart, upgrade) {
  return cartHasAll(cart, upgrade?.cart?.keys || []);
}

// ── HTML ─────────────────────────────────────────────────────────────────────
function printLabel(p) {
  return [CB_SET_LABELS[p.card_set] || cap(p.card_set), CB_FOIL_LABELS[p.foil], cap(p.rarity)].filter(Boolean).join(' · ');
}

function chip(text, state = '', tip = null) {
  return `<span class="cm-chip cx-chip${state ? ` is-${state}` : ''}"${tip ? tipAttrs(tip) : ''}>${esc(text)}</span>`;
}

function plotLink(plot) {
  const player = cx.deps?.player?.() || '';
  return `<a class="cx-plot" href="/?${player ? `player=${encodeURIComponent(player)}&` : ''}plot=${encodeURIComponent(plot.label)}">${esc(plot.label)}</a>`;
}

function copyLine(copy) {
  const reasons = (copy.reasons || []).map((r) => `<span class="cx-reason is-${cxReasonState(r.code)}">${esc(r.text)}</span>`).join('');
  const where = copy.plot && copy.plot.label ? ` · on ${plotLink(copy.plot)}` : '';
  return `<li class="cx-copy">Lv ${esc(copy.level ?? '—')} · ${num(copy.bcx)} CC${where} ${reasons}</li>`;
}

function blockedHtml(group) {
  const blocked = (group.copies || []).filter((c) => !c.combinable);
  if (!blocked.length) return '';
  const permanent = blocked.some((c) => (c.reasons || []).some((r) => cxReasonState(r.code) === 'danger'));
  const temporary = blocked.some((c) => (c.reasons || []).some((r) => cxReasonState(r.code) === 'warn'));
  const state = temporary ? 'warn' : permanent ? 'danger' : 'meta';
  const title = `${blocked.length} ${blocked.length === 1 ? 'copy cannot' : 'copies cannot'} combine now`;
  return `<details class="cx-blocked is-${state}"><summary class="cx-toggle">${esc(title)}</summary><ul>${blocked.map(copyLine).join('')}</ul></details>`;
}

function setsHtml(group) {
  const sets = group.sets || [];
  if (!sets.length) {
    const why = group.note ? cap(group.note) : group.combinable_count < 2 ? `${group.combinable_count === 1 ? 'One copy is' : 'No copy is'} free to combine; nothing to combine now` : 'Any two free copies together would pass max CC';
    return `<p class="cx-line is-meta">${esc(why)}.</p>`;
  }
  const first = sets[0];
  const maxed = first.after?.maxed;
  const head = `<div class="cx-ledger"><span class="cx-lv">Lv ${esc(first.before?.level ?? '—')}</span><span class="cx-arrow" aria-hidden="true">→</span><span class="cx-lv${maxed ? ' is-ok' : ''}">Lv ${esc(first.after?.level ?? '—')}</span>${maxed ? chip('Max level', 'ok') : ''}</div>`;
  const facts = `<p class="cx-line">${first.count} copies · CC ${num(first.before?.bcx)} → ${num(first.after?.bcx)} / ${num(group.print.bcx_max)} · PP ${num(first.before?.land_base_pp)} → ${num(first.after?.land_base_pp)} <span class="cx-meta">(${num(first.destroyed_count)} destroyed)</span></p>`;
  const more = sets.length > 1
    ? `<details class="cx-more-sets"><summary class="cx-toggle">+ ${sets.length - 1} more ${sets.length === 2 ? 'set' : 'sets'} from the copies left over</summary><ul>${sets.slice(1).map((s) => `<li>${esc(cxSetText(s))}</li>`).join('')}</ul></details>`
    : '';
  const lock = sets.some((s) => (s.warnings || []).some((w) => w.code === 'card_lock'))
    ? `<p class="cx-line is-warn">${esc((first.warnings || []).find((w) => w.code === 'card_lock')?.text || 'This copy is under a card lock; the combined card will be locked as well.')}</p>`
    : '';
  return head + facts + lock + more;
}

function hintHtml(group) {
  const hint = group.upgrade_hint;
  if (!hint) return '';
  if (hint.possible === false) {
    const why = { max: 'Already at max level', not_on_sale: 'Not on the market now', dearer: 'Fewer PP/$ than the going rate', none: 'Nothing to price' }[hint.reason] || 'Not worth a price check';
    return `<p class="cx-line is-meta">${esc(why)}</p>`;
  }
  return hint.discount_likely ? `<p class="cx-line">${chip('Discount copy likely', 'ok', { title: 'Discount copy likely', body: 'A copy above 1 CC is on sale for less per CC than the cheapest single copy. Check the price to see it.' })}</p>` : '';
}

function verdictHtml(group, entry) {
  const data = entry.data;
  const v = data.verdict || {};
  const state = cxVerdictState(v.kind);
  const label = `<p class="cx-verdict is-${state}">${esc(v.label || '—')}</p>`;
  if (!['max', 'level', 'discount'].includes(v.kind)) return label;
  const max = group.print.bcx_max;
  const parts = [];
  parts.push(`<p class="cx-line cx-price">${cartPriceHtml(v.total_usd, v.total_dec)}<span class="cx-meta">to Lv ${esc(v.level)} · CC ${num(v.to_bcx)} / ${num(max)} · +${num(v.pp_added)} PP</span></p>`);
  if (Number(v.saving_usd) > 0) parts.push(`<p class="cx-line is-ok">Saves ${esc(rate(v.saving_usd))}${Number(v.saving_dec) > 0 ? ` (${num(v.saving_dec)} DEC)` : ''} against the going rate</p>`);
  if (data.combine_first) parts.push(`<p class="cx-line is-warn">Priced from the combined card: combine the proposed set first</p>`);
  if (v.max_option) parts.push(`<p class="cx-line is-meta">Or ${v.max_option.kind === 'max' ? 'max it' : `Level ${esc(v.max_option.level)}`} for ${esc(rate(v.max_option.total_usd))}</p>`);
  if (v.stop && Number.isFinite(Number(v.stop.vs_going))) parts.push(`<p class="cx-line is-danger">Level ${esc(v.stop.to_level)} would cost ${Number(v.stop.vs_going).toFixed(2)}× the going rate</p>`);
  const inCart = cxInCart(cartState(), data);
  const group_ = inCart ? (cartState().items || []).find((it) => (data.cart?.keys || []).includes(it.lkey) && String(it.group || '').startsWith('up-'))?.group : null;
  const button = inCart
    ? (group_ ? `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-control-case="destructive" data-cx-action="uncart" data-cx-group="${esc(group_)}">Remove from cart</button>` : chip('In cart', 'ok'))
    : `<button type="button" class="ceres-btn ceres-btn--primary" data-control-density="compact" data-cx-action="cart" data-cx-pid="${esc(group.print.id)}"${tipAttrs({ title: 'Add to cart', body: `Adds the ${(data.cart?.keys || []).length} suggested ${(data.cart?.keys || []).length === 1 ? 'listing' : 'listings'} to your cart. Nothing is bought here.` })}>Add to cart</button>`;
  return label + parts.join('') + `<div class="cx-actions">${button}</div>`;
}

function upgradeHtml(group) {
  const entry = cx.upgrades.get(group.print.id);
  let inner;
  if (entry?.loading) inner = `<p class="cx-line cx-reading"><span class="spinner" aria-hidden="true"></span>Reading the market…</p>`;
  else if (entry?.error) inner = `<p class="cx-line is-danger">${esc(entry.error)}</p>`;
  else if (entry?.data) inner = verdictHtml(group, entry);
  else if (group.base?.maxed) inner = `<p class="cx-line is-meta">Your best copy is already at max level</p>`;
  else inner = hintHtml(group) + (group.upgrade_hint?.possible === false ? '' : `<div class="cx-actions"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-cx-action="price" data-cx-pid="${esc(group.print.id)}">Check price</button></div>`);
  return `<section class="cx-part cx-upgrade"><h4>Upgrade price</h4>${inner}</section>`;
}

function tileFor(group) {
  const p = group.print;
  const own = (cx.deps?.cards?.() || []).filter((c) => cartPrintId(c) === p.id).sort((a, b) => (Number(b.bcx) || 0) - (Number(a.bcx) || 0));
  // the print's art, name and identity icons; its level and Maxed are left to the parts beside it (they differ per copy)
  const card = own[0] ? { ...own[0], maxed: false } : { ...p, level: group.base?.level, bcx: group.base?.bcx, land_base_pp: group.base?.land_base_pp, maxed: false };
  return cx.deps.tileHtml({ card, extra: { className: 'cx-tile', levelTag: false, metrics: '', label: `${p.name}. ${printLabel(p)}.` } });
}

export function cxEntryHtml(group) {
  const p = group.print;
  const canCombine = (group.sets || []).length > 0;
  const opening = cx.opening === p.id;
  const combineBtn = cx.held.has(p.id)
    ? `<p class="cx-line is-warn" role="status">Combine sent; the game has not confirmed it yet. Combine stays closed for this card so it is never signed twice.</p>`
    : cx.refreshing.has(p.id)
      ? `<p class="cx-line cx-reading" role="status"><span class="spinner" aria-hidden="true"></span>Reading your cards after the combine…</p>`
      : canCombine
        ? `<button type="button" class="ceres-btn ceres-btn--primary" data-control-density="compact" data-cx-action="combine" data-cx-pid="${esc(p.id)}"${opening ? ' disabled aria-busy="true"' : ''}${tipAttrs({ title: 'Combine', body: 'Opens the combine dialog here with the proposed set chosen. You review and sign there.' })}>Combine</button>`
        : '';
  return `<article class="cx-entry" data-cx-pid="${esc(p.id)}"><div class="cx-art">${tileFor(group)}</div><div class="cx-body"><header class="cx-head"><h3>${esc(p.name)}</h3><span class="cx-meta">${esc(printLabel(p))} · ${num(group.owned_count)} ${group.owned_count === 1 ? 'copy' : 'copies'}</span></header>`
    + `<section class="cx-part cx-combine"><h4>Combine</h4>${setsHtml(group)}${blockedHtml(group)}${combineBtn ? `<div class="cx-actions">${combineBtn}</div>` : ''}${cx.combineError?.pid === p.id ? `<p class="cx-line is-danger" role="alert">${esc(cx.combineError.text)}</p>` : ''}</section>`
    + `${upgradeHtml(group)}</div></article>`;
}

// ── rendering ────────────────────────────────────────────────────────────────
function visibleGroups() {
  const groups = cx.body?.groups || [];
  const cards = cx.deps.cards();
  // before /api/cards has answered there is nothing to filter by: every print shows
  const allowed = cards.length ? cxAllowedPrints(cards, cx.deps.filters()) : null;
  return cxSorted(allowed ? groups.filter((g) => allowed.has(g.print.id)) : groups, cx.sort, cx.upgrades);
}

function summaryHtml(shown) {
  const s = cx.body?.summary || {};
  const withSets = shown.filter((g) => (g.sets || []).length);
  const copies = withSets.reduce((n, g) => n + g.sets.reduce((m, set) => m + set.count, 0), 0);
  const destroyed = withSets.reduce((n, g) => n + g.sets.reduce((m, set) => m + set.destroyed_count, 0), 0);
  const items = [`<span class="cb-summary-item"><strong>${num(withSets.length)}</strong> ${withSets.length === 1 ? 'card can' : 'cards can'} combine (${num(copies)} copies, ${num(destroyed)} destroyed)</span>`];
  if (s.multi_copy_without_set) items.push(`<span class="cb-summary-item cb-summary-note">${num(s.multi_copy_without_set)} more have copies that cannot combine now</span>`);
  const rateBody = cx.body?.going_rate;
  if (rateBody && rateBody.usd_per_pp != null) items.push(`<span class="cb-summary-item"${tipAttrs({ title: 'Going rate', body: `${rateBody.method || 'Median price per PP of the Land cards on sale'}: ${rate(rateBody.usd_per_pp)} for each PP.` })}>Going rate <strong>${esc(ppPerUsd(rateBody.usd_per_pp))}</strong> PP/$</span>`);
  const left = cxNextBatch(shown, cx.upgrades).length;
  const label = !cx.market ? 'Check prices' : left ? `Check ${Math.min(left, CX_BATCH)} more prices` : 'All shown cards checked';
  items.push(`<span class="cb-summary-item cx-summary-actions"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-cx-action="check"${(cx.market && !left) || cx.checking ? ' disabled' : ''}${tipAttrs({ title: 'Check prices', body: `Reads the market once, then prices the next ${CX_BATCH} cards shown here. Nothing is read until you press it.` })}>${esc(label)}</button>`
    + `<span class="cx-sort" role="group" aria-label="Order"><button type="button" class="ceres-btn ceres-btn--choice" data-control-density="compact" data-cx-action="sort" data-cx-sort="levels" aria-pressed="${cx.sort === 'levels'}">Levels gained</button><button type="button" class="ceres-btn ceres-btn--choice" data-control-density="compact" data-cx-action="sort" data-cx-sort="saving" aria-pressed="${cx.sort === 'saving'}">Biggest saving</button></span></span>`);
  return items.join('');
}

export function renderCombinables() {
  if (!cx.deps || !cxMode()) return;
  const list = $('cx-list');
  const summary = $('cx-summary');
  const message = $('cx-message');
  const loading = $('cx-loading');
  if (loading) loading.hidden = !cx.loading;
  if (!cx.body) {
    if (summary) summary.innerHTML = '';
    if (list) list.innerHTML = '';
    if (message) { message.hidden = !cx.error; message.textContent = cx.error; }
    return;
  }
  const shown = visibleGroups();
  if (summary) summary.innerHTML = summaryHtml(shown) + (cx.note ? `<span class="cb-summary-item cx-note is-${esc(cx.note.state)}" role="status">${esc(cx.note.text)}</span>` : '');
  if (message) {
    message.hidden = shown.length > 0 && !cx.error;
    message.textContent = cx.error || ((cx.body.groups || []).length ? 'No combinable card matches these filters.' : 'No card has two copies that can combine now.');
  }
  if (list) {
    list.innerHTML = shown.slice(0, cx.shown).map(cxEntryHtml).join('');
    syncNarrow();
  }
  const more = $('cx-more');
  if (more) more.hidden = shown.length <= cx.shown;
  document.body.dataset.viewReady = 'true';
}

function syncNarrow() {
  const list = $('cx-list');
  if (list) list.classList.toggle('is-narrow', list.getBoundingClientRect().width < 640);
}

// ── reads ────────────────────────────────────────────────────────────────────
async function readJson(url) {
  const response = await fetch(url, { cache: 'no-store' });
  let body = null;
  try { body = await response.json(); } catch (_) { body = null; }
  if (!response.ok) {
    const detail = body && typeof body.detail === 'string' ? body.detail : '';
    throw new Error(response.status === 429 ? 'Too many market reads in a short time; wait a minute and try again.' : detail || `The cards could not be read (${response.status}).`);
  }
  return body;
}

function query(extra = {}) {
  const params = new URLSearchParams(extra);
  const player = cx.deps.player();
  if (player) params.set('player', player);
  return params.toString();
}

async function loadList({ market = false } = {}) {
  if (!cx.deps.player()) { cx.error = 'Type a player name above to find the cards you can combine.'; renderCombinables(); return false; }
  cx.loading = true;
  cx.error = '';
  renderCombinables();
  try {
    cx.body = await readJson(`/api/cards/combinables?${query(market ? { market: '1' } : {})}`);
    cx.market = !!market;
    return true;
  } catch (error) {
    cx.error = error instanceof Error ? error.message : 'The cards could not be read.';
    return false;
  } finally {
    cx.loading = false;
    renderCombinables();
  }
}

async function priceOne(print) {
  const pid = print.id;
  cx.upgrades.set(pid, { loading: true });
  renderEntry(pid);
  try {
    const data = await readJson(`/api/cards/combinables/upgrade?${query({ card_detail_id: print.card_detail_id, edition: print.edition, foil: print.foil })}`);
    cx.upgrades.set(pid, { data });
  } catch (error) {
    cx.upgrades.set(pid, { error: error instanceof Error ? error.message : 'The market could not be read.' });
  }
  renderEntry(pid);
}

// Re-draws one entry in place (the list keeps its scroll position); the summary follows.
function renderEntry(pid) {
  const node = document.querySelector?.(`.cx-entry[data-cx-pid="${CSS.escape(pid)}"]`);
  const group = (cx.body?.groups || []).find((g) => g.print.id === pid);
  if (node && group) node.outerHTML = cxEntryHtml(group);
  const summary = $('cx-summary');
  if (summary && cx.body) summary.innerHTML = summaryHtml(visibleGroups()) + (cx.note ? `<span class="cb-summary-item cx-note is-${esc(cx.note.state)}" role="status">${esc(cx.note.text)}</span>` : '');
}

async function checkPrices() {
  if (cx.checking) return;
  cx.checking = true;
  renderCombinables();
  try {
    if (!cx.market && !(await loadList({ market: true }))) return;
    const batch = cxNextBatch(visibleGroups().slice(0, Math.max(cx.shown, CX_BATCH)), cx.upgrades);
    await Promise.all(batch.map(priceOne));  // at most CX_BATCH reads at once
  } finally {
    cx.checking = false;
    renderCombinables();
  }
}

// ── cart ─────────────────────────────────────────────────────────────────────
function storage() {
  try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch (_) { return null; }
}

// card-cart.js keeps the cart's state; this page writes it through the same model and storage key and repaints the header
// count the way card-cart.js does.
function saveCart() {
  const ok = cartStore(storage(), cx.deps.player(), cartState());
  syncCartBadge();
  return ok;
}

export function syncCartBadge() {
  const badge = $('cm-cart-count');
  const state = cartState();
  const totals = cartTotals(state);
  if (badge) {
    badge.textContent = String(totals.count);
    badge.dataset.state = state.removed?.length ? 'sold' : state.items.some((it) => it.status !== 'ok') ? 'changed' : totals.count ? 'ok' : 'empty';
  }
  // the button's label and tooltip follow the count through watchCartTip ()
}

function addToCart(pid) {
  const data = cx.upgrades.get(pid)?.data;
  if (!data?.cart) return;
  const { added } = cxCartAdd(cartState(), data);
  const stored = added ? saveCart() : true;
  cx.note = !stored
    ? { state: 'danger', text: 'The cart could not be saved in this browser; it holds the listings for this visit only.' }
    : added ? { state: 'ok', text: `Added ${added} ${added === 1 ? 'listing' : 'listings'} of ${data.print?.name || 'this card'} to the cart.` }
      : { state: 'warn', text: 'These listings are already in your cart.' };
  renderEntry(pid);
}

// ── combine in place () ───────────────────────────────────────────────
// The combine dialog is the board's (combine-dialog.js + combine.js), loaded on the first Combine press only -- this page's
// first paint never imports the board's module graph. wallet.js loads first: entering the graph at the dialog would
// otherwise evaluate board.js before wallet.js's own imports have run ("Cannot access 'CURRENT_PLAYER' before
// initialization"). Importing the graph runs no board load and draws nothing: only app.js starts the board.
let dialogModule = null;
export function cxLoadCombineDialog(load = (path) => import(path)) {
  if (!dialogModule) {
    dialogModule = load('./wallet.js').then(() => load('./combine-dialog.js'));
    dialogModule.catch(() => { dialogModule = null; });  // the next press tries again
  }
  return dialogModule;
}

// The page's hooks for the dialog (COMBINE_HOST in combine-dialog.js).
export function cxCombineHost(pid) {
  return {
    onConfirmed: ({ combineRefresh } = {}) => afterCombine(pid, combineRefresh),
    onClosed: ({ outcome } = {}) => {
      holdTooltipsUntilPointerMoves();  // no tooltip for what the closing dialog leaves under a resting pointer
      if (outcome !== 'unknown') return;
      cx.held.add(pid);
      renderEntry(pid);
    },
  };
}

export async function cxOpenCombine(pid, { load = cxLoadCombineDialog } = {}) {
  const group = (cx.body?.groups || []).find((g) => g.print.id === pid);
  if (!group || cx.opening || cx.held.has(pid)) return;
  cx.opening = pid;
  cx.combineError = null;
  renderEntry(pid);
  try {
    const dialog = await load();
    if (!dialog.openCombineInPlace(group, cxCombineHost(pid))) cx.combineError = { pid, text: 'This card has no two copies to combine now.' };
  } catch (_) {
    cx.combineError = { pid, text: 'The combine dialog could not be loaded. Check the connection and press Combine again.' };
  } finally {
    cx.opening = '';
    renderEntry(pid);
    holdTooltipsUntilPointerMoves();  // the redrawn button under the resting pointer opens no tooltip over the dialog
  }
}

// After a confirmed combine (the dialog shows its own success state): once the server's post-combine read of the account
// has settled (card-cart.js's cartRefreshOwned, the checkout's hook; the confirmation already started that read), My cards
// reloads and this card's entry is read again from the stored snapshot.
export async function afterCombine(pid, combineRefresh = null, { refreshOwned = cartRefreshOwned } = {}) {
  cx.held.delete(pid);
  cx.refreshing.add(pid);
  cx.upgrades.delete(pid);  // its price was read for the copies before the combine
  renderEntry(pid);
  const player = cx.deps.player();
  await refreshOwned(player, () => cx.deps.reloadCards?.(), { start: combineRefresh?.triggered === false });
  await refreshGroup(pid);
}

export async function refreshGroup(pid) {
  try {
    const body = await readJson(`/api/cards/combinables?${query({ print: pid })}`);
    const fresh = (body?.groups || []).find((g) => g?.print?.id === pid) || null;
    if (cx.body) {
      const groups = cx.body.groups || [];
      const index = groups.findIndex((g) => g.print.id === pid);
      if (fresh && index >= 0) groups[index] = fresh;
      else if (fresh) groups.push(fresh);
      else if (index >= 0) groups.splice(index, 1);
    }
  } catch (error) {
    cx.combineError = { pid, text: 'The combine is confirmed, but this card could not be read again. It updates on the next visit to Find combinables.' };
  } finally {
    cx.refreshing.delete(pid);
    renderCombinables();
  }
}

// ── mode and events ──────────────────────────────────────────────────────────
// the header keeps My cards / Market; Find combinables is the
// filter row's pill (#cb-mode-combine, a checkbox like Maxed), a view of My cards scoped by the same row's filters.
function syncModeButtons(mode) {
  $('cb-mode-cards')?.setAttribute('aria-pressed', String(mode === 'cards' || mode === 'combine'));
  $('cb-mode-market')?.setAttribute('aria-pressed', String(mode === 'market'));
  if ($('cb-mode-purchases')) $('cb-mode-purchases').checked = mode === 'purchases';
  const pill = $('cb-mode-combine');
  if (pill) pill.checked = mode === 'combine';
}

export function enterCombinables() {
  document.body.dataset.cbMode = 'combine';
  syncModeButtons('combine');
  if (!cx.body && !cx.loading) loadList();
  else renderCombinables();
  cx.deps.syncClear?.();
}

function onClick(event) {
  const button = event.target.closest?.('[data-cx-action]');
  if (!button || button.disabled) return;
  const action = button.dataset.cxAction;
  const pid = button.dataset.cxPid;
  if (action === 'check') checkPrices();
  else if (action === 'price') {
    const group = (cx.body?.groups || []).find((g) => g.print.id === pid);
    if (group) priceOne(group.print);
  } else if (action === 'cart') addToCart(pid);
  else if (action === 'uncart') {
    if (cartRemoveGroup(cartState(), button.dataset.cxGroup)) saveCart();
    cx.note = null;
    renderEntry(button.closest('.cx-entry')?.dataset.cxPid || '');
  } else if (action === 'sort') {
    cx.sort = button.dataset.cxSort === 'saving' ? 'saving' : 'levels';
    renderCombinables();
  } else if (action === 'combine') cxOpenCombine(pid);  // in place, never the board
}

// the header cart button shows only its icon and count; its tooltip and accessible name read "Cart · N items" and
// follow the count, whichever module changes it (card-cart.js rewrites the label with the count; this runs after it).
function watchCartTip() {
  const badge = $('cm-cart-count');
  const button = $('cm-cart-btn');
  if (!badge || !button) return;
  const sync = () => {
    const n = Number(badge.textContent) || 0;
    const label = `Cart · ${n} ${n === 1 ? 'item' : 'items'}`;
    button.dataset.tip = label;
    if (button.getAttribute('aria-label') !== label) button.setAttribute('aria-label', label);
  };
  sync();
  if (typeof MutationObserver !== 'function') return;
  new MutationObserver(sync).observe(badge, { childList: true, characterData: true, subtree: true });
  new MutationObserver(sync).observe(button, { attributes: true, attributeFilter: ['aria-label'] });  // sync() only writes on a change
}

export function initCombinables(deps) {
  cx.deps = deps;
  watchCartTip();
  $('cb-mode-combine')?.addEventListener('change', (event) => {  // the filter-row pill
    if (event.target.checked) enterCombinables();
    else { setMode('cards'); syncModeButtons('cards'); cx.deps.syncClear?.(); }
  });
  // card-market.js switches My cards / Market and knows only those two buttons
  $('cb-mode-cards')?.addEventListener('click', () => syncModeButtons('cards'));
  $('cb-mode-market')?.addEventListener('click', () => syncModeButtons('market'));
  $('cx-list')?.addEventListener('click', onClick);
  $('cx-summary')?.addEventListener('click', onClick);
  $('cx-more')?.addEventListener('click', () => { cx.shown += CX_PAGE; renderCombinables(); });
  if (typeof ResizeObserver === 'function' && $('cx-list')) new ResizeObserver(syncNarrow).observe($('cx-list'));
  if (new URLSearchParams(location.search).get('mode') === 'combine') enterCombinables();
}

export const _cxForTests = cx;
