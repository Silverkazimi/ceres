"""routing happens immediately after harvest or not at all.

A failed run is closed on write, its failure is recorded and logged, completed
steps and transaction evidence stay untouched, and no later routing action
(skip, review, handoff, retry) is accepted for it. Supersedes the 
skip-and-continue recovery contract.
"""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceres.app import create_app
from ceres.config import CeresConfig
from ceres import harvest_routing_runs as runs
from ceres import routes_harvest_routing as routing_routes
from ceres import harvest_routing_validator as routing_validator


def _record(*, root: Path, steps: list[dict], state: str = "failed", group_review=None, phase: str = "after") -> dict:
    record = {
        "run_id": "a" * 32, "player": "player0005", "created": 1.0,
        "updated": 1.0, "phase": phase, "state": state,
        "scope": {"kind": "regions", "region_uids": ["PR-BRL-41"]},
        "steps": steps, "rules": [], "reserves": [], "slippage": "2.5",
        "harvest_attempt_ids": [],
    }
    if group_review is not None:
        record["group_review"] = group_review
    with runs.locked(root, "player0005") as folder:
        runs.write(folder, record)
    return record


def _read(root: Path) -> dict:
    with runs.locked(root, "player0005") as folder:
        return runs.read(folder, "a" * 32, "player0005")


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005")))


def test_failed_run_is_closed_with_recorded_failure_and_preserved_evidence(tmp_path: Path, caplog):
    confirmed = {"step_id": "f" * 32, "state": "confirmed", "kind": "sell", "tx_id": "e" * 40}
    failed = {"step_id": "b" * 32, "state": "failed", "kind": "sell",
              "tx_id": "c" * 40, "error": "The game refused this operation"}
    planned = {"step_id": "d" * 32, "state": "planned", "kind": "liquidity"}
    with caplog.at_level("ERROR"):
        _record(root=tmp_path / ".ceres", steps=[confirmed, failed, planned])

    saved = _read(tmp_path / ".ceres")
    assert saved["state"] == "failed"
    assert saved["routing_closed"] is True
    assert saved["failure"]["code"] == "CERES_ROUTING_FAILED"
    assert saved["failure"]["message"] == "The game refused this operation"
    assert saved["steps"][0] == confirmed
    assert saved["steps"][1]["tx_id"] == "c" * 40
    assert saved["steps"][2]["state"] == "planned"
    assert any("CERES_ROUTING_FAILED" in line and "a" * 32 in line for line in caplog.messages)


@pytest.mark.parametrize("path,body", [
    ("skip-failed", {"run_id": "a" * 32, "step_id": "b" * 32}),
    ("review", {"run_id": "a" * 32}),
    ("handoff", {"run_id": "a" * 32, "review_digest": "x"}),
    ("retry-unsent", {"run_id": "a" * 32}),
])
def test_closed_run_refuses_every_later_routing_action(tmp_path: Path, path: str, body: dict):
    failed = {"step_id": "b" * 32, "state": "failed", "kind": "sell",
              "tx_id": "c" * 40, "error": "Game refused"}
    _record(root=tmp_path / ".ceres", steps=[failed, {"step_id": "d" * 32, "state": "planned", "kind": "sell"}])

    response = _client(tmp_path).post(f"/api/harvest/routing/{path}?player=player0005", json=body)

    # handoff is refused by the broadcast-authority gate (403) before the run check.
    assert response.status_code in {403, 409}, response.text
    assert _read(tmp_path / ".ceres")["steps"][0]["state"] == "failed"


def test_stop_closes_an_active_run_and_is_idempotent(tmp_path: Path):
    steps = [{"step_id": "b" * 32, "state": "confirmed", "kind": "sell", "tx_id": "c" * 40},
             {"step_id": "d" * 32, "state": "planned", "kind": "sell"}]
    _record(root=tmp_path / ".ceres", steps=steps, state="ready",
            group_review={"member_ids": ["d" * 32], "digest": "digest"})
    client = _client(tmp_path)

    response = client.post("/api/harvest/routing/stop?player=player0005", json={"run_id": "a" * 32})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "stopped"
    assert result["routing_closed"] is True
    assert result["failure"]["code"] == "CERES_ROUTING_INTERRUPTED"
    assert result["group_review"]["digest"] == "digest"  # evidence of the reviewed group is kept
    assert result["steps"][0]["tx_id"] == "c" * 40
    again = client.post("/api/harvest/routing/stop?player=player0005", json={"run_id": "a" * 32})
    assert again.status_code == 200 and again.json()["state"] == "stopped"


def test_stop_never_reopens_or_alters_a_finished_run(tmp_path: Path):
    _record(root=tmp_path / ".ceres", steps=[{"step_id": "b" * 32, "state": "confirmed", "kind": "sell"}], state="complete")
    result = _client(tmp_path).post("/api/harvest/routing/stop?player=player0005", json={"run_id": "a" * 32}).json()
    assert result["state"] == "complete"
    assert "failure" not in result


def test_expired_dependent_release_retains_identity_and_cannot_retry(tmp_path: Path):
    step = {"step_id": "b" * 32, "state": "submitted", "kind": "sell", "tx_id": "c" * 40,
            "submitted_at": time.time(), "review": {"payload": {}, "step": {}, "operations": []}}
    _record(root=tmp_path / ".ceres", steps=[step], state="pending")
    client = _client(tmp_path)

    released = client.post("/api/harvest/routing/release-unsent?player=player0005", json={
        "run_id": "a" * 32, "step_id": "b" * 32, "tx_id": "c" * 40,
        "depends_on": "d" * 40, "queue_state": "expired",
    })

    assert released.status_code == 200, released.text
    saved = released.json()["steps"][0]
    assert saved["state"] == "failed" and saved["wallet_outcome"] == "expired"
    assert saved["tx_id"] == "c" * 40
    assert saved["unsent_reviews"][-1]["released_tx_id"] == "c" * 40
    retried = client.post("/api/harvest/routing/retry-unsent?player=player0005",
                          json={"run_id": "a" * 32})
    assert retried.status_code == 409, retried.text


def test_funding_handoff_accepts_a_61_second_review_when_exact_details_match(tmp_path: Path, monkeypatch):
    built = {"payload": {"params": {"n": "nonce", "to": "sps", "qty": 1}},
             "operations": [["custom_json", {"id": "sm_sps"}]], "authority": "Active",
             "amount": "1", "recipient": "sps"}
    review = {**built, "created": time.time() - 61, "digest": "funding-digest"}
    step = {"step_id": "b" * 32, "state": "settling", "kind": "sell",
            "funding": {"state": "reviewed", "review": review}}
    _record(root=tmp_path / ".ceres", steps=[step], state="settling")
    monkeypatch.setattr(routing_routes, "_funding_effects", lambda player, item: {"pending_swap_id": 1})
    monkeypatch.setattr(routing_routes, "build_sps_funding", lambda player, effects, kind: built)
    monkeypatch.setattr(routing_routes, "_funding_balance", lambda request, player, record, item: None)
    client = TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005",
                                               harvest_routing_signing_enabled=True)))

    response = client.post("/api/harvest/routing/funding?player=player0005", json={
        "action": "handoff", "run_id": "a" * 32, "step_id": "b" * 32,
        "review_digest": "funding-digest",
    })

    assert response.status_code == 200, response.text
    assert response.json()["authority"] == "Active"


@pytest.mark.parametrize("state,steps", [
    ("pending", [{"step_id": "b" * 32, "state": "pending", "kind": "sell", "tx_id": "c" * 40}]),
    ("ready", [{"step_id": "b" * 32, "state": "awaiting_wallet", "kind": "sell"}]),
])
def test_stop_never_closes_a_run_with_a_transaction_in_flight(tmp_path: Path, state: str, steps: list):
    _record(root=tmp_path / ".ceres", steps=steps, state=state)
    result = _client(tmp_path).post("/api/harvest/routing/stop?player=player0005", json={"run_id": "a" * 32}).json()
    assert result["state"] == state
    assert not result.get("routing_closed") and "failure" not in result


def test_before_harvest_failure_stays_reviewable(tmp_path: Path):
    failed = {"step_id": "b" * 32, "state": "failed", "kind": "transfer", "tx_id": "c" * 40, "error": "Game refused"}
    _record(root=tmp_path / ".ceres", steps=[failed], state="failed", phase="harvest")
    saved = _read(tmp_path / ".ceres")
    assert saved["state"] == "ready" and not saved.get("routing_closed") and "failure" not in saved
    assert saved["steps"][0]["state"] == "failed"  # the failed STEP keeps its state; only the run recovers


def test_before_harvest_failure_accepts_skip_failed(tmp_path: Path):
    failed = {"step_id": "b" * 32, "state": "failed", "kind": "transfer", "tx_id": "c" * 40, "error": "Game refused"}
    planned = {"step_id": "d" * 32, "state": "planned", "kind": "sell"}
    _record(root=tmp_path / ".ceres", steps=[failed, planned], state="failed", phase="harvest")

    response = _client(tmp_path).post("/api/harvest/routing/skip-failed?player=player0005",
                                      json={"run_id": "a" * 32, "step_id": "b" * 32})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] not in runs.TERMINAL
    assert not result.get("routing_closed")


def test_stop_closes_a_harvest_phase_run_without_routing_steps_in_flight(tmp_path: Path):
    root = tmp_path / ".ceres"
    with runs.locked(root, "player0005") as folder:
        record = {
            "run_id": "a" * 32, "player": "player0005", "created": 1.0,
            "updated": 1.0, "phase": "harvest", "state": "harvesting",
            "scope": {"kind": "regions", "region_uids": ["PR-BRL-41"]},
            "steps": [], "rules": [], "reserves": [], "slippage": "2.5",
            "harvest_attempt_ids": [], "harvest_attempts": ["x"],
        }
        runs.write(folder, record)
    result = _client(tmp_path).post("/api/harvest/routing/stop?player=player0005", json={"run_id": "a" * 32}).json()
    assert result["state"] == "stopped"
    assert result.get("routing_closed") is True


def test_not_sent_releases_an_abandoned_wallet_prompt(tmp_path: Path):
    root = tmp_path / ".ceres"
    record = _record(root=root, steps=[{"step_id": "b" * 32, "state": "awaiting_wallet", "kind": "sell"}],
                      state="awaiting_wallet", phase="after")
    response = _client(tmp_path).post("/api/harvest/routing/not-sent?player=player0005",
                                      json={"run_id": "a" * 32, "step_id": "b" * 32, "broadcasted": False})
    assert response.status_code == 200
    saved = _read(root)
    assert runs.in_flight(saved) is False
    stopped = _client(tmp_path).post("/api/harvest/routing/stop?player=player0005", json={"run_id": "a" * 32}).json()
    assert stopped.get("routing_closed") is True


def test_create_refuses_a_new_run_while_another_scope_is_in_flight(tmp_path: Path):
    _record(root=tmp_path / ".ceres", steps=[{"step_id": "b" * 32, "state": "pending", "kind": "sell", "tx_id": "c" * 40}],
            state="pending")
    with pytest.raises(Exception) as excinfo:
        runs.create(tmp_path / ".ceres", "player0005",
                    {"preview_id": "z" * 32, "preview_digest": "digest", "player": "player0005",
                     "phase": "before", "scope": {"kind": "regions", "region_uids": ["PR-BRL-99"]},
                     "slippage": "2.5", "rules": [], "reserves": [], "harvest_attempt_ids": [], "steps": []})
    message = str(excinfo.value)
    assert "still awaiting confirmation" in message and "PR-BRL-41" in message


def test_failure_endpoint_logs_a_single_line_reason(tmp_path: Path, caplog):
    with caplog.at_level("ERROR"):
        response = _client(tmp_path).post("/api/harvest/routing/failure?player=player0005",
                                          json={"reason": "quote\nexpired\r" + "x" * 2000})
    assert response.status_code == 200 and response.json() == {"logged": True}
    line = next(m for m in caplog.messages if "CERES_ROUTING_FAILED" in m)
    assert "player=player0005" in line and "\n" not in line and "quote expired" in line
    assert len(line) < 1200


def test_verify_expires_a_never_included_transaction(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing_routes, "_game_result", lambda player, step: {"state": "pending", "seen": False})
    step = {"step_id": "b" * 32, "state": "submitted", "kind": "sell", "tx_id": "c" * 40,
            "operation_index": 0, "submitted_at": time.time() - 5000, "review": {"payload": {}, "step": {}}}
    _record(root=tmp_path / ".ceres", steps=[step], state="pending", phase="after")
    client = _client(tmp_path)

    first = client.post("/api/harvest/routing/verify?player=player0005", json={"run_id": "a" * 32})
    assert first.status_code == 200, first.text
    assert first.json()["steps"][0]["state"] == "pending"  # one unseen sample is not enough

    second = client.post("/api/harvest/routing/verify?player=player0005", json={"run_id": "a" * 32})

    assert second.status_code == 200, second.text
    result = second.json()
    assert result["steps"][0]["state"] == "failed"
    assert result["steps"][0]["wallet_outcome"] == "expired"
    assert "seen" not in result["steps"][0]
    assert result["routing_closed"] is True


def test_verify_never_expires_a_settling_or_confirmed_step(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing_routes, "_game_result", lambda player, step: {"state": "pending", "seen": False})
    settling = {"step_id": "b" * 32, "state": "settling", "kind": "sell", "tx_id": "c" * 40,
                "operation_index": 0, "submitted_at": time.time() - 5000,
                "game_result": {"state": "settling"}, "review": {"payload": {}, "step": {}}}
    _record(root=tmp_path / ".ceres", steps=[settling], state="pending", phase="after")
    client = _client(tmp_path)

    client.post("/api/harvest/routing/verify?player=player0005", json={"run_id": "a" * 32})
    second = client.post("/api/harvest/routing/verify?player=player0005", json={"run_id": "a" * 32})

    assert second.status_code == 200, second.text
    assert second.json()["steps"][0]["state"] != "failed"

    submitted = {"step_id": "d" * 32, "state": "submitted", "kind": "sell", "tx_id": "e" * 40,
                 "operation_index": 0, "submitted_at": time.time() - 5000,
                 "game_result": {"state": "confirmed"}, "review": {"payload": {}, "step": {}}}
    _record(root=tmp_path / ".ceres", steps=[submitted], state="pending", phase="after")
    result = client.post("/api/harvest/routing/verify?player=player0005", json={"run_id": "a" * 32}).json()
    assert result["steps"][0]["state"] != "failed"


def test_verify_uses_submitted_at_not_handoff_at(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing_routes, "_game_result", lambda player, step: {"state": "pending", "seen": False})
    step = {"step_id": "b" * 32, "state": "submitted", "kind": "sell", "tx_id": "c" * 40,
            "operation_index": 0, "handoff_at": time.time() - 5000, "submitted_at": time.time() - 60,
            "review": {"payload": {}, "step": {}}}
    _record(root=tmp_path / ".ceres", steps=[step], state="pending", phase="after")
    client = _client(tmp_path)

    client.post("/api/harvest/routing/verify?player=player0005", json={"run_id": "a" * 32})
    result = client.post("/api/harvest/routing/verify?player=player0005", json={"run_id": "a" * 32}).json()

    assert result["steps"][0]["state"] == "pending"
    assert not result.get("routing_closed")


def test_game_result_reports_seen_from_the_real_lookup(tmp_path: Path, monkeypatch):
    tx = "c" * 40
    payload = {"op": "sell", "params": {"n": 1}}
    step = {"step_id": "b" * 32, "state": "submitted", "kind": "sell", "tx_id": tx,
            "operation_index": 0, "review": {"payload": payload, "step": {}}}

    class _NoTrxInfoResponse:
        data: dict = {}

    class _NoTrxInfoTransport:
        def __init__(self, *args, **kwargs):
            pass

        def get_json(self, url):
            return _NoTrxInfoResponse()

    monkeypatch.setattr(routing_routes, "FixedGetTransport", _NoTrxInfoTransport)
    unseen = routing_routes._game_result("player0005", step)
    assert unseen == {"state": "pending", "seen": False}

    class _PendingTrxInfoResponse:
        data = {"trx_info": {"id": tx, "player": "player0005", "type": "land_operation",
                              "data": payload, "success": None}}

    class _PendingTrxInfoTransport:
        def __init__(self, *args, **kwargs):
            pass

        def get_json(self, url):
            return _PendingTrxInfoResponse()

    monkeypatch.setattr(routing_routes, "FixedGetTransport", _PendingTrxInfoTransport)
    seen = routing_routes._game_result("player0005", step)
    assert seen == {"state": "pending", "seen": True}


def test_funding_verify_expires_a_never_seen_transfer(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing_routes, "funding_receipt", lambda player, tx, review: {"state": "pending", "seen": False})
    step = {"step_id": "b" * 32, "state": "settling", "kind": "sell", "tx_id": "c" * 40,
            "operation_index": 0, "review": {"payload": {}, "step": {}}, "game_result": {"x": 1},
            "funding": {"state": "submitted", "tx_id": "d" * 40, "submitted_at": time.time() - 5000,
                        "review": {"payload": {"params": {}}, "amount": "1", "recipient": "player0015",
                                   "operations": [], "authority": "Active"}}}
    _record(root=tmp_path / ".ceres", steps=[step], state="settling", phase="after")
    client = _client(tmp_path)
    body = {"run_id": "a" * 32, "step_id": "b" * 32, "action": "verify"}

    first = client.post("/api/harvest/routing/funding?player=player0005", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["steps"][0]["funding"]["state"] == "pending"  # one unseen sample is not enough

    second = client.post("/api/harvest/routing/funding?player=player0005", json=body)

    assert second.status_code == 200, second.text
    result = second.json()
    funding = result["steps"][0]["funding"]
    assert funding["state"] == "expired"
    assert funding["error"] == ("The SPS funding transfer has not been seen by the validator within an hour "
                                 "and is treated as expired; if it did go through, attach its transaction ID below.")
    assert "seen" not in funding
    assert result["steps"][0]["state"] == "settling"  # the step itself is not failed

    monkeypatch.setattr(routing_routes, "_funding_effects", lambda player, step: {})
    monkeypatch.setattr(routing_routes, "_funding_balance", lambda request, player, record, review: None)
    monkeypatch.setattr(routing_routes, "build_sps_funding",
                        lambda player, effects, kind: {"payload": {"params": {}}, "operations": [],
                                                        "authority": "Active", "amount": "1",
                                                        "recipient": "player0015"})
    review = client.post("/api/harvest/routing/funding?player=player0005",
                         json={"run_id": "a" * 32, "step_id": "b" * 32, "action": "review"})

    assert review.status_code == 200, review.text
    reviewed = review.json()["steps"][0]["funding"]
    assert reviewed["state"] == "reviewed"
    archived = review.json()["steps"][0]["funding_reviews"]
    assert any(item.get("state") == "expired" for item in archived)


def test_funding_verify_never_expires_a_seen_or_recent_transfer(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing_routes, "funding_receipt", lambda player, tx, review: {"state": "pending", "seen": True})
    step = {"step_id": "b" * 32, "state": "settling", "kind": "sell", "tx_id": "c" * 40,
            "operation_index": 0, "review": {"payload": {}, "step": {}}, "game_result": {"x": 1},
            "funding": {"state": "submitted", "tx_id": "d" * 40, "submitted_at": time.time() - 5000,
                        "review": {"payload": {"params": {}}, "amount": "1", "recipient": "player0015",
                                   "operations": [], "authority": "Active"}}}
    _record(root=tmp_path / ".ceres", steps=[step], state="settling", phase="after")
    client = _client(tmp_path)
    body = {"run_id": "a" * 32, "step_id": "b" * 32, "action": "verify"}

    client.post("/api/harvest/routing/funding?player=player0005", json=body)
    result = client.post("/api/harvest/routing/funding?player=player0005", json=body).json()
    assert result["steps"][0]["funding"]["state"] in {"submitted", "pending"}

    monkeypatch.setattr(routing_routes, "funding_receipt", lambda player, tx, review: {"state": "pending", "seen": False})
    recent_step = {"step_id": "b" * 32, "state": "settling", "kind": "sell", "tx_id": "c" * 40,
                   "operation_index": 0, "review": {"payload": {}, "step": {}}, "game_result": {"x": 1},
                   "funding": {"state": "submitted", "tx_id": "d" * 40, "submitted_at": time.time() - 60,
                               "review": {"payload": {"params": {}}, "amount": "1", "recipient": "player0015",
                                          "operations": [], "authority": "Active"}}}
    _record(root=tmp_path / ".ceres", steps=[recent_step], state="settling", phase="after")
    client.post("/api/harvest/routing/funding?player=player0005", json=body)
    result = client.post("/api/harvest/routing/funding?player=player0005", json=body).json()
    assert result["steps"][0]["funding"]["state"] == "pending"

    # unseen, then seen (resets the counter), then unseen again must not expire — the
    # counter needs 2 CONSECUTIVE unseen samples, not 2 unseen samples ever.
    reset_receipts = iter([
        {"state": "pending", "seen": False},
        {"state": "pending", "seen": True},
        {"state": "pending", "seen": False},
    ])
    monkeypatch.setattr(routing_routes, "funding_receipt",
                        lambda player, tx, review: next(reset_receipts))
    old_step = {"step_id": "b" * 32, "state": "settling", "kind": "sell", "tx_id": "c" * 40,
                "operation_index": 0, "review": {"payload": {}, "step": {}}, "game_result": {"x": 1},
                "funding": {"state": "submitted", "tx_id": "d" * 40, "submitted_at": time.time() - 5000,
                            "review": {"payload": {"params": {}}, "amount": "1", "recipient": "player0015",
                                       "operations": [], "authority": "Active"}}}
    _record(root=tmp_path / ".ceres", steps=[old_step], state="settling", phase="after")
    client.post("/api/harvest/routing/funding?player=player0005", json=body)  # unseen: counter -> 1
    client.post("/api/harvest/routing/funding?player=player0005", json=body)  # seen: counter -> 0
    result = client.post("/api/harvest/routing/funding?player=player0005", json=body).json()  # unseen: counter -> 1
    assert result["steps"][0]["funding"]["state"] == "pending"


def test_funding_verify_on_already_expired_record_returns_200_and_stays_expired(tmp_path: Path, monkeypatch):
    # "expired" is an admitted verify state but a terminal one -- verifying it again must
    # never call funding_receipt (which would try to re-check a request already given up on).
    def boom(*args, **kwargs):
        raise AssertionError("funding_receipt must not be called for an already-expired record")
    monkeypatch.setattr(routing_routes, "funding_receipt", boom)
    step = {"step_id": "b" * 32, "state": "settling", "kind": "sell", "tx_id": "c" * 40,
            "operation_index": 0, "review": {"payload": {}, "step": {}}, "game_result": {"x": 1},
            "funding": {"state": "expired", "tx_id": "d" * 40, "submitted_at": time.time() - 5000,
                        "error": "The SPS funding transfer has not been seen by the validator within an "
                                 "hour and is treated as expired; if it did go through, attach its "
                                 "transaction ID below.",
                        "review": {"payload": {"params": {}}, "amount": "1", "recipient": "player0015",
                                   "operations": [], "authority": "Active"}}}
    _record(root=tmp_path / ".ceres", steps=[step], state="settling", phase="after")
    client = _client(tmp_path)
    response = client.post("/api/harvest/routing/funding?player=player0005",
                           json={"run_id": "a" * 32, "step_id": "b" * 32, "action": "verify"})
    assert response.status_code == 200, response.text
    assert response.json()["steps"][0]["funding"]["state"] == "expired"


def test_expired_funding_can_adopt_a_late_confirmed_transfer(tmp_path: Path, monkeypatch):
    review = {"payload": {"params": {}}, "amount": "1", "recipient": "player0015",
              "operations": [], "authority": "Active"}
    step = {"step_id": "b" * 32, "state": "settling", "kind": "sell", "tx_id": "c" * 40,
            "operation_index": 0, "review": {"payload": {}, "step": {}}, "game_result": {"x": 1},
            "funding": {"state": "expired", "tx_id": "d" * 40, "submitted_at": time.time() - 5000,
                        "error": "The SPS funding transfer has not been seen by the validator within an "
                                 "hour and is treated as expired; if it did go through, attach its "
                                 "transaction ID below.",
                        "review": review}}
    _record(root=tmp_path / ".ceres", steps=[step], state="settling", phase="after")
    client = _client(tmp_path)

    monkeypatch.setattr(routing_routes, "recovered_funding_receipt",
                        lambda player, tx, review: {"state": "funded", "transaction_id": tx,
                                                     "block_num": 1, "confirmed_at": time.time(),
                                                     "amount": review["amount"], "recipient": review["recipient"]})

    response = client.post("/api/harvest/routing/funding?player=player0005",
                           json={"run_id": "a" * 32, "step_id": "b" * 32,
                                 "action": "recover-confirmed", "tx_id": "e" * 40})

    assert response.status_code == 200, response.text
    result = response.json()
    funding = result["steps"][0]["funding"]
    assert funding["state"] == "funded"
    assert funding["tx_id"] == "e" * 40
    archived = result["steps"][0]["funding_reviews"]
    assert any(item.get("state") == "expired" for item in archived)


def test_funding_receipt_reports_seen(monkeypatch):
    tx = "c" * 40
    review = {"payload": {"params": {"n": 1}}, "amount": "1", "recipient": "player0015"}

    monkeypatch.setattr(routing_validator, "_read", lambda url, missing_ok=False: None)
    assert routing_validator.funding_receipt("player0005", tx, review) == {"state": "pending", "seen": False}

    record = {"id": tx, "player": "player0005", "type": "token_transfer",
              "data": review["payload"]["params"], "success": None}
    monkeypatch.setattr(routing_validator, "_read", lambda url, missing_ok=False: record)
    assert routing_validator.funding_receipt("player0005", tx, review) == {"state": "pending", "seen": True}


def test_ui_has_no_deferred_run_recovery_and_shows_failure_panel():
    source = (Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js").read_text()
    assert "Unfinished routing run" not in source
    assert "cancel-other" not in source
    assert "TERMINAL = new Set(['complete', 'cancelled', 'failed', 'stopped'])" in source
    assert "Routing failed" in source
    assert "api('failure', { reason: S.error })" in source
    assert "S.failed" not in source  # only the server closes a run
    assert "!inFlight(S.run)" in source  # stop() never touches a run with a transaction on chain
    assert "S.adopted = !!adopted;" in source  # adoption is computed from the same-scope in-flight run
    assert "if (S.adopted) return '';" in source  # verify-only: never offers signing
    assert "['review', 'sign', 'skip-failed', 'retry-unsent', 'cancel'].includes(name)" in source  # verify-only actions refused
    assert "return { html, footerHtml, change, action, load, stop" in source
    assert "html, footerHtml, change, action, load, stop, clear" in source
    modal = (Path(__file__).parents[2] / "ceres" / "static" / "harvest-modal.js").read_text()
    assert "fetch('/api/harvest/routing/stop?player=' + encodeURIComponent(account)" in modal
    assert "body: JSON.stringify({ run_id: runId }), keepalive: true" in modal


def test_cascade_dependents_skips_a_step_whose_dependency_failed():
    record = {"steps": [
        {"step_id": "b" * 32, "state": "skipped", "kind": "sell"},
        {"step_id": "d" * 32, "state": "planned", "kind": "sell", "depends_on_step_id": "b" * 32},
    ]}
    runs.cascade_dependents(record)
    assert record["steps"][1]["state"] == "skipped"
    assert record["steps"][1]["error"] == "No DEC proceeds from the first step"
    assert record["steps"][1]["skip_metadata"]["reason"] == "dependency_unfunded"
    # a dependency that is only confirmed, in flight or still planned never cascades
    other = {"steps": [{"step_id": "e" * 32, "state": "confirmed", "kind": "sell"},
                        {"step_id": "f" * 32, "state": "planned", "kind": "sell", "depends_on_step_id": "e" * 32}]}
    runs.cascade_dependents(other)
    assert other["steps"][1]["state"] == "planned"


def test_write_cascades_a_skipped_first_leg_to_its_dependent_second_leg(tmp_path: Path):
    root = tmp_path / ".ceres"
    first = {"step_id": "b" * 32, "state": "skipped", "kind": "sell"}
    second = {"step_id": "d" * 32, "state": "planned", "kind": "sell", "depends_on_step_id": "b" * 32}
    _record(root=root, steps=[first, second], state="ready", phase="after")

    saved = _read(root)

    assert saved["steps"][1]["state"] == "skipped"
    assert saved["steps"][1]["error"] == "No DEC proceeds from the first step"
    assert saved["steps"][1]["skip_metadata"]["reason"] == "dependency_unfunded"


def _two_step_pools():
    from decimal import Decimal as D
    from ceres import harvest_routing as routing
    return {"SPS": routing.Pool(3, "SPS", D(1000000), D(1000000), D(100000), external=True)}


def test_two_step_second_leg_refuses_review_before_the_first_leg_is_confirmed():
    first = {"step_id": "b" * 32, "state": "planned", "kind": "sell", "source_region": "PR-BRL-41",
             "symbol": "GRAIN", "quantity": "50.000", "slippage": "2.5",
             "quote": {"source_symbol": "GRAIN", "destination_symbol": "DEC", "input": "50.000",
                       "output": "44.000", "minimum": "40.000", "intermediate_dec": "0", "external": False},
             "two_step": {"role": "first", "final_symbol": "SPS"}}
    second = {"step_id": "d" * 32, "state": "planned", "kind": "sell", "source_region": "inventory",
              "symbol": "DEC", "destination_symbol": "SPS", "destination_region": "inventory",
              "depends_on_step_id": "b" * 32, "quantity": "0", "slippage": "2.5",
              "quote": {"source_symbol": "DEC", "destination_symbol": "SPS", "input": "44.000",
                        "output": "0.440", "minimum": "0.400", "intermediate_dec": "0",
                        "external": True, "estimate": True},
              "two_step": {"role": "second"}}
    record = {"player": "player0005", "phase": "after", "reserves": [], "slippage": "2.5",
              "harvest_attempt_ids": [], "steps": [first, second]}
    context = {"public": {"blocked": False}, "ledger": routing_routes.routing.Ledger({}), "pools": _two_step_pools()}

    with pytest.raises(routing_routes.routing.RoutingError, match="Confirm the sale to DEC first"):
        routing_routes._fresh_step(None, "player0005", record, second, context)


def test_two_step_second_leg_uses_the_exact_confirmed_dec_receipt():
    from decimal import Decimal as D
    from ceres import harvest_routing as routing

    first = {"step_id": "b" * 32, "state": "confirmed", "kind": "sell", "source_region": "PR-BRL-41",
             "symbol": "GRAIN", "quantity": "50.000", "slippage": "2.5",
             "quote": {"source_symbol": "GRAIN", "destination_symbol": "DEC", "input": "50.000",
                       "output": "44.000", "minimum": "40.000", "intermediate_dec": "0", "external": False},
             "two_step": {"role": "first", "final_symbol": "SPS"},
             "receipt": {"received": [{"symbol": "DEC", "amount": "123.456", "region_uid": "inventory"}],
                        "transaction_id": "e" * 40, "confirmed_at": 1.0}}
    second = {"step_id": "d" * 32, "state": "planned", "kind": "sell", "source_region": "inventory",
              "symbol": "DEC", "destination_symbol": "SPS", "destination_region": "inventory",
              "depends_on_step_id": "b" * 32, "quantity": "0", "slippage": "2.5",
              "quote": {"source_symbol": "DEC", "destination_symbol": "SPS", "input": "44.000",
                        "output": "0.440", "minimum": "0.400", "intermediate_dec": "0",
                        "external": True, "estimate": True},
              "two_step": {"role": "second"}}
    record = {"player": "player0005", "phase": "after", "reserves": [], "slippage": "2.5",
              "harvest_attempt_ids": [], "steps": [first, second]}
    ledger = routing.Ledger({("PR-BRL-41", "GRAIN"): D(1000), (routing.INVENTORY, "DEC"): D(100000)})
    context = {"public": {"blocked": False}, "ledger": ledger, "pools": _two_step_pools()}

    fresh, _public = routing_routes._fresh_step(None, "player0005", record, second, context)

    assert fresh["quote"]["input"] == "123.456"
    assert "estimate" not in fresh["quote"]
    assert ledger.allocated[(routing.INVENTORY, "DEC")] == D("123.456")


def _two_step_ledger(D):
    from ceres import harvest_routing as routing
    return routing.Ledger({("PR-BRL-41", "GRAIN"): D(1000), (routing.INVENTORY, "DEC"): D(100000)})


def _two_step_first(*, receipt_received=None, game_result_dec_amount=None):
    first = {"step_id": "b" * 32, "state": "confirmed", "kind": "sell", "source_region": "PR-BRL-41",
             "symbol": "GRAIN", "quantity": "50.000", "slippage": "2.5",
             "quote": {"source_symbol": "GRAIN", "destination_symbol": "DEC", "input": "50.000",
                       "output": "44.000", "minimum": "40.000", "intermediate_dec": "0", "external": False},
             "two_step": {"role": "first", "final_symbol": "SPS"},
             "receipt": {"received": receipt_received or [], "transaction_id": "e" * 40, "confirmed_at": 1.0}}
    if game_result_dec_amount is not None:
        first["game_result"] = {"success": True, "result": {"success": True,
                                "data": {"dec_amount": game_result_dec_amount, "resource": "DEC"}}}
    return first


def _two_step_second(*, quantity="0.000"):
    return {"step_id": "d" * 32, "state": "planned", "kind": "sell", "source_region": "inventory",
            "symbol": "DEC", "destination_symbol": "SPS", "destination_region": "inventory",
            "depends_on_step_id": "b" * 32, "quantity": "0", "slippage": "2.5",
            "quote": {"source_symbol": "DEC", "destination_symbol": "SPS", "input": quantity,
                      "output": "0.440", "minimum": "0.400", "intermediate_dec": "0",
                      "external": True, "estimate": True},
            "two_step": {"role": "second"}}


def test_two_step_second_leg_floors_the_confirmed_amount_to_pool_precision():
    from decimal import Decimal as D
    first = _two_step_first(receipt_received=[{"symbol": "DEC", "amount": "123.4567", "region_uid": "inventory"}])
    second = _two_step_second()
    record = {"player": "player0005", "phase": "after", "reserves": [], "slippage": "2.5",
              "harvest_attempt_ids": [], "steps": [first, second]}
    context = {"public": {"blocked": False}, "ledger": _two_step_ledger(D), "pools": _two_step_pools()}

    fresh, _public = routing_routes._fresh_step(None, "player0005", record, second, context)

    assert fresh["quote"]["input"] == "123.456"


def test_two_step_second_leg_becomes_unfundable_below_pool_precision():
    from decimal import Decimal as D
    first = _two_step_first(receipt_received=[{"symbol": "DEC", "amount": "0.00001", "region_uid": "inventory"}])
    second = _two_step_second()
    record = {"player": "player0005", "phase": "after", "reserves": [], "slippage": "2.5",
              "harvest_attempt_ids": [], "steps": [first, second]}
    context = {"public": {"blocked": False}, "ledger": _two_step_ledger(D), "pools": _two_step_pools()}

    with pytest.raises(routing_routes.routing.ProceedsTooSmall):
        routing_routes._fresh_step(None, "player0005", record, second, context)


def test_review_auto_skips_a_dependent_leg_whose_proceeds_are_too_small(tmp_path: Path, monkeypatch):
    from decimal import Decimal as D
    root = tmp_path / ".ceres"
    first = _two_step_first(receipt_received=[{"symbol": "DEC", "amount": "0.00001", "region_uid": "inventory"}])
    second = _two_step_second()
    _record(root=root, steps=[first, second], state="ready", phase="after")
    fake_context = {"public": {"blocked": False, "observed_at": "x"}, "ledger": _two_step_ledger(D), "pools": _two_step_pools()}
    monkeypatch.setattr(routing_routes, "_context", lambda request, player: fake_context)
    monkeypatch.setattr(routing_routes, "_require_posting_contract", lambda: None)
    client = _client(tmp_path)

    response = client.post("/api/harvest/routing/review?player=player0005", json={"run_id": "a" * 32})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["review"] is None
    second_saved = result["run"]["steps"][1]
    assert second_saved["state"] == "skipped"
    assert second_saved["skip_metadata"]["reason"] == "insufficient_dependency_proceeds"
    assert second_saved["error"] == "Proceeds too small to swap"


def test_two_step_second_leg_falls_back_to_the_raw_game_result_dec_amount():
    from decimal import Decimal as D
    first = _two_step_first(receipt_received=[], game_result_dec_amount=77.259)
    second = _two_step_second()
    record = {"player": "player0005", "phase": "after", "reserves": [], "slippage": "2.5",
              "harvest_attempt_ids": [], "steps": [first, second]}
    context = {"public": {"blocked": False}, "ledger": _two_step_ledger(D), "pools": _two_step_pools()}

    fresh, _public = routing_routes._fresh_step(None, "player0005", record, second, context)

    assert fresh["quote"]["input"] == "77.259"


def test_two_step_second_leg_refuses_when_confirmed_but_proceeds_are_unknown():
    from decimal import Decimal as D
    first = _two_step_first(receipt_received=[], game_result_dec_amount=None)
    second = _two_step_second()
    record = {"player": "player0005", "phase": "after", "reserves": [], "slippage": "2.5",
              "harvest_attempt_ids": [], "steps": [first, second]}
    context = {"public": {"blocked": False}, "ledger": _two_step_ledger(D), "pools": _two_step_pools()}

    with pytest.raises(routing_routes.routing.RoutingError,
                       match="Step 1 confirmed but its DEC proceeds are unknown"):
        routing_routes._fresh_step(None, "player0005", record, second, context)


def test_skip_failed_accepts_a_planned_dependent_step_with_unknown_parent_proceeds(tmp_path: Path):
    root = tmp_path / ".ceres"
    first = _two_step_first(receipt_received=[], game_result_dec_amount=None)
    second = _two_step_second()
    _record(root=root, steps=[first, second], state="ready", phase="after")
    client = _client(tmp_path)

    response = client.post("/api/harvest/routing/skip-failed?player=player0005",
                           json={"run_id": "a" * 32, "step_id": "d" * 32})

    assert response.status_code == 200, response.text
    result = response.json()
    saved = result["steps"][1]
    assert saved["state"] == "skipped"
    assert saved["skip_metadata"]["reason"] == "dependency_proceeds_unknown"
    assert saved["error"] == "Step 1 confirmed but its DEC proceeds are unknown"


def test_dependency_proceeds_distinguishes_not_confirmed_from_unknown():
    first_planned = {"step_id": "b" * 32, "state": "planned"}
    second = _two_step_second()
    record = {"steps": [first_planned, second]}
    assert routing_routes._dependency_proceeds(record, second) == (None, False)

    first_confirmed_unknown = {"step_id": "b" * 32, "state": "confirmed", "receipt": {"received": []}}
    record = {"steps": [first_confirmed_unknown, second]}
    assert routing_routes._dependency_proceeds(record, second) == (None, True)


def test_materialize_steps_translates_depends_on_to_depends_on_step_id():
    preview_steps = [
        {"id": "one:dec", "kind": "sell", "status": "ready", "two_step": {"role": "first"}},
        {"id": "one:sps", "kind": "sell", "status": "ready", "depends_on": "one:dec", "two_step": {"role": "second"}},
    ]
    materialized = runs._materialize_steps(preview_steps)
    assert len(materialized) == 2
    first_id, second_id = materialized[0]["step_id"], materialized[1]["step_id"]
    assert materialized[1]["depends_on"] == "one:dec"
    assert materialized[1]["depends_on_step_id"] == first_id
    assert first_id != second_id


def test_fresh_step_refuses_to_size_from_the_estimate_when_depends_on_never_resolved():
    from decimal import Decimal as D
    # B's planning-time `depends_on` id never resolved to a created step (its A was filtered as
    # "empty") — the materialized step therefore carries `depends_on` but no `depends_on_step_id`.
    second = _two_step_second(quantity="44.000")
    second.pop("depends_on_step_id")
    second["depends_on"] = "one:dec"
    record = {"player": "player0005", "phase": "after", "reserves": [], "slippage": "2.5",
              "harvest_attempt_ids": [], "steps": [second]}
    context = {"public": {"blocked": False}, "ledger": _two_step_ledger(D), "pools": _two_step_pools()}

    with pytest.raises(routing_routes.routing.RoutingError, match="Confirm the sale to DEC first"):
        routing_routes._fresh_step(None, "player0005", record, second, context)


def test_review_members_never_bundles_either_leg_of_a_two_step_sale():
    first = _two_step_first()
    other_dec_sale = {"step_id": "f" * 32, "state": "planned", "kind": "sell", "source_region": "PR-BRL-65",
                       "symbol": "WOOD", "quote": {"source_symbol": "WOOD", "destination_symbol": "DEC",
                                                   "output": "1", "external": False}}
    record = {"phase": "after", "steps": [first, other_dec_sale]}
    assert routing_routes._review_members(record, first) == [first]

    second = _two_step_second()
    another_dec_sale = {"step_id": "g" * 32, "state": "planned", "kind": "sell", "source_region": "PR-BRL-65",
                        "symbol": "STONE", "quote": {"source_symbol": "STONE", "destination_symbol": "DEC",
                                                     "output": "1", "external": False}}
    record = {"phase": "after", "steps": [second, another_dec_sale]}
    assert routing_routes._review_members(record, second) == [second]
