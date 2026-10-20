import unittest

from fides_land.intent import StakeIntent
from fides_land.ops import LandOpError, intents_to_ops



TEST_APP = "ceres/0.0.0"

def intent(deed, stake_type, direction, uid, *, asset_ref=None, slot=None):
    return StakeIntent(deed, stake_type, direction, uid, asset_ref, slot)


def assert_envelope(test, op, account="player0005", app=TEST_APP):
    test.assertEqual(op.operation_id, "sm_stake_change")
    test.assertEqual(op.required_auths, [])
    test.assertEqual(op.required_posting_auths, [account])
    test.assertIn("n", op.json_payload)
    test.assertFalse(op.json_payload["auto_buy_grain"])
    test.assertEqual(op.json_payload["app"], TEST_APP)


class IntentPayloadTests(unittest.TestCase):
    def test_rejects_third_party_app_identifier(self):
        with self.assertRaisesRegex(LandOpError, "third-party app identifier"):
            intents_to_ops([intent("deed-A", "STK-LND-TOT", "stake", "TOTEM-001")], account="player0005", app="peakmonsters")

    def test_totem_stake_uses_items_shape(self):
        op = intents_to_ops([intent("deed-A", "STK-LND-TOT", "stake", "TOTEM-001")], account="player0005", app=TEST_APP)[0]
        assert_envelope(self, op)
        self.assertEqual(op.json_payload["deed_uid"], "deed-A")
        self.assertEqual(op.json_payload["unstake"], {"cards": [], "items": []})
        self.assertEqual(op.json_payload["stake"], {"cards": [], "items": [{"item_uid": "TOTEM-001", "stake_type_uid": "STK-LND-TOT"}]})

    def test_totem_unstake_uses_items_shape(self):
        op = intents_to_ops([intent("deed-A", "STK-LND-TOT", "unstake", "TOTEM-001")], account="player0005", app=TEST_APP)[0]
        self.assertEqual(op.json_payload["unstake"], {"cards": [], "items": [{"item_uid": "TOTEM-001"}]})
        self.assertEqual(op.json_payload["stake"], {"cards": [], "items": []})

    def test_power_core_stake_uses_items_shape(self):
        op = intents_to_ops([intent("deed-A", "STK-LND-PCR", "stake", "CORE-001")], account="player0005", app=TEST_APP)[0]
        self.assertEqual(op.json_payload["stake"], {"cards": [], "items": [{"item_uid": "CORE-001", "stake_type_uid": "STK-LND-PCR"}]})

    def test_power_core_unstake_uses_items_shape(self):
        op = intents_to_ops([intent("deed-A", "STK-LND-PCR", "unstake", "CORE-001")], account="player0005", app=TEST_APP)[0]
        self.assertEqual(op.json_payload["unstake"], {"cards": [], "items": [{"item_uid": "CORE-001"}]})

    def test_mixed_stakes_batch_cards_and_items_for_one_deed(self):
        ops = intents_to_ops([
            intent("deed-A", "STK-LND-WKR", "stake", "CARD-001", asset_ref="card-ref", slot=3),
            intent("deed-A", "STK-LND-TOT", "stake", "TOTEM-001"),
            intent("deed-A", "STK-LND-PCR", "stake", "CORE-001"),
        ], account="player0005", app=TEST_APP)
        self.assertEqual(len(ops), 1)
        payload = ops[0].json_payload
        self.assertEqual(payload["stake"], {
            "cards": [{"card_uid": "CARD-001", "stake_type_uid": "STK-LND-WKR", "slot": "3"}],
            "items": [
                {"item_uid": "CORE-001", "stake_type_uid": "STK-LND-PCR"},
                {"item_uid": "TOTEM-001", "stake_type_uid": "STK-LND-TOT"},
            ],
        })
        self.assertEqual(payload["unstake"], {"cards": [], "items": []})

    def test_multiple_deeds_produce_multiple_ops(self):
        ops = intents_to_ops([
            intent("deed-Z", "STK-LND-TOT", "stake", "TOTEM-Z"),
            intent("deed-A", "STK-LND-PCR", "stake", "CORE-A"),
        ], account="player0005", app=TEST_APP)
        self.assertEqual([op.json_payload["deed_uid"] for op in ops], ["deed-A", "deed-Z"])
        self.assertEqual(len(ops), 2)
        self.assertEqual(ops[0].json_payload["stake"]["items"], [{"item_uid": "CORE-A", "stake_type_uid": "STK-LND-PCR"}])
        self.assertEqual(ops[1].json_payload["stake"]["items"], [{"item_uid": "TOTEM-Z", "stake_type_uid": "STK-LND-TOT"}])

    def test_split_sides_keeps_each_op_single_sided(self):
        ops = intents_to_ops([
            intent("deed-A", "STK-LND-WKR", "unstake", "CARD-001"),
            intent("deed-A", "STK-LND-TOT", "stake", "TOTEM-001"),
        ], account="player0005", app=TEST_APP)
        self.assertEqual(len(ops), 2)
        for op in ops:
            payload = op.json_payload
            self.assertNotEqual(bool(payload["stake"]["cards"] or payload["stake"]["items"]), bool(payload["unstake"]["cards"] or payload["unstake"]["items"]))

    def test_unknown_class_fails_closed(self):
        with self.assertRaisesRegex(LandOpError, "unrecognised|unknown"):
            intents_to_ops([intent("deed-A", "STK-LND-UNKNOWN", "stake", "ASSET-001")], account="player0005", app=TEST_APP)

    def test_title_stakes_and_unstakes_using_items_shape(self):
        """the internal rules's 2026-08-27 ruling ("titles must not be locked ... behave like all
 other assets") -- Title's payload shape is observed both directions (an isolated
 Title-only swap, Posting tier), the same footing as Totem/Power Core, so it must
 build the exact same `items[]` shape they do, never raise. Inverts the old
 `test_title_raises_unverified_shape`, which this ruling supersedes.

 KNOWN GAP (reported to the dispatching session, not fixed here -- `fides_land/ops.py`
 is outside this agent's `tests/`-only scope): `_ITEM_STAKE_TYPES` at `fides_land/
 ops.py:59` is `frozenset({STAKE_TYPE_POWER_CORE, STAKE_TYPE_TOTEM})` and was never
 updated to include `STAKE_TYPE_TITLE`, so `_intent_class` still falls through to
 `raise LandOpError(f"unknown stake_type_uid {stake_type_uid!r}; refusing to emit")`
 for every Title intent even though `fides_land/render.py`'s allowlist and `ceres/
 op_preview.py`'s `UNVERIFIED_TYPES` were both correctly emptied. This test asserts
 the RULING's intended behaviour and is therefore currently RED against real master
 (observed: `fides_land.ops.LandOpError: unknown stake_type_uid 'STK-LND-TTL';
 refusing to emit`, both directions) -- adding STAKE_TYPE_TITLE to `_ITEM_STAKE_TYPES`
 is the one-line fix that turns it green; verified locally by applying that exact
 edit, confirming this test passes, then reverting it, so this is not a guess."""
        for direction in ("stake", "unstake"):
            with self.subTest(direction=direction):
                op = intents_to_ops([intent("deed-A", "STK-LND-TTL", direction, "TITLE-001")], account="player0005", app=TEST_APP)[0]
                if direction == "stake":
                    self.assertEqual(op.json_payload["stake"]["items"], [{"item_uid": "TITLE-001", "stake_type_uid": "STK-LND-TTL"}])
                    self.assertEqual(op.json_payload["unstake"], {"cards": [], "items": []})
                else:
                    self.assertEqual(op.json_payload["unstake"]["items"], [{"item_uid": "TITLE-001"}])
                    self.assertEqual(op.json_payload["stake"], {"cards": [], "items": []})

    def test_runi_stake_uses_cards_shape_with_null_slot(self):
        # Observed live (2026-08-26): the Runi rides in `cards[]` like a worker card, but
        # its stake payload carries a literal JSON `slot: null` -- it occupies the reserved
        # slot 0, never an assigned monster-card slot.
        op = intents_to_ops([intent("deed-A", "STK-LND-RUNI", "stake", "C2-505-0000001780")], account="player0005", app=TEST_APP)[0]
        assert_envelope(self, op)
        self.assertEqual(op.json_payload["stake"], {
            "cards": [{"card_uid": "C2-505-0000001780", "stake_type_uid": "STK-LND-RUNI", "slot": None}],
            "items": [],
        })
        self.assertEqual(op.json_payload["unstake"], {"cards": [], "items": []})

    def test_runi_unstake_uses_bare_card_shape(self):
        # Observed live (2026-08-26): the unstake side is bare -- no stake_type_uid, no
        # slot -- identical in shape to every other asset class's unstake.
        op = intents_to_ops([intent("deed-A", "STK-LND-RUNI", "unstake", "C2-505-0000001780")], account="player0005", app=TEST_APP)[0]
        self.assertEqual(op.json_payload["unstake"], {"cards": [{"card_uid": "C2-505-0000001780"}], "items": []})
        self.assertEqual(op.json_payload["stake"], {"cards": [], "items": []})

    def test_runi_stake_rejects_a_non_null_slot(self):
        with self.assertRaisesRegex(LandOpError, "slot"):
            intents_to_ops([intent("deed-A", "STK-LND-RUNI", "stake", "RUNI-001", slot=2)], account="player0005", app=TEST_APP)

    def test_item_stake_does_not_accept_slot(self):
        with self.assertRaisesRegex(LandOpError, "slot"):
            intents_to_ops([intent("deed-A", "STK-LND-TOT", "stake", "TOTEM-001", slot=1)], account="player0005", app=TEST_APP)

    def test_worker_stake_requires_slot(self):
        with self.assertRaisesRegex(LandOpError, "missing slot"):
            intents_to_ops([intent("deed-A", "STK-LND-WKR", "stake", "CARD-001")], account="player0005", app=TEST_APP)

    def test_item_unstake_does_not_accept_slot(self):
        with self.assertRaisesRegex(LandOpError, "slot"):
            intents_to_ops([intent("deed-A", "STK-LND-TOT", "unstake", "TOTEM-001", slot=1)], account="player0005", app=TEST_APP)

    def test_invalid_direction_is_rejected(self):
        with self.assertRaisesRegex(LandOpError, "direction"):
            intents_to_ops([intent("deed-A", "STK-LND-TOT", "hold", "TOTEM-001")], account="player0005", app=TEST_APP)

    def test_empty_uids_are_rejected(self):
        for field, value in (("deed_uid", ""), ("stake_type_uid", ""), ("asset_uid", "")):
            with self.subTest(field=field):
                values = {"deed_uid": "deed-A", "stake_type_uid": "STK-LND-TOT", "direction": "stake", "asset_uid": "TOTEM-001"}
                values[field] = value
                with self.assertRaisesRegex(LandOpError, field):
                    intents_to_ops([StakeIntent(**values)], account="player0005", app=TEST_APP)


if __name__ == "__main__":
    unittest.main()
