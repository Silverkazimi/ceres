""" (2026-09-14, found by the player0016 measurement): under the worker's RLIMIT_DATA cap
(1100 MB) orjson could not allocate its parse buffer for a 98.7 MB snapshot and raised a
JSONDecodeError("Not enough memory to allocate buffer for parsing"); `load_snapshot` called that
"invalid Land snapshot", `_save_merged`'s recovery treated the good board as absent and
the fresh quick read OVERWROTE a 6303-plot board with a 0-of-6303 one.

Guards: (a) an orjson allocation failure is a `MemoryError`, not a decode error; (b) a snapshot
that could not be read is never overwritten and the run fails with a clear message; (c) the cap
grows with the stored board. The subprocess reproduction (`test_reproduction_...`) is slow and
allocates ~1.3 GB: `CERES_RUN_SLOW=1` selects it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ceres import capture as capture_mod
from ceres.config import CeresConfig
from praetoria import fastjson
from praetoria.errors import CacheError, SchemaError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ORJSON_ALLOC_MESSAGE = "Not enough memory to allocate buffer for parsing"


# (a) fastjson

def test_fastjson_alloc_failure_is_memory_error(monkeypatch):
    orjson = pytest.importorskip("orjson")

    def _fail(_data):
        raise orjson.JSONDecodeError(ORJSON_ALLOC_MESSAGE, "", 0)

    monkeypatch.setattr(fastjson._orjson, "loads", _fail)
    with pytest.raises(MemoryError) as info:
        fastjson.loads(b"{}")
    assert ORJSON_ALLOC_MESSAGE in str(info.value)
    assert isinstance(info.value.__cause__, orjson.JSONDecodeError)


def test_fastjson_real_corruption_stays_decode_error():
    with pytest.raises(json.JSONDecodeError):
        fastjson.loads(b"{not json")


def test_load_snapshot_alloc_failure_is_not_cache_error(tmp_path, monkeypatch):
    from praetoria.land_analysis import load_snapshot

    path = tmp_path / "land-x-latest.json"
    path.write_bytes(b"{}")
    monkeypatch.setattr(fastjson, "loads", lambda _data: (_ for _ in ()).throw(MemoryError(ORJSON_ALLOC_MESSAGE)))
    with pytest.raises(MemoryError):
        load_snapshot(path)


# (b) _save_merged

def _config(tmp_path: Path) -> CeresConfig:
    config = CeresConfig(root=tmp_path, player="s5420")
    config.ensure_dirs()
    return config


def test_save_merged_never_overwrites_an_unreadable_snapshot(tmp_path, monkeypatch):
    config = _config(tmp_path)
    path = capture_mod._latest_path(config, "s5420")
    original = b'{"schema_version":"praetoria.land.v1","plots":[{"deed_uid":"good"}]}\n'
    path.write_bytes(original)
    monkeypatch.setattr(capture_mod, "_active_capture_cap_mb", 1100)

    def _oom(_config, _player):
        raise MemoryError(ORJSON_ALLOC_MESSAGE)

    monkeypatch.setattr(capture_mod, "latest_snapshot", _oom)
    fresh = {"schema_version": "praetoria.land.v1", "plots": [], "coverage": {}}
    with pytest.raises(capture_mod.SnapshotUnreadableError) as info:
        capture_mod._save_merged(config, "s5420", fresh)
    assert str(info.value) == (
        "could not read the existing board snapshot (out of memory under the capture cap 1100 MB); nothing was overwritten"
    )
    assert path.read_bytes() == original


def test_run_marks_failed_with_the_message_and_keeps_the_file(tmp_path, monkeypatch):
    config = _config(tmp_path)
    path = capture_mod._latest_path(config, "s5420")
    original = b'{"schema_version":"praetoria.land.v1","plots":[{"deed_uid":"good"}]}\n'
    path.write_bytes(original)
    monkeypatch.setattr(capture_mod, "_active_capture_cap_mb", 1100)
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda _c, _p: (_ for _ in ()).throw(MemoryError(ORJSON_ALLOC_MESSAGE)))
    monkeypatch.setattr(capture_mod, "_capture_quick_body", lambda config, player, emit, known_plots=None: capture_mod._save_merged(config, player, {"plots": []}))
    events: list[tuple[str, dict]] = []
    capture_mod._run_capture_body(config, "s5420", "quick", lambda event, **fields: events.append((event, fields)))
    assert events == [("failed", {"error": "could not read the existing board snapshot (out of memory under the capture cap 1100 MB); nothing was overwritten", "outcome": None})]
    assert path.read_bytes() == original


@pytest.mark.parametrize("error", [CacheError("invalid Land snapshot: x"), SchemaError("not v1")])
def test_save_merged_still_recovers_a_genuinely_corrupt_file(tmp_path, monkeypatch, error):
    """ recovery stays: a corrupt file is overwritten by the fresh read."""
    from tests.ceres.conftest import build_report_quick

    config = _config(tmp_path)
    path = capture_mod._latest_path(config, "s5420")
    path.write_bytes(b"{corrupt")
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda _c, _p: (_ for _ in ()).throw(error))
    report = build_report_quick()
    merged = capture_mod._save_merged(config, "s5420", report)
    assert merged.get("plots")
    assert path.read_bytes() != b"{corrupt"


# (c) cap sizing

def test_cap_default_is_2048(monkeypatch):
    monkeypatch.delenv("CERES_CAPTURE_MEMORY_MB", raising=False)
    assert capture_mod.capture_memory_mb() == 2048
    assert capture_mod.capture_memory_mb(24 * 1024 * 1024) == 2048  # player0006: default


def test_cap_grows_with_the_stored_board(monkeypatch):
    monkeypatch.delenv("CERES_CAPTURE_MEMORY_MB", raising=False)
    player0016 = int(98.7 * 1024 * 1024)
    cap = capture_mod.capture_memory_mb(player0016)
    assert cap >= 1300
    assert cap == 2048  # 98.7 MB * 14 + 256 = 1637 MB; the 2048 default already covers player0016
    assert capture_mod.capture_memory_mb(200 * 1024 * 1024) == 200 * 14 + 256  # 3056: sized up past the default
    assert capture_mod.capture_memory_mb(400 * 1024 * 1024) == capture_mod.CAPTURE_MEMORY_MB_MAX  # 3072 ceiling


def test_cap_env_override_is_the_floor(monkeypatch):
    monkeypatch.setenv("CERES_CAPTURE_MEMORY_MB", "1100")
    assert capture_mod.capture_memory_mb() == 1100
    assert capture_mod.capture_memory_mb(int(98.7 * 1024 * 1024)) == int(98.7 * 1024 * 1024) * 14 // (1024 * 1024) + 256


def test_supervisor_sizes_the_cap_from_the_snapshot_file(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("CERES_CAPTURE_MEMORY_MB", raising=False)
    config = _config(tmp_path)
    path = capture_mod._latest_path(config, "s5420")
    with path.open("wb") as fh:
        fh.truncate(200 * 1024 * 1024)  # sparse 200 MB file
    assert capture_mod._stored_snapshot_size(config, "s5420") == 200 * 1024 * 1024
    assert capture_mod.capture_memory_mb(capture_mod._stored_snapshot_size(config, "s5420")) == 3056


# Reproduction (slow)

_REPRO = textwrap.dedent(
    """
    import resource, sys
 from pathlib import Path
 cap = int(sys.argv[1]) * 1024 * 1024
 resource.setrlimit(resource.RLIMIT_DATA, (cap, cap))
 sys.path.insert(0, sys.argv[3])
 from praetoria.land_analysis import load_snapshot
 from praetoria.errors import CacheError
 try:
 load_snapshot(Path(sys.argv[2]))
 print("parsed")
 except MemoryError as exc:
 print("MemoryError", exc)
 except CacheError as exc:
 print("CacheError", exc)
    """
)


@pytest.mark.slow
@pytest.mark.skipif(os.getenv("CERES_RUN_SLOW") != "1", reason="reproduction: ~90 MB JSON under RLIMIT_DATA 1100 MB; CERES_RUN_SLOW=1 selects it")
def test_reproduction_cap_1100_on_a_90mb_snapshot_is_memory_error_not_cache_error(tmp_path):
    pytest.importorskip("orjson")
    target = tmp_path / "land-big-latest.json"
    row = b'{"deed_uid":"R123-T45-P678","worksite":{"cards":[{"uid":"C7-123456-ABCDEF","pp":1234.5}]},"note":"' + b"x" * 2000 + b'"},'
    count = 90 * 1024 * 1024 // len(row)
    with target.open("wb") as fh:
        fh.write(b'{"schema_version":"praetoria.land.v1","plots":[')
        for _ in range(count - 1):
            fh.write(row)
        fh.write(row[:-1] + b"]}")
    assert target.stat().st_size > 80 * 1024 * 1024
    run = subprocess.run(
        [sys.executable, "-c", _REPRO, "1100", str(target), str(PROJECT_ROOT)],
        capture_output=True, text=True, timeout=300, cwd=str(PROJECT_ROOT),
    )
    assert run.returncode == 0, run.stderr[-2000:]
    assert run.stdout.startswith("MemoryError"), run.stdout
    assert "CacheError" not in run.stdout
