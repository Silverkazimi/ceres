"""Guards for deferred plot-detail handoff across board generations."""

from __future__ import annotations

import subprocess

import pytest

from .vm_context import read_entry_source, run_in_module_context


_HANDOFF_BODY = r"""
BOARD_GENERATION = 11;
CURRENT_PLAYER = 'player0005';
EXPANDED_ROWS.clear();
EXPANDED_DATA_LOADED.clear();
EXPAND_ERRORS.clear();
LOADING_PLOTS.clear();
const deedUid = 'I-handoff';
const rowA = {deed_uid: deedUid, workers: [{asset_ref: 'worker-on-A'}], assets_loaded: true};
const rowB = {deed_uid: deedUid, workers: [], assets_loaded: true, board_revision: 'N+1'};
const detail = {deed_uid: deedUid, workers: [{asset_ref: 'worker-on-B'}], board_revision: 'detail', assets_loaded: true};
BOARD = {player: 'player0005', plots: [rowA]};
CURRENT_ROWS = [rowA];
EXPANDED_ROWS.add(deedUid);
const mountedUpdates = [];
updateMountedRow = (uid, options = {}) => mountedUpdates.push({uid, options});
const resolvers = [];
let fetchCount = 0;
fetch = () => {
  fetchCount += 1;
  return new Promise((resolve) => { resolvers.push(resolve); });
};
(async () => {
  const pending = loadPlotDetail(rowA, BOARD_GENERATION);
  BOARD_GENERATION = 12;
  BOARD.plots[0] = rowB;
  CURRENT_ROWS[0] = rowB;
  resolvers[0]({ok: true, status: 200, json: async () => ({deed_uid: deedUid, workers: [{asset_ref: 'stale-response'}], assets_loaded: true})});
  await pending;
  await Promise.resolve();
  if (fetchCount !== 2) throw new Error('handoff did not issue exactly one recovery fetch: ' + fetchCount);
  resolvers[1]({ok: true, status: 200, json: async () => detail});
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (fetchCount !== 2) throw new Error('handoff issued a redundant fetch: ' + fetchCount);
  if (BOARD.plots[0] !== detail) throw new Error('detail did not land on the successor BOARD row');
  if (CURRENT_ROWS[0] !== detail) throw new Error('detail did not land on the successor CURRENT_ROWS entry');
  if (rowA.workers.length !== 1 || rowA.workers[0].asset_ref !== 'worker-on-A' || rowA.board_revision !== undefined) throw new Error('stale detail was applied to row A');
  if (!EXPANDED_DATA_LOADED.has(deedUid)) throw new Error('successor detail was not marked ready');
  if (!mountedUpdates.some(({uid, options}) => uid === deedUid && options.readiness === true)) {
    throw new Error('successor row was not remounted with readiness');
  }
})();
"""


def test_deferred_detail_hands_off_to_successor_row_and_snapshot() -> None:
    run_in_module_context(_HANDOFF_BODY)


_SAME_GENERATION_BODY = r"""
BOARD_GENERATION = 21;
CURRENT_PLAYER = 'player0005';
EXPANDED_ROWS.clear();
EXPANDED_DATA_LOADED.clear();
EXPAND_ERRORS.clear();
LOADING_PLOTS.clear();
const deedUid = 'I-same-generation';
const rowA = {deed_uid: deedUid, workers: [{asset_ref: 'worker-on-A'}], assets_loaded: true};
const rowB = {deed_uid: deedUid, workers: [], assets_loaded: true, board_revision: 'same-generation-B'};
const detail = {deed_uid: deedUid, workers: [{asset_ref: 'worker-on-B'}], board_revision: 'detail', assets_loaded: true};
BOARD = {player: 'player0005', plots: [rowA]};
CURRENT_ROWS = [rowA];
const mountedUpdates = [];
updateMountedRow = (uid, options = {}) => mountedUpdates.push({uid, options});
let resolveDetail;
let fetchCount = 0;
fetch = () => {
  fetchCount += 1;
  return new Promise((resolve) => { resolveDetail = resolve; });
};
(async () => {
  BOARD_GENERATION = 22;
  const click = toggleRow(rowA);
  BOARD.plots[0] = rowB;
  CURRENT_ROWS[0] = rowB;
  resolveDetail({ok: true, status: 200, json: async () => detail});
  await click;
  if (fetchCount !== 1) throw new Error('same-generation handoff issued a redundant fetch: ' + fetchCount);
  if (BOARD.plots[0] !== detail || CURRENT_ROWS[0] !== detail) throw new Error('same-generation detail missed successor entries');
  if (rowA.workers.length !== 1 || rowA.workers[0].asset_ref !== 'worker-on-A') throw new Error('same-generation stale detail was applied to row A');
  if (!EXPANDED_DATA_LOADED.has(deedUid)) throw new Error('same-generation successor detail was not marked ready');
  if (!mountedUpdates.some(({uid, options}) => uid === deedUid && options.readiness === true)) {
    throw new Error('same-generation successor row was not remounted with readiness');
  }
  console.log('SAME_GENERATION_HANDOFF_OK');
})();
"""


def test_generation_bump_before_click_still_hands_off_within_generation() -> None:
    unmutated = run_in_module_context(_SAME_GENERATION_BODY, check=False)
    assert unmutated.returncode == 0
    assert 'SAME_GENERATION_HANDOFF_OK' in unmutated.stdout


_304_NO_RECOVERY_BODY = r"""
BOARD_GENERATION = 31;
CURRENT_PLAYER = 'alpha';
EXPANDED_ROWS.clear();
EXPANDED_DATA_LOADED.clear();
EXPAND_ERRORS.clear();
LOADING_PLOTS.clear();
const deedUid = 'I-304';
const rowA = {deed_uid: deedUid, workers: [{asset_ref: 'worker-on-A'}], assets_loaded: true};
const rowB = {deed_uid: deedUid, workers: [{asset_ref: 'worker-on-B'}], assets_loaded: true, board_revision: 'beta-board'};
BOARD = {player: 'alpha', plots: [rowA]};
CURRENT_ROWS = [rowA];
EXPANDED_ROWS.add(deedUid);
const mountedUpdates = [];
updateMountedRow = (uid, options = {}) => mountedUpdates.push({uid, options});
let resolveDetail;
let fetchCount = 0;
fetch = () => {
  fetchCount += 1;
  return new Promise((resolve) => { resolveDetail = resolve; });
};
(async () => {
  const pending = loadPlotDetail(rowA, BOARD_GENERATION);
  BOARD_GENERATION = 32;
  CURRENT_PLAYER = 'beta';
  BOARD = {player: 'beta', plots: [rowB]};
  CURRENT_ROWS = [rowB];
  resolveDetail({ok: false, status: 304, json: async () => ({detail: 'not modified'})});
  await pending;
  if (fetchCount !== 1) throw new Error('304 player/generation change issued a redundant recovery fetch: ' + fetchCount);
  if (BOARD.plots[0] !== rowB || CURRENT_ROWS[0] !== rowB) throw new Error('foreign 304 response changed the new player board');
  if (rowB.board_revision !== 'beta-board' || rowB.workers[0].asset_ref !== 'worker-on-B') throw new Error('stale 304 detail was applied to the new player row');
  if (EXPANDED_DATA_LOADED.has(deedUid)) throw new Error('304 response incorrectly marked detail ready');
  if (EXPAND_ERRORS.has(deedUid)) throw new Error('stale 304 response surfaced an expansion error');
})();
"""


def test_304_after_player_generation_change_does_not_recover_foreign_row() -> None:
    run_in_module_context(_304_NO_RECOVERY_BODY)


def test_old_terminal_drop_rejects_successor_handoff() -> None:
    source = read_entry_source()
    anchor = """      if (!current() || idx === -1 || BOARD.plots[idx] !== row) {
        targetRow = handoffRow();
        if (!targetRow) return;
        idx = BOARD.plots.findIndex((p) => p.deed_uid === deedUid);
      }
      if (!current() || idx === -1 || BOARD.plots[idx] !== targetRow) return;"""
    # put the not-loaded check between this guard and reconcileCapPrediction(); the mutation (drop the
    # handoff) does not depend on the line after the guard, so the anchor ends at the guard.
    assert source.count(anchor) == 1
    mutated = source.replace(
        anchor,
        """      if (!current() || idx === -1 || BOARD.plots[idx] !== row) return;""",
        1,
    )
    assert mutated != source
    with pytest.raises(subprocess.CalledProcessError) as failure:
        run_in_module_context(_SAME_GENERATION_BODY, source=mutated)
    failure_output = (failure.value.stdout or '') + (failure.value.stderr or '')
    assert 'same-generation handoff issued a redundant fetch' in failure_output
    assert 'ReferenceError' not in failure_output
