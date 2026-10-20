// card-cart.js -- / : the Market's card menu and the cart. Read only: nothing here buys, signs or transfers.
// (maintainer: "when clicking a card in the shop, open a menu that shows all available copies, a bundled buy for max and
// other levels, add to cart, price in dollar and dec, remove from cart. all clean in our approved style. use color coding for
// special situations"). (maintainer: "i want to be able to add one maxed card (or as many copies as it takes to combine
// one) to my cart with one button click"). (maintainer decision): checkout will buy from Ceres in a separate, later step
// currency DEC or credits chosen there; until then Checkout is shown but disabled.
// The menu reads one print in depth (GET /api/market/print) only when it opens or "Add maxed card" is pressed; the cart is
// re-checked (GET /api/market/check) once after the first Market read of a visit and when the cart opens after 60 s.
// The cart lives in this browser per player (localStorage, every access wrapped), holding opaque listing keys and the
// price seen; card UIDs never appear.
import { tipAttrs } from './tooltip.js';
import { CB_FOIL_LABELS, CB_SET_LABELS, cbCopyStatus, cbUnavailableLabel } from './card-browser-model.js';
import { countdownText } from './countdown.js';
import {
  cartAdd, cartApplyCheck, cartCheckFromPrint, cartClear, cartCountForPrint, cartGroups, cartHas, cartHasAll, cartLoad,
  cartMaxGroups, cartPrintId, cartPrintParts, cartRemove, cartRemoveGroup, cartStore, cartTotals, cmLevelBundles, cmMaxedPick,
  cmRowState,
} from './card-cart-model.js';

const DEC_ICON = '/static/assets/land/token-dec.png';
const PRINT_TTL_MS = 60000;
const CHECK_TTL_MS = 60000;
// Checkout opens card-checkout.js (loaded on first use): Ceres re-checks every listing, you choose DEC or Credits and sign.
export const CHECKOUT_TIP = 'Buy these listings from Ceres: every listing is checked again, you choose DEC or Credits and approve the purchase in your wallet (Active key).';

const cart = {
  deps: null,
  player: null,
  state: { items: [], removed: [] },
  prints: new Map(),   // pid -> { at, data }
  menu: null,          // { pid, row, art, data, loading, error, note }
  checkedAt: 0,
  checking: false,
  lastFocus: null,
  ownedCombineHeld: new Set(),
};

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function num(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return Math.round(Number(value)).toLocaleString('en-US');
}
function usd(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return `$${new Intl.NumberFormat('en-US', { minimumFractionDigits: n < 1 ? 3 : 2, maximumFractionDigits: n < 1 ? 3 : 2 }).format(n)}`;
}
function dec(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value)) || Number(value) <= 0) return '—';
  const n = Number(value);
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: n < 10 ? 2 : 0 }).format(n);
}
function ratio(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  if (n === 0) return '0';
  const abs = Math.abs(n);
  const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : abs >= 1 ? 2 : Math.min(6, 1 - Math.floor(Math.log10(abs)));
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(n);
}
const cap = (s) => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : '');
const $ = (id) => (typeof document !== 'undefined' && document.getElementById ? document.getElementById(id) : null);

function storage() {
  try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch (_) { return null; }
}

// $ large, then DEC with its icon -- the price pair used everywhere in the menu and the cart.
export function cartPriceHtml(priceUsd, priceDec) {
  const decPart = priceDec > 0 ? `<span class="cm-dec">${esc(dec(priceDec))}<img class="cm-dec-icon" src="${DEC_ICON}" alt="DEC" width="16" height="16"></span>` : '';
  return `<span class="cm-pair"><span class="cm-usd">${esc(usd(priceUsd))}</span>${decPart}</span>`;
}

function printLabel(p) {
  return [CB_SET_LABELS[p.card_set] || cap(p.card_set) || '', CB_FOIL_LABELS[p.foil] || '', cap(p.rarity)].filter(Boolean).join(' · ');
}

// ── state shared with the tiles ───────────────────────────────────────────────
export function cartState() { return cart.state; }

// A tile is "in cart" when every listing it stands for is in the cart (one listing, or the parts of a combined Maxed offer).
export function cartTileKeys(row) {
  if (!row) return [];
  if (Array.isArray(row.parts_keys) && row.parts_keys.length) return row.parts_keys.filter(Boolean);
  return row.lkey ? [row.lkey] : [];
}
// the maxed card this tile's button added (if it is still in the cart).
export function cartTileMaxGroup(row) {
  if (!row) return null;
  const via = cartTileKeys(row).join(',');
  const groups = cartMaxGroups(cart.state, cartPrintId(row)).filter((g) => via && g.via === via);
  return groups.length ? groups[groups.length - 1] : null;
}
export function cartTileInCart(row) {
  return cartHasAll(cart.state, cartTileKeys(row)) || Boolean(cartTileMaxGroup(row));
}

// (Add maxed cart\" after it is added to remove from cart with red color theme")
// the tile's one-click button -- "Add maxed card", or, once the maxed card it added is in the cart, "Remove from cart" in the
// approved destructive treatment, removing exactly that maxed card.
export function cartMaxButtonHtml(row, { skeleton = false } = {}) {
  if (!row || (!(row.bcx_max > 0) && !(row.foil === 3 || row.foil === 4))) return '';
  const group = skeleton ? null : cartTileMaxGroup(row);
  if (group) {
    const tip = tipAttrs({ title: 'Remove from cart', body: `Removes the maxed card this button added (${group.keys.length} ${group.keys.length === 1 ? 'listing' : 'listings'}) from your cart.` });
    return `<button type="button" class="ceres-btn ceres-btn--secondary cm-add-max is-remove" data-control-density="compact" data-control-case="destructive" data-cm-action="remove-max" data-cm-group="${esc(group.group)}"${tip}>Remove from cart</button>`;
  }
  const tip = skeleton ? '' : tipAttrs({ title: 'Add maxed card', body: 'Adds one max-level card of this print to your cart: a single listing that is already maxed when that is cheapest, otherwise the cheapest listings whose CC adds up to max level. Your own listings are never added.' });
  return `<button type="button" class="ceres-btn ceres-btn--secondary cm-add-max" data-control-density="compact" data-cm-action="add-max"${skeleton ? ' tabindex="-1" disabled' : ''}${tip}>Add maxed card</button>`;
}

function save() {
  cartStore(storage(), cart.player, cart.state);
  syncCount();
  syncTiles();
  syncMenuFoot();
}

// the open menu's footer follows the cart live (any add, remove or re-check while it is open).
function syncMenuFoot() {
  const foot = $('cm-menu-foot');
  if (!foot || !isOpen('cm-menu-overlay')) return;
  foot.innerHTML = cartFootHtml(true);
  foot.hidden = !foot.innerHTML;
}

function syncCount() {
  const badge = $('cm-cart-count');
  const button = $('cm-cart-btn');
  const totals = cartTotals(cart.state);
  if (badge) {
    badge.textContent = String(totals.count);
    badge.dataset.state = cart.state.removed.length ? 'sold' : cart.state.items.some((it) => it.status !== 'ok') ? 'changed' : totals.count ? 'ok' : 'empty';
  }
  if (button) button.setAttribute('aria-label', `Cart: ${totals.count} ${totals.count === 1 ? 'listing' : 'listings'}${totals.count ? `, ${usd(totals.usd)}` : ''}`);
}

// In-place update of the drawn Market tiles (no re-render, the scroll position stays): the green in-cart theme, the "In cart"
// tag and the one-click button ().
function syncTiles() {
  if (typeof document === 'undefined' || !document.querySelectorAll || !cart.deps) return;
  const rows = cart.deps.rows();
  document.querySelectorAll('#cm-grid .cm-tile[data-cm-index]').forEach((tile) => {
    const row = rows[Number(tile.dataset.cmIndex)];
    if (!row) return;
    const inCart = cartTileInCart(row);
    tile.classList.toggle('is-in-cart', inCart);
    const tag = tile.querySelector('.cm-incart');
    if (tag) tag.hidden = !inCart;
    const button = tile.querySelector('.cm-add-max');
    const group = cartTileMaxGroup(row);
    const want = group ? `remove-max:${group.group}` : 'add-max:';
    const have = button ? `${button.dataset.cmAction}:${button.dataset.cmGroup || ''}` : want;
    if (button && have !== want && !button.disabled) button.outerHTML = cartMaxButtonHtml(row);
  });
}

// ── reads ────────────────────────────────────────────────────────────────────
function printUrl(pid) {
  const [cardDetailId, edition, foil] = pid.split('-');
  const params = new URLSearchParams({ card_detail_id: cardDetailId, edition, foil });
  if (cart.player) params.set('player', cart.player);
  return `/api/market/print?${params.toString()}`;
}

async function readJson(url) {
  const response = await fetch(url, { cache: 'no-store' });
  let body = null;
  try { body = await response.json(); } catch (_) { body = null; }
  if (!response.ok) {
    const detail = body && typeof body.detail === 'string' ? body.detail : '';
    throw new Error(response.status === 429 ? 'Too many market reads in a short time; wait a minute and try again.' : detail || `The market could not be read (${response.status}).`);
  }
  return body;
}

export async function cartReadPrint(pid, { fresh = false } = {}) {
  const hit = cart.prints.get(pid);
  if (hit && !fresh && Date.now() - hit.at < PRINT_TTL_MS) return hit.data;
  const data = await readJson(printUrl(pid));
  cart.prints.set(pid, { at: Date.now(), data });
  const marks = cartCheckFromPrint(cart.state, data);  // a menu read re-checks this print's cart listings for free
  if (marks.sold || marks.changed || marks.unverified) save();
  return data;
}

// After a Market read: listings drawn on the page carry their current price (free), then one re-check of the cart's prints
// per visit (and again after 60 s when the cart opens).
export function cartAfterMarketLoad(rows) {
  let changed = false;
  const byKey = new Map((rows || []).filter((r) => r.lkey).map((r) => [r.lkey, r]));
  cart.state.items = cart.state.items.map((it) => {
    const live = byKey.get(it.lkey);
    if (!live || live.price_usd === it.seen_usd || !(live.price_usd > 0)) return it;
    changed = true;
    return { ...it, status: 'changed', was_usd: it.seen_usd, price_usd: live.price_usd, price_dec: live.price_dec };
  });
  if (changed) save(); else syncTiles();
  if (!cart.checkedAt) cartRecheck();
}

export async function cartRecheck() {
  if (cart.checking || !cart.state.items.length) return;
  const pids = [...new Set(cart.state.items.map((it) => it.pid))].slice(0, 12);
  cart.checking = true;
  cart.checkedAt = Date.now();
  try {
    const params = new URLSearchParams({ prints: pids.join(',') });
    if (cart.player) params.set('player', cart.player);
    const body = await readJson(`/api/market/check?${params.toString()}`);
    cartApplyCheck(cart.state, body && body.prints);
    save();
  } catch (_) {
    /* the next Market visit checks again; the cart keeps what it had */
  } finally {
    cart.checking = false;
    if (isOpen('cm-cart-overlay')) renderCart();
  }
}

// ── dialogs (the board's overlay and panel: .slot-hazard-overlay / .slot-hazard-panel, as the combine dialog) ──────────
function isOpen(id) {
  const el = $(id);
  return Boolean(el && !el.hidden);
}

function ensureOverlay(id, titleId, bodyId, footId) {
  let overlay = $(id);
  if (overlay) return overlay;
  overlay = document.createElement('div');
  overlay.id = id;
  overlay.className = 'slot-hazard-overlay cm-dialog-overlay';
  overlay.hidden = true;
  overlay.innerHTML = `<div class="slot-hazard-panel cm-dialog-panel" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><header><h3 id="${titleId}"></h3><button type="button" class="hazard-close ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" data-cm-action="close" aria-label="Close">×</button></header><div class="slot-hazard-body cm-dialog-body" id="${bodyId}"></div><footer class="cm-dialog-foot" id="${footId}"></footer></div>`;
  overlay.addEventListener('click', (event) => {
    // a click beside the wallet that sits over this dialog closes the wallet only, never the dialog under it.
    if (event.target === overlay) { if (!document.getElementById('regional-dec-drawer')?.open) closeDialog(id); return; }
    onDialogClick(event, id);
  });
  overlay.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closeDialog(id); return; }
    if (event.key !== 'Tab') return;
    const focusable = [...overlay.querySelectorAll('button:not([disabled]), a[href]')];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.body.appendChild(overlay);
  return overlay;
}

// The dialog's narrow layout follows the panel's own width (a class, as this page's stylesheet has no at-rules).
function syncNarrow() {
  document.querySelectorAll?.('.cm-dialog-panel').forEach((panel) => panel.classList.toggle('is-narrow', panel.getBoundingClientRect().width < 640));
}

function openDialog(id) {
  const overlay = $(id);
  if (!overlay) return;
  if (!cart.narrowBound) { cart.narrowBound = true; window.addEventListener('resize', syncNarrow); }
  if (overlay.hidden) cart.lastFocus = document.activeElement;
  overlay.hidden = false;
  document.body.dataset.cmDialog = id === 'cm-cart-overlay' ? 'cart' : 'menu';  // no tile fade while a dialog is open
  syncNarrow();
  overlay.querySelector('.hazard-close')?.focus();
}

function closeDialog(id) {
  const overlay = $(id);
  if (overlay) overlay.hidden = true;
  if (id === 'cm-menu-overlay') cart.menu = null;
  if (!isOpen('cm-menu-overlay') && !isOpen('cm-cart-overlay')) delete document.body.dataset.cmDialog;
  else document.body.dataset.cmDialog = isOpen('cm-cart-overlay') ? 'cart' : 'menu';
  if (cart.lastFocus && typeof cart.lastFocus.focus === 'function' && document.contains?.(cart.lastFocus)) cart.lastFocus.focus();
}

// ── the card menu ────────────────────────────────────────────────────────────
function rowTags(listing, state) {
  const tags = [];
  if (state === 'in-cart') tags.push('<span class="cm-chip is-ok">In cart</span>');
  if (state === 'own') tags.push(`<span class="cm-chip is-warn"${tipAttrs({ title: 'Your listing', body: 'Your own card on the market; it cannot go into your cart.' })}>Your listing</span>`);
  if (listing.over_flag && state !== 'own') {
    tags.push(`<span class="cm-chip is-warn"${tipAttrs({ title: 'Expensive per CC', body: `${Math.round(listing.over_cheapest * 100)}% more per CC than the cheapest copy of this card on sale.` })}>+${Math.round(listing.over_cheapest * 100)}% over cheapest</span>`);
  }
  return tags.join('');
}

// a real table -- one shared column template (CSS subgrid)
// so level, CC, PP, $, the DEC icon (its own fixed slot), the DEC amount and PP/$ line up across every row; numbers are
// right-aligned with tabular figures. On a narrow panel PP/$ moves into the PP cell's tooltip.
// the card menu's copy list
// names each listing's seller (plain text, escaped); the maintainer's own listing reads "You" and keeps its "Your listing" badge.
export function sellerCell(listing) {
  const name = listing.own ? 'You' : String(listing.seller || '').trim() || '—';
  return `<span class="cm-cell cm-c-seller" role="cell"${listing.own ? '' : ` title="${esc(name)}"`}>${esc(name)}</span>`;
}
export const CM_COPY_HEAD = '<div class="cm-copy cm-copy-head" role="row"><span class="cm-cell cm-c-lv" role="columnheader">Level</span><span class="cm-cell cm-c-cc" role="columnheader">CC</span><span class="cm-cell cm-c-pp" role="columnheader">PP</span><span class="cm-cell cm-c-usd" role="columnheader">Price</span><span class="cm-cell cm-c-dec" role="columnheader">DEC</span><span class="cm-cell cm-c-decicon" role="columnheader" aria-hidden="true"></span><span class="cm-cell cm-c-rate" role="columnheader">PP/$</span><span class="cm-cell cm-c-seller" role="columnheader">Seller</span><span class="cm-cell cm-c-tags" role="columnheader"></span><span class="cm-cell cm-c-act" role="columnheader"></span></div>';

function listingRow(listing) {
  const state = cmRowState(listing, cart.state);
  // your own listing is shown, never addable, with the reason in words.
  const action = state === 'own' || state === 'unavailable'
    ? `<button type="button" class="ceres-btn ceres-btn--secondary cm-row-btn" data-control-density="compact" disabled${tipAttrs({ title: state === 'own' ? 'Your listing' : 'Not addable', body: state === 'own' ? 'You cannot buy your own listing.' : 'This listing cannot be identified for the cart.' })}>Add</button>`
    : state === 'in-cart'
      ? `<button type="button" class="ceres-btn ceres-btn--secondary cm-row-btn" data-control-density="compact" data-control-case="destructive" data-cm-action="remove" data-cm-key="${esc(listing.lkey)}">Remove</button>`
      : `<button type="button" class="ceres-btn ceres-btn--secondary cm-row-btn" data-control-density="compact" data-cm-action="add" data-cm-key="${esc(listing.lkey)}">Add</button>`;
  const ownNote = state === 'own' ? '<span class="cm-own-note">You cannot buy your own listing</span>' : '';
  const ppTip = tipAttrs({ title: `${num(listing.land_base_pp)} PP`, body: `${ratio(listing.pp_per_usd)} PP/$.` });
  const decIcon = listing.price_dec > 0 ? `<img class="cm-dec-icon" src="${DEC_ICON}" alt="" width="16" height="16">` : '';
  // a buyable copy without the
  // "+N% over cheapest" flag shows its price in green.
  const cheap = !listing.over_flag && state !== 'own' && state !== 'unavailable';
  return `<div class="cm-copy${cheap ? ' is-cheap' : ''}" data-state="${state}" role="row"><span class="cm-cell cm-c-lv" role="cell">Lv ${esc(listing.level ?? '—')}</span><span class="cm-cell cm-c-cc" role="cell">${num(listing.bcx)}<small class="cm-phone-label"> CC</small></span><span class="cm-cell cm-c-pp" role="cell"${ppTip}>${num(listing.land_base_pp)}<small class="cm-phone-label"> PP</small></span><span class="cm-cell cm-c-usd" role="cell">${esc(usd(listing.price_usd))}</span><span class="cm-cell cm-c-dec" role="cell">${esc(dec(listing.price_dec))}</span><span class="cm-cell cm-c-decicon" role="cell">${decIcon}</span><span class="cm-cell cm-c-rate" role="cell">${esc(ratio(listing.pp_per_usd))}</span>${sellerCell(listing)}<span class="cm-cell cm-c-tags" role="cell">${rowTags(listing, state)}${ownNote}</span><span class="cm-cell cm-c-act" role="cell">${action}</span></div>`;
}

// My cards already loaded every owned copy. Keep the market list's row treatment
// while showing only collection facts and never exposing a card UID.
function ownedCopyStatus(copy) {
  if (copy.soulbound) return 'Soulbound';
  const status = cbCopyStatus(copy);
  if (status === 'staked') return copy.plot ? `Staked on ${copy.plot}` : 'Staked';
  if (status === 'cooling') return `Cooling${copy.ready_at ? ` · ${countdownText(copy.ready_at, Date.now(), { compact: true })}` : ''}`;
  return cbUnavailableLabel(copy) || (status === 'idle' ? 'Idle' : cap(status));
}

function ownedCopiesHtml(copies, selected) {
  const showVariant = copies.some((copy) => Number(copy.foil) !== Number(selected.foil) || Number(copy.edition) !== Number(selected.edition));
  const head = `<div class="cm-copy cm-copy-head" role="row"><span class="cm-cell cm-c-lv" role="columnheader">Level</span><span class="cm-cell cm-c-cc" role="columnheader">CC</span><span class="cm-cell cm-c-pp" role="columnheader">Land PP</span><span class="cm-cell cm-c-tags" role="columnheader">Status${showVariant ? ' · print' : ''}</span></div>`;
  const rows = copies.map((copy) => {
    const variant = showVariant ? ` · ${CB_FOIL_LABELS[copy.foil] || 'Foil'} · ${CB_SET_LABELS[copy.card_set] || `Edition ${num(copy.edition)}`}` : '';
    return `<div class="cm-copy cm-owned-copy" role="row"><span class="cm-cell cm-c-lv" role="cell">Lv ${esc(copy.level ?? '—')}</span><span class="cm-cell cm-c-cc" role="cell">${num(copy.bcx)}<small class="cm-phone-label"> CC</small></span><span class="cm-cell cm-c-pp" role="cell">${num(copy.soulbound ? 0 : copy.land_base_pp)}<small class="cm-phone-label"> PP</small></span><span class="cm-cell cm-c-tags" role="cell">${esc(ownedCopyStatus(copy) + variant)}</span></div>`;
  }).join('');
  return `<section class="cm-section" aria-label="Owned copies"><h4>Owned copies <small>${num(copies.length)}</small></h4><div class="cm-copy-table cm-owned-table" role="table">${head}${rows}</div></section>`;
}

// one equal-size button per level
// the level, then the total ($ large, DEC with its icon smaller) and "N cards"; PP and PP/$ () in the tooltip. A level whose
// bundle is in the cart is a green button that removes exactly that bundle; an unreachable level is disabled, the reason in
// its tooltip. : the shared
// choice treatment (aria-pressed = in cart, painted --ok), equal tiles in a grid; styling in card-checkout.css.
function bundleLabel(level, maxLevel) {
  return level === maxLevel ? 'Max level' : `Level ${level}`;
}

function bundleButton(bundle, maxLevel, inCartGroup, maxAction = false, another = false) {
  const label = bundleLabel(bundle.level, maxLevel);
  if (inCartGroup) {
    const items = inCartGroup.items;
    const usdTotal = items.reduce((s, it) => s + (it.price_usd || 0), 0);
    const decTotal = items.every((it) => it.price_dec > 0) ? items.reduce((s, it) => s + it.price_dec, 0) : null;
    const tip = tipAttrs({ title: `${label} · in your cart`, body: `${items.length} ${items.length === 1 ? 'card' : 'cards'}, ${items.reduce((s, it) => s + (it.bcx || 0), 0).toLocaleString('en-US')} CC. Press to remove this bundle from the cart.` });
    return `<button type="button" class="ceres-btn ceres-btn--choice cm-bundle-btn is-in-cart" data-control-density="comfortable" aria-pressed="true"${maxAction ? ` aria-label="Remove maxed card from cart · ${items.length} ${items.length === 1 ? 'card' : 'cards'} · ${esc(usd(usdTotal))}"` : ''} data-cm-action="${maxAction && inCartGroup.group.startsWith('max-') ? 'remove-max' : 'remove-group'}" data-cm-group="${esc(inCartGroup.group)}"${tip}><span class="cm-bb-level">${esc(label)}</span><span class="cm-bb-usd">${esc(usd(usdTotal))}</span>${decTotal ? `<span class="cm-bb-dec">${esc(dec(decTotal))}<img class="cm-dec-icon" src="${DEC_ICON}" alt="DEC" width="16" height="16"></span>` : ''}<span class="cm-bb-n">${maxAction ? `${items.length} ${items.length === 1 ? 'card' : 'cards'} · Remove` : `In cart · ${items.length} ${items.length === 1 ? 'card' : 'cards'}`}</span></button>`;
  }
  if (!bundle.reachable) {
    const tip = tipAttrs({ title: `${label} · not available`, body: `Not enough copies on sale: ${num(bundle.cc_available)} of ${num(bundle.cc_needed)} CC.` });
    return `<span class="cm-bundle-slot"${tip}><button type="button" class="ceres-btn ceres-btn--choice cm-bundle-btn" data-control-density="comfortable" disabled><span class="cm-bb-level">${esc(label)}</span><span class="cm-bb-usd">—</span><span class="cm-bb-n">not on sale</span></button></span>`;
  }
  const reached = bundle.reached_level && bundle.reached_level !== bundle.level ? ` It reaches level ${bundle.reached_level}.` : '';
  const tip = tipAttrs({ title: label, body: `${num(bundle.land_base_pp)} PP · ${ratio(bundle.pp_per_usd)} PP/$ · ${num(bundle.bcx)} CC from ${bundle.count} ${bundle.count === 1 ? 'listing' : 'listings'} (${(bundle.parts_cc || []).map(num).join(' + ')} CC), the cheapest set not already in your cart.${reached}` });
  const action = maxAction ? 'add-max' : 'add-bundle';
  return `<button type="button" class="ceres-btn ceres-btn--choice cm-bundle-btn" data-control-density="comfortable" aria-pressed="false"${maxAction ? ` aria-label="Add ${another ? 'another' : 'a'} maxed card · ${bundle.count} ${bundle.count === 1 ? 'card' : 'cards'} · ${esc(usd(bundle.price_usd))}"` : ''} data-cm-action="${action}" data-cm-level="${esc(bundle.level)}" data-cm-keys="${esc(bundle.keys.join(','))}"${tip}><span class="cm-bb-level">${esc(label)}</span><span class="cm-bb-usd">${esc(usd(bundle.price_usd))}</span>${bundle.price_dec > 0 ? `<span class="cm-bb-dec">${esc(dec(bundle.price_dec))}<img class="cm-dec-icon" src="${DEC_ICON}" alt="DEC" width="16" height="16"></span>` : ''}<span class="cm-bb-n">${bundle.count} ${bundle.count === 1 ? 'card' : 'cards'}${maxAction ? ` · ${another ? 'Add another' : 'Add'}` : ''}</span></button>`;
}

export function cartBundleButtonsHtml(data) {
  const p = data.print;
  const fresh = cmLevelBundles(data, cart.state);
  const pick = cmMaxedPick(data, cart.state);
  // the bundles in the cart, by level (the latest one per level; a maxed card counts as the max-level bundle)
  const inCart = new Map();
  for (const part of cartPrintParts(cart.state, p.id)) {
    const level = Number(String(part.label || '').replace(/^Lv\s*/, ''));
    if (part.group && Number.isFinite(level) && level > 0) inCart.set(level, part);
  }
  const byLevel = new Map(fresh.map((b) => [b.level, b]));
  const maxItems = pick.listings;
  const maxBundle = maxItems.length ? {
    level: p.max_level, reachable: true, keys: maxItems.map((it) => it.lkey), count: maxItems.length,
    bcx: maxItems.reduce((sum, it) => sum + (it.bcx || 0), 0), parts_cc: maxItems.map((it) => it.bcx || 0),
    price_usd: maxItems.reduce((sum, it) => sum + (it.price_usd || 0), 0),
    price_dec: maxItems.every((it) => it.price_dec > 0) ? maxItems.reduce((sum, it) => sum + it.price_dec, 0) : null,
    land_base_pp: p.max_level_pp,
  } : null;
  if (maxBundle) maxBundle.pp_per_usd = maxBundle.price_usd > 0 ? p.max_level_pp / maxBundle.price_usd : null;
  const maxGroup = inCart.get(p.max_level);
  const maxButtons = [
    maxGroup ? bundleButton(maxBundle || { level: p.max_level }, p.max_level, maxGroup, true) : '',
    maxBundle ? bundleButton(maxBundle, p.max_level, null, true, Boolean(maxGroup)) : '',
    !maxGroup && !maxBundle ? bundleButton(byLevel.get(p.max_level) || { level: p.max_level, reachable: false, cc_needed: p.bcx_max || 1, cc_available: 0 }, p.max_level) : '',
  ].filter(Boolean);
  const levels = [...new Set([...byLevel.keys(), ...inCart.keys()])].filter((level) => level !== p.max_level).sort((a, b) => b - a);
  return maxButtons.join('') + levels.map((level) => bundleButton(byLevel.get(level) || { level, reachable: false, cc_needed: (p.thresholds || [])[level - 1], cc_available: 0 }, p.max_level, inCart.get(level))).join('');
}

export function cartMenuHtml(data, art, note) {
  const p = data.print;
  const thumb = art ? `<img class="cm-menu-art" src="${esc(art)}" alt="" width="96" height="134">` : '<span class="cm-menu-art cm-menu-art-missing" aria-hidden="true"></span>';
  const pick = cmMaxedPick(data, cart.state);
  const inCart = cartCountForPrint(cart.state, p.id);
  // the official-market link lives in the menu only, never on the tile.
  const officialLink = p.market_url && /^https:\/\/splinterlands\.com\//.test(p.market_url)
    ? `<a class="cm-menu-link" href="${esc(p.market_url)}" target="_blank" rel="noopener noreferrer"${tipAttrs({ title: 'View on market', body: 'Opens the official Splinterlands card page, For Sale tab, in a new tab. Nothing is bought from Ceres.' })}>View on market ↗</a>`
    : '';
  const facts = `<span>Max level ${esc(p.max_level ?? '—')}</span><span>Max CC ${p.bcx_max === 0 ? 'always max' : num(p.bcx_max)}</span><span>Max-level PP ${num(p.max_level_pp)}</span>`;
  const scope = data.truncated
    ? `${num(data.count)} cheapest per CC of ${num(data.on_sale)} on sale`
    : `${num(data.count)} on sale`;
  const head = `<div class="cm-menu-head">${thumb}<div class="cm-menu-facts"><div class="cm-menu-print">${esc(printLabel(p))}</div><div class="cm-menu-stats">${facts}</div><div class="cm-menu-scope">${esc(scope)}${inCart ? ` · <span class="cm-chip is-ok">${inCart} in cart</span>` : ''}</div>${officialLink}</div></div>`;
  const noteHtml = note ? `<p class="cm-note" data-state="${esc(note.state || 'warn')}" role="status">${esc(note.text)}</p>` : '';
  const sold = cart.state.removed.filter((it) => it.pid === p.id);
  const soldHtml = sold.length ? `<p class="cm-note" data-state="sold" role="status">${sold.length === 1 ? 'A listing' : `${sold.length} listings`} in your cart ${sold.length === 1 ? 'was' : 'were'} sold or withdrawn and ${sold.length === 1 ? 'was' : 'were'} removed from the cart.</p>` : '';
  // the bundles are priced from the copies not already in the cart; : one button each.
  const buttons = cartBundleButtonsHtml(data);
  const bundles = `<section class="cm-section" aria-label="Bundles per level"><h4>Bundles</h4><div class="cm-bundle-grid">${buttons}</div>${!pick.listings.length && pick.reason ? `<p class="cm-menu-reason">${esc(pick.reason)}</p>` : ''}</section>`;
  const copies = (data.listings || []).length
    ? `<section class="cm-section" aria-label="Copies on sale"><h4>Copies on sale <small>cheapest per CC first</small></h4><div class="cm-copy-table" role="table">${CM_COPY_HEAD}${data.listings.map(listingRow).join('')}</div></section>`
    : '<section class="cm-section"><h4>Copies on sale</h4><p class="cm-empty">No copy of this card is on sale right now.</p></section>';
  return head + noteHtml + soldHtml + bundles + copies;
}

// an empty cart gives the menu no footer at all (no
// "Open cart", no "0 listings" line); renderMenu hides the footer then and shows it again with the first item.
export function cartFootHtml(withOpen) {
  const t = cartTotals(cart.state);
  if (!t.count) return '';
  const totals = `<span class="cm-foot-total">${t.count} ${t.count === 1 ? 'listing' : 'listings'} in cart · ${cartPriceHtml(t.usd, t.dec)}</span>`;
  const open = withOpen ? '<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-cm-action="open-cart">Open cart</button>' : '';
  return totals + open;
}

function renderMenu() {
  const m = cart.menu;
  const title = $('cm-menu-title');
  const body = $('cm-menu-body');
  const foot = $('cm-menu-foot');
  if (!m || !body) return;
  if (title) title.textContent = m.row?.name || m.data?.print?.name || 'Card';
  if (m.owned) {
    const thumb = m.art ? `<img class="cm-menu-art" src="${esc(m.art)}" alt="" width="96" height="134">` : '';
    const group = m.group;
    const reason = m.error ? `<p class="cm-note" data-state="sold" role="status">${esc(m.error)}</p>` : '';
    const held = cart.ownedCombineHeld.has(m.pid);
    body.innerHTML = `<div class="cm-menu-head">${thumb}<div class="cm-menu-facts"><div class="cm-menu-print">${esc(m.row.name)}</div><div class="cm-menu-stats"><span>${num(m.copies.length)} owned ${m.copies.length === 1 ? 'copy' : 'copies'}</span></div></div></div>`
      + ownedCopiesHtml(m.copies, m.row)
      + (m.loading ? '<div class="cb-loading" role="status"><span class="spinner" aria-hidden="true"></span><span>Checking Combine…</span></div>' : reason || (held ? '<p class="cm-note" data-state="warn" role="status">Combine sent; waiting for game confirmation.</p>' : group?.sets?.length ? '<section class="cm-section"><button type="button" class="ceres-btn ceres-btn--primary" data-control-density="compact" data-cm-action="combine-owned">Combine</button></section>' : '<p class="cm-empty">No two eligible owned copies can be combined now.</p>'));
    if (foot) { foot.innerHTML = ''; foot.hidden = true; }
    return;
  }
  if (m.loading && !m.data) body.innerHTML = '<div class="cb-loading" role="status"><span class="spinner" aria-hidden="true"></span><span>Reading every copy on sale…</span></div>';
  else if (m.error && !m.data) body.innerHTML = `<p class="cm-note" data-state="sold" role="status">${esc(m.error)}</p>`;
  else body.innerHTML = cartMenuHtml(m.data, m.art, m.note);
  if (foot) { foot.innerHTML = cartFootHtml(true); foot.hidden = !foot.innerHTML; }
}

// the My cards tile uses the same card-menu shell, with this print's owned-copy answer.
// The existing combine dialog performs the eligibility, preview and signing guards.
export async function cartOpenOwnedMenu(row, art = '', { player = null, copies = [], onConfirmed = null } = {}) {
  ensureOverlay('cm-menu-overlay', 'cm-menu-title', 'cm-menu-body', 'cm-menu-foot');
  const pid = cartPrintId(row);
  cart.menu = { pid, row, art, owned: true, player, copies: copies.filter((copy) => cartPrintId(copy) === pid), onConfirmed, loading: true, group: null, error: '' };
  renderMenu();
  openDialog('cm-menu-overlay');
  try {
    const params = new URLSearchParams({ print: pid });
    if (player) params.set('player', player);
    const response = await fetch(`/api/cards/combinables?${params}`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Owned copies could not be read. Try again.');
    const body = await response.json();
    if (cart.menu?.owned && cart.menu.pid === pid) cart.menu.group = (body.groups || []).find((g) => g.print?.id === pid) || null;
  } catch (error) {
    if (cart.menu?.owned && cart.menu.pid === pid) cart.menu.error = error instanceof Error ? error.message : 'Owned copies could not be read.';
  }
  if (cart.menu?.owned && cart.menu.pid === pid) { cart.menu.loading = false; renderMenu(); }
}

export async function cartOpenMenu(row, art = '', note = null) {
  ensureOverlay('cm-menu-overlay', 'cm-menu-title', 'cm-menu-body', 'cm-menu-foot');
  const pid = cartPrintId(row);
  cart.menu = { pid, row, art, data: null, loading: true, error: '', note };
  renderMenu();
  openDialog('cm-menu-overlay');
  try {
    const data = await cartReadPrint(pid);
    if (!cart.menu || cart.menu.pid !== pid) return;
    cart.menu.data = data;
  } catch (error) {
    if (!cart.menu || cart.menu.pid !== pid) return;
    cart.menu.error = error instanceof Error ? error.message : 'The market could not be read.';
  }
  if (cart.menu) cart.menu.loading = false;
  renderMenu();
}

function listingsByKeys(data, keys) {
  const byKey = new Map((data?.listings || []).map((l) => [l.lkey, l]));
  return keys.map((k) => byKey.get(k)).filter(Boolean);
}

// one press adds one maxed card -- a single max-level listing when that is cheapest, otherwise the combination. The
// listings carry one group id (: "Remove from cart" removes exactly that maxed card) and the tile they came from.
export async function cartAddMaxed(row, { via = null } = {}) {
  const pid = cartPrintId(row);
  const data = await cartReadPrint(pid);
  const pick = cmMaxedPick(data, cart.state);
  const group = `max-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  const added = cartAdd(cart.state, pick.listings, { now: Date.now(), bundle: `Lv ${data.print.max_level ?? 'max'}`, group, via });
  if (added) save();
  return { added, reason: pick.reason || '' };
}

function refreshOpen() {
  if (isOpen('cm-menu-overlay')) renderMenu();
  if (isOpen('cm-cart-overlay')) renderCart();
}

async function onDialogClick(event, id) {
  const button = event.target.closest?.('[data-cm-action]');
  if (!button || button.disabled) return;
  const action = button.dataset.cmAction;
  const data = cart.menu?.data;
  if (action === 'close') { closeDialog(id); return; }
  if (action === 'combine-owned' && cart.menu?.owned && cart.menu.group?.sets?.length && !cart.ownedCombineHeld.has(cart.menu.pid)) {
    button.disabled = true;
    const menu = cart.menu;
    try {
      await import('./wallet.js');
      const dialog = await import('./combine-dialog.js');
      if (cart.menu !== menu) return;
      closeDialog(id);
      if (!dialog.openCombineInPlace(menu.group, {
        onConfirmed: ({ combineRefresh } = {}) => {
          cart.ownedCombineHeld.delete(menu.pid);
          void cartRefreshOwned(menu.player, () => menu.onConfirmed?.(), { start: combineRefresh?.triggered === false });
        },
        onClosed: ({ outcome } = {}) => { if (outcome === 'unknown') cart.ownedCombineHeld.add(menu.pid); },
      })) { menu.error = 'This card has no two copies to combine now.'; openDialog(id); renderMenu(); }
    } catch (_) {
      if (cart.menu !== menu) return;
      menu.error = 'The combine dialog could not be loaded. Try again.';
      button.disabled = false;
      openDialog(id);
      renderMenu();
    }
    return;
  }
  if (action === 'open-cart') { cartOpenCart(); return; }
  if (action === 'checkout') { cartCheckout(); return; }
  if (action === 'buy-max') { cartCheckout(button.dataset.cmGroup); return; }
  if (action === 'add' && data) {
    if (cartAdd(cart.state, listingsByKeys(data, [button.dataset.cmKey]), { now: Date.now() })) save();
  } else if (action === 'remove' || action === 'remove-item') {
    if (cartRemove(cart.state, button.dataset.cmKey)) save();
  } else if (action === 'add-bundle' && data) {
    const keys = String(button.dataset.cmKeys || '').split(',').filter(Boolean);
    const group = `lv-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
    if (cartAdd(cart.state, listingsByKeys(data, keys), { now: Date.now(), bundle: `Lv ${button.dataset.cmLevel}`, group })) save();
  } else if (action === 'remove-group') {
    if (cartRemoveGroup(cart.state, button.dataset.cmGroup)) save();
  } else if (action === 'remove-max') {
    if (cartRemoveGroup(cart.state, button.dataset.cmGroup)) save();
    if (cart.menu) cart.menu.note = null;
  } else if (action === 'add-max' && cart.menu) {
    const { added, reason } = await cartAddMaxed(cart.menu.row, { via: cartTileKeys(cart.menu.row).join(',') });
    if (cart.menu) cart.menu.note = added ? { state: 'ok', text: `Added one maxed card (${added} ${added === 1 ? 'listing' : 'listings'}) to the cart.` } : { state: 'warn', text: reason };
  } else if (action === 'remove-group') {
    if (cartRemove(cart.state, cart.state.items.filter((it) => it.pid === button.dataset.cmPid).map((it) => it.lkey))) save();
  } else if (action === 'clear-cart') {
    cartClear(cart.state);
    save();
  } else if (action === 'dismiss-sold') {
    cart.state.removed = [];
    save();
  }
  refreshOpen();
}

// ── the cart panel ───────────────────────────────────────────────────────────
function itemStatus(it) {
  if (it.status === 'changed') return `<span class="cm-chip is-warn"${tipAttrs({ title: 'Price changed', body: `Was ${usd(it.was_usd)} when you added it; ${usd(it.price_usd)} now.` })}>Price changed · was ${esc(usd(it.was_usd))}</span>`;
  if (it.status === 'unverified') return `<span class="cm-chip is-warn"${tipAttrs({ title: 'Not re-checked', body: 'The market gives only the 60 cheapest copies per CC of this card, and this listing was not among them on the last read. It may still be on sale.' })}>Not re-checked</span>`;
  return '';
}

export function cartPanelHtml() {
  const groups = cartGroups(cart.state);
  const sold = cart.state.removed;
  const soldHtml = sold.length
    ? `<div class="cm-note" data-state="sold" role="status"><p>${sold.length === 1 ? 'One listing was' : `${sold.length} listings were`} sold or withdrawn since you added ${sold.length === 1 ? 'it' : 'them'} and ${sold.length === 1 ? 'was' : 'were'} removed from your cart:</p><ul>${sold.map((it) => `<li>${esc(it.name)} · Lv ${esc(it.level ?? '—')} · ${num(it.bcx)} CC · ${esc(usd(it.seen_usd))}</li>`).join('')}</ul><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-cm-action="dismiss-sold">Dismiss</button></div>`
    : '';
  if (!groups.length) return `${soldHtml}<p class="cm-empty">Your cart is empty. Open a card on the Market to add copies, a bundle or a maxed card.</p>`;
  const body = groups.map((g) => {
    const items = g.items.map((it) => `<div class="cm-row cm-cart-item" data-state="${esc(it.status)}" role="row"><span class="cm-cell cm-c-lv" role="cell">Lv ${esc(it.level ?? '—')}</span><span class="cm-cell cm-c-cc" role="cell">${num(it.bcx)} CC</span><span class="cm-cell cm-c-pp" role="cell">${num(it.land_base_pp)} PP</span><span class="cm-cell cm-c-price" role="cell">${cartPriceHtml(it.price_usd, it.price_dec)}</span><span class="cm-cell cm-c-tags" role="cell">${itemStatus(it)}</span><span class="cm-cell cm-c-act" role="cell"><button type="button" class="ceres-btn ceres-btn--secondary cm-row-btn" data-control-density="compact" data-control-case="destructive" data-cm-action="remove-item" data-cm-key="${esc(it.lkey)}">Remove</button></span></div>`).join('');
    const label = [CB_SET_LABELS[g.card_set] || cap(g.card_set), CB_FOIL_LABELS[g.foil], cap(g.rarity)].filter(Boolean).join(' · ');
    // a maxed card added as several listings is bought in one purchase, then combined
    const bundleLabels = [...new Set(g.items.map((it) => it.bundle).filter(Boolean))].map((label) => `<span class="cm-chip cm-group-bundle">${esc(label)} bundle</span>`).join('');
    const buyMax = cartMaxGroups(cart.state, g.pid).filter((m) => m.keys.length >= 2 && m.keys.length <= 150).map((m) => `<button type="button" class="ceres-btn ceres-btn--secondary cm-buymax" data-control-density="compact" data-cm-action="buy-max" data-cm-group="${esc(m.group)}"${tipAttrs({ title: 'Buy & max', body: `Buys these ${m.keys.length} listings in one purchase (Active key), then, once every copy is confirmed bought, combines them into one max-level card (Posting key). 2 signatures: buy, then combine.` })}>Buy &amp; max · ${m.keys.length} copies</button>`).join('');
    return `<section class="cm-section cm-cart-group"><h4>${esc(g.name)} ${bundleLabels}<small>${esc(label)} · ${num(g.cc)} CC · ${g.items.length} ${g.items.length === 1 ? 'listing' : 'listings'}</small><span class="cm-group-total">${cartPriceHtml(g.usd, g.dec)}</span></h4>${buyMax ? `<div class="cm-buymax-row">${buyMax}</div>` : ''}<div class="cm-rows" role="table">${items}</div></section>`;
  }).join('');
  return cartPayBarHtml() + soldHtml + body;
}

// the total and Checkout sit in a
// bar at the top of the cart's scroll area (sticky); each maxed card's Buy & max stays on top of its own listings.
export function cartPayBarHtml() {
  const t = cartTotals(cart.state);
  return `<section class="cm-paybar co-paybar" aria-label="Total and checkout"><div class="co-pay-head"><span class="co-pay-total cm-foot-total">Total · ${t.count} ${t.count === 1 ? 'listing' : 'listings'} · ${cartPriceHtml(t.usd, t.dec)}</span>`
    + `<span class="cm-checkout-wrap"${tipAttrs({ title: 'Checkout', body: CHECKOUT_TIP })}><button type="button" class="ceres-btn ceres-btn--primary cm-checkout" data-control-density="comfortable" id="cm-checkout" data-cm-action="checkout"${t.count ? '' : ' disabled'} aria-describedby="cm-checkout-note">Checkout</button><span class="sr-only" id="cm-checkout-note">${CHECKOUT_TIP}</span></span></div></section>`;
}

function cartPanelFootHtml() {
  const t = cartTotals(cart.state);
  return `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-case="destructive" data-cm-action="clear-cart"${t.count ? '' : ' disabled'}>Clear cart</button>`;
}

function renderCart() {
  const title = $('cm-cart-title');
  const body = $('cm-cart-body');
  const foot = $('cm-cart-foot');
  if (title) title.textContent = 'Cart';
  if (body) body.innerHTML = cartPanelHtml();
  if (foot) foot.innerHTML = cartPanelFootHtml();
}

export function cartOpenCart() {
  ensureOverlay('cm-cart-overlay', 'cm-cart-title', 'cm-cart-body', 'cm-cart-foot');
  renderCart();
  openDialog('cm-cart-overlay');
  if (Date.now() - cart.checkedAt > CHECK_TTL_MS) cartRecheck();
}

// the checkout dialog (card-checkout.js, loaded on demand). Bought listings leave the cart; the rest stay.
// `onlyGroup` = Buy & max of one maxed-card group (just its listings); the whole cart otherwise (its maxed-card groups
// still stay in one transaction each and are offered the combine once bought).
async function cartCheckout(onlyGroup = null) {
  const { openCheckout } = await import('./card-checkout.js');
  openCheckout({
    player: cart.player,
    buyMax: Boolean(onlyGroup),
    items: cart.state.items.filter((it) => !onlyGroup || it.group === onlyGroup).map((it) => ({ ...it, seen_usd: it.seen_usd ?? it.price_usd })),  // a rise since adding is amber ()
    onBought: (keys) => { if (cartRemove(cart.state, keys)) save(); refreshOpen(); cartAfterPurchase(); },
    onCombined: () => cartAfterPurchase({ combined: true }),
    onClose: () => refreshOpen(),
  });
}

// after a confirmed purchase, and again after a confirmed Buy & max
// combine, the Market reads its listings again (the normal debounced read; the server dropped its 60 s market cache on the
// confirmed purchase, so bought listings are gone) and My cards reads the account again: one quick board read (the board's own
// api/capture/refresh; the combine's confirmation already started one), waited for on /api/capture/status, then the Cards list
// reloads. No manual reload.
export const OWNED_WAIT_ROUNDS = 24;   // x 5 s long-poll (CAPTURE_STATUS_WAIT_MAX_MS) = up to 2 minutes
export async function cartRefreshOwned(player, reloadCards, { start = true, fetcher = globalThis.fetch } = {}) {
  const q = `player=${encodeURIComponent(player)}`;
  try {
    if (start) await fetcher(`/api/capture/refresh?${q}`, { method: 'POST', cache: 'no-store' });
    for (let i = 0; i < OWNED_WAIT_ROUNDS; i += 1) {
      const response = await fetcher(`/api/capture/status?${q}&wait=5000&wait_for=settled`, { cache: 'no-store' });
      const status = response?.ok ? await response.json() : null;
      if (!status || !['running', 'queued'].includes(status.state)) break;
    }
  } catch (_) { /* the list reloads from what is stored */ }
  reloadCards?.();
}
function cartAfterPurchase({ combined = false } = {}) {
  cart.deps?.reloadMarket?.();
  if (cart.player) cartRefreshOwned(cart.player, cart.deps?.reloadCards, { start: !combined });
}

// ── tiles: click opens the menu, "Add maxed card" adds one maxed card ─────────
function rowForTile(tile, rows) {
  const index = Number(tile?.dataset?.cmIndex);
  return Number.isInteger(index) ? rows()[index] : null;
}

async function onTileAddMax(button, row) {
  button.disabled = true;
  try {
    const { added, reason } = await cartAddMaxed(row, { via: cartTileKeys(row).join(',') });
    if (added) {
      button.disabled = false;
      syncTiles();  // the tile turns green and its button becomes "Remove from cart"
      return;
    }
    button.disabled = false;
    cartOpenMenu(row, button.closest('.cm-tile')?.querySelector('.cb-art img')?.getAttribute('src') || '', { state: 'warn', text: reason });
  } catch (error) {
    button.disabled = false;
    cartOpenMenu(row, '', { state: 'sold', text: error instanceof Error ? error.message : 'The market could not be read.' });
  }
}

export function initCart(deps) {
  cart.deps = deps;
  cart.player = deps.player ? deps.player() : null;
  cart.state = cartLoad(storage(), cart.player);
  syncCount();
  $('cm-cart-btn')?.addEventListener('click', () => cartOpenCart());
  const grid = $('cm-grid');
  if (!grid || !grid.addEventListener) return;
  grid.addEventListener('click', (event) => {
    if (event.target.closest?.('a')) return;  // "View on market ↗" opens the official page, never the menu
    const tile = event.target.closest?.('.cm-tile');
    if (!tile) return;
    const row = rowForTile(tile, deps.rows);
    if (!row) return;
    const add = event.target.closest('[data-cm-action="add-max"]');
    if (add) { if (!add.disabled) onTileAddMax(add, row); return; }
    const remove = event.target.closest('[data-cm-action="remove-max"]');
    if (remove) { if (cartRemoveGroup(cart.state, remove.dataset.cmGroup)) save(); return; }
    cartOpenMenu(row, tile.querySelector('.cb-art img')?.getAttribute('src') || '');
  });
  grid.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const tile = event.target;
    if (!tile?.classList?.contains('cm-tile')) return;
    event.preventDefault();
    const row = rowForTile(tile, deps.rows);
    if (row) cartOpenMenu(row, tile.querySelector('.cb-art img')?.getAttribute('src') || '');
  });
}

export const _cartForTests = cart;
export { cartHas };
