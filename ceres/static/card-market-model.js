// card-market-model.js -- : the Cards page's Market mode, pure functions (no imports, no DOM), so the vm harness
// tests them directly (tests/ceres/test_ir9182_card_market.py). Rows come from GET /api/market, one per listing (or, with the
// Maxed filter, one per combined max-level offer). (maintainer: "All listin will always be loaded automatically when
// needed"): the page reads on entering Market mode and after each settled filter change that changes the prints in scope.

export const CM_SORTS = {
  cost_per_pp: 'Cost per PP',
  price: 'Price',
  pp: 'PP',
};

// The filters the server applies when it picks which prints to read (it reads the best 100 prints of the whole market).
// Maxed is read by the server too (it reads deeper and combines listings up to max CC). (maintainer: "bug: adding
// 3000 PP into the minimum field gives 0 market results"): the PP range too -- it picks the prints in scope before the cap, so a
// PP change reads again (debounced) instead of only re-filtering the prints already loaded.
export const CM_SERVER_FILTERS = ['set', 'rarity', 'foil', 'splinter', 'q', 'maxed', 'min_pp', 'max_pp'];

function toList(value) {
  if (Array.isArray(value)) return value.map(String).filter((v) => v !== '');
  return value === '' || value === null || value === undefined ? [] : [String(value)];
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(String(value).replace(/[\s,]/g, ''));
  return Number.isFinite(n) ? n : null;
}

export function cmPpBounds(filters) {
  const min = numberOrNull((filters || {}).min_pp);
  const max = numberOrNull((filters || {}).max_pp);
  return { min: min !== null && min > 0 ? min : null, max: max !== null && max >= 0 ? max : null };
}

// The filters of the Cards row that apply to a listing: name, set, rarity, foil, splinter and the PP range (status, level and
// land ability belong to the maintainer's own copies).
export function cmMatches(row, filters) {
  const f = filters || {};
  if (f.q) {
    const needle = String(f.q).trim().toLowerCase();
    if (needle && !String(row.name || '').toLowerCase().includes(needle)) return false;
  }
  // set / rarity / foil / splinter are lists ([] = any); a bare string still works
  const sets = toList(f.set);
  if (sets.length && !sets.includes(String(row.card_set || ''))) return false;
  const rarities = toList(f.rarity);
  if (rarities.length && !rarities.includes(String(row.rarity || ''))) return false;
  const foils = toList(f.foil);
  if (foils.length && !foils.includes(String(row.foil))) return false;
  const splinters = toList(f.splinter).map((s) => s.toLowerCase());
  if (splinters.length && !(row.splinters || []).some((s) => splinters.includes(String(s).toLowerCase()))) return false;
  if (f.maxed && row.maxed !== true) return false;
  // the same range the server applies (card_market.pp_bounds): Min PP 0 or less and a negative Max PP are no bound
  // so a listing the server returned is never dropped here for a bound the server ignored
  const { min, max } = cmPpBounds(f);
  if (min !== null || max !== null) {
    const pp = row.land_base_pp;
    if (pp === null || pp === undefined) return false;
    if (min !== null && pp < min) return false;
    if (max !== null && pp > max) return false;
  }
  return true;
}

// DEC paid per PP: the price over the PP it brings. Current = this listing as it is; max = the listing plus the CC still
// missing to max level at today's cheapest price per CC for that print, over the max-level PP (computed server side).
export function cmDecPerPp(row) {
  return row && row.price_dec > 0 && row.land_base_pp > 0 ? row.price_dec / row.land_base_pp : null;
}
export function cmDecPerPpMax(row) {
  return row && row.max_cost_dec > 0 && row.max_level_pp > 0 ? row.max_cost_dec / row.max_level_pp : null;
}
export function cmPpPerDec(row) {
  return row && row.price_dec > 0 && row.land_base_pp > 0 ? row.land_base_pp / row.price_dec : null;
}
export function cmPpPerDecMax(row) {
  return row && row.max_cost_dec > 0 && row.max_level_pp > 0 ? row.max_level_pp / row.max_cost_dec : null;
}

// (maintainer, 2026-10-14): the market ranks and shows PP per $ (USD as listed). DEC is USD at one rate, so the order is the
// same as by DEC; DEC stays as the small second price.
export function cmPpPerUsd(row) {
  return row && row.price_usd > 0 && row.land_base_pp > 0 ? row.land_base_pp / row.price_usd : null;
}
export function cmPpPerUsdMax(row) {
  return row && row.max_cost_usd > 0 && row.max_level_pp > 0 ? row.max_level_pp / row.max_cost_usd : null;
}

// Cheapest first for the cost and price sorts; most PP first for the PP sort. Unknown values go last. : ties break as the
// server's page windows do (card_market.offer_key): equal price -> more PP first, equal PP -> cheaper first, equal rate ->
// cheaper first; then by name. The sort is stable.
export function cmSort(rows, sort = 'cost_per_pp') {
  const value = (r) => {
    if (sort === 'price') return r.price_usd ?? r.price_dec ?? null;
    if (sort === 'pp') return r.land_base_pp ?? null;
    const v = cmPpPerUsd(r);
    return v ? 1 / v : null;
  };
  const descending = sort === 'pp';
  const second = (a, b) => (sort === 'price' ? (b.land_base_pp || 0) - (a.land_base_pp || 0) : (a.price_usd || 0) - (b.price_usd || 0));
  return [...rows].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    const ka = va === null || va === undefined;
    const kb = vb === null || vb === undefined;
    if (ka !== kb) return ka ? 1 : -1;
    if (!ka && va !== vb) return descending ? vb - va : va - vb;
    const tie = second(a, b);
    if (tie) return tie;
    const byName = String(a.name || '').toLowerCase().localeCompare(String(b.name || '').toLowerCase());
    if (byName) return byName;
    return (a.price_usd || 0) - (b.price_usd || 0);
  });
}

export function cmSummary(rows) {
  let best = null;
  for (const row of rows) {
    const rate = cmPpPerUsd(row);
    if (rate !== null && (best === null || rate > best.rate)) best = { rate, name: row.name, dec: cmDecPerPp(row) };
  }
  // the listings behind the offers (a combined Maxed offer stands for its N listings)
  const behind = rows.reduce((sum, row) => sum + (row.combined > 1 ? row.combined : 1), 0);
  return { listings: rows.length, behind, cheapest: best };
}

// (maintainer: "When searched by name show only one copy per foil/set and a click on the tile opens the menu. we still want
// seperate copies but can limit them to a base % of max ... All below folds into a container tile opening the menu wth all
// copies"; chosen rule "≥25% CC or a good deal"): per print (card + edition + foil), a listing keeps its own tile when its CC is
// at least CM_FOLD_CC_SHARE of the print's max CC, or its PP/$ is within CM_FOLD_DEAL of the print's best PP/$ (the gold/green
// band of : best ÷ its PP/$ − 1 ≤ 20 %). The print's other listings fold into one container tile, drawn where the best
// of them sits in the sort (so the order of the page and the server's page windows stay intact). A print with a single listing
// to fold keeps it as its own tile (a container would save nothing). Not with Maxed (every offer is a maxed card), and not for
// a print whose max CC is unknown or 0 (black foils: every copy is max level). Only under a name search (cmView).
export const CM_FOLD_CC_SHARE = 0.25;
export const CM_FOLD_DEAL = 0.2;
// at most this many listings per print keep a tile for being a good deal
// alone (the best by PP/$, the earlier in the sort on a tie); further good deals fold with the rest. Copies of 25 % CC and more
// always keep their tiles.
export const CM_FOLD_DEAL_TILES = 2;
const cmPrintId = (row) => `${row.card_detail_id}-${row.edition}-${row.foil}`;

// 'size' (25 % CC or more, or a print that never folds), 'deal' (within 20 % of the print's best PP/$) or false (folds)
export function cmFoldReason(row, bestRate) {
  if (!(row.bcx_max > 0) || row.combined > 1) return 'size';
  if (row.bcx >= CM_FOLD_CC_SHARE * row.bcx_max - 1e-9) return 'size';
  const rate = cmPpPerUsd(row);
  return rate !== null && bestRate !== null && bestRate / rate - 1 <= CM_FOLD_DEAL + 1e-9 ? 'deal' : false;
}

export function cmFoldKeeps(row, bestRate) {
  return cmFoldReason(row, bestRate) !== false;
}

// Sorted rows -> sorted items: { row } for a tile of its own, { row, fold: { rows, count, fromUsd, fromDec } } for a container
// (`row` is its best folded listing: the one the menu and the cart helpers see).
export function cmFold(rows, { maxed = false } = {}) {
  if (maxed) return rows.map((row) => ({ row }));
  const best = new Map();
  for (const row of rows) {
    const rate = cmPpPerUsd(row);
    const id = cmPrintId(row);
    if (rate !== null && (!best.has(id) || rate > best.get(id))) best.set(id, rate);
  }
  // per print, the CM_FOLD_DEAL_TILES best good deals keep their tiles; the other good deals fold
  const deals = new Map();
  const reason = new Map();
  rows.forEach((row, index) => {
    const why = cmFoldReason(row, best.has(cmPrintId(row)) ? best.get(cmPrintId(row)) : null);
    reason.set(row, why);
    if (why === 'deal') {
      if (!deals.has(cmPrintId(row))) deals.set(cmPrintId(row), []);
      deals.get(cmPrintId(row)).push({ row, index, rate: cmPpPerUsd(row) });
    }
  });
  const keptDeals = new Set();
  for (const list of deals.values()) {
    list.sort((a, b) => (b.rate - a.rate) || (a.index - b.index));
    for (const d of list.slice(0, CM_FOLD_DEAL_TILES)) keptDeals.add(d.row);
  }
  const folded = new Map();
  for (const row of rows) {
    const why = reason.get(row);
    if (why === 'size' || (why === 'deal' && keptDeals.has(row))) continue;
    const id = cmPrintId(row);
    if (!folded.has(id)) folded.set(id, []);
    folded.get(id).push(row);
  }
  for (const [id, list] of folded) if (list.length < 2) folded.delete(id);
  const out = [];
  for (const row of rows) {
    const list = folded.get(cmPrintId(row));
    if (!list || !list.includes(row)) { out.push({ row }); continue; }
    if (list[0] !== row) continue;  // the container sits at its best folded listing
    const usd = list.map((r) => r.price_usd).filter((v) => Number.isFinite(v));
    const dec = list.map((r) => r.price_dec).filter((v) => Number.isFinite(v));
    out.push({ row, fold: { rows: list, count: list.length, fromUsd: usd.length ? Math.min(...usd) : null, fromDec: dec.length ? Math.min(...dec) : null } });
  }
  return out;
}

// The tile model the Cards tile renderer draws: a listing is shown like one copy (no status, no plot, never a uid). Items are
// rows or cmFold items.
export function cmTiles(items) {
  return items.map((item, index) => {
    const row = item && item.row ? item.row : item;
    return {
      key: index,
      card: { ...row, no_pp: false, land_abilities: Array.isArray(row.land_abilities) ? row.land_abilities : [], skin: null },
      status: 'idle',
      plot: null,
      readyAt: null,
      unavailable: '',
      ready: false,
      listing: row,
      fold: item && item.fold ? item.fold : null,
    };
  });
}

export function cmView(rows, filters, sort) {
  const matching = (rows || []).filter((row) => cmMatches(row, filters));
  const sorted = cmSort(matching, sort || 'cost_per_pp');
  // folding applies under a name search only (the maintainer's scope; without one the server keeps 3 listings per print, too
  // few to judge a print's best deal, and the approved plain grid stays as it is)
  const named = String((filters || {}).q || '').trim() !== '';
  return { tiles: cmTiles(named ? cmFold(sorted, { maxed: Boolean((filters || {}).maxed) }) : sorted), summary: cmSummary(matching) };
}

// True when a server-side filter differs from the one the loaded listings were read with (the page then reads again).
const serverValue = (filters, key) => {
  if (key === 'maxed') return (filters || {}).maxed ? '1' : '';
  const raw = String((filters || {})[key] ?? '').trim();
  if (key === 'min_pp' || key === 'max_pp') { const n = cmPpBounds(filters)[key === 'min_pp' ? 'min' : 'max']; return n === null ? '' : String(n); }
  if (['set', 'rarity', 'foil', 'splinter'].includes(key)) return [...new Set(toList((filters || {})[key]).map((v) => v.trim()))].sort().join(',');  // OR list
  return raw;
};

export function cmFiltersChanged(readWith, current) {
  return CM_SERVER_FILTERS.some((key) => serverValue(readWith, key).toLowerCase() !== serverValue(current, key).toLowerCase());
}

// Market query: only the server-side filters that are set, plus the sort that picks which prints are read. The same filters
// give the same URL, so the page compares URLs to know whether a change needs a new read.
export function cmQuery(player, filters, sort) {
  const params = new URLSearchParams();
  if (player) params.set('player', player);
  for (const key of CM_SERVER_FILTERS) {
    const value = serverValue(filters, key);
    if (value) params.set(key, value);
  }
  params.set('sort', sort || 'cost_per_pp');
  return `/api/market?${params.toString()}`;
}

// Small ratios need significant digits, large ones thousands commas: 0.3698 -> "0.37", 0.0823 -> "0.082", 12.149 -> "12.1"
export function cmFormatRatio(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  if (n === 0) return '0';
  const abs = Math.abs(n);
  const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : abs >= 1 ? 2 : Math.min(6, 1 - Math.floor(Math.log10(abs)));
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(n);
}

export function cmFormatDec(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: n < 10 ? 2 : 0 }).format(n);
}

export function cmFormatUsd(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return `$${new Intl.NumberFormat('en-US', { minimumFractionDigits: n < 1 ? 3 : 2, maximumFractionDigits: n < 1 ? 3 : 2 }).format(n)}`;
}
