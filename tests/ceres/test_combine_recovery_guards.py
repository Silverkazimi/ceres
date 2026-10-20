"""Recovery of three combine-reachability gaps recorded in
work/combine-reachability/spec.md's "Queued recovery work" (added 2026-08-30):

1. `groupHasCombinableRow` had its only standalone test deleted with
 `test_combine_maxed_rule.py` (commit `2a717567`, the Idle Cards removal). It
 still runs indirectly through the door-level fixtures in
 test_combine_reachability.py, but nothing pins its own behaviour directly, and
 it gates whether a Combine affordance appears at all across all three doors.
2. `bcx_max: null` must mean UNKNOWN, never maxed -- `isCardMaxed` handles this
 correctly today but nothing guards it, and a regression here would silently
 hide Combine for exactly the cards whose printing ceiling could not be
 resolved (the same class of card `b9432732` found mis-keyed).
3. The worker-table door (`plotCombineCandidates` -> the click handler's
 'combine-plot' branch) has never been exercised end to end -- no live plot
 currently has two or more qualifying workers, so this builds a synthetic one
 and drives the real click handler.

 (campaign plan `ceres-modularisation-campaign.md` rev 3 Batch D,
census row 15, method `ceres-ir1854-retire-app-js-slice-harness.md`). The prior
version of this file sliced `combineGroupKey`..the ledger comment out of
`ceres/static/app.js` with hand-rolled string markers and ran the slice in a bare
`node -e` -- fragile in exactly the way retires it: the worker-table
rebuild (commit `9dc5c91a`) hoisted `cardFoilValue`/`isNonCombinableFoil` earlier
in the file and every slice went `ReferenceError` (an earlier 
queue patched this in place with a `_foil_helpers()` stub, noted here only for the
record -- that stub is now GONE, superseded by the fix below, not merely
retained).

Injection: this file now loads the REAL, WHOLE `ceres/static/app.js` module
through `tests/ceres/vm_context.py`'s `vm.runInNewContext` sandbox
(`run_in_module_context`) instead of slicing a byte range. Every function called
below -- `groupHasCombinableRow`, `isCardMaxed`, `handleSlotEditorClick`,
`combineGroupKey`, `editorGroups`, and every helper they call transitively
(`cardFoilValue`, `isNonCombinableFoil`, `editorOccupant`, ...) -- is the real,
unmodified function; nothing here is hand-stubbed. This is immune to any future
function move *within* app.js (no string markers to go stale); it breaks only if
the module fails to evaluate at all (a real syntax/reference error) or the split
itself moves these functions to another entry file (vm_context.py's own `ENTRY`
flips at that point, per its docstring). Each guard below still has its own
defect injected via a source-text replace, run through the SAME sandbox so the
mutation is exercised against the real module, not a hand-built substitute.

Finding, not fixed here ( -- a test migration does not patch product code):
the worker-table door's 'combine-plot' click branch (`handleSlotEditorClick`,
app.js ~6689) has NO rendering call site left in the current source --
`renderEditorGroup`'s own comment (app.js ~3922-3925) says "The plot-level
Combine control is REMOVED unconditionally... The per-tile chip is the single
combine affordance." No button anywhere in app.js carries
`data-slot-action="combine-plot"`; the handler branch is live but unreachable
from a real click. `test_combine_reachability.py`'s door-1 tests were converted
to read this directly (see that file for the fuller finding); the two tests
below keep exercising the handler itself via a synthetic click event -- there is
no real element to click -- documented here so the gap is visible rather than
implied by a passing test.
"""

from __future__ import annotations

import subprocess

import pytest

from .vm_context import read_entry_source, run_in_module_context


def _run(body_js: str, *, source: str | None = None) -> None:
    run_in_module_context(body_js, source=source)


def _assert_mismatch_red(body_js: str, mutated_source: str) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        _run(body_js, source=mutated_source)


# 1. groupHasCombinableRow -- standalone guard

_GROUP_HAS_COMBINABLE_ROW_BODY = r"""
Two copies, both maxed -- neither row would show a Combine button, so the
// group must not count as combinable.
const bothMaxed = [
 {uid: 'A1', bcx: 10, bcx_max: 10, available: true},
 {uid: 'A2', bcx: 10, bcx_max: 10, available: true},
];
if (groupHasCombinableRow(bothMaxed) !== false) process.exit(50);

// One maxed, one not -- the not-maxed row has an eligible partner (the maxed
// one is still a valid PARTNER even though it can't itself be a combine
// target), so the group counts.
const oneMaxedOneNot = [
 {uid: 'B1', bcx: 10, bcx_max: 10, available: true},
 {uid: 'B2', bcx: 1, bcx_max: 10, available: true},
];
if (groupHasCombinableRow(oneMaxedOneNot) !== true) process.exit(51);

// A single copy with no partner at all -- not combinable.
const lone = [{uid: 'C1', bcx: 1, bcx_max: 10, available: true}];
if (groupHasCombinableRow(lone) !== false) process.exit(52);

// Empty/undefined input must not throw.
if (groupHasCombinableRow([]) !== false) process.exit(53);
if (groupHasCombinableRow(undefined) !== false) process.exit(54);
"""


def test_group_has_combinable_row_via_real_function() -> None:
    """Calls the REAL groupHasCombinableRow (whole module, vm_context) against a
 both-maxed group (false), a mixed maxed/unmaxed group (true), a lone copy
 (false) and empty/undefined input (false, no throw)."""
    _run(_GROUP_HAS_COMBINABLE_ROW_BODY)


def test_group_has_combinable_row_mutation_is_red() -> None:
    """Injected defect: groupHasCombinableRow() uses .every instead of .some, as if
 a group only counted when EVERY copy in it could individually combine. Calling
 the REAL function on the mixed maxed/unmaxed group (one row genuinely
 combinable, one maxed) now wrongly returns false -- exactly the case that must
 still show a Combine button (rowCanCombine is true for the unmaxed row)."""
    source = read_entry_source()
    mutated = source.replace(
        "function groupHasCombinableRow(copies) {\n return (copies || []).some((c) => rowCanCombine(c, copies));\n}",
        "function groupHasCombinableRow(copies) {\n return (copies || []).every((c) => rowCanCombine(c, copies));\n}",
        1,
    )
    assert mutated != source, "expected source text not found -- app.js changed shape"
    _assert_mismatch_red(_GROUP_HAS_COMBINABLE_ROW_BODY, mutated)


# 2. isCardMaxed -- bcx_max: null means UNKNOWN, never maxed

_IS_CARD_MAXED_BODY = r"""
bcx_max unresolved (null) -- must read as NOT maxed, i.e. unknown, per
// the internal rules's own worked case: the printing's regime couldn't be keyed, so
// treating it as maxed would silently hide Combine for exactly these cards.
if (isCardMaxed({uid: 'X', bcx: 5, bcx_max: null}) !== false) process.exit(60);
if (isCardMaxed({uid: 'X', bcx: 999, bcx_max: null}) !== false) process.exit(61); // even a huge bcx can't prove maxed without a ceiling

// bcx unresolved (null) -- also unknown, not maxed.
if (isCardMaxed({uid: 'X', bcx: null, bcx_max: 10}) !== false) process.exit(62);

// Both known and at/over the ceiling -- genuinely maxed.
if (isCardMaxed({uid: 'X', bcx: 10, bcx_max: 10}) !== true) process.exit(63);
if (isCardMaxed({uid: 'X', bcx: 12, bcx_max: 10}) !== true) process.exit(64);

// Both known, below the ceiling -- not maxed.
if (isCardMaxed({uid: 'X', bcx: 1, bcx_max: 10}) !== false) process.exit(65);
"""


def test_is_card_maxed_treats_null_ceiling_as_unknown_via_real_function() -> None:
    """Calls the REAL isCardMaxed (whole module, vm_context) against a null
 bcx_max (must read false, however large bcx is), a null bcx, and both-known
 above/at/below-ceiling cases."""
    _run(_IS_CARD_MAXED_BODY)


def test_is_card_maxed_null_ceiling_mutation_is_red() -> None:
    """Injected defect: isCardMaxed() drops the bcx_max null guard, so an
 unresolved ceiling (bcx_max: null) reads as maxed whenever bcx is truthy --
 calling the REAL function on {bcx: 5, bcx_max: null} now wrongly returns
 true, which is exactly the silent-Combine-vanishes failure the internal rules warns
 about for the 4.1% of cards the regime keying got wrong (b9432732)."""
    source = read_entry_source()
    mutated = source.replace(
        "function isCardMaxed(c) {\n return c.bcx_max != null && c.bcx != null && c.bcx >= c.bcx_max;\n}",
        "function isCardMaxed(c) {\n return c.bcx_max == null || (c.bcx != null && c.bcx >= c.bcx_max);\n}",
        1,
    )
    assert mutated != source, "expected source text not found -- app.js changed shape"
    _assert_mismatch_red(_IS_CARD_MAXED_BODY, mutated)


# 3. The worker-table door (plotCombineCandidates + the 'combine-plot' click
# branch) exercised end to end against a synthetic plot with two qualifying
# workers -- this door has never been clicked in verification because no live
# plot currently has more than one worker with a combinable idle duplicate, AND
# (finding, see module docstring) no button in the current source ever renders
# with this dataset -- a synthetic event is the only way to reach this branch
# at all today.

_WORKER_TABLE_DOOR_BODY = r"""
BOARD = { plots: [{
 deed_uid: 'plot-1',
 capacity: { potential_card_capacity: 5 },
 workers: [
 { slot: 1, asset_ref: 'a1', uid: 'a1', card_detail_id: 100, edition: 1, foil: false, name: 'Card A' },
 { slot: 2, asset_ref: 'b1', uid: 'b1', card_detail_id: 200, edition: 1, foil: false, name: 'Card B' },
 ],
 staking_facts: {},
 bonus: {},
}] };

const openCombineGroupDialogCalls = [];
openCombineGroupDialog = (key) => { openCombineGroupDialogCalls.push(key); };
const openPlotCombineChooserCalls = [];
openPlotCombineChooser = (row, candidates) => { openPlotCombineChooserCalls.push({ row, candidates }); };

function fakeEvent(dataset) {
 return {
 target: { closest: (selector) => (selector === '[data-slot-action]' ? { dataset } : null) },
 preventDefault: () => {},
 stopPropagation: () => {},
 };
}

(async () => {
 // Case A: exactly ONE qualifying worker on the plot -- click must open
 // the real dialog directly, no intermediate chooser.
 S.IDLE_CARDS_CACHE = [
 { uid: 'A1', card_detail_id: 100, edition: 1, foil: false, bcx: 1, bcx_max: 10, available: true, name: 'Card A' },
 { uid: 'A2', card_detail_id: 100, edition: 1, foil: false, bcx: 1, bcx_max: 10, available: true, name: 'Card A' },
 ];
 await handleSlotEditorClick(fakeEvent({ slotAction: 'combine-plot', deedUid: 'plot-1', groupId: 'worker' }));
 if (openCombineGroupDialogCalls.length !== 1) process.exit(70);
 if (openCombineGroupDialogCalls[0] !== combineGroupKey({ card_detail_id: 100, edition: 1, foil: false })) process.exit(71);
 if (openPlotCombineChooserCalls.length !== 0) process.exit(72);

 // Case B: TWO qualifying workers -- click must open the chooser, not the
 // dialog directly.
 openCombineGroupDialogCalls.length = 0;
 openPlotCombineChooserCalls.length = 0;
 S.IDLE_CARDS_CACHE = S.IDLE_CARDS_CACHE.concat([
 { uid: 'B1', card_detail_id: 200, edition: 1, foil: false, bcx: 1, bcx_max: 10, available: true, name: 'Card B' },
 { uid: 'B2', card_detail_id: 200, edition: 1, foil: false, bcx: 1, bcx_max: 10, available: true, name: 'Card B' },
 ]);
 await handleSlotEditorClick(fakeEvent({ slotAction: 'combine-plot', deedUid: 'plot-1', groupId: 'worker' }));
 if (openCombineGroupDialogCalls.length !== 0) process.exit(73);
 if (openPlotCombineChooserCalls.length !== 1) process.exit(74);
 if (openPlotCombineChooserCalls[0].candidates.length !== 2) process.exit(75);

 // Case C: the idle-card cache was cleared after this button rendered
 // the stale click must be a no-op because there are no current candidates.
 openCombineGroupDialogCalls.length = 0;
 openPlotCombineChooserCalls.length = 0;
 S.IDLE_CARDS_CACHE = null;
 await handleSlotEditorClick(fakeEvent({ slotAction: 'combine-plot', deedUid: 'plot-1', groupId: 'worker' }));
 if (openCombineGroupDialogCalls.length !== 0) process.exit(76);
 if (openPlotCombineChooserCalls.length !== 0) process.exit(77);
})().catch((error) => { console.error(error); process.exit(1); });
"""


def test_worker_table_door_click_opens_real_dialog() -> None:
    """Drives the REAL handleSlotEditorClick (whole module, vm_context) against a
 synthetic plot with two workers, each carrying a combinable idle duplicate --
 the worker-table door (plotCombineCandidates -> the 'combine-plot' branch)
 that has never been clicked in live verification because no real plot
 currently qualifies, AND (see module docstring) has no rendering call site
 left in the current source. Asserts a single qualifying worker opens the
 shared combine dialog directly (by its real combineGroupKey), two qualifying
 workers open the plot chooser instead, and a stale click after the current
 idle-card cache is cleared is a no-op."""
    _run(_WORKER_TABLE_DOOR_BODY)


def test_worker_table_door_single_candidate_mutation_is_red() -> None:
    """Injected defect: the click handler's off-by-one -- `candidates.length === 1`
 becomes `candidates.length === 0` -- so a plot with exactly one qualifying
 worker no longer opens the dialog directly; it falls through to the
 multi-worker chooser instead, showing a pointless one-item picker in front of
 the dialog the player should have landed on immediately."""
    source = read_entry_source()
    mutated = source.replace(
        "    if (candidates.length === 1) {\n IDLE_COMBINE_GROUPS.set(candidates[0].key, candidates[0].copies);\n openCombineGroupDialog(candidates[0].key);\n } else {\n openPlotCombineChooser(row, candidates);\n }",
        "    if (candidates.length === 0) {\n IDLE_COMBINE_GROUPS.set(candidates[0].key, candidates[0].copies);\n openCombineGroupDialog(candidates[0].key);\n } else {\n openPlotCombineChooser(row, candidates);\n }",
        1,
    )
    assert mutated != source, "expected source text not found -- app.js changed shape"
    _assert_mismatch_red(_WORKER_TABLE_DOOR_BODY, mutated)
