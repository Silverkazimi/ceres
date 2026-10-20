"""Direct route guards for the snapshot-only `/api/board` contract.

These tests call the route without TestClient because this sandbox cannot open sockets;
the capture trap is still the observable boundary and no browser/live service is used.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ceres import capture as capture_mod
from ceres.config import CeresConfig
from ceres.routes_board import get_board


def _request(config: CeresConfig, headers: dict | None = None):
    state = SimpleNamespace(ceres_config=config)
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        headers=SimpleNamespace(get=lambda key, default="": hdrs.get(key.lower(), default)),
    )


def test_existing_snapshot_get_never_starts_capture(monkeypatch, report, tmp_path):
    """G01 mutation trial: inserting `start_refresh()` into GET made this RED; the
 restored snapshot-only route is GREEN."""
    config = CeresConfig(root=tmp_path / "ceres", player="player0005")
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    monkeypatch.setattr(capture_mod, "freshness", lambda config, player: capture_mod.CaptureStatus(state="idle", age_seconds=1, stale=False))
    monkeypatch.setattr(capture_mod, "start_refresh", lambda *args: (_ for _ in ()).throw(AssertionError("GET must not capture")))
    monkeypatch.setattr("ceres.routes_board.load_details_by_id", lambda: {})
    monkeypatch.setattr("ceres.routes_board.project_board", lambda *args, **kwargs: {"schema": "ceres.board.v1", "plots": []})

    response = get_board(_request(config))
    assert response.status_code == 200


def test_missing_snapshot_get_returns_503_without_capture(monkeypatch, tmp_path):
    """G02 mutation trial: replacing the 503 branch with capture startup made this
 guard RED; the restored no-data response is GREEN."""
    config = CeresConfig(root=tmp_path / "ceres", player="player0005")
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (None, None))
    monkeypatch.setattr(capture_mod, "start_refresh", lambda *args: (_ for _ in ()).throw(AssertionError("GET must not capture")))

    with pytest.raises(HTTPException) as caught:
        get_board(_request(config))
    assert caught.value.status_code == 503


# (2026-09-20 speed audit, F4): the whale-board serialized-response-bytes
# cache. These tests exercise ONLY the fallback ("no board artifact yet") branch of
# `get_board()` -- `_board_bytes_cache` never runs on the happy path.


def _fallback_setup(monkeypatch, report, tmp_path, *, token="tok-1", details_mtime=111, board=None):
    """Force `get_board()` down the fallback branch (no `board_artifact` for this
 player) with a real `SnapshotRef` (a usable path+token, unlike the bare
 `object()` sentinel the other tests in this file use, which deliberately makes
 `_board_cache_key` derive no cache key at all). Patches `project_board` (the
 lowest-level projection), NOT `_cached_project_board` itself, so the real
 key-derivation, object-cache and bytes-cache code under test actually runs."""
    config = CeresConfig(root=tmp_path / "ceres", player="player0005")
    ref = capture_mod.SnapshotRef(path=str(tmp_path / "snap.json"), generated_at="t", player="player0005", token=token)
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, ref))
    monkeypatch.setattr(capture_mod, "freshness", lambda config, player: capture_mod.CaptureStatus(state="idle", age_seconds=1, stale=False))
    monkeypatch.setattr("ceres.routes_board.load_details_by_id", lambda: {})
    monkeypatch.setattr("ceres.routes_board.details_cache_mtime", lambda: details_mtime)
    board = board if board is not None else {"schema": "ceres.board.v1", "plots": [], "pad": "x" * 2000}
    monkeypatch.setattr("ceres.routes_board.project_board", lambda *a, **k: dict(board))
    return config


def _clear_board_caches():
    from ceres.routes_board import _board_bytes_cache, _board_bytes_cache_sizes, _board_cache, _board_cache_bytes

    import ceres.routes_board as rb

    _board_cache.clear()
    _board_cache_bytes.clear()
    _board_bytes_cache.clear()
    _board_bytes_cache_sizes.clear()
    rb._board_cache_total_bytes = 0


def _count_dumps(monkeypatch):
    import praetoria.fastjson as fastjson_mod

    real_dumps = fastjson_mod.dumps
    calls = {"n": 0}

    def counting_dumps(value, **kwargs):
        calls["n"] += 1
        return real_dumps(value, **kwargs)

    monkeypatch.setattr("ceres.routes_board.fastjson.dumps", counting_dumps)
    return calls


def test_repeat_read_of_same_snapshot_reuses_serialized_bytes(monkeypatch, report, tmp_path):
    _clear_board_caches()
    config = _fallback_setup(monkeypatch, report, tmp_path)
    calls = _count_dumps(monkeypatch)

    r1 = get_board(_request(config))
    r2 = get_board(_request(config))

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.body == r2.body
    assert calls["n"] == 1, "second read of the same snapshot must reuse the cached bytes, not re-serialize"


def test_changed_snapshot_token_misses_the_bytes_cache(monkeypatch, report, tmp_path):
    _clear_board_caches()
    config = _fallback_setup(monkeypatch, report, tmp_path, token="tok-1")
    calls = _count_dumps(monkeypatch)
    get_board(_request(config))
    assert calls["n"] == 1

    # A fresh live capture always mints a new token () -- a second read that
    # names a NEW token must never be served the first token's cached bytes.
    _fallback_setup(monkeypatch, report, tmp_path, token="tok-2")
    get_board(_request(config))
    assert calls["n"] == 2, "a changed snapshot token must miss the bytes cache"


def test_changed_details_mtime_misses_the_bytes_cache(monkeypatch, report, tmp_path):
    _clear_board_caches()
    config = _fallback_setup(monkeypatch, report, tmp_path, token="tok-1", details_mtime=111)
    calls = _count_dumps(monkeypatch)
    get_board(_request(config))
    assert calls["n"] == 1

    # Same snapshot token, but the card-details cache changed underneath it -- the
    # projected board can differ even though the token didn't, so this must also miss.
    _fallback_setup(monkeypatch, report, tmp_path, token="tok-1", details_mtime=222)
    get_board(_request(config))
    assert calls["n"] == 2, "a changed details_cache_mtime() must miss the bytes cache"


def test_key_derivation_failure_still_returns_200_uncached(monkeypatch, report, tmp_path):
    """`ref` with no usable `.token` (the bare `object()` sentinel other tests in this
 file already rely on) must degrade to plain, uncached serialization -- never a 500."""
    from ceres.routes_board import _board_bytes_cache

    _clear_board_caches()
    config = CeresConfig(root=tmp_path / "ceres", player="player0005")
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    monkeypatch.setattr(capture_mod, "freshness", lambda config, player: capture_mod.CaptureStatus(state="idle", age_seconds=1, stale=False))
    monkeypatch.setattr("ceres.routes_board.load_details_by_id", lambda: {})
    monkeypatch.setattr("ceres.routes_board.project_board", lambda *a, **k: {"schema": "ceres.board.v1", "plots": [], "pad": "x" * 2000})

    response = get_board(_request(config, headers={"Accept-Encoding": "gzip"}))
    assert response.status_code == 200
    assert len(_board_bytes_cache) == 0, "no cache entry should be written when no key can be derived"


def _count_gzip_compress(monkeypatch):
    import gzip as gzip_mod

    real_compress = gzip_mod.compress
    calls = {"n": 0}

    def counting_compress(*args, **kwargs):
        calls["n"] += 1
        return real_compress(*args, **kwargs)

    monkeypatch.setattr("ceres.routes_board.gzip.compress", counting_compress)
    return calls


def test_gzip_lazy_fill_and_reuse_on_cache_hit(monkeypatch, report, tmp_path):
    _clear_board_caches()
    config = _fallback_setup(monkeypatch, report, tmp_path)
    gzip_calls = _count_gzip_compress(monkeypatch)

    r1 = get_board(_request(config, headers={"Accept-Encoding": "gzip"}))
    assert gzip_calls["n"] == 1, "first gzip-accepting read computes the gzip copy"
    assert r1.headers["content-encoding"] == "gzip"
    assert r1.headers["vary"] == "Accept-Encoding"

    r2 = get_board(_request(config, headers={"Accept-Encoding": "gzip"}))
    assert gzip_calls["n"] == 1, "second gzip-accepting read of the same snapshot reuses the cached gzip bytes"
    assert r2.headers["content-encoding"] == "gzip"
    assert r1.body == r2.body


def test_gzip_not_computed_when_client_never_accepts_it(monkeypatch, report, tmp_path):
    _clear_board_caches()
    config = _fallback_setup(monkeypatch, report, tmp_path)
    gzip_calls = _count_gzip_compress(monkeypatch)

    response = get_board(_request(config))
    assert response.status_code == 200
    assert gzip_calls["n"] == 0, "a caller that never sends Accept-Encoding: gzip must never pay to compress a copy nobody wants"


def test_gzip_from_an_older_raw_generation_is_never_served_with_newer_raw(monkeypatch, report, tmp_path):
    """Regression for the review finding: a late fill of the gzip copy for an OLD raw
 generation must not be attached to a NEWER raw entry stored at the same key in
 between (same token, `details_cache_mtime()` changed and a fresh raw was cached)."""
    from ceres.routes_board import _board_bytes_cache, _board_cache_key, _cached_board_bytes, _cached_board_gzip

    _clear_board_caches()
    ref = capture_mod.SnapshotRef(path=str(tmp_path / "snap.json"), generated_at="t", player="player0005", token="tok-1")
    key, token, _ = _board_cache_key(ref, "player0005", True)

    board_a = {"schema": "ceres.board.v1", "plots": [], "pad": "A" * 2000}
    board_b = {"schema": "ceres.board.v1", "plots": [], "pad": "B" * 2000}

    raw_a, _ = _cached_board_bytes(key, token, 111, board_a)
    # A NEW entry (same token, different details_mtime_ns) supersedes A at the same key.
    raw_b, _ = _cached_board_bytes(key, token, 222, board_b)
    assert raw_a != raw_b

    # A late gzip fill for the now-superseded A generation must be rejected, not
    # attached to B's cache entry.
    gz_a = _cached_board_gzip(key, token, 111, raw_a)
    cached = _board_bytes_cache.get(key)
    assert cached is not None
    assert cached[2] == raw_b
    assert cached[3] is None, "a stale generation's gzip must never be stored against the current raw entry"

    # And reading gzip for B's own generation must never return A's.
    gz_b = _cached_board_gzip(key, token, 222, raw_b)
    assert gz_a != gz_b
    cached = _board_bytes_cache.get(key)
    assert cached[3] == gz_b
