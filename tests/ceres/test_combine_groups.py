""": big combines group identical free and cooling copies into one row, show five rows per step with "Show N more",
and keep every staked copy listed. "Leave out all" on a grouped row leaves out every copy of the group."""
from tests.ceres.test_chain_model import STATIC
from tests.ceres.test_sign_order import SETUP
from tests.ceres.vm_context import run_in_module_context

BIG_PLAN = r"""
BOARD={player:'alice',plots:[{deed_uid:'plot-one',plot:{region:1,tract:2,plot:3}}]};
const now=Date.parse('2026-10-14T12:00:00Z'); const soon='2026-10-16T09:30:00Z';
const inputs=[{uid:'C7-ANCHOR0001',cc:30,kind:'staked',status:'active',source:{deed_uid:'plot-one',slot_index:0},unstake:{tx_id:'t1',signed_at:'2026-10-14T10:00:00Z',confirmed_at:'2026-10-14T10:05:00Z'},ready_at:soon},
 {uid:'C7-STAKED00002',cc:20,kind:'staked',status:'active',source:{deed_uid:'plot-one',slot_index:1},unstake:{tx_id:'t2',signed_at:'2026-10-14T10:00:00Z'},ready_at:null}];
for(let i=0;i<3;i++) inputs.push({uid:'C7-COOLING000'+i,cc:1,kind:'cooling',status:'active',ready_at:soon.replace(':00Z',':'+(10+i)+'Z')});
for(let i=0;i<12;i++) inputs.push({uid:'C7-FREE000000'+String(i).padStart(2,'0'),cc:1,kind:'free',status:'active',ready_at:null});
const plan={version:2,id:'cc-big',state:'cooling',problem:null,origin:{kind:'tile',asset_uid:'C7-ANCHOR0001',stage_back:true},
 card:{name:'Hamford Sparkwright',level:4,bcx:30,base_pp:150},expected:{level:5,bcx:65,bcx_max:400,base_pp:325,survivor_uid:'C7-ANCHOR0001'},ready_at:soon,inputs};
const step=(html,n)=>html.slice(html.indexOf('chain-step-'+n),html.indexOf('chain-step-'+(n+1)));
const text=(html)=>html.replace(/<[^>]*>/g,' ');
const rows=(html)=>(html.match(/class="chain-input"/g)||[]).length;
"""


def test_step_three_groups_free_and_cooling_copies_and_keeps_the_counts_true():
    run_in_module_context(BIG_PLAN + r"""
const html=chainCardHtml('cc-big',plan,now);
if(/C7-[A-Z0-9]+|Card ID|UID/.test(text(html))) throw Error('identifier on screen');
const three=step(html,3); const words=text(three);
// worker, one staked, one cooling group, one free group -- four rows, no "Show more" needed.
if(rows(three)!==4||/Show \d+ more/.test(three)) throw Error('step 3 rows: '+three);
if(!words.includes('The worker being upgraded')||words.indexOf('CC 30')>words.indexOf('CC 20')) throw Error('worker first');
if(!words.includes('3 cooling copies · CC 1 each')||!words.includes('Cooling until')) throw Error('cooling group: '+words);
if(!words.includes('12 free copies · CC 1 each')||!words.includes('Free in the collection')) throw Error('free group: '+words);
if(!words.includes('65 / 400 CC → Level 5')) throw Error('result line');
if((three.match(/Leave out all/g)||[]).length!==1||/>Leave out</.test(three)) throw Error('one Leave out all on the free group, no per-copy Leave out: '+three);
if(!/data-upgrade-action="leave-group" data-chain-id="cc-big" data-chain-inputs="5 6 7 8 9 10 11 12 13 14 15 16"/.test(three)) throw Error('group indices: '+three);
// The counts count copies, not rows.
if(!words.includes('17 cards into one')||!text(html).includes('17 cards · 2 from land')) throw Error('counts: '+text(html));
// Step 2 groups the cooling copies too (same minute) and offers Leave out all there; the staked rows stay separate.
const two=step(html,2);
if(rows(two)!==3||!text(two).includes('3 cooling copies · CC 1 each')||(two.match(/Leave out all/g)||[]).length!==1) throw Error('step 2: '+two);
// Cooling copies ending in different minutes stay separate rows.
plan.inputs[3].ready_at='2026-10-16T09:31:00Z';
const split=step(chainCardHtml('cc-big',plan,now),3);
if(rows(split)!==5||!text(split).includes('2 cooling copies')||!/CC 1 · Cooling copy/.test(text(split))) throw Error('split cooling: '+split);
""", entry=STATIC / 'combine-chain-ui.js')


def test_nine_staked_copies_collapse_to_five_rows_and_show_more_opens_the_list():
    run_in_module_context(BIG_PLAN + r"""
renderCheckoutSurface=()=>{};
plan.inputs=[plan.inputs[0]]; for(let i=1;i<9;i++) plan.inputs.push({uid:'C7-STAKED0000'+i,cc:2,kind:'staked',status:'active',source:{deed_uid:'plot-one',slot_index:i},unstake:null,ready_at:null});
const one=step(chainCardHtml('cc-big',plan,now),1);
if(rows(one)!==6||!/>Show 4 more</.test(one)||!/data-upgrade-action="show-more" data-chain-id="cc-big" data-chain-step="1"/.test(one)) throw Error('collapsed step 1: '+one);
if(text(step(chainCardHtml('cc-big',plan,now),3)).includes('copies · CC')) throw Error('staked copies must never group');
(async()=>{
const event={preventDefault(){},stopPropagation(){},target:{closest:(selector)=>selector==='[data-upgrade-action]'?{dataset:{upgradeAction:'show-more',chainId:'cc-big',chainStep:'1'}}:null}};
if(await handleUpgradeAction(event)!==true) throw Error('show-more not handled');
const opened=step(chainCardHtml('cc-big',plan,now),1);
if(rows(opened)!==9||/Show \d+ more/.test(opened)) throw Error('opened step 1: '+opened);
// Another step of the same plan stays collapsed until its own Show more.
const three=step(chainCardHtml('cc-big',plan,now),3);
if(rows(three)!==6||!/>Show 4 more</.test(three)) throw Error('step 3 still collapsed: '+three);
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'combine-chain-ui.js')


def test_leave_out_all_leaves_out_every_copy_of_the_group():
    run_in_module_context(SETUP + BIG_PLAN + r"""
(async()=>{
plan.origin.stage_back=false; saveChain(plan.id,plan);
confirmChainRemoval=async(_plan,inputs,cancel)=>{ if(cancel||inputs.length!==12) throw Error('the dialog must name the 12 copies without cancelling'); return 'keep_off'; };
chainPost=async(route,body)=> route==='inputs' ? {copies:activeChainInputs(plan).map(i=>({uid:i.uid,eligible:true,kind:i.unstake?.confirmed_at?'cooling':i.kind,bcx:i.cc,source:i.source,ready_at:i.ready_at})),observed_at:'2026-10-14T12:30:00Z'} : {expected:plan.expected};
const html=chainCardHtml('cc-big',plan,now);
const indices=step(html,3).match(/data-upgrade-action="leave-group" data-chain-id="cc-big" data-chain-inputs="([\d ]+)"/)[1];
const event={preventDefault(){},stopPropagation(){},target:{closest:(selector)=>selector==='[data-upgrade-action]'?{dataset:{upgradeAction:'leave-group',chainId:'cc-big',chainInputs:indices}}:null}};
await handleUpgradeAction(event);
const saved=chainPlans()['cc-big'];
if(saved.state==='cancelled') throw Error('five copies remain, no cancel');
if(saved.inputs.filter(i=>i.kind==='free').some(i=>i.status!=='left_out'||i.left_out_choice!=='keep_off')) throw Error('every free copy left out');
if(saved.inputs.filter(i=>i.kind!=='free').some(i=>i.status!=='active')) throw Error('staked and cooling copies untouched');
if(activeChainInputs(saved).length!==5) throw Error('five active copies remain');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'checkout.js')
