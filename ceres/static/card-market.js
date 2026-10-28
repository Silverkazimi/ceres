// card-market.js -- : the Cards page's Market mode ("shop", phase 2). Read only.
// (maintainer: "remove the load market listings. All listin will always be loaded automatically when needed. each filter
// change will automatically show the correct cards on the market"): the market is read when the page enters Market mode, and
// again after every filter or sort change that changes which prints the server reads (debounced 400 ms; the read still in
// flight is aborted). A change of a filter the server does not use (the PP range) re-filters the loaded listings only.
// Listings are drawn with the Cards page's own tile (card-browser.js tileHtml, passed in), with a price badge on the art
// PP / PP/$ below it (: no market link on the tile; the card menu has it). With the Maxed filter a tile can be
// a combination of listings (). There is no buy, sign or transfer action anywhere in this module.
// a tile opens the card menu (card-cart.js) and carries "Add maxed card"; the cart button sits in the header.
// the price badge is anchored to the art's bottom edge.
import { tipAttrs } from './tooltip.js';
import { cartAfterMarketLoad, cartMaxButtonHtml, cartTileInCart, cartTileKeys, initCart } from './card-cart.js';
import {
  CM_SORTS, cmFormatDec, cmFormatRatio, cmFormatUsd, cmMatches, cmPpPerDec, cmPpPerUsd, cmQuery, cmSummary, cmView,
} from './card-market-model.js';

const MODE_KEY = 'ceres:cards:mode';
const SORT_KEY = 'ceres:cards:market-sort';
const CHUNK = 48;
export const CM_DEBOUNCE_MS = 400;

const market = {
  deps: null,
  rows: [],
  pages: [],        // one array of listings per page read (Load more)
  page: 1,
  moreController: null,
  loaded: false,
  loading: false,
  readWith: null,
  url: '',          // the query of the rows on screen
  wantUrl: '',      // the query being read or waiting for the debounce
  controller: null,
  timer: 0,
  meta: null,
  view: null,
  rendered: 0,
  sort: 'cost_per_pp',
};

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function num(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return Math.round(Number(value)).toLocaleString('en-US');
}

export function cmMode() {
  return document.body?.dataset?.cbMode === 'market' ? 'market' : 'cards';
}

// ── tile extras (drawn into the Cards tile) ─────────────────────────────────
// a tile
// that stands for several listings says how many.
export function cmCombinedText(row) {
  return row && row.combined > 1 ? `${row.combined} listings combined` : '';
}

const DEC_ICON = '/static/assets/land/token-dec.png';  // icons.js resource 'dec'

function priceTip(row) {
  const usd = row.price_usd > 0 ? cmFormatUsd(row.price_usd) : '—';
  const combined = cmCombinedText(row);
  return tipAttrs({
    title: `${combined ? 'Total price' : 'Price'} ${usd} · ${cmFormatDec(row.price_dec)} DEC`,
    body: [
      combined
        ? `The cheapest set of ${row.combined} listings of this print whose CC adds up to max level (${(row.parts_cc || []).map((cc) => num(cc)).join(' + ')} CC), prices added together; ${cmFormatDec(row.price_dec)} DEC at today's rate.`
        : `As listed on the market; ${cmFormatDec(row.price_dec)} DEC at today's rate.`,
      row.own ? 'This is your own listing.' : '',
    ].filter(Boolean).join(' '),
  });
}

// (maintainer: "build a clean full width badge, that sits on the bottom of the card tiles and shows the price in $ and DEC.
// Format this well. use the dec icon. same distances, centeed, well readable, large numbers, glass design"): the board's
// full-width .slot-wait-badge, $ large, then DEC with its icon. (maintainer: "the price banner must be at the bottom of the
// tile"): drawn at the art's bottom edge (tileHtml extra.bottom), below the name, no longer in the foot over it.
export function cmPriceBadge(row) {
  const usd = row.price_usd > 0 ? cmFormatUsd(row.price_usd) : '—';
  const dec = row.price_dec > 0
    ? `<span class="cm-price-dec"><img class="cm-dec-icon" src="${DEC_ICON}" alt="DEC" width="16" height="16">${esc(cmFormatDec(row.price_dec))}</span>`
    : '';
  // (maintainer: "split the price badge into two side-by-side boxes with the same style. the first shows $ and the second DEC
  // each centered"): two equal boxes, $ left, DEC (icon and amount as one unit) right, one tooltip on the pair.
  return `<span class="cm-price-badges"${priceTip(row)}><span class="slot-wait-badge cm-price-badge cm-price-box-usd"><span class="cm-price-usd">${esc(usd)}</span></span><span class="slot-wait-badge cm-price-badge cm-price-box-dec">${dec || '<span class="cm-price-dec">—</span>'}</span></span>`;
}

// The top tag row (right side): "N listings combined" on a combined Maxed offer, "Your listing" on the maintainer's own listing
// (amber, ), "In cart" (green, ; always drawn, shown while the tile's listings are in the cart).
const ORDINAL = { 2: '2nd', 3: '3rd' };
export function cmTopTags(row, inCart = false) {
  const combined = cmCombinedText(row);
  // the following maxed copies of a print are priced after the cheaper ones before them are taken out.
  const seq = row.maxed_seq > 1
    ? `<span class="cm-tag cm-seq"${tipAttrs({ title: `${ORDINAL[row.maxed_seq] || `${row.maxed_seq}th`} maxed copy`, body: 'Priced from the listings left after the cheaper maxed copies of this card before it are bought, so buying both costs both prices.' })}>${esc(ORDINAL[row.maxed_seq] || `${row.maxed_seq}th`)} maxed copy</span>`
    : '';
  const tags = `${seq}${combined ? `<span class="cm-tag cm-combined"${priceTip(row)}>${esc(combined)}</span>` : ''}${row.own ? '<span class="cm-tag cm-own">Your listing</span>' : ''}`
    + `<span class="cm-tag cm-incart"${inCart ? '' : ' hidden'}>In cart</span>`;
  return `<span class="cm-tags">${tags}</span>`;
}

// the one-click button ("Add maxed card" / "Remove from cart") is drawn by card-cart.js (cartMaxButtonHtml).

// PP and PP/$ only.
// (maintainer: "cost per pp filter color codes the PP per $ box in gold (top pick), green (above 20% pp/$ range to top pick)
// orange (20-40% above top pick) and red (40+% above top pick)"): under the Cost per PP sort only, the tile's cost per PP against
// the top pick (the best PP/$ among the offers shown, every loaded page): gap = top PP/$ ÷ tile PP/$ − 1. 0 (ties included)
// is-top, up to 20 % is-good, up to 40 % is-fair, beyond is-dear. The classes are styled by card-browser.css.
export function cmRateTier(rate, top) {
  if (!(rate > 0) || !(top > 0)) return null;
  const gap = Math.max(0, top / rate - 1);
  const round = Math.round(gap * 1e9) / 1e9;  // float noise never moves a tile across a boundary
  const cls = round === 0 ? 'is-top' : round <= 0.2 ? 'is-good' : round <= 0.4 ? 'is-fair' : 'is-dear';
  return { cls, gap: round };
}

export function cmTopRate(rows) {
  let top = null;
  for (const row of rows || []) {
    const rate = cmPpPerUsd(row);
    if (rate !== null && (top === null || rate > top)) top = rate;
  }
  return top;
}

export function cmMetrics(row, tier = null) {
  const now = cmPpPerUsd(row);
  const ppTip = tipAttrs({
    title: row.combined > 1 ? 'Max-level Land base PP' : 'Current Land base PP',
    body: row.combined > 1 ? 'The listings combined reach max level: the print\'s max-level PP.'
      : row.pp_source === 'listing' ? 'As the game reports it for this listing.' : row.pp_source === 'formula'
        ? 'Derived: max-level PP × CC ÷ max CC (Land PP grows with CC up to max level).' : 'Not known for this print.',
  });
  const rank = tier ? (tier.cls === 'is-top' ? ' Top pick.' : ` ${Math.round(tier.gap * 100)}% more per PP than the top pick.`) : '';
  const nowTip = tipAttrs({
    title: 'PP/$',
    body: `${row.combined > 1 ? 'Max-level PP ÷ total price' : 'Current PP ÷ price'} in $. ${cmFormatRatio(cmPpPerDec(row))} PP per DEC at today's rate.${rank}`,
  });
  return `<span class="candidate-grid-metric cb-metric-pp"${ppTip}><strong>${num(row.land_base_pp)}</strong><small>PP</small></span>`
    + `<span class="candidate-grid-metric cm-metric-rate${tier ? ` ${tier.cls}` : ''}"${nowTip}><strong>${esc(cmFormatRatio(now))}</strong><small>PP/$</small></span>`;
}

// no official-market link on the tile; the card menu carries it.

export function cmTileHtml(tile, tileHtml, { fade = false, skeleton = false } = {}) {
  const row = tile.listing;
  if (skeleton) {
    const html = tileHtml({ ...tile, extra: { className: 'cm-tile cm-skeleton', attrs: ' aria-hidden="true"', art: cmTopTags(row), bottom: cmPriceBadge(row), metrics: cmMetrics(row), after: `<span class="cm-tile-actions">${cartMaxButtonHtml(row, { skeleton: true })}</span>`, label: 'Loading' } });
    return html.replace(/<img [^>]*data-cb-art=[^>]*>/, '');
  }
  if (tile.fold) return cmFoldTileHtml(tile, tileHtml, { fade });
  const combined = cmCombinedText(row);
  const inCart = cartTileInCart(row);
  const label = `${row.name}. Level ${row.level ?? '—'}. CC ${row.bcx ?? '—'}.${combined ? ` ${combined}.` : ''} Price ${cmFormatUsd(row.price_usd)} (${cmFormatDec(row.price_dec)} DEC). PP ${num(row.land_base_pp)}, ${cmFormatRatio(cmPpPerUsd(row))} PP/$. Opens the card menu.`;
  // the tile opens the card menu (card-cart.js, delegated on #cm-grid); the keys are opaque listing keys, never shown.
  const keys = cartTileKeys(row).join(',');
  const attrs = ` tabindex="0" aria-haspopup="dialog" data-cm-index="${esc(tile.key)}"${keys ? ` data-cm-keys="${esc(keys)}"` : ''}`;
  const after = `<span class="cm-tile-actions">${cartMaxButtonHtml(row)}</span>`;
  return tileHtml({ ...tile, extra: { className: `cm-tile${inCart ? ' is-in-cart' : ''}${fade ? ' cm-fade-in' : ''}`, attrs, art: cmTopTags(row, inCart), bottom: cmPriceBadge(row), metrics: cmMetrics(row, tile.rateTier || null), after, label } });
}

// a container stands for a print's
// small copies (card-market-model.js cmFold). It is drawn with the same tile and slots as a listing -- art, price badge, PP and
// PP/$ of its best copy, the one-click button -- so the grid keeps one tile size; the count tag (cm-fold-count) and the "from"
// price (cm-fold-from) say what it holds, and a click opens the card menu with every copy (card-cart.js, by data-cm-index).
export function cmFoldText(fold) {
  return `+${num(fold.count)} more ${fold.count === 1 ? 'copy' : 'copies'}`;
}

export function cmFoldTileHtml(tile, tileHtml, { fade = false } = {}) {
  const row = tile.listing;
  const fold = tile.fold;
  const inCart = cartTileInCart(row);
  const from = `from ${cmFormatUsd(fold.fromUsd)}`;
  const ccs = fold.rows.map((r) => r.bcx).filter((v) => Number.isFinite(v));
  const span = ccs.length ? (Math.min(...ccs) === Math.max(...ccs) ? `${num(Math.min(...ccs))} CC` : `${num(Math.min(...ccs))}–${num(Math.max(...ccs))} CC`) : '';
  const tip = tipAttrs({
    title: `${cmFoldText(fold)} · ${from}`,
    body: `${num(fold.count)} smaller copies of this card${span ? ` (${span})` : ''}: under 25% of its max CC and not among its two best deals (PP/$ within 20% of its best). Click to see every copy in the card menu.`,
  });
  const dec = fold.fromDec > 0
    ? `<span class="cm-price-dec"><img class="cm-dec-icon" src="${DEC_ICON}" alt="DEC" width="16" height="16">${esc(cmFormatDec(fold.fromDec))}</span>`
    : '<span class="cm-price-dec">—</span>';
  const bottom = `<span class="cm-price-badges cm-fold-from"${tip}><span class="slot-wait-badge cm-price-badge cm-price-box-usd"><span class="cm-price-usd">${esc(from)}</span></span><span class="slot-wait-badge cm-price-badge cm-price-box-dec">${dec}</span></span>`;
  const art = `<span class="cm-tags"><span class="cm-tag cm-fold-count"${tip}>${esc(cmFoldText(fold))}</span><span class="cm-tag cm-incart"${inCart ? '' : ' hidden'}>In cart</span></span>`;
  const keys = cartTileKeys(row).join(',');
  const attrs = ` tabindex="0" aria-haspopup="dialog" data-cm-index="${esc(tile.key)}" data-cm-fold="${esc(fold.count)}"${keys ? ` data-cm-keys="${esc(keys)}"` : ''}`;
  const label = `${row.name}. ${cmFoldText(fold)}${span ? `, ${span}` : ''}, ${from} (${cmFormatDec(fold.fromDec)} DEC). Opens the card menu with every copy.`;
  const after = `<span class="cm-tile-actions">${cartMaxButtonHtml(row)}</span>`;
  return tileHtml({ ...tile, extra: { className: `cm-tile cm-fold-tile${inCart ? ' is-in-cart' : ''}${fade ? ' cm-fade-in' : ''}`, attrs, art, bottom, metrics: cmMetrics(row, tile.rateTier || null), after, label } });
}

// ── rendering ────────────────────────────────────────────────────────────────
function readTime(iso) {
  const at = Date.parse(iso || '');
  return Number.isFinite(at) ? new Date(at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
}

// the summary bar is
// always drawn in Market mode (its height is reserved in card-browser.css); before the first read it shows the same items with
// dashes, and during a re-read it keeps the last read's figures until the new answer is in.
export const CM_SUMMARY_PLACEHOLDER = '<span class="cb-summary-item"><strong>—</strong> listings shown</span><span class="cb-summary-item">Best <strong>— PP/$</strong></span><span class="cb-summary-item cb-summary-note">Reading the market…</span>';

export function cmSummaryCount(s, maxedOn) {
  if (maxedOn) return '<strong>' + num(s.listings) + '</strong> maxed ' + (s.listings === 1 ? 'card' : 'cards') + ' (from ' + num(s.behind) + ' ' + (s.behind === 1 ? 'listing' : 'listings') + ')';
  return '<strong>' + num(s.listings) + '</strong> ' + (s.listings === 1 ? 'listing' : 'listings') + ' shown';
}

function renderSummary() {
  const box = $('cm-summary');
  if (!box) return;
  box.hidden = false;
  if (!market.loaded || !market.view) { box.innerHTML = CM_SUMMARY_PLACEHOLDER; return; }
  const s = market.view.summary;
  const m = market.meta || {};
  const best = s.cheapest
    ? `<span class="cb-summary-item">Best <strong>${esc(cmFormatRatio(s.cheapest.rate))} PP/$</strong> <span class="cb-summary-note">${esc(s.cheapest.name)}${s.cheapest.dec > 0 ? ` · ${esc(cmFormatRatio(1 / s.cheapest.dec))} PP per DEC` : ''}</span></span>`
    : '';
  const scope = m.prints_matching ? ' <span class="cb-summary-note">(best ' + num(m.prints_read) + ' of ' + num(m.prints_matching) + ' prints on sale)</span>' : '';
  let time = '';
  if (m.read_at) {
    const took = m.elapsed_ms ? ' in ' + (m.elapsed_ms / 1000).toFixed(1) + ' s' : '';
    const rate = m.dec_usd > 0 ? ' · 1 DEC = $' + Number(m.dec_usd).toPrecision(3) : '';
    time = '<span class="cb-summary-item cb-summary-note">Market read at ' + esc(readTime(m.read_at) + took + rate) + '</span>';
  }
  // (maintainer chose "Option 1"): with Maxed on, the maxed cards and the listings behind them.
  const maxedOn = Boolean(market.deps.filters().maxed);
  const shown = '<span class="cb-summary-item">' + cmSummaryCount(s, maxedOn) + scope + '</span>';
  box.innerHTML = shown + best + time;
}

function renderMore() {
  const grid = $('cm-grid');
  if (!grid || !market.view) return;
  const tiles = market.view.tiles;
  if (market.rendered >= tiles.length) return;
  const end = Math.min(tiles.length, market.rendered + CHUNK);
  const count = grid.children ? grid.children.length : 0;
  grid.insertAdjacentHTML('beforeend', tiles.slice(market.rendered, end).map((tile) => cmTileHtml(tile, market.deps.tileHtml, { fade: market.fade })).join(''));
  if (market.fade && grid.children) [...grid.children].slice(count).forEach((el, index) => fadeIn(el, index));
  market.rendered = end;
}

// placeholder tiles before the first read -- the real tile renderer with an empty listing, so each placeholder has the
// exact size of a market tile; the text is hidden and the boxes shimmer softly (card-browser.css .cm-skeleton).
const SKELETON_ROW = {
  name: '', card_detail_id: null, edition: null, card_set: null, rarity: null, foil: 0, level: 1, max_level: 1, bcx: 1, bcx_max: 1,
  maxed: false, land_base_pp: 0, max_level_pp: 0, price_usd: 0, price_dec: 0, splinters: [], combined: 1,
};
export function cmSkeletonHtml(tileHtml) {
  const tile = { key: -1, skeleton: true, card: { ...SKELETON_ROW, no_pp: false, land_abilities: [], skin: null }, status: 'idle', plot: null, readyAt: null, unavailable: '', ready: false, listing: SKELETON_ROW };
  return cmTileHtml(tile, tileHtml, { skeleton: true });
}

// (maintainer: "the loading animation shows one tile too much, which breaks into the next row. Instead fill the entire page
// with empty tiles and fill them in gracefully, removing those that are not needed when results are too few"): whole rows only
// the grid's own column count times the rows that fill the visible viewport below the grid's top.
export function cmSkeletonCount(columns, viewportHeight, gridTop, rowHeight, rowGap) {
  const cols = Math.max(1, Math.floor(Number(columns)) || 1);
  const step = Number(rowHeight) > 0 ? Number(rowHeight) + (Number(rowGap) || 0) : 0;
  if (!step) return cols;  // the tile size is unknown: one full row
  const visible = Math.max(Number(rowHeight), Number(viewportHeight) - Math.max(0, Number(gridTop) || 0));
  return cols * Math.max(1, Math.ceil(visible / step));
}

function gridColumns(grid) {
  try {
    const tracks = String(getComputedStyle(grid).gridTemplateColumns || '').trim();
    return tracks && tracks !== 'none' ? tracks.split(/\s+/).length : 1;
  } catch (_) { return 1; }
}

function renderSkeletons(grid) {
  const one = cmSkeletonHtml(market.deps.tileHtml);
  const cols = gridColumns(grid);
  grid.innerHTML = one.repeat(cols);  // one row first, to measure a placeholder
  let height = 0;
  let gap = 0;
  try {
    height = grid.firstElementChild?.getBoundingClientRect?.().height || 0;
    gap = parseFloat(getComputedStyle(grid).rowGap) || 0;
  } catch (_) { height = 0; }
  const top = grid.getBoundingClientRect ? grid.getBoundingClientRect().top : 0;
  const count = cmSkeletonCount(cols, window.innerHeight, top, height, gap);
  if (count > cols) grid.insertAdjacentHTML('beforeend', one.repeat(count - cols));
  market.skeletonCols = cols;
  shimmer(grid);
}

function fillViewport() {
  const sentinel = $('cm-sentinel');
  let guard = 0;
  while (sentinel && market.view && cmMode() === 'market' && market.rendered < market.view.tiles.length
    && sentinel.getBoundingClientRect().top < window.innerHeight * 2 && guard < 20) {
    renderMore();
    guard += 1;
  }
}

function setMessage(text) {
  const message = $('cm-message');
  if (!message) return;
  message.hidden = !text;
  message.textContent = text || '';
}

// the new tiles take the slots of the tiles on screen (placeholders or the last read's tiles) one after another, each
// fading in 30 ms after the one before (opacity only, every tile in its slot); slots left over fade out, then go. Returns false
// when the grid cannot be swapped in place (no slots, or no DOM to parse with), so the caller draws it plainly.
export const CM_STAGGER_MS = 30;

// Motion runs as Web Animations (this page's stylesheet keeps plain .card-browser rules only, no @keyframes / @media); under
// prefers-reduced-motion nothing fades or shimmers (body.cb-reduce-motion also drops the CSS transitions).
export function cmReduceMotion() {
  try { return Boolean(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); } catch (_) { return false; }
}
function fadeIn(el, index) {
  if (cmReduceMotion() || typeof el?.animate !== 'function') return;
  el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, delay: Math.min(index, 30) * CM_STAGGER_MS, easing: 'ease', fill: 'backwards' });
}
function shimmer(grid) {
  if (cmReduceMotion() || !grid?.querySelectorAll) return;
  grid.querySelectorAll('.cm-skeleton .cb-art, .cm-skeleton .candidate-grid-metric').forEach((el) => {
    if (typeof el.animate === 'function') el.animate([{ opacity: 0.5 }, { opacity: 0.9 }, { opacity: 0.5 }], { duration: 1600, iterations: Infinity, easing: 'ease-in-out' });
  });
}
function swapInPlace(grid, tiles) {
  const old = grid && grid.children ? [...grid.children] : [];
  if (swapSlots(grid, tiles.slice(0, CHUNK), old)) { market.rendered = Math.min(tiles.length, CHUNK); return true; }
  return false;
}

function swapSlots(grid, first, old) {
  if (!old.length || typeof document.createElement !== 'function' || typeof grid.replaceChild !== 'function') return false;
  const template = document.createElement('template');
  if (!template.content) return false;
  template.innerHTML = first.map((tile) => cmTileHtml(tile, market.deps.tileHtml, { fade: true })).join('');
  const fresh = [...template.content.children];
  fresh.forEach((el, index) => {
    if (index < old.length) grid.replaceChild(el, old[index]);
    else grid.appendChild(el);
    fadeIn(el, index);
  });
  cmTrimSlots(old.slice(fresh.length));
  return true;
}

// Unused slots (fewer results than slots): fade out, then removed; they are the last ones, so no filled tile moves.
export function cmTrimSlots(extra) {
  for (const el of extra) {
    el.classList.add('cm-fade-out');
    el.setAttribute('aria-hidden', 'true');
    setTimeout(() => el.remove(), cmReduceMotion() ? 0 : 220);
  }
}

// called only when what is shown changes (a new answer, a client-side filter, the mode switch) -- never to clear the
// grid while a read is pending; after a read the new tiles take the slots in place (, `fade`).
export function renderMarket({ fade = false } = {}) {
  if (cmMode() !== 'market') return;
  const grid = $('cm-grid');
  market.rendered = 0;
  market.fade = fade;
  if (!market.loaded) {
    market.view = null;
    setMessage('');
    if (grid) { if (market.loading) renderSkeletons(grid); else grid.innerHTML = ''; }
  } else {
    market.view = cmPagedView(market.pages, market.deps.filters(), market.sort);
    setMessage(market.view.tiles.length ? '' : market.rows.length ? 'No listings match these filters.' : 'The market has no listings for these filters.');
    if (grid && !(fade && swapInPlace(grid, market.view.tiles))) grid.innerHTML = '';
  }
  renderSummary();
  renderMore();
  fillViewport();
  market.fade = false;
  syncMore();
  document.body.dataset.viewReady = 'true';
}

// ── market reads: on entering Market mode and after each settled filter change () ─────
function currentUrl() {
  return cmQuery(market.deps.player(), market.deps.filters(), market.sort);
}

// the spinner floats over the tiles (it never takes layout); the tiles of the last read stay in place, softly dimmed.
function setLoading(on) {
  market.loading = on;
  const spinner = $('cm-loading');
  if (spinner) spinner.hidden = !on;
  const grid = $('cm-grid');
  if (grid && grid.dataset) {
    if (on && market.loaded) grid.dataset.cmLoading = 'true';
    else delete grid.dataset.cmLoading;
  }
  syncMore();
}

async function loadMarket(url) {
  if (market.controller) market.controller.abort();  // a newer filter set wins; the old answer is never drawn
  if (market.moreController) { market.moreController.abort?.(); market.moreController = null; dropMoreSlots(); }
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  market.controller = controller;
  market.wantUrl = url;
  const filters = { ...market.deps.filters() };
  setLoading(true);
  setMessage('');
  document.body.dataset.marketState = 'loading';
  const started = Date.now();
  try {
    const response = await fetch(url, { cache: 'no-store', ...(controller ? { signal: controller.signal } : {}) });
    let body = null;
    try { body = await response.json(); } catch (_) { body = null; }
    if (market.controller !== controller) return;
    if (!response.ok) {
      const detail = body && typeof body.detail === 'string' ? body.detail : '';
      throw new Error(response.status === 429 ? 'Too many market reads in a short time; wait a minute, then change a filter to read again.' : detail || `The market could not be read (${response.status}).`);
    }
    market.rows = Array.isArray(body?.listings) ? body.listings : [];
    market.pages = [market.rows];  // a new read starts again at page 1
    market.page = 1;
    market.meta = { ...body, listings: undefined, elapsed_ms: Date.now() - started };
    market.readWith = filters;
    market.url = url;
    market.stale = false;
    market.loaded = true;
    document.body.dataset.marketState = 'loaded';
  } catch (error) {
    if (market.controller !== controller || error?.name === 'AbortError') return;
    market.controller = null;
    market.wantUrl = '';
    document.body.dataset.marketState = 'failed';
    setLoading(false);
    renderMarket();
    setMessage(error instanceof Error && error.message ? error.message : 'The market could not be read. Check the connection; the next filter change reads again.');
    return;
  }
  market.controller = null;
  setLoading(false);
  renderMarket({ fade: true });
  cartAfterMarketLoad(market.rows);  // re-check the cart against this read (and once per visit against the market)
}

// ── : the next 100 prints, same ranking and filters ─────────────────────
// Each page is sorted on its own and drawn after the pages before it, so a press only adds tiles below: nothing on screen moves.
export function cmPagedView(pages, filters, sort) {
  const tiles = [];
  const all = [];
  for (const rows of pages || []) {
    const view = cmView(rows, filters, sort);
    for (const tile of view.tiles) tiles.push({ ...tile, key: tiles.length });
    all.push(...(rows || []).filter((row) => cmMatches(row, filters)));
  }
  // the PP/$ colour tier, under the Cost per PP sort only, against the best offer of every loaded page
  const top = sort === 'cost_per_pp' ? cmTopRate(all) : null;
  if (top !== null) for (const tile of tiles) tile.rateTier = cmRateTier(cmPpPerUsd(tile.listing), top);
  return { tiles, summary: cmSummary(all), top };
}

function syncMore() {
  const button = $('cm-more');
  if (!button) return;
  const show = market.loaded && !market.loading && Boolean(market.meta && market.meta.has_more) && cmMode() === 'market';
  button.hidden = !show;
  button.disabled = Boolean(market.moreController);
  button.textContent = market.moreController ? 'Loading more…' : 'Load more';
}

function dropMoreSlots() {
  const grid = $('cm-grid');
  if (grid && grid.querySelectorAll) grid.querySelectorAll('.cm-more-slot').forEach((el) => el.remove());
}

export async function cmLoadMore() {
  if (!market.loaded || market.loading || market.moreController || !market.meta?.has_more) return;
  const grid = $('cm-grid');
  while (market.view && market.rendered < market.view.tiles.length) renderMore();  // the page on screen, drawn to its end
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  market.moreController = controller || {};
  syncMore();
  // placeholders in whole rows below the last tile ( rules)
  if (grid && grid.insertAdjacentHTML) {
    const one = cmSkeletonHtml(market.deps.tileHtml).replace('cm-skeleton', 'cm-skeleton cm-more-slot');
    grid.insertAdjacentHTML('beforeend', one.repeat(gridColumns(grid)));
    shimmer(grid);
  }
  const next = market.page + 1;
  try {
    // a page ends where its window ends (the server reads further blocks of prints until the window is full), so the
    // next page starts at the print the last one left unread (next_start)
    const from = Number.isFinite(Number(market.meta?.next_start)) ? `&start=${Number(market.meta.next_start)}` : '';
    const response = await fetch(`${market.url}&page=${next}${from}`, { cache: 'no-store', ...(controller ? { signal: controller.signal } : {}) });
    let body = null;
    try { body = await response.json(); } catch (_) { body = null; }
    if (market.moreController !== (controller || market.moreController)) return;
    if (!response.ok) throw new Error(response.status === 429 ? 'Too many market reads in a short time; wait a minute, then press Load more again.' : (body && body.detail) || `The market could not be read (${response.status}).`);
    const rows = Array.isArray(body?.listings) ? body.listings : [];
    market.pages.push(rows);
    market.rows = market.pages.flat();
    market.page = next;
    market.meta = { ...market.meta, ...body, listings: undefined };
    const before = market.view ? market.view.tiles.length : 0;
    const topBefore = market.view ? market.view.top : null;
    market.view = cmPagedView(market.pages, market.deps.filters(), market.sort);
    if (market.view.top !== topBefore && before) { renderMarket(); cartAfterMarketLoad(rows); return; }  // a new top pick recolours every tile
    const slots = grid && grid.querySelectorAll ? [...grid.querySelectorAll('.cm-more-slot')] : [];
    const fresh = market.view.tiles.slice(before, before + CHUNK);
    market.rendered = before;
    if (!(slots.length && swapSlots(grid, fresh, slots))) { dropMoreSlots(); market.fade = true; renderMore(); market.fade = false; }
    else market.rendered = before + fresh.length;
    renderSummary();
    fillViewport();
    cartAfterMarketLoad(rows);
  } catch (error) {
    if (error?.name === 'AbortError') return;
    dropMoreSlots();
    setMessage(error instanceof Error && error.message ? error.message : 'More listings could not be read.');
  } finally {
    if (market.moreController === (controller || market.moreController)) market.moreController = null;
    syncMore();
  }
}

// Reads when the prints in scope changed (a new query URL), otherwise re-filters what is loaded. `delay` 0 reads at once.
export function cmRefresh(delay = CM_DEBOUNCE_MS) {
  if (cmMode() !== 'market' || !market.deps) return;
  const url = currentUrl();
  clearTimeout(market.timer);
  market.timer = 0;
  if (!market.stale && url === (market.controller ? market.wantUrl : market.url)) { renderMarket(); return; }
  if (market.controller && url !== market.wantUrl) { market.controller.abort(); market.controller = null; market.wantUrl = ''; }
  setLoading(true);
  if (!market.loaded) renderMarket();  // placeholders on first entry; a re-read keeps the current tiles (dimmed)
  if (delay <= 0) { loadMarket(url); return; }
  market.timer = setTimeout(() => { market.timer = 0; loadMarket(currentUrl()); }, delay);
}

// read the market again after a confirmed purchase or combine -- the
// normal debounced read, even for an unchanged query (bought listings disappear, prices update). Outside Market mode the next
// entry reads again.
export function cmReload(delay = CM_DEBOUNCE_MS) {
  if (!market.deps) return;
  market.stale = true;
  if (cmMode() === 'market') cmRefresh(delay);
}

// ── mode switch ──────────────────────────────────────────────────────────────
export function setMode(mode) {
  const next = mode === 'market' ? 'market' : 'cards';
  document.body.dataset.cbMode = next;
  if (next === 'cards' && $('cb-maxed')?.checked) {
    $('cb-maxed').checked = false;
    market.deps.filters().maxed = false;
  }
  if (market.deps.filters().maxed) document.body.dataset.cbMaxed = '1';
  else delete document.body.dataset.cbMaxed;
  const cards = $('cb-mode-cards');
  const shop = $('cb-mode-market');
  if (cards) cards.setAttribute('aria-pressed', String(next === 'cards'));
  if (shop) shop.setAttribute('aria-pressed', String(next === 'market'));
  $('cb-mode-purchases')?.setAttribute('aria-pressed', 'false');
  try { localStorage.setItem(MODE_KEY, next); } catch (_) { /* the switch still works for this visit */ }
  if (next === 'market') cmRefresh(0);  // entering Market mode reads the market (or re-filters a read for these filters)
  else {
    clearTimeout(market.timer);
    market.timer = 0;
    market.deps.renderCards();
  }
}

function initialMode(player) {
  const fromUrl = new URLSearchParams(location.search).get('mode');
  if (fromUrl === 'market' || fromUrl === 'cards' || fromUrl === 'purchases') return fromUrl;
  if (fromUrl === 'combine') return 'cards';  // Find combinables from the board's Menu is a view of My cards (card-combinables.js)
  // no player -> the Market.
  if (!player) return 'market';
  try { return localStorage.getItem(MODE_KEY) === 'market' ? 'market' : 'cards'; } catch (_) { return 'cards'; }
}

export function initMarket(deps) {
  market.deps = deps;
  const syncMotion = () => document.body?.classList?.toggle('cb-reduce-motion', cmReduceMotion());
  syncMotion();
  try { window.matchMedia?.('(prefers-reduced-motion: reduce)').addEventListener?.('change', syncMotion); } catch (_) { /* static */ }
  initCart({ player: deps.player, rows: () => (market.view ? market.view.tiles.map((t) => t.listing) : []),
    reloadMarket: () => cmReload(), reloadCards: deps.reloadCards });
  try {
    const saved = localStorage.getItem(SORT_KEY);
    if (saved && saved in CM_SORTS) market.sort = saved;
  } catch (_) { /* default sort */ }
  const sort = $('cm-sort');
  if (sort) {
    sort.innerHTML = Object.entries(CM_SORTS).map(([value, text]) => `<option value="${value}">Sort: ${esc(text)}</option>`).join('');
    sort.value = market.sort;
    sort.addEventListener('change', () => {
      market.sort = sort.value in CM_SORTS ? sort.value : 'cost_per_pp';
      try { localStorage.setItem(SORT_KEY, market.sort); } catch (_) { /* this visit only */ }
      cmRefresh();  // the sort picks which prints are read
    });
    if (typeof deps.initSelect === 'function') deps.initSelect(sort);
  }
  $('cb-mode-cards')?.addEventListener('click', () => setMode('cards'));
  $('cb-mode-market')?.addEventListener('click', () => setMode('market'));
  // The Cards row's filters (card-browser.js reads them first: its listeners are added earlier). The name box waits for its own
  // 160 ms read in card-browser.js, then the 400 ms debounce here.
  document.querySelectorAll('.cb-controls select[data-cb-filter]').forEach((el) => el.addEventListener('change', () => cmRefresh()));
  $('cb-maxed')?.addEventListener('change', () => cmRefresh());
  let timer = 0;
  ['cb-q', 'cb-min-pp', 'cb-max-pp'].forEach((id) => $(id)?.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => cmRefresh(), 200);
  }));
  $('cb-clear')?.addEventListener('click', () => cmRefresh());
  $('cm-more')?.addEventListener('click', () => cmLoadMore());
  // the placeholders follow the grid's column count when the window changes size
  window.addEventListener?.('resize', () => { if (!market.loaded && market.loading && cmMode() === 'market') renderMarket(); });
  const sentinel = $('cm-sentinel');
  if (sentinel && typeof IntersectionObserver === 'function') {
    new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { renderMore(); fillViewport(); }
    }, { rootMargin: '1200px 0px' }).observe(sentinel);
  }
  const mode = initialMode(deps.player());
  document.body.dataset.cbMode = mode;
  if (mode === 'market') setMode('market');
  else if (mode === 'purchases') {
    $('cb-mode-cards')?.setAttribute('aria-pressed', 'false');
    $('cb-mode-market')?.setAttribute('aria-pressed', 'false');
    $('cb-mode-purchases')?.setAttribute('aria-pressed', 'true');
  }
  else {
    $('cb-mode-cards')?.setAttribute('aria-pressed', 'true');
    $('cb-mode-market')?.setAttribute('aria-pressed', 'false');
  }
}
