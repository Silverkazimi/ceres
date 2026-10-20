import { abilityTipModel, bloodlineStatus, decodeAbilityEntry } from './badges.js';
import { esc, setNameLabel } from './format.js';
import { ASSET_BASE } from './state.js';
import { abilityTipAttrs, tipAttrs, tipIcon } from './tooltip.js';

export const FILL_TRAY_ICON = '<svg class="hdr-ic fill-tray-icon" viewBox="0 0 24 24" aria-hidden="true"><path class="f" d="M4 4.5A1.5 1.5 0 0 1 5.5 3h13A1.5 1.5 0 0 1 20 4.5v9A1.5 1.5 0 0 1 18.5 15h-13A1.5 1.5 0 0 1 4 13.5z"/><path class="d" d="M6.5 7.5h11M6.5 11h7"/><path class="a h" d="M12 17v4m0 0-2.5-2.5M12 21l2.5-2.5"/></svg>';

// (maintainer ruling 2026-09-19): the expanded plot's and slot picker's
// Close buttons become an icon-only red chevron-up on a semi-transparent
// ground -- an inline SVG, never the .ceres-chevron mask element (that one
// is a generic down-pointing toggle indicator, not a close affordance).
export const CHEVRON_CLOSE_ICON = '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 10l4-4 4 4"/></svg>';

// Rarity gems replace the plain rarity text (the owner: "the four gems for
// rarities instead of text"). Rarity name kept as alt/title for accessibility.
// The gem set is common/rare/epic/legendary only -- no mythic asset exists on the
// CDN (checked, board-fix batch; player0001's account has 2 mythic plots, its
// two kingdom/Keep-Castle plots). An unmapped rarity must degrade to its name as
// plain text, never an <img> that 404s into a broken-image icon -- same defensive
// rule for any future rarity this table doesn't yet know about.
const RARITY_GEM_FILE = new Set(['common', 'rare', 'epic', 'legendary']);
export function rarityGemHtml(rarity, tooltipModel = null) {
  if (!rarity) return '—';
  const key = String(rarity).toLowerCase();
  const label = key.charAt(0).toUpperCase() + key.slice(1);
  const tooltip = typeof tooltipModel === 'object' && tooltipModel
    ? { ...tooltipModel }
    : { title: `Rarity: ${label}`, icon: tooltipIconPath('rarity', key), peer: 'icons_identity' };
  if (!RARITY_GEM_FILE.has(key)) return `<span class="rarity-text"${tipAttrs(tooltip)}>${esc(label)}</span>`;
  return `<img class="rarity-gem" src="${ASSET_BASE}/rarity/gem-${key}.svg" alt="${label}"${tipAttrs(tooltip)}>`;
}

// Mythic rarity, plot-row Rarity cell only ( board-fix batch amendment
// the owner: "Use the castle and keep icon for the rarity icon (which we just
// removed). It is not quite accurate as rarity but should look great at that
// spot and makes sense") -- every mythic plot in the live data IS a Keep or
// Castle (both of player0001's kingdom plots), so the mapping is exact in
// practice even though it isn't literally a rarity gem. Uses the `On` icon
// variant, keyed by `worksite_type`, so this cell reads differently from the
// Type cell (which already uses the `Off` variant of the same castle/keeps
// family, established convention from item 2/KINGDOM_ICON_FILE above)
// same subject, visually distinguished; Type also carries a text label
// ("Keep"/"Castle") while this cell stays icon-only like the other rarities.
// A mythic plot whose worksite_type doesn't resolve to either (not yet
// observed) falls through to the plain-text defensive rule in rarityGemHtml
// above -- this only ever ADDS an icon for the two known cases, never removes
// the never-404 guarantee for an unmapped one.
// the house-style
// hourglass (assets/land/status-hourglass.svg, gold with the halo like status-castle/keeps) for the build countdown
// states. New path, so the 30-day immutable asset cache needs no buster.
// width/height reserve the 14x18 box before the SVG arrives, so a Building badge never widens after its column was sized.
export const HOURGLASS_IMG = `<img class="status-hourglass" src="${ASSET_BASE}/land/status-hourglass.svg" width="14" height="18" alt="" aria-hidden="true">`;
const MYTHIC_RARITY_ICON = { KEEP: 'land/status-keeps-on.svg', CASTLE: 'land/status-castle-on.svg' };
export function plotRarityCellHtml(row) {
  const rarity = String(row.rarity ?? 'Unknown');
  const rawBonus = Number(row?.rarity_production_bonus);
  const bonus = Number.isFinite(rawBonus)
    ? `+${(rawBonus * 100).toLocaleString('en-US', { maximumFractionDigits: 2 })}% for this plot's production`
    : 'The game did not report this plot’s production bonus.';
  const tooltip = {
    title: `Rarity: ${rarity.charAt(0).toUpperCase() + rarity.slice(1)}`,
    items: [{ name: 'Production bonus', effect: bonus }],
    icon: tooltipIconPath('rarity', rarity),
    peer: 'icons_identity',
  };
  if (String(row.rarity ?? '').toLowerCase() !== 'mythic') return rarityGemHtml(row.rarity, tooltip);
  const wt = String(row.worksite_type ?? '').toUpperCase();
  const file = MYTHIC_RARITY_ICON[wt];
  if (!file) return rarityGemHtml(row.rarity, tooltip);
  const tip = KINGDOM_LABEL[wt] ? `Mythic (${KINGDOM_LABEL[wt]})` : 'Mythic';
  return `<img class="rarity-gem" src="${ASSET_BASE}/${file}" alt="${esc(tip)}"${tipAttrs({ ...tooltip, title: 'Rarity: Mythic', body: KINGDOM_LABEL[wt] ? `${KINGDOM_LABEL[wt]} plot` : '', icon: file })}>`;
}

// the Rarity FILTER option icon. rarityGemHtml() above degrades an unmapped rarity to its NAME as text, which
// the option renderer then followed with the label -- "Mythicmythic". A filter option is icon-or-nothing
// gems for the four gem rarities, the castle (the same asset the Rarity cell uses for Castle plots) for
// mythic, an empty string for anything else, so the label is printed once by the renderer alone.
export function rarityOptionIconHtml(rarity) {
  const key = String(rarity ?? '').toLowerCase();
  if (key === 'mythic') return `<img class="rarity-gem" src="${ASSET_BASE}/${MYTHIC_RARITY_ICON.CASTLE}" alt="">`;
  if (!RARITY_GEM_FILE.has(key)) return '';
  return `<img class="rarity-gem" src="${ASSET_BASE}/rarity/gem-${key}.svg" alt="">`;
}

// Terrain cell: official terrain color as background (data color, defined
// once as named CSS custom properties in ceres.css -- see
// ".terrain-cell.terrain-<name>"), plus the terrain emblem glyph. Both
// light and dark theme use the same swatch; per-terrain foreground was
// picked by computed WCAG contrast, not assumed.
// only these terrains have an emblem file; an "Unsurveyed Deed" asked for terrain-emblem-unsurveyed deed.svg (404).
const TERRAIN_EMBLEMS = new Set(['badlands', 'bog', 'caldera', 'canyon', 'desert', 'forest', 'hills', 'jungle', 'lake', 'mountain', 'plains', 'river', 'swamp', 'tundra']);
// the bare emblem <img>, no trailing name -- factored out of terrainCellHtml so a
// thumbnail-only surface can reuse the exact same asset/guard without also getting
// the table cell's trailing terrain-name text. terrainCellHtml keeps its own return shape.
export function terrainEmblemHtml(terrain) {
  const key = String(terrain ?? '').toLowerCase();
  return TERRAIN_EMBLEMS.has(key) ? `<img class="terrain-emblem" src="${ASSET_BASE}/land/terrain-emblem-${key}.svg" alt="">` : '';
}
export function terrainCellHtml(terrain) {
  if (!terrain) return { cls: '', html: '—' };
  const key = String(terrain).toLowerCase();
  // Gate document #70: no tooltip -- the terrain name is printed beside the emblem.
  const html = `${terrainEmblemHtml(terrain)}${esc(terrain)}`;
  return { cls: `terrain-cell terrain-${key}`, html };
}

// Resource icon. wood/stone/iron/aura use the CDN's sized 200px webp
// grain uses the owner's own compressed PNG (see manifest.json -- 46KB vs the
// CDN original's 186KB for the same table-cell job, 4x lighter); research
// and sps only exist as unsized PNGs upstream.
const RESOURCE_ICON = {
  dec: 'land/token-dec.png',
  wood: 'resources/wood_200.webp',
  stone: 'resources/stone_200.webp',
  iron: 'resources/iron_200.webp',
  aura: 'resources/aura_200.webp',
  grain: 'resources/grain.png',
  research: 'resources/research.png',
  sps: 'resources/sps.png',
  voucher: 'resources/voucher.svg',
};
// every
// whole-word resource name in already-escaped text gets its canonical icon appended. Input MUST be escaped
// HTML (esc(...)); the icon markup is the only HTML this adds. "Vouchers" and plurals are matched by stem.
// a resource word that starts a WORKSITE name ("Grain Farm", "Research Hut", "Aura Lab"…) is a
// building, not a resource mention — no icon inside the name.
const RESOURCE_MENTION_RE = /\b(Grain|Wood|Stone|Iron|Research|Aura|SPS|DEC|Vouchers?)\b(?!\s+(?:Farm|Hut|Lab|Mine|Camp|Quarry|Works|Mill)\b)/g;
export function decorateResourceMentions(escapedText) {
  return String(escapedText ?? '').replace(RESOURCE_MENTION_RE, (word) => {
    const file = RESOURCE_ICON[word.toLowerCase().replace(/s$/, '')] || RESOURCE_ICON[word.toLowerCase()];
    return file ? `${word}<img class="res-inline-icon" src="${ASSET_BASE}/${file}" alt="" aria-hidden="true">` : word;
  });
}
// SPS is an acronym (Splintershards), not a word -- plain title-casing renders it "Sps".
// Everything else in RESOURCE_ICON is an ordinary word and title-cases correctly on its own.
const RESOURCE_LABEL_OVERRIDE = { dec: 'DEC', sps: 'SPS' };
// the centred-square Keep/Castle crop alone (), factored out of resourceIconHtml's
// tax branch so a thumbnail-only surface can reuse the exact same asset without also
// getting the table cell's tooltip/label markup. Returns null when the terrain has no crop (an
// unmapped terrain) -- the caller decides the no-art fallback, never a guessed path.
export function taxWorksiteArtPath(worksiteType, terrain) {
  const type = String(worksiteType ?? '').toUpperCase();
  const terrainKey = String(terrain ?? '').toLowerCase();
  if ((type !== 'KEEP' && type !== 'CASTLE') || !TERRAIN_EMBLEMS.has(terrainKey)) return null;
  // (2026-09-14): icons recropped to a centred square; ?r= busts the 30-day immutable asset cache.
  return `${ASSET_BASE}/land/worksites/productionBuildingUniform/tax/${type.toLowerCase()}/${terrainKey}_icon.webp?r=20260914a`;
}
export function resourceIconHtml(resource, worksiteType, tooltipOverride = null, terrain = null) {
  if (!resource) return '—';
  const key = String(resource).toLowerCase();
  // a tax plot's resource is TAX, which had no icon, so the
  // cell printed the word "Tax". It shows the Keep or Castle icon the Rarity/Type cells already use.
  if (key === 'tax') {
    const type = String(worksiteType || '').toUpperCase();
    // the building itself, on its terrain.
    const croppedPath = taxWorksiteArtPath(worksiteType, terrain);
    const file = croppedPath ? null : (type === 'KEEP' ? 'land/status-keeps-on.svg' : 'land/status-castle-on.svg');
    const name = type === 'KEEP' ? 'Keep' : 'Castle';
    const tooltip = tooltipOverride || { title: name, body: 'Collects tax from the harvests in its tract or region.', peer: 'resource_identity' };
    // landscape building art gets a bigger box (CSS .resource-icon.is-tax) so the building reads at the same visual size as the other resource icons; the margin-box stays 1.3 rem so row height is untouched.
    return `<img class="resource-icon is-tax" src="${croppedPath || `${ASSET_BASE}/${file}`}" alt="${name}"${tipAttrs(tooltip)}>`;
  }
  const file = RESOURCE_ICON[key];
  const label = RESOURCE_LABEL_OVERRIDE[key] || (key.charAt(0).toUpperCase() + key.slice(1));
  if (!file) return esc(label);
  const icon = tooltipIconPath('resource', key);
  // Gate document #71/#72: say what the icon counts, never the bare resource word.
  const tooltip = tooltipOverride ? tooltipOverride : worksiteType
    ? { title: String(worksiteType).toLowerCase().replace(/[_-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase()), body: `Produces ${tipIcon(icon)}${label}`, icon, peer: 'resource_identity' }
    : { title: `${label} produced per hour`, icon, peer: 'resource_identity' };
  return `<img class="resource-icon" src="${ASSET_BASE}/${file}" alt="${label}"${tipAttrs(tooltip)}>`;
}

// Plot-type mark: icon + name text, one icon per type EXCEPT magical which
// gets two (the owner, 2026-08-21 polish pass, final ruling after two rounds of
// correction on this same cell -- read all three notes below if touching this
// again, the history matters)
// Round 1 finding (still true, just not the final word): the ORIGINAL
// `land/status-{magical,occupied}-{on,off}.svg` assets are the game's own
// side-menu FILTER TOGGLES (`ui_elements/lands/sideMenu/*.svg`), not
// purpose-built plot-type badges -- the old code rendered both On/Off states
// of two of them in every cell regardless of actual type ("two icons
// always"). Round 2 (the owner, same day): corrected the read on this family
// all FOUR plot types (natural/magical/occupied/kingdom) have their own
// On/Off pair in this exact asset family, confirmed live (8/8 URLs HTTP 200
// the owner supplied the paths directly), and the **Off** variant of each is
// the one to use as the neutral, static-display type mark -- explicit
// instruction, not a guess. `kingdom` is a real 4th plot type the game
// supports (absent from the owner's current 130 plots -- natural 90/magical
// 31/occupied 9 -- but handled unconditionally below so a future Kingdom
// plot renders correctly rather than falling through to "Unknown"). See
// manifest.json for the visual-similarity note on natural (still the same
// checkmark-glyph family as magical/occupied -- flagged for the record, used
// per instruction regardless) and kingdom (a distinct crown glyph).
// Cell contents, per type (icon + visible name text on all four; the owner item
// 6, "Type cell shows ICON AND TEXT, the type mark plus its name")
// natural / occupied / kingdom -> that type's own Off-variant icon + name.
// magical -> TWO icons in order (the owner item 7, explicitly supersedes the
// round-1 "element icon replaces the type icon" reading): the magical
// type's own Off-variant icon FIRST, then the plot's own element icon
// (splinter/<element>.svg, keyed off `magic_type`) SECOND, then the name.
const TYPE_ICON_FILE = {
  natural: 'land/status-natural-off.svg',
  magical: 'land/status-magical-off.svg',
  occupied: 'land/status-occupied-off.svg',
  kingdom: 'land/status-kingdom-off.svg', // fallback only -- see KINGDOM_ICON_FILE below
};
// Kingdom is not one type -- `plot_status` is 'kingdom' for BOTH Keep and Castle
// plots; the distinction lives in `worksite_type` ( board-fix batch, the owner
// "Use Keep and Castle for Type, instead of Kingdom for both" -- confirmed live
// against player0001's account: 41-2-61 worksite_type KEEP, 41-5-94 CASTLE). Keep's
// own icon family is "keeps" (PLURAL -- team-lead verified live 2026-08-21
// keepsOn.svg/keepsOff.svg exist on the CDN, keepOn.svg singular 403s), a
// worksite-specific icon distinct from the generic "kingdom" plot-status icon
// status-keeps-off.svg for Keep, status-castle-off.svg for Castle, both fetched
// 2026-08-21 -- see assets/manifest.json. A kingdom plot whose worksite_type
// doesn't match either (not yet observed) falls back to the generic kingdom
// icon/label rather than rendering nothing.
const KINGDOM_ICON_FILE = { KEEP: 'land/status-keeps-off.svg', CASTLE: 'land/status-castle-off.svg' };
const KINGDOM_LABEL = { KEEP: 'Keep', CASTLE: 'Castle' };
// Order is now fixed across ALL four types (the owner, 2026-08-21, third pass on
// this cell: "Move the Magical Type icon behind the text for vertical
// consistency... Icon + Text + either nothing or icon"). Every row's type
// icon and text now sit at the SAME horizontal position regardless of type
// magical's element icon moved from 2nd position (right after the type icon)
// to trailing, after the text, so the column reads cleanly top to bottom
// instead of magical rows alone being shunted right by an extra leading icon.
export function plotTypeTipAttrs(row) {
  const status = String(row?.plot_status ?? '').toLowerCase();
  if (status !== 'magical') return '';
  const typeIcon = tooltipIconPath('type', status, row?.worksite_type);
  const element = row?.magic_type ? String(row.magic_type).toLowerCase() : '';
  const elementLabel = element ? element.charAt(0).toUpperCase() + element.slice(1) : 'not reported';
  const elementIcon = tooltipIconPath('splinter', element);
  return tipAttrs({
    title: 'Magical plot',
    icon: typeIcon,
    items: [
      { icon: typeIcon, name: 'Plot type', effect: 'Magical', nowrap: true },
      { icon: elementIcon, name: 'Element', effect: elementLabel },
    ],
    peer: 'icons_identity',
  });
}

export function typeMarkHtml(status, magicType, worksiteType, sharedTooltip = false) {
  const s = String(status ?? '').toLowerCase();
  let label = status ? status.charAt(0).toUpperCase() + status.slice(1) : 'Unknown';
  let file = TYPE_ICON_FILE[s];
  if (s === 'kingdom') {
    const wt = String(worksiteType ?? '').toUpperCase();
    file = KINGDOM_ICON_FILE[wt] || file;
    label = KINGDOM_LABEL[wt] || label;
  }
  if (!file) return `<span${sharedTooltip ? '' : tipAttrs({ title: 'Plot type not reported', peer: 'icons_identity' })}>—</span>`;
  // Icon-only (board-polish batch 19, item 1 -- the owner: "Remove text in the
  // table (not header) for: Type... Move the text into the tooltip"). The
  // icon already carried its own tipAttrs(label) tooltip before this change
  // (it was redundant with the visible text label); removing the label span
  // just means that tooltip is now the ONLY place the name shows, reusing the
  // board's existing initTooltips() mechanism rather than a new one.
  // (maintainer: "make the type column show only one icon ... remove [the magical icon] from all columns and keep only the
  // splinter icons"): a magical plot with a known element shows the element's splinter icon alone; the magical icon stays in the
  // type filter's own options.
  if (s === 'magical') {
    const element = magicType ? String(magicType).toLowerCase() : null;
    const elementIcon = element ? tooltipIconPath('splinter', element) : null;
    if (elementIcon) {
      const elLabel = element.charAt(0).toUpperCase() + element.slice(1);
      // The element tooltip never says "Magical" (tests/ceres/test_tooltip_icons.py, an earlier ruling): the splinter icon itself is the mark.
      return `<img class="type-mark type-mark-element" src="${ASSET_BASE}/${elementIcon}" alt="${esc(`${elLabel} plot`)}"${sharedTooltip ? '' : tipAttrs({ title: `Plot type: ${elLabel} plot`, icon: elementIcon, peer: 'icons_identity' })}>`;
    }
  }
  const html = `<img class="type-mark" src="${ASSET_BASE}/${file}" alt="${esc(label)}"${sharedTooltip ? '' : tipAttrs({ title: `Plot type: ${label}`, icon: tooltipIconPath('type', s, worksiteType), peer: 'icons_identity' })}>`;
  return html;
}

// Icon-only Type mark for the Type filter DROPDOWN (board-polish batch 9
// item 8) -- the main-table Type cell (typeMarkHtml above) always pairs its
// icon with visible text, but a <select> OPTION already carries its own text
// node (the raw filter value, e.g. "natural"), so the dropdown decoration
// only needs the bare icon. Reuses the same TYPE_ICON_FILE table so the two
// never drift -- an unrecognised value renders no icon, same fallback shape
// as typeMarkHtml.
export function typeIconOnlyHtml(status) {
  const file = TYPE_ICON_FILE[String(status ?? '').toLowerCase()];
  return file ? `<img class="csel-opt-icon" src="${ASSET_BASE}/${file}" alt="">` : '';
}

// Terrain emblem for the Terrain filter DROPDOWN (board-polish batch 9, item
// 8) -- the option's own <li> gets the terrain-colour BACKGROUND FILL (same
// terrain-<name> tokens the table cells use, via the `terrain-<key>` class
// customSelect.js's decorateOption hook adds -- see initFilterDropdowns()
// below) plus this emblem glyph before the text.
export function terrainOptionIconHtml(terrain) {
  const key = String(terrain ?? '').toLowerCase();
  return TERRAIN_EMBLEMS.has(key) ? `<img class="csel-opt-icon" src="${ASSET_BASE}/land/terrain-emblem-${key}.svg" alt="">` : '';
}

// Splinter icon (worker rows, idle cards) -- direct 1:1 with the splinter
// name already on the row (Fire/Water/Earth/Life/Death/Dragon/Neutral).
// `large` (worker-subtable SPL column only, board-polish 2026-08-21: "increase
// spl icon size") adds a size-only modifier class on top of the base
// `splinter-icon` class rather than swapping it out -- the idle-cards table's
// own splinter icons (splinterIconHtml(c.splinter), no `large` arg) keep the
// original size untouched, and both variants still share the one asset file
// and the one tooltip mechanism.
export function cardSplinters(card) {
  const values = Array.isArray(card?.splinters) && card.splinters.length ? card.splinters : [card?.splinter];
  return [...new Set(values.filter(value => typeof value === 'string' && value.trim()).map(value => value.trim().toLowerCase()))];
}

export function cardSplinterTipHtml(card) {
  return cardSplinters(card).map(value => `${tipIcon(tooltipIconPath('splinter', value))}${esc(value.charAt(0).toUpperCase() + value.slice(1))}`).join(' / ');
}

export function splinterIconHtml(splinter, large, options = {}) {
  if (!splinter) return '—';
  const key = String(splinter).toLowerCase();
  const cls = large ? 'splinter-icon splinter-icon-lg' : 'splinter-icon';
  if (options.decorativeImage) {
    if (!['fire', 'water', 'earth', 'life', 'death', 'dragon', 'neutral'].includes(key)) return '';
    return `<img class="${cls}" src="${ASSET_BASE}/splinter/${key}.svg" alt="" aria-hidden="true" focusable="false">`;
  }
  return `<img class="${cls}" src="${ASSET_BASE}/splinter/${key}.svg" alt="${esc(splinter)}"${tipAttrs({ title: `${splinter} splinter`, icon: tooltipIconPath('splinter', key), peer: 'icons_identity' })}>`;
}

// Icon selection now keys off the main-set label above (lowercased) rather
// than `card_set`, so the set ICON always matches whatever the label text
// now shows -- e.g. a Riftwatchers card (main set "Chaos") shows the same
// chaos-legion icon as its "Chaos" label, not a mismatched Riftwatchers-only
// icon (none exists in the production bundle, per manifest.json's `missing`
// block) alongside old edition-precise text.
const MAIN_SET_ICON = {
  alpha: 'alpha', beta: 'beta', chaos: 'chaos-legion', conclave: 'conclave-arcana',
  foundations: 'foundations', eternal: 'eternal-land', rebellion: 'rebellion', untamed: 'untamed',
};
function setIconHtml(cardSet, edition) {
  const label = setNameLabel(cardSet, edition);
  const file = MAIN_SET_ICON[label.toLowerCase()];
  const icon = file
    ? `<img class="set-icon" src="${ASSET_BASE}/sets/${file}.svg" alt="${esc(label)}"${tipAttrs({ title: `${label} set`, icon: tooltipIconPath('set', cardSet, edition), peer: 'icons_identity' })}>`
    : '';
  return `${icon}<span class="set-name-label">${esc(label)}</span>`;
}

// Icon-only variant of setIconHtml() above, worker-subtable Edition column only
// (board-polish, 2026-08-21: "apply the same strategy for worker tables as for
// main... reduce text into tooltip for edition, keep icon, center icons" -- same
// text-into-tooltip treatment typeMarkHtml() already applies to the main table's
// Type/Totem/Title columns). Deliberately a SEPARATE function rather than a
// behaviour change to setIconHtml() itself, whose own text-label rendering
// (the owner's prior instruction: "show the set/edition name as text on all
// cards") stays the default elsewhere. Reuses the same tipAttrs()
// initTooltips() mechanism
// no bare `title=` attribute, no second tooltip system. Falls back to a
// tooltip-bearing text span (no icon markup) when no icon file exists for the
// label, same defensive shape as typeMarkHtml()'s own `if (!file)` branch, so
// an unrecognised set is never silently blank.
export function setIconOnlyHtml(cardSet, edition, editionDetail = '') {
  const label = setNameLabel(cardSet, edition);
  const file = MAIN_SET_ICON[label.toLowerCase()];
  const source = file ? `${ASSET_BASE}/sets/${file}.svg` : `${ASSET_BASE}/sets/edition-generic.svg`;
  const body = editionDetail ? `Edition: ${editionDetail}` : '';
  return `<img class="set-icon${file ? '' : ' set-icon-generic'}" src="${source}" alt="${esc(label)}"${tipAttrs({ title: `Set: ${label}`, body, icon: tooltipIconPath('set', cardSet, edition), peer: 'icons_identity' })}>`;
}

// Totem tier icon -- direct 1:1 with the totem's own rarity field. `tip`
// defaults to the bare rarity (the original behaviour) but the main-table
// Totem cell (fmtTotem below, board-polish batch 19 item 1) passes the full
// name+boost text that used to render as a visible label, so nothing is
// lost -- it just moves into this icon's own tooltip via the existing
// tipAttrs()/initTooltips() mechanism.
// Only 4 totem tiers have a CDN asset (checked -- same set as RARITY_GEM_FILE
// above). Some callers derive `rarity` by splitting the item's NAME on its
// first word, which is reliable when the name follows the "{Rarity} Totem"
// convention (confirmed against bonus_assets/name_lookup, e.g. "Common
// Totem") but is NOT guaranteed for every totem name Praetoria could ever
// surface -- an unrecognised first word must degrade to text, never an
// <img> that 404s into a broken-image icon (board-polish batch 19 item 7, fixing the known
// bug at this call site). Same defensive rule as rarityGemHtml.
export const TOTEM_ICON_FILE = new Set(['common', 'rare', 'epic', 'legendary']);
export function totemIconHtml(rarity, tooltipModel) {
  if (!rarity) return '';
  const key = String(rarity).toLowerCase();
  const tooltip = typeof tooltipModel === 'object'
    ? { ...(tooltipModel || {}), icon: tooltipIconPath('totem', key), peer: 'icons_boosts' }
    : { title: String(tooltipModel || rarity), icon: tooltipIconPath('totem', key), peer: 'icons_boosts' };
  if (!TOTEM_ICON_FILE.has(key)) return `<span class="rarity-text"${tipAttrs(tooltip)}>${esc(String(rarity))}</span>`;
  return `<img class="totem-icon" src="${ASSET_BASE}/totems/totem-${key}.png" alt=""${tipAttrs(tooltip)}>`;
}

// Title icon -- NAME-SPECIFIC mark, one real icon per title (board-polish
// batch 19 wiring pass, 2026-08-21, applying `work/titles-manifest.md`).
// The earlier "generic mark only" era is over: that Stage-B-era note
// documented a CDN probe that found only 3 title icon files
// (`icon_title_{proven,veteran,renown}.svg`) and concluded no name-specific
// icon existed for any other title. That conclusion was itself superseded
// a later Playwright render of the live market catalogue page
// (`splinterlands.com/market/other-items?group=collectibles&tab=titles`)
// read the real `<img src>` for all 28 titles directly off the DOM
// (`aria-label="TITLES: 0 owned out of 28 available"`), and all 28
// name-specific icons now exist in `ceres/static/assets/titles/`
// (see the manifest for the full catalogue, CDN filenames, and how the
// naming-convention probe missed 9 of them the first time). `TITLE_ICON_SLUG`
// below is that manifest's wiring spec, pasted in as-is.
// An unrecognised title name (none of the current 28, but a future 29th
// title is a real possibility) falls back to the old generic
// `title-proven.svg` mark rather than ever rendering a broken <img>
// same defensive contract as `totemIconHtml`'s TOTEM_ICON_FILE guard above.
// `tip` defaults to the bare "Title" mark (original behaviour, still used by
// the unassigned-bonus-family call site) -- the main-table Title cell
// (fmtTitle below, board-polish batch 19 item 1) passes the full name+boost
// text that used to render as a visible label.
export const TITLE_ICON_SLUG = {
  "The Untamed": "untamed", "The Praetorian": "praetorian",
  "The Grandmaster": "grandmaster", "The High Roller": "highroller",
  "The Burninator": "burninator", "The Dawnbringer": "dawnbringer",
  "The Renowned": "renown", "The Myth Hunter": "myth-hunter",
  "The Arcane": "arcane", "The Ascendant": "ascendant",
  "The Explorer": "explorer", "The Gambler": "gambler",
  "The Incinerator": "incinerator", "The Defiant": "defiant",
  "The Tower Mage": "tower-mage", "The Veteran": "veteran",
  "The Fable Scout": "fable-scout", "The Emissary": "emissary",
  "The Enigma": "enigma", "The Rising": "rising",
  "The Legionnaire": "legionnaire", "The Watcher": "watcher",
  "The Scorcher": "scorcher", "The Rebel": "rebel",
  "The Proven": "proven", "The Saga Seeker": "saga-seeker",
  "The Mystic": "mystic", "The Emergent": "emergent",
};
export function titleIconHtml(tooltipModel, name) {
  const slug = (name && TITLE_ICON_SLUG[name]) || 'proven';
  const tooltip = typeof tooltipModel === 'object'
    ? { ...(tooltipModel || {}), icon: tooltipIconPath('title', name), peer: 'icons_boosts' }
    : { title: String(tooltipModel || name || 'Title'), icon: tooltipIconPath('title', name), peer: 'icons_boosts' };
  return `<img class="title-icon" src="${ASSET_BASE}/titles/title-${slug}.svg" alt=""${tipAttrs(tooltip)}>`;
}

// Title RARITY GEM -- carries the actual tier, since title NAMES are
// evocative but opaque (the owner: "the title names are nice to have but
// unclear... that way it is clearer what bonus is used"). Titles and totems
// share boost VALUES but NOT tier LABELS -- confirmed by the owner directly
// 2026-08-21 ("there are only rare, epic and legendary titles. The Emissary
// is epic") after an initial attempt wrongly reused the totem ladder (which
// has a Common tier at 0.10; titles do not, and 0.25 is Epic for a title but
// Rare for a totem). THIS TABLE MUST NOT BE MERGED WITH THE TOTEM ONE
// see `totemIconHtml` above, a structurally separate lookup on purpose.
// Live evidence: The Proven x3 + The Mystic at boost 0.100 = Rare, The
// Emissary at boost 0.250 = Epic, The Renowned + The Untamed at boost 0.500
// = Legendary. Legendary is no longer unestablished -- confirmed 2026-08-21
// directly off the live market catalogue page (Playwright DOM read of
// `splinterlands.com/market/other-items?group=collectibles&tab=titles`
// see `work/titles-manifest.md`): the catalogue's own section headers state
// the boost per tier (Rare 10% / Epic 25% / Legendary 50%), and every
// title-tier pairing observed in the live account (Renowned/Untamed =
// Legendary) matches the catalogue exactly, 0 discrepancies. An unrecognised
// boost still renders no gem at all, with a tooltip saying so explicitly
// rather than guessing a plausible tier -- that defensive fallback stays
// it just should now be unreachable for any of the 28 real titles.
const TITLE_RARITY_GEM = { 0.1: 'rare', 0.25: 'epic', 0.5: 'legendary' };
export function titleRarityGem(boost) {
  if (boost === null || boost === undefined) return null;
  return TITLE_RARITY_GEM[boost] || null;
}

export function tooltipIconPath(kind, value, edition) {
  const key = String(value ?? '').toLowerCase();
  if (kind === 'rarity') return RARITY_GEM_FILE.has(key) ? `rarity/gem-${key}.svg` : null;
  if (kind === 'terrain') return key && TERRAIN_EMBLEMS.has(key) ? `land/terrain-emblem-${key}.svg` : null;
  if (kind === 'resource') return key === 'dec' ? 'land/token-dec.png' : RESOURCE_ICON[key] || null;
  if (kind === 'splinter') return ['fire', 'water', 'earth', 'life', 'death', 'dragon', 'neutral'].includes(key) ? `splinter/${key}.svg` : null;
  if (kind === 'type') {
    const file = key === 'kingdom'
      ? KINGDOM_ICON_FILE[String(edition ?? '').toUpperCase()] || TYPE_ICON_FILE[key]
      : TYPE_ICON_FILE[key];
    return file || null;
  }
  if (kind === 'set') {
    const file = MAIN_SET_ICON[setNameLabel(value, edition).toLowerCase()];
    return file ? `sets/${file}.svg` : null;
  }
  if (kind === 'totem') return TOTEM_ICON_FILE.has(key) ? `totems/totem-${key}.png` : null;
  if (kind === 'title') return `titles/title-${TITLE_ICON_SLUG[value] || 'proven'}.svg`;
  if (kind === 'ability') {
    if (key === 'research') return RESOURCE_ICON.research;
    if (key === 'sps') return RESOURCE_ICON.sps;
    if (key === 'static_charge') return null;
    const slug = ABILITY_ICON_SLUG[value];
    return slug ? `abilities/ability_${slug}.png` : null;
  }
  return null;
}

// NOTE: deed artwork (the 49 surveyed-deed JPGs) is deliberately NOT rendered
// anywhere in the board -- the owner, 2026-08-21: "deed artwork is for a later
// stage, if at all." The cached files and their manifest.json entries stay in
// place (inventory for a possible future stage) and still show on the
// contact sheet (assets-preview.html), but nothing in this file references
// them. Do not re-add a deedArtworkHtml()-style helper without asking first.

// Territory BACKGROUND FILL (the owner item J, 2026-08-21 polish pass, revised
// same day from an edge marker) -- applied to the EXISTING Plot cell, never a
// new Territory column (hard constraint). the owner: "use territory colors as
// background, like terrain" -- same treatment as `terrainCellHtml` below: a
// CSS class selects a full-bleed, square-cornered background + a
// SCRIPT-COMPUTED (not assumed) contrast-paired foreground, both declared as
// named CSS custom properties in ceres.css's "Land data colors" block. Full
// territory name stays in the cell's own tooltip. Pristine Northwest is pure
// white (#FFFFFF) and would otherwise vanish against a light theme -- it gets
// a full 1px border in its paired `accent_hex` (#777777, from
// manifest.json) so the cell stays visibly delimited, matching
// assets-preview.html's own territory contact sheet treatment for that swatch.
export function territorySlug(territory) {
  if (!territory) return null;
  return String(territory).toLowerCase().replace(/[^a-z]+/g, '-').replace(/^-|-$/g, '');
}
export function territoryCellHtml(territory, deedUid) {
  const slug = territorySlug(territory);
  const cls = slug ? `plot-cell territory-${slug}` : 'plot-cell';
  const tip = territory
    ? tipAttrs(deedUid ? { title: territory, body: deedUid, peer: 'territory_identity' } : { title: territory, peer: 'territory_identity' })
    : '';
  return { cls, tip };
}

// Ability CODE -> CDN icon slug (board-polish, 2026-08-21). Icons live at
// `https://d36mxiodymuqjm.cloudfront.net/website/abilities/ability_{slug}.png`
// cached locally under `ceres/static/assets/abilities/` -- fetch log and probe
// results (14/17 codes have a dedicated icon; RESEARCH/SPS/STATIC_CHARGE do not
// see below) in
// `work/ability-icons-manifest.md`. Mapping is by DIRECT NAME EQUIVALENCE
// against `ABILITY_NAME` above, not guessed from slug similarity -- every row
// below is a case-folded match of that label to the owner's supplied slug list
// (e.g. "Toil and Kin" -> `toil-and-kin`, "Bountiful Grain" -> `bountiful-grain`
// "Labor's Luck" -> `labors-luck`), and all 14 slugs resolved HTTP 200 on
// first try, so there is no ambiguity to flag for any mapped code.
// RESEARCH, SPS, and STATIC_CHARGE have no dedicated CDN asset. Reuse the
// existing resource icon deliberately as the generic ability fallback so an
// undocumented or assetless code never becomes bare text or a broken image.
export const ABILITY_ICON_FALLBACK_SLUG = 'bountiful-grain';
export const ABILITY_ICON_SLUG = {
  GRAIN: 'bountiful-grain',
  WOOD: 'bountiful-wood',
  STONE: 'bountiful-stone',
  IRON: 'bountiful-iron',
  AURA: 'bountiful-aura',
  RESEARCH: ABILITY_ICON_FALLBACK_SLUG,
  SPS: ABILITY_ICON_FALLBACK_SLUG,
  BLOODLINE: 'toil-and-kin',
  DD: 'dark-discount',
  RATIONING: 'rationing',
  RATIONING_LITE: 'light-rationing',
  STATIC_CHARGE: ABILITY_ICON_FALLBACK_SLUG,
  ENERGIZED: 'energized',
  LL: 'labors-luck',
  ALTERATION: 'alteration',
  GLINT_RECOVERY: 'glint-recovery',
  FORTUNE_SEEKER: 'fortune-seeker',
};

// Toil and Kin first, resource bonuses next, then plot-efficiency modifiers.
export const LAND_ABILITY_TIERS = Object.freeze({
  BLOODLINE: 'toil-and-kin',
  GRAIN: 'resource-bonus', WOOD: 'resource-bonus', STONE: 'resource-bonus',
  IRON: 'resource-bonus', AURA: 'resource-bonus', RESEARCH: 'resource-bonus', SPS: 'resource-bonus',
  RATIONING: 'plot-efficiency', RATIONING_LITE: 'plot-efficiency', DD: 'plot-efficiency',
  STATIC_CHARGE: 'plot-efficiency',
  ENERGIZED: 'plot-efficiency', LL: 'plot-efficiency', ALTERATION: 'plot-efficiency',
  GLINT_RECOVERY: 'plot-efficiency', FORTUNE_SEEKER: 'plot-efficiency',
});

const LAND_ABILITY_TIER_ORDER = { 'toil-and-kin': 0, 'resource-bonus': 1, 'plot-efficiency': 2 };

export function orderLandAbilities(entries) {
  const source = Array.isArray(entries) ? entries : [];
  const ranked = source.map((entry, index) => {
    const code = Array.isArray(entry) ? entry[0] : entry;
    const tier = LAND_ABILITY_TIERS[code] || 'plot-efficiency';
    return { entry, index, rank: LAND_ABILITY_TIER_ORDER[tier] };
  });
  ranked.sort((left, right) => left.rank - right.rank || left.index - right.index);
  for (let index = 1; index < ranked.length; index += 1) {
    if (ranked[index - 1].rank > ranked[index].rank) throw new Error('Land ability tier order invariant violated');
  }
  const ordered = ranked.map(({ entry }) => entry);
  // his words: "Move the alteration ability to a lower order prio and move up Dark
  // Discount. Basically switch them." Both sit in the plot-efficiency tier, where order
  // otherwise follows the card's own listing -- so when a card lists Alteration before
  // Dark Discount, swap the two and leave every other ability where it is.
  const codeOf = (entry) => (Array.isArray(entry) ? entry[0] : entry);
  const alteration = ordered.findIndex((entry) => codeOf(entry) === 'ALTERATION');
  const darkDiscount = ordered.findIndex((entry) => codeOf(entry) === 'DD');
  if (alteration !== -1 && darkDiscount !== -1 && alteration < darkDiscount) {
    [ordered[alteration], ordered[darkDiscount]] = [ordered[darkDiscount], ordered[alteration]];
  }
  return ordered;
}

export function abilityIconHtml(entry, peer = 'ability_icons') {
  const code = Array.isArray(entry) ? entry[0] : entry;
  const text = decodeAbilityEntry(entry);
  const tip = abilityTipModel(entry);
  const status = bloodlineStatus(entry);
  const icon = tooltipIconPath('ability', code);
  const statusCls = status ? (status.active ? ' ability-bloodline-active ability-bloodline-paired' : ' ability-bloodline-inactive ability-bloodline-solo') : '';
  const tipAttributes = abilityTipAttrs({ ...tip, icon, peer });
  if (!icon) return `<span class="ability-icon ability-icon-text${statusCls}"${tipAttributes}>${esc(text)}</span>`;
  return `<img class="ability-icon${statusCls}" src="${ASSET_BASE}/${icon}" alt=""${tipAttributes}>`;
}

export const CARD_CDN_BASE = 'https://d36mxiodymuqjm.cloudfront.net';
export const CARD_CDN_SET = [];
CARD_CDN_SET[0] = 'cards_v2.2';
CARD_CDN_SET[1] = 'cards_beta';
CARD_CDN_SET[2] = 'cards_v2.2';
CARD_CDN_SET[3] = 'cards_beta';
CARD_CDN_SET[4] = 'cards_untamed';
CARD_CDN_SET[5] = 'cards_untamed';
CARD_CDN_SET[6] = 'cards_gladiator';
CARD_CDN_SET[7] = 'cards_chaos';
CARD_CDN_SET[8] = 'cards_riftwatchers';
// editions 9 and 13 follow the official app bundle
// (https://splinterlands.com/assets/index-CETWKRFI.js): 9 -> cards_v2.2, 13 -> cards_soulboundrb.
// Both said `cards_soulbound` here; for every Rebellion soulbound card that folder answers 403
// which was the whole "some cards never load" population on a whale account (40 art keys).
CARD_CDN_SET[9] = 'cards_nightmare';  // Soulkeep (edition 9) art lives in cards_nightmare; cards_v2.2 answers 403
CARD_CDN_SET[10] = 'cards_soulbound';
CARD_CDN_SET[11] = 'cards_nightmare';
CARD_CDN_SET[12] = 'cards_rebellion';
CARD_CDN_SET[13] = 'cards_soulboundrb';
CARD_CDN_SET[14] = 'cards_v2.2';
CARD_CDN_SET[15] = 'cards_v2.2';
CARD_CDN_SET[16] = 'cards_v2.2';
CARD_CDN_SET[17] = 'cards_v2.2';
CARD_CDN_SET[18] = 'cards_v2.2';
CARD_CDN_SET[19] = 'cards_land';
CARD_CDN_SET[20] = 'cards_v2.2';
CARD_CDN_SET[21] = 'cards_land';
export const CARD_CDN_SOURCE_SET = {
  alpha: 'cards_v2.2', beta: 'cards_beta', chaos: 'cards_chaos', untamed: 'cards_untamed',
  rebellion: 'cards_rebellion', soulbound: 'cards_soulbound', conclave: 'cards_v2.2',
};
export const POWER_CORE_IMAGE = `${CARD_CDN_BASE}/website/icons/land/power_core.png`;
