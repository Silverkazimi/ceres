""" round 6: fresh reviewed vectors across wallet retry handoffs.

The Node VM adapter scenarios below exercise purchase, combine, and Changes
handoffs against server fakes that reject reused attempt nonces.
"""

import json

from tests.ceres.test_replay import FAKE_WORLD, STATIC, _exposed
from tests.ceres.vm_context import run_in_module_context


def _run(entry_name, exposed, body, patches=()):
    entry, source = _exposed(entry_name, exposed, patches)
    result = run_in_module_context(body, entry=entry, source=source,
                                   extra_context_js=FAKE_WORLD, check=False)
    line = next((line for line in result.stdout.splitlines()
                 if line.startswith("IR9545-RESULT ")), None)
    assert line, (result.stdout[-2000:], result.stderr[-3000:])
    return json.loads(line.split(" ", 1)[1])


PURCHASE = r"""
(async () => {
  const changed = CHANGED;
  const restored = RESTORED;
  CURRENT_PLAYER = 'alice'; BOARD = { player: 'alice', plots: [] };
  render = () => {}; walletReady = () => true; walletKind = () => 'keychain'; walletAccount = () => 'alice';
  __world.scenario = { wallet: 'declined', neverSent: true };
  const calls = [], begins = [], previews = [], seen = new Set();
  const oldFetch = fetch;
  fetch = window.fetch = async (url, init = {}) => {
    if (String(url).startsWith('/api/confirm/intent')) {
      const body = JSON.parse(init.body);
      if (body.event === 'begin') {
        const nonce = JSON.parse(body.operations[0][1].json).n;
        if (seen.has(nonce)) return __world.reply({detail:'reused nonce'}, 409);
        seen.add(nonce); begins.push(nonce);
      }
    }
    return oldFetch(url, init);
  };
  window.hive_keychain.requestBroadcast = (_account, operations, _key, callback) => {
    calls.push(JSON.parse(JSON.stringify(operations)));
    setTimeout(() => callback({success:false,error:'user_cancel',message:'Request was canceled by the user.'}), 10);
  };
  const item = 'ab'.repeat(20) + '-0';
  const op = (n, price) => ['custom_json', {id:'sm_market_purchase', required_auths:['alice'], required_posting_auths:[],
    json:JSON.stringify({currency:'DEC',items:[item],price,app:'ceres/1.0',n})}];
  let seq = 0, price = 1;
  __world.gameFetch = async (path) => {
    if (path === '/api/market/checkout/preview') {
      seq++;
      if (changed && seq === 2) return __world.reply({detail:'Price changed',quote_id:'q2',
        currencies:{DEC:{total:'2.000',enough:true}},signing:{enabled:true},
        items:[{lkey:'L-1',price_usd:2}],dec_price:1},409);
      const n = seq.toString(16).padStart(16, '0'); previews.push(n);
      return __world.reply({transactions:[{nonce:n,payload:{currency:'DEC',items:[item],price,app:'ceres/1.0',n},
        operations:[op(n,price)],lkeys:['L-1'],price:price.toFixed(3),currency:'DEC'}]});
    }
    if (path === '/api/market/checkout/find') return __world.reply({status:'not_found',covered:true});
    return null;
  };
  co.player='alice'; co.stage='confirm'; co.currency='DEC'; co.items=[{lkey:'L-1',name:'Card'}];
  co.quote={quote_id:'q1',currencies:{DEC:{total:'1.000',enough:true}},signing:{enabled:true},
    items:[{lkey:'L-1',price_usd:1}],dec_price:1};
  const oldNonce='f'.repeat(16);
  if (restored) {
    co.run={token:'restored',currency:'DEC',txs:[{nonce:oldNonce,payload:{n:oldNonce},
      operations:[op(oldNonce,1)],lkeys:['L-1'],price:'1.000',currency:'DEC',state:'declined'}],groups:[]};
    writeRun('alice',co.run);
    seen.add(oldNonce);
  }
  let firstDone = false;
  sign().then(() => { firstDone = true; });
  for (let i = 0; i < 20 && !firstDone; i++) await __advance(1000);
  if (!firstDone) throw Error('first sign did not finish');
  const firstState = co.run?.txs?.[0]?.state;
  if (changed) price = 2;
  let secondDone = false;
  sign().then(() => { secondDone = true; });
  for (let i = 0; i < 20 && !secondDone; i++) await __advance(1000);
  if (!secondDone) throw Error('second sign did not finish');
  const reviewBeforeSecondWallet = changed && co.stage === 'review' && co.currency === null && calls.length === 1;
  if (changed) {
    co.currency='DEC';
    let thirdDone=false; sign().then(()=>{thirdDone=true});
    for(let i=0;i<20&&!thirdDone;i++) await __advance(1000);
    if(!thirdDone) throw Error('confirmed changed price did not finish');
  }
  const ns = calls.map(ops => JSON.parse(ops[0][1].json).n);
  // The fake server must reject any attempt to register the first nonce again.
  const reuse = await fetch('/api/confirm/intent', {body:JSON.stringify({event:'begin',
    operations:[op(ns[0],1)],attempt:'reuse'})});
  console.log('IR9545-RESULT '+JSON.stringify({wallet:calls.length,ns,begins,previews,firstState,
    reuseRejected:reuse.status===409,secondState:co.run?.txs?.[0]?.state,
    reviewBeforeSecondWallet,oldNonce,restored}));
  process.exit(0);
})().catch(error => {console.log('IR9545-RESULT '+JSON.stringify({error:String(error.stack||error)}));process.exit(0)});
"""


def test_market_purchase_retry_rebuilds_before_second_wallet():
    result = _run("card-checkout.js", ["render", "walletAccount", "co"],
                  PURCHASE.replace("CHANGED", "false").replace("RESTORED", "false"))
    assert result.get("wallet") == 2, result
    assert result["firstState"] == "declined", result
    assert len(result["previews"]) == 2, result
    assert len(set(result["ns"])) == 2, result
    assert result["begins"] == result["ns"], result
    assert result["reuseRejected"], result


def test_market_purchase_changed_price_requires_review_before_second_wallet():
    result = _run("card-checkout.js", ["render", "walletAccount", "co"],
                  PURCHASE.replace("CHANGED", "true").replace("RESTORED", "false"))
    assert result.get("wallet") == 2, result
    assert result["firstState"] == "declined", result
    assert result["reviewBeforeSecondWallet"], result
    assert len(result["previews"]) == 2, result
    assert len(set(result["ns"])) == 2 and result["begins"] == result["ns"], result
    assert result["reuseRejected"], result


def test_market_purchase_restored_preview_is_not_signed_again():
    result = _run("card-checkout.js", ["render", "walletAccount", "co", "writeRun"],
                  PURCHASE.replace("CHANGED", "false").replace("RESTORED", "true"))
    assert result.get("wallet") == 2, result
    assert result["firstState"] == "declined", result
    assert result["oldNonce"] not in result["ns"], result
    assert len(result["previews"]) == 2, result
    assert len(set(result["ns"])) == 2 and result["begins"] == result["ns"], result
    assert result["reuseRejected"], result


COMBINE = r"""
(async () => {
  CURRENT_PLAYER = 'alice'; BOARD = { player: 'alice', plots: [] };
  __world.scenario = {wallet:'declined',neverSent:true};
  const calls=[], begins=[], builds=[], seen=new Set();
  const oldFetch=fetch;
  fetch=window.fetch=async (url, init={}) => {
    if (String(url).startsWith('/api/confirm/intent')) {
      const body=JSON.parse(init.body);
      if (body.event==='begin') {
        const n=JSON.parse(body.operations[0][1].json).n;
        if (seen.has(n)) return __world.reply({detail:'reused nonce'},409);
        seen.add(n); begins.push(n);
      }
    }
    return oldFetch(url,init);
  };
  window.hive_keychain.requestBroadcast=(_account,operations,_key,callback)=>{
    calls.push(JSON.parse(JSON.stringify(operations)));
    setTimeout(()=>callback({success:false,error:'user_cancel',message:'Request was canceled by the user.'}),10);
  };
  const op=n=>['custom_json',{id:'sm_combine_cards',required_auths:[],required_posting_auths:['alice'],
    json:JSON.stringify({cards:['C1','C2'],n})}];
  __world.gameFetch=async path=>{
    if (path==='/api/combine/build') {
      const n=(builds.length+1).toString(16).padStart(16,'0'); builds.push(n);
      return __world.reply({preview:{buildable:true,account:'alice',uids:['C1','C2'],
        predicted_survivor_uid:'C1',resulting_bcx:2,hazard:{destroyed_uids:['C2']}},operation:op(n)});
    }
    return null;
  };
  COMBINE_PREVIEW={payload:{buildable:true,account:'alice',uids:['C1','C2'],
    predicted_survivor_uid:'C1',resulting_bcx:2,hazard:{destroyed_uids:['C2']}}};
  COMBINE_SELECTION.clear();COMBINE_SELECTION.set('C1',{});COMBINE_SELECTION.set('C2',{});
  COMBINE_DIALOG={name:'Card',copies:[]}; renderCombineDialog=()=>{};
  for(let attempt=0;attempt<2;attempt++) {
    let done=false; confirmCombineBroadcast().then(()=>done=true);
    for(let i=0;i<20&&!done;i++) await __advance(1000);
    if(!done) throw Error('combine attempt did not finish');
  }
  const ns=calls.map(ops=>JSON.parse(ops[0][1].json).n);
  const reuse=await fetch('/api/confirm/intent',{body:JSON.stringify({event:'begin',
    operations:[op(ns[0])],attempt:'reuse'})});
  console.log('IR9545-RESULT '+JSON.stringify({wallet:calls.length,ns,begins,builds,
    reuseRejected:reuse.status===409}));process.exit(0);
})().catch(error=>{console.log('IR9545-RESULT '+JSON.stringify({error:String(error.stack||error)}));process.exit(0)});
"""


def test_combine_dialog_retry_rebuilds_before_second_wallet():
    result = _run("combine-dialog.js", ["COMBINE_PREVIEW", "COMBINE_SELECTION",
        "COMBINE_DIALOG", "renderCombineDialog"], COMBINE)
    assert result.get("wallet") == 2, result
    assert len(result["builds"]) == 2, result
    assert len(set(result["ns"])) == 2, result
    assert result["begins"] == result["ns"], result
    assert result["reuseRejected"], result


PLAN_COMBINE = r"""
(async()=>{
  CURRENT_PLAYER='alice';BOARD={player:'alice',plots:[]};render=()=>{};
  walletReady=()=>true;walletKind=()=> 'keychain';walletAccount=()=> 'alice';
  __world.scenario={wallet:'declined',neverSent:true};
  const calls=[],begins=[],seen=new Set();let builds=0;
  const oldFetch=fetch;
  fetch=window.fetch=async(url,init={})=>{
    if(String(url).startsWith('/api/confirm/intent')){
      const body=JSON.parse(init.body);
      if(body.event==='begin'){
        const n=JSON.parse(body.operations[0][1].json).n;
        if(seen.has(n))return __world.reply({detail:'reused nonce'},409);
        seen.add(n);begins.push(n);
      }
    }
    return oldFetch(url,init);
  };
  window.hive_keychain.requestBroadcast=(_account,operations,_key,callback)=>{
    calls.push(JSON.parse(JSON.stringify(operations)));
    setTimeout(()=>callback({success:false,error:'user_cancel',message:'Request was canceled by the user.'}),10);
  };
  const op=n=>['custom_json',{id:'sm_combine_cards',required_auths:[],required_posting_auths:['alice'],
    json:JSON.stringify({cards:['C1','C2'],n})}];
  const witness={predicted_survivor_uid:'C1',resulting_bcx:2,hazard:{destroyed_uids:['C2']}};
  __world.gameFetch=async path=>{
    if(path==='/api/market/checkout/combine'){
      builds++;return __world.reply({status:'ready',operation:op(builds.toString(16).padStart(16,'0')),
        resulting_bcx:2,witness});
    }
    return null;
  };
  co.player='alice';co.stage='outcome';co.currency='DEC';co.items=[];
  co.run={token:'t',currency:'DEC',txs:[{tx_id:'a'.repeat(40),nonce:'p'.repeat(16),payload:{n:'p'.repeat(16)}}],groups:[]};
  const group={state:'ready',tx:0,lkeys:['L-1'],operation:op('0'.repeat(16)),witness};
  co.run.groups=[group];
  for(let attempt=0;attempt<2;attempt++){
    let done=false;signCombine(group).then(()=>done=true);
    for(let i=0;i<20&&!done;i++)await __advance(1000);
    if(!done)throw Error('plan combine attempt did not finish');
  }
  const ns=calls.map(ops=>JSON.parse(ops[0][1].json).n);
  const reuse=await fetch('/api/confirm/intent',{body:JSON.stringify({event:'begin',
    operations:[op(ns[0])],attempt:'reuse'})});
  console.log('IR9545-RESULT '+JSON.stringify({wallet:calls.length,ns,begins,builds,
    reuseRejected:reuse.status===409,state:group.state}));process.exit(0);
})().catch(error=>{console.log('IR9545-RESULT '+JSON.stringify({error:String(error.stack||error)}));process.exit(0)});
"""


def test_plan_combine_retry_rebuilds_before_second_wallet():
    result = _run("card-checkout.js", ["render", "walletAccount", "co", "signCombine"], PLAN_COMBINE)
    assert result.get("wallet") == 2, result
    assert result["builds"] == 2, result
    assert len(set(result["ns"])) == 2, result
    assert result["begins"] == result["ns"], result
    assert result["reuseRejected"], result


CHANGES = r"""
(async()=>{
  CURRENT_PLAYER='alice';BOARD={player:'alice',plots:[]};
  __world.scenario={wallet:'declined',neverSent:true};
  const target={scope:'portfolio'},calls=[],begins=[],seen=new Set();let previews=0;
  const op=(n,index)=>['custom_json',{id:'sm_stake_change',required_auths:[],required_posting_auths:['alice'],
    json:JSON.stringify({items:[index],n})}];
  const payload=()=>({account:'alice',chunks:[0,1].map(index=>({operations:[op(
    (previews*2+index+1).toString(16).padStart(16,'0'),index)]})),hazards:[]});
  const oldFetch=fetch;
  fetch=window.fetch=async(url,init={})=>{
    const path=String(url).split('?')[0];
    if(path==='/api/capture/status')return __world.reply({state:'ready',latest_snapshot:{generated_at:null}});
    if(path==='/api/op-preview'){previews++;return __world.reply(payload());}
    if(path==='/api/confirm/review')return __world.reply({action_id:'a'.repeat(32),revision:previews});
    if(path==='/api/confirm/intent'){
      const body=JSON.parse(init.body);
      if(body.event==='begin'){
        const n=JSON.parse(body.operations[0][1].json).n;
        if(seen.has(n))return __world.reply({detail:'reused nonce'},409);
        seen.add(n);begins.push(n);
      }
    }
    return oldFetch(url,init);
  };
  window.hive_keychain.requestBroadcast=(_account,operations,_key,callback)=>{
    calls.push(JSON.parse(JSON.stringify(operations)));
    setTimeout(()=>callback({success:false,error:'user_cancel',message:'Request was canceled by the user.'}),10);
  };
  CERES_PREVIEWS.set(previewKey(target),{payload:{account:'alice',chunks:[
    {operations:[op('0'.repeat(16),0)]},{operations:[op('f'.repeat(16),1)]}],hazards:[]},stagedSnapshot:[]});
  const button={disabled:false,hidden:false,setAttribute(){}};
  const stateEl={dataset:{}};
  const reviewEl={querySelector:sel=>sel.includes('confirm')?button:null,querySelectorAll:()=>[stateEl,stateEl]};
  document.querySelector=sel=>sel.includes('op-preview-review')?reviewEl:
    sel.includes('data-op-action="confirm"')?button:null;
  walletReady=()=>true;renderCheckoutSurface=()=>{};prepareSigningBatch=async()=>null;
  setVerificationNotice=()=>{};setConfirmationBannerState=()=>{};renderConfirmationBanner=()=>{};
  for(let attempt=0;attempt<2;attempt++){
    let done=false;confirmOperationBroadcast(target).then(()=>done=true);
    for(let i=0;i<20&&!done;i++)await __advance(1000);
    if(!done)throw Error('Changes attempt did not finish');
  }
  const ns=calls.map(ops=>JSON.parse(ops[0][1].json).n);
  const reuse=await fetch('/api/confirm/intent',{body:JSON.stringify({event:'begin',
    operations:[op(ns[0],0)],attempt:'reuse'})});
  console.log('IR9545-RESULT '+JSON.stringify({wallet:calls.length,ns,begins,previews,
    reuseRejected:reuse.status===409,reviewChunks:CERES_PREVIEWS.get(previewKey(target))?.payload?.chunks?.length}));
  process.exit(0);
})().catch(error=>{console.log('IR9545-RESULT '+JSON.stringify({error:String(error.stack||error)}));process.exit(0)});
"""


def test_changes_chunks_retry_rebuilds_before_second_wallet():
    result = _run("operations.js", ["previewKey", "walletReady", "renderCheckoutSurface",
        "prepareSigningBatch", "setVerificationNotice", "setConfirmationBannerState",
        "renderConfirmationBanner"], CHANGES)
    assert result.get("wallet") == 2, result
    assert result["reviewChunks"] == 2, result
    assert result["previews"] == 2, result
    assert len(set(result["ns"])) == 2 and result["begins"] == result["ns"], result
    assert result["reuseRejected"], result


MULTI_PURCHASE = r"""
(async()=>{
  CURRENT_PLAYER='alice';BOARD={player:'alice',plots:[]};render=()=>{};
  walletReady=()=>true;walletKind=()=> 'keychain';walletAccount=()=> 'alice';
  __world.scenario={neverSent:true};
  const calls=[],begins=[],seen=new Set();let previews=0;
  const item1='ab'.repeat(20)+'-0',item2='cd'.repeat(20)+'-0';
  const op=(n,item)=>['custom_json',{id:'sm_market_purchase',required_auths:['alice'],required_posting_auths:[],
    json:JSON.stringify({currency:'DEC',items:[item],price:1,app:'ceres/1.0',n})}];
  const oldFetch=fetch;
  fetch=window.fetch=async(url,init={})=>{
    if(String(url).startsWith('/api/confirm/intent')){
      const body=JSON.parse(init.body);
      if(body.event==='begin'){
        const n=JSON.parse(body.operations[0][1].json).n;
        if(seen.has(n))return __world.reply({detail:'reused nonce'},409);
        seen.add(n);begins.push(n);
      }
    }
    return oldFetch(url,init);
  };
  window.hive_keychain.requestBroadcast=(_account,operations,_key,callback)=>{
    calls.push(JSON.parse(JSON.stringify(operations)));
    const i=calls.length;
    if(i===1)__world.sent=__now();
    setTimeout(()=>callback(i===1?{success:true,result:{id:__world.txId},data:{key:'Active'}}:
      {success:false,error:'user_cancel',message:'Request was canceled by the user.'}),10);
  };
  __world.gameFetch=async path=>{
    if(path==='/api/market/checkout/preview'){
      previews++;
      return __world.reply({transactions:[item1,item2].map((item,index)=>{
        const n=(previews*2+index).toString(16).padStart(16,'0');
        return {nonce:n,payload:{currency:'DEC',items:[item],price:1,app:'ceres/1.0',n},
          operations:[op(n,item)],lkeys:[index?'L-2':'L-1'],price:'1.000',currency:'DEC'};
      })});
    }
    if(path==='/api/market/checkout/find')return __world.reply({status:'not_found',covered:true});
    if(path==='/api/market/checkout/verify')return __world.reply({status:'pending'});
    return null;
  };
  co.player='alice';co.stage='confirm';co.currency='DEC';co.items=[{lkey:'L-1',name:'A'},{lkey:'L-2',name:'B'}];
  co.quote={quote_id:'q1',currencies:{DEC:{total:'2.000',enough:true}},signing:{enabled:true},
    items:[{lkey:'L-1',price_usd:1},{lkey:'L-2',price_usd:1}],dec_price:1};
  let done=false;sign().then(()=>done=true);
  for(let i=0;i<30&&!done;i++)await __advance(1000);
  if(!done)throw Error('multi purchase sign did not finish');
  const ns=calls.map(ops=>JSON.parse(ops[0][1].json).n);
  const reuse=await fetch('/api/confirm/intent',{body:JSON.stringify({event:'begin',
    operations:[op(ns[0],item1)],attempt:'reuse'})});
  console.log('IR9545-RESULT '+JSON.stringify({wallet:calls.length,ns,begins,previews,
    reuseRejected:reuse.status===409,txs:co.run.txs.map(tx=>({state:tx.state,nonce:tx.nonce})),
    failure:globalThis.__failure||null}));
  process.exit(0);
})().catch(error=>{console.log('IR9545-RESULT '+JSON.stringify({error:String(error.stack||error)}));process.exit(0)});
"""


def test_market_purchase_later_handoff_requotes_before_second_wallet():
    result = _run("card-checkout.js", ["render", "walletAccount", "co"], MULTI_PURCHASE,
                  (("catch (error) { failure = error;",
                    "catch (error) { failure = error; globalThis.__failure = String(error && (error.stack || error.message));"),))
    assert result.get("wallet") == 2, result
    assert result["previews"] == 2, result
    assert len(set(result["ns"])) == 2 and result["begins"] == result["ns"], result
    assert result["txs"][1]["nonce"] == result["ns"][1], result
    assert result["reuseRejected"], result
