"""Suite-wide test selection and the CI network guard.

Every page test that drives a real browser is marked ``browser``; every test module that talks to the game's API
directly is marked ``live``. CI runs ``-m "not browser and not live"``. With ``CERES_CI_NO_NETWORK=1`` any outbound
connection other than loopback (and local sockets) is refused, so "no live calls" is proven rather than assumed.
"""
from __future__ import annotations

import ipaddress
import os
import socket

import pytest

_BROWSER_MARKS = ("live_browser", "spawn_live_ceres", "playwright")
_LIVE_MARKS = ("PRAETORIA_LIVE", "CERES_RUN_SLOW", "CERES_RUN_PERF")
_SOURCE_CACHE: dict[str, str] = {}


def _module_source(item) -> str:
    path = str(getattr(item, "fspath", "") or "")
    if path not in _SOURCE_CACHE:
        try:
            with open(path, encoding="utf-8") as handle:
                _SOURCE_CACHE[path] = handle.read()
        except OSError:
            _SOURCE_CACHE[path] = ""
    return _SOURCE_CACHE[path]


def pytest_collection_modifyitems(config, items):
    for item in items:
        fixtures = set(getattr(item, "fixturenames", ()) or ())
        source = _module_source(item)
        if fixtures & {"live_browser"} or any(mark in source for mark in _BROWSER_MARKS):
            item.add_marker(pytest.mark.browser)
        if any(mark in source for mark in _LIVE_MARKS):
            item.add_marker(pytest.mark.live)


def _loopback(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return True  # unix sockets and other local families
    host = str(address[0])
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _ci_no_network(monkeypatch):
    if os.environ.get("CERES_CI_NO_NETWORK") != "1":
        yield
        return
    original = socket.socket.connect

    def guarded(self, address):
        if not _loopback(address):
            raise OSError(f"network access refused in CI: {address!r}")
        return original(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield
