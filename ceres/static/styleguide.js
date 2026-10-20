// styleguide.js -- : the living Ceres style guide (/styleguide). Read only: no data read, no API call, no account
// nothing signed or bought. Every specimen is drawn with the REAL stylesheets the page links (ceres.css, buttons.css
// badge-colours.css, card-browser.css, card-checkout.css, mobile-plot.css) from static sample data embedded below.
// Where a component has a pure HTML builder that can be imported without side effects, the specimen calls that builder
// (card-market.js cmTileHtml / cmSkeletonHtml, card-cart.js cartMenuHtml / cartPanelHtml / cartPriceHtml, customSelect.js
// the card-browser-tips.js tooltip models, tooltip.js), so a markup change shows up here too. Builders that live inside
// a module with side effects (card-browser.js tileHtml, editor.js renderEditorTile, card-checkout.js) are mirrored
// below and marked "mirror of"; keep them in step when the original changes.
// The sample data carries plot labels and card names only, never a card UID (memory: no UIDs in frontend). The listing
// keys (lkey) are made-up opaque hex keys, like the real cart's, and are never shown.
import { initTooltips, tipAttrs } from './tooltip.js';
import { initCustomSelect } from './customSelect.js';
import { CB_ABILITY_LABELS, CB_FOIL_LABELS, CB_RARITIES, CB_SET_LABELS, CB_SPLINTERS, cbFormatPpPerCc, cbPpPerCcOf } from './card-browser-model.js';
import {
  cbAbilityIconPath, cbAbilityTip, cbCcTip, cbCoolingTip, cbFoilTip, cbMaxedTip, cbPlotTip, cbPpPerCcTip, cbPpTip,
  cbRarityTip, cbSetTip, cbSplinterTip, cbStakedTip, cbUnavailableTip,
} from './card-browser-tips.js';
import { countdownText, bindCountdowns } from './countdown.js';
import { cmRateTier, cmSkeletonHtml, cmTileHtml, cmTopRate } from './card-market.js';
import { cmPpPerUsd } from './card-market-model.js';
import { _cartForTests as CART, cartMenuHtml, cartPanelHtml, cartPriceHtml } from './card-cart.js';
import { cartItemFrom } from './card-cart-model.js';

const ASSET_BASE = '/static/assets';
const DEC_ICON = '/static/assets/land/token-dec.png';
const CREDITS_ICON = '/static/assets/land/token-credits.png';
const THEMES = ['dark', 'light', 'wood', 'alchemical'];
const THEME_LABEL = { dark: 'Dark', light: 'Light', wood: 'Wood', alchemical: 'Alchemical' };
const STANDARD = 'library/ceres-ui-standards.md';

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
function usd(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return `$${new Intl.NumberFormat('en-US', { minimumFractionDigits: n < 1 ? 3 : 2, maximumFractionDigits: n < 1 ? 3 : 2 }).format(n)}`;
}
const iso = (ms) => new Date(Date.now() + ms).toISOString();

// ── sample data ──────────────────────────────────────────────────────────────────────────────────────────────────────
// Beta prints (edition 1, art from the same card CDN the Cards page uses); a missing picture falls back to the tile's own
// "No image" box, exactly as on the Cards page.
const CARD = {
  goblin: { name: 'Goblin Shaman', card_detail_id: 131, edition: 1, card_set: 'beta', rarity: 'common', foil: 0, splinters: ['fire'], level: 4, max_level: 10, bcx: 22, bcx_max: 400, land_base_pp: 420, max_level_pp: 4000, maxed: false, land_abilities: [['GRAIN', 0.1]] },
  kobold: { name: 'Kobold Miner', card_detail_id: 152, edition: 1, card_set: 'beta', rarity: 'common', foil: 1, splinters: ['earth'], level: 10, max_level: 10, bcx: 400, bcx_max: 400, land_base_pp: 4000, max_level_pp: 4000, maxed: true, land_abilities: [['STONE', 0.1], ['BLOODLINE', 0.15, 'Goblin']] },
  frost: { name: 'Frost Giant', card_detail_id: 141, edition: 1, card_set: 'beta', rarity: 'rare', foil: 0, splinters: ['water'], level: 5, max_level: 8, bcx: 33, bcx_max: 115, land_base_pp: 1650, max_level_pp: 5750, maxed: false, land_abilities: [] },
  lord: { name: 'Lord of Darkness', card_detail_id: 160, edition: 1, card_set: 'beta', rarity: 'legendary', foil: 0, splinters: ['death'], level: 2, max_level: 4, bcx: 2, bcx_max: 11, land_base_pp: 3500, max_level_pp: 19250, maxed: false, land_abilities: [['DD', 0.1], ['LL', 0.05]] },
  shield: { name: 'Shieldbearer', card_detail_id: 136, edition: 1, card_set: 'beta', rarity: 'common', foil: 3, splinters: ['life'], level: 10, max_level: 10, bcx: 1, bcx_max: 0, land_base_pp: 4000, max_level_pp: 4000, maxed: true, land_abilities: [['RATIONING', 0.1]] },
  serpent: { name: 'Serpent of the Flame', card_detail_id: 145, edition: 1, card_set: 'beta', rarity: 'rare', foil: 0, splinters: ['fire'], level: 8, max_level: 8, bcx: 115, bcx_max: 115, land_base_pp: 5750, max_level_pp: 5750, maxed: true, land_abilities: [['ENERGIZED', 1]] },
};

// My cards tiles: the copy states of the card browser (, , , , ).
const MY_TILES = [
  { note: 'Staked on a plot: the full-width badge with the region plaque, linked to /?plot=<label> (public, public)', card: CARD.goblin, status: 'staked', plot: { label: '065-09-083', territory: 'Central Fire' } },
  { note: 'Maxed gold foil, Toil & Kin and a resource ability below the name (public, public)', card: CARD.kobold, status: 'idle' },
  { note: 'Cooling down: the centred waiting badge with a live timer (public)', card: CARD.frost, status: 'cooling', readyAt: iso(41 * 3600e3) },
  { note: 'Not available: the red status badge', card: CARD.lord, status: 'idle', unavailable: 'Listed on the market' },
  { note: 'Black foil, always max CC ("max"); no picture on the CDN, so the tile shows its own "No image" fallback', card: CARD.shield, status: 'staked' },
];

// Market listings (, , , , , ). PP/$ tiers are computed by the real cmRateTier.
const KEY = (n) => (0xa1b2c3d4 + n * 7919).toString(16).padStart(12, '0');
const MARKET_ROWS = [
  { note: 'Top pick (gold PP/$), Lv tag', row: { ...CARD.goblin, lkey: KEY(1), level: 6, bcx: 60, land_base_pp: 600, price_usd: 0.42, price_dec: 1050, combined: 1, pp_source: 'formula' } },
  { note: 'Maxed, 3 listings combined, 2nd maxed copy, in cart (green tile); PP/$ 20–40 % over the top pick (orange)', inCart: true, row: { ...CARD.kobold, foil: 0, lkey: null, parts_keys: [KEY(2), KEY(3), KEY(4)], parts_cc: [200, 150, 50], combined: 3, maxed_seq: 2, level: 10, bcx: 400, land_base_pp: 4000, price_usd: 3.8, price_dec: 9500 } },
  { note: 'Within 20 % of the top pick (green)', row: { ...CARD.frost, lkey: KEY(5), price_usd: 1.3, price_dec: 3250, combined: 1, pp_source: 'listing' } },
  { note: 'Your own listing (amber), 20–40 % over the top pick (orange), maxed', row: { ...CARD.serpent, lkey: KEY(6), own: true, price_usd: 5.2, price_dec: 13000, combined: 1, pp_source: 'listing' } },
  { note: 'More than 40 % over the top pick (red), black foil', row: { ...CARD.shield, lkey: KEY(7), price_usd: 9.6, price_dec: 24000, combined: 1, pp_source: 'listing' } },
];

// The card menu's print (the market print read's shape; nothing is read here): copies on sale, cheapest per CC first.
const PRINT = { id: '131-1-0', name: 'Goblin Shaman', card_detail_id: 131, edition: 1, foil: 0, card_set: 'beta', rarity: 'common', max_level: 10, bcx_max: 400, max_level_pp: 4000, thresholds: [1, 5, 14, 30, 60, 100, 150, 220, 300, 400], market_url: 'https://splinterlands.com/card-detail/131/beta/regular' };
const LISTING = (n, level, bcx, priceUsd, extra = {}) => ({ lkey: KEY(20 + n), name: PRINT.name, card_detail_id: 131, edition: 1, foil: 0, card_set: 'beta', rarity: 'common', level, bcx, land_base_pp: Math.round(4000 * bcx / 400), price_usd: priceUsd, price_dec: Math.round(priceUsd * 2500), pp_per_usd: (4000 * bcx / 400) / priceUsd, ...extra });
const MENU_DATA = {
  print: PRINT,
  count: 6,
  on_sale: 7,
  truncated: false,
  listings: [
    LISTING(1, 9, 300, 2.1),
    LISTING(2, 6, 60, 0.45),
    LISTING(3, 7, 100, 0.74),
    LISTING(5, 3, 14, 0.2, { over_flag: true, over_cheapest: 0.85 }),
    LISTING(4, 4, 22, 0.19, { own: true }),
    LISTING(6, 1, 1, 0.012),
  ],
};

function seedCart() {
  // The cart module's own state object (no localStorage): one bundle, one single copy, one price change, one sold copy.
  const l = MENU_DATA.listings;
  const items = [
    cartItemFrom(l[1], { now: 1, bundle: 'Lv 6', group: 'lv-sample-6' }),
    cartItemFrom(l[5], { now: 2 }),
    ...MARKET_ROWS[1].row.parts_keys.map((lkey, i) => cartItemFrom({ ...MARKET_ROWS[1].row, lkey, bcx: MARKET_ROWS[1].row.parts_cc[i], price_usd: [1.9, 1.4, 0.5][i], price_dec: [4750, 3500, 1250][i], land_base_pp: MARKET_ROWS[1].row.parts_cc[i] * 10 }, { now: 3, bundle: 'Lv 10', group: 'max-sample-1', via: MARKET_ROWS[1].row.parts_keys.join(',') })),
  ].filter(Boolean);
  const changed = items.find((it) => it.lkey === l[5].lkey);
  if (changed) Object.assign(changed, { status: 'changed', was_usd: 0.009 });
  CART.state = { items, removed: [{ ...cartItemFrom(LISTING(9, 5, 40, 0.31), { now: 0 }), seen_usd: 0.31 }] };
}

// ── shared bits ──────────────────────────────────────────────────────────────────────────────────────────────────────
function splinterIcon(splinter) {
  const key = String(splinter || '').toLowerCase();
  return CB_SPLINTERS.includes(key) ? `<img class="splinter-icon" src="${ASSET_BASE}/splinter/${key}.svg" alt="${esc(cap(key))}"${cbSplinterTip(key)}>` : '';
}
function rarityGem(rarity) {
  const key = String(rarity || '').toLowerCase();
  return CB_RARITIES.includes(key) ? `<img class="rarity-gem" src="${ASSET_BASE}/rarity/gem-${key}.svg" alt="${esc(cap(key))}"${cbRarityTip(key)}>` : '';
}
const SET_ICON = { alpha: 'alpha', beta: 'beta', untamed: 'untamed', chaos: 'chaos-legion', rebellion: 'rebellion', conclave: 'conclave-arcana', foundations: 'foundations', land: 'eternal-land', gladius: 'eternal-land' };
function setIcon(cardSet, edition = null) {
  const file = SET_ICON[cardSet];
  const label = CB_SET_LABELS[cardSet] || cap(cardSet);
  return `<img class="set-icon${file ? '' : ' set-icon-generic'}" src="${ASSET_BASE}/sets/${file || 'edition-generic'}.svg" alt="${esc(label)}"${cbSetTip(label, file, edition)}>`;
}
const CARD_CDN_BASE = 'https://d36mxiodymuqjm.cloudfront.net';
function cardArt(card) {
  if (!card.name) return [];
  const suffix = [1, 2].includes(card.foil) ? '_gold' : [3, 4].includes(card.foil) ? '_blk' : '';
  const file = encodeURIComponent(card.name) + suffix;
  return [`${CARD_CDN_BASE}/cards_beta/${file}.jpg`, `${CARD_CDN_BASE}/cards_beta/${file}.png`];
}
function territorySlug(territory) {
  return territory ? String(territory).toLowerCase().replace(/[^a-z]+/g, '-').replace(/^-|-$/g, '') : null;
}

// Art fallback, as card-browser.js onArtError / onArtLoad (tiles) and editor.js (plot tiles): next candidate, then the
// tile's own "No image" box.
function onArtError(event) {
  const img = event.target;
  if (!(img instanceof HTMLImageElement)) return;
  const listAttr = img.dataset.cbArt !== undefined ? 'cbArt' : img.dataset.cardImageCandidates !== undefined ? 'cardImageCandidates' : null;
  if (!listAttr) return;
  let list = [];
  try { list = JSON.parse(img.dataset[listAttr] || '[]'); } catch (_) { list = []; }
  const indexAttr = listAttr === 'cbArt' ? 'cbArtIndex' : 'cardImageIndex';
  const next = Number(img.dataset[indexAttr] || 0) + 1;
  if (next < list.length) { img.dataset[indexAttr] = String(next); img.src = list[next]; return; }
  const box = img.parentElement;
  img.remove();
  if (box && listAttr === 'cbArt') {
    box.classList.add('cb-art-missing');
    box.insertAdjacentHTML('afterbegin', '<span class="cb-art-missing-text">No image</span>');
  }
}
function onArtLoad(event) {
  const img = event.target;
  if (img instanceof HTMLImageElement && img.dataset.cbArt !== undefined) img.classList.add('is-art-in');
}

// ── mirror of card-browser.js tileHtml (and its statusBadges / coolingBadge / abilityRow / ccLine / levelTagHtml) ────────
function statusBadges(tile) {
  const parts = [];
  if (tile.plot) {
    const p = tile.plot;
    const slug = territorySlug(p.territory);
    parts.push(`<span class="slot-wait-badge cb-onland"><span class="slot-wait-label">Staked on</span><a class="ch-plot-jump ch-plot-badge ceres-region-plaque${slug ? ` territory-${slug}` : ''}" href="/?plot=${encodeURIComponent(p.label)}"${cbPlotTip(p.label, p.territory)}>${esc(p.label)}</a></span>`);
  } else if (tile.status === 'staked') {
    parts.push(`<span class="slot-wait-badge cb-onland"${cbStakedTip()}><span class="slot-wait-label">Staked</span></span>`);
  }
  if (tile.unavailable) parts.push(`<span class="candidate-status-badge is-danger cb-badge"${cbUnavailableTip(tile.unavailable)}><span>${esc(tile.unavailable)}</span></span>`);
  return parts.length ? `<span class="cb-badges">${parts.join('')}</span>` : '';
}
function coolingBadge(tile) {
  if (tile.status !== 'cooling') return '';
  const until = tile.readyAt && Number.isFinite(Date.parse(tile.readyAt)) ? tile.readyAt : null;
  const time = until
    ? `<span class="slot-wait-time" data-countdown-until="${esc(until)}" data-countdown-compact data-countdown-ended="ready">${esc(Date.parse(until) <= Date.now() ? 'ready' : countdownText(until, Date.now(), { compact: true }))}</span>`
    : '';
  return `<span class="slot-wait-badge cb-wait"${cbCoolingTip(until)}>⏳<span class="slot-wait-label">Cooling</span>${time}</span>`;
}
function abilityRow(card) {
  const abilities = Array.isArray(card.land_abilities) ? card.land_abilities : [];
  if (!abilities.length) return '';
  const items = abilities.map((entry) => {
    const key = Array.isArray(entry) ? entry[0] : entry;
    const icon = cbAbilityIconPath(key);
    return `<span class="slot-ability-item">${icon ? `<img class="ability-icon" src="${ASSET_BASE}/${icon}" alt=""${cbAbilityTip(entry)}>` : `<span class="ability-icon ability-icon-text"${cbAbilityTip(entry)}>${esc(String(key).slice(0, 2))}</span>`}</span>`;
  }).join('');
  return `<span class="slot-ability-row is-centred cb-abilities" aria-label="Land abilities"><span class="slot-ability-row">${items}</span></span>`;
}
function ccLine(card) {
  const cc = card.bcx > 0 ? num(card.bcx) : '—';
  const max = card.bcx_max === 0 ? 'max' : card.bcx_max > 0 ? num(card.bcx_max) : '—';
  return `<span class="cb-cc"${cbCcTip(card)}>CC <b>${cc}</b> / ${max}</span>`;
}
function levelTagHtml(card) {
  const of = card.max_level ? ` of ${card.max_level}` : '';
  const cc = card.bcx > 0 ? ` · CC ${num(card.bcx)}${card.bcx_max > 0 ? ` / ${num(card.bcx_max)}` : ''}` : '';
  return `<span class="candidate-tag is-info cb-maxed cb-level-tag"${tipAttrs({ title: `Level ${card.level}${of}${cc}` })}>Lv ${esc(card.level)}</span>`;
}
function tileHtml(tile) {
  const card = tile.card;
  const extra = tile.extra || null;
  const art = cardArt(card);
  const foil = [1, 2, 3, 4].includes(card.foil) ? `<span class="foil-badge foil-${card.foil}"${cbFoilTip(card.foil)}>${esc(CB_FOIL_LABELS[card.foil])}</span>` : '';
  const maxed = card.maxed && !card.no_pp ? `<span class="candidate-tag is-info cb-maxed"${cbMaxedTip(card)}>Maxed</span>` : '';
  const levelTag = extra && extra.levelTag !== false && !maxed && !card.maxed && card.level != null ? levelTagHtml(card) : '';
  const top = foil || maxed || levelTag ? `<span class="cb-art-tags">${foil}${maxed}${levelTag}</span>` : '';
  const artExtra = extra?.art || '';
  const image = art.length ? `<img src="${esc(art[0])}" alt="" loading="lazy" decoding="async" data-cb-art="${esc(JSON.stringify(art))}" data-cb-art-index="0">` : '';
  const level = card.max_level ? `Lv ${esc(card.level ?? '—')}/${esc(card.max_level)}` : `Lv ${esc(card.level ?? '—')}`;
  const icons = `<span class="candidate-icon-row">${(card.splinters || []).map((s) => `<span class="candidate-icon-box">${splinterIcon(s)}</span>`).join('')}<span class="candidate-icon-box">${rarityGem(card.rarity)}</span><span class="candidate-icon-box">${setIcon(card.card_set, card.edition)}</span></span>`;
  let maxLine;
  if (card.no_pp) maxLine = 'no land PP';
  else if (card.maxed) maxLine = 'max level';
  else maxLine = `max ${num(card.max_level_pp)}`;
  const perCc = card.land_base_pp ? cbPpPerCcOf(card) : null;
  const ppPerCc = perCc !== null ? `<span class="candidate-grid-metric cb-metric-cc"${cbPpPerCcTip()}><strong>${cbFormatPpPerCc(perCc)}</strong><small>PP per CC</small></span>` : '';
  const badges = statusBadges(tile) + (extra?.foot || '');
  const abilities = abilityRow(card);
  const above = extra ? abilities : '';
  const below = extra ? '' : abilities.replace('slot-ability-row is-centred cb-abilities', 'slot-ability-row is-centred cb-abilities cb-abilities-below');
  const foot = badges || above ? `<span class="slot-foot cb-foot">${above}${badges}</span>` : '';
  const caption = `<span class="${String(card.name || '').length > 18 ? 'slot-caption slot-caption--long' : 'slot-caption'} cb-caption"><strong class="slot-name">${esc(card.name)}</strong>${below}</span>`;
  const topBar = top || artExtra ? `<span class="cb-art-top">${top}${artExtra}</span>` : '';
  const accessible = extra?.label || `${card.name}. ${level}. CC ${card.bcx ?? '—'}.${tile.plot ? ` Staked on ${tile.plot.label}.` : ''} Base PP ${num(card.land_base_pp)}, ${maxLine}.`;
  const metrics = extra?.metrics ?? `<span class="candidate-grid-metric cb-metric-pp"${cbPpTip(card, maxLine)}><strong>${num(card.land_base_pp)}</strong><small>${esc(card.maxed && !card.no_pp ? 'PP' : maxLine)}</small></span>${ppPerCc}`;
  return `<article class="candidate-card candidate-grid-card cb-tile${extra?.className ? ` ${extra.className}` : ''}" aria-label="${esc(accessible)}"${extra?.attrs || ''}><span class="candidate-grid-face"><span class="candidate-art cb-art" data-name="${esc(card.name)}">${image}${topBar}${coolingBadge(tile)}${foot}${caption}${extra?.bottom || ''}</span><span class="candidate-grid-icon-level cb-level">${icons}<span class="cb-level-cc"><span class="cb-lv">${level}</span><span class="cb-sep" aria-hidden="true">·</span>${ccLine(card)}</span></span><span class="candidate-grid-metrics">${metrics}</span>${extra?.after || ''}</span></article>`;
}

// ── mirror of editor.js renderEditorTile (worker tiles) and badges.js bloodlineTileBadgesHtml ──────────────────────────
const KIN_ICON = `<img class="slot-kin-icon" src="${ASSET_BASE}/abilities/ability_toil-and-kin.png" alt="" aria-hidden="true">`;
function kinBadge(cls, text, tip) {
  return `<span class="slot-kin-corner"><span class="slot-kin-badges"><span class="slot-kin-badge ${cls}"${tipAttrs({ ...tip, peer: 'tile_chip_rail' })}>${KIN_ICON}<span class="slot-kin-text">${esc(text)}</span></span></span></span>`;
}
function slotAbilities(entries) {
  if (!entries.length) return '';
  const items = entries.map((entry) => {
    const icon = cbAbilityIconPath(entry[0]);
    return `<span class="slot-ability-item">${icon ? `<img class="ability-icon" src="${ASSET_BASE}/${icon}" alt=""${cbAbilityTip(entry)}>` : ''}</span>`;
  }).join('');
  return `<div class="slot-foot"><div class="slot-ability-row is-centred" aria-label="Land abilities"><span class="slot-ability-row">${items}</span></div></div>`;
}
function slotWaitBadge(state, hours) {
  const until = hours ? iso(hours * 3600e3) : null;
  const time = until
    ? `<span class="slot-wait-time" data-countdown-until="${esc(until)}" data-countdown-compact data-countdown-ended="due">${esc(countdownText(until, Date.now(), { compact: true }))}</span>`
    : '<span class="slot-wait-time">72h</span>';
  const body = state === 'elsewhere' ? 'Its unstake on the other plot signs first; the stake here waits for its 72-hour cooldown.' : 'Signable once its cooldown ends.';
  return `<span class="slot-wait-badge${state === 'elsewhere' ? ' is-elsewhere' : ''}"${tipAttrs({ title: 'Waiting', body })}>⏳<span class="slot-wait-label">Waiting</span>${time}</span>`;
}
function workerTile(card, { kin = '', wait = '', stage = '', caption = card.name } = {}) {
  const art = cardArt(card);
  const img = `<span class="slot-art slot-art-worker"><img class="eos-portrait-fill" src="${esc(art[0])}" alt="${esc(card.name)} card art" loading="lazy" decoding="async" data-card-image-candidates="${esc(JSON.stringify(art))}" data-card-image-index="0"></span>`;
  const abilities = wait ? '' : slotAbilities((card.land_abilities || []).filter((e) => !(kin && e[0] === 'BLOODLINE')));
  const stageCls = stage ? ` is-staged is-stage-${stage}` : '';
  const tip = tipAttrs({ title: card.name, body: `Level ${card.level} · ${num(card.land_base_pp)} Base PP` });
  const cls = String(caption).split(/\s+/).some((t) => t.length > 11) ? 'slot-caption slot-caption--long' : 'slot-caption';
  return `<div class="slot-tile-cell"><div class="slot-tile slot-tile-card has-slot-art is-view-only${stageCls}" data-group-id="worker"${tip}>${wait}${kin}${img}${abilities}<span class="${cls}"><span class="slot-name">${esc(caption)}</span></span></div></div>`;
}
function emptyTile(label) {
  return `<div class="slot-tile-cell"><div class="slot-tile slot-tile-card is-empty is-view-only"><span class="plus">+</span><span class="empty-label">${esc(label)}</span></div></div>`;
}

// ── specimens ────────────────────────────────────────────────────────────────────────────────────────────────────────
function specimen(label, html, { wide = false, note = '' } = {}) {
  return `<figure class="sg-specimen${wide ? ' is-wide' : ''}"><div class="sg-specimen-body">${html}</div><figcaption>${esc(label)}${note ? ` <span class="sg-note">${esc(note)}</span>` : ''}</figcaption></figure>`;
}
function row(html) { return `<div class="sg-row">${html}</div>`; }
function panel(title, body, foot = '', extraClass = '') {
  return `<div class="slot-hazard-panel cm-dialog-panel sg-inline-panel${extraClass ? ` ${extraClass}` : ''}" role="region" aria-label="${esc(title)}"><header><h3>${esc(title)}</h3><button type="button" class="hazard-close ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" aria-label="Close">×</button></header><div class="slot-hazard-body cm-dialog-body">${body}</div>${foot ? `<footer class="cm-dialog-foot">${foot}</footer>` : ''}</div>`;
}

// C: colour tokens. The meaning column is C-4's state vocabulary.
const TOKENS = [
  ['--bg', 'page surface'], ['--bg2', 'panel surface (W-1)'], ['--bg3', 'control surface (K-1)'],
  ['--text', 'running text (= --body)'], ['--text-dim', 'secondary text (= --meta)'], ['--heading', 'headings, control text'],
  ['--meta', 'neutral / info text'], ['--link', 'links'],
  ['--primary', 'main action, filled input, header art'], ['--accent', 'focus ring, selected choice, secondary hover border'],
  ['--ok', 'ok: possible, positive, unlocked, gain, selected filter, in cart, bought'],
  ['--warn', 'warn: pending, staged, cooldown, your listing, price rose'],
  ['--danger', 'danger: blocked, negative, locked, cost, destructive, sold, close'],
  ['--info', 'neutral: info'], ['--gold', 'gold: golden badges, waiting badge, top pick, splinter ring'], ['--gold-dim', 'gold: dim gold'],
  ['--orchid', 'Iris accent (no state)'], ['--border', 'hairlines, dividers (K-8)'], ['--border-bright', 'panel edge (W-1), disabled border'],
  ['--shadow', 'shadows and backdrops (E-2)'], ['--hi', 'the only white: highlights'], ['--tint-base', 'Iris tint base'],
  ['--stage-planned', 'Ceres: planned card (public)'], ['--ready-alert', 'Ceres: Changes ready mode (public)'],
];
const TERRITORIES = ['pristine-northwest', 'broken-lands', 'land-of-prosperity', 'central-fire', 'wild-northeast', 'great-lowlands', 'shimmering-coasts'];
const TERRAINS = ['river', 'lake', 'hills', 'forest', 'mountain', 'jungle', 'caldera', 'plains', 'swamp', 'canyon', 'desert', 'tundra', 'badlands', 'bog'];

function colourSection() {
  const swatches = TOKENS.map(([name, meaning]) => `<div class="sg-token"><span class="sg-swatch" style="background:var(${name})"></span><code>${name}</code><span class="sg-token-meaning">${esc(meaning)}</span><span class="sg-token-value" data-sg-token="${name}"></span></div>`).join('');
  const terr = TERRITORIES.map((t) => `<span class="ch-plot-badge ceres-region-plaque territory-${t}">${esc(t.split('-').map(cap).join(' '))}</span>`).join('');
  const terrain = TERRAINS.map((t) => `<span class="sg-chip-swatch" style="background:var(--terrain-${t})">${esc(cap(t))}</span>`).join('');
  return `<div class="sg-tokens">${swatches}</div>`
    + specimen('Territory plaques: the plot badge in each region colour (C-3, public region plaque, .ch-plot-badge)', `<div class="sg-wrap">${terr}</div>`, { wide: true })
    + specimen('Terrain colours (C-3, --terrain-*)', `<div class="sg-wrap">${terrain}</div>`, { wide: true });
}

function typeSection() {
  const steps = [
    ['--fs-h-display', 'var(--step-2)', 'Display: start page', 'var(--font-display)', 800],
    ['--fs-h-panel', '1.25rem', 'Panel heading', 'var(--font-display)', 700],
    ['--fs-body', '1rem', 'Running text and comfortable controls', 'var(--font-body)', 400],
    ['--fs-table', 'clamp(.875rem, .75rem + .15625cqi, .9375rem)', 'Plot table body 1,234.56', 'var(--font-body)', 400],
    ['--fs-ledger', '.9rem', 'Harvest all ledger text', 'var(--font-body)', 400],
    ['--fs-control', '.8rem', 'Every control: buttons, dropdowns, inputs, pills', 'var(--font-body)', 700],
    ['--fs-label', '.75rem', 'Pill labels, state text', 'var(--font-body)', 700],
    ['--fs-caption', '.7rem', 'Caption: the readable minimum', 'var(--font-body)', 400],
  ];
  const rows = steps.map(([name, value, text, family, weight]) => `<div class="sg-type-row"><code>${name}</code><code class="sg-type-value">${esc(value)}</code><span style="font-size:${value};font-family:${family};font-weight:${weight};color:var(--heading)">${esc(text)}</span></div>`).join('');
  const weights = [[400, 'body'], [600, 'dropdown value, text inputs, emphasis'], [700, 'buttons, table header, labels'], [800, 'display numerals']]
    .map(([w, use]) => `<div class="sg-type-row is-two"><code>${w}</code><span style="font-weight:${w}">Ceres ${w} — ${esc(use)}</span></div>`).join('');
  const fonts = `<div class="sg-type-row is-two"><code>--font-body</code><span style="font-family:var(--font-body)">Inter — body face (T-1)</span></div><div class="sg-type-row is-two"><code>--font-display</code><span style="font-family:var(--font-display);font-size:1.25rem">Display — panel headings only</span></div><div class="sg-type-row is-two"><code>--font-mono</code><span style="font-family:var(--font-mono)">065-09-083 · only ids, plot numbers, tx hashes</span></div>`;
  const numbers = `<div class="sg-num-demo"><span>Base PP</span><b>584,325.00</b><span>DEC/hr</span><b>1,015.42</b><span>No such value</span><b>—</b></div>`;
  return specimen('T-3 type scale (names are the planned tokens)', rows, { wide: true })
    + specimen('T-2 weights', weights, { wide: true })
    + specimen('T-1 faces', fonts, { wide: true })
    + specimen('T-4 numbers: right-aligned, tabular, two decimals, "—" for no value', numbers);
}

// K-1 / : the button contract (tests/ceres/fixtures/ceres-button-contract.json): treatments P/S/C, densities, cases.
const DENSITIES = ['dense', 'compact', 'header', 'comfortable', 'touch', 'navigation'];
const ICON_X = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M5 5L19 19M19 5L5 19"/></svg>';
function buttonSection() {
  const matrix = DENSITIES.map((d) => `<div class="sg-btn-row"><code>${d}</code><button type="button" class="ceres-btn ceres-btn--primary" data-control-density="${d}">Primary</button><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="${d}">Secondary</button><button type="button" class="ceres-btn ceres-btn--choice" data-control-density="${d}" aria-pressed="false">Choice</button><button type="button" class="ceres-btn ceres-btn--choice" data-control-density="${d}" aria-pressed="true">Chosen</button></div>`).join('');
  const states = row(`<button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable">Rest</button>`
    + `<button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" disabled>Disabled</button>`
    + `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" disabled>Disabled</button>`
    + `<button type="button" class="ceres-btn ceres-btn--secondary sg-focus-demo" data-control-density="comfortable">Focus (keyboard)</button>`
    + `<button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" aria-busy="false"><span class="spinner spinner-sm" aria-hidden="true"></span>Working</button>`);
  const cases = row(`<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-case="destructive">Destructive</button>`
    + `<button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" data-control-case="commit">Commit · Sign</button>`
    + `<button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" data-control-case="commit destructive">Commit + destructive</button>`
    + `<label class="picker-toggle ceres-btn ceres-btn--choice" data-control-density="compact" data-control-case="signal"><span class="ceres-btn__check" aria-hidden="true"></span><input type="checkbox"><span>Signal toggle</span></label>`);
  const icons = row(`<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" aria-label="Close">×</button>`
    + `<button type="button" class="slot-remove ceres-btn ceres-btn--secondary" data-control-density="dense" data-control-mode="icon" data-slot-action="remove" aria-label="Stage an unstake">${ICON_X}</button>`
    + `<button type="button" class="ceres-btn ceres-btn--primary" data-control-density="header" data-control-mode="icon" aria-label="Plots"><svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true"><path class="f" d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/><path class="d" d="M10 6.5h4M6.5 10v4M17.5 10v4M10 17.5h4"/></svg></button>`
    + `<button type="button" class="ceres-btn ceres-btn--primary cm-cart-btn" data-control-density="header" aria-label="Cart · 3 items"><svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true"><path class="f" d="M5.2 6.2h16l-2.1 8.3H7.3z"/><path class="d" d="M1.8 3.2h3l3 12.2h11"/><circle class="f" cx="9.2" cy="19.4" r="1.9"/><circle class="f" cx="17.6" cy="19.4" r="1.9"/></svg> <span class="cm-cart-count" data-state="ok">3</span></button>`);
  const tabs = `<div class="ceres-tabs" role="tablist" aria-label="Sample tabs"><button type="button" class="ceres-btn ceres-btn--secondary" role="tab" aria-selected="true">Grain</button><button type="button" class="ceres-btn ceres-btn--secondary" role="tab" aria-selected="false">Wood</button><button type="button" class="ceres-btn ceres-btn--secondary" role="tab" aria-selected="false">Stone</button></div>`;
  return specimen('K-1 / K-2 treatment × density (buttons.css --ceres-control-*): one row = one height', `<div class="sg-btn-matrix">${matrix}</div>`, { wide: true })
    + specimen('K-1 states: rest, disabled, focus, busy (hover and pressed are live)', states, { wide: true })
    + specimen('K-1 / K-6a cases: destructive, commit, commit+destructive, signal', cases, { wide: true })
    + specimen('Icon mode, tile remove (K-7), header icon, cart count', icons)
    + specimen('K-6 tabs (.ceres-tabs, public)', tabs);
}

function filterSection() {
  const rowHtml = `<div class="controls-row sg-demo-controls">`
    + `<label class="board-filter-field"><span class="sr-only">Name</span><input type="text" class="ceres-wide-input" placeholder="card name…" aria-label="Sample search"></label>`
    + `<label class="board-filter-field"><span class="sr-only">Filled</span><input type="text" class="ceres-wide-input" value="Goblin" aria-label="Sample filled search"></label>`
    + `<span class="controls-sep" aria-hidden="true"></span>`
    + `<label class="board-filter-field"><span class="sr-only">Rarity</span><select data-sg-select="multi-rarity"><option value="">All</option>${CB_RARITIES.map((r) => `<option value="${r}"${r === 'rare' ? ' selected' : ''}>${cap(r)}</option>`).join('')}</select></label>`
    + `<label class="board-filter-field"><span class="sr-only">Splinter</span><select data-sg-select="multi-splinter"><option value="">All</option>${CB_SPLINTERS.map((s) => `<option value="${s}">${cap(s)}</option>`).join('')}</select></label>`
    + `<label class="board-filter-field"><span class="sr-only">Ability</span><select data-sg-select="multi-ability"><option value="">All</option><option value="__any__">Has an ability</option>${Object.entries(CB_ABILITY_LABELS).map(([code, label]) => `<option value="${code}">${label}</option>`).join('')}</select></label>`
    + `<label class="board-filter-field"><span class="sr-only">Sort</span><select data-sg-select="single"><option value="pp">Sort: Base PP</option><option value="cost">Sort: Cost per PP</option><option value="name">Sort: Name</option></select></label>`
    + `<span class="controls-sep" aria-hidden="true"></span>`
    + `<label class="board-filter-field cb-pp-field"><span class="sr-only">Min PP</span><input type="text" class="ceres-wide-input" inputmode="decimal" placeholder="Min PP" aria-label="Sample minimum PP"></label>`
    + `<label class="picker-toggle board-filter-pill ceres-btn ceres-btn--choice" data-control-density="compact" data-tip="Maxed cards"><span class="ceres-btn__check" aria-hidden="true"></span><input type="checkbox" checked><span>Max</span></label>`
    + `<div class="board-clear-filters-row"><button type="button" class="board-clear-filters ceres-btn ceres-btn--secondary" data-control-density="compact" data-control-mode="icon" data-tip="Clear all filters" aria-label="Clear all filters"><svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>`
    + `</div>`;
  const iconStates = `<div class="controls-row sg-demo-controls">${[1, 3, 4].map((count) => `<label class="board-filter-field"><span class="sr-only">${count} splinters selected</span><select data-sg-select="multi-splinter" multiple><option value="">All</option>${CB_SPLINTERS.map((s, i) => `<option value="${s}"${i < count ? ' selected' : ''}>${cap(s)}</option>`).join('')}</select></label>`).join('')}</div>`;
  return specimen('Filter row: wide input (empty, filled), dividers, multi select with icons, single select, Min PP, pills, Clear all', rowHtml + iconStates, { wide: true, note: 'Icon choices: one shows icon and name, two or three show icons with names in the accessible label and tooltip, four or more show a count. The examples below use one, three and four splinters.' });
}

function glassSection() {
  const rows = [
    ['065-09-083', 'central-fire', 'plains', 'Plains', 'grain.png', 'Grain', '584,325', '5.30x', '11,687'],
    ['112-41-007', 'shimmering-coasts', 'lake', 'Lake', 'wood_200.webp', 'Wood', '211,040', '3.10x', '2,940'],
    ['018-02-151', 'broken-lands', 'caldera', 'Caldera', 'stone_200.webp', 'Stone', '98,500', '1.85x', '702'],
  ].map(([label, terr, terrain, terrainName, res, resName, pp, boost, rate]) => `<tr class="plot-row"><td class="plot-cell territory-${terr}">${label}</td><td class="terrain-cell terrain-${terrain}"><img class="terrain-emblem" src="${ASSET_BASE}/land/terrain-emblem-${terrain}.svg" alt="">${terrainName}</td><td class="resource-cell"><img class="resource-icon" src="${ASSET_BASE}/resources/${res}" alt="${resName}"></td><td class="col-num">${pp}</td><td class="col-num">${boost}</td><td class="col-num"><span>${rate}</span> <img class="resource-icon" src="${ASSET_BASE}/resources/${res}" alt="${resName}"></td><td class="col-num">—</td></tr>`).join('');
  const table = `<div class="table-scroll sg-table"><table class="plot-table"><thead><tr><th data-key="plot" aria-sort="ascending">Plot<span class="sort-chevron" aria-hidden="true"></span></th><th>Terrain</th><th>Res.</th><th data-key="pp" aria-sort="none">Base PP<span class="sort-chevron" aria-hidden="true"></span></th><th>Boost</th><th>Rate/hr</th><th>Wood/hr</th></tr></thead><tbody id="plot-tbody">${rows}</tbody></table></div>`;
  return specimen('Board rows over the page artwork: plain opaque head row, glass rows, zebra, tinted plot and terrain cells (hover a row)', table, { wide: true, note: 'Desktop glass public/public/public; plot table public (36px rows, 14–15px text).' })
    + specimen('Header: the page artwork itself, untinted (public) — this page\'s own header above', '<p class="sg-note">The header at the top of this page is the real <code>.board-header</code> with the real version tag and local marker.</p>');
}

function plotSection() {
  const paired = kinBadge('is-paired', 'Toil & Kin +15%', { title: 'Toil and Kin · Goblin', body: 'This card\'s Goblin companion is staked here, so both cards produce more.' });
  const solo = kinBadge('is-solo', 'Toil & Kin: Solo', { title: 'Toil and Kin · Goblin', body: 'No Goblin companion is staked on this plot, so this card\'s Toil and Kin adds nothing.' });
  const companion = kinBadge('is-companion', 'Companion', { title: 'Companion · Goblin', body: 'This card completes Kobold Miner\'s Goblin Toil and Kin.' });
  const tiles = workerTile(CARD.kobold, { kin: paired })
    + workerTile(CARD.lord, { kin: solo })
    + workerTile(CARD.goblin, { kin: companion })
    + workerTile(CARD.frost, { stage: 'blocked', wait: slotWaitBadge('blocked', 41) })
    + workerTile(CARD.serpent, { stage: 'elsewhere', wait: slotWaitBadge('elsewhere', 0) })
    + workerTile(CARD.shield, { stage: 'ready' })
    + emptyTile('Worker 5');
  const band = `<div class="editor-band sg-band"><section class="slot-group" data-group-id="worker"><div class="slot-grid">${tiles}</div></section></div>`;
  return specimen('Worker tiles: Toil & Kin paired (green), Solo (amber), Companion; ability icons; Waiting badge (cooldown, moving); staged ready outline; empty slot', band, { wide: true, note: 'Tile V2 public; kin corner public; ability row public; waiting badge public; stage tones public / public.' });
}

function cardsSection() {
  const grid = `<section class="candidate-list is-card-grid cb-grid sg-grid" aria-label="Sample cards">${MY_TILES.map((t) => tileHtml({ ...t, unavailable: t.unavailable || '' })).join('')}</section>`;
  const notes = `<ol class="sg-list">${MY_TILES.map((t) => `<li>${esc(t.card.name)} — ${esc(t.note)}</li>`).join('')}</ol>`;
  const summary = '<div class="cb-summary"><span class="cb-summary-item"><strong>812</strong> copies</span><span class="cb-summary-item">Base PP <strong>1,204,330</strong></span><span class="cb-summary-item">At max level <strong>3,980,000</strong> <span class="cb-summary-note">(4 without a max value)</span></span></div>';
  return specimen('Summary bar', summary, { wide: true }) + specimen('My cards tiles', grid + notes, { wide: true });
}

function marketTiles() {
  const top = cmTopRate(MARKET_ROWS.map((m) => m.row));
  return MARKET_ROWS.map((m, i) => {
    const r = m.row;
    const tile = { key: i, listing: r, card: { ...r, no_pp: false, land_abilities: r.land_abilities || [], skin: null }, status: 'idle', plot: null, readyAt: null, unavailable: '', rateTier: cmRateTier(cmPpPerUsd(r), top) };
    return cmTileHtml(tile, tileHtml);
  }).join('');
}
function marketSection() {
  const grid = `<section class="candidate-list is-card-grid cb-grid cm-grid sg-grid" aria-label="Sample market listings">${marketTiles()}</section>`;
  const notes = `<ol class="sg-list">${MARKET_ROWS.map((m) => `<li>${esc(m.row.name)} — ${esc(m.note)}</li>`).join('')}</ol>`;
  const summary = '<div class="cb-summary"><span class="cb-summary-item"><strong>5</strong> listings shown</span><span class="cb-summary-item">Best <strong>1,429 PP/$</strong> <span class="cb-summary-note">Goblin Shaman · 0.57 PP per DEC</span></span><span class="cb-summary-item cb-summary-note">Market read at 14:02:11 in 1.4 s · 1 DEC = $0.000400</span></div>';
  return specimen('Market summary', summary, { wide: true })
    + specimen('Market tiles: $ and DEC price boxes, Lv N / Maxed, N listings combined, Nth maxed copy, Your listing, In cart, PP/$ rank gold / green / orange / red', grid + notes, { wide: true, note: 'Drawn by card-market.js cmTileHtml itself (rank colours under the Cost per PP sort, public).' });
}

function menuSection() {
  const body = cartMenuHtml(MENU_DATA, cardArt(CARD.goblin)[0], null);
  const foot = '<span class="cm-foot-total">5 listings in cart · ' + cartPriceHtml(4.26, 10650) + '</span><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable">Open cart</button>';
  return specimen('Card menu: print facts, Max-level tile adds a maxed card, bundle buttons (in cart green), copy table rows cheap (green) / in cart / your listing / +N % over cheapest', panel('Goblin Shaman', body, foot), { wide: true, note: 'card-cart.js cartMenuHtml itself; max price and count appear once, inside the Max-level choice. public / public / public / public / public / public. On phones the copy rows carry their own CC and PP labels and the desktop heading is hidden.' });
}

function cartSection() {
  const foot = '<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-case="destructive">Clear cart</button>';
  return specimen('Cart panel: Checkout primary at top; group bundle label once, Buy & max secondary, price changed chip, Remove', panel('Cart', cartPanelHtml(), foot), { wide: true, note: 'card-cart.js cartPanelHtml itself (public, public); footer mirrors cartPanelFootHtml.' });
}

// mirror of card-checkout.js quoteRowsHtml / reviewHtml / confirmHtml / outcomeHtml / groupsHtml (module has side effects)
function coRow(state, name, meta, price, chip = '', reason = '') {
  return `<div class="co-row" data-state="${state}" role="row"><span class="co-name" role="cell"><b>${esc(name)}</b><small>${esc(meta)}</small></span>${price === null ? '' : `<span class="co-price" role="cell">${cartPriceHtml(price, price / 0.0004)}</span>`}<span class="co-tags" role="cell">${chip}${reason ? `<span class="co-reason">${esc(reason)}</span>` : ''}</span></div>`;
}
function coCurrency(label, total, balance, { pressed = false, disabled = false, why = '' } = {}) {
  return `<span class="co-cur"><button type="button" class="ceres-btn ceres-btn--choice co-cur-btn" data-control-density="comfortable" aria-pressed="${pressed}"${disabled ? ' disabled' : ''}><span class="ceres-btn__check" aria-hidden="true"></span><span class="co-cur-label">Pay with ${label === 'DEC' ? 'DEC' : 'Credits'}</span><span class="co-cur-total">${coAmount(label, total)}</span></button><span class="co-cur-bal">You have ${coAmount(label, balance)}</span>${why ? `<span class="co-cur-why" data-state="danger">${esc(why)}</span>` : ''}</span>`;
}
function coAmount(currency, text) {
  return `<span class="co-amount">${esc(text)}<img class="cm-dec-icon co-cur-icon" src="${currency === 'DEC' ? DEC_ICON : CREDITS_ICON}" alt="" width="16" height="16"></span>`;
}
function checkoutSection() {
  const walletFoot = `<button type="button" class="ceres-btn ceres-btn--secondary co-wallet-btn" data-control-density="comfortable" data-control-mode="icon" aria-label="Wallet"><img src="${DEC_ICON}" alt="" width="20" height="20"></button>`;
  const review = `<section class="co-paybar" aria-label="Total and payment"><div class="co-pay-head"><span class="co-pay-total">Total · ${coAmount('DEC', '10,250 DEC')} · $4.10</span><button type="button" class="ceres-btn ceres-btn--primary co-pay-go" data-control-density="comfortable">Continue</button></div><div class="co-cur-row" role="group" aria-label="Pay with">${coCurrency('DEC', '10,250 DEC', '42,000 DEC', { pressed: true })}${coCurrency('CREDITS', '4,100 credits', '900 credits', { disabled: true, why: 'Not enough Credits. Short by 3,200 credits.' })}</div></section>`
    + '<p class="cm-note co-note" data-state="sold" role="status">One listing is no longer for sale and is struck out. The total below is for the rest.</p>'
    + '<p class="cm-note co-note" data-state="warn" role="status">One price rose since you added it (amber). Check the new total.</p>'
    + '<p class="cm-note co-note" data-state="ok" role="status">One price fell since you added it.</p>'
    + `<section class="cm-section"><h4>5 listings <small>3 can be bought</small></h4><div class="co-rows" role="table">`
    + coRow('ok', 'Goblin Shaman', 'Lv 6 · 60 CC · Regular', 0.45)
    + coRow('rise', 'Frost Giant', 'Lv 5 · 33 CC · Regular', 1.95, `<span class="cm-chip is-warn co-chip">Price rose · was ${esc(usd(1.85))}</span>`)
    + coRow('drop', 'Kobold Miner', 'Lv 10 · 200 CC · Regular', 1.7, `<span class="cm-chip is-ok co-chip">Price fell · was ${esc(usd(1.9))}</span>`)
    + coRow('sold', 'Goblin Shaman', 'Lv 5 · 40 CC · Regular', 0.31, '<span class="cm-chip is-danger co-chip">Sold</span>', 'Bought by another player.')
    + coRow('own', 'Serpent of the Flame', 'Lv 8 · 115 CC · Regular', 7.9, '<span class="cm-chip is-warn co-chip">Your own listing</span>', 'You cannot buy your own listing.')
    + '</div></section>';
  const reviewFoot = walletFoot + '<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable">Close</button>';
  const confirm = `<section class="co-paybar co-confirm-pay" aria-label="Total and payment"><span class="co-pay-total co-sum">Buy 3 listings for ${coAmount('DEC', '10,250 DEC')} ($4.10)</span><p class="co-pay-balance">You have ${coAmount('DEC', '42,000 DEC')}</p><p class="co-key">Pay with DEC. Your wallet asks for your <b>Active</b> key.</p><p class="cm-note co-undo" data-state="sold" role="note">A market purchase cannot be undone. As soon as the game accepts it, the payment leaves your account and the cards are yours.</p><div class="co-pay-head"><button type="button" class="ceres-btn ceres-btn--primary co-sign" data-control-density="comfortable">Buy now with your wallet</button></div></section>`;
  const confirmFoot = walletFoot + '<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable">Back</button>';
  const outRow = (tone, name, meta, decPrice, label = '') => `<div class="co-row co-outcome-row" data-state="out-${tone}" role="row"><span class="co-name" role="cell"><b>${esc(name)}</b><small>${esc(meta)}</small></span><span class="co-price" role="cell"><span class="co-copy-dec">${coAmount('DEC', decPrice)}</span></span><span class="co-tags" role="cell">${label ? `<span class="co-outcome-label is-${tone}">${esc(label)}</span>` : '<span class="sr-only">Bought</span>'}</span></div>`;
  const outcome = '<section class="cm-section co-group" data-state="warn"><h4>Max Kobold Miner <small>3 copies · 2 signatures: buy, then combine.</small></h4><p class="cm-note co-group-line" data-state="warn" role="status">All copies are bought. Sign the combine to make one max-level card (Posting key).</p><div class="co-group-actions"><button type="button" class="ceres-btn ceres-btn--primary co-combine-btn" data-control-density="comfortable">Sign the combine with your wallet</button><button type="button" class="ceres-btn ceres-btn--secondary co-combine-btn" data-control-density="comfortable">Not now</button></div></section>'
    + '<section class="cm-section co-tx"><p class="cm-note co-result" data-state="ok" role="status">Bought 2 of 2. Paid $2.44.</p><details class="co-outcome-detail"><summary>2 bought copies · item prices</summary><div class="co-rows" role="table">'
    + outRow('ok', 'Goblin Shaman', 'Lv 6 · 60 CC', '1,125 DEC') + outRow('ok', 'Frost Giant', 'Lv 5 · 33 CC', '4,975 DEC') + '</div></details></section>'
    + '<section class="cm-section co-tx"><p class="cm-note co-result" data-state="sold" role="status">The game refused this purchase. Nothing was bought.</p><div class="co-rows" role="table">'
    + outRow('danger', 'Kobold Miner', 'Lv 10 · 200 CC', '4,150 DEC', 'Not bought') + '</div></section>';
  const outcomeFoot = '<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable">Close (keeps checking)</button>';
  const loading = '<div class="cb-loading" role="status"><span class="spinner" aria-hidden="true"></span><span>Checking every listing in your cart…</span></div>';
  return specimen('Review: notes (sold red, rose amber, fell green), rows ok / rise / drop / sold / own, currency choice chosen and disabled', panel('Checkout', review, reviewFoot, 'co-panel'), { wide: true, note: 'Mirror of card-checkout.js reviewHtml (public, public).' })
    + specimen('Confirm', panel('Checkout', confirm, confirmFoot, 'co-panel'), { wide: true })
    + specimen('Outcome: bought, waiting, Buy & max combine ready, combine not possible', panel('Your purchase', outcome, outcomeFoot, 'co-panel'), { wide: true, note: 'public' })
    + specimen('Loading', panel('Checkout', loading, '<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable">Close</button>', 'co-panel'), { wide: true });
}

function loadingSection() {
  const spinners = row('<span class="spinner" aria-hidden="true"></span><span class="spinner spinner-sm" aria-hidden="true"></span><div class="cb-loading" role="status"><span class="spinner" aria-hidden="true"></span><span>Reading your cards…</span></div>');
  const skeletonOn = new URLSearchParams(location.search).get('skeleton') === '1';
  const skeletons = skeletonOn
    ? `<section class="candidate-list is-card-grid cb-grid cm-grid sg-grid" aria-hidden="true">${cmSkeletonHtml(tileHtml).repeat(5)}</section>`
    : '<p class="sg-note">The skeleton tiles are shown with <a href="?skeleton=1#sg-loading">?skeleton=1</a>: a skeleton on the page holds the browser rig\'s "no placeholder" ready check, so it is off by default.</p>';
  return specimen('One global spinner (public): .spinner, .spinner-sm, .cb-loading', spinners, { wide: true })
    + specimen('Market skeleton tiles (public: the real tile with an empty listing, cmSkeletonHtml)', skeletons, { wide: true });
}

function tooltipSection() {
  const brief = `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact"${tipAttrs({ title: 'Back to the plots' })}>Brief</button>`;
  const extended = `<button type="button" class="ceres-btn ceres-btn--secondary sg-tip-demo" data-control-density="compact"${tipAttrs({ title: 'Add maxed card', body: 'Adds one max-level card of this print to your cart: a single listing that is already maxed when that is cheapest, otherwise the cheapest listings whose CC adds up to max level.', foot: 'Your own listings are never added.' })}>Extended</button>`;
  const items = `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact"${tipAttrs({ title: 'Land abilities', items: [{ icon: 'abilities/ability_bountiful-grain.png', name: 'Bountiful Grain', effect: '+10% production on a Grain worksite' }, { icon: 'abilities/ability_rationing.png', name: 'Rationing', effect: '−10% Grain upkeep' }] })}>Item list</button>`;
  return specimen('W-4 one engine (#ceres-tooltip): brief, extended (title, body, foot), item list, ability icons — point at each', row(brief + extended + items + abilityRow(CARD.lord)), { wide: true, note: 'tooltip.js itself; keyboard focus opens a tooltip too (Tab to "Extended").' });
}

function bannerSection() {
  const banner = '<div class="sg-fixed-frame"><div class="reload-banner" role="button" tabindex="0">New version deployed — click to load it</div></div>';
  const marker = '<div class="sg-marker-frame"><span class="board-logo"><img class="board-logo-img" src="/static/assets/logos/ceres-logo-alchemical-header.png" width="225" height="150" alt="Ceres"><span class="board-version">v0.31.0</span><span class="ceres-local-marker" data-tip="Local — unreleased changes, not on Atlas" data-tip-kind="explain"><span class="ceres-local-marker-id">+local.1a2b3c4d</span> <span class="ceres-local-marker-text">Local — unreleased changes, not on Atlas</span><span class="ceres-local-marker-short">Local</span></span></span></div>';
  return specimen('New-version banner (.reload-banner, public), shown in a frame here instead of at the top of the page', banner, { wide: true, note: 'The poller (version-banner.js) is not loaded on this page: it polls the server for a new version.' })
    + specimen('Version tag and local marker (public): amber, right of the logo; a dot on the phone tier', marker, { wide: true });
}

// the header's glass menu buttons and the Plots | Cards switcher. `.ceres-btn--glass` is the same recipe
// every header button wears by context (buttons.css); the icons come from the one header icon module (header-icons.js).
function headerSection() {
  const ic = (name) => (globalThis.ceresHeaderIcons?.ICONS?.[name] || '');
  const half = (name, label, current) => `<a class="ceres-btn ceres-btn--choice ceres-btn--glass" data-control-density="header" data-control-mode="icon" href="#sg-header"${current ? ' aria-current="page"' : ''} aria-label="${label}" data-tip="${label}">${ic(name)}</a>`;
  const sw = (current) => `<nav class="ceres-btn-group ceres-page-switch" data-control-density="header" aria-label="Plots or cards">${half('plots', 'Plots', current === 'plots')}${half('cards', 'Cards', current === 'cards')}</nav>`;
  const glass = row(`<button type="button" class="ceres-btn ceres-btn--primary ceres-btn--glass" data-control-density="header" data-control-mode="icon" aria-label="Harvest" data-tip="Harvest"><svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true"><path class="f" d="M9 15.8C5.4 11.6 7 4.7 13.5 3.3c4.7-1 8 2.2 7.2 6.1-1.4-2.4-4-3.3-6.5-2.5-3.6 1.1-5 5-3.5 8.3z"/><path class="d" d="M10.9 13.6c-.9-2.9.7-6.2 3.6-7.1"/><path class="a h" d="M9.4 15.3 3.6 21.1"/></svg></button>`
    + `<button type="button" class="ceres-btn ceres-btn--choice ceres-btn--glass" data-control-density="header" data-control-mode="icon" aria-label="My cards" data-tip="My cards" aria-pressed="true">${ic('cards')}</button>`
    + `<button type="button" class="ceres-btn ceres-btn--choice ceres-btn--glass" data-control-density="header" data-control-mode="icon" aria-label="Market" data-tip="Market" aria-pressed="false">${ic('market')}</button>`
    + `<button type="button" class="ceres-btn ceres-btn--primary ceres-btn--glass" data-control-density="header" disabled>Disabled</button>`);
  return specimen('Glass menu buttons (D-2: tint + light edge, no blur): rest, pressed in the accent, disabled; hover is live', glass, { note: 'Every header button is glass by context; .ceres-btn--glass names the recipe elsewhere.' })
    + specimen('Plots | Cards switcher (I-4, D-3): the current page pressed in the accent; always the last header control', row(sw('plots') + sw('cards')), { note: 'Icon only: aria-label and tooltip say Plots / Cards; 44 px halves on phones.' });
}

const SECTIONS = [
  { id: 'sg-colour', title: 'Colour tokens', rules: ['C-1', 'C-2', 'C-3', 'C-4', 'C-6', 'C-7'], classes: ['var(--token) only', '.ceres-region-plaque.territory-*', '--terrain-*'], render: colourSection },
  { id: 'sg-type', title: 'Typography', rules: ['T-1', 'T-2', 'T-3', 'T-4', 'T-5'], classes: ['--font-body', '--font-display', '--font-mono', '--step-2'], render: typeSection },
  { id: 'sg-buttons', title: 'Buttons', rules: ['K-1', 'K-2', 'K-6', 'K-6a', 'K-7', 'M-1', 'M-3', 'public'], classes: ['.ceres-btn', '.ceres-btn--primary', '.ceres-btn--secondary', '.ceres-btn--choice', '[data-control-density]', '[data-control-case]', '[data-control-mode="icon"]', '.ceres-tabs', '.slot-remove'], render: buttonSection },
  { id: 'sg-filters', title: 'Filter controls and the custom select', rules: ['K-2', 'K-3', 'K-4', 'K-5', 'K-8', 'L-1', 'L-4'], classes: ['.controls-row', '.board-filter-field', '.ceres-wide-input', '.csel-wrap', '.csel-trigger', '.csel-value', '.board-filter-pill', '.controls-sep', '.board-clear-filters'], render: filterSection },
  { id: 'sg-glass', title: 'Glass surfaces', rules: ['Desktop board glass (public, public, public, public, public)', 'Plot table (public)', 'L-1', 'M-4', 'Q-11'], classes: ['header.board-header', '.controls-row', 'table.plot-table', '#plot-tbody tr.plot-row', 'td.plot-cell.territory-*', 'td.terrain-cell.terrain-*'], render: glassSection },
  { id: 'sg-plot', title: 'Plot worker tiles', rules: ['Tile V2 (public)', 'I-2', 'L-3', 'L-5', 'E-3', 'M-2', 'public', 'public', 'public'], classes: ['.editor-band', '.slot-grid', '.slot-tile-cell', '.slot-tile', '.slot-kin-badge.is-paired/.is-solo/.is-companion', '.slot-ability-row', '.ability-icon', '.slot-wait-badge', '.is-stage-ready/-blocked/-elsewhere', '.is-empty'], render: plotSection },
  { id: 'sg-cards', title: 'Cards tile — My cards', rules: ['public', 'public', 'public', 'public', 'public', 'public', 'T-4', 'L-3'], classes: ['.cb-grid', '.cb-tile', '.cb-art', '.cb-art-tags', '.foil-badge', '.cb-maxed', '.slot-wait-badge.cb-onland', '.ch-plot-badge.ceres-region-plaque', '.candidate-status-badge.is-danger', '.cb-level', '.cb-cc', '.candidate-grid-metric.cb-metric-pp/.cb-metric-cc'], render: cardsSection },
  { id: 'sg-market', title: 'Market tile', rules: ['public', 'public', 'public', 'public', 'public', 'public', 'public', 'C-4'], classes: ['.cm-tile', '.is-in-cart', '.cm-price-badges', '.cm-price-box-usd/-dec', '.cm-tags', '.cm-tag.cm-combined/.cm-own/.cm-incart/.cm-seq', '.cb-level-tag', '.cm-metric-rate.is-top/.is-good/.is-fair/.is-dear', '.cm-add-max'], render: marketSection },
  { id: 'sg-menu', title: 'Card menu', rules: ['public', 'public', 'public', 'public', 'public', 'public', 'public', 'public', 'W-1', 'C-4'], classes: ['.cm-dialog-panel', '.cm-menu-head', '.cm-bundle-grid', '.cm-bundle-btn(.is-in-cart)', '.cm-copy-table', '.cm-copy.is-cheap', '[data-state="in-cart"/"own"/"over"]', '.cm-chip.is-ok/.is-warn', '.cm-note'], render: menuSection },
  { id: 'sg-cart', title: 'Cart panel', rules: ['public', 'public', 'public', 'public', 'W-1'], classes: ['.cm-cart-group', '.cm-group-bundle', '.cm-group-total', '.cm-buymax', '.cm-row.cm-cart-item', '.cm-chip', '.cm-note[data-state="sold"]', '.cm-foot-total', '.cm-checkout'], render: cartSection },
  { id: 'sg-checkout', title: 'Checkout dialog states', rules: ['public', 'public', 'public', 'K-6a', 'W-1', 'C-4'], classes: ['.co-panel', '.co-confirm-pay', '.co-outcome-detail', '.co-copy-dec', '.co-row[data-state="ok"/"rise"/"drop"/"sold"/"own"/"out-*"]', '.co-cur-btn', '.co-cur-why', '.co-confirm', '.co-undo', '.co-tx', '.co-group', '.co-status'], render: checkoutSection },
  { id: 'sg-loading', title: 'Loading spinner and skeleton tiles', rules: ['One global spinner (public)', 'M-3', 'L-6', 'public', 'public'], classes: ['.spinner', '.spinner-sm', '.cb-loading', '.cm-skeleton'], render: loadingSection },
  { id: 'sg-tooltips', title: 'Tooltips', rules: ['W-4', 'Normal and extended tooltips'], classes: ['#ceres-tooltip', '.tip-inner', '[data-tip]', '[data-tip-body]', '[data-tip-foot]', '[data-tip-items]'], render: tooltipSection },
  { id: 'sg-banner', title: 'Version banner and local marker', rules: ['public', 'public', 'public'], classes: ['.reload-banner', '.board-version', '.ceres-local-marker', '.ceres-local-marker-short'], render: bannerSection },
  { id: 'sg-header', title: 'Header: glass menu buttons and the Plots | Cards switcher', rules: ['I-4', 'D-2', 'D-3', 'K-1', 'M-4', 'public', 'public'], classes: ['.ceres-btn--glass', 'header.board-header .ceres-btn', '.ceres-page-switch', '[aria-current="page"]', '[data-header-icon]'], render: headerSection },
];

// ── page ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
function currentMode() {
  const t = new URLSearchParams(location.search).get('theme');
  if (t === 'all') return 'all';
  return THEMES.includes(t) ? t : document.documentElement.getAttribute('data-theme') || 'dark';
}

function sectionHtml(section, mode) {
  let inner;
  try {
    inner = mode === 'all'
      // Each pane is its own .card-browser scope, so the page's token-derived glass (--cb-glass, --cb-panel-glass, ...) is
      // recomputed from that pane's theme tokens by card-browser.css itself.
      ? `<div class="sg-quad">${THEMES.map((t) => `<div class="sg-pane card-browser" data-theme="${t}"><div class="sg-pane-label">${THEME_LABEL[t]}</div>${section.render()}</div>`).join('')}</div>`
      : `<div class="sg-single">${section.render()}</div>`;
  } catch (error) {
    // A builder that changed shape shows here, never as a blank page.
    inner = `<p class="cm-note" data-state="sold" role="status">This section could not be drawn: ${esc(error instanceof Error ? error.message : String(error))}</p>`;
  }
  const rules = section.rules.map((r) => `<span class="sg-rule">${esc(r)}</span>`).join('');
  const classes = section.classes.map((c) => `<code>${esc(c)}</code>`).join(' ');
  return `<section class="sg-section" id="${section.id}" aria-labelledby="${section.id}-h"><header class="sg-section-head"><h2 id="${section.id}-h">${esc(section.title)}</h2><div class="sg-meta"><span class="sg-meta-label">Rules (${esc(STANDARD)})</span>${rules}</div><div class="sg-meta"><span class="sg-meta-label">Classes</span>${classes}</div></header>${inner}</section>`;
}

function fillTokenValues(root) {
  root.querySelectorAll('[data-sg-token]').forEach((el) => {
    const swatch = el.parentElement?.querySelector('.sg-swatch');
    if (swatch) el.textContent = getComputedStyle(swatch).backgroundColor;
  });
}

function initSelects(root) {
  root.querySelectorAll('select[data-sg-select]').forEach((el) => {
    const kind = el.dataset.sgSelect;
    const decorate = kind === 'multi-rarity' ? (v) => (v ? { icon: rarityGem(v) } : null)
      : kind === 'multi-splinter' ? (v) => (v ? { icon: splinterIcon(v) } : null) : null;
    const name = { 'multi-rarity': 'Rarity', 'multi-splinter': 'Splinter', 'multi-ability': 'Ability' }[kind];
    initCustomSelect(el, { ariaLabel: name ? `${name} filter` : 'Sample sort', ...(name ? { emptyLabel: name } : {}), multiple: kind !== 'single', ...(decorate ? { decorateOption: decorate } : {}) });
  });
}

function syncNarrow(root) {
  // As card-cart.js / card-checkout.js syncNarrow: the dialog's narrow layout follows the panel's own width.
  root.querySelectorAll('.cm-dialog-panel').forEach((p) => p.classList.toggle('is-narrow', p.getBoundingClientRect().width < 640));
}

function render() {
  const mode = currentMode();
  const html = document.documentElement;
  if (mode !== 'all') html.setAttribute('data-theme', mode);
  document.body.classList.toggle('sg-all', mode === 'all');
  document.querySelectorAll('[data-sg-theme]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.sgTheme === mode)));
  seedCart();
  const box = document.getElementById('sg-sections');
  box.innerHTML = SECTIONS.map((s) => sectionHtml(s, mode)).join('');
  initSelects(box);
  syncNarrow(box);
  bindCountdowns();
  requestAnimationFrame(() => fillTokenValues(box));
}

function setMode(mode) {
  const url = new URL(location.href);
  url.searchParams.set('theme', mode);
  history.replaceState(null, '', url);
  render();
}

function init() {
  document.addEventListener('error', onArtError, true);
  document.addEventListener('load', onArtLoad, true);
  initTooltips();
  const jump = document.getElementById('sg-jump');
  if (jump) {
    jump.insertAdjacentHTML('beforeend', SECTIONS.map((s) => `<option value="${s.id}">${esc(s.title)}</option>`).join(''));
    initCustomSelect(jump, { ariaLabel: 'Jump to section' });
    jump.addEventListener('change', () => { if (jump.value) document.getElementById(jump.value)?.scrollIntoView({ block: 'start' }); });
  }
  document.querySelectorAll('[data-sg-theme]').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.sgTheme)));
  window.addEventListener('resize', () => syncNarrow(document));
  render();
  const focus = document.querySelector('.sg-focus-demo');
  if (focus) { try { focus.focus({ preventScroll: true, focusVisible: true }); } catch (_) { focus.focus({ preventScroll: true }); } }
  document.body.dataset.viewReady = 'true';  // the rig's page: view waits for this
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
