""" irreversible combine gates, offline throughout."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from tests.ceres.test_sign_order import SETUP, STATIC
from tests.ceres.vm_context import run_in_module_context


def test_wallet_decline_ambiguity_stale_and_reload():
    run_in_module_context(SETUP + r"""
(async()=>{
fetch=async()=>({ok:true,json:async()=>makeBuild(p)});
let signs=0;beginSharedOperation=async()=>{signs++;throw Object.assign(new Error('declined'),{broadcasted:false})};
const review={id:p.id,witness:JSON.stringify(chainSignWitness(p)),build:makeBuild(p)};
await signPlanCombine(review);
if(chainPlans()[p.id].state!=='ready'||chainPlans()[p.id].combine||chainPlans()[p.id].problem)throw Error('decline mutated plan');
fetch=async()=>{const b=makeBuild(p);b.preview.hazard.rows[0].level=5;return {ok:true,json:async()=>b}};
await signPlanCombine(review);if(signs!==1||chainPlans()[p.id].problem.code!=='combine_refused')throw Error('changed copy signed');
saveChain(p.id,p);fetch=async()=>({ok:true,json:async()=>makeBuild(p)});
beginSharedOperation=async()=>{signs++;throw Object.assign(new Error('timeout'),{ambiguousBroadcast:true,txId:'known-tx'})};
await signPlanCombine(review);await signPlanCombine(review);
if(signs!==2||chainPlans()[p.id].state!=='combine_signed'||readyPlanCombines().length)throw Error('ambiguous resend');
const stored=JSON.parse(localStorage.getItem('ceres:stage-plan:v1:alice')).chains[p.id];
if(stored.combine.tx_id!=='known-tx'||!stored.combine.nonce||!stored.combine.signed_at)throw Error('recovery missing');
PLANS.chains={[p.id]:stored};let reads=0;
fetch=async(url,opts)=>{reads++;if(!url.includes('/api/op-verify')||JSON.parse(opts.body).tx_id!=='known-tx')throw Error('scan');return {ok:true,json:async()=>({state:'domain-pending'})}};
await verifyPlanCombine(p.id);if(reads!==1||chainPlans()[p.id].state!=='combine_signed')throw Error('recovery');
for(const state of ['planned','cooling','combined','done','cancelled']) {const x=makePlan();x.state=state;if(state==='planned'||state==='cooling'){x.inputs[0].kind='cooling';x.inputs[0].ready_at=null}if(chainCanCombine(x))throw Error(state)}
const x=makePlan();x.inputs.pop();if(chainCanCombine(x))throw Error('one input');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'checkout.js')


def plan_records():
    rows = [dict(uid='C-A', card_detail_id=7, edition=4, gold=False, bcx=2, level=1, land_base_pp=200, player='alice'),
            dict(uid='C-B', card_detail_id=7, edition=4, gold=False, bcx=1, level=1, land_base_pp=100, player='alice')]
    review = dict(state='ready', card=dict(card_detail_id=7, edition=4, gold=False), inputs=[dict(uid=row['uid'],cc=row['bcx']) for row in rows],
                  expected=dict(survivor_uid='C-A',bcx=3,level=2,base_pp=300))
    return rows, review


@pytest.mark.parametrize('change', ['owner','missing_owner','cc','printing','foil','staked','cooling','survival','locked','bad_date','not_ready','result','duplicate'])
def test_live_build_rejects_changed_or_unready_inputs(monkeypatch, change):
    import ceres.routes_combine_chain as route
    rows, review = plan_records()
    monkeypatch.setattr(route, 'load_details_by_id', lambda: {})
    monkeypatch.setattr(route, 'expected_card', lambda *a, **k: dict(survivor_uid='C-A',bcx=3,level=2,base_pp=300))
    if change == 'owner': rows[0]['player']='bob'
    elif change == 'missing_owner': del rows[0]['player']
    elif change == 'cc': rows[0]['bcx']=3
    elif change == 'printing': rows[0]['card_detail_id']=8
    elif change == 'foil': rows[0]['gold']=True
    elif change == 'staked': rows[0]['stake_ref_uid']='plot'
    elif change == 'cooling': rows[0]['stake_end_date']='2099-01-01'
    elif change == 'survival': rows[0]['survival_date']='2099-01-01'
    elif change == 'locked': rows[0]['lock_days']=3
    elif change == 'bad_date': rows[0]['stake_end_date']='unknown'
    elif change == 'not_ready': review['state']='cooling'
    elif change == 'result': review['expected']['base_pp']=299
    elif change == 'duplicate': review['inputs'][1]=review['inputs'][0]
    with pytest.raises(HTTPException): route.validate_plan_combine(review,rows,'alice','2026-10-05')


def test_plan_live_validation_accepts_exact_witness(monkeypatch):
    import ceres.routes_combine_chain as route
    rows, review = plan_records()
    monkeypatch.setattr(route, 'load_details_by_id', lambda: {})
    monkeypatch.setattr(route, 'expected_card', lambda *a, **k: review['expected'])
    assert route.validate_plan_combine(review,rows,'alice','2026-10-05') == review['expected']


def test_verified_result_resume_is_by_known_id_and_captures_once(monkeypatch, tmp_path):
    import json
    import ceres.routes_op_preview as route
    from ceres import combine_evidence
    from ceres.config import CeresConfig
    from tests.ceres.test_op_verify_combine import _request
    config = CeresConfig(root=tmp_path, player='alice')
    rows, _review = plan_records()
    combine_evidence.save(config,'nonce',account='alice',uids=['C-A','C-B'],records=rows)
    calls=[]
    monkeypatch.setattr(route, 'verify_chain', lambda ops, tx: calls.append(('by_id', tx)) or {'status':'chain_verified'})
    monkeypatch.setattr(route, 'game_verdict', lambda tx, **kw: {'status':'game_accepted'})
    monkeypatch.setattr(route.capture_mod, 'is_running', lambda player: False)
    monkeypatch.setattr(route.capture_mod, 'start_refresh', lambda *a: calls.append(('capture',)) or True)
    monkeypatch.setattr(route, 'SplinterlandsClient', lambda: SimpleNamespace(collection=lambda player: {'cards':[dict(rows[1],bcx=3,level=2,land_base_pp=300)]}))
    op=['custom_json',dict(id='sm_combine_cards',required_auths=[],required_posting_auths=['alice'],json=json.dumps(dict(cards=['C-A','C-B'],n='nonce',app='ceres/test')))]
    body=dict(tx_id='a'*40,operations=[op],plan_combine=True)
    result=json.loads(route.op_verify(body,_request(config)).body)
    assert result['combine_result']['uid']=='C-B'
    assert result['combine_observed_at']
    assert json.loads(route.op_verify(body,_request(config)).body)==result
    assert calls==[('by_id','a'*40),('capture',)]


def test_storage_failure_never_opens_wallet():
    run_in_module_context(SETUP + r"""
(async()=>{
fetch=async()=>({ok:true,json:async()=>makeBuild(p)});
let calls=0;beginSharedOperation=async()=>{calls++};
localStorage.setItem=()=>{throw Error('quota')};
await signPlanCombine({id:p.id,witness:JSON.stringify(chainSignWitness(p)),build:makeBuild(p)});
if(calls)throw Error('unsigned record not durable');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'checkout.js')


@pytest.mark.parametrize("delta", [dict(bcx=4), dict(land_base_pp=301)])
def test_accepted_mismatch_is_exposed_for_hold_without_false_domain_success(monkeypatch, tmp_path, delta):
    import json
    import ceres.routes_op_preview as route
    from ceres import combine_evidence
    from ceres.config import CeresConfig
    from tests.ceres.test_op_verify_combine import _request
    config=CeresConfig(root=tmp_path,player='alice')
    rows, _ = plan_records()
    combine_evidence.save(config,'mismatch',account='alice',uids=['C-A','C-B'],records=rows)
    monkeypatch.setattr(route,'verify_chain',lambda *a: {'status':'chain_verified'})
    monkeypatch.setattr(route,'game_verdict',lambda *a,**k: {'status':'game_accepted'})
    monkeypatch.setattr(route.capture_mod,'is_running',lambda p: False)
    monkeypatch.setattr(route.capture_mod,'start_refresh',lambda *a: True)
    observed=dict(rows[1],bcx=3,level=2,land_base_pp=300);observed.update(delta)
    monkeypatch.setattr(route,'SplinterlandsClient',lambda: SimpleNamespace(collection=lambda p: {'cards':[observed]}))
    op=['custom_json',dict(id='sm_combine_cards',required_auths=[],required_posting_auths=['alice'],json=json.dumps(dict(cards=['C-A','C-B'],n='mismatch',app='ceres/test')))]
    result=json.loads(route.op_verify(dict(tx_id='b'*40,operations=[op],plan_combine=True),_request(config)).body)
    assert result['combine_result']==observed
    if 'bcx' in delta:
        assert result['domain']['status']=='domain_pending'
        assert result['state']!='domain-confirmed'


def test_device_merge_cannot_cancel_signed_combine_or_resurrect_decline():
    run_in_module_context(SETUP + r"""
const signed={...p,state:'combine_signed',updated_at:'2026-10-05T01:00:00Z',combine:{nonce:'n',tx_id:'tx'}};
const cancelled={...p,state:'cancelled',updated_at:'2026-10-05T02:00:00Z',inputs:[]};
const merge=(a,b)=>mergeStaged({chains:{[p.id]:a}},{chains:{[p.id]:b}}).chains[p.id];
if(merge(signed,cancelled).state!=='combine_signed'||merge(cancelled,signed).inputs.length!==2)throw Error('signed plan cancelled');
const declined={...p,state:'ready',cleared_combine_nonce:'n',updated_at:'2026-10-05T03:00:00Z'};
if(merge(signed,declined).state!=='ready'||merge(declined,signed).combine)throw Error('decline resurrected');
""", entry=STATIC / 'checkout.js')
