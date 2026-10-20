""" equality (including capped over-combines) and explicit hold resolution."""
from tests.ceres.test_sign_order import SETUP, STATIC
from tests.ceres.vm_context import run_in_module_context


def test_mismatch_two_choices_and_overcombined_equality():
    run_in_module_context(SETUP + r"""
(async()=>{
p.state='combined';p.combine={tx_id:'known'};p.result={uid:'C-input-b',bcx:4,level:2,base_pp:300,observed_at:'2026-10-04'};p.problem={code:'combine_mismatch'};
saveChain(p.id,p);CERES_STAGE.push({chain_id:p.id,action:'stake',deed_uid:'d',slot_index:2,_asset:{planned:true},cooldown:{ready:false}});
if(chainResultMatches(p,p.result)||stageSetAside(CERES_STAGE[0])!=='blocked')throw Error('CC hold');
if(chainResultMatches(p,{bcx:3,base_pp:301}))throw Error('PP equality');
const html=chainCardHtml(p.id,p);
for(const copy of ['expected CC 3','game shows CC 4','Use this card anyway','Cancel the placement'])if(!html.includes(copy))throw Error(copy);
if(html.includes('Finish plan')||html.includes('Cancel plan')||html.includes('Leave out'))throw Error('destructive plan cancellation after sign');
await resolvePlanMismatch(p.id,true);
if(chainPlans()[p.id].state!=='combined'||stageSetAside(CERES_STAGE[0])||CERES_STAGE[0].asset_ref!=='C-input-b')throw Error('explicit release');
saveChain(p.id,p);await resolvePlanMismatch(p.id,false);
if(CERES_STAGE.length||chainPlans()[p.id].state!=='combined')throw Error('placement cancel killed plan');
p.expected={bcx:150,base_pp:1100};if(!chainResultMatches(p,{bcx:150,base_pp:1100,level:8}))throw Error('capped equality');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'checkout.js')
