""" coverage journal persistence tests.

The journal is evidence only: snapshot persistence must remain successful when the
journal cannot be opened or rotated.
"""

from __future__ import annotations

import json
from pathlib import Path

from ceres.capture import _save_merged
from ceres.config import CeresConfig


def _report() -> dict:
    return {
        "query": {"player": "player0005"},
        "plots": [
            {
                "deed_uid": "a",
                "staking_facts": {"worker_count": 0},
                "asset_fetch": {"status": "retrieved"},
                "observed_at": "2099-01-01T00:00:00Z",
            }
        ],
        "workers": [],
        "assignments": [],
        "bonus_assets": [],
        "cards": [],
        "coverage": {"assets": {"retrieved": 1, "expected": 1, "complete": True}},
    }


def test_save_merged_appends_one_parseable_line_with_ordered_journal_keys(tmp_path) -> None:
    config = CeresConfig(root=tmp_path)
    config.ensure_dirs()

    _save_merged(config, "player0005", _report())
    _save_merged(config, "player0005", _report())

    journal = config.root / "coverage-journal.jsonl"
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    expected_keys = [
        "at", "player", "plots", "retrieved", "dropped", "failed", "not_requested",
        "worker_detail_pending", "complete", "needs_refetch", "pending_deeds",
        "snapshot_bytes", "oldest_observed_at",
    ]
    for line in lines:
        entry = json.loads(line)
        assert list(entry) == expected_keys
        assert entry["player"] == "player0005"
        assert entry["plots"] == 1
        assert entry["snapshot_bytes"] > 0


def test_coverage_journal_write_failure_does_not_block_snapshot(monkeypatch, tmp_path) -> None:
    config = CeresConfig(root=tmp_path)
    config.ensure_dirs()
    original_open = Path.open

    def fail_journal_open(path, *args, **kwargs):
        if path.name == "coverage-journal.jsonl":
            raise OSError("journal unavailable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_journal_open)
    _save_merged(config, "player0005", _report())

    assert (config.snapshots_dir / "land-player0005-latest.json").exists()


def test_coverage_journal_rotates_once_past_five_megabytes(tmp_path) -> None:
    config = CeresConfig(root=tmp_path)
    config.ensure_dirs()
    journal = config.root / "coverage-journal.jsonl"
    journal.write_bytes(b"x" * (5 * 1024 * 1024 + 1))

    _save_merged(config, "player0005", _report())

    assert (config.root / "coverage-journal.1.jsonl").stat().st_size == 5 * 1024 * 1024 + 1
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1
