""": a dropped saved change names the one live cause, not 'listed, delegated, or rented'."""
from pathlib import Path
from tests.ceres.vm_context import run_in_module_context
STATIC = Path(__file__).parents[2] / 'ceres' / 'static'


def test_card_unavailable_names_the_live_cause():
    run_in_module_context(r"""
if(dropReasonText('card_unavailable','delegated to someone')!=='this card is delegated to someone now') throw Error('delegated');
if(dropReasonText('card_unavailable','locked until 2026-11-01')!=='this card is locked until 2026-11-01') throw Error('locked');
const generic=dropReasonText('card_unavailable');
if(generic.includes('listed, delegated, or rented now')) throw Error('old catch-all');
if(dropReasonText('slot_taken','x')!=='a different card is in this slot now') throw Error('other reasons keep their text');
""", entry=STATIC / 'stage-plans.js')


def test_changes_row_passes_the_detail():
    text = (STATIC / 'checkout.js').read_text()
    assert 'dropReasonText(entry._plan_invalid_reason, entry._plan_invalid_detail)' in text


def test_rented_in_card_with_rental_market_id_is_available():
    from ceres.metrics import classify_idle_availability
    rented_in = {"market_id": "abc-78", "rental_type": "season", "delegated_to": "renter", "player": "owner"}
    assert classify_idle_availability(rented_in, None, "renter") == (True, None)
    assert classify_idle_availability({"market_id": "abc-1"}, None, "owner") == (False, "listed on market")
    rented_out = {"market_id": "abc-2", "rental_type": "season", "delegated_to": "someone", "player": "owner"}
    assert classify_idle_availability(rented_out, None, "owner")[0] is False
