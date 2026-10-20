""" -- the two Ceres-side halves of the snapshot-corruption fix, both offline
(fake `LandCapture.expand_deed`, no network):

1. A corrupt on-disk snapshot must not be TERMINAL: `_save_merged` (used by both quick
   and full capture) must recover by overwriting it, not by propagating the parse error
   forever (`POST /api/capture/refresh` used to fail with no way to clear it).
2. Two concurrent `expand_plot` calls for the same player must not silently drop one
   deed's merge -- the read-merge-write around the on-disk snapshot is now serialised
   per player (`capture._snapshot_write_lock`).

Both guards were confirmed by hand: temporarily reverting the fix under test --
removing the `try/except` around `latest_snapshot()` in `_save_merged` for #1;
un-indenting `expand_plot`'s read-merge-write out from under
`with _snapshot_write_lock(player):` for #2, with the second test's own
`slow_latest_snapshot` wrapper widening the read/write window so the race is forced
rather than timing-luck -- turned the corresponding test red 3/3 trials; restoring the
fix turned it green 3/3. Not kept as permanently-red tests per the mutation standard."""

from __future__ import annotations

import threading
import time

import pytest

from ceres import capture
from ceres.config import CeresConfig
import praetoria.land_analysis as land_analysis
from praetoria.errors import CacheError
from praetoria.land_analysis import LAND_SCHEMA_VERSION
from ceres import board_artifact


@pytest.fixture
def config(tmp_path) -> CeresConfig:
    cfg = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    cfg.ensure_dirs()
    return cfg


@pytest.fixture(autouse=True)
def _no_card_details_refresh(monkeypatch):
    monkeypatch.setattr(capture, "_refresh_card_details", lambda: None)


def _plot(deed_uid: str, retrieved: bool, worker_count: int | None = None) -> dict:
    plot = {
        "deed_uid": deed_uid,
        # the board row carries the numeric plot_id the live path needs.
        "facts": {"plot_id": 1 + ord(deed_uid[0]) % 97},
        "asset_fetch": {"status": "retrieved" if retrieved else "not_requested"},
    }
    if worker_count is not None:
        plot["staking_facts"] = {"worker_count": worker_count}
    return plot


def _report(plots: list[dict], workers: list[dict] | None = None) -> dict:
    return {
        "schema_version": LAND_SCHEMA_VERSION,
        "generated_at": "2026-08-28T00:00:00Z",
        "plots": plots,
        "workers": workers or [],
        "cards": [], "bonus_assets": [], "unassigned_items": {}, "coverage": {},
    }


# 1. A corrupt snapshot must not block a capture from recovering it.

def test_save_merged_overwrites_a_corrupt_existing_snapshot(config):
    path = capture._latest_path(config, "player0005")
    path.write_text("{not valid json at all", encoding="utf-8")

    # Sanity: the corrupt file really does raise on a direct read, same as
    # `_run_capture_quick`/`_run_capture_full` would hit before this fix.
    with pytest.raises(CacheError):
        capture.latest_snapshot(config, "player0005")

    fresh = _report([_plot("a", True)], workers=[{"stake_ref_uid": "a", "name": "W1"}])
    merged = capture._save_merged(config, "player0005", fresh)

    assert merged["plots"][0]["deed_uid"] == "a"
    persisted, _ref = capture.latest_snapshot(config, "player0005")
    assert persisted["plots"][0]["deed_uid"] == "a"


# 2. Two concurrent expand_plot calls must not drop either one's merge.

def test_concurrent_expand_plot_preserves_both_deeds(config, monkeypatch):
    base = _report([_plot("a", False), _plot("b", False)])
    capture._save_snapshot_and_refresh_cache(base, capture._latest_path(config, "player0005"))

    def fake_expand_deed(self, deed_uid, player, offline=False, bundle_ttl=3600, include_collection=False, narrow=False, plot_id=None):
        # Real expand_deed semantics: every plot present, only `deed_uid` retrieved
        # see LandCapture.expand_deed's own docstring.
        time.sleep(0.05)  # widen the race window so the two calls genuinely overlap
        plots = [_plot("a", deed_uid == "a"), _plot("b", deed_uid == "b")]
        workers = [{"stake_ref_uid": deed_uid, "name": f"W-{deed_uid}"}]
        return _report(plots, workers=workers)

    monkeypatch.setattr(capture.LandCapture, "expand_deed", fake_expand_deed)

    # Widen the window between the read and the write inside the read-merge-write
    # section itself, so two threads' reads are forced to overlap regardless of how
    # fast the fake network fetch above runs -- makes the race deterministic rather
    # than timing-dependent.
    real_latest_snapshot = capture.latest_snapshot

    def slow_latest_snapshot(cfg, player):
        result = real_latest_snapshot(cfg, player)
        time.sleep(0.05)
        return result

    monkeypatch.setattr(capture, "latest_snapshot", slow_latest_snapshot)

    results: dict[str, Exception] = {}

    def _expand(deed_uid: str) -> None:
        try:
            capture.expand_plot(config, "player0005", deed_uid)
        except Exception as exc:  # pragma: no cover - surfaced via assertion below
            results[deed_uid] = exc

    threads = [threading.Thread(target=_expand, args=(d,)) for d in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not results, f"expand_plot raised: {results}"

    persisted, _ref = capture.latest_snapshot(config, "player0005")
    statuses = {p["deed_uid"]: p["asset_fetch"]["status"] for p in persisted["plots"]}
    assert statuses == {"a": "retrieved", "b": "retrieved"}, (
        "one deed's expand was silently dropped by the other's concurrent write"
    )
    assert {w["stake_ref_uid"] for w in persisted["workers"]} == {"a", "b"}


def test_save_snapshot_removes_temp_file_after_replace_exception(tmp_path, monkeypatch):
    """Removing save_snapshot's finally must make this post-write failure guard red."""
    target = tmp_path / "land-player0005-latest.json"

    def fail_replace(source, destination):
        assert source.exists()
        raise OSError("injected replace failure")

    monkeypatch.setattr(land_analysis.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        land_analysis.save_snapshot(_report([_plot("a", True)]), target)

    assert [path for path in tmp_path.iterdir() if path.suffix == ".tmp"] == []


def test_concurrent_saves_publish_monotonic_tokens_with_existing_artifacts(config, monkeypatch):
    """M7: removing the per-player publication lock must make the final identity unsafe."""
    report = _report([_plot("a", True)])
    monkeypatch.setattr("ceres.board.load_details_by_id", lambda: {})
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: {"schema": "ceres.board.v1"})
    errors = []

    def save():
        try:
            capture._save_snapshot_and_refresh_cache(report, capture._latest_path(config, "player0005"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=save) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for _ in range(100):
        identity = board_artifact.read_identity(config.snapshots_dir, "player0005")
        if identity is not None and identity.complete:
            break
        time.sleep(0.01)
    identity = board_artifact.read_identity(config.snapshots_dir, "player0005")
    artifacts = list(config.snapshots_dir.glob(f"board-player0005-{identity.token}.json"))
    assert not errors
    assert identity.complete is True
    assert artifacts
    assert identity.write_counter == 8


def test_heal_counter_is_not_suppressed_by_pool_flag(config, monkeypatch):
    """M8: making pool_may_have_changed gate identity allocation must fail this guard."""
    report = _report([_plot("a", True)])
    monkeypatch.setattr("ceres.board.load_details_by_id", lambda: {})
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: {"schema": "ceres.board.v1"})
    capture._save_snapshot_and_refresh_cache(report, capture._latest_path(config, "player0005"))
    first = board_artifact.read_identity(config.snapshots_dir, "player0005")
    capture._save_snapshot_and_refresh_cache(report, capture._latest_path(config, "player0005"), pool_may_have_changed=False)
    second = board_artifact.read_identity(config.snapshots_dir, "player0005")

    assert second.write_counter == first.write_counter + 1
    assert second.token != first.token
