import unittest

from fides.op import Op
from fides.verify import get_transaction, ops_match, verify_chain


def fake_transport_factory(result):
    def transport(node, method, params):
        return result
    return transport


class VerifyChainTests(unittest.TestCase):
    def setUp(self):
        self.op = Op(operation_id="sm_stake_change", required_auths=[], required_posting_auths=["player0005"], json_payload={"deed_uid": "I-1", "n": "abc"})

    def test_get_transaction_reports_not_found_on_genuine_rpc_not_found(self):
        """A node error that specifically says the transaction is unknown is affirmative."""
        def failing_transport(node, method, params):
            raise RuntimeError("Unable to find transaction")
        tx, reason_kind = get_transaction("tx1", transport=failing_transport)
        self.assertIsNone(tx)
        self.assertEqual(reason_kind, "not_found")

    def test_get_transaction_reports_lookup_failed_on_missing_rpc_method(self):
        def failing_transport(node, method, params):
            raise RuntimeError("Could not find method get_transaction")
        tx, reason_kind = get_transaction("tx1", transport=failing_transport)
        self.assertIsNone(tx)
        self.assertEqual(reason_kind, "lookup_failed")

    def test_get_transaction_reports_lookup_failed_on_rpc_rate_limit(self):
        def failing_transport(node, method, params):
            raise RuntimeError("Rate limit exceeded")
        tx, reason_kind = get_transaction("tx1", transport=failing_transport)
        self.assertIsNone(tx)
        self.assertEqual(reason_kind, "lookup_failed")

    def test_get_transaction_reports_lookup_failed_on_transport_error(self):
        """A transport-level failure (network blip, timeout, malformed response) is NOT
        evidence the transaction is absent -- a real 2026-08-24 live-broadcast case showed
        `verifyBroadcast` can be called before the tx has even reached a block, and this
        must never be collapsed into the same 'not found' bucket as an affirmative RPC
        not-found response."""
        def failing_transport(node, method, params):
            raise OSError("upstream down")
        tx, reason_kind = get_transaction("tx1", transport=failing_transport)
        self.assertIsNone(tx)
        self.assertEqual(reason_kind, "lookup_failed")

    def test_ops_match_true_for_identical_parsed_payloads_different_key_order(self):
        observed = [{"id": "sm_stake_change", "json": '{"n":"abc","deed_uid":"I-1"}', "required_auths": [], "required_posting_auths": ["player0005"]}]
        self.assertTrue(ops_match([self.op], observed))

    def test_ops_match_false_on_payload_divergence(self):
        observed = [{"id": "sm_stake_change", "json": '{"n":"abc","deed_uid":"I-2"}', "required_auths": [], "required_posting_auths": ["player0005"]}]
        self.assertFalse(ops_match([self.op], observed))

    def test_ops_match_false_on_count_mismatch(self):
        self.assertFalse(ops_match([self.op, self.op], [{"id": "sm_stake_change", "json": "{}", "required_auths": [], "required_posting_auths": []}]))

    def test_ops_match_order_independent(self):
        op2 = Op(operation_id="sm_stake_change", required_auths=[], required_posting_auths=["player0005"], json_payload={"deed_uid": "I-2", "n": "xyz"})
        observed = [
            {"id": "sm_stake_change", "json": '{"deed_uid":"I-2","n":"xyz"}', "required_auths": [], "required_posting_auths": ["player0005"]},
            {"id": "sm_stake_change", "json": '{"deed_uid":"I-1","n":"abc"}', "required_auths": [], "required_posting_auths": ["player0005"]},
        ]
        self.assertTrue(ops_match([self.op, op2], observed))

    def test_verify_chain_returns_chain_verified_on_match(self):
        tx = {"operations": [["custom_json", {"id": "sm_stake_change", "json": '{"n":"abc","deed_uid":"I-1"}', "required_auths": [], "required_posting_auths": ["player0005"]}]]}
        result = verify_chain([self.op], "tx1", transport=fake_transport_factory(tx))
        self.assertEqual(result["status"], "chain_verified")

    def test_verify_chain_returns_chain_absent_when_tx_missing(self):
        result = verify_chain([self.op], "tx-does-not-exist", transport=fake_transport_factory(None))
        self.assertEqual(result["status"], "chain_absent")

    def test_verify_chain_returns_chain_absent_on_mismatch(self):
        tx = {"operations": [["custom_json", {"id": "sm_stake_change", "json": '{"n":"different","deed_uid":"I-9"}', "required_auths": [], "required_posting_auths": ["player0005"]}]]}
        result = verify_chain([self.op], "tx1", transport=fake_transport_factory(tx))
        self.assertEqual(result["status"], "chain_absent")
        self.assertIn("reason", result["evidence"])

    def test_verify_chain_ignores_non_custom_json_ops_in_the_tx(self):
        tx = {"operations": [
            ["vote", {"voter": "player0005"}],
            ["custom_json", {"id": "sm_stake_change", "json": '{"n":"abc","deed_uid":"I-1"}', "required_auths": [], "required_posting_auths": ["player0005"]}],
        ]}
        result = verify_chain([self.op], "tx1", transport=fake_transport_factory(tx))
        self.assertEqual(result["status"], "chain_verified")

    def test_verify_chain_handles_typed_operations_shape(self):
        # Some node responses use {"type": "custom_json_operation", "value": {...}} instead
        # of the legacy [name, body] tuple -- both must be handled.
        tx = {"operations": [{"type": "custom_json_operation", "value": {"id": "sm_stake_change", "json": '{"n":"abc","deed_uid":"I-1"}', "required_auths": [], "required_posting_auths": ["player0005"]}}]}
        result = verify_chain([self.op], "tx1", transport=fake_transport_factory(tx))
        self.assertEqual(result["status"], "chain_verified")


if __name__ == "__main__":
    unittest.main()
