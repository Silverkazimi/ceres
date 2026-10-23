""" source/VM guards for the board badge filter's shared truth."""

from __future__ import annotations

from .vm_context import STATIC, run_in_module_context


INDEX_SOURCE = (STATIC / "index.html").read_text(encoding="utf-8")


def test_badges_category_exists_with_neutral_any_default() -> None:
    assert INDEX_SOURCE.count('id="badge-filter"') == 1
    select = INDEX_SOURCE.split('id="badge-filter"', 1)[1].split("</select>", 1)[0]
    assert '<option value="">All</option>' in select


def test_options_and_accepted_rules_use_the_real_badge_predicate_in_a_vm() -> None:
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
BOARD = {badge_meta: {custom_badge: {label: 'Custom badge'}}};
const labels = new Map(badgeFilterOptions().map((option) => [option.key, option.label]));
for (const [key, label] of [
  ['staged_changes', 'X Changes'], ['staged_cooldown', 'Waiting'], ['pending_change', 'Building'],
  ['bloodline_paired', 'PAIRED'], ['bloodline_solo', 'SOLO'],
  // key renamed sole_ability_holder -> land_ability_card (maintainer decision 2026-09-16: neutral
  // land-ability identity; label text "Land Card / VoV" itself is unchanged).
  ['land_ability_card', 'Land Card / VoV'], ['underfilled', 'Open slots'],
  ['over_cap', 'Over Cap'], ['region_dec', 'Region DEC'], ['runi', 'RUNI'],
  ['custom_badge', 'Custom badge'],
]) assert(labels.get(key) === label, `${key} must use the Badges column label`);

const baseRow = {deed_uid: 'plot', badges: [], capacity: {}, workers: [], bonus: {}};
CERES_STAGE.splice(0, CERES_STAGE.length, {deed_uid: 'plot', action: 'stake'});
assert(rowHasBadge(baseRow, 'staged_changes'), 'X Changes must use the unsigned staged-plan truth');
CERES_STAGE.splice(0, CERES_STAGE.length, {
  deed_uid: 'plot', action: 'stake', cooldown: {ready: false, state: 'cooldown'},
});
assert(rowHasBadge(baseRow, 'staged_cooldown'), 'Waiting must use the cooldown chip truth');
assert(rowHasBadge({...baseRow, pending_change: {to_label: 'Grain'}}, 'pending_change'), 'Building must cover a pending worksite change');

const paired = {...baseRow, workers: [{land_abilities: [['BLOODLINE', 0.1, 'Undead', {active: true, applied: true, companions: ['Ally']}]]}]};
const solo = {...baseRow, workers: [{land_abilities: [['BLOODLINE', 0.1, 'Undead', {active: false}]]}]};
assert(rowHasBadge(paired, 'bloodline_paired'), 'PAIRED must use the paired chip truth');
assert(rowHasBadge(solo, 'bloodline_solo'), 'SOLO must use the solo chip truth');

// v0.9.513 ("retain green Land Card identity"): every row.ability_holders entry renders its
// chip regardless of row.badges, including cards whose abilities are shared with another worker
// the old badges-array gate before the chip could show was deliberately dropped.
const sole = {...baseRow, badges: [], workers: [{asset_ref: 'card', land_abilities: [['GRAIN', 0.1]]}],
  ability_holders: [{asset_ref: 'card', name: 'Land card', level: 1, card_detail_id: 866, sole_holder_for: ['GRAIN']}]};
assert(rowHasBadge(sole, 'sole_ability_holder'), 'a rendered ability holder must match regardless of the badges gate');
assert(!rowHasBadge({...sole, ability_holders: []}, 'sole_ability_holder'), 'no ability holder means no chip');

assert(rowHasBadge({...baseRow, region_efficiency: 0.5}, 'region_dec'), 'Region DEC must use the rendered regional chip');
assert(rowHasBadge({...baseRow, capacity: {runi_staked: true}}, 'runi'), 'Runi must use capacity.runi_staked');
assert(rowHasBadge({...baseRow, badges: ['custom_badge']}, 'custom_badge'), 'ordinary codes must use row.badges');

const source = {
  deed_uid: 'source', _badge_roster_loaded: true, capacity: {card_capacity: 2, worker_count: 2, open_slots: 0},
  workers: [{slot: 1, asset_ref: 'moving', base_pp: 70000}, {slot: 2, asset_ref: 'staying', base_pp: 40000}],
  badges: ['over_cap'], base_pp: 110000, base_pp_cap: 100000, base_pp_after_cap: 100000,
};
CERES_STAGE.splice(0, CERES_STAGE.length, {
  deed_uid: 'target', _source_deed_uid: 'source', asset_ref: 'moving',
  base: {status: 'point', value: 70000, low: null, high: null},
});
assert(rowHasBadge(source, 'underfilled'), 'the real caller must project a cross-plot source slot opening');
assert(!rowHasBadge(source, 'over_cap'), 'the real caller must project a cross-plot source move below cap');
""",
        entry=STATIC / "badges.js",
    )


def test_matches_filters_calls_the_real_badge_predicate_in_a_vm() -> None:
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
const controls = new Map([
  ['text-filter', {value: ''}], ['region-filter', {value: ''}], ['rarity-filter', {value: ''}],
  ['terrain-filter', {value: ''}], ['resource-filter', {value: ''}], ['status-filter', {value: ''}],
  ['badge-filter', {value: 'filter_me'}], ['hide-idle', {checked: false}],
  ['staged-only', {checked: false}],
]);
document.getElementById = (id) => controls.get(id) || null;
const shown = {plot: '1-1-1', deed_uid: 'shown', badges: ['filter_me'], capacity: {worker_count: 1}, workers: []};
const hidden = {...shown, deed_uid: 'hidden', badges: []};
assert(matchesFilters(shown), 'matching badge must remain in the normal filter pipeline');
assert(!matchesFilters(hidden), 'non-matching badge must leave the normal filter pipeline');
""",
        entry=STATIC / "board.js",
    )


def test_underfilled_and_over_cap_use_real_staged_state_in_a_vm() -> None:
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) { console.error(message); throw new Error(message); } }
const lean = {assets_loaded: true, capacity: {worker_count: 3, open_slots: 2, card_capacity: 5}, badges: ['underfilled'], workers: []};
let state = effectiveUnderfilledState(lean);
assert(state.active && state.openSlots === 2, 'lean row must preserve backend underfilled truth');

const staged = {deed_uid: 'staged', _badge_roster_loaded: true, capacity: {card_capacity: 5, worker_count: 5, open_slots: 0}, workers: [
  {slot: 0, base_pp: 1500}, {slot: 1}, {slot: 2}, {slot: 3}, {slot: 4}, {slot: 5},
]};
state = effectiveUnderfilledState(staged);
assert(!state.active && state.openSlots === 0, 'full staged roster must not be underfilled');
staged.workers = staged.workers.filter((worker) => worker.slot !== 5);
state = effectiveUnderfilledState(staged, [{deed_uid: 'staged'}]);
assert(state.active && state.openSlots === 1, 'staged removal must expose one open monster slot');
staged.workers = staged.workers.filter((worker) => worker.slot === 0);
state = effectiveUnderfilledState(staged, [{deed_uid: 'staged'}]);
assert(state.active && state.openSlots === 5, 'Runi must not consume a monster slot');
staged.workers = [];
assert(!effectiveUnderfilledState(staged, [{deed_uid: 'staged'}]).active, 'empty plot must preserve the worker-count gate');

const crossingUp = {
  deed_uid: 'up', _badge_roster_loaded: true, badges: [], base_pp: 90000, base_pp_cap: 100000,
  base_pp_after_cap: 90000, workers: [{slot: 1, base_pp: 60000}, {slot: 2, base_pp: 50000}],
};
let capState = effectiveOverCapState(crossingUp, [{deed_uid: 'up', base: {status: 'point', value: 20000, low: null, high: null}}]);
assert(capState.active && capState.before === 110000 && capState.after === 100000, 'staged add must cross over cap');

const crossingDown = {
  deed_uid: 'down', _badge_roster_loaded: true, badges: ['over_cap'], base_pp: 110000, base_pp_cap: 100000,
  base_pp_after_cap: 100000, workers: [{slot: 1, base_pp: 50000}, {slot: 2, base_pp: 40000}],
};
capState = effectiveOverCapState(crossingDown, [{deed_uid: 'down', base: {status: 'point', value: -20000, low: null, high: null}}]);
assert(!capState.active && capState.before === 90000, 'staged removal must cross below cap');

const leanOverCap = {assets_loaded: true, badges: ['over_cap'], base_pp: 110000, base_pp_cap: 100000,
  base_pp_after_cap: 100000, capacity: {worker_count: 2}, workers: []};
capState = effectiveOverCapState(leanOverCap, [{deed_uid: 'lean', base: {status: 'point', value: -110000, low: null, high: null}}]);
assert(capState.active && capState.before === 110000, 'restored stage on a lean row must preserve live over-cap truth');

const unknown = {...crossingDown, workers: [{slot: 1}]};
capState = effectiveOverCapState(unknown, [{deed_uid: 'down', base: {status: 'unknown', value: null, low: null, high: null}}]);
assert(capState.active && capState.before === 110000, 'non-point projection must preserve live badge truth');

const source = {
  deed_uid: 'source', _badge_roster_loaded: true, capacity: {card_capacity: 2, worker_count: 2, open_slots: 0},
  workers: [{slot: 1, asset_ref: 'moving', base_pp: 70000}, {slot: 2, asset_ref: 'staying', base_pp: 40000}],
  badges: ['over_cap'], base_pp: 110000, base_pp_cap: 100000, base_pp_after_cap: 100000,
};
const move = {deed_uid: 'target', _source_deed_uid: 'source', asset_ref: 'moving', base: {status: 'point', value: 70000, low: null, high: null}};
state = effectiveUnderfilledState(source, [move]);
assert(state.active && state.openSlots === 1, 'cross-plot source removal must open the source slot');
capState = effectiveOverCapState(source, [move]);
assert(!capState.active && capState.before === 40000, 'cross-plot source removal must update source over-cap truth');
""",
        entry=STATIC / "badge-state.js",
    )


def test_partly_loaded_badge_is_removed_even_from_cached_payloads() -> None:
    run_in_module_context(
        r"""
BOARD = {badge_meta: {
  assets_not_loaded: {label: 'Partly loaded', kind: 'property'},
  building_in_box: {label: 'Building in box', kind: 'property'},
}};
const row = {deed_uid: 'plot', badges: ['assets_not_loaded', 'building_in_box'], capacity: {}, workers: [], bonus: {}};
const html = badgeHtml(row);
if (html.includes('assets_not_loaded') || html.includes('Partly loaded') || html.includes('…')) throw new Error('Retired badge rendered');
if (!html.includes('badge-building_in_box')) throw new Error('Unrelated badge removed');
if (badgeFilterOptions().some(x => x.key === 'assets_not_loaded')) throw new Error('Retired filter remains');
if (rowHasBadge(row, 'assets_not_loaded')) throw new Error('Retired badge still matches');
""",
        entry=STATIC / "badges.js",
    )
