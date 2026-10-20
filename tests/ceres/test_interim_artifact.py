""": interim board = compact artifact right after the bulk read (no snapshot); the final
publish supersedes it by token; in the window the plot routes answer 409 board_finishing ()."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ceres import board_artifact, capture
from ceres.app import create_app
from ceres.config import CeresConfig
from tests.ceres.conftest import build_report_quick

PLAYER = "player0005"


class _FakeCapture:  # calls `on_bulk_ready` (the interim publish) and lets the test look at the window
    def __init__(self, report, in_window):
        self.report, self.in_window = report, in_window
        self.on_bulk_ready = self.on_deed_count = self.request_budget = None

    def capture_quick(self, player, include_asset_ids=False):
        self.on_bulk_ready(self.report)
        self.in_window()
        return self.report


@pytest.fixture
def config(tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path / ".ceres", player=PLAYER)
    config.ensure_dirs()
    monkeypatch.setattr(board_artifact, "details_identity", lambda: (None, None))
    monkeypatch.setattr("ceres.routes_board.details_cache_mtime", lambda: None)
    monkeypatch.setattr("ceres.board.load_details_by_id", lambda: {})
    monkeypatch.setattr(capture, "_start_optional_balances", lambda player: (None, None))
    monkeypatch.setattr(capture, "_finish_optional_balances", lambda *args: None)
    monkeypatch.setattr(capture, "_self_heal_refetches", lambda config, player, merged, **kw: merged)
    monkeypatch.setattr(capture, "_append_coverage_journal", lambda *args: None)
    return config


def test_interim_artifact_then_final_publish(config, monkeypatch):
    report = build_report_quick()
    report["query"] = {**(report.get("query") or {}), "player": PLAYER}
    deed_uid = report["plots"][0]["deed_uid"]
    client = TestClient(create_app(config))
    events: list[tuple[str, dict]] = []
    seen: dict = {}

    def emit(event, **fields):
        events.append((event, fields))
        driver.handle(event, fields)

    def in_window():
        identity = board_artifact.read_identity(config.snapshots_dir, PLAYER)
        seen["identity"] = identity
        seen["files"] = sorted(p.name for p in config.snapshots_dir.iterdir())
        seen["board"] = client.get("/api/board")
        seen["status"] = client.get("/api/capture/status").json()
        seen["plot"] = client.get(f"/api/plots/{deed_uid}")
        seen["expand"] = client.post(f"/api/plots/{deed_uid}/expand", headers={"origin": "http://testserver"})
        # the previous snapshot is never the answer for these two either
        seen["idle_cards"] = client.get("/api/idle-cards")
        seen["cooling"] = client.get("/api/cooling", params={"capture_generated_at": report.get("generated_at") or ""})

    capture._run_state[PLAYER] = capture._RunState(state="running", mode="quick")
    driver = capture._RunStateDriver(config, PLAYER, "quick", None)
    monkeypatch.setattr(capture, "_shared_capture", lambda: _FakeCapture(report, in_window))
    capture._run_capture_body(config, PLAYER, "quick", emit, known_plots=len(report["plots"]))

    interim = seen["identity"]
    assert interim.interim is True and interim.complete is True and interim.snapshot_path is None
    assert f"board-{PLAYER}-{interim.token}.json" in seen["files"] and f"board-{PLAYER}-{interim.token}.json.gz" in seen["files"]
    assert f"land-{PLAYER}-latest.json" not in seen["files"]
    assert seen["board"].status_code == 200
    assert {p["deed_uid"] for p in seen["board"].json()["plots"]} == {p["deed_uid"] for p in report["plots"]}
    assert seen["status"]["board_ready"] is True and seen["status"]["interim"] is True
    assert seen["status"]["latest_snapshot"]["token"] == interim.token
    assert seen["plot"].status_code == 409 and seen["plot"].json()["reason"] == "board_finishing"
    assert seen["expand"].status_code == 409 and seen["expand"].json()["reason"] == "board_finishing"
    assert seen["idle_cards"].status_code == 409 and seen["idle_cards"].json()["reason"] == "board_finishing"
    assert seen["cooling"].status_code == 409 and seen["cooling"].json()["reason"] == "board_finishing"
    ready = [f for e, f in events if e == "board_ready"]
    assert ready[0]["interim"] is True and ready[0]["token"] == interim.token and len(ready) == 2

    final = board_artifact.read_identity(config.snapshots_dir, PLAYER)
    assert final.interim is False and final.token != interim.token and final.write_counter == interim.write_counter + 1
    assert (config.snapshots_dir / f"land-{PLAYER}-latest.json").exists()
    assert not (config.snapshots_dir / f"board-{PLAYER}-{interim.token}.json").exists()
    assert ready[1]["token"] == final.token and "interim" not in ready[1]
    assert events[-1][0] == "done" and capture._run_state[PLAYER].state == "idle"
    status = client.get("/api/capture/status").json()
    assert status["interim"] is False and status["latest_snapshot"]["token"] == final.token
    assert client.get("/api/board").status_code == 200
    assert client.get(f"/api/plots/{deed_uid}").status_code == 200
    assert client.get("/api/idle-cards").status_code == 200
    final_report = client.get("/api/board").json()
    assert client.get("/api/cooling", params={"capture_generated_at": final_report["generated_at"]}).status_code == 200
