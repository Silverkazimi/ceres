""": missing mutation gates and lifecycle regressions, entirely offline."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from tests.ceres.test_sign_order import SETUP, STATIC
from tests.ceres.vm_context import run_in_module_context


def test_reviewed_printing_compares_witness_not_only_copies(monkeypatch):
    import ceres.routes_combine_chain as route
    from tests.ceres.test_combine_safety import plan_records
    rows, review = plan_records()
    for row in rows:
        row['card_detail_id'] = 8
    monkeypatch.setattr(route, 'load_details_by_id', lambda: {})
    monkeypatch.setattr(route, 'expected_card', lambda *a, **k: review['expected'])
    with pytest.raises(HTTPException, match='printing'):
        route.validate_plan_combine(review, rows, 'alice', '2026-10-05')


def test_build_route_always_uses_fresh_collection(monkeypatch, tmp_path):
    import ceres.routes_combine as route
    from ceres.config import CeresConfig
    from tests.ceres.test_op_verify_combine import _request
    calls = []
    def prepare(*args, fresh=False):
        calls.append(fresh)
        return 'alice', {}, [], SimpleNamespace(json_payload={}, as_keychain_tuple=lambda: [])
    monkeypatch.setattr(route, '_prepare', prepare)
    route.combine_build({}, _request(CeresConfig(root=tmp_path, player='alice')))
    assert calls == [True]


@pytest.mark.parametrize('mutation', ["b.operation[1].json=JSON.stringify({cards:['C-other','C-input-b'],n:'nonce'})", "b.operation[1].required_auths=['alice']"])
def test_client_rejects_wrong_operation_inputs_and_active_auth(mutation):
    run_in_module_context(SETUP + r"""
(async()=>{
const b=makeBuild(p);
""" + mutation + r""";
fetch=async()=>({ok:true,json:async()=>b});
let refused=false;try{await buildPlanCombine(p)}catch(_){refused=true}
if(!refused)throw Error('unsafe operation accepted');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC/'checkout.js')


def test_pending_attempt_and_unconfirmed_unstake_block_readiness():
    run_in_module_context(SETUP + r"""
p.combine={nonce:'held'};if(chainCanCombine(p))throw Error('pending attempt rearmed');
p.combine=null;p.inputs[0].kind='staked';p.inputs[0].ready_at='2020-01-01';p.inputs[0].unstake={tx_id:'u',signed_at:'2020-01-01'};
if(chainInputReady(p.inputs[0])||chainCanCombine(p))throw Error('unconfirmed unstake released');
p.inputs[0].unstake.confirmed_at='2020-01-02';if(!chainCanCombine(p))throw Error('confirmed fixture');
""", entry=STATIC/'checkout.js')


def test_no_web_locks_fails_closed_and_no_id_probe_makes_no_fetch():
    run_in_module_context(SETUP + r"""
(async()=>{
navigator.locks=undefined;let calls=0;
fetch=async()=>({ok:true,json:async()=>makeBuild(p)});beginSharedOperation=async()=>{calls++};
await signPlanCombine({id:p.id,witness:JSON.stringify(chainSignWitness(p)),build:makeBuild(p)});
if(calls)throw Error('unlocked irreversible dispatch');
fetch=async()=>{calls++;throw Error('network')};await probeAdmission('alice',{txId:null});if(calls)throw Error('no-id fetch');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC/'checkout.js')


def test_terminal_compaction_preserves_unresolved_and_prevents_resurrection():
    result = run_in_module_context(SETUP + r"""
const chains={};for(let i=0;i<250;i++)chains['cc-'+i]={...p,id:'cc-'+i,state:'cancelled'};
chains[p.id]={...p,state:'combine_signed',combine:{nonce:'held',operation:makeBuild(p).operation}};
PLANS.chains=chains;PLANS.lastSig=null;persistStagePlans();
const compact=JSON.parse(localStorage.getItem('ceres:stage-plan:v1:alice')).chains;
if(Object.values(compact).filter(x=>!x.tombstone).length!==51||!compact[p.id].combine)throw Error('compaction lost pending');
const id=Object.keys(compact).find(id=>compact[id].tombstone);
if(!mergeStaged({chains:{[id]:{...p,updated_at:'2099-01-01'}}},{chains:compact}).chains[id].tombstone)throw Error('resurrection');
console.log(JSON.stringify({chains:compact}));
""", entry=STATIC/'checkout.js')
    from ceres.player_space import validate_payload
    validate_payload('staged', json.loads(result.stdout.strip().splitlines()[-1]))


@pytest.mark.parametrize('field,value', [('card', {'level':'<img src=x onerror=alert(1)>'}), ('expected', {'level':float('inf')}), ('combine', 'bad'), ('result', []), ('inputs', [None])])
def test_server_nested_shapes_fail_closed(field, value):
    from ceres.player_space import validate_payload, SpaceError
    from tests.ceres.test_chain_store_sync import plan
    saved=plan();saved[field]=value
    with pytest.raises(SpaceError):
        validate_payload('staged', {'chains':{'cc-bad':saved}})


def test_client_nested_shapes_rejected():
    run_in_module_context(SETUP + r"""
for(const [key,value] of [['card',{level:'<img>'}],['expected',{level:Infinity}],['combine','bad'],['result',[]],['inputs',[null]]]){
 if(validChainPlan({...p,[key]:value}))throw Error('malformed '+key);
}
""", entry=STATIC/'checkout.js')


def test_arrival_hides_identifiers():
    run_in_module_context(SETUP + r"""
const html=combineArrivalConsumedCardHtml({uid:'SECRET-UID',name:'Worker'});
if(html.includes('SECRET-UID'))throw Error('visible identifier');
""", entry=STATIC/'checkout.js')


def test_logical_width_hint_matches_sign_controls():
    css=(STATIC/'ceres.css').read_text()
    assert 'html.ceres-lw-le-1279 .chain-phone { display: inline; }' in css


def test_two_survivors_remain_pending_even_if_one_has_total_cc():
    from fides_cards.verify import CardVerificationSpec
    rows=[dict(uid='A',card_detail_id=7,bcx=5,level=1,player='alice'),dict(uid='B',card_detail_id=7,bcx=3,level=1,player='alice')]
    observed=[dict(rows[0],bcx=8),rows[1]]
    checker=CardVerificationSpec(lambda:{'cards':observed}, expected_records=rows, intent='alice')
    op=dict(id='sm_combine_cards',required_auths=[],required_posting_auths=['alice'],json=json.dumps(dict(cards=['A','B'],n='n')))
    assert checker.check_domain('test',[op])['status']=='domain_pending'


def test_recovery_after_cache_expiry_and_tamper_rejection(monkeypatch,tmp_path):
    from ceres import combine_evidence
    import ceres.routes_op_preview as route
    from ceres.config import CeresConfig
    from tests.ceres.test_op_verify_combine import _request
    from tests.ceres.test_combine_safety import plan_records
    rows, review=plan_records(); config=CeresConfig(root=tmp_path,player='alice')
    envelope=combine_evidence.recovery_envelope(config,'oldnonce',account='alice',records=rows,expected=review['expected'])
    combine_evidence.save(config,'oldnonce',account='alice',uids=['C-A','C-B'],records=rows)
    monkeypatch.setattr(combine_evidence.time,'time',lambda:10**12)
    combine_evidence.sweep(config)
    assert not (tmp_path/'combine_evidence'/'oldnonce.json').exists()
    assert combine_evidence.recover(config,envelope,account='alice',nonce='oldnonce',uids=['C-A','C-B'])==rows
    bad=deepcopy(envelope);bad['payload']['expected']['bcx']=4
    assert combine_evidence.recover(config,bad,account='alice',nonce='oldnonce',uids=['C-A','C-B']) is None
    altered=deepcopy(envelope);altered['payload']['expected']['base_pp']=301
    assert combine_evidence.recover(config,altered,account='alice',nonce='oldnonce',uids=['C-A','C-B']) is None
    assert combine_evidence.recover(config,envelope,account='bob',nonce='oldnonce',uids=['C-A','C-B']) is None
    after=[dict(rows[1],bcx=3,level=2,land_base_pp=300)]
    monkeypatch.setattr(route.capture_mod,'latest_snapshot',lambda *a: ({'cards':after},None))
    monkeypatch.setattr(route.capture_mod,'load_card_pool',lambda report:report)
    monkeypatch.setattr(route.capture_mod,'is_running',lambda *a:False)
    monkeypatch.setattr(route.capture_mod,'start_refresh',lambda *a:True)
    monkeypatch.setattr(route,'verify_chain',lambda *a:{'status':'chain_verified'})
    monkeypatch.setattr(route,'game_verdict',lambda *a,**k:{'status':'game_accepted'})
    monkeypatch.setattr(route,'SplinterlandsClient',lambda:SimpleNamespace(collection=lambda account:{'cards':after}))
    op=['custom_json',dict(id='sm_combine_cards',required_auths=[],required_posting_auths=['alice'],json=json.dumps(dict(cards=['C-A','C-B'],n='oldnonce',app='ceres/test')))]
    result=json.loads(route.op_verify(dict(tx_id='a'*40,operations=[op],plan_combine=True,combine_recovery=envelope),_request(config)).body)
    assert result['state']=='domain-confirmed'
    assert result['combine_result']['uid']=='C-B'
    monkeypatch.setattr(combine_evidence.time,'time',lambda:10**12+90000)
    combine_evidence.sweep(config)
    assert not list((tmp_path/'combine_evidence').glob('*.json'))


def test_same_nonce_sync_cannot_erase_learned_transaction_id():
    run_in_module_context(SETUP + r"""
const pending={...p,state:'combine_signed',combine:{nonce:'n',tx_id:null}};
const known={...pending,combine:{nonce:'n',tx_id:'known'}};
for(const [local,saved] of [[pending,known],[known,pending]]){
 if(mergeStaged({chains:{[p.id]:local}},{chains:{[p.id]:saved}}).chains[p.id].combine.tx_id!=='known')throw Error('transaction id lost');
}
""", entry=STATIC/'checkout.js')


def test_documented_pp_rounding_gap_stays_strict_until_owner_decision():
    from ceres.combine_chain import expected_card
    cards=[dict(uid='G-A',card_detail_id=1,edition=0,card_set='alpha',rarity='rare',gold=True,bcx=14,bcx_max=17,land_base_pp=32941.176),
           dict(uid='G-B',card_detail_id=1,edition=0,card_set='alpha',rarity='rare',gold=True,bcx=3,bcx_max=17,land_base_pp=7058.824)]
    expected=expected_card(cards)
    assert round(expected['base_pp'],3)==39999.999
    run_in_module_context(SETUP + f"p.expected={{bcx:17,base_pp:{expected['base_pp']}}};if(chainResultMatches(p,{{bcx:17,base_pp:40000,level:8}}))throw Error('unapproved tolerance');", entry=STATIC/'checkout.js')


def test_restore_holds_malformed_plan_without_rendering_or_overwriting():
    run_in_module_context(SETUP + r"""
(async()=>{
const key='ceres:stage-plan:v1:alice';const doc=JSON.parse(localStorage.getItem(key));doc.chains[p.id].inputs=[null];
const raw=JSON.stringify(doc);localStorage.setItem(key,raw);let notices=0;stageToast=()=>{notices++};
await restoreStagePlans();
if(stagePlansReady()||Object.keys(chainPlans()).length||localStorage.getItem(key)!==raw||notices!==1)throw Error('malformed restore was not held');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC/'checkout.js')


def test_other_plan_id_with_overlapping_card_cannot_open_wallet():
    run_in_module_context(SETUP + r"""
(async()=>{
const key='ceres:stage-plan:v1:alice';const doc=JSON.parse(localStorage.getItem(key));
doc.chains['cc-other']={...p,id:'cc-other',state:'combine_signed',combine:{nonce:'existing'},inputs:[p.inputs[1]]};
localStorage.setItem(key,JSON.stringify(doc));let signs=0;
fetch=async()=>({ok:true,json:async()=>makeBuild(p)});beginSharedOperation=async()=>{signs++};
await signPlanCombine({id:p.id,witness:JSON.stringify(chainSignWitness(p)),build:makeBuild(p)});
if(signs)throw Error('overlapping different plan signed');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC/'checkout.js')


def test_unresolved_evidence_survives_age_until_verified(monkeypatch,tmp_path):
    from ceres import combine_evidence
    from ceres.config import CeresConfig
    config=CeresConfig(root=tmp_path,player='alice')
    rows=[{'uid':'A','bcx':2},{'uid':'B','bcx':1}]
    combine_evidence.save(config,'legacy',account='alice',uids=['A','B'],records=rows)
    monkeypatch.setattr(combine_evidence.time,'time',lambda:10**12)
    assert combine_evidence.load(config,'legacy',account='alice',unresolved=True)==rows
    combine_evidence.sweep(config)
    assert combine_evidence.load(config,'legacy',account='alice',unresolved=True)==rows
    combine_evidence.save_plan_result(config,'legacy',account='alice',tx_id='known',operations=[],result={})
    monkeypatch.setattr(combine_evidence.time,'time',lambda:10**12+90000)
    combine_evidence.sweep(config)
    assert not list((tmp_path/'combine_evidence').glob('*.json'))


def test_unavailable_leave_out_recomputes_without_cancelling_two_remaining():
    run_in_module_context(SETUP + r"""
(async()=>{
p.inputs.push({uid:'C-lost',cc:1,kind:'free',status:'unavailable'});saveChain(p.id,p);
confirmChainRemoval=async()=> 'keep_off';let projected=[];
chainPost=async(route,body)=> route==='inputs' ? {copies:p.inputs.slice(0,2).map(i=>({uid:i.uid,eligible:true,kind:'free',bcx:i.cc})),observed_at:'2026-10-05'} : (projected=body.uids,{expected:p.expected});
await leaveUpgradeInputs(p.id,2);
if(chainPlans()[p.id].state!=='ready'||chainPlans()[p.id].inputs[2].status!=='left_out'||projected.length!==2||!chainCanCombine(chainPlans()[p.id]))throw Error('smaller selection not ready');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC/'checkout.js')


def test_incomplete_saved_result_cannot_be_restored_as_verified():
    from ceres.player_space import validate_payload,SpaceError
    from tests.ceres.test_chain_store_sync import plan
    value=plan('combined');value['result']={'uid':'A','bcx':2,'base_pp':200}
    with pytest.raises(SpaceError):
        validate_payload('staged',{'chains':{'cc-bad':value}})
    run_in_module_context(SETUP + "if(validChainPlan({...p,state:'combined',result:{uid:'A',bcx:3,base_pp:300}}))throw Error('incomplete verified restore');", entry=STATIC/'checkout.js')


def test_pure_refresh_keeps_user_edit_timestamp():
    run_in_module_context(SETUP + r"""
(async()=>{
saveChain(p.id,{...p,updated_at:'2020-01-01T00:00:00Z'},{refresh:true});
chainPost=async(route,body)=> route==='inputs' ? {copies:p.inputs.map(i=>({uid:i.uid,eligible:true,kind:'free',bcx:i.cc})),observed_at:'2026-10-05'} : {expected:p.expected};
await refreshChain(p.id);
if(chainPlans()[p.id].updated_at!=='2020-01-01T00:00:00Z')throw Error('automatic refresh became a new edit');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC/'checkout.js')


def test_account_lock_callback_rereads_durable_state_before_reservation():
    run_in_module_context(SETUP + r"""
(async()=>{
let signs=0,locks=0;navigator.locks.request=async(name,fn)=>{
 if(name!=='ceres:stage-plan:alice')throw Error('wrong lock');locks++;
 const key='ceres:stage-plan:v1:alice';const doc=JSON.parse(localStorage.getItem(key));
 doc.chains['cc-intervening']={...p,state:'combine_signed',combine:{nonce:'held'},inputs:[p.inputs[0]]};localStorage.setItem(key,JSON.stringify(doc));
 return fn();
};
fetch=async()=>({ok:true,json:async()=>makeBuild(p)});beginSharedOperation=async()=>{signs++};
await signPlanCombine({id:p.id,witness:JSON.stringify(chainSignWitness(p)),build:makeBuild(p)});
if(signs||locks<1)throw Error('reservation ignored intervening locked state');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC/'checkout.js')


def test_deep_malformed_plan_is_rejected_without_recursion_failure():
    from ceres.player_space import validate_payload,SpaceError
    from tests.ceres.test_chain_store_sync import plan
    value=plan();nested={};value['extra']=nested
    for _ in range(100):
        nested['extra']={};nested=nested['extra']
    with pytest.raises(SpaceError):
        validate_payload('staged',{'chains':{'cc-deep':value}})
    run_in_module_context(SETUP + "let v={};p.extra=v;for(let i=0;i<100;i++){v.extra={};v=v.extra}if(validChainPlan(p))throw Error('deep malformed plan');", entry=STATIC/'checkout.js')


def test_next_write_deletes_expired_prepared_and_verified_cache(monkeypatch,tmp_path):
    from ceres import combine_evidence
    from ceres.config import CeresConfig
    config=CeresConfig(root=tmp_path,player='alice')
    combine_evidence.save(config,'expired',account='alice',uids=['A','B'],records=[])
    combine_evidence.save_plan_result(config,'expired',account='alice',tx_id='known',operations=[],result={})
    monkeypatch.setattr(combine_evidence.time,'time',lambda:10**12)
    combine_evidence.save(config,'new',account='alice',uids=['C','D'],records=[])
    assert sorted(path.name for path in (tmp_path/'combine_evidence').glob('*.json'))==['new.json']


def test_reference_opens_with_current_contract_and_labels_history():
    text=(STATIC.parents[1]/'library/ceres-combine-chain.md').read_text()
    assert text.startswith('# Ceres combine chain —  contract')
    assert '## Historical – implementation notes' in text
    assert 'OWNER DECISION' in text


def test_newer_nonce_cannot_replace_durable_irreversible_attempt():
    run_in_module_context(SETUP + r"""
const old={...p,state:'combine_signed',updated_at:'2020-01-01',combine:{nonce:'old',tx_id:null}};
const newer={...old,updated_at:'2099-01-01',combine:{nonce:'new',tx_id:null}};
const merged=mergeStaged({chains:{[p.id]:newer}},{chains:{[p.id]:old}});
if(merged.chains[p.id].combine.nonce!=='old')throw Error('durable nonce replaced');
""", entry=STATIC/'checkout.js')


def test_completion_uses_original_account_lock_after_switch():
    run_in_module_context(SETUP + r"""
(async()=>{
saveChain(p.id,{...p,state:'combine_signed',combine:{nonce:'held',tx_id:null}});
CURRENT_PLAYER='bob';let locks=[];navigator.locks.request=async(name,fn)=>{locks.push(name);return fn()};
await completeChainAttempt('alice',p.id,'held','known');
if(locks.join()!=='ceres:stage-plan:alice'||JSON.parse(localStorage.getItem('ceres:stage-plan:v1:alice')).chains[p.id].combine.tx_id!=='known')throw Error('completion not locked to original account');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC/'checkout.js')
