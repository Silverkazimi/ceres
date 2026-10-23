"""Focused guards for the / construction presentation."""

import json
from pathlib import Path

from ceres.harvest import construction_facts

from .vm_context import STATIC, run_in_module_context


ROOT = Path(__file__).parents[2]
HARVEST = (STATIC / "harvest-state.js").read_text(encoding="utf-8")
ROWS = (STATIC / "rows.js").read_text(encoding="utf-8")
TERRAINS = {
    "badlands", "bog", "caldera", "canyon", "desert", "forest", "hills",
    "jungle", "lake", "mountain", "plains", "river", "swamp", "tundra",
}


def test_pending_change_wins_over_uncleared_state_and_renders_real_pp_progress() -> None:
    run_in_module_context(
        r"""
const row = {
  deed_uid: 'under-clear', terrain: 'River', resource: null, worksite_type: '',
  is_construction: true, harvest: {state: 'no_project'},
  pending_change: {
    kind: 'worksite_change', to: 'grain', to_label: 'Grain Farm',
    started_at: '2026-09-15T12:00:00Z', ready_at: '2099-09-17T12:00:00Z',
    pp_spent: 375000, pp_required: 1500000, progress_pct: .25, stalled: false,
  },
};
const info = buildInfo(row, null);
if (!info || info.toLabel !== 'Grain Farm') process.exit(10);
const html = buildBarHtml(row, '', info);
if (!html.includes('Under construction — Grain Farm')) process.exit(11);
if (html.includes('No worksite yet')) process.exit(12);
if (html.includes('375,000 / 1,500,000 PP')) process.exit(13); // removes the PP counter; progress remains.
if (!html.includes('style="--pct:25.00%"')) process.exit(14);
if (!html.includes('role="meter"') || !html.includes('aria-valuenow="25"')) process.exit(15);
const nameAt = html.indexOf('Grain Farm');
const iconAt = html.indexOf('class="resource-icon"');
if (nameAt < 0 || iconAt <= nameAt) process.exit(16);
const complete = buildInfo(row, {projection: {
  state: 'construction', source: 'fresh', construction: {},
  worksite_completion: {grain_cost: 15000},
}});
if (!buildBarHtml(row, '', complete).includes('data-harvest-action="feed-workers"')) process.exit(17);
if (buildInfo(row, {projection: {state: 'harvesting', source: 'fresh'}}) !== null) process.exit(18);
process.exit(0);
""",
        entry=STATIC / "harvest-state.js",
    )


def test_fresh_construction_projection_carries_live_project_values() -> None:
    facts = construction_facts(
        {
            "project_type": "worksite_grain_construction",
            "start_date": "2026-09-15T12:00:00Z",
            "projected_end": "2026-09-17T12:00:00Z",
            "hours_to_completion": 48,
            "pp_spent": 30672.093,
            "pp_required": 1500000,
        },
        {},
    )
    assert facts["to_label"] == "Grain Farm"
    assert facts["pp_spent"] == 30672.093
    assert facts["pp_required"] == 1500000
    assert facts["hours_to_completion"] == 48
    assert facts["projected_end"] == facts["ready_at"]


def test_all_construction_terrains_use_dedicated_art_and_finished_art_fallback() -> None:
    terrain_rows = ",".join(f"'{terrain}'" for terrain in sorted(TERRAINS))
    run_in_module_context(
        f"""
for (const terrain of [{terrain_rows}]) {{
  const row = {{
    deed_uid: 'construction-' + terrain, terrain, resource: null, worksite_type: '',
    pending_change: {{kind: 'worksite_change', to: 'grain', to_label: 'Grain Farm'}},
  }};
  const layers = editorSceneLayers(row);
  if (!layers.worksite.includes('/land/constructions/construction_' + terrain + '.png')) process.exit(20);
  if (!layers.worksiteFallback.includes('/land/worksites/productionBuildingUniform/')) process.exit(21);
  const geometry = worksiteArtGeometry(row);
  if (!geometry || geometry.hRem !== 13 || !(geometry.r > geometry.l) || Math.abs(geometry.ratio - 2829 / 1080) > 0.000001) process.exit(22);
}}
process.exit(0);
""",
        entry=STATIC / "art.js",
    )
    # The public tree carries no artwork: a construction image counts when it is on disk (a maintainer checkout)
    # or on the reviewed list Ceres downloads it from on first use (ceres/static/assets/sources.json).
    assets = ROOT / "ceres/static/assets"
    on_disk = {path.stem.removeprefix("construction_") for path in (assets / "land/constructions").glob("construction_*.png")}
    source_manifest = assets / "sources.json"
    listed = {
        row["path"].removeprefix("land/constructions/construction_").removesuffix(".png")
        for row in (json.loads(source_manifest.read_text(encoding="utf-8")) if source_manifest.is_file() else [])
        if row["path"].startswith("land/constructions/construction_") and row["path"].endswith(".png")
    }
    assert on_disk | listed == TERRAINS


def test_scene_loader_and_mobile_sheet_preserve_finished_art_on_missing_construction_image() -> None:
    run_in_module_context(
        r"""
const properties = {};
const artProperties = {};
const art = {style: {setProperty(name, value) { artProperties[name] = value; }}};
const frame = {
  style: {setProperty(name, value) { properties[name] = value; }},
  querySelector(selector) { return selector === '.editor-worksite-art' ? art : null; },
};
const row = {
  deed_uid: 'missing-construction', terrain: 'River', resource: null, worksite_type: '',
  pending_change: {kind: 'worksite_change', to: 'grain', to_label: 'Grain Farm'},
};
const fallbackUrl = editorSceneLayers(row).worksiteFallback;
applyScene({
  mountedFrame: frame, sceneRow: row, sceneReleased: true,
  sceneOutcome: {
    worksite: {kind: 'unavailable', permanent: true},
    worksiteFallback: {kind: 'decoded', url: fallbackUrl},
  },
});
if (!properties['--scene-worksite'].includes('/land/worksites/productionBuildingUniform/')) process.exit(30);
if (Number(artProperties['--art-ratio']) !== 1560 / 640) process.exit(31);
process.exit(0);
""",
        entry=STATIC / "eos.js",
    )
    assert "layers.worksiteFallback || layers.worksite" in ROWS
    assert "loadImageWithRetry(layers.worksite)" in ROWS


def test_construction_box_keeps_completion_route_and_global_palette_styling() -> None:
    assert "if (completion)" in HARVEST
    assert 'data-harvest-action="feed-workers"' in HARVEST
    construction_css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    rules = "\n".join(
        line for line in construction_css.splitlines()
        if "harvest-bar-build-name" in line or "harvest-bar-build-pp" in line or "harvest-bar-build-sep" in line
    )
    assert "#" not in rules and "rgb(" not in rules and "hsl(" not in rules
    assert "var(--heading)" in construction_css and "var(--body)" in construction_css
