""" r6 §3: two-wallet freshness through the real worksite and harvest adapters."""
from __future__ import annotations

import json

import pytest

from tests.ceres.test_replay import STATIC, _exposed, _harvest_source, _run


# The fake registration endpoint refuses the second use of any signed nonce. Its
# event log also proves that each wallet opening has its own completed begin.
INSTRUMENT = r"""
const events = [], vectors = [], reserved = new Set();
const originalFetch = fetch;
fetch = window.fetch = async (url, init = {}) => {
  if (String(url).split('?')[0] === '/api/confirm/intent') {
    const body = JSON.parse(init.body || '{}');
    if (body.event === 'begin') {
      const ns = body.operations.map(op => JSON.parse(op[1].json).n);
      if (ns.some(n => reserved.has(n))) return __world.reply({detail:'reused nonce'}, 409);
      ns.forEach(n => reserved.add(n));
      events.push({type:'begin', ns});
    }
  }
  return originalFetch(url, init);
};
const originalWallet = window.hive_keychain.requestBroadcast;
window.hive_keychain.requestBroadcast = (account, operations, authority, callback) => {
  const copy = JSON.parse(JSON.stringify(operations));
  vectors.push(copy);
  events.push({type:'wallet', ns:copy.map(op => JSON.parse(op[1].json).n)});
  originalWallet(account, operations, authority, callback);
};
const result = () => ({wallet:__world.walletCalls, events, vectors,
  nonces:vectors.map(ops => ops.map(op => JSON.parse(op[1].json).n)),
  economics:vectors.map(ops => ops.map(([kind, body]) => {
    const payload = JSON.parse(body.json); delete payload.n;
    return [kind, {...body, json:payload}];
  }))});
"""


def _assert_lineage(out, count=2):
    assert out.get("state") == "done", out
    assert out["wallet"] == count, out
    assert len(out["vectors"]) == count, out
    assert out["nonces"][0] != out["nonces"][1], out
    assert out["economics"][0] == out["economics"][1], out
    assert [event["type"] for event in out["events"]] == ["begin", "wallet", "begin", "wallet"], out
    assert out["events"][0]["ns"] == out["events"][1]["ns"], out
    assert out["events"][2]["ns"] == out["events"][3]["ns"], out


WORKSITE = r"""
(async () => {
CURRENT_PLAYER='alice';
const row={deed_uid:'',plot:'1-1-1',worksite_type:'Grain Farm',swap_targets:[]};
BOARD={player:'alice',plots:[row]};
keychainReady=()=>true; worksiteWhatText=()=> 'worksite';
worksiteModalStillOpenFor=()=>true; renderWorksiteVerdict=()=>{};
renderWorksiteModalBody=()=>{}; closeWorksiteModal=()=>{};
refreshConfirmedPlot=async()=>{}; reportWorksiteVerdict=()=>{};
document.getElementById=id=>id==='worksite-modal-overlay'?{hidden:false,dataset:{deedUid:''}}:null;
const op=n=>['custom_json',{id:'sm_land_operation',required_auths:[],required_posting_auths:['alice'],
  json:JSON.stringify({op: FLOW==='completion'?'complete_worksite':'start_worksite', target:'grain', n})}];
const preview=n=>({account:'alice',kind:FLOW==='completion'?'worksite_completion':'worksite',target:'grain',
  chunks:[{operations:[op(n)]}]});
let builds=0;
const old=preview('savednonce000001');
S.WORKSITE_MODAL_PREVIEW={payload:old};
localStorage.setItem('ir9545:worksite-review',JSON.stringify({payload:old}));
__world.gameFetch=async(path)=>{
  if(path==='/api/plots//worksite-preview') return __world.reply(preview('freshnonce00000'+(++builds)));
  if(path==='/api/op-verify') return __world.reply({state:'domain-confirmed',refreshed_deeds:[''],refresh_errors:[]});
  return null;
};
// Staged and completion use the same real signer, with its queue builder
// replaced by a matching envelope so the wallet/intent path stays real.
prepareSigningBatch=async(account, groups, authority)=>groups.map(operations=>
  ({account,authority,transaction:{operations}}));
walletCanSignTransaction=()=>false;
__world.scenario={wallet:'declined', neverSent:true};
const sign=()=>FLOW==='modal' ? confirmWorksiteBroadcast(row,null)
  : signStagedWorksiteBuild(row,FLOW==='completion'?null:'grain',0,{chunk:{operations:[op('savednonce000001')]}});
const first=sign();
for(let i=0;i<10 && __world.walletCalls<1;i++) await __advance(1000);
await __advance(3000);
await first;
if(RESTORE) S.WORKSITE_MODAL_PREVIEW=JSON.parse(localStorage.getItem('ir9545:worksite-review'));
__world.scenario={wallet:'ok'};
const second=sign();
for(let i=0;i<10 && __world.walletCalls<2;i++) await __advance(1000);
await __advance(4000);
await second;
console.log('IR9545-RESULT '+JSON.stringify({state:'done', builds, ...result()}));
process.exit(0);
})().catch(e=>{console.log('IR9545-RESULT '+JSON.stringify({state:'error:'+ (e.stack||e.message)}));process.exit(0)});
"""


@pytest.mark.parametrize("flow", ["modal", "staged", "completion"])
def test_worksite_two_wallets(flow):
    entry, source = _exposed("worksite.js", ["keychainReady", "worksiteWhatText", "worksiteModalStillOpenFor",
        "renderWorksiteVerdict", "renderWorksiteModalBody", "closeWorksiteModal", "refreshConfirmedPlot",
        "reportWorksiteVerdict", "prepareSigningBatch", "walletCanSignTransaction"])
    body = INSTRUMENT + WORKSITE.replace("FLOW", json.dumps(flow)).replace("RESTORE", "false")
    out = _run(body, entry, source)
    _assert_lineage(out)
    assert out["builds"] == 2, out


def test_worksite_restored_preview_rebuilds():
    entry, source = _exposed("worksite.js", ["keychainReady", "worksiteWhatText", "worksiteModalStillOpenFor",
        "renderWorksiteVerdict", "renderWorksiteModalBody", "closeWorksiteModal", "refreshConfirmedPlot",
        "reportWorksiteVerdict", "prepareSigningBatch", "walletCanSignTransaction"])
    out = _run(INSTRUMENT + WORKSITE.replace("FLOW", '"modal"').replace("RESTORE", "true"), entry, source)
    _assert_lineage(out)
    assert "savednonce000001" not in out["nonces"][1], out


HARVEST = r"""
(async () => {
const op=(n,region)=>['custom_json',{id:'sm_land_operation',required_auths:[],required_posting_auths:['alice'],
 json:JSON.stringify({op:'harvest_all',region_uid:region,n})}];
const make=n=>{
  const regions=FLOW==='single'?['PR-1']:['PR-1','PR-2'];
  const members=regions.map((region,i)=>({nonce:n+i,op:'harvest_all',region_uid:region,deed_uids:['']}));
  return {signing_enabled:true,preview_id:'p'+n,preview_digest:'d'+n,
    chunks:[{operations:members.map(m=>op(m.nonce,m.region_uid)),members}]};
};
const saved=make('savednonce00000');
localStorage.setItem('ir9545:harvest-review',JSON.stringify(saved));
Object.assign(M,{open:true,openId:1,gen:1,player:'alice',mode:FLOW==='single'?'plot':'batch',
 scopeKind:FLOW==='single'?'deeds':'regions',deedUids:[''],selectedRegions:new Set(['PR-1','PR-2']),
 preview:saved,run:null,reviewing:null,routingRunId:null});
CURRENT_PLAYER='alice'; BOARD={player:'alice',plots:[{deed_uid:'',plot:'1-1-1'}]};
walletReady=()=>true; walletKind=()=> 'keychain'; render=()=>{};
prepareSigningBatch=async()=>null;
let builds=0;
postJson=async(url,body)=>{
 if(url.endsWith('/preview')) return make('freshnonce0000'+(++builds));
 if(url.endsWith('/preflight')) return {outcome:'PERMIT',chunk:M.preview.chunks[0]};
 if(url.endsWith('/prepare')||url.endsWith('/attempt')) return {};
 if(url.endsWith('/verify')) return {outcome:'pending',members:[]};
 throw Error('unexpected '+url);
};
__world.scenario={wallet:'declined',neverSent:true};
confirmHarvest().catch(()=>{});
for(let i=0;i<10 && __world.walletCalls<1;i++) await __advance(1000);
await __advance(3000);
M.run=null;
if(RESTORE) M.preview=JSON.parse(localStorage.getItem('ir9545:harvest-review'));
__world.scenario={wallet:'ok'};
confirmHarvest().catch(()=>{});
for(let i=0;i<10 && __world.walletCalls<2;i++) await __advance(1000);
console.log('IR9545-RESULT '+JSON.stringify({state:'done',builds,...result()}));
process.exit(0);
})().catch(e=>{console.log('IR9545-RESULT '+JSON.stringify({state:'error:'+ (e.stack||e.message)}));process.exit(0)});
"""


@pytest.mark.parametrize("flow", ["single", "batch"])
def test_harvest_two_wallets(flow):
    entry, source = _harvest_source()
    out = _run(INSTRUMENT + HARVEST.replace("FLOW", json.dumps(flow)).replace("RESTORE", "false"), entry, source)
    _assert_lineage(out)
    assert out["builds"] == 2, out


def test_harvest_restored_preview_rebuilds():
    entry, source = _harvest_source()
    out = _run(INSTRUMENT + HARVEST.replace("FLOW", '"single"').replace("RESTORE", "true"), entry, source)
    _assert_lineage(out)
    assert all(not n.startswith("saved") for n in out["nonces"][1]), out
