""" (2026-09-14): the board read runs in a memory-capped worker child
(`python -m ceres.capture_worker`), one heavy job at a time, driven by
`ceres.capture.CaptureSupervisor`. These tests run REAL child processes; the child is
given a fake `LandCapture` through the worker's `worker_setup` hook (`_worker_setup`
below, named by `CERES_CAPTURE_WORKER_SETUP`). The fake's behaviour is chosen by the
player name: `s7700-ok`, `s7700-slow` (waits until terminated), `s7700-mem` (allocates past the cap),
`s7700-kill` (SIGKILLs itself mid-read)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ceres import capture as capture_mod
from ceres.config import CeresConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SETUP = "tests.ceres.test_capture_worker:_worker_setup"
PLAYERS = ("s7700-ok", "s7700-slow", "s7700-mem", "s7700-kill", "s7700-second")


def _fake_report(player: str) -> dict:
    # The real offline fixture report (tests/fixtures/land), so the artifact projection has
    # every field it needs; only the player name is rewritten.
    from tests.ceres.conftest import build_report_quick

    report = build_report_quick()
    report["query"] = {**(report.get("query") or {}), "player": player}
    report["capture"] = {"requests": 5, "cache_hits": 1}
    return report


class _FakeLandCapture:
    def __init__(self, *args, **kwargs):
        self.on_deed_count = None
        self.on_bulk_ready = None
        self.request_budget = None

    def capture_quick(self, player, include_asset_ids=False):
        behaviour = player.rsplit("-", 1)[-1]
        report = _fake_report(player)
        if self.on_deed_count:
            self.on_deed_count(len(report["plots"]))
        if behaviour == "slow":
            ready_marker = os.getenv("S7700_SLOW_READY_MARKER")
            terminated_marker = os.getenv("S7700_SLOW_TERMINATED_MARKER")
            if not ready_marker or not terminated_marker:
                time.sleep(1.2)
                return report
            ready_path = Path(ready_marker)
            terminated_path = Path(terminated_marker)

            def record_termination(signum, frame):
                terminated_path.write_text(str(os.getpid()), encoding="ascii")
                os._exit(128 + signum)

            signal.signal(signal.SIGTERM, record_termination)
            ready_path.write_text(str(os.getpid()), encoding="ascii")
            time.sleep(30)
        elif behaviour == "mem":
            hog = bytearray(int(os.environ["S7700_ALLOC_MB"]) * 1024 * 1024)  # past RLIMIT_DATA -> MemoryError
            hog[0] = 1
        elif behaviour == "kill":
            os.kill(os.getpid(), signal.SIGKILL)
        return report

    def repair_assets(self, queued, player):
        return _fake_report(player)


def _worker_setup(capture_module, job) -> None:
    """Runs INSIDE the child before the read (see ceres.capture_worker)."""
    from ceres import board_artifact

    capture_module.LandCapture = _FakeLandCapture
    capture_module._fetch_balances = lambda player: None
    capture_module._refresh_card_details = lambda: None
    original_hook = capture_module._board_ready_hook
    snapshots_dir = Path(job["root"]) / "snapshots"

    def wrapped_hook(emit, report):
        inner = original_hook(emit, report)

        def hook(identity=None):
            path = board_artifact.artifact_path(snapshots_dir, job["player"], identity.token)
            emit("test_artifact_check", exists=path.exists(), gz_exists=path.with_name(path.name + ".gz").exists())
            inner(identity)
        return hook

    capture_module._board_ready_hook = wrapped_hook


@pytest.fixture(autouse=True)
def _subprocess_mode(monkeypatch):
    monkeypatch.setenv("CERES_CAPTURE_IN_PROCESS", "0")
    monkeypatch.setenv("CERES_CAPTURE_WORKER_SETUP", SETUP)
    monkeypatch.setenv("CERES_CAPTURE_MEMORY_MB", "256")
    monkeypatch.setenv("S7700_ALLOC_MB", "700")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in [str(PROJECT_ROOT), os.environ.get("PYTHONPATH", "")] if p))
    yield
    with capture_mod._lock:
        for player in PLAYERS:
            capture_mod._run_state.pop(player, None)
    with capture_mod._supervisor._guard:
        capture_mod._supervisor._client_generations.clear()


@pytest.fixture
def config(tmp_path) -> CeresConfig:
    return CeresConfig(root=tmp_path / ".ceres", player="s7700-ok")


def _run_worker(job: dict) -> tuple[int, list[dict]]:
    proc = subprocess.run(
        [sys.executable, "-m", "ceres.capture_worker"], input=json.dumps(job), text=True,
        capture_output=True, cwd=str(PROJECT_ROOT), env=dict(os.environ), timeout=60,
    )
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    return proc.returncode, events


def _job(tmp_path: Path, player: str, memory_mb: int = 256) -> dict:
    return {"player": player, "mode": "quick", "root": str(tmp_path / ".ceres"), "stale_after_seconds": 60,
            "memory_mb": memory_mb, "known_plots": None, "worker_setup": SETUP}


def _wait_settled(config: CeresConfig, player: str, timeout: float = 30.0) -> capture_mod.CaptureStatus:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = capture_mod.freshness(config, player)
        if status.state not in ("running", "queued"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"{player} did not settle: {capture_mod.freshness(config, player).as_dict()}")


# (a) worker protocol

def test_worker_emits_events_in_order_and_board_ready_after_artifact(tmp_path):
    code, events = _run_worker(_job(tmp_path, "s7700-ok"))
    assert code == 0, events
    names = [event["event"] for event in events]
    assert names[0] == "phase" and events[0]["phase"] == "plots"
    assert names.index("deed_count") < names.index("board_ready") < names.index("done")
    assert events[names.index("deed_count")]["total"] == 11
    check = events[names.index("board_ready") - 1]
    assert check["event"] == "test_artifact_check" and check["exists"] is True and check["gz_exists"] is True
    ready = events[names.index("board_ready")]
    assert ready["token"] and ready["generated_at"]
    done = events[-1]
    assert done["event"] == "done" and done["total_deeds"] == 11 and done["requests"] == 5
    assert (tmp_path / ".ceres" / "snapshots").exists()


# (c) memory cap

def test_worker_over_the_cap_exits_3_with_the_memory_message(tmp_path):
    code, events = _run_worker(_job(tmp_path, "s7700-mem", memory_mb=256))
    assert code == capture_mod.WORKER_EXIT_MEMORY
    failed = [event for event in events if event["event"] == "failed"]
    assert failed and failed[-1]["error"] == "memory limit 256 MB exceeded"
    assert failed[-1]["outcome"]["code"] == "memory_limit"


# (b) supervisor

def test_second_player_supersedes_and_reaps_the_active_worker_promptly(config, monkeypatch, tmp_path):
    ready_path = tmp_path / "slow-ready"
    terminated_path = tmp_path / "slow-terminated"
    monkeypatch.setenv("S7700_SLOW_READY_MARKER", str(ready_path))
    monkeypatch.setenv("S7700_SLOW_TERMINATED_MARKER", str(terminated_path))
    assert capture_mod.start_refresh(config, "s7700-slow") is True
    deadline = time.monotonic() + 5
    while not ready_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready_path.exists()
    slow_pid = int(ready_path.read_text(encoding="ascii"))
    with capture_mod._supervisor._guard:
        displaced_child = capture_mod._supervisor._child
    assert displaced_child is not None and displaced_child.pid == slow_pid
    replacement_started = time.monotonic()
    assert capture_mod.start_refresh(config, "s7700-second") is True
    displaced = _wait_settled(config, "s7700-slow")
    assert displaced.state == "failed"
    assert displaced.outcome == {"code": "superseded"}
    second = _wait_settled(config, "s7700-second", timeout=8)
    assert time.monotonic() - replacement_started < 5
    assert second.state == "idle" and second.queue_position is None
    assert terminated_path.read_text(encoding="ascii") == str(slow_pid)
    with pytest.raises(ProcessLookupError):
        os.kill(slow_pid, 0)
    assert displaced_child.poll() is not None
    assert displaced_child.stdin is not None and displaced_child.stdin.closed
    assert displaced_child.stdout is not None and displaced_child.stdout.closed


def test_same_player_refresh_replaces_the_active_run_token(config):
    assert capture_mod.start_refresh(config, "s7700-slow") is True
    deadline = time.monotonic() + 5
    while capture_mod.freshness(config, "s7700-slow").state != "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert capture_mod.freshness(config, "s7700-slow").state == "running"
    with capture_mod._lock:
        first_token = capture_mod._run_state["s7700-slow"].run_token
    assert capture_mod.start_refresh(config, "s7700-slow") is True
    with capture_mod._lock:
        replacement_token = capture_mod._run_state["s7700-slow"].run_token
    assert replacement_token is not first_token
    replacement = _wait_settled(config, "s7700-slow")
    assert replacement.state == "idle" and replacement.total_deeds == 11


def test_stale_browser_generation_does_not_supersede_the_current_job(config):
    load_id = "s7700-browser-order"
    assert capture_mod.start_refresh(
        config, "s7700-slow", load_id=load_id, load_generation=2,
    ) is True
    deadline = time.monotonic() + 5
    while capture_mod.freshness(config, "s7700-slow").state != "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    with capture_mod._lock:
        current_token = capture_mod._run_state["s7700-slow"].run_token
    assert capture_mod.start_refresh(
        config, "s7700-second", load_id=load_id, load_generation=1,
    ) is False
    with capture_mod._lock:
        assert capture_mod._run_state["s7700-slow"].run_token is current_token
        assert "s7700-second" not in capture_mod._run_state
    current = _wait_settled(config, "s7700-slow")
    assert current.state == "idle" and current.total_deeds == 11


def test_stale_driver_events_cannot_overwrite_a_replacement_run(config):
    old_token = object()
    replacement_token = object()
    driver = capture_mod._RunStateDriver(
        config, "s7700-second", "quick", "old-start", external=True, run_token=old_token,
    )
    with capture_mod._lock:
        capture_mod._run_state["s7700-second"] = capture_mod._RunState(
            state="running", mode="quick", started_at="replacement-start", run_token=replacement_token,
        )
    driver("phase", phase="gaps")
    driver("board_ready")
    driver("done", requests=99, cache_hits=0, total_deeds=99)
    with capture_mod._lock:
        replacement = capture_mod._run_state["s7700-second"]
    assert replacement.state == "running"
    assert replacement.started_at == "replacement-start"
    assert replacement.phase is None and replacement.board_ready is False


def test_child_exit_3_fails_honestly_and_a_later_request_runs(config):
    assert capture_mod.start_refresh(config, "s7700-mem") is True
    failed = _wait_settled(config, "s7700-mem")
    assert failed.state == "failed"
    assert failed.error == "the read ran out of memory"
    assert failed.outcome["code"] == "memory_limit" and failed.outcome["memory_mb"] == 256
    assert capture_mod.start_refresh(config, "s7700-ok") is True
    after = _wait_settled(config, "s7700-ok")
    assert after.state == "idle"


def test_killed_child_fails_the_run_and_the_parent_serves_the_next_job(config):
    assert capture_mod.start_refresh(config, "s7700-kill") is True
    killed = _wait_settled(config, "s7700-kill")
    assert killed.state == "failed" and killed.error == "the read ran out of memory"
    assert killed.outcome == {"code": "worker_exit", "returncode": -9, "memory_mb": 256}
    assert capture_mod._supervisor._thread is not None and capture_mod._supervisor._thread.is_alive()
    assert capture_mod.start_refresh(config, "s7700-ok") is True
    after = _wait_settled(config, "s7700-ok")
    assert after.state == "idle" and after.total_deeds == 11
    assert (config.snapshots_dir / "land-s7700-ok-latest.json").exists()
