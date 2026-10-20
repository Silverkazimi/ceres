"""Auto-reload Layer 3 -- process-level self-restart on *.py file change.

Ceres deliberately does NOT use uvicorn's own `reload=True`/`--reload`: every other
project's `serve.py` disables it in production because it is incompatible with the
pre-created dual-socket bind those services use for the WSL2 localhost relay. Ceres never needs that dual-socket
bind -- it is loopback-only by design (`docs/ceres.md`), and 127.0.0.1 is already
relayed by WSL2 without it -- so this uses a much smaller mechanism instead: a
background thread that polls the mtimes of files under `ceres/`, `praetoria/` and
`fides_land/` and, on a settled change, asks the process to exit. Paired with
`Restart=always` in the systemd unit (`docs/service-deployment-runbook.md` install
pattern) the service comes back up within ~1s running the new code, satisfying
the "no manual restart, ever" requirement. (An interim hand-rolled restart
loop stood in for the systemd unit before this project's user session could reach
D-Bus; retired once the unit was confirmed enabled and functioning.)

Restart-worthy vs. hash-worthy are NOT the same set (2026-08-21 fix, `work/
ceres-plot-expand-bug.md`): a *.py change needs a fresh interpreter, but static
assets don't -- `/static` is served straight from disk per request
(`StaticFiles`, `app.py`) and `index.html` is re-read per request too, so an
html/js/css edit is already live with no restart at all. Restarting for one
anyway was pure downtime: on this project's live board it produced 505 restarts
in one session (every agent edit to app.js/ceres.css), each one a ~1.7s window
of ERR_CONNECTION_REFUSED plus whatever request was in flight, which is exactly
what surfaced as "Could not load this plot's worker data: TypeError: Failed to
fetch" on some clicks but not others. So this module watches only the `*.py`
subset of `routes_board.watched_paths()` -- the single shared definition of
"what counts as a change" stays in `routes_board.py`; `/api/dev/watch`'s hash
still covers the FULL set (py + html + js + css), so the browser reload-banner
(Layer 2) still fires on a static edit even though the process itself does not
restart for one. Two lists that must agree is the defect class this project
already documents -- this stays one list
(`watched_paths()`) with a derived subset, not a second, divergent list.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

from .routes_board import watched_paths

logger = logging.getLogger("ceres.reload_watch")

POLL_SECONDS = 2.0
# A file can be mid-write (editor/agent tool) when a poll lands on its moved
# mtime; wait this long and re-check before acting, so a half-written file
# never triggers a restart on its own first, incomplete mtime.
DEBOUNCE_SECONDS = 0.75


def _py_paths() -> list:
    """Restart-triggering subset of watched_paths() -- *.py only. Derived from the
    single shared list rather than a second, hand-maintained glob, so it cannot
    drift from what `/api/dev/watch` hashes."""
    return [path for path in watched_paths() if path.suffix == ".py"]


def _snapshot(paths) -> dict[str, float]:
    out: dict[str, float] = {}
    for path in paths:
        try:
            out[str(path)] = path.stat().st_mtime_ns
        except OSError:
            continue
    return out


def _watch_loop(baseline: dict[str, float]) -> None:
    while True:
        time.sleep(POLL_SECONDS)
        current = _snapshot(_py_paths())
        if current == baseline:
            continue
        # Debounce: confirm the change is settled before acting.
        time.sleep(DEBOUNCE_SECONDS)
        settled = _snapshot(_py_paths())
        if settled != current:
            # Still moving -- not settled yet. Adopt this instant as the new
            # comparison point and keep polling rather than restarting mid-write.
            baseline = current
            continue
        logger.warning(
            "ceres: *.py change detected -- requesting graceful restart for "
            "auto-reload (systemd Restart=always)"
        )
        # SIGTERM, not os._exit(0): uvicorn installs a SIGTERM handler that
        # drains in-flight requests (graceful shutdown) before the process
        # actually exits, so a restart triggered mid-request no longer fails
        # that request outright the way a hard exit did.
        os.kill(os.getpid(), signal.SIGTERM)
        return


def start_reload_watch() -> threading.Thread:
    """Start the Layer-3 watcher as a daemon thread and return it (mainly for tests)."""
    baseline = _snapshot(_py_paths())
    thread = threading.Thread(target=_watch_loop, args=(baseline,), name="ceres-reload-watch", daemon=True)
    thread.start()
    return thread
