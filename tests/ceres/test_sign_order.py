""" warning first, card batches then isolated combines then worksites."""
from pathlib import Path
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / 'ceres' / 'static'
SETUP = r"""
navigator.locks={request:async(_name,fn)=>fn()}; // exercise the locked wallet path.
CURRENT_PLAYER='alice'; BOARD={player:'alice',generated_at:'2026-10-05T00:00:00Z',plots:[]};
PLANS.open=true;PLANS.ready=true;PLANS.account='alice';
renderCheckoutSurface=()=>{};refreshStageImpactForRow=()=>{};updateMountedRow=()=>{};applyStageToRow=()=>{};
const makePlan=()=>createChain({id:'cc-r4',origin:{kind:'picker'},card:{name:'Worker',card_detail_id:7,edition:4,level:1,bcx:2},
 copies:[{uid:'C-input-a',bcx:2,kind:'free'},{uid:'C-input-b',bcx:1,kind:'free'}],expected:{survivor_uid:'C-input-a',bcx:3,level:2,base_pp:300}});
const makeBuild=(plan)=>({operation:['custom_json',{id:'sm_combine_cards',required_auths:[],required_posting_auths:['alice'],json:JSON.stringify({cards:plan.inputs.map(i=>i.uid),n:'nonce',app:'ceres/test'})}],
 preview:{account:'alice',buildable:true,uids:plan.inputs.map(i=>i.uid),predicted_survivor_uid:'C-input-a',resulting_bcx:3,expected:plan.expected,
 hazard:{rows:plan.inputs.map(i=>({card_id:i.uid,name:'Worker',bcx:i.cc,level:1,edition:4,foil:'Regular Foil'})),destroyed_uids:['C-input-b'],survivor:{bcx:2},warning:'NEVER SHOW C-input-a C-input-b'}}});
const p=makePlan(); saveChain(p.id,p);
"""


def test_warning_before_any_sign_and_skip_leaves_ready():
    run_in_module_context(SETUP + r"""
(async()=>{
const order=[]; let choice='all';
fetch=async()=>({ok:true,json:async()=>makeBuild(p)});
confirmCombineWarning=async reviews=>{order.push('warning'); if(reviews.length!==1)throw Error('reviews');return choice};
checkoutSignableEntries=()=>CERES_STAGE.filter(e=>!e.txId);
CERES_STAGE.push({deed_uid:'d',action:'unstake'});
CERES_PREVIEWS.set(CERES_PORTFOLIO_KEY,{payload:{chunks:[{operations:[['unstake']]}]}});
confirmOperationBroadcast=async()=>{order.push('unstake');CERES_STAGE[0].txId='unstake-tx'};
signPlanCombine=async review=>{order.push('combine'); if(review.id!==p.id)throw Error('wrong plan')};
CERES_STAGED_WORKSITES.set('w',{target:'quarry'});
prepareStagedWorksiteBuild=async()=>({account:'alice',chunk:{operations:[['build']]}});
prepareSigningBatch=async()=>[{}];
signStagedWorksiteBuild=async()=>{order.push('worksite');return {ok:true}};
unstageWorksiteBuild=async id=>CERES_STAGED_WORKSITES.delete(id);
stageToast=()=>{};
STAGED_PLOT_LOCKS.set('d',{action:'lock'});
freshRowForWorksiteSigning=async()=>({deed_uid:'d'});signPlotLockReview=async()=>{order.push('lock')};
constructionModel=()=>({signable:[]});
await confirmCheckoutWithWorksites();
if(order.join()!= 'warning,unstake,combine,worksite,lock')throw Error(order);
order.length=0;choice='without';CERES_STAGE[0].txId=null;CERES_STAGED_WORKSITES.set('w',{target:'quarry'});
await confirmCheckoutWithWorksites();
if(order.join()!=='warning,unstake,worksite,lock'||chainPlans()[p.id].state!=='ready')throw Error('skip '+order);
order.length=0;choice='cancel';await confirmCheckoutWithWorksites();
if(order.join()!=='warning')throw Error('cancel signed');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'checkout.js')


def test_warning_uses_hazard_names_cc_without_identifiers():
    run_in_module_context(SETUP + r"""
const html=combineWarningHtml([{plan:p,build:makeBuild(p)}]);
if(!html.includes('Worker')||!html.includes('CC 1')||!html.includes('base PP 300')||html.includes('C-input')||html.includes('NEVER SHOW'))throw Error(html);
const review=planReviewHtml(p);
if(review.includes('data-upgrade-copy')||review.includes('data-combine-action="sign"')||!review.includes('Close')||review.includes('C-input'))throw Error(review);
""", entry=STATIC / 'checkout.js')
