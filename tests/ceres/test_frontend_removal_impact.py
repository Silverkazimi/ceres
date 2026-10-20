from __future__ import annotations

import json
from types import SimpleNamespace

from ceres import routes_candidates
from ceres.candidates import CandidateRow, serialize_candidate
from praetoria.land_reallocate import MovableCard
from .vm_context import ENTRY, run_in_module_context


def test_candidate_serialization_preserves_cross_deed_donor_identity():
    card = MovableCard("Donor", "Red", 1000, 1000, "staked", "source-deed", asset_ref="donor")
    row = CandidateRow(card, {}, {}, "staked", "STK-LND-WKR", {"ready": True})

    assert serialize_candidate(row)["source_deed_uid"] == "source-deed"


def test_historical_idle_candidate_does_not_serialize_stale_donor_identity():
    card = MovableCard(
        "Historical idle", "Red", 1000, 0, "idle", "stale-prior-deed",
        bucket="stake_expired_or_historical", asset_ref="historical-idle",
    )
    row = CandidateRow(card, {}, {}, "stake_expired_or_historical", "STK-LND-WKR", {"ready": True})

    assert serialize_candidate(row)["source_deed_uid"] is None


def test_all_bare_removal_paths_use_one_server_batch_pricer():
    """ queue F4: converted from a literal-source substring hunt (an
 absence check on the dead `-Number(occupant.current_harvest_pp || 0)` pattern, plus
 four presence checks for `stagePricedRemovals`/`applyRemovalImpactToStage` call
 sites -- none of it exercising real behaviour) to two real calls of the actual
 `stagePricedRemovals`/`removeCheckoutStage` functions inside the shared
 `vm_context` sandbox (census row 22; plan §15 ruling 6b's whole-module `vm` route,
 since neither function has a DOM surface beyond the state it mutates).

 Scenario A proves the "one server batch pricer" claim directly: staging a
 two-worker removal in one call produces exactly ONE `fetch` call carrying both
 asset_refs in a single JSON body, and the staged deltas come from the SERVER's
 `own_harvest_pp` (42, 13) -- never a client-side negation of the stale
 `occupant.current_harvest_pp` (999, 500), which is the exact bug this test used to
 guard against by string. Scenario B proves the OTHER call site the old test named,
 `removeCheckoutStage`'s `applyRemovalImpactToStage(deedUid, remainingImpact)`:
 undoing one of two staged removals reprices the remaining one through the same
 server path.
    """
    script = r"""
Scenario A: stagePricedRemovals is the one server batch pricer.
const rowA = {
 deed_uid: 'plot-a',
 capacity: { potential_card_capacity: 2, runi_staked: false },
 staking_facts: {},
 workers: [
 { slot: 1, asset_ref: 'worker-1', name: 'Worker One', current_harvest_pp: 999, land_abilities: [], sole_holder_for: [] },
 { slot: 2, asset_ref: 'worker-2', name: 'Worker Two', current_harvest_pp: 500, land_abilities: [], sole_holder_for: [] },
 ],
 bonus: {},
};
BOARD = { plots: [rowA], player: 'player0005' };
CURRENT_PLAYER = 'player0005';
BOARD_GENERATION = 1;
const fetchCallsA = [];
fetch = (url, opts) => {
 fetchCallsA.push({ url: String(url), body: opts.body });
 return Promise.resolve({
 ok: true,
 status: 200,
 json: async () => ({
 deed_uid: 'plot-a', before_powered: true, after_powered: true,
 removed: [
 { asset_ref: 'worker-1', own_harvest_pp: 42, own_construction_pp: 7, own_base_pp: 7 },
 { asset_ref: 'worker-2', own_harvest_pp: 13, own_construction_pp: 2, own_base_pp: 2 },
 ],
 plot_residual_delta: -3, plot_ability_delta: -3, harvest_pp_delta: -58,
 own_construction_pp_delta: { status: 'point', value: -9, low: -9, high: -9, reason: null },
 own_base_pp_delta: { status: 'point', value: -9, low: -9, high: -9, reason: null },
 notices: [],
 }),
 });
};
(async () => {
 const groupA = editorGroups(rowA).find((g) => g.id === 'worker');
 const occupant1 = groupA.slots[0];
 const occupant2 = groupA.slots[1];
 const ok = await stagePricedRemovals(
 rowA,
 [{ group: groupA, index: 0, occupant: occupant1 }, { group: groupA, index: 1, occupant: occupant2 }],
 'Staged clear for the whole plot.',
 );
 if (!ok) process.exit(10);
 if (fetchCallsA.length !== 1) process.exit(11); // ONE server batch call, not one per removed asset
 if (fetchCallsA[0].body !== JSON.stringify({ asset_refs: ['worker-1', 'worker-2'] })) process.exit(12);
 const entry1 = CERES_STAGE.find((item) => item.asset_ref === 'worker-1');
 const entry2 = CERES_STAGE.find((item) => item.asset_ref === 'worker-2');
 if (!entry1 || !entry2) process.exit(13);
 // Deltas come from the SERVER's own_harvest_pp (42, 13), plus the shared
 // plot_residual_delta landing on the first priced entry only. The pre-fix bug
 // negated the client-side occupant.current_harvest_pp instead (999, 500)
 // distinct sentinel values, so a regression to that pattern fails these checks.
 if (entry1.delta !== -45) process.exit(14);
 if (entry2.delta !== -13) process.exit(15);
 if (rowA.workers.length !== 0) process.exit(16); // mutateEditorSlot(currentRow, spec.group, spec.index, null) really ran for both specs

 // Scenario B: removeCheckoutStage's applyRemovalImpactToStage(deedUid, remainingImpact)
 // reprices whatever removal remains once one staged removal is undone.
 const rowB = {
 deed_uid: 'plot-b',
 capacity: { potential_card_capacity: 2, runi_staked: false },
 staking_facts: {},
 workers: [
 { slot: 1, asset_ref: 'b-worker-1', name: 'B One', current_harvest_pp: 10, land_abilities: [], sole_holder_for: [] },
 { slot: 2, asset_ref: 'b-worker-2', name: 'B Two', current_harvest_pp: 20, land_abilities: [], sole_holder_for: [] },
 ],
 bonus: {},
 };
 BOARD = { plots: [rowB], player: 'player0005' };
 BOARD_GENERATION = 2;
 // Stage two unstake entries directly (bypassing the network) to reach the state
 // removeCheckoutStage(entry) operates on: at least one OTHER staged removal remains
 // after this one is undone, so it must reprice via applyRemovalImpactToStage.
 stageForSlot('plot-b', 'worker', 0, 'unstake', 'b-worker-1', 0, null, 'B One');
 stageForSlot('plot-b', 'worker', 1, 'unstake', 'b-worker-2', 0, null, 'B Two');
 const undone = CERES_STAGE.find((item) => item.asset_ref === 'b-worker-2');
 const fetchCallsB = [];
 fetch = (url, opts) => {
 fetchCallsB.push({ url: String(url), body: opts.body });
 return Promise.resolve({
 ok: true,
 status: 200,
 json: async () => ({
 deed_uid: 'plot-b', before_powered: true, after_powered: true,
 removed: [{ asset_ref: 'b-worker-1', own_harvest_pp: 77, own_construction_pp: 5, own_base_pp: 5 }],
 plot_residual_delta: -2, plot_ability_delta: -2, harvest_pp_delta: -79,
 own_construction_pp_delta: { status: 'point', value: -5, low: -5, high: -5, reason: null },
 own_base_pp_delta: { status: 'point', value: -5, low: -5, high: -5, reason: null },
 notices: [],
 }),
 });
 };
 await removeCheckoutStage(undone);
 if (fetchCallsB.length !== 1) process.exit(20); // one server call to reprice the remaining removal
 if (fetchCallsB[0].body !== JSON.stringify({ asset_refs: ['b-worker-1'] })) process.exit(21);
 const remaining = CERES_STAGE.find((item) => item.asset_ref === 'b-worker-1');
 if (!remaining) process.exit(22);
 if (CERES_STAGE.includes(undone)) process.exit(23); // the undone entry itself is gone
 if (remaining.delta !== -79) process.exit(24); // applyRemovalImpactToStage(deedUid, remainingImpact) really re-ran
})().catch((error) => { console.error(error); process.exit(1); });
"""
    run_in_module_context(script, entry=ENTRY)


# R-45 removed the mixed-direction refusal guards under ; replacement
# coverage lives in tests/ceres/test_mixed_direction_staging.py.

def test_stage_move_payload_never_carries_a_display_only_field():
    """The object `stageMovePayload` sends to `/api/.../op-preview` must contain the move
 contract only -- `deed_uid`/`group_id`/`slot_index`/`action`/`asset_ref`/`evict_asset_ref`/
 `delta` -- and never a display-only field (`_asset_name`, `_previous_asset`,
 `_source_deed_uid`, `_notices`, `base`, `cooldown`, or any future one `stageForSlot`
 grows). Rewritten from a literal-source assertion (the internal rules's mutation-standard
 corollary "assert on the observable, not the spelling") after v0.5.81 added
 `cooldown` to the destructure and broke a test that matched the exact line text;
 this asserts the response's actual key set instead, which survives any future
 display-only field being added to `stageForSlot`/`stageMovePayload` the same way
 `cooldown` was.

 queue F7 (census row 22, plan §15 ruling 6b): converted from a
 hand-sliced `cloneStageAsset`/`stageForSlot`/`stageMovePayload` extraction shelled
 out to a raw `node -e` subprocess, to real calls of the REAL `stageForSlot` and
 `stageMovePayload` inside the shared `vm_context` whole-module sandbox (census row
 22) -- no more hand-picked helper slice to go stale.

 Injection: added a new display-only field `_extra_display_note: 'x'` to the entry
 object built by `stageForSlot` in `ceres/static/app.js` (inside the object literal
 at the `cooldown:`/`txId:` lines) WITHOUT adding it to `stageMovePayload`'s
 destructure strip list -- re-ran (against an in-memory copy of the module source
 passed to `run_in_module_context`'s `source=` override, not a disk edit -- this
 ticket's own scope is test-files-only) and went red: stderr's `console.error`
 line showed `got ['_extra_display_note', 'action', ...]` against the same
 `expected` list, proving the leaked key was caught before the process crashed
 on the `process.exit(30)` call this sandbox has no `process` global for
 (`vm_context`'s `vm.runInNewContext` sandbox omits it; any exit call in a
 `run_in_module_context` body script always surfaces as a `ReferenceError`-driven
 `CalledProcessError`, never the specific intended code -- a pre-existing
 `vm_context.py` property, not touched here). Restored and confirmed green.
    """
    script = r"""
CERES_STAGE.length = 0;
BOARD = null;
stageForSlot(
 'deed-1', 'worker', 2, 'replace', 'asset-ref', 500, 'evicted-ref',
 'Display Name', 'source-deed', {status: 'point', value: 10, low: 10, high: 10, reason: null},
 {ready: true, class: 'worker_card'},
);
const entry = CERES_STAGE.find((item) => item.deed_uid === 'deed-1');
if (!entry) process.exit(29);
entry._notices = [{code: 'x'}];
const payload = stageMovePayload(entry);
const keys = Object.keys(payload).sort();
const expected = ['action', 'asset_ref', 'deed_uid', 'delta', 'evict_asset_ref', 'group_id', 'slot_index'].sort();
if (JSON.stringify(keys) !== JSON.stringify(expected)) {
 console.error('got', keys, 'expected', expected);
 process.exit(30);
}
if (payload.deed_uid !== 'deed-1' || payload.asset_ref !== 'asset-ref' || payload.delta !== 500) process.exit(31);
"""
    run_in_module_context(script, entry=ENTRY)


def test_http_200_removal_payload_validation_rejects_partial_and_nonfinite_data():
    """ (campaign plan §15 ruling 6b, census row 22): input
 validation has no rendered surface beyond "did it throw", so this stays a
 whole-module `vm` unit test rather than being forced into a browser --
 but it now calls the REAL `validateRemovalImpact` inside the shared
 `tests/ceres/vm_context.py` sandbox instead of slicing `validateRemovalImpact`
 and `isValidEstimate`/`ESTIMATE_STATUSES` out of the source by string
 position (the old ``source.index("function validateRemovalImpact")`` /
 ``source.index("\\nasync function removalImpact", start)`` pair -- exactly
 the position-coupling this campaign retires). Same ~15 malformed-payload
 shapes as before; only the loading mechanism changed."""
    script = r"""
const valid = {
 deed_uid: 'plot-a', before_powered: true, after_powered: true,
 removed: [
 {asset_ref: 'a', own_harvest_pp: 10, own_construction_pp: 5, own_base_pp: 5},
 {asset_ref: 'b', own_harvest_pp: 20, own_construction_pp: null, own_base_pp: null},
 ],
 plot_residual_delta: -3, plot_ability_delta: -3, harvest_pp_delta: -33,
 own_construction_pp_delta: {status: 'point', value: -5, low: -5, high: -5, reason: null},
 own_base_pp_delta: {status: 'point', value: -5, low: -5, high: -5, reason: null},
};
if (validateRemovalImpact(valid, ['a', 'b'], 'plot-a') !== valid) process.exit(10);
function mustThrow(payload, refs, deedUid = 'plot-a') {
 try { validateRemovalImpact(payload, refs, deedUid); } catch (_error) { return; }
 process.exit(11);
}
function mustThrowMessage(payload, refs, expected, deedUid = 'plot-a') {
 try { validateRemovalImpact(payload, refs, deedUid); } catch (error) {
 if (error.message !== expected) process.exit(12);
 return;
 }
 process.exit(13);
}
mustThrow({...valid, removed: [valid.removed[0]]}, ['a', 'b']);
mustThrow({...valid, removed: [{asset_ref: 'a', own_harvest_pp: NaN}, valid.removed[1]]}, ['a', 'b']);
mustThrow({...valid, removed: [{...valid.removed[0], own_construction_pp: NaN}, valid.removed[1]]}, ['a', 'b']);
mustThrow({...valid, removed: [{...valid.removed[0], own_base_pp: NaN}, valid.removed[1]]}, ['a', 'b']);
mustThrow({...valid, removed: [{asset_ref: 'a', own_harvest_pp: 10, own_construction_pp: 5}, valid.removed[1]]}, ['a', 'b']);
mustThrow({...valid, plot_residual_delta: Infinity}, ['a', 'b']);
mustThrow({...valid, plot_ability_delta: null}, ['a', 'b']);
mustThrow({...valid, harvest_pp_delta: undefined}, ['a', 'b']);
mustThrow({...valid, deed_uid: 'plot-b'}, ['a', 'b']);
mustThrow({...valid, before_powered: 1}, ['a', 'b']);
mustThrow({...valid, after_powered: null}, ['a', 'b']);
mustThrow({...valid, own_construction_pp_delta: null}, ['a', 'b']);
mustThrow({...valid, own_construction_pp_delta: {status: 'bogus', value: 1, low: 1, high: 1}}, ['a', 'b']);
mustThrow({...valid, own_base_pp_delta: null}, ['a', 'b']);
mustThrow({...valid, own_base_pp_delta: {status: 'bogus', value: 1, low: 1, high: 1}}, ['a', 'b']);
mustThrowMessage({...valid, removed: [{asset_ref: 'a', own_harvest_pp: 10, own_construction_pp: 5}, valid.removed[1]]}, ['a', 'b'], 'Removal impact response contained an invalid Base PP value. Nothing was staged.');
mustThrowMessage({...valid, own_base_pp_delta: undefined}, ['a', 'b'], 'Removal impact response contained an invalid Base PP value. Nothing was staged.');
"""

    run_in_module_context(script, entry=ENTRY)


def test_removal_quotes_are_bound_to_board_generation_player_and_row_identity():
    """ queue F4: converted from a literal-source substring hunt to real
 calls of `removalBoardContext`/`currentRemovalRow`/`mutateEditorSlot` inside the
 shared `vm_context` sandbox (census row 22). Proves the actual staleness guard --
 a captured `{generation, player, row}` context stops matching the moment the board
 generation ticks, the player changes, or the board reloads a NEW row object for the
 same `deed_uid` (identity, not equality) -- rather than grepping the source for the
 guard's variable names.
    """
    script = r"""
const row1 = {
 deed_uid: 'plot-h', capacity: { potential_card_capacity: 1, runi_staked: false }, staking_facts: {},
 workers: [{ slot: 1, asset_ref: 'h1', name: 'H1', land_abilities: [], sole_holder_for: [] }], bonus: {},
};
BOARD = { plots: [row1], player: 'player0005' };
CURRENT_PLAYER = 'player0005';
BOARD_GENERATION = 10;

const ctx = removalBoardContext(row1);
if (ctx.generation !== 10 || ctx.player !== 'player0005' || ctx.row !== row1) process.exit(10);

if (currentRemovalRow('plot-h', ctx) !== row1) process.exit(11);

// Stale generation (what `const generation = ++BOARD_GENERATION;` in a board reload does).
BOARD_GENERATION = 11;
if (currentRemovalRow('plot-h', ctx) !== null) process.exit(12);
BOARD_GENERATION = 10;

// Stale player.
CURRENT_PLAYER = 'someone-else';
if (currentRemovalRow('plot-h', ctx) !== null) process.exit(13);
CURRENT_PLAYER = 'player0005';

// Row identity: a NEW object with the same deed_uid (a board reload) must not match
// `currentRow === context.row`, never a deed_uid equality check.
const row1Reloaded = { ...row1 };
BOARD = { plots: [row1Reloaded], player: 'player0005' };
if (currentRemovalRow('plot-h', ctx) !== null) process.exit(14);
BOARD = { plots: [row1], player: 'player0005' };
if (currentRemovalRow('plot-h', ctx) !== row1) process.exit(15);

// The mutation this quote gates: mutateEditorSlot(currentRow, spec.group, spec.index, null).
const groupH = editorGroups(row1).find((g) => g.id === 'worker');
mutateEditorSlot(row1, groupH, 0, null);
if (row1.workers.length !== 0) process.exit(16);
"""
    run_in_module_context(script, entry=ENTRY)


def test_candidate_breakdown_surfaces_real_plot_residual_term_only():
    """ queue F4: converted from a literal-source substring hunt to a
 real call of `renderCandidateBreakdown` inside the shared `vm_context` sandbox
 (census row 22) -- the exact "(b) substring half -> rendered read" the ticket
 calls for, since this function IS a pure render (it returns the breakdown string
 shown in the candidate picker), unlike the removal-impact validation tests above.
    """
    script = r"""
const detail = renderCandidateBreakdown({
 net_gain_breakdown: {
 own_harvest_delta: 1,
 evicted_loss: -2,
 plot_residual_delta: -3,
 donor_plot_delta: 4,
 precondition_delta: -5,
 // A retired field name -- must never surface under any label, even present.
 ability_broadcast_delta: 999,
 },
});
if (!detail.includes('Other workers on this plot')) process.exit(10);
if (detail.includes('999')) process.exit(11);
if (detail.includes('rest of plot')) process.exit(12);
if (detail.includes('ability_broadcast_delta')) process.exit(13);
"""
    run_in_module_context(script, entry=ENTRY)


def test_incoming_candidate_ability_metadata_reaches_the_staged_slot(monkeypatch):
    details = {
        867: {
            "id": 867,
            "stats": {"land_abilities": [[] for _ in range(7)] + [[["WOOD", 0.7], ["BLOODLINE", 0.3, "Elf"]]]},
        },
    }
    candidate = {"card_detail_id": 867, "level": 8, "asset_ref": "kinbound", "name": "Kinbound Druid"}
    monkeypatch.setattr(routes_candidates.capture_mod, "latest_snapshot", lambda config, player: ({"plots": []}, "fixture"))
    monkeypatch.setattr(routes_candidates, "load_details_by_id", lambda: details)
    monkeypatch.setattr(routes_candidates, "rank_candidates", lambda *args, **kwargs: {"candidates": [candidate.copy()]})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(player="player0005"))))

    response = routes_candidates.get_candidates("plot", request, group_id="worker")
    body = json.loads(response.body)

    assert body["candidates"][0]["land_abilities"] == [["WOOD", 0.7], ["BLOODLINE", 0.3, "Elf"]]
    assert body["candidates"][0]["sole_holder_for"] == ["WOOD", "BLOODLINE"]

    # queue F4: the FRONTEND half used to be two source substring
    # checks (`"land_abilities: occupant.land_abilities || []"`, `.filter((code) =>
    # counts.get(code) === 1)`); now a real call of `mutateEditorSlot` inside the
    # shared `vm_context` sandbox (census row 22), proving the ability metadata this
    # backend response carries actually threads through into the staged slot AND the
    # per-plot sole-holder recompute, not just that the strings are present somewhere.
    script = r"""
const row = {
 deed_uid: 'plot-i', capacity: { potential_card_capacity: 2 },
 workers: [{ slot: 1, asset_ref: 'existing', name: 'Existing', land_abilities: [['WOOD', 0.5]], sole_holder_for: ['WOOD'] }],
 bonus: {},
};
const groupI = editorGroups(row).find((g) => g.id === 'worker');
const incoming = {
 asset_ref: 'kinbound', name: 'Kinbound Druid',
 land_abilities: [['WOOD', 0.7], ['BLOODLINE', 0.3, 'Elf']],
};
mutateEditorSlot(row, groupI, 1, incoming);
const placed = row.workers.find((w) => w.asset_ref === 'kinbound');
const existingWorker = row.workers.find((w) => w.asset_ref === 'existing');
if (!placed) process.exit(10);
// land_abilities threaded through onto the staged slot unchanged.
if (!placed.land_abilities.some((entry) => entry[0] === 'WOOD' && entry[1] === 0.7)) process.exit(11);
// WOOD is now shared by two workers on this plot -- neither is sole holder.
if (placed.sole_holder_for.includes('WOOD')) process.exit(12);
if (existingWorker.sole_holder_for.includes('WOOD')) process.exit(13);
// BLOODLINE is unique to the incoming worker -- it IS sole holder.
if (!placed.sole_holder_for.includes('BLOODLINE')) process.exit(14);
"""
    run_in_module_context(script, entry=ENTRY)
