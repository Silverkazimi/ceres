""" actual survivor, capture freshness, next Sign and confirmation."""
from tests.ceres.test_sign_order import SETUP, STATIC
from tests.ceres.vm_context import run_in_module_context


def test_actual_survivor_rebind_capture_and_next_sign():
    run_in_module_context(SETUP + r"""
(async()=>{
p.inputs[0].kind='staked';p.inputs[0].source={deed_uid:'d',slot_index:2};p.inputs[0].unstake={tx_id:'u',confirmed_at:'2026-10-01'};p.inputs[0].ready_at='2026-10-01';
saveChain(p.id,p);
CERES_STAGE.push({chain_id:p.id,action:'stake',deed_uid:'d',slot_index:2,asset_ref:'planned:cc-r4',_asset:{planned:true,chain_id:p.id},cooldown:{ready:false}});
let calls=0,reads=0,premature=false;
fetch=async(url,options)=>{calls++;
 if(url.includes('combine/build'))return {ok:true,json:async()=>makeBuild(p)};
 if(!url.includes('/api/op-verify')||JSON.parse(options.body).tx_id!=='known-tx')throw Error('unbounded lookup');
 return {ok:true,json:async()=>({state:'domain-confirmed',combine_result:{uid:'C-input-b',bcx:3,level:2,land_base_pp:300},combine_observed_at:'2026-10-05T01:00:00Z',combine_refresh:{triggered:true}})};
};
let signed=0;beginSharedOperation=async opts=>{signed++;if(opts.operations.length!==1||opts.operations[0][1].id!=='sm_combine_cards')throw Error('not isolated');return {txId:'known-tx',entryId:'r'}};
publishOperationReceipt=x=>x;stopOperationPoll=()=>{};
settleCombineRefresh=async(_refresh,{after})=>{reads++;premature=stageSetAside(CERES_STAGE[0])!=='blocked';BOARD.generated_at='2026-10-05T02:00:00Z';after()};
const review={id:p.id,witness:JSON.stringify(chainSignWitness(p)),build:makeBuild(p)};
await signPlanCombine(review);
const entry=CERES_STAGE[0], result=chainPlans()[p.id];
if(premature)throw Error(': stale capture released');
if(signed!==1||reads!==1||calls!==2||result.problem||!result.result.survivor_differs)throw Error('verify count');
if(entry.asset_ref!=='C-input-b'||entry._asset.planned||entry.slot_index!==2||stageSetAside(entry))throw Error('rebind');
if(stripStateLabel(entry,'included')!=='To slot 3 · ready')throw Error('Back strip/phone marker');
if(chainReturn(result,'d',2).label!=='Returns upgraded to slot 3'||!plannedReturnChip(entry._asset,'d',2).includes('Returns upgraded to slot 3'))throw Error('Back marker');
const kept=planFromEntry(entry);if(!kept.combine_verified_at)throw Error('freshness lost on reload');
if(!collectReadyItems({stage:[entry],plans:[result]}).items.length)throw Error('ready hook');
recordChainUnstake(entry,'confirmed','stake-tx');if(chainPlans()[p.id].state!=='done')throw Error('not done');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'checkout.js')
