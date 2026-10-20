""" -- `/api/op-verify` unconditionally routed every operation through
`LandVerificationSpec`, which requires a `deed_uid` on every non-worksite op. A real
`sm_combine_cards` payload has no `deed_uid` at all, so a SUCCESSFUL combine was reported
as `domain-mismatch` -- deterministically, not intermittently. These exercise the fix
through the real route function (`ceres.routes_op_preview.op_verify`), using the actual
observed on-chain payload shape (synthetic ids).

MUTATION INJECTION (recorded per the project's mutation standard, the internal rules): with the
dispatch in `ceres/routes_op_preview.py::op_verify` forced to `is_combine_batch = False`
(i.e. routing the combine op through `LandVerificationSpec` regardless -- the exact 
defect), `test_real_combine_payload_is_domain_confirmed_not_mismatch` goes red with
`AssertionError: assert 'domain-mismatch' == 'domain-confirmed'`, `domain.evidence.mismatches
== ["op has no deed_uid: {...}"]`. Restoring the dispatch makes it green again.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from ceres import capture as capture_mod
import ceres.routes_op_preview as routes_op_preview_mod
from ceres.config import CeresConfig
from ceres.routes_op_preview import op_verify
from praetoria.transport import ResponseData


class _LandEvidence:
    def __init__(self, account, **_kwargs):
        self.account = account
        self.capture = self

    def assets(self, _uid):
        return ResponseData({"status": "success", "data": {"cards": [{"uid": "CARD-1", "slot": "1"}], "items": []}}, "2026-09-05T00:00:00Z", 1)

    def worksite_facts(self, _uid):
        return None

    def provenance(self):
        return {"account": self.account, "resources": {}}

# A synthetic sm_combine_cards payload in the observed shape -- flat `cards` array plus `app`/`n`, no `op` field, no `deed_uid`.
REAL_COMBINE_PAYLOAD = {
    "cards": ["G18-100-000001", "G18-100-000002"],
    "app": "splinterlands/0.7.60",
    "n": "AbCdEfGhIj",
}


def _combine_op() -> list:
    return ["custom_json", {
        "id": "sm_combine_cards", "json": json.dumps(REAL_COMBINE_PAYLOAD),
        "required_auths": ["player0005"], "required_posting_auths": [],
    }]


def _request(config):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))


def _chain_verified_stub(*_args, **_kwargs):
    return {"status": "chain_verified", "evidence": {"tx_id": "tx-combine-1", "observed": []}}


def _pre_combine_records() -> dict:
    # Higher BCX wins the survivor rule (fides_cards/ops.py::survivor_rule) -- the
    # 003832 copy (bcx 10) survives, 003873 (bcx 5) is consumed. card_detail_id is
    # required for `_card_id` to resolve, matching a real collection row's shape.
    return {
        "cards": [
            {"uid": "G18-100-000001", "card_detail_id": "879", "bcx": 10, "level": 1},
            {"uid": "G18-100-000002", "card_detail_id": "879", "bcx": 5, "level": 1},
        ],
        "coverage": {"collection": {"complete": True}},
        "generated_at": "2026-08-26T08:00:00Z",
    }


def _post_combine_collection() -> SimpleNamespace:
    # The live re-read after the combine has propagated: the survivor carries the
    # merged bcx (10 + 5 = 15); the consumed copy is gone entirely from the collection.
    return SimpleNamespace(data={"cards": [
        {"uid": "G18-100-000001", "card_detail_id": "879", "bcx": 15, "level": 1},
    ]})


def test_real_combine_payload_is_domain_confirmed_not_mismatch(monkeypatch, tmp_path):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (_pre_combine_records(), None))
    # A confirmed combine now also triggers a background quick re-capture
    # see routes_op_preview.py's `combine_refresh` block) so the board stops serving
    # the pre-combine collection; stubbed here to a no-op so this test never spawns a
    # real background thread hitting the live API.
    monkeypatch.setattr(capture_mod, "is_running", lambda player: False)
    started_for: list[str] = []

    def _fail_if_full_started(config, player):
        raise AssertionError("confirmed combines must use quick capture, not full capture")

    monkeypatch.setattr(capture_mod, "start_refresh_full", _fail_if_full_started, raising=False)  # Q9: removed
    monkeypatch.setattr(capture_mod, "start_refresh", lambda config, player: started_for.append(player) or True)

    class _FakeClient:
        def collection(self, player):
            return _post_combine_collection()

    monkeypatch.setattr(routes_op_preview_mod, "SplinterlandsClient", _FakeClient)

    body = {"tx_id": "tx-combine-1", "operations": [_combine_op()], "verification_attempt": 1}
    response = op_verify(body, _request(config), player=None)
    payload = json.loads(response.body)

    # THE FIX: not domain-mismatch. Before the fix this was deterministically
    # "domain-mismatch" with evidence.mismatches == ["op has no deed_uid: ..."].
    assert payload["state"] == "domain-confirmed"
    assert payload["domain"]["status"] == "domain_verified"
    assert payload["domain"]["evidence"]["survivor_uid"] == "G18-100-000001"
    assert payload["domain"]["evidence"]["present_consumed_uids"] == []
    # A confirmed combine has no deed_uid, so the deed_uid-keyed refresh loop above
    # cannot pick it up -- this is the SEPARATE combine-specific trigger.
    assert payload["refreshed_deeds"] == []
    assert payload["combine_refresh"] == {"triggered": True, "mode": "quick", "already_running": False}
    assert started_for == ["player0005"]


def test_confirmed_combine_triggers_quick_recapture_not_full(monkeypatch, tmp_path):
    """MUTATION INJECTION: restore `start_refresh_full` in the route -- this goes red
    because `quick_started` stays empty and `full_started` is populated. The guard
    ensures the combine path keeps using the cheap capture."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (_pre_combine_records(), None))
    monkeypatch.setattr(capture_mod, "is_running", lambda player: False)
    full_started: list[str] = []
    quick_started: list[str] = []
    monkeypatch.setattr(capture_mod, "start_refresh_full", lambda config, player: full_started.append(player) or True, raising=False)
    monkeypatch.setattr(capture_mod, "start_refresh", lambda config, player: quick_started.append(player) or True)

    class _FakeClient:
        def collection(self, player):
            return _post_combine_collection()

    monkeypatch.setattr(routes_op_preview_mod, "SplinterlandsClient", _FakeClient)

    body = {"tx_id": "tx-combine-1", "operations": [_combine_op()], "verification_attempt": 1}
    op_verify(body, _request(config), player=None)

    assert quick_started == ["player0005"]
    assert full_started == []


def test_combine_refresh_replaces_an_already_running_capture(monkeypatch, tmp_path):
    """A pre-confirmation read can contain the old collection and must be replaced."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (_pre_combine_records(), None))
    monkeypatch.setattr(capture_mod, "is_running", lambda player: True)

    started_for = []
    monkeypatch.setattr(capture_mod, "start_refresh", lambda config, player: started_for.append(player) or True)

    class _FakeClient:
        def collection(self, player):
            return _post_combine_collection()

    monkeypatch.setattr(routes_op_preview_mod, "SplinterlandsClient", _FakeClient)

    body = {"tx_id": "tx-combine-1", "operations": [_combine_op()], "verification_attempt": 1}
    response = op_verify(body, _request(config), player=None)
    payload = json.loads(response.body)

    assert payload["combine_refresh"] == {"triggered": True, "mode": "quick", "already_running": True}
    assert started_for == ["player0005"]


def test_unpropagated_combine_is_pending_not_mismatch(monkeypatch, tmp_path):
    """The consumed copy is still present (propagation lag) -- must be pending, never
    treated as either success or a hard failure."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (_pre_combine_records(), None))

    class _FakeClient:
        def collection(self, player):
            # Stale read: both pre-combine copies still present, neither merged yet.
            return SimpleNamespace(data={"cards": _pre_combine_records()["cards"]})

    monkeypatch.setattr(routes_op_preview_mod, "SplinterlandsClient", _FakeClient)

    body = {"tx_id": "tx-combine-1", "operations": [_combine_op()]}
    response = op_verify(body, _request(config), player=None)
    payload = json.loads(response.body)

    assert payload["state"] == "domain-pending"
    assert payload["domain"]["status"] == "domain_pending"
    assert payload["refreshed_deeds"] == []  # never refreshed on a non-confirmed state


def test_missing_pre_combine_snapshot_is_could_not_verify(monkeypatch, tmp_path):
    """No snapshot to predict the survivor from -- genuinely unknown, never mismatch."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (None, None))

    class _FakeClient:
        def collection(self, player):
            raise AssertionError("must not be called when no expected_records are available")

    monkeypatch.setattr(routes_op_preview_mod, "SplinterlandsClient", _FakeClient)

    body = {"tx_id": "tx-combine-1", "operations": [_combine_op()]}
    response = op_verify(body, _request(config), player=None)
    payload = json.loads(response.body)

    assert payload["state"] == "domain-could-not-verify"
    assert payload["domain"]["status"] == "domain_could_not_verify"


def test_land_ops_still_go_through_land_verification_spec(monkeypatch, tmp_path):
    """Guard against a dispatch that routes everything to the combine checker instead --
    a land op must still resolve via LandVerificationSpec, unchanged from before this fix."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    monkeypatch.setattr(capture_mod, "MnemosyneEvidence", _LandEvidence)

    stake_payload = {
        "unstake": {"cards": [], "items": []},
        "stake": {"cards": [{"card_uid": "CARD-1", "stake_type_uid": "STK-LND-WKR", "slot": "1"}], "items": []},
        "deed_uid": "deed-1", "auto_buy_grain": False, "app": "splinterlands/0.7.170", "n": "abcdefghij",
    }
    op = ["custom_json", {"id": "sm_stake_change", "json": json.dumps(stake_payload)}]

    response = op_verify({"tx_id": "tx-land-1", "operations": [op]}, _request(config), player=None)
    payload = json.loads(response.body)

    assert payload["state"] == "domain-confirmed"
    assert payload["domain"]["status"] == "domain_verified"
