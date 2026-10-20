""": "all storage of current board data is strictly forbidden. 100% fresh data for anything ceres
wide. Always" -- guards for the steps that have landed (plan
work/notes/PLAN-S16880-no-stored-board-data-2026-09-27.md, maintainer answers )."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

from ceres import capture as capture_mod
from ceres import stored_data_cleanup
from ceres.app import create_app
from ceres.config import CeresConfig
from praetoria.land_analysis import LandCapture, analyze_bundle, save_snapshot
from tests.ceres.test_plot_open import DEED_A, _bundle, _deed

ROOT = Path(__file__).resolve().parents[2]


def test_step1a_plot_open_writes_no_asset_cache(tmp_path, monkeypatch):
    """: a plot open no longer leaves land_assets_<ref>.json behind (only static tables may be written)."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    config.ensure_dirs()
    save_snapshot(analyze_bundle("player0005", _bundle(_deed(DEED_A, 101)), {}), capture_mod._latest_path(config, "player0005"))
    made: list[object] = []

    class FakeCapture:
        def __init__(self, *args, **kwargs):
            made.append(self)

        def expand_deed(self, deed_uid, player, **kwargs):
            return analyze_bundle("player0005", _bundle(_deed(DEED_A, 101)), {DEED_A: {"cards": [], "items": []}})

    monkeypatch.setattr(capture_mod, "LandCapture", FakeCapture)
    capture_mod.expand_plot(config, "player0005", DEED_A)
    assert len(made) == 1 and made[0].persist_cache is capture_mod.persist_static_reference_only
    assert capture_mod.persist_static_reference_only("card_details")
    assert capture_mod.persist_static_reference_only("item_details")
    for key in ("land_assets_abc", "land_deeds_player0005", "collection_player0005", "land_owned_counts_player0005"):
        assert not capture_mod.persist_static_reference_only(key)


def test_step1a_legacy_expand_and_preflight_defaults_never_serve_a_cache():
    import inspect

    assert inspect.signature(LandCapture.expand_deed).parameters["bundle_ttl"].default == 0
    assert inspect.signature(LandCapture.preflight_deed_count).parameters["ttl"].default == 0


def test_step1a_full_capture_route_is_gone(tmp_path, monkeypatch):
    """ Q9: the hidden Full capture (six-hour resume cache) is removed; the plot-count estimate
 stays (the rate-limit guards use it) and reads the count live without writing it."""
    app = create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005"))
    # FastAPI 0.141 keeps included routers as wrappers in app.routes, so read the registered API paths from
    # the schema, and confirm the removed route really answers 404.
    paths = set(app.openapi()["paths"])
    assert "/api/capture/refresh_full" not in paths
    assert "/api/capture/refresh" in paths
    assert TestClient(app).post("/api/capture/refresh_full").status_code == 404
    for name in ("start_refresh_full", "_capture_full_body", "_run_capture_full"):
        assert not hasattr(capture_mod, name), name
    made: list[object] = []

    class FakeCapture:
        def __init__(self, *args, **kwargs):
            made.append(self)

        def preflight_deed_count(self, player):
            return 1

    monkeypatch.setattr(capture_mod, "LandCapture", FakeCapture)
    capture_mod.estimate_capture("player0005")
    assert made[0].persist_cache is capture_mod.persist_static_reference_only
    with TestClient(app) as client:
        assert client.post("/api/capture/refresh_full").status_code in (404, 405)


def test_step1a_cap_predictions_never_reach_local_storage():
    source = (ROOT / "ceres/static/staging.js").read_text()
    assert not re.search(r"localStorage\.(getItem|setItem)\(", source)
    assert "localStorage.removeItem(name)" in source  # Q5: the old copy is removed silently


def _touch(path: Path, age: float = 0.0) -> None:
    path.write_text("{}")
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))


def test_step1a_cleanup_removes_only_files_this_version_no_longer_uses(tmp_path, monkeypatch):
    """ Q5: delete automatically, only unused files, never Q3/Q4 data or files still read; log it."""
    cache = tmp_path / "cache"
    cache.mkdir()
    removed = ["land_assets_1f883c7850126c843ec3ca11.json", "land_owned_counts_player0005.json",
               "land_project_history_I-290-abc.json", "collection_player0005.json", "collection_player0017.etag",
               "collection_player0017.etag.json"]
    kept = ["card_details.json", "item_details.json", "land_deeds_player0005.json",
            "land_available_STK-LND-TOT_player0005_0.json", "collection_unbinding_v2_player0005.etag",
            "collection_unbinding_v2_player0005.etag.json", "some_other_tool.json"]
    for name in removed + kept:
        _touch(cache / name)
    _touch(cache / "collection_player0005.1268631.tmp", age=2 * 86400)
    _touch(cache / "land_assets_abcdef12.json.fresh.tmp")  # a write in progress: too young
    ceres_root = tmp_path / ".ceres"
    snapshots = ceres_root / "snapshots"
    snapshots.mkdir(parents=True)
    _touch(snapshots / "land-player0005-latest.json")
    monkeypatch.setenv("PRAETORIA_CACHE_DIR", str(cache))
    monkeypatch.delenv("CERES_NO_STORED_DATA_CLEANUP", raising=False)

    summary = stored_data_cleanup.run(ceres_root, "test")

    left = sorted(p.name for p in cache.iterdir())
    assert left == sorted(kept + ["land_assets_abcdef12.json.fresh.tmp"])
    assert summary["files"] == len(removed) + 1
    assert (snapshots / "land-player0005-latest.json").exists()
    assert "land_assets" in (ceres_root / "stored-data-cleanup.jsonl").read_text()


def test_step1a_cleanup_is_off_under_tests_and_never_blocks_start(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAETORIA_CACHE_DIR", str(tmp_path))
    _touch(tmp_path / "land_assets_1f883c7850126c843ec3ca11.json")
    assert stored_data_cleanup.run(tmp_path / ".ceres", "test") is None  # conftest sets the switch
    assert stored_data_cleanup.start_in_background(tmp_path / ".ceres", "test") is None
    assert (tmp_path / "land_assets_1f883c7850126c843ec3ca11.json").exists()
    source = (ROOT / "ceres/__main__.py").read_text()
    assert "stored_data_cleanup.start_in_background(" in source
