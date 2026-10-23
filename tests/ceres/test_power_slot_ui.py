"""Regression coverage for the single, two-position Power slot."""

from __future__ import annotations

import copy
import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from .conftest import spawn_live_ceres
from .vm_context import read_entry_source, run_in_module_context


CSS = Path(__file__).parents[2] / "ceres" / "static" / "ceres.css"
# The opened plot mounts at once as a reserved scaffold (`preparing`); its slot groups and
# power slot exist only once the band reaches ready|degraded.
# The board's layout width is the window less its reserved scrollbar gutter (index.html);
# at 1280 px that falls to <=1279, the phone card view, which has no plot table. These
# power-slot guards drive the desktop table, so they run at a plain desktop width.
_DESKTOP = {"width": 1440, "height": 900}
_BAND_READY = '.slot-editor .editor-band:is([data-plot-state="ready"],[data-plot-state="degraded"])'


_ENERGIZED_BADGE_BODY = r"""
const row = {
  deed_uid: 'energized-plot',
  capacity: { potential_card_capacity: 2, runi_staked: false },
  staking_facts: { is_power_core_staked: false, is_energized: true, is_runi_staked: false },
  workers: [
    { slot: 1, asset_ref: 'energized-worker', name: 'Energized Worker', base_pp: 1000, land_abilities: [['ENERGIZED']], sole_holder_for: [], badges: [] },
    { slot: 2, asset_ref: 'ordinary-worker', name: 'Ordinary Worker', base_pp: 900, land_abilities: [], sole_holder_for: [], badges: [] },
  ],
  bonus: {},
};
const group = editorGroups(row).find((item) => item.id === 'worker');
const html = renderEditorGroup(row, group);
// Worker cells now carry the Eos permanent-frame class and reveal attributes.
const starts = [...html.matchAll(/<div class="slot-tile-cell[ "]/g)].map((match) => match.index);
if (starts.length !== 2) process.exit(30);
const first = html.slice(starts[0], starts[1]);
const second = html.slice(starts[1]);
if (!first.includes('slot-energized-satellite')) process.exit(31);
if (second.includes('slot-energized-satellite')) process.exit(32);
const badge = first.slice(first.indexOf('slot-energized-satellite'));
if (badge.includes('data-slot-action')) process.exit(33);
// The approved copy is now a titled tooltip: "Energized" over the sentence below,
// with the Energized ability icon (editor.js renderEnergizedBadge).
if (!badge.includes('data-tip="Energized"')) process.exit(34);
if (!badge.includes('data-tip-body="Powers this plot&#39;s worker slots, like a Power Core."')) process.exit(34);
if (!badge.includes('ability_energized.png')) process.exit(35);
const tileStart = first.indexOf('<div class="slot-tile ');
const badgeStart = first.indexOf('<div class="slot-energized-satellite');
if (tileStart < 0 || badgeStart < 0 || badgeStart <= tileStart) process.exit(36);
let depth = 0;
for (const match of first.slice(tileStart, badgeStart).matchAll(/<\/?div\b[^>]*>/g)) {
  depth += match[0].startsWith('</') ? -1 : 1;
}
if (depth !== 0) process.exit(37);
if (!badge.includes('<span class="power-satellite is-filled" role="img"')) process.exit(38);
"""


def _detail_route(deed_uid: str) -> str:
    """The plot's detail route. Opening a board row whose assets are not loaded asks for
    the full detail with `POST /api/plots/<deed>/expand`, not the plain GET, so the
    stand-in detail answers both (and nothing else under the plot, e.g. `/harvest`)."""
    return f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}{{,/expand}}*"


def _loaded_detail(base_url: str, deed_uid: str) -> dict:
    """The plot's fully loaded detail. The plain GET can answer with the not-yet-loaded
    detail (`assets_loaded: false`), which the page refuses to draw; the page itself then
    asks `POST .../expand`, so the stand-in starts from that same loaded answer."""
    request = urllib.request.Request(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}/expand", method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        detail = json.loads(response.read().decode("utf-8"))
    assert detail.get("assets_loaded") is True, deed_uid
    return detail


def _is_runi(worker: dict) -> bool:
    """The Runi is identified by its card (format.js isRuniWorker), not by slot 0: a plot
    detail's worker rows may carry no slot at all (roster slot unknown)."""
    return (worker.get("runi_number") not in (None, "") or worker.get("card_detail_id") == 505
            or str(worker.get("card_name") or worker.get("name") or "").strip().lower() == "runi")


def _assert_mismatch_red(body_js: str, mutated_source: str) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(body_js, source=mutated_source)


def test_energized_badge_is_card_specific_display_only_and_exact_tooltip() -> None:
    """Guards 1–4: the worker's own `land_abilities` entry selects one sibling
    badge, the badge has no delegation key, and its tooltip is the approved copy.
    The badge is a `.slot-tile-cell` sibling so `.slot-tile` overflow cannot clip it,
    and the inner span exposes its accessible name as an image. Injections: use a
    non-card-specific predicate, add `data-slot-action`, reword the tooltip, or move
    the badge inside the tile; each mutation makes this real whole-module render
    assertion red."""
    run_in_module_context(_ENERGIZED_BADGE_BODY)


def test_energized_badge_is_red_when_the_predicate_stops_reading_the_card() -> None:
    """Guard 1 injection: replacing the card-level `land_abilities` predicate
    with `const energized = true;` badges every worker, so the ordinary worker
    assertion must fail. The name records the actual mutation; this function does
    not claim to inject the plot-level `is_energized` flag, which is not in scope
    for `renderEnergizedBadge(occupant)`."""
    source = read_entry_source()
    mutated = source.replace(
        "const energized = (occupant?.land_abilities || []).some((entry) => Array.isArray(entry) && entry[0] === 'ENERGIZED');",
        "const energized = true;",
        1,
    )
    assert mutated != source
    _assert_mismatch_red(_ENERGIZED_BADGE_BODY, mutated)


def test_energized_badge_inertness_guard_is_red_when_action_is_added() -> None:
    """Guard 2 injection: adding a `data-slot-action` to the display-only power
    core makes the badge eligible for delegated staging, so the no-action assertion
    must fail."""
    source = read_entry_source()
    mutated = source.replace(
        "<span class=\"power-satellite is-filled\" role=\"img\"${tipAttrs({ title: 'Energized'",
        "<span class=\"power-satellite is-filled\" role=\"img\" data-slot-action=\"remove\"${tipAttrs({ title: 'Energized'",
        1,
    )
    assert mutated != source
    _assert_mismatch_red(_ENERGIZED_BADGE_BODY, mutated)


def test_energized_badge_tooltip_guard_is_red_when_copy_changes() -> None:
    """Guard 3 injection: changing the approved tooltip sentence makes the exact
    copy assertion fail."""
    source = read_entry_source()
    mutated = source.replace(
        "body: \"Powers this plot's worker slots, like a Power Core.\"",
        "body: \"Powers this plot.\"",
        1,
    )
    assert mutated != source
    _assert_mismatch_red(_ENERGIZED_BADGE_BODY, mutated)


def test_energized_badge_placement_guard_is_red_when_badge_is_inside_tile() -> None:
    """Guard 4 injection: moving the badge into the tile template leaves a div
    open at the badge index, so the sibling-depth assertion must fail."""
    source = read_entry_source()
    mutated = source.replace(
        '${tile}${energizedBadge}',
        '${tile.slice(0, tile.lastIndexOf(\'</div>\'))}${energizedBadge}</div>',
        1,
    )
    assert mutated != source
    _assert_mismatch_red(_ENERGIZED_BADGE_BODY, mutated)


def _assert_energized_badge_css_guard(source: str) -> None:
    shared_rule = source[source.index('.slot-power-satellite, .slot-energized-satellite {'):]
    shared_rule = shared_rule[:shared_rule.index('}')]
    energized_start = source.index('\n.slot-energized-satellite {') + 1
    energized_rule = source[energized_start:]
    energized_rule = energized_rule[:energized_rule.index('}')]
    assert 'top: calc(var(--power-satellite-size)' in shared_rule
    assert '--power-satellite-size: 2.3rem;' in energized_rule


def test_energized_badge_css_guard_keeps_the_overhang_variable_and_selector() -> None:
    """Guard 5: the Energized satellite gets its own size variable because the
    shared top calculation otherwise becomes invalid, and the shared selector
    includes the Energized satellite."""
    _assert_energized_badge_css_guard(CSS.read_text(encoding='utf-8'))


def test_energized_badge_css_guard_is_red_when_size_variable_is_removed() -> None:
    """Guard 5 injection: deleting the Energized satellite's size declaration
    silently removes the overhang, so the CSS-text guard must fail."""
    source = CSS.read_text(encoding='utf-8')
    mutated = source.replace('--power-satellite-size: 2.3rem; }', '} ', 1)
    assert mutated != source
    with pytest.raises(AssertionError):
        _assert_energized_badge_css_guard(mutated)


_STAGED_ENERGIZED_BADGE_BODY = r"""
const row = {
  deed_uid: 'staged-energized-plot',
  capacity: { potential_card_capacity: 1, runi_staked: false },
  staking_facts: { is_power_core_staked: false, is_energized: false, is_runi_staked: false },
  workers: [],
  bonus: {},
};
const group = editorGroups(row).find((item) => item.id === 'worker');
const candidate = {
  asset_ref: 'staged-energized-worker',
  card_uid: 'staged-energized-card',
  name: 'Staged Energized Worker',
  card_name: 'Staged Energized Worker',
  editions: '2',
  edition: 2,
  land_abilities: [['ENERGIZED']],
  sole_holder_for: [],
  badges: [],
};
const incoming = editorOccupant(candidate, 'worker');
mutateEditorSlot(row, group, 0, incoming);
const stagedGroup = editorGroups(row).find((item) => item.id === 'worker');
const html = renderEditorGroup(row, stagedGroup);
if (!html.includes('slot-energized-satellite')) process.exit(40);
if (!row.workers[0]?.land_abilities?.some((entry) => Array.isArray(entry) && entry[0] === 'ENERGIZED')) process.exit(41);
"""


def test_energized_badge_staged_picker_candidate_renders_from_its_ability_data() -> None:
    """Guard 6: the picker-to-slot occupant projection preserves a candidate's
    `land_abilities`, and the resulting staged worker tile renders the badge."""
    run_in_module_context(_STAGED_ENERGIZED_BADGE_BODY)


def test_energized_badge_staged_candidate_guard_is_red_when_abilities_are_dropped() -> None:
    """Guard 6 injection: replacing the occupant projection's copied
    `land_abilities` with `[]` removes the badge from a staged candidate tile, so
    the rendered assertion must fail."""
    source = read_entry_source()
    mutated = source.replace(
        'land_abilities: asset.land_abilities || [],',
        'land_abilities: [],',
        1,
    )
    assert mutated != source
    _assert_mismatch_red(_STAGED_ENERGIZED_BADGE_BODY, mutated)


def test_power_slot_is_one_fact_driven_surface_with_four_states() -> None:
    """ queue F4: converted from a literal-source substring hunt over a
    hand-sliced `renderPowerSlot` to a real call of the REAL `renderPowerSlot` (plus
    its real `renderPowerSatellite`/`renderEditorTile`/`foilBadgeHtml`/`slotAssetImageHtml`
    dependencies -- no more hand-rolled stand-ins to go stale, which is exactly the class
    of failure census row 30 recorded against this file's sibling node tests) inside the
    shared `vm_context` sandbox (census row 30).

     queue F7 (census row 30, plan §15 ruling 6b): the file's last two
    positional reads dropped. `renderPowerSlot` defined exactly once now reads via
    `typeof renderPowerSlot === 'function'` inside the same `run_in_module_context`
    call below (a real-execution-adjacent existence check, not a source-position
    count -- the module either evaluates to define exactly one top-level
    `renderPowerSlot` or it does not; `vm.runInNewContext` throws
    `SyntaxError: Identifier 'renderPowerSlot' has already been declared` on load if a
    second top-level declaration existed, so a duplicate can never reach this
    assertion silently). The OTHER structural claim the old test made by grepping the
    source -- that `renderSlotEditor` calls `renderPowerSlot` -- is dropped outright,
    per this queue's ruling: a file-shape/"is called by" claim has no rendered
    equivalent and becomes meaningless the moment `renderSlotEditor` and
    `renderPowerSlot` are free to live in different post-split modules (the campaign's
    whole premise); asserting it here would just be a new spelling of the same
    position-coupling this campaign retires. No injection accompanies this drop --
    's injection requirement is for a REPLACEMENT proving continued coverage;
    this is a ruled deletion of a check with no successor, not a replacement.
    """
    script = r"""
if (typeof renderPowerSlot !== 'function') process.exit(9);
function makeGroups(coreOccupant, runiOccupant) {
  return [
    { id: 'power_core', label: 'Power Core', slots: [coreOccupant] },
    { id: 'runi', label: 'Runi', slots: [runiOccupant] },
  ];
}
CERES_STAGE.length = 0;
const row = { deed_uid: 'plot-1' };
const coreOccupant = { name: 'Power Core Card', asset_ref: 'core-1', land_abilities: [], sole_holder_for: [], badges: [] };
const runiOccupant = { name: 'Runi Card', asset_ref: 'runi-1', land_abilities: [], sole_holder_for: [], badges: [] };
const states = [
  { groups: makeGroups(coreOccupant, runiOccupant), expected: 'both' },
  { groups: makeGroups(coreOccupant, null), expected: 'core-only' },
  { groups: makeGroups(null, runiOccupant), expected: 'runi-only' },
  { groups: makeGroups(null, null), expected: 'neither' },
];
for (const item of states) {
  const html = renderPowerSlot(row, item.groups);
  if (!html.includes(`data-power-state="${item.expected}"`)) process.exit(10);
}
// satelliteGroup priority: when Runi is filled, the satellite shows the OTHER slot
// (Power Core). When only a Power Core is filled it is the large tile and carries no
// empty Runi bubble above it (editor.js renderPowerSlot, maintainer ruling).
const bothHtml = renderPowerSlot(row, makeGroups(coreOccupant, runiOccupant));
if (!bothHtml.includes('data-group-id="power_core"')) process.exit(11);
const coreOnlyHtml = renderPowerSlot(row, makeGroups(coreOccupant, null));
if (!coreOnlyHtml.includes('<div class="slot-power-satellite"></div>') || coreOnlyHtml.includes('aria-label="Add Runi"')) process.exit(12);

// row.bonus.runi is never read as a Runi source (mutateEditorSlot's own comment: it
// rides row.workers/row.capacity.runi_staked instead) -- proven behaviourally by
// seeding row.bonus.runi with a value that would leak into the render if it were
// ever read, and confirming editorGroups() ignores it entirely.
const rowPoison = {
  deed_uid: 'plot-2', capacity: { potential_card_capacity: 0, runi_staked: true },
  staking_facts: { is_runi_staked: true },
  // the Runi tile takes only a Runi card (format.js isRuniWorker), so the real Runi is one.
  workers: [{ slot: 0, asset_ref: 'runi-real', name: 'Real Runi', card_detail_id: 505, land_abilities: [], sole_holder_for: [] }],
  bonus: { runi: { name: 'POISON should never render' } },
};
const runiGroup = editorGroups(rowPoison).find((g) => g.id === 'runi');
if (runiGroup.slots[0]?.name !== 'Real Runi') process.exit(13);
"""
    run_in_module_context(script)


def test_power_slot_branching_renders_each_state_and_affordance() -> None:
    """ queue F4: this test was RED (`ReferenceError: foilBadgeHtml is
    not defined`) because its hand-rolled `renderEditorTile`/`slotAssetImageHtml`
    stand-ins went stale the moment `renderPowerSatellite` grew a real call to
    `foilBadgeHtml` -- exactly census §3 Class A, "a fixed debt that is really an
    accruing one." Converted to a real call of the whole module (no hand stand-ins at
    all, so there is nothing left to go stale) inside the shared `vm_context` sandbox
    (census row 30).
    """
    script = r"""
CERES_STAGE.length = 0;
const row = { deed_uid: 'plot-1' };
const core = { id: 'power_core', label: 'Power Core', slots: [{ name: 'Power Core', asset_ref: 'core-1', land_abilities: [], sole_holder_for: [], badges: [] }] };
const runi = { id: 'runi', label: 'Runi', slots: [{ name: 'Runi', asset_ref: 'runi-1', land_abilities: [], sole_holder_for: [], badges: [] }] };
const states = [
  { groups: [core, runi], expected: 'both' },
  { groups: [{ ...core, slots: [core.slots[0]] }, { ...runi, slots: [null] }], expected: 'core-only' },
  { groups: [{ ...core, slots: [null] }, runi], expected: 'runi-only' },
  { groups: [{ ...core, slots: [null] }, { ...runi, slots: [null] }], expected: 'neither' },
];
for (const item of states) {
  const html = renderPowerSlot(row, item.groups);
  if (!html.includes(`data-power-state="${item.expected}"`)) process.exit(10);
}
// A Power Core alone is the large tile with no empty Runi bubble above it.
const coreOnly = renderPowerSlot(row, [{ ...core, slots: [core.slots[0]] }, { ...runi, slots: [null] }]);
if (!coreOnly.includes('<div class="slot-power-satellite"></div>') || coreOnly.includes('aria-label="Add Runi"')
    || !coreOnly.includes('data-group-id="power_core"')) process.exit(11);
const runiOnly = renderPowerSlot(row, [{ ...core, slots: [null] }, runi]);
if (!runiOnly.includes('data-slot-action="add-power-core"') || !runiOnly.includes('aria-label="Add Power Core"')) process.exit(12);
const neither = renderPowerSlot(row, [{ ...core, slots: [null] }, { ...runi, slots: [null] }]);
if (!neither.includes('data-slot-action="add-power-core"') || !neither.includes('data-group-id="power_core"')) process.exit(13);
const both = renderPowerSlot(row, [core, runi]);
if (!both.includes('class="power-satellite is-filled"') || !both.includes('data-slot-action="remove"')
    || !both.includes('data-group-id="power_core"') || !both.includes('aria-label="Remove Power Core"')) process.exit(14);
"""
    run_in_module_context(script)


def test_live_slot_editor_renders_power_after_workers_before_totem_and_title(live_browser) -> None:
    """ (campaign plan §3.1 Batch D, census row 30: "the ordering
    assertion ... needs real DOM traversal (`page.evaluate` walking `.slot-editor`
    children) to avoid becoming its own kind of substring guard"). Replaces the
    old sliced `renderEditorGroup` + hand-built `power`/`bundled` fixtures +
    `html.matchAll(/data-render-order=.../)` regex-over-a-string harness.

    `editorGroups()` (app.js:3499-3538) always returns Totem and Title as
    capacity-1 groups even when unstaked (a null slot, not an absent group),
    and `renderEditorGroup()` (app.js:3940-3949) always pushes divider, then
    power, then every bundled group onto `columns` in that fixed order when
    `bundledGroups.length` -- i.e. on every plot with an actionable worker
    group -- so no special totem/title fixture is needed: any plot with
    `potential_card_capacity > 0` exercises the full order. Reads the REAL
    rendered DOM's class names inside `.slot-group[data-group-id="worker"]
    .slot-grid`, not a regex over an HTML string.

    Injection named, not performed (/): swapping the push order at
    app.js:3944-3945 (`columns.push({kind: 'power', ...})` before
    `columns.push({kind: 'divider', ...})`) would render `power` immediately
    after the last worker tile with no divider between them -- this
    assertion's exact-sequence check (`kinds == [...]`) would catch that as
    a mismatched list rather than silently passing, since it checks the full
    ordered sequence, not just that each kind is present somewhere.
    """
    browser, base_url = live_browser
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    target = next(
        row for row in board["plots"]
        if int((row.get("capacity") or {}).get("potential_card_capacity", 0)) > 0
    )
    deed_uid = target["deed_uid"]
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(base_url)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(_BAND_READY).wait_for(timeout=30_000)
    kinds = page.evaluate(
        """() => [...document.querySelectorAll('.slot-group[data-group-id="worker"] .slot-grid > *')]
            .map((node) => {
                if (node.classList.contains('slot-tile-cell')) return 'worker';
                if (node.classList.contains('slot-grid-divider')) return 'divider';
                // the power slot now sits in its Eos permanent frame wrapper
                if (node.classList.contains('slot-power') || node.querySelector(':scope > .slot-power')) return 'power';
                if (node.classList.contains('asset-slot')) return 'item';
                return 'unknown:' + node.className;
            })"""
    )
    page.close()
    assert kinds, "worker slot-grid rendered no columns"
    assert not any(kind.startswith("unknown:") for kind in kinds), kinds
    worker_count = kinds.count("worker")
    assert worker_count >= 1, kinds
    assert kinds == ["worker"] * worker_count + ["divider", "power", "item", "item"], kinds


def test_editor_groups_keep_worker_numbers_and_power_sources_for_rendering() -> None:
    """ queue F4: converted from hand-sliced `editorGroups`/
    `editorSlotNumber`/`renderPowerSlot` plus hand-stubbed `editorOccupant`/
    `renderPowerSatellite`/`renderEditorTile` to real calls of the whole module
    inside the shared `vm_context` sandbox (census row 30) -- the fixture now goes
    through the REAL `editorOccupant`, so it needs the shape that function actually
    reads (`land_abilities`/`sole_holder_for` included) rather than a hand-picked
    subset of fields the old stub happened to forward.
    """
    script = r"""
CERES_STAGE.length = 0;
const row = {
  deed_uid: 'plot-1',
  capacity: { potential_card_capacity: 3, runi_staked: true },
  staking_facts: { is_power_core_staked: true, is_energized: false, is_runi_staked: true },
  workers: [
    { slot: 1, asset_ref: 'w1', name: 'Worker 1', land_abilities: [], sole_holder_for: [] },
    { slot: 3, asset_ref: 'w3', name: 'Worker 3', land_abilities: [], sole_holder_for: [] },
    { slot: 0, asset_ref: 'runi-1', name: 'Runi', land_abilities: [], sole_holder_for: [] },
  ],
  bonus: {
    power_core: { asset_ref: 'core-1', name: 'Power Core' },
    totem: { asset_ref: 'totem-1', name: 'Totem' },
    title: { asset_ref: 'title-1', name: 'Title' },
  },
};
const groups = editorGroups(row);
if (JSON.stringify(groups.map((group) => group.id)) !== JSON.stringify(['worker', 'power_core', 'runi', 'totem', 'title'])) process.exit(9);
const worker = groups.find((group) => group.id === 'worker');
if (JSON.stringify(worker.slotNumbers) !== JSON.stringify([1, 2, 3])) process.exit(10);
if (JSON.stringify(worker.slots.map((slot, index) => editorSlotNumber(worker, index))) !== JSON.stringify([1, 2, 3])) process.exit(11);
const core = groups.find((group) => group.id === 'power_core');
const runi = groups.find((group) => group.id === 'runi');
if (!core || !runi || !core.slots[0] || !runi.slots[0]) process.exit(12);
if (!core.actionable || !runi.actionable) process.exit(13);
const power = renderPowerSlot(row, groups);
if (!power.includes('data-group-id="runi"') && !power.includes('data-group-id="power_core"')) process.exit(14);
"""
    run_in_module_context(script)


def test_editor_groups_distinguish_live_runi_capacity_from_staged_transitions() -> None:
    script = r"""
CERES_STAGE.length = 0;
const cases = [
  {
    name: 'already Runi-only live four',
    expected: 4,
    row: {
      deed_uid: 'runi-live', workers: [], bonus: { runi: true },
      capacity: { potential_card_capacity: 5, powered_card_capacity: 5, powered_slots: 4, runi_staked: true },
      is_power_core_staked: false, is_energized: false,
    },
  },
  {
    name: 'full five staged down to Runi-only',
    expected: 4,
    row: {
      deed_uid: 'core-removal', workers: [], bonus: { power_core: null, runi: true },
      capacity: { potential_card_capacity: 5, powered_card_capacity: 5, powered_slots: 5, runi_staked: true },
      is_power_core_staked: true, is_energized: false,
    },
  },
  {
    name: 'newly staged Runi without live capacity',
    expected: 4,
    row: {
      deed_uid: 'runi-stake', workers: [], bonus: { runi: false },
      capacity: { potential_card_capacity: 5, powered_card_capacity: 5, powered_slots: 0, runi_staked: true },
      is_power_core_staked: false, is_energized: false,
    },
  },
  {
    name: 'full five Energized staged down to Runi-only',
    expected: 4,
    stage: [{ deed_uid: 'energized-removal', group_id: 'worker', action: 'unstake', slot_index: 0 }],
    row: {
      deed_uid: 'energized-removal', workers: [], bonus: { runi: true },
      capacity: { potential_card_capacity: 5, powered_card_capacity: 5, powered_slots: 5, runi_staked: true },
      is_power_core_staked: false, is_energized: true,
    },
  },
  {
    name: 'staged Core onto live Runi four',
    expected: 5,
    row: {
      deed_uid: 'core-stake', workers: [], bonus: { power_core: { asset_ref: 'core' }, runi: true },
      capacity: { potential_card_capacity: 5, powered_card_capacity: 5, powered_slots: 4, runi_staked: true },
      is_power_core_staked: false, is_energized: false,
    },
  },
];
for (const item of cases) {
  CERES_STAGE.splice(0, CERES_STAGE.length, ...(item.stage || []));
  const group = editorGroups(item.row).find((entry) => entry.id === 'worker');
  if (group.poweredCapacity !== item.expected) {
    console.error(item.name, group.poweredCapacity);
    process.exit(50);
  }
}
"""
    run_in_module_context(script)


def test_power_slot_uses_worker_power_predicate_and_preserves_hazard_path() -> None:
    """ queue F4: converted the `plotPoweredAfterStage` half from a
    literal-source substring hunt to real calls of the actual function (a pure
    predicate over `row`/`CERES_STAGE` -- exactly what the ticket calls "vm_context
    for pure predicates") inside the shared `vm_context` sandbox (census row 30).

    The hazard-path half (`isSoleSource`, and the specific `handleSlotEditorClick`
    wiring that reads the hazard overlay's dataset and picks the Runi- vs Power-Core-
    worded toast) is converted too, but by constructing a minimal fake DOM event
    (`event.target.closest(selector)` returning the one matching element, a fake
    `#slot-hazard-overlay` returned from `document.getElementById`) and calling the
    REAL `handleSlotEditorClick` end to end -- not a live-browser click, since that
    would need staging a real sole-power-source plot through the whole picker/stage
    flow first, but a real call of the real handler function all the same, proving
    `isSoleSource` really gates it and the real message really lands.
    """
    script = r"""
// plotPoweredAfterStage: a pure predicate over row + CERES_STAGE.
CERES_STAGE.length = 0;
if (!plotPoweredAfterStage({ staking_facts: { is_power_core_staked: true, is_energized: false, is_runi_staked: false } })) process.exit(10);
if (!plotPoweredAfterStage({ staking_facts: { is_power_core_staked: false, is_energized: true, is_runi_staked: false } })) process.exit(11);
// A Runi alone powers four worker slots, so it satisfies the plot's power predicate.
if (!plotPoweredAfterStage({ deed_uid: 'plot-power', staking_facts: { is_power_core_staked: false, is_energized: false, is_runi_staked: true } })) process.exit(12);
// A staged Power Core UNSTAKE flips an otherwise-powered plot to unpowered.
CERES_STAGE.push({ deed_uid: 'plot-power', group_id: 'power_core', action: 'unstake' });
if (plotPoweredAfterStage({ deed_uid: 'plot-power', staking_facts: { is_power_core_staked: true, is_energized: false, is_runi_staked: false } })) process.exit(13);
CERES_STAGE.length = 0;
// A staged Power Core STAKE flips an otherwise-unpowered plot to powered.
CERES_STAGE.push({ deed_uid: 'plot-power', group_id: 'power_core', action: 'stake' });
if (!plotPoweredAfterStage({ deed_uid: 'plot-power', staking_facts: { is_power_core_staked: false, is_energized: false, is_runi_staked: false } })) process.exit(14);
CERES_STAGE.length = 0;
CERES_STAGE.push({ deed_uid: 'plot-power', group_id: 'runi', action: 'stake' });
if (!plotPoweredAfterStage({ deed_uid: 'plot-power', staking_facts: { is_power_core_staked: false, is_energized: false, is_runi_staked: false } })) process.exit(141);
CERES_STAGE.length = 0;
CERES_STAGE.push({ deed_uid: 'plot-power', group_id: 'runi', action: 'unstake' });
if (plotPoweredAfterStage({ deed_uid: 'plot-power', staking_facts: { is_power_core_staked: false, is_energized: false, is_runi_staked: true } })) process.exit(142);
CERES_STAGE.length = 0;
CERES_STAGE.push({ deed_uid: 'plot-power', group_id: 'worker', action: 'unstake', slot_index: 0 });
if (plotPoweredAfterStage({ deed_uid: 'plot-power', workers: [], staking_facts: { is_power_core_staked: false, is_energized: true, is_runi_staked: false } })) process.exit(143);
CERES_STAGE.length = 0;
CERES_STAGE.push({ deed_uid: 'plot-power', group_id: 'worker', action: 'stake', slot_index: 0 });
if (!plotPoweredAfterStage({ deed_uid: 'plot-power', workers: [{ land_abilities: [['ENERGIZED', 1]] }], staking_facts: { is_power_core_staked: false, is_energized: false, is_runi_staked: false } })) process.exit(144);
CERES_STAGE.length = 0;

// isSoleSource: the hazard gate, called directly.
if (!isSoleSource({ staking_facts: { is_power_core_staked: true, is_energized: false, is_runi_staked: false } }, 'power_core')) process.exit(15);
if (isSoleSource({ staking_facts: { is_power_core_staked: true, is_energized: true, is_runi_staked: false } }, 'power_core')) process.exit(16); // Energized also covers it -- not sole
if (!isSoleSource({ staking_facts: { is_power_core_staked: false, is_energized: false, is_runi_staked: true } }, 'runi')) process.exit(17);
if (isSoleSource({ staking_facts: { is_power_core_staked: true, is_energized: false, is_runi_staked: true } }, 'runi')) process.exit(18); // Power Core also covers it -- not sole

(async () => {
  // The real hazard-confirm path inside handleSlotEditorClick: isSoleSource gates a
  // sole-Runi unstake, and the toast message is chosen by group.id === 'runi'.
  const rowH = {
    deed_uid: 'plot-hazard', capacity: { potential_card_capacity: 0, runi_staked: true },
    staking_facts: { is_power_core_staked: false, is_energized: false, is_runi_staked: true },
    workers: [{ slot: 0, asset_ref: 'runi-sole', name: 'Sole Runi', card_detail_id: 505, land_abilities: [], sole_holder_for: [] }],
    bonus: {},
  };
  BOARD = { plots: [rowH], player: 'player0005' };
  CURRENT_PLAYER = 'player0005';
  BOARD_GENERATION = 1;
  const overlay = { dataset: { deedUid: 'plot-hazard', groupId: 'runi', slotIndex: '0' }, querySelector() { return null; } };
  document.getElementById = (id) => (id === 'slot-hazard-overlay' ? overlay : null);
  const hazardAction = { dataset: { hazardAction: 'confirm' } };
  const target = { closest(selector) { return selector === '[data-hazard-action]' ? hazardAction : null; } };
  const toastMessages = [];
  stageToast = (message) => { toastMessages.push(message); };
  fetch = (url, opts) => Promise.resolve({
    ok: true, status: 200,
    json: async () => ({
      deed_uid: 'plot-hazard', before_powered: true, after_powered: false,
      removed: [{ asset_ref: 'runi-sole', own_harvest_pp: 0, own_construction_pp: null, own_base_pp: null }],
      plot_residual_delta: 0, plot_ability_delta: 0, harvest_pp_delta: 0,
      own_construction_pp_delta: { status: 'not_applicable', value: null, low: null, high: null, reason: null },
      own_base_pp_delta: { status: 'not_applicable', value: null, low: null, high: null, reason: null },
      notices: [],
    }),
  });
  await handleSlotEditorClick({ target, preventDefault() {}, stopPropagation() {} });
  const entry = CERES_STAGE.find((item) => item.deed_uid === 'plot-hazard' && item.action === 'unstake');
  if (!entry) process.exit(20); // the click really staged the removal via stagePricedRemovals
  if (!toastMessages.includes('Staged: sole Runi unstake.')) process.exit(21); // group.id === 'runi' picked the Runi-worded message
})().catch((error) => { console.error(error); process.exit(1); });
"""
    run_in_module_context(script)


def test_live_power_slot_screenshots_and_core_affordance(live_browser) -> None:
    """Render all states in Chromium and exercise the Runi-only Core affordance."""
    browser, base_url = live_browser
    screen_dir = Path(__file__).parents[2] / "work" / "ceres-power-slot-ui"
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    target = next(row for row in board["plots"] if row.get("capacity", {}).get("runi_staked"))
    deed_uid = target["deed_uid"]
    detail = _loaded_detail(base_url, deed_uid)
    workers = [worker for worker in detail.get("workers", []) if not _is_runi(worker)]
    runi = next(worker for worker in detail.get("workers", []) if _is_runi(worker))
    core = copy.deepcopy(detail.get("bonus", {}).get("power_core")) or {"name": "Power Core", "asset_ref": "core-test"}
    core.setdefault("asset_ref", "core-test")

    def state_detail(name: str) -> dict:
        state = copy.deepcopy(detail)
        state["workers"] = workers + ([copy.deepcopy(runi)] if name in {"both", "runi-only"} else [])
        state["capacity"]["runi_staked"] = name in {"both", "runi-only"}
        state["is_power_core_staked"] = name in {"both", "core-only"}
        state["is_energized"] = False
        state["bonus"] = copy.deepcopy(detail.get("bonus", {}))
        state["bonus"]["power_core"] = copy.deepcopy(core) if name in {"both", "core-only"} else None
        return state

    def open_state(page, name: str) -> None:
        page.route("**/api/board*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(board)))
        state = state_detail(name)
        page.route(
            _detail_route(deed_uid),
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(state)),
        )
        page.goto(base_url)
        page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
        page.locator(_BAND_READY).wait_for(timeout=30_000)
        assert page.locator(".slot-power").get_attribute("data-power-state") == name

    for name in ("both", "core-only", "runi-only", "neither"):
        page = browser.new_page(viewport=_DESKTOP)
        open_state(page, name)
        power_slot = page.locator(".slot-power")
        if name == "core-only":
            assert page.locator(".slot-power-large .slot-name").inner_text() == "Active"
        elif name == "neither":
            assert "Power slot" in power_slot.inner_text()
        if name == "both":
            assert power_slot.locator(".power-satellite.is-filled").get_attribute("aria-label") == "Remove Power Core"
        satellite_image = power_slot.locator(".power-satellite img")
        if satellite_image.count():
            satellite_box = power_slot.locator(".power-satellite").bounding_box()
            image_box = satellite_image.bounding_box()
            assert satellite_box and image_box
            assert abs((image_box["x"] + image_box["width"] / 2) - (satellite_box["x"] + satellite_box["width"] / 2)) <= 0.1
            assert abs((image_box["y"] + image_box["height"] / 2) - (satellite_box["y"] + satellite_box["height"] / 2)) <= 0.1
        large_image = power_slot.locator(".slot-power-large .slot-art img")
        if large_image.count():
            large_box = power_slot.locator(".slot-power-large .slot-tile").first.bounding_box()
            image_box = large_image.bounding_box()
            assert large_box and image_box
            assert abs((image_box["x"] + image_box["width"] / 2) - (large_box["x"] + large_box["width"] / 2)) <= 0.1
            assert abs((image_box["y"] + image_box["height"] / 2) - (large_box["y"] + large_box["height"] / 2)) <= 0.1
        page.locator(".slot-editor").screenshot(path=str(screen_dir / f"power-slot-{name}.png"))
        page.close()

    page = browser.new_page(viewport=_DESKTOP)
    candidates = {
        "candidates": [{"asset_ref": "core-test-new", "name": "Power Core", "plot_impact": {"status": "point", "value": 0}, "construction_pp_delta": {"status": "not_applicable"}}],
        "total": 1,
    }
    page.route("**/api/board*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(board)))
    state = state_detail("runi-only")
    page.route(_detail_route(deed_uid), lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(state)))
    page.route("**/api/plots/*/candidates*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(candidates)))
    page.goto(base_url)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(_BAND_READY).wait_for(timeout=30_000)
    with page.expect_request("**/api/plots/*/candidates*") as candidate_request:
        page.locator(".power-satellite[data-slot-action='add-power-core']").click()
    assert "group_id=power_core" in candidate_request.value.url
    page.wait_for_function("document.querySelector('.slot-power')?.dataset.powerState === 'both'", timeout=30_000)
    assert page.locator(".slot-power-large .slot-name").inner_text() == "Runi"
    page.close()

    page = browser.new_page(viewport=_DESKTOP)
    page.route("**/api/board*", lambda route, request, payload=board: route.fulfill(status=200, content_type="application/json", body=json.dumps(payload)))
    page.route(_detail_route(deed_uid), lambda route, request, payload=state_detail("runi-only"): route.fulfill(status=200, content_type="application/json", body=json.dumps(payload)))
    page.route("**/api/plots/*/candidates*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(candidates)))
    page.goto(base_url)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(_BAND_READY).wait_for(timeout=30_000)
    satellite = page.locator(".power-satellite.is-empty")
    with page.expect_request("**/api/plots/*/candidates*"):
        satellite.focus()
        satellite.press("Enter")
    page.close()

    page = browser.new_page(viewport=_DESKTOP)
    page.route("**/api/board*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(board)))
    state = state_detail("both")
    page.route(_detail_route(deed_uid), lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(state)))
    impact = {"deed_uid": deed_uid, "before_powered": True, "after_powered": True, "removed": [{"asset_ref": runi.get("asset_ref"), "own_harvest_pp": 0, "own_construction_pp": 1500, "own_base_pp": 1500}], "plot_residual_delta": 0, "plot_ability_delta": 0, "harvest_pp_delta": 0, "own_construction_pp_delta": {"status": "point", "value": -1500, "low": -1500, "high": -1500}, "own_base_pp_delta": {"status": "point", "value": -1500, "low": -1500, "high": -1500}, "notices": []}
    page.route("**/api/plots/*/removal-impact", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(impact)))
    page.goto(base_url)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(_BAND_READY).wait_for(timeout=30_000)
    page.locator(".slot-power-large .slot-tile").hover()
    page.locator(".slot-power-large .slot-remove").click()
    page.wait_for_function("document.querySelector('.slot-power')?.dataset.powerState === 'core-only'", timeout=30_000)
    assert page.locator(".slot-power-large .slot-tile-item").count() == 1
    page.close()


def test_live_power_satellite_removal_uses_priced_removal_path(live_browser) -> None:
    browser, base_url = live_browser
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    target = next(row for row in board["plots"] if row.get("capacity", {}).get("runi_staked"))
    deed_uid = target["deed_uid"]
    detail = _loaded_detail(base_url, deed_uid)
    workers = [worker for worker in detail.get("workers", []) if not _is_runi(worker)]
    runi = next(worker for worker in detail.get("workers", []) if _is_runi(worker))
    core = copy.deepcopy(detail.get("bonus", {}).get("power_core")) or {"name": "Power Core", "asset_ref": "core-test"}
    core.setdefault("asset_ref", "core-test")
    state = copy.deepcopy(detail)
    state["workers"] = workers + [copy.deepcopy(runi)]
    state["capacity"]["runi_staked"] = True
    state["is_power_core_staked"] = True
    state["is_energized"] = False
    state["bonus"] = copy.deepcopy(detail.get("bonus", {}))
    state["bonus"]["power_core"] = copy.deepcopy(core)
    impact = {"deed_uid": deed_uid, "before_powered": True, "after_powered": False, "removed": [{"asset_ref": core.get("asset_ref"), "own_harvest_pp": 0, "own_construction_pp": None, "own_base_pp": None}], "plot_residual_delta": 0, "plot_ability_delta": 0, "harvest_pp_delta": 0, "own_construction_pp_delta": {"status": "not_applicable", "value": None, "low": None, "high": None}, "own_base_pp_delta": {"status": "not_applicable", "value": None, "low": None, "high": None}, "notices": []}
    page = browser.new_page(viewport=_DESKTOP)
    page.route("**/api/board*", lambda route, request, payload=board: route.fulfill(status=200, content_type="application/json", body=json.dumps(payload)))
    def plot_route(route, request):
        payload = impact if "/removal-impact" in request.url.split("?", 1)[0] else state
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/plots/**", plot_route)
    page.goto(base_url)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(_BAND_READY).wait_for(timeout=30_000)
    satellite = page.locator(".power-satellite.is-filled")
    assert satellite.evaluate("(element) => element.tagName") == "BUTTON"
    with page.expect_request(lambda request: "/removal-impact" in request.url):
        satellite.click()
    page.wait_for_function("document.querySelector('.slot-power')?.dataset.powerState === 'runi-only'", timeout=30_000)
    assert page.locator(".slot-power .power-satellite.is-empty").get_attribute("aria-label") == "Add Power Core"
    page.close()

    page = browser.new_page(viewport=_DESKTOP)
    page.route("**/api/board*", lambda route, request, payload=board: route.fulfill(status=200, content_type="application/json", body=json.dumps(payload)))
    page.route(_detail_route(deed_uid), lambda route, request, payload=state: route.fulfill(status=200, content_type="application/json", body=json.dumps(payload)))
    page.route("**/api/plots/*/removal-impact", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(impact)))
    page.goto(base_url)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(_BAND_READY).wait_for(timeout=30_000)
    satellite = page.locator(".power-satellite.is-filled")
    with page.expect_request(lambda request: "/removal-impact" in request.url):
        satellite.focus()
        satellite.press("Enter")
    page.close()


def test_live_badge_colours_follow_caution_severity_in_all_themes(live_browser) -> None:
    browser, base_url = live_browser
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    badge_board = copy.deepcopy(board)
    badge_row = badge_board["plots"][0]
    badge_row["badges"] = ["unpowered_worker", "redundant_power_core", "dark_discount_wasted"]
    badge_row["notices"] = []
    for theme in ("alchemical", "dark", "light", "wood"):
        page = browser.new_page(viewport=_DESKTOP)
        page.add_init_script(f"localStorage.setItem('ceres:theme', '{theme}')")
        page.route("**/api/board*", lambda route, request, payload=badge_board: route.fulfill(status=200, content_type="application/json", body=json.dumps(payload)))
        page.goto(base_url)
        row = page.locator(f"tr.plot-row[data-deed-uid='{badge_row['deed_uid']}']")
        row.wait_for(timeout=30_000)
        # Badge chips are built only in a row's slide-out, opened from its rail handle.
        page.locator(f"#badge-bar .badge-bar-row[data-deed-uid='{badge_row['deed_uid']}']").click()
        row = page.locator(f".badge-overlay-row[data-deed-uid='{badge_row['deed_uid']}']")
        row.locator(".badge").first.wait_for(timeout=30_000)
        # All three are caution-severity problems; the glass badge face colours each by its
        # served state group (badge-colours.css): critical -> --danger, warning -> --warn.
        # Unpowered worker is a critical-group problem (it earns zero), the other two warnings.
        tokens = {"critical": "--danger", "warning": "--warn"}
        for code in ("unpowered_worker", "redundant_power_core", "dark_discount_wasted"):
            meta = board["badge_meta"][code]
            assert meta["severity"] == "caution", (code, meta)
            expected = page.evaluate("""token => {
              const probe = document.createElement('span');
              probe.style.color = `var(${token})`;
              document.body.appendChild(probe);
              const colour = getComputedStyle(probe).color;
              probe.remove();
              return colour;
            }""", tokens[meta["state_group"]])
            badge = row.locator(f".badge-{code}")
            styles = badge.evaluate("(element) => { const style = getComputedStyle(element); return {color: style.color, border: style.borderColor, borderStyle: style.borderStyle}; }")
            assert styles["color"] == expected, (code, theme)
            assert styles["border"] == expected, (code, theme)
            assert styles["borderStyle"] == "solid"
        row.screenshot(path=str(Path(__file__).parents[2] / "work" / f"badges-after-{theme}.png"))
        page.close()


def test_live_power_slot_screenshots_cover_all_themes(live_browser) -> None:
    browser, base_url = live_browser
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    target = next(row for row in board["plots"] if row.get("capacity", {}).get("runi_staked"))
    deed_uid = target["deed_uid"]
    detail = _loaded_detail(base_url, deed_uid)
    workers = [worker for worker in detail.get("workers", []) if not _is_runi(worker)]
    runi = next(worker for worker in detail.get("workers", []) if _is_runi(worker))
    core = copy.deepcopy(detail.get("bonus", {}).get("power_core")) or {"name": "Power Core", "asset_ref": "core-test"}
    state = copy.deepcopy(detail)
    state["workers"] = workers + [copy.deepcopy(runi)]
    state["capacity"]["runi_staked"] = True
    state["is_power_core_staked"] = True
    state["is_energized"] = False
    state["bonus"] = copy.deepcopy(detail.get("bonus", {}))
    state["bonus"]["power_core"] = copy.deepcopy(core)
    for theme in ("alchemical", "dark", "light", "wood"):
        page = browser.new_page(viewport=_DESKTOP)
        page.add_init_script(f"localStorage.setItem('ceres:theme', '{theme}')")
        page.route("**/api/board*", lambda route, request, payload=board: route.fulfill(status=200, content_type="application/json", body=json.dumps(payload)))
        page.route(_detail_route(deed_uid), lambda route, request, payload=state: route.fulfill(status=200, content_type="application/json", body=json.dumps(payload)))
        page.goto(base_url)
        page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
        page.locator(_BAND_READY).wait_for(timeout=30_000)
        page.locator(".slot-power").screenshot(path=str(Path(__file__).parents[2] / "work" / f"power-slot-after-{theme}.png"))
        page.close()


@pytest.fixture(scope="module")
def live_browser():
    with spawn_live_ceres() as value:
        yield value
