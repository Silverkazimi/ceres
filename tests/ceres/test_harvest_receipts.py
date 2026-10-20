import pytest
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal

from ceres import harvest, harvest_evidence
from ceres import routes_harvest as routes
from ceres.routes_harvest_routing import _receipts

ROOT = Path(__file__).resolve().parents[2]
TX = "0fcd432c88ef8df449fb5a179cc538eb60155cfe"
IR8220_TX = "016d23b2a11787ca06841b9a9aa018786ffbe4ff-2"
IR8220_DEED = "I-294-237bfc96053480"

def fixture():
    if not Path("work/notes/evidence-game-result.json").exists():
        pytest.skip("private work/ evidence file not included in this export")
    return json.loads((ROOT / "work/notes/evidence-game-result.json").read_text())

def prepared(monkeypatch, tmp_path, response=None):
    import importlib
    verdict_module = importlib.import_module("fides.game_verdict")
    info = (response or fixture())["trx_info"]
    payload = json.loads(info["data"])
    op = {"lookup_id": TX, "nonce": payload["n"], "success": True,
          "data": payload, "result": json.loads(info["result"])}
    monkeypatch.setattr(verdict_module, "game_verdict", lambda *a, **k: {"status": "game_accepted", "evidence": {"operations": [op]}})
    monkeypatch.setattr(routes, "record_verification", lambda **k: None)
    monkeypatch.setattr(routes, "_paced", lambda fn, *args: fn(*args))
    monkeypatch.setattr(routes, "_client", SimpleNamespace(
        resource_history=lambda tx: {"status": "success", "data": []},
        reward_actions=lambda deed, limit: {"data": [{"trx_id": TX, "deed_uid": deed}]}))
    preview = {"preview_id": "fixture", "preview_digest": "fixture", "chunks": [{"operations": [], "members": [{"op": "harvest_all", "region_uid": "PR-BRL-41", "nonce": payload["n"], "deed_uids": []}]}]}
    record = harvest_evidence.begin_attempt(tmp_path, "player0005", preview, 0, "region41fixture")
    harvest_evidence.update_attempt(tmp_path, "player0005", record["attempt_id"], tx_id=TX, state="submitted")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(root=tmp_path))))
    return request, record

def test_real_region_harvest_recovers_amounts_without_ledger(monkeypatch, tmp_path):
    request, record = prepared(monkeypatch, tmp_path)
    result = routes._verify_harvest_attempt(request, "player0005", {"attempt_id": record["attempt_id"]})
    assert result["outcome"] == "success"
    member = result["members"][0]
    assert member["received_by_token"] == {"GRAIN": 72920.304, "WOOD": 26898.019, "STONE": 10142.645, "IRON": 3710.618, "SPS": 101.384}
    assert member["costs_by_token"] == {"GRAIN": 76690.946, "WOOD": 5092.2, "STONE": 2036.88, "IRON": 509.22}
    assert "regional_amounts" in member["evidence"]
    saved = harvest_evidence.load_attempt(tmp_path, "player0005", record["attempt_id"])
    assert saved["members"][0]["received_by_token"] == member["received_by_token"]
    budget = _receipts(request, "player0005", [record["attempt_id"]])
    assert budget[("PR-BRL-41", "WOOD")] == Decimal("26898.019")
    assert budget[("inventory", "SPS")] == Decimal("101.384")

def test_old_reward_only_terminal_receipt_is_repaired_before_routing(monkeypatch, tmp_path):
    request, record = prepared(monkeypatch, tmp_path)
    member = {**record["members"][0], "outcome": "success", "game_state": "accepted", "evidence": ["regional_rewards"], "received_by_token": {}, "costs_by_token": {}}
    harvest_evidence.update_attempt(tmp_path, "player0005", record["attempt_id"], state="reconciled", members=[member])
    budget = _receipts(request, "player0005", [record["attempt_id"]])
    assert budget[("PR-BRL-41", "GRAIN")] == Decimal("72920.304")

def test_reward_evidence_without_amounts_is_never_terminal_success(monkeypatch, tmp_path):
    response = fixture()
    result = json.loads(response["trx_info"]["result"])
    for row in result["result"]["data"]["results"]:
        row.pop("received_amount")
    response["trx_info"]["result"] = json.dumps(result)
    request, record = prepared(monkeypatch, tmp_path, response)
    verified = routes._verify_harvest_attempt(request, "player0005", {"attempt_id": record["attempt_id"]})
    assert verified["outcome"] == "pending"
    assert verified["members"][0]["game_state"] == "accepted"
    assert harvest_evidence.load_attempt(tmp_path, "player0005", record["attempt_id"])["state"] == "submitted"

def test_invalid_amounts_are_unknown_and_real_empty_results_are_known():
    assert harvest.regional_harvest_effects([{"resource_symbol": "GRAIN", "received_amount": "NaN"}]) is None
    assert harvest.regional_harvest_effects([{"resource_symbol": "GRAIN", "received_amount": -1}]) is None
    assert harvest.regional_harvest_effects([])["received"] == {}
