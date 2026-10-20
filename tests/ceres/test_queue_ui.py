from pathlib import Path
from tests.ceres.vm_context import run_in_module_context
ROOT=Path(__file__).resolve().parents[2]


def test_queued_staged_changes_are_locked_and_labelled_queued():
    run_in_module_context(r"""
(async()=>{
CURRENT_PLAYER='alice';BOARD={player:'alice',plots:[]};
const entry={deed_uid:'D-1',group_id:'worker',slot_index:0,action:'stake',asset_ref:'C-1',txId:'a'.repeat(40),queued_at:new Date().toISOString()};
CERES_STAGE.push(entry);
if(changeStatus(entry,{})!=='working')throw Error('queued change became signable');
if(!stripStateLabel(entry,'working').includes('queued'))throw Error('queued change labelled sent');
stageToast=()=>{};
await removeCheckoutStage(entry);
if(!CERES_STAGE.includes(entry))throw Error('queued signed change could be undone');
})().then(()=>process.exit(0),e=>{console.error(e);process.exit(1)});
""",entry=ROOT/'ceres/static/checkout.js')


def test_worksite_group_prepares_all_before_signing_without_waiting_submission():
    run_in_module_context(r"""
(async()=>{
CURRENT_PLAYER='alice';BOARD={player:'alice',plots:[{deed_uid:'D-1'},{deed_uid:'D-2'},{deed_uid:'D-3'}]};
const events=[];
for(const row of BOARD.plots) CERES_STAGED_WORKSITES.set(row.deed_uid,{target:'grain',firstClear:false});
pendingWorksitePlanCount=()=>0;checkoutSignableEntries=()=>[];
freshRowForWorksiteSigning=async id=>BOARD.plots.find(row=>row.deed_uid===id);
updateMountedRow=()=>{};renderCheckoutSurface=()=>{};stageToast=()=>{};
prepareStagedWorksiteBuild=async row=>{events.push('prepare');return {row,account:'alice',chunk:{operations:[]}};};
prepareSigningBatch=async()=>null;walletReady=()=>true;
requestSignatureThroughGate=async()=>{events.push('sign');return {txId:'tx-'+events.length,queued:true,submitted:new Promise(()=>{})};};
const result=await confirmCheckoutWithWorksites();
if(!result.ok || events.join(',')!=='prepare,prepare,prepare,sign,sign,sign')throw Error(JSON.stringify({result,events}));
})().then(()=>process.exit(0),e=>{console.error(e);process.exit(1)});
""",entry=ROOT/'ceres/static/checkout.js')


