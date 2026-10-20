""" `/api/op-verify`'s domain check for a card combine derived
`expected_records` from `capture.latest_snapshot()`, but by the time verify runs the
destroyed input copies are already gone from the live collection and any capture taken
between the combine and the verify replaces the snapshot with POST-combine data. So
domain verification could never reach `domain-confirmed` by construction -- the driving
report against the live server (maintainer, 2026-08-30) showed `domain-could-not-verify` on
three real on-chain combines, with the arrival state never rendering and the
server-side recapture never firing as a result.

The fix: `/api/combine/build` already reads the live collection to resolve each input's
BCX (to predict the survivor); `ceres/combine_evidence.py` persists exactly that read,
keyed by the op's own nonce (`n`), which the client already round-trips verbatim through
Keychain and back into its `/api/op-verify` call. `/api/op-verify` now prefers this
carried evidence over `latest_snapshot()`, falling back to the snapshot only when no
evidence was carried (nonce missing/expired, or the route is reached directly without a
prior `/api/combine/build` call -- exactly what every pre-existing test in
`test_op_verify_combine.py` does, and which must keep passing unchanged).

MUTATION INJECTIONS (recorded per the project's mutation standard, the internal rules):

1. `test_carried_evidence_reaches_domain_confirmed_when_snapshot_is_unavailable` --
 forcing `expected_records = None` unconditionally in
 `ceres/routes_op_preview.py::op_verify` (i.e. dropping the carried evidence and
 always falling through to the snapshot branch) goes red: the stubbed
 `capture_mod.latest_snapshot` raises `RuntimeError("no snapshot available")` in this
 test (the exact reported condition -- no usable snapshot exists at verify time), so
 `domain-could-not-verify` renders instead of `domain-confirmed`,
 `AssertionError: assert 'domain-could-not-verify' == 'domain-confirmed'`.

2. `test_missing_carried_evidence_never_false_confirms` -- forcing the fallback branch
 to fabricate a confirm when `expected_records` is `None` (e.g. `state =
 "domain-confirmed"` unconditionally rather than routing through
 `CardVerificationSpec.check_domain`) goes red:
 `AssertionError: assert 'domain-confirmed' == 'domain-could-not-verify'`.

3. `test_carried_evidence_still_reports_pending_when_unpropagated` -- collapsing
 `domain_pending` into `domain-confirmed` in the `op_verify` status dispatch goes red:
 `AssertionError: assert 'domain-confirmed' == 'domain-pending'`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ceres import capture as capture_mod
from ceres.config import CeresConfig
from ceres.routes_combine import combine_build
import ceres.routes_op_preview as routes_op_preview_mod
from ceres.routes_op_preview import op_verify
from fides.op import Op
from fides_cards.verify import CardVerificationSpec

# Printings in the shape of an on-chain combine of gold-foil copies
# of the same card. card_detail_id/edition/gold are arbitrary-but-consistent stand-ins
# for that card's actual identity fields -- what matters for this fix is the shape
# (uid/card_detail_id/edition/gold/bcx/level), not the specific card.
SURVIVOR_UID = "G-3N15RQTMXS"
CONSUMED_UID = "G-IZMUIEO55C"

PAYLOAD_SURVIVOR_UID = "C2-858-4Q5U1RLFJ4"
PAYLOAD_CONSUMED_UID = "C2-858-I6H034Z5FK"
COLLECTION_WINNER_UID = "C3-79-0B847L3PGG"


def _request(config):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))


def _pre_combine_report() -> dict:
    return {
        "cards": [
            {"uid": SURVIVOR_UID, "card_detail_id": "49", "edition": "6", "gold": True, "bcx": 10, "level": 1},
            {"uid": CONSUMED_UID, "card_detail_id": "49", "edition": "6", "gold": True, "bcx": 4, "level": 1},
        ],
        "coverage": {"collection": {"complete": True}},
        "generated_at": "2026-08-30T08:00:00Z",
    }


def _chain_verified_stub(*_args, **_kwargs):
    return {"status": "chain_verified", "evidence": {"tx_id": "tx-evidence-1", "observed": []}}


def _build_and_get_operation(config, monkeypatch) -> dict:
    """Drives the real `/api/combine/build` route so the evidence file is written
 exactly the way the live server writes it -- not a hand-constructed fixture."""
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda cfg, player: (_pre_combine_report(), None))
    from ceres import routes_combine
    monkeypatch.setattr(routes_combine, "_live_collection", lambda account: (_pre_combine_report(), "2026-08-30T08:00:00Z"))
    body = {"uids": [SURVIVOR_UID, CONSUMED_UID]}
    response = combine_build(body, _request(config), player="player0005")
    return json.loads(response.body)["operation"]


def _stub_common(monkeypatch, *, latest_snapshot):
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified_stub)
    monkeypatch.setattr(capture_mod, "latest_snapshot", latest_snapshot)
    monkeypatch.setattr(capture_mod, "is_running", lambda player: False)
    monkeypatch.setattr(capture_mod, "start_refresh", lambda cfg, player: True)


def _scope_test_op() -> Op:
    return Op(
        operation_id="sm_combine_cards",
        json_payload={"cards": [PAYLOAD_SURVIVOR_UID, PAYLOAD_CONSUMED_UID], "app": "ceres-fides/0.1.0", "n": "scope-test"},
    )


def test_domain_survivor_is_scoped_to_payload_not_collection_winner():
    """The survivor evidence must come from the combine payload.

 Mutation injection for independent observation: restore
 ``survivor_rule(self.expected_records)`` in ``CardVerificationSpec.check_domain``.
 The assertion must then go red because the collection-wide 740-BCX winner is not one
 of the two combined cards.
    """
    expected_records = [
        {"uid": PAYLOAD_SURVIVOR_UID, "card_detail_id": "858", "edition": 2, "gold": False, "bcx": 1, "level": 1},
        {"uid": PAYLOAD_CONSUMED_UID, "card_detail_id": "858", "edition": 2, "gold": False, "bcx": 1, "level": 1},
        {"uid": COLLECTION_WINNER_UID, "card_detail_id": "79", "edition": 3, "gold": False, "bcx": 740, "level": 1},
    ]
    observed = {
        "cards": [
            {"uid": PAYLOAD_SURVIVOR_UID, "card_detail_id": "858", "edition": 2, "gold": False, "bcx": 2, "level": 1},
        ]
    }

    result = CardVerificationSpec(lambda: observed, expected_records=expected_records).check_domain(
        "scope-test", [_scope_test_op()]
    )

    assert result["status"] == "domain_verified"
    assert result["evidence"]["survivor_uid"] == PAYLOAD_SURVIVOR_UID
    assert result["evidence"]["survivor_uid"] in result["evidence"]["consumed_uids"]


def test_domain_unknown_survivor_keeps_specific_refusal_reason():
    """An unknown survivor retains the dedicated refusal reason.

 Mutation injection for independent observation: remove the ``survivor_uid ==
 \"unknown\"`` early return in ``CardVerificationSpec._expected``. The assertion must
 then go red because ``_expected`` falls through to its generic unavailable-input
 refusal instead of the existing survivor-specific reason.
    """
    expected_records = [
        {"uid": PAYLOAD_SURVIVOR_UID, "card_detail_id": "858", "edition": 2, "gold": False, "bcx": 1, "level": 1},
        {"uid": PAYLOAD_CONSUMED_UID, "card_detail_id": "858", "edition": 2, "gold": False, "level": 1},
    ]

    result = CardVerificationSpec(lambda: {"cards": []}, expected_records=expected_records).check_domain(
        "scope-test", [_scope_test_op()]
    )

    assert result == {
        "status": "domain_could_not_verify",
        "evidence": {"reason": "survivor cannot be predicted from the pre-combine records"},
    }


def test_carried_evidence_reaches_domain_confirmed_when_snapshot_is_unavailable(tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    operation = _build_and_get_operation(config, monkeypatch)

    def _no_snapshot(cfg, player):
        # The exact reported condition: no usable snapshot exists at verify time
        # (destroyed copies gone, no capture between combine and verify). If the fix
        # regresses to always deriving expected_records from this call, this raise
        # surfaces as the mutation's red assertion below, not a masked exception
        # op_verify's own try/except around latest_snapshot logs and degrades to None.
        raise RuntimeError("no snapshot available")

    _stub_common(monkeypatch, latest_snapshot=_no_snapshot)

    class _FakeClient:
        def collection(self, player):
            # The merged survivor: bcx 10 + 4 = 14, consumed copy gone entirely.
            return SimpleNamespace(data={"cards": [
                {"uid": SURVIVOR_UID, "card_detail_id": "49", "edition": "6", "gold": True, "bcx": 14, "level": 1},
            ]})

    monkeypatch.setattr(routes_op_preview_mod, "SplinterlandsClient", _FakeClient)

    body = {"tx_id": "tx-evidence-1", "operations": [operation], "verification_attempt": 1}
    response = op_verify(body, _request(config), player="player0005")
    payload = json.loads(response.body)

    assert payload["state"] == "domain-confirmed"
    assert payload["domain"]["status"] == "domain_verified"
    assert payload["domain"]["evidence"]["survivor_uid"] == SURVIVOR_UID
    assert payload["domain"]["evidence"]["present_consumed_uids"] == []
    assert payload["combine_refresh"] == {"triggered": True, "mode": "quick", "already_running": False}


def test_evidence_is_discarded_after_a_confirmed_verify(tmp_path, monkeypatch):
    """The carried evidence file must not be replayable after it has done its job --
 a second verify of the SAME nonce with no evidence left must degrade to the
 snapshot fallback, never silently re-confirm off a stale file."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    operation = _build_and_get_operation(config, monkeypatch)
    nonce = json.loads(operation[1]["json"])["n"]
    from ceres import combine_evidence
    assert combine_evidence.load(config, nonce, account="player0005") is not None

    _stub_common(monkeypatch, latest_snapshot=lambda cfg, player: (_pre_combine_report(), None))

    class _FakeClient:
        def collection(self, player):
            return SimpleNamespace(data={"cards": [
                {"uid": SURVIVOR_UID, "card_detail_id": "49", "edition": "6", "gold": True, "bcx": 14, "level": 1},
            ]})

    monkeypatch.setattr(routes_op_preview_mod, "SplinterlandsClient", _FakeClient)

    body = {"tx_id": "tx-evidence-1", "operations": [operation], "verification_attempt": 1}
    op_verify(body, _request(config), player="player0005")

    assert combine_evidence.load(config, nonce, account="player0005") is None


def test_missing_carried_evidence_never_false_confirms(tmp_path, monkeypatch):
    """No prior `/api/combine/build` call ever happened for this op (matches every
 pre-existing test in test_op_verify_combine.py), and the snapshot fallback also has
 nothing to offer -- must be could-not-verify, never a confirm fabricated from
 absent evidence."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    payload = {"cards": [SURVIVOR_UID, CONSUMED_UID], "app": "splinterlands/0.7.60", "n": "NeverBuilt01"}
    operation = ["custom_json", {
        "id": "sm_combine_cards", "json": json.dumps(payload),
        "required_auths": ["player0005"], "required_posting_auths": [],
    }]

    _stub_common(monkeypatch, latest_snapshot=lambda cfg, player: (None, None))

    class _FakeClient:
        def collection(self, player):
            raise AssertionError("must not be called when no expected_records are available")

    monkeypatch.setattr(routes_op_preview_mod, "SplinterlandsClient", _FakeClient)

    body = {"tx_id": "tx-evidence-2", "operations": [operation]}
    response = op_verify(body, _request(config), player="player0005")
    result = json.loads(response.body)

    assert result["state"] == "domain-could-not-verify"
    assert result["domain"]["status"] == "domain_could_not_verify"


def test_carried_evidence_still_reports_pending_when_unpropagated(tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    operation = _build_and_get_operation(config, monkeypatch)

    _stub_common(monkeypatch, latest_snapshot=lambda cfg, player: (_pre_combine_report(), None))

    class _FakeClient:
        def collection(self, player):
            # Stale read: both pre-combine copies still present, neither merged yet.
            return SimpleNamespace(data={"cards": _pre_combine_report()["cards"]})

    monkeypatch.setattr(routes_op_preview_mod, "SplinterlandsClient", _FakeClient)

    body = {"tx_id": "tx-evidence-1", "operations": [operation]}
    response = op_verify(body, _request(config), player="player0005")
    payload = json.loads(response.body)

    assert payload["state"] == "domain-pending"
    assert payload["domain"]["status"] == "domain_pending"


def test_evidence_for_a_different_account_is_never_used(tmp_path, monkeypatch):
    """A nonce is not scoped by account in the file name alone (collisions are
 astronomically unlikely but not impossible) -- the account check inside
 combine_evidence.load is the actual guard. Verifying as a different player than
 the one who built the op must not pick up the first account's evidence."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    operation = _build_and_get_operation(config, monkeypatch)

    _stub_common(monkeypatch, latest_snapshot=lambda cfg, player: (None, None))

    class _FakeClient:
        def collection(self, player):
            raise AssertionError("must not be called when no expected_records are available")

    monkeypatch.setattr(routes_op_preview_mod, "SplinterlandsClient", _FakeClient)

    body = {"tx_id": "tx-evidence-1", "operations": [operation]}
    response = op_verify(body, _request(config), player="someoneelse")
    payload = json.loads(response.body)

    assert payload["state"] == "domain-could-not-verify"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
