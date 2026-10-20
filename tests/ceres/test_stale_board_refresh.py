"""Stale-worker-after-broadcast fix (2026-08-24, a real reported case): after
unstaking Lyanna Natura from plot 41-9-56 / `I-294-99ed4b7a4d170e` via Ceres, the live
Splinterlands API confirmed slot 1 was free, but Ceres kept showing her, `worker_count: 6`,
and clicking Refresh did not help. Root-caused by Asclepius, read-only, cited below.

Two independent fixes:

1. `ceres/routes_op_preview.py::op_verify` -- on `domain-confirmed`, re-fetch and persist
 every deed touched by the broadcast ops via `capture_mod.expand_plots`, instead of
 throwing the domain-check's own fresh fetch away. `expand_plots` merges through
 `merge_reports` with a genuinely `retrieved` incoming plot, which REPLACES (not
 preserves) the base's stale worker rows, and persists via
 `_save_snapshot_and_refresh_cache`, which advances the snapshot mtime and so
 invalidates `routes_board.py`'s `_board_cache` for free. `/api/board` itself is
 untouched -- it still never triggers a capture; the mutation is driven entirely by the
 broadcast-confirmation path.

2. `ceres/capture.py::merge_reports` -- a cheap, class-wide backstop: a quick refresh whose
 bulk `worker_count` for a deed CONTRADICTS the count of that deed's own worker rows in
 the base snapshot now drops the stale base plot instead of preserving it. A quick
 refresh that merely LACKS asset detail (no contradiction, nothing new to disprove the
 base) still preserves the enriched base -- the original "never regress a board a prior
 full capture enriched" intent survives unchanged for that, the normal, case.

FOLLOW-UP (same date): Asclepius found `domain-confirmed` was actually UNREACHABLE --
`/api/op-verify` 500ed on a real happy-path batch, so the refresh wiring above never
fired in practice until this was fixed too:

3. THE 500 -- `check_domain` (`fides_land/verify.py`) consumes body-form op dicts
 (`raw_op.get("json")`), not the Keychain pair form `["custom_json", {...}]` that
 `_expected_ops` validates `operations` to be. `routes_op_preview.py:162` now unwraps
 (`[op[1] for op in raw_operations]`) before calling it. The early tests in this file
 (`test_domain_confirmed_refreshes_and_persists_the_touched_deed`,
 `test_domain_mismatch_does_not_trigger_a_refresh`) monkeypatch `LandVerificationSpec`
 wholesale and so never exercised the real shape contract -- that is exactly how this
 shipped broken.
 `test_domain_confirmed_reachable_with_the_real_verification_stack` below is the
 regression test: it drives the REAL `verify_chain` and the REAL
 `LandVerificationSpec.check_domain` (only the actual network boundaries -- the Hive RPC
 transport and `MnemosyneEvidence.assets` -- are mocked), with a genuine
 Keychain-shaped `operations` array.

4. THE `chain_absent` RACE -- `op_verify` makes one direct `verify_chain` lookup and runs the
 domain check EVEN WHEN that chain check stays inconclusive, letting a `domain_verified`
 result stand on its own -- the domain re-read is authoritative and does not depend on Hive
 RPC indexing at all. `chain_absent` + `domain_verified` is now a CONFIRMATION, not a failure.

5. `fides/verify.py::get_transaction` now distinguishes `"not_found"` (the node responded
 affirmatively that the tx does not exist) from `"lookup_failed"` (a transport-level
 failure -- NOT evidence of absence), preserved as `chain.evidence.reason_kind`. See
 `tests/fides/test_verify.py` for the unit-level coverage of that distinction;
 `fides/ledger.py`'s `VALID_STATES`/`TERMINAL_FAILURE_STATES` are UNCHANGED -- `chain`'s
 top-level `status` vocabulary stays exactly `{"chain_verified", "chain_absent"}`, since
 `fides/cli.py`'s `record_chain_result` only ever accepts those two. `reason_kind` is new
 evidence detail, not a new ledger state, so no ledger semantics changed.

CURRENT SEAM: operation-equality mismatch detection belongs to `fides.verify.verify_chain`,
not `/api/op-inclusion`. The latter exposes only whether a block number exists and cannot
replace the mismatch guard. `test_chain_mismatch_is_reported_by_verify_chain` below keeps that
direct behavior under a transport injection.
"""

from __future__ import annotations

import json as json_mod

from types import SimpleNamespace

from ceres import capture as capture_mod
from ceres.config import CeresConfig
from ceres.routes_op_preview import op_verify
from praetoria.transport import ResponseData
from fides import verify as fides_verify

# Real deed + real worker set from the player0005 snapshot (~/.ceres/snapshots
# land-player0005-latest.json), matching the exact reported plot: I-294-99ed4b7a4d170e
# staking_facts.worker_count 6, six real workers including "Lyanna Natura" in slot 1
# (asset_ref d6b2cf51f5db22b08388d0dd, uid G-TESTCARD01). Copied verbatim to anchor the
# fixtures below in the real shape of the bug; the report-level plumbing (the surrounding
# `report` dict) is a minimal fixture, stated per test.
REAL_DEED_UID = "I-294-99ed4b7a4d170e"
REAL_WORKERS = [
    {"asset_ref": "d6b2cf51f5db22b08388d0dd", "uid": "G-TESTCARD01", "name": "Lyanna Natura", "slot": 1, "stake_ref_uid": REAL_DEED_UID},
    {"asset_ref": "7ec99c3b594ade26f2df0fd9", "uid": "G-2XBICU7WTS", "name": "Silver Shield Paladin", "slot": 2, "stake_ref_uid": REAL_DEED_UID},
    {"asset_ref": "3c1715af6e3c3dab25fc879a", "uid": "G1-33-STZWZZ7BI8", "name": "Spirit of the Forest", "slot": 3, "stake_ref_uid": REAL_DEED_UID},
    {"asset_ref": "36e25d50035f7b6a1960447b", "uid": "C1-112-9FC8NCRTFK", "name": "Prince Rennyn", "slot": 4, "stake_ref_uid": REAL_DEED_UID},
    {"asset_ref": "c0d1a943864eabe0455adbfd", "uid": "C19-867-029SO0Y0FK", "name": "Kinbound Druid", "slot": 5, "stake_ref_uid": REAL_DEED_UID},
    {"asset_ref": "304faf190d28c8f9ba92c694", "uid": "C2-505-0000000001", "name": "Runi", "slot": 0, "stake_ref_uid": REAL_DEED_UID, "stake_type_uid": "STK-LND-RUNI"},
]


def _real_plot(worker_count: int, *, retrieved: bool) -> dict:
    return {
        "deed_uid": REAL_DEED_UID,
        "facts": {"plot": REAL_DEED_UID},
        "staking_facts": {"max_workers_allowed": 6, "worker_count": worker_count},
        "asset_fetch": {"status": "retrieved" if retrieved else "not_requested"},
    }


# 1. op_verify: domain-confirmed refreshes the touched deed's snapshot on disk.

def test_domain_confirmed_refreshes_and_persists_the_touched_deed(monkeypatch, tmp_path):
    """Real deed/worker shape (see module docstring). `verify_chain` and
 `LandVerificationSpec.check_domain` are monkeypatched to force the confirmed branch
 (a real chain/domain check needs a real broadcast, out of scope for a unit test) --
 mutation evidence: pass `include_collection=True` for this stake refresh; expected red:
 the targeted-refresh contract regresses to the account-wide collection sweep. Everything
 downstream of that (`expand_plots`, `merge_reports`,
 `_save_snapshot_and_refresh_cache`) runs for real against a tmp_path snapshot store."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    config.ensure_dirs()

    stale_report = {
        "generated_at": "2026-08-24T00:00:00Z",
        "plots": [_real_plot(6, retrieved=True)],
        "workers": list(REAL_WORKERS),
        "cards": [], "bonus_assets": [], "unassigned_items": {}, "coverage": {},
    }
    capture_mod._save_snapshot_and_refresh_cache(stale_report, capture_mod._latest_path(config, "player0005"))

    def fake_expand_deeds(self, deed_uids, player, **kwargs):
        assert set(kwargs) == {"evidence", "include_collection"}
        assert kwargs["evidence"].account == player
        assert kwargs["evidence"].capture is self
        assert kwargs["include_collection"] is False
        assert deed_uids == [REAL_DEED_UID]
        assert player == "player0005"
        # The real post-unstake state: five workers, Lyanna gone, worker_count now 5.
        return {
            "generated_at": "2026-08-24T00:05:00Z",
            "plots": [_real_plot(5, retrieved=True)],
            "workers": [w for w in REAL_WORKERS if w["name"] != "Lyanna Natura"],
            "cards": [], "bonus_assets": [], "unassigned_items": {},
        }

    monkeypatch.setattr(capture_mod.LandCapture, "expand_deeds", fake_expand_deeds)
    monkeypatch.setattr("ceres.routes_op_preview.verify_chain", lambda expected, tx_id: {"status": "chain_verified"})
    monkeypatch.setattr(
        "ceres.routes_op_preview.LandVerificationSpec",
        lambda fetch_assets, **kwargs: SimpleNamespace(check_domain=lambda kind, ops: {"status": "domain_verified"}),
    )

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))
    payload = {"unstake": {"cards": [{"card_uid": "G-TESTCARD01"}], "items": []}, "stake": {"cards": [], "items": []}, "deed_uid": REAL_DEED_UID}
    body = {
        "tx_id": "fake-tx-id",
        "operations": [["custom_json", {"id": "sm_stake_change", "json": __import__("json").dumps(payload), "required_auths": [], "required_posting_auths": ["player0005"]}]],
    }

    response = op_verify(body, request, player="player0005")
    content = __import__("json").loads(response.body)
    assert content["state"] == "domain-confirmed"
    assert content["refreshed_deeds"] == [REAL_DEED_UID]
    assert content["refresh_errors"] == []

    # The snapshot ON DISK -- what /api/board actually serves -- no longer carries Lyanna
    # and worker_count reflects the removal, with no extra capture round-trip needed.
    persisted, _ref = capture_mod.latest_snapshot(config, "player0005")
    names = {w["name"] for w in persisted["workers"]}
    assert "Lyanna Natura" not in names
    plot = next(p for p in persisted["plots"] if p["deed_uid"] == REAL_DEED_UID)
    assert plot["staking_facts"]["worker_count"] == 5


def test_domain_mismatch_does_not_trigger_a_refresh(monkeypatch, tmp_path):
    """A domain-mismatch (the hazard-class outcome the internal rules warns about -- chain-confirmed
 but domain-rejected) must not call `expand_plots` at all: there is nothing to refresh
 because the mutation the user staged never actually applied."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    config.ensure_dirs()
    calls: list[str] = []
    monkeypatch.setattr(capture_mod, "expand_plots", lambda cfg, player, deed_uids, **kwargs: calls.extend(deed_uids) or {"capture": {"refreshed_deeds": list(deed_uids), "refresh_errors": []}})
    monkeypatch.setattr("ceres.routes_op_preview.verify_chain", lambda expected, tx_id: {"status": "chain_verified"})
    monkeypatch.setattr(
        "ceres.routes_op_preview.LandVerificationSpec",
        lambda fetch_assets, **kwargs: SimpleNamespace(check_domain=lambda kind, ops: {"status": "domain_mismatch"}),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))
    payload = {"unstake": {"cards": [{"card_uid": "G-TESTCARD01"}], "items": []}, "stake": {"cards": [], "items": []}, "deed_uid": REAL_DEED_UID}
    body = {
        "tx_id": "fake-tx-id",
        "operations": [["custom_json", {"id": "sm_stake_change", "json": __import__("json").dumps(payload), "required_auths": [], "required_posting_auths": ["player0005"]}]],
    }
    response = op_verify(body, request, player="player0005")
    content = __import__("json").loads(response.body)
    assert content["state"] == "domain-mismatch"
    assert calls == []
    assert content["refreshed_deeds"] == []


# 2. merge_reports: the class-wide worker_count-contradiction guard.

def test_quick_refresh_drops_stale_base_plot_when_bulk_worker_count_contradicts_it():
    """Real deed/worker shape. Base snapshot: retrieved plot, 6 real workers (incl.
 Lyanna). Incoming (a quick, bulk-only refresh -- no per-deed assets, but bulk
 worker_count IS always present): worker_count now 5, contradicting the base's 6
 per-deed worker rows. The stale base plot must be DROPPED, not preserved -- and the
 stale worker row must not be carried forward either."""
    base = {
        "plots": [_real_plot(6, retrieved=True)],
        "workers": list(REAL_WORKERS),
        "cards": [], "bonus_assets": [], "unassigned_items": {}, "coverage": {},
    }
    incoming = {
        "plots": [_real_plot(5, retrieved=False)],
        "workers": [], "cards": [], "bonus_assets": [], "unassigned_items": {}, "coverage": {},
    }
    merged = capture_mod.merge_reports(base, incoming)
    plot = next(p for p in merged["plots"] if p["deed_uid"] == REAL_DEED_UID)
    assert plot["asset_fetch"]["status"] == "dropped_stale"
    assert "bulk worker_count 5 contradicts 6" in plot["asset_fetch"]["reason"]
    assert not any(str(w).startswith("STALE DATA") for w in merged["warnings"])  # diagnostic, not a bar
    assert any(d.get("code") == "worker_detail_dropped_stale" for d in merged["diagnostics"])
    assert merged["coverage"]["assets"]["dropped"] == 1
    names = {w["name"] for w in merged["workers"]}
    assert "Lyanna Natura" not in names
    assert names == set()  # nothing carried forward for this deed at all


def test_quick_refresh_still_preserves_enriched_base_when_uncontradicted():
    """The existing 'never regress' intent must survive: a quick refresh that merely
 LACKS asset detail (bulk worker_count agrees with the base's own worker rows -- no
 mutation happened) still preserves the base's retrieved plot and its full worker
 list, exactly as before this fix."""
    base = {
        "plots": [_real_plot(6, retrieved=True)],
        "workers": list(REAL_WORKERS),
        "cards": [], "bonus_assets": [], "unassigned_items": {}, "coverage": {},
    }
    incoming = {
        "plots": [_real_plot(6, retrieved=False)],  # bulk worker_count still 6 -- agrees
        "workers": [], "cards": [], "bonus_assets": [], "unassigned_items": {}, "coverage": {},
    }
    merged = capture_mod.merge_reports(base, incoming)
    plot = next(p for p in merged["plots"] if p["deed_uid"] == REAL_DEED_UID)
    assert plot["asset_fetch"]["status"] == "retrieved"  # base plot preserved
    names = {w["name"] for w in merged["workers"]}
    assert "Lyanna Natura" in names
    assert len(merged["workers"]) == 6


# 3. The 500-on-the-happy-path bug: op-shape contract mismatch between
# `_expected_ops`'s Keychain pair-form requirement and `check_domain`'s body-form
# expectation. Regression test drives the REAL verification stack.

def _real_unstake_operation(deed_uid: str, card_uid: str) -> list:
    """A genuine Keychain pair-form `custom_json` operation -- `["custom_json", {...}]`,
 the "json" field itself JSON-encoded -- matching exactly what `fides_land.ops.to_ops`/
 `intents_to_ops` and `fides.op.requests_for_broadcast` actually produce, and what the
 real Keychain browser extension actually hands back to `verifyBroadcast`. Building this
 by hand (rather than monkeypatching `LandVerificationSpec`) is the point: it is the
 only way to exercise `_expected_ops`'s pair-form requirement AND `check_domain`'s
 body-form requirement against the same object, which is exactly where the 500 lived."""
    payload = {
        "unstake": {"cards": [{"card_uid": card_uid}], "items": []},
        "stake": {"cards": [], "items": []},
        "deed_uid": deed_uid,
        "auto_buy_grain": False,
        "app": "praetoria-fides/0.1.0",
        "n": "test-nonce",
    }
    return ["custom_json", {
        "id": "sm_stake_change",
        "json": json_mod.dumps(payload),
        "required_auths": [],
        "required_posting_auths": ["player0005"],
    }]


def _land_assets_response(remaining_card_uids: list[str]) -> dict:
    """A real-shaped `/land/stake/deeds/{uid}/assets` success envelope -- the exact shape
 `interpret_assets_response` (`fides_land/verify.py`) requires: `status: "success"`
 wrapping `data: {"cards": [...]}`."""
    return {"status": "success", "data": {"cards": [{"uid": uid} for uid in remaining_card_uids], "items": []}}


def _real_chain_tx(operation: list) -> dict:
    """A Hive `get_transaction` response shape containing exactly the broadcast op."""
    return {"operations": [operation], "block_num": 100}


def _real_chain_rpc(operation: list, method: str) -> dict:
    if method == "condenser_api.get_dynamic_global_properties":
        return {"head_block_number": 120}
    return _real_chain_tx(operation)


def test_domain_confirmed_reachable_with_the_real_verification_stack(monkeypatch, tmp_path):
    """THE regression test for the 500. Real deed/worker/card_uid (see module docstring).
 Nothing about `verify_chain` or `LandVerificationSpec.check_domain` is monkeypatched --
 only the two actual network boundaries (the Hive RPC transport, and
 `MnemosyneEvidence.assets`) are. This is precisely what the earlier tests in this
 file did NOT do, and precisely how the shape mismatch shipped without a failing test."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    config.ensure_dirs()
    unstaked_uid = "G-TESTCARD01"  # Lyanna Natura, real uid from the player0005 snapshot
    operation = _real_unstake_operation(REAL_DEED_UID, unstaked_uid)

    stale_report = {
        "generated_at": "2026-08-24T00:00:00Z",
        "plots": [_real_plot(6, retrieved=True)],
        "workers": list(REAL_WORKERS),
        "cards": [], "bonus_assets": [], "unassigned_items": {}, "coverage": {},
    }
    capture_mod._save_snapshot_and_refresh_cache(stale_report, capture_mod._latest_path(config, "player0005"))

    monkeypatch.setattr(fides_verify, "_http_rpc_call", lambda node, method, params: _real_chain_tx(operation))
    monkeypatch.setattr(
        capture_mod.MnemosyneEvidence, "assets",
        lambda self, deed_uid: ResponseData(_land_assets_response([w["uid"] for w in REAL_WORKERS if w["name"] != "Lyanna Natura"]), "2026-09-05T00:00:00Z", 1),
    )

    def fake_expand_deeds(self, deed_uids, player, **kwargs):
        assert set(kwargs) == {"evidence", "include_collection"}
        assert kwargs["evidence"].account == player
        assert kwargs["evidence"].capture is self
        assert kwargs["include_collection"] is False
        return {
            "generated_at": "2026-08-24T00:05:00Z",
            "plots": [_real_plot(5, retrieved=True)],
            "workers": [w for w in REAL_WORKERS if w["name"] != "Lyanna Natura"],
            "cards": [], "bonus_assets": [], "unassigned_items": {},
        }
    monkeypatch.setattr(capture_mod.LandCapture, "expand_deeds", fake_expand_deeds)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))
    body = {"tx_id": "real-shape-tx", "operations": [operation]}

    response = op_verify(body, request, player="player0005")  # must not raise / 500
    content = json_mod.loads(response.body)
    assert content["state"] == "domain-confirmed"
    assert content["refreshed_deeds"] == [REAL_DEED_UID]


def test_domain_check_survives_a_real_chain_lookup_failure_and_still_confirms(monkeypatch, tmp_path):
    """A real chain transport failure must not prevent authoritative domain confirmation."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    config.ensure_dirs()
    unstaked_uid = "G-TESTCARD01"
    operation = _real_unstake_operation(REAL_DEED_UID, unstaked_uid)

    stale_report = {
        "generated_at": "2026-08-24T00:00:00Z",
        "plots": [_real_plot(6, retrieved=True)],
        "workers": list(REAL_WORKERS),
        "cards": [], "bonus_assets": [], "unassigned_items": {}, "coverage": {},
    }
    capture_mod._save_snapshot_and_refresh_cache(stale_report, capture_mod._latest_path(config, "player0005"))

    # The chain lookup fails at the transport seam, reported as `lookup_failed` rather than
    # evidence that the transaction is absent.
    call_count = {"n": 0}

    def flaky_transport(node, method, params):
        call_count["n"] += 1
        raise OSError("upstream down")
    monkeypatch.setattr(fides_verify, "_http_rpc_call", flaky_transport)
    monkeypatch.setattr(
        capture_mod.MnemosyneEvidence, "assets",
        lambda self, deed_uid: ResponseData(_land_assets_response([w["uid"] for w in REAL_WORKERS if w["name"] != "Lyanna Natura"]), "2026-09-05T00:00:00Z", 1),
    )

    def fake_expand_deeds(self, deed_uids, player, **kwargs):
        assert set(kwargs) == {"evidence", "include_collection"}
        assert kwargs["evidence"].account == player
        assert kwargs["evidence"].capture is self
        assert kwargs["include_collection"] is False
        return {
            "generated_at": "2026-08-24T00:05:00Z",
            "plots": [_real_plot(5, retrieved=True)],
            "workers": [w for w in REAL_WORKERS if w["name"] != "Lyanna Natura"],
            "cards": [], "bonus_assets": [], "unassigned_items": {},
        }
    monkeypatch.setattr(capture_mod.LandCapture, "expand_deeds", fake_expand_deeds)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))
    body = {"tx_id": "flaky-chain-tx", "operations": [operation]}

    response = op_verify(body, request, player="player0005")
    content = json_mod.loads(response.body)
    # Current master performs one direct chain lookup; the domain check still runs after its
    # transport failure and remains authoritative.
    assert call_count["n"] == 1
    assert content["chain"]["status"] == "chain_absent"
    assert content["chain"]["evidence"]["reason_kind"] == "lookup_failed"
    # ...but the domain re-read is authoritative and confirms on its own.
    assert content["state"] == "domain-confirmed"
    assert content["refreshed_deeds"] == [REAL_DEED_UID]


def test_chain_confirmed_unpropagated_unstake_is_terminal_not_applied(monkeypatch, tmp_path):
    """Mutation evidence: leave the pending-to-terminal family guard blanket across all ops;
 expected red: the chain-confirmed stake/unstake remains `domain-pending` and no refresh runs."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    config.ensure_dirs()
    unstaked_uid = "G-TESTCARD01"
    operation = _real_unstake_operation(REAL_DEED_UID, unstaked_uid)
    monkeypatch.setattr(fides_verify, "_http_rpc_call", lambda node, method, params: _real_chain_rpc(operation, method))
    # Domain re-read shows the card STILL present -- the unstake never actually applied.
    monkeypatch.setattr(
        capture_mod.MnemosyneEvidence, "assets",
        lambda self, deed_uid: ResponseData(_land_assets_response([w["uid"] for w in REAL_WORKERS]), "2026-09-05T00:00:00Z", 1),
    )
    expand_calls: list[tuple[str, bool, str]] = []
    monkeypatch.setattr(capture_mod, "expand_plots", lambda cfg, player, deed_uids, **kwargs: expand_calls.append((deed_uids[0], kwargs["include_collection"], kwargs["evidence"].account)) or {"capture": {"refreshed_deeds": list(deed_uids), "refresh_errors": []}})

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))
    body = {"tx_id": "mismatch-tx", "operations": [operation], "verification_attempt": 2}
    response = op_verify(body, request, player="player0005")
    content = json_mod.loads(response.body)
    assert content["state"] == "domain-not-applied"
    assert content["domain"]["status"] == "domain_pending"
    assert content["domain"]["evidence"]["inclusion_block"] == 100
    assert content["domain"]["evidence"]["head_block"] == 120
    assert content["domain"]["evidence"]["age_blocks"] == 20
    assert content["domain"]["evidence"]["age_seconds"] == 60
    assert content["domain"]["evidence"]["attempt"] == 2
    assert expand_calls == [(REAL_DEED_UID, False, "player0005")]
    assert content["refreshed_deeds"] == [REAL_DEED_UID]


def test_chain_mismatch_is_reported_by_verify_chain(monkeypatch):
    """Operation-equality mismatch detection remains on the direct fides verification seam.

 `/api/op-inclusion` is not equivalent: it reports only whether a block number exists and
 never compares the submitted operations.
    """
    from fides.op import Op

    op = Op(operation_id="sm_stake_change", required_auths=[], required_posting_auths=["player0005"], json_payload={"deed_uid": "I-1", "n": "abc"})
    # A real transaction whose actual op payload differs from what's expected.
    mismatched_tx = {"operations": [["custom_json", {
        "id": "sm_stake_change", "json": json_mod.dumps({"deed_uid": "I-1", "n": "different"}),
        "required_auths": [], "required_posting_auths": ["player0005"],
    }]]}
    call_count = {"n": 0}

    def transport(node, method, params):
        call_count["n"] += 1
        return mismatched_tx
    monkeypatch.setattr(fides_verify, "_http_rpc_call", transport)

    result = fides_verify.verify_chain([op], "mismatch-tx")
    assert result["status"] == "chain_absent"
    assert result["evidence"]["reason_kind"] == "mismatch"
    assert call_count["n"] == 1
