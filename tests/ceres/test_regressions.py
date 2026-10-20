""": positive offline regressions converted from the Astra defect probes."""
import pytest
from tests.ceres.test_sign_order import SETUP, STATIC
from tests.ceres.vm_context import run_in_module_context


def test_second_tab_cannot_replace_pending_nonce_or_dispatch_same_inputs():
    run_in_module_context(SETUP + r"""
(async()=>{
let signs=[];let serial=0;
fetch=async()=>{const b=makeBuild(p);b.operation[1].json=JSON.stringify({cards:p.inputs.map(i=>i.uid),n:'nonce-'+(++serial),app:'ceres/test'});return {ok:true,json:async()=>b}};
beginSharedOperation=async args=>{signs.push(JSON.parse(args.operations[0][1].json));throw Object.assign(new Error('timeout'),{ambiguousBroadcast:true,txId:'first-tx'})};
const stale=JSON.parse(JSON.stringify(p));const staleRevision=PLANS.revision;
const review={id:p.id,witness:JSON.stringify(chainSignWitness(p)),build:makeBuild(p)};
await signPlanCombine(review);
if(chainPlans()[p.id].state!=='combine_signed')throw Error('fixture');
// The other tab retains its old memory/revision; storage contains first-tab pending combine.
PLANS.chains={[p.id]:stale};PLANS.revision=staleRevision;PLANS.lastSig=null;
await new Promise(resolve=>setTimeout(resolve,5));
await signPlanCombine(review);
if(signs.length!==1)throw Error('Expected duplicate dispatch defect: '+JSON.stringify(signs));
if(chainPlans()[p.id].combine.nonce!==signs[0].n)throw Error('pending nonce replaced');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'checkout.js')


def test_saved_plan_level_is_escaped():
    run_in_module_context(SETUP + r"""
p.card.level='<img src=x onerror="globalThis.auditXss=1">';
const html=chainCardHtml(p.id,p);
if(html.includes(p.card.level))throw Error('Expected unescaped saved level');
""", entry=STATIC / 'checkout.js')


def test_undo_restore_refuses_detached_chain_unstake():
    run_in_module_context(SETUP + r"""
(async()=>{
p.inputs[0].kind='staked';p.inputs[0].source={deed_uid:'d',slot_index:0};p.state='planned';saveChain(p.id,p);
BOARD.plots=[{deed_uid:'d'}];editorGroups=()=>[];removalImpact=async()=>({});applyRemovalImpactToStage=()=>{};
const entry={deed_uid:'d',group_id:'worker',slot_index:0,action:'unstake',asset_ref:p.inputs[0].uid,chain_id:p.id,chain_input_uid:p.inputs[0].uid};
rememberUndone(entry);leaveChainUnstake(entry);await restoreUndoneChange('d','worker',0);
if(CERES_STAGE.some(e=>e.deed_uid==='d'))throw Error('detached linked unstake restored');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'checkout.js')


def test_sync_response_preserves_new_pending_combine():
    result = run_in_module_context(SETUP + r"""
(async()=>{
rememberSession('alice','offline-test',3600);
const initial=JSON.parse(localStorage.getItem('ceres:stage-plan:v1:alice'));
const remote={...initial,chains:{'cc-other':{...p,id:'cc-other'}}};
loadDocument=async()=>({exists:true,document:{payload:remote},etag:'old'});
let release;let entered;const putStarted=new Promise(r=>entered=r);
let puts=0;saveDocument=async()=>{if(++puts===1){entered();await new Promise(r=>release=r)}return {ok:true}};
restoreStagePlans=async()=>{PLANS.chains=JSON.parse(localStorage.getItem('ceres:stage-plan:v1:alice')).chains};
const syncing=syncStagedNow();await putStarted;
saveChain(p.id,{...p,state:'combine_signed',combine:{nonce:'newly-sent',tx_id:'pending-tx'}});
release();await syncing;
if(chainPlans()[p.id].state!=='combine_signed'||chainPlans()[p.id].combine?.nonce!=='newly-sent')throw Error('pending record lost');
console.log('sync-complete');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'app.js')

    assert 'sync-complete' in result.stdout


def test_unavailable_copy_can_be_excluded_from_remaining_two():
    run_in_module_context(SETUP + r"""
p.inputs.push({uid:'C-lost',cc:1,kind:'free',status:'unavailable'});
p.problem=null;p.state='ready';saveChain(p.id,p);
if(chainCanCombine(chainPlans()[p.id]))throw Error('Expected blocking copy_unavailable');
const html=chainCardHtml(p.id,chainPlans()[p.id]);
if(!html.includes('data-chain-input="2"'))throw Error('Unexpected ability to remove unavailable input');
chainPlans()[p.id].inputs[2].status='left_out';if(deriveChain(chainPlans()[p.id]).problem)throw Error('exclusion still blocks');
""", entry=STATIC / 'checkout.js')


def test_server_rejects_saved_html_level_and_incomplete_combine_record():
    from ceres.player_space import validate_payload, SpaceError
    from tests.ceres.test_chain_store_sync import plan
    value=plan('combine_signed')
    value['card']['level']='<img src=x onerror="globalThis.auditXss=1">'
    value['combine']='malformed'
    with pytest.raises(SpaceError):
        validate_payload('staged', {'chains': {'cc-inject': value}})


@pytest.mark.parametrize("state,level", [("domain-pending", None), ("domain-confirmed", None), ("domain-pending", 2)])
def test_mismatch_result_missing_level_is_held(state, level):
    script = SETUP + r"""
(async()=>{
p.state='combine_signed';p.combine={tx_id:'known-tx',nonce:'n',operation:makeBuild(p).operation};saveChain(p.id,p);
CERES_STAGE.push({chain_id:p.id,action:'stake',deed_uid:'d',slot_index:0,_asset:{planned:true},cooldown:{ready:false}});
fetch=async()=>({ok:true,json:async()=>({state:'domain-pending',combine_result:{uid:'C-input-b',bcx:3,land_base_pp:300},combine_observed_at:'2026-10-04',combine_refresh:{triggered:true}})});
settleCombineRefresh=async(_r,{after})=>after();await verifyPlanCombine(p.id);
if(chainPlans()[p.id].problem?.code!=='combine_mismatch'||!stageSetAside(CERES_STAGE[0]))throw Error('Expected incomplete result release');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    script = script.replace("state:'domain-pending'", "state:" + repr(state))
    if level is not None:
        script = script.replace("bcx:3,land_base_pp:300", "bcx:3,level:2,land_base_pp:300")
    run_in_module_context(script, entry=STATIC / 'checkout.js')


def test_null_transaction_recovery_never_requests_account_history(monkeypatch):
    import json
    import ceres.routes_op_preview as route
    calls=[]
    monkeypatch.setattr(route,'_rpc',lambda method,params:(calls.append((method,params)) or {'history':[]}))
    route._request_identity_inclusion('alice',dict(tx_id=None,created_at='2020-01-01T00:00:00Z',claim_ids=[json.dumps(['alice','custom_json','sm_combine_cards','n'])]))
    assert calls == []


def test_newer_automatic_refresh_cannot_resurrect_cancellation():
    run_in_module_context(SETUP + r"""
const cancelled={...p,state:'cancelled',updated_at:'2026-10-05T10:00:00Z'};
const refreshed={...p,updated_at:'2026-10-05T10:00:01Z'};
const merged=mergeStaged({chains:{[p.id]:refreshed}},{chains:{[p.id]:cancelled}});
if(merged.chains[p.id].state!=='cancelled')throw Error('Expected resurrection');
""", entry=STATIC/'checkout.js')


def test_account_switch_preserves_successful_combine_transaction_id():
    run_in_module_context(SETUP + r"""
(async()=>{
fetch=async()=>({ok:true,json:async()=>makeBuild(p)});
beginSharedOperation=async opts=>{CURRENT_PLAYER='bob';opts.onResponse({success:true,result:{id:'known-after-switch'}});return {txId:'known-after-switch',entryId:'receipt'}};
await signPlanCombine({id:p.id,witness:JSON.stringify(chainSignWitness(p)),build:makeBuild(p)});
const saved=JSON.parse(localStorage.getItem('ceres:stage-plan:v1:alice')).chains[p.id];
if(saved.state!=='combine_signed'||saved.combine.tx_id!=='known-after-switch')throw Error('Expected lost tx id');
CURRENT_PLAYER='alice';PLANS.chains={[p.id]:saved};let calls=0;fetch=async()=>{calls++;throw Error('unexpected')};
await verifyPlanCombine(p.id);
if(calls!==1||!chainCardHtml(p.id,saved).includes('data-upgrade-action="check-combine"'))throw Error('Expected unrecoverable pending plan');
})().catch(e=>{console.error(e);process.exitCode=1});
""",entry=STATIC/'checkout.js')


def test_existing_plan_evidence_still_verifies_after_one_day(monkeypatch,tmp_path):
    import json
    from types import SimpleNamespace
    from ceres import combine_evidence
    import ceres.routes_op_preview as route
    from ceres.config import CeresConfig
    from tests.ceres.test_op_verify_combine import _request
    from tests.ceres.test_combine_safety import plan_records
    rows,_=plan_records();config=CeresConfig(root=tmp_path,player='alice')
    combine_evidence.save(config,'oldnonce',account='alice',uids=['C-A','C-B'],records=rows)
    monkeypatch.setattr(combine_evidence.time,'time',lambda: 10**12)
    after=[dict(rows[1],bcx=3,level=2,land_base_pp=300)]
    monkeypatch.setattr(route.capture_mod,'latest_snapshot',lambda *args: ({'cards':after},None))
    monkeypatch.setattr(route.capture_mod,'load_card_pool',lambda report:report)
    monkeypatch.setattr(route.capture_mod,'is_running',lambda *args:False)
    monkeypatch.setattr(route.capture_mod,'start_refresh',lambda *args:True)
    monkeypatch.setattr(route,'verify_chain',lambda *args:{'status':'chain_verified'})
    monkeypatch.setattr(route,'game_verdict',lambda *args,**kwargs:{'status':'game_accepted'})
    monkeypatch.setattr(route,'SplinterlandsClient',lambda:SimpleNamespace(collection=lambda account:{'cards':after}))
    op=['custom_json',dict(id='sm_combine_cards',required_auths=[],required_posting_auths=['alice'],json=json.dumps(dict(cards=['C-A','C-B'],n='oldnonce',app='ceres/test')))]
    result=json.loads(route.op_verify(dict(tx_id='a'*40,operations=[op],plan_combine=True),_request(config)).body)
    assert result['combine_result']['uid']=='C-B'
    assert result['state']=='domain-confirmed'
    assert (tmp_path/'combine_evidence'/'oldnonce.json').exists() # retained during the resolved-result cache window

