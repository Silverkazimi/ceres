"""silent transaction-id reservations survive disposable routing runs."""
import json
import re
import time
from pathlib import Path

from ceres import harvest_evidence, routing_safety

TX = "a" * 40


def run(attempts, state="outcome_unknown", tx_id=TX, attached_at=None):
    return {"player": "player0012", "harvest_attempt_ids": attempts,
            "bundles": {"bundle": {"state": state, "tx_id": tx_id,
                                     "attached_at": attached_at or time.time()}}}


def test_deleted_unknown_run_silently_excludes_its_receipts(tmp_path):
    routing_safety.sync_run(tmp_path, "player0012", run(["receipt-1", "receipt-2"]))
    assert routing_safety.filter_attempts(tmp_path, "player0012", ["receipt-1", "receipt-2", "fresh"],
                                           lambda _tx: None) == ["fresh"]
    assert not re.search(r"safety-check|safety-notsent|A routing transaction from|I checked my wallet: it was not sent",
                         _rendered_routing_sources())


def test_included_by_id_marks_receipts_and_clears_record(tmp_path):
    attempt = {"attempt_id": "receipt-1", "player": "player0012", "state": "confirmed"}
    harvest_evidence._atomic_write(harvest_evidence._attempt_path(tmp_path, "player0012", "receipt-1"), attempt)
    routing_safety.sync_run(tmp_path, "player0012", run(["receipt-1"]))
    def included(tx_id):
        return {"trx_info": {"id": tx_id, "player": "player0012", "type": "land_operation",
                             "success": True, "result": json.dumps({"success": True,
                                                                       "result": {"success": True}})}}
    assert routing_safety.filter_attempts(tmp_path, "player0012", ["receipt-1", "fresh"], included) == ["fresh"]
    assert routing_safety.unresolved(tmp_path, "player0012") == []
    assert routing_safety.is_routed(tmp_path, "player0012", ["receipt-1"])


def test_not_included_expired_clears_record_and_allows_full_routing(tmp_path):
    routing_safety.sync_run(tmp_path, "player0012", run(["receipt-1"], attached_at=time.time() - 4000))
    assert routing_safety.filter_attempts(tmp_path, "player0012", ["receipt-1"], lambda _tx: None) == ["receipt-1"]
    assert routing_safety.unresolved(tmp_path, "player0012") == []


def test_unresolved_reservation_is_minimal_and_survives_restart_purge(tmp_path):
    routing_safety.sync_run(tmp_path, "player0012", run(["receipt-1"]))
    record, = routing_safety.unresolved(tmp_path, "player0012")
    assert set(record) == {"tx_id", "expires_at", "attempt_ids"}
    assert record["tx_id"] == TX
    assert routing_safety.filter_attempts(tmp_path, "player0012", ["receipt-1"], lambda _tx: None) == []


def _rendered_routing_sources():
    root = Path(__file__).resolve().parents[2]
    paths = ("ceres/static/harvest-routing.js", "ceres/static/routing-window.js",
             "ceres/static/routing-run.js", "ceres/routes_routing_v2.py")
    return "\n".join((root / path).read_text(encoding="utf-8") for path in paths)
