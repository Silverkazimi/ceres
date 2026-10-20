from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from praetoria.cache import JsonCache
from praetoria.land_analysis import (
    AVAILABLE_PAGE_SIZE,
    MAX_UNASSIGNED_PAGES,
    LandCapture,
)
from praetoria.transport import ResponseData


class AvailableStub:
    def __init__(self, full_pages: set[str] | None = None, short_ids: list[str] | None = None):
        self.full_pages = full_pages or set()
        self.short_ids = short_ids or []
        self.calls: list[tuple[str, int, int]] = []

    def available_raw(self, stake_type_uid, deed_uid, player, offset=0, limit=5):
        self.calls.append((stake_type_uid, offset, limit))
        if stake_type_uid in self.full_pages:
            ids = [{"uid": f"I-318-{offset + index}"} for index in range(AVAILABLE_PAGE_SIZE)]
        elif offset == 0 and stake_type_uid == "STK-LND-TOT":
            ids = [{"uid": uid} for uid in self.short_ids]
        else:
            ids = []
        return ResponseData(
            data={"status": "success", "data": {"ids": ids}},
            retrieved_at="2026-09-25T00:00:00Z",
            attempts=1,
        )


class UnassignedPageCapTests(unittest.TestCase):
    def make_capture(self, stub):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        capture = LandCapture(
            land_client=stub,
            cache=JsonCache(Path(temporary.name)),
            min_delay=0,
            concurrency=1,
        )
        capture.persist_cache = lambda key: False
        return capture

    def test_stops_at_cap_and_reports_truncation(self):
        stub = AvailableStub(full_pages={"STK-LND-TOT"})
        capture = self.make_capture(stub)

        result = capture._capture_unassigned_items(
            [{"deed_uid": "I-1-anchor"}], "player", False, {}, False,
        )["totem"]

        self.assertEqual(len([call for call in stub.calls if call[0] == "STK-LND-TOT"]), MAX_UNASSIGNED_PAGES)
        self.assertEqual(result["count"], AVAILABLE_PAGE_SIZE * MAX_UNASSIGNED_PAGES)
        self.assertEqual(result["pages_fetched"], MAX_UNASSIGNED_PAGES)
        self.assertTrue(result["page_cap_hit"])
        self.assertTrue(any("may be truncated" in warning for warning in result["warnings"]))

    def test_short_listing_keeps_all_items_and_is_not_truncated(self):
        stub = AvailableStub(short_ids=["I-318-one", "I-318-two"])
        capture = self.make_capture(stub)

        result = capture._capture_unassigned_items(
            [{"deed_uid": "I-1-anchor"}], "player", False, {}, True,
        )["totem"]

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["uids"], ["I-318-one", "I-318-two"])
        self.assertEqual(result["pages_fetched"], 1)
        self.assertFalse(result["page_cap_hit"])
        self.assertNotIn("warnings", result)


if __name__ == "__main__":
    unittest.main()
