"""Whale-capture budget sizing and progress reporting (Ceres feature, 2026-08-21):
`ceres.capture.derive_request_budget`/`estimate_capture_seconds` (pure functions) and
`_run_capture`'s preflight + progress wiring, all offline via a fake `LandCapture`."""

from __future__ import annotations

import time

import pytest

from ceres import capture
from ceres.config import CeresConfig
from praetoria.land_analysis import DEFAULT_REQUEST_BUDGET


@pytest.fixture
def config(tmp_path) -> CeresConfig:
    return CeresConfig(root=tmp_path / ".ceres", player="player0005")


@pytest.fixture(autouse=True)
def _no_card_details_refresh(monkeypatch):
    """This file only fakes `LandCapture` -- `_refresh_card_details` (a real,
    unmocked `card_details` cache read/possible network fetch every
    `_run_capture_quick`/`_run_capture_full` call now makes) is out of scope here
    and, left unmocked, can outlast these tests' short poll windows and leave a
    background thread racing into the NEXT test via the shared `_run_state` dict.
    Balance reads are outside this test's fake capture contract for the same reason."""
    monkeypatch.setattr(capture, "_refresh_card_details", lambda: None)
    monkeypatch.setattr(capture, "_fetch_balances", lambda player: None)


def test_derive_request_budget_scales_with_deed_count():
    # player0005-scale (130 deeds) stays close to the old default.
    small = capture.derive_request_budget(130)
    assert small >= 130

    # player0017-scale (6303 deeds) -- the fixed DEFAULT_REQUEST_BUDGET (200) would fail
    # this outright; the derived budget must comfortably exceed the deed count.
    whale = capture.derive_request_budget(6303)
    assert whale > 6303
    assert whale > DEFAULT_REQUEST_BUDGET


def test_derive_request_budget_never_drops_below_the_default():
    assert capture.derive_request_budget(0) == DEFAULT_REQUEST_BUDGET
    assert capture.derive_request_budget(1) >= DEFAULT_REQUEST_BUDGET


def test_estimate_capture_seconds_uses_the_concurrent_dispatch_ceiling():
    """2026-08-21 concurrency feature: the estimate now reflects the concurrent
    fan-out's own dispatch ceiling (10 req/s), not the old fully-serial pacing (3.3
    req/s) -- roughly a 3x drop in the estimate, on top of the whale-scale budget
    itself already being derived rather than a fixed constant."""
    from praetoria.land_analysis import CONCURRENT_DISPATCH_INTERVAL_SECONDS

    seconds = capture.estimate_capture_seconds(6303)
    budget = capture.derive_request_budget(6303)
    assert seconds == pytest.approx(budget * CONCURRENT_DISPATCH_INTERVAL_SECONDS)
    # ~11-12 minutes is the new estimate for player0017-scale at the concurrent ceiling
    # down from the ~31.5 min fully-serial figure measured before concurrency shipped.
    assert 9 * 60 < seconds < 14 * 60


def _fake_report(player, plots=None):
    plots = plots or []
    retrieved = sum(1 for p in plots if (p.get("asset_fetch") or {}).get("status") == "retrieved")
    return {
        "capture": {"requests": 5, "cache_hits": 1}, "generated_at": "2026-08-21T00:00:00Z",
        "schema_version": "praetoria.land.analysis.v1", "plots": plots, "workers": [], "cards": [],
        "bonus_assets": [], "warnings": [],
        "coverage": {"assets": {"retrieved": retrieved, "expected": len(plots), "complete": bool(plots) and retrieved == len(plots)}},
        "query": {"player": player},
    }


class FakeLandCaptureFull:
    """Duck-types praetoria.land_analysis.LandCapture's FULL-capture surface: preflight +
    capture, wired to fire the same `progress(fetched, total)` callback shape the real
    one uses. Also records the `concurrency` it was constructed with, so
    test_run_capture_full_passes_configured_concurrency can prove
    CeresConfig.capture_concurrency actually reaches LandCapture()."""

    def __init__(self, *a, concurrency=None, **kw):
        self.request_budget = None
        self.concurrency = concurrency

    def preflight_deed_count(self, player):
        return 3

    def capture(self, player, include_asset_ids=False, resume=False, progress=None):
        for i in range(1, 4):
            if progress:
                progress(i, 3)
        plots = [{"deed_uid": f"I-1-{i}", "asset_fetch": {"status": "retrieved"}} for i in range(3)]
        return _fake_report(player, plots)


class FakeLandCaptureQuick:
    """Duck-types the QUICK-capture surface only -- no `capture()`/`preflight_deed_count`,
    proving `start_refresh` (the default board load) never calls into the per-deed path."""

    def __init__(self, *a, **kw):
        pass

    def capture_quick(self, player, include_asset_ids=False):
        plots = [{"deed_uid": f"I-1-{i}", "asset_fetch": {"status": "not_requested"}} for i in range(5)]
        return _fake_report(player, plots)


def test_start_refresh_uses_the_quick_path_only(config, monkeypatch):
    monkeypatch.setattr(capture, "LandCapture", FakeLandCaptureQuick)
    started = capture.start_refresh(config, "player0005")
    assert started is True

    for _ in range(50):
        if not capture.is_running("player0005"):
            break
        time.sleep(0.02)

    status = capture.freshness(config, "player0005")
    assert status.state == "idle"
    assert status.mode == "quick"
    assert status.assets_complete is False


def test_estimate_capture_reports_deeds_and_cost(monkeypatch):
    class FakePreflight:
        def __init__(self, *a, **kw):
            pass

        def preflight_deed_count(self, player):
            return 6303

    monkeypatch.setattr(capture, "LandCapture", FakePreflight)
    result = capture.estimate_capture("player0017")
    assert result["player"] == "player0017"
    assert result["deeds"] == 6303
    assert result["requests_estimated"] > 6303
    # Concurrent-ceiling estimate (2026-08-21) -- ~11-12 min for player0017-scale, down
    # from the old fully-serial ~31.5 min figure.
    assert 9 < result["estimated_minutes"] < 14
