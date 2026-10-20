import json
import unittest

from fides.bundle import build_bundle
from fides.op import Op
from fides_land.intent import StakeIntent
from fides_land.ops import (

    STAKE_TYPE_POWER_CORE,
    STAKE_TYPE_RUNI,
    STAKE_TYPE_TITLE,
    STAKE_TYPE_TOTEM,
    STAKE_TYPE_WORKER,
    LandOpError,
    intents_to_ops,
)
from fides_land.render import allowlist_rules, build_deed_labels, build_renderer, build_uid_index, evaluate_allowlist, wave_summarizer
from tests.fides_land.fixtures import backfill_row, card, evicted, move, plan_dict


TEST_APP = "ceres/0.0.0"

def clean_land_ops():
    """A realistic, allowlist-clean pair of land ops."""
    unstake_payload = {"unstake": {"cards": [{"card_uid": "UOLD"}], "items": []}, "stake": {"cards": [], "items": []}, "deed_uid": "deed-1", "auto_buy_grain": False, "app": TEST_APP, "n": "abc1234567"}
    stake_payload = {"unstake": {"cards": [], "items": []}, "stake": {"cards": [{"card_uid": "UNEW", "stake_type_uid": "STK-LND-WKR", "slot": "1"}], "items": []}, "deed_uid": "deed-1", "auto_buy_grain": False, "app": TEST_APP, "n": "xyz9876543"}
    return [
        Op(operation_id="sm_stake_change", required_auths=[], required_posting_auths=["player0005"], json_payload=unstake_payload),
        Op(operation_id="sm_stake_change", required_auths=[], required_posting_auths=["player0005"], json_payload=stake_payload),
    ]


def clean_worksite_op():
    return Op(
        operation_id="sm_land_operation",
        required_auths=[],
        required_posting_auths=["player0005"],
        json_payload={
            "op": "worksite_aura_construction",
            "region_uid": "PR-BRL-41",
            "deed_uids": ["deed-1"],
            "time_crystals": 0,
            "app": TEST_APP,
        },
    )


def build_clean_bundle(ops=None):
    ops = ops or clean_land_ops()
    plan = plan_dict()
    renderer = build_renderer(build_uid_index(plan), build_deed_labels({}))
    return build_bundle(
        [("wave-1", ops)],
        consumer="fides_land",
        renderer=renderer,
        allowlist=allowlist_rules("player0005", app=TEST_APP),
        wave_summarizer=wave_summarizer,
    )


class RenderRowTests(unittest.TestCase):
    def test_renderer_produces_named_row_from_same_snapshot_record(self):
        c = card("Tireless Farmhand", base_pp=9400.0, origin="staked", asset_ref="rx", card_uid="UX", source_uid="deed-S")
        plan = plan_dict(moves=[move(c, destination_uid="deed-D")])
        uid_index = build_uid_index(plan)
        labels = build_deed_labels({"deed-S": {"plot": "41-8-13", "rarity": "common", "mult": 1.10}})
        renderer = build_renderer(uid_index, labels)
        unstake_op = Op(operation_id="sm_stake_change", required_auths=[], required_posting_auths=["player0005"],
                         json_payload={"unstake": {"cards": [{"card_uid": "UX"}], "items": []}, "stake": {"cards": [], "items": []}, "deed_uid": "deed-S"})
        row = renderer(unstake_op)
        self.assertIn("Tireless Farmhand", row["text"])
        self.assertIn("9,400", row["text"])
        self.assertIn("41-8-13", row["text"])


class AllowlistAttemptedViolationTests(unittest.TestCase):
    """One test per allowlist rule (plan §9 Phase 4: 'refuses each of the six land allowlist
    violations individually'). fides_land implements seven checks (the plan's prose lists a
    'key must be Posting' item beyond the six the acceptance bullet counts, folded in here as
    defence in depth since it costs nothing) -- every one of them is individually violation-
    tested below, a superset of the six named."""

    def test_clean_bundle_passes(self):
        result = evaluate_allowlist(build_clean_bundle())
        self.assertTrue(result["ok"], result["violations"])

    def test_refuses_third_party_app_identifier(self):
        bundle = build_clean_bundle()
        operation = bundle["waves"][0]["chunks"][0]["keychain_operations"][0][1]
        parsed = json.loads(operation["json"])
        parsed["app"] = "peakmonsters"
        operation["json"] = json.dumps(parsed)
        result = evaluate_allowlist(bundle)
        self.assertFalse(result["ok"])
        self.assertTrue(any("every emitted app" in violation for violation in result["violations"]))

    def test_clean_worksite_bundle_passes(self):
        result = evaluate_allowlist(build_clean_bundle([clean_worksite_op()]))
        self.assertTrue(result["ok"], result["violations"])

    def test_refuses_absent_worksite_op(self):
        op = clean_worksite_op()
        op.json_payload.pop("op")
        result = evaluate_allowlist(build_clean_bundle([op]))
        self.assertFalse(result["ok"])
        self.assertTrue(any("worksite_<target>_construction" in v for v in result["violations"]))

    def test_refuses_absent_worksite_time_crystals(self):
        op = clean_worksite_op()
        op.json_payload.pop("time_crystals")
        result = evaluate_allowlist(build_clean_bundle([op]))
        self.assertFalse(result["ok"])
        self.assertTrue(any("time_crystals" in v for v in result["violations"]))

    def test_refuses_empty_worksite_deed_uids(self):
        op = clean_worksite_op()
        op.json_payload["deed_uids"] = []
        result = evaluate_allowlist(build_clean_bundle([op]))
        self.assertFalse(result["ok"])
        self.assertTrue(any("deed_uids" in v for v in result["violations"]))

    def test_refuses_stake_card_without_stake_type_uid(self):
        ops = clean_land_ops()
        ops[1].json_payload["stake"]["cards"][0].pop("stake_type_uid")
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertFalse(result["ok"])
        self.assertTrue(any("stake.cards" in v for v in result["violations"]))

    def test_refuses_absent_operation_id(self):
        bundle = build_clean_bundle()
        del bundle["waves"][0]["chunks"][0]["keychain_operations"][0][1]["id"]
        result = evaluate_allowlist(bundle)
        self.assertFalse(result["ok"])
        self.assertTrue(any("op id None" in v for v in result["violations"]))

    def test_refuses_absent_family_auth_tier(self):
        bundle = build_clean_bundle()
        del bundle["header"]["allowlist"]["families"]["sm_stake_change"]["auth_tier"]
        result = evaluate_allowlist(bundle)
        self.assertFalse(result["ok"])
        self.assertTrue(any("auth tier must be" in v for v in result["violations"]))

    def test_refuses_non_sm_stake_change_id(self):
        ops = clean_land_ops()
        ops[0].operation_id = "sm_market_rent"
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertFalse(result["ok"])
        self.assertTrue(any("sm_stake_change" in v for v in result["violations"]))

    def test_refuses_active_authority(self):
        ops = clean_land_ops()
        ops[0].required_auths = ["player0005"]  # Active-tier auth injected -- must never be allowed
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertFalse(result["ok"])
        self.assertTrue(any("required_auths" in v for v in result["violations"]))

    def test_refuses_wrong_stake_type_uid(self):
        ops = clean_land_ops()
        ops[1].json_payload["stake"]["cards"][0]["stake_type_uid"] = "STK-LND-TOT"
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertFalse(result["ok"])
        self.assertTrue(any("STK-LND-WKR" in v for v in result["violations"]))

    def test_permits_power_core_in_stake_items(self):
        """: fides_land/ops.py:intents_to_ops already routes STK-LND-PCR/STK-LND-TOT into
        items[] -- the allowlist must permit exactly what the emitter already produces, not
        refuse it. Reproduces the exact bundle shape from the bug report."""
        ops = clean_land_ops()
        ops[1].json_payload["stake"]["items"] = [{"item_uid": "ITEM1", "stake_type_uid": "STK-LND-PCR"}]
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertTrue(result["ok"], result["violations"])

    def test_permits_totem_in_unstake_items(self):
        ops = clean_land_ops()
        ops[0].json_payload["unstake"]["items"] = [{"item_uid": "I-1", "stake_type_uid": "STK-LND-TOT"}]
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertTrue(result["ok"], result["violations"])

    def test_permits_the_real_bare_unstake_item_shape_with_no_stake_type_uid_at_all(self):
        """`fides_land/ops.py:_intent_entry` never puts `stake_type_uid` on an unstake-side
        item -- `{"item_uid": ...}` is the ONLY shape a real Power Core/Totem unstake ever
        takes. Found live 2026-08-26 wiring this allowlist into the browser signing path for
        the first time: `_resolve_path` resolved the absent key to `None`, which then failed
        the 'in' check on every real unstake -- the rule's own "(when present)" label was
        never actually vacuous. `test_permits_totem_in_unstake_items` above never caught this
        because it hand-set an explicit `stake_type_uid` no real op ever carries."""
        ops = clean_land_ops()
        ops[0].json_payload["unstake"]["items"] = [{"item_uid": "I-1"}]
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertTrue(result["ok"], result["violations"])

    def test_permits_title_in_stake_items(self):
        """the internal rules's 2026-08-27 ruling ("titles must not be locked ... behave like all
        other assets") -- Title's stake and unstake payloads are both observed (an isolated
        Title-only swap, Posting tier), the same evidentiary footing as Totem/Power Core, so
        the allowlist must permit it in `stake.items[*]` exactly as it does STK-LND-PCR/
        STK-LND-TOT above. Inverts the old `test_refuses_title_in_stake_items`, which this
        ruling supersedes. MUTATION: reverting `stake.items[*]`'s allowed value list in
        `fides_land/render.py` from `["STK-LND-PCR", "STK-LND-TOT", "STK-LND-TTL"]` back to
        `["STK-LND-PCR", "STK-LND-TOT"]` turns this red with `result["ok"] is False` and a
        `stake.items` violation naming STK-LND-TTL -- confirmed live, see dispatch report."""
        ops = clean_land_ops()
        ops[1].json_payload["stake"]["items"] = [{"item_uid": "I-1", "stake_type_uid": "STK-LND-TTL"}]
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertTrue(result["ok"], result["violations"])

    def test_permits_runi_in_stake_cards(self):
        """ finding 3: `ops.py` legitimately emits STK-LND-RUNI in `stake.cards[*]`
        (its payload is now OBSERVED both directions), but the allowlist pinned
        `stake.cards[*]` to STK-LND-WKR only -- a real, valid Runi stake bundle was
        refused. The allowlist must permit exactly what the emitter now produces."""
        ops = clean_land_ops()
        ops[1].json_payload["stake"]["cards"][0]["stake_type_uid"] = "STK-LND-RUNI"
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertTrue(result["ok"], result["violations"])

    def test_refuses_title_in_stake_cards(self):
        # Title lives in items[], the same container as Totem/Power Core -- never cards[]
        # which is reserved for STK-LND-WKR/STK-LND-RUNI. This is a WRONG-CONTAINER refusal
        # not a Title-specific lock: the 2026-08-27 ruling makes Title behave like every
        # other asset, and "every other item-class asset" is refused here too if misplaced
        # into cards[] (see test_refuses_wrong_stake_type_uid above for STK-LND-TOT in the
        # same slot). Superseded reasoning ("cooldown DURATION... must stay refused") is
        # retracted -- a cooldown-timing warning is not a payload-shape gate, see
        # test_permits_title_in_stake_items.
        ops = clean_land_ops()
        ops[1].json_payload["stake"]["cards"][0]["stake_type_uid"] = "STK-LND-TTL"
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertFalse(result["ok"])
        self.assertTrue(any("STK-LND-WKR" in v for v in result["violations"]))

    def test_refuses_runi_in_unstake_items(self):
        ops = clean_land_ops()
        ops[0].json_payload["unstake"]["items"] = [{"item_uid": "I-1", "stake_type_uid": "STK-LND-RUNI"}]
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertFalse(result["ok"])
        self.assertTrue(any("unstake.items" in v for v in result["violations"]))

    def test_refuses_combine_family_with_land_payload(self):
        # The registered combine family must not accept a different operation shape.
        ops = clean_land_ops()
        ops[0].operation_id = "sm_combine_cards"
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertFalse(result["ok"])
        self.assertTrue(any("combine cards" in v for v in result["violations"]))

    def test_refuses_auth_tier_mismatch_within_known_family(self):
        # Auth tier is read from the op itself, not inferred from the op id: a Posting-family op
        # carrying an Active-shaped auth set must be refused even though its id is recognized.
        ops = clean_land_ops()
        ops[1].required_auths = ["player0005"]
        ops[1].required_posting_auths = []
        result = evaluate_allowlist(build_clean_bundle(ops))
        self.assertFalse(result["ok"])
        self.assertTrue(any("auth tier must be" in v for v in result["violations"]))

    def test_refuses_account_mismatch(self):
        ops = clean_land_ops()
        ops[0].required_posting_auths = ["someoneelse"]
        # account is derived from ops -- force a mixed-account bundle by making one op differ
        # from the header's derived account (built from the OTHER op still saying player0005)
        with self.assertRaises(ValueError):
            build_clean_bundle(ops)  # required_account() itself refuses mixed-account batches

    def test_refuses_account_mismatch_via_tampered_bundle_not_construction(self):
        # simulate a bundle that WAS built cleanly, then had its account field tampered after
        # the fact (the realistic tamper scenario the allowlist defends against)
        bundle = build_clean_bundle()
        bundle["header"]["account"] = "attacker"
        result = evaluate_allowlist(bundle)
        self.assertFalse(result["ok"])
        self.assertTrue(any("account" in v for v in result["violations"]))


class RefusalSetIsEmptyTests(unittest.TestCase):
    """POSITIVE guard, item 3 of the 2026-08-27 dispatch brief ("titles must not be locked
    ... Make 100% of all assets and workers unlockable at any state without exceptions"):
    every stake type Ceres can address must survive a full intent -> op -> render allowlist
    round trip, in BOTH directions, with no payload-shape refusal anywhere in the chain.
    This is the union of the individual per-type tests scattered across `test_render.py`,
    `test_intent.py` and `test_waves.py` -- each of those proves ONE type in ONE module;
    this proves ALL FIVE in ONE round trip through the real emitter and the real allowlist
    together, so a type that passes each module in isolation but breaks when chained (e.g.
    a shape one module emits that another's allowlist doesn't recognise) cannot hide."""

    ALL_FIVE_STAKE_TYPES = (STAKE_TYPE_WORKER, STAKE_TYPE_RUNI, STAKE_TYPE_POWER_CORE, STAKE_TYPE_TOTEM, STAKE_TYPE_TITLE)

    def test_all_five_stake_types_survive_intent_to_op_to_render_round_trip(self):
        """MUTATION: putting `STAKE_TYPE_TITLE` back into `fides_land/ops.py`'s
        `_UNVERIFIED_STAKE_TYPES` (its pre-2026-08-27 value) turns this red on both Title
        subtests with `fides_land.ops.LandOpError: stake type 'STK-LND-TTL' has payload
        shape UNVERIFIED`, caught below and reported by name rather than aborting the whole
        test -- confirmed live, see dispatch report.

        Also currently RED on real master for the OTHER reason recorded throughout this
        dispatch (a gap outside `tests/`, not this injection): `fides_land/ops.py:59`'s
        `_ITEM_STAKE_TYPES` frozenset was never given STAKE_TYPE_TITLE, so both Title
        subtests fail with `LandOpError: unknown stake_type_uid 'STK-LND-TTL'`. Verified
        locally that adding STAKE_TYPE_TITLE to `_ITEM_STAKE_TYPES` turns both Title
        subtests (and this whole test) green; reverted after confirming."""
        refused = []
        for stake_type in self.ALL_FIVE_STAKE_TYPES:
            for direction in ("stake", "unstake"):
                with self.subTest(stake_type=stake_type, direction=direction):
                    slot = 1 if stake_type == STAKE_TYPE_WORKER and direction == "stake" else None
                    intent = StakeIntent("deed-A", stake_type, direction, "ASSET-001", None, slot)
                    try:
                        op = intents_to_ops([intent], account="player0005", app=TEST_APP)[0]
                        bundle = build_clean_bundle([op])
                        result = evaluate_allowlist(bundle)
                        if not result["ok"]:
                            refused.append((stake_type, direction, "allowlist", result["violations"]))
                    except LandOpError as exc:
                        refused.append((stake_type, direction, "intents_to_ops", str(exc)))
        self.assertEqual(refused, [], f"stake type(s) refused for want of a verified payload shape: {refused!r}")

    def test_the_refusal_set_itself_is_empty(self):
        """Direct check on the constant this whole ruling emptied -- the two guards above
        prove the BEHAVIOUR; this proves the DATA it comes from, so a future edit that
        re-populates the set is caught even before it changes any observable op result."""
        from fides_land.ops import _UNVERIFIED_STAKE_TYPES
        self.assertEqual(_UNVERIFIED_STAKE_TYPES, frozenset())


if __name__ == "__main__":
    unittest.main()
