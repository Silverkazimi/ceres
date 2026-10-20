"""board occupants have opaque references, not collection card IDs."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ceres.combine_chain import ChainInputError, upgrade_inputs
from tests.ceres.test_chain_model import STATIC
from tests.ceres.vm_context import run_in_module_context


@pytest.fixture
def real_shape(monkeypatch):
    import ceres.routes_combine_chain as routes
    cards = [dict(uid=uid, asset_ref=ref, card_detail_id=7, edition=4,
                  card_set='chaos', foil=0, gold=False, bcx=2,
                  land_base_pp='100.000', player='alice')
             for uid, ref in [('A', 'a' * 24), ('B', 'b' * 24)]]
    cards[0]['stake_ref_uid'] = 'plot'
    monkeypatch.setattr(routes.client_limits, 'limit', lambda *args: None)
    monkeypatch.setattr(routes, '_records', lambda *args: ('alice', cards, '2026-10-05T00:00:00Z', {}))
    monkeypatch.setattr(routes, 'load_details_by_id', lambda: {7: {'rarity': 2}})
    monkeypatch.setattr(routes, '_live_collection', lambda *args: pytest.fail('live call'))
    return routes, cards


def test_route_and_classifier_resolve_unique_asset_ref(real_shape):
    routes, cards = real_shape
    result = routes.combine_chain_inputs({'uid': cards[0]['asset_ref']}, SimpleNamespace())
    assert len(result['copies']) == 2
    assert all(copy['eligible'] for copy in result['copies'])
    assert result['copies'][0]['uid'] == 'A'
    assert result['copies'][0]['source'] == {'deed_uid': 'plot', 'slot_index': None}
    assert upgrade_inputs(cards, cards[0]['asset_ref'], account='alice')[0]['uid'] == 'A'


@pytest.mark.parametrize('ambiguous', [False, True])
def test_unknown_and_ambiguous_refs_refused(real_shape, ambiguous):
    routes, cards = real_shape
    reference = 'missing'
    if ambiguous:
        reference = cards[0]['asset_ref']
        cards[1]['asset_ref'] = reference
    code = 'anchor_ambiguous' if ambiguous else 'anchor_missing'
    with pytest.raises(ChainInputError) as exc:
        upgrade_inputs(cards, reference, account='alice')
    assert exc.value.code == code
    with pytest.raises(HTTPException) as exc:
        routes.combine_chain_inputs({'uid': reference}, SimpleNamespace())
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == code
    if ambiguous:
        assert routes.combine_chain_inputs({'uid': 'A'}, SimpleNamespace())['copies'][0]['uid'] == 'A'


def test_real_shape_client_lookup_open_and_failed_fetch():
    run_in_module_context(r"""
(async()=>{
CURRENT_PLAYER='alice'; updateMountedRow=()=>{}; renderUpgradeWindow=()=>{};
openCombinePlanDialog=()=>{};
const card={name:'Worker',card_uid:null,asset_ref:'aaaaaaaaaaaaaaaaaaaaaaaa'};
const picker={groupId:'worker',currentOccupant:card,deedUid:'plot'};
S.CERES_PICKER=picker;
fetch=async(url,init)=>{
 if(JSON.parse(init.body).uid!==card.asset_ref) throw Error('wrong reference');
 return {ok:true,json:async()=>({copies:[{uid:'A',asset_ref:card.asset_ref,eligible:true},{uid:'B',eligible:true}]})};
};
await loadOccupantUpgrade(picker);
if(!upgradeActionHtml(card,'occupant',true).includes('Upgrade Worker')) throw Error('missing title');
await openUpgradeWindow(card,{kind:'tile'});
if(UPGRADE_WINDOW?.anchor!=='A'||UPGRADE_WINDOW.selected[0]!=='A') throw Error('wrong anchor');
let warnings=0;console.warn=()=>{warnings++};fetch=async()=>({ok:false});
await loadOccupantUpgrade(picker);
if(card.upgrade||upgradeActionHtml(card)||warnings!==1) throw Error('stale action or silent failure');
picker.mobile=true;fetch=async()=>{throw Error('phone fetched')};
await loadOccupantUpgrade(picker);
if(warnings!==1) throw Error('phone lookup');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'combine-chain-ui.js')


def test_missing_slot_rescued_by_asset_ref_through_staging():
    run_in_module_context(r"""
(async()=>{
CURRENT_PLAYER='alice'; PLANS.open=true;PLANS.ready=true;PLANS.account='alice';
const card={name:'Worker',card_uid:null,asset_ref:'aaaaaaaaaaaaaaaaaaaaaaaa',bcx:2,edition:4,foil:0,rarity:'rare',land_base_pp:'100.000'};
const group={id:'worker',slots:[null,null,card]};
BOARD={player:'alice',plots:[{deed_uid:'plot',group}]};
editorGroups=row=>[row.group];let loaded=0;loadPlotDetail=async()=>{loaded++};
renderUpgradeWindow=()=>{};renderCheckoutSurface=()=>{};updateMountedRow=()=>{};
openCombinePlanDialog=()=>{};closeCombineDialog=()=>{};stageToast=()=>{};
removalImpact=async()=>({removed:[],notices:[]});applyRemovalImpactToStage=()=>{};
mutateEditorSlot=(row,g,index,asset)=>{g.slots[index]=asset};
fetch=async(url)=>({ok:true,json:async()=>url.includes('/inputs')?{copies:[
 {uid:'A',asset_ref:card.asset_ref,eligible:true,bcx:2,kind:'staked',source:{deed_uid:'plot',slot_index:null},asset:{uid:'A',asset_ref:card.asset_ref,edition:4,foil:0,land_base_pp:'100.000'}},
 {uid:'B',asset_ref:'bbbbbbbbbbbbbbbbbbbbbbbb',eligible:true,bcx:2,kind:'free',asset:{uid:'B',asset_ref:'bbbbbbbbbbbbbbbbbbbbbbbb',edition:4,foil:0,land_base_pp:null}}]}:{expected:{survivor_uid:'A',bcx:4,level:2,base_pp:200}}});
await openUpgradeWindow(card,{kind:'tile',deed_uid:'plot',slot_index:2});
UPGRADE_WINDOW.selected.push('B');await updateUpgradeSelection();await stageUpgradePlan();
const plan=Object.values(chainPlans())[0];
if(!loaded||!plan||plan.inputs[0].source.slot_index!==2) throw Error('slot rescue failed');
if(CERES_STAGE.length!==1||CERES_STAGE[0].slot_index!==2||CERES_STAGE[0].action!=='unstake'||CERES_STAGE[0].chain_input_uid!=='A') throw Error('wrong unstake');
if(card.uid||card.card_uid) throw Error('board acquired UID');
// round 2: carry the real shape through persistence and the combine continuation.
await persistStagePlans();CERES_STAGE.length=0;PLANS.chains={};
checkAllPlans=async request=>request.map(item=>({...item,verdict:'ok'}));
await restoreStagePlans();
const restored=chainPlans()[plan.id];
if(!PLANS.open||!validChainPlan(restored)||restored.inputs[0].asset.land_base_pp!==100)throw Error('saved shape held');
// A later observed read after the explicitly simulated unstake makes both copies free.
restored.inputs.forEach(input=>{input.kind='free';input.source=null});restored.state='ready';
await saveChain(plan.id,restored);
const operation=['custom_json',{id:'sm_combine_cards',required_auths:[],required_posting_auths:['alice'],json:JSON.stringify({cards:['A','B'],n:'shape-attempt'})}];
const built={operation,preview:{account:'alice',buildable:true,uids:['A','B'],expected:restored.expected,hazard:{rows:[{card_id:'A',bcx:2},{card_id:'B',bcx:2}]}}};
rebuildCombineForSigning=async()=>built;
await buildPlanCombine(restored);
await saveChain(plan.id,{...restored,state:'combine_signed',combine:{nonce:'shape-attempt',tx_id:'fiction-tx',operation}});
fetch=async()=>({ok:true,json:async()=>({state:'domain-confirmed',combine_result:{uid:'A',bcx:4,level:2,land_base_pp:'200.000'},combine_observed_at:'2026-10-05'})});
settleCombineRefresh=async()=>{};await verifyPlanCombine(plan.id);
if(chainPlans()[plan.id].state!=='combined'||chainPlans()[plan.id].problem)throw Error('verified shape held');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'combine-chain-ui.js')


def test_owner_local_snapshot_staked_worker_resolves_offline(monkeypatch):
    from ceres import capture, routes_combine_chain as routes
    from ceres.board import load_details_by_id, project_single_plot
    from ceres.config import CeresConfig
    from ceres.routes_combine import _collection
    root = Path.home() / '.ceres/snapshots'
    if not all((root / name).is_file() for name in ('land-player0005-latest.json', 'land-player0005-cards-latest.json')):
        pytest.skip('maintainer local snapshot/sidecar absent')
    report, _ = capture.latest_snapshot(CeresConfig(player='player0005'), 'player0005')
    records = _collection(capture.load_card_pool(dict(report)))
    details = load_details_by_id()
    monkeypatch.setattr(routes.client_limits, 'limit', lambda *args: None)
    monkeypatch.setattr(routes, '_records', lambda *args: ('player0005', records, 'local', {}))
    monkeypatch.setattr(routes, '_live_collection', lambda *args: pytest.fail('live call'))
    for plot in report['plots']:
        row = project_single_plot(report, plot['deed_uid'], details)
        for worker in (row or {}).get('workers') or []:
            if not worker.get('asset_ref') or worker.get('uid') or worker.get('card_uid'):
                continue
            result = routes.combine_chain_inputs({'uid': worker['asset_ref']}, SimpleNamespace())
            anchor = next(copy for copy in result['copies'] if copy['asset_ref'] == worker['asset_ref'])
            if anchor['eligible'] and sum(copy['eligible'] for copy in result['copies']) >= 2:
                assert anchor['kind'] == 'staked'
                # B01: optional local evidence also crosses persistence/restore.
                from tests.ceres.test_r2_shapes import roundtrip
                from ceres.combine_chain import expected_card
                chosen = [anchor, next(copy for copy in result['copies'] if copy['eligible'] and copy['uid'] != anchor['uid'])]
                selected = [next(record for record in records if record['uid'] == copy['uid']) for copy in chosen]
                # Source resolution itself is covered above; no real plot data enters repo fixtures.
                roundtrip([dict(copy, kind='free', source=None) for copy in chosen], expected_card(selected, details_by_id=details))
                return
    pytest.fail('no eligible real staked worker with another copy')
