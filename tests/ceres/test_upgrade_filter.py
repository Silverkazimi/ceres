""": Upgrade is a remembered action-visibility preference, not a query."""
from tests.ceres.test_chain_model import STATIC
from tests.ceres.vm_context import run_in_module_context


def test_toggle_renders_grid_list_without_filtering_or_changing_title():
    run_in_module_context(r"""
(async()=>{
CURRENT_PLAYER='alice';const row={deed_uid:'plot',terrain:'Forest'};
BOARD={player:'alice',plots:[row]};
const candidate={uid:'A',asset_ref:'ref',name:'Worker',bcx:2,base_pp:100,upgrade:{eligible:true,usable_copies:2}};
const picker={row,deedUid:'plot',groupId:'worker',groupLabel:'Workers',slotIndex:0,mode:'replace',currentOccupant:{...candidate},
 candidates:[candidate,{...candidate,uid:'B',name:'Other',upgrade:{eligible:false,usable_copies:2}}],filters:{},sort:'name',viewMode:'grid',offset:0,total:2};
S.CERES_PICKER=picker;
updateMountedRow=()=>{};
const query=candidateQuery(picker,0);
const click=()=>handleSlotEditorClick({target:{closest:s=>s==='[data-picker-action]'?{dataset:{pickerAction:'toggle-upgrade'}}:null},preventDefault(){},stopPropagation(){}});
for(const view of ['grid','list']) {
 picker.viewMode=view;picker.filters.show_upgrade=false;
 let html=renderInlineCandidatePicker(row);
 if(!html.includes('data-picker-action="toggle-upgrade" aria-pressed="false"')) throw Error('default toggle');
 if(!html.includes('Upgrade Worker')||html.includes('data-upgrade-index="0"')) throw Error('off actions');
 await click();html=renderInlineCandidatePicker(row);
 if(!html.includes('data-picker-action="toggle-upgrade" aria-pressed="true"')||!html.includes('data-upgrade-index="0"')||html.includes('data-upgrade-index="1"')) throw Error('on actions');
 if(!html.includes('Upgrade Worker')||!html.includes('Other')) throw Error('filtered/title lost');
 if(view==='list'&&!renderCandidateRow(candidate,0,picker,true).includes('candidate-actions')) throw Error('list actions');
 if(candidateQuery(picker,0)!==query) throw Error('query changed');
 if(readPickerMemory('worker').filters.show_upgrade!==true) throw Error('not remembered');
 await click();
 if(readPickerMemory('worker').filters.show_upgrade!==false) throw Error('off not remembered');
}
picker.mobile=true;
const phone=renderInlineCandidatePicker(row);
if(!phone.includes('toggle-upgrade')||phone.includes('data-upgrade-index="occupant"')) throw Error('phone title/toggle');
CURRENT_PLAYER='bob';if(readPickerMemory('worker')!==null) throw Error('account leaked');
})().catch(e=>{console.error(e);process.exitCode=1});
""", entry=STATIC / 'picker-actions.js')


def test_default_off_and_full_width_shared_control_css():
    actions = (STATIC / 'picker-actions.js').read_text()
    assert 'show_upgrade: false' in actions
    css = (STATIC / 'buttons.css').read_text()
    assert '.candidate-grid-tools > button.candidate-upgrade.ceres-btn,' in css
    assert '.candidate-actions > button.candidate-upgrade.ceres-btn { width: 100%;' in css
    assert 'flex: 0 0 100%' in css


def test_phone_keeps_optional_card_actions_but_not_title_or_signing_controls():
    run_in_module_context(r"""
const selectors=[];
constrainMobileEditorActions({querySelectorAll(selector){selectors.push(selector);return [];}});
if(selectors[0].includes(', .candidate-upgrade,')||!selectors[0].includes('.picker-bar-title .candidate-upgrade')) throw Error('phone entry scope');
if(!selectors[0].includes('[data-combine-action]')) throw Error('phone signing control escaped');
""", entry=STATIC / 'rows.js')
