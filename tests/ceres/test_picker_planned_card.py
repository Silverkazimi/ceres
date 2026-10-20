""" expected cards use real plot pricing and survive picker filters."""
from pathlib import Path
from copy import deepcopy
from ceres.candidates import planned_card
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / 'ceres' / 'static'


def test_expected_card_priced_without_mutating_snapshot(report, details_by_id):
    before = deepcopy(report)
    plan = {'id': 'cc-test', 'card': {'name': 'Worker', 'card_detail_id': 2, 'edition': 0},
            'expected': {'bcx': 150, 'bcx_max': 115, 'level': 8, 'base_pp': 1100, 'base_pp_capped': True},
            'ready_at': '2026-10-08T00:00:00Z'}
    row = planned_card(report, report['plots'][0]['deed_uid'], plan, player='player0005', slot=0, details_by_id=details_by_id)
    assert row['planned'] and row['chain_id'] == 'cc-test'
    assert row['base_pp'] == 1100 and row['bcx'] == 150 and row['base_pp_capped']
    assert row['construction_pp']['status'] == 'point'
    assert row['construction_pp']['value'] == 1100 * (1 + row['terrain_boost'])
    assert row['cooldown'] == {'ready': False, 'state': 'planned', 'ready_at': None, 'reason': 'after the combine'}
    assert report == before
    plan['expected']['base_pp'] = None
    row = planned_card(report, report['plots'][0]['deed_uid'], plan, player='player0005', slot=0, details_by_id=details_by_id)
    assert row['construction_pp']['value'] is None and row['construction_pp']['reason']


def test_visibility_reservation_and_all_picker_variants():
    run_in_module_context(r"""
PLANS.chains={'cc-test':{id:'cc-test',state:'planned',card:{name:'Worker'},expected:{level:8,bcx:150,base_pp:1100},inputs:[
 {uid:'secret-a',asset_ref:'secret-a',kind:'staked',status:'active',source:{deed_uid:'d',slot_index:2}},
 {uid:'secret-b',kind:'free',status:'active'}]}};
const plan=chainPlans()['cc-test'];
if(chainPickerVisible(plan)) throw Error('unsigned visible');
plan.inputs[0].unstake={tx_id:'signed'};
if(!chainPickerVisible(plan)) throw Error('signed hidden');
const picker={groupId:'worker',deedUid:'d',slotIndex:2,filters:{q:'does not match',ready_now:true},row:{},sort:'name'};
S.CERES_PICKER=picker;
const c={planned:true,chain_id:'cc-test',name:'Worker',level:8,bcx:150,bcx_max:115,base_pp:1100,base_pp_capped:true,cooldown:{state:'planned',ready:false}};
if(candidateStatusModel(c).selectable) throw Error('confirming selectable');
plan.inputs[0].unstake.confirmed_at='now';
if(!candidateStatusModel(c).selectable) throw Error('confirmed disabled');
for(const html of [renderCandidateRow(c,0,picker,false),renderCandidateGridCard(c,0,picker,false)]) {
 if(!html.includes('Planned')||!html.includes('Not yet combined')||!html.includes('Returns upgraded to this slot')||!html.includes('Stake here')) throw Error(html);
 if(html.includes('secret-a')||html.includes('secret-b')) throw Error('UID shown');
}
if(!candidateReadinessOverlayHtml(c).includes('about 3 days')) throw Error('unknown time');
""", entry=STATIC / 'candidates.js')


def test_route_injects_before_filters_and_keeps_normal_page_offsets(monkeypatch, report, details_by_id):
    import json
    from types import SimpleNamespace
    import ceres.routes_candidates as routes
    monkeypatch.setattr(routes, 'player_for_request', lambda *_: 'player0005')
    monkeypatch.setattr(routes.capture_mod, 'latest_snapshot', lambda *_: (report, None))
    monkeypatch.setattr(routes, 'with_live_slots', lambda _config, _player, _deed, snapshot: snapshot)
    monkeypatch.setattr(routes, 'load_details_by_id', lambda: details_by_id)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=object())))
    plan = {'id': 'cc-test', 'card': {'name': 'Worker', 'card_detail_id': 2, 'edition': 0},
            'expected': {'level': 8, 'bcx': 150, 'base_pp': 1100}}
    common = dict(group_id='worker', player='player0005', q='never matches any real card', min_pp=None, max_pp=None,
                  edition=None, slot=0, limit=25, staged_moves=None, staged_worksite=None, staged_worksite_type=None,
                  planned_cards=json.dumps([plan]))
    first = json.loads(routes.get_candidates(report['plots'][0]['deed_uid'], request, offset=0, **common).body)
    assert len(first['candidates']) == 1 and first['candidates'][0]['planned']
    second = json.loads(routes.get_candidates(report['plots'][0]['deed_uid'], request, offset=25, **common).body)
    assert not second['candidates']
    source = (STATIC / 'picker-actions.js').read_text()
    assert 'picker.offset = picker.candidates.filter(candidate => !candidate.planned).length' in source
