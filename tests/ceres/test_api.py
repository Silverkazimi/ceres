"""§6.6 test_api.py -- FastAPI TestClient over an injected fake capture layer.

TestClient talks to the ASGI app in-process (no real socket); this module also
monkeypatches `socket.socket` to raise, so any code path that tried to open a real
socket would fail the test loudly instead of silently reaching the network.
"""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from ceres import capture as capture_mod
from ceres.app import create_app
from ceres.config import CeresConfig


@pytest.fixture(autouse=True)
def block_real_sockets(monkeypatch):
    """Block outbound network connections without breaking TestClient's own in-process
    AF_UNIX socketpair (used internally by its anyio event-loop plumbing, not the
    network) -- patching `socket.socket` itself blocks that plumbing too, so this
    patches `create_connection` (the actual outbound-connect path urllib/httpx use)."""
    def _raise(*args, **kwargs):
        raise AssertionError("test_api.py must not open a real outbound network connection")

    monkeypatch.setattr(socket, "create_connection", _raise)


@pytest.fixture
def config(tmp_path) -> CeresConfig:
    return CeresConfig(root=tmp_path / ".ceres", player="player0005")


@pytest.fixture
def client(config) -> TestClient:
    app = create_app(config)
    return TestClient(app)


def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["schema"] == "ceres.board.v1"


def test_board_503_when_no_snapshot(client, monkeypatch):
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (None, None))
    resp = client.get("/api/board")
    assert resp.status_code == 503
    # R-111 / 4125a78f: icon-only reload replaced the internal snapshot wording.
    assert "no stored board is available" in resp.json()["detail"]


def test_board_200_with_fake_snapshot(client, monkeypatch, report):
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    monkeypatch.setattr(capture_mod, "freshness", lambda config, player: capture_mod.CaptureStatus(state="idle", age_seconds=10, stale=False))
    resp = client.get("/api/board")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema"] == "ceres.board.v1"
    assert isinstance(body["plots"], list)


def test_plot_404_for_unknown_deed_uid(client, monkeypatch, report):
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    resp = client.get("/api/plots/does-not-exist")
    assert resp.status_code == 404


def test_plot_200_for_known_deed_uid(client, monkeypatch, report):
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    known = report["plots"][0]["deed_uid"]
    resp = client.get(f"/api/plots/{known}")
    assert resp.status_code == 200
    assert resp.json()["deed_uid"] == known


def test_capture_refresh_returns_202_and_never_blocks(client, monkeypatch):
    started = {}

    def fake_start_refresh(config, player):
        started["called"] = True
        return True

    monkeypatch.setattr(capture_mod, "is_running", lambda player: False)
    monkeypatch.setattr(capture_mod, "start_refresh", fake_start_refresh)
    monkeypatch.setattr(capture_mod, "freshness", lambda config, player: capture_mod.CaptureStatus(state="running", started_at="2026-08-20T00:00:00Z"))
    resp = client.post("/api/capture/refresh")
    assert resp.status_code == 202
    assert resp.json()["state"] == "running"
    assert started["called"] is True


def test_capture_refresh_replaces_an_already_running_read(client, monkeypatch):
    started = []
    monkeypatch.setattr(capture_mod, "is_running", lambda player: True)
    monkeypatch.setattr(capture_mod, "start_refresh", lambda config, player: started.append(player) or True)
    monkeypatch.setattr(capture_mod, "freshness", lambda config, player: capture_mod.CaptureStatus(state="queued", started_at="2026-08-20T00:00:01Z", queue_position=1))
    resp = client.post("/api/capture/refresh")
    assert resp.status_code == 202
    assert resp.json()["state"] == "queued"
    assert started == ["player0005"]


def test_capture_refresh_threads_load_order_to_supervisor(client, monkeypatch):
    started = []
    monkeypatch.setattr(
        capture_mod,
        "start_refresh",
        lambda config, player, **order: started.append((player, order)) or True,
    )
    monkeypatch.setattr(
        capture_mod,
        "freshness",
        lambda config, player: capture_mod.CaptureStatus(state="queued", queue_position=1),
    )
    resp = client.post("/api/capture/refresh?load_id=tab-1&load_generation=7")
    assert resp.status_code == 202
    assert started == [("player0005", {"load_id": "tab-1", "load_generation": 7})]


def test_capture_refresh_reports_stale_generation_as_superseded_noop(client, monkeypatch):
    monkeypatch.setattr(capture_mod, "start_refresh", lambda config, player, **order: False)
    monkeypatch.setattr(
        capture_mod,
        "freshness",
        lambda config, player: (_ for _ in ()).throw(AssertionError("stale requests do not inspect or replace the current run")),
    )
    resp = client.post("/api/capture/refresh?load_id=tab-1&load_generation=6")
    assert resp.status_code == 202
    assert resp.json() == {"state": "superseded", "mode": "quick"}


def test_capture_refresh_rejects_partial_or_unbounded_load_identity(client):
    assert client.post("/api/capture/refresh?load_id=tab-1").status_code == 400
    assert client.post("/api/capture/refresh?load_generation=1").status_code == 400
    assert client.post(f"/api/capture/refresh?load_id={'x' * 129}&load_generation=1").status_code == 400


def test_capture_estimate_reports_deed_count_and_cost(client, monkeypatch):
    monkeypatch.setattr(
        capture_mod, "estimate_capture",
        lambda player: {"player": player, "deeds": 6303, "requests_estimated": 6970, "estimated_seconds": 2091.0, "estimated_minutes": 34.9},
    )
    resp = client.get("/api/capture/estimate", params={"player": "player0017"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["player"] == "player0017"
    assert body["deeds"] == 6303
    assert body["estimated_minutes"] == 34.9


def test_capture_estimate_502_when_preflight_fails(client, monkeypatch):
    def boom(player):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(capture_mod, "estimate_capture", boom)
    resp = client.get("/api/capture/estimate", params={"player": "player0017"})
    assert resp.status_code == 502


def test_expand_plot_returns_the_updated_row(client, monkeypatch, report, details_by_id):
    known = report["plots"][0]["deed_uid"]
    monkeypatch.setattr(capture_mod, "is_running", lambda player: False)
    monkeypatch.setattr(capture_mod, "expand_plot", lambda config, player, deed_uid: report)
    resp = client.post(f"/api/plots/{known}/expand")
    assert resp.status_code == 200
    assert resp.json()["deed_uid"] == known


def test_expand_plot_409_when_a_capture_is_running(client, monkeypatch):
    monkeypatch.setattr(capture_mod, "is_running", lambda player: True)
    resp = client.post("/api/plots/some-deed/expand")
    assert resp.status_code == 409


def test_expand_plot_503_when_no_snapshot_yet(client, monkeypatch):
    monkeypatch.setattr(capture_mod, "is_running", lambda player: False)

    def boom(config, player, deed_uid):
        raise LookupError(f"no snapshot for {player}; capture the board first")

    monkeypatch.setattr(capture_mod, "expand_plot", boom)
    resp = client.post("/api/plots/some-deed/expand")
    assert resp.status_code == 503


def test_expand_plot_404_for_unknown_deed_uid(client, monkeypatch, report):
    monkeypatch.setattr(capture_mod, "is_running", lambda player: False)
    monkeypatch.setattr(capture_mod, "expand_plot", lambda config, player, deed_uid: report)
    resp = client.post("/api/plots/does-not-exist/expand")
    assert resp.status_code == 404
