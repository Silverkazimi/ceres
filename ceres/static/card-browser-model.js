// card-browser-model.js -- : the card browser's pure filter, sort, one-tile-per-copy and
// summary functions. No imports and no DOM, so the vm harness can test them directly
// (tests/ceres/test_ir9173_card_browser.py). Rows come from GET /api/cards, one per copy.

export const CB_SET_LABELS = {
  alpha: 'Alpha', beta: 'Beta', untamed: 'Untamed', chaos: 'Chaos', rebellion: 'Rebellion',
  conclave: 'Conclave', land: 'Land / Verico', foundations: 'Foundations', gladius: 'Gladius', other: 'Soulkeep',
};
export const CB_SET_ORDER = ['alpha', 'beta', 'untamed', 'chaos', 'rebellion', 'conclave', 'land', 'foundations', 'gladius', 'other'];
export const CB_RARITIES = ['common', 'rare', 'epic', 'legendary'];
export const CB_SPLINTERS = ['fire', 'water', 'earth', 'life', 'death', 'dragon', 'neutral'];
export const CB_FOIL_LABELS = { 0: 'Regular', 1: 'Gold', 2: 'Gold Arcane', 3: 'Black Foil', 4: 'Black Foil Arcane' };
export const CB_SORTS = {
  pp: 'PP',
  max_pp: 'Max-level PP',
  pp_per_cc: 'PP per CC',
  name: 'Name',
};
export const CB_ABILITY_LABELS = Object.freeze({
  GRAIN: 'Bountiful Grain', WOOD: 'Bountiful Wood', STONE: 'Bountiful Stone', IRON: 'Bountiful Iron',
  AURA: 'Bountiful Aura', RESEARCH: 'Bountiful Research', SPS: 'Bountiful SPS', BLOODLINE: 'Toil and Kin',
  DD: 'Dark Discount', RATIONING: 'Rationing', RATIONING_LITE: 'Light Rationing', ENERGIZED: 'Energized',
  LL: "Labor's Luck", ALTERATION: 'Alteration', GLINT_RECOVERY: 'Glint Recovery', FORTUNE_SEEKER: 'Fortune Seeker',
  STATIC_CHARGE: 'Static Charge',
});
export const CB_ANY_ABILITY = '__any__';
export function cbAbilityCodes(card) {
  return (Array.isArray(card?.land_abilities) ? card.land_abilities : [])
    .map((entry) => Array.isArray(entry) ? String(entry[0] || '').toUpperCase() : '')
    .filter(Boolean);
}

export function cbDefaultFilters() {
  return { q: '', set: [], rarity: [], foil: [], splinter: [], status: [], level: [], min_pp: '', max_pp: '', ability: [], maxed: false, sort: 'pp' };
}

// a filter is a list of chosen values ([] = any). An old stored
// single value (a string) or a stray number is read as a one-element list, so a remembered filter never throws.
export const CB_MULTI_FILTERS = ['set', 'rarity', 'foil', 'splinter', 'status', 'level'];
export function cbList(value) {
  if (Array.isArray(value)) return value.map(String).filter((v) => v !== '');
  return value === '' || value === null || value === undefined ? [] : [String(value)];
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(String(value).replace(/[\s,]/g, ''));
  return Number.isFinite(n) ? n : null;
}

// A copy whose cooldown end has passed since the snapshot is ready again.
export function cbCopyStatus(card, now = Date.now()) {
  if (card.status === 'cooling' && card.ready_at) {
    const end = Date.parse(card.ready_at);
    if (Number.isFinite(end) && end <= now) return 'idle';
  }
  return card.status || 'idle';
}

// "Ready to stake": idle, free, stakeable and carrying Land PP.
export function cbReadyToStake(card, now = Date.now()) {
  return cbCopyStatus(card, now) === 'idle' && card.available !== false && !card.soulbound && !card.no_pp && (card.land_base_pp || 0) > 0;
}

export function cbUnavailableLabel(card) {
  if (card.soulbound) return 'Soulbound';
  const reason = String(card.unavailable_reason || '');
  if (!reason) return '';
  if (reason.startsWith('listed')) return 'Listed for sale';
  if (reason.startsWith('rented')) return 'Rented out';
  if (reason.startsWith('delegated')) return 'Delegated';
  if (reason.startsWith('locked')) return 'Locked';
  if (reason.startsWith('dead')) return 'Dead in Survival';
  return 'Not available';
}

export function cbMatches(card, filters, now = Date.now()) {
  const f = filters || {};
  if (f.q) {
    const needle = String(f.q).trim().toLowerCase();
    if (needle && !String(card.name || '').toLowerCase().includes(needle)) return false;
  }
  const sets = cbList(f.set);
  if (sets.length && !sets.includes(String(card.card_set || ''))) return false;
  const rarities = cbList(f.rarity);
  if (rarities.length && !rarities.includes(String(card.rarity || ''))) return false;
  const foils = cbList(f.foil);
  if (foils.length && !foils.includes(String(card.foil))) return false;
  const splinters = cbList(f.splinter).map((s) => s.toLowerCase());
  if (splinters.length && !(card.splinters || []).some((s) => splinters.includes(String(s).toLowerCase()))) return false;
  const statuses = cbList(f.status);
  if (statuses.length) {
    const status = cbCopyStatus(card, now);
    const passes = (want) => (want === 'ready' ? cbReadyToStake(card, now) : want === 'staked' ? status === 'staked'
      : want === 'cooling' ? status === 'cooling' : want === 'unavailable' ? Boolean(cbUnavailableLabel(card))
        : want === 'not_soulbound' ? !card.soulbound : true);
    if (!statuses.some(passes)) return false;
  }
  const levels = cbList(f.level);
  if (levels.length && !levels.some((want) => (want === 'maxed' ? card.maxed === true : want === 'not_maxed' ? card.maxed === false : true))) return false;
  if (f.maxed && card.maxed !== true) return false;  // (Maxed\" and show only maxed cards")
  const abilities = cbList(f.ability === true ? CB_ANY_ABILITY : f.ability);
  if (abilities.length && !(abilities.includes(CB_ANY_ABILITY) ? cbAbilityCodes(card).length
    : cbAbilityCodes(card).some((code) => abilities.includes(code)))) return false;
  // Min PP 0 or less and a negative Max PP are no bound (the Market's rule, card_market.pp_bounds), so "0" never
  // hides the copies whose PP is not known
  const rawMin = numberOrNull(f.min_pp);
  const rawMax = numberOrNull(f.max_pp);
  const min = rawMin !== null && rawMin > 0 ? rawMin : null;
  const max = rawMax !== null && rawMax >= 0 ? rawMax : null;
  if (min !== null || max !== null) {
    const pp = card.land_base_pp;  // always the current PP (the max-level toggle is gone)
    if (pp === null || pp === undefined) return false;
    if (min !== null && pp < min) return false;
    if (max !== null && pp > max) return false;
  }
  return true;
}

// (maintainer: "Show copies of all cards dont bundle them by name. We need to see each copy as an individual to prepare them
// for land"): one tile per copy, with that copy's own status, plot, cooldown end and unavailable reason. No uid is carried to the
// screen; a copy is told apart by its plot, level and CC.
export function cbTiles(cards, now = Date.now()) {
  return cards.map((card, index) => {
    const status = cbCopyStatus(card, now);
    return {
      key: index,
      card,
      status,
      plot: status === 'staked' && card.plot ? { label: card.plot, territory: card.plot_territory || null } : null,
      readyAt: status === 'cooling' ? card.ready_at || null : null,
      unavailable: cbUnavailableLabel(card),
      ready: cbReadyToStake(card, now),
    };
  });
}

// CC combined past the card's max-level CC is
// carried but never raises Land PP, so PP per CC counts at most bcx_max (a black foil's bcx_max 0 = always max: its own CC).
export function cbPpCc(card) {
  if (!(card?.bcx > 0)) return 0;
  return card.bcx_max > 0 ? Math.min(card.bcx, card.bcx_max) : card.bcx;
}

export function cbPpPerCcOf(card) {
  const cc = cbPpCc(card);
  return cc > 0 && card.land_base_pp !== null && card.land_base_pp !== undefined ? card.land_base_pp / cc : null;
}

// CC past the max-level CC, per copy (0 when unknown or a black foil).
export function cbSurplusCc(card) {
  return card?.bcx_max > 0 && card.bcx > card.bcx_max ? card.bcx - card.bcx_max : 0;
}

// en-US grouping, at most two decimals, trailing zeros dropped (2500 -> "2,500", 2352.941 -> "2,352.94", 5.00 -> "5").
// The board's own formatter (format.js num) sits behind the board graph, so this page uses Intl with the same grouping.
const PP_PER_CC_FORMAT = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
export function cbFormatPpPerCc(value) {
  return value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : PP_PER_CC_FORMAT.format(Number(value));
}

export function cbPpPerCc(tile) {
  return cbPpPerCcOf(tile.card);
}

// Copies of one card sit together: ties break by name, then CC (highest first), then level; the sort is stable.
export function cbSort(tiles, sort = 'pp') {
  const name = (g) => String(g.card.name || '').toLowerCase();
  const value = (g) => {
    if (sort === 'max_pp') return g.card.max_level_pp;
    if (sort === 'pp_per_cc') return cbPpPerCc(g);
    return g.card.land_base_pp;
  };
  return [...tiles].sort((a, b) => {
    if (sort !== 'name') {
      const va = value(a);
      const vb = value(b);
      const ka = va === null || va === undefined;
      const kb = vb === null || vb === undefined;
      if (ka !== kb) return ka ? 1 : -1;
      if (!ka && vb !== va) return vb - va;
    }
    const byName = name(a).localeCompare(name(b));
    if (byName) return byName;
    if ((b.card.bcx || 0) !== (a.card.bcx || 0)) return (b.card.bcx || 0) - (a.card.bcx || 0);
    return (b.card.level || 0) - (a.card.level || 0);
  });
}

export function cbSummary(cards) {
  let pp = 0;
  let maxPp = 0;
  let maxUnknown = 0;
  for (const card of cards) {
    pp += card.land_base_pp || 0;
    if (card.max_level_pp === null || card.max_level_pp === undefined) maxUnknown += 1;
    else maxPp += card.max_level_pp;
  }
  return { copies: cards.length, pp, maxPp, maxUnknown };
}

// One call for the page: filter the copies, one tile each, sorted.
export function cbView(cards, filters, now = Date.now()) {
  const matching = cards.filter((card) => cbMatches(card, filters, now));
  return { matching, tiles: cbSort(cbTiles(matching, now), filters?.sort || 'pp'), summary: cbSummary(matching) };
}
