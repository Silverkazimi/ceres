import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from praetoria.models import QuerySpec
from praetoria.query import run_query
from praetoria.sets import VERICO_IDS, resolve_universe


def detail(card_id, name, editions="21"):
    return {"id": card_id, "name": name, "editions": editions, "rarity": 1, "type": "Monster"}


def card(uid, card_id, bcx, gold=False, stake_ref_uid=None):
    return {
        "uid": uid,
        "card_detail_id": card_id,
        "xp": 1,
        "bcx": bcx,
        "gold": gold,
        "edition": 21,
        "card_set": "land",
        "collection_power": bcx * 5,
        "stake_ref_uid": stake_ref_uid,
        "stake_start_date": "2026-01-01T00:00:00Z" if stake_ref_uid else None,
        "stake_end_date": None,
    }


class FakeResult:
    def __init__(self, data):
        self.data = data
        self.retrieved_at = "2026-08-07T00:00:00Z"


class FakeClient:
    def __init__(self, cards, details):
        self.cards = cards
        self.details_data = details

    def collection(self, player):
        return FakeResult({"player": player, "cards": self.cards})

    def details(self):
        return FakeResult(self.details_data)


class PraetoriaTests(unittest.TestCase):
    def test_verico_universe_is_exactly_54_ids(self):
        details = [detail(card_id, f"Card {card_id}") for card_id in VERICO_IDS]
        universe, resolution, warnings = resolve_universe(details, QuerySpec("test", set_name="verico", include_zero=True), [])
        self.assertEqual([item["id"] for item in universe], list(VERICO_IDS))
        self.assertEqual(resolution["count"], 54)
        self.assertTrue(warnings)

    def test_aggregation_uses_bcx_not_xp_and_includes_zero_rows(self):
        details = [detail(985, "Mystery 1"), detail(986, "Sunlit Hart"), *[detail(i, f"Card {i}") for i in range(987, 1039)]]
        cards = [card("a", 987, 30), card("b", 987, 70), card("c", 988, 400, gold=True)]
        with tempfile.TemporaryDirectory() as directory:
            result = run_query(QuerySpec("test", set_name="verico", include_zero=True, as_of=datetime(2026, 8, 7, tzinfo=timezone.utc)), FakeClient(cards, details), __import__("praetoria.cache", fromlist=["JsonCache"]).JsonCache(Path(directory)))
        self.assertEqual(result["summary"]["total_bcx"], 500)
        self.assertEqual(result["summary"]["matched_instances"], 3)
        self.assertEqual(result["summary"]["matched_distinct_cards"], 2)
        self.assertEqual(len(result["cards"]), 54)
        self.assertEqual(next(row for row in result["cards"] if row["card_detail_id"] == 985)["name"], "Mystery 1")

    def test_duplicate_uid_is_rejected(self):
        details = [detail(i, f"Card {i}") for i in VERICO_IDS]
        cards = [card("same", 987, 30), card("same", 987, 30)]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Exception):
                run_query(QuerySpec("test", set_name="verico"), FakeClient(cards, details), __import__("praetoria.cache", fromlist=["JsonCache"]).JsonCache(Path(directory)))


if __name__ == "__main__":
    unittest.main()
