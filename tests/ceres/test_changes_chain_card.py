""" rendered plan text, states and no exposed card identifiers."""
from tests.ceres.test_chain_model import STATIC
from tests.ceres.vm_context import run_in_module_context


def test_plan_card_and_window_no_card_ids_and_four_steps():
    run_in_module_context(r"""
BOARD={player:'alice',plots:[{deed_uid:'plot-one',plot:{region:1,tract:2,plot:3}}]};
const card={name:'Lord Arianthus',level:2,bcx:3,base_pp:300};
const copies=[{uid:'C7-ABCDEF123456',bcx:3,kind:'staked',eligible:true,source:{deed_uid:'plot-one',slot_index:0}},
 {uid:'C7-987654FEDCBA',bcx:2,kind:'cooling',eligible:true,ready_at:'2026-10-07T00:00:00Z'}];
const expected={bcx:5,level:3,base_pp:500,survivor_uid:copies[0].uid};
const plan=createChain({id:'cc-test',card,origin:{kind:'tile',asset_uid:copies[0].uid},copies,expected});
const view={card,copies,anchor:copies[0].uid,selected:copies.map(c=>c.uid),expected,thresholds:[1,3,5,11],target:'manual',losses:[]};
const windowHtml=upgradeWindowHtml(view);
if(!windowHtml.includes('data-upgrade-copy="0" checked disabled')||!windowHtml.includes('Stage the upgrade plan')) throw Error('locked anchor');
for(const state of ['planned','cooling','ready','problem']) {
 if(state!=='planned') plan.inputs[0].unstake={tx_id:'transaction',confirmed_at:'2026-10-05T00:00:00Z'};
 plan.inputs[0].ready_at='2026-10-08T00:00:00Z';
 if(state==='problem') plan.inputs[1].status='unavailable';
 const html=chainCardHtml('cc-test',plan,Date.parse(state==='ready'?'2026-10-09':'2026-10-05'));
 const visible=(html+windowHtml).replace(/<[^>]*>/g,'');
 if(/C7-[A-Z0-9]+|Card ID|UID/.test(visible)) throw Error('identifier on screen');
 for(let i=1;i<=4;i++) if(!html.includes('chain-step-'+i)) throw Error('missing step');
 if(state==='problem'&&!html.includes('Not enough cards')) throw Error('missing problem');
}
""", entry=STATIC / 'combine-chain-ui.js')


def test_countdown_and_entry_integration():
    assert '#checkout-panel [data-countdown-until]' in (STATIC / 'countdown.js').read_text()
    checkout = (STATIC / 'checkout.js').read_text()
    assert '${upgradePlansHtml()}${groupHtml}' in checkout
    assert 'const n = activeChainPlans().length +' in checkout
    assert 'leaveChainUnstake(entry)' in checkout
    assert 'recordChainUnstake(entry, \'confirmed\'' in (STATIC / 'operations.js').read_text()
