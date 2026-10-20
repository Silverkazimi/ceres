"""Ceres' capture never persists the raw collection response, but keeps the deeds bundle."""
from __future__ import annotations

from pathlib import Path

from ceres.capture import _shared_capture
from praetoria.cache import JsonCache
from praetoria.land_analysis import LandCapture
from tests.test_land_project_history import FakeCollectionClient, FakeLandClient


def _capture(tmp_path: Path) -> LandCapture:
    capture = LandCapture(
        land_client=FakeLandClient({}),
        collection_client=FakeCollectionClient(),
        cache=JsonCache(tmp_path),
        min_delay=0.0,
        concurrency=1,
    )
    capture.persist_cache = _shared_capture().persist_cache
    return capture


def test_collection_is_fetched_but_not_written_while_deeds_bundle_is(tmp_path):
    capture = _capture(tmp_path)
    report = capture.capture("player0005")
    assert report["coverage"]["collection"]["complete"] is True
    names = sorted(path.name for path in tmp_path.glob("*.json"))
    assert not [name for name in names if name.startswith("collection_")], names
    assert "land_deeds_player0005.json" in names


def test_default_capture_still_persists_collection(tmp_path):
    capture = LandCapture(
        land_client=FakeLandClient({}), collection_client=FakeCollectionClient(),
        cache=JsonCache(tmp_path), min_delay=0.0, concurrency=1,
    )
    capture.capture("player0005")
    assert (tmp_path / "collection_player0005.json").exists()
