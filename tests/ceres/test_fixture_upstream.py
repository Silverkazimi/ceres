""": fixture-backed upstream -- served from files, fail closed, no socket, refused off loopback."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceres import fixture_upstream
from ceres.config import CeresConfig
from tests.ceres.fixture_upstream_mini import SAMPLE, board_entries, entry, write_set

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))  # a leak would land here, not in the real home
    monkeypatch.delenv(fixture_upstream.ENV, raising=False)
    monkeypatch.delenv(fixture_upstream.CACHE_ENV, raising=False)
    monkeypatch.delenv(fixture_upstream.ALLOW_PRODUCTION_FLAGS_ENV, raising=False)
    monkeypatch.delenv("CERES_DEV_WATCH", raising=False)
    monkeypatch.delenv("CERES_CARD_ART_CACHE", raising=False)
    yield
    fixture_upstream.uninstall()


@pytest.fixture
def no_network(monkeypatch):
    """Any non-loopback socket connect fails the test: fixture mode must never open one."""
    real = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"network attempted in fixture mode: {address}")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def mini(tmp_path) -> Path:
    return write_set(tmp_path / "mini", board_entries())


def test_recorded_get_is_served_and_missing_fails_closed(mini, no_network):
    from praetoria.land_analysis import LandClient

    fixture_upstream.install(mini)
    bundle = LandClient(retries=1).bundle(SAMPLE).data
    assert bundle["data"]["deeds"][0]["player"] == SAMPLE
    with pytest.raises(Exception) as caught:
        LandClient(retries=1).owned_deed_counts(SAMPLE)
    assert "599" in str(caught.value)
    assert any("/land/deeds/owned/" in label for label in fixture_upstream.active().missing)


def test_quick_capture_completes_offline_in_process(mini, tmp_path, no_network):
    from ceres import capture

    fixture_upstream.install(mini)
    config = CeresConfig(root=tmp_path / "home")
    config.ensure_dirs()
    events = []
    capture._run_capture_body(config, SAMPLE, "quick", lambda kind, **kw: events.append(kind))
    assert "board_ready" in events and events[-1] == "done"
    assert fixture_upstream.active().missing == []


def test_capture_worker_child_uses_the_same_set(mini, tmp_path):
    """The supervised capture runs in a child process; it must answer from the fixture set too."""
    from ceres import capture

    fixture_upstream.install(mini)
    config = CeresConfig(root=tmp_path / "home")
    config.ensure_dirs()
    assert capture.start_refresh(config, SAMPLE)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = capture.freshness(config, SAMPLE)
        if status.state not in ("running", "queued"):
            break
        time.sleep(0.2)
    assert status.state not in ("running", "queued", "failed"), status
    snapshot = capture._latest_path(config, SAMPLE).read_text(encoding="utf-8")
    assert "I-296-b0dbc1ebc5ef75" in snapshot  # a deed only the fixture set knows


def test_json_rpc_entries_are_keyed_by_method_and_params(tmp_path, no_network):
    from ceres import hive_rpc

    rpc = {"method": "database_api.get_dynamic_global_properties", "params": {}}
    body = {"jsonrpc": "2.0", "id": 1, "result": {"last_irreversible_block_num": 123}}
    fixture_upstream.install(write_set(tmp_path / "rpc", [entry("api.hive.blog", "/", body, method="POST", rpc=rpc)]))
    assert hive_rpc._rpc("database_api.get_dynamic_global_properties", {})["last_irreversible_block_num"] == 123


def test_fides_rpc_is_served_from_the_set(tmp_path, no_network):
    from fides import verify

    rpc = {"method": "condenser_api.get_transaction", "params": ["ab" * 20]}
    body = {"jsonrpc": "2.0", "id": 1, "result": {"block_num": 7}}
    fixture_upstream.install(write_set(tmp_path / "fides", [entry("api.hive.blog", "/", body, method="POST", rpc=rpc)]))
    assert verify._http_rpc_call(verify.HIVE_NODE_DEFAULT, rpc["method"], rpc["params"]) == {"block_num": 7}


def test_time_shift_keeps_relative_deadlines(tmp_path):
    recorded = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    ends = recorded + timedelta(hours=2)
    body = {"projected_end": ends.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "block_num": 5, "ms": 1_000}
    root = write_set(tmp_path / "shift", [entry("vapi.splinterlands.com", "/x", body)],
                     recorded_at=recorded.isoformat().replace("+00:00", "Z"), time_shift=True)
    now = recorded + timedelta(days=3)
    loaded = fixture_upstream.FixtureSet(root, now=now)
    status, _headers, data = loaded.lookup("GET", "vapi.splinterlands.com", "/x", None)
    shifted = json.loads(data)
    assert status == 200
    assert shifted["projected_end"] == (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert shifted["block_num"] == 5 and shifted["ms"] == 1_000  # only listed epoch keys shift


def test_startup_refusals(mini, monkeypatch, tmp_path):
    with pytest.raises(fixture_upstream.FixtureRefusal, match="not loopback"):
        fixture_upstream.check_startup("0.0.0.0", mini)
    with pytest.raises(fixture_upstream.FixtureRefusal, match="fixture-manifest"):
        fixture_upstream.check_startup("127.0.0.1", tmp_path / "nothing-here")
    monkeypatch.setenv("CERES_DEV_WATCH", "0")
    with pytest.raises(fixture_upstream.FixtureRefusal, match="production"):
        fixture_upstream.check_startup("127.0.0.1", mini)
    monkeypatch.setenv(fixture_upstream.ALLOW_PRODUCTION_FLAGS_ENV, "1")
    assert fixture_upstream.check_startup("127.0.0.1", mini) == mini
    assert fixture_upstream.check_startup("0.0.0.0", None) is None  # off unless asked for


def test_create_app_refuses_a_public_bind(mini, tmp_path):
    from ceres.app import create_app

    with pytest.raises(fixture_upstream.FixtureRefusal):
        create_app(CeresConfig(root=tmp_path / "home", host="0.0.0.0", fixture_upstream=mini))


def test_cli_exits_2_on_a_public_bind(mini, tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), fixture_upstream.ENV: str(mini),
           "PYTHONPATH": ":".join(p for p in sys.path if p)}
    result = subprocess.run([sys.executable, "-m", "ceres", "--host", "0.0.0.0", "--port", "0", "--no-reload-watch"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 2
    assert "not loopback" in result.stderr


def test_app_in_fixture_mode_flags_health_and_refuses_proxied_requests(mini, tmp_path):
    from ceres.app import create_app

    client = TestClient(create_app(CeresConfig(root=tmp_path / "home", player=SAMPLE, fixture_upstream=mini)))
    assert client.get("/api/health").json()["fixture_upstream"] == "mini"
    refused = client.get("/api/health", headers={"X-Forwarded-For": "203.0.113.9"})
    assert refused.status_code == 503


def test_health_has_no_fixture_key_normally(tmp_path):
    from ceres.app import create_app

    assert "fixture_upstream" not in TestClient(create_app(CeresConfig(root=tmp_path / "home"))).get("/api/health").json()


def test_fixture_mode_turns_the_card_art_cache_off(mini, tmp_path):
    assert CeresConfig(root=tmp_path, fixture_upstream=mini).card_art_cache_enabled is False


def test_fixture_capture_never_touches_the_real_praetoria_cache(mini, tmp_path, monkeypatch):
    """Catalogues are cached for a day under ~/.praetoria: fixture mode must neither read nor write there."""
    from ceres import capture

    home = tmp_path / "fake-home"
    stale = home / ".praetoria" / "cache"
    stale.mkdir(parents=True)
    (stale / "card_details.json").write_text('{"cached_at": 9999999999, "data": []}', encoding="utf-8")
    fixture_upstream.install(mini)
    config = CeresConfig(root=tmp_path / "home")
    config.ensure_dirs()
    events = []
    capture._run_capture_body(config, SAMPLE, "quick", lambda kind, **kw: events.append(kind))
    assert events[-1] == "done"
    assert sorted(p.name for p in stale.iterdir()) == ["card_details.json"]
    assert "ceres-fixture-cache-" in __import__("os").environ[fixture_upstream.CACHE_ENV]
    fixture_upstream.uninstall()
    assert fixture_upstream.CACHE_ENV not in __import__("os").environ
