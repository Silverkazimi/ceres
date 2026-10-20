""" : independently timed inputs and kept-plan transitions."""
from pathlib import Path
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / 'ceres' / 'static'


def test_partial_signing_unknown_ends_and_latest_verdict():
    run_in_module_context(r"""
const at = Date.parse('2026-10-05T00:00:00Z');
const plan = {state:'planned',inputs:[
 {uid:'a',kind:'staked',status:'active',unstake:null},
 {uid:'b',kind:'staked',status:'active',unstake:null},
 {uid:'c',kind:'free',status:'active'}]};
if (deriveChain(plan,at).state !== 'planned') throw Error('unsigned');
plan.inputs[0].unstake={tx_id:'tx1',signed_at:'2026-10-04',confirmed_at:'2026-10-04'};
plan.inputs[0].ready_at='2026-10-06T00:00:00Z';
if (deriveChain(plan,at).state !== 'planned') throw Error('partial');
plan.inputs[1].unstake={tx_id:'tx2',signed_at:'2026-10-04'};
if (deriveChain(plan,at).state !== 'cooling' || deriveChain(plan,at).ready_at !== null) throw Error('unknown end');
plan.inputs[1].unstake.confirmed_at='2026-10-04';
plan.inputs[1].ready_at='2026-10-07T00:00:00Z';
if (deriveChain(plan,at).ready_at !== '2026-10-07T00:00:00.000Z') throw Error('latest');
if (deriveChain(plan,Date.parse('2026-10-08')).state !== 'ready') throw Error('ready');
reconcileChainCopies(plan,[{uid:'a',eligible:true,kind:'cooling',ready_at:'2026-10-09T00:00:00Z'},
 {uid:'b',eligible:true,kind:'cooling',ready_at:'2026-10-07T00:00:00Z'},
 {uid:'c',eligible:true,kind:'free'}],'2026-10-05T00:00:00Z',at);
if (plan.ready_at !== '2026-10-09T00:00:00.000Z') throw Error('later verdict must replace');
plan.inputs[0].status='left_out'; plan.inputs[1].status='unavailable';
if (deriveChain(plan,at).problem.code !== 'not_enough_cards') throw Error('minimum');
plan.state='cancelled'; if (deriveChain(plan,at).state !== 'cancelled') throw Error('tombstone');
""", entry=STATIC / 'combine-chain.js')


def test_model_never_reads_network_or_invents_cooldown():
    source = (STATIC / 'combine-chain.js').read_text()
    assert 'fetch(' not in source
    assert '72 *' not in source


def test_store_signed_confirmed_refused_records_and_linkage():
    run_in_module_context(r"""
CURRENT_PLAYER='alice'; BOARD={player:'alice',plots:[]};
PLANS.open=true; PLANS.ready=true; PLANS.account='alice';
const p=createChain({id:'cc-test',origin:{kind:'tile',asset_uid:'a'},card:{name:'Worker'},
 copies:[{uid:'a',bcx:5,kind:'staked',source:{deed_uid:'plot',slot_index:0}},{uid:'b',bcx:3,kind:'free'}],expected:{bcx:8}});
saveChain('cc-test',p);
const entry={chain_id:'cc-test',chain_input_uid:'a',deed_uid:'plot',group_id:'worker',slot_index:0,action:'unstake'};
recordChainUnstake(entry,'signed','tx-one','2026-10-05T10:00:00Z');
recordChainUnstake(entry,'confirmed','tx-one','2026-10-05T10:01:00Z');
const input=chainPlans()['cc-test'].inputs[0];
if(input.unstake.tx_id!=='tx-one'||input.unstake.signed_at!=='2026-10-05T10:00:00Z'||!input.unstake.confirmed_at||input.ready_at!==null) throw Error('timestamp or guessed end');
if(planFromEntry(entry).chain_input_uid!=='a') throw Error('lost link');
recordChainUnstake(entry,'refused','tx-one');
if(chainPlans()['cc-test'].problem.code!=='unstake_refused') throw Error('refusal');
leaveChainUnstake(entry);
if(chainPlans()['cc-test'].inputs[0].status!=='left_out') throw Error('undo did not leave out');
""", entry=STATIC / 'stage-plans.js')


def test_staging_multiple_plots_uses_ordinary_unstakes_and_keeps_free_copies():
    run_in_module_context(r"""
(async()=>{
CURRENT_PLAYER='alice';
const asset=(uid)=>({uid,asset_ref:uid,name:'Worker',bcx:2});
const a=asset('a'),b=asset('b');
const groupA={id:'worker',slots:[a]},groupB={id:'worker',slots:[b]};
BOARD={player:'alice',plots:[{deed_uid:'d1',group:groupA},{deed_uid:'d2',group:groupB}]};
PLANS.open=true;PLANS.ready=true;PLANS.account='alice';
editorGroups=(row)=>[row.group]; loadPlotDetail=async()=>{};
renderUpgradeWindow=()=>{};renderCheckoutSurface=()=>{};updateMountedRow=()=>{};
removalImpact=async(row,refs)=>({removed:refs.map(asset_ref=>({asset_ref,own_base_pp:100,own_harvest_pp:100})),notices:[]});
applyRemovalImpactToStage=()=>{};mutateEditorSlot=(row,group,index,asset)=>{group.slots[index]=asset};
closeCombineDialog=()=>{};stageToast=()=>{};
chainPost=async()=>({expected:{bcx:6,level:2,base_pp:300}});
UPGRADE_WINDOW={player:'alice',card:{name:'Worker',bcx:2,level:1},origin:{kind:'tile'},anchor:'a',selected:['a','b','c'],expected:{bcx:6},loading:false,
 copies:[{uid:'a',asset_ref:'a',bcx:2,kind:'staked',source:{deed_uid:'d1',slot_index:0}},{uid:'b',asset_ref:'b',bcx:2,kind:'staked',source:{deed_uid:'d2',slot_index:0}},{uid:'c',bcx:2,kind:'free'}]};
await stageUpgradePlan();
const plans=Object.values(chainPlans());
if(plans.length!==1||plans[0].inputs.length!==3||CERES_STAGE.length!==2) throw Error('wrong stage count');
if(CERES_STAGE.some(e=>e.action!=='unstake'||!e.chain_id||!e.chain_input_uid)) throw Error('ordinary linkage');
const saved=JSON.parse(localStorage.getItem('ceres:stage-plan:v1:alice'));
if(Object.values(saved.plans).some(e=>!e.chain_id)||Object.keys(saved.chains).length!==1) throw Error('persistence');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'combine-chain-ui.js')


def test_cancelled_signed_return_survives_until_ordinary_stake_can_be_staged():
    run_in_module_context(r"""
(async()=>{
CURRENT_PLAYER='alice'; BOARD={player:'alice',plots:[{deed_uid:'d',workers:[]}]};
PLANS.open=true;PLANS.ready=true;PLANS.account='alice';
const plan=createChain({id:'cc-return',origin:{kind:'tile',asset_uid:'a'},card:{name:'Worker'},copies:[{uid:'a',bcx:2,kind:'staked',source:{deed_uid:'d',slot_index:0}},{uid:'b',bcx:1,kind:'free'}],expected:{bcx:3}});
plan.state='cancelled'; plan.inputs[0].status='left_out'; plan.inputs[0].left_out_choice='put_back';
plan.inputs[0].unstake={tx_id:'tx',signed_at:'2026-10-05',confirmed_at:null}; saveChain('cc-return',plan);
editorGroups=()=>[{id:'worker',slots:[null]}]; renderCheckoutSurface=()=>{};updateMountedRow=()=>{};applyStageToRow=()=>{};refreshStageImpactForRow=()=>{};
await materializeChainReturns('cc-return');
if(CERES_STAGE.length || activeChainPlans().length!==1) throw Error('pending return lost or staged too early');
recordChainUnstake({chain_id:'cc-return',chain_input_uid:'a'},'confirmed','tx');
await materializeChainReturns('cc-return');
if(CERES_STAGE.length!==1||CERES_STAGE[0].action!=='stake'||CERES_STAGE[0].cooldown.ready!==false) throw Error('return not safely staged');
await materializeChainReturns('cc-return');
if(CERES_STAGE.length!==1||activeChainPlans().length!==0) throw Error('duplicate return');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'combine-chain-ui.js')


def test_refused_unstake_does_not_return_from_an_older_device():
    run_in_module_context(r"""
const base={version:2,state:'planned',created_at:'2026-10-05T00:00:00Z',origin:{kind:'tile'},card:{name:'Worker'},expected:{bcx:3}};
const signed={...base,updated_at:'2026-10-05T01:00:00Z',inputs:[{uid:'a',cc:2,kind:'staked',status:'active',unstake:{tx_id:'refused',signed_at:'2026-10-05T01:00:00Z'}}]};
const refused={...base,updated_at:'2026-10-05T02:00:00Z',inputs:[{uid:'a',cc:2,kind:'staked',status:'active',unstake:null,refused_unstake_tx_id:'refused',problem:{code:'unstake_refused'}}]};
const merged=mergeStaged({chains:{'cc-test':refused}},{chains:{'cc-test':signed}}).chains['cc-test'];
if(merged.inputs[0].unstake || merged.inputs[0].problem.code!=='unstake_refused') throw Error('refused signature resurrected');
""", entry=STATIC / 'account-space.js')
