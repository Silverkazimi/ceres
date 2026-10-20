"""`save_snapshot`'s temp file used to be scoped to the PROCESS
(`target.with_suffix(f".{os.getpid()}.tmp")`), not the writer. Ceres calls
`save_snapshot` from FastAPI's sync threadpool, so two THREADS in one process picked
the same temp path, both opened it for write, and interleaved -- corrupting the file
`.replace()` renamed over the real target. This reproduces that race directly against
`praetoria.land_analysis.save_snapshot`, with no Ceres or network involved.

Injection performed by hand to confirm this guard is load-bearing (not recorded as a
permanently-red test, per the mutation standard -- a corruption-asserting test would
itself need deleting once fixed): temporarily reverted `save_snapshot`'s temp name to
`target.with_suffix(f".{os.getpid()}.tmp")` (the pre-fix form) and re-ran
`test_concurrent_writers_produce_a_valid_file` in isolation -- it failed with
`json.JSONDecodeError` reliably (matching the diagnosis's 5/5). Restored the fix and
the same run went green.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from praetoria.land_analysis import LAND_SCHEMA_VERSION, save_snapshot


def _report(tag: str) -> dict:
    return {
        "schema_version": LAND_SCHEMA_VERSION,
        "generated_at": f"2026-08-28T00:00:0{tag}Z",
        "snapshot": {},
        "plots": [{"deed_uid": f"deed-{tag}"}],
        "workers": [],
        "cards": [],
        "bonus_assets": [],
        "unassigned_items": {},
        "coverage": {},
    }


def test_concurrent_writers_produce_a_valid_file():
    """Two threads racing `save_snapshot` against the SAME path must never leave a
 file that fails to parse -- whichever write lands last, it must be a complete,
 valid document, not an interleave of both."""
    with TemporaryDirectory() as directory:
        target = Path(directory) / "land-race-latest.json"
        errors: list[Exception] = []

        def _write(tag: str) -> None:
            try:
                save_snapshot(_report(tag), target)
            except Exception as exc:  # a losing writer must still finish cleanly
                errors.append(exc)

        threads = [threading.Thread(target=_write, args=(str(i),)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"writer thread(s) raised: {errors}"
        # The observable that matters: the file on disk parses. Not which report won.
        parsed = json.loads(target.read_text(encoding="utf-8"))
        assert parsed["schema_version"] == LAND_SCHEMA_VERSION
        assert parsed["plots"][0]["deed_uid"].startswith("deed-")
