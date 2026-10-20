from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ceres.board import load_details_by_id
from ceres.candidates import rank_candidates
from praetoria.land_reallocate import build_plots


SNAPSHOT = Path.home() / ".ceres" / "snapshots" / "land-player0005-latest.json"


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="largest live snapshot is absent")
@pytest.mark.skipif(os.environ.get("CERES_RUN_PERF") != "1", reason="performance benchmark runs in isolation")
def test_candidates_p95_under_250ms_on_largest_snapshot():
    script = """
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from ceres.board import load_details_by_id
from ceres.candidates import rank_candidates
from praetoria.land_reallocate import build_plots

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
details = load_details_by_id()
plots = build_plots(report)
deed_uids = [uid for uid, plot in plots.items() if plot.card_capacity > 0 and len(plot.cards) >= plot.card_capacity]
deed_uids = deed_uids[:8] or list(plots)[:8]
for deed_uid in deed_uids:
    for _ in range(2):
        rank_candidates(report, deed_uid, "worker", player="player0005", now=datetime.now(timezone.utc), bucket="idle", limit=100, slot=0, details_by_id=details)
timings = []
for deed_uid in deed_uids:
    for _ in range(3):
        started = time.perf_counter()
        body = rank_candidates(report, deed_uid, "worker", player="player0005", now=datetime.now(timezone.utc), bucket="idle", limit=100, slot=0, details_by_id=details)
        timings.append((time.perf_counter() - started) * 1000)
        assert body["returned"] <= 100
print(json.dumps(timings))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(SNAPSHOT)], cwd=Path.cwd(),
        check=True, capture_output=True, text=True,
    )
    timings = json.loads(result.stdout)
    assert sorted(timings)[int(len(timings) * 0.95) - 1] < 250
