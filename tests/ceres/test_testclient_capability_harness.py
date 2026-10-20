from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from fastapi.testclient import TestClient
from tests.ceres import conftest
from tests.ceres.conftest import (
    _probe_testclient_capability,
    _probe_testclient_socket,
    _testclient_init_for_capability,
)


class _FakeSocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _restore_probe_elapsed(monkeypatch):
    monkeypatch.setattr(conftest, "_TESTCLIENT_PROBE_ELAPSED", conftest._TESTCLIENT_PROBE_ELAPSED)


def test_socket_probe_closes_a_successful_socket():
    created = []

    def socket_factory():
        sock = _FakeSocket()
        created.append(sock)
        return sock

    assert _probe_testclient_socket(socket_factory) is True
    assert created[0].closed is True


def test_socket_probe_reports_permission_error_as_unavailable():
    def socket_factory():
        raise PermissionError("socket creation denied")

    assert _probe_testclient_socket(socket_factory) is False
    portal_probes = []
    assert _probe_testclient_capability(
        socket_factory, portal_probe=lambda: portal_probes.append(True)
    ) is False
    assert portal_probes == []


def test_capability_probe_runs_portal_probe_after_socket_probe():
    portal_probes = []

    def portal_probe():
        portal_probes.append(True)

    assert _probe_testclient_capability(lambda: _FakeSocket(), portal_probe=portal_probe) is True
    assert portal_probes == [True]


def test_slow_successful_probe_does_not_exclude_testclient(monkeypatch):
    clock = [100.0]
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs["timeout"])
        clock[0] += 10.0
        if kwargs["timeout"] < 10:
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(conftest.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(conftest.subprocess, "run", run)

    capable = _probe_testclient_capability(lambda: _FakeSocket())
    assert capable is True
    assert calls == [30]
    initialized = []
    _testclient_init_for_capability(lambda self: initialized.append(True), capable=capable)(object())
    assert initialized == [True]
    monkeypatch.setattr(conftest, "_TESTCLIENT_SOCKET_CAPABLE", capable)
    conftest.require_testclient_socket()


@pytest.mark.parametrize("failure", ["timeout", "exit"])
def test_failing_probe_excludes_with_measured_time(monkeypatch, failure):
    clock = [100.0]
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs["timeout"])
        if failure == "timeout":
            clock[0] += kwargs["timeout"]
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        clock[0] += 6.2
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(conftest.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(conftest.subprocess, "run", run)

    capable = _probe_testclient_capability(lambda: _FakeSocket())
    assert capable is False
    assert calls == [30, 30]
    monkeypatch.setattr(conftest, "_TESTCLIENT_SOCKET_CAPABLE", capable)
    guarded_init = _testclient_init_for_capability(lambda self: None, capable=capable)
    elapsed = "60.0" if failure == "timeout" else "12.4"
    for guard in (conftest.require_testclient_socket, lambda: guarded_init(object())):
        with pytest.raises(pytest.skip.Exception) as skipped:
            guard()
        assert "CERES_TESTCLIENT_SOCKET_EXCLUSION" in str(skipped.value)
        assert f"probe took {elapsed} s of 60 s (30 s per attempt)" in str(skipped.value)


def test_capability_probe_retries_after_timeout():
    calls = []

    def portal_probe():
        calls.append(True)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("portal", 30)

    assert _probe_testclient_capability(lambda: _FakeSocket(), portal_probe=portal_probe) is True
    assert len(calls) == 2


def test_portal_probe_uses_a_bounded_subprocess(monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(conftest.subprocess, "run", run)

    with pytest.raises(subprocess.TimeoutExpired):
        conftest._run_testclient_portal_probe()

    assert calls[0][1]["timeout"] == 30
    assert calls[0][1]["stdin"] is subprocess.DEVNULL


def test_capable_machine_does_not_exclude_testclient():
    initialized = []

    def original_init(instance, *args, **kwargs):
        initialized.append((args, kwargs))

    guarded_init = _testclient_init_for_capability(original_init, capable=True)
    guarded_init(object(), "app", follow_redirects=True)

    assert initialized == [(('app',), {"follow_redirects": True})]


def test_capability_guard_mutation_is_red():
    def original_init(instance, *args, **kwargs):
        return None

    injected_exclusion = _testclient_init_for_capability(original_init, capable=False)
    with pytest.raises(pytest.skip.Exception, match="CERES_TESTCLIENT_SOCKET_EXCLUSION"):
        injected_exclusion(object())


def test_pytest_configure_capable_branch_does_not_install_guard(monkeypatch):
    previous_init = TestClient.__init__
    previous_saved_init = conftest._TESTCLIENT_INIT
    previous_excluded_nodeids = conftest._TESTCLIENT_EXCLUDED_NODEIDS
    previous_capable = conftest._TESTCLIENT_SOCKET_CAPABLE
    conftest._TESTCLIENT_INIT = None
    conftest._TESTCLIENT_EXCLUDED_NODEIDS = []
    monkeypatch.setattr(conftest, "_probe_testclient_capability", lambda: True)
    try:
        conftest.pytest_configure(SimpleNamespace())
        assert TestClient.__init__ is previous_init
        assert conftest._TESTCLIENT_SOCKET_CAPABLE is True
        assert conftest._TESTCLIENT_INIT is None
        assert conftest._TESTCLIENT_EXCLUDED_NODEIDS == []
    finally:
        TestClient.__init__ = previous_init
        conftest._TESTCLIENT_INIT = previous_saved_init
        conftest._TESTCLIENT_EXCLUDED_NODEIDS = previous_excluded_nodeids
        conftest._TESTCLIENT_SOCKET_CAPABLE = previous_capable
