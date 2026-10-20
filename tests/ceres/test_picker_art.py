""" guard for candidate art surviving managed editor re-renders."""

from __future__ import annotations

from pathlib import Path

from .test_tile_redesign_static import BARE_BROWSER_GLOBALS_JS
from .vm_context import read_entry_source, run_in_module_context


ROOT = Path(__file__).parents[2]
EDITOR = ROOT / "ceres" / "static" / "editor.js"


_PICKER_BODY = r"""
const row = {
  deed_uid: 'ir3540-plot', plot: '1-2-3', terrain: 'Plains', worksite_type: 'Farm',
  resource: 'GRAIN', harvest_pp: 0, base_pp: 0, base_pp_after_cap: 0, base_pp_cap: 1000,
  workers: [], capacity: {potential_card_capacity: 0, worker_count: 0, max_workers_allowed: 0, open_slots: 0},
  staking_facts: {is_power_core_staked: false, is_energized: false, is_runi_staked: false},
};
S.CERES_PICKER = {
  deedUid: row.deed_uid, groupId: 'worker', groupLabel: 'Workers', slotIndex: 0,
  mode: 'stake', currentOccupant: null,
  filters: {q: '', rarity: '', splinter: '', edition: '', min_pp: '', bucket: 'all', ready_now: true,
    include_terrain_penalised: false, bloodline_pairs: false},
  candidates: [{name: 'Guard Candidate', card_name: 'Guard Candidate', editions: '2', edition: 2,
    card_set: 'chaos', level: 1, base_pp: 100, construction_pp: 100, plot_impact: 0,
    terrain_boost: 0, cooldown: null}],
  total: 1, offset: 0, loading: false, error: null, excludedCounts: {}, notices: [],
  source: 'fixture', sort: 'name', bloodlineSubTypes: [], targetTerrain: 'Plains', row,
};
const html = renderSlotEditor(row, {geometryProfile: {pageSize: 1}, slotPage: 0});
const start = html.indexOf('<div class="candidate-visible-slice">');
if (start < 0) throw new Error('picker did not render inside the slot editor');
const pickerHtml = html.slice(start);
const images = [...pickerHtml.matchAll(/<img\b[^>]*>/g)].map(([image]) => image);
if (!images.length) throw new Error('picker rendered no candidate art');
for (const image of images) {
  if (!image.match(/\bsrc="[^"\s]+"/)) throw new Error('picker candidate image has no src: ' + image);
  if (image.includes('data-eos-managed')) throw new Error('picker candidate image is Eos-managed: ' + image);
}
"""


def test_picker_images_keep_src_and_stay_outside_eos_management() -> None:
    run_in_module_context(_PICKER_BODY, entry=EDITOR, extra_context_js=BARE_BROWSER_GLOBALS_JS)


def test_picker_art_guard_goes_red_when_managed_flag_is_reenabled() -> None:
    source = read_entry_source(entry=EDITOR)
    old = "    managedEditorArt = false;\n    try {\n      return dataReady ? renderInlineCandidatePicker(row) : '';"
    mutated = source.replace(old, "    managedEditorArt = true;\n    try {\n      return dataReady ? renderInlineCandidatePicker(row) : '';", 1)
    assert mutated != source
    result = run_in_module_context(
        _PICKER_BODY,
        entry=EDITOR,
        source=mutated,
        extra_context_js=BARE_BROWSER_GLOBALS_JS,
        check=False,
    )
    assert result.returncode != 0
