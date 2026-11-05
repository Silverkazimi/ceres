"""IN-1014: the Type sort groups magical types, then Total PP desc, then rarity desc."""

import json

from .vm_context import run_in_module_context

ROWS = r"""
const rows = [
  {id: 'm-water-lo', plot: '1-1-1', plot_status_rank: 1, magic_type: 'water', harvest_pp: 10, rarity_rank: 1},
  {id: 'm-fire-lo', plot: '1-1-2', plot_status_rank: 1, magic_type: 'fire', harvest_pp: 10, rarity_rank: 1},
  {id: 'm-water-hi', plot: '1-1-3', plot_status_rank: 1, magic_type: 'water', harvest_pp: 50, rarity_rank: 1},
  {id: 'm-fire-hi-common', plot: '1-1-4', plot_status_rank: 1, magic_type: 'fire', harvest_pp: 50, rarity_rank: 0},
  {id: 'm-fire-hi-epic', plot: '1-1-5', plot_status_rank: 1, magic_type: 'Fire', harvest_pp: 50, rarity_rank: 2},
  {id: 'natural', plot: '1-1-6', plot_status_rank: 0, magic_type: null, harvest_pp: 99, rarity_rank: 3},
  {id: 'kingdom', plot: '1-1-7', plot_status_rank: 3, magic_type: null, harvest_pp: 1, rarity_rank: 0},
  {id: 'm-none', plot: '1-1-8', plot_status_rank: 1, magic_type: null, harvest_pp: 500, rarity_rank: 3},
];
"""


def _order(direction):
    result = run_in_module_context(ROWS + f"""
    S.SORT_KEY = 'plot_status_rank';
    S.SORT_DIR = {direction};
    console.log('__T__' + JSON.stringify(sortRows(rows).map(r => r.id)));
    """)
    out = result.stdout.split("__T__", 1)
    assert len(out) == 2, result.stdout
    return json.loads(out[1].splitlines()[0])


def test_type_sort_first_click_groups_magical_types_then_pp_then_rarity():
    assert _order(-1) == [
        "kingdom",
        "m-fire-hi-epic", "m-fire-hi-common", "m-fire-lo",
        "m-water-hi", "m-water-lo",
        "m-none",
        "natural",
    ]


def test_type_sort_toggle_flips_group_order_but_keeps_pp_and_rarity_descending():
    assert _order(1) == [
        "natural",
        "m-water-hi", "m-water-lo",
        "m-fire-hi-epic", "m-fire-hi-common", "m-fire-lo",
        "m-none",
        "kingdom",
    ]
