import { rarityGemHtml, resourceIconHtml, titleIconHtml, titleRarityGem, tooltipIconPath, totemIconHtml } from './icons.js';
import { DARK_ENERGY_ICON, DARK_ENERGY_SRC, tipAttrs, tipIcon } from './tooltip.js';
import { esc } from './escape.js';

// his words: "Rename \"DEC needed\" to \"DEC stake\" and add an approprite tooltip
// to each cell. Use the dark energy icon in the tooltip and after each stake requirement
// number in the cells ... If the DEC is 0 or lowered, explain what exactly is responsible
// for that (i.e. Runi or Dark Discount)." Runi zeroes the plot's whole DEC requirement
// regardless of Dark Discount; Dark Discount lowers it by the strongest holder's rate
// (library/land-worker-abilities.md §8); a plot with no workers simply needs none.
export function decStakeCell(row) {
  const needed = row?.dec?.needed;
  const dec = tipIcon(DARK_ENERGY_ICON);
  const dd = tipIcon('abilities/ability_dark-discount.png');
  const discount = Number(row?.dec?.needed_discount);
  const workers = Array.isArray(row?.workers) ? row.workers : [];
  const ddHolders = workers
    .map((worker) => ({ name: worker.name || worker.card_name, entry: (worker.land_abilities || []).find((entry) => Array.isArray(entry) && entry[0] === 'DD') }))
    .filter((holder) => holder.entry && holder.name);
  const strongest = Number.isFinite(discount) && discount < 0
    ? ddHolders.filter((holder) => Math.abs(Number(holder.entry[1]) - discount) < 1e-6)
    : [];
  // The server names the discount's source on every row (lean list rows have no workers).
  const serverCards = Array.isArray(row?.dec_discount_cards) ? row.dec_discount_cards.filter(Boolean) : [];
  const responsible = (serverCards.length ? serverCards : (strongest.length ? strongest : ddHolders).map((holder) => holder.name)).join(', ');
  const runi = row?.capacity?.runi_staked === true;
  const lines = [];
  if (needed === null || needed === undefined) {
    lines.push('Not reported for this plot.');
  } else if (runi) {
    lines.push(`0 ${dec}DEC — the staked Runi removes this plot's whole DEC stake requirement.`);
    if (ddHolders.length) lines.push(`${dd}Dark Discount on ${responsible} has no effect while the Runi is staked.`);
  } else if (Number(needed) === 0) {
    lines.push(workers.length ? `0 ${dec}DEC — no worker on this plot needs DEC.` : `0 ${dec}DEC — no workers are staked, so no DEC is needed.`);
  } else {
    lines.push(`${num(needed)} ${dec}DEC to stake for 100% efficiency.`);
    if (Number.isFinite(discount) && discount < 0) {
      lines.push(`Lowered ${Math.round(Math.abs(discount) * 100)}% by ${dd}Dark Discount${responsible ? ` on ${responsible}` : ''}.`);
    }
  }
  const tip = tipAttrs({ title: 'DEC stake', icon: DARK_ENERGY_ICON, items: lines.map((effect) => ({ name: '', effect })), peer: 'plot_metrics' });
  const html = needed === null || needed === undefined
    ? '—'
    : `${num(needed)}<img class="dec-stake-icon" src="${DARK_ENERGY_SRC}" alt="" aria-hidden="true">`;
  return { tip, html };
}

// esc lives in escape.js (no imports) so tooltip.js can run without the board graph; re-exported here.
export { esc };

export function cardFoilValue(card) {
  const value = card && typeof card === 'object' ? (card.foil ?? card.gold) : card;
  if (value === true || value === 1 || value === 'true' || value === '1' || value === 'gold') return '1';
  if (value === false || value === 0 || value === 'false' || value === '0' || value === 'regular') return '0';
  return String(value ?? '');
}

// Set/edition icon + SHORT NAME TEXT (worker rows, idle cards). the owner
// 2026-08-21 polish pass: "show the set/edition name as text on all cards, not
// just promo/reward" and "use SHORT set names" (his examples: Chaos, Verico
// Alpha, Beta, Untamed, Rebellion, Conclave, Foundations, Eternal, Land
// Gladius, Riftwatchers, Dice, Soulbound).
// SUPERSEDED 2026-08-21 (Ceres board-polish batch, "Card editions should
// always show the main set in the worker table, as that defines the bonus PP
// they receive"): the visible TEXT label is now the card's MAIN SET (Alpha
// Beta, Untamed, Chaos, Rebellion, Conclave, Foundations, Eternal, ...)
// the fact that actually determines the card's PP multiplier -- with the
// precise `edition` (Riftwatchers, Soulbound, Gladius, Verico, Dice, ...)
// preserved in the tooltip instead of lost. The earlier edition-precise-label
// design (see EDITION_LABEL below, still used for the tooltip) was a
// deliberate choice at the time to avoid collapsing Riftwatchers/Soulbound
// into "Chaos" -- that reasoning is superseded, not wrong: the tooltip now
// carries exactly the detail the label used to carry, so nothing is lost
// and the label carries the multiplier-relevant fact instead.
// Mapping derived from `library/open-questions.md`'s resolved edition table
// (Ceres Stage-A asset/taxonomy pass, 2026-08-21, sourced from the production
// frontend bundle's own EDITIONS enum): core main sets map to themselves
// mini-sets/soulbound/reward-track editions filed under one main set in the
// game's own data map to that set (Dice->Untamed, Riftwatchers/Soulbound->
// Chaos, Soulbound Rebellion->Rebellion, Foundations Soulbound->Foundations
// Soulbound Conclave/Escalation->Conclave); Gladius/Land/Verico map to the
// official Eternal pseudo-edition (`EDITIONS.ETERNAL={ID:-3}`).
// CORRECTED 2026-08-21 (board-polish batch 16, official PP-multiplier gitbook
// see docs/ceres.md): Promo(2)/Reward(3)/Extra(17) were originally treated as
// print classes with NO attributable main set, on the reasoning that they
// appear alongside every main `card_set` value. That reasoning doesn't survive
// contact with the primary source -- the official multiplier table explicitly
// lists each tier's OWN promo/reward editions ("Alpha: Core, Promo"; "Beta
// Core, Promo, Reward, Orb"; "Untamed: Core, Promo, Reward, Dice"), so an
// Alpha-family Promo card gets Alpha's 10X, a Beta-family Reward card gets
// Beta's 5X -- the multiplier tracks `card_set`, not a standalone "Promo"
// bucket. `PRINT_CLASS_EDITIONS` below routes these three editions to
// `card_set` instead of this table (779 live cards affected: 195 Promo, 504
// Reward, 80 Extra). Nightmare(9)/Soulkeep Promo(11) are a genuinely different
// case -- a Soulkeep reward track, `card_set:"other"`, not paired with any
// main-set family in the game's own data -- and keep their own labels.
const MAIN_SET_LABEL = {
  0: 'Alpha', 1: 'Beta', 2: 'Promo', 3: 'Reward', 4: 'Untamed', 5: 'Untamed', 6: 'Eternal',
  7: 'Chaos', 8: 'Chaos', 9: 'Nightmare', 10: 'Chaos', 11: 'Soulkeep Promo',
  12: 'Rebellion', 13: 'Rebellion', 14: 'Conclave', 15: 'Foundations',
  16: 'Foundations', 17: 'Extra', 18: 'Conclave', 19: 'Eternal',
  20: 'Conclave', 21: 'Eternal', '-1': 'Orb',
};
// Promo/Reward/Extra -- take the main-set label from `card_set` instead of
// MAIN_SET_LABEL above (see the correction note just above). Falls back to
// MAIN_SET_LABEL's own "Promo"/"Reward"/"Extra" only if `card_set` is somehow
// missing on the row.
const PRINT_CLASS_EDITIONS = new Set([2, 3, 17]);
// Precise edition/mini-set labels -- no longer the visible text (see above)
// kept as the TOOLTIP so the Soulbound/Riftwatchers/Gladius/Verico/Dice
// detail the earlier design existed to preserve is still one hover away.
export const EDITION_LABEL = {
  0: 'Alpha', 1: 'Beta', 2: 'Promo', 3: 'Reward', 4: 'Untamed', 5: 'Dice', 6: 'Gladius',
  7: 'Chaos', 8: 'Riftwatchers', 9: 'Nightmare', 10: 'Soulbound', 11: 'Soulkeep Promo',
  12: 'Rebellion', 13: 'Soulbound Rebellion', 14: 'Conclave', 15: 'Foundations',
  16: 'Foundations Soulbound', 17: 'Extra', 18: 'Soulbound Conclave', 19: 'Land',
  20: 'Escalation', 21: 'Verico', '-1': 'Orb',
};
// Full names, only where they differ from the short label above -- read live
// from the same production bundle's pack-name strings
// (`fullName`/`titleName` pairs). Everything not listed here has no separate
// full form; the short label doubles as both.
const EDITION_FULL_NAME = {
  5: 'ΛZMΛRÉ Dice', 7: 'Chaos Legion', 8: 'Riftwatchers', 10: 'Chaos Legion Soulbound',
  14: 'Conclave Arcana', 21: 'Visions of Verico',
};

export function editionDisplayLabel(edition) {
  if (edition === null || edition === undefined || edition === '') return 'Unknown edition';
  const id = Number(edition);
  return EDITION_FULL_NAME[id] || EDITION_LABEL[id] || `Unknown edition (ID ${edition})`;
}

export function setNameLabel(cardSet, edition) {
  if (edition !== null && edition !== undefined && PRINT_CLASS_EDITIONS.has(edition) && cardSet) {
    return cardSet.charAt(0).toUpperCase() + cardSet.slice(1);
  }
  if (edition !== null && edition !== undefined && MAIN_SET_LABEL[edition] !== undefined) {
    return MAIN_SET_LABEL[edition];
  }
  if (cardSet) return cardSet.charAt(0).toUpperCase() + cardSet.slice(1);
  return 'Unknown';
}

// Precise edition/mini-set name for the tooltip -- the specific detail the
// main-set label above no longer carries (Riftwatchers, Soulbound, Gladius
// Verico, Dice, ...). Falls back to the full-name table, then the main-set
// label itself when no edition-specific entry exists.
export function editionTooltip(cardSet, edition, mainLabel) {
  if (edition !== null && edition !== undefined) {
    return editionDisplayLabel(edition);
  }
  return mainLabel;
}

// Foil labels are text chips because foil is a card-art variant, not a
// compositable icon. Codes 0 through 4 are formatted by fmtFoil below.
// DELIBERATELY DOES NOT STYLE THE NAME CELL (the owner, 2026-08-21 seventh
// polish pass -- "the third time should be impossible by construction, not
// by tuning the stops"). This class used to apply a CSS gradient treatment
// to the card-name text -- twice fixed and twice still wrong: first pass
// used `background-clip: text; color: transparent` with no fallback, making
// gold names invisible wherever the clip didn't apply; the "fix" moved the
// gradient behind `@supports (background-clip: text)`, which every current
// browser supports, so the SAME pale gradient stops (#fff6d8/#e2b93b
// #a9861f/#fff2c2) kept rendering -- just as unreadable against a light
// theme or the light territory backgrounds this project added the same day
// (Pristine Northwest #FFFFFF, Land of Prosperity #999999). Both failures
// share one root cause: conveying data by making the NAME text a gradient is
// structurally fragile no matter how the stops are tuned, because the name
// is the one thing on the row that must always be legible.
export function isGoldFoil(w) {
  return ['1', '2'].includes(cardFoilValue(w));
}

// black foil and black foil arcane (3 / 4). Their card art is the "_blk" file on the CDN, as the official site builds it.
export function isBlackFoil(w) {
  return ['3', '4'].includes(cardFoilValue(w));
}

export function isNonCombinableFoil(card) {
  return ['3', '4'].includes(cardFoilValue(card));
}

// Fast thousands-separator formatter (2026-08-21 scroll-perf fix). This used to be
// a one-line `Number.toLocaleString(undefined, {maximumFractionDigits: digits})`
// simpler, but a CPU profile of a slow-scroll repro (Playwright, CDP Profiler.stop)
// showed it alone at 15.8% of ALL sampled time during a 20-frame scroll, called
// 5-7x per row (base/construction/harvest PP, DEC needed, PP/DEC, ...) for every
// row the virtualized renderTable() mounts on EVERY scroll tick -- `toLocaleString`
// does full ICU locale resolution on every call, which V8 does not cache across
// calls with `undefined` locale. Hand-rolled grouping matches its output for this
// project's only ever-used locale (en-US-style: comma group, dot decimal
// maximumFractionDigits semantics -- rounds to at most `digits` places, no
// zero-padding) at a fraction of the cost.
// the Runi is not a worker
// slot. It is identified by what the card IS -- the Runi card (card_detail_id 505, a runi_number, or the name Runi)
// never by a slot number. Matching `Number(worker.slot) === 0` let any worker with an unknown slot (`Number(null)`
// is 0) take the Runi tile when a plot's details were not loaded.
export function isRuniWorker(worker) {
  if (!worker) return false;
  if (worker.runi_number !== null && worker.runi_number !== undefined && worker.runi_number !== '') return true;
  if (Number(worker.card_detail_id) === 505) return true;
  return /^runi$/i.test(String(worker.card_name || worker.name || '').trim());
}

export function num(v, digits = 0) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(n);
  const factor = 10 ** digits;
  const rounded = Math.round(Math.abs(n) * factor) / factor;
  const [intPart, fracPart] = rounded.toString().split('.');
  let grouped = '';
  for (let i = 0; i < intPart.length; i++) {
    if (i > 0 && (intPart.length - i) % 3 === 0) grouped += ',';
    grouped += intPart[i];
  }
  return (n < 0 ? '-' : '') + grouped + (fracPart ? '.' + fracPart : '');
}

export function terrainValue(value) {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export const TERRAIN_AFFINITY_SPLINTERS = Object.freeze([
  Object.freeze(['red', 'Fire']),
  Object.freeze(['blue', 'Water']),
  Object.freeze(['green', 'Earth']),
  Object.freeze(['white', 'Life']),
  Object.freeze(['black', 'Death']),
  Object.freeze(['gold', 'Dragon']),
]);

function terrainAffinityValue(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function terrainAffinityPercentText(value) {
  const percent = value * 100;
  if (Number.isFinite(percent)) return String(Math.abs(percent));
  const [mantissa, exponent] = value.toExponential().split('e');
  const shiftedExponent = Number(exponent) + 2;
  return `${mantissa.replace('-', '')}e${shiftedExponent >= 0 ? '+' : ''}${shiftedExponent}`;
}

function terrainAffinityPeer(rawKey, splinter, value) {
  const scaledPercent = value === null ? null : value * 100;
  const percent = value === null ? null : Number.isFinite(scaledPercent) ? scaledPercent : `${value < 0 ? '−' : ''}${terrainAffinityPercentText(value)}`;
  const state = value === null ? 'unknown' : value > 0 ? 'favoured' : value < 0 ? 'penalised' : 'unchanged';
  const display = value === null ? 'Modifier unavailable · not received' : `${value > 0 ? '+' : value < 0 ? '−' : ''}${terrainAffinityPercentText(value)}%`;
  return {
    rawKey,
    splinter,
    value,
    percent,
    state,
    display,
    overflow: value !== null && Math.abs(value) > 0.5,
    barRatio: value === null ? 0 : Math.min(Math.abs(value), 0.5) / 0.5,
  };
}

export function terrainAffinityProfile(row) {
  const biome = row?.biome && typeof row.biome === 'object' && !Array.isArray(row.biome) ? row.biome : null;
  const peers = TERRAIN_AFFINITY_SPLINTERS.map(([rawKey, splinter]) => terrainAffinityPeer(
    rawKey,
    splinter,
    terrainAffinityValue(biome && Object.prototype.hasOwnProperty.call(biome, rawKey) ? biome[rawKey] : null),
  ));
  const groups = [
    { id: 'favoured', label: 'Favoured', glyph: '↑', peers: peers.filter((peer) => peer.state === 'favoured') },
    { id: 'unchanged', label: 'No change', glyph: '—', peers: peers.filter((peer) => peer.state === 'unchanged') },
    { id: 'penalised', label: 'Penalised', glyph: '↓', peers: peers.filter((peer) => peer.state === 'penalised') },
    { id: 'unknown', label: 'Modifier unavailable', glyph: '?', peers: peers.filter((peer) => peer.state === 'unknown') },
  ].filter((group) => group.peers.length);
  return {
    terrain: String(row?.terrain || 'this plot'),
    peers,
    groups,
    neutral: { splinter: 'Neutral', label: 'No terrain effect', display: 'No terrain effect' },
  };
}

export function formatPlotLabel(plot) {
  const parts = String(plot ?? '').split('-');
  if (parts.length !== 3) return String(plot ?? '');
  const widths = [3, 2, 3];
  return parts.map((part, index) => part.padStart(widths[index], '0')).join('-');
}

// Harvest/hr cell: `staking_facts.total_work_per_hour` is already the plot's real
// resource-unit output per hour (the owner, 2026-08-21 -- "the plot's real resource
// output, not just PP"); this is a straight display of that existing field, never
// a recomputation. Formatted with the plot's own resource ICON (not the word
// the owner, 2026-08-21 beautification pass: "the harvest column should be
// beautified and use the resource icon"), reusing the exact same
// `resourceIconHtml()`/`RESOURCE_ICON` mapping the Worksite column already uses so
// the two columns can never disagree about which icon means which resource. That
// helper also supplies the `data-tip` tooltip naming the resource (e.g. "Grain")
// and the shared icon drop-shadow treatment -- nothing new to wire up here. A bare
// number is still meaningless without knowing which resource it counts -- the
// per-resource PP-to-work_per_hour divisor varies 500x across resources (see
// the internal rules's corrected durable fact) -- so the icon is load-bearing, same as the
// word it replaces.
// No-worksite fix ( board-fix batch, the owner: "'No worksite' should render an
// em dash, not '0/hr'"): a plot with no worksite has no `resource` at all (verified
// against player0001's account: every plot carrying the `no_worksite` diagnostic has
// `resource: null` and `work_per_hour: 0` -- an exact, unbroken correlation) and its
// literal 0 is not a measured production rate of anything, so showing "0/hr" implies
// a defined-but-zero rate rather than "no resource pathway exists here at all"
// the same honest-null distinction this board applies everywhere else. Plots with a
// worksite that simply haven't produced yet still have a `resource`, and keep
// showing their real (possibly zero) rate with its icon.
// "/hr" dropped from the body cell (board-polish batch 19, item 2 -- the owner
// "drop '/hr' from the Harvest/hr BODY cells, keep it in the header cell
// only"); the header's own "Harvest/hr" label still states the unit, and the
// resource icon's tooltip (from resourceIconHtml) still names the resource
// so nothing about what's being measured is lost, just the redundant suffix
// repeated on every one of 130 rows.
// SPS per PP per hour is ~0.00005 -- three significant digits, never rounded to 0.
export function fmtSpsPerPp(rate) {
  return Number(rate).toPrecision(3);
}

// "PR-BRL-41" -> "Region 41" (the game's region number; the name is not in the board payload).
export function regionLabel(regionUid) {
  const m = /(\d+)\s*$/.exec(String(regionUid || ''));
  return m ? `Region ${m[1]}` : 'This region';
}

export function fmtWorkPerHour(row) {
  if (row.work_per_hour === null || row.work_per_hour === undefined) return '—';
  if (!row.resource) return '—';
  const digits = Math.abs(row.work_per_hour) < 10 ? 3 : 0;
  const n = num(row.work_per_hour, digits);
  // (maintainer: "tax is dynamic and depends on other peoples harvest in that tract or region. Show the tax percentage
  // there instead"): a Keep/Castle's work_per_hour is its PP, not a production rate -- show its tax rate.
  if (String(row.resource).toUpperCase() === 'TAX') {
    const rate = Number(row.tax_rate);
    if (!Number.isFinite(rate)) return '—';
    const pct = `${(rate * 100).toLocaleString('en-US', { maximumFractionDigits: 1 })}%`;
    const tip = { title: `Tax rate ${pct}`, body: `This ${String(row.worksite_type || 'tax plot').toLowerCase()} collects ${pct} of the harvest tax in its tract or region. What that pays depends on how much the other players there harvest, so it has no fixed amount per hour.`, peer: 'harvest_per_hour' };
    return `<span${tipAttrs(tip)}>${pct}</span> ${resourceIconHtml(row.resource, row.worksite_type, null, row.terrain)}`;
  }
  // the figure is the GROSS production (it equals the
  // game's rewards_per_hour); the game keeps 10% as tax at harvest. The tooltip shows both.
  const resource = String(row.resource).toLowerCase();
  const name = resource === 'sps' ? 'SPS' : resource.charAt(0).toUpperCase() + resource.slice(1);
  const icon = tooltipIconPath('resource', resource);
  const gross = Number(row.work_per_hour);
  const tax = gross * 0.1;
  const fmt = (value) => num(value, Math.abs(value) < 10 ? 3 : 0);
  const items = [
    { icon, name: 'Before tax', effect: `${fmt(gross)} ${name}/h`, group: 'Per hour' },
    { icon: null, name: 'Tax 10%', effect: `−${fmt(tax)} ${name}/h`, group: 'Per hour' },
    { icon, name: 'After tax (you receive)', effect: `${fmt(gross - tax)} ${name}/h`, group: 'Per hour' },
  ];
  // in a region short of DEC the rate above is already the game's scaled figure; the tooltip keeps
  // everything it says at 100% and ADDS the efficiency line and the 100% rate (maintainer: an addition, never a replacement).
  const eff = Number(row.region_efficiency);
  const unscaled = Number(row.work_per_hour_unscaled);
  const shortRegion = Number.isFinite(eff) && eff < 1 && Number.isFinite(unscaled) && unscaled !== gross;
  if (shortRegion) {
    const pct = `${(eff * 100).toLocaleString('en-US', { maximumFractionDigits: 1 })}%`;
    items.push({ icon: null, name: `Region DEC efficiency ${pct}`, effect: `${fmt(unscaled)} ${name}/h at 100%`, group: 'Per hour' });
  }
  const effNote = shortRegion ? ` ${regionLabel(row.region)} has too little DEC staked, so this plot produces at ${(eff * 100).toLocaleString('en-US', { maximumFractionDigits: 1 })}% (a Runi plot is exempt).` : '';
  // an SPS plot's own rate per PP -- the shared pool's rate as the game paid it.
  const spsNote = resource === 'sps' && gross > 0 && row.harvest_pp > 0
    ? ` ${fmtSpsPerPp(gross / row.harvest_pp)} SPS per PP per hour on this plot now; the shared pool's rate changes during the day.` : '';
  const tip = { title: `${tipIcon(icon)}${name} per hour — before tax`, items, foot: `What this plot produces per hour. The game keeps 10% as tax when you harvest.${effNote}${spsNote}`, peer: 'harvest_per_hour' };
  return `<span${shortRegion ? ' class="is-region-short"' : ''}${tipAttrs(tip)}>${n}</span> ${resourceIconHtml(row.resource)}`;
}

// Exact-value DOM attribute for the three PP columns (the owner, 2026-08-21
// eighth polish pass: "remove all decimals in the three PP columns but keep
// them invisible for future statistics which might need them. dont fully cut
// them in the backend"). RENDERING ONLY -- `ceres.board.v1`'s payload is
// completely untouched (no rounding in `board.py`/the projection); this
// supersedes the internal rules's upstream three-decimal DISPLAY convention for PP
// values specifically on the board's PP columns, on the owner's explicit
// instruction -- the underlying data still carries that precision, so the
// convention is unbroken where it actually matters (the payload). Chosen
// attribute so a future statistics view or export can read it programmatically
// without a round trip back to `/api/board`. Honest nulls stay null: no attribute
// is emitted for a null/undefined value, never a fabricated 0.
export function ppAttrs(value) {
  if (value === null || value === undefined) return '';
  const exact = ` data-pp-exact="${value}"`;
  return exact;
}

export function fmtBasePp(row) {
  const effective = Number(row?.base_pp_after_cap);
  return num(Number.isFinite(effective) ? effective : row?.base_pp, 0);
}

export function basePpTipAttrs(row) {
  const before = Number(row?.base_pp);
  const after = Number(row?.base_pp_after_cap);
  if (!Number.isFinite(before) || !Number.isFinite(after)) return '';
  const cap = Number(row?.base_pp_cap);
  const loss = before - after;
  const capReached = row?.base_pp_cap_reached === true;
  if (!capReached) return tipAttrs({ title: 'Base PP', key: num(after), body: 'Base PP before terrain and production modifiers.', peer: 'plot_metrics' });
  const capText = loss > 0 ? ` The plot's ${num(cap)} cap removes ${num(loss)} PP.` : ` The plot is at its ${num(cap)} cap.`;
  return tipAttrs({ title: 'Base PP', key: num(after), body: `Before cap ${num(before)}. After cap ${num(after)}.${capText}`, peer: 'plot_metrics' });
}

const PP_MODIFIER_LABELS = {
  deed_rarity_boost: 'Deed rarity', totem_boost: 'Totem', title_boost: 'Title',
  runi_boost: 'Runi', deed_status_token_boost: 'Status token',
  card_abilities_boost: 'Worker abilities', card_bloodlines_boost: 'Worker bloodlines',
};

function ppPercent(value) {
  return `${Number(value) >= 0 ? '+' : ''}${num(Number(value) * 100)}%`;
}

function ppWorkers(row) {
  const workers = Array.isArray(row?.pp_tooltip?.workers) ? row.pp_tooltip.workers : row?.worker_names;
  return Array.isArray(workers) && workers.length ? workers.join(', ') : 'No workers reported';
}

export function boostablePpTipAttrs(row) {
  const detail = row?.pp_tooltip || {};
  const terrain = Number(detail.terrain_boost);
  const items = [
    { name: 'Workers', effect: ppWorkers(row) },
    { name: 'Base PP after cap', effect: num(row?.base_pp_after_cap ?? row?.base_pp) },
    ...(Number.isFinite(terrain) ? [{ name: 'Terrain modifier', effect: ppPercent(terrain) }] : []),
    { name: 'Boostable PP', effect: num(row?.construction_pp) },
  ];
  return tipAttrs({ title: 'Boostable PP', items, foot: 'This plot’s game-reported Base PP after cap and terrain modifier.', peer: 'plot_metrics' });
}

export function totalPpTipAttrs(row) {
  const detail = row?.pp_tooltip || {};
  const modifiers = detail.production_modifiers && typeof detail.production_modifiers === 'object' ? detail.production_modifiers : {};
  const modifierItems = Object.entries(modifiers)
    .filter(([, value]) => Number.isFinite(Number(value)) && Number(value) !== 0)
    .map(([name, value]) => ({ name: PP_MODIFIER_LABELS[name] || name, effect: ppPercent(value) }));
  const production = Number(detail.production_boost);
  const items = [
    { name: 'Workers', effect: ppWorkers(row) },
    { name: 'Boostable PP', effect: num(row?.construction_pp) },
    ...modifierItems,
    ...(Number.isFinite(production) ? [{ name: 'Production modifiers total', effect: ppPercent(production) }] : []),
    { name: 'Total PP', effect: num(row?.harvest_pp) },
  ];
  return tipAttrs({ title: 'Total PP', items, foot: 'This plot’s game-reported Boostable PP with its own production modifiers.', peer: 'plot_metrics' });
}

// the Boost
// multiplier is 1 + the game's production modifiers; every non-zero one is listed, then their sum and the multiplier it makes.
export function boostTipAttrs(row) {
  const modifiers = row?.pp_tooltip?.production_modifiers;
  if (!modifiers || typeof modifiers !== 'object' || row?.multiplier == null) return '';
  const entries = Object.entries(modifiers).filter(([, value]) => Number.isFinite(Number(value)) && Number(value) !== 0);
  const sum = entries.reduce((total, [, value]) => total + Number(value), 0);
  const items = [
    { name: 'Base', effect: '100%' },
    ...entries.sort((a, b) => Number(b[1]) - Number(a[1])).map(([name, value]) => ({ name: PP_MODIFIER_LABELS[name] || name, effect: ppPercent(value) })),
    { name: 'Boosts added up', effect: ppPercent(sum) },
    { name: 'Multiplier', effect: `${Number(row.multiplier).toFixed(2)}x` },
  ];
  const mismatch = Math.abs(1 + sum - Number(row.multiplier)) > 0.006;
  return tipAttrs({ title: 'Boost', items, foot: mismatch ? 'The game reports a multiplier that differs from these boosts added up.' : 'Total PP = Boostable PP × Boost.', peer: 'plot_metrics' });
}

// Owner ruling (2026-08-30): the `resolved`/`derived` pills next to every
// Totem/Title cell were "noise and disturbing" -- `resolved` is the normal
// case and says nothing about provenance anywhere now. `conflict` is the one
// exception, kept as a visible pill: a
// disagreement between the bulk boost and the loaded asset is a genuine
// anomaly, not noise, and hiding it behind a hover would make a data
// problem invisible.
// Gate document #61: boosts read as percentages, never as raw fractions (0.5).
function boostPercentText(value) {
  const n = Number(value);
  return value !== null && value !== undefined && Number.isFinite(n) ? `+${num(n * 100)}%` : 'unknown';
}

function bonusResolutionHtml(bonus) {
  if (!bonus || bonus.resolution !== 'conflict') return '';
  const conflict = bonus.conflict || {};
  return `<span class="bonus-resolution bonus-conflict"${tipAttrs({
    title: 'Boost mismatch',
    body: `Plot data says ${boostPercentText(conflict.bulk_boost)} · the card itself reports ${boostPercentText(conflict.asset_boost)}.`,
    peer: 'data_disagreements',
  })}>Conflict</span>`;
}

function titleTooltipModel(title) {
  let boostTxt;
  if (title.boost !== null && title.boost !== undefined) {
    boostTxt = (title.boost * 100).toFixed(0) + '%';
  }
  const gemTier = titleRarityGem(title.boost);
  const rarityLabel = gemTier ? gemTier.charAt(0).toUpperCase() + gemTier.slice(1) : 'Unknown';
  const gem = (tier) => tipIcon(tooltipIconPath('rarity', tier));
  const unknownTierBody = title.boost === null || title.boost === undefined
    ? 'The boost could not be read, so the tier is unknown.'
    : `Boost +${boostTxt} fits no tier: ${gem('rare')}Rare 10% · ${gem('epic')}Epic 25% · ${gem('legendary')}Legendary 50%`;
  return {
    title: title.name,
    icon: tooltipIconPath('title', title.name),
    items: [
      ...(gemTier ? [{ icon: tooltipIconPath('rarity', gemTier), name: 'Rarity', effect: rarityLabel }] : []),
      ...(!gemTier ? [{ name: 'Title tier', effect: `Title tier unmatched. ${unknownTierBody}` }] : []),
      ...(boostTxt ? [{ name: 'Production boost', effect: `+${boostTxt} for every worker` }] : []),
    ],
    peer: 'icons_boosts',
    boostTxt,
    gemTier,
  };
}

export function titleTipAttrs(row) {
  const bonus = row._live_bonus || row.bonus;
  const title = bonus && bonus.title;
  if (!title) return '';
  const model = titleTooltipModel(title);
  return tipAttrs(model);
}

// Icon-only (board-polish batch 19, item 1) -- the visible name+boost text
// is gone; totemIconHtml's own tooltip now carries it in full, via the
// existing tipAttrs()/initTooltips() mechanism rather than a separate span.
export function fmtTotem(row) {
  const bonus = row._live_bonus || row.bonus; // live state only in the row
  const totem = bonus && bonus.totem;
  if (!totem) return '—';
  const name = totem.name ?? totem.rarity;
  const tooltipModel = { title: name };
  if (totem.boost !== null && totem.boost !== undefined) {
    tooltipModel.body = `${boostPercentText(totem.boost)} production for this plot`;
  }
  return `${totemIconHtml(totem.rarity, tooltipModel)}${bonusResolutionHtml(totem)}`;
}

// Icon-only (board-polish batch 19, item 1 -- the owner: "Title will keep both
// icons, title plus rarity, perfectly vertical aligned"). The visible name
// text is gone; the generic title-icon's own tooltip now carries the full
// name+rarity+boost model through the same tipAttrs()/initTooltips() mechanism
// as everywhere else on this board. The surrounding title slot owns that model
// so either icon hover drives the same singleton tooltip without moving the
// icon boxes. The "tier unknown" anomaly stays in the tooltip body.
export function fmtTitle(row) {
  const bonus = row._live_bonus || row.bonus;
  const title = bonus && bonus.title;
  if (!title) return '—';
  const tooltipModel = titleTooltipModel(title);
  const { gemTier } = tooltipModel;
  const iconHtml = titleIconHtml(null, title.name);
  const gemHtml = gemTier
    ? rarityGemHtml(gemTier, {})
    : '';
  return `${iconHtml}${gemHtml}${bonusResolutionHtml(title)}`;
}

function fmtDecEfficiency(row) {
  if (row.plot_dec_efficiency !== null && row.plot_dec_efficiency !== undefined) {
    return row.plot_dec_efficiency.toFixed(2);
  }
  // Gate document #65: say it once, in plain words, with the DEC icon.
  const reason = String(row.plot_dec_efficiency_flag || '');
  const model = /runi/i.test(reason)
    ? { title: 'DEC efficiency not applicable', body: "Runi sets this plot's DEC stake to 0." }
    : { title: 'DEC efficiency not available', body: reason ? `${reason.charAt(0).toUpperCase()}${reason.slice(1)}.` : 'The plot reports no DEC figure yet.' };
  return `<span class="dec-efficiency-flag"${tipAttrs({ ...model, icon: tooltipIconPath('resource', 'dec'), peer: 'dec_efficiency' })}>—</span>`;
}

export function fmtFoil(w) {
  const names = { 0: 'Regular', 1: 'Gold', 2: 'Gold Arcane', 3: 'Black Foil', 4: 'Black Foil Arcane' };
  return names[cardFoilValue(w)] || 'Unknown Foil';
}

function fmtCc(w) {
  // CC (the owner's term for BCX) over the max BCX for this exact printing
  // `bcx_max` is a static table lookup (ceres/bcx_cap.py::resolve_bcx_max, sourced
  // from api.splinterlands.com/settings' combine-rate tables), not an observed
  // value, so it's populated for every printing this table covers -- not only
  // ones the owner happens to own a maxed copy of. A null here means the printing
  // genuinely isn't in the table, i.e. UNKNOWN -- never treat it as maxed.
  const cc = w.bcx ?? '—';
  const ccMax = w.bcx_max ?? '—';
  return `${cc}/${ccMax}`;
}

// Defensive per-row rendering (the owner/team-lead, 2026-08-21 urgent report
// "it shows no workers at all now"). Not reproduced against live data
// `workerSubtable`/`idleCardsTable` were run against the actual live
// `/api/board` payload (182 workers, 7,568 idle cards) in a Node harness this
// pass and threw zero exceptions -- but a single bad record throwing inside
// `Array.prototype.map()` would blank the ENTIRE calling table (one throw
// aborts the whole map, and nothing downstream ever runs), which is a real
// structural risk regardless of whether today's data happens to trigger it.
// `safeMap` makes that impossible: each row renders in its own try/catch, so
// one malformed card can only ever cost its own line, never the table.
function safeMap(items, renderOne, describe) {
  return items.map((item) => {
    try {
      return renderOne(item);
    } catch (e) {
      const what = describe ? describe(item) : '(unidentified row)';
      return `<tr class="render-error-row"><td colspan="99">Render error for ${esc(what)}: ${esc(e.message)}</td></tr>`;
    }
  }).join('');
}

// multiplier_parts is the actual COMPOSITION of the Mult column above it
// exactly what the owner's own spreadsheet broke out as separate Rarity/Totem
// Title/Runi/Occ columns (docs/ceres.md's "Dropped or renamed spreadsheet
// columns" section) -- so unlike deed_stats this is genuinely useful, just
// badly presented as JSON. Shown as a compact "Label +NN%" breakdown, only
// non-zero contributors (a zero contributor tells you nothing you don't
// already know from its absence).
const MULTIPLIER_PART_LABELS = {
  deed_rarity_boost: 'Deed rarity', totem_boost: 'Totem', title_boost: 'Title',
  runi_boost: 'Runi', deed_status_token_boost: 'Status token',
  card_abilities_boost: 'Abilities', card_bloodlines_boost: 'Bloodlines',
};
export function multiplierPartsHtml(parts) {
  if (!parts) return '';
  const contributors = Object.entries(parts)
    .filter(([, v]) => v)
    .map(([k, v]) => `${MULTIPLIER_PART_LABELS[k] || k} +${(v * 100).toFixed(0)}%`);
  if (!contributors.length) return '';
  return `<div class="multiplier-breakdown">${contributors.map(esc).join(' · ')}</div>`;
}

// "Cooling" is a lie for a currently-staked card (the owner, 2026-08-25 live report
// "cards that are actually staked ... show also under cooling ... they should not
// say cooling but staked in the badge"). Root cause: a staked card has never been
// unstaked, so it has no `stake_end_date`, which used to collapse into the same
// "not ready" rendering as a genuinely cooling or unrecognised-class card
// (`ceres/candidates.py`'s `cooldown_state`, see its docstring). The gate itself
// (`ready`) is unchanged and still a hard refusal -- only the LABEL changes here.
// `candidate.cooldown.state` now names the real condition directly, so no
// heuristics live in the renderer.
// Follow-up (the owner, same session): "'staked on I-294', which is an internal ID
// that I cannot read. It should say the actual Plot number, i.e. 65-10-88."
// `I-294` is the `deed_uid` prefix, an internal key. Only `candidate.source_plot`
// (the source plot's real `facts.plot` number, e.g. "65-10-88", added server-side
// in `ceres.candidates.serialize_candidate`) is shown; `source_deed_uid` stays
// internal (staging/lookup only, never rendered as text).
// Also: "the cooldown badge should be cleaned up, as it uses a technical time
// stamp which should be human readable but include the actual time, not just
// date. Seconds are redundant." `cooldown.ready_at` stays a UTC ISO string
// end-to-end (API/tests depend on it) -- only the DISPLAY changes here, rendered
// in the viewer's LOCAL time (a UTC instant shown as if local would silently
// mislead by hours, exactly the kind of error this fix exists to prevent) with
// an explicit timezone name so "which clock is this" is never ambiguous. Seconds
// dropped per his instruction; a relative form ("in 2d 4h") is ADDED, not a
// replacement -- he asked specifically for the actual time.
export function humanizeInstant(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  // dateStyle+timeStyle combined with timeZoneName throws in this engine
  // ("Invalid option : option", verified live) -- format the date/time and the
  // timezone name separately and concatenate, so the reader always sees an
  // explicit clock (e.g. "PDT") rather than silently losing it to a caught
  // exception. Seconds are never present: 'short' timeStyle omits them.
  const absoluteDateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  let tzLabel = '';
  try {
    const zonePart = new Intl.DateTimeFormat(undefined, { timeZoneName: 'short' }).formatToParts(date)
      .find((part) => part.type === 'timeZoneName');
    if (zonePart) tzLabel = ` ${zonePart.value}`;
  } catch (err) { /* no timezone name available in this engine; the local date/time still stands on its own */ }
  const absolute = `${absoluteDateTime}${tzLabel}`;
  const diffMinutes = Math.round((date.getTime() - Date.now()) / 60000);
  const past = diffMinutes < 0;
  const totalMinutes = Math.abs(diffMinutes);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const parts = [];
  if (days) parts.push(`${days}d`);
  parts.push(`${hours}h`);
  const relative = `${past ? '' : 'in '}${parts.join(' ')}${past ? ' ago' : ''}`;
  return { absolute, relative };
}
