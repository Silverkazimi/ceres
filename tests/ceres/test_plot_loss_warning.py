""": name every source plot once, including multiple copies on a plot."""
from tests.ceres.test_chain_model import STATIC
from tests.ceres.vm_context import run_in_module_context


def test_all_source_plots_named_and_free_cooling_excluded():
    run_in_module_context(r"""
BOARD={plots:[{deed_uid:'d1',plot:'41-2-27'},{deed_uid:'d2',plot:'9-1-1'}]};
const copies=[{kind:'staked',source:{deed_uid:'d1',slot_index:0}},
 {kind:'staked',source:{deed_uid:'d1',slot_index:1}},
 {kind:'staked',source:{deed_uid:'d2',slot_index:2}}, {kind:'free'}, {kind:'cooling'}];
if(chainPlotLosses(copies).length!==2) throw Error('distinct source plots');
const html=plotLossWarningHtml(copies,'Worker');
if(!html.includes('Two plots lose a worker')||!html.includes('041-02-027')||!html.includes('009-01-001')) throw Error(html);
if(plotLossWarningHtml([{kind:'free'},{kind:'cooling'}],'Worker')!=='') throw Error('false warning');
""", entry=STATIC / 'combine-chain-ui.js')
