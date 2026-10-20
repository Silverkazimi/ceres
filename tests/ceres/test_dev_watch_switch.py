""" `CERES_DEV_WATCH=0` silences the dev loop; the default keeps it exactly as before."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ceres import routes_board
from ceres.app import create_app
from ceres.config import CeresConfig


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005")))


def _forbid_scan(monkeypatch):
    def _boom():
        raise AssertionError("watched_paths() must not be called when CERES_DEV_WATCH=0")
    monkeypatch.setattr(routes_board, "watched_paths", _boom)


def test_off_returns_enabled_false_without_scanning(client, monkeypatch):
    monkeypatch.setenv("CERES_DEV_WATCH", "0")
    _forbid_scan(monkeypatch)
    body = client.get("/api/dev/watch").json()
    assert body["enabled"] is False
    assert "hash" not in body
    assert client.get("/api/health").json()["dev_watch"] is False


def test_default_keeps_polling_contract(client, monkeypatch):
    monkeypatch.delenv("CERES_DEV_WATCH", raising=False)
    body = client.get("/api/dev/watch").json()
    assert body["enabled"] is True
    assert len(body["hash"]) == 32 and body["version"]
    assert client.get("/api/health").json()["dev_watch"] is True


def test_cli_flag_is_the_same_switch(monkeypatch):
    from ceres import __main__ as cli
    monkeypatch.delenv("CERES_DEV_WATCH", raising=False)
    monkeypatch.setattr(cli, "start_reload_watch", lambda: (_ for _ in ()).throw(AssertionError("watcher started")))
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(cli, "configure_logging", lambda *a, **k: None)
    monkeypatch.setattr("sys.argv", ["ceres", "--no-reload-watch"])
    cli.main()
    assert routes_board.dev_watch_enabled() is False
