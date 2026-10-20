"""§6.6 test_freshness.py -- all offline; capture.py never called with real network access
here, only its file-reading/threading logic against a temp `~/.ceres`-shaped root."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ceres import capture
from ceres.config import CeresConfig
from praetoria.land_analysis import LAND_SCHEMA_VERSION


def _write_snapshot(config: CeresConfig, player: str, generated_at: str, plots: list | None = None) -> None:
    config.ensure_dirs()
    report = {"schema_version": LAND_SCHEMA_VERSION, "generated_at": generated_at, "plots": plots or [], "workers": [], "cards": [], "bonus_assets": [], "warnings": [], "coverage": {}, "query": {"player": player}}
    path = config.snapshots_dir / f"land-{player}-latest.json"
    path.write_text(json.dumps(report))


@pytest.fixture
def config(tmp_path) -> CeresConfig:
    return CeresConfig(root=tmp_path / ".ceres", player="player0005")


def test_age_seconds_and_stale_computed_from_generated_at(config):
    generated = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    _write_snapshot(config, "player0005", generated)
    status = capture.freshness(config, "player0005")
    assert status.age_seconds is not None
    assert 25 <= status.age_seconds <= 40
    assert status.stale is False


def test_stale_flag_flips_past_the_configured_threshold(config):
    config.stale_after_seconds = 60
    generated = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat().replace("+00:00", "Z")
    _write_snapshot(config, "player0005", generated)
    status = capture.freshness(config, "player0005")
    assert status.stale is True


def test_no_snapshot_reports_none(config):
    status = capture.freshness(config, "player0005")
    assert status.latest_snapshot is None
    assert status.age_seconds is None
    assert status.stale is False


def test_expand_plot_raises_when_no_snapshot_yet(config):
    with pytest.raises(LookupError):
        capture.expand_plot(config, "player0005", "I-1-a")


def test_expand_plot_merges_one_deed_and_saves(config, monkeypatch):
    generated = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
    # the board row carries the numeric plot_id the narrow live path needs.
    _write_snapshot(config, "player0005", generated, plots=[{"deed_uid": "I-1-a", "facts": {"plot_id": 101}}])

    class FakeExpandCapture:
        def __init__(self, *a, **kw):
            pass

        def expand_deed(self, deed_uid, player, *, bundle_ttl=None, include_collection=False, narrow=False, plot_id=None):
            # a plain row expand does NOT re-sweep the collection.
            # and it analyses the one deed only (`narrow=True`).
            # (): it reads that deed live by its numeric
            # plot_id and never reuses a cached bundle (the old `bundle_ttl == 3600` pinned
            # that breach).
            assert plot_id == 101
            assert bundle_ttl in (None, 0)
            assert include_collection is False
            assert narrow is True
            return {
                "generated_at": generated, "schema_version": LAND_SCHEMA_VERSION,
                "plots": [{"deed_uid": deed_uid, "asset_fetch": {"status": "retrieved"}}],
                "workers": [{"stake_ref_uid": deed_uid, "name": "Some Worker"}],
                "cards": [], "bonus_assets": [], "warnings": [], "unassigned_items": None,
                "coverage": {"assets": {"retrieved": 1, "expected": 1, "complete": True}},
                "query": {"player": player},
            }

    monkeypatch.setattr(capture, "LandCapture", FakeExpandCapture)
    merged = capture.expand_plot(config, "player0005", "I-1-a")
    assert merged["plots"][0]["asset_fetch"]["status"] == "retrieved"
    assert merged["workers"][0]["name"] == "Some Worker"

    # persisted -- a fresh read agrees
    report, ref = capture.latest_snapshot(config, "player0005")
    assert report["plots"][0]["asset_fetch"]["status"] == "retrieved"


def test_failed_refresh_keeps_serving_old_snapshot(config, monkeypatch):
    generated = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
    _write_snapshot(config, "player0005", generated)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated capture failure")

    class BoomCapture:
        def __init__(self, *a, **kw):
            pass

        def capture(self, *a, **kw):
            raise RuntimeError("simulated capture failure")

    monkeypatch.setattr(capture, "LandCapture", BoomCapture)
    # This file's own docstring promises no real network access -- `_refresh_card_details`
    # (added alongside `LandCapture` in `_run_capture_quick`) would otherwise make one.
    monkeypatch.setattr(capture, "_refresh_card_details", lambda: None)
    started = capture.start_refresh(config, "player0005")
    assert started is True

    import time
    for _ in range(50):
        if not capture.is_running("player0005"):
            break
        time.sleep(0.05)

    status = capture.freshness(config, "player0005")
    assert status.state == "failed"
    assert status.error is not None
    # the old snapshot is still the one served
    assert status.latest_snapshot is not None
    assert status.latest_snapshot.generated_at == generated
