"""Client restore hydrates missing plot evidence before applying a verdict."""
from pathlib import Path
import subprocess

def test_client_hydrates_retries_and_preserves_failures():
    source = (Path(__file__).parents[2] / "ceres/static/stage-plans.js").read_text()
    fn = source[source.index("async function checkPlans(plans) {"):source.index("//  phase 4:")]
    script = """
const assert = require('node:assert/strict');
let CURRENT_PLAYER='player0005', BOARD={player:'player0005',generated_at:'old'};
let calls=[], mode='ok', checked=0;
function playerQueryAppend(url){return url+'?player='+CURRENT_PLAYER;}
async function adoptLatestCapture(){calls.push('adopt');BOARD.generated_at='new';}
async function fetch(url, options){
 calls.push(url);
 if(url.includes('/expand')){
  if(mode==='switch') CURRENT_PLAYER='other';
  return {ok:mode!=='failed',status:mode==='failed'?503:200};
 }
 checked++;
 const verdicts=checked===1 || mode==='incomplete'
  ? [{key:'core',verdict:'needs_assets',deed_uid:'deed'}, {key:'other',verdict:'needs_assets',deed_uid:'deed'}]
  : [{key:'core',verdict:'ok'}];
 return {ok:true,status:200,json:async()=>({verdicts})};
}
""" + fn + """
(async()=>{
 let verdict=await checkPlans([]);
 assert.equal(verdict[0].verdict,'ok');
 assert.equal(calls.filter(x=>x.includes('/expand')).length,1);
 assert.equal(checked,2);assert.ok(calls.includes('adopt'));
 for(const scenario of ['failed','switch','incomplete']){
  mode=scenario;checked=0;calls=[];CURRENT_PLAYER='player0005';
  await assert.rejects(checkPlans([]));
  if(scenario==='failed' || scenario==='switch') assert.equal(checked,1);
  if(scenario==='incomplete') assert.equal(checked,3);
 }
 console.log('4 hydration scenarios passed');
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
