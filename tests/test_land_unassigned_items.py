"""Unassigned/spare bonus-item capture (Ceres board, 2026-08-21) -- Source A of the
"Unassigned bonus assets" section: a real fetch against
/land/stake/items/{code}/available, account-scoped (verified live against two
different owned deeds returning identical results, 2026-08-21). All offline --
LandCapture is wired to fake land_client/collection_client objects, never real
network I/O. Fixtures reproduce the exact P0001-wrapped-in-200 failure shape so the
distinguishing rule (`available_item_ids`) is actually exercised, not merely assumed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from praetoria.cache import JsonCache
from praetoria.errors import UpstreamError
from praetoria.land_analysis import (
    AVAILABLE_PAGE_SIZE,
    LandCapture,
    UNASSIGNED_ITEM_FAMILY,
    item_detail_id_from_uid,
)
from praetoria.transport import ResponseData

ROOT = Path(__file__).parent / "fixtures" / "land"
# Isolated cache dir for this module's fake LandCapture instances. Bug found while
# verifying Ceres's preflight/estimate feature (2026-08-21): `make_capture()` never
# passed a `cache=`, so LandCapture fell back to its real default
# (`~/.praetoria/cache`) and every test run here overwrote the SHARED, real
# `land_deeds_player0005` cache entry with this file's one-fake-deed fixture -- corrupting
# it for anything that reads that cache with a positive ttl (Ceres's new
# `preflight_deed_count`), even though no test here performs actual network I/O.
_CACHE_DIR = tempfile.mkdtemp(prefix="ceres-test-unassigned-cache-")
AS_OF = datetime(2026, 8, 9, tzinfo=timezone.utc)


def fixture(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _response(data):
    return ResponseData(data=data, retrieved_at="2026-08-21T00:00:00Z", attempts=1)


class FakeLandClient:
    """Duck-types LandClient.bundle/assets/available_raw against canned fixtures.
    `available_calls` records every (stake_type_uid, deed_uid, offset) triple so
    tests can assert pagination and deed-anchoring behavior without a network."""

    def __init__(self, deed_uids, available_pages):
        self.deed_uids = deed_uids
        self.available_pages = available_pages  # {stake_type_uid: [payload, payload, ...]}
        self.available_calls: list[tuple[str, str, int]] = []

    def bundle(self, player):
        deeds = [{"deed_uid": uid} for uid in self.deed_uids]
        return _response({"status": "success", "data": {"deeds": deeds, "worksite_details": [], "staking_details": []}})

    def assets(self, deed_uid):
        return _response({"status": "success", "data": {"cards": [], "items": []}})

    def available_raw(self, stake_type_uid, deed_uid, player, offset=0, limit=5):
        self.available_calls.append((stake_type_uid, deed_uid, offset))
        pages = self.available_pages.get(stake_type_uid, [])
        index = offset // AVAILABLE_PAGE_SIZE
        if index >= len(pages):
            return _response({"status": "success", "data": {"ids": []}})
        return _response(pages[index])


class FakeCollectionClient:
    def collection(self, player):
        return _response({"player": player, "cards": []})


def make_capture(deed_uids, available_pages):
    return LandCapture(
        land_client=FakeLandClient(deed_uids, available_pages),
        collection_client=FakeCollectionClient(),
        cache=JsonCache(Path(_CACHE_DIR)),
        min_delay=0.0,
    )


class UnassignedItemsTests(unittest.TestCase):
    def test_item_detail_id_from_uid_parses_the_embedded_id(self):
        self.assertEqual(item_detail_id_from_uid("I-318-2TJ9K58LCW"), 318)
        self.assertEqual(item_detail_id_from_uid({"uid": "I-322-abc"}["uid"]), 322)
        self.assertIsNone(item_detail_id_from_uid("not-a-uid"))
        self.assertIsNone(item_detail_id_from_uid(None))

    def test_fetched_families_and_the_declared_unsupported_one_are_distinguished(self):
        pages = {
            "STK-LND-TOT": [fixture("available_totem_candidates.json")["data"]],
            "STK-LND-TTL": [fixture("available_title_empty.json")["data"]],
            "STK-LND-PCR": [fixture("available_power_core_candidates.json")["data"]],
            "STK-LND-RUNI": [fixture("available_runi_body_error_p0001.json")["data"]],
        }
        # The fake transport returns raw `data`, but available_raw()'s caller
        # (LandCapture) expects the full envelope via _cached -> available_item_ids
        # which reads .get("status")/.get("data") off the whole payload -- wrap back.
        wrapped = {k: [{"status": "success", "data": p} for p in v] for k, v in pages.items()}
        capture = make_capture(["I-1-aaaa"], wrapped)
        report = capture.capture("player0005", as_of=AS_OF)
        unassigned = report["unassigned_items"]

        self.assertTrue(unassigned["totem"]["capturable"])
        self.assertEqual(unassigned["totem"]["count"], 5)
        self.assertEqual({i["item_detail_id"] for i in unassigned["totem"]["items"]}, {318})
        self.assertEqual(unassigned["totem"]["items"][0]["count"], 5)

        self.assertTrue(unassigned["title"]["capturable"])
        self.assertEqual(unassigned["title"]["count"], 0)
        self.assertEqual(unassigned["title"]["items"], [])

        self.assertTrue(unassigned["power_core"]["capturable"])
        self.assertEqual(unassigned["power_core"]["count"], 5)

        # . Semantic: Runi has no unassigned-item source upstream, so the question is
        # not asked at all -- and the answer still says so rather than showing an empty list.
        # Mutation: put "STK-LND-RUNI" back into UNASSIGNED_ITEM_FAMILY -- the call reappears
        # in available_calls and this goes red. Input: a transport that WOULD answer Runi with
        # the documented P0001-wrapped-in-200 body, which must go untouched.
        self.assertEqual([c for c in capture.land_client.available_calls if c[0] == "STK-LND-RUNI"], [])
        self.assertFalse(unassigned["runi"]["capturable"])
        self.assertIsNone(unassigned["runi"]["count"])
        self.assertIn("no unassigned-item source", unassigned["runi"]["reason"])

    def test_pagination_continues_until_a_short_page(self):
        full_page = {"status": "success", "data": {"ids": [{"uid": f"I-318-{i:04d}"} for i in range(AVAILABLE_PAGE_SIZE)]}}
        short_page = {"status": "success", "data": {"ids": [{"uid": "I-318-9999"}]}}
        pages = {
            "STK-LND-TOT": [full_page, short_page],
            "STK-LND-TTL": [{"status": "success", "data": {"ids": []}}],
            "STK-LND-PCR": [{"status": "success", "data": {"ids": []}}],
            "STK-LND-RUNI": [fixture("available_runi_body_error_p0001.json")],
        }
        capture = make_capture(["I-1-aaaa"], pages)
        report = capture.capture("player0005", as_of=AS_OF)
        unassigned = report["unassigned_items"]
        self.assertEqual(unassigned["totem"]["count"], AVAILABLE_PAGE_SIZE + 1)
        offsets = [c[2] for c in capture.land_client.available_calls if c[0] == "STK-LND-TOT"]
        self.assertEqual(offsets, [0, AVAILABLE_PAGE_SIZE])

    def test_no_owned_deed_marks_every_family_uncapturable(self):
        capture = make_capture([], {})
        report = capture.capture("player0005", as_of=AS_OF)
        unassigned = report["unassigned_items"]
        for family in UNASSIGNED_ITEM_FAMILY.values():
            self.assertFalse(unassigned[family]["capturable"])
            self.assertIn("no owned deed", unassigned[family]["reason"])
        # . Semantic: the declared-unsupported family must appear in THIS branch too
        # with its own reason -- the loop above walks the FETCHED families only, so without
        # this the early return could drop Runi entirely and no test would notice.
        # Mutation: move the UNSUPPORTED_ITEM_FAMILY seeding below the `if not deed_rows`
        # early return -- KeyError here, red.
        self.assertFalse(unassigned["runi"]["capturable"])
        self.assertIn("no unassigned-item source", unassigned["runi"]["reason"])

    def test_item_names_resolve_from_already_captured_assigned_bonus_assets(self):
        # An unknown catalogue ID remains identifiable by its item family and ID.
        pages = {
            "STK-LND-TOT": [{"status": "success", "data": {"ids": [{"uid": "I-999-zzzz"}]}}],
            "STK-LND-TTL": [{"status": "success", "data": {"ids": []}}],
            "STK-LND-PCR": [{"status": "success", "data": {"ids": []}}],
            "STK-LND-RUNI": [fixture("available_runi_body_error_p0001.json")],
        }
        capture = make_capture(["I-1-aaaa"], pages)
        report = capture.capture("player0005", as_of=AS_OF)
        item = report["unassigned_items"]["totem"]["items"][0]
        self.assertEqual(item["item_detail_id"], 999)
        self.assertEqual(item["name"], "Totem item 999")


if __name__ == "__main__":
    unittest.main()
