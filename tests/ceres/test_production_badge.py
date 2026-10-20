"""a fourth change badge shows the staged effect on the plot's production.

Sandbox-runnable VM guards (no browser). Production is linear in Total PP on a plot,
so the staged rate is the measured rate scaled by the Total PP ratio.
"""

from __future__ import annotations

import json

from .vm_context import run_in_module_context


_JS = r"""
const row = {work_per_hour: 120, harvest_pp: 60000, resource: 'iron'};
const estimate = {status: 'unknown'};
const records = {
 up: stagedProduction(row, 90000),
 sps: stagedProduction({...row, resource: 'sps'}, 90000),
 noResource: stagedProduction({...row, resource: null}, 90000),
 zeroHarvest: stagedProduction({...row, harvest_pp: 0}, 90000),
 htmlUp: renderChangeBadges(estimate, 30000, 2, null, 90000, row),
 htmlDown: renderChangeBadges(estimate, -15000, 1, null, 45000, row),
 htmlNoRow: renderChangeBadges(estimate, 30000, 2, null, 90000, null),
};
console.log('__FR1941__' + JSON.stringify(records));
"""


def _run() -> dict:
    result = run_in_module_context(_JS)
    out = result.stdout.split('__FR1941__', 1)
    assert len(out) == 2, result.stdout
    return json.loads(out[1].splitlines()[0])


def test_production_scales_with_total_pp_and_refuses_unscalable_plots() -> None:
    records = _run()
    assert records['up'] == {'resource': 'iron', 'now': 120, 'after': 180, 'delta': 60}
    assert records['sps'] is None
    assert records['noResource'] is None
    assert records['zeroHarvest'] is None


def test_fourth_badge_renders_signed_rate_with_resource_icon_and_tooltip() -> None:
    records = _run()
    up = records['htmlUp']
    assert up.count('editor-change-badge ') == 2  # Total + Production
    assert 'editor-change-production is-positive' in up
    assert '+60 ' in up and 'resource-icon' in up
    assert 'Iron production after the change' in up
    # the resource icon sits inside the sentence (an inline-icon marker in the
    # attribute), and the badge's own icon carries no second tooltip.
    assert 'Now: 120 {{ic:resources/iron_200.webp}}Iron per hour' in up
    assert 'After: 180 {{ic:resources/iron_200.webp}}Iron per hour' in up
    assert up.count('data-tip=') == 2  # Total badge + production badge; the icon has none
    down = records['htmlDown']
    assert 'editor-change-production is-negative' in down and '−30 ' in down
    assert 'editor-change-production' not in records['htmlNoRow']
