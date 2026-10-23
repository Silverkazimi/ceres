from pathlib import Path
from tests.ceres.vm_context import run_in_module_context

GATE = Path(__file__).resolve().parents[2] / "ceres/static/broadcast-gate.js"


def run(body):
    run_in_module_context("""
const ops = (n, prefix) => Array.from({length:n}, (_, i) => ['custom_json', {id:'sm_land_operation',json:JSON.stringify({n:prefix+i})}]);
let calls = 0;
window.hive_keychain = {requestBroadcast(a,o,k,cb) {calls++; cb({success:true,result:{id:'tx-'+calls}});}};
""" + body, entry=GATE, extra_context_js="context.navigator.locks = {request(n,o,cb) {return cb();}};")


def test_four_plus_four_waits_for_chain_only():
    run("""
(async () => {
let reads=0;
fetch=async(url, options)=>{
  if (!url.includes('/api/op-inclusion?player=alice')) throw Error('must use chain-only probe');
  if (JSON.parse(options.body).tx_id !== 'tx-1' || calls !== 1) throw Error('next wallet opened too early');
  reads++;
  return {ok:true,json:async()=>({included:true,block_num:123})};
};
await BroadcastGate.broadcast('alice',ops(4,'a'));
await BroadcastGate.broadcast('alice',ops(4,'b'));
if(calls!==2 || reads!==1) throw Error('wrong admission sequence');
})().then(()=>process.exit(0), e=>{console.error(e);process.exit(1)});
""")


def test_four_plus_one_and_other_account_are_immediate():
    run("""
(async () => {
fetch=()=>{throw Error('unnecessary inclusion wait');};
await BroadcastGate.broadcast('alice',ops(4,'a'));
await BroadcastGate.broadcast('ALICE',ops(1,'b'));
await BroadcastGate.broadcast('bob',ops(4,'c'));
if(calls!==3) throw Error('missing wallet requests');
})().then(()=>process.exit(0), e=>{console.error(e);process.exit(1)});
""")


def test_persisted_other_tab_budget_and_inconclusive_lookup_block_send():
    run("""
(async () => {
localStorage.setItem('ceres:pending-custom-ops:v1:alice',JSON.stringify([{id:'other-tab',count:4,txId:'old-tx',createdAt:new Date().toISOString()}]));
setTimeout=(cb,delay)=>{if(delay===1000) Promise.resolve().then(cb);return 1;};  // : one-second budget checks
clearTimeout=()=>{};
fetch=async()=>({ok:true,json:async()=>({included:null,block_num:null})});
const error=await BroadcastGate.broadcast('alice',ops(4,'new')).catch(e=>e);
if(error.broadcasted!==false || calls!==0) throw Error('sent without budget');
if(JSON.parse(localStorage.getItem('ceres:pending-custom-ops:v1:alice')).length!==1) throw Error('lost prior budget');
})().then(()=>process.exit(0), e=>{console.error(e);process.exit(1)});
""")


def test_node_rejection_is_concise_and_releases_rejected_budget():
    run("""
(async () => {
window.hive_keychain.requestBroadcast=(a,o,k,cb)=>{calls++;cb({success:false,error:{code:-32003,message:'Assert Exception',data:{stack:[{format:'HIVE_CUSTOM_OP_BLOCK_LIMIT',data:{trx:{signatures:['PRIVATE-RAW-DETAIL']}}}]}}});};
const error=await BroadcastGate.broadcast('alice',ops(4,'a')).catch(e=>e);
if(!error.broadcastRejected || error.broadcasted!==false || !error.message.includes('signed transaction') || error.message.includes('PRIVATE-RAW-DETAIL')) throw Error('wrong rejection classification');
if(JSON.parse(localStorage.getItem('ceres:pending-custom-ops:v1:alice')).length) throw Error('rejected budget retained');
})().then(()=>process.exit(0), e=>{console.error(e);process.exit(1)});
""")


def test_oversized_batch_never_reaches_wallet():
    run("""
(async () => {
const error=await BroadcastGate.broadcast('alice',ops(6,'a')).catch(e=>e);
if(error.broadcasted!==false || calls!==0) throw Error('oversized batch sent');
})().then(()=>process.exit(0), e=>{console.error(e);process.exit(1)});
""")


def test_combined_wallet_cannot_repeat_a_sign_only_queue_claim():
    run("""
(async () => {
const operations=ops(1,'shared');
localStorage.setItem('ceres:signed-queue:v1:alice',JSON.stringify([{account:'alice',txId:'a'.repeat(40),state:'queued',transaction:{operations}}]));
const error=await BroadcastGate.broadcast('alice',operations).catch(e=>e);
if(!error.duplicateBroadcast || calls!==0) throw Error('combined wallet duplicated a queued signature');
})().then(()=>process.exit(0),e=>{console.error(e);process.exit(1)});
""")
