""" pilot: offline claim display and by-id confirmation; no wallet request is made."""
from .vm_context import STATIC, run_in_module_context


def test_claim_states_stay_in_rewards_card_without_manual_recovery_controls():
    body = """
const rewards = {total:'5.000',rows:[{label:'Land',source:'land',balance:'5.000'}]};
const base = {player:'player0005',busy:false,signing:false,rewards,claim:{busy:false,review:null,pending:null,checking:false}};
const row = {nonce:'a'.repeat(16),tx_id:'b'.repeat(40),total:'5.000',status:'pending'};
for (const phase of ['asked','sent','checking','retried']) {
 const html = claimHtml({...base,signing:phase==='asked',claim:{...base.claim,pending:{...row,phase}}});
 if (!html.includes('wallet-claim-flow is-'+phase)) throw Error(phase+' not in shared flow');
 if (/Transaction ID|Discard this claim|Check transaction|Check again|Change transaction ID/.test(html)) throw Error('manual recovery shown: '+html);
}
const asked = claimHtml({...base,signing:true,claim:{...base.claim,pending:{...row,phase:'asked'}}});
if (!asked.includes('Check Keychain for approval')) throw Error('Keychain instruction missing');
const done = claimHtml({...base,claim:{...base.claim,pending:{...row,status:'confirmed',claimed:[]}}});
if (!done.includes('wallet-claim-flow is-done')) throw Error('done state missing');
const failed = claimHtml({...base,claim:{...base.claim,pending:{...row,status:'failed'}}});
if (!failed.includes('wallet-claim-flow is-failed')) throw Error('failed state missing');
"""
    run_in_module_context(body, entry=STATIC / "wallet-claim.js")


def test_confirmed_claim_emits_balance_change_without_done_and_retries_same_id():
    body = """
globalThis.setTimeout = fn => { fn(); return 1; };
const D={player:'player0005',generation:0,error:'',signing:false,busy:false,
 claim:{busy:false,review:null,pending:{nonce:'a'.repeat(16),tx_id:'b'.repeat(40),total:'5.000',status:'pending'},checking:false}};
let reloads=0, balanceEvents=0;
window.dispatchEvent = event => { if (event.type === 'ceres:balances-changed' && event.detail.assets.includes('SPS')) balanceEvents++; return true; };
(async()=>{
 await claimAction(D,'verify',()=>{},()=>D.player,async()=>{reloads++});
 if (D.claim.pending?.status!=='confirmed') throw Error('game confirmation not kept');
 if (reloads!==0 || balanceEvents!==1) throw Error('confirmed claim did not use the shared balance signal');
 if (calls!==2) throw Error('did not retry the same transaction');
})().then(()=>process.exit(0),e=>{console.error(e);process.exit(1)});
"""
    extra = """
context.calls=0;
context.fetch=()=>{context.calls++;return Promise.resolve({ok:true,json:async()=>context.calls===1?{status:'not_found'}:{status:'confirmed',total:'5.000',claimed:[]}})};
context.window.fetch=context.fetch;
"""
    run_in_module_context(body, entry=STATIC / "wallet-claim.js", extra_context_js=extra)
