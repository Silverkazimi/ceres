// card-purchases.js -- "My purchases" for the Cards page. Read only: nothing here buys, signs, stores or transfers.
// Owner (): "do we log the buys? It is all on the blockchain anyways" / "it could show features lie avergae PP/$ and
// other useful stats" -> "Yes, on demand (Recommended)". Nothing is read until the maintainer presses "Load my purchases"
// (: no automatic or background history read); a window button after that is a new press. The server reads the game's
// own market history (GET /api/cards/purchases, ceres/purchase_history.py) and computes every figure; this file only draws it.
// No uid, transaction id or seller is in the answer (no-UIDs rule): a purchase is its card name, level, CC and date.
// Mount: initPurchases(container, { player: () => name }) -- see work/notes/-purchase-history/integration.md.
import { tipAttrs } from './tooltip.js';
import { cmRateTier } from './card-market.js';

export const CP_WINDOWS = [7, 14, 30];
export const CP_DEFAULT_DAYS = 30;
const DEC_ICON = '/static/assets/land/token-dec.png';  // icons.js resource 'dec'
const SET_LABELS = { alpha: 'Alpha', beta: 'Beta', untamed: 'Untamed', chaos: 'Chaos Legion', rebellion: 'Rebellion', conclave: 'Conclave Arcana',
  gladius: 'Gladius', foundations: 'Foundations', land: 'Land', other: 'Other' };
const FOIL_LABELS = { 0: 'Regular', 1: 'Gold', 2: 'Gold Arcane', 3: 'Black', 4: 'Black Arcane' };
const TIER_WORDS = { 'is-top': 'at or above today\'s going rate', 'is-good': 'up to 20 % more per PP than today\'s going rate',
  'is-fair': '20–40 % more per PP than today\'s going rate', 'is-dear': 'more than 40 % more per PP than today\'s going rate' };
const COLUMNS = [
  { key: 'date', label: 'Date' }, { key: 'name', label: 'Card' }, { key: 'bcx', label: 'Level · CC' }, { key: 'pp', label: 'PP', num: true },
  { key: 'price_usd', label: 'Price', num: true }, { key: 'pp_per_usd', label: 'PP/$', num: true },
];

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const finite = (v) => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
const cap = (s) => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : '');

// ── pure helpers (tests/ceres/test_ir9255_purchase_history.py) ───────────────
export function cpUrl(player, days) {
  const params = new URLSearchParams();
  if (player) params.set('player', player);
  params.set('days', String(CP_WINDOWS.includes(Number(days)) ? Number(days) : CP_DEFAULT_DAYS));
  return `/api/cards/purchases?${params}`;
}
export function cpUsd(v) {
  if (!finite(v)) return '—';
  const n = Number(v);
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: n < 0.1 && n > 0 ? 3 : 2, maximumFractionDigits: n < 0.1 && n > 0 ? 3 : 2 })}`;
}
export function cpNum(v, digits = 0) {
  return finite(v) ? Number(v).toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 }) : '—';
}
export function cpRate(v) {
  if (!finite(v)) return '—';
  const n = Number(v);
  return cpNum(n, n >= 100 ? 0 : n >= 10 ? 1 : 2);
}
export function cpDate(iso) {
  return typeof iso === 'string' && iso.length >= 10 ? iso.slice(0, 10) : '—';
}
export function cpMonth(key) {
  const m = /^(\d{4})-(\d{2})$/.exec(String(key || ''));
  if (!m) return 'Unknown';
  return `${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][Number(m[2]) - 1]} ${m[1]}`;
}
// Unknown values always go last, whichever direction; ties keep the newest first.
export function cpSorted(purchases, key = 'date', dir = 'desc') {
  const sign = dir === 'asc' ? 1 : -1;
  const value = (p) => (key === 'name' ? String(p.name || '').toLowerCase() : key === 'date' ? String(p.date || '') : finite(p[key]) ? Number(p[key]) : null);
  return [...(purchases || [])].map((p, i) => ({ p, i })).sort((a, b) => {
    const va = value(a.p); const vb = value(b.p);
    if (va === null || va === '') return vb === null || vb === '' ? a.i - b.i : 1;
    if (vb === null || vb === '') return -1;
    if (va < vb) return -sign;
    if (va > vb) return sign;
    return a.i - b.i;
  }).map((x) => x.p);
}
export function cpVsText(ratio) {
  if (!finite(ratio)) return '';
  const r = Number(ratio);
  if (Math.abs(r - 1) < 0.005) return 'the same as today\'s going rate';
  return r > 1 ? `${cpNum((r - 1) * 100)} % more PP per $ than today's going rate` : `${cpNum((1 - r) * 100)} % less PP per $ than today's going rate`;
}

function decHtml(amount) {
  return `<span class="cp-dec">${esc(cpNum(amount, amount < 10 ? 2 : 0))}<img class="cp-dec-icon" src="${DEC_ICON}" alt="DEC" width="16" height="16"></span>`;
}
function paidHtml(amount, currency) {
  if (!finite(amount) || !currency) return '';
  if (currency === 'DEC') return decHtml(Number(amount));
  return `<span class="cp-paid-other">${esc(cpNum(amount, 2))} ${esc(currency === 'CREDITS' ? 'Credits' : currency)}</span>`;
}
function printMeta(p) {
  return [SET_LABELS[p.card_set] || cap(p.card_set), FOIL_LABELS[p.foil] && p.foil ? FOIL_LABELS[p.foil] : '', cap(p.rarity)].filter(Boolean).join(' · ');
}
function rateHtml(value, tier, extra = '') {
  tier = cmRateTier(Number(value), Number(cp.body?.going?.pp_per_usd))?.cls || tier;
  return `<span class="cp-rate${tier ? ` ${esc(tier)}` : ''}"${tier ? tipAttrs({ title: 'PP/$', body: `Land base PP ÷ price in $: ${TIER_WORDS[tier]}.${extra}` }) : ''}>${esc(cpRate(value))}</span>`;
}

// ── state ────────────────────────────────────────────────────────────────────
const cp = { root: null, deps: null, days: CP_DEFAULT_DAYS, body: null, loading: false, error: '', sort: 'date', dir: 'desc', seq: 0 };
export const _cpForTests = cp;

function statHtml(label, value, sub = '', cls = '', tip = null) {
  return `<div class="cp-stat${cls ? ` ${cls}` : ''}"${tip ? tipAttrs(tip) : ''}><span class="cp-stat-label">${esc(label)}</span><span class="cp-stat-value">${value}</span>${sub ? `<span class="cp-stat-sub">${sub}</span>` : ''}</div>`;
}

export function cpSummaryHtml(body) {
  const s = body.summary || {};
  const going = body.going || {};
  const paid = Object.entries(s.paid || {}).map(([cur, amt]) => paidHtml(amt, cur)).join('');
  const stats = [
    statHtml('Spent', esc(cpUsd(s.usd)), `${paid}<span>${esc(cpNum(s.count))} ${s.count === 1 ? 'card' : 'cards'} in ${esc(cpNum(s.transactions))} ${s.transactions === 1 ? 'purchase' : 'purchases'}</span>`),
    statHtml('PP bought', `${esc(cpNum(s.pp))}<small>PP</small>`, esc(`${cpNum(s.land_count)} Land ${s.land_count === 1 ? 'card' : 'cards'}${s.count > s.land_count ? ` · ${cpNum(s.count - s.land_count)} without Land PP` : ''}`),
      '', { title: 'PP bought', body: 'Land base PP of every Land card at the CC it was bought with. Cards that cannot work on Land count in Spent only.' }),
    statHtml('Average PP/$', `${rateHtml(s.pp_per_usd, s.tier)}<span class="cp-going"${tipAttrs({ title: 'Going rate', body: `${going.method ? `${cap(going.method)}. ` : ''}The game keeps no historic rate, so every purchase is compared with today's.` })}>Going rate today <strong>${esc(cpRate(going.pp_per_usd))} PP/$</strong></span>`, `<span>${esc(s.vs_going ? cpVsText(s.vs_going) : 'No going rate today')}</span><span>Today’s rate, not the rate on the day you bought</span>`, 'cp-stat-rate',
      { title: 'Average PP/$', body: 'PP bought ÷ dollars paid for those Land cards (weighted by price, not an average of ratios).' }),
  ];
  return `<div class="cp-stats">${stats.join('')}</div>`;
}

export function cpMoreStatsHtml(body) {
  const s = body.summary || {};
  const stats = [];
  const other = s.other_purchases;
  if (other) stats.push(statHtml('Other purchases', esc(cpUsd(other.usd)),
    `${finite(other.dec) ? decHtml(other.dec) : ''}<span>${esc(cpNum(other.count))} packs, plots or items · Separate feed may omit purchases</span>`, '',
    { title: 'Other purchases', body: `Money spent on non-card purchases shown by the game’s separate market activity feed. No PP is counted. ${other.note || ''}` }));
  if (finite(s.value_now_usd)) {
    const gain = Number(s.value_now_usd) - Number(s.value_now_paid_usd);
    stats.push(statHtml('Value today', esc(cpUsd(s.value_now_usd)), `<span class="${gain >= 0 ? 'cp-up' : 'cp-down'}">${esc(gain >= 0 ? '+' : '−')}${esc(cpUsd(Math.abs(gain)))}</span><span>vs ${esc(cpUsd(s.value_now_paid_usd))} paid · ${esc(cpNum(s.value_now_count))} ${s.value_now_count === 1 ? 'card' : 'cards'}</span>`, '',
      { title: 'Value today', body: 'Each card\'s CC at its print\'s cheapest price per CC on the market now, next to what you paid for the same cards. Cards not on sale today are left out of both sides.' }));
  }
  if (s.looked_up) {
    stats.push(statHtml('Combined later', `${esc(cpNum(s.combined))}<small>of ${esc(cpNum(s.looked_up))}</small>`, esc(s.not_owned ? `${cpNum(s.not_owned)} no longer yours` : 'All still yours'), '',
      { title: 'Combined later', body: 'Bought cards that are now part of a higher-CC card: combined into another copy, or other copies were combined into them.' }));
  }
  return stats.length ? `<div class="cp-stats cp-stats-secondary">${stats.join('')}</div>` : '';
}

function groupTable(title, rows, label) {
  if (!rows || !rows.length) return '';
  const body = rows.map((g) => `<tr><th scope="row">${esc(label(g.key))}</th><td>${esc(cpNum(g.count))}</td><td>${esc(cpUsd(g.usd))}</td><td>${esc(cpNum(g.pp))}</td><td>${esc(cpRate(g.pp_per_usd))}</td></tr>`).join('');
  return `<div class="cp-panel"><h3 class="cp-panel-title">${esc(title)}</h3><table class="cp-mini"><thead><tr><th scope="col"></th><th scope="col">Cards</th><th scope="col">Spent</th><th scope="col">PP</th><th scope="col">PP/$</th></tr></thead><tbody>${body}</tbody></table></div>`;
}
function buysPanel(title, rows) {
  if (!rows || !rows.length) return '';
  const items = rows.map((p) => `<li><span class="cp-buy-name">${esc(p.name)}<small>${esc(printMeta(p))} · Lv ${esc(p.level ?? '—')} · ${esc(cpDate(p.date))}</small></span><span class="cp-buy-price">${esc(cpUsd(p.price_usd))}</span>${rateHtml(p.pp_per_usd, p.tier)}</li>`).join('');
  return `<div class="cp-panel"><h3 class="cp-panel-title">${esc(title)}</h3><ol class="cp-buys">${items}</ol></div>`;
}
export function cpBreakdownHtml(body) {
  const s = body.summary || {};
  return `<div class="cp-panels">${buysPanel('Best buys by PP/$', s.best)}${buysPanel('Weakest buys by PP/$', s.worst)}`
    + `${groupTable('By month', s.by_month, cpMonth)}${groupTable('By rarity', s.by_rarity, (k) => cap(k) || 'Unknown')}`
    + `${groupTable('By edition', s.by_edition, (k) => SET_LABELS[k] || cap(k) || 'Unknown')}</div>`;
}

export function cpRowHtml(p) {
  const tags = [p.combined ? '<span class="cp-tag">Combined later</span>' : '', p.owned === false ? '<span class="cp-tag">No longer yours</span>' : '',
    !p.land ? `<span class="cp-tag cp-tag-dim"${tipAttrs({ title: 'No Land PP', body: 'This card cannot work on Land (a tower, soulbound or no-PP card), so it counts in Spent only.' })}>No Land PP</span>` : ''].join('');
  const vs = p.vs_going ? ` ${cpVsText(p.vs_going)}.` : '';
  // The Cards page supplies its existing art candidates; its delegated image-error handler
  // advances the same cache/CDN candidate list as every other card image on the page.
  const art = cp.deps?.artCandidates?.(p) || [];
  const portrait = art.length
    ? `<span class="cp-art" aria-hidden="true"><img src="${esc(art[0])}" alt="" loading="eager" decoding="async" data-cb-art="${esc(JSON.stringify(art))}" data-cb-art-index="0"></span>`
    : `<span class="cp-art cp-art-empty" aria-hidden="true"></span>`;
  return `<div class="cp-row" role="row">${portrait}<span class="cp-c-date" role="cell">${esc(cpDate(p.date))}</span>`
    + `<span class="cp-c-card" role="cell"><span class="cp-name">${Number(p.quantity) > 1 ? `<strong class="cp-quantity">${esc(cpNum(p.quantity))} ×</strong> ` : ''}${esc(p.name)}</span><small>${esc(printMeta(p))}</small>${tags ? `<span class="cp-tags">${tags}</span>` : ''}</span>`
    + `<span class="cp-c-level" role="cell">${p.level ? `Lv ${esc(p.level)}` : '—'}<small>${finite(p.bcx) ? `${esc(cpNum(p.bcx))} CC` : ''}</small></span>`
    + `<span class="cp-c-pp" role="cell"${p.pp_source ? tipAttrs({ title: 'Land base PP', body: p.pp_source === 'game' ? 'As the game reports it for this card.' : 'Derived: max-level PP × CC ÷ max CC, at the CC it was bought with.' }) : ''}>${esc(cpNum(p.pp))}</span>`
    + `<span class="cp-c-price" role="cell"><span class="cp-usd">${esc(cpUsd(p.price_usd))}</span>${paidHtml(p.paid, p.paid_currency)}</span>`
    + `<span class="cp-c-rate" role="cell">${rateHtml(p.pp_per_usd, p.tier, vs)}</span></div>`;
}

export function cpListHtml(purchases, sort, dir) {
  const head = COLUMNS.map((c) => {
    const on = c.key === sort;
    return `<span role="columnheader" class="cp-h-${c.key}"${on ? ` aria-sort="${dir === 'asc' ? 'ascending' : 'descending'}"` : ''}><button type="button" class="ceres-btn ceres-btn--choice cp-sort" data-control-density="dense" aria-pressed="${on}" data-cp-sort="${c.key}">${esc(c.label)}${on ? `<span class="cp-sort-dir${dir === 'asc' ? ' is-asc' : ''}" aria-hidden="true"></span>` : ''}</button></span>`;
  }).join('');
  const rows = cpSorted(purchases, sort, dir).map(cpRowHtml).join('');
  return `<div class="cp-list" role="table" aria-label="Purchases"><div class="cp-row cp-head" role="row">${head}</div>${rows}</div>`;
}

function barHtml() {
  const windows = CP_WINDOWS.map((d) => `<button type="button" class="ceres-btn ceres-btn--choice" data-control-density="compact" data-cp-days="${d}" aria-pressed="${cp.days === d}"${cp.loading ? ' disabled' : ''}>${d} days</button>`).join('');
  const label = cp.body ? 'Read again' : 'Load my purchases';
  const read = cp.body ? `Read ${esc(String(cp.body.read_at || '').slice(11, 16))} UTC · ${esc(cp.body.coverage?.note || '')}` : 'Nothing is read until you press the button. The game\'s history reaches back about 30 days.';
  return `<div class="cp-bar"><h1 class="cp-bar-title">My purchases</h1><button type="button" class="ceres-btn ceres-btn--primary" data-control-density="compact" data-cp-action="load"${cp.loading ? ' disabled aria-busy="true"' : ''}${tipAttrs({ title: label, body: 'Reads your card purchases from the game\'s own market history (the last 30 days at most) and works out the stats. Nothing is stored.' })}>${label}</button>`
    + `<span class="cp-windows" role="group" aria-label="Window">${windows}</span><span class="cp-note">${read}</span></div>`;
}

export function renderPurchases() {
  if (!cp.root) return;
  const moreOpen = cp.root.querySelector?.('.cp-more')?.open || false;
  let main = '';
  if (cp.loading) main = '<div class="cp-loading" role="status" aria-live="polite"><span class="spinner" aria-hidden="true"></span><span>Reading your purchases…</span></div>';
  else if (cp.error) main = `<p class="cp-message is-error" role="alert">${esc(cp.error)}</p>`;
  else if (cp.body) {
    const more = cpMoreStatsHtml(cp.body) + cpBreakdownHtml(cp.body);
    main = cpSummaryHtml(cp.body)
      + ((cp.body.purchases || []).length ? cpListHtml(cp.body.purchases, cp.sort, cp.dir) : `<p class="cp-message">No card purchases in the last ${esc(cp.body.days)} days.</p>`)
      + (more ? `<details class="cp-more"${moreOpen ? ' open' : ''}><summary>More stats</summary><div class="cp-more-body">${more}</div></details>` : '');
  }
  cp.root.innerHTML = `<section class="card-purchases" aria-label="My purchases">${barHtml()}<div class="cp-main">${main}</div></section>`;
  if (cp.root.dataset) cp.root.dataset.cpReady = cp.loading ? 'false' : 'true';
}

async function readJson(url) {
  const get = cp.deps?.fetch || ((u) => fetch(u, { cache: 'no-store' }));
  const response = await get(url);
  let body = null;
  try { body = await response.json(); } catch (_) { body = null; }
  if (!response.ok) {
    const detail = body && typeof body.detail === 'string' ? body.detail : '';
    throw new Error(response.status === 429 ? 'Too many reads in a short time; wait a minute and try again.' : detail || `Your purchases could not be read (${response.status}).`);
  }
  return body;
}

// The one read. Called only from the maintainer's press (Load / Read again / a window button once loaded).
export async function loadPurchases() {
  const seq = ++cp.seq;
  cp.loading = true; cp.error = '';
  renderPurchases();
  try {
    const body = await readJson(cpUrl(cp.deps?.player?.() || '', cp.days));
    if (seq !== cp.seq) return;
    cp.body = body;
  } catch (err) {
    if (seq !== cp.seq) return;
    cp.error = err?.message || 'Your purchases could not be read.';
  }
  cp.loading = false;
  renderPurchases();
}

function onClick(event) {
  const target = event.target?.closest?.('[data-cp-action], [data-cp-days], [data-cp-sort]');
  if (!target || target.disabled) return;
  if (target.dataset.cpAction === 'load') { loadPurchases(); return; }
  if (target.dataset.cpDays) {
    const days = Number(target.dataset.cpDays);
    if (!CP_WINDOWS.includes(days) || days === cp.days) return;
    cp.days = days;
    if (cp.body) loadPurchases(); else renderPurchases();
    return;
  }
  if (target.dataset.cpSort) {
    const key = target.dataset.cpSort;
    cp.dir = key === cp.sort ? (cp.dir === 'asc' ? 'desc' : 'asc') : (key === 'name' ? 'asc' : 'desc');
    cp.sort = key;
    renderPurchases();
  }
}

// Mounts the view into `container`. deps: { player: () => string, fetch?: (url) => Response }. Draws the bar only: no read.
export function initPurchases(container, deps = {}) {
  cp.root = container || null;
  cp.deps = deps;
  if (!cp.root) return null;
  if (!cp.root.dataset?.cpBound) {
    cp.root.addEventListener?.('click', onClick);
    if (cp.root.dataset) cp.root.dataset.cpBound = '1';
  }
  renderPurchases();
  return { load: loadPurchases, render: renderPurchases };
}
