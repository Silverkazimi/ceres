import tempfile
import unittest
from pathlib import Path

from fides.ledger import Ledger, LedgerError, TERMINAL_FAILURE_STATES


class StubSpec:
    """A stub VerificationSpec -- deliberately generic, no domain vocabulary, per Phase 2's
 acceptance criteria ("never a land one")."""

    def __init__(self, domain_result="domain_verified"):
        self.domain_result = domain_result
        self.calls = []

    def check_domain(self, unit_id, ops):
        self.calls.append((unit_id, ops))
        return {"status": self.domain_result, "evidence": {"stub": True}}

    def gate_status(self, unit_id):
        return {"ready": True}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "ledger.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_happy_path_state_machine(self):
        self.ledger.append("wave-1", "pending")
        self.ledger.record_broadcast("wave-1", "tx123")
        self.assertEqual(self.ledger.current_state("wave-1"), "broadcast_claimed")
        self.ledger.record_chain_result("wave-1", {"status": "chain_verified", "evidence": {}})
        self.assertEqual(self.ledger.current_state("wave-1"), "chain_verified")
        spec = StubSpec("domain_verified")
        self.ledger.advance_with_domain_check("wave-1", spec, ["op1"])
        self.assertEqual(self.ledger.current_state("wave-1"), "domain_verified")
        self.assertTrue(self.ledger.is_domain_verified("wave-1"))
        self.assertEqual(spec.calls, [("wave-1", ["op1"])])

    def test_chain_absent_is_terminal(self):
        self.ledger.append("wave-1", "pending")
        self.ledger.record_broadcast("wave-1", "tx-bad")
        self.ledger.record_chain_result("wave-1", {"status": "chain_absent", "evidence": {"reason": "not found"}})
        self.assertEqual(self.ledger.current_state("wave-1"), "chain_absent")
        self.assertTrue(self.ledger.is_terminal_failure("wave-1"))
        with self.assertRaises(LedgerError):
            self.ledger.append("wave-1", "chain_verified")  # no transition out of a terminal state

    def test_domain_mismatch_is_terminal_and_chain_alone_never_reaches_it(self):
        self.ledger.append("wave-1", "pending")
        self.ledger.record_broadcast("wave-1", "tx123")
        self.ledger.record_chain_result("wave-1", {"status": "chain_verified", "evidence": {}})
        spec = StubSpec("domain_mismatch")
        self.ledger.advance_with_domain_check("wave-1", spec, ["op1"])
        self.assertEqual(self.ledger.current_state("wave-1"), "domain_mismatch")
        self.assertIn("domain_mismatch", TERMINAL_FAILURE_STATES)

    def test_domain_pending_is_retryable_and_not_a_failure(self):
        with self.assertRaises(LedgerError):
            self.ledger.append("wave-1", "domain_pending")
        self.ledger.append("wave-1", "pending")
        self.ledger.record_broadcast("wave-1", "tx123")
        self.ledger.record_chain_result("wave-1", {"status": "chain_verified", "evidence": {}})
        spec = StubSpec("domain_pending")
        self.ledger.advance_with_domain_check("wave-1", spec, ["op1"])
        self.assertEqual(self.ledger.current_state("wave-1"), "domain_pending")
        spec.domain_result = "domain_verified"
        self.ledger.advance_with_domain_check("wave-1", spec, ["op1"])
        self.assertTrue(self.ledger.is_domain_verified("wave-1"))

    def test_domain_check_refuses_to_run_before_chain_verified(self):
        self.ledger.append("wave-1", "pending")
        spec = StubSpec("domain_verified")
        with self.assertRaises(LedgerError):
            self.ledger.advance_with_domain_check("wave-1", spec, [])

    def test_chain_verified_never_recorded_as_domain_verified_by_itself(self):
        # The core's central safety property: chain evidence alone must never appear as
        # domain_verified in the ledger -- only VerificationSpec.check_domain can produce it.
        self.ledger.append("wave-1", "pending")
        self.ledger.record_broadcast("wave-1", "tx123")
        self.ledger.record_chain_result("wave-1", {"status": "chain_verified", "evidence": {}})
        self.assertNotEqual(self.ledger.current_state("wave-1"), "domain_verified")
        with self.assertRaises(LedgerError):
            self.ledger.append("wave-1", "domain_verified")  # cannot be forced without going through check_domain

    def test_illegal_transition_rejected(self):
        self.ledger.append("wave-1", "pending")
        with self.assertRaises(LedgerError):
            self.ledger.append("wave-1", "domain_verified")

    def test_first_entry_must_be_pending(self):
        with self.assertRaises(LedgerError):
            self.ledger.append("wave-1", "broadcast_claimed")

    def test_unknown_state_rejected(self):
        with self.assertRaises(LedgerError):
            self.ledger.append("wave-1", "not_a_real_state")

    def test_ledger_is_append_only_and_resumable_by_replay(self):
        self.ledger.append("wave-1", "pending")
        self.ledger.record_broadcast("wave-1", "tx123")
        raw = self.ledger.path.read_text(encoding="utf-8")
        self.assertEqual(len(raw.splitlines()), 2)  # one JSON object per transition
        # a fresh Ledger instance over the same path reconstructs identical state by replay
        reopened = Ledger(self.ledger.path)
        self.assertEqual(reopened.current_state("wave-1"), "broadcast_claimed")

    def test_multiple_units_tracked_independently(self):
        self.ledger.append("wave-1", "pending")
        self.ledger.append("wave-2", "pending")
        self.ledger.record_broadcast("wave-1", "tx1")
        self.assertEqual(self.ledger.current_state("wave-1"), "broadcast_claimed")
        self.assertEqual(self.ledger.current_state("wave-2"), "pending")
        self.assertEqual(self.ledger.unit_ids(), ["wave-1", "wave-2"])


if __name__ == "__main__":
    unittest.main()
