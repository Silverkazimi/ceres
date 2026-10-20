"""the board's Boost cell lists every single boost and adds them up to the multiplier."""
from pathlib import Path
from tests.ceres.vm_context import run_in_module_context
STATIC = Path(__file__).parents[2] / 'ceres' / 'static'


def test_boost_tooltip_lists_and_adds_up_every_boost():
    run_in_module_context(r"""
const row = { multiplier: 5.3, pp_tooltip: { production_modifiers: { card_abilities_boost: 0.8, card_bloodlines_boost: 0.25, deed_rarity_boost: 1, deed_status_token_boost: 0, runi_boost: 1, title_boost: 0.25, totem_boost: 1 } } };
const html = boostTipAttrs(row).replace(/&quot;/g, '"').replace(/&amp;/g, '&');
for (const text of ['Deed rarity', 'Totem', 'Runi', 'Worker abilities', 'Worker bloodlines', 'Title', '+430%', '5.30x']) if (!html.includes(text)) throw Error('missing ' + text + ': ' + html);
if (html.includes('Status token')) throw Error('a zero boost is listed');
if (html.includes('differs')) throw Error('a matching sum is flagged');
const odd = boostTipAttrs({ multiplier: 3, pp_tooltip: { production_modifiers: { totem_boost: 1 } } }).replace(/&quot;/g, '"');
if (!odd.includes('differs')) throw Error('a mismatch is not flagged');
if (boostTipAttrs({ multiplier: null, pp_tooltip: {} }) !== '') throw Error('no data must give no tooltip');
""", entry=STATIC / 'format.js')


def test_board_cell_uses_the_tooltip():
    assert "${boostTipAttrs(row)}>${row.multiplier !== null" in (STATIC / 'rows.js').read_text()
