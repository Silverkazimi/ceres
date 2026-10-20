import { BOARD, catalogNotice, noticeMessage } from './board.js';
import { cardFoilValue, esc, fmtFoil, humanizeInstant, num, regionLabel } from './format.js';
import { ASSET_BASE, LAND_CARD_IDS, VOV_ID_RANGE } from './state.js';
import { tooltipIconPath } from './icons.js';
import { tipAttrs, tipIcon } from './tooltip.js';
import { CERES_STAGE } from './staging.js';
import { cooldownReasonFragment } from './candidates.js';
import { effectiveOverCapState, effectiveUnderfilledState } from './badge-state.js';

const TOIL_AND_KIN_ICON = 'abilities/ability_toil-and-kin.png';
const ENERGIZED_ICON = 'abilities/ability_energized.png';
const DD_ICON = 'abilities/ability_dark-discount.png';
const DEC_ICON = 'land/token-dec.png';

// Gate document #49-#52 (approved ): short lines, each named ability or resource
// with its icon (). Built lazily so the icon markers resolve at render time.
const BADGE_TIP_OVERRIDE = {
  unpowered: {
    title: 'Unpowered',
    lines: () => ['Worker slots have no power, so workers earn zero.', `A Power Core or ${tipIcon(ENERGIZED_ICON)}Energized worker powers all five; a Runi powers four.`],
  },
  unpowered_worker: {
    title: 'Unpowered worker',
    // name the exact slot and card from this plot's own message ("Worker slot 5
    // (Toiling Ox) has no power: ..."); a plot without that message keeps the generic line.
    lines: (message) => {
      const who = /^(.*?) has no power/.exec(String(message || ''))?.[1];
      // its abilities are off for every worker too -- say what that costs the plot.
      const loss = /lowering the whole plot by ([\d,]+) PP/.exec(String(message || ''))?.[1];
      return [who ? `${who} is unpowered and inactive; it produces nothing (0 PP).` : 'This worker is unpowered and inactive; it produces nothing (0 PP).',
        ...(loss ? [`Its abilities are switched off too, for every worker on this plot: the plot makes ${loss} PP less.`] : []),
        `Power it with a Power Core or an ${tipIcon(ENERGIZED_ICON)}Energized worker; moving the worker costs a 72-hour cooldown.`];
    },
  },
  redundant_power_core: {
    title: 'Redundant Power Core',
    lines: () => [`An ${tipIcon(ENERGIZED_ICON)}Energized worker already powers this plot.`, 'This core could power another plot.'],
  },
  dark_discount_wasted: {
    title: `Wasted ${tipIcon(DD_ICON)}Dark Discount`,
    lines: () => [`Runi already sets this plot's ${tipIcon(DEC_ICON)}DEC stake to 0.`, 'The card would cut DEC on a plot without a Runi.'],
  },
};

export function foilBadgeHtml(card, extraClass = '') {
  const value = cardFoilValue(card);
  if (!['1', '2', '3', '4'].includes(value)) return '';
  const className = `foil-badge foil-${value}${extraClass ? ` ${extraClass}` : ''}`;
  const label = fmtFoil(card);
  return `<span class="${className}">${esc(label)}</span>`;
}

// Land/Verico ability-code -> human name (board-polish batch 9 continued
// item 9). Source: library/land-worker-abilities.md §2 (primary-sourced from
// the official @splinterlands technical-spec post, read verbatim via
// api.hive.blog). GRAIN/WOOD/STONE/IRON/AURA/RESEARCH/SPS follow the primary
// source's own "Bountiful <Resource>" naming (confirmed for the 5 codes that
// appear in player0005's live account -- Grain/Wood/Stone/Iron/Aura; RESEARCH/SPS
// are not observed live but follow the identical naming pattern, flagged here
// rather than silently assumed settled).
const ABILITY_NAME = {
  GRAIN: 'Bountiful Grain', WOOD: 'Bountiful Wood', STONE: 'Bountiful Stone',
  IRON: 'Bountiful Iron', AURA: 'Bountiful Aura', RESEARCH: 'Bountiful Research', SPS: 'Bountiful SPS',
  BLOODLINE: 'Toil and Kin', DD: 'Dark Discount', RATIONING: 'Rationing',
  RATIONING_LITE: 'Light Rationing', ENERGIZED: 'Energized', LL: "Labor's Luck",
  ALTERATION: 'Alteration', GLINT_RECOVERY: 'Glint Recovery', FORTUNE_SEEKER: 'Fortune Seeker',
  STATIC_CHARGE: 'Static Charge',
};

// Decode one raw `land_abilities` table entry (e.g. `['IRON', 1]`
// `['BLOODLINE', 0.3, 'Undead']`, or a bare flag entry) into a human string
// e.g. "Bountiful Iron +100%" or "Toil and Kin (Undead) +30%". Rate/sub_type
// are read from the RAW tuple (row.workers[].land_abilities), not from the
// bare code lists in row.ability_holders[].codes/sole_holder_for -- those are
// stripped of rate/sub_type by ceres.metrics.ability_codes() on the backend
// so this decoder always works from the raw per-worker table.
// BLOODLINE entries carry a 4th element from the backend since board-polish
// batch 16 (2026-08-21) -- `{active, companions}`, computed against the
// REAL plot roster (ceres/metrics.py's `annotate_land_abilities()`) and
// validated against the API's own `card_bloodlines_boost` field (0
// mismatches across 79 live plots carrying a comparable value, 2026-08-21).
// the owner: "Toil & Kin needs a bloodline check, if one of the paired cards
// actually do have the required bloodline." A solo BLOODLINE holder with no
// OTHER worker sharing its sub_type contributes ZERO to the plot
// (library/land-worker-abilities.md §3) -- rendering it identically to an
// active one would silently overstate the plot's real boost, so the status
// is appended to the text here (works everywhere this decoder is used
// including the Land Card/VoV tooltip) and also drives the styled
// HTML variant below for the worker sub-table's Abilities cell.
// RULING (the owner, 2026-08-26, supersedes every earlier "deliberate state
// not a defect" ruling here): SOLO is a warning -- "is by default a warning
// badge to me... a clear underuse of potential" -- regardless of whether the
// holder is a VoV/Land card kept for other abilities (skin drops, glint
// bombs) or a plain farming placement. A card-level SOLO/PAIRED marking, a
// hover tooltip naming the missing Bloodline, and a warning when removing a
// worker would break an ACTIVE pair are all in-bounds; `bloodlineCardBadgeHtml()`
// and the server's own pair-break notices (staged after a removal) implement
// this.
export function bloodlineStatus(entry) {
  return Array.isArray(entry) && entry.length > 3 ? entry[3] : null;
}

export function workerSubType(worker) {
  if (worker?.sub_type) return String(worker.sub_type);
  const holder = (worker?.land_abilities || []).find((entry) => Array.isArray(entry) && entry[0] === 'BLOODLINE' && entry[2]);
  return holder ? String(holder[2]) : null;
}

// Every distinct BLOODLINE sub_type carried by a holder on this plot
// PAIRED holders included, not just SOLO ones. Feeds the "Bloodline pairs"
// picker filter, which is a FILTER on which cards are shown, never a
// scoring-priority term (the internal rules).
// RULING (maintainer, 2026-08-31, -- supersedes the earlier solo-only
// version of this function): "the filter does not work right now. Instead
// the filter must ALWAYS show the companion bloodline exclusively and filter
// for only those cards" -- including when the plot's bloodline is already
// PAIRED, because he wants to find a DIFFERENT or BETTER companion than the
// one currently staked, not just fill an empty pairing. The earlier version
// only collected SOLO sub_types, so toggling the filter on an already-paired
// plot returned an empty set and the picker refused with a needs-check
// ("No solo BLOODLINE holder... needs a companion") -- exactly the bug
// report. There is no active/inactive branch here on purpose: pairing state
// is irrelevant to which cards the filter should show, only to whether the
// SOLO warning badge renders (bloodlineStateChips/bloodlineCardBadgeHtml
// unchanged by this function).
export function bloodlinePlotSubTypes(row) {
  const workers = Array.isArray(row?.workers) ? row.workers : [];
  const wanted = new Set();
  workers.forEach((worker) => (worker.land_abilities || []).forEach((entry) => {
    if (!Array.isArray(entry) || entry[0] !== 'BLOODLINE' || !entry[2]) return;
    wanted.add(String(entry[2]));
  }));
  return [...wanted];
}

// BLOODLINE's display name, "Toil and Kin" -- reads the single source of truth
// (`ABILITY_NAME.BLOODLINE`) when it's in scope, falling back to the literal it
// always resolves to when a narrower call context doesn't carry that module-scope
// map (e.g. a test harness slicing just these functions out of app.js).
function bloodlineAbilityLabel() {
  return (typeof ABILITY_NAME !== 'undefined' && ABILITY_NAME.BLOODLINE) || 'Toil and Kin';
}

// Exact-bonus phrase for one BLOODLINE entry -- "+30% (12,345 PP)" when the
// server-computed `pp_impact` (ceres.metrics.bloodline_plot_pp_impact(), shipped as
// `status.pp_impact`) is available, else just the percentage. The percentage comes
// straight from the card's own static `land_abilities` rate (`entry[1]`) -- not PP
// math, safe to read client-side -- but the PP figure is never computed here: PP
// math is server-authoritative (the internal rules, ), so a null `pp_impact` (unmodeled
// plot) drops the PP part rather than guessing one.
function bloodlineImpactText(entry) {
  const status = bloodlineStatus(entry);
  // Toil and Kin does not stack -- only the largest active bonus applies.
  // An inactive entry's `gain_rate` is what a companion would ADD over the bonus already applied.
  const rawRate = status?.active === false && typeof status.gain_rate === 'number' ? status.gain_rate : entry[1];
  const rate = typeof rawRate === 'number' ? rawRate : null;
  if (rate === null) return null;
  const pct = `${rate >= 0 ? '+' : ''}${(rate * 100).toFixed(0)}%`;
  if (status?.active === true && status.applied === false) return `${pct} (not stacking)`;
  const ppImpact = status && typeof status.pp_impact === 'number' ? status.pp_impact : null;
  const text = ppImpact === null ? pct : `${pct} (${Math.round(ppImpact).toLocaleString()} PP)`;
  return status?.applied === true && status.overridden > 0 ? `${text} applied` : text;
}

// true when a candidate's Toil and Kin `entry` would add nothing on a plot whose
// `workers` already carry an active, equal-or-larger Toil and Kin of another bloodline.
export function bloodlineOutranked(entry, workers) {
  if (!Array.isArray(entry) || typeof entry[1] !== 'number') return false;
  return (workers || []).some((worker) => (worker.land_abilities || []).some((other) => Array.isArray(other)
    && other[0] === 'BLOODLINE' && other[2] !== entry[2] && other[3]?.active === true
    && typeof other[1] === 'number' && other[1] >= entry[1]));
}

// client-side twin of ceres.metrics.mark_bloodline_applied() for a
// staged roster re-annotated in the browser (annotateEditorBloodlines). Compares the
// cards' static rates only -- no PP math. Only the largest active bonus applies.
export function markBloodlineApplied(workers) {
  const entries = (workers || []).flatMap((worker) => worker.land_abilities || [])
    .filter((entry) => Array.isArray(entry) && entry[0] === 'BLOODLINE' && entry[3] && typeof entry[1] === 'number');
  const active = entries.filter((entry) => entry[3].active);
  if (!active.length) return;
  const best = active.reduce((a, b) => (b[1] > a[1] ? b : a));
  const overridden = active.filter((entry) => entry[2] !== best[2]).length;
  entries.forEach((entry) => {
    const status = entry[3];
    if (status.active) {
      status.applied = entry[2] === best[2];
      if (status.applied) status.overridden = overridden;
    } else {
      status.gain_rate = Math.max(0, entry[1] - best[1]);
      status.not_stacking = status.gain_rate <= 0;
      delete status.pp_impact;
    }
  });
}

function bloodlineStateChips(row) {
  const entries = (row?.workers || []).flatMap((worker) => worker.land_abilities || [])
    .concat((row?.workers?.length ? [] : row?.ability_holders || []).flatMap((holder) => holder.land_abilities || []));
  const paired = [];
  const solo = [];
  const seenPaired = new Set();
  const seenSolo = new Set();
  entries.forEach((entry) => {
    if (!Array.isArray(entry) || entry[0] !== 'BLOODLINE' || !entry[2]) return;
    const status = bloodlineStatus(entry);
    const subType = String(entry[2]);
    if (status?.active === true && !seenPaired.has(subType)) {
      seenPaired.add(subType);
      paired.push({ subType, impact: bloodlineImpactText(entry), companions: bloodlineStatus(entry)?.companions || [] });
    } else if (status?.active === false && !seenSolo.has(subType)) {
      seenSolo.add(subType);
      solo.push({ subType, impact: bloodlineImpactText(entry), notStacking: status.not_stacking === true });
    }
  });
  const chips = [];
  if (paired.length) {
    // Names the specific Bloodline the same way SOLO already does (the owner
    // 2026-08-26: PAIRED's tooltip should name the bloodline too), plus the
    // exact ability bonus this plot is currently earning from it.
    // Gate document #41: the ability, the pair and what it earns -- one icon, in the header.
    const items = paired.map((p) => ({ name: '', effect: `${p.subType} pair${p.companions.length ? ` with ${p.companions.join(', ')}` : ''} · ${p.impact || 'bonus active'} plot-wide` }));
    chips.push(`<span class="${badgeClasses('bloodline_paired', 'badge-property')}"${tipAttrs({ title: 'Toil and Kin active', icon: TOIL_AND_KIN_ICON, items, peer: 'bloodline_plot_badges' })}>PAIRED</span>`);
  }
  if (solo.length) {
    // SOLO is a warning badge, not the neutral property queue (the owner
    // 2026-08-26, supersedes the earlier deliberate-state ruling): "is by
    // default a warning badge to me... a clear underuse of potential." The
    // tooltip names the SPECIFIC missing Bloodline rather than just "solo"
    // `sub_type` is the internal field name and never appears in user-facing
    // copy, only the Bloodline name itself (e.g. "Undead") -- and states the
    // exact unrealised bonus this plot is missing out on.
    // Gate document #42 and his model tooltip (): lead with "adds nothing now".
    const items = solo.flatMap((s) => [
      { name: '', effect: `No ${s.subType} companion on this plot.` },
      ...(s.notStacking ? [{ name: '', effect: 'A companion would add nothing: a larger Toil and Kin already applies (not stacking).' }]
        : s.impact ? [{ name: '', effect: `A companion would add ${s.impact}.` }] : []),
    ]);
    chips.push(`<span class="${badgeClasses('bloodline_solo', 'badge-problem')}"${tipAttrs({ title: 'Toil and Kin inactive', icon: TOIL_AND_KIN_ICON, items, peer: 'bloodline_plot_badges' })}>SOLO</span>`);
  }
  return chips;
}

// CARD-level marking (2026-08-25, v0.5.20 follow-up -- the owner: "I see a new
// badge or bloodlines, solo and paired... But the cards are still not marked
// as bloodline pair or not"). bloodlineStateChips() above answers "does this
// PLOT have any BLOODLINE state" for the Badges column; this answers "is THIS
// specific card the one carrying it, and is it paired or solo" for its own
// slot tile in the editor -- rendered as a small solid-background pill (see
// `.slot-bloodline-badge` in ceres.css) rather than a bare-text colour, so it
// stays readable sitting on top of busy card art regardless of theme. Reuses
// bloodlineAbilityTip() for the hover text so the badge, the tile's own
// tooltip, and the tiny ability icon never drift apart. Returns '' for any
// occupant without a BLOODLINE entry -- most tiles render nothing extra.
export function bloodlineCardBadgeHtml(occupant, row) {
  const entry = (occupant?.land_abilities || []).find((e) => Array.isArray(e) && e[0] === 'BLOODLINE' && e[2]);
  if (!entry) return '';
  const status = bloodlineStatus(entry);
  const paired = status?.active === true;
  const label = paired ? 'PAIRED' : 'SOLO';
  const subType = String(entry[2]);
  const impact = bloodlineImpactText(entry);
  const body = paired
    ? (status.applied === false
      ? `This card's ${subType} companion is staked here, but a larger Toil and Kin already applies — ${impact}, adds nothing.`
      : `This card's ${subType} companion is staked here, so both cards produce more${impact ? ` — currently ${impact} to this plot` : ''}.`)
    : status?.not_stacking
      ? `No ${subType} companion is staked on this plot. Staking one would add nothing: a larger Toil and Kin already applies (not stacking).`
      : `No ${subType} companion is staked on this plot, so this card's Toil and Kin adds nothing. Staking one would add${impact ? ` ${impact}` : ''}.`;
  const tip = { title: `Bloodline: ${subType}`, body, peer: 'tile_chip_rail' };
  if (paired || !row) {
    return `<span class="slot-bloodline-badge ${paired ? 'is-paired' : 'is-solo'}"${tipAttrs(tip)}>${label}</span>`;
  }
  // SOLO is a warning, and the affordance to act on it must be OPT-IN and
  // RESPONSIVE -- offered from the badge itself, never triggered unsolicited on
  // an untouched plot (the owner, 2026-08-26: SOLO "is by default a warning
  // badge... merely the tooltip should be clear about the missing companion").
  // Reuses the exact same open-bloodline-pairs flow the pair-break removal
  // warning already offers (handleSlotEditorClick below; openBloodlinePairPicker)
  // rather than a second mechanism -- a click is the only way this ever fires.
  return `<button type="button" class="slot-bloodline-badge is-solo" data-notice-action="open-bloodline-pairs" data-sub-type="${esc(subType)}"${tipAttrs({ ...tip, body: `${body} Select to find a matching ${subType} Bloodline companion.` })}>${label}</button>`;
}

// COMPANION-side marking (2026-08-25, v0.5.21 follow-up -- the owner: "the
// paired card does not have that badge at all and shows nothing in its
// tooltip... They should BOTH have the badge, because the paired card is
// actually more relevant than the land card"). bloodlineCardBadgeHtml() above
// only fires for the card CARRYING a BLOODLINE entry (the Land/Verico
// holder); an ordinary monster that merely shares the holder's `sub_type` on
// this plot renders nothing there, which is the asymmetry the owner flagged.
// This answers "is THIS occupant satisfying some OTHER worker's active
// BLOODLINE on this plot" by scanning `row.workers` (raw board records, not
// the editorOccupant()-shaped `occupant` -- editorOccupant() clones a new
// object per call, so `occupant !== row.workers[i]`; match by asset_ref
// instead) for a holder whose BLOODLINE sub_type equals this occupant's own
// sub_type (workerSubType() -- the SAME field annotateEditorBloodlines()
// already uses to decide who counts as a companion, so this can't invent a
// pairing rule the rest of the file disagrees with) and whose status is
// already active. Handles a companion shared by MULTIPLE holders (rare but
// real -- e.g. two Land cards both requiring "Elf") by returning one
// relation per holder; the caller joins them into one tooltip. A card that is
// BOTH a holder (of one bloodline) and a companion (for a different one) is
// also real -- Toil and Kin never stacks, only the largest active bonus applies
// (, ; markBloodlineApplied) -- and is handled by simply
// rendering both this badge AND bloodlineCardBadgeHtml()'s holder badge on
// the same tile; the caption already stacks multiple rows (badge/name/meta)
// so a second, rare badge row is a known, accepted trade-off rather than an
// oversight.
export function bloodlineCompanionRelations(row, occupant) {
  const subType = workerSubType(occupant);
  if (!subType) return [];
  const workers = Array.isArray(row?.workers) ? row.workers : [];
  const seen = new Set();
  const relations = [];
  workers.forEach((holder) => {
    if (String(holder.asset_ref) === String(occupant.asset_ref)) return;
    (holder.land_abilities || []).forEach((entry) => {
      if (!Array.isArray(entry) || entry[0] !== 'BLOODLINE' || entry[2] !== subType) return;
      if (bloodlineStatus(entry)?.active !== true) return;
      const key = `${holder.name}|${entry[2]}`;
      if (seen.has(key)) return;
      seen.add(key);
      relations.push({ holderName: holder.name, entry });
    });
  });
  return relations;
}

export function bloodlineCompanionBadgeHtml(row, occupant) {
  const relations = bloodlineCompanionRelations(row, occupant);
  if (!relations.length) return '';
  const body = relations
    .map((rel) => `This card completes ${rel.holderName}'s Bloodline pairing. Both cards produce more while it stays here.`)
    .join(' ');
  return `<span class="slot-bloodline-badge is-companion"${tipAttrs({ title: 'Bloodline: Companion', body, peer: 'tile_chip_rail' })}>COMPANION</span>`;
}

// "use the same style echo does it ... on the bottom of the tile"): the labelled
// Toil & Kin / Companion pills the tile redesign (v0.9.47) dropped from the
// caption, back on the tile as Echo Reapers draws them (worksite-tiles.js
// bloodlineTileBadges, /): ONE pill per card -- the holder reads
// "Toil & Kin +30%" (paired) or "Toil & Kin solo", a companion reads
// "Companion · <bloodline>"; a card that holds a Toil & Kin of its own keeps the
// holder pill only. Rendered inside the tile's bottom `.slot-foot`, above the name.
export function bloodlineTileBadgesHtml(row, occupant) {
  const pills = [];
  (occupant?.land_abilities || []).forEach((entry) => {
    if (!Array.isArray(entry) || entry[0] !== 'BLOODLINE' || !entry[2]) return;
    const status = bloodlineStatus(entry);
    const paired = status?.active === true;
    const subType = String(entry[2]);
    const rate = typeof entry[1] === 'number' ? `${entry[1] >= 0 ? '+' : ''}${(entry[1] * 100).toFixed(0)}%` : '';
    const impact = bloodlineImpactText(entry);
    const body = paired
      ? (status.applied === false
        ? `This card's ${subType} companion is staked here, but a larger Toil and Kin already applies — ${impact}, adds nothing.`
        : `This card's ${subType} companion is staked here, so both cards produce more${impact ? ` — currently ${impact} to this plot` : ''}.`)
      : status?.not_stacking
        ? `No ${subType} companion is staked on this plot. Staking one would add nothing: a larger Toil and Kin already applies (not stacking).`
        : `No ${subType} companion is staked on this plot, so this card's Toil and Kin adds nothing. Staking one would add${impact ? ` ${impact}` : ''}.`;
    // on the tile the Toil & Kin icon stands for the words ("+25%" / "Solo") so the tag fits the top-left row
    // beside the remove X; the full wording stays the tag's accessible name and the tooltip title.
    pills.push({ cls: paired ? 'is-paired' : 'is-solo', text: paired ? `Toil & Kin ${rate}`.trim() : 'Toil & Kin: Solo'  /* maintainer wording */, short: paired ? (rate || 'Paired') : 'Solo', tip: { title: `Toil and Kin · ${subType}`, body, peer: 'tile_chip_rail' } });
  });
  if (!pills.length) {
    // One pill even when two holders share this companion; the tooltip names each pair. The bloodline's
    // name stays in the tooltip: a 107px tile has no room for "Companion · Divine Construct".
    const relations = bloodlineCompanionRelations(row, occupant);
    if (relations.length) {
      const body = relations.map((rel) => { const impact = bloodlineImpactText(rel.entry); return `This card completes ${rel.holderName}'s ${rel.entry[2]} Toil and Kin${impact ? ` (${impact})` : ''}. Both cards produce more while it stays here.`; }).join(' ');
      pills.push({ cls: 'is-companion', text: 'Companion', tip: { title: `Companion · ${relations.map((rel) => rel.entry[2]).filter((v, i, all) => all.indexOf(v) === i).join(', ')}`, body, peer: 'tile_chip_rail' } });
    }
  }
  if (!pills.length) return '';
  const icon = `<img class="slot-kin-icon" src="${ASSET_BASE}/${TOIL_AND_KIN_ICON}" alt="" aria-hidden="true">`;
  return `<span class="slot-kin-badges">${pills.map((pill) => `<span class="slot-kin-badge ${pill.cls}" role="img" aria-label="${esc(pill.text)}"${tipAttrs(pill.tip)}>${icon}<span class="slot-kin-text" aria-hidden="true">${esc(pill.short || pill.text)}</span></span>`).join('')}</span>`;
}

function decodeAbilityParts(entry) {
  const code = Array.isArray(entry) ? entry[0] : entry;
  const rate = Array.isArray(entry) && typeof entry[1] === 'number' ? entry[1] : null;
  const subType = Array.isArray(entry) && entry.length > 2 ? entry[2] : null;
  const known = Object.prototype.hasOwnProperty.call(ABILITY_NAME, code);
  const title = ABILITY_NAME[code] || code;
  const effects = [];
  if (subType) effects.push(String(subType));
  if (rate !== null) effects.push(`${rate >= 0 ? '+' : ''}${(rate * 100).toFixed(0)}%`);
  const status = bloodlineStatus(entry);
  if (status) effects.push(status.active ? (status.applied === false ? 'Active (not stacking)' : 'Active') : 'Inactive');
  return {
    title,
    body: known ? effects.join(' ') || 'No additional effect described.' : 'The effect of this ability is not described in the available details.',
  };
}

export function decodeAbilityEntry(entry) {
  const { title, body } = decodeAbilityParts(entry);
  return `${title}${body === 'No additional effect described.' ? '' : ` ${body}`}`;
}

// Gate document #81-#98 (approved ) and /: every ability says what it
// does and when, in the fewest words, with the icon of every resource it names ().
// abilityTipModel() is the ability icon's own tooltip; abilityRowText() is the compact
// line used inside another tooltip (worker card, "+N more", sole holder) -- the ability's
// own icon and name sit in that row's label, so the line never repeats them.
const RESOURCE_ABILITY = { GRAIN: 'Grain', WOOD: 'Wood', STONE: 'Stone', IRON: 'Iron', AURA: 'Aura', RESEARCH: 'Research', SPS: 'SPS' };

function abilityRate(entry) {
  return Array.isArray(entry) && typeof entry[1] === 'number' ? entry[1] : null;
}

function signedRate(rate) {
  return rate === null ? '' : `${rate >= 0 ? '+' : '−'}${Math.abs(rate * 100).toFixed(0)}%`;
}

function reductionRate(rate) {
  return rate === null ? '' : `−${Math.abs(rate * 100).toFixed(0)}%`;
}

function resourceWorksite(resource, iconAfter = false) {
  const icon = tipIcon(tooltipIconPath('resource', resource.toLowerCase()));
  const place = resource === 'Aura' ? 'Aura Lab' : `${resource} worksite`;
  const article = /^[AEIOS]/.test(resource) ? 'an' : 'a';
  return iconAfter ? `${article} ${resource} ${icon}${resource === 'Aura' ? 'Lab' : 'worksite'}` : `${article} ${icon}${place}`;
}

export function abilityRowText(entry, { resourceIconAfter = false } = {}) {
  const code = Array.isArray(entry) ? entry[0] : entry;
  const rate = abilityRate(entry);
  if (RESOURCE_ABILITY[code]) return `${signedRate(rate)} on ${resourceWorksite(RESOURCE_ABILITY[code], resourceIconAfter)}`.trim();
  if (code === 'DD') return `${reductionRate(rate)} ${resourceIconAfter ? `DEC ${tipIcon(DEC_ICON)}` : `${tipIcon(DEC_ICON)}DEC`} stake needed`.trim();
  // his words: "It lowers the food cost for the workers, not the harvest".
  const lessGrain = `Workers eat ${rate === null ? '' : `${Math.abs(rate * 100).toFixed(0)}% `}less ${resourceIconAfter ? `Grain ${tipIcon(tooltipIconPath('resource', 'grain'))}` : `${tipIcon(tooltipIconPath('resource', 'grain'))}Grain`}`;
  if (code === 'RATIONING') return lessGrain;
  if (code === 'RATIONING_LITE') return `${lessGrain}\nup to 20,000 Base PP`; // second line
  if (code === 'ENERGIZED') return "Powers this plot's worker slots";
  if (code === 'LL') return 'Foil Land Card chance per harvest';
  if (code === 'ALTERATION') return 'Monthly skin airdrop';
  if (code === 'GLINT_RECOVERY') return 'Daily Glint airdrop chance';
  if (code === 'FORTUNE_SEEKER') return 'Weekly foil Verico card chance';
  if (code === 'STATIC_CHARGE') return `${signedRate(rate)} · effect not documented yet`.replace(/^ · /, '');
  if (code === 'BLOODLINE') {
    const status = bloodlineStatus(entry);
    const subType = Array.isArray(entry) ? entry[2] : '';
    const impact = bloodlineImpactText(entry);
    if (status?.active === false) return `Inactive — no ${subType} companion`;
    return `${impact || signedRate(rate)} · ${subType} pair`;
  }
  return decodeAbilityParts(entry).body;
}

export function abilityTipModel(entry) {
  const code = Array.isArray(entry) ? entry[0] : entry;
  const rate = abilityRate(entry);
  const icon = tooltipIconPath('ability', code);
  // Toil and Kin keeps its live text -- the tooltip he quoted as the model ().
  if (code === 'BLOODLINE') return { ...bloodlineAbilityTip(entry), icon };
  const title = ABILITY_NAME[code] || code;
  if (RESOURCE_ABILITY[code]) return { title, icon, body: `${signedRate(rate)} production on ${resourceWorksite(RESOURCE_ABILITY[code])} · plot-wide, best holder counts`.trim() };
  if (code === 'DD') return { title, icon, body: `${abilityRowText(entry)} · no effect with a Runi` };
  if (code === 'STATIC_CHARGE') return { title: `${title}${rate === null ? '' : ` ${signedRate(rate)}`}`, icon, body: 'Effect not documented yet.' };
  if (['ALTERATION', 'GLINT_RECOVERY', 'FORTUNE_SEEKER'].includes(code)) return { title, icon, body: `${abilityRowText(entry)} · works as soon as the card is staked` };
  return { title, icon, body: abilityRowText(entry) };
}

// Icon form for the worker sub-table's Abilities column (replaces the old
// inline-text rendering). The full text (name, rate, sub_type, and -- for
// BLOODLINE -- the active/inactive companion detail) moves entirely into
// the tooltip via
// tipAttrs(), same mechanism as every other icon on this board
// (totemIconHtml/titleIconHtml/etc.) -- no second tooltip mechanism, no bare
// `title=`. An unmapped code (RESEARCH/SPS, or any future addition) degrades
// to a styled <span> carrying the same text that used to render inline
// never a broken <img> (same defensive contract as totemIconHtml's
// TOTEM_ICON_FILE guard). No CSS filter/drop-shadow/box-shadow is applied to
// these icons () -- active/inactive BLOODLINE status is instead
// signalled with a plain opacity class (`.ability-icon.ability-bloodline-inactive`
// in ceres.css), which is not a per-pixel raster filter.
// Shared by abilityIconHtml() below AND editorWorkerTip()/bloodlineCardBadgeHtml()
// further down this file -- ONE builder for "what does this BLOODLINE entry
// mean right now," so the tiny inline ability icon, the card-level PAIRED/SOLO
// badge, and the tile's own hover tooltip can never say three different things
// about the same entry (card-marking task, 2026-08-25).
// `role`/`holderName` (2026-08-25, companion-badge follow-up): the owner's report
// on v0.5.21 was that the PAIRED badge/tooltip is useful and correct on the
// Land/Verico HOLDER's own tile, but the companion -- "actually more relevant
// than the land card. Land cards are easy to identify by default, paired
// buddies are not" -- got nothing at all, on either its badge or its tooltip.
// Rather than write a second decoder for the companion's side (which could
// drift from this one), the SAME entry object and the SAME status the holder
// reads is passed through here with `role: 'companion'` -- one word of the
// entry's meaning, told from the other card's point of view. The holder
// CARRIES the ability; the companion only ever SATISFIES it -- keep that verb
// distinction so neither tile can be misread as claiming the other's role.
export function bloodlineAbilityTip(entry, opts = {}) {
  const { role = 'holder', holderName = null } = opts;
  const parts = decodeAbilityParts(entry);
  const status = bloodlineStatus(entry);
  const plotWide = 'Affects every worker on this plot.';
  if (!status) return { title: parts.title, body: parts.body };
  if (role === 'companion') {
    // bloodlineCompanionBadgeHtml() only ever calls this for an ACTIVE match
    // (an inactive/solo holder has no companion by definition), so there is
    // no "inactive" phrasing to mirror here.
    return { title: parts.title, body: `${parts.body}\nThis card satisfies "${entry[2]}" for ${holderName || 'another card on this plot'}, which carries ${ABILITY_NAME.BLOODLINE}.\n${plotWide}` };
  }
  const detail = status.active
    ? `Shares "${entry[2]}" with: ${status.companions.join(', ')}.`
    : `Missing a companion for the ${entry[2]} Bloodline — no other worker on this plot shares it, so this contributes nothing to the plot's harvest right now.`;
  return { title: parts.title, body: `${parts.body}\n${detail}\n${plotWide}` };
}

function abilitySetLabel(cardDetailId) {
  if (LAND_CARD_IDS.includes(Number(cardDetailId))) return 'Land Card';
  if (cardDetailId >= VOV_ID_RANGE[0] && cardDetailId <= VOV_ID_RANGE[1]) return 'VoV';
  return 'Ability';
}

// Card identity does not depend on whether another worker shares its abilities.
function landAbilityCardChips(row) {
  return (row.ability_holders || []).map((holder) => {
    const worker = (row.workers || []).find((entry) => entry.asset_ref === holder.asset_ref);
    const abilities = worker?.land_abilities || (holder.codes || []).map((code) => [code]);
    const items = abilities.map((entry) => {
      const code = Array.isArray(entry) ? entry[0] : entry;
      return { icon: tooltipIconPath('ability', code), name: ABILITY_NAME[code] || code, effect: abilityRowText(entry) };
    });
    const title = `${holder.name || worker?.card_name || 'Land ability card'}${holder.level ? ` · Level ${holder.level}` : ''}`;
    return `<span class="${badgeClasses('land_ability_card', 'badge-property')}"${tipAttrs({ title, items, peer: 'plot_warnings' })}>${esc(abilitySetLabel(holder.card_detail_id))}</span>`;
  });
}

// UNDERFILLED badge -- renamed from the fixed "FUL" abbreviation, which read
// as the opposite of what it means, to a live open-slot COUNT (the owner
// board-polish batch 9 continued, item 10: *"FUL uses the term occupied
// which is confusing as that is a land type. And the name FUL is
// counter-intuitive as it is all but full."*). All 54 live underfilled plots
// happen to show exactly 4 open slots (one lone Verico worker at 1/5), so a
// count is the real signal, not filler. "Occupied" is purged from this
// badge's text entirely -- that word is reserved for the plot TYPE
// everywhere else on this board (Type column, filter, typeMarkHtml).
function relevantBadgeStages(row) {
  return CERES_STAGE.filter((entry) => entry?.deed_uid === row?.deed_uid || entry?._source_deed_uid === row?.deed_uid);
}

function underfilledChip(row) {
  const staged = relevantBadgeStages(row);
  const { active, openSlots } = effectiveUnderfilledState(row, staged);
  if (!active) return null;
  const text = openSlots ? `${openSlots} open` : 'Open';
  // Gate document #58: this badge had no tooltip at all.
  const capacity = Number(row.capacity && row.capacity.card_capacity);
  const title = openSlots && Number.isFinite(capacity) && capacity > 0
    ? `${openSlots} of ${capacity} worker slots empty`
    : openSlots ? `${openSlots} worker slot${openSlots === 1 ? '' : 's'} empty` : 'Worker slots empty';
  return `<span class="${badgeClasses('underfilled', 'badge-problem')}"${tipAttrs({ title, body: 'Empty slots add no Base PP.', peer: 'plot_warnings' })}>${esc(text)}</span>`;
}

// Over-cap indicator, relocated into the Badges column (the owner, 2026-08-21)
// previously rendered inline in the Base PP cell by fmtBasePp() (board-polish batch
// 9, item 4). Measured over-cap behavior: plot 41-9-56 clips slot 4 from 7,500 to
// 2,058.824 PP and slot 5 from 2,200 to 0, so this is a problem state. Same per-row
// chip pattern as underfilledChip()
// landAbilityCardChips() above -- board.badge_meta carries only a static fallback
// tooltip for "over_cap", so this renders its own with the actual after-cap/cap
// values, same text the old inline badge carried.
function overCapChip(row) {
  const staged = relevantBadgeStages(row);
  const state = effectiveOverCapState(row, staged);
  if (!state.active) return null;
  // Gate document #44: the plot's own numbers, never a hard-coded 100,000.
  const before = Number(state.before);
  const cap = Number(state.cap);
  const after = Number(state.after);
  const lost = Number.isFinite(before) && Number.isFinite(after) ? before - after : null;
  const figures = Number.isFinite(before) && Number.isFinite(cap)
    ? `Base PP ${num(before)} · cap ${num(cap)}${lost > 0 ? ` · ${num(lost)} PP lost` : ''}`
    : null;
  return `<span class="${badgeClasses('over_cap', 'badge-problem')}"${tipAttrs({
    title: 'Over the plot cap',
    items: [
      ...(figures ? [{ name: '', effect: figures }] : []),
      { name: '', effect: 'Cards marked red below produce less.' },
    ],
    peer: 'plot_metrics',
  })}>Over Cap</span>`;
}

// Badges mix two different kinds of thing (the owner, 2026-08-20 review): PROBLEMs
// (unpowered/incomplete/underfilled/idle -- something to fix) and PROPERTIES
// (land_ability_card -- a fact to know before touching the plot, not a fault).
// board.badge_meta (ceres/board.py:BADGE_META) is the one source of truth for the
// label/tooltip/kind of every code EXCEPT underfilled/land_ability_card, which
// now render their own dynamic, per-plot text and tooltip above (item 9/10)
// board.badge_meta still carries a generic fallback for those two codes for any
// other consumer of the payload, but the frontend no longer reads it for them.
// One line, fixed row height (the owner, 2026-08-21 polish pass: "make sure the badges
// are not stacked and forcing the table row to be double-sized"). The previous
// problem-queue/property-queue split rendered as two stacked `display:block` rows
// doubling row height on any plot carrying both kinds (live account: up to 5 badges
// on one plot). Fixed by condensing every badge to a short fixed-width abbreviation
// chip -- only BiB and UT use dashed borders; all other badges are solid. Chips sit
// in one `inline-flex` row (`.badge-row`, `flex-wrap: nowrap`) instead of two lanes.
// The full label and tooltip text are never lost -- both now render through the
// custom tooltip (item 11) instead of a native `title`.
const BADGE_ABBR = {
  idle: 'Idle',
  unpowered: 'No power',
  unpowered_worker: 'Unpowered',
  redundant_power_core: 'Spare core',
  dark_discount_wasted: 'DD wasted',
  // Split from the single "INC" badge (the owner's worksite ruling, 2026-08-25): a
  // worksite cannot be removed once built, so its total absence means clearing has
  // never finished (open-ended, depends on rarity/PP) -- distinct from an existing
  // worksite mid-build/mid-change, which always takes exactly 24 hours. A user
  // could not tell those two waits apart under one "INC" chip.
  no_worksite: 'Not cleared',
  worksite_incomplete: 'Worksite change',
  ll: 'LL',
  // Moved out of the worker sub-table's plain-text deed_stats note (the owner
  // 2026-08-21, board-polish batch 15) -- see badgeHtml()/ceres.css's
  // .badge-building_in_box for why this one is styled quiet rather than loud.
  unstable_totem: 'UT',
  building_in_box: 'BiB',
  // Bulk-first redesign (2026-08-21): the default board load fetches no per-deed
  // worker/ability data at all -- this is the "we genuinely don't know yet" signal
  // not a problem with the plot. Click the row to expand and load it.
};
// Runi badge (board-polish batch 19, item 4) -- `row.capacity.runi_staked` is
// already computed server-side (ceres/board.py's capacity block, the same
// field the "5/5+R" capacity display reads) so no new payload field is
// needed. PROPERTY queue, not a problem: a staked Runi zeroes this plot's DEC
// staking requirement entirely (the internal rules durable fact, regardless of Dark
// Discount) and shares the Power slot without consuming any of the 5 regular
// worker slots -- a fact worth knowing at a glance, not a fault to fix.
// 100% boost added to this tooltip (the owner, 2026-08-21: "add 100% boost to
// runi tooltip"). Confirmed against live data, not just stated: every one of
// player0005's 3 Runi-staked plots reports `multiplier_parts.runi_boost: 1.0`
// (queried live via GET /api/board, 2026-08-21) -- the same figure the
// worker-subtable's own multiplier breakdown already shows as "Runi +100%"
// (multiplierPartsHtml() above), so this tooltip states a fact the board
// elsewhere already displays, not a new or independent claim.
function hasRuniBadge(row) {
  return row?.capacity?.runi_staked === true;
}
function runiBadgeChip() {
  // Gate document #45: the boost in the title, one short line per fact.
  return `<span class="${badgeClasses('runi', 'badge-property')}"${tipAttrs({
    title: 'Runi staked — +100% production, stacks with Totem and Title',
    items: [
      { name: '', effect: 'Own Base PP, not limited by the plot cap' },
      { name: '', effect: `${tipIcon(DEC_ICON)}DEC stake 0 on this plot` },
      { name: '', effect: 'Power slot · powers four worker slots' },
      { name: '', effect: 'Power Core or other Power Source + Runi · opens a sixth worker slot (bare Runi caps at five)' },
      { name: '', effect: 'Takes one of the plot\'s worker slots itself' },
    ],
    peer: 'plot_properties',
  })}>RUNI</span>`;
}
const BADGE_GROUP_RANK = Object.freeze({ critical: 0, special: 1, warning: 2, positive: 3, info: 4, neutral: 5 });

// One runtime table owns every chip/filter label, colour group, and within-group order.
// Backend badges are merged from BOARD.badge_meta; synthesized badges live here because
// the server never emits them as badge catalogue entries.
const BADGE_TABLE = new Map([
  ['pending_change_stalled', { kind: 'problem', label: 'Stalled', group: 'critical', groupOrder: 4 }],
  ['staged_changes', { kind: 'property', label: 'X Changes', group: 'special', groupOrder: 0 }],
  ['staged_cooldown', { kind: 'property', label: 'Waiting', group: 'special', groupOrder: 1 }],
  // (maintainer): "The 'building' badge must use the golden category" -- a build in progress (or ready to feed) sits in the gold group after his own unsigned changes.
  ['pending_change', { kind: 'property', label: 'Building', group: 'special', groupOrder: 2 }],
  ['over_cap', { kind: 'problem', label: 'Over Cap', group: 'critical', groupOrder: 2 }],
  ['region_dec', { kind: 'problem', label: 'Region DEC', group: 'critical', groupOrder: 3 }],
  ['underfilled', { kind: 'problem', label: 'Open slots', group: 'warning', groupOrder: 3 }],
  ['bloodline_solo', { kind: 'problem', label: 'SOLO', group: 'warning', groupOrder: 4 }],
  ['bloodline_paired', { kind: 'property', label: 'PAIRED', group: 'positive', groupOrder: 0 }],
  ['runi', { kind: 'property', label: 'RUNI', group: 'info', groupOrder: 0 }],
  ['land_ability_card', { kind: 'property', label: 'Land Card / VoV', group: 'positive', groupOrder: 1 }],
]);
let BADGE_META_SOURCE = null;

function syncBackendBadges() {
  const meta = (BOARD && BOARD.badge_meta) || {};
  if (meta === BADGE_META_SOURCE) return;
  for (const [key, definition] of BADGE_TABLE) {
    if (definition.backend) BADGE_TABLE.delete(key);
  }
  Object.entries(meta).forEach(([key, info]) => {
    if (key === 'assets_not_loaded') return;
    BADGE_TABLE.set(key, {
      label: key === 'pending_change_stalled' ? 'Stalled' : (info.label || key),
      kind: key === 'land_ability_card' || key === 'sole_ability_holder' ? 'property' : (info.kind || 'problem'),
      group: info.state_group || 'neutral',
      groupOrder: Number.isFinite(Number(info.group_order)) ? Number(info.group_order) : Number.MAX_SAFE_INTEGER,
      backend: true,
    });
  });
  BADGE_META_SOURCE = meta;
}

export function badgeDefinitions() {
  syncBackendBadges();
  return [...BADGE_TABLE.entries()].map(([key, definition]) => ({ key, ...definition }));
}

function badgeDefinition(code) {
  syncBackendBadges();
  return BADGE_TABLE.get(code) || { label: code, kind: 'problem', group: 'neutral', groupOrder: Number.MAX_SAFE_INTEGER };
}

export function badgeGroup(code) {
  return badgeDefinition(code).group;
}

function badgeRank(code) {
  const definition = badgeDefinition(code);
  const groupRank = BADGE_GROUP_RANK[definition.group] ?? BADGE_GROUP_RANK.neutral;
  return (groupRank * 100) + definition.groupOrder;
}

function badgeClasses(code, kind = '', cssCode = code) {
  return ['badge', `badge-${cssCode}`, kind, `badge-group-${badgeGroup(code)}`].filter(Boolean).join(' ');
}

export function badgeFilterOptions() {
  return badgeDefinitions()
    .filter((definition) => definition.filter !== false)
    .sort((a, b) => badgeRank(a.key) - badgeRank(b.key) || a.label.localeCompare(b.label))
    .map(({ key, label, group }) => ({ key, label, group }));
}
// Phase 0 -- the only frontend consumer of `pending_change` anywhere in
// this file. Until this landed, `grep -n "pending_change\|pendingChange"
// ceres/static/app.js` returned exactly one hit and it was a comment
// `ceres/worksite_swap.py::build_pending_change` has its own passing unit tests
// and shipped with zero renderers. Reads `progress_pct`/`ready_at`/`stalled`
// exactly as emitted -- this never recomputes any of them, only formats
// `ready_at` for display via the existing `humanizeInstant()` helper (the same
// one candidate cooldown tags already use).
function pendingChangeChip(row) {
  const change = row?.pending_change;
  if (!change) return null;
  const toLabel = change?.to_label || change?.to || 'a new worksite';
  if (change?.stalled) {
    // "stalled", never a countdown to the year-116104 overflow date
    // build_pending_change already guards against (the internal rules/plan §4.3).
    const tip = { title: 'Worksite change stalled', body: `This plot produces no PP, so the build toward ${toLabel} is not progressing.`, peer: 'plot_pending' };
    return `<span class="${badgeClasses('pending_change_stalled', 'badge-problem', 'pending_change')}"${tipAttrs(tip)}>Stalled</span>`;
  }
  const when = change?.ready_at ? humanizeInstant(change.ready_at) : null;
  const pct = change?.progress_pct !== null && change?.progress_pct !== undefined ? `${Math.round(change.progress_pct * 100)}%` : null;
  const hasReadyTime = Boolean(when?.absolute && when?.relative);
  const tip = {
    title: 'Worksite change under way',
    ...(pct ? { key: `${pct} built` } : {}),
    body: hasReadyTime ? `Building ${toLabel}. Ready ${when.absolute} (${when.relative}).` : `Building ${toLabel}. Ready time unknown.`,
    peer: 'plot_pending',
  };
  return `<span class="${badgeClasses('pending_change', 'badge-property')}"${tipAttrs(tip)}>Building</span>`;
}

// (maintainer: "Each change on a plot that is not yet signed adds a plot badge to the first position in badges.
// This badge must be animated to draw attention. When a change is undone or signed, the badge disappears from the
// plot. It should say "X Changes""): every staged move on this plot -- incoming, or a card leaving it for another
// plot -- that has no transaction yet. A signed move carries its txId until the game confirms it; it no longer counts.
// phase 3 (K2/K3): the two badges are disjoint. "X Changes" counts what can be signed now (green, and orange on
// both plots); "⏳ N in cooldown" counts the red ones on their target plot (land cooldown or Survival).
function stagedBlocked(entry) {
  return entry.action !== 'unstake' && entry.cooldown && entry.cooldown.ready === false
    && !(entry.cooldown.state === 'staked' && entry._source_deed_uid);
}
export function unsignedChangeCount(deedUid) {
  return CERES_STAGE.filter((entry) => !entry?.txId && !entry._synthetic && (entry.deed_uid === deedUid || entry._source_deed_uid === deedUid)
    && !(entry.deed_uid === deedUid && stagedBlocked(entry))).length;
}
export function cooldownChangeEntries(deedUid) {
  return CERES_STAGE.filter((entry) => !entry?.txId && entry.deed_uid === deedUid && stagedBlocked(entry));
}
export function stageBadgeKey(deedUid) {
  return `${unsignedChangeCount(deedUid)}|${cooldownChangeEntries(deedUid).length}`;
}
// every plot's badge key from ONE pass over the stage (same predicates as the two counts above), so a
// refresh costs O(changes) instead of O(plots x changes). Only deeds with a change are in the map ('0|0' otherwise).
export function stageBadgeKeysByDeed() {
  const unsigned = new Map();
  const cooling = new Map();
  const bump = (map, deed) => map.set(deed, (map.get(deed) || 0) + 1);
  CERES_STAGE.forEach((entry) => {
    if (entry?.txId) return;
    const blocked = stagedBlocked(entry);
    if (blocked) bump(cooling, entry.deed_uid);
    if (entry._synthetic) return;
    if (!blocked) bump(unsigned, entry.deed_uid);
    if (entry._source_deed_uid != null && entry._source_deed_uid !== entry.deed_uid) bump(unsigned, entry._source_deed_uid);
  });
  const keys = new Map();
  new Set([...unsigned.keys(), ...cooling.keys()]).forEach((deed) => keys.set(deed, `${unsigned.get(deed) || 0}|${cooling.get(deed) || 0}`));
  return keys;
}

function cooldownChangesChip(row) {
  const entries = cooldownChangeEntries(row.deed_uid);
  if (!entries.length) return null;
  const tip = {
    title: `${entries.length} planned change${entries.length === 1 ? '' : 's'} waiting`,
    items: entries.map((entry) => ({ name: entry._asset_name || 'A card', effect: cooldownReasonFragment(entry.cooldown) })),
    peer: 'plot_pending',
  };
  return `<span class="${badgeClasses('staged_cooldown', '', 'staged-cooldown')}"${tipAttrs(tip)}>Waiting</span>`;
}

function stagedChangesChip(row) {
  const count = unsignedChangeCount(row.deed_uid);
  if (!count) return null;
  const label = `${count} Change${count === 1 ? '' : 's'}`;
  const tip = { title: `${label} staged`, body: 'Not signed yet. Open Changes to review and sign, or undo the change on the plot.', peer: 'plot_pending' };
  return `<span class="${badgeClasses('staged_changes', '', 'staged-changes')}"${tipAttrs(tip)}>${label}</span>`;
}

// a plot in a region short of DEC -- the game scales every non-Runi plot's production by the region's
// efficiency (041-01-067: 98.4 -> 64.2 Aura/hr at 65.3%). Synthesised client-side from row.region_efficiency.
function regionDecChip(row) {
  const raw = row.region_efficiency;
  if (raw === null || raw === undefined || (typeof raw === 'string' && !raw.trim())) return '';
  const eff = Number(raw);
  if (!Number.isFinite(eff) || eff < 0 || eff >= 1 || row.bonus?.runi) return '';
  const pct = `${(eff * 100).toLocaleString('en-US', { maximumFractionDigits: 0 })}%`;
  return `<span class="${badgeClasses('region_dec', 'badge-problem')}"${tipAttrs({ title: `Region DEC ${pct}`, body: `${regionLabel(row.region)} has too little DEC staked: every plot in it (except Runi plots) produces at ${pct}. Stake DEC on the region in the game to bring it to 100%.`, peer: 'badge_region_dec' })}>DEC ${pct}</span>`;
}

export function rowHasBadge(row, key) {
  if (!key) return true;
  if (key === 'sole_ability_holder') key = 'land_ability_card';
  return badgeChips(row).some((chip) => chip.key === key);
}

function badgeChips(row) {
  const badges = (row.badges || []).filter((code) => code !== 'assets_not_loaded');
  const meta = (BOARD && BOARD.badge_meta) || {};
  const chips = [];
  const staged = stagedChangesChip(row);
  if (staged) chips.push({ key: 'staged_changes', rank: badgeRank('staged_changes'), html: staged });
  const waiting = cooldownChangesChip(row);
  if (waiting) chips.push({ key: 'staged_cooldown', rank: badgeRank('staged_cooldown'), html: waiting });
  const pendingChange = pendingChangeChip(row);
  if (pendingChange) chips.push({ key: row?.pending_change?.stalled ? 'pending_change_stalled' : 'pending_change', rank: badgeRank(row?.pending_change?.stalled ? 'pending_change_stalled' : 'pending_change'), html: pendingChange });
  const regionDec = regionDecChip(row);
  if (regionDec) chips.push({ key: 'region_dec', rank: badgeRank('region_dec'), html: regionDec });
  const overCap = overCapChip(row);
  if (overCap) chips.push({ key: 'over_cap', rank: badgeRank('over_cap'), html: overCap });
  const underfilled = underfilledChip(row);
  if (underfilled) chips.push({ key: 'underfilled', rank: badgeRank('underfilled'), html: underfilled });
  // PAIRED (badge-property) and SOLO (badge-problem) rank independently -- one
  // "bloodline_state" position could not express that PAIRED belongs in the
  // property group while SOLO belongs in the attention group (BADGE_ORDER split
  for (const html of bloodlineStateChips(row)) {
    const isPaired = html.includes('badge-bloodline_paired');
    chips.push({ key: isPaired ? 'bloodline_paired' : 'bloodline_solo', rank: badgeRank(isPaired ? 'bloodline_paired' : 'bloodline_solo'), html });
  }
  for (const b of badges) {
    if (b === 'land_ability_card' || b === 'sole_ability_holder') continue;
    if (b === 'underfilled' || b === 'over_cap') continue;
    const info = meta[b] || { label: b, tooltip: b, kind: 'problem' };
    const abbr = BADGE_ABBR[b] || info.label.replace(/[^A-Za-z]/g, '').slice(0, 4).toUpperCase();
    const kindCls = info.kind === 'property' ? 'badge-property' : 'badge-problem';
    // (v0.9.32): a lean list row carries `{code, context}` per notice and the page
    // hydrates the rest from the catalogue it already holds; a full row (or a row notice
    // with its own message) is used as it comes.
    const rawNotice = (row.notices || []).find((notice) => notice.code === b);
    const rowNotice = rawNotice
      ? { ...catalogNotice(rawNotice.code, rawNotice.context || {}), ...rawNotice, message: rawNotice.message || noticeMessage(catalogNotice(rawNotice.code, rawNotice.context || {})) }
      : undefined;
    const tooltip = rowNotice?.message || info.tooltip;
    // The backend assigns every badge a severity and the row notice carries one too
    // prefer the row's, since it reflects this plot's actual state. `neutral` is the
    // absence of a severity, not a third treatment -- pass undefined so the tooltip
    // renders plain rather than inventing a style for it.
    const rawSeverity = rowNotice?.severity || info.severity;
    const severity = rawSeverity === 'caution' || rawSeverity === 'danger' ? rawSeverity : undefined;
    // Gate document #49-#52: the power and Dark Discount warnings get their approved short
    // lines with the Energized / Dark Discount / DEC icons; every other badge keeps its
    // server message (those were graded MEETS).
    const override = BADGE_TIP_OVERRIDE[b];
    const warningModel = b === 'idle'
      ? { title: tooltip, peer: 'plot_properties' }
      : {
        ...(severity ? { kind: 'warn', severity } : {}),
        title: override ? override.title : info.label,
        ...(override ? { items: override.lines(rowNotice?.message).map((effect) => ({ name: '', effect })) } : { body: tooltip }),
        peer: 'plot_properties',
      };
    chips.push({ key: b, rank: badgeRank(b), html: `<span class="${badgeClasses(b, kindCls)}"${tipAttrs(warningModel)}>${abbr}</span>` });
  }
  for (const html of landAbilityCardChips(row)) chips.push({ key: 'land_ability_card', rank: badgeRank('land_ability_card'), html });
  if (hasRuniBadge(row)) chips.push({ key: 'runi', rank: badgeRank('runi'), html: runiBadgeChip() });
  return chips.sort((a, b) => a.rank - b.rank);
}

export function badgeHtml(row) {
  const chips = badgeChips(row);
  return chips.length ? `<span class="badge-row">${chips.map((chip) => chip.html).join('')}</span>` : '';
}

// the codes badgeHtml() would render for this row, in the same rank order
// the one list badgeStripHtml() (table strip) and badge-overlay.js (the panel, via
// badgeHtml() itself) both read, so the strip and the panel can never disagree about
// which badges a row carries.
export function badgeCodesFor(row) {
  return badgeChips(row).map((chip) => chip.key);
}

export function badgeRosterLoaded(row) {
  return row?.assets_loaded === true;
}

function badgeStatusNames(codes) {
  return codes.map((code) => BADGE_ABBR[code] || badgeDefinition(code).label || code);
}

export function badgeStripStatus(row) {
  const safeRow = row || {};
  const codes = badgeCodesFor(safeRow);
  const names = badgeStatusNames(codes);
  const details = names.length ? `Badges: ${names.join(', ')}` : '';
  const result = (kind, title) => ({ kind, label: details ? `${title}. ${details}` : title });
  if (codes.some((code) => ['staged_changes', 'staged_cooldown', 'pending_change'].includes(code))) {
    return result('pending', 'Pending change');
  }
  if (codes.some((code) => badgeDefinition(code).group === 'critical')) return result('critical', 'Critical');
  if (codes.some((code) => badgeDefinition(code).group === 'warning' || badgeDefinition(code).kind === 'problem')) {
    return result('warning', 'Warning');
  }
  if (!codes.length && !badgeRosterLoaded(safeRow)) return result('loading', 'Loading: badge details not fully loaded');
  return result('clean', names.length ? 'No warning or critical badges' : 'No issues');
}

export function badgeStatusGlyph(kind) {
  if (kind === 'pending') return '<svg class="badge-status-glyph is-pending" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path class="badge-clock-hand" d="M12 6v6l4 2"/></svg>';
  if (kind === 'critical') return '<svg class="badge-status-glyph is-critical" viewBox="0 0 24 24" aria-hidden="true"><path class="badge-status-mark" d="M12 3v10.5"/><circle cx="12" cy="20" r="2.4"/></svg>';
  if (kind === 'warning') return '<svg class="badge-status-glyph is-warning" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 22 21H2z"/><path class="badge-status-mark" d="M12 7.5v5"/><circle cx="12" cy="17" r="1.6"/></svg>';
  if (kind === 'loading') return '<svg class="badge-status-glyph is-loading" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path class="badge-status-mark" d="M9.5 9a2.6 2.6 0 1 1 4.6 1.7c-1 .9-2.1 1.4-2.1 3m0 3v.1"/></svg>';
  return '<svg class="badge-status-glyph is-clean" viewBox="0 0 24 24" aria-hidden="true"><path d="m4 12 5 5L20 6"/></svg>';
}

export function badgeStripHtml(row) {
  const status = badgeStripStatus(row);
  const flagged = ['pending', 'critical', 'warning'].includes(status.kind);
  const stateClass = flagged ? 'is-flagged' : status.kind === 'loading' ? 'is-unknown' : 'is-none';
  return `<span class="badge-strip ${stateClass}" role="img" aria-label="${esc(status.label)}"${tipAttrs({ title: status.label })}>${badgeStatusGlyph(status.kind)}</span>`;
}

// Plot-level food cost (item 3, board-polish 2026-08-21): `grain_cost_per_hour` is
// `worksite_facts.grain_req_per_hour` -- established against the Phase 1.5 Food
// whitepaper (0.01 Grain/base-PP/hr) and verified against all 130 live plots with
// zero exceptions (see ceres/board.py's comment at its computation). Shown here
// not in the main table (the owner's explicit placement), always in Grain regardless
// of the plot's own produced resource -- workers eat Grain no matter what they mine.
// `worksite_facts.grain_required` (a much larger, ~22.1x figure) is a DIFFERENT
// not-yet-understood quantity and is deliberately not shown.
// his words: "Use rationing icon where applicable and name the worker which applies
// it, one line per info (boosts dont count and paid at each harvest get their own lines
// each)", and the source footnote was "confsing and unclear" -- so the foot now states the
// one fact a reader cannot see instead: the rate counts Base PP after the plot cap.
// One model for the Food/hr column cell and the food cost line under each plot.
export function foodCostTip(row) {
  const grain = tipIcon(tooltipIconPath('resource', 'grain'));
  const lines = [`1 ${grain}Grain per 100 Base PP per hour`, "Boosts don't count", 'Paid at each harvest'];
  const cards = Array.isArray(row?.food_discount_cards) ? row.food_discount_cards.filter((card) => card && card.name) : [];
  const label = { RATIONING: 'Rationing', RATIONING_LITE: 'Light Rationing' };
  const icon = { RATIONING: 'abilities/ability_rationing.png', RATIONING_LITE: 'abilities/ability_light-rationing.png' };
  cards.forEach((card) => {
    const pct = Math.round(Math.abs(Number(card.rate)) * 100);
    lines.push(`${tipIcon(icon[card.code])}${label[card.code] || 'Rationing'} −${pct}% from ${card.name}`);
  });
  const discount = Number(row?.food_discount);
  if (!cards.length && Number.isFinite(discount) && discount < 0) {
    lines.push(`${tipIcon(icon.RATIONING)}Rationing −${Math.round(Math.abs(discount) * 100)}% already included`);
  }
  return {
    title: 'Grain your workers eat',
    icon: tooltipIconPath('resource', 'grain'),
    items: lines.map((effect) => ({ name: '', effect })),
    foot: 'Base PP is counted after the plot cap.',
    peer: 'plot_metrics',
  };
}

// the Food/hr column cell -- the plot's Grain cost per hour and the shared tooltip.
export function foodPerHourCell(row) {
  if (row?.grain_cost_per_hour === null || row?.grain_cost_per_hour === undefined) return { tip: '', html: '—' };
  // the Grain icon follows the number -- decorative, no tooltip of its own ().
  const grainIcon = tooltipIconPath('resource', 'grain');
  const icon = grainIcon ? `<img class="dec-stake-icon" src="${ASSET_BASE}/${grainIcon}" alt="" aria-hidden="true">` : '';
  return { tip: tipAttrs(foodCostTip(row)), html: `${num(row.grain_cost_per_hour, 2)}${icon}` };
}

export function costLineHtml(row) {
  if (row.grain_cost_per_hour === null || row.grain_cost_per_hour === undefined) return '';
  // the same improved tooltip as the Food/hr column cell (foodCostTip above).
  return `<div class="cost-line"${tipAttrs(foodCostTip(row))}>Food cost: <strong>${num(row.grain_cost_per_hour, 2)} grain/hr</strong></div>`;
}

// the Wood/hr, Stone/hr, Iron/hr column cells -- same shape as foodPerHourCell()
// above, one per material. `field`/`resourceKey` name the flat sort key and the canonical
// icon slug (row.wood_cost_per_hour etc.; see ceres/board.py::_plot_row). Absent/null means
// this worksite's recipe has no such input (a base-resource worksite, or facts unavailable)
// never rendered as 0.00.
const MATERIAL_CELL_FIELDS = {
  WOOD: { field: 'wood_cost_per_hour', resourceKey: 'wood', label: 'Wood' },
  STONE: { field: 'stone_cost_per_hour', resourceKey: 'stone', label: 'Stone' },
  IRON: { field: 'iron_cost_per_hour', resourceKey: 'iron', label: 'Iron' },
};

export function materialCostCell(row, symbol) {
  const spec = MATERIAL_CELL_FIELDS[symbol];
  const value = row?.[spec.field];
  if (value === null || value === undefined) return { tip: '', html: '—' };
  const iconPath = tooltipIconPath('resource', spec.resourceKey);
  // review fix: NOT .dec-stake-icon (fixed 14px) -- .material-cost-icon joins the
  // shared table icon clamp (ceres.css) so it reads at the same size as every other
  // table icon (20-22px), not a small badge glyph.
  const icon = iconPath ? `<img class="material-cost-icon" src="${ASSET_BASE}/${iconPath}" alt="" aria-hidden="true">` : '';
  const tip = tipAttrs({
    title: `${spec.label} consumed per hour`,
    icon: iconPath,
    items: [{ name: '', effect: `Projected from this worksite's recipe times its effective Base PP.` }],
    peer: 'plot_metrics',
  });
  // review fix: plan §2's contract is exact -- "—" means not applicable, "0.00" means
  // applicable and measured zero. The shared num() helper (used elsewhere for Food/hr etc.)
  // strips trailing zeros for a whole number (num(0, 2) -> "0", not "0.00"), so a genuine
  // measured zero here would read as visually identical to a rounded near-zero. Force fixed
  // 2 decimals (still thousands-grouped) instead of reusing num() for this cell only.
  const formatted = value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return { tip, html: `${formatted}${icon}` };
}
