// card-browser.js -- : the read-only card browser at /cards.
// One fetch per page load (GET /api/cards), then filtering and sorting (one tile per copy, ) run in the
// browser (card-browser-model.js). Tiles reuse the worker picker's grid card classes and the
// board's filter-row controls (customSelect.js, .board-filter-field, .board-filter-pill).
// Only the tiles near the viewport are built: the grid grows in chunks as the page scrolls.
import { mountThemePicker } from './theme.js';
import { initCustomSelect, selectValues, setSelectValues, syncCustomSelect } from './customSelect.js';
import { bindCountdowns, countdownText } from './countdown.js';
import {
  CB_ABILITY_LABELS, CB_ANY_ABILITY, CB_FOIL_LABELS, CB_RARITIES, CB_SET_LABELS, CB_SET_ORDER, CB_SORTS, CB_SPLINTERS,
  cbDefaultFilters, cbList, cbFormatPpPerCc, cbPpPerCcOf, cbView,
} from './card-browser-model.js';
// the board's tooltip engine (tooltip.js imports only escape.js and state.js, never the board graph).
import { initTooltips, tipAttrs } from './tooltip.js';
import {
  cbAbilityIconPath, cbAbilityTip, cbCcTip, cbCoolingTip, cbFoilTip, cbMaxedTip, cbPlotTip, cbPpPerCcTip,
  cbPpTip, cbRarityTip, cbSetTip, cbSplinterTip, cbStakedTip, cbUnavailableTip,
} from './card-browser-tips.js';
// the Market mode (read only, loaded only on the maintainer's press) draws its listings with this page's tile renderer.
import { initMarket, setMode } from './card-market.js';
// Find combinables, the third mode (own combine sets, the upgrade price check on the maintainer's press).
import { initCombinables, renderCombinables } from './card-combinables.js';
import { initPurchases } from './card-purchases.js';
import { initCardsWallet } from './cards-wallet.js';
import { cartOpenOwnedMenu, cartRefreshOwned } from './card-cart.js';

const ASSET_BASE = '/static/assets';
const CHUNK = 48;
const FILTER_STORE_KEY = 'ceres:cards:filters';
const PLAYER_MEMORY_KEY = 'ceres:lastPlayer';

// Card art hosts: the same tables icons.js uses for the board's card art (CARD_CDN_*).
const CARD_CDN_BASE = 'https://d36mxiodymuqjm.cloudfront.net';
const CARD_CDN_SET = {
  0: 'cards_v2.2', 1: 'cards_beta', 2: 'cards_v2.2', 3: 'cards_beta', 4: 'cards_untamed', 5: 'cards_untamed',
  6: 'cards_gladiator', 7: 'cards_chaos', 8: 'cards_riftwatchers', 9: 'cards_nightmare', 10: 'cards_soulbound',
  11: 'cards_nightmare', 12: 'cards_rebellion', 13: 'cards_soulboundrb', 14: 'cards_v2.2', 15: 'cards_v2.2',
  16: 'cards_v2.2', 17: 'cards_v2.2', 18: 'cards_v2.2', 19: 'cards_land', 20: 'cards_v2.2', 21: 'cards_land',
};
const CARD_CDN_SOURCE_SET = {
  alpha: 'cards_v2.2', beta: 'cards_beta', chaos: 'cards_chaos', untamed: 'cards_untamed',
  rebellion: 'cards_rebellion', soulbound: 'cards_soulbound', conclave: 'cards_v2.2',
};
const SET_ICON = {
  alpha: 'alpha', beta: 'beta', untamed: 'untamed', chaos: 'chaos-legion', rebellion: 'rebellion',
  conclave: 'conclave-arcana', foundations: 'foundations', land: 'eternal-land', gladius: 'eternal-land',
};

const state = { player: null, cards: [], view: null, rendered: 0, filters: cbDefaultFilters() };
const landStakePp = (cards) => cards.map((card) => card.soulbound
  ? { ...card, land_base_pp: 0, max_level_pp: 0 } : card);

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function num(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return Math.round(Number(value)).toLocaleString('en-US');
}

function cap(value) {
  const text = String(value || '');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function territorySlug(territory) {
  return territory ? String(territory).toLowerCase().replace(/[^a-z]+/g, '-').replace(/^-|-$/g, '') : null;
}

// ── icons (same assets and classes as the board's icons.js) ─────────────────
function splinterIcon(splinter) {
  const key = String(splinter || '').toLowerCase();
  return CB_SPLINTERS.includes(key) ? `<img class="splinter-icon" src="${ASSET_BASE}/splinter/${key}.svg" alt="${esc(cap(key))}"${cbSplinterTip(key)}>` : '';
}
function rarityGem(rarity) {
  const key = String(rarity || '').toLowerCase();
  return CB_RARITIES.includes(key) ? `<img class="rarity-gem" src="${ASSET_BASE}/rarity/gem-${key}.svg" alt="${esc(cap(key))}"${cbRarityTip(key)}>` : '';
}
function setIcon(cardSet, edition = null) {
  const file = SET_ICON[cardSet];
  const label = CB_SET_LABELS[cardSet] || cap(cardSet);
  return `<img class="set-icon${file ? '' : ' set-icon-generic'}" src="${ASSET_BASE}/sets/${file || 'edition-generic'}.svg" alt="${esc(label)}"${cbSetTip(label, file, edition)}>`;
}
// ── card art (same CDN order as editor.js's cardArtCandidates) ───────────────
function cardImagePart(value) {
  return encodeURIComponent(String(value)).replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
}
function cardArtCandidates(card) {
  const sets = [...new Set([CARD_CDN_SET[card.edition], CARD_CDN_SOURCE_SET[String(card.card_set || '').toLowerCase()]].filter(Boolean))];
  if (!card.name || !sets.length) return [];
  // gold and gold arcane draw the "_gold" art, black and black arcane the "_blk" art (the official site's own file names).
  const suffix = [1, 2].includes(card.foil) || (card.foil == null && card.gold) ? '_gold' : [3, 4].includes(card.foil) ? '_blk' : '';
  const file = `${cardImagePart(card.name)}${suffix}`;
  const skin = card.skin ? `/${cardImagePart(card.skin)}` : '';
  const out = [];
  sets.forEach((set) => ['jpg', 'png'].forEach((ext) => out.push(`${CARD_CDN_BASE}/${set}${skin}/${file}.${ext}`)));
  if (skin) sets.forEach((set) => ['jpg', 'png'].forEach((ext) => out.push(`${CARD_CDN_BASE}/${set}/${file}.${ext}`)));
  return [...new Set(out)];
}
function onArtError(event) {
  const img = event.target;
  if (!(img instanceof HTMLImageElement) || img.dataset.cbArt === undefined) return;
  let list = [];
  try { list = JSON.parse(img.dataset.cbArt || '[]'); } catch (_) { list = []; }
  const next = Number(img.dataset.cbArtIndex || 0) + 1;
  if (next < list.length) {
    img.dataset.cbArtIndex = String(next);
    img.src = list[next];
    return;
  }
  const box = img.parentElement;
  img.remove();
  if (box) {
    box.classList.add('cb-art-missing');
    box.insertAdjacentHTML('afterbegin', '<span class="cb-art-missing-text">No image</span>');  // the name is on the tile's caption
  }
}
// (maintainer: "there is still a strong flcker when scrolling beween tiles. this seems to stronger or only on the market
// page"): the art is read when its tile is built (loading="eager" in tileHtml), not when the tile comes near the screen
// with lazy loading inside the content-visibility:auto tiles the read only began as a tile scrolled in, so the art box stood
// empty, then faded in (ceres.css 420 ms) on screen; worst on the Market, whose art is never cached. Art that arrives off
// screen is decoded at once and shown without the fade (.is-art-instant): nobody saw it arrive.
function onArtLoad(event) {
  const img = event.target;
  if (!(img instanceof HTMLImageElement) || img.dataset.cbArt === undefined) return;
  const box = (img.closest('.cb-tile') || img).getBoundingClientRect();  // the tile's own box: no layout inside a skipped tile
  if (box.bottom <= 0 || box.top >= window.innerHeight || !box.height) {
    img.classList.add('is-art-instant');
    img.decode?.().catch(() => {});
  }
  img.classList.add('is-art-in');
}

// ── tile ─────────────────────────────────────────────────────────────────────
// (maintainer: "make sure all badges are applied on the tile not above or below the PP boxes. cooling should use our new
// full width badge in the middle of the card"; "the name and land abilities can go on the tile as well. Name like always and
// land abilities like Echo Reapers on top of the name"): everything but the level/CC line and the PP boxes is drawn ON the card
// art -- foil and Maxed top left, the board's centred waiting badge (.slot-wait-badge) for a cooldown
// and at the bottom, as on the plot tiles ( .slot-foot above .slot-caption): the staked / unavailable badges, the land
// ability icons and the name.
function statusBadges(tile) {
  const parts = [];
  if (tile.plot) {
    // (staked on\" followed by the region badge with
    // link to the plot as in our picker"): the picker's on-land line -- plain words, then the region badge, which opens the plot.
    const player = new URLSearchParams(location.search).get('player') || '';
    const p = tile.plot;
    const slug = territorySlug(p.territory);
    // (maintainer: "the plot badge must be the same full width tile badge we use for the waiting badge with timer. place over
    // the name"): the board's full-width .slot-wait-badge, directly above the name.
    parts.push(`<span class="slot-wait-badge cb-onland"><span class="slot-wait-label">Staked on</span><a class="ch-plot-jump ch-plot-badge ceres-region-plaque${slug ? ` territory-${slug}` : ''}" href="/?${player ? `player=${encodeURIComponent(player)}&` : ''}plot=${encodeURIComponent(p.label)}"${cbPlotTip(p.label, p.territory)}>${esc(p.label)}</a></span>`);
  } else if (tile.status === 'staked') {
    parts.push(`<span class="slot-wait-badge cb-onland"${cbStakedTip()}><span class="slot-wait-label">Staked</span></span>`);
  }
  if (tile.unavailable) parts.push(`<span class="candidate-status-badge is-danger cb-badge"${cbUnavailableTip(tile.unavailable)}><span>${esc(tile.unavailable)}</span></span>`);
  return parts.length ? `<span class="cb-badges">${parts.join('')}</span>` : '';
}

// The board's centred waiting badge (editor.js stageWaitBadgeHtml) with this copy's cooldown end as a live timer (countdown.js).
function coolingBadge(tile) {
  if (tile.status !== 'cooling') return '';
  const until = tile.readyAt && Number.isFinite(Date.parse(tile.readyAt)) ? tile.readyAt : null;
  const time = until
    ? `<span class="slot-wait-time" data-countdown-until="${esc(until)}" data-countdown-compact data-countdown-ended="ready">${esc(Date.parse(until) <= Date.now() ? 'ready' : countdownText(until, Date.now(), { compact: true }))}</span>`
    : '';
  // the hourglass in its own span, so it takes the badge's amber like the text
  return `<span class="slot-wait-badge cb-wait"${cbCoolingTip(until)}><span class="cb-wait-icon" aria-hidden="true">⏳</span><span class="slot-wait-label">Cooling</span>${time}</span>`;
}

// The plot tiles' ability row (editor.js renderAbilityRow, centred queue) with this page's own icon table.
function abilityRow(card) {
  const abilities = Array.isArray(card.land_abilities) ? card.land_abilities : [];
  if (!abilities.length) return '';
  const code = (entry) => (Array.isArray(entry) ? entry[0] : entry);
  const items = abilities.map((entry) => {
    const key = code(entry);
    const icon = cbAbilityIconPath(key);
    const src = icon ? `${ASSET_BASE}/${icon}` : null;
    const name = CB_ABILITY_LABELS[key] || String(key);
    // the plot tiles' ability tooltip (name and effect) on each icon, from card-browser-tips.js.
    return `<span class="slot-ability-item">${src ? `<img class="ability-icon" src="${src}" alt=""${cbAbilityTip(entry)}>` : `<span class="ability-icon ability-icon-text"${cbAbilityTip(entry)}>${esc(name.slice(0, 2))}</span>`}</span>`;
  }).join('');
  return `<span class="slot-ability-row is-centred cb-abilities" aria-label="Land abilities"><span class="slot-ability-row">${items}</span></span>`;
}

// current CC / the print's max CC; "—" when the max is
// unknown, "max" for a black foil (bcx_max 0 = always max).
function ccLine(card) {
  const cc = card.bcx > 0 ? num(card.bcx) : '—';
  const max = card.bcx_max === 0 ? 'max' : card.bcx_max > 0 ? num(card.bcx_max) : '—';
  return `<span class="cb-cc"${cbCcTip(card)}>CC <b>${cc}</b> / ${max}</span>`;
}

// no Overcombined tag on the art; PP per CC
// still counts CC only up to max level ().
// .cb-scrolling stays on until 250 ms after the last scroll event.
// (maintainer: "make scrolling between tiles much smoother. there is still a lot of fade-in jumping, where the entire board
// lights up for a split second"): scrolling freezes the hover state instead of resetting it. At the first scroll event each
// shown grid records what it shows -- data-cb-hold="dim" (a tile is hovered and the others are dimmed; that tile is .cb-held)
// or "clear" -- and the grid takes no pointer events (.cb-scrolling), so no tile changes while the content moves under the
// pointer. 250 ms after the last scroll event the pointer events come back, but the hold stays until the browser has re-read
// the hover -- its next mouseover (Chromium's own after-scroll hover update) or pointer move, or the pointer leaving the page.
// Releasing on a timer instead dropped every tile to "nothing hovered" for a moment whenever that update came late: a flash
// at scroll end (measured, 2026-10-15). Then the dimming follows the pointer again (220 ms grace).
let cbScrollTimer = 0;
let cbScrollGen = 0;  // a scroll that starts before the release keeps the hold
function cbHoldGrids() {
  document.querySelectorAll('.cb-grid').forEach((grid) => {
    if (grid.dataset.cbHold || !grid.offsetParent) return;
    const held = grid.querySelector(':scope > .cb-tile:hover');
    const other = held ? [...grid.children].find((el) => el !== held && el.classList.contains('cb-tile')) : null;
    const dimmed = !!(other && Number(getComputedStyle(other).opacity) < 0.99);
    if (dimmed) held.classList.add('cb-held');
    grid.dataset.cbHold = dimmed ? 'dim' : 'clear';
  });
}
function cbReleaseGrids() {
  document.querySelectorAll('.cb-grid[data-cb-hold]').forEach((grid) => {
    delete grid.dataset.cbHold;
    grid.querySelectorAll(':scope > .cb-held').forEach((el) => el.classList.remove('cb-held'));
  });
}
function cbReleaseAfterHover(gen) {
  const events = ['mouseover', 'pointermove', 'mouseout'];
  const release = (event) => {
    if (event?.type === 'mouseout' && event.relatedTarget) return;  // only the pointer leaving the page
    events.forEach((type) => document.removeEventListener(type, release, true));
    if (gen === cbScrollGen) cbReleaseGrids();
  };
  events.forEach((type) => document.addEventListener(type, release, true));
}
window.addEventListener('scroll', () => {
  cbScrollGen += 1;
  if (!document.body?.classList.contains('cb-scrolling')) { cbHoldGrids(); document.body?.classList.add('cb-scrolling'); }
  clearTimeout(cbScrollTimer);
  cbScrollTimer = setTimeout(() => {
    document.body?.classList.remove('cb-scrolling');
    cbReleaseAfterHover(cbScrollGen);
  }, 250);
}, { passive: true });

function levelTagHtml(card) {
  const of = card.max_level ? ` of ${card.max_level}` : '';
  const cc = card.bcx > 0 ? ` · CC ${num(card.bcx)}${card.bcx_max > 0 ? ` / ${num(card.bcx_max)}` : ''}` : '';
  return `<span class="candidate-tag is-info cb-maxed cb-level-tag"${tipAttrs({ title: `Level ${card.level}${of}${cc}` })}>Lv ${esc(card.level)}</span>`;
}

function tileHtml(tile) {
  const card = tile.card;
  const extra = tile.extra || null;  // (Market mode): a price badge on the art, its own PP boxes and a market link under them
  const art = cardArtCandidates(card);
  const foil = [1, 2, 3, 4].includes(card.foil) ? `<span class="foil-badge foil-${card.foil}"${cbFoilTip(card.foil)}>${esc(CB_FOIL_LABELS[card.foil])}</span>` : '';
  const maxed = card.maxed && !card.no_pp ? `<span class="candidate-tag is-info cb-maxed"${cbMaxedTip(card)}>Maxed</span>` : '';
  // a Market tile that is not
  // maxed carries its level where Maxed would be, drawn like it; My cards tiles are unchanged.
  const levelTag = extra && extra.levelTag !== false && !maxed && !card.maxed && card.level != null ? levelTagHtml(card) : '';
  const top = foil || maxed || levelTag ? `<span class="cb-art-tags">${foil}${maxed}${levelTag}</span>` : '';
  const artExtra = extra?.art || '';
  const image = art.length
    ? `<img src="${esc(art[0])}" alt="" loading="eager" decoding="async" data-cb-art="${esc(JSON.stringify(art))}" data-cb-art-index="0">`
    : '';
  const level = card.max_level ? `Lv ${esc(card.level ?? '—')}/${esc(card.max_level)}` : `Lv ${esc(card.level ?? '—')}`;
  const icons = `<span class="candidate-icon-row">${(card.splinters || []).map((s) => `<span class="candidate-icon-box">${splinterIcon(s)}</span>`).join('')}<span class="candidate-icon-box">${rarityGem(card.rarity)}</span><span class="candidate-icon-box">${setIcon(card.card_set, card.edition)}</span></span>`;
  // (max level\" in the PP box must be replaced with \"PP\""): a maxed copy's box reads "PP"; the tooltip
  // and the accessible label still say max level.
  let maxLine;
  if (card.no_pp) maxLine = 'no land PP';
  else if (card.maxed) maxLine = 'max level';
  else maxLine = `max ${num(card.max_level_pp)}`;
  const perCc = card.land_base_pp ? cbPpPerCcOf(card) : null;  // surplus CC past max adds no PP
  const ppPerCc = perCc !== null ? `<span class="candidate-grid-metric cb-metric-cc"${cbPpPerCcTip()}><strong>${cbFormatPpPerCc(perCc)}</strong><small>PP per CC</small></span>` : '';
  const badges = statusBadges(tile) + (extra?.foot || '');  // the market's full-width price badge, over the name like the staked badge
  const abilities = abilityRow(card);
  // the
  // ability icons, then the full-width staked badge (and the unavailable badge) directly above the name.
  // (, "My cards only"): on My cards
  // tiles the icons sit in the strip below the name, inside the caption; Market tiles keep them above the name (their price
  // badges own the bottom strip). The staked badge stays full width over the name.
  const above = extra ? abilities : '';
  const below = extra ? '' : abilities.replace('slot-ability-row is-centred cb-abilities', 'slot-ability-row is-centred cb-abilities cb-abilities-below');
  const foot = badges || above ? `<span class="slot-foot cb-foot">${above}${badges}</span>` : '';
  const caption = `<span class="${String(card.name || '').length > 18 ? 'slot-caption slot-caption--long' : 'slot-caption'} cb-caption"><strong class="slot-name">${esc(card.name)}</strong>${below}</span>`;
  // one top bar -- the tags wrap side by side on the
  // left, the market price (Market mode) on the right, all top-aligned.
  const topBar = top || artExtra ? `<span class="cb-art-top">${top}${artExtra}</span>` : '';
  const accessible = extra?.label || `${card.name}. ${level}. CC ${card.bcx ?? '—'}.${tile.plot ? ` Staked on ${tile.plot.label}.` : ''} Base PP ${num(card.land_base_pp)}, ${maxLine}.`;
  const metrics = extra?.metrics ?? `<span class="candidate-grid-metric cb-metric-pp"${cbPpTip(card, maxLine)}><strong>${num(card.land_base_pp)}</strong><small>${esc(card.maxed && !card.no_pp ? 'PP' : maxLine)}</small></span>${ppPerCc}`;
  // extra.attrs makes a Market tile open the card menu; : extra.bottom is the price badge at the art's bottom edge.
  return `<article class="candidate-card candidate-grid-card cb-tile${extra?.className ? ` ${extra.className}` : ''}" aria-label="${esc(accessible)}"${extra?.attrs || (!extra ? ' tabindex="0" aria-haspopup="dialog"' : '')}><span class="candidate-grid-face"><span class="candidate-art cb-art" data-name="${esc(card.name)}">${image}${topBar}${coolingBadge(tile)}${foot}${caption}${extra?.bottom || ''}</span><span class="candidate-grid-icon-level cb-level">${icons}<span class="cb-level-cc"><span class="cb-lv">${level}</span><span class="cb-sep" aria-hidden="true">·</span>${ccLine(card)}</span></span><span class="candidate-grid-metrics">${metrics}</span>${extra?.after || ''}</span></article>`;
}

// ── rendering ────────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

function renderSummary() {
  const box = $('cb-summary');
  if (!box || !state.view) return;
  const s = state.view.summary;
  const unknown = s.maxUnknown ? ` <span class="cb-summary-note">(${num(s.maxUnknown)} without a max value)</span>` : '';
  box.innerHTML = `<span class="cb-summary-item"><strong>${num(s.copies)}</strong> ${s.copies === 1 ? 'copy' : 'copies'}</span><span class="cb-summary-item">Base PP <strong>${num(s.pp)}</strong></span><span class="cb-summary-item">At max level <strong>${num(s.maxPp)}</strong>${unknown}</span>`;
}

function renderMore() {
  const grid = $('cb-grid');
  if (!grid || !state.view) return;
  const tiles = state.view.tiles;
  if (state.rendered >= tiles.length) return;
  const end = Math.min(tiles.length, state.rendered + CHUNK);
  grid.insertAdjacentHTML('beforeend', tiles.slice(state.rendered, end).map(tileHtml).join(''));
  state.rendered = end;
  bindCountdowns();  // the cooling badges' live timers (no-op when none is on the page)
}

function fillViewport() {
  const sentinel = $('cb-sentinel');
  let guard = 0;
  while (sentinel && state.view && state.rendered < state.view.tiles.length && sentinel.getBoundingClientRect().top < window.innerHeight * 2 && guard < 40) {
    renderMore();
    guard += 1;
  }
}

function render() {
  if (document.body?.dataset?.cbMode === 'purchases') return;
  if (document.body?.dataset?.cbMode === 'market') { syncClear(); return; }  // the market draws its own grid; My cards renders again on the switch back
  if (document.body?.dataset?.cbMode === 'combine') { renderCombinables(); syncClear(); return; }  // the same filters, its own list
  state.view = cbView(state.cards, { ...state.filters, maxed: false });
  state.rendered = 0;
  const grid = $('cb-grid');
  if (grid) grid.innerHTML = '';
  const message = $('cb-message');
  if (message) {
    message.hidden = state.view.tiles.length > 0;
    message.textContent = state.cards.length ? 'No cards match these filters.' : 'This player has no cards in the stored board.';
  }
  renderSummary();
  renderMore();
  fillViewport();
  syncClear();
  document.body.dataset.viewReady = 'true';  // the rig's page: view waits for this
}

function redrawFreshCards(cards) {
  if (JSON.stringify(cards) === JSON.stringify(state.cards)) return;
  const oldTiles = state.view?.tiles || [];
  const nextView = cbView(cards, { ...state.filters, maxed: false });
  const grid = $('cb-grid');
  state.cards = cards;
  if (document.body?.dataset?.cbMode !== 'cards' || !grid || oldTiles.length !== nextView.tiles.length) {
    render();
    return;
  }
  // Most captures change only a few copies. Keep every unaffected tile (including
  // its decoded art and hover state) mounted and replace only changed rows.
  const shown = Math.min(state.rendered, nextView.tiles.length);
  for (let index = 0; index < shown; index += 1) {
    if (JSON.stringify(oldTiles[index]) !== JSON.stringify(nextView.tiles[index])) {
      const tile = grid.children[index];
      if (tile) tile.outerHTML = tileHtml(nextView.tiles[index]);
    }
  }
  state.view = nextView;
  renderSummary();
  fillViewport();
}

// ── filters ──────────────────────────────────────────────────────────────────
// Status picks one value
// like the sort; the other filter lists stay multi-selects ().
const CB_SINGLE_FILTERS = new Set(['sort', 'status']);

function readFilters() {
  const f = state.filters;
  f.q = $('cb-q').value;
  f.min_pp = $('cb-min-pp').value;
  f.max_pp = $('cb-max-pp').value;
  f.maxed = !!$('cb-maxed')?.checked;  // (Maxed\" and show only maxed cards")
  syncMaxed();
  // the six filter dropdowns are multi-selects (a list each); the sort stays one value
  document.querySelectorAll('select[data-cb-filter]').forEach((el) => { f[el.dataset.cbFilter] = el.dataset.cbFilter === 'sort' ? el.value : selectValues(el); });
  try { localStorage.setItem(FILTER_STORE_KEY, JSON.stringify(f)); } catch (_) { /* filters still apply for this visit */ }
}

function writeFilters() {
  const f = state.filters;
  if (f.ability === true) f.ability = [CB_ANY_ABILITY];
  if (f.ability === false) f.ability = [];
  $('cb-q').value = f.q || '';
  $('cb-min-pp').value = f.min_pp || '';
  $('cb-max-pp').value = f.max_pp || '';
  if ($('cb-maxed')) $('cb-maxed').checked = !!f.maxed;
  syncMaxed();
  document.querySelectorAll('select[data-cb-filter]').forEach((el) => {
    const key = el.dataset.cbFilter;
    if (key === 'sort') {
      const value = f[key] ?? '';
      el.value = [...el.options].some((o) => o.value === String(value)) ? String(value) : el.options[0]?.value || '';
      f[key] = el.value;
    } else {
      setSelectValues(el, cbList(f[key]));  // an old stored single value becomes a one-element list
      f[key] = selectValues(el);
    }
    syncCustomSelect(el);
  });
  syncPills();
}

function filtersActive() {
  const f = state.filters;
  return !!(f.q || cbList(f.set).length || cbList(f.rarity).length || cbList(f.foil).length || cbList(f.splinter).length
    || cbList(f.status).length || cbList(f.level).length || cbList(f.ability).length || f.min_pp || f.max_pp
    || (document.body?.dataset.cbMode === 'market' && f.maxed));
}

// card-browser.css hides the Lv · CC line while
// body[data-cb-maxed] is set (both modes).
function syncMaxed() {
  const data = document.body?.dataset;
  if (!data) return;
  if (state.filters.maxed) data.cbMaxed = '1';
  else delete data.cbMaxed;
}

// The filter row's shared "selected filter" look (buttons.css: [data-filter-active="true"])
// the toggle pills get it from their checked input.
function syncPills() {
  document.querySelectorAll('.cb-controls select[data-cb-filter]').forEach((el) => {
    const trigger = el._csel?.trigger;
    if (!trigger) return;
    if (el.dataset.cbFilter !== 'sort' && el.value) trigger.dataset.filterActive = 'true';
    else delete trigger.dataset.filterActive;
  });
}

function syncClear() {
  const clear = $('cb-clear');
  if (clear) clear.hidden = !filtersActive();
}

function fillOptions(select, values, label) {
  const chosen = select.multiple ? selectValues(select) : null;  // rebuilding the options keeps the choice
  const keep = select.options[0];
  select.innerHTML = '';
  if (keep && keep.value === '') select.appendChild(keep);
  for (const value of values) {
    const option = document.createElement('option');
    option.value = String(value);
    option.textContent = label(value);
    select.appendChild(option);
  }
  if (chosen && chosen.length) setSelectValues(select, chosen);
}

function populateOptions() {
  const present = (key) => new Set(state.cards.map((c) => c[key]));
  const sets = present('card_set');
  fillOptions($('cb-set'), [...CB_SET_ORDER.filter((s) => sets.has(s)), ...[...sets].filter((s) => s && !CB_SET_ORDER.includes(s)).sort()], (s) => CB_SET_LABELS[s] || cap(s));
  const rarities = present('rarity');
  fillOptions($('cb-rarity'), CB_RARITIES.filter((r) => rarities.has(r)), cap);
  const foils = present('foil');
  fillOptions($('cb-foil'), [0, 1, 2, 3, 4].filter((f) => foils.has(f)), (f) => CB_FOIL_LABELS[f]);
  const splinters = new Set(state.cards.flatMap((c) => (c.splinters || []).map((s) => String(s).toLowerCase())));
  fillOptions($('cb-splinter'), CB_SPLINTERS.filter((s) => splinters.has(s)), cap);
  const chosenAbilities = cbList(state.filters.ability === true ? CB_ANY_ABILITY : state.filters.ability);
  const abilities = new Set(state.cards.flatMap((c) => (c.land_abilities || []).map((entry) => Array.isArray(entry) ? String(entry[0]).toUpperCase() : '')));
  fillOptions($('cb-ability'), [CB_ANY_ABILITY, ...Object.keys(CB_ABILITY_LABELS).filter((code) => abilities.has(code)),
    ...[...abilities].filter((code) => code && !(code in CB_ABILITY_LABELS)).sort()], (code) => code === CB_ANY_ABILITY ? 'Has an ability' : CB_ABILITY_LABELS[code] || code);
  setSelectValues($('cb-ability'), chosenAbilities);
  fillSortOptions();
}

function fillSortOptions() {
  const sort = $('cb-sort');
  sort.innerHTML = Object.entries(CB_SORTS).map(([value, text]) => `<option value="${value}">Sort: ${esc(text)}</option>`).join('');
}

// the filter row shows its
// final labels before the cards are read -- the sort list and the remembered filter choices (their labels need no data) are
// in the dropdowns when they are first drawn, and Clear all is shown at once -- so the row's width and line count do not
// change when the cards arrive. populateOptions() then narrows the lists to what the player owns, as before.
function prefillOptions() {
  const f = state.filters;
  fillOptions($('cb-set'), cbList(f.set), (s) => CB_SET_LABELS[s] || cap(s));
  fillOptions($('cb-rarity'), cbList(f.rarity), cap);
  fillOptions($('cb-foil'), cbList(f.foil).filter((v) => v in CB_FOIL_LABELS), (v) => CB_FOIL_LABELS[v]);
  fillOptions($('cb-splinter'), cbList(f.splinter), cap);
  fillOptions($('cb-ability'), [CB_ANY_ABILITY, ...cbList(f.ability === true ? CB_ANY_ABILITY : f.ability).filter((code) => code !== CB_ANY_ABILITY)],
    (code) => code === CB_ANY_ABILITY ? 'Has an ability' : CB_ABILITY_LABELS[code] || code);
  fillSortOptions();
  // the six filter lists are multi-selects (initFilters makes them so); set it first, or a remembered list would keep one value
  document.querySelectorAll('select[data-cb-filter]').forEach((el) => { if (!CB_SINGLE_FILTERS.has(el.dataset.cbFilter)) el.multiple = true; });
  writeFilters();
}

function initFilters() {
  const decorate = {
    set: (v) => (v ? { icon: setIcon(v) } : null),
    rarity: (v) => (v ? { icon: rarityGem(v) } : null),
    splinter: (v) => (v ? { icon: splinterIcon(v) } : null),
  };
  document.querySelectorAll('select[data-cb-filter]').forEach((el) => {
    const decorateOption = decorate[el.dataset.cbFilter];
    const filterNoun = cap(el.dataset.cbFilter);
    const pluralNoun = { set: 'sets', rarity: 'rarities', foil: 'foils', splinter: 'splinters', status: 'statuses', level: 'levels', ability: 'abilities' }[el.dataset.cbFilter];
    const filterLabels = el.dataset.cbFilter === 'sort' ? {} : { emptyLabel: filterNoun, ...(pluralNoun ? { pluralNoun } : {}) };
    initCustomSelect(el, { ariaLabel: `${filterNoun} filter`, ...filterLabels, multiple: !CB_SINGLE_FILTERS.has(el.dataset.cbFilter), ...(decorateOption ? { decorateOption } : {}) });
    el.addEventListener('change', () => { readFilters(); syncPills(); render(); });
  });
  let timer = 0;
  ['cb-q', 'cb-min-pp', 'cb-max-pp'].forEach((id) => $(id).addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { readFilters(); render(); }, 160);
  }));
  $('cb-maxed')?.addEventListener('change', () => { readFilters(); syncPills(); render(); });
  $('cb-clear').addEventListener('click', () => {
    const sort = state.filters.sort;
    state.filters = { ...cbDefaultFilters(), sort };
    writeFilters();
    readFilters();
    render();
  });
}

// ── header, theme, player ────────────────────────────────────────────────────
function boardUrl(player) {
  return player ? `/?player=${encodeURIComponent(player)}` : '/';
}

function placeThemeDock() {
  const dock = $('header');
  const header = document.querySelector('header.board-header');
  const picker = document.querySelector('.theme-picker');
  if (!dock || !header || !picker) return;
  const h = header.getBoundingClientRect();
  const p = picker.getBoundingClientRect();
  if (!p.height) return;
  dock.style.top = `${Math.max(0, h.top + h.height / 2 - p.height / 2 - 10)}px`;
  // the header yields the Iris picker's measured width as on the board (app.js ), so the switcher -- the last
  // header control -- ends on the same pixel on both pages instead of at the 4.25rem fallback.
  const toggle = picker.querySelector('.theme-toggle');
  const t = toggle?.getBoundingClientRect();
  if (!t?.width) return;
  const cs = getComputedStyle(picker);
  const px = (value) => Number.parseFloat(value) || 0;
  const closed = t.width + px(cs.paddingLeft) + px(cs.paddingRight) + px(cs.borderLeftWidth) + px(cs.borderRightWidth);
  const edge = Math.max(0, document.documentElement.getBoundingClientRect().right - p.right);
  header.style.setProperty('--hdr-pad-right', `${Math.max(Math.ceil(closed + edge + 12), 16)}px`);
}

// The filter row sticks under the header at --ceres-header-height, the same variable the
// board's table.js measures for its own sticky band.
function syncHeaderHeight() {
  const header = document.querySelector('header.board-header');
  const h = header ? Math.round(header.getBoundingClientRect().height) : 0;
  if (h > 0) document.documentElement.style.setProperty('--ceres-header-height', `${h}px`);
  placeThemeDock();
}

function initHeader() {
  mountThemePicker({ storageKey: 'ceres:theme', defaultTheme: 'dark' });
  requestAnimationFrame(syncHeaderHeight);
  setTimeout(syncHeaderHeight, 300); // once more after the picker's fonts settle (as the board, app.js)
  const header = document.querySelector('header.board-header');
  if (header && typeof ResizeObserver === 'function') new ResizeObserver(syncHeaderHeight).observe(header);
  window.addEventListener('resize', syncHeaderHeight);
  const input = $('player-input');
  input.value = state.player || '';
  const go = () => {
    const name = String(input.value || '').trim().toLowerCase();
    if (name) location.href = `/cards?player=${encodeURIComponent(name)}`;
  };
  $('cb-player-go').addEventListener('click', go);
  input.addEventListener('keydown', (event) => { if (event.key === 'Enter') go(); });
  // the switcher's Plots half (a link) goes to this player's plots.
  $('page-switch-plots')?.setAttribute('href', boardUrl(state.player));
  $('cb-logo-link').setAttribute('href', boardUrl(state.player));
  syncCardsAvatar(state.player);
}

// the same avatar medallion as the board header (board.js syncPlayerAvatar): the avatar-builder layers, retried
// twice, hidden without a player. A press reads the cards again; the filters stay.
function syncCardsAvatar(account) {
  const box = $('player-avatar');
  if (!box) return;
  if (!account) { box.hidden = true; box.replaceChildren(); return; }
  const attempt = async (round) => {
    try {
      // a lazy import keeps card-browser.js loadable by the test VM, which reads its source without module imports
      const { playerAvatarLayers } = await import('./avatar.js');
      const urls = await playerAvatarLayers(account);
      if (!urls?.length) throw new Error('No avatar layers');
      const images = urls.map((url) => { const img = new Image(); img.alt = ''; img.decoding = 'async'; img.referrerPolicy = 'no-referrer'; img.src = url; return img; });
      const decoded = await Promise.allSettled(images.map((img) => img.decode()));
      const loaded = images.filter((_, index) => decoded[index].status === 'fulfilled');
      if (!loaded.length) throw new Error('Avatar images unavailable');
      box.replaceChildren(...loaded);
      box.hidden = false;
    } catch (_) {
      if (round < 2) setTimeout(() => attempt(round + 1), [250, 750][round]);
      else if (!box.querySelector('img')) box.hidden = true;
    }
  };
  attempt(0);
  box.addEventListener('click', async () => {
    if (box.dataset.refreshState === 'loading') return;
    box.dataset.refreshState = 'loading';
    box.setAttribute('aria-busy', 'true');
    try { await load(); box.dataset.refreshState = 'success'; } catch (_) { delete box.dataset.refreshState; }
    box.removeAttribute('aria-busy');
    setTimeout(() => { if (box.dataset.refreshState === 'success') delete box.dataset.refreshState; }, 900);
  });
}

function showMessage(text) {
  $('cb-loading').hidden = true;
  const message = $('cb-message');
  message.hidden = false;
  message.textContent = text;
  document.body.dataset.viewReady = 'true';
}

async function load() {
  if (!state.player) {
    showMessage('Type a player name above to see their cards.');
    return;
  }
  try {
    const response = await fetch(`/api/cards?player=${encodeURIComponent(state.player)}`);
    if (!response.ok) {
      let detail = '';
      try { detail = (await response.json()).detail || ''; } catch (_) { detail = ''; }
      showMessage(response.status === 503 ? 'No stored board for this player yet. Open the board for this player first, then come back.' : `The cards could not be read${detail ? `: ${detail}` : '.'}`);
      return;
    }
    const body = await response.json();
    state.cards = Array.isArray(body.cards) ? landStakePp(body.cards) : [];
  } catch (_) {
    showMessage('The cards could not be read. Check the connection and reload the page.');
    return;
  }
  $('cb-loading').hidden = true;
  populateOptions();
  writeFilters();
  render();
  // The stored pool gives an immediate first paint. One ordinary board capture then
  // reconciles stake state from the game and redraws this view without resetting filters.
  void cartRefreshOwned(state.player, async () => {
    try {
      const fresh = await fetch(`/api/cards?player=${encodeURIComponent(state.player)}`, { cache: 'no-store' });
      if (!fresh.ok) return;
      const body = await fresh.json();
      if (!Array.isArray(body.cards)) return;
      const grid = $('cb-grid');
      const scroll = window.scrollY;
      const oldHeight = grid?.getBoundingClientRect().height || 0;
      if (grid && scroll > grid.getBoundingClientRect().top + scroll) grid.style.minHeight = `${oldHeight}px`;
      redrawFreshCards(landStakePp(body.cards));
      window.scrollTo(0, scroll);
      // Once the reader scrolls again the temporary height can follow the new result count.
      if (grid?.style.minHeight) window.addEventListener('scroll', () => { grid.style.minHeight = ''; }, { once: true });
    } catch (_) { /* keep the first painted rows when the fresh read fails */ }
  });
}

function init() {
  const fromUrl = new URLSearchParams(location.search).get('player');
  let remembered = null;
  try { remembered = localStorage.getItem(PLAYER_MEMORY_KEY); } catch (_) { remembered = null; }
  state.player = String(fromUrl || remembered || '').trim().toLowerCase() || null;
  if (state.player && !fromUrl) {
    const url = new URL(location.href);
    url.searchParams.set('player', state.player);
    history.replaceState(null, '', url);
  }
  try {
    const saved = JSON.parse(localStorage.getItem(FILTER_STORE_KEY) || 'null');
    // (Use max-level PP\""): a remembered use_max from before is dropped, never applied; the next
    // stored filters are rewritten without it.
    if (saved && typeof saved === 'object') {
      const { use_max: _dropped, ...kept } = saved;
      state.filters = { ...cbDefaultFilters(), ...kept };
      if ('use_max' in saved) localStorage.setItem(FILTER_STORE_KEY, JSON.stringify(state.filters));
    }
  } catch (_) { /* defaults */ }
  if (document.documentElement.dataset.cbStart !== 'market') state.filters.maxed = false;
  syncMaxed();
  initHeader();
  initTooltips();
  prefillOptions();  // before the dropdowns are drawn, so they open with their final labels
  initFilters();
  syncClear();  // Clear all for remembered filters now, not after the cards are read
  initMarket({
    tileHtml, filters: () => state.filters, player: () => state.player, renderCards: () => render(), reloadCards: () => load(),
    initSelect: (el) => initCustomSelect(el, { ariaLabel: 'Market sort' }),
  });
  initCombinables({
    tileHtml, cards: () => state.cards, filters: () => state.filters, player: () => state.player, syncClear,
    reloadCards: () => load(),  // My cards after a confirmed combine in place
  });
  initPurchases($('cp-root'), { player: () => state.player });
  const ownedGrid = $('cb-grid');
  const openOwnedTile = (tile) => {
    const index = [...ownedGrid.children].indexOf(tile);
    const card = state.view?.tiles?.[index]?.card;
    if (card) cartOpenOwnedMenu(card, tile.querySelector('.cb-art img')?.getAttribute('src') || '', {
      player: state.player, copies: state.cards, onConfirmed: () => load(),
    });
  };
  // the tile menu ships once its copy actions are approved (maintainer review menus-round-3); off until then.
  const CB_TILE_MENU = false;
  ownedGrid?.addEventListener('click', (event) => {
    if (!CB_TILE_MENU) return;
    if (event.target.closest?.('a')) return;  // Staked on keeps its plot link.
    const tile = event.target.closest?.('.cb-tile');
    if (tile && document.body.dataset.cbMode === 'cards') openOwnedTile(tile);
  });
  ownedGrid?.addEventListener('keydown', (event) => {
    if (!CB_TILE_MENU || (event.key !== 'Enter' && event.key !== ' ')) return;
    if (event.target?.classList?.contains('cb-tile') && document.body.dataset.cbMode === 'cards') {
      event.preventDefault(); openOwnedTile(event.target);
    }
  });
  if (document.body.dataset.cbMode === 'purchases') document.body.dataset.viewReady = 'true';
  $('cb-mode-purchases')?.addEventListener('click', () => {
    document.body.dataset.cbMode = 'purchases';
    $('cb-mode-combine').checked = false;
    $('cb-mode-cards').setAttribute('aria-pressed', 'false');
    $('cb-mode-market').setAttribute('aria-pressed', 'false');
    $('cb-mode-purchases').setAttribute('aria-pressed', 'true');
  });
  initCardsWallet();
  // the dropdowns are drawn (both sorts included); their stand-ins from cards.html go in the same task, before a paint
  document.querySelectorAll('.cb-pre-select').forEach((el) => el.remove());
  document.addEventListener('error', onArtError, true);
  document.addEventListener('load', onArtLoad, true);
  const sentinel = $('cb-sentinel');
  if (sentinel && typeof IntersectionObserver === 'function') {
    new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { renderMore(); fillViewport(); }
    }, { rootMargin: '1200px 0px' }).observe(sentinel);
  }
  load();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
