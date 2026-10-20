from __future__ import annotations

import threading
import time
from pathlib import Path

from ceres import capture as capture_module
from ceres.config import CeresConfig
from praetoria.cache import JsonCache
from praetoria.land_analysis import LandCapture
from praetoria.transport import ResponseData


def _response(data):
    return ResponseData(data=data, retrieved_at="2026-09-07T00:00:00Z", attempts=1)


class _OverlappingLandClient:
    def __init__(self):
        self.bundle_started = threading.Event()
        self.collection_started = threading.Event()
        self.stamps: dict[str, float] = {}

    def bundle(self, _player):
        self.stamps["bundle_start"] = time.monotonic()
        self.bundle_started.set()
        self.collection_started.wait(timeout=1.0)
        self.stamps["bundle_end"] = time.monotonic()
        return _response({
            "status": "success",
            "data": {"deeds": [{"deed_uid": "I-1-a"}], "worksite_details": [], "staking_details": []},
        })

    def available_raw(self, *_args, **_kwargs):
        return _response({"status": "success", "data": {"ids": []}})


class _OverlappingCollectionClient:
    def __init__(self, land_client):
        self.land_client = land_client

    def collection(self, _player):
        self.land_client.stamps["collection_start"] = time.monotonic()
        self.land_client.collection_started.set()
        self.land_client.bundle_started.wait(timeout=1.0)
        self.land_client.stamps["collection_end"] = time.monotonic()
        return _response({"player": "player0005", "cards": []})

    def details(self):
        return _response([])

    def item_details(self):
        return _response([])


def test_quick_bundle_and_collection_reads_overlap(tmp_path: Path):
    land_client = _OverlappingLandClient()
    capture = LandCapture(
        land_client=land_client,
        collection_client=_OverlappingCollectionClient(land_client),
        cache=JsonCache(tmp_path),
        min_delay=0.0,
    )

    capture.capture_quick("player0005")

    # RED MUTATION: move the bundle read back before the pool; one of these strict
    # start/end relationships must turn red because the reads no longer overlap.
    assert land_client.stamps["bundle_start"] < land_client.stamps["collection_end"]
    assert land_client.stamps["collection_start"] < land_client.stamps["bundle_end"]


def test_failed_optional_balances_do_not_discard_the_board(tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    saved: dict[str, dict] = {}
    report = {
        "capture": {"requests": 1, "cache_hits": 0},
        "generated_at": "2026-09-07T00:00:00Z",
        "plots": [{"deed_uid": "I-1-a"}],
        "workers": [],
        "cards": [],
        "bonus_assets": [],
        "warnings": [],
        "coverage": {},
    }

    class _Capture:
        def capture_quick(self, _player, include_asset_ids=False):
            return dict(report)

    def _save(_config, _player, incoming):
        saved["report"] = incoming
        return incoming

    monkeypatch.setattr(capture_module, "LandCapture", _Capture)
    monkeypatch.setattr(capture_module, "_refresh_card_details", lambda: None)

    def _failed_balances(_player):
        raise RuntimeError("simulated balances outage")

    monkeypatch.setattr(capture_module, "_fetch_balances", _failed_balances)
    monkeypatch.setattr(capture_module, "_save_merged", _save)
    monkeypatch.setattr(capture_module, "_self_heal_refetches", lambda *_args, **_kwargs: _args[2])
    monkeypatch.setattr(capture_module, "_run_state", {})

    assert capture_module._begin_run("player0005", "quick") is True
    capture_module._run_capture_quick(config, "player0005")

    # RED MUTATION: make a failed optional balance future raise through the capture
    # path; the board report must still be saved, with only its footer degraded.
    assert saved["report"]["plots"] == report["plots"]
    assert saved["report"]["tc_balance"] is None
    assert saved["report"]["permit_balance"] is None
