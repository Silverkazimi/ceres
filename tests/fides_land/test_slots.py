import unittest

from fides_land.slots import SlotAssignmentError, assign_slots, observed_worker_slots


class SlotsTests(unittest.TestCase):
    def test_lowest_free_integer_assignment(self):
        stake = {"deed-1": [{"asset_ref": "r1", "card_uid": "U1"}, {"asset_ref": "r2", "card_uid": "U2"}]}
        assign_slots(stake, {}, [], {"deed-1": 5})
        self.assertEqual(sorted(c["slot"] for c in stake["deed-1"]), [1, 2])

    def test_skips_occupied_slots(self):
        stake = {"deed-1": [{"asset_ref": "new", "card_uid": "UNEW"}]}
        assignments = [
            {"asset_ref": "existing1", "deed_uid": "deed-1", "slot": 1, "asset_kind": "worker_card"},
            {"asset_ref": "existing2", "deed_uid": "deed-1", "slot": 2, "asset_kind": "worker_card"},
        ]
        assign_slots(stake, {}, assignments, {"deed-1": 5})
        self.assertEqual(stake["deed-1"][0]["slot"], 3)

    def test_vacated_slot_from_this_batchs_own_unstake_becomes_available(self):
        stake = {"deed-1": [{"asset_ref": "new", "card_uid": "UNEW"}]}
        unstake = {"deed-1": [{"asset_ref": "existing1", "card_uid": "UOLD"}]}
        assignments = [{"asset_ref": "existing1", "deed_uid": "deed-1", "slot": 1, "asset_kind": "worker_card"}]
        assign_slots(stake, unstake, assignments, {"deed-1": 5})
        self.assertEqual(stake["deed-1"][0]["slot"], 1)  # vacated by this batch's own unstake

    def test_runi_slot_0_excluded_from_worker_bookkeeping(self):
        stake = {"deed-1": [{"asset_ref": "new", "card_uid": "UNEW"}]}
        assignments = [{"asset_ref": "runi1", "deed_uid": "deed-1", "slot": 0, "asset_kind": "runi_worker_card"}]
        assign_slots(stake, {}, assignments, {"deed-1": 5})
        self.assertEqual(stake["deed-1"][0]["slot"], 1)  # not pushed to slot 1 avoidance by Runi's slot 0

    def test_raises_when_deed_over_capacity(self):
        stake = {"deed-1": [{"asset_ref": f"r{i}", "card_uid": f"U{i}"} for i in range(3)]}
        assignments = [{"asset_ref": "existing1", "deed_uid": "deed-1", "slot": 1, "asset_kind": "worker_card"}]
        with self.assertRaises(SlotAssignmentError):
            assign_slots(stake, {}, assignments, {"deed-1": 3})  # 1 existing + 3 new > capacity 3

    def test_raises_when_capacity_unknown(self):
        stake = {"deed-1": [{"asset_ref": "r1", "card_uid": "U1"}]}
        with self.assertRaises(SlotAssignmentError):
            assign_slots(stake, {}, [], {})

    def test_deterministic_order_by_asset_ref(self):
        stake = {"deed-1": [{"asset_ref": "zzz", "card_uid": "UZ"}, {"asset_ref": "aaa", "card_uid": "UA"}]}
        assign_slots(stake, {}, [], {"deed-1": 5})
        by_ref = {c["asset_ref"]: c["slot"] for c in stake["deed-1"]}
        self.assertEqual(by_ref["aaa"], 1)
        self.assertEqual(by_ref["zzz"], 2)

    def test_observed_worker_slots_excludes_non_worker_kinds(self):
        assignments = [
            {"asset_ref": "w1", "deed_uid": "d1", "slot": 1, "asset_kind": "worker_card"},
            {"asset_ref": "r1", "deed_uid": "d1", "slot": 0, "asset_kind": "runi_worker_card"},
            {"asset_ref": "i1", "deed_uid": "d1", "slot": None, "asset_kind": "power_core"},
        ]
        out = observed_worker_slots(assignments)
        self.assertEqual(out, {"d1": {"w1": 1}})


if __name__ == "__main__":
    unittest.main()
