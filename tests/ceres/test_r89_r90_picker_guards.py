"""Whole-module guards for the R-90 candidate-picker surface."""

from __future__ import annotations

import json
import subprocess

import pytest

from ceres.notices import notice_catalog

from .vm_context import read_entry_source, run_in_module_context


TERRAIN_PENALTY_MESSAGE = notice_catalog()["terrain_penalty"]["message"]

_PICKER_BODY = "NOTICE_CATALOG = " + json.dumps(notice_catalog()) + ";\n" + r"""
const row = { deed_uid: 'picker-plot', terrain: 'Plains', worksite_type: 'Farm' };
const filters = {
  q: '', rarity: '', splinter: '', edition: '', min_pp: '', bucket: 'all',
  ready_now: true, include_terrain_penalised: false, bloodline_pairs: false,
};
function picker(groupId) {
  return {
    deedUid: row.deed_uid, groupId, groupLabel: groupId === 'worker' ? 'Workers' : 'Runi',
    slotIndex: 0, mode: 'stake', currentOccupant: null, filters: { ...filters },
    candidates: [], total: 0, offset: 0, loading: false, error: null,
    excludedCounts: { terrain_penalty: 2 }, notices: [], source: 'fixture', sort: 'name',
    bloodlineSubTypes: [],
  };
}
S.CERES_PICKER = picker('runi');
const runiHtml = renderInlineCandidatePicker(row);
if (runiHtml.includes('candidate-worker-affinity')) process.exit(60);
S.CERES_PICKER = picker('worker');
const workerHtml = renderInlineCandidatePicker(row);
if (!workerHtml.includes('candidate-worker-affinity')) process.exit(61);
if (!workerHtml.includes(TERRAIN_PENALTY_MESSAGE)) process.exit(62);
""".replace("TERRAIN_PENALTY_MESSAGE", json.dumps(TERRAIN_PENALTY_MESSAGE))


def _assert_mismatch_red(body_js: str, mutated_source: str) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(body_js, source=mutated_source)


def test_terrain_filter_scope_and_empty_notice_are_preserved() -> None:
    """Guards 6–7: the terrain checkbox is rendered only on the worker picker,
    where the request parameter acts, and a filtered empty result retains the
    actionable terrain-penalty notice."""
    run_in_module_context(_PICKER_BODY)


def test_terrain_filter_scope_guard_is_red_when_runi_gets_the_control() -> None:
    """Guard 6 injection: rendering the terrain control for every worker-like
    group makes the inert Runi picker expose `include_terrain_penalised`."""
    source = read_entry_source()
    mutated = source.replace(
        "const affinity = isWorker ?",
        "const affinity = true ?",
        1,
    )
    assert mutated != source
    _assert_mismatch_red(_PICKER_BODY, mutated)


def test_terrain_empty_notice_guard_is_red_when_notice_is_removed() -> None:
    """Guard 7 injection: dropping the terrain-penalty empty-state notice leaves
    the filtered worker picker unexplained, so the approved remedy assertion fails."""
    source = read_entry_source()
    old = "if (Number(excluded.terrain_penalty) > 0) {"
    assert source.count(old) == 1
    mutated = source.replace(old, "if (false) {", 1)
    assert mutated != source
    _assert_mismatch_red(_PICKER_BODY, mutated)
