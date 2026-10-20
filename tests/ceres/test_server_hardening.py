""" S1 (v1 clean-up, audit findings L1-02, L1-04, L1-05, L1-07): bounded input, bounded logs, metered upstream-reading routes."""
import logging
import re
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ceres import client_limits, harvest_routing as routing
from ceres.logging_setup import access_log_middleware, log_safe
from ceres.request_limits import BodyLimitMiddleware, recursion_handler

ROOT = Path(__file__).parents[2] / "ceres"


# L1-02: an amount like 0e-1000000000 allocated about a gigabyte
@pytest.mark.parametrize("value", ["0e-1000000000", "1e1000000", "9" * 80, "0." + "0" * 80 + "1", "1e-31"])
def test_l1_02_hostile_amounts_are_refused_fast(value):
    started = time.perf_counter()
    with pytest.raises(routing.RoutingError):
        routing.amount(value)
    assert time.perf_counter() - started < 0.2


@pytest.mark.parametrize("value", ["0", "10", "12.345", "0.001", "1000000"])
def test_l1_02_normal_amounts_still_parse(value):
    assert routing.amount(value) == routing.amount(str(value))


# L1-04: no body limit, uncaught deep nesting
def _app():
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware)
    app.add_exception_handler(RecursionError, recursion_handler)

    @app.post("/echo")
    async def echo(request: Request):
        return {"keys": len(await request.json())}

    return TestClient(app, raise_server_exceptions=False)


def test_l1_04_oversized_body_is_refused_with_413_by_declared_length():
    assert _app().post("/echo", content=b"[" + b"1," * (3 * 1024 * 1024) + b"1]").status_code == 413


def test_l1_04_oversized_streamed_body_without_content_length_is_refused():
    def chunks():
        for _ in range(6):
            yield b"[" + b"1," * (1024 * 1024) + b"1]"
    assert _app().post("/echo", content=chunks()).status_code == 413


def test_l1_04_deeply_nested_json_is_a_clean_422():
    # deep enough to exceed the parser's recursion limit on every supported Python (3.12 parses 1,500 levels without complaint)
    assert _app().post("/echo", content=b"[" * 400000 + b"]" * 400000).status_code in (413, 422)


def test_l1_04_normal_bodies_still_pass():
    assert _app().post("/echo", json=[1, 2, 3]).json() == {"keys": 3}


def test_l1_04_the_real_app_carries_the_limit():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "app.add_middleware(BodyLimitMiddleware)" in source and "RecursionError" in source


# L1-07: a %0A player could forge a log line
def test_l1_07_log_safe_escapes_controls_and_bounds_length():
    assert "\n" not in log_safe("a\nb\r\x1b[31m ") and "\\x0a" in log_safe("a\nb")
    assert len(log_safe("x" * 5000)) < 200


def test_l1_07_a_forged_player_cannot_add_a_log_line(caplog):
    app = FastAPI()
    app.middleware("http")(access_log_middleware)

    @app.post("/api/x")
    async def x():
        return {}

    with caplog.at_level(logging.INFO):
        TestClient(app).post("/api/x?player=bob%0A2026-10-01T00:00:00+0000%20ERROR%20ceres:%20forged")
    assert caplog.records and all("\n" not in record.getMessage() for record in caplog.records)


# --- L1-05: unmetered upstream-reading routes ---------------------------------------------------------------------------------
def test_l1_05_buckets_exist():
    assert {"verify", "routing"} <= set(client_limits.BUCKETS)


@pytest.mark.parametrize("module, marker", [
    ("routes_wallet.py", '"preview"'), ("routes_dec.py", '"verify"'), ("routes_plot_lock.py", '"verify"'),
    ("routes_op_preview.py", '"verify"'), ("routes_harvest_routing.py", '"routing"')])
def test_l1_05_each_route_module_spends_a_bucket(module, marker):
    source = (ROOT / module).read_text(encoding="utf-8")
    assert re.search(r"client_limits\.limit\(request " + re.escape(marker) + r"\)", source), module
    assert re.search(r"^from \. import .*client_limits", source, re.M), module + " calls client_limits without importing it"


def test_l1_05_the_bucket_really_limits():
    limiter = client_limits.Limiter()
    allowed = sum(limiter.hit("verify", "1.2.3.4") is None for _ in range(300))
    assert allowed == client_limits.BUCKETS["verify"][0][0]


# --- L1-03: blocking file lock and receipt read on the event loop ---------------------------------------------------------------
def test_l1_03_preview_moves_the_lock_and_the_receipt_read_off_the_event_loop():
    source = (ROOT / "routes_harvest_routing.py").read_text(encoding="utf-8")
    preview = source[source.index("async def preview("):source.index("def _remaining_harvest")]
    assert "await asyncio.to_thread(finish)" in preview
    assert "await asyncio.to_thread(_receipts, request, player" in preview
    assert "with runs.locked(" not in preview.split("def finish()")[0]       # no lock is taken in the async body itself
