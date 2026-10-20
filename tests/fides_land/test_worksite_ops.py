
""" Phase 3 -- the worksite-change op builder (`fides_land.ops.worksite_change_op`).

No broadcast anywhere in this module: these tests only prove the payload shape, the
refusal guards, and that the built op passes the render.py allowlist. Real captured
payload shape (a captured op):
    {"op":"worksite_aura_construction","region_uid":"PR-BRL-41",
     "deed_uids":["I-298-2dd53d9ac60331"],"time_crystals":0,"app":"splinterlands/0.7.60","n":"..."}
"""

import unittest

from fides.bundle import build_bundle
from fides_land import ops as land_ops
from fides_land.render import allowlist_rules, build_deed_labels, build_renderer, build_uid_index, evaluate_allowlist, wave_summarizer

TEST_APP = "ceres/0.0.0"

DEED = "I-298-2dd53d9ac60331"
REGION = "PR-BRL-41"
ACCOUNT = "player0005"


def _build(target, *, terrain="mountain", current_resource=None, eligible=True, time_crystals=0):
    return land_ops.worksite_change_op(
        DEED, REGION, target,
        account=ACCOUNT, app=TEST_APP, eligible=eligible, terrain=terrain, current_resource=current_resource,
        time_crystals=time_crystals,
    )


def _payload_minus_nonce(op):
    payload = dict(op.json_payload)
    nonce = payload.pop("n")
    return payload, nonce


class WorksiteChangeOpPayloadTests(unittest.TestCase):
    """One test per target worksite -- the exact payload, not just 'it builds something'."""

    def test_grain_payload(self):
        op = _build("grain", terrain="plains")
        self.assertEqual(op.operation_id, "sm_land_operation")
        payload, nonce = _payload_minus_nonce(op)
        self.assertTrue(nonce and isinstance(nonce, str))
        self.assertEqual(payload, {
            "op": "worksite_grain_construction", "region_uid": REGION, "deed_uids": [DEED],
            "time_crystals": 0, "app": TEST_APP,
        })
        self.assertEqual(op.required_auths, [])
        self.assertEqual(op.required_posting_auths, [ACCOUNT])

    def test_wood_payload(self):
        op = _build("wood", terrain="swamp")
        self.assertEqual(op.json_payload["op"], "worksite_wood_construction")

    def test_stone_payload(self):
        op = _build("stone", terrain="desert")
        self.assertEqual(op.json_payload["op"], "worksite_stone_construction")

    def test_iron_payload(self):
        op = _build("iron", terrain="mountain")
        self.assertEqual(op.json_payload["op"], "worksite_iron_construction")

    def test_research_payload(self):
        op = _build("research", terrain="mountain")
        self.assertEqual(op.json_payload["op"], "worksite_research_construction")

    def test_sps_payload(self):
        op = _build("sps", terrain="mountain")
        self.assertEqual(op.json_payload["op"], "worksite_sps_construction")

    def test_aura_payload(self):
        op = _build("aura", terrain="mountain")
        self.assertEqual(op.json_payload["op"], "worksite_aura_construction")

    def test_target_lives_in_op_name_no_body_field_carries_it(self):
        op = _build("aura", terrain="mountain")
        payload = op.json_payload
        self.assertEqual(set(payload), {"op", "region_uid", "deed_uids", "time_crystals", "app", "n"})
        for key, value in payload.items():
            if key == "op":
                continue
            self.assertNotIn("aura", str(value).lower(), f"target leaked into non-op field {key!r}: {value!r}")

    def test_update_worksite_is_never_the_op_name(self):
        # update_worksite is the unrelated Feed Worker action (plan §0.3) -- this builder must
        # never emit it under any target. "mountain" is legal for iron/research/sps/aura
        # the natural targets outside that set are exercised on their own native terrain.
        terrain_by_target = {"grain": "plains", "wood": "swamp", "stone": "desert", "iron": "mountain",
                              "research": "mountain", "sps": "mountain", "aura": "mountain"}
        for target in sorted(land_ops.WORKSITE_SWAP_TARGETS):
            op = _build(target, terrain=terrain_by_target[target])
            self.assertNotEqual(op.json_payload["op"], "update_worksite")
            self.assertTrue(op.json_payload["op"].endswith("_construction"))

    def test_rejects_third_party_app_identifier(self):
        with self.assertRaisesRegex(land_ops.LandOpError, "third-party app identifier"):
            land_ops.worksite_change_op(
                DEED, REGION, "aura", account=ACCOUNT, app="peakmonsters", eligible=True, terrain="mountain",
            )


class WorksiteChangeOpAuthTierTests(unittest.TestCase):
    def test_declares_posting_never_active(self):
        op = _build("aura", terrain="mountain")
        self.assertEqual(op.required_auths, [])
        self.assertEqual(op.required_posting_auths, [ACCOUNT])


class WorksiteChangeOpTimeCrystalTests(unittest.TestCase):
    def test_defaults_to_zero(self):
        op = _build("aura", terrain="mountain")
        self.assertEqual(op.json_payload["time_crystals"], 0)

    def test_explicit_fifty_carries_through(self):
        op = _build("aura", terrain="mountain", time_crystals=50)
        self.assertEqual(op.json_payload["time_crystals"], 50)

    def test_refuses_any_other_value(self):
        for bad in (1, 25, 100, -50):
            with self.assertRaises(land_ops.LandOpError):
                _build("aura", terrain="mountain", time_crystals=bad)


class WorksiteChangeOpGuardTests(unittest.TestCase):
    """H1 and H5 -- both refuse to BUILD, not merely to broadcast."""

    def test_refuses_zero_pp_plot(self):
        # before this guard existed nothing stopped a build for an ineligible plot; simulate
        # that regression by asserting the call raises when eligible=False.
        with self.assertRaises(land_ops.LandOpError) as ctx:
            _build("aura", terrain="mountain", eligible=False)
        self.assertIn("zero-PP", str(ctx.exception))

    def test_refuses_illegal_target_for_terrain(self):
        # "grain" is not one of mountain's four legal targets (iron/research/sps/aura)
        with self.assertRaises(land_ops.LandOpError) as ctx:
            _build("grain", terrain="mountain")
        self.assertIn("not one of this plot's legal worksites", str(ctx.exception))

    def test_permits_legacy_grain_on_non_grain_terrain_as_the_natural_slot(self):
        # a pre-1.75 grain farm still on a mountain terrain: current_resource corrects the
        # legal-target set so the plot's own actual current worksite is never "illegal".
        op = _build("grain", terrain="mountain", current_resource="grain")
        self.assertEqual(op.json_payload["op"], "worksite_grain_construction")

    def test_refuses_unrecognised_terrain(self):
        with self.assertRaises(land_ops.LandOpError):
            _build("aura", terrain="not-a-real-terrain")

    def test_refuses_unknown_target(self):
        with self.assertRaises(land_ops.LandOpError):
            _build("diamond", terrain="mountain")

    def test_refuses_empty_deed_uid(self):
        with self.assertRaises(land_ops.LandOpError):
            land_ops.worksite_change_op("", REGION, "aura", account=ACCOUNT, app=TEST_APP, eligible=True, terrain="mountain")

    def test_refuses_empty_account(self):
        with self.assertRaises(land_ops.LandOpError):
            land_ops.worksite_change_op(DEED, REGION, "aura", account="", app=TEST_APP, eligible=True, terrain="mountain")


class WorksiteLegalTargetsTests(unittest.TestCase):
    def test_every_terrain_yields_exactly_four_targets(self):
        for terrain in land_ops._NATURAL_TERRAIN_RESOURCE:
            targets = land_ops.worksite_legal_targets(terrain)
            self.assertEqual(len(targets), 4, f"{terrain} yielded {targets!r}")
            self.assertIn("research", targets)
            self.assertIn("sps", targets)
            self.assertIn("aura", targets)

    def test_unrecognised_terrain_returns_none(self):
        self.assertIsNone(land_ops.worksite_legal_targets("volcano"))


class WorksiteChangeOpAllowlistIntegrationTests(unittest.TestCase):
    """End-to-end: build (never broadcast) -> render -> evaluate_allowlist, on a real plot
    code, proving the built op is signable-shaped without ever calling requestBroadcast."""

    def _bundle(self, op):
        renderer = build_renderer(build_uid_index({}), build_deed_labels({}))
        return build_bundle(
            [("wave-1", [op])], consumer="fides_land", renderer=renderer,
            allowlist=allowlist_rules(ACCOUNT, app=TEST_APP), wave_summarizer=wave_summarizer,
        )

    def test_well_formed_worksite_op_passes_allowlist(self):
        op = _build("aura", terrain="mountain")
        bundle = self._bundle(op)
        self.assertEqual(bundle["header"]["key_tier"], "Posting")
        result = evaluate_allowlist(bundle)
        self.assertTrue(result["ok"], result["violations"])

    def test_renderer_names_the_target_and_deed(self):
        op = _build("aura", terrain="mountain")
        renderer = build_renderer(build_uid_index({}), {DEED: "41-2-27 (epic, x1.85)"})
        row = renderer(op)
        self.assertIn("aura", row["text"])
        self.assertIn("41-2-27", row["text"])

    def test_allowlist_refuses_bad_op_name_even_if_hand_tampered_after_build(self):
        op = _build("aura", terrain="mountain")
        op.json_payload["op"] = "worksite_diamond_construction"  # simulate a tampered bundle
        bundle = self._bundle(op)
        result = evaluate_allowlist(bundle)
        self.assertFalse(result["ok"])
        self.assertTrue(any("worksite_" in v and "construction targets" in v for v in result["violations"]))

    def test_allowlist_refuses_update_worksite_id(self):
        # if something ever tried to slip update_worksite (the unrelated Feed Worker op)
        # through this family, it must be refused, not silently accepted.
        op = _build("aura", terrain="mountain")
        op.json_payload["op"] = "update_worksite"
        bundle = self._bundle(op)
        result = evaluate_allowlist(bundle)
        self.assertFalse(result["ok"])

    def test_allowlist_refuses_out_of_range_time_crystals_even_if_hand_tampered(self):
        op = _build("aura", terrain="mountain")
        op.json_payload["time_crystals"] = 999
        bundle = self._bundle(op)
        result = evaluate_allowlist(bundle)
        self.assertFalse(result["ok"])
        self.assertTrue(any("time_crystals" in v for v in result["violations"]))

    def test_allowlist_refuses_active_tier_worksite_op_via_direct_body(self):
        # bypasses build_bundle's own account/tier derivation to isolate the allowlist's
        # per-op tier check on a hand-built bundle body, matching the tamper-after-build
        # pattern the rest of this class uses.
        op = _build("aura", terrain="mountain")
        bundle = self._bundle(op)
        raw_op = bundle["waves"][0]["chunks"][0]["keychain_operations"][0][1]
        raw_op["required_auths"] = [ACCOUNT]
        raw_op["required_posting_auths"] = []
        result = evaluate_allowlist(bundle)
        self.assertFalse(result["ok"])
        self.assertTrue(any("auth tier must be" in v for v in result["violations"]))

    def test_sm_land_operation_family_present_alongside_sm_stake_change(self):
        rules = allowlist_rules(ACCOUNT, app=TEST_APP)
        self.assertIn("sm_stake_change", rules["families"])
        self.assertIn("sm_land_operation", rules["families"])
        self.assertEqual(rules["families"]["sm_land_operation"]["auth_tier"], "Posting")

    def test_combine_family_rejects_worksite_payload(self):
        op = _build("aura", terrain="mountain")
        op.operation_id = "sm_combine_cards"
        bundle = self._bundle(op)
        result = evaluate_allowlist(bundle)
        self.assertFalse(result["ok"])
        self.assertTrue(any("combine cards" in v for v in result["violations"]))


if __name__ == "__main__":
    unittest.main()
