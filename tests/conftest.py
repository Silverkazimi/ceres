"""Suite-wide test selection and the CI network guard.

Every page test that drives a real browser is marked ``browser``; every test module that talks to the game's API
directly is marked ``live``. CI runs ``-m "not browser and not live"``. With ``CERES_CI_NO_NETWORK=1`` any outbound
connection other than loopback (and local sockets) is refused, so "no live calls" is proven rather than assumed.
"""
from __future__ import annotations

import ipaddress
import json
import math
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


class _Shard:
    @pytest.hookimpl(trylast=True)  # after `-m` deselection, so shards split only the selected tests
    def pytest_collection_modifyitems(self, config, items):
        _shard_collection(config, items)


def pytest_configure(config):
    config.pluginmanager.register(_Shard(), "ceres-shard")


_DURATIONS = os.path.join(os.path.dirname(__file__), "ceres", "browser_durations.json")


def _shard_collection(config, items):
    """`CERES_TEST_SHARD=i/n` (1-based) keeps only shard i of n.

    Files are weighted by their measured duration (tests/ceres/browser_durations.json, seconds per file;
    a file not listed counts 8 s per test, about the measured average). A file heavier than the per-shard target is cut into consecutive
    chunks of its tests; the chunks and whole files are then placed longest first on the lightest shard. Every
    shard collects the same items, so the split is identical on every runner and each test runs exactly once."""
    spec = os.environ.get("CERES_TEST_SHARD", "").strip()
    if not spec:
        return
    index, count = (int(part) for part in spec.split("/"))
    if not 1 <= index <= count:
        raise pytest.UsageError(f"CERES_TEST_SHARD must be i/n with 1 <= i <= n, got {spec!r}")
    try:
        with open(_DURATIONS, encoding="utf-8") as handle:
            durations = json.load(handle)
    except (OSError, ValueError):
        durations = {}
    by_file: dict[str, list] = {}
    for item in items:
        by_file.setdefault(item.nodeid.split("::")[0], []).append(item)
    weight = {path: float(durations.get(path, 8 * len(tests))) for path, tests in by_file.items()}
    target = sum(weight.values()) / count
    chunks = []  # (weight, path, first, last)
    for path, tests in by_file.items():
        pieces = max(1, min(len(tests), math.ceil(weight[path] / target)))
        size = math.ceil(len(tests) / pieces)
        for start in range(0, len(tests), size):
            part = tests[start:start + size]
            chunks.append((weight[path] * len(part) / len(tests), path, start, start + len(part)))
    loads = [0.0] * count
    keep_ids = set()
    for chunk_weight, path, first, last in sorted(chunks, key=lambda chunk: (-chunk[0], chunk[1], chunk[2])):
        target_shard = loads.index(min(loads))
        loads[target_shard] += chunk_weight
        if target_shard == index - 1:
            keep_ids.update(id(item) for item in by_file[path][first:last])
    keep = [item for item in items if id(item) in keep_ids]
    dropped = [item for item in items if id(item) not in keep_ids]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = keep


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
