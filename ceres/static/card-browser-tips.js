// card-browser-tips.js -- : the tooltip models for
// every icon on a /cards tile and in its filter row, drawn by the board's own tooltip engine (tooltip.js, which
// imports only escape.js and state.js -- never the board graph). Wording follows the board: icons.js
// rarityGemHtml / setIconOnlyHtml ("Rarity: …", "Set: …" + "Edition: …"), and the plot tiles' ability tooltips
// (badges.js abilityTipModel / abilityRowText). badges.js imports board.js, so the ability text is carried here
// for the raw card rows /api/cards sends (no plot, so no Toil and Kin pairing status); the parity test in
// tests/ceres/test_ir9173_card_browser.py runs both against the same entries and fails if they ever drift.
// No uid ever reaches a tooltip: plots are shown by their label, cards by their name.
import { abilityTipAttrs, tipAttrs, tipIcon } from './tooltip.js';

const RESOURCE_ICON = {
  grain: 'resources/grain.png', wood: 'resources/wood_200.webp', stone: 'resources/stone_200.webp',
  iron: 'resources/iron_200.webp', aura: 'resources/aura_200.webp', research: 'resources/research.png', sps: 'resources/sps.png',
};
const DEC_ICON = 'land/token-dec.png';
const SPLINTERS = ['fire', 'water', 'earth', 'life', 'death', 'dragon', 'neutral'];
const RARITIES = ['common', 'rare', 'epic', 'legendary'];

const ABILITY_NAME = {
  GRAIN: 'Bountiful Grain', WOOD: 'Bountiful Wood', STONE: 'Bountiful Stone',
  IRON: 'Bountiful Iron', AURA: 'Bountiful Aura', RESEARCH: 'Bountiful Research', SPS: 'Bountiful SPS',
  BLOODLINE: 'Toil and Kin', DD: 'Dark Discount', RATIONING: 'Rationing',
  RATIONING_LITE: 'Light Rationing', ENERGIZED: 'Energized', LL: "Labor's Luck",
  ALTERATION: 'Alteration', GLINT_RECOVERY: 'Glint Recovery', FORTUNE_SEEKER: 'Fortune Seeker',
  STATIC_CHARGE: 'Static Charge',
};
const ABILITY_SLUG = {
  GRAIN: 'bountiful-grain', WOOD: 'bountiful-wood', STONE: 'bountiful-stone', IRON: 'bountiful-iron', AURA: 'bountiful-aura',
  BLOODLINE: 'toil-and-kin', DD: 'dark-discount', RATIONING: 'rationing', RATIONING_LITE: 'light-rationing',
  ENERGIZED: 'energized', LL: 'labors-luck', ALTERATION: 'alteration', GLINT_RECOVERY: 'glint-recovery', FORTUNE_SEEKER: 'fortune-seeker',
};
const RESOURCE_ABILITY = { GRAIN: 'Grain', WOOD: 'Wood', STONE: 'Stone', IRON: 'Iron', AURA: 'Aura', RESEARCH: 'Research', SPS: 'SPS' };

// format.js EDITION_LABEL / editionDisplayLabel (the board's "Edition: …" tooltip line).
const EDITION_LABEL = {
  0: 'Alpha', 1: 'Beta', 2: 'Promo', 3: 'Reward', 4: 'Untamed', 5: 'Dice', 6: 'Gladius',
  7: 'Chaos', 8: 'Riftwatchers', 9: 'Nightmare', 10: 'Soulbound', 11: 'Soulkeep Promo',
  12: 'Rebellion', 13: 'Soulbound Rebellion', 14: 'Conclave', 15: 'Foundations',
  16: 'Foundations Soulbound', 17: 'Extra', 18: 'Soulbound Conclave', 19: 'Land',
  20: 'Escalation', 21: 'Verico', '-1': 'Orb',
};
const EDITION_FULL_NAME = {
  5: 'ΛZMΛRÉ Dice', 7: 'Chaos Legion', 8: 'Riftwatchers', 10: 'Chaos Legion Soulbound',
  14: 'Conclave Arcana', 21: 'Visions of Verico',
};

export function cbEditionLabel(edition) {
  if (edition === null || edition === undefined || edition === '') return '';
  const id = Number(edition);
  return EDITION_FULL_NAME[id] || EDITION_LABEL[id] || '';
}

function cap(value) {
  const text = String(value || '');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// ── identity icons (icons.js wording) ───────────────────────────────────────
export function cbSplinterTip(splinter) {
  const key = String(splinter || '').toLowerCase();
  if (!SPLINTERS.includes(key)) return '';
  return tipAttrs({ title: `Splinter: ${cap(key)}`, icon: `splinter/${key}.svg`, peer: 'icons_identity' });
}

export function cbRarityTip(rarity) {
  const key = String(rarity || '').toLowerCase();
  if (!RARITIES.includes(key)) return '';
  return tipAttrs({ title: `Rarity: ${cap(key)}`, icon: `rarity/gem-${key}.svg`, peer: 'icons_identity' });
}

// `iconFile` is the set icon's own file (sets/<file>.svg), `label` the page's set name; the edition line is shown
// only when it says something the set name does not (Chaos -> "Chaos Legion", Land -> "Visions of Verico").
export function cbSetTip(label, iconFile, edition = null) {
  if (!label) return '';
  const fullEdition = cbEditionLabel(edition);
  const editionName = fullEdition && fullEdition !== label ? fullEdition : '';  // no "Edition: Alpha" under "Set: Alpha"
  return tipAttrs({
    title: `Set: ${label}`,
    body: editionName ? `Edition: ${editionName}` : '',
    icon: iconFile ? `sets/${iconFile}.svg` : '',
    peer: 'icons_identity',
  });
}

// ── tags on the card art ────────────────────────────────────────────────────
const FOIL_TIP = {
  1: { title: 'Gold foil' },
  2: { title: 'Gold Arcane foil' },
  3: { title: 'Black foil', body: 'Always max level.' },
  4: { title: 'Black Foil Arcane', body: 'Always max level.' },
};
export function cbFoilTip(foil) {
  const tip = FOIL_TIP[foil];
  return tip ? tipAttrs(tip) : '';
}

export function cbMaxedTip(card) {
  const level = card?.max_level ? `Level ${card.level ?? card.max_level} of ${card.max_level}. ` : '';
  return tipAttrs({ title: 'Max level', body: `${level}More CC adds no Land PP.` });
}

export function cbCoolingTip(until) {
  const when = until && Number.isFinite(Date.parse(until))
    ? new Date(until).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
    : '';
  return tipAttrs({
    title: 'Cooling down',
    body: when ? `Unstaked recently. Ready to stake again on ${when}.` : 'Unstaked recently; the end of the cooldown is not known.',
    severity: 'caution',
  });
}

// ── status badges ───────────────────────────────────────────────────────────
export function cbPlotTip(label, territory) {
  return tipAttrs({ title: `Plot ${label}${territory ? ` · ${territory}` : ''}`, body: 'Opens the plot on the board.' });
}

export function cbStakedTip() {
  return tipAttrs({ title: 'Staked on land', body: "This copy works a plot; the plot's address is not in the stored board." });
}

const UNAVAILABLE_TIP = {
  Soulbound: 'Still bound to this account. Only a fully unbound copy can be staked on land.',
  'Listed for sale': 'Listed on the market. Cancel the listing to stake it.',
  'Rented out': 'Rented out to another player. It cannot be staked until the rental ends.',
  Delegated: 'Delegated to another player. Undelegate it to stake it.',
  Locked: 'Locked by the game. It cannot be staked right now.',
  'Dead in Survival': 'Died in Survival mode. It cannot be staked until it revives.',
  'Not available': 'The game reports this copy as not available for land.',
};
export function cbUnavailableTip(label) {
  if (!label) return '';
  return tipAttrs({ title: label, body: UNAVAILABLE_TIP[label] || UNAVAILABLE_TIP['Not available'], severity: 'danger' });
}

// ── PP boxes and the CC line ────────────────────────────────────────────────
export function cbPpTip(card, maxLine) {
  return tipAttrs({ title: 'Current Land base PP', body: card?.no_pp ? 'This card gives no Land PP.' : `This copy at its level and CC; ${maxLine}.` });
}

export function cbPpPerCcTip() {
  return tipAttrs({ title: 'PP per CC', body: 'Land base PP ÷ CC, counted up to max level: CC combined past max level adds no PP.' });
}

export function cbCcTip(card) {
  if (card?.bcx_max === 0) return tipAttrs({ title: 'CC', body: 'Collection power of this copy. A black foil is always max level.' });
  return tipAttrs({ title: 'CC / max CC', body: 'Collection power of this copy, over the CC it needs for max level.' });
}

// ── land abilities (badges.js abilityTipModel / abilityRowText, raw card rows) ──
export function cbAbilityIconPath(code) {
  if (code === 'RESEARCH') return RESOURCE_ICON.research;
  if (code === 'SPS') return RESOURCE_ICON.sps;
  return ABILITY_SLUG[code] ? `abilities/ability_${ABILITY_SLUG[code]}.png` : null;
}

function abilityRate(entry) {
  return Array.isArray(entry) && typeof entry[1] === 'number' ? entry[1] : null;
}
function signedRate(rate) {
  return rate === null ? '' : `${rate >= 0 ? '+' : '−'}${Math.abs(rate * 100).toFixed(0)}%`;
}
function reductionRate(rate) {
  return rate === null ? '' : `−${Math.abs(rate * 100).toFixed(0)}%`;
}
function resourceWorksite(resource) {
  const icon = tipIcon(RESOURCE_ICON[resource.toLowerCase()]);
  const place = resource === 'Aura' ? 'Aura Lab' : `${resource} worksite`;
  const article = /^[AEIOS]/.test(resource) ? 'an' : 'a';
  return `${article} ${icon}${place}`;
}
function decodeAbilityBody(entry) {
  const code = Array.isArray(entry) ? entry[0] : entry;
  const rate = abilityRate(entry);
  const subType = Array.isArray(entry) && entry.length > 2 ? entry[2] : null;
  if (!Object.prototype.hasOwnProperty.call(ABILITY_NAME, code)) return 'The effect of this ability is not described in the available details.';
  const effects = [];
  if (subType) effects.push(String(subType));
  if (rate !== null) effects.push(`${rate >= 0 ? '+' : ''}${(rate * 100).toFixed(0)}%`);
  return effects.join(' ') || 'No additional effect described.';
}
function abilityRowText(entry) {
  const code = Array.isArray(entry) ? entry[0] : entry;
  const rate = abilityRate(entry);
  if (RESOURCE_ABILITY[code]) return `${signedRate(rate)} on ${resourceWorksite(RESOURCE_ABILITY[code])}`.trim();
  if (code === 'DD') return `${reductionRate(rate)} ${tipIcon(DEC_ICON)}DEC stake needed`.trim();
  const lessGrain = `Workers eat ${rate === null ? '' : `${Math.abs(rate * 100).toFixed(0)}% `}less ${tipIcon(RESOURCE_ICON.grain)}Grain`;
  if (code === 'RATIONING') return lessGrain;
  if (code === 'RATIONING_LITE') return `${lessGrain}\nup to 20,000 Base PP`;
  if (code === 'ENERGIZED') return "Powers this plot's worker slots";
  if (code === 'LL') return 'Foil Land Card chance per harvest';
  if (code === 'ALTERATION') return 'Monthly skin airdrop';
  if (code === 'GLINT_RECOVERY') return 'Daily Glint airdrop chance';
  if (code === 'FORTUNE_SEEKER') return 'Weekly foil Verico card chance';
  return decodeAbilityBody(entry);
}

export function cbAbilityTipModel(entry) {
  const code = Array.isArray(entry) ? entry[0] : entry;
  const rate = abilityRate(entry);
  const icon = cbAbilityIconPath(code);
  const title = ABILITY_NAME[code] || code;
  if (code === 'BLOODLINE') return { title, body: decodeAbilityBody(entry), icon };
  if (RESOURCE_ABILITY[code]) return { title, icon, body: `${signedRate(rate)} production on ${resourceWorksite(RESOURCE_ABILITY[code])} · plot-wide, best holder counts`.trim() };
  if (code === 'DD') return { title, icon, body: `${abilityRowText(entry)} · no effect with a Runi` };
  if (code === 'STATIC_CHARGE') return { title: `${title}${rate === null ? '' : ` ${signedRate(rate)}`}`, icon, body: 'Effect not documented yet.' };
  if (['ALTERATION', 'GLINT_RECOVERY', 'FORTUNE_SEEKER'].includes(code)) return { title, icon, body: `${abilityRowText(entry)} · works as soon as the card is staked` };
  return { title, icon, body: abilityRowText(entry) };
}

// The plot tiles' ability icon tooltip (icons.js abilityIconHtml with the 'tile_ability_icons' peer).
export function cbAbilityTip(entry) {
  return abilityTipAttrs({ ...cbAbilityTipModel(entry), peer: 'tile_ability_icons' });
}
