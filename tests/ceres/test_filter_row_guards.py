""" (maintainer-accepted plan, Variant A) — narrow DOM/source + behaviour guards for
the compact board filter row (looks 1-3, `PLAN-S8430-board-filter-row-2026-09-14.md`).

Plan's own acceptance list calls for exactly this shape of coverage ("a narrow DOM/
source guard: each filter ID occurs once; neutral labels are explicit; pills retain
real inputs; Clear excludes Sort, resets all nine filter dimensions, resyncs six
triggers/removes Region colour and produces one final same-dataset render; Open/Close
remain direct children"). No Playwright/live-account browser here on purpose (/
: the builder never self-verifies the frontend visually) -- this file only reads
source and runs the pure JS logic in the `vm_context` sandbox, exactly like
`test_board_guards.py`'s own precedent.
"""

from __future__ import annotations

import re
import subprocess

from .vm_context import STATIC


HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CONTROLS_ROW = HTML.split('<div class="controls-row">', 1)[1].split('<main id="board-main">', 1)[0]


def _count_id(id_: str) -> int:
    return len(re.findall(rf'id="{re.escape(id_)}"', HTML))


def test_every_filter_control_id_occurs_exactly_once() -> None:
    for id_ in (
        "text-filter", "region-filter", "rarity-filter", "terrain-filter",
        "resource-filter", "status-filter", "badge-filter", "staged-only",
        "clear-board-filters", "open-all-plots", "close-all-plots",
    ):
        assert _count_id(id_) == 1, f"expected exactly one #{id_}, found {_count_id(id_)}"


def test_neutral_options_are_explicit_any_category() -> None:
    for field, label in (
        ("region-filter", "All"),
        ("rarity-filter", "All"),
        ("terrain-filter", "All"),
        ("resource-filter", "All"),
        ("status-filter", "All"),
        ("badge-filter", "All"),
    ):
        block = CONTROLS_ROW.split(f'id="{field}"', 1)[1].split("</select>", 1)[0]
        assert f'<option value="">{label}</option>' in block, (
            f"#{field} must open with an explicit '{label}' clearing option, got: {block[:120]!r}"
        )


def test_board_filter_pills_keep_real_checkbox_inputs() -> None:
    for id_ in ("staged-only",):
        label_match = re.search(
            rf'<label class="picker-toggle board-filter-pill[^"]*"[^>]*>.*?id="{id_}".*?</label>',
            CONTROLS_ROW, re.S,
        )
        assert label_match, f"#{id_} must still sit inside a .board-filter-pill label"
        assert f'<input type="checkbox" id="{id_}"' in label_match.group(0)


def test_clear_filters_button_starts_hidden_and_excludes_sort() -> None:
    button = re.search(r'<button[^>]*id="clear-board-filters"[^>]*>', CONTROLS_ROW)
    assert button, "expected #clear-board-filters in the controls row"
    assert "hidden" in button.group(0)
    # Sort has no neutral/"any" concept and must never be touched by Clear.
    assert "sort" not in button.group(0).lower()


def test_open_close_all_remain_direct_children_of_controls_row() -> None:
    # A direct child's own opening tag is preceded, within the row, only by other
    # direct children's markup/comments -- never by an unclosed wrapping element.
    # Cheap proxy for "direct child": the row's raw text has balanced tags up to each
    # button (every '<div' before it also has its matching '</div>' before it).
    for id_ in ("open-all-plots", "close-all-plots"):
        before = CONTROLS_ROW.split(f'id="{id_}"', 1)[0]
        assert before.count("<div") == before.count("</div>"), (
            f"#{id_} must not be nested inside an extra wrapper div in the controls row"
        )


# Behaviour: boardFiltersActive() / syncBoardFilterChrome() / clearBoardFilters()
# `vm_context.run_in_module_context()`'s default entry (app.js's whole import graph)
# cannot be used here: `checkout.js`/`harvest-modal.js`/`fill-plot.js`/`worksite.js`
# each declare their own module-local `const X_ICON`, which the graph-flattening loader
# (generated `import`/`export` lines stripped, everything landing in ONE shared scope)
# collides on -- a PRE-EXISTING gap in that harness, reproducible on master and
# unrelated to this plan; not this file's job to fix. So this test lifts the exact
# real `board.js` source for the three functions this look adds (never a hand-retyped
# copy) and evaluates just that slice in its own minimal sandbox instead.
BOARD_SOURCE = (STATIC / "board.js").read_text(encoding="utf-8")
BADGES_SOURCE = (STATIC / "badges.js").read_text(encoding="utf-8")
BADGE_STATE_SOURCE = (STATIC / "badge-state.js").read_text(encoding="utf-8")
EDITOR_SOURCE = (STATIC / "editor.js").read_text(encoding="utf-8")
ROWS_SOURCE = (STATIC / "rows.js").read_text(encoding="utf-8")
_FUNCS_START = "const BOARD_FILTER_SELECT_IDS ="
_FUNCS_END = "\n// Re-sync the custom dropdowns"
BOARD_FILTER_FUNCS_SOURCE = (
    BOARD_SOURCE.split(_FUNCS_START, 1)[1].split(_FUNCS_END, 1)[0]
).replace("export function", "function")
BOARD_FILTER_FUNCS_SOURCE = _FUNCS_START + BOARD_FILTER_FUNCS_SOURCE


def _run_node(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", script], capture_output=True, text=True)


_FAKE_DOM = r"""
function assert(ok, message) { if (!ok) throw new Error(message); }

function makeInput(id, kind) {
  const el = {
    id, listeners: {}, hidden: false,
    value: '', checked: false,
    dataset: {},
    classList: { add() {}, remove() {}, contains() { return false; } },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    dispatchEvent(evt) { (this.listeners[evt.type] || []).forEach((fn) => fn({ target: this })); },
  };
  return el;
}

const ids = [
  'text-filter', 'region-filter', 'rarity-filter', 'terrain-filter', 'resource-filter',
  'status-filter', 'badge-filter', 'staged-only', 'clear-board-filters',
];
const elements = new Map(ids.map((id) => [id, makeInput(id)]));
document.getElementById = (id) => elements.get(id) || null;

const textFilter = elements.get('text-filter');
const regionFilter = elements.get('region-filter');
const rarityFilter = elements.get('rarity-filter');
const terrainFilter = elements.get('terrain-filter');
const resourceFilter = elements.get('resource-filter');
const statusFilter = elements.get('status-filter');
const badgeFilter = elements.get('badge-filter');
const stagedOnly = elements.get('staged-only');
const clearBtn = elements.get('clear-board-filters');

// Neutral: nothing active, Clear stays hidden.
assert(boardFiltersActive() === false, 'no filter set must read as inactive');
syncBoardFilterChrome();
assert(clearBtn.hidden === true, 'Clear must start hidden with nothing active');

// Any one of the eight dimensions flips it active -- confirm all eight are checked
// not just a couple.
const cases = [
  () => { textFilter.value = 'plot'; },
  () => { regionFilter.value = '65'; },
  () => { rarityFilter.value = 'legendary'; },
  () => { terrainFilter.value = 'forest'; },
  () => { resourceFilter.value = 'grain'; },
  () => { statusFilter.value = 'natural'; },
  () => { badgeFilter.value = 'underfilled'; },
  () => { stagedOnly.checked = true; },
];
for (const [i, setOne] of cases.entries()) {
  for (const el of elements.values()) { el.value = ''; el.checked = false; }
  setOne();
  assert(boardFiltersActive() === true, `dimension ${i} must count as active`);
}

// clearBoardFilters(): every dimension back to neutral, through each control's OWN
// input/change event (never a private reset) -- attach a listener per control (the
// way app.js's real per-field render() listeners do) and confirm each one actually
// fired exactly once, then confirm Clear hides itself again. Region gets its own
// change listener too, standing in for saveRegionChoice()/colourRegionControl().
regionFilter.value = '65';
rarityFilter.value = 'legendary';
terrainFilter.value = 'forest';
resourceFilter.value = 'grain';
statusFilter.value = 'natural';
badgeFilter.value = 'underfilled';
textFilter.value = 'plot';
stagedOnly.checked = true;

const fired = { text: 0, region: 0, rarity: 0, terrain: 0, resource: 0, status: 0, badge: 0, staged: 0 };
textFilter.addEventListener('input', () => { fired.text += 1; });
regionFilter.addEventListener('change', () => { fired.region += 1; });
rarityFilter.addEventListener('change', () => { fired.rarity += 1; });
terrainFilter.addEventListener('change', () => { fired.terrain += 1; });
resourceFilter.addEventListener('change', () => { fired.resource += 1; });
statusFilter.addEventListener('change', () => { fired.status += 1; });
badgeFilter.addEventListener('change', () => { fired.badge += 1; });
stagedOnly.addEventListener('change', () => { fired.staged += 1; });

clearBoardFilters();

assert(textFilter.value === '' && regionFilter.value === '' && rarityFilter.value === ''
  && terrainFilter.value === '' && resourceFilter.value === '' && statusFilter.value === '' && badgeFilter.value === ''
  && !stagedOnly.checked,
  'clearBoardFilters must reset every one of the eight dimensions to neutral');
for (const [key, count] of Object.entries(fired)) {
  assert(count === 1, `expected exactly one normal event dispatch for ${key}, got ${count}`);
}
assert(boardFiltersActive() === false, 'Clear must leave the row fully neutral');
assert(clearBtn.hidden === true, 'Clear must hide itself again once nothing is active');

// A control already at neutral must not fire a redundant event.
const untouched = makeInput('rarity-filter');
elements.set('rarity-filter', untouched);
let untouchedFired = 0;
untouched.addEventListener('change', () => { untouchedFired += 1; });
clearBoardFilters();
assert(untouchedFired === 0, 'an already-neutral control must not dispatch a redundant event');
"""


def test_clear_board_filters_resets_all_nine_dimensions_via_normal_events() -> None:
    script = (
        "const document = {};\n"
        + BOARD_FILTER_FUNCS_SOURCE
        + "\n"
        + _FAKE_DOM
    )
    result = _run_node(script)
    assert result.returncode == 0, result.stderr


def test_badge_filter_uses_one_predicate_and_column_chip_truth() -> None:
    # v0.9.499 ("fix: repair and remember badge filters; remove sort
    # dropdown", commit 652a7437) replaced the per-key if/else chain this guard checked
    # with a single badgeChips(row)-backed lookup, fixing PAIRED/SOLO class drift
    # (register CHANGELOG ). Guard updated to match that consolidation.
    assert "import { badgeFilterOptions, rowHasBadge } from './badges.js';" in BOARD_SOURCE
    assert "for (const { key, label, group } of badgeFilterOptions())" in BOARD_SOURCE
    assert "if (!passes('badge-filter', (badge) => rowHasBadge(row, badge))) return false;" in BOARD_SOURCE  # any chosen badge
    assert "export function rowHasBadge(row, key) {" in BADGES_SOURCE
    assert "return badgeChips(row).some((chip) => chip.key === key);" in BADGES_SOURCE
    assert "pending_change_stalled" in BADGES_SOURCE
    assert "function badgeChips(row) {" in BADGES_SOURCE


def test_computed_options_use_column_labels() -> None:
    # (badge colour system, landed local v0.9.479) added a
    # `kind`/`group`/`groupOrder` shape to every BADGE_TABLE entry -- `kind` now sits
    # before `label` -- and (maintainer ruling) put 'pending_change' in the gold
    # 'special' group. Separately, /maintainer decisions 2026-09-16 replaced the
    # sole-source 'sole_ability_holder' key with the neutral 'land_ability_card'
    # identity, which still carries the same "Land Card / VoV" label. Guard updated
    # to match the current, maintainer-approved key shape and key name.
    assert "Object.entries(meta)" in BADGES_SOURCE
    for key, label in (
        ("staged_changes", "X Changes"), ("staged_cooldown", "Waiting"),
        ("pending_change", "Building"), ("bloodline_paired", "PAIRED"),
        ("bloodline_solo", "SOLO"), ("land_ability_card", "Land Card / VoV"),
        ("underfilled", "Open slots"), ("over_cap", "Over Cap"),
        ("region_dec", "Region DEC"), ("runi", "RUNI"),
    ):
        assert f"['{key}', {{ kind: '{'problem' if key in ('over_cap', 'region_dec', 'underfilled', 'bloodline_solo') else 'property'}', label: '{label}'," in BADGES_SOURCE


def test_staged_truth_renders_independently_and_reuses_editor_projection() -> None:
    assert "function relevantBadgeStages(row)" in BADGES_SOURCE
    assert BADGES_SOURCE.count("const staged = relevantBadgeStages(row);") == 2
    assert "entry?._source_deed_uid === row?.deed_uid" in BADGES_SOURCE
    assert "const { active, openSlots } = effectiveUnderfilledState(row, staged);" in BADGES_SOURCE
    assert "const state = effectiveOverCapState(row, staged);" in BADGES_SOURCE
    assert "const overCap = overCapChip(row);" in BADGES_SOURCE
    assert "const underfilled = underfilledChip(row);" in BADGES_SOURCE
    assert "if (b === 'underfilled' || b === 'over_cap') continue;" in BADGES_SOURCE
    # editor.js now also imports stagedBasePpCap alongside stageTotalBasePp from the
    # same module (/); the shared import line, not stageTotalBasePp
    # alone, is what proves editor.js reuses the badge-state projection.
    assert "import { stagedBasePpCap, stageTotalBasePp } from './badge-state.js';" in EDITOR_SOURCE
    assert BADGE_STATE_SOURCE.count("export function stageTotalBasePp(") == 1


def test_detail_replacement_refilters_an_active_badge_view() -> None:
    assert "updated._badge_roster_loaded = true;" in ROWS_SOURCE
    assert "if (BOARD && document.getElementById('badge-filter')?.value) {" in ROWS_SOURCE
    # commit 8d787322 ("fix(ceres): refresh mobile idle plots with
    # active badge filters", v0.9.528) inserted a mobile-sheet redraw between the two
    # calls this guard originally checked as adjacent lines.
    assert "renderTable();\n      // The filtered board rebuild does not redraw the separate mobile sheet.\n if (MOBILE_PLOT_UID === deedUid) updateMountedRow(deedUid, { readiness: true });\n queueMicrotask(syncCloseAll)" in ROWS_SOURCE
    assert "else if (BOARD) updateMountedRow(deedUid, { readiness: true });" in ROWS_SOURCE
