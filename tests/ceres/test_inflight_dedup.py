from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from ceres import routes_dec, routes_harvest, routes_wallet
from praetoria import singleflight as singleflight_module


def _track_waiting_caller(monkeypatch):
    waiting = threading.Event()

    class TrackingEvent:
        def __init__(self):
            self.event = threading.Event()

        def wait(self, timeout=None):
            waiting.set()
            return self.event.wait(timeout)

        def set(self):
            self.event.set()

    class Pending:
        def __init__(self):
            self.event = TrackingEvent()
            self.value = None
            self.error = None

    monkeypatch.setattr(singleflight_module, "_Pending", Pending)
    return waiting


def test_singleflight_shares_only_overlapping_fetches(monkeypatch):
    key = ("s16764", "shared")
    started = threading.Event()
    release = threading.Event()
    calls = []
    waiting = _track_waiting_caller(monkeypatch)

    def fetch():
        calls.append("fetch")
        started.set()
        assert release.wait(2)
        return {"value": 7}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(singleflight_module.singleflight, key, fetch)
        assert started.wait(2)
        second = pool.submit(singleflight_module.singleflight, key, fetch)
        assert waiting.wait(2)
        release.set()
        assert first.result(timeout=2) == {"value": 7}
        assert second.result(timeout=2) == {"value": 7}
    assert calls == ["fetch"]

    assert singleflight_module.singleflight(key, fetch) == {"value": 7}
    assert singleflight_module.singleflight(key, fetch) == {"value": 7}
    assert calls == ["fetch", "fetch", "fetch"]


def test_singleflight_propagates_failure_and_does_not_reuse_it(monkeypatch):
    key = ("s16764", "error")
    started = threading.Event()
    release = threading.Event()
    error = RuntimeError("expected fetch failure")
    calls = []
    waiting = _track_waiting_caller(monkeypatch)

    def fail():
        calls.append("fail")
        started.set()
        assert release.wait(2)
        raise error

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(singleflight_module.singleflight, key, fail)
        assert started.wait(2)
        second = pool.submit(singleflight_module.singleflight, key, fail)
        assert waiting.wait(2)
        release.set()
        for future in (first, second):
            try:
                future.result(timeout=2)
            except RuntimeError as exc:
                assert exc is error
            else:
                raise AssertionError("the shared failure was not propagated")

    assert singleflight_module.singleflight(key, lambda: "recovered") == "recovered"
    assert calls == ["fail"]


def test_singleflight_different_keys_do_not_share():
    calls = []

    def fetch(value):
        calls.append(value)
        return value

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(singleflight_module.singleflight, ("s16764", "a"), lambda: fetch("a"))
        b = pool.submit(singleflight_module.singleflight, ("s16764", "b"), lambda: fetch("b"))
        assert {a.result(timeout=2), b.result(timeout=2)} == {"a", "b"}
    assert sorted(calls) == ["a", "b"]


def test_harvest_cached_inflight_keeps_display_cache(monkeypatch):
    monkeypatch.setattr(routes_harvest, "_cache", {})
    calls = []

    def fetch():
        calls.append("fetch")
        return SimpleNamespace(data={"data": []})

    key = ("s16764-cache", "player")
    assert routes_harvest._cached(key, fetch, inflight=True) == {"data": []}
    assert routes_harvest._cached(key, fetch, inflight=True) == {"data": []}
    assert calls == ["fetch"]


def test_harvest_fresh_inflight_never_reads_or_writes_cache(monkeypatch):
    class ForbiddenCache:
        def get(self, key):
            raise AssertionError("fresh reads must not read the display cache")

        def __setitem__(self, key, value):
            raise AssertionError("fresh reads must not write the display cache")

    monkeypatch.setattr(routes_harvest, "_cache", ForbiddenCache())
    assert routes_harvest._cached(("s16764-fresh", "player"), lambda: SimpleNamespace(data="fresh"),
                                  fresh=True, inflight=True) == "fresh"


def test_preflight_fresh_rebuild_does_not_enable_inflight(monkeypatch):
    player = "s16764-player"
    member = {"op": "harvest", "region_uid": "region-1", "deed_uids": []}
    stored = {"scope": {"kind": "regions"}, "chunks": [{"members": [member]}]}
    build_calls = []

    class Request:
        async def json(self):
            return {"preview_id": "preview", "preview_digest": "digest", "chunk_index": 0}

    def build_preview(request, requested_player, scope, **kwargs):
        build_calls.append(kwargs)
        return {"issues": [], "blocked": False, "chunks": [{"members": [member]}]}

    async def run_in_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(routes_harvest, "_require_same_origin", lambda request: None)
    monkeypatch.setattr(routes_harvest, "player_for_request", lambda request, value=None: player)
    monkeypatch.setattr(routes_harvest.evidence, "load_preview", lambda *args: stored)
    monkeypatch.setattr(routes_harvest, "_signing_or_403", lambda *args: None)
    monkeypatch.setattr(routes_harvest, "_build_preview", build_preview)
    monkeypatch.setattr(routes_harvest, "run_in_threadpool", run_in_threadpool)

    result = asyncio.run(routes_harvest.harvest_preflight(Request(), player=player))

    assert result["outcome"] == "PERMIT"
    assert len(build_calls) == 1
    assert build_calls[0].get("fresh") is True
    assert build_calls[0].get("inflight") in (None, False)


def test_wallet_and_dec_share_concurrent_balance_read(monkeypatch):
    player = "s16764-player"
    started = threading.Event()
    release = threading.Event()
    calls = []
    waiting = _track_waiting_caller(monkeypatch)
    payload = [{"player": player, "token": "DEC", "balance": 12.5}]

    class StubSplinterlandsClient:
        def __init__(self, **kwargs):
            pass

        def balances(self, requested_player):
            calls.append(requested_player)
            started.set()
            assert release.wait(2)
            return SimpleNamespace(data=payload)

    monkeypatch.setattr(routes_wallet, "SplinterlandsClient", StubSplinterlandsClient)
    monkeypatch.setattr(routes_dec, "SplinterlandsClient", StubSplinterlandsClient)
    barrier = threading.Barrier(3)

    def invoke(fn):
        barrier.wait()
        return fn(player)

    with ThreadPoolExecutor(max_workers=2) as pool:
        wallet = pool.submit(invoke, routes_wallet._balances)
        dec = pool.submit(invoke, routes_dec._balance)
        barrier.wait()
        assert started.wait(2)
        assert waiting.wait(2)
        release.set()
        assert wallet.result(timeout=2) == payload
        assert dec.result(timeout=2) == 12.5

    assert calls == [player]
