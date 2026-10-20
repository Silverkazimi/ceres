import unittest

from fides_land import ops as land_ops
from fides_land.slots import assign_slots
from tests.fides_land.fixtures import backfill_row, card, evicted, move, plan_dict



TEST_APP = "ceres/0.0.0"

def _worked_example_plan():
    """The plan §3.2 worked example: move card X (staked at S) -> D, evicting E (staked at
    D); backfill idle card Y -> S."""
    x = card("X", origin="staked", asset_ref="ref-x", card_uid="UID-X", source_uid="deed-S")
    e = evicted("E", asset_ref="ref-e", card_uid="UID-E")
    y = card("Y", origin="idle", bucket="unstaked_apparent", asset_ref="ref-y", card_uid="UID-Y")
    return plan_dict(
        moves=[move(x, destination_uid="deed-D", evicted_row=e)],
        backfill=[backfill_row(y, plot_uid="deed-S")],
    )


def _assignments():
    return []  # no prior occupants in this fixture -- both deeds start with free slot 1


def _capacity():
    return {"deed-S": 5, "deed-D": 4}


class OpsWorkedExampleTests(unittest.TestCase):
    def test_worked_example_produces_expected_op_shape(self):
        plan = _worked_example_plan()
        ops = land_ops.plan_to_ops(plan, account="player0005", app=TEST_APP, assignments=_assignments(), capacity_by_deed=_capacity())
        by_deed = {op.json_payload["deed_uid"]: op for op in ops}
        # deed S: unstake X, stake Y (per §3.2's worked example: two separate ops)
        s_ops = [op for op in ops if op.json_payload["deed_uid"] == "deed-S"]
        self.assertEqual(len(s_ops), 2)
        unstake_s = next(op for op in s_ops if op.json_payload["unstake"]["cards"])
        stake_s = next(op for op in s_ops if op.json_payload["stake"]["cards"])
        self.assertEqual([c["card_uid"] for c in unstake_s.json_payload["unstake"]["cards"]], ["UID-X"])
        self.assertEqual([c["card_uid"] for c in stake_s.json_payload["stake"]["cards"]], ["UID-Y"])
        # deed D: unstake E, stake X
        d_ops = [op for op in ops if op.json_payload["deed_uid"] == "deed-D"]
        self.assertEqual(len(d_ops), 2)
        unstake_d = next(op for op in d_ops if op.json_payload["unstake"]["cards"])
        stake_d = next(op for op in d_ops if op.json_payload["stake"]["cards"])
        self.assertEqual([c["card_uid"] for c in unstake_d.json_payload["unstake"]["cards"]], ["UID-E"])
        self.assertEqual([c["card_uid"] for c in stake_d.json_payload["stake"]["cards"]], ["UID-X"])


class OpsAttemptedViolationTests(unittest.TestCase):
    """Each test forces the property the plan's Phase 3 acceptance criteria named, and checks
    the implementation actually refuses/never produces the violating shape -- not just that
    the happy path looks right."""

    def test_to_ops_rejects_third_party_app_identifier(self):
        with self.assertRaisesRegex(land_ops.LandOpError, "third-party app identifier"):
            land_ops.to_ops({"deed-S": [{"card_uid": "UID-X", "slot": 1}]}, {}, account="player0005", app="peakmonsters")

    def test_no_op_has_both_sides_populated(self):
        plan = _worked_example_plan()
        ops = land_ops.plan_to_ops(plan, account="player0005", app=TEST_APP, assignments=_assignments(), capacity_by_deed=_capacity())
        for op in ops:
            has_stake = bool(op.json_payload["stake"]["cards"])
            has_unstake = bool(op.json_payload["unstake"]["cards"])
            self.assertFalse(has_stake and has_unstake, f"op has both sides populated: {op.json_payload}")

    def test_no_operation_id_other_than_sm_stake_change(self):
        plan = _worked_example_plan()
        ops = land_ops.plan_to_ops(plan, account="player0005", app=TEST_APP, assignments=_assignments(), capacity_by_deed=_capacity())
        for op in ops:
            self.assertEqual(op.operation_id, "sm_stake_change")

    def test_no_stake_type_uid_other_than_worker_is_ever_accepted(self):
        stake, unstake = land_ops.build_stake_unstake_maps(_worked_example_plan())
        assign_slots(stake, unstake, _assignments(), _capacity())
        # tamper: force a non-worker stake_type_uid into one entry
        for cards in stake.values():
            if cards:
                cards[0]["stake_type_uid"] = "STK-LND-TOT"
                break
        with self.assertRaises(land_ops.LandOpError):
            land_ops.to_ops(stake, unstake, account="player0005", app=TEST_APP)

    def test_no_items_entry_ever(self):
        plan = _worked_example_plan()
        ops = land_ops.plan_to_ops(plan, account="player0005", app=TEST_APP, assignments=_assignments(), capacity_by_deed=_capacity())
        for op in ops:
            self.assertEqual(op.json_payload["stake"]["items"], [])
            self.assertEqual(op.json_payload["unstake"]["items"], [])

    def test_every_card_uid_in_every_op_traceable_to_source_plan(self):
        plan = _worked_example_plan()
        ops = land_ops.plan_to_ops(plan, account="player0005", app=TEST_APP, assignments=_assignments(), capacity_by_deed=_capacity())
        source_uids = {"UID-X", "UID-E", "UID-Y"}
        emitted_uids = set()
        for op in ops:
            emitted_uids.update(c["card_uid"] for c in op.json_payload["stake"]["cards"])
            emitted_uids.update(c["card_uid"] for c in op.json_payload["unstake"]["cards"])
        self.assertTrue(emitted_uids.issubset(source_uids), f"emitted uids not traceable to plan: {emitted_uids - source_uids}")

    def test_chunking_never_exceeds_op_or_byte_caps(self):
        from fides.op import chunk_ops
        moves = [move(card(f"C{i}", origin="idle", bucket="unstaked_apparent", asset_ref=f"ref-{i}", card_uid=f"UID-{i}"), destination_uid=f"deed-{i}") for i in range(20)]
        plan = plan_dict(moves=moves)
        capacity = {f"deed-{i}": 5 for i in range(20)}
        ops = land_ops.plan_to_ops(plan, account="player0005", app=TEST_APP, assignments=[], capacity_by_deed=capacity)
        chunks = chunk_ops(ops, max_ops_per_tx=8, max_bytes_per_tx=6 * 1024)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 8)
            total = 2 + sum(op.serialized_bytes() for op in chunk) + max(0, len(chunk) - 1)
            self.assertLessEqual(total, 6 * 1024)

    def test_missing_card_uid_raises_rather_than_partial_emitting(self):
        # a plan captured without --include-asset-ids has no card_uid at all
        x = card("X", origin="idle", bucket="unstaked_apparent", asset_ref="ref-x", card_uid=None)
        plan = plan_dict(moves=[move(x, destination_uid="deed-D")])
        with self.assertRaises(land_ops.LandOpError):
            land_ops.plan_to_ops(plan, account="player0005", app=TEST_APP, assignments=[], capacity_by_deed={"deed-D": 5})

    def test_missing_card_uid_on_eviction_raises(self):
        x = card("X", origin="idle", bucket="unstaked_apparent", asset_ref="ref-x", card_uid="UID-X")
        e = evicted("E", asset_ref="ref-e", card_uid=None)
        plan = plan_dict(moves=[move(x, destination_uid="deed-D", evicted_row=e)])
        with self.assertRaises(land_ops.LandOpError):
            land_ops.plan_to_ops(plan, account="player0005", app=TEST_APP, assignments=[], capacity_by_deed={"deed-D": 5})

    def test_ops_sorted_by_deed_uid_for_determinism(self):
        moves = [
            move(card("A", origin="idle", bucket="unstaked_apparent", asset_ref="ra", card_uid="UA"), destination_uid="deed-Z"),
            move(card("B", origin="idle", bucket="unstaked_apparent", asset_ref="rb", card_uid="UB"), destination_uid="deed-A"),
        ]
        plan = plan_dict(moves=moves)
        ops = land_ops.plan_to_ops(plan, account="player0005", app=TEST_APP, assignments=[], capacity_by_deed={"deed-Z": 5, "deed-A": 5})
        deeds_in_order = [op.json_payload["deed_uid"] for op in ops]
        self.assertEqual(deeds_in_order, sorted(deeds_in_order))


if __name__ == "__main__":
    unittest.main()
