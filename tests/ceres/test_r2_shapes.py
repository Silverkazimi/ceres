""" B01/B02/A08/B05: invented identities with audited collection field shapes."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from ceres.combine_chain import expected_card
from ceres.player_space import validate_payload, SpaceError
from tests.ceres.test_sign_order import SETUP, STATIC
from tests.ceres.vm_context import run_in_module_context


def rows():
    return [dict(uid=uid, asset_ref=ref * 24, player='alice', card_detail_id=7,
                 edition=4, foil=0, gold=False, card_set='chaos', bcx=cc,
                 level=10, land_base_pp=pp, stake_end_date=None)
            for uid, ref, cc, pp in [('C-fiction-a', 'a', 529, '1000.000'),
                                      ('C-fiction-b', 'b', 430, '1000.000')]]


def roundtrip(copies, expected, envelope=None):
    script = SETUP + '\nconst copies=' + json.dumps(copies) + ';const expected=' + json.dumps(expected) + ';const envelope=' + json.dumps(envelope) + r""";
(async()=>{
const real=createChain({id:'cc-shape',origin:{kind:'picker',slot_index:null},card:{name:'Invented',rarity:'common',edition:4,foil:0},copies,expected});
if(envelope) real.combine={nonce:envelope.payload.nonce,operation:makeBuild(real).operation,recovery:envelope};
if(!validChainPlan(real)) throw Error('created model invalid');
await saveChain(real.id,real);
console.log('CREATED '+localStorage.getItem('ceres:stage-plan:v1:alice'));
// Legacy saved display strings migrate on restore; the seal payload stays unchanged.
const key='ceres:stage-plan:v1:alice';const doc=JSON.parse(localStorage.getItem(key));
const legacyPP=copies[0].asset.land_base_pp;
doc.chains[real.id].inputs[0].asset.land_base_pp=legacyPP==null?null:String(legacyPP);localStorage.setItem(key,JSON.stringify(doc));
CERES_STAGE.length=0; PLANS.chains={}; await restoreStagePlans();await persistStagePlans();
if(!PLANS.open||!PLANS.ready||!validChainPlan(chainPlans()[real.id]))throw Error('restore held');
if(chainPlans()[real.id].inputs[0].asset.land_base_pp!==(legacyPP==null?null:Number(legacyPP)))throw Error('migration failed');
if(envelope&&JSON.stringify(chainPlans()[real.id].combine.recovery)!==JSON.stringify(envelope))throw Error('authenticated bytes altered');
console.log('DOCUMENT '+localStorage.getItem(key));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = run_in_module_context(script, entry=STATIC/'checkout.js')
    created = json.loads(next(line[8:] for line in result.stdout.splitlines() if line.startswith('CREATED ')))
    validate_payload('staged', created)
    document = json.loads(next(line[9:] for line in result.stdout.splitlines() if line.startswith('DOCUMENT ')))
    validate_payload('staged', document)
    return document


def test_b01_creation_save_validation_restore_with_sealed_raw_facts(tmp_path):
    from ceres.combine_evidence import recovery_envelope, recover
    from ceres.config import CeresConfig
    records = rows()
    records.append(dict(records[0], uid='C-fiction-null', asset_ref='c'*24, bcx=1, land_base_pp=None))
    expected = expected_card(records, details_by_id={7: {'rarity': 1}})
    config = CeresConfig(root=tmp_path, player='alice')
    envelope = recovery_envelope(config, 'fiction-nonce', account='alice', records=records, expected=expected)
    copies = [dict(uid=row['uid'], asset_ref=row['asset_ref'], asset=row, bcx=row['bcx'], kind='free', source={'deed_uid':'invented-plot','slot_index':None}) for row in records]
    document = roundtrip(copies, expected, envelope)
    restored = document['chains']['cc-shape']['combine']['recovery']
    assert recover(config, restored, account='alice', nonce='fiction-nonce', uids=[r['uid'] for r in records]) == records
    for bad in ['NaN', 'Infinity', '9'*400, '<img src=x>', -1]:
        broken = deepcopy(document)
        broken['chains']['cc-shape']['combine']['recovery'].pop('payload_json', None)
        broken['chains']['cc-shape']['combine']['recovery']['payload']['records'][0]['land_base_pp'] = bad
        with pytest.raises(SpaceError):
            validate_payload('staged', broken)


@pytest.mark.parametrize('bcx', [3, 959])
def test_b02_string_result_exact_or_explicit_acceptance_and_reload(bcx):
    script = SETUP + r"""
(async()=>{
p.expected.bcx=BCX;p.expected.base_pp=1000;
p.state='combine_signed';p.combine={tx_id:'known',nonce:'nonce',operation:makeBuild(p).operation};await saveChain(p.id,p);
fetch=async()=>({ok:true,json:async()=>({state:'domain-confirmed',combine_result:{uid:p.inputs[0].uid,bcx:BCX,level:10,land_base_pp:'1000.000'},combine_observed_at:'2026-10-05'})});
settleCombineRefresh=async()=>{};await verifyPlanCombine(p.id);
if(chainPlans()[p.id].problem||chainPlans()[p.id].result.base_pp!==1000)throw Error('exact result held');
await saveChain(p.id,{...chainPlans()[p.id],problem:{code:'combine_mismatch'}});
await resolvePlanMismatch(p.id,true);await persistStagePlans();
if(chainPlans()[p.id].problem||!chainPlans()[p.id].mismatch_accepted)throw Error('explicit acceptance failed');
await restoreStagePlans();if(!PLANS.open||chainPlans()[p.id].result.base_pp!==1000)throw Error('reload failed');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    run_in_module_context(script.replace('BCX', str(bcx)), entry=STATIC/'checkout.js')


def test_a08_decimal_projection_order_independent_and_strict_third_decimal():
    records=rows()
    records[0].update(bcx=300,land_base_pp='1125.000')
    records[1].update(bcx=1,land_base_pp='3.750')
    a=expected_card(records, details_by_id={7:{'rarity':1}})
    b=expected_card(records[::-1], details_by_id={7:{'rarity':1}})
    assert a['base_pp']==b['base_pp']==1128.75
    records[1]['land_base_pp']='3.751'
    assert expected_card(records[::-1], details_by_id={7:{'rarity':1}})['base_pp']==1128.75
    run_in_module_context(SETUP+r"""
p.expected.base_pp=7500.000000000001;
if(!chainResultMatches(p,{bcx:3,level:10,base_pp:7500}))throw Error('binary residue held');
if(chainResultMatches(p,{bcx:3,level:10,base_pp:7500.001}))throw Error('third decimal mismatch released');
p.expected.base_pp=39999.99942857143;
if(chainResultMatches(p,{bcx:3,level:10,base_pp:40000}))throw Error('maintainer decision bypassed');
""",entry=STATIC/'checkout.js')


def test_b05_absent_stake_class_retains_observed_future_end():
    from ceres.routes_combine_chain import _verdicts
    record=rows()[0];record['stake_end_date']='2099-10-05T00:00:00Z'
    verdict=_verdicts([record])[record['uid']]
    assert verdict['ready_at'] is not None and not verdict['ready']
    record['stake_type_uid']='unknown'
    assert _verdicts([record])[record['uid']]['ready_at'] is None


def test_b02_string_result_cached_once(monkeypatch,tmp_path):
    from ceres import combine_evidence
    from ceres.config import CeresConfig
    import ceres.routes_op_preview as route
    from tests.ceres.test_op_verify_combine import _request
    records=rows();config=CeresConfig(root=tmp_path,player='alice')
    # Build against the same raw shape through the real route, with a local collection stub.
    from ceres import routes_combine as build_route, routes_combine_chain as chain_route
    monkeypatch.setattr(chain_route, 'load_details_by_id', lambda: {7: {'rarity': 1}})
    monkeypatch.setattr(build_route.client_limits, 'limit', lambda *args: None)
    monkeypatch.setattr(build_route, '_live_collection', lambda *args: ({'cards': records, 'coverage': {'collection': {'complete': True}}}, '2026-10-05'))
    expected = expected_card(records, details_by_id={7: {'rarity': 1}})
    copies = [dict(uid=r['uid'], asset_ref=r['asset_ref'], asset=r, bcx=r['bcx'], kind='free') for r in records]
    restored = roundtrip(copies, expected)['chains']['cc-shape']
    witness = dict(state='ready', card=records[0], inputs=[dict(uid=r['uid'], cc=r['bcx']) for r in records], expected=restored['expected'])
    built = json.loads(build_route.combine_build(dict(uids=[r['uid'] for r in records], plan_review=witness, nonce='rawnonce'), _request(config)).body)
    assert built['recovery']['payload']['records'][0]['land_base_pp']=='1000.000'
    roundtrip(copies, expected, built['recovery'])
    after=[dict(records[0],bcx=959,land_base_pp='1000.000')]
    monkeypatch.setattr(route.capture_mod,'latest_snapshot',lambda *args: ({'cards':after},None))
    monkeypatch.setattr(route.capture_mod,'load_card_pool',lambda report:report)
    monkeypatch.setattr(route.capture_mod,'is_running',lambda *args:False)
    captures=[]
    monkeypatch.setattr(route.capture_mod,'start_refresh',lambda *args: captures.append(True) or True)
    monkeypatch.setattr(route,'verify_chain',lambda *args:{'status':'chain_verified'})
    monkeypatch.setattr(route,'game_verdict',lambda *args,**kwargs:{'status':'game_accepted'})
    monkeypatch.setattr(route,'SplinterlandsClient',lambda:SimpleNamespace(collection=lambda account:{'cards':after}))
    op=built['operation']
    for _ in range(2):
        result=json.loads(route.op_verify(dict(tx_id='a'*40,operations=[op],plan_combine=True),_request(config)).body)
        assert result['state']=='domain-confirmed'
        assert result['combine_result']['land_base_pp']==1000
    assert len(captures)==1
    assert list((tmp_path/'combine_evidence').glob('*.verified.json'))


@pytest.mark.parametrize('raw', [None, 'NaN', 'Infinity', '1e999', '<img>', '', -1, 10**20])
def test_b02_malformed_result_remains_held(raw):
    script = SETUP + "const raw=" + json.dumps(raw) + r""";
(async()=>{
await saveChain(p.id,{...p,state:'combine_signed',combine:{tx_id:'known',nonce:'nonce',operation:makeBuild(p).operation}});
fetch=async()=>({ok:true,json:async()=>({state:'domain-confirmed',combine_result:{uid:p.inputs[0].uid,bcx:3,level:2,land_base_pp:raw}})});
settleCombineRefresh=async()=>{};await verifyPlanCombine(p.id);await resolvePlanMismatch(p.id,true);
if(chainPlans()[p.id].problem?.code!=='combine_mismatch')throw Error('malformed facts released');
await restoreStagePlans();if(!PLANS.open||chainPlans()[p.id].problem?.code!=='combine_mismatch')throw Error('held result not recoverable');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    run_in_module_context(script, entry=STATIC/'checkout.js')
