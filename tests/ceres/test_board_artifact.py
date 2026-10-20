"""P1 guards for token-addressed lean board publication and reads."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ceres import board_artifact, capture
from ceres.config import CeresConfig
from ceres.routes_board import get_board


PLAYER = "player0005"


def _request(config, headers=None):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)),
        headers=headers or {},
    )


def _identity(config, token, report):
    path = capture._latest_path(config, PLAYER)
    path.write_bytes(json.dumps(report, separators=(",", ":")).encode())
    token_value, counter, epoch = token, int(token.rsplit("-", 1)[1]), token.rsplit("-", 1)[0]
    reserved = board_artifact.commit_snapshot_identity(
        config.snapshots_dir,
        PLAYER,
        token_value,
        counter,
        epoch,
        path,
        path.stat(),
        None,
        snapshot_generated_at=report.get("generated_at"),
    )
    return path, reserved


# publication is synchronous inside the snapshot save -- no drain thread to join.


@pytest.fixture(autouse=True)
def isolated_details_identity(monkeypatch):
    """Pin the card-details identity these guards publish against.

    `board_artifact.publish` refuses to publish unless the committed identity's
    `details_mtime_ns`/`details_size` still match a LIVE `details_identity()` read
    (board_artifact.py:342-349), and `details_identity()` stats the real
    `~/.praetoria/cache/card_details.json` -- a file outside `tmp_path` that exists on a
    developer machine and not in a clean checkout. Without this pin every `publish()`
    here returns None on a machine that has the cache, so no artifact is ever written and
    the reads below see `None`. Pin it to the "no details cache" identity the fixtures
    already commit.
    """
    monkeypatch.setattr(board_artifact, "details_identity", lambda: (None, None))
    monkeypatch.setattr("ceres.routes_board.details_cache_mtime", lambda: None)


@pytest.fixture
def config(tmp_path):
    config = CeresConfig(root=tmp_path / ".ceres", player=PLAYER)
    config.ensure_dirs()
    return config


def test_cold_artifact_is_the_canonical_lean_projection(config, monkeypatch):
    """M1: replacing the lean projection with a different shape must fail byte parity."""
    expected = {"schema": "ceres.board.v1", "plots": [{"deed_uid": "a"}], "generated_at": "same"}
    report = {"generated_at": "same"}
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: expected)
    path, identity = _identity(config, "a" * 32 + "-1", report)

    board_artifact.publish(config.snapshots_dir, PLAYER, identity, report, {})
    loaded_identity, payload = board_artifact.load_current_payload(
        config.snapshots_dir,
        PLAYER,
        snapshot_path=path,
        details_mtime_ns=None,
    )

    assert loaded_identity.token == identity.token
    assert json.loads(payload.raw) == expected
    assert json.loads(board_artifact.artifact_path(config.snapshots_dir, PLAYER, identity.token).read_text()) == expected


def test_publish_writes_gzip_copy_and_sizes_it_in_identity(config, monkeypatch):
    """: the gzip copy is written at publish time and its size recorded, so no request
    thread ever compresses the board."""
    board = {"schema": "ceres.board.v1", "generated_at": "same", "plots": [{"deed_uid": "a"} for _ in range(200)], "coverage": {"assets": {"expected": 3, "retrieved": 3}}}
    report = {"generated_at": "same"}
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: board)
    path, identity = _identity(config, "9" * 32 + "-1", report)

    board_artifact.publish(config.snapshots_dir, PLAYER, identity, report, {})
    gz_path = board_artifact.gzip_artifact_path(config.snapshots_dir, PLAYER, identity.token)
    index = json.loads(board_artifact.index_path(config.snapshots_dir, PLAYER).read_text(encoding="utf-8"))
    raw = board_artifact.artifact_path(config.snapshots_dir, PLAYER, identity.token).read_bytes()

    assert gz_path.exists()
    assert index["artifact_gzip_size"] == gz_path.stat().st_size
    assert index["artifact_size"] == len(raw)
    assert gzip.decompress(gz_path.read_bytes()) == raw
    assert index["artifact_generated_at"] == "same"
    assert (index["artifact_assets"]["expected"], index["artifact_assets"]["retrieved"]) == (3, 3)  # prepare_board flattens coverage
    assert index["artifact_notice_count"] == 0

    monkeypatch.setattr(gzip, "compress", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("request thread compressed the board")))
    loaded_identity, payload = board_artifact.load_current_payload(config.snapshots_dir, PLAYER, snapshot_path=path, details_mtime_ns=None)
    assert loaded_identity.token == identity.token
    assert payload.gzip == gz_path.read_bytes()
    assert payload.raw == raw


def test_payload_exposes_no_parsed_board(config, monkeypatch):
    """: the web process serves bytes plus a small `meta`; `json.loads` of the board
    never runs on the request thread."""
    board = {"schema": "ceres.board.v1", "generated_at": "same", "plots": [], "coverage": {"assets": {"expected": 1, "retrieved": 0, "failed": 1}}, "notices": [{"code": "x"}]}
    report = {"query": {"player": PLAYER}, "generated_at": "same"}
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: board)
    path, identity = _identity(config, "8" * 32 + "-1", report)
    board_artifact.publish(config.snapshots_dir, PLAYER, identity, report, {})
    real_loads = json.loads

    def guarded_loads(value, *args, **kwargs):
        # the identity file is small text; the artifact is the bytes blob
        if isinstance(value, (bytes, bytearray)):
            raise AssertionError("request thread parsed the board")
        return real_loads(value, *args, **kwargs)

    monkeypatch.setattr(board_artifact.json, "loads", guarded_loads)
    loaded_identity, payload = board_artifact.load_current_payload(config.snapshots_dir, PLAYER, snapshot_path=path, details_mtime_ns=None)
    assert loaded_identity.token == identity.token
    monkeypatch.setattr(board_artifact.json, "loads", real_loads)

    assert not hasattr(payload, "board")
    assert payload.meta["generated_at"] == "same"
    assert payload.meta["notice_count"] == 2  # the fixture notice + prepare_board's assets_incomplete
    assets = payload.meta["coverage"]["assets"]
    assert (assets["expected"], assets["retrieved"], assets["failed"]) == (1, 0, 1)
    response = get_board(_request(config, {"accept-encoding": "gzip"}))
    assert response.status_code == 200
    assert response.headers.get("content-encoding") is None  # below the 1024-byte gzip floor
    assert json.loads(response.headers["x-ceres-board-status"])["notices"][0]["code"] == "assets_incomplete"


def test_matching_warm_token_returns_304(config, monkeypatch):
    """M2: ignoring the token in conditional requests must fail the warm-read guard."""
    board = {"schema": "ceres.board.v1", "generated_at": "same", "plots": [], "coverage": {"assets": {}}}
    report = {"query": {"player": PLAYER}, "generated_at": "same"}
    path, identity = _identity(config, "b" * 32 + "-1", report)
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: board)
    board_artifact.publish(config.snapshots_dir, PLAYER, identity, report, {})
    monkeypatch.setattr("ceres.routes_board.details_cache_mtime", lambda: None)
    monkeypatch.setattr(capture, "latest_snapshot", lambda *args: (_ for _ in ()).throw(AssertionError("fast path parsed snapshot")))

    response = get_board(_request(config, {"if-none-match": f'"{identity.token}"'}))

    assert response.status_code == 304
    assert response.headers["etag"] == f'"{identity.token}"'
    assert response.body == b""
    assert path.exists()


def test_warm_304_uses_only_the_in_memory_artifact(config, monkeypatch):
    report = {"query": {"player": PLAYER}, "generated_at": "same"}
    board = {"schema": "ceres.board.v1", "generated_at": "same", "plots": [], "coverage": {"assets": {}}}
    path, identity = _identity(config, "f" * 32 + "-1", report)
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: board)
    board_artifact.publish(config.snapshots_dir, PLAYER, identity, report, {})

    first = get_board(_request(config))
    opened = []
    real_open = Path.open

    def counted_open(candidate, *args, **kwargs):
        opened.append(Path(candidate))
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)
    response = get_board(_request(config, {"if-none-match": first.headers["etag"]}))

    assert response.status_code == 304
    assert response.body == b""
    assert not opened
    assert path.exists()


def test_warm_200_serves_cached_artifact_bytes(config, monkeypatch):
    report = {"query": {"player": PLAYER}, "generated_at": "same"}
    board = {"schema": "ceres.board.v1", "generated_at": "same", "plots": [], "coverage": {"assets": {}}}
    _path, identity = _identity(config, "1" * 32 + "-1", report)
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: board)
    board_artifact.publish(config.snapshots_dir, PLAYER, identity, report, {})

    first = get_board(_request(config))
    opened = []
    real_open = Path.open

    def counted_open(candidate, *args, **kwargs):
        opened.append(Path(candidate))
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)
    response = get_board(_request(config))

    assert response.status_code == 200
    assert response.body == first.body
    assert not opened


def test_token_change_replaces_cached_artifact(config, monkeypatch):
    first_report = {"query": {"player": PLAYER}, "generated_at": "first"}
    second_report = {"query": {"player": PLAYER}, "generated_at": "second"}
    first_board = {"schema": "ceres.board.v1", "generated_at": "first", "plots": [], "coverage": {"assets": {}}}
    second_board = {"schema": "ceres.board.v1", "generated_at": "second", "plots": [], "coverage": {"assets": {}}}
    snapshot_path, first_identity = _identity(config, "2" * 32 + "-1", first_report)
    monkeypatch.setattr(
        board_artifact,
        "project_board",
        lambda value, details, lean: first_board if value["generated_at"] == "first" else second_board,
    )
    board_artifact.publish(config.snapshots_dir, PLAYER, first_identity, first_report, {})
    first_response = get_board(_request(config))

    second_snapshot_path, second_identity = _identity(config, "3" * 32 + "-2", second_report)
    board_artifact.publish(config.snapshots_dir, PLAYER, second_identity, second_report, {})
    opened = []
    real_open = Path.open

    def counted_open(candidate, *args, **kwargs):
        opened.append(Path(candidate))
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)
    second_response = get_board(_request(config))

    assert first_response.headers["etag"] == f'"{first_identity.token}"'
    assert second_response.headers["etag"] == f'"{second_identity.token}"'
    assert second_response.body != first_response.body
    assert json.loads(second_response.body)["generated_at"] == "second"
    first_artifact = board_artifact.artifact_path(config.snapshots_dir, PLAYER, first_identity.token)
    second_artifact = board_artifact.artifact_path(config.snapshots_dir, PLAYER, second_identity.token)

    assert set(opened) == {second_artifact, board_artifact.gzip_artifact_path(config.snapshots_dir, PLAYER, second_identity.token)}
    assert not first_artifact.exists()
    assert second_artifact.exists()
    # `_identity` returns the ONE rewritten snapshot (`land-<player>-latest.json`), not an
    # artifact -- both captures write the same file, so it can never be the pruned one.
    assert second_snapshot_path == snapshot_path
    assert snapshot_path.exists()


def test_valid_artifact_fast_path_never_hashes_snapshot(config, monkeypatch):
    report = {"query": {"player": PLAYER}, "generated_at": "same"}
    board = {"schema": "ceres.board.v1", "generated_at": "same", "plots": [], "coverage": {"assets": {"expected": 0}}}
    path, identity = _identity(config, "d" * 32 + "-1", report)
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: board)
    board_artifact.publish(config.snapshots_dir, PLAYER, identity, report, {})
    real_hash = board_artifact._snapshot_sha256
    real_stat = Path.stat
    snapshot_reads = []
    snapshot_stats = []

    def guarded_hash(candidate):
        if Path(candidate) == path:
            snapshot_reads.append(candidate)
            raise AssertionError("fast artifact validation read the snapshot")
        return real_hash(candidate)

    monkeypatch.setattr(board_artifact, "_snapshot_sha256", guarded_hash)

    def counted_stat(candidate, *args, **kwargs):
        if candidate == path:
            snapshot_stats.append(candidate)
        return real_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counted_stat)
    loaded_identity, loaded = board_artifact.load_current(
        config.snapshots_dir,
        PLAYER,
        snapshot_path=path,
        details_mtime_ns=None,
        details_size=None,
    )

    assert loaded_identity.token == identity.token
    assert loaded == board_artifact.artifact_meta(loaded_identity)
    assert loaded["coverage"]["assets"]["expected"] == 0
    assert not snapshot_reads
    assert snapshot_stats


def test_snapshot_save_never_reads_or_hashes_replaced_snapshot(config, monkeypatch):
    report = {"query": {"player": PLAYER}, "generated_at": "same"}
    path = capture._latest_path(config, PLAYER)
    replaced = False
    snapshot_opens = []
    hash_calls = []
    real_replace = capture.os.replace
    real_open = Path.open
    real_sha256 = board_artifact.hashlib.sha256

    def tracked_replace(source, destination):
        nonlocal replaced
        real_replace(source, destination)
        if Path(destination) == path:
            replaced = True

    def counted_open(candidate, *args, **kwargs):
        if replaced and Path(candidate) == path:
            snapshot_opens.append(candidate)
        return real_open(candidate, *args, **kwargs)

    def counted_sha256(*args, **kwargs):
        hash_calls.append(args)
        return real_sha256(*args, **kwargs)

    monkeypatch.setattr(capture.os, "replace", tracked_replace)
    monkeypatch.setattr(Path, "open", counted_open)
    monkeypatch.setattr(board_artifact.hashlib, "sha256", counted_sha256)
    # the artifact publish hashes the ARTIFACT bytes by design; this guard is about the snapshot
    monkeypatch.setattr(capture, "_publish_board_artifact", lambda *args: None)

    capture._save_snapshot_and_refresh_cache(report, path)

    assert replaced
    assert not snapshot_opens
    assert not hash_calls
    assert "snapshot_sha256" not in json.loads(
        board_artifact.index_path(config.snapshots_dir, PLAYER).read_text(encoding="utf-8")
    )


def test_conditional_board_refreshes_capture_failure_status_header(config, monkeypatch):
    report = {"query": {"player": PLAYER}, "generated_at": "same"}
    board = {"schema": "ceres.board.v1", "generated_at": "same", "plots": [], "coverage": {}}
    path, identity = _identity(config, "e" * 32 + "-1", report)
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: board)
    board_artifact.publish(config.snapshots_dir, PLAYER, identity, report, {})
    monkeypatch.setattr(board_artifact, "details_identity", lambda: (None, None))
    monkeypatch.setattr("ceres.routes_board.details_cache_mtime", lambda: None)
    states = iter((capture.CaptureStatus(error="capture failed"), capture.CaptureStatus(), capture.CaptureStatus(error="capture failed")))
    monkeypatch.setattr(capture, "freshness_from_board", lambda *args: next(states))

    first = get_board(_request(config))
    second = get_board(_request(config, {"if-none-match": first.headers["etag"]}))
    third = get_board(_request(config, {"if-none-match": first.headers["etag"]}))

    assert first.status_code == 200
    assert second.status_code == 304
    assert third.status_code == 304
    assert "capture_failed" not in first.body.decode()
    assert json.loads(first.headers["x-ceres-board-status"])["notices"][0]["code"] == "capture_failed"
    assert not json.loads(second.headers["x-ceres-board-status"])["notices"]
    assert json.loads(third.headers["x-ceres-board-status"])["notices"][0]["code"] == "capture_failed"


@pytest.mark.parametrize("failure", ["missing", "stale", "corrupt"])
def test_invalid_artifact_falls_back_to_current_projection(config, monkeypatch, failure):
    """M3: serving an invalid artifact instead of recomputing must fail this matrix."""
    report = {"query": {"player": PLAYER}, "generated_at": "same"}
    expected = {
        "schema": "ceres.board.v1",
        "generated_at": "same",
        "plots": [],
        "coverage": {key: None for key in (
            "expected", "retrieved", "fresh", "dropped", "failed", "not_requested",
            "worker_detail_pending", "worker_detail_pending_deeds", "oldest_observed_at",
        )},
    }
    path, identity = _identity(config, "c" * 32 + "-1", report)
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: expected)
    board_artifact.publish(config.snapshots_dir, PLAYER, identity, report, {})
    artifact = board_artifact.artifact_path(config.snapshots_dir, PLAYER, identity.token)
    if failure == "missing":
        artifact.unlink()
    elif failure == "corrupt":
        artifact.write_text("{broken", encoding="utf-8")
    else:
        path.write_bytes(path.read_bytes() + b" ")
    ref = capture.SnapshotRef(str(path), "same", PLAYER, token=identity.token)
    monkeypatch.setattr(capture, "latest_snapshot", lambda config, player: (report, ref))
    monkeypatch.setattr(capture, "freshness", lambda config, player: capture.CaptureStatus(age_seconds=1))
    monkeypatch.setattr("ceres.routes_board.load_details_by_id", lambda: {})
    monkeypatch.setattr(
        "ceres.routes_board._cached_project_board",
        lambda *args, **kwargs: (expected, (str(path), PLAYER, True, identity.token), identity.token, None),
    )

    response = get_board(_request(config, {"if-none-match": f'"{identity.token}"'}))

    assert response.status_code == 200
    assert json.loads(response.body) == expected


def test_heal_with_unchanged_generated_at_rotates_token_and_body(config, monkeypatch):
    """M8: suppressing the heal write counter must fail even when timestamps are unchanged."""
    report = {"query": {"player": PLAYER}, "generated_at": "same"}
    first = {"schema": "ceres.board.v1", "generated_at": "same", "plots": [{"value": 1}]}
    healed = {"schema": "ceres.board.v1", "generated_at": "same", "plots": [{"value": 2}]}
    first_report = {**report, "version": 1}
    healed_report = {**report, "version": 2}
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: first if value.get("version") == 1 else healed)
    monkeypatch.setattr("ceres.board.load_details_by_id", lambda: {})
    capture._save_snapshot_and_refresh_cache(first_report, capture._latest_path(config, PLAYER))
    old = board_artifact.read_identity(config.snapshots_dir, PLAYER)
    capture._save_snapshot_and_refresh_cache(healed_report, capture._latest_path(config, PLAYER))
    new = board_artifact.read_identity(config.snapshots_dir, PLAYER)

    assert new.write_counter > old.write_counter
    assert new.token != old.token
    assert board_artifact.artifact_path(config.snapshots_dir, PLAYER, new.token).exists()
    assert not board_artifact.artifact_path(config.snapshots_dir, PLAYER, old.token).exists()
    monkeypatch.setattr("ceres.routes_board.details_cache_mtime", lambda: new.details_mtime_ns)
    response = get_board(_request(config, {"if-none-match": f'"{old.token}"'}))
    assert response.status_code == 200
    assert response.headers["etag"] == f'"{new.token}"'
    assert json.loads(response.body)["plots"] == healed["plots"]


def test_artifact_is_published_before_the_board_ready_callback(config, monkeypatch):
    """ (replaces M4): `board_ready` can never be true before the artifact exists --
    publication runs synchronously and the ready callback sees the artifact (+ .gz) on disk."""
    report = {"query": {"player": PLAYER}, "generated_at": "same"}
    events = []
    monkeypatch.setattr("ceres.board.load_details_by_id", lambda: {})
    monkeypatch.setattr(board_artifact, "project_board", lambda value, details, lean: {"schema": "ceres.board.v1", "generated_at": "same", "plots": []})
    real_publish = board_artifact.publish

    def recording_publish(*args, **kwargs):
        events.append(("artifact", args[2].token))
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(board_artifact, "publish", recording_publish)

    def board_ready():
        identity = board_artifact.read_identity(config.snapshots_dir, PLAYER)
        events.append((
            "ready",
            board_artifact.artifact_path(config.snapshots_dir, PLAYER, identity.token).exists(),
            board_artifact.gzip_artifact_path(config.snapshots_dir, PLAYER, identity.token).exists(),
            identity.complete,
        ))

    capture._save_snapshot_and_refresh_cache(report, capture._latest_path(config, PLAYER), before_pool_write=board_ready)

    assert [event[0] for event in events] == ["artifact", "ready"]
    assert events[1] == ("ready", True, True, True)


def test_quick_capture_run_starts_no_prewarm_thread(config, monkeypatch):
    """: after the final publish nothing but the self-heal retries runs; the automatic
    picker prewarm and the wait-for-first-read pause are gone."""
    assert not hasattr(capture, "_prewarm_picker")
    assert not hasattr(capture, "_wait_for_first_board_read")
    assert not hasattr(capture, "_enqueue_board_artifact")
    from ceres import candidates
    assert not hasattr(candidates, "prewarm_candidates")
    assert candidates._heavy_lane._max_workers == 1
