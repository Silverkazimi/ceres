""": the board-read worker child -- ``python -m ceres.capture_worker``.

The web process (``ceres.capture.CaptureSupervisor``) starts one of these per board read,
one at a time, writes ONE JSON job on its stdin and replays the JSON lines it prints on
stdout into the parent's run state. The child does the whole read and every disk write
(snapshot, card sidecar, artifact + gzip, identity index); no report object ever crosses
the pipe. It runs under a hard memory cap so a whale account (1.94 GB RSS measured on
player0016,  audit) can never wedge the 2 GB / MemoryMax=1500M webserver: the cap
fails the READ, honestly, and the web process stays small and alive.

Job (stdin, one line)::

    {"player", "mode": "quick"|"full", "root", "stale_after_seconds", "memory_mb",
     "known_plots", ["capture_concurrency"], ["worker_setup": "module:function"]}

Events (stdout, one JSON object per line, ``event`` names the kind)::

    deed_count{total, estimated_seconds}   phase{phase}   progress{fetched, total}
    board_ready{token, generated_at, [interim]}   repair{token, repaired_deeds}
    done{requests, cache_hits, total_deeds, peak_rss_mb}   failed{error, outcome}

: ``RLIMIT_DATA`` counts the allocator's RESERVATIONS, not resident memory. orjson
parsing the stored board reserves ~13x the file size (98.7 MB snapshot: peak RSS 638 MB, yet
cap 1100 MB -> "Not enough memory to allocate buffer for parsing", cap 1400 -> SIGSEGV,
cap 1600 -> fine), so the parent sizes ``memory_mb`` from the snapshot file
(``ceres.capture.capture_memory_mb``: default 2048, ``size * 14 + 256 MB``, max 3072).

Exit codes: 0 after ``done`` or ``failed``; ``WORKER_EXIT_MEMORY`` (3) when the memory
cap was hit (``failed{error: "memory limit N MB exceeded"}`` is emitted first, best effort).
``worker_setup`` is a test hook: a dotted ``module:function`` imported and called as
``function(capture_module, job)`` before the read, so a test can install a fake
``LandCapture`` inside the child.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import signal
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

WORKER_EXIT_MEMORY = 3
_PR_SET_PDEATHSIG = 1


def apply_memory_cap(memory_mb: int) -> int | None:
    """RLIMIT_DATA = brk + private anonymous mmap, i.e. what malloc hands out -- unlike RLIMIT_AS
    it is not inflated by thread stacks or shared libraries. Returns the cap applied (bytes)."""
    try:
        import resource
    except ImportError:
        return None
    cap = int(memory_mb) * 1024 * 1024
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
        if hard != resource.RLIM_INFINITY:
            cap = min(cap, hard)
        resource.setrlimit(resource.RLIMIT_DATA, (cap, cap if hard == resource.RLIM_INFINITY else hard))
        return cap
    except (ValueError, OSError):
        return None


def prefer_oom_kill() -> None:
    """A cgroup OOM kill (MemoryMax on a systemd host) always picks this child over the web process."""
    try:
        Path("/proc/self/oom_score_adj").write_text("1000\n", encoding="ascii")
    except OSError:
        pass


def die_with_parent() -> None:
    """prctl(PR_SET_PDEATHSIG, SIGTERM): a parent restart (the local board auto-reloads on
    every saved .py) or crash never leaves an orphaned whale read running."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, int(signal.SIGTERM), 0, 0, 0)
    except (OSError, AttributeError):
        pass


def _emitter(stream: Any):
    def emit(event: str, **fields: Any) -> None:
        try:
            stream.write(json.dumps({"event": event, **fields}, default=str) + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass
    return emit


def _read_job(stream: Any) -> dict[str, Any]:
    line = stream.readline()
    if not line.strip():
        line = stream.read()
    job = json.loads(line)
    if not isinstance(job, dict):
        raise ValueError("job must be a JSON object")
    return job


def run_job(job: dict[str, Any], out: Any) -> int:
    emit = _emitter(out)
    from . import fixture_upstream

    fixture_upstream.install_from_env()  # the capture child answers from the same fixture set
    memory_mb = int(job.get("memory_mb") or 0)
    if memory_mb > 0:
        apply_memory_cap(memory_mb)
        logging.getLogger(__name__).info("capture cap %d MB applied (RLIMIT_DATA)", memory_mb)
    prefer_oom_kill()
    die_with_parent()
    try:
        from . import capture as capture_mod
        from .config import CeresConfig

        capture_mod._active_capture_cap_mb = memory_mb or None  # named in a read failure

        config = CeresConfig(
            root=Path(str(job["root"])),
            player=str(job["player"]),
            stale_after_seconds=int(job.get("stale_after_seconds") or 60),
            capture_concurrency=int(job.get("capture_concurrency") or 8),
        )
        player = capture_mod.security.validate_player(str(job["player"]))
        mode = "quick"  # Q9: the Full capture is removed
        setup = job.get("worker_setup")
        if setup:
            module_name, _, attribute = str(setup).partition(":")
            getattr(import_module(module_name), attribute)(capture_mod, job)
        known = job.get("known_plots")
        capture_mod._run_capture_body(
            config, player, mode, emit, known_plots=int(known) if isinstance(known, int) else None,
        )
        return 0
    except MemoryError:
        emit("failed", error=f"memory limit {memory_mb} MB exceeded", outcome={"code": "memory_limit", "memory_mb": memory_mb})
        return WORKER_EXIT_MEMORY
    except Exception as exc:
        emit("failed", error=str(exc), outcome=None)
        return 1


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=os.getenv("CERES_WORKER_LOG_LEVEL", "INFO"), format="worker %(levelname)s %(name)s: %(message)s")
    # Only protocol lines may reach stdout: the read's own prints (if any) go to stderr.
    out = sys.stdout
    sys.stdout = sys.stderr
    try:
        job = _read_job(sys.stdin)
    except Exception as exc:
        _emitter(out)("failed", error=f"bad worker job: {exc}", outcome=None)
        return 2
    return run_job(job, out)


if __name__ == "__main__":
    sys.exit(main())
