import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fides_land import waves
from fides_land.intent import StakeIntent
from fides_land import ops as land_ops
from tests.fides_land.fixtures import backfill_row, card, evicted, move, plan_dict


TEST_APP = "ceres/0.0.0"

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


class CardReadyNowTests(unittest.TestCase):
    def test_unstaked_apparent_always_ready_no_ready_at(self):
        c = card("A", origin="idle", bucket="unstaked_apparent")
        ready, ready_at = waves.card_ready_now(c, now=NOW)
        self.assertTrue(ready)
        self.assertIsNone(ready_at)

    def test_expired_historical_still_cooling(self):
        c = card("A", origin="idle", bucket="stake_expired_or_historical", stake_end_date=(NOW - timedelta(hours=1)).isoformat())
        ready, ready_at = waves.card_ready_now(c, now=NOW)
        self.assertFalse(ready)
        self.assertIsNotNone(ready_at)
        self.assertGreater(ready_at, NOW)

    def test_stake_end_date_is_cooldown_end_with_only_action_margin(self):
        """Mutation check: replaced ready_at with end + timedelta(hours=cooldown_hours + margin_hours), ran this test red, then restored it."""
        c = card("A", origin="idle", bucket="stake_expired_or_historical", stake_end_date=(NOW - timedelta(hours=10)).isoformat())
        ready, ready_at = waves.card_ready_now(c, now=NOW, cooldown_hours=72.0, margin_hours=6.0)
        self.assertTrue(ready)
        self.assertEqual(ready_at, NOW - timedelta(hours=4))

    def test_expired_historical_end_well_past_action_margin(self):
        c = card("A", origin="idle", bucket="stake_expired_or_historical", stake_end_date=(NOW - timedelta(hours=80)).isoformat())
        ready, ready_at = waves.card_ready_now(c, now=NOW)
        self.assertTrue(ready)  # the displayed cooldown end is already 80 hours in the past

    def test_expired_historical_missing_stake_end_date_treated_as_not_ready(self):
        c = card("A", origin="idle", bucket="stake_expired_or_historical", stake_end_date=None)
        ready, ready_at = waves.card_ready_now(c, now=NOW)
        self.assertFalse(ready)  # conservative: never optimistically assumed clear

    def test_rejects_staked_origin_card(self):
        c = card("A", origin="staked")
        with self.assertRaises(ValueError):
            waves.card_ready_now(c, now=NOW)


class ClassifyPlanTests(unittest.TestCase):
    def test_wave1_contains_every_unstake(self):
        x = card("X", origin="staked", asset_ref="rx", card_uid="UX", source_uid="deed-S")
        e = evicted("E", asset_ref="re", card_uid="UE")
        plan = plan_dict(moves=[move(x, destination_uid="deed-D", evicted_row=e)])
        split = waves.classify_plan(plan, now=NOW)
        unstaked_uids = {c["card_uid"] for cards in split.unstake.values() for c in cards}
        self.assertEqual(unstaked_uids, {"UX", "UE"})

    def test_wave3_gets_same_card_round_trip(self):
        x = card("X", origin="staked", asset_ref="rx", card_uid="UX", source_uid="deed-S")
        plan = plan_dict(moves=[move(x, destination_uid="deed-D")])
        split = waves.classify_plan(plan, now=NOW)
        deferred_uids = {c["card_uid"] for cards in split.stake_deferred.values() for c in cards}
        now_uids = {c["card_uid"] for cards in split.stake_now.values() for c in cards}
        self.assertIn("UX", deferred_uids)
        self.assertNotIn("UX", now_uids)

    def test_wave2_gets_unstaked_apparent_idle(self):
        y = card("Y", origin="idle", bucket="unstaked_apparent", asset_ref="ry", card_uid="UY")
        plan = plan_dict(moves=[move(y, destination_uid="deed-D")])
        split = waves.classify_plan(plan, now=NOW)
        now_uids = {c["card_uid"] for cards in split.stake_now.values() for c in cards}
        self.assertIn("UY", now_uids)

    def test_wave2_gets_expired_idle_past_cooldown(self):
        y = card("Y", origin="idle", bucket="stake_expired_or_historical", asset_ref="ry", card_uid="UY", stake_end_date=(NOW - timedelta(hours=100)).isoformat())
        plan = plan_dict(moves=[move(y, destination_uid="deed-D")])
        split = waves.classify_plan(plan, now=NOW)
        now_uids = {c["card_uid"] for cards in split.stake_now.values() for c in cards}
        self.assertIn("UY", now_uids)

    def test_wave3_gets_expired_idle_still_cooling(self):
        y = card("Y", origin="idle", bucket="stake_expired_or_historical", asset_ref="ry", card_uid="UY", stake_end_date=(NOW - timedelta(hours=1)).isoformat())
        plan = plan_dict(moves=[move(y, destination_uid="deed-D")])
        split = waves.classify_plan(plan, now=NOW)
        deferred_uids = {c["card_uid"] for cards in split.stake_deferred.values() for c in cards}
        self.assertIn("UY", deferred_uids)

    def test_wave3_equals_wave1_unstakes_plus_still_cooling_idles(self):
        # explicit Phase 3 acceptance wording: "wave 3 contains exactly the cards wave 1
        # unstakes plus still-cooling idles"
        round_trip = card("X", origin="staked", asset_ref="rx", card_uid="UX", source_uid="deed-S")
        cooling_idle = card("Y", origin="idle", bucket="stake_expired_or_historical", asset_ref="ry", card_uid="UY", stake_end_date=(NOW - timedelta(hours=1)).isoformat())
        ready_idle = card("Z", origin="idle", bucket="unstaked_apparent", asset_ref="rz", card_uid="UZ")
        plan = plan_dict(moves=[
            move(round_trip, destination_uid="deed-D"),
            move(cooling_idle, destination_uid="deed-D2"),
            move(ready_idle, destination_uid="deed-D3"),
        ])
        split = waves.classify_plan(plan, now=NOW)
        deferred_uids = {c["card_uid"] for cards in split.stake_deferred.values() for c in cards}
        self.assertEqual(deferred_uids, {"UX", "UY"})  # X is the round-trip, Y is still cooling
        now_uids = {c["card_uid"] for cards in split.stake_now.values() for c in cards}
        self.assertEqual(now_uids, {"UZ"})

    def test_backfill_classified_same_as_idle_moves(self):
        y = card("Y", origin="idle", bucket="unstaked_apparent", asset_ref="ry", card_uid="UY")
        plan = plan_dict(backfill=[backfill_row(y, plot_uid="deed-S")])
        split = waves.classify_plan(plan, now=NOW)
        now_uids = {c["card_uid"] for cards in split.stake_now.values() for c in cards}
        self.assertIn("UY", now_uids)


class BuildWaveOpsTests(unittest.TestCase):
    def test_slots_not_collided_between_wave2_and_wave3_on_same_deed(self):
        # two idle cards target the same destination deed -- one ready now, one still cooling
        ready_card = card("A", origin="idle", bucket="unstaked_apparent", asset_ref="ra", card_uid="UA")
        cooling_card = card("B", origin="idle", bucket="stake_expired_or_historical", asset_ref="rb", card_uid="UB", stake_end_date=(NOW - timedelta(hours=1)).isoformat())
        plan = plan_dict(moves=[move(ready_card, destination_uid="deed-D"), move(cooling_card, destination_uid="deed-D")])
        split = waves.classify_plan(plan, now=NOW)
        wave_ops = waves.build_wave_ops(split, account="player0005", app=TEST_APP, assignments=[], capacity_by_deed={"deed-D": 5})
        now_slot = split.stake_now["deed-D"][0]["slot"]
        deferred_slot = split.stake_deferred["deed-D"][0]["slot"]
        self.assertNotEqual(now_slot, deferred_slot)
        self.assertEqual({now_slot, deferred_slot}, {1, 2})

    def test_returns_all_three_wave_keys_even_when_empty(self):
        plan = plan_dict()
        split = waves.classify_plan(plan, now=NOW)
        wave_ops = waves.build_wave_ops(split, account="player0005", app=TEST_APP, assignments=[], capacity_by_deed={})
        self.assertEqual(set(wave_ops), {"unstake", "stake_now", "stake_deferred"})
        self.assertEqual(wave_ops["unstake"], [])
        self.assertEqual(wave_ops["stake_now"], [])
        self.assertEqual(wave_ops["stake_deferred"], [])


class IntentWaveTests(unittest.TestCase):
    def test_unknown_class_fails_closed_to_cooling(self):
        unknown = "STK-LND-NOT-IN-EITHER-TABLE"
        self.assertEqual(waves.cooldown_class(unknown), "cards")
        self.assertTrue(waves.stake_type_is_cooling(unknown))

    def test_items_are_immediate_while_worker_cards_are_deferred(self):
        intents = [
            StakeIntent("deed-A", "STK-LND-TOT", "stake", "TOTEM-001", None, None),
            StakeIntent("deed-A", "STK-LND-PCR", "unstake", "CORE-001", None, None),
            StakeIntent("deed-A", "STK-LND-WKR", "stake", "CARD-001", "card-ref", 2),
        ]
        split = waves.classify_intents(intents)
        self.assertEqual(split["unstake"], [intents[1]])
        self.assertEqual(split["stake_now"], [intents[0]])
        self.assertEqual(split["stake_deferred"], [intents[2]])

    def test_unknown_intent_class_is_rejected_before_wave_assignment(self):
        unknown = StakeIntent("deed-A", "STK-LND-UNKNOWN", "stake", "ASSET-001", None, None)
        with self.assertRaisesRegex(land_ops.LandOpError, "unknown"):
            waves.classify_intents([unknown])

    def test_validator_acceptance_cannot_silently_skip_wave_assignment(self):
        intent = StakeIntent("deed-A", land_ops.STAKE_TYPE_WORKER, "rebalance", "CARD-001", None, 1)
        with patch.object(land_ops, "_validate_intent", return_value="card"):
            with self.assertRaisesRegex(land_ops.LandOpError, "no wave assignment"):
                waves.classify_intents([intent])

    def test_op_payload_support_does_not_auto_exempt_a_new_stake_type_from_cooldown(self):
        """`land_ops._ITEM_STAKE_TYPES` and `waves`'s cooldown classification are
 deliberately DECOUPLED (found while wiring Ceres's browser-side cooldown gate,
 `ceres-multiplot-batch-checkout-gap.md` W3): `_ITEM_STAKE_TYPES` tracks which
 stake types have a verified op PAYLOAD shape, nothing about the ~3-day unstake
 cooldown. Before this fix, `cooldown_class` read `land_ops._ITEM_STAKE_TYPES`
 directly, which meant Title (`STK-LND-TTL`) -- a real card the internal rules's ruling
 says does NOT cool down, but whose payload shape is still unverified and so is
 absent from `_ITEM_STAKE_TYPES` -- would have been wrongly classified as cooling
 the moment Title op support landed and someone forgot to also touch the cooldown
 table. This test locks in the correct, safer default: adding op-payload support
 for a brand new stake type does NOT silently exempt it from the cooldown: it
 stays "cards" (cooling, fail-closed) until the internal rules explicitly rules on
 it and it is added to `waves._NON_COOLING_STAKE_TYPES`. Op construction itself
 (a purely payload-shape concern) is unaffected by that decoupling."""
        hypothetical = "STK-LND-HYP"
        intent = StakeIntent("deed-A", hypothetical, "stake", "ASSET-001", None, None)
        with patch.object(land_ops, "_ITEM_STAKE_TYPES", land_ops._ITEM_STAKE_TYPES | {hypothetical}):
            self.assertEqual(waves.cooldown_class(hypothetical), "cards")
            split = waves.classify_intents([intent])
            self.assertEqual(split["stake_deferred"], [intent])
            op = land_ops.intents_to_ops([intent], account="player0005", app=TEST_APP)[0]
        self.assertEqual(op.json_payload["stake"]["items"], [{"item_uid": "ASSET-001", "stake_type_uid": hypothetical}])

    def test_title_is_immediately_reusable_as_an_item(self):
        for direction in ("stake", "unstake"):
            with self.subTest(direction=direction):
                intent = StakeIntent("deed-A", land_ops.STAKE_TYPE_TITLE, direction, "ASSET-001", None, None)
                split = waves.classify_intents([intent])
                if direction == "stake":
                    self.assertEqual(split["stake_deferred"], [])
                    self.assertEqual(split["stake_now"], [intent])
                else:
                    self.assertEqual(split["unstake"], [intent])

    def test_runi_is_no_longer_refused(self):
        # STK-LND-RUNI's payload shape is now OBSERVED both directions -- unlike Titles
        # it must NOT raise "UNVERIFIED" here.
        for direction in ("stake", "unstake"):
            with self.subTest(direction=direction):
                intent = StakeIntent("deed-A", land_ops.STAKE_TYPE_RUNI, direction, "ASSET-001", None, None)
                waves.classify_intents([intent])

    def test_classify_intents_and_intents_to_ops_reject_same_malformed_intents(self):
        malformed = [
            ("worker stake missing slot", StakeIntent("deed-A", land_ops.STAKE_TYPE_WORKER, "stake", "CARD-001", None, None)),
            ("item stake carrying slot", StakeIntent("deed-A", land_ops.STAKE_TYPE_TOTEM, "stake", "TOTEM-001", None, 1)),
            ("empty deed uid", StakeIntent("", land_ops.STAKE_TYPE_WORKER, "stake", "CARD-001", None, 1)),
            ("empty asset uid", StakeIntent("deed-A", land_ops.STAKE_TYPE_WORKER, "stake", "", None, 1)),
            ("negative slot", StakeIntent("deed-A", land_ops.STAKE_TYPE_WORKER, "stake", "CARD-001", None, -1)),
        ]
        for label, intent in malformed:
            with self.subTest(label=label):
                with self.assertRaises(land_ops.LandOpError) as classify_error:
                    waves.classify_intents([intent])
                with self.assertRaises(land_ops.LandOpError) as ops_error:
                    land_ops.intents_to_ops([intent], account="player0005", app=TEST_APP)
                self.assertEqual(str(classify_error.exception), str(ops_error.exception))


if __name__ == "__main__":
    unittest.main()
