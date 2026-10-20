""" guards for the shared badge colour and severity table."""

from __future__ import annotations

import json
import re

from ceres.notices import _BADGES, badge_meta_projection

from .vm_context import STATIC, run_in_module_context


BADGES = (STATIC / "badges.js").read_text(encoding="utf-8")
BOARD = (STATIC / "board.js").read_text(encoding="utf-8")
COLOURS = (STATIC / "badge-colours.css").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
GROUPS = {"critical", "special", "warning", "positive", "info", "neutral"}


def test_backend_badges_project_exactly_one_group_and_within_group_order() -> None:
    projected = badge_meta_projection()
    assert set(projected) == set(_BADGES)
    for code, definition in projected.items():
        assert definition["state_group"] in GROUPS, code
        assert isinstance(definition["group_order"], int), code


def test_column_and_filter_share_the_complete_badge_table_in_a_vm() -> None:
    backend = json.dumps(badge_meta_projection())
    run_in_module_context(
        f"""
function assert(ok, message) {{ if (!ok) throw new Error(message); }}
BOARD = {{badge_meta: {backend}}};
const expected = new Map([
  ['unpowered', 'critical'], ['unpowered_worker', 'critical'], ['over_cap', 'critical'],
  ['region_dec', 'critical'], ['pending_change_stalled', 'critical'],
  ['staged_changes', 'special'], ['staged_cooldown', 'special'],
  ['dark_discount_wasted', 'warning'], ['no_worksite', 'warning'],
  ['redundant_power_core', 'warning'], ['underfilled', 'warning'],
  ['bloodline_solo', 'warning'], ['worksite_incomplete', 'warning'],
  ['bloodline_paired', 'positive'], ['pending_change', 'special'], ['runi', 'info'],
  // key renamed sole_ability_holder -> land_ability_card and moved info -> positive (green) by
  // v0.9.513 "Keep Land Card and VoV badges in the green category" (maintainer decision 2026-09-16).
  ['land_ability_card', 'positive'], ['building_in_box', 'neutral'], ['idle', 'neutral'],
  ['unstable_totem', 'neutral'],
  // assets_not_loaded ("Partly loaded") removed page-wide by  (v0.9.615); it no longer
  // enters the shared badge table (syncBackendBadges explicitly skips it).
]);
const definitions = badgeDefinitions();
assert(definitions.length === expected.size, 'every badge must occur once in the shared table');
assert(new Set(definitions.map((entry) => entry.key)).size === definitions.length, 'badge keys must be unique');
for (const entry of definitions) {{
  assert(expected.get(entry.key) === entry.group, `${{entry.key}} has the wrong or missing group`);
  assert(Number.isInteger(entry.groupOrder), `${{entry.key}} needs a within-group order`);
}}
const options = new Map(badgeFilterOptions().map((entry) => [entry.key, entry]));
for (const entry of definitions.filter((candidate) => candidate.filter !== false)) {{
  assert(options.get(entry.key)?.group === entry.group, `${{entry.key}} filter colour drifted from the shared table`);
}}
CERES_STAGE.splice(0, CERES_STAGE.length, {{deed_uid: 'plot', action: 'stake'}});
const html = badgeHtml({{
  deed_uid: 'plot', badges: ['unstable_totem', 'no_worksite', 'unpowered'], notices: [],
  pending_change: {{stalled: true, to_label: 'Grain'}},
  capacity: {{worker_count: 1, card_capacity: 1, open_slots: 0}}, workers: [], bonus: {{}},
  region_efficiency: 1,
}});
const groups = [...html.matchAll(/class="[^"]*badge-group-([a-z]+)/g)].map((match) => match[1]);
assert(groups.join(',') === 'critical,critical,special,warning,neutral', `wrong rendered group order: ${{groups}}`);
""",
        entry=STATIC / "badges.js",
    )


def test_both_surfaces_call_the_shared_table_helpers() -> None:
    assert "const BADGE_TABLE = new Map([" in BADGES
    assert "function badgeClasses(code" in BADGES
    assert "badge-group-${badgeGroup(code)}" in BADGES
    assert "return badgeDefinitions()" in BADGES
    assert "for (const { key, label, group } of badgeFilterOptions())" in BOARD
    assert "option.dataset.group = group;" in BOARD


def test_badge_styles_use_only_global_colour_tokens() -> None:
    raw_colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|(?<!s)\brgba?\(|\bhsla?\(|\b(?:white|black|transparent)\b")
    assert not raw_colour.search(COLOURS)
    # badge-filter-color added by (v0.9.627): a locally-scoped alias set per group to one
    # of the already-allowed base tokens (danger/warn/ok/info/meta/gold) -- not a new raw colour.
    allowed = {"danger", "warn", "ok", "info", "meta", "gold", "hi", "bg2", "body", "badge-filter-color"}
    assert set(re.findall(r"var\(--([a-z0-9-]+)\)", COLOURS)) <= allowed
    for group in GROUPS:
        assert f".badge.badge-group-{group}" in COLOURS
        assert f".csel-option.badge-option-group-{group}" in COLOURS
        assert f".csel-trigger.badge-filled.badge-option-group-{group}" in COLOURS


def test_index_loads_only_the_new_versioned_badge_stylesheet_line() -> None:
    link = '<link rel="stylesheet" href="/static/badge-colours.css?v={{CERES_VERSION}}">'
    assert INDEX.count(link) == 1
    expected = "\n".join([
        '<link rel="stylesheet" href="/static/ceres.css?v={{CERES_VERSION}}">',
        '<link rel="stylesheet" href="/static/buttons.css?v={{CERES_VERSION}}">',
        link,
    ])
    assert expected in INDEX
    assert INDEX.count("badge-colours.css") == 1
