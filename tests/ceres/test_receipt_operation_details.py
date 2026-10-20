import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_worker_receipt_names_slots_directions_and_missing_evidence():
    source = (ROOT / 'ceres/static/operations.js').read_text()
    source = source[source.index('function operationResultName'):source.index('function setVerificationNotice')]
    script = source + r"""
const op = (side, cards, deed='plot') => ['custom_json', {id:'sm_stake_change',json:JSON.stringify({deed_uid:deed,[side]:{cards}})}];
const entry={deed_uid:'plot',group_id:'worker',slot_index:4,action:'replace',asset_ref:'opaque-new',evict_asset_ref:'opaque-old',_previous_asset:{card_uid:'old',name:'Medusa'},_asset:{uid:'new',name:'Tireless Farmhand'}};
const remove=op('unstake',[{card_uid:'old'}]);
const add=op('stake',[{card_uid:'new',slot:'5',stake_type_uid:'STK-LND-WKR'}]);
const chunk={operations:[remove,add],receiptEntries:[entry]};
const results=perOperationResults({op_results:[{index:1,success:false,error:'Rejected'},{index:0,success:true}]},chunk);
console.log(JSON.stringify({results,missing:operationResultName(remove),wrongPlot:operationResultName(op('unstake',[{card_uid:'old'}],'other'),[entry]),pending:perOperationResults({op_results:[{index:0,success:true}]},chunk)[1]}));
"""
    result=json.loads(subprocess.check_output(['node','-e',script],cwd=ROOT,text=True))
    assert [r['label'] for r in result['results']] == ['Unstake Medusa · Worker slot 5','Stake Tireless Farmhand · Worker slot 5']
    assert result['results'][0]['success'] is True
    assert result['results'][1]['success'] is False
    assert result['results'][1]['outcome'] == 'Rejected'
    assert result['missing'] == result['wrongPlot'] == 'Unstake old · Card'
    assert result['pending']['outcome'] == 'game result not available yet'
