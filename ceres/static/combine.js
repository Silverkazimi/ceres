import { BOARD, CURRENT_PLAYER, loadBoard, playerQueryAppend } from './board.js';
import { COMBINE_SELECTION } from './combine-dialog.js';
import { cardFoilValue, esc, isNonCombinableFoil, num } from './format.js';
import { updatePickerRegion } from './picker-actions.js';
import { EXPANDED_ROWS, renderRow, updateMountedRow, waitForBoardToFinish } from './rows.js';
import { stageToast } from './staging.js';
import { S } from './state.js';
import { tipAttrs } from './tooltip.js';

// item 6, his words: "Combine tooltip is still bad and needs to be clearer." One
// model for every Combine door (tile chip, picker button, upgrade action) so they can never
// say different things: what happens to the card, what happens to the copies, and that a
// preview comes before anything is signed.
export function combineTipModel(count, peer, subject = 'this card') {
  const copies = `${count} idle ${Number(count) === 1 ? 'copy' : 'copies'}`;
  return {
    title: `Combine ${copies} into ${subject}`,
    items: [
      { name: '', effect: `${subject.charAt(0).toUpperCase()}${subject.slice(1)} gains their card XP (CC) and can level up.` },
      { name: '', effect: 'The merged copies are used up; one card remains.' },
    ],
    foot: 'Click to see a preview first — nothing is signed yet.',
    peer,
  };
}

// Combine door 2 (reachability pass) -- the worker-tile combine offer. It
// merges the occupant's own IDLE duplicates while it stays staked (no unstake, no
// 72h cooldown -- work/combine-reachability/spec.md's "two different offers"
// distinction, which must not be blurred). Same absent-not-disabled rule as the
// worker-table door: nothing renders unless the occupant's printing actually has
// a combinable idle copy.
export function combineTileActionHtml(row, group, index, occupant) {
  if (group.id !== 'worker' || !occupant || !S.IDLE_CARDS_CACHE) return '';
  if (occupant.card_detail_id == null || occupant.edition == null) return '';
  // A MAXED card can never gain from a combine: at the BCX ceiling `land_base_pp`
  // flatlines while BCX climbs, so the idle copies would burn for nothing. Owning
  // idle copies is therefore not sufficient to offer the chip -- the OCCUPANT must
  // still have headroom. Fails closed: an unresolvable ceiling offers nothing.
  if (!hasResolvedCombineCeiling(occupant) || isCardMaxed(occupant)) return '';
  const key = combineGroupKey(occupant);
  const copies = idleCombineGroups(S.IDLE_CARDS_CACHE).get(key);
  if (!copies || !groupHasCombinableRow(copies)) return '';
  // Owner ruling (2026-08-29): the previous label repeated the occupant's own
  // name (already shown on the card above it), hid the one fact that matters
  // how many copies would combine -- and rendered as a full-width in-tile block
  // that wrapped to three lines and visually dominated the card. This is now a
  // COMPACT CHIP, positioned above the tile by its CALLER (renderEditorGroup's
  // tileEntries.map) rather than returned as part of the tile's own HTML
  // `.slot-tile`'s `overflow: hidden` would otherwise clip anything positioned
  // above it, which is why the chip is a `.slot-tile` sibling inside
  // `.slot-tile-cell`, never a child.
  // Owner ruling (2026-08-30): the glyph rendered as tofu in his font stack
  // word + count only, no parentheses, no card name, no exotic codepoint.
  const count = copies.length;
  const label = 'Combine';
  const tip = `Combine up to ${count} idle ${count === 1 ? 'copy' : 'copies'}`;
  return `<button type="button" class="tile-combine-chip ceres-btn ceres-btn--secondary" data-control-density="dense" data-slot-action="combine-tile" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}" data-slot-index="${index}" aria-label="${esc(tip)}"${tipAttrs(combineTipModel(count, 'tile_chip_rail'))}>${esc(label)}</button>`;
}


// combineGroupKey() -> copies[], recomputed from the full idle-cards cache on
// every mount (never the filtered/sorted view) so group membership stays correct
// regardless of the Upgradeable filter's own state.
export let IDLE_COMBINE_GROUPS = new Map();
// The exact three fields fides_cards/ops.py::build_combine_op requires to match
// before it will sign (same card, same edition, same foil) -- grouping on
// anything looser would gather a dialog the server refuses to build.
export function combineGroupKey(c) {
  return `${c.card_detail_id}|${c.edition}|${cardFoilValue(c)}`;
}

export function combineGroupsFrom(cards) {
  const groups = new Map();
  (cards || []).forEach((c) => {
    if (c.card_detail_id == null || c.edition == null) return;
    if (isNonCombinableFoil(c)) return;
    const key = combineGroupKey(c);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(c);
  });
  return groups;
}

function combineCardUidKey(card) {
  return String(card?.uid || card?.card_uid || card?.asset_ref || '');
}

export function applyCombineConfirmedUpdate(dialog, preview, ledger, survivorUid) {
  if (!S.IDLE_CARDS_CACHE || !dialog?.copies?.length) return;
  const printingKey = combineGroupKey(dialog.copies[0]);
  const destroyedUids = new Set((preview?.hazard?.destroyed_uids || []).map(String));
  const kept = [];
  S.IDLE_CARDS_CACHE.forEach((card) => {
    if (!destroyedUids.has(combineCardUidKey(card))) kept.push(card);
  });
  if (survivorUid && ledger) {
    const survivor = kept.find((card) => combineCardUidKey(card) === String(survivorUid));
    if (survivor) {
      survivor.bcx = ledger.afterBcx;
      survivor.level = ledger.afterLevel;
      if (ledger.afterPp != null) survivor.base_pp = ledger.afterPp;
    }
  }
  S.IDLE_CARDS_CACHE.length = 0;
  S.IDLE_CARDS_CACHE.push(...kept);
  GROUPS_BY_COLLECTION.delete(S.IDLE_CARDS_CACHE);  // S10 / L4-04: the array changed in place, so its grouping is rebuilt on the next read
  refreshCombinePrintingSurfaces(printingKey);
}

// Re-renders every mounted plot row whose expanded worker table holds a card
// of this printing (the only rows whose "Combine N" chip
// count could have changed), plus the open worker picker if it's showing the
// same printing. Rows never expanded carry no IDLE_CARDS_CACHE-derived chip
// yet, so they need no refresh -- toggleRow() computes it fresh on open.
function refreshCombinePrintingSurfaces(printingKey) {
  if (BOARD?.plots) {
    BOARD.plots.forEach((row) => {
      if ((row.workers || []).some((worker) => worker && combineGroupKey(worker) === printingKey)) {
        updateMountedRow(row.deed_uid);
      }
    });
  }
  if (S.CERES_PICKER) updatePickerRegion(S.CERES_PICKER.deedUid);
}

// The button is a PROMISE that pressing it can do something (maintainer's rule
// 2026-08-28) -- a maxed card must never show it, not disabled, ABSENT. `bcx_max`
// is a static table lookup (see fmtCc()'s comment / ceres/bcx_cap.py), so it's
// populated whenever this printing is in the table -- a null here means the
// printing isn't covered, i.e. UNKNOWN, and must never be treated as maxed.
function isCardMaxed(c) {
  return c.bcx_max != null && c.bcx != null && c.bcx >= c.bcx_max;
}

function hasResolvedCombineCeiling(c) {
  if (c?.bcx === null || c?.bcx === undefined || c?.bcx_max === null || c?.bcx_max === undefined) return false;
  const bcx = Number(c?.bcx);
  const bcxMax = Number(c?.bcx_max);
  return Number.isFinite(bcx) && Number.isFinite(bcxMax);
}

// The other copies a row could actually combine into/from -- blocked copies
// (market-listed, delegated/rented, lock-held) can't be selected in the
// dialog, so they don't count as a usable partner.
function combinePartners(c, group) {
  return (group || []).filter((other) => other !== c && other.available !== false);
}

// Single predicate for whether a ROW gets a Combine button -- the row button
// the "N combinable" badge, and the "Upgradeable only" filter all derive from
// this (via groupHasCombinableRow below), so they can never disagree about
// which cards are actually combinable. Suppressed when: the card is already
// maxed, or it has no eligible partner left (covers both "only one copy
// owned" and "every other copy ineligible").
function rowCanCombine(c, group) {
  return !isNonCombinableFoil(c) && hasResolvedCombineCeiling(c) && !isCardMaxed(c) && combinePartners(c, group).length >= 1;
}

// A group counts toward the "N combinable" badge/filter only if at least one
// of its rows would actually show a Combine button -- e.g. two maxed copies of
// the same printing must not inflate the count when neither row offers the
// button.
export function groupHasCombinableRow(copies) {
  return (copies || []).some((c) => rowCanCombine(c, copies));
}

// Deterministic candidate list for the worker-table Combine door (reachability
// pass, work/combine-reachability/spec.md): one entry per DISTINCT printing among
// this plot's staked workers that has a combinable idle duplicate, ordered by
// ascending worker slot index -- slot indices are unique per plot, so the list a
// plot offers never depends on IDLE_CARDS_CACHE's fetch/array order. A card ON a
// plot can never be a combine input (unstake -> 72h -> combine is game-enforced
// the internal rules) -- IDLE_CARDS_CACHE already holds only idle (non-staked) copies, so
// this reuses the exact same groupHasCombinableRow()/combineGroupKey() predicate
// the Idle Cards table's own Combine button uses, never a second eligibility rule.
const GROUPS_BY_COLLECTION = new WeakMap();
// S7 / L4-03: the grouping of the idle cards is built ONCE per fetched collection (the array itself is the revision), not once per plot.
export function idleCombineGroups(cards) {
  if (!cards) return new Map();
  let groups = GROUPS_BY_COLLECTION.get(cards);
  if (!groups) { groups = combineGroupsFrom(cards); GROUPS_BY_COLLECTION.set(cards, groups); }
  return groups;
}
export function plotCombineCandidates(row, group) {
  if (!S.IDLE_CARDS_CACHE || !group || group.id !== 'worker') return [];
  if (!(group.slots || []).some((occupant) => occupant && occupant.card_detail_id != null && occupant.edition != null)) return [];  // empty plot: nothing to match
  const groups = idleCombineGroups(S.IDLE_CARDS_CACHE);
  const seen = new Set();
  const out = [];
  group.slots.forEach((occupant, slotIndex) => {
    if (!occupant || occupant.card_detail_id == null || occupant.edition == null) return;
    const key = combineGroupKey(occupant);
    if (seen.has(key)) return;
    const copies = groups.get(key);
    if (!copies || !groupHasCombinableRow(copies)) return;
    seen.add(key);
    out.push({ key, slotIndex, name: occupant.name, copies });
  });
  return out;
}

// Door 3 (candidate picker) -- same derivation as plotCombineCandidates
// combineTileActionHtml, reused rather than a third eligibility rule: a candidate
// only offers Combine when it is ITSELF one of IDLE_CARDS_CACHE's own idle copies
// (never a 'staked' bucket row -- a card on another plot cannot be a combine input)
// and rowCanCombine() says so (excludes maxed, requires >=1 eligible partner).
// Returns null (render the dash) rather than a falsy count, so callers never need
// their own maxed/partner logic either.
export function candidateCombineInfo(candidate) {
  if (!S.IDLE_CARDS_CACHE || !candidate || candidate.card_detail_id == null || candidate.edition == null) return null;
  const uid = String(candidate.uid || candidate.card_uid || candidate.asset_ref || '');
  if (!uid) return null;
  const key = combineGroupKey(candidate);
  const copies = idleCombineGroups(S.IDLE_CARDS_CACHE).get(key);
  if (!copies) return null;
  const self = copies.find((c) => String(c.uid || c.asset_ref) === uid);
  if (!self || !rowCanCombine(self, copies)) return null;
  return { key, copies, count: combinePartners(self, copies).length };
}

// Combine dialog outcome ledger (v0.7.73 redesign)
// The modal's decision is "what level does this get me, and what does it
// cost" (work/combine-modal-redesign/redesign-plan.html), not "which copies".
// Everything below computes that from fields the board already ships per idle
// card -- bcx, bcx_max, base_pp, level_bcx_thresholds (ceres/level_cap.py)
// with NO extra round trip, so the ledger updates instantly as the quantity
// control moves.

// Land PP is linear in BCX for a given printing, up to the printing's max-BCX
// ceiling, where it flatlines (the internal rules) -- never extrapolate a projection past
// it. The rate is read from whichever owned copy actually carries a nonzero
// base_pp, never from a hardcoded table: a copy that has never been staked can
// read `land_base_pp: 0` as a data artifact, not a true zero
// (work/pp-per-cc/pp-per-cc-table.md's "zero-PP artifact" section) -- deriving
// the rate from a live, staked-or-has-been-staked copy stays honest to the
// snapshot instead of trusting an unreviewed research table.
function combinePpRate(copies) {
  for (const c of copies || []) {
    const bcx = Number(c.bcx);
    const pp = Number(c.base_pp);
    if (bcx > 0 && pp > 0) return pp / bcx;
  }
  return null;
}

function combinePpAtBcx(rate, bcx, bcxMax) {
  if (rate == null || bcx == null || !Number.isFinite(bcx)) return null;
  const effective = bcxMax != null ? Math.min(bcx, bcxMax) : bcx;
  return rate * effective;
}

// Mirrors ceres/level_cap.py::level_for_bcx exactly -- the server ships the
// per-level BCX ladder once per printing (`level_bcx_thresholds`) so this side
// only ever does the arithmetic, never a second copy of the regime table.
function combineLevelForBcx(thresholds, bcx) {
  if (!thresholds || !thresholds.length || bcx == null) return null;
  let level = 0;
  for (let i = 0; i < thresholds.length; i += 1) {
    if (bcx >= thresholds[i]) level = i + 1; else break;
  }
  return level || null;
}

// Client-side PREDICTION of fides_cards/ops.py::survivor_rule (highest BCX
// survives; a tie breaks alphabetically by Card ID) -- used only to build the
// default/preset quantity selections before the round trip. The dialog always
// prefers the server's own predicted_survivor_uid the moment /api/combine/preview
// answers (renderCombineDialog); Ceres can PREDICT the survivor and cannot
// control it (the internal rules), so this is never treated as authoritative.
function combineCurrentSurvivor(copies) {
  if (!copies || !copies.length) return null;
  return copies.reduce((best, c) => {
    if (!best) return c;
    const diff = (Number(c.bcx) || 0) - (Number(best.bcx) || 0);
    if (diff > 0) return c;
    if (diff < 0) return best;
    return String(c.uid || c.asset_ref).localeCompare(String(best.uid || best.asset_ref)) < 0 ? c : best;
  }, null);
}

// Owner's ruling, 2026-08-30: consume the LARGEST non-survivor copies first
// then top up with singles. The reason is liquidity, not size -- a copy that
// already carries many CC is ONE illiquid listing, while the same CC held as
// single cards is many easy sales, so an already-merged duplicate has the
// least to lose by being consumed, and single-CC copies are the stock worth
// preserving. Sorting descending by BCX and taking the largest first IS this
// rule: singles (bcx=1) sort last and are only touched once every larger copy
// is already spent. Deterministic, so the same quantity always burns the same
// cards on any opening of this dialog.
function combineConsumptionOrder(copies, survivorUid) {
  return (copies || [])
    .filter((c) => String(c.uid || c.asset_ref) !== String(survivorUid))
    .slice()
    .sort((a, b) => (Number(b.bcx) || 0) - (Number(a.bcx) || 0)
      || String(a.uid || a.asset_ref).localeCompare(String(b.uid || b.asset_ref)));
}

// The available (unblocked) copies for a dialog -- the pool every quantity
// control, preset and consumption order draws from. Blocked copies (market
// listed, delegated/rented, lock-held, no explicit Card ID) can never be
// selected, same as the old per-row checkbox model.
export function combineAvailableCopies(dialog) {
  return (dialog?.copies || []).filter((c) => !dialog.blocked.has(String(c.uid || c.asset_ref)));
}

// Smallest quantity (survivor + N consumed, largest-first) whose resulting BCX
// reaches `targetBcx` -- the engine behind the "+1 level" preset ("To max level"
// is combineFitToMaxCopies since ). Falls back to every available copy if the target is unreachable
// with what's owned.
function combineQuantityForTargetBcx(available, survivor, targetBcx) {
  let bcx = Number(survivor.bcx) || 0;
  let count = 1;
  if (bcx < targetBcx) {
    const ordered = combineConsumptionOrder(available, String(survivor.uid || survivor.asset_ref));
    for (const c of ordered) {
      bcx += Number(c.bcx) || 0;
      count += 1;
      if (bcx >= targetBcx) break;
    }
  }
  return Math.max(2, Math.min(count, available.length));
}

// the copies whose CC total is the largest at or under the print's max CC -- exactly max when the owned copies
// allow it, never over (: "no overcombine"; PP stops at max CC). At least two copies, fewest copies on a tie, then
// the larger copies first (the 2026-08-30 liquidity rule: singles are the stock worth keeping). The same rule as the
// server's proposed set (ceres/card_combinables.py best_combine_set). The old "To max level" preset added copies largest
// first until max was reached and so could pass it (6 + 4 + 3 = 13 on an 11-CC card). null when no two copies fit.
export function combineFitToMaxCopies(available, bcxMax) {
  const max = Math.floor(Number(bcxMax));
  if (!(max > 0)) return null;
  const pool = (available || [])
    .filter((c) => Number.isInteger(Number(c.bcx)) && Number(c.bcx) > 0 && Number(c.bcx) <= max)
    .slice()
    .sort((a, b) => (Number(b.bcx) || 0) - (Number(a.bcx) || 0)
      || String(a.uid || a.asset_ref).localeCompare(String(b.uid || b.asset_ref)));
  // state (CC total, copy count class 0/1/2 = "two or more") -> { n, copy, prev }: a linked chain, never copied arrays
  // so a 400-single common stays fast in the browser.
  const best = new Map([['0|0', { total: 0, klass: 0, n: 0, copy: null, prev: null }]]);
  for (const copy of pool) {
    const cc = Number(copy.bcx);
    for (const node of [...best.values()]) {
      const total = node.total + cc;
      if (total > max) continue;
      const klass = Math.min(node.klass + 1, 2);
      const key = `${total}|${klass}`;
      const current = best.get(key);
      if (!current || node.n + 1 < current.n) best.set(key, { total, klass, n: node.n + 1, copy, prev: node });
    }
  }
  let pick = null;
  for (const node of best.values()) {
    if (node.klass !== 2) continue;
    if (!pick || node.total > pick.total || (node.total === pick.total && node.n < pick.n)) pick = node;
  }
  if (!pick) return null;
  const out = [];
  for (let node = pick; node && node.copy; node = node.prev) out.push(node.copy);
  return out.reverse();
}

function combineBcxMax(available) {
  const withMax = (available || []).find((c) => c.bcx_max !== null && c.bcx_max !== undefined && Number.isFinite(Number(c.bcx_max)));
  return withMax ? Number(withMax.bcx_max) : null;
}

// the copies the "To max level" preset selects (combineFitToMaxCopies), or null when the max CC is unknown.
export function combineToMaxCopies(dialog) {
  const available = combineAvailableCopies(dialog);
  const bcxMax = combineBcxMax(available);
  return bcxMax == null ? null : combineFitToMaxCopies(available, bcxMax);
}

// The three preset quantities this dialog offers, keyed by name -- undefined
// when a preset doesn't apply (no BCX ceiling known, or already at max level).
// toMax is the size of the fit-to-max selection (combineToMaxCopies), which never passes max CC.
export function combinePresetQuantities(dialog) {
  const available = combineAvailableCopies(dialog);
  const survivor = combineCurrentSurvivor(available);
  const out = { everything: available.length };
  if (!survivor) return out;
  const thresholds = survivor.level_bcx_thresholds;
  if (thresholds && thresholds.length && survivor.level != null && survivor.level < thresholds.length) {
    // thresholds[level] (0-indexed) is the BCX needed for level (level + 1).
    out.plusOne = combineQuantityForTargetBcx(available, survivor, thresholds[survivor.level]);
  }
  const fit = combineToMaxCopies(dialog);
  if (fit) out.toMax = fit.length;
  return out;
}

// selects an explicit set of copies (the fit-to-max preset, or the set Find combinables proposed).
// Only available copies are taken; fewer than two leaves the selection unchanged and returns false. `preset` names the
// preset button shown as chosen ('to_max', 'proposed').
export function combineApplySelection(dialog, copies, preset = null) {
  const allowed = new Map(combineAvailableCopies(dialog).map((c) => [String(c.uid || c.asset_ref), c]));
  const chosen = (copies || []).map((c) => allowed.get(String(typeof c === 'string' ? c : (c.uid || c.asset_ref)))).filter(Boolean);
  if (new Set(chosen).size < 2 || chosen.length !== (copies || []).length) return false;
  COMBINE_SELECTION.clear();
  chosen.forEach((c) => COMBINE_SELECTION.set(String(c.uid || c.asset_ref), c));
  dialog.quantity = COMBINE_SELECTION.size;
  dialog.preset = preset;
  return true;
}

// Applies a QUANTITY (total copies participating, survivor included) by
// picking copies in combineConsumptionOrder -- this is the single place that
// turns "how many" into "which explicit Card IDs", so every caller (presets
// the stepper, the dialog's initial default) always burns the same cards for
// the same quantity.
export function combineApplyQuantity(dialog, quantity) {
  const available = combineAvailableCopies(dialog);
  const survivor = combineCurrentSurvivor(available);
  if (!survivor) return;
  const clamped = Math.max(2, Math.min(Math.round(quantity), available.length));
  const consumed = combineConsumptionOrder(available, String(survivor.uid || survivor.asset_ref)).slice(0, clamped - 1);
  COMBINE_SELECTION.clear();
  COMBINE_SELECTION.set(String(survivor.uid || survivor.asset_ref), survivor);
  consumed.forEach((c) => COMBINE_SELECTION.set(String(c.uid || c.asset_ref), c));
  dialog.quantity = clamped;
  dialog.preset = null;  // a quantity is never an explicit preset selection
}

// Default quantity on open: "To max level" when a BCX ceiling is known for this
// printing, else every available copy (there's no ceiling to aim short of).
export function combineDefaultQuantity(dialog) {
  const presets = combinePresetQuantities(dialog);
  return presets.toMax ?? presets.everything;
}

// The outcome ledger's before/after figures for the dialog's CURRENT selection
// (COMBINE_SELECTION) -- level, BCX and PP, computed fresh on every render so
// moving the quantity control always reflects the actual chosen copies, never a
// stale snapshot of an earlier quantity.
export function combineLedgerData(dialog) {
  const available = combineAvailableCopies(dialog);
  const selected = [...COMBINE_SELECTION.values()];
  // a fit-to-max or proposed set need not hold the largest copy, so "Now" is the selection's own survivor
  const survivor = combineCurrentSurvivor(selected.length ? selected : available);
  if (!survivor) return null;
  const beforeBcx = Number(survivor.bcx) || 0;
  const afterBcx = selected.length
    ? selected.reduce((sum, c) => sum + (Number(c.bcx) || 0), 0)
    : beforeBcx;
  const thresholds = survivor.level_bcx_thresholds;
  const bcxMax = survivor.bcx_max ?? null;
  const rate = combinePpRate(dialog.copies);
  const beforePp = Number(survivor.base_pp) > 0 ? Number(survivor.base_pp) : combinePpAtBcx(rate, beforeBcx, bcxMax);
  const afterPp = combinePpAtBcx(rate, afterBcx, bcxMax);
  const beforeLevel = survivor.level ?? combineLevelForBcx(thresholds, beforeBcx);
  const afterLevel = combineLevelForBcx(thresholds, afterBcx) ?? beforeLevel ?? null;
  const maxLevel = thresholds && thresholds.length ? thresholds.length : null;
  return {
    survivor, beforeBcx, afterBcx, beforeLevel, afterLevel, beforePp, afterPp, maxLevel,
    isMaxed: maxLevel != null && afterLevel != null && afterLevel >= maxLevel,
  };
}

function combineCardByUid(uid, hazardRow = null) {
  const key = String(uid);
  const selected = COMBINE_SELECTION.get(key);
  const cached = (S.IDLE_CARDS_CACHE || []).find((card) => String(card.uid || card.card_uid || card.asset_ref) === key);
  const source = selected || cached || {};
  return {
    ...source,
    asset_ref: key,
    name: source.name && source.name !== 'Unknown card' ? source.name : (hazardRow?.name && hazardRow.name !== 'Unknown card' ? hazardRow.name : 'Unknown card'),
    bcx: source.bcx ?? hazardRow?.bcx,
    level: source.level ?? hazardRow?.level,
    foil: source.foil ?? hazardRow?.foil,
    edition: source.edition ?? hazardRow?.edition,
  };
}

function combineHazardRows(preview) {
  const hazardRows = preview?.hazard?.rows || [];
  const byUid = new Map(hazardRows.map((row) => [String(row.card_id), row]));
  return (preview?.uids || []).map((uid) => combineCardByUid(uid, byUid.get(String(uid))));
}

function combineBlockedLabel(reason) {
  const text = String(reason || 'Blocked');
  const lower = text.toLowerCase();
  if (lower.includes('unstaking')) return 'Unstaking from Land';
  if (lower.includes('staked')) return 'Staked on Land';
  if (lower.includes('listed')) return 'Listed';
  if (lower.includes('rented')) return 'Rented';
  if (lower.includes('delegated')) return 'Delegated';
  if (lower.includes('card set')) return 'In a card set';
  if (lower.includes('unlock')) return 'Mid-unlock';
  if (lower.includes('lock')) return 'Locked';
  if (lower.includes('max')) return 'Already at max level';
  if (lower.includes('black foil') || lower.includes('arcane')) return 'Black Foil or Arcane Variant';
  return text;
}

// where a copy is, in words a player can read -- the plot label when it is staked, else its status. A Card ID
// never reaches visible text (it stays in data-uid and the selection map for signing).
export function combineCopyWhere(copy, blockedReason = null) {
  const plot = copy?.plot || copy?.plot_label;
  if (plot) return String(plot);
  const status = String(copy?.status || '').toLowerCase();
  const reason = String(blockedReason || copy?.blocked_reason || '').toLowerCase();
  if (status === 'cooling' || reason.includes('cooling') || reason.includes('unstaking')) return 'Cooling';
  if (reason.includes('listed')) return 'Listed';
  if (reason.includes('rented') || reason.includes('delegated')) return 'Rented';
  if (reason.includes('lock')) return 'Locked';
  if (status === 'staked' || reason.includes('staked')) return 'Staked';
  return 'Idle';
}

// one copy in words ("Lv 3 · 12 CC · Idle") for refusal texts that the server words with Card IDs.
function combineCopyWords(copy) {
  return `Lv ${copy?.level ?? '—'} · ${num(copy?.bcx, 0)} CC · ${combineCopyWhere(copy)}`;
}

const COMBINE_UID_PATTERN = /\b[A-Z]{1,3}\d*-\d+-[A-Za-z0-9]+\b/g;
function combineScrub(text) {
  const copies = new Map();
  [...COMBINE_SELECTION.values(), ...(S.IDLE_CARDS_CACHE || [])].forEach((c) => copies.set(String(c.uid || c.card_uid || c.asset_ref), c));
  return String(text ?? '').replace(COMBINE_UID_PATTERN, (uid) => (copies.has(uid) ? `the ${combineCopyWords(copies.get(uid))} copy` : 'a copy'))
    .replace(/Card IDs?/g, 'card details');
}

export function combineCopyRowsHtml(dialog, preview, survivorUid) {
  const hazardRows = combineHazardRows(preview);
  const hazardByUid = new Map(hazardRows.map((card) => [String(card.asset_ref), card]));
  const serverDestroyed = preview?.hazard ? new Set(preview.hazard.destroyed_uids || []) : null;
  const selected = new Set(COMBINE_SELECTION.keys());
  const copies = (dialog?.copies || []).map((copy) => hazardByUid.get(String(copy.uid || copy.asset_ref)) || copy);
  const selectedRows = copies.filter((copy) => selected.has(String(copy.uid || copy.asset_ref)));
  const blockedRows = copies.filter((copy) => dialog.blocked.has(String(copy.uid || copy.asset_ref)) || isCardMaxed(copy));
  const contextRows = copies.filter((copy) => !selected.has(String(copy.uid || copy.asset_ref)) && !blockedRows.includes(copy)).slice(0, 3);
  const visible = [...selectedRows, ...blockedRows, ...contextRows];
  const seen = new Set();
  const rows = visible.filter((copy) => {
    const key = String(copy.uid || copy.asset_ref);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const renderRow = (copy) => {
    const uid = String(copy.uid || copy.asset_ref || '');
    const blockedReason = dialog.blocked.get(uid) || (isCardMaxed(copy) ? 'is already at max level' : null);
    const isSurvivor = !blockedReason && selected.has(uid) && uid === String(survivorUid || '');
    const isDestroyed = !blockedReason && selected.has(uid) && !isSurvivor && (serverDestroyed ? serverDestroyed.has(uid) : true);
    const mark = isSurvivor ? '✚' : isDestroyed ? '✕' : '·';
    const state = isSurvivor ? 'Survives' : isDestroyed ? 'Destroyed' : blockedReason ? combineBlockedLabel(blockedReason) : 'Not included';
    const stateClass = isSurvivor ? 'survives' : isDestroyed ? 'destroyed' : blockedReason ? 'blocked' : 'not-included';
    const tooltip = isSurvivor
      ? 'Survives: the game keeps the highest CC; on a tie it keeps the first copy listed.'
      : blockedReason || '';
    return `<li class="combine-copy-row is-${stateClass}"${tooltip ? ` title="${esc(tooltip)}"` : ''}>` +
      `<span class="combine-copy-mark" aria-hidden="true">${mark}</span>` +
      `<code class="combine-copy-id${isDestroyed ? ' is-destroyed' : ''}" data-uid="${esc(uid)}">${esc(combineCopyWhere(copy, blockedReason))}</code>` +
      `<span class="combine-copy-meta">${num(copy.bcx, 0)} CC · Lv ${esc(copy.level ?? '—')}</span>` +
      `<span class="combine-copy-state">${esc(state)}</span></li>`;
  };
  const omitted = copies.length - rows.length;
  return `<ul class="combine-copy-list" role="list" aria-label="Cards in this combine">${rows.map(renderRow).join('')}` +
    `${omitted > 0 ? `<li class="combine-copy-more">+ ${omitted} more copies not included</li>` : ''}</ul>`;
}

export function combineReason(preview) {
  const detail = preview?.detail || {};
  const parts = [];
  if (detail.message) parts.push(detail.message);
  if (Array.isArray(detail.reasons)) {
    detail.reasons.forEach((item) => {
      if (item?.uid && item?.reason) parts.push(`${combineCopyWords(combineCardByUid(item.uid))}: ${combineScrub(item.reason)}`);
    });
  }
  if (detail.missing_uids?.length) parts.push(`${detail.missing_uids.length} selected ${detail.missing_uids.length === 1 ? 'copy is' : 'copies are'} no longer in your collection`);
  return parts.map(combineScrub).join(' — ') || preview?.error || 'The server did not provide a refusal reason.';
}

export function combineObservedAt(preview) {
  return preview?.payload?.observed_at || preview?.detail?.preview?.observed_at || preview?.detail?.observed_at || 'unknown';
}

// S7 / L4-01: the player whose idle-cards request is in flight (null = none). A request is only ever applied to the player it was started
// for, and a request for one player never blocks the next player's own request.
let IDLE_CARDS_LOADING = null;

// Post-combine board settle (): a confirmed combine mutates the account's
// card collection server-side, and `routes_op_preview.py`'s `op_verify` starts a
// background FULL recapture for it (the only capture mode that re-reads the
// collection -- see that route's own comment). This polls GET /api/capture/status
// the same way the existing capture-status flow does, then RENDERS that finished
// snapshot -- `loadBoard({ fromCompletedCapture: true })`, never a plain loadBoard()
// which would POST /api/capture/refresh and sweep the game's API a second time for data
// this function has just watched land (). loadBoard()'s own render() call already
// nulls and re-fetches IDLE_CARDS_CACHE, so nothing here needs to touch it directly. Never blocks the dialog-close/toast above; runs
// entirely in the background so the player is free to keep working while it settles.
export async function settleCombineRefresh(combineRefresh, { player = CURRENT_PLAYER, after = null } = {}) {
  let adoptTries = 0;   // C01: a board load that fails after the capture settled is retried a few times, never reported as success
  const poll = async () => {
    if (CURRENT_PLAYER !== player) return;
    let status;
    try {
      status = await (await fetch(playerQueryAppend('/api/capture/status'))).json();
    } catch (_err) {
      setTimeout(poll, 2000); // transient fetch failure -- keep trying
      return;
    }
    if (status.state === 'running' || status.state === 'queued') {   // C01: 'queued' is a replacement capture waiting for its worker, not a finished one
      stageToast(status.total_deeds
        ? `Updating board after combine — ${status.fetched_deeds || 0}/${status.total_deeds}…`
        : 'Updating board after combine…');
      setTimeout(poll, 2000);
      return;
    }
    if (status.state === 'failed') {
      stageToast(`Board update after the combine failed: ${status.error || 'unknown read error'}.`, true);
      return;
    }
    // the server's post-combine capture has just been polled to completion
    // above; rendering it must not trigger a second sweep of the game's API.
    if (CURRENT_PLAYER !== player) return;
    const loaded = await loadBoard({ fromCompletedCapture: true });
    if (loaded === false) {
      if (++adoptTries < 5) { setTimeout(poll, 2000); return; }
      stageToast('The combine is confirmed, but the board could not be refreshed. Reload the page to see the combined card.', true);
      return;
    }
    if (CURRENT_PLAYER === player) after?.();
    stageToast('Board updated with the combined card.');
  };
  if (combineRefresh && combineRefresh.triggered === false) {
    // The server failed to start the post-combine board update outside the normal
    // running/failed run-state machine; surface that failure directly.
    stageToast('Could not start the post-combine board update.', true);
    return;
  }
  await poll();
}

// Background prefetch, unconditional on any UI being open -- populates
// IDLE_CARDS_CACHE as soon as the board loads so every combine door is live
// immediately rather than only once some now-deleted element was opened.
// Uses GET /api/idle-cards, which reads only the cached snapshot -- like
// GET /api/board, this never triggers a live network capture.
export async function prefetchIdleCardsCache() {
  const owner = BOARD?.player ?? null;
  if (!BOARD?.idle_cards_loaded || S.IDLE_CARDS_CACHE !== null || IDLE_CARDS_LOADING === owner) return;
  IDLE_CARDS_LOADING = owner;
  const stillMine = () => (BOARD?.player ?? null) === owner;
  try {
    let resp = await fetch(playerQueryAppend('/api/idle-cards'));
    // a 409 `board_finishing` is the interim window of a whale's read -- the previous
    // snapshot is never served (). Quiet no-op: wait for the status token to change at
    // the cadence, then retry once. Never a toast.
    if (resp.status === 409) {
      const refusal = await resp.clone().json().catch(() => ({}));
      if (refusal?.reason !== 'board_finishing') return;
      const player = BOARD?.player ?? null;
      const stillCurrent = () => S.IDLE_CARDS_CACHE === null && (BOARD?.player ?? null) === player;
      await waitForBoardToFinish(stillCurrent);
      if (!stillCurrent()) return;
      resp = await fetch(playerQueryAppend('/api/idle-cards'));
    }
    if (!resp.ok || S.IDLE_CARDS_CACHE !== null || !stillMine()) return; // a real load may have started meanwhile, or the player changed
    const body = await resp.json();
    if (S.IDLE_CARDS_CACHE !== null || !stillMine()) return;
    S.IDLE_CARDS_CACHE = body.cards || [];
    // an editor or picker opened while this fetch was slow rendered with no
    // combine offer; repaint the mounted rows that now gain one (updateMountedRow keeps
    // the open .editor-band host) and the open picker, instead of waiting for a redraw.
    (BOARD?.plots || []).forEach((row) => {
      if (EXPANDED_ROWS.has(row.deed_uid) || plotCombineCandidates(row, { id: 'worker', slots: row.workers || [] }).length) updateMountedRow(row.deed_uid);
    });
    if (S.CERES_PICKER) updatePickerRegion(S.CERES_PICKER.deedUid);
  } catch (_) {
    // best-effort -- combine doors simply stay unavailable until a retry succeeds
  } finally {
    if (IDLE_CARDS_LOADING === owner) IDLE_CARDS_LOADING = null;
  }
}
