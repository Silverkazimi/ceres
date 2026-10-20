from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ceres import board_artifact, capture
from ceres.board import project_board
from ceres.config import CeresConfig
from ceres.routes_board import get_board
from praetoria.land_analysis import LandCapture, LandClient, analyze_bundle, save_snapshot
from praetoria.transport import ResponseData


DEED_A = "I-290-plot-a"
DEED_B = "I-290-plot-b"


def _deed(uid: str, plot_id: int) -> dict:
    return {
        "deed_uid": uid, "plot_id": plot_id, "player": "player0005",
        "region_number": 1, "tract_number": 1, "plot_number": plot_id,
        "region_uid": "PR-TEST-1", "region_name": "Test", "territory": "Test",
        "resource_symbol": "WOOD", "rarity": "common", "plot_status": "natural",
        "worksite_type": "Logging Camp", "deed_type": "Plain", "lock_days": 30,
        "unlock_date": "2026-10-01T00:00:00Z",
    }


def _bundle(*deeds: dict) -> dict:
    return {
        "status": "success",
        "data": {
            "deeds": list(deeds), "worksite_details": [],
            "staking_details": [{"deed_uid": deed["deed_uid"], "worker_count": 0, "max_workers_allowed": 5} for deed in deeds],
        },
    }


def test_expand_reads_live_narrow_deed_worksite_staking_and_assets_without_bundle_or_cache(tmp_path):
    calls: list[str] = []

    class Transport:
        def get_json(self, url):
            calls.append(url)
            if url.endswith("/land/deeds/101"):
                data = _deed(DEED_A, 101)
            elif url.endswith(f"/land/projects/deed/{DEED_A}/active"):
                data = None
            elif url.endswith(f"/land/stake/deed/details/{DEED_A}"):
                data = {"deed_uid": DEED_A, "worker_count": 0, "max_workers_allowed": 5}
            elif url.endswith(f"/land/stake/deeds/{DEED_A}/assets"):
                data = {"cards": [], "items": []}
            else:
                raise AssertionError(f"unexpected endpoint: {url}")
            return ResponseData({"status": "success", "data": data}, "2026-09-25T00:00:00Z", 1)

    class NoReadCache:
        def __init__(self):
            self.root = Path(tmp_path)

        def read(self, *args, **kwargs):
            raise AssertionError("plot open read a cache")

        def write(self, *args, **kwargs):
            raise AssertionError("plot open wrote a cache")

    worker = LandCapture(land_client=LandClient(transport=Transport()), cache=NoReadCache(), min_delay=0)
    worker.persist_cache = lambda _key: False
    report = worker.expand_deed(DEED_A, "player0005", narrow=True, plot_id=101)

    assert len(calls) == 4
    assert all("/land/deeds?player=" not in url for url in calls)
    assert calls[0].endswith("/land/deeds/101")
    assert report["plots"][0]["facts"]["lock_days"] == 30
    assert report["plots"][0]["facts"]["unlock_date"] == "2026-10-01T00:00:00Z"
    assert report["capture"]["cache_hits"] == 0


def test_plot_open_publishes_one_patch_and_preserves_other_plot(tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    config.ensure_dirs()
    base = analyze_bundle("player0005", _bundle(_deed(DEED_A, 101), _deed(DEED_B, 102)), {})
    snapshot = capture._latest_path(config, "player0005")
    save_snapshot(base, snapshot)
    before_bytes = snapshot.read_bytes()
    before_board = project_board(base, {}, lean=True)
    token, counter, epoch = board_artifact.reserve_identity(config.snapshots_dir, "player0005")
    details_mtime, details_size = board_artifact.details_identity()
    identity = board_artifact.commit_snapshot_identity(
        config.snapshots_dir, "player0005", token, counter, epoch, snapshot,
        snapshot.stat(), details_mtime, snapshot_generated_at=base["generated_at"], details_size=details_size,
    )
    assert board_artifact.publish(config.snapshots_dir, "player0005", identity, base, {}) is not None
    expanded = analyze_bundle("player0005", _bundle(_deed(DEED_A, 101)), {DEED_A: {"cards": [], "items": []}})

    class FakeCapture:
        def expand_deed(self, deed_uid, player, **kwargs):
            assert deed_uid == DEED_A and player == "player0005"
            assert kwargs["narrow"] is True and kwargs["plot_id"] == 101
            assert kwargs["bundle_ttl"] == 0
            return expanded

    monkeypatch.setattr(capture, "LandCapture", FakeCapture)
    monkeypatch.setattr(capture, "_save_snapshot_and_refresh_cache_locked", lambda *a, **k: (_ for _ in ()).throw(AssertionError("full snapshot write")))

    opened = capture.expand_plot(config, "player0005", DEED_A)
    assert snapshot.read_bytes() == before_bytes
    patch_files = capture._plot_patch_files(config, "player0005")
    assert len(patch_files) == 1
    assert patch_files[0].stat().st_size < 64_000
    assert opened["plots"][1] == base["plots"][1]
    persisted, ref = capture.latest_snapshot(config, "player0005")
    assert persisted["plots"][0]["asset_fetch"]["status"] == "retrieved"
    assert persisted["plots"][1] == base["plots"][1]
    assert ref.token is None or "-p" in ref.token
    after_board = project_board(persisted, {}, lean=True)
    assert after_board["plots"][1] == before_board["plots"][1]
    monkeypatch.setattr("ceres.routes_board.load_details_by_id", lambda: {})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)), headers={})
    served_board = json.loads(get_board(request).body)
    served_opened = next(row for row in served_board["plots"] if row["deed_uid"] == DEED_A)
    assert served_opened["assets_loaded"] is True
    served_other = next(row for row in served_board["plots"] if row["deed_uid"] == DEED_B)
    assert served_other == before_board["plots"][1]
