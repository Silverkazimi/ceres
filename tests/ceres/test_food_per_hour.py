""": the Food/hr column and food cost line share one tooltip, one line per fact.

Sandbox-runnable VM guards (no browser) for badges.js foodCostTip()/foodPerHourCell().
"""

from __future__ import annotations

import html
import json
import re

from .vm_context import run_in_module_context


_JS = r"""
const named = {grain_cost_per_hour: 672, food_discount: -0.3, food_discount_cards: [{name: 'Tireless Farmhand', code: 'RATIONING', rate: -0.3}]};
const lite = {grain_cost_per_hour: 1.455, food_discount: -0.03, food_discount_cards: [{name: 'Verico Worker', code: 'RATIONING_LITE', rate: -0.03}]};
const unnamed = {grain_cost_per_hour: 672, food_discount: -0.3, food_discount_cards: []};
const plain = {grain_cost_per_hour: 1015, food_discount: 0, food_discount_cards: []};
const records = {
  named: foodPerHourCell(named),
  lite: foodPerHourCell(lite),
  unnamed: foodPerHourCell(unnamed),
  plain: foodPerHourCell(plain),
  missing: foodPerHourCell({grain_cost_per_hour: null}),
  line: costLineHtml(named),
};
console.log('__FOOD__' + JSON.stringify(records));
"""


def _run() -> dict:
    result = run_in_module_context(_JS)
    out = result.stdout.split('__FOOD__', 1)
    assert len(out) == 2, result.stdout
    return json.loads(out[1].splitlines()[0])


def _attr(fragment: str, name: str) -> str:
    match = re.search(rf'{name}="([^"]*)"', fragment)
    return html.unescape(match.group(1)) if match else ''


def test_one_line_per_fact_and_rationing_names_the_card_with_its_icon() -> None:
    records = _run()
    rows = json.loads(_attr(records['named']['tip'], 'data-tip-items'))
    effects = [row['effect'] for row in rows]
    assert effects[1] == "Boosts don't count"
    assert effects[2] == 'Paid at each harvest'
    assert '{{ic:abilities/ability_rationing.png}}Rationing −30% from Tireless Farmhand' in effects
    lite_rows = json.loads(_attr(records['lite']['tip'], 'data-tip-items'))
    assert any('ability_light-rationing.png' in row['effect'] and 'Light Rationing −3% from Verico Worker' in row['effect'] for row in lite_rows)
    unnamed_rows = json.loads(_attr(records['unnamed']['tip'], 'data-tip-items'))
    assert any('Rationing −30% already included' in row['effect'] for row in unnamed_rows)
    plain_rows = json.loads(_attr(records['plain']['tip'], 'data-tip-items'))
    assert len(plain_rows) == 3


def test_foot_states_the_cap_not_a_source_and_cell_and_line_share_the_tooltip() -> None:
    records = _run()
    assert _attr(records['named']['tip'], 'data-tip-foot') == 'Base PP is counted after the plot cap.'
    assert 'Official rate' not in json.dumps(records)
    # the Grain icon follows the number. format.js num() uses maximumFractionDigits
    # semantics (no zero-padding), so an integer value prints without trailing zeros
    # test never matched this since landing ( landed with "tests not run", ).
    assert records['named']['html'].startswith('672<img class="dec-stake-icon"')
    assert 'resources/grain.png' in records['named']['html']
    assert records['missing']['html'] == '—' and records['missing']['tip'] == ''
    assert _attr(records['line'], 'data-tip-items') == _attr(records['named']['tip'], 'data-tip-items')
    for record in (records['named'], records['lite'], records['unnamed'], records['plain']):
        assert '{{ic:' not in _attr(record['tip'], 'aria-label')
