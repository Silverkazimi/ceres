"""The live harvest verifier and the receipt model consume the same game result (: through game_read)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ceres import game_read, receipt_model, routes_harvest
import fides.game_verdict as verdict_module

FIXTURE = Path(__file__).parent / "fixtures" / "receipts" / "lookup-harvest_all.json"


def test_harvest_verifier_and_receipt_model_are_identical(monkeypatch, tmp_path):
    lookup = json.loads(FIXTURE.read_text())
    info = lookup["trx_info"]
    payload = json.loads(info["data"])
    member = {"op": "harvest_all", "region_uid": payload["region_uid"],
              "nonce": payload["n"], "deed_uids": []}
    record = {"attempt_id": "fixture", "tx_id": info["id"], "members": [member]}
    operation = {"nonce": payload["n"], "lookup_id": info["id"], "success": True,
                 "data": payload, "result": json.loads(info["result"])}
    fragment = {"status": "success", "data": []}
    calls = []

    monkeypatch.setattr(routes_harvest, "_config", lambda request: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(game_read, "_MINUTE_CALLS", {})
    monkeypatch.setattr(routes_harvest.evidence, "load_attempt", lambda root, player, attempt: record)
    monkeypatch.setattr(routes_harvest.evidence, "update_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes_harvest, "_reward_card_art", lambda members: None)
    monkeypatch.setattr(routes_harvest, "record_verification", lambda **kwargs: None)
    monkeypatch.setattr(verdict_module, "game_verdict", lambda *args, **kwargs: {
        "status": "game_accepted", "evidence": {"operations": [operation]}})

    def fake_get(host, path, *, timeout=12):
        calls.append((host, path))
        assert host == "vapi.splinterlands.com"
        assert path == "/land/resources/fragment_history/" + info["id"]
        return fragment

    monkeypatch.setattr(game_read, "_get", fake_get)

    verified = routes_harvest._verify_harvest_attempt(object(), info["player"], {"attempt_id": "fixture"})
    header = {"tx_id": info["id"], "operation_index": 0, "player": info["player"],
              "custom_json_id": "sm_land_operation", "payload": payload, "timestamp": None}
    expected = receipt_model.receipt(header, lookup, {"fragment_history": fragment})
    assert verified["members"][0]["receipt"] == expected
    assert calls == [("vapi.splinterlands.com", "/land/resources/fragment_history/" + info["id"])]
