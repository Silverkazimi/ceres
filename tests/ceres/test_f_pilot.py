"""queue F pilot: ability/status semantics and the moved Cards controls."""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context


STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_ability_and_not_soulbound_match_real_copy_fields():
    run_in_module_context("""
const base = {name:'A', card_set:'land', rarity:'common', foil:0, splinters:['Earth'], status:'idle',
  available:true, soulbound:false, maxed:false, land_base_pp:10, land_abilities:[['GRAIN',0.1],['WOOD',0.2]]};
const bound = {...base, soulbound:true};
const empty = {...base, land_abilities:[]};
const matches = (card, ability, status=[]) => cbMatches(card, {...cbDefaultFilters(), ability, status});
if (!matches(base, ['GRAIN']) || !matches(base, ['WOOD']) || !matches(base, ['STONE','WOOD'])) throw Error('ability OR failed');
if (matches(base, ['STONE']) || matches(empty, ['__any__']) || !matches(base, ['__any__'])) throw Error('ability match failed');
if (!matches(base, [], ['not_soulbound']) || matches(bound, [], ['not_soulbound'])) throw Error('soulbound status failed');
if (!matches(base, [], ['cooling','not_soulbound'])) throw Error('status OR failed');
""", entry=STATIC / "card-browser-model.js")


def test_pilot_controls_have_expected_accessible_names_and_locations():
    cards = (STATIC / "cards.html").read_text()
    board = (STATIC / "index.html").read_text()
    header, controls = cards.split('<div class="controls-row cb-controls"', 1)
    assert 'id="cb-mode-purchases"' in header and 'data-header-icon="purchases"' in header
    assert 'id="cb-mode-purchases"' not in controls
    assert 'id="cb-ability" data-cb-filter="ability"' in controls
    assert 'id="cb-status"' in controls and 'value="not_soulbound">Not soulbound' in controls
    assert '<span>Combine</span>' in controls and '<span>Max</span>' in controls
    assert 'id="cb-maxed"' in controls and 'cm-only' in controls.split('id="cb-maxed"')[0].split('<label')[-1]
    for html, name in ((cards, 'cb-clear'), (board, 'clear-board-filters')):
        tag = html.split(f'id="{name}"', 1)[1].split('</button>', 1)[0]
        assert 'aria-label="Clear all filters"' in tag and '<svg' in tag and ' hidden>' in tag
