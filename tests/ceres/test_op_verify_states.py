"""`/api/op-verify` state regression tests -- IR: the endpoint was conflating "the game
effect is not observable YET" with "the operation FAILED". These exercise the fix through
the real route function (`ceres.routes_op_preview.op_verify`), never the client, covering
the four contract cases: stale-read -> pending (NOT mismatch), None facts -> could-not-
verify, genuine mismatch -> mismatch, correct read-back -> confirmed.

Fixture shape: `worksite_facts == {"project_type": "worksite_<resource>_construction"}` is
the shape already used throughout this project's existing suite (`tests/ceres/
test_worksite_broadcast.py`, `tests/fides_land/test_verify.py`) and documented in the internal rules
as the live-observed re-read shape -- not invented for this test.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ceres import capture as capture_mod
import ceres.routes_op_preview as routes_op_preview_mod
from ceres.config import CeresConfig
from ceres.routes_op_preview import op_verify
from praetoria.transport import ResponseData


def _request(config):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))


def _worksite_op(deed_uid: str, target: str) -> list:
    payload = {
        "op": f"worksite_{target}_construction", "region_uid": "PR-X",
        "deed_uids": [deed_uid], "time_crystals": 0, "app": "splinterlands/0.7.170", "n": "abcdefghij",
    }
    return ["custom_json", {
        "id": "sm_land_operation", "json": json.dumps(payload),
        "required_auths": [], "required_posting_auths": ["player0005"],
    }]


def _stake_op(deed_uid: str, card_uid: str) -> list:
    payload = {
        "unstake": {"cards": [], "items": []},
        "stake": {"cards": [{"card_uid": card_uid, "stake_type_uid": "STK-LND-WKR", "slot": "1"}], "items": []},
        "deed_uid": deed_uid, "auto_buy_grain": False, "app": "splinterlands/0.7.170", "n": "abcdefghij",
    }
    return ["custom_json", {
        "id": "sm_stake_change", "json": json.dumps(payload),
        "required_auths": [], "required_posting_auths": ["player0005"],
    }]


def _chain_verified_stub(*_args, **_kwargs):
    return {"status": "chain_verified", "evidence": {"tx_id": "tx-1", "block_num": 100, "observed": []}}


class _Evidence:
    def __init__(self, account, assets=None, worksite=None, **_kwargs):
        self.account = account
        self.capture = self
        self._assets = assets or (lambda _uid: {"status": "success", "data": {"cards": [], "items": []}})
        self._worksite = worksite or (lambda _uid: None)
        self._requests = 0

    def assets(self, uid):
        return ResponseData(self._assets(uid), "2026-09-05T00:00:00Z", 1)

    def worksite_facts(self, uid):
        return self._worksite(uid)

    def provenance(self):
        return {"account": self.account, "resources": {}}


def _settled_head(monkeypatch, head_block: int = 120):
    monkeypatch.setattr(
        routes_op_preview_mod.fides_verify,
        "_http_rpc_call",
        lambda node, method, params: {"head_block_number": head_block},
    )


def test_stale_worksite_read_is_pending_not_mismatch(monkeypatch, tmp_path):
    """Mutation evidence: apply the stake/unstake terminal rule to `sm_land_operation` too;
    expected red: a chain-confirmed worksite whose `project_type` remains the prior value becomes
    `domain-not-applied` instead of retaining propagation-lag `domain-pending`."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    _settled_head(monkeypatch)
    # The read is stale -- still the pre-broadcast settled state, not yet the
    # in-flight `_construction` form. This must never surface as a mismatch.
    monkeypatch.setattr(capture_mod, "MnemosyneEvidence", lambda account, **kwargs: _Evidence(account, worksite=lambda _uid: {"project_type": "worksite_grain"}))

    body = {"tx_id": "tx-1", "operations": [_worksite_op("deed-1", "aura")], "verification_attempt": 5}
    response = op_verify(body, _request(config), player=None)
    payload = json.loads(response.body)

    assert payload["state"] == "domain-pending"
    assert payload["domain"]["status"] == "domain_pending"
    assert payload["domain"]["evidence"]["pending_metadata"] == {
        "attempt": 5, "escalate_after_attempts": 5, "requires_escalation": True,
    }
    assert payload["refreshed_deeds"] == []  # never refreshed on a non-confirmed state
    notice = payload["notices"][0]
    # defect 2 -- `requires_escalation: True` (attempt 5 of 5) must now
    # produce the distinct escalated notice, not the ordinary "still propagating" one
    # this is the consumer `verification_notice()` was missing before this fix
    # `pending_metadata` was computed and never read by anything.
    assert notice["code"] == "verification_pending_escalated"
    assert notice["severity"] != "danger"  # escalation is not a failure verdict
    assert notice["action"] == {"id": "retry_verification", "label": "Check again"}


def test_worksite_facts_none_is_could_not_verify(monkeypatch, tmp_path):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    # A missing worksite fact means no evidence was obtainable at
    # all (e.g. the deed dropped out of the fresh bundle) -- genuinely unknown.
    monkeypatch.setattr(capture_mod, "MnemosyneEvidence", lambda account, **kwargs: _Evidence(account, worksite=lambda _uid: None))

    body = {"tx_id": "tx-1", "operations": [_worksite_op("deed-1", "aura")]}
    response = op_verify(body, _request(config), player=None)
    payload = json.loads(response.body)

    assert payload["state"] == "domain-could-not-verify"
    assert payload["domain"]["status"] == "domain_could_not_verify"
    notice = payload["notices"][0]
    assert notice["code"] == "verification_could_not_verify"


def test_chain_verified_stake_without_live_effect_is_terminal_not_applied(monkeypatch, tmp_path):
    """Guard 7. Mutation evidence: omit terminal age evidence; expected red: a terminal receipt
    loses the server-measured inclusion block, head, age, or attempt that justifies re-signing."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    _settled_head(monkeypatch)
    # A live read that lacks the requested stake is the maintainer's terminal case.
    monkeypatch.setattr(capture_mod, "MnemosyneEvidence", lambda account, **kwargs: _Evidence(account))

    body = {"tx_id": "tx-1", "operations": [_stake_op("deed-1", "CARD-1")], "verification_attempt": 2}
    response = op_verify(body, _request(config), player=None)
    payload = json.loads(response.body)

    assert payload["state"] == "domain-not-applied"
    assert payload["domain"]["status"] == "domain_pending"
    notice = payload["notices"][0]
    assert notice["code"] == "verification_not_applied"
    assert notice["severity"] == "danger"
    assert "landed on chain" in notice["message"]
    assert "did not apply" in notice["message"]
    evidence = payload["domain"]["evidence"]
    assert evidence["inclusion_block"] == 100
    assert evidence["head_block"] == 120
    assert evidence["age_blocks"] == 20
    assert evidence["age_seconds"] == 60
    assert evidence["attempt"] == 2


def test_chain_verified_stake_below_settle_floor_remains_pending(monkeypatch, tmp_path):
    """Guard 1 server half. Mutation evidence: remove the age comparison; expected red: a
    second live read below the 20-block floor becomes re-signable too early."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    _settled_head(monkeypatch, head_block=119)
    monkeypatch.setattr(
        capture_mod, "MnemosyneEvidence",
        lambda account, **kwargs: _Evidence(account),
    )

    response = op_verify(
        {"tx_id": "tx-1", "operations": [_stake_op("deed-1", "CARD-1")], "verification_attempt": 2},
        _request(config),
        player=None,
    )
    payload = json.loads(response.body)
    assert payload["state"] == "domain-pending"
    assert payload["domain"]["evidence"]["pending_metadata"]["attempt"] == 2


def test_chain_verified_stake_first_read_is_pending_even_after_settle(monkeypatch, tmp_path):
    """Guard 2. Mutation evidence: remove the minimum-attempt clause; expected red: one live
    domain read can produce a terminal verdict."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    _settled_head(monkeypatch)
    monkeypatch.setattr(
        capture_mod, "MnemosyneEvidence",
        lambda account, **kwargs: _Evidence(account),
    )

    response = op_verify(
        {"tx_id": "tx-1", "operations": [_stake_op("deed-1", "CARD-1")], "verification_attempt": 1},
        _request(config),
        player=None,
    )
    assert json.loads(response.body)["state"] == "domain-pending"


@pytest.mark.parametrize("head_block, expected_state", [(119, "domain-pending"), (120, "domain-not-applied")])
def test_chain_verified_stake_settle_floor_is_inclusive(monkeypatch, tmp_path, head_block, expected_state):
    """Guard 3. Mutation evidence: change `>=` to `>` or lower the constant; one boundary row
    must turn red while the other remains correct."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    _settled_head(monkeypatch, head_block=head_block)
    monkeypatch.setattr(
        capture_mod, "MnemosyneEvidence",
        lambda account, **kwargs: _Evidence(account),
    )

    response = op_verify(
        {"tx_id": "tx-1", "operations": [_stake_op("deed-1", "CARD-1")], "verification_attempt": 2},
        _request(config),
        player=None,
    )
    assert json.loads(response.body)["state"] == expected_state


@pytest.mark.parametrize(
    "chain_evidence, head_block, rpc_error",
    [
        ({"tx_id": "tx-1"}, None, None),
        ({"tx_id": "tx-1", "block_num": 100}, None, OSError("head unavailable")),
        ({"tx_id": "tx-1", "block_num": 100}, 99, None),
    ],
)
def test_chain_verified_stake_age_lookup_is_fail_safe(monkeypatch, tmp_path, chain_evidence, head_block, rpc_error):
    """Guard 4. Mutation evidence: return a large default age or let the head RPC exception
    escape; missing inclusion evidence and RPC failure must both stay pending without a 500."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(
        routes_op_preview_mod,
        "verify_chain",
        lambda *_args, **_kwargs: {"status": "chain_verified", "evidence": chain_evidence},
    )
    if rpc_error:
        def fail(*_args, **_kwargs):
            raise rpc_error
        monkeypatch.setattr(routes_op_preview_mod.fides_verify, "_http_rpc_call", fail)
    elif head_block is not None:
        _settled_head(monkeypatch, head_block=head_block)
    monkeypatch.setattr(
        capture_mod, "MnemosyneEvidence",
        lambda account, **kwargs: _Evidence(account),
    )

    response = op_verify(
        {"tx_id": "tx-1", "operations": [_stake_op("deed-1", "CARD-1")], "verification_attempt": 2},
        _request(config),
        player=None,
    )
    payload = json.loads(response.body)
    assert payload["state"] == "domain-pending"


def test_stake_without_chain_inclusion_remains_pending(monkeypatch, tmp_path):
    """Mutation evidence: remove the `chain_verified` condition from the terminal branch;
    expected red: a live domain absence with no observed inclusion is incorrectly declared a
    failed game application."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(
        routes_op_preview_mod,
        "verify_chain",
        lambda *_args, **_kwargs: {
            "status": "chain_absent",
            "evidence": {"reason_kind": "not_found", "tx_id": "tx-1", "block_num": 100, "observed": []},
        },
    )
    _settled_head(monkeypatch)
    monkeypatch.setattr(capture_mod, "MnemosyneEvidence", lambda account, **kwargs: _Evidence(account))

    response = op_verify(
        {"tx_id": "tx-1", "operations": [_stake_op("deed-1", "CARD-1")], "verification_attempt": 2},
        _request(config),
        player=None,
    )
    payload = json.loads(response.body)
    assert payload["chain"]["status"] == "chain_absent"
    assert payload["domain"]["status"] == "domain_pending"
    assert payload["state"] == "domain-pending"
    assert payload["notices"][0]["code"] == "verification_pending"


def test_raising_card_fetcher_is_could_not_verify_and_surfaces_explanatory_notice(monkeypatch, tmp_path):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)

    def fetch_assets(deed_uid):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(capture_mod, "MnemosyneEvidence", lambda account, **kwargs: _Evidence(account, assets=fetch_assets))

    body = {"tx_id": "tx-1", "operations": [_stake_op("deed-1", "CARD-1")]}
    response = op_verify(body, _request(config), player=None)
    payload = json.loads(response.body)

    assert payload["state"] == "domain-could-not-verify"
    assert payload["domain"]["status"] == "domain_check_failed"
    assert [notice["code"] for notice in payload["notices"]] == [
        "verification_could_not_verify", "verification_domain_check_failed"
    ]
    assert payload["notices"][1]["severity"] != "danger"


def test_structurally_malformed_card_op_still_reports_mismatch(monkeypatch, tmp_path):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    payload = json.loads(_stake_op("deed-1", "CARD-1")[1]["json"])
    payload.pop("deed_uid")
    operation = ["custom_json", {"id": "sm_stake_change", "json": json.dumps(payload)}]

    response = op_verify({"tx_id": "tx-1", "operations": [operation]}, _request(config), player=None)
    result = json.loads(response.body)

    assert result["state"] == "domain-mismatch"
    assert result["domain"]["status"] == "domain_mismatch"
    assert result["notices"][0]["code"] == "verification_mismatch"


def test_domain_mismatch_is_not_lost_when_chain_read_is_inconclusive(monkeypatch, tmp_path):
    """Injected defect: gated domain-mismatch on chain_verified; this test went red with chain_absent before restoration."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(
        routes_op_preview_mod,
        "verify_chain",
        lambda *_args, **_kwargs: {"status": "chain_absent", "evidence": {"reason_kind": "not_found"}},
    )
    operation = _stake_op("deed-1", "CARD-1")
    malformed_payload = json.loads(operation[1]["json"])
    malformed_payload.pop("deed_uid")
    operation[1]["json"] = json.dumps(malformed_payload)

    response = op_verify(
        {"tx_id": "tx-1", "operations": [operation]},
        _request(config),
        player=None,
    )
    result = json.loads(response.body)

    assert set(result) == {
        "state", "chain", "domain", "game", "refreshed_deeds", "refresh_errors", "combine_refresh", "notices", "notice_catalog", "op_results", "op_outcomes", "combine_result", "combine_observed_at",
    }
    assert result["state"] == "domain-mismatch"
    assert result["domain"]["status"] == "domain_mismatch"
    assert result["notices"][0]["code"] == "verification_mismatch"


def test_correct_worksite_readback_is_confirmed_and_refreshes_the_deed(monkeypatch, tmp_path):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    monkeypatch.setattr(capture_mod, "MnemosyneEvidence", lambda account, **kwargs: _Evidence(account, worksite=lambda _uid: {"project_type": "worksite_aura_construction"}))
    refreshed = []
    monkeypatch.setattr(capture_mod, "expand_plots", lambda config, account, deed_uids, **kwargs: (refreshed.extend(deed_uids) or {"capture": {"refreshed_deeds": list(deed_uids), "refresh_errors": []}}))

    body = {"tx_id": "tx-1", "operations": [_worksite_op("deed-1", "aura")]}
    response = op_verify(body, _request(config), player=None)
    payload = json.loads(response.body)

    assert payload["state"] == "domain-confirmed"
    assert payload["domain"]["status"] == "domain_verified"
    assert refreshed == ["deed-1"]
    notice = payload["notices"][0]
    assert notice["code"] == "verification_confirmed"


def test_confirmed_refresh_is_persisted_before_response(monkeypatch, tmp_path):
    """A confirmed response must not expose a deed until its fresh state is persisted."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    monkeypatch.setattr(capture_mod, "MnemosyneEvidence", lambda account, **kwargs: _Evidence(account, worksite=lambda _uid: {"project_type": "worksite_aura_construction"}))
    calls = []
    monkeypatch.setattr(capture_mod, "expand_plots", lambda config, account, deed_uids, **kwargs: (calls.extend(deed_uids) or {"capture": {"refreshed_deeds": list(deed_uids), "refresh_errors": []}}))

    body = {"tx_id": "tx-1", "operations": [_worksite_op("deed-1", "aura")]}
    response = op_verify(body, _request(config), player=None)
    payload = json.loads(response.body)

    assert payload["state"] == "domain-confirmed"
    assert payload["refreshed_deeds"] == ["deed-1"]
    assert calls == ["deed-1"]
