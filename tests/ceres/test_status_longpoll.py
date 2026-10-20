"""GET /api/capture/status?wait=<ms> long-polls instead of returning an
immediate snapshot. F3 measured the client's fixed-interval status polling
(500 ms / 1 s / 2 s) adding round-trip waste on top of the capture's own wall time --
a poll landing just after the capture finished still slept the FULL interval before
asking again. `wait` makes the server itself block (short internal re-check cadence)
until the capture stops being busy or the wait budget runs out, so the client's next
`/api/board` fetch follows within the server's own re-check granularity of the capture
actually finishing, not a whole extra client-side interval.
"""

from __future__ import annotations

import re
import socket
import time

import pytest
from fastapi.testclient import TestClient

from ceres import capture as capture_mod
from ceres.app import create_app
from ceres.config import CeresConfig
from ceres.routes_board import CAPTURE_STATUS_WAIT_POLL_SECONDS


@pytest.fixture(autouse=True)
def block_real_sockets(monkeypatch):
    def _raise(*args, **kwargs):
        raise AssertionError("test_status_longpoll.py must not open a real outbound network connection")

    monkeypatch.setattr(socket, "create_connection", _raise)


@pytest.fixture
def config(tmp_path) -> CeresConfig:
    return CeresConfig(root=tmp_path / ".ceres", player="player0005")


@pytest.fixture
def client(config) -> TestClient:
    return TestClient(create_app(config))


def _status(state: str, **extra) -> "capture_mod.CaptureStatus":
    return capture_mod.CaptureStatus(state=state, started_at="2026-09-20T00:00:00Z", **extra)


def test_wait_returns_immediately_once_idle_without_wait_param(client, monkeypatch):
    """No `wait` -- unchanged, one immediate read (the pre- behaviour)."""
    monkeypatch.setattr(capture_mod, "freshness", lambda config, player: _status("idle"))
    resp = client.get("/api/capture/status")
    assert resp.status_code == 200
    assert resp.json()["state"] == "idle"


def test_wait_blocks_until_capture_turns_idle_then_returns_promptly(client, monkeypatch):
    """The capture turns idle partway through the wait budget -- the response must
 land close to that moment, not after the full `wait` window elapses."""
    calls = {"n": 0}
    turns_idle_after = 3  # freshness() calls; each internal re-check is CAPTURE_STATUS_WAIT_POLL_SECONDS apart

    def fake_freshness(config, player):
        calls["n"] += 1
        if calls["n"] >= turns_idle_after:
            return _status("idle")
        return _status("running", board_ready=False)

    monkeypatch.setattr(capture_mod, "freshness", fake_freshness)
    started = time.monotonic()
    resp = client.get("/api/capture/status", params={"wait": 2000})
    elapsed = time.monotonic() - started

    assert resp.status_code == 200
    assert resp.json()["state"] == "idle"
    expected_idle_at = (turns_idle_after - 1) * CAPTURE_STATUS_WAIT_POLL_SECONDS
    # Promptly: within one extra internal re-check tick of the moment it turned idle
    # nowhere near the full 2000 ms `wait` budget requested.
    assert elapsed < expected_idle_at + CAPTURE_STATUS_WAIT_POLL_SECONDS + 0.25
    assert elapsed < 2.0


def test_wait_respects_the_budget_when_capture_never_settles(client, monkeypatch):
    """A capture that never turns idle within the wait budget returns the last-seen
 (still running) status when the budget runs out -- it never blocks past `wait`."""
    monkeypatch.setattr(capture_mod, "freshness", lambda config, player: _status("running", board_ready=False))
    started = time.monotonic()
    resp = client.get("/api/capture/status", params={"wait": 150})
    elapsed = time.monotonic() - started

    assert resp.status_code == 200
    assert resp.json()["state"] == "running"
    assert 0.10 <= elapsed < 1.0  # bounded by the requested wait, not the 30-minute ceiling


def test_wait_is_capped_at_the_server_maximum(client, monkeypatch):
    """A caller asking for an absurd `wait` is bounded by CAPTURE_STATUS_WAIT_MAX_MS,
 not honoured verbatim -- otherwise one slow browser tab could tie up a worker
 thread indefinitely."""
    from ceres.routes_board import CAPTURE_STATUS_WAIT_MAX_MS

    monkeypatch.setattr(capture_mod, "freshness", lambda config, player: _status("running", board_ready=False))
    started = time.monotonic()
    resp = client.get("/api/capture/status", params={"wait": 10_000_000})
    elapsed = time.monotonic() - started

    assert resp.status_code == 200
    assert elapsed < (CAPTURE_STATUS_WAIT_MAX_MS / 1000) + 1.0


def test_wait_exits_early_on_board_ready_even_while_running(client, monkeypatch):
    """'s interim board_ready flag ends the wait too -- readBoard()'s own
 early-exit condition (board.js) must be honoured server-side as well, so the
 long-poll never outlives the moment the client itself would have stopped waiting.
 Explicit `wait_for=board_ready` (readBoard()'s own value -- see ) rather than
 relying on the parameter's default, so this test stays meaningful if the default
 ever changes."""
    calls = {"n": 0}

    def fake_freshness(config, player):
        calls["n"] += 1
        if calls["n"] >= 2:
            return _status("running", board_ready=True)
        return _status("running", board_ready=False)

    monkeypatch.setattr(capture_mod, "freshness", fake_freshness)
    started = time.monotonic()
    resp = client.get("/api/capture/status", params={"wait": 2000, "wait_for": "board_ready"})
    elapsed = time.monotonic() - started

    body = resp.json()
    assert body["state"] == "running" and body["board_ready"] is True
    assert elapsed < 0.5


def test_wait_for_settled_ignores_board_ready_and_blocks_until_state_leaves_running(client, monkeypatch):
    """ regression: `waitForCaptureToSettle()` exits on `state !== 'running'`,
 never on `board_ready` -- the interim board publishes and turns `board_ready` true
 WHILE `state` is still `running` through the self-heal tail (). With the
 `board_ready` wait semantics the server returned instantly on every call once that
 happened, and the settle loop -- which never sleeps client-side between `wait=`
 calls -- became a request-per-tick busy spin. `wait_for=settled` must keep blocking
 through that window and only end once the capture actually leaves running/queued."""
    calls = {"n": 0}
    turns_idle_after = 3

    def fake_freshness(config, player):
        calls["n"] += 1
        if calls["n"] >= turns_idle_after:
            return _status("idle")
        # board_ready true from the very first call -- the exact regression shape.
        return _status("running", board_ready=True)

    monkeypatch.setattr(capture_mod, "freshness", fake_freshness)
    started = time.monotonic()
    resp = client.get("/api/capture/status", params={"wait": 2000, "wait_for": "settled"})
    elapsed = time.monotonic() - started

    body = resp.json()
    assert body["state"] == "idle"
    # Must have actually re-checked across the internal cadence, not returned on the
    # first (board_ready-true-but-running) read.
    assert calls["n"] >= turns_idle_after
    assert (turns_idle_after - 1) * CAPTURE_STATUS_WAIT_POLL_SECONDS <= elapsed < 2.0


@pytest.mark.parametrize("bad_wait", ["not-a-number", "", "-50", "NaN", "1e3"])
def test_wait_coerces_garbage_to_zero_instead_of_422(client, monkeypatch, bad_wait):
    """a malformed `wait` (a hand-typed URL, a stale cached page sending an old
 param shape) must never 422 -- it behaves like `wait` was omitted (one immediate
 read), same as `wait=0`."""
    monkeypatch.setattr(capture_mod, "freshness", lambda config, player: _status("idle"))
    resp = client.get("/api/capture/status", params={"wait": bad_wait})
    assert resp.status_code == 200
    assert resp.json()["state"] == "idle"


def test_client_loops_carry_the_floor_sleep_and_settle_uses_wait_for_settled():
    """Source guard, no VM execution needed: both board.js polling loops must retain the
 floor sleep (defense-in-depth against a server that ignores `wait`, or any
 future `wait_for` mismatch), and the settle loop must pass `wait_for=settled` -- the
 root fix for the busy-spin regression this file's `test_wait_for_settled_...` test
 exercises server-side."""
    from .vm_context import STATIC

    source = (STATIC / "board.js").read_text(encoding="utf-8")
    read_board = source[source.index("export async function readBoard("):source.index("async function waitForCaptureToSettle(")]
    settle = source[source.index("async function waitForCaptureToSettle("):]
    settle = settle[:settle.index("\nexport async function switchPlayer(")]

    for name, body in (("readBoard", read_board), ("waitForCaptureToSettle", settle)):
        assert "CAPTURE_STATUS_FLOOR_MS" in body, f"{name} lost its floor sleep"
        assert re.search(
            r"abortableDelay\(\s*waitMs\s*-\s*elapsed\s*,\s*signal\s*\)", body,
        ), f"{name} floor sleep must be abortable and sleep the cadence remainder"

    assert "wait_for=settled" in settle, "waitForCaptureToSettle must pass wait_for=settled ( root fix)"
    assert "wait_for=settled" not in read_board, "readBoard must keep the default (board_ready) wait_for"
