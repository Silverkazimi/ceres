"""v1.0 C3: per-client limits, the "already running" answer, and the operator pause."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceres import capture, client_limits
from ceres.app import create_app
from ceres.capture import CaptureStatus
from ceres.config import CeresConfig


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(client_limits.ENV_SWITCH, raising=False)
    monkeypatch.delenv(client_limits.PAUSE_ENV, raising=False)
    client_limits._pause_cache.clear()


@pytest.fixture
def app(tmp_path):
    return create_app(CeresConfig(root=tmp_path / "home"))


def _client(app, host="198.51.100.1"):
    return TestClient(app, client=(host, 40000))


@pytest.fixture
def estimate_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(capture, "estimate_capture", lambda player: calls.append(player) or {"deeds": 1})
    return calls


def test_limiter_windows_and_retry_after():
    limiter = client_limits.Limiter()
    for i in range(3):
        assert limiter.hit("capture_full", "a", now=100.0 + i) is None
    wait = limiter.hit("capture_full", "a", now=103.0)
    assert wait == pytest.approx(3600.0 - 3.0)
    assert limiter.hit("capture_full", "b", now=103.0) is None  # another client has its own window
    assert limiter.hit("capture_full", "a", now=3700.5) is None  # the oldest event has aged out


def test_bucket_refuses_at_limit_plus_one_without_an_upstream_call(app, estimate_calls):
    client = _client(app)
    for _ in range(20):
        assert client.get("/api/capture/estimate?player=sample01").status_code == 200
    refused = client.get("/api/capture/estimate?player=sample01")
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    assert len(estimate_calls) == 20


def test_limits_off_switch(app, estimate_calls, monkeypatch):
    monkeypatch.setenv(client_limits.ENV_SWITCH, "off")
    client = _client(app)
    assert all(client.get("/api/capture/estimate?player=sample01").status_code == 200 for _ in range(25))


def test_spoofed_forwarded_for_does_not_change_the_key(app, estimate_calls):
    client = _client(app)
    for i in range(20):
        client.get("/api/capture/estimate?player=sample01", headers={"X-Forwarded-For": f"203.0.113.{i}"})
    assert client.get("/api/capture/estimate?player=sample01", headers={"X-Forwarded-For": "203.0.113.99"}).status_code == 429
    assert _client(app, "198.51.100.2").get("/api/capture/estimate?player=sample01").status_code == 200


@pytest.fixture
def capture_stub(monkeypatch):
    running: dict[str, str] = {}
    starts = []

    def start_refresh(config, player, **_):
        starts.append(player)
        running.clear()
        running[player] = "running"
        return True

    monkeypatch.setattr(capture, "start_refresh", start_refresh)
    monkeypatch.setattr(capture, "freshness", lambda config, player: CaptureStatus(state=running.get(player, "idle")))
    return starts, running


def test_run_owner_rules(app, capture_stub):
    starts, running = capture_stub
    owner, stranger = _client(app, "198.51.100.1"), _client(app, "198.51.100.2")
    assert owner.post("/api/capture/refresh?player=sample01").status_code == 202
    joined = stranger.post("/api/capture/refresh?player=sample01")
    assert joined.status_code == 409 and joined.json()["state"] == "running"  # the page polls status and joins
    refused = stranger.post("/api/capture/refresh?player=sample02")
    assert refused.status_code == 429 and int(refused.headers["Retry-After"]) >= 5
    assert owner.post("/api/capture/refresh?player=sample02").status_code == 202  # the maintainer still supersedes
    assert starts == ["sample01", "sample02"]
    running.clear()  # nothing running any more: anyone may start
    assert stranger.post("/api/capture/refresh?player=sample03").status_code == 202


def test_pause_by_env_and_file(app, tmp_path, monkeypatch, estimate_calls):
    stops = []
    monkeypatch.setattr(capture._supervisor, "shutdown", lambda: stops.append(1))
    client = _client(app)
    monkeypatch.setenv(client_limits.PAUSE_ENV, "1")
    paused = client.get("/api/capture/estimate?player=sample01")
    assert paused.status_code == 503 and paused.json()["detail"] == client_limits.PAUSE_DETAIL
    health = client.get("/api/health")
    assert health.status_code == 200 and health.json()["paused"] is True
    assert stops == [1]  # the running read is stopped once, on entering the pause
    monkeypatch.delenv(client_limits.PAUSE_ENV)
    client_limits._pause_cache.clear()
    assert client.get("/api/capture/estimate?player=sample01").status_code == 200

    flag = tmp_path / "home" / client_limits.PAUSE_FILE
    flag.write_text("maintenance\n")
    time.sleep(1.1)  # the file is re-checked at most once a second
    assert client.get("/api/capture/estimate?player=sample01").status_code == 503
    assert client.get("/").status_code == 200  # the page itself still loads and can show the message
    flag.unlink()
    time.sleep(1.1)
    assert client.get("/api/capture/estimate?player=sample01").status_code == 200
    assert client.get("/api/health").json()["paused"] is False


def _behind_proxy(app, peer):
    """The app as uvicorn serves it: ProxyHeadersMiddleware trusting 127.0.0.1 (ceres/__main__.py)."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    return TestClient(ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1"), client=(peer, 40000))


def test_visitors_behind_the_local_proxy_are_told_apart(app, estimate_calls):
    proxy = _behind_proxy(app, "127.0.0.1")
    for _ in range(20):
        assert proxy.get("/api/capture/estimate?player=sample01", headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 200
    assert proxy.get("/api/capture/estimate?player=sample01", headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 429
    # another visitor through the same proxy has its own window
    assert proxy.get("/api/capture/estimate?player=sample01", headers={"X-Forwarded-For": "198.51.100.8"}).status_code == 200
    # a visitor prepending a fake address still lands on its real (rightmost) address
    spoof = proxy.get("/api/capture/estimate?player=sample01", headers={"X-Forwarded-For": "203.0.113.1, 198.51.100.7"})
    assert spoof.status_code == 429


def test_forwarded_for_from_an_untrusted_peer_is_ignored(app, estimate_calls):
    direct = _behind_proxy(app, "198.51.100.20")
    for i in range(20):
        direct.get("/api/capture/estimate?player=sample01", headers={"X-Forwarded-For": f"203.0.113.{i}"})
    assert direct.get("/api/capture/estimate?player=sample01", headers={"X-Forwarded-For": "203.0.113.99"}).status_code == 429


def test_run_owner_rules_hold_for_two_visitors_behind_the_proxy(app, capture_stub):
    proxy = _behind_proxy(app, "127.0.0.1")
    first = {"X-Forwarded-For": "198.51.100.7"}
    second = {"X-Forwarded-For": "198.51.100.8"}
    assert proxy.post("/api/capture/refresh?player=sample01", headers=first).status_code == 202
    assert proxy.post("/api/capture/refresh?player=sample02", headers=second).status_code == 429  # not a restart
    assert proxy.post("/api/capture/refresh?player=sample01", headers=second).status_code == 409  # joins


def test_main_trusts_exactly_the_local_proxy(monkeypatch):
    import sys

    import ceres.__main__ as ceres_main

    seen = {}
    monkeypatch.setattr(sys, "argv", ["ceres", "--no-reload-watch"])
    monkeypatch.setattr(ceres_main, "create_app", lambda config: object())
    monkeypatch.setattr(ceres_main, "configure_logging", lambda *a, **k: None)
    monkeypatch.setattr(ceres_main.uvicorn, "run", lambda app, **kw: seen.update(kw))
    ceres_main.main()
    assert seen["proxy_headers"] is True and seen["forwarded_allow_ips"] == "127.0.0.1"
