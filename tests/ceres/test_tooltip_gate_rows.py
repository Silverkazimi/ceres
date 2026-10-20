""" gate rebuild: plain-line rows (empty label) must survive into the tooltip.

Sandbox-runnable VM guards. Runs the real builders (Combine, Over Cap, Runi badge,
worksite tile) through tipAttrs() and asserts every row reaches data-tip-items and that
no inline-icon marker leaks into the accessible text.
"""

from __future__ import annotations

import html
import json
import re

from .vm_context import run_in_module_context


_JS = r"""
const records = {
  combine: tipAttrs(combineTipModel(3, 'tile_chip_rail')),
  mixed: tipAttrs({title: 'T', items: [{name: '', effect: 'plain line'}, {name: 'Label', effect: 'value'}, {name: 'Empty', effect: ''}], peer: 'tile_chip_rail'}),
  editor: tipAttrs(editorRuniTip({base_pp: null}, {}, 0, {multiplier_parts: {runi_boost: 1}})),
  add: renderPowerSatellite({deed_uid: 'add-runi'}, {id: 'runi'}, null),
  runi: runiBadgeChip(),
  terrain: tipAttrs({title: 'Earth splinter', body: `${tipIcon('land/terrain-emblem-forest.svg')}Forest terrain bonus +10%`, peer: 'icons_identity'}),
};
console.log('__GATE__' + JSON.stringify(records));
"""


def _run() -> dict:
    result = run_in_module_context(_JS)
    out = result.stdout.split('__GATE__', 1)
    assert len(out) == 2, result.stdout
    return json.loads(out[1].splitlines()[0])


def _attr(fragment: str, name: str) -> str | None:
    match = re.search(rf'{name}="([^"]*)"', fragment)
    return html.unescape(match.group(1)) if match else None


def test_plain_line_rows_are_kept_and_empty_values_dropped_alone() -> None:
    records = _run()
    combine = json.loads(_attr(records['combine'], 'data-tip-items'))
    assert len(combine) == 2 and all(row['name'] == '' and row['effect'] for row in combine)
    mixed = json.loads(_attr(records['mixed'], 'data-tip-items'))
    assert [row['effect'] for row in mixed] == ['plain line', 'value']
    for key in ('editor', 'runi'):
        items = json.loads(_attr(records[key], 'data-tip-items'))
        assert len(items) == {'editor': 4, 'runi': 5}[key]
        effects = [row['effect'] for row in items]
        assert 'Power Core or other Power Source + Runi · opens a sixth worker slot (bare Runi caps at five)' in effects
        assert 'Takes one of the plot\'s worker slots itself' in effects
    add_body = _attr(records['add'], 'data-tip-body') or ''
    assert 'sixth slot with a Power Core or an Energized worker' in add_body
    assert 'Runi itself uses one worker-count slot' in add_body


def test_runi_tooltip_strings_have_no_snake_case_tokens() -> None:
    records = _run()
    tooltip_values = []
    for key in ('editor', 'add', 'runi'):
        fragment = records[key]
        for name in ('data-tip-title', 'data-tip-body', 'data-tip-items', 'aria-label'):
            value = _attr(fragment, name)
            if value:
                tooltip_values.append(value)
    assert not re.search(r'\b[A-Za-z0-9]+_[A-Za-z0-9_]*\b', '\n'.join(tooltip_values))


def test_inline_icon_marker_never_reaches_accessible_text() -> None:
    records = _run()
    for fragment in records.values():
        label = _attr(fragment, 'aria-label') or ''
        assert '{{ic:' not in label, label
    assert 'Forest terrain bonus +10%' in (_attr(records['terrain'], 'aria-label') or '')
