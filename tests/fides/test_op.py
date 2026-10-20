import unittest

from fides.op import Op, chunk_ops, op_key_tier, required_account, required_key_tier, requests_for_broadcast


def make_op(n=1, account="player0005", posting=True, payload=None):
    return Op(
        operation_id="custom_json_op",
        required_auths=[] if posting else [account],
        required_posting_auths=[account] if posting else [],
        json_payload=payload or {"n": n, "pad": "x" * 200},
    )


class OpTests(unittest.TestCase):
    def test_as_custom_json_and_keychain_tuple_shape(self):
        op = Op(operation_id="sm_stake_change", required_auths=[], required_posting_auths=["player0005"], json_payload={"a": 1})
        cj = op.as_custom_json()
        self.assertEqual(cj["id"], "sm_stake_change")
        self.assertEqual(cj["required_posting_auths"], ["player0005"])
        self.assertEqual(cj["required_auths"], [])
        import json
        self.assertEqual(json.loads(cj["json"]), {"a": 1})
        tup = op.as_keychain_tuple()
        self.assertEqual(tup[0], "custom_json")
        self.assertEqual(tup[1]["id"], "sm_stake_change")

    def test_chunk_ops_respects_op_count_ceiling(self):
        ops = [make_op(i) for i in range(10)]
        chunks = chunk_ops(ops, max_ops_per_tx=4, max_bytes_per_tx=10_000_000)
        self.assertEqual([len(c) for c in chunks], [4, 4, 2])
        self.assertEqual(sum(len(c) for c in chunks), 10)

    def test_chunk_ops_respects_byte_ceiling(self):
        ops = [make_op(i) for i in range(10)]
        one_op_bytes = ops[0].serialized_bytes()
        # ceiling that fits exactly 3 ops per chunk
        ceiling = one_op_bytes * 3 + 10
        chunks = chunk_ops(ops, max_ops_per_tx=1000, max_bytes_per_tx=ceiling)
        for chunk in chunks:
            total = 2 + sum(op.serialized_bytes() for op in chunk) + max(0, len(chunk) - 1)
            self.assertLessEqual(total, ceiling)
        self.assertEqual(sum(len(c) for c in chunks), 10)

    def test_chunk_ops_never_splits_a_single_oversized_op(self):
        huge = make_op(0, payload={"pad": "x" * 10_000})
        chunks = chunk_ops([huge, make_op(1)], max_ops_per_tx=8, max_bytes_per_tx=100)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], [huge])

    def test_chunk_ops_rejects_invalid_ceilings(self):
        with self.assertRaises(ValueError):
            chunk_ops([make_op()], max_ops_per_tx=0)
        with self.assertRaises(ValueError):
            chunk_ops([make_op()], max_bytes_per_tx=0)

    def test_requests_for_broadcast_shape(self):
        ops = [make_op(0), make_op(1)]
        reqs = requests_for_broadcast(ops)
        self.assertEqual(len(reqs), 2)
        self.assertTrue(all(r[0] == "custom_json" for r in reqs))

    def test_required_account_single_account(self):
        ops = [make_op(0, account="player0005"), make_op(1, account="player0005")]
        self.assertEqual(required_account(ops), "player0005")

    def test_required_account_rejects_mixed_accounts(self):
        ops = [make_op(0, account="player0005"), make_op(1, account="someoneelse")]
        with self.assertRaises(ValueError):
            required_account(ops)

    def test_required_key_tier_posting_only(self):
        ops = [make_op(0, posting=True), make_op(1, posting=True)]
        self.assertEqual(required_key_tier(ops), "Posting")

    def test_required_key_tier_escalates_to_active_if_any_op_needs_it(self):
        ops = [make_op(0, posting=True), make_op(1, posting=False)]
        self.assertEqual(required_key_tier(ops), "Active")

    def test_required_key_tier_rejects_ops_with_no_auth_at_all(self):
        op = Op(operation_id="x", required_auths=[], required_posting_auths=[], json_payload={})
        with self.assertRaises(ValueError):
            required_key_tier([op])

    def test_op_key_tier_reads_posting_and_active_per_op(self):
        self.assertEqual(op_key_tier(make_op(0, posting=True)), "Posting")
        self.assertEqual(op_key_tier(make_op(0, posting=False)), "Active")

    def test_op_key_tier_rejects_an_op_with_no_auth_at_all(self):
        op = Op(operation_id="x", required_auths=[], required_posting_auths=[], json_payload={})
        with self.assertRaises(ValueError):
            op_key_tier(op)

    def test_chunk_ops_never_mixes_tiers_in_one_chunk(self):
        """FINDING B (Aletheia MEDIUM): `requestBroadcast` signs one chunk with one key, so a
        chunk containing both a Posting-only op and an Active-required op would silently sign
        the Posting op under Active authority it never asked for. A tier change must force a
        new chunk exactly like either size ceiling already does -- even with ceilings generous
        enough that count/bytes alone would have kept everything in one chunk."""
        ops = [make_op(0, posting=True), make_op(1, posting=True), make_op(2, posting=False), make_op(3, posting=True)]
        chunks = chunk_ops(ops, max_ops_per_tx=100, max_bytes_per_tx=10_000_000)
        self.assertEqual([len(c) for c in chunks], [2, 1, 1])
        self.assertEqual([op_key_tier(c[0]) for c in chunks], ["Posting", "Active", "Posting"])
        for chunk in chunks:
            self.assertEqual(len({op_key_tier(op) for op in chunk}), 1)


if __name__ == "__main__":
    unittest.main()
