""" guards for construction action visibility across ready and claimed states."""

from .vm_context import STATIC, run_in_module_context


def test_construction_action_tracks_progress_completion_and_claim() -> None:
    run_in_module_context(
        r"""
const row = {
 deed_uid: 'plot-construction-1', terrain: 'River', resource: 'GRAIN',
 worksite_type: 'Grain Farm', is_construction: true,
 harvest: {state: 'construction'},
 pending_change: {
 kind: 'worksite_change', project_id: 42, to: 'grain', to_label: 'Grain Farm',
 started_at: '2026-09-24T12:00:00Z', ready_at: '2099-09-25T12:00:00Z',
 progress_pct: .75, pp_spent: 75, pp_required: 100, stalled: false,
 },
};

const inProgress = buildBarHtml(row, '', buildInfo(row, null));
if (!inProgress.includes('data-slot-action="cancel-construction"')) process.exit(10);
if (inProgress.includes('data-harvest-action="feed-workers"')) process.exit(11);

const ready = buildInfo(row, {projection: {
 state: 'construction', source: 'fresh', construction: {project_id: 42},
 worksite_completion: {project_id: 42, grain_cost: 25},
}});
const finished = buildBarHtml(row, '', ready);
if (!finished.includes('data-harvest-action="feed-workers"')) process.exit(12);
if (finished.includes('data-slot-action="cancel-construction"')) process.exit(13);

const claimed = {...row, is_construction: false, pending_change: null,
 harvest: {state: 'harvesting', token: 'GRAIN', anchor_at: '2026-09-25T12:00:00Z',
 net_at_anchor: 1, hours_at_anchor: 0, gross_per_hour: 1, tax_rate: .1, costs: []}};
if (buildInfo(claimed, {projection: {state: 'harvesting', source: 'fresh'}}) !== null) process.exit(14);
const normal = harvestBarHtml(claimed);
if (!normal.includes('data-harvest-action="open-plot"')) process.exit(15);
if (normal.includes('data-slot-action="cancel-construction"')) process.exit(16);
if (normal.includes('data-harvest-action="feed-workers"')) process.exit(17);
process.exit(0);
""",
        entry=STATIC / "harvest-state.js",
    )
