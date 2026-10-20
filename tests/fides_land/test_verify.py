import json
import unittest
from datetime import datetime, timedelta, timezone

from fides.ledger import LedgerEntry
from fides_land.verify import LandDomainVerificationError, LandVerificationSpec, interpret_assets_response, wave1_ready_at


def stake_op(deed_uid, card_uid, slot="1"):
    import json
    payload = {"unstake": {"cards": [], "items": []}, "stake": {"cards": [{"card_uid": card_uid, "stake_type_uid": "STK-LND-WKR", "slot": slot}], "items": []}, "deed_uid": deed_uid, "auto_buy_grain": False, "app": "x", "n": "n"}
    return {"id": "sm_stake_change", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": ["player0005"]}


def unstake_op(deed_uid, card_uid):
    import json
    payload = {"unstake": {"cards": [{"card_uid": card_uid}], "items": []}, "stake": {"cards": [], "items": []}, "deed_uid": deed_uid, "auto_buy_grain": False, "app": "x", "n": "n"}
    return {"id": "sm_stake_change", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": ["player0005"]}


def item_unstake_op(deed_uid, item_uid):
    import json
    payload = {"unstake": {"cards": [], "items": [{"item_uid": item_uid}]}, "stake": {"cards": [], "items": []}, "deed_uid": deed_uid, "auto_buy_grain": False, "app": "x", "n": "n"}
    return {"id": "sm_stake_change", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": ["player0005"]}


def item_stake_op(deed_uid, item_uid):
    import json
    payload = {"unstake": {"cards": [], "items": []}, "stake": {"cards": [], "items": [{"item_uid": item_uid, "stake_type_uid": "STK-LND-PCR"}]}, "deed_uid": deed_uid, "auto_buy_grain": False, "app": "x", "n": "n"}
    return {"id": "sm_stake_change", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": ["player0005"]}


CAPTURED_POWER_CORE_ITEM = {
    "uid": "5F2E1B10B9262CE239",
    "item_detail_id": 322,
    "player": "player0005",
    "stake_start_date": "2026-07-20T20:49:03.000Z",
    "stake_type_uid": "STK-LND-PCR",
    "stake_ref_uid": "I-294-8bb3ce6436e586",
    "stake_ref_id": None,
    "name": "Power Core",
    "boost": "0.000",
}


def captured_assets_response_with_item():
    return {"status": "success", "data": {"cards": [], "items": [dict(CAPTURED_POWER_CORE_ITEM)]}}


_MISSING = object()
DEGRADED_ASSET_SHAPES = (
    ("absent", _MISSING),
    ("none", None),
    ("dict", {"uid": "not-a-list"}),
    ("string", "not-a-list"),
    ("number", 42),
    ("error object", {"error": "P0001"}),
)


def assets_response_with_shape(field, shape):
    data = {"cards": [], "items": []}
    if shape is _MISSING:
        del data[field]
    else:
        data[field] = shape
    return {"status": "success", "data": data}


def worksite_change_op_dict(deed_uid, target, region_uid="PR-BRL-41"):
    """The real captured shape (a captured op):
 {"op":"worksite_aura_construction","region_uid":"PR-BRL-41",
 "deed_uids":["I-298-2dd53d9ac60331"],"time_crystals":0,"app":"...","n":"..."}"""
    import json
    payload = {"op": f"worksite_{target}_construction", "region_uid": region_uid, "deed_uids": [deed_uid], "time_crystals": 0, "app": "x", "n": "n"}
    return {"id": "sm_land_operation", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": ["player0005"]}


def worksite_feed_op_dict(deed_uid, project_id=397948, region_uid="PR-BRL-41"):
    payload = {"op": "update_worksite", "region_uid": region_uid, "deed_uids": [deed_uid], "project_id": project_id, "time_crystals": 0, "auto_buy_grain": False, "app": "x", "n": "n"}
    return {"id": "sm_land_operation", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": ["player0005"]}


class InterpretAssetsResponseTests(unittest.TestCase):
    def test_valid_response(self):
        resp = {"status": "success", "data": {"cards": [{"uid": "U1", "slot": "1"}], "items": []}}
        out = interpret_assets_response(resp)
        self.assertEqual(out["present_card_uids"], ["U1"])
        self.assertEqual(out["present_item_uids"], [])

    def test_empty_lists_are_valid_empty_asset_evidence(self):
        out = interpret_assets_response({"status": "success", "data": {"cards": [], "items": []}})
        self.assertEqual(out["present_card_uids"], [])
        self.assertEqual(out["present_item_uids"], [])

    def test_p0001_error_shape_rejected_not_treated_as_absent(self):
        # the internal rules durable fact: HTTP 200 status:"success" can still wrap a P0001 error
        # accept only when data contains both list-valued asset fields.
        resp = {"status": "success", "data": {"message": "P0001: no candidates", "detail": None}}
        with self.assertRaises(LandDomainVerificationError):
            interpret_assets_response(resp)

    def test_items_error_object_is_rejected_like_cards_error_object(self):
        with self.assertRaises(LandDomainVerificationError):
            interpret_assets_response(assets_response_with_shape("items", {"error": "P0001"}))

    def test_non_success_status_rejected(self):
        with self.assertRaises(LandDomainVerificationError):
            interpret_assets_response({"status": "error", "data": {}})


class LandVerificationSpecTests(unittest.TestCase):
    def test_check_domain_verified_when_expected_state_observed(self):
        def fetch(deed_uid):
            return {"status": "success", "data": {"cards": [{"uid": "U1", "slot": "1"}], "items": []}}
        spec = LandVerificationSpec(fetch)
        result = spec.check_domain("wave-1", [stake_op("deed-1", "U1")])
        self.assertEqual(result["status"], "domain_verified")

    def test_check_domain_stake_not_observed_is_pending(self):
        def fetch(deed_uid):
            return {"status": "success", "data": {"cards": [], "items": []}}
        spec = LandVerificationSpec(fetch)
        result = spec.check_domain("wave-1", [stake_op("deed-1", "U1")])
        self.assertEqual(result["status"], "domain_pending")
        self.assertIn("U1", result["evidence"]["pending"][0])

    def test_pending_evidence_exposes_escalation_attempt(self):
        """Injected defect: removed pending_metadata from the pending response; this test went red before the fix was restored."""
        spec = LandVerificationSpec(
            lambda deed_uid: {"status": "success", "data": {"cards": [], "items": []}}
        )
        result = spec.check_domain("wave-1", [stake_op("deed-1", "U1")], verification_attempt=5)

        self.assertEqual(
            set(result["evidence"]["pending_metadata"]),
            {"attempt", "escalate_after_attempts", "requires_escalation"},
        )
        self.assertEqual(result["evidence"]["pending_metadata"]["attempt"], 5)
        self.assertTrue(result["evidence"]["pending_metadata"]["requires_escalation"])
        self.assertEqual(result["status"], "domain_pending")

    def test_check_domain_unstake_target_still_present_is_pending(self):
        def fetch(deed_uid):
            return {"status": "success", "data": {"cards": [{"uid": "U1", "slot": "1"}], "items": []}}
        spec = LandVerificationSpec(fetch)
        result = spec.check_domain("wave-1", [unstake_op("deed-1", "U1")])
        self.assertEqual(result["status"], "domain_pending")
        self.assertIn("U1", result["evidence"]["pending"][0])

    def test_check_domain_item_unstake_target_still_present_is_pending(self):
        """Injection: replace verify.py:46 with present_items = {}; this must go red."""
        def fetch(deed_uid):
            return captured_assets_response_with_item()
        spec = LandVerificationSpec(fetch)
        result = spec.check_domain("wave-1", [item_unstake_op("deed-1", CAPTURED_POWER_CORE_ITEM["uid"])])
        self.assertEqual(result["status"], "domain_pending")
        self.assertIn(CAPTURED_POWER_CORE_ITEM["uid"], result["evidence"]["pending"][0])

    def test_check_domain_item_stake_target_still_present_is_verified(self):
        """Injection: replace verify.py:46 with present_items = {}; this must go red in the opposite direction."""
        def fetch(deed_uid):
            return captured_assets_response_with_item()
        spec = LandVerificationSpec(fetch)
        result = spec.check_domain("wave-1", [item_stake_op("deed-1", CAPTURED_POWER_CORE_ITEM["uid"])])
        self.assertEqual(result["status"], "domain_verified")

    def test_check_domain_treats_upstream_error_as_unverifiable_not_silent_pass(self):
        def fetch(deed_uid):
            return {"status": "success", "data": {"message": "P0001"}}
        spec = LandVerificationSpec(fetch)
        result = spec.check_domain("wave-1", [stake_op("deed-1", "U1")])
        self.assertEqual(result["status"], "domain_could_not_verify")
        self.assertIn("P0001", result["evidence"]["unverifiable"][0])

    def test_g1_item_error_object_is_could_not_verify(self):
        response = assets_response_with_shape("items", {"error": "P0001"})
        result = LandVerificationSpec(lambda deed_uid: response).check_domain("wave-1", [item_unstake_op("deed-1", "I1")])
        self.assertEqual(result["status"], "domain_could_not_verify")

    def test_g2_card_error_object_is_still_could_not_verify(self):
        response = assets_response_with_shape("cards", {"error": "P0001"})
        result = LandVerificationSpec(lambda deed_uid: response).check_domain("wave-1", [unstake_op("deed-1", "U1")])
        self.assertEqual(result["status"], "domain_could_not_verify")

    def test_g3_successful_item_unstake_with_empty_items_is_verified(self):
        response = {"status": "success", "data": {"cards": [], "items": []}}
        result = LandVerificationSpec(lambda deed_uid: response).check_domain("wave-1", [item_unstake_op("deed-1", "I1")])
        self.assertEqual(result["status"], "domain_verified")

    def test_g4_cards_and_items_agree_for_every_degraded_shape(self):
        for label, shape in DEGRADED_ASSET_SHAPES:
            with self.subTest(shape=label):
                card_result = LandVerificationSpec(
                    lambda deed_uid, shape=shape: assets_response_with_shape("cards", shape)
                ).check_domain("wave-1", [unstake_op("deed-1", "U1")])
                item_result = LandVerificationSpec(
                    lambda deed_uid, shape=shape: assets_response_with_shape("items", shape)
                ).check_domain("wave-1", [item_unstake_op("deed-1", "I1")])
                self.assertEqual(card_result["status"], "domain_could_not_verify")
                self.assertEqual(item_result["status"], card_result["status"])

    def test_check_domain_malformed_card_op_remains_a_mismatch(self):
        payload = {"stake": {"cards": [{"card_uid": "U1"}]}}
        import json
        op = {"id": "sm_stake_change", "json": json.dumps(payload)}
        result = LandVerificationSpec(lambda deed_uid: {}).check_domain("wave-1", [op])
        self.assertEqual(result["status"], "domain_mismatch")
        self.assertIn("no deed_uid", result["evidence"]["mismatches"][0])

    # Phase 4: worksite-change domain verification

    def test_worksite_change_domain_verified_when_project_type_matches_op_name(self):
        # the internal rules's durable fact: `worksite_facts.project_type` reads back the
        # IDENTICAL string to the chain op name while a change is in flight.
        def fetch_worksite(deed_uid):
            return {"project_type": "worksite_aura_construction"}
        spec = LandVerificationSpec(lambda d: {}, fetch_worksite_facts=fetch_worksite)
        result = spec.check_domain("wave-1", [worksite_change_op_dict("deed-1", "aura")])
        self.assertEqual(result["status"], "domain_verified")
        self.assertEqual(result["evidence"]["deeds"]["deed-1"]["project_type"], "worksite_aura_construction")

    def test_worksite_change_domain_pending_when_project_type_unchanged(self):
        # A stale read moments after broadcast is propagation lag, not proof of
        # failure -- must come back PENDING, never asserted as a mismatch
        # (that conflation was IR/FR's whole reason for this state split).
        def fetch_worksite(deed_uid):
            return {"project_type": "worksite_grain"}
        spec = LandVerificationSpec(lambda d: {}, fetch_worksite_facts=fetch_worksite)
        result = spec.check_domain("wave-1", [worksite_change_op_dict("deed-1", "aura")])
        self.assertEqual(result["status"], "domain_pending")

    def test_worksite_feed_domain_verified_when_construction_is_settled(self):
        def fetch_worksite(deed_uid):
            return {"project_type": "worksite_stone", "project_id": 397948, "is_construction": False}
        spec = LandVerificationSpec(lambda d: {}, fetch_worksite_facts=fetch_worksite)
        result = spec.check_domain("wave-1", [worksite_feed_op_dict("deed-1")])
        self.assertEqual(result["status"], "domain_verified")

    def test_worksite_feed_domain_pending_while_construction_remains(self):
        def fetch_worksite(deed_uid):
            return {"project_type": "worksite_stone_construction", "project_id": 397948, "is_construction": True}
        spec = LandVerificationSpec(lambda d: {}, fetch_worksite_facts=fetch_worksite)
        result = spec.check_domain("wave-1", [worksite_feed_op_dict("deed-1")])
        self.assertEqual(result["status"], "domain_pending")
        self.assertIn("deed-1", result["evidence"]["pending"][0])

    def test_worksite_change_could_not_verify_when_worksite_facts_missing(self):
        # `fetch_worksite_facts` returning None means no evidence was obtainable at
        # all -- genuinely unknown, never success, never a mismatch.
        def fetch_worksite(deed_uid):
            return None
        spec = LandVerificationSpec(lambda d: {}, fetch_worksite_facts=fetch_worksite)
        result = spec.check_domain("wave-1", [worksite_change_op_dict("deed-1", "aura")])
        self.assertEqual(result["status"], "domain_could_not_verify")
        self.assertIn("deed-1", result["evidence"]["unverifiable"][0])

    def test_worksite_change_never_confirmed_on_a_transaction_id_alone(self):
        # No fetcher configured at all -- must refuse to confirm rather than
        # silently passing (H3: a custom_json landing on-chain proves nothing).
        # Structurally this is "we cannot check", not "it disagrees".
        spec = LandVerificationSpec(lambda d: {})
        result = spec.check_domain("wave-1", [worksite_change_op_dict("deed-1", "aura")])
        self.assertEqual(result["status"], "domain_could_not_verify")
        self.assertIn("no worksite_facts fetcher", result["evidence"]["unverifiable"][0])

    def test_worksite_change_fetch_failure_is_could_not_verify_not_a_silent_pass(self):
        def fetch_worksite(deed_uid):
            raise RuntimeError("network flake")
        spec = LandVerificationSpec(lambda d: {}, fetch_worksite_facts=fetch_worksite)
        result = spec.check_domain("wave-1", [worksite_change_op_dict("deed-1", "aura")])
        self.assertEqual(result["status"], "domain_could_not_verify")
        self.assertIn("network flake", result["evidence"]["unverifiable"][0])

    def test_worksite_change_malformed_op_with_no_deed_uids_is_a_genuine_mismatch(self):
        # This is the one worksite-change case that stays a real mismatch -- a
        # structurally broken payload is a defect, not a timing question.
        import json
        payload = {"op": "worksite_aura_construction", "region_uid": "PR-BRL-41", "deed_uids": [], "time_crystals": 0, "app": "x", "n": "n"}
        op = {"id": "sm_land_operation", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": ["player0005"]}
        spec = LandVerificationSpec(lambda d: {}, fetch_worksite_facts=lambda d: {"project_type": "worksite_aura_construction"})
        result = spec.check_domain("wave-1", [op])
        self.assertEqual(result["status"], "domain_mismatch")
        self.assertIn("no deed_uids", result["evidence"]["mismatches"][0])

    def test_worksite_change_checks_every_deed_in_deed_uids(self):
        # `deed_uids` is an array (the plan's own multi-plot-batching room)
        # every entry must be independently re-read, not just the first.
        calls = []

        def fetch_worksite(deed_uid):
            calls.append(deed_uid)
            return {"project_type": "worksite_aura_construction" if deed_uid == "deed-1" else "worksite_grain"}

        import json
        payload = {"op": "worksite_aura_construction", "region_uid": "PR-BRL-41", "deed_uids": ["deed-1", "deed-2"], "time_crystals": 0, "app": "x", "n": "n"}
        op = {"id": "sm_land_operation", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": ["player0005"]}
        spec = LandVerificationSpec(lambda d: {}, fetch_worksite_facts=fetch_worksite)
        result = spec.check_domain("wave-1", [op])
        self.assertEqual(sorted(calls), ["deed-1", "deed-2"])
        self.assertEqual(result["status"], "domain_pending")  # deed-2 still unchanged -- stale, not wrong
        self.assertIn("deed-2", result["evidence"]["pending"][0])

    def test_worksite_change_mixed_pending_and_unverifiable_prefers_unverifiable(self):
        # Priority ordering: mismatches > unverifiable > pending. One deed unreadable
        # and another merely stale must not be reported as the (weaker) pending state
        # "we don't know" outranks "probably still propagating".
        def fetch_worksite(deed_uid):
            if deed_uid == "deed-1":
                return {"project_type": "worksite_grain"}  # stale/pending
            return None  # unreadable

        import json
        payload = {"op": "worksite_aura_construction", "region_uid": "PR-BRL-41", "deed_uids": ["deed-1", "deed-2"], "time_crystals": 0, "app": "x", "n": "n"}
        op = {"id": "sm_land_operation", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": ["player0005"]}
        spec = LandVerificationSpec(lambda d: {}, fetch_worksite_facts=fetch_worksite)
        result = spec.check_domain("wave-1", [op])
        self.assertEqual(result["status"], "domain_could_not_verify")

    def test_card_move_ops_still_verify_unaffected_by_worksite_dispatch(self):
        # The new dispatch-on-op-name branch must not disturb the existing
        # stake/unstake verification path for ops that carry no `op` key at all.
        def fetch(deed_uid):
            return {"status": "success", "data": {"cards": [{"uid": "U1", "slot": "1"}], "items": []}}
        spec = LandVerificationSpec(fetch)
        result = spec.check_domain("wave-1", [stake_op("deed-1", "U1")])
        self.assertEqual(result["status"], "domain_verified")

    def test_gate_status_ready_when_no_entry(self):
        spec = LandVerificationSpec(lambda d: {}, ready_at_by_unit={})
        self.assertEqual(spec.gate_status("wave-3")["ready"], True)

    def test_gate_status_blocked_when_gate_declared_but_ready_at_unknown(self):
        # unit_id present with an explicit None -- a gate applies but isn't computable yet
        # (e.g. wave 1 hasn't chain-confirmed). Must never be treated as "ready".
        spec = LandVerificationSpec(lambda d: {}, ready_at_by_unit={"wave-3": None})
        status = spec.gate_status("wave-3")
        self.assertFalse(status["ready"])

    def test_gate_status_blocked_before_ready_at(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
        spec = LandVerificationSpec(lambda d: {}, ready_at_by_unit={"wave-3": future})
        status = spec.gate_status("wave-3")
        self.assertFalse(status["ready"])
        self.assertEqual(status["ready_at"], future)

    def test_gate_status_ready_after_ready_at(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        spec = LandVerificationSpec(lambda d: {}, ready_at_by_unit={"wave-3": past})
        self.assertTrue(spec.gate_status("wave-3")["ready"])


class Wave1ReadyAtTests(unittest.TestCase):
    def test_prefers_chain_timestamp_over_local_clock(self):
        chain_ts = "2026-08-19T00:00:00Z"
        entries = [
            LedgerEntry(ts="2026-08-19T05:00:00Z", unit_id="wave-1", state="broadcast_claimed", detail={"tx_id": "t1"}),
            LedgerEntry(ts="2026-08-19T05:01:00Z", unit_id="wave-1", state="chain_verified", detail={"evidence": {"chain_timestamp": chain_ts}}),
        ]
        ready_at, assumed = wave1_ready_at(entries, "wave-1", cooldown_hours=72, margin_hours=6)
        self.assertFalse(assumed)
        self.assertTrue(ready_at.startswith("2026-08-22"))

    def test_falls_back_to_local_clock_when_no_chain_timestamp_and_flags_it(self):
        entries = [LedgerEntry(ts="2026-08-19T05:00:00Z", unit_id="wave-1", state="broadcast_claimed", detail={"tx_id": "t1"})]
        ready_at, assumed = wave1_ready_at(entries, "wave-1", cooldown_hours=72, margin_hours=6)
        self.assertTrue(assumed)
        self.assertIsNotNone(ready_at)

    def test_returns_none_when_no_entries_at_all(self):
        ready_at, assumed = wave1_ready_at([], "wave-1", cooldown_hours=72, margin_hours=6)
        self.assertIsNone(ready_at)
        self.assertTrue(assumed)


if __name__ == "__main__":
    unittest.main()
