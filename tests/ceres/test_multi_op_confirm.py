"""one Hive transaction, several Splinterlands game operations -- every operation's own record is read.

The game files the first game operation under the bare Hive id and the following ones under `<id>-1`, `<id>-2` ...
(measured 2026-10-11 on ddfbacfe8d89f155b1f498e0b017bb63a18f35f8, four `sm_land_operation` harvests; `-4` answered
"Transaction [...] not found."). The record shapes below are copied from that capture (player pseudonymised, nonces
synthetic) and from the recorded survival refusal `tests/fixtures/fides/refusal_error_45.json`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from praetoria.errors import UpstreamError
from praetoria.transport import ResponseData

from ceres import capture as capture_mod
from ceres.config import CeresConfig
import ceres.routes_op_preview as routes_op_preview_mod
from ceres.routes_op_preview import op_verify
from fides.game_verdict import game_verdict

ROOT = Path(__file__).resolve().parents[2]
TX = "ddfbacfe8d89f155b1f498e0b017bb63a18f35f8"
PLAYER = "player-0001"  # pseudonym (wiki scheme player_0001; Hive names allow no underscore)
LOOKUP = "https://api.splinterlands.com/transactions/lookup?trx_id="
REFUSAL = json.loads((ROOT / "tests" / "fixtures" / "fides" / "refusal_error_45.json").read_text(encoding="utf-8"))["error"]

# Recorded harvest_all result envelope (8b2a9a…-1, 2026-10-11), trimmed to one deed.
_HARVEST_RESULT = json.dumps({"success": True, "result": {"success": True, "error": "", "data": {
    "success": True, "error": "", "message": "1 deeds were harvested", "results": [{
        "deed_uid": "I-293-8cafc444b5ed47", "region_number": 13, "resource_amount": 10761.674, "received_amount": 9685.507,
        "tax_amount": 1076.167, "resource_symbol": "GRAIN", "result_message": "Successful"}]}, "balanceHistory": []}})


def _record(lookup_id: str, nonce: str, *, success: bool = True, error: str | None = None, region: str = "PR-PNW-13") -> dict:
    data = {"op": "harvest_all", "region_uid": region, "deed_uids": [], "auto_buy_grain": False,
            "resource_symbol": "ALL", "app": "ceres/0.27.11", "n": nonce}
    info = {"id": lookup_id, "type": "land_operation", "player": PLAYER, "data": json.dumps(data),
            "success": success, "error": error, "block_num": 110675977, "created_date": "2026-10-11T08:48:57.000Z",
            "result": _HARVEST_RESULT if success else None}
    body = {"trx_info": info}
    if error is not None:
        body["error"] = error
    return body


class RecordedLookup:
    """Answers like the game: a recorded body per id; an unknown id raises the transport's "not found" error."""

    def __init__(self, records: dict[str, dict]):
        self.records = records
        self.urls: list[str] = []

    def get_json(self, url: str):
        self.urls.append(url)
        lookup_id = url.removeprefix(LOOKUP)
        if lookup_id not in self.records:
            raise UpstreamError(f"Transaction [{lookup_id}] not found.")
        return ResponseData(self.records[lookup_id], "2026-10-11T08:49:00Z", 1)


NONCES = ["nonce00000", "nonce00001", "nonce00002"]


def _three_ops(op2: dict | None) -> dict[str, dict]:
    records = {TX: _record(TX, NONCES[0]), f"{TX}-2": _record(f"{TX}-2", NONCES[2], region="PR-LOP-55")}
    if op2 is not None:
        records[f"{TX}-1"] = op2
    return records


def test_three_ops_with_op_two_refused_reports_each_operation_with_the_games_reason_verbatim():
    lookup = RecordedLookup(_three_ops(_record(f"{TX}-1", NONCES[1], success=False, error=REFUSAL, region="PR-LOP-51")))
    verdict = game_verdict(TX, op_count=3, expected_nonces=NONCES, account=PLAYER, transport=lookup)
    assert [url.removeprefix(LOOKUP) for url in lookup.urls] == [TX, f"{TX}-1", f"{TX}-2"]
    assert verdict["status"] == "game_rejected"
    outcomes = verdict["evidence"]["op_outcomes"]
    assert [row["outcome"] for row in outcomes] == ["done", "refused", "done"]
    assert outcomes[1]["error"] == REFUSAL
    assert [row["lookup_id"] for row in outcomes] == [TX, f"{TX}-1", f"{TX}-2"]


def test_three_ops_all_done_is_accepted():
    lookup = RecordedLookup(_three_ops(_record(f"{TX}-1", NONCES[1], region="PR-LOP-51")))
    verdict = game_verdict(TX, op_count=3, expected_nonces=NONCES, account=PLAYER, transport=lookup)
    assert verdict["status"] == "game_accepted"
    assert [row["outcome"] for row in verdict["evidence"]["op_outcomes"]] == ["done", "done", "done"]


def test_a_missing_record_stays_unknown_and_the_records_after_it_are_still_read():
    lookup = RecordedLookup(_three_ops(None))
    verdict = game_verdict(TX, op_count=3, expected_nonces=NONCES, account=PLAYER, transport=lookup)
    assert len(lookup.urls) == 3
    assert verdict["status"] == "game_unknown"
    assert verdict["evidence"]["missing"] == [f"{TX}-1"]
    assert [row["outcome"] for row in verdict["evidence"]["op_outcomes"]] == ["done", "unknown", "done"]


def test_a_refusal_next_to_a_missing_record_is_shown_but_the_transaction_stays_unknown():
    # S3: an unknown operation never yields a terminal verdict that would permit signing again.
    records = {TX: _record(TX, NONCES[0], success=False, error=REFUSAL)}
    verdict = game_verdict(TX, op_count=3, expected_nonces=NONCES, account=PLAYER, transport=RecordedLookup(records))
    assert verdict["status"] == "game_unknown"
    assert [row["outcome"] for row in verdict["evidence"]["op_outcomes"]] == ["refused", "unknown", "unknown"]
    assert verdict["evidence"]["op_outcomes"][0]["error"] == REFUSAL


def test_an_unprocessed_transaction_reads_only_the_bare_id():
    lookup = RecordedLookup({})
    verdict = game_verdict(TX, op_count=3, expected_nonces=NONCES, account=PLAYER, transport=lookup)
    assert len(lookup.urls) == 1
    assert verdict["status"] == "game_unknown" and verdict["evidence"]["unknown_reason"] == "not_found"
    assert [row["outcome"] for row in verdict["evidence"]["op_outcomes"]] == ["unknown"] * 3


# api/op-verify: the board's confirmation reads every game op and returns the per-op outcomes

def _land_op(nonce: str, region: str) -> list:
    payload = {"op": "harvest_all", "region_uid": region, "deed_uids": [], "auto_buy_grain": False,
               "resource_symbol": "ALL", "app": "ceres/0.27.11", "n": nonce}
    return ["custom_json", {"id": "sm_land_operation", "json": json.dumps(payload), "required_auths": [],
                            "required_posting_auths": [PLAYER]}]


def _op_verify(monkeypatch, tmp_path, records, operations):
    lookup = RecordedLookup(records)
    real = game_verdict
    monkeypatch.setattr(routes_op_preview_mod, "game_verdict", lambda *a, **k: real(*a, **k, transport=lookup))
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain",
                        lambda *_a, **_k: {"status": "chain_verified", "evidence": {"tx_id": TX, "block_num": 100, "observed": []}})
    monkeypatch.setattr(routes_op_preview_mod.LandVerificationSpec, "check_domain",
                        lambda *_a, **_k: {"status": "domain_pending", "evidence": {}})
    monkeypatch.setattr(capture_mod, "expand_plots", lambda *_a, **_k: {"capture": {"refreshed_deeds": [], "refresh_errors": []}})
    config = CeresConfig(root=tmp_path / ".ceres", player=PLAYER)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))
    return json.loads(op_verify({"tx_id": TX, "operations": operations}, request, player=PLAYER).body), lookup


def test_op_verify_reports_each_operation_of_a_three_op_transaction(monkeypatch, tmp_path):
    operations = [_land_op(NONCES[0], "PR-PNW-13"), _land_op(NONCES[1], "PR-LOP-51"), _land_op(NONCES[2], "PR-LOP-55")]
    result, lookup = _op_verify(monkeypatch, tmp_path,
                                _three_ops(_record(f"{TX}-1", NONCES[1], success=False, error=REFUSAL, region="PR-LOP-51")), operations)
    assert len(lookup.urls) == 3
    assert result["state"] == "game-refused"
    assert [row["outcome"] for row in result["op_outcomes"]] == ["done", "refused", "done"]
    assert [row["success"] for row in result["op_results"]] == [True, False, True]
    assert result["op_outcomes"][1]["error"] == REFUSAL


def test_op_verify_missing_record_is_never_refused_or_confirmed(monkeypatch, tmp_path):
    operations = [_land_op(NONCES[0], "PR-PNW-13"), _land_op(NONCES[1], "PR-LOP-51"), _land_op(NONCES[2], "PR-LOP-55")]
    result, _ = _op_verify(monkeypatch, tmp_path, _three_ops(None), operations)
    assert result["game"]["status"] == "game_unknown"
    assert result["state"] not in {"game-refused", "domain-confirmed"}
    assert [row["outcome"] for row in result["op_outcomes"]] == ["done", "unknown", "done"]


def test_op_verify_does_not_count_a_non_game_operation(monkeypatch, tmp_path):
    other = ["custom_json", {"id": "not_a_game_op", "json": json.dumps({"n": "x"}), "required_auths": [],
                             "required_posting_auths": [PLAYER]}]
    operations = [_land_op(NONCES[0], "PR-PNW-13"), _land_op(NONCES[1], "PR-LOP-51"), other]
    records = {TX: _record(TX, NONCES[0]), f"{TX}-1": _record(f"{TX}-1", NONCES[1], region="PR-LOP-51")}
    result, lookup = _op_verify(monkeypatch, tmp_path, records, operations)
    assert len(lookup.urls) == 2
    assert result["game"]["status"] == "game_accepted"
    assert [row["index"] for row in result["op_outcomes"]] == [0, 1]


# the board: a mixed verdict clears the accepted moves and marks only the refused ones

def _slice(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


def test_board_splits_a_mixed_verdict_per_operation():
    source = (ROOT / "ceres" / "static" / "operations.js").read_text(encoding="utf-8")
    script = "\n".join([
        _slice(source, "function chunkDeedDirections", "function stageEntryMatchesChunk"),
        _slice(source, "function firstClearOutcomeChunks", "function markGameRefusedChunk"),
        r"""
const stake = (deed) => ['custom_json', { id: 'sm_stake_change', json: JSON.stringify({ deed_uid: deed,
 stake: { cards: [{ card_uid: 'C-' + deed }], items: [] }, unstake: { cards: [], items: [] }, n: deed }) }];
const chunk = { operations: [stake(''), stake(''), stake('')] };
const mixed = { op_results: [{ index: 0, success: true }, { index: 1, success: false, error: 'refused' }, { index: 2, success: true }] };
const split = firstClearOutcomeChunks(mixed, chunk);
const same = firstClearOutcomeChunks(mixed, { operations: [stake(''), stake(''), stake('')] });
const allRefused = firstClearOutcomeChunks({ op_results: [{ index: 0, success: false, error: 'x' }] }, { operations: [stake('')] });
console.log(JSON.stringify({
 accepted: [...chunkDeedDirections(split.accepted)], refused: [...chunkDeedDirections(split.refused)],
 ambiguous: same, allRefused,
}));
""",
    ])
    out = json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout)
    assert out["accepted"] == ["D1|stake", "D3|stake"]
    assert out["refused"] == ["D2|stake"]
    assert out["ambiguous"] is None
    assert out["allRefused"] is None
