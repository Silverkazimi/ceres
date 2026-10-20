""" : source-based return markers, independent of survivor identity."""
from pathlib import Path
from tests.ceres.vm_context import run_in_module_context
STATIC = Path(__file__).parents[2] / 'ceres' / 'static'


def test_return_cases_and_separate_ordered_changes():
    run_in_module_context(r"""
const plan={state:'cooling',card:{name:'Worker'},inputs:[
 {uid:'secret-a',status:'active',kind:'staked',source:{deed_uid:'d',slot_index:2},unstake:{tx_id:'tx',confirmed_at:'yes'}},
 {uid:'secret-b',status:'active',kind:'staked',source:{deed_uid:'d',slot_index:3}}]};
PLANS.chains={'cc-test':plan};
const e={deed_uid:'d',group_id:'worker',slot_index:2,action:'stake',chain_id:'cc-test',asset_ref:'old'};
if(chainReturn(plan,'d',2).label!=='Returns upgraded to slot 3') throw Error('same slot');
if(chainReturn(plan,'d',1).label!=='Returns upgraded to this plot (was slot 3)') throw Error('other slot');
if(chainReturn(plan,'other',2)!==null) throw Error('other plot');
if(!chainPlacementBlocked(plan,'d',3).startsWith('Sign this slot')) throw Error('unsigned gate');
plan.inputs[1].unstake={tx_id:'tx2'};
if(!chainPlacementBlocked(plan,'d',3).startsWith('Wait a moment')) throw Error('flight gate');
e._asset={planned:true,chain_id:'cc-test',name:'Worker',level:8,uid:'secret-a'};
const label=stripStateLabel(e,'waiting');e.asset_ref='new-survivor';
if(stripStateLabel(e,'waiting')!==label||!label.includes('To slot 3')) throw Error('rebind');
const remove={deed_uid:'d',group_id:'worker',slot_index:3,action:'unstake',chain_id:'cc-test'};
const model=buildChangesModel([remove,e],stageSetAside);
if(model.intents.length!==2||model.groups.size!==1||model.counts.included!==1||model.counts.waiting!==1) throw Error('rows');
if(changeRowRank(e)>=changeRowRank(remove)) throw Error('phone order');
const marker=plannedReturnChip(e._asset,'d',2);
if(!marker.includes('Returns upgraded to slot 3')||marker.includes('secret-a')) throw Error('tile marker');
BOARD={plots:[{deed_uid:'d',plot:'041-02-027',workers:[]}]};
CERES_STAGE.push(e,remove);
const html=plotListHtml({row:BOARD.plots[0],deedUid:'d',items:[{entry:remove,status:'included',name:'Worker'},{entry:e,status:'waiting',name:'Worker'}]},new Map());
if((html.match(/class="ch-mrow is-/g)||[]).length!==2||html.includes('chain-step')||html.indexOf('To slot 3')>html.indexOf('Unstake')) throw Error('phone rows');
const step=chainCardHtml('cc-test',plan);
if(!step.includes('Returns upgraded to ')||!step.includes('041-02-027')||!step.includes(', slot 3')||!step.includes('signable after the combine')) throw Error('step 4');
if([label,marker,html,step].some(text=>text.includes('secret-')||text.includes('new-survivor'))) throw Error('UID');
""", entry=STATIC / 'checkout.js')
