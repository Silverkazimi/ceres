""" B03/B04: actual persistence, deterministic lock contention, no wallet I/O."""
from tests.ceres.test_sign_order import SETUP, STATIC
from tests.ceres.vm_context import run_in_module_context


def test_b03_stale_ordinary_writer_queues_behind_reservation_and_cannot_erase_it():
    run_in_module_context(SETUP+r"""
(async()=>{
const key='ceres:stage-plan:v1:alice';const stale=JSON.parse(localStorage.getItem(key));
let allowA=false,queue=[],requests=0;
navigator.locks.request=(_name,callback)=>{requests++;if(allowA)return Promise.resolve(callback());return new Promise((resolve,reject)=>queue.push(()=>{try{resolve(callback())}catch(e){reject(e)}}))};
// B's ordinary write waits while the lock has been granted to A.
PLANS.chains=JSON.parse(JSON.stringify(stale.chains));PLANS.lastSig=null;
const ordinary=persistStagePlans();
if(requests!==1||queue.length!==1)throw Error('ordinary writer bypassed account lock');
// A executes the actual locked reservation and reaches the wallet stub before B.
allowA=true;let wallets=0;
fetch=async()=>({ok:true,json:async()=>makeBuild(p)});
beginSharedOperation=async()=>{wallets++;throw Object.assign(new Error('unobserved'),{ambiguousBroadcast:true})};
await signPlanCombine({id:p.id,witness:JSON.stringify(chainSignWitness(p)),build:makeBuild(p)});
if(wallets!==1||JSON.parse(localStorage.getItem(key)).chains[p.id].combine?.nonce!=='nonce')throw Error('A did not reserve');
PLANS.chains=JSON.parse(JSON.stringify(stale.chains));PLANS.revision=stale.revision;PLANS.lastSig=null;
queue.shift()();await ordinary;
if(JSON.parse(localStorage.getItem(key)).chains[p.id].combine?.nonce!=='nonce')throw Error('pending erased');
navigator.locks.request=async(_name,callback)=>callback();
PLANS.chains=JSON.parse(JSON.stringify(stale.chains));PLANS.lastSig=null;
fetch=async()=>({ok:true,json:async()=>makeBuild(p)});
fetch=async()=>{const fresh=makeBuild(p);fresh.operation[1].json=JSON.stringify({cards:p.inputs.map(i=>i.uid),n:'second-attempt'});return {ok:true,json:async()=>fresh}};
beginSharedOperation=async()=>{wallets++;throw Error('duplicate wallet')};
await signPlanCombine({id:p.id,witness:JSON.stringify(chainSignWitness(p)),build:makeBuild(p)});
if(wallets!==1)throw Error('second wallet reached');
})().catch(e=>{console.error(e);process.exitCode=1});
""",entry=STATIC/'checkout.js')


def test_b04_51_completions_dominate_same_attempt_signed_or_combined():
    run_in_module_context(SETUP+r"""
const chains={};for(let i=0;i<51;i++)chains['cc-'+i]={...p,id:'cc-'+i,state:'done',updated_at:new Date(1700000000000+i*1000).toISOString(),combine:{nonce:'attempt-'+i,tx_id:'known'}};
const compact=compactChains(chains);if(!compact['cc-0'].tombstone)throw Error('fixture not compacted');
for(const state of ['combined','combine_signed'])for(const reverse of [false,true]){
 const stale={...chains['cc-0'],state};const a={chains:compact},b={chains:{'cc-0':stale}};
 const merged=reverse?mergeStaged(a,b):mergeStaged(b,a);
 if(merged.chains['cc-0'].state!=='done'||Object.values(merged.chains).some(plan=>plan.combine?.nonce==='attempt-0'&&['combined','combine_signed'].includes(plan.state)))throw Error('same attempt revived');
}
const other={...chains['cc-0'],state:'combine_signed',combine:{nonce:'distinct',tx_id:null}};
const merged=mergeStaged({chains:{'cc-0':other}},{chains:compact});
if(!Object.values(merged.chains).some(plan=>plan.combine?.nonce==='distinct'))throw Error('distinct unresolved attempt lost');
""",entry=STATIC/'checkout.js')


def test_b03_missing_or_throwing_locks_do_not_write():
    run_in_module_context(SETUP+r"""
(async()=>{
const key='ceres:stage-plan:v1:alice',before=localStorage.getItem(key);
for(const locks of [null,{request:()=>{throw Error('unavailable')}},{request:async()=>{throw Error('unavailable')}}]){
 navigator.locks=locks;PLANS.lastSig=null;PLANS.chains[p.id].problem={code:'test'};
 await persistStagePlans();if(localStorage.getItem(key)!==before)throw Error('unlocked ordinary write');
}
})().catch(e=>{console.error(e);process.exitCode=1});
""",entry=STATIC/'checkout.js')
