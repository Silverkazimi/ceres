import json
import tempfile
import unittest
from pathlib import Path

from fides.bundle import BundleError, all_unit_ids, build_bundle, load_bundle, ops_for_unit, write_bundle
from fides.op import Op


def stub_renderer(op):
    return {"text": f"OP {op.operation_id} -> {op.json_payload}"}


STUB_ALLOWLIST = {"rules": []}


def make_ops(n, account="player0005"):
    return [Op(operation_id="generic_op", required_auths=[], required_posting_auths=[account], json_payload={"i": i}) for i in range(n)]


class BundleRoundTripTests(unittest.TestCase):
    def test_generic_op_list_round_trips_with_no_land_content(self):
        ops = make_ops(3)
        body = build_bundle([("wave-1", ops)], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)
        self.assertEqual(body["header"]["consumer"], "generic-test")
        self.assertEqual(body["header"]["account"], "player0005")
        self.assertEqual(body["header"]["key_tier"], "Posting")
        self.assertEqual(len(body["waves"]), 1)
        self.assertEqual(body["waves"][0]["op_count"], 3)
        blob = json.dumps(body)
        self.assertNotIn("splinterlands", blob.lower())
        self.assertNotIn("deed", blob.lower())
        self.assertNotIn("sm_stake_change", blob)

    def test_batches_at_configured_op_ceiling(self):
        ops = make_ops(10)
        body = build_bundle([("wave-1", ops)], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST, max_ops_per_tx=4, max_bytes_per_tx=10_000_000)
        chunks = body["waves"][0]["chunks"]
        self.assertEqual([len(c["ops"]) for c in chunks], [4, 4, 2])
        self.assertEqual(all_unit_ids(body), ["wave-1.0", "wave-1.1", "wave-1.2"])

    def test_single_chunk_wave_gets_bare_wave_name_as_unit_id(self):
        body = build_bundle([("wave-1", make_ops(2))], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)
        self.assertEqual(all_unit_ids(body), ["wave-1"])

    def test_multiple_waves_produce_independent_unit_ids(self):
        body = build_bundle(
            [("unstake", make_ops(1)), ("stake-now", make_ops(1)), ("stake-deferred", make_ops(1))],
            consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST,
        )
        self.assertEqual(all_unit_ids(body), ["unstake", "stake-now", "stake-deferred"])

    def test_rejects_empty_waves(self):
        with self.assertRaises(BundleError):
            build_bundle([], consumer="x", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)

    def test_rejects_all_waves_with_no_ops(self):
        with self.assertRaises(BundleError):
            build_bundle([("wave-1", [])], consumer="x", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)

    def test_ops_for_unit_returns_raw_custom_json_ops(self):
        body = build_bundle([("wave-1", make_ops(2))], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)
        ops = ops_for_unit(body, "wave-1")
        self.assertEqual(len(ops), 2)
        self.assertTrue(all(o["id"] == "generic_op" for o in ops))

    def test_ops_for_unit_raises_on_unknown_unit(self):
        body = build_bundle([("wave-1", make_ops(1))], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)
        with self.assertRaises(BundleError):
            ops_for_unit(body, "does-not-exist")

    def test_mixed_tier_wave_splits_into_per_unit_homogeneous_chunks_with_own_tier(self):
        """FINDING B (Aletheia MEDIUM): the signer page must know each UNIT's own tier, not
 just the bundle-wide `header.key_tier` (which escalates to Active the moment ANY op
 anywhere needs it -- `fides/op.py`'s `required_key_tier`). A mixed-tier wave must
 split into tier-homogeneous chunks, and each chunk must carry its OWN `key_tier`
 alongside `header.key_tier`'s bundle-wide figure."""
        posting_op = Op(operation_id="generic_op", required_auths=[], required_posting_auths=["player0005"], json_payload={"i": 0})
        active_op = Op(operation_id="generic_op", required_auths=["player0005"], required_posting_auths=[], json_payload={"i": 1})
        body = build_bundle([("wave-1", [posting_op, active_op])], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)
        chunks = body["waves"][0]["chunks"]
        self.assertEqual(body["header"]["key_tier"], "Active")  # bundle-wide: still the highest tier present
        self.assertEqual([c["key_tier"] for c in chunks], ["Posting", "Active"])
        # each row states its own op's tier too, matching its chunk's tier
        self.assertEqual(chunks[0]["rows"][0]["tier"], "Posting")
        self.assertEqual(chunks[1]["rows"][0]["tier"], "Active")

    def test_wave_summarizer_content_carried_through_untouched(self):
        def summarizer(wave_name, ops):
            return {"custom_field": f"{wave_name}-{len(ops)}"}
        body = build_bundle([("wave-1", make_ops(2))], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST, wave_summarizer=summarizer)
        self.assertEqual(body["waves"][0]["summary"], {"custom_field": "wave-1-2"})


class BundleWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_bundle_round_trips_bundle_json_and_bundle_js(self):
        body = build_bundle([("wave-1", make_ops(2))], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)
        out_dir = Path(self.tmp.name) / "batch1"
        json_path, js_path = write_bundle(body, out_dir)
        self.assertTrue(json_path.exists())
        self.assertTrue(js_path.exists())
        reloaded = load_bundle(out_dir)
        self.assertEqual(reloaded, body)
        js_text = js_path.read_text(encoding="utf-8")
        self.assertTrue(js_text.startswith("window.FIDES_BUNDLE = "))
        inner = json.loads(js_text[len("window.FIDES_BUNDLE = "):].rstrip().rstrip(";"))
        self.assertEqual(inner, body)

    def test_write_bundle_is_immutable_identical_rewrite_is_a_noop(self):
        body = build_bundle([("wave-1", make_ops(1))], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)
        out_dir = Path(self.tmp.name) / "batch1"
        write_bundle(body, out_dir)
        write_bundle(body, out_dir)  # identical content -- must not raise

    def test_write_bundle_refuses_to_overwrite_with_different_content(self):
        body1 = build_bundle([("wave-1", make_ops(1))], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)
        body2 = build_bundle([("wave-1", make_ops(2))], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)
        out_dir = Path(self.tmp.name) / "batch1"
        write_bundle(body1, out_dir)
        with self.assertRaises(FileExistsError):
            write_bundle(body2, out_dir)

    def test_write_bundle_leaves_no_temp_files_behind(self):
        body = build_bundle([("wave-1", make_ops(1))], consumer="generic-test", renderer=stub_renderer, allowlist=STUB_ALLOWLIST)
        out_dir = Path(self.tmp.name) / "batch1"
        write_bundle(body, out_dir)
        names = sorted(p.name for p in out_dir.iterdir())
        self.assertEqual(names, ["bundle.js", "bundle.json"])


if __name__ == "__main__":
    unittest.main()
