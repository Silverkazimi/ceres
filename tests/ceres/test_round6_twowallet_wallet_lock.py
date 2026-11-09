""" round 6: fresh reviewed vectors for repeated wallet handoffs."""

from __future__ import annotations

import json

import pytest

from tests.ceres.test_replay import FAKE_WORLD, STATIC
from tests.ceres.vm_context import run_in_module_context


WORLD = FAKE_WORLD + r"""
const T = context.__twowallet = { built: [], signed: [], begins: [], seen: new Set(), rejected: 0 };
const originalFetch = context.fetch;
context.fetch = context.window.fetch = async (url, init = {}) => {
  const path = String(url).split('?')[0];
  const body = init.body ? JSON.parse(init.body) : {};
  if (path === '/api/confirm/intent' && body.event === 'begin') {
    const marker = body.operations.map(op => {
      const payload = JSON.parse(op[1].json);
      return payload.params?.n || payload.n;
    }).join('|');
    if (T.seen.has(marker)) { T.rejected++; return W.reply({detail:'nonce already used'}, 409); }
    T.seen.add(marker);
    T.begins.push({ marker, operations: body.operations });
  }
  if (W.adapterFetch) {
    const answer = await W.adapterFetch(path, body);
    if (answer) return answer;
  }
  return originalFetch(url, init);
};
const originalBroadcast = context.window.hive_keychain.requestBroadcast;
context.window.hive_keychain.requestBroadcast = (account, operations, authority, callback) => {
  T.signed.push(JSON.parse(JSON.stringify(operations)));
  return originalBroadcast(account, operations, authority, callback);
};
context.__finish = data => { console.log('IR9545-RESULT ' + JSON.stringify(data)); process.exit(0); };
"""


def _run(body: str, entry=STATIC / "wallet-lp.js") -> dict:
    wrapped = "(async()=>{\n" + body + "\n})().catch(e=>__finish({error:String(e?.stack||e)}));"
    result = run_in_module_context(wrapped, entry=entry, extra_context_js=WORLD, check=False)
    line = next((s for s in result.stdout.splitlines() if s.startswith("IR9545-RESULT ")), None)
    assert line, (result.stdout[-1500:], result.stderr[-1500:])
    return json.loads(line.split(" ", 1)[1])


LP_SETUP = r"""
const ctx = {player:'alice', account:()=> 'alice', drawer:()=>null, render:()=>{},
  reload:async()=>{}, setSigning:()=>{}, wallet:()=>wallet};
const target = {closest:()=>({dataset:{lpSymbol:'SPS'}})};
const wallet = {account:{SPS:'100', DEC:'1000'}, regions:[],
  swap:{symbols:['SPS'], account_symbols:['SPS'], signing_enabled:true,
    directions:['swap_sell','swap_buy'], rates:{SPS:{swap_sell:1}}},
  pool:{symbols:['SPS'],account_symbols:['SPS'],reserves:{SPS:{resource:1000,dec:1000,shares:1000}},
    positions:{SPS:{shares:'100',share_percent:'10',resource_amount:'100',dec_amount:'100'}}}};
__world.scenario = {wallet:'declined'};
const makeOp = (kind, n) => [['custom_json',{id:kind==='funding'?'sm_sps':'sm_land_operation',
  required_auths:kind==='funding'?['alice']:[], required_posting_auths:kind==='funding'?[]:['alice'],
  json:JSON.stringify(kind==='funding'
    ? {action:'token_transfer',params:{token:'SPS',qty:10,to:'pool',memo:'lpsid:42',n}}
    : {op:kind,resource_symbol:'SPS',amount:'10.000',n})}]];
const next = kind => {const n=(++__world.buildSeq).toString(16).padStart(16,'0');
  const operations=makeOp(kind,n); __twowallet.built.push(operations); return {operations,nonce:n,payload:{n},
    quote:{input:10,output:5},deposit:{resource_amount:10,dec_amount:10},
    withdraw:{resource_amount:10,dec_amount:10}};};
__world.buildSeq=0;
__world.adapterFetch=async (path, body)=>{
  if(path==='/api/wallet/lp/preview') return __world.reply(next(body.kind||'swap_sell'));
  if(path==='/api/wallet/lp/position') return __world.reply({locked_shares:0});
  if(path==='/api/wallet/lp/sps-funding') return __world.reply({status:'ready',...next('funding')});
  return null;
};
const marker = ops => {const p=JSON.parse(ops[0][1].json); return p.params?.n||p.n;};
const summarize = () => ({wallet:__world.walletCalls, built:__twowallet.built.length,
  signed:__twowallet.signed, begins:__twowallet.begins.map(x=>x.marker), rejected:__twowallet.rejected,
  markers:__twowallet.signed.map(marker)});
const probeReuse = async operations => (await fetch('/api/confirm/intent', {method:'POST',
  body:JSON.stringify({event:'begin',attempt:'probe',operations})})).status===409;
"""


def _assert_two(out: dict) -> None:
    assert "error" not in out, out
    assert out["wallet"] == 2, out
    assert out["markers"][0] != out["markers"][1], out
    assert out["begins"] == out["markers"], out
    assert out["rejected"] == 1, out


@pytest.mark.parametrize("flow", ["swap", "add", "withdraw"])
def test_lp_handoff_rebuilds_review_before_second_wallet(flow):
    body = LP_SETUP + f"\nconst flow={json.dumps(flow)};\n" + r"""
const st=swapState('alice','SPS');
if(flow==='swap') {
  Object.assign(st,{amount:'10',quoted:'10',quote:{kind:'swap_sell',input:10,output:5}});
  const first=swapAction(ctx,'swap',target); await __advance(1500); await first;
  __world.scenario={wallet:'ok',ackMs:1000};
  const second=swapAction(ctx,'swap',target); await __advance(1500); await second;
} else {
  await swapAction(ctx,'pool-'+(flow==='add'?'add':'remove'),target);
  if(flow==='add') st.pool.amount='10'; else st.pool.pct='10';
  const first=swapAction(ctx,'pool-sign',target); await __advance(1500); await first;
  __world.scenario={wallet:'ok',ackMs:1000};
  const second=swapAction(ctx,'pool-sign',target); await __advance(1500); await second;
}
const out=summarize(); out.reuseRejected=await probeReuse(__twowallet.signed[0]); out.rejected=__twowallet.rejected; __finish(out);
"""
    out = _run(body)
    _assert_two(out)
    assert out["built"] == 2, out
    assert out["reuseRejected"], out


@pytest.mark.parametrize("restored", [False, True], ids=["live", "restored-preview"])
def test_sps_funding_rebuilds_before_each_wallet(restored):
    body = LP_SETUP + f"\nconst restored={str(restored).lower()};\n" + r"""
const old=makeOp('funding','f'.repeat(16));
const receipt={kind:'swap_sell',pay:'SPS',receive:'DEC',symbol:'SPS',
  tx_id:'b'.repeat(40),nonce:'a'.repeat(16),payload:{n:'a'.repeat(16)},
  funding_ops:old,output:5};
if(restored) {
  localStorage.setItem('ceres:wallet-lp-intent:v1:alice:SPS',
    JSON.stringify({stage:'funding',receipt,token:'saved',savedAt:Date.now()}));
}
const st=swapState('alice','SPS');
if(!restored) {Object.assign(st,{stage:'funding',receipt,token:'saved'});
  syncStoredIntent('alice','SPS',st);}
const first=swapAction(ctx,'fund',target); await __advance(1500); await first;
__world.scenario={wallet:'ok',ackMs:1000};
const second=swapAction(ctx,'fund',target); await __advance(1500); await second;
const out=summarize(); out.restored=!!st.receipt?.restored;
out.funding=out.signed.map(ops=>JSON.parse(ops[0][1].json).params);
out.cached=marker(old); out.reuseRejected=await probeReuse(__twowallet.signed[0]); out.rejected=__twowallet.rejected; __finish(out);
"""
    out = _run(body)
    _assert_two(out)
    assert out["built"] == 2, out
    assert out["reuseRejected"], out
    assert out["markers"][0] != out["cached"], out
    assert out["markers"][1] != out["cached"], out
    assert out["restored"] is restored, out
    assert len({(p["memo"], p["to"], p["qty"], p["token"]) for p in out["funding"]}) == 1, out
    assert (out["funding"][0]["memo"], out["funding"][0]["to"], out["funding"][0]["qty"], out["funding"][0]["token"]) == ("lpsid:42", "pool", 10, "SPS"), out


@pytest.mark.parametrize("action", ["lock", "unlock"])
@pytest.mark.parametrize("restored", [False, True], ids=["live", "restored-plan"])
def test_deed_handoff_rebuilds_review_and_envelope(action, restored):
    body = f"const action={json.dumps(action)}, restored={str(restored).lower()};\n" + r"""
CURRENT_PLAYER='alice';
document.getElementById=id=>id==='board-version'?{textContent:'1.2.3'}:null;
const row={deed_uid:'I-1-aaaaaaaa',plot:'1-1-1',lock_days:action==='lock'?0:3,
  unlock_date:null};
BOARD={player:'alice',plots:[row]};
const key='ceres:plot-lock-plan:v1:alice';
const saved={deed_uid:row.deed_uid,action,baselineDays:action==='lock'?null:3,
 baselineUnlockDate:row.unlock_date,lockDays:3};
const savedNonce='11111111-1111-4111-8111-111111111111';
if(restored) saved.operations=[['custom_json',{id:'sm_'+action+'_assets',required_auths:['alice'],required_posting_auths:[],
 json:JSON.stringify({type:'item',asset_ids:[row.deed_uid],app:'ceres/1.2.3',n:savedNonce,
 ...(action==='lock'?{lock_days:3}:{})})}]];
if(restored) localStorage.setItem(key,JSON.stringify([saved]));
reconcilePlotLockPlans(BOARD);
if(restored && JSON.parse(STAGED_PLOT_LOCKS.get(row.deed_uid).operations[0][1].json).n!==savedNonce) throw Error('saved review not restored');
if(!restored) {if(!togglePlotLock(row)) throw Error('cannot stage');}
walletCanSignTransaction=()=>true;
prepareSigningBatch=async(account, groups, authority, metadata)=>groups.map(operations=>
 ({account,authority,transaction:{operations},metadata}));
requestSignatureThroughGate=async(account, operations, onResponse, authority, prepared, metadata)=>{
 if(JSON.stringify(prepared.transaction.operations)!==JSON.stringify(operations)) throw Error('stale envelope');
 return BroadcastGate.broadcast(account,operations,onResponse,authority,metadata);
};
__world.scenario={wallet:'declined'};
const old=await preparePlotLockReview(row);
const oldNonce=JSON.parse(old.operations[0][1].json).n;
const first=signPlotLockReview(row).catch(()=>{}); await __advance(1500); await first;
__world.scenario={wallet:'ok',ackMs:1000};
const second=signPlotLockReview(row).catch(()=>{}); await __advance(1500); await second;
const out={wallet:__world.walletCalls,
 markers:__twowallet.signed.map(ops=>JSON.parse(ops[0][1].json).n),
 begins:__twowallet.begins.map(x=>x.marker),
 ids:__twowallet.signed.map(ops=>ops[0][1].id),oldNonce,savedNonce,restored,stage:STAGED_PLOT_LOCKS.get(row.deed_uid)?.action};
out.reuseRejected=(await fetch('/api/confirm/intent',{method:'POST',body:JSON.stringify({event:'begin',attempt:'probe',operations:__twowallet.signed[0]})})).status===409;
out.rejected=__twowallet.rejected;
__finish(out);
"""
    out = _run(body, STATIC / "plot-lock.js")
    _assert_two(out)
    assert out["reuseRejected"], out
    assert out["markers"][0] != out["oldNonce"], out
    assert out["markers"][1] != out["oldNonce"], out
    if restored:
        assert out["savedNonce"] not in out["markers"], out
    assert out["ids"] == [f"sm_{action}_assets"] * 2, out
    assert out["stage"] == action, out
