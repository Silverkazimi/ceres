""": Dark Discount and Alteration switch places in the ability order.

Sandbox-runnable VM guard (no browser) for icons.js orderLandAbilities().
"""

from __future__ import annotations

import html
import json

from .vm_context import run_in_module_context


_JS = r"""
const codes = (list) => orderLandAbilities(list).map((entry) => Array.isArray(entry) ? entry[0] : entry);
const records = {
  swapped: codes([['ALTERATION', 0], ['LL'], ['DD', -0.5]]),
  already: codes([['DD', -0.5], ['ALTERATION', 0]]),
  tiers: codes([['ALTERATION', 0], ['GRAIN', 0.6], ['DD', -0.99], ['BLOODLINE', 0.3, 'Elf', {active: true, companions: []}]]),
  onlyOne: codes([['ALTERATION', 0], ['LL']]),
  // The tile's "+N more" list and the worker tooltip follow the same order ().
  overflow: renderAbilityRow({land_abilities: [['ALTERATION', 0], ['LL'], ['GRAIN', 0.6], ['RATIONING', -0.1], ['DD', -0.5], ['WOOD', 0.4], ['STONE', 0.85]]}),
  workerTip: editorWorkerTip({
    asset_ref: 'w1', name: 'Worker', base_pp: 1000, splinter: 'Life', rarity: 'Rare', card_set: 'chaos', edition: 7, level: 3, foil: 0,
    terrain_boost: 0, land_abilities: [['ALTERATION', 0], ['DD', -0.5]], sole_holder_for: [], dec_stake_needed: 0,
  }, {id: 'worker', label: 'Workers', slots: []}, 0, {workers: []}),
};
console.log('__ORDER__' + JSON.stringify(records));
"""


def _run() -> dict:
    result = run_in_module_context(_JS)
    out = result.stdout.split('__ORDER__', 1)
    assert len(out) == 2, result.stdout
    return json.loads(out[1].splitlines()[0])


def test_dark_discount_moves_ahead_of_alteration_and_nothing_else_moves() -> None:
    records = _run()
    assert records['swapped'] == ['DD', 'LL', 'ALTERATION']
    assert records['already'] == ['DD', 'ALTERATION']
    assert records['tiers'] == ['BLOODLINE', 'GRAIN', 'DD', 'ALTERATION']
    assert records['onlyOne'] == ['ALTERATION', 'LL']


def test_more_list_and_worker_tooltip_share_the_tile_order() -> None:
    records = _run()
    # Seven abilities: four on the row, three in the overflow; preserve Dark Discount order.
    overflow = html.unescape(records['overflow'])
    assert overflow.index('Dark Discount') < overflow.index('Rationing') < overflow.index('Alteration')
    assert '+3' in overflow
    hidden = overflow[overflow.index('class="slot-ability-count"'):]
    assert 'Dark Discount' not in hidden
    assert hidden.index("Labor's Luck") < hidden.index('Rationing') < hidden.index('Alteration')
    names = [item['name'] for item in records['workerTip']['items'] if item.get('group') == 'Abilities']
    assert names.index('Dark Discount') < names.index('Alteration')
