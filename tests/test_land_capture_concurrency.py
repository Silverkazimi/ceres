"""Concurrent per-deed asset fan-out (2026-08-21 --  was very slow to
capture large accounts). All offline -- fake `land_client`
with a small `time.sleep` standing in for the measured ~0.48s server think-time, so the
concurrency win is provable without any real network I/O. `min_delay=0.0` throughout
(pacing itself is covered by test_land_capture_resume.py); the point here is the
thread-pool fan-out, determinism, and the adaptive backoff.
"""

from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import praetoria.land_analysis as land_analysis
from praetoria.cache import JsonCache
from praetoria.errors import TransportError
from praetoria.land_analysis import LandCapture
from praetoria.transport import ResponseData

AS_OF = datetime(2026, 8, 9, tzinfo=timezone.utc)
PER_REQUEST_DELAY = 0.05  # stands in for the measured ~0.48s server think-time


def _response(data):
    return ResponseData(data=data, retrieved_at="2026-08-21T00:00:00Z", attempts=1)


class SlowLandClient:
    """Every `assets()` call sleeps `PER_REQUEST_DELAY` before returning -- simulates
    real server think-time so concurrency has something real to overlap. Thread-safe
    bookkeeping (a lock around the shared lists) since this is called from a pool."""

    def __init__(self, deed_uids, fail_for=()):
        self.deed_uids = deed_uids
        self.fail_for = set(fail_for)
        self._lock = threading.Lock()
        self.assets_calls: list[str] = []
        self.max_concurrent = 0
        self._in_flight = 0

    def bundle(self, player):
        deeds = [{"deed_uid": uid} for uid in self.deed_uids]
        return _response({"status": "success", "data": {"deeds": deeds, "worksite_details": [], "staking_details": []}})

    def assets(self, deed_uid):
        with self._lock:
            self.assets_calls.append(deed_uid)
            self._in_flight += 1
            self.max_concurrent = max(self.max_concurrent, self._in_flight)
        time.sleep(PER_REQUEST_DELAY)
        with self._lock:
            self._in_flight -= 1
        if deed_uid in self.fail_for:
            raise RuntimeError(f"simulated failure for {deed_uid}")
        return _response({"status": "success", "data": {"cards": [], "items": []}})

    def available_raw(self, stake_type_uid, deed_uid, player, offset=0, limit=5):
        return _response({"status": "success", "data": {"ids": []}})


class FakeCollectionClient:
    def collection(self, player):
        return _response({"player": player, "cards": []})


def _capture(land_client, cache_dir, **kw):
    return LandCapture(
        land_client=land_client,
        collection_client=FakeCollectionClient(),
        cache=JsonCache(Path(cache_dir)),
        min_delay=0.0,
        **kw,
    )


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_capture_is_faster_than_serial(self):
        deed_uids = [f"I-1-{i}" for i in range(16)]
        with TemporaryDirectory() as tmp_serial, TemporaryDirectory() as tmp_concurrent:
            serial = _capture(SlowLandClient(deed_uids), tmp_serial, concurrency=1)
            t0 = time.monotonic()
            serial.capture("player0005", as_of=AS_OF)
            serial_elapsed = time.monotonic() - t0

            concurrent_client = SlowLandClient(deed_uids)
            concurrent = _capture(concurrent_client, tmp_concurrent, concurrency=8)
            t0 = time.monotonic()
            concurrent.capture("player0005", as_of=AS_OF)
            concurrent_elapsed = time.monotonic() - t0

            self.assertGreater(concurrent_client.max_concurrent, 1, "the pool never actually overlapped requests")
            self.assertLess(concurrent_elapsed, serial_elapsed * 0.6)

    def test_default_concurrency_is_eight(self):
        # A pure attribute check -- never calls .capture()/.preflight_deed_count(), so
        # no cache I/O happens regardless -- but pass an isolated cache= anyway so this
        # stays correct if the test is ever extended (cache-safety sweep, 2026-08-21).
        with TemporaryDirectory() as tmp:
            cap = LandCapture(cache=JsonCache(Path(tmp)))
            self.assertEqual(cap.concurrency, 8)

    def test_output_is_deterministic_regardless_of_completion_order(self):
        """Plots are always sorted by deed_uid (analyze_bundle's own contract) no
        matter which thread finished first -- run twice, compare the plot order."""
        deed_uids = [f"I-1-{i}" for i in range(12)]
        with TemporaryDirectory() as tmp1, TemporaryDirectory() as tmp2:
            report1 = _capture(SlowLandClient(deed_uids), tmp1, concurrency=8).capture("player0005", as_of=AS_OF)
            report2 = _capture(SlowLandClient(deed_uids), tmp2, concurrency=8).capture("player0005", as_of=AS_OF)
            order1 = [p["deed_uid"] for p in report1["plots"]]
            order2 = [p["deed_uid"] for p in report2["plots"]]
            self.assertEqual(order1, order2)
            self.assertEqual(order1, sorted(order1))

    def test_progress_still_reports_every_deed_exactly_once_under_concurrency(self):
        deed_uids = [f"I-1-{i}" for i in range(10)]
        with TemporaryDirectory() as tmp:
            calls = []
            capture = _capture(SlowLandClient(deed_uids), tmp, concurrency=8)
            capture.capture("player0005", as_of=AS_OF, progress=lambda f, t: calls.append((f, t)))
            self.assertEqual(calls, [(i, 10) for i in range(1, 11)])

    def test_resume_skips_cached_deeds_under_concurrency(self):
        deed_uids = [f"I-1-{i}" for i in range(10)]
        with TemporaryDirectory() as tmp:
            failing_client = SlowLandClient(deed_uids, fail_for=("I-1-3",))
            first = _capture(failing_client, tmp, concurrency=8)
            first.capture("player0005", resume=True, as_of=AS_OF)
            self.assertEqual(sorted(failing_client.assets_calls), sorted(deed_uids))

            # A brand-new client that would raise if asked to re-fetch any
            # already-successful deed -- proves resume still skips them under a
            # concurrent second run.
            resumed_client = SlowLandClient(deed_uids, fail_for=[uid for uid in deed_uids if uid != "I-1-3"])
            second = _capture(resumed_client, tmp, concurrency=8)
            second.capture("player0005", resume=True, as_of=AS_OF)
            self.assertEqual(resumed_client.assets_calls, ["I-1-3"])

    def test_budget_is_never_oversold_under_concurrency(self):
        deed_uids = [f"I-1-{i}" for i in range(20)]
        with TemporaryDirectory() as tmp:
            capture = _capture(SlowLandClient(deed_uids), tmp, concurrency=8, request_budget=10)
            capture.capture("player0005", as_of=AS_OF)
            # Budget covers the bundle (1) + up to 10 assets calls -- never more than
            # request_budget total, even with 8 workers racing the check.
            self.assertLessEqual(capture._requests, 10)

    def test_repeated_failures_trigger_adaptive_throttle(self):
        # Fake sleeper -- the throttle backoff itself would otherwise really sleep a
        # few seconds once triggered; this test only needs to prove it triggers.
        deed_uids = [f"I-1-{i}" for i in range(6)]
        with TemporaryDirectory() as tmp:
            capture = _capture(SlowLandClient(deed_uids, fail_for=deed_uids), tmp, concurrency=4, sleeper=lambda s: None)
            capture.capture("player0005", as_of=AS_OF)
            self.assertGreater(capture._throttle_until, 0.0)

    def test_full_capture_passes_lazy_asset_mapping(self):
        """Injecting the old response-valued dict into analyze_bundle makes this guard fail.
        The full capture must pass cache keys through a lazy mapping instead of retaining
        every raw asset response in its fan-out result.
        """
        deed_uids = [f"I-1-{i}" for i in range(4)]
        with TemporaryDirectory() as tmp:
            capture = _capture(SlowLandClient(deed_uids), tmp, concurrency=2)
            with patch.object(land_analysis, "analyze_bundle", wraps=land_analysis.analyze_bundle) as analyze:
                capture.capture("player0005", as_of=AS_OF)
            assets_by_deed = analyze.call_args.args[2]
            self.assertNotIsInstance(assets_by_deed, dict)
            self.assertEqual(set(assets_by_deed), set(deed_uids))


if __name__ == "__main__":
    unittest.main()
