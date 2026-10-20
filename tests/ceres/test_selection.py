"""  selection prioritizes available copies without losing the anchor."""
from tests.ceres.test_chain_model import STATIC
from tests.ceres.vm_context import run_in_module_context
from ceres.combine_chain import expected_card, upgrade_inputs


def test_priority_largest_first_anchor_and_overcombine():
    run_in_module_context(r"""
const copies=[{uid:'anchor',bcx:4,kind:'staked',eligible:true},
 {uid:'land',bcx:60,kind:'staked',eligible:true},{uid:'cool',bcx:30,kind:'cooling',eligible:true},
 {uid:'small',bcx:1,kind:'free',eligible:true},{uid:'big',bcx:20,kind:'free',eligible:true},
 {uid:'locked',bcx:100,kind:'free',eligible:false}];
if(JSON.stringify(selectChainLevel(copies,'anchor',24))!==JSON.stringify(['anchor','big'])) throw Error('free first');
if(JSON.stringify(selectChainLevel(copies,'anchor',50))!==JSON.stringify(['anchor','big','small','cool'])) throw Error('cooling before land');
if(selectChainLevel(copies,'anchor',100).includes('locked')) throw Error('unusable');
if(selectChainLevel([{uid:'a',bcx:150,kind:'free',eligible:true},{uid:'b',bcx:160,kind:'free',eligible:true}],'a',115).length!==2) throw Error('overcombine requires two');
""", entry=STATIC / 'combine-chain.js')


def test_maxed_inputs_retain_cc_and_cap_production():
    cards = [dict(uid=uid, card_detail_id=1, edition=4, card_set='chaos', rarity='rare', gold=False,
                  bcx=cc, bcx_max=115, land_base_pp=1100, player='alice') for uid, cc in [('A',150),('B',160)]]
    assert all(row['eligible'] for row in upgrade_inputs(cards, 'A', account='alice'))
    result = expected_card(cards)
    assert result['bcx'] == 310
    assert result['level'] == 8
    assert result['base_pp'] == 1100
    assert result['base_pp_capped'] is True


def test_input_route_live_read_is_explicit_and_expected_allows_overcombine(monkeypatch):
    from types import SimpleNamespace
    import ceres.routes_combine_chain as routes
    cards = [dict(uid=uid, asset_ref=uid, card_detail_id=7, edition=4, card_set='chaos', rarity='rare', gold=False,
                  bcx=cc, bcx_max=115, land_base_pp=1100, player='alice') for uid, cc in [('A',150),('B',160)]]
    report = {'cards': cards, 'coverage': {'collection': {'complete': True}}}
    monkeypatch.setattr(routes.client_limits, 'limit', lambda *args: None)
    monkeypatch.setattr(routes, 'player_for_request', lambda *args: 'alice')
    monkeypatch.setattr(routes, '_snapshot', lambda *args: (report, None, '2026-10-05T00:00:00Z'))
    monkeypatch.setattr(routes, 'load_details_by_id', lambda: {7: {'rarity': 2}})
    calls = []
    monkeypatch.setattr(routes, '_live_collection', lambda account: (calls.append(account) or report, '2026-10-05T01:00:00Z'))
    request = SimpleNamespace()
    result = routes.combine_chain_inputs({'uid': 'A'}, request)
    assert result['thresholds'][-1] == 115
    assert calls == []
    assert routes.combine_chain_expected_card({'uids': ['A', 'B']}, request)['expected']['bcx'] == 310
    assert calls == []
    result = routes.combine_chain_inputs({'uid': 'A', 'live': True}, request)
    assert calls == ['alice']
    assert result['observed_at'] == '2026-10-05T01:00:00Z'
