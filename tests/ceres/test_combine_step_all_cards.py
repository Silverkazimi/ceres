""": step 3 "Combine" lists every copy that goes in -- staked, cooling and free -- and names the result as CC / max CC and level."""
from tests.ceres.test_chain_model import STATIC
from tests.ceres.vm_context import run_in_module_context


def test_combine_step_lists_every_copy_with_its_state_and_the_result():
    run_in_module_context(r"""
BOARD={player:'alice',plots:[{deed_uid:'plot-one',plot:{region:1,tract:2,plot:3}}]};
const now=Date.parse('2026-10-14T12:00:00Z'); const soon='2026-10-16T09:30:00Z';
const plan={version:2,id:'cc-test',state:'cooling',problem:null,origin:{kind:'tile',asset_uid:'C7-ANCHOR0001',stage_back:true},
 card:{name:'Hamford Sparkwright',level:4,bcx:30,base_pp:150},expected:{level:5,bcx:61,bcx_max:400,base_pp:305,survivor_uid:'C7-ANCHOR0001'},ready_at:soon,
 inputs:[{uid:'C7-ANCHOR0001',cc:30,kind:'staked',status:'active',source:{deed_uid:'plot-one',slot_index:0},unstake:{tx_id:'t1',signed_at:'2026-10-14T10:00:00Z',confirmed_at:'2026-10-14T10:05:00Z'},ready_at:soon},
  {uid:'C7-STAKED00002',cc:20,kind:'staked',status:'active',source:{deed_uid:'plot-one',slot_index:1},unstake:{tx_id:'t2',signed_at:'2026-10-14T10:00:00Z'},ready_at:null},
  {uid:'C7-COOLING0003',cc:10,kind:'cooling',status:'active',ready_at:soon},
  {uid:'C7-FREE0000004',cc:1,kind:'free',status:'active',ready_at:null}]};
const html=chainCardHtml('cc-test',plan,now);
const step=html.slice(html.indexOf('chain-step-3'),html.indexOf('chain-step-4'));
const text=step.replace(/<[^>]*>/g,' ');
if(/C7-[A-Z0-9]+|Card ID|UID/.test(text)) throw Error('identifier on screen');
if((step.match(/class="chain-input"/g)||[]).length!==4) throw Error('step 3 must list all four copies: '+step);
if(!text.includes('The worker being upgraded')||text.indexOf('CC 30')>text.indexOf('CC 20')) throw Error('anchor first');
for(const words of ['Unstaked, cooling until','Staked, unstake signed','Cooling until','Free in the collection']) if(!text.includes(words)) throw Error('missing state: '+words);
if(!text.includes('61 / 400 CC → Level 5')) throw Error('missing result: '+text);
if((step.match(/Leave out/g)||[]).length!==1) throw Error('only the free copy offers Leave out in step 3');
// Without a known max CC the result still names CC and level.
delete plan.expected.bcx_max;
const bare=chainCardHtml('cc-test',plan,now).replace(/<[^>]*>/g,' ');
if(!bare.includes('61 CC → Level 5')) throw Error('result without max');
// A signed combine keeps the list and the result under its status line.
plan.state='combine_signed'; plan.combine={nonce:'n',operation:['custom_json',{}]};
const signed=chainCardHtml('cc-test',plan,now);
const signedStep=signed.slice(signed.indexOf('chain-step-3'),signed.indexOf('chain-step-4'));
if(!signedStep.includes('Do not combine these copies again')||(signedStep.match(/class="chain-input"/g)||[]).length!==4||!signedStep.includes('61 CC → Level 5')) throw Error('signed step lost the list');
if(/C7-[A-Z0-9]+/.test(signed.replace(/<[^>]*>/g,''))) throw Error('identifier on screen after signing');
""", entry=STATIC / 'combine-chain-ui.js')
