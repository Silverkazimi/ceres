"""Whale-capture resumability (Ceres feature, 2026-08-21) -- `LandCapture.capture`'s
`resume`/`resume_ttl` and `preflight_deed_count`. All offline: fake `land_client`/
`collection_client`, real `JsonCache` pointed at a tmp dir so cache hits are genuine
on-disk reads, never network I/O.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from praetoria.cache import JsonCache
from praetoria.land_analysis import LandCapture
from praetoria.transport import ResponseData

AS_OF = datetime(2026, 8, 9, tzinfo=timezone.utc)


def _response(data):
    return ResponseData(data=data, retrieved_at="2026-08-21T00:00:00Z", attempts=1)


class CountingLandClient:
    """Duck-types LandClient.bundle/assets/owned_deed_counts. Counts every call so
    tests can prove a resumed capture skips deeds already served from cache."""

    def __init__(self, deed_uids, fail_assets_for=()):
        self.deed_uids = deed_uids
        self.fail_assets_for = set(fail_assets_for)
        self.bundle_calls = 0
        self.owned_deed_counts_calls = 0
        self.assets_calls: list[str] = []

    def bundle(self, player):
        self.bundle_calls += 1
        deeds = [{"deed_uid": uid} for uid in self.deed_uids]
        return _response({"status": "success", "data": {"deeds": deeds, "worksite_details": [], "staking_details": []}})

    def owned_deed_counts(self, player):
        # whale-account performance batch: preflight_deed_count() now calls
        # THIS (a ~150-byte real endpoint), not bundle() -- see that method's own
        # docstring for why. One fake region carrying the whole count is enough to
        # exercise the sum-over-rows logic.
        self.owned_deed_counts_calls += 1
        return _response({"status": "success", "data": [{"count": len(self.deed_uids), "uid": "PR-TEST-1"}]})

    def assets(self, deed_uid):
        self.assets_calls.append(deed_uid)
        if deed_uid in self.fail_assets_for:
            raise RuntimeError(f"simulated interruption fetching {deed_uid}")
        return _response({"status": "success", "data": {"cards": [], "items": []}})

    def available_raw(self, stake_type_uid, deed_uid, player, offset=0, limit=5):
        return _response({"status": "success", "data": {"ids": []}})


class FakeCollectionClient:
    def __init__(self, cards=None, fail=False):
        self.calls = []
        self.cards = cards or []
        self.fail = fail

    def collection(self, player):
        self.calls.append(player)
        if self.fail:
            raise RuntimeError("simulated collection failure")
        return _response({"player": player, "cards": self.cards})


class ResumeTests(unittest.TestCase):
    def _capture(self, land_client, cache_dir, collection_client=None):
        return LandCapture(
            land_client=land_client,
            collection_client=collection_client or FakeCollectionClient(),
            cache=JsonCache(Path(cache_dir)),
            min_delay=0.0,
        )

    def test_preflight_uses_the_cheap_owned_endpoint_not_the_bundle(self):
        # Renamed and rewritten (whale-account performance batch) -- was
        # "test_preflight_then_capture_reuses_the_bundle_from_cache": preflight used
        # to fetch the FULL bundle (cached, so a following capture() could reuse it
        # never fetching the bundle twice). Superseded: preflight now calls the
        # ~150-byte owned_deed_counts() endpoint instead, so a following capture()
        # always does its own bundle fetch -- that's the correct trade-off, since
        # preflight is not always followed by a capture (showing a cost estimate
        # the user then declines is the exact case this change optimizes for), and
        # the bundle fetch was never saved on the CAPTURE side either way.
        with TemporaryDirectory() as tmp:
            land_client = CountingLandClient(["I-1-a", "I-1-b"])
            capture = self._capture(land_client, tmp)
            count = capture.preflight_deed_count("player0005")
            self.assertEqual(count, 2)
            self.assertEqual(land_client.owned_deed_counts_calls, 1)
            self.assertEqual(land_client.bundle_calls, 0, "preflight must not touch the full bundle at all")
            capture.capture("player0005", resume=True, as_of=AS_OF)
            # capture() fetches its own bundle -- preflight no longer pre-warms it.
            self.assertEqual(land_client.bundle_calls, 1)

    def test_interrupted_capture_resumes_from_cache_without_refetching(self):
        with TemporaryDirectory() as tmp:
            deed_uids = ["I-1-a", "I-1-b", "I-1-c"]
            # First run fails partway through the third deed -- simulates the process
            # dying mid-capture.
            failing_client = CountingLandClient(deed_uids, fail_assets_for=("I-1-c",))
            first = self._capture(failing_client, tmp)
            first.capture("player0005", resume=True, as_of=AS_OF)
            self.assertEqual(sorted(failing_client.assets_calls), ["I-1-a", "I-1-b", "I-1-c"])

            # Second run, brand-new LandCapture (same cache dir), where the client would
            # raise if asked to re-fetch ANY previously-successful deed -- proves resume
            # actually reads a-and-b from cache rather than hitting the network again.
            resumed_client = CountingLandClient(deed_uids, fail_assets_for=("I-1-a", "I-1-b"))
            second = self._capture(resumed_client, tmp)
            second.capture("player0005", resume=True, as_of=AS_OF)
            # a and b must NOT appear in this run's own network calls (they were
            # cache-served); only c, which genuinely failed last time, was refetched.
            self.assertNotIn("I-1-a", resumed_client.assets_calls)
            self.assertNotIn("I-1-b", resumed_client.assets_calls)
            self.assertIn("I-1-c", resumed_client.assets_calls)
            self.assertGreaterEqual(second._cache_hits, 2)

    def test_progress_callback_reports_every_deed(self):
        with TemporaryDirectory() as tmp:
            deed_uids = ["I-1-a", "I-1-b", "I-1-c"]
            land_client = CountingLandClient(deed_uids)
            capture = self._capture(land_client, tmp)
            calls: list[tuple[int, int]] = []
            capture.capture("player0005", as_of=AS_OF, progress=lambda fetched, total: calls.append((fetched, total)))
            self.assertEqual(calls, [(1, 3), (2, 3), (3, 3)])

    def test_resume_false_still_refetches_every_deed(self):
        """resume=True is opt-in -- the default (resume=False) preserves the original
        always-fetch behavior, so nothing else in Praetoria that relies on `capture()`
        always reflecting live data changes silently."""
        with TemporaryDirectory() as tmp:
            deed_uids = ["I-1-a", "I-1-b"]
            land_client = CountingLandClient(deed_uids)
            capture = self._capture(land_client, tmp)
            capture.capture("player0005", as_of=AS_OF)
            first_calls = list(land_client.assets_calls)
            capture.capture("player0005", as_of=AS_OF)
            # v0.9.27 (): the fan-out's connections come from a shared free-list, so
            # the ORDER two concurrent deeds are fetched in is a scheduling accident. The
            # guarantee under test is that every deed is fetched again -- compare as sets
            # with multiplicity, not as sequences.
            self.assertEqual(sorted(land_client.assets_calls), sorted(first_calls * 2))


class QuickCaptureTests(unittest.TestCase):
    """`capture_quick()` -- the bulk-only path (2026-08-21 correction: the board's main
    table needs no per-deed fan-out at all). Proves it never touches `assets()` and its
    cost stays flat regardless of deed count."""

    def _capture(self, land_client, cache_dir, collection_client=None):
        return LandCapture(
            land_client=land_client,
            collection_client=collection_client or FakeCollectionClient(),
            cache=JsonCache(Path(cache_dir)),
            min_delay=0.0,
        )

    def test_quick_capture_never_fetches_per_deed_assets(self):
        with TemporaryDirectory() as tmp:
            deed_uids = [f"I-1-{i}" for i in range(50)]
            land_client = CountingLandClient(deed_uids)
            capture = self._capture(land_client, tmp)
            report = capture.capture_quick("player0005", as_of=AS_OF)
            self.assertEqual(land_client.assets_calls, [])
            self.assertEqual(len(report["plots"]), 50)
            self.assertTrue(all(p["asset_fetch"]["status"] == "not_requested" for p in report["plots"]))
            self.assertEqual(report["workers"], [])
            self.assertEqual(report["capture"]["mode"], "quick")
            # Cost is flat -- bundle (1) + collection (1) + up to 4 unassigned-item family
            # pages, independent of deed count (50 here vs. a real whale's 6,303).
            self.assertLessEqual(report["capture"]["requests"], 6)

    def test_quick_capture_fetches_collection_and_marks_coverage_complete(self):
        """ regression: deleting the quick-path collection fetch made this
        assertion fail because the report contained no cards and
        `coverage.collection.complete` was false; the injected deletion was observed
        red before restoring the fetch."""
        with TemporaryDirectory() as tmp:
            collection_client = FakeCollectionClient(cards=[{"uid": "CARD-1", "card_detail_id": 1, "bcx": 1}])
            capture = self._capture(CountingLandClient(["I-1-a"]), tmp, collection_client)
            report = capture.capture_quick("player0005", include_asset_ids=True, as_of=AS_OF)
            self.assertEqual(collection_client.calls, ["player0005"])
            self.assertEqual(report["cards"][0]["uid"], "CARD-1")
            self.assertIs(report["coverage"]["collection"]["complete"], True)

    def test_quick_capture_uses_collection_roster_without_asset_fanout(self):
        """The two bulk reads provide the complete current roster; `/assets` remains
        untouched. Injecting a per-deed asset loop into `capture_quick()` makes the
        zero-asset-call assertion fail; replacing its `collection` argument to
        `analyze_bundle()` with `None` makes the roster assertion fail."""
        with TemporaryDirectory() as tmp:
            collection_client = FakeCollectionClient(cards=[
                {"uid": "worker-a", "card_detail_id": 998, "stake_ref_uid": "I-1-a", "stake_end_date": None},
                {"uid": "leaving-a", "card_detail_id": 999, "stake_ref_uid": "I-1-a", "stake_end_date": "2026-09-06T00:00:00Z"},
            ])
            land_client = CountingLandClient(["I-1-a"])
            report = self._capture(land_client, tmp, collection_client).capture_quick("player0005", include_asset_ids=True, as_of=AS_OF)
            self.assertEqual(land_client.assets_calls, [])
            self.assertEqual([worker["uid"] for worker in report["workers"]], ["worker-a"])
            # lowered this from 6 to 5: Runi is no longer asked for on the
            # available-items route, because the upstream has no unassigned-item source for
            # it and answered every deed with a wrapped P0001 error. The count is still
            # pinned exactly -- a saved request must stay saved, and any NEW fanout added to
            # capture_quick() must still fail here rather than hide inside a loosened bound.
            self.assertEqual(report["capture"]["requests"], 5)

    def test_quick_capture_collection_failure_is_soft_and_marked_incomplete(self):
        """A collection endpoint failure must not abort the board capture; removing
        the quick-path exception handling made this test raise instead of returning an
        incomplete report, and that mutation was observed red before restoration."""
        with TemporaryDirectory() as tmp:
            collection_client = FakeCollectionClient(fail=True)
            capture = self._capture(CountingLandClient(["I-1-a"]), tmp, collection_client)
            report = capture.capture_quick("player0005", as_of=AS_OF)
            self.assertEqual(collection_client.calls, ["player0005"])
            self.assertIs(report["coverage"]["collection"]["complete"], False)
            self.assertTrue(any("collection" in warning for warning in report["warnings"]))

    def test_quick_capture_still_carries_staking_pp_and_worker_counts(self):
        """staking_details supplies every PP field and worker_count/max_workers_allowed --
        the main table's real content -- from the bulk call alone."""
        with TemporaryDirectory() as tmp:
            land_client = CountingLandClient(["I-1-a"])

            def bundle_with_staking(player):
                return _response({
                    "status": "success",
                    "data": {
                        "deeds": [{"deed_uid": "I-1-a", "region_number": 1, "tract_number": 1, "plot_number": 1}],
                        "worksite_details": [],
                        "staking_details": [{"deed_uid": "I-1-a", "total_harvest_pp": 42.0, "worker_count": 3, "max_workers_allowed": 5}],
                    },
                })

            land_client.bundle = bundle_with_staking
            capture = self._capture(land_client, tmp)
            report = capture.capture_quick("player0005", as_of=AS_OF)
            plot = report["plots"][0]
            self.assertEqual(plot["staking_facts"]["total_harvest_pp"], 42.0)
            self.assertEqual(plot["staking_facts"]["worker_count"], 3)
            self.assertEqual(plot["staking_facts"]["max_workers_allowed"], 5)


class ExpandDeedTests(unittest.TestCase):
    def _capture(self, land_client, cache_dir):
        return LandCapture(
            land_client=land_client,
            collection_client=FakeCollectionClient(),
            cache=JsonCache(Path(cache_dir)),
            min_delay=0.0,
        )

    def test_expand_deed_fetches_only_the_one_deed(self):
        with TemporaryDirectory() as tmp:
            deed_uids = ["I-1-a", "I-1-b", "I-1-c"]
            land_client = CountingLandClient(deed_uids)
            capture = self._capture(land_client, tmp)
            report = capture.expand_deed("I-1-b", "player0005")
            self.assertEqual(land_client.assets_calls, ["I-1-b"])
            by_uid = {p["deed_uid"]: p for p in report["plots"]}
            self.assertEqual(by_uid["I-1-b"]["asset_fetch"]["status"], "retrieved")
            self.assertEqual(by_uid["I-1-a"]["asset_fetch"]["status"], "not_requested")
            self.assertEqual(by_uid["I-1-c"]["asset_fetch"]["status"], "not_requested")

    def test_expand_deed_reuses_the_bundle_from_cache(self):
        with TemporaryDirectory() as tmp:
            deed_uids = ["I-1-a", "I-1-b"]
            land_client = CountingLandClient(deed_uids)
            capture = self._capture(land_client, tmp)
            capture.capture_quick("player0005", as_of=AS_OF)
            # A positive TTL allows expand_deed to reuse the quick capture's bundle.
            capture.expand_deed("I-1-a", "player0005", bundle_ttl=300)
            # One bundle fetch total -- expand_deed's own bundle read is a cache hit.
            self.assertEqual(land_client.bundle_calls, 1)

    def test_mutation_expand_forces_fresh_bundle_and_collection(self):
        with TemporaryDirectory() as tmp:
            land_client = CountingLandClient(["I-1-a", "I-1-b"])
            collection_client = FakeCollectionClient()
            capture = LandCapture(
                land_client=land_client,
                collection_client=collection_client,
                cache=JsonCache(Path(tmp)),
                min_delay=0.0,
            )
            capture.capture_quick("player0005", as_of=AS_OF)
            land_client.deed_uids.append("I-1-c")
            report = capture.expand_deed("I-1-b", "player0005", bundle_ttl=0, include_collection=True)

            self.assertEqual(land_client.bundle_calls, 2)
            self.assertEqual(collection_client.calls, ["player0005", "player0005"])
            self.assertEqual({plot["deed_uid"] for plot in report["plots"]}, {"I-1-a", "I-1-b", "I-1-c"})
            self.assertTrue(report["coverage"]["collection"]["complete"])


if __name__ == "__main__":
    unittest.main()
