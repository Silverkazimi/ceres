from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from ceres.candidates import rank_candidates, cooldown_state
from ceres.op_preview import build_preview, PreviewValidationError
from ceres.stage_plans import check_plans
from tests.ceres.test_op_preview import _plot
from tests.ceres.vm_context import run_in_module_context

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)
TYPES = {"totem": "STK-LND-TOT", "title": "STK-LND-TTL", "power_core": "STK-LND-PCR"}

def fixture(group):
    source, target = "z-source", "a-target"
    item = {"asset_ref": "item-ref", "uid": "I-322-test", "name": "Test item", "item_detail_id": 322,
            "stake_type_uid": TYPES[group], "stake_ref_uid": source, "player": "player0005", "boost": .25}
    report = {"plots": [_plot(source, workers=0), _plot(target, workers=0)], "workers": [], "cards": [],
              "bonus_assets": [item], "unassigned_items": {group: {"capturable": True, "uids": [], "items": []}}}
    moves = [{"deed_uid": source, "group_id": group, "slot_index": 0, "action": "unstake", "asset_ref": "item-ref"},
             {"deed_uid": target, "group_id": group, "slot_index": 0, "action": "stake", "asset_ref": "item-ref"}]
    return report, moves

@pytest.mark.parametrize("group", TYPES)
@pytest.mark.parametrize("reverse", [False, True])
def test_item_move_one_transaction_removal_before_placement(group, reverse):
    report, moves = fixture(group)
    result = build_preview(report, list(reversed(moves)) if reverse else moves, account="player0005", now=NOW)
    assert result["chunk_count"] == 1
    ops = [json.loads(op[1]["json"]) for op in result["chunks"][0]["operations"]]
    assert len(ops) == 2
    assert ops[0]["deed_uid"] == "z-source" and ops[0]["unstake"]["items"] == [{"item_uid": "I-322-test"}]
    assert ops[1]["deed_uid"] == "a-target" and ops[1]["stake"]["items"][0]["item_uid"] == "I-322-test"

@pytest.mark.parametrize("group", TYPES)
def test_released_item_picker_and_reservation(group):
    report, moves = fixture(group)
    kwargs = dict(player="player0005", now=NOW, details_by_id={}, sort="boost")
    assert rank_candidates(report, "a-target", group, **kwargs)["candidates"] == []
    rows = rank_candidates(report, "a-target", group, released_asset_refs={"item-ref"}, **kwargs)["candidates"]
    assert len(rows) == 1 and rows[0]["source_deed_uid"] == "z-source"
    assert rows[0]["cooldown"]["ready"] is True
    assert rank_candidates(report, "a-target", group, released_asset_refs={"item-ref"}, reserved_asset_refs={"item-ref"}, **kwargs)["candidates"] == []
    assert rank_candidates(report, "z-source", group, released_asset_refs={"item-ref"}, **kwargs)["candidates"] == []
    assert rank_candidates(report, "a-target", group, released_asset_refs={"fake"}, **kwargs)["candidates"] == []

@pytest.mark.parametrize("group", TYPES)
def test_move_plan_survives_validation_and_duplicate_destination_refused(group):
    report, moves = fixture(group)
    assert all(v["verdict"] == "ok" for v in check_plans(report, moves, account="player0005", now=NOW))
    report["plots"].append(_plot("third", workers=0))
    with pytest.raises(PreviewValidationError):
        build_preview(report, moves + [{**moves[1], "deed_uid": "third"}], account="player0005", now=NOW)


def test_item_moves_sign_together_and_source_undo_removes_destination():
    run_in_module_context(r"""
(async()=>{
BOARD={player:'player0005',plots:[{deed_uid:'source'},{deed_uid:'target'}]};
renderCheckoutSurface=()=>{};refreshStageImpactForRow=()=>{};updateMountedRow=()=>{};restoreStagedMove=()=>{};stageToast=()=>{};S.CERES_PICKER=null;
const source={deed_uid:'source',group_id:'totem',slot_index:0,action:'unstake',asset_ref:'item'};
const target={deed_uid:'target',group_id:'totem',slot_index:0,action:'stake',asset_ref:'item',_source_deed_uid:'source',cooldown:{ready:true}};
CERES_STAGE.push(source);
if(stagedReleasedItemRefs('totem','target').join()!=='item')throw Error('removed item unavailable');
CERES_STAGE.push(target);
if(stagedReleasedItemRefs('totem','third').length)throw Error('double allocation offered');
if(stageSetAside(target)!==null || checkoutSignableEntries().length!==2)throw Error('restake parked');
await removeCheckoutStage(source);
if(CERES_STAGE.length)throw Error('orphaned destination after undo');
CERES_STAGE.push(source,target);target.txId='signed';target.queued_at='now';
await removeCheckoutStage(source);
if(CERES_STAGE.length!==2)throw Error('signed linked move was undone');
})().then(()=>process.exit(0),e=>{console.error(e);process.exit(1)});
""", entry=ROOT/'ceres/static/checkout.js')

@pytest.mark.parametrize("group", TYPES)
def test_item_swap_unstakes_both_before_restaking(group):
    report, moves = fixture(group)
    report["bonus_assets"].append({**report["bonus_assets"][0], "asset_ref": "other-ref", "uid": "I-322-other", "stake_ref_uid": "a-target"})
    swaps = [{**moves[0], "action": "replace", "asset_ref": "other-ref", "evict_asset_ref": "item-ref"},
             {**moves[1], "action": "replace", "evict_asset_ref": "other-ref"}]
    preview = build_preview(report, swaps, account="player0005", now=NOW)
    assert preview["chunk_count"] == 1
    ops = [json.loads(op[1]["json"]) for op in preview["chunks"][0]["operations"]]
    assert len(ops) == 4
    assert all(op["unstake"]["items"] for op in ops[:2])
    assert all(op["stake"]["items"] for op in ops[2:])


def test_large_move_batch_preserves_remove_before_reuse_across_chunks():
    report, moves = fixture("totem")
    report["plots"] = []; report["bonus_assets"] = []; moves = []
    for i in range(6):
        for prefix in ["z", "a"]: report["plots"].append(_plot(f"{prefix}-{i}", workers=0))
        report["bonus_assets"].append({"asset_ref": f"ref-{i}", "uid": f"I-1-{i}", "stake_type_uid": "STK-LND-TOT", "stake_ref_uid": f"z-{i}"})
        for prefix,action in [("z","unstake"),("a","stake")]:
            moves.append({"deed_uid": f"{prefix}-{i}", "group_id":"totem", "action":action, "asset_ref":f"ref-{i}"})
    preview=build_preview(report,moves,account="player0005",now=NOW)
    assert preview["chunk_count"]==3
    removed=set()
    for chunk in preview["chunks"]:
        for op in chunk["operations"]:
            body=json.loads(op[1]["json"])
            removed.update(item["item_uid"] for item in body["unstake"]["items"])
            assert all(item["item_uid"] in removed for item in body["stake"]["items"])
