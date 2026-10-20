from pathlib import Path
import pytest
from tests.ceres.vm_context import run_in_module_context
ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / 'ceres/static/transaction-queue.js'
SETUP = r"""
context.crypto = require('node:crypto').webcrypto;
context.TextEncoder = TextEncoder;
context.AbortController = AbortController;
const locks = new Map();
context.navigator.locks = { request(name, options, callback) {
 if (options.ifAvailable && locks.has(name)) return Promise.resolve(callback(null));
 const before = locks.get(name) || Promise.resolve();
 const next = before.catch(()=>{}).then(()=>callback({name}));
 locks.set(name,next);
 next.finally(()=>{if(locks.get(name)===next) locks.delete(name);}).catch(()=>{});
 return next;
}};
"""
PRELUDE = r"""
CURRENT_PLAYER='alice';BOARD={player:'alice',plots:[]};
const assert = (value, message) => { if (!value) throw Error(message); };
const op = n => ['custom_json',{id:'sm_land_operation',required_auths:[],required_posting_auths:['alice'],json:JSON.stringify({op:'tax_collection',deed_uids:['I-1-aaaaaaaa'],n})}];
let signatures=0, broadcasts=0, head=100;
const sent = new Map();
const props = () => ({head_block_number:head,head_block_id:'d6908f068242802d3791ff650ddcaefbb54782a5',time:new Date().toISOString().slice(0,19)});
window.hive_keychain={requestBroadcast(){throw Error('combined wallet API must not be used');},requestSignTx(a,tx,k,cb){signatures++;cb({success:true,result:{...tx,signatures:['ab'.repeat(65)]}});}};
queueRpc=async(method,params)=>{
 if(method.endsWith('get_dynamic_global_properties')) return props();
 if(method.endsWith('broadcast_transaction')) {broadcasts++;const tx=params[0];sent.set(await queueTransactionId(tx),tx);return null;}
 if(method.endsWith('get_transaction')) {const tx=sent.get(params[0]);return tx?{...tx,block_num:head}:null;}
 throw Error('unexpected RPC '+method);
};
const settle=async(condition)=>{for(let i=0;i<500;i++){if(condition())return;await new Promise(resolve=>setTimeout(resolve,5));}throw Error('condition timed out');};
"""

def run(body):
    run_in_module_context(PRELUDE + '\n(async()=>{\n' + body + '\n})().then(()=>process.exit(0),e=>{console.error(e);process.exit(1)});', entry=ENTRY, extra_context_js=SETUP)


def test_all_signatures_proceed_while_first_broadcast_is_unresolved():
    run(r"""
const normalRpc=queueRpc;
let release;
queueRpc=async(method,params)=>{
 if(method.endsWith('broadcast_transaction') && broadcasts===0){broadcasts++;await new Promise(resolve=>{release=resolve;});broadcasts--;}
 return normalRpc(method,params);
};
const envelopes=await prepareQueueBatch('alice',[[op('one')],[op('two')],[op('three')]]);
const first=await signQueuedTransaction(envelopes[0]);
await settle(()=>release);
const second=await signQueuedTransaction(envelopes[1]);
const third=await signQueuedTransaction(envelopes[2]);
assert(signatures===3 && broadcasts===1,'signing waited on network or broadcasts overlapped');
assert(signedQueueRows('alice')[1].state==='queued','second signature not durable');
release();
await Promise.all([first.submitted,second.submitted,third.submitted]);
await resumeSignedQueue('alice');
assert(broadcasts===3,'missing broadcast');
assert(signedQueueRows('alice').every(r=>r.state==='included' && !r.signedTransaction),'not reconciled or signatures retained');
    """)


def test_routing_can_link_separately_prepared_exact_envelopes_without_resigning():
    run(r"""
const normalRpc=queueRpc;
let release;
queueRpc=async(method,params)=>{
 if(method.endsWith('broadcast_transaction') && broadcasts===0){broadcasts++;await new Promise(resolve=>{release=resolve;});broadcasts--;}
 return normalRpc(method,params);
};
const first=(await prepareQueueBatch('alice',[[op('route-one')]]))[0];
const second=(await prepareQueueBatch('alice',[[op('route-two')]]))[0];
second.dependsOn=first.txId;
const firstId=first.txId,secondId=second.txId;
const firstJob=await signQueuedTransaction(first);
await settle(()=>release);
const secondJob=await signQueuedTransaction(second);
const duplicate=await signQueuedTransaction(second).catch(error=>error);
const rows=signedQueueRows('alice');
assert(signatures===2 && broadcasts===1,'routing prompts waited on submission or repeated');
assert(duplicate.ambiguousBroadcast,'the already-signed routing envelope was accepted again');
assert(rows[0].txId===firstId && rows[1].txId===secondId,'deterministic transaction ids changed');
assert(rows[1].dependsOn===firstId,'cross-prepare dependency was not retained');
release();
await Promise.all([firstJob.submitted,secondJob.submitted]);
""")


def test_run_metadata_does_not_change_transaction_id_envelope_or_queue_key():
    run(r"""
const fixed=props();
queueRpc=async(method,params)=>{
 if(method.endsWith('get_dynamic_global_properties')) return fixed;
 if(method.endsWith('broadcast_transaction')) {broadcasts++;const tx=params[0];sent.set(await queueTransactionId(tx),tx);return null;}
 if(method.endsWith('get_transaction')) {const tx=sent.get(params[0]);return tx?{...tx,block_num:100}:null;}
 throw Error('unexpected RPC '+method);
};
const plain=(await prepareQueueBatch('alice',[[op('run-metadata')]]))[0];
const tagged=(await prepareQueueBatch('alice',[[op('run-metadata')]],'Posting',{runId:'run-2026-09-20T14:02-deadbeef',runKind:'plan'}))[0];
assert(tagged.txId===plain.txId,'run metadata changed deterministic transaction id');
assert(JSON.stringify(tagged.transaction)===JSON.stringify(plain.transaction),'run metadata entered the signed envelope');
const job=await signQueuedTransaction(tagged);
assert(localStorage.getItem('ceres:signed-queue:v1:alice')!==null,'run metadata changed the account queue key');
const saved=signedQueueRows('alice')[0];
assert(saved.runId==='run-2026-09-20T14:02-deadbeef' && saved.runKind==='plan','run metadata was not saved as queue-row siblings');
await job.submitted;
""")


@pytest.mark.parametrize('change', ['operations', 'authority', 'expiration'])
def test_changed_wallet_transaction_is_never_broadcast(change):
    run(r"""
const prepared=(await prepareQueueBatch('alice',[[op('change')]]))[0];
window.hive_keychain.requestSignTx=(a,tx,k,cb)=>{
 const result={...tx,signatures:['ab'.repeat(65)]};
 if('CHANGE'==='operations') result.operations=[op('different')];
 if('CHANGE'==='expiration') result.expiration='2030-01-01T00:00:00';
 cb({success:true,result,data:{key:'CHANGE'==='authority'?'Active':'Posting'}});
};
const error=await signQueuedTransaction(prepared).catch(e=>e);
assert(error.broadcasted===false && broadcasts===0,'altered signature was used');
assert(signedQueueRows('alice')[0].state==='cancelled','failure missing from journal');
""".replace('CHANGE',change))


def test_definite_admission_rejection_retries_identical_signature_after_new_block():
    run(r"""
const normalRpc=queueRpc;let rejected=null;
queueRpc=async(method,params)=>{
 if(method.endsWith('broadcast_transaction') && !rejected){rejected=JSON.stringify(params[0]);const e=Error('limit');e.rpcError={message:'HIVE_CUSTOM_OP_BLOCK_LIMIT'};head++;throw e;}
 if(method.endsWith('broadcast_transaction')) assert(JSON.stringify(params[0])===rejected,'retry changed signed transaction');
 return normalRpc(method,params);
};
const prepared=(await prepareQueueBatch('alice',[[op('limited')]]))[0];
const job=await signQueuedTransaction(prepared);await job.submitted;await resumeSignedQueue('alice');
assert(signatures===1 && broadcasts===1 && rejected,'admission retry asked for another signature');
""")


def test_ambiguous_response_is_never_rebroadcast_and_blocks_dependent_batch():
    run(r"""
const normalRpc=queueRpc;
queueRpc=async(method,params)=>{
 if(method.endsWith('broadcast_transaction')){broadcasts++;throw Error('connection lost');}
 if(method.endsWith('get_transaction')) return null;
 return normalRpc(method,params);
};
const envelopes=await prepareQueueBatch('alice',[[op('unknown')],[op('dependent')]]);
const first=await signQueuedTransaction(envelopes[0]);
const second=await signQueuedTransaction(envelopes[1]);
const error=await first.submitted.catch(e=>e);
assert(error.ambiguousBroadcast && signatures===2,'wrong ambiguity state');
await new Promise(resolve=>setTimeout(resolve,30));
assert(broadcasts===1 && signedQueueRows('alice')[1].state==='queued','dependent or duplicate broadcast occurred');
""")


def test_reload_submitting_record_only_reconciles():
    run(r"""
const row=(await prepareQueueBatch('alice',[[op('reload')]]))[0];
sent.set(row.txId,{...row.transaction,signatures:['ab'.repeat(65)]});
localStorage.setItem('ceres:signed-queue:v1:alice',JSON.stringify([{...row,state:'submitting',signedTransaction:sent.get(row.txId)}]));
await resumeSignedQueue('alice');
assert(broadcasts===0 && signatures===0,'recovery sent or signed again');
assert(signedQueueRows('alice')[0].state==='included','recovery lost submitted transaction');
""")


def test_reload_queued_signature_submits_once_and_expired_dependency_stops_following():
    run(r"""
const rows=await prepareQueueBatch('alice',[[op('expired')],[op('dependent')]]);
rows[0].transaction.expiration='2020-01-01T00:00:00';
rows[0].txId=await queueTransactionId(rows[0].transaction);rows[1].dependsOn=rows[0].txId;
localStorage.setItem('ceres:signed-queue:v1:alice',JSON.stringify(rows.map(row=>({...row,state:'queued',signedTransaction:{...row.transaction,signatures:['ab'.repeat(65)]}}))));
await resumeSignedQueue('alice');
assert(broadcasts===0 && signatures===0,'expired/dependent transaction sent');
assert(signedQueueRows('alice').map(r=>r.state).join(',')==='expired,blocked','wrong expiry dependency state');
""")


def test_duplicate_same_envelope_and_storage_failure_never_sign_twice():
    run(r"""
const row=(await prepareQueueBatch('alice',[[op('duplicate')]]))[0];
const first=await signQueuedTransaction(row);
const duplicate=await signQueuedTransaction(row).catch(e=>e);
assert(duplicate.ambiguousBroadcast && signatures===1,'duplicate signature prompt');
await first.submitted;await resumeSignedQueue('alice');
const other=(await prepareQueueBatch('alice',[[op('quota')]]))[0];
localStorage.setItem=()=>{throw Error('quota');};
await signQueuedTransaction(other).catch(()=>{});
assert(signatures===1,'wallet requested before durable storage');
""")


def test_twenty_fully_changed_plots_collect_all_signatures_before_first_submission_finishes():
    run(r"""
const normalRpc=queueRpc;let release;
queueRpc=async(method,params)=>{
 if(method.endsWith('broadcast_transaction') && broadcasts===0){broadcasts++;await new Promise(resolve=>{release=resolve;});broadcasts--;}
 return normalRpc(method,params);
};
const operations=[];
for(let plot=0;plot<20;plot++) {
 for(const action of ['unstake','stake']) operations.push(['custom_json',{
 id:'sm_stake_change',required_auths:[],required_posting_auths:['alice'],
 json:JSON.stringify({n:`plot-${plot}-${action}`,deed_uid:`I-1-${plot.toString(16).padStart(8,'0')}`,action,
 cards:Array.from({length:5},(_,slot)=>`worker-${plot}-${action}-${slot}`),
 items:[`core-${plot}-${action}`,`totem-${plot}-${action}`,`title-${plot}-${action}`]})
 }]);
}
const groups=[];for(let i=0;i<operations.length;i+=4) groups.push(operations.slice(i,i+4));
const envelopes=await prepareQueueBatch('alice',groups);
const jobs=[];
for(const envelope of envelopes) jobs.push(await signQueuedTransaction(envelope));
await settle(()=>release);
assert(signatures===10 && broadcasts===1,'20-plot signatures waited for submission');
assert(signedQueueRows('alice').length===10,'lost a large-checkout record');
release();
await Promise.all(jobs.map(job=>job.submitted));await resumeSignedQueue('alice');
assert(broadcasts===10 && signedQueueRows('alice').every(row=>row.state==='included'),'large checkout incomplete');
const actual=[...sent.values()].flatMap(tx=>tx.operations);
assert(JSON.stringify(actual)===JSON.stringify(operations),'plot operations were changed or reordered');
""")


def test_same_nonce_with_new_reference_is_still_duplicate():
    run(r"""
const row=(await prepareQueueBatch('alice',[[op('same-nonce')]]))[0];
const job=await signQueuedTransaction(row);await job.submitted;await resumeSignedQueue('alice');
head++;
const different=(await prepareQueueBatch('alice',[[op('same-nonce')]]))[0];
assert(different.txId!==row.txId,'test did not change transaction reference');
const error=await signQueuedTransaction(different).catch(e=>e);
assert(error.ambiguousBroadcast && signatures===1,'same operation signed twice with new reference');
""")


def test_reload_valid_queued_signature_needs_no_wallet():
    run(r"""
const row=(await prepareQueueBatch('alice',[[op('saved')]]))[0];
localStorage.setItem('ceres:signed-queue:v1:alice',JSON.stringify([{...row,state:'queued',signedTransaction:{...row.transaction,signatures:['ab'.repeat(65)]}}]));
window.hive_keychain=null;
await resumeSignedQueue('alice');
assert(signatures===0 && broadcasts===1 && signedQueueRows('alice')[0].state==='included','saved signed request was not recovered exactly once');
""")


def test_serializer_matches_hive_node_vector():
    import json
    if not Path('work/notes/evidence-hive-serialization-vector.json').exists():
        pytest.skip("private work/ evidence file not included in this export")
    vector = json.loads((ROOT / 'work/notes/evidence-hive-serialization-vector.json').read_text())
    run("const tx = " + json.dumps(vector['transaction']) + ";\n"
        + "const hex=Array.from(unsignedTransactionBytes(tx),b=>b.toString(16).padStart(2,'0')).join('');\n"
        + "assert(hex+'00' === " + json.dumps(vector['serialized_signed_hex']) + "'Hive serialization mismatch');\n"
        + "assert(await queueTransactionId(tx) === " + json.dumps(vector['unsigned_sha256_prefix']) + "'transaction id mismatch');")


def test_sign_timeout_discards_late_signature_without_broadcast():
    run(r"""
const prepared=(await prepareQueueBatch('alice',[[op('late-signature')]]))[0];
const nativeTimer=setTimeout;let timeout,callback;
setTimeout=(fn,ms)=>ms===120000?(timeout=fn,123):nativeTimer(fn,ms);
window.hive_keychain.requestSignTx=(a,tx,k,cb)=>{callback=cb;};
const pending=signQueuedTransaction(prepared).catch(e=>e);
await settle(()=>callback && timeout);timeout();
const error=await pending;
callback({success:true,result:{...prepared.transaction,signatures:['ab'.repeat(65)]}});
assert(error.broadcasted===false && broadcasts===0,'late signature was broadcast');
assert(signedQueueRows('alice')[0].state==='cancelled','late signature revived cancelled request');
""")


def test_changed_selected_account_prevents_next_signature():
    run(r"""
const prepared=(await prepareQueueBatch('alice',[[op('player-switch')]]))[0];
CURRENT_PLAYER='bob';
const error=await signQueuedTransaction(prepared).catch(e=>e);
assert(error.broadcasted===false && signatures===0 && broadcasts===0,'signed after player switched');
""")
