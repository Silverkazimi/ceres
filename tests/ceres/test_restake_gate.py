""" : a nonexistent card is kept but never signed, even at expiry."""
from pathlib import Path
from tests.ceres.vm_context import run_in_module_context
STATIC = Path(__file__).parents[2] / 'ceres' / 'static'


def test_placement_gate_reuses_blocked_restakes_and_undo_keeps_plan():
    run_in_module_context(r"""
BOARD={plots:[]}; PLANS.chains={'cc-test':{state:'cooling',inputs:[]}};
renderCheckoutSurface=()=>{};refreshStageImpactForRow=()=>{};
const asset={planned:true,chain_id:'cc-test',name:'Worker',level:8};
stageForSlot('d','worker',2,'stake','planned:cc-test',999,null,'Worker',null,null,{ready:true},asset);
const entry=CERES_STAGE[0];
if(entry.action!=='stake'||entry.chain_id!=='cc-test'||stageSetAside(entry)!=='blocked'||entry.cooldown.ready_at!==null) throw Error('gate');
entry.cooldown={ready:true,state:'ready'};
if(stageSetAside(entry)!=='blocked') throw Error('expiry released planned card');
const kept=planFromEntry(entry);
CERES_STAGE.length=0;
stageFromPlan(kept,{verdict:'ok',cooldown:{ready:true}});
if(CERES_STAGE[0].cooldown.ready!==false||stageSetAside(CERES_STAGE[0])!=='blocked') throw Error('restore released');
CERES_STAGE.length=0;
if(!chainPlans()['cc-test']) throw Error('undo removed plan');
""", entry=STATIC / 'checkout.js')
