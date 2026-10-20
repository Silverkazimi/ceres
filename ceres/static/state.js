// state.js -- the nine bindings that are assigned from more
// than one module (STATE-MATRIX section 2, widened per
// TDZ-DIAGNOSIS.md). They live on one object because
// assignment to an imported binding is a TypeError.
export const S = {
  // Font-size state used by the runtime relock check.
  LAST_ROOT_FONT_SIZE: null,
  LAST_COLUMN_LAYOUT_TIER: null,
  SORT_KEY: 'harvest_pp',
  SORT_DIR: -1,
  CERES_PICKER: null,
  // Card-derived surfaces change only after domain verification confirms the game effect.
  CERES_CHECKOUT_OPEN: false,
  // {loading, error, payload} for the op preview currently staged in the open
  // modal, or null before Confirm is first clicked. Reset on every open/close and
  // on every tile re-selection -- never carried across plots or targets.
  WORKSITE_MODAL_PREVIEW: null,
  // {chunk, txId} for the most recent broadcast this modal session has verified
  // or null -- lets the "Check again" verdict button re-run the same op-verify
  // call without re-broadcasting anything.
  WORKSITE_MODAL_LAST_VERIFY: null,
  // IDLE_CARDS_CACHE holds the fetched idle-cards array for the CURRENTLY
  // loaded board only -- render() resets it to null on every fresh board
  // load/player switch, so a stale player's idle cards never leak into a new
  // one's combine doors. The rendering surface that used to sit on top of this
  // cache (the "Idle cards" <details> table, its "Upgradeable only" filter
  // and the "Unassigned bonus assets" section below) was removed 2026-08-30
  // this fetch is kept because all three combine doors (the tile chip, the
  // worker-table Combine button, and the picker column's Combine button) read
  // IDLE_CARDS_CACHE directly and would otherwise go dark.
  IDLE_CARDS_CACHE: null,
};
// Stage B visual layer (2026-08-21). Cell-decoration helpers only -- the
// table structure (columns, sort, filters, sections) is untouched per
// the owner's hard constraint. Every helper here reads from
// ceres/static/assets/manifest.json's inventory; nothing is fetched or
// guessed at runtime that isn't already in that manifest.
export const ASSET_BASE = '/static/assets';
// The plot table's total DOM column count -- Plot, Rarity, Type, Terrain, Worksite
// Total PP, Boostable, Base PP, Totem, Title, Boost, Harvest/hr, Food/hr, Wood/hr, Stone/hr
// Iron/hr, Workers, DEC stake, Lock, Count (the listed-plot counter, ). Used by every expand/error/spacer colSpan (rows.js, table.js)
// keep it and index.html's <colgroup>/<thead> in lockstep. A display:none column (the
// html.ceres-lw-le-1759 Wood/Stone/Iron hide, see ceres.css) still counts: colSpan measures DOM
// columns, not visible ones.
export const PLOT_TABLE_COLUMN_COUNT = 20;

// Edition 19 is not contiguous: Lumina Lightweaver was added as card 1051.
// Mirrored from praetoria/sets.py; badge identity is independent of ability level.
export const LAND_CARD_IDS = [866, 867, 868, 869, 1051];
export const VOV_ID_RANGE = [985, 1038];
