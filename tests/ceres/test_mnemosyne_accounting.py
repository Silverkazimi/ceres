""" Phase 1 guards, authored for the separate Apollo execution queue.

Wire fixtures replace connections below real production adapters. Guard docstrings
name the semantic state and mutation that must make the assertion fail.
"""

from __future__ import annotations

import gzip
import http.client
import io
import json
import os
import threading
import urllib.parse
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

from mnemosyne import accounting
from ceres import capture as capture_mod
from ceres.config import CeresConfig
from ceres import routes_op_preview as routes
from fides import verify as fides_verify
from praetoria.api import SplinterlandsClient
from praetoria.errors import SchemaError, TransportError, UpstreamError
from praetoria.land_analysis import LandCapture, LandClient
from praetoria.transport import FixedGetTransport, ResponseTooLarge
from tests.ceres import test_shared_verification_evidence as shared


_HTTP_CONTENT_LENGTH = http.client.HTTPConnection._get_content_length


class MemorySink:
    def __init__(self):
        self.records = []

    def offer(self, record):
        self.records.append(record)
        return True


class LockAwareHealth(dict):
    def __getitem__(self, key):
        assert accounting._observer_health_lock.locked()
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        assert accounting._observer_health_lock.locked()
        return super().__setitem__(key, value)


class WireResponse(io.BytesIO):
    def __init__(self, body=b"{}", *, status=200, headers=None):
        super().__init__(body)
        self.status = self.code = status
        self.reason = "fixture response"
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self.msg = self.reason
        self.read_calls = []

    def read(self, size=-1):
        self.read_calls.append(size)
        return super().read(size)

    def getcode(self):
        return self.status

    def info(self):
        return self.headers


class FakeWire:
    def __init__(self, replies):
        self.replies = deque(replies) if not callable(replies) else replies
        self.sends = []

    def connection_class(self):
        wire = self

        class Connection:
            debuglevel = 0  # Python 3.12's AbstractHTTPHandler reads HTTPConnection.debuglevel and _http_vsn from the class
            _http_vsn = 11
            _http_vsn_str = "HTTP/1.1"
            mnemosyne_observation = "single_attempt"
            _get_content_length = staticmethod(_HTTP_CONTENT_LENGTH)

            def __init__(self, host, port=None, timeout=None, **kwargs):
                self.host = host
                self.port = port
                self.sock = None

            def set_debuglevel(self, level):
                pass

            def set_tunnel(self, host, port=None, headers=None):
                self.host = host
                self.port = port

            def request(self, method, target, body=None, headers=None, **kwargs):
                send = {"host": self.host, "method": method, "target": target, "body": body}
                wire.sends.append(send)
                self.reply = wire.replies(send) if callable(wire.replies) else wire.replies.popleft()
                if isinstance(self.reply, BaseException):
                    raise self.reply

            def getresponse(self):
                return self.reply

            def close(self):
                pass

        return Connection

    def install(self, monkeypatch):
        monkeypatch.setenv("no_proxy", "*")
        connection = self.connection_class()
        monkeypatch.setattr(http.client, "HTTPConnection", connection)
        monkeypatch.setattr(http.client, "HTTPSConnection", connection)
        # Ceres now shares pooled clients across captures. Give each wire its own
        # pools, or a warm connection keeps the previous fixture's reply closure.
        monkeypatch.setattr(capture_mod, "_SHARED_LAND_CLIENT", LandClient())
        monkeypatch.setattr(capture_mod, "_SHARED_COLLECTION_CLIENT", SplinterlandsClient())


def get_transport(monkeypatch, wire, adapter, *, retries=3, max_response_bytes=1024):
    wire.install(monkeypatch)
    monkeypatch.setattr("praetoria.transport.time.sleep", lambda delay: None)
    opener = accounting.observed_opener() if adapter == "opener" else None
    return FixedGetTransport(opener=opener, retries=retries, max_response_bytes=max_response_bytes)


def attempts(sink):
    return [record for record in sink.records if record["type"] == "attempt"]


def summaries(sink):
    return [record for record in sink.records if record["type"] == "run-summary"]


@pytest.mark.parametrize("adapter", ["pooled", "opener"])
@pytest.mark.parametrize("exhausted", [False, True])
def test_g1_retry_has_one_land_and_transport_dispatch_but_every_terminal_attempt(monkeypatch, tmp_path, adapter, exhausted):
    """G1 red: count outside retry, successes only, pooled only, or Land per retry."""
    statuses = [503, 503, 503] if exhausted else [503, 200]
    wire = FakeWire([WireResponse(status=status) for status in statuses])
    transport = get_transport(monkeypatch, wire, adapter)
    capture = LandCapture(min_delay=0, cache=shared._NoCache())
    sink = MemorySink()
    with accounting.run_scope("verification", sink=sink) as run:
        fetch = lambda: transport.get_json("https://api.splinterlands.com/cards/get_details")
        if exhausted:
            with pytest.raises(TransportError):
                capture._request(fetch)
        else:
            assert capture._request(fetch).attempts == 2
    summary = run.snapshot()
    assert capture._requests == summary["land_logical_dispatches"] == 1
    assert summary["transport_logical_dispatches"] == 1
    assert summary["outbound_attempts_started"] == summary["outbound_attempts_finished"] == len(wire.sends) == len(statuses)
    events = attempts(sink)
    assert len(events) == len(statuses)
    assert len({event["dispatch_id"] for event in events}) == 1
    assert len({event["land_dispatch_id"] for event in events}) == 1
    assert all(event["dispatch_id"] and event["land_dispatch_id"] for event in events)
    assert len({event["attempt_id"] for event in events}) == len(statuses)
    assert [event["retry_ordinal"] for event in events] == list(range(1, len(statuses) + 1))
    assert [event["status"] for event in events] == statuses
    for event in events:
        assert event["run_id"] == summary["run_id"]
        assert event["cause"] == "verification"
        assert event["origin"] == "https://api.splinterlands.com:443"
        assert event["started_at"] and event["ended_at"]
        assert event["elapsed_seconds"] >= 0
        assert event["outcome"] == ("error" if event["status"] == 503 else "response")
    assert summary["count_complete"] is True


def test_g2_cache_invalid_origin_and_exhausted_budget_do_not_invent_attempts(monkeypatch):
    """G2 red: count cache reads/reservations as wire or dispatch past Land budget."""
    wire = FakeWire([WireResponse()])
    transport = get_transport(monkeypatch, wire, "pooled", retries=1)
    cache = SimpleNamespace(read=lambda *args, **kwargs: ({"cached": True}, {}))
    capture = LandCapture(cache=cache, min_delay=0, request_budget=1)
    with accounting.run_scope("verification", sink=MemorySink()) as run:
        assert capture._cached("warm", lambda: pytest.fail("cache dispatched"), False, ttl=60) == {"cached": True}
        with pytest.raises(UpstreamError):
            capture._request(lambda: transport.get_json("https://foreign.example/forbidden"))
        with pytest.raises(TransportError, match="budget exhausted"):
            capture._request(lambda: transport.get_json("https://api.splinterlands.com/cards/get_details"))
    summary = run.snapshot()
    assert capture._requests == summary["land_logical_dispatches"] == 1
    assert summary["outbound_attempts_started"] == summary["transport_logical_dispatches"] == len(wire.sends) == 0


@pytest.mark.parametrize("adapter", ["pooled", "opener"])
def test_g2_connection_failure_is_failed_attempt_with_unknown_delivery(monkeypatch, adapter):
    """G2 red: drop DNS/connect failures or label adapter invocation delivered."""
    wire = FakeWire([ConnectionError("private network error must not be serialized")])
    transport = get_transport(monkeypatch, wire, adapter, retries=1)
    sink = MemorySink()
    with accounting.run_scope("verification", sink=sink) as run:
        with pytest.raises(TransportError):
            transport.get_json("https://api.splinterlands.com/cards/get_details")
    assert run.snapshot()["outbound_attempts_started"] == len(wire.sends) == 1
    event, = attempts(sink)
    assert event["status"] is None
    assert event["send_progress"] == "unknown"
    assert event["error_class"]
    assert "private network error" not in json.dumps(sink.records)


@pytest.mark.parametrize("adapter", ["pooled", "opener"])
def test_g8_gzip_measures_consumed_encoded_bytes_separately_from_decoded(monkeypatch, adapter):
    """G8 red: count decoded/reserialized JSON as body transfer or sum both sizes."""
    decoded = json.dumps({"cards": ["repeated payload" for _ in range(20)]}, indent=2).encode()
    encoded = gzip.compress(decoded)
    response = WireResponse(encoded, headers={"Content-Encoding": "gzip", "Content-Length": str(len(encoded))})
    wire = FakeWire([response])
    transport = get_transport(monkeypatch, wire, adapter)
    sink = MemorySink()
    with accounting.run_scope("verification", sink=sink):
        assert transport.get_json("https://api.splinterlands.com/cards/get_details").data == json.loads(decoded)
    event, = attempts(sink)
    assert event["request_body_bytes_submitted"] == 0
    assert event["response_encoded_body_bytes"] == len(encoded)
    assert event["response_decoded_body_bytes"] == len(decoded)
    assert event["response_body_completeness"] == event["request_body_completeness"] == "complete"
    assert event["wire_bytes"] is None


@pytest.mark.parametrize("failure", ["truncated_gzip", "oversized", "unread_http_error", "incomplete_read"])
def test_g8_partial_or_unread_bodies_never_become_exact_zero(monkeypatch, failure):
    """G8 red: erase partial bytes, mark unread complete, or drain for telemetry."""
    encoded = gzip.compress(b'{"cards": [1, 2, 3]}')
    if failure == "truncated_gzip":
        response = WireResponse(encoded[:-5], headers={"Content-Encoding": "gzip"})
        error = SchemaError
    elif failure == "oversized":
        response = WireResponse(b"x" * 50, headers={"Content-Length": "50"})
        # The transport now reports its explicit size-limit error before reading
        # it is not a JSON schema failure and must still leave bytes incomplete.
        error = ResponseTooLarge
    elif failure == "unread_http_error":
        response = WireResponse(b"private unread server error", status=503)
        error = TransportError
    else:
        class PartialResponse(WireResponse):
            def read(self, size=-1):
                self.read_calls.append(size)
                raise http.client.IncompleteRead(b"known partial", 40)
        response = PartialResponse()
        error = TransportError
    wire = FakeWire([response])
    transport = get_transport(monkeypatch, wire, "pooled", retries=1, max_response_bytes=25)
    sink = MemorySink()
    with accounting.run_scope("verification", sink=sink) as run:
        with pytest.raises(error):
            transport.get_json("https://api.splinterlands.com/cards/get_details")
    event, = attempts(sink)
    assert event["response_body_completeness"] != "complete"
    assert run.snapshot()["bytes_complete"] is False
    if failure == "truncated_gzip":
        assert event["response_encoded_body_bytes"] == len(encoded) - 5
    elif failure == "incomplete_read":
        assert event["response_encoded_body_bytes"] == len(b"known partial")
        assert len(response.read_calls) == 1
    else:
        assert response.read_calls == []


def test_g9_opaque_injected_opener_cannot_certify_hidden_attempts(monkeypatch):
    """G9 red: assume opaque opener's final success proves one physical dispatch."""
    class OpaqueOpener:
        def open(self, request, **kwargs):
            return WireResponse()
    sink = MemorySink()
    with accounting.run_scope("verification", sink=sink) as run:
        FixedGetTransport(opener=OpaqueOpener()).get_json("https://api.splinterlands.com/cards/get_details")
    summary = run.snapshot()
    assert summary["transport_coverage"] == "unknown"
    assert summary["count_complete"] is False
    assert summary["transport_logical_dispatches"] == 1
    assert summary["outbound_attempts_started"] == 0


def terminal_fixture(monkeypatch, tmp_path, family="stake", *, pending=False, game_unknown=False):
    config = CeresConfig(root=tmp_path / ".ceres", player=shared.PLAYER)
    shared._seed_snapshot(config)
    operations = [shared._stake_with_card(uid) for uid in shared.DEEDS] if family == "stake" else [shared._batch_worksite()]
    for index, operation in enumerate(operations):
        payload = json.loads(operation[1]["json"])
        payload["n"] = f"fixture-nonce-{index}"
        operation[1]["json"] = json.dumps(payload)
    timeline = []

    def reply(send):
        timeline.append(("wire", send["host"], send["target"]))
        target = urllib.parse.urlsplit(send["target"])
        if send["method"] == "POST":
            payload = json.loads(send["body"])
            if payload["method"] == "condenser_api.get_dynamic_global_properties":
                result = {"head_block_number": 120}
            else:
                assert payload["method"] == "condenser_api.get_transaction"
                result = {"operations": operations, "block_num": 100}
            value = {"jsonrpc": "2.0", "id": 1, "result": result}
        elif target.path == "/transactions/lookup":
            if game_unknown:
                value = {}
            else:
                lookup = urllib.parse.parse_qs(target.query)["trx_id"][0]
                index = int(lookup.rsplit("-", 1)[1]) if "-" in lookup else 0
                value = {"trx_info": {"success": True, "player": shared.PLAYER, "data": operations[index][1]["json"]}}
        elif target.path.endswith("/assets"):
            uid = urllib.parse.unquote(target.path.split("/")[-2])
            value = {"status": "success", "data": {"cards": [], "items": []}} if pending else shared._asset_payload(uid)
        elif target.path.startswith("/cards/collection/"):
            value = {"player": shared.PLAYER, "cards": [shared._asset_payload(uid)["data"]["cards"][0] for uid in shared.DEEDS]}
        elif target.path.startswith("/land/deeds"):
            value = shared._batch_bundle()
        else:
            raise AssertionError(f"unexpected outbound request: {send['method']} {target.path}")
        return WireResponse(json.dumps(value).encode())

    wire = FakeWire(reply)
    wire.install(monkeypatch)
    captures = []

    def make_capture(**clients):  # ceres/capture.py hands the shared land/collection clients through
        capture = LandCapture(cache=shared._ReferenceOnlyCache(), min_delay=0, sleeper=lambda delay: None, **clients)
        captures.append(capture)
        return capture

    monkeypatch.setattr(capture_mod, "LandCapture", make_capture)
    # Terminal refresh now holds the snapshot lock across merge and publication
    # so observe its locked writer, after the actual disk/cache update completes.
    original_save = capture_mod._save_snapshot_and_refresh_cache_locked

    def save(*args, **kwargs):
        result = original_save(*args, **kwargs)
        timeline.append(("persisted",))
        return result

    monkeypatch.setattr(capture_mod, "_save_snapshot_and_refresh_cache_locked", save)

    class OrderedSink(MemorySink):
        def offer(self, record):
            timeline.append(("telemetry", record["type"]))
            return super().offer(record)

    sink = OrderedSink()
    request = shared._request(config)
    request.app.state.mnemosyne_writer = sink
    return SimpleNamespace(config=config, request=request, sink=sink, wire=wire, captures=captures, operations=operations, timeline=timeline)


def invoke_terminal(fixture, *, attempt=2):
    return json.loads(routes.op_verify({
        "tx_id": "a" * 40, "operations": fixture.operations, "verification_attempt": attempt,
    }, fixture.request, player=None).body)


# MnemosyneEvidence memoizes three asset reads and one bundle; expand_plots
# adds collection only for worksite. Add one Hive + three/one game lookups
# stake = 4 + 1 + 3 = 8, worksite = 5 + 1 + 1 = 7 physical requests.
@pytest.mark.parametrize("family, physical, logical", [("stake", 8, 4), ("worksite", 7, 5)])
def test_g3_whole_terminal_root_encloses_chain_game_domain_and_last_save(monkeypatch, tmp_path, family, physical, logical):
    """G3 red: root at evidence, omit Hive/game, finish before save, or reread per deed."""
    fixture = terminal_fixture(monkeypatch, tmp_path, family)
    result = invoke_terminal(fixture)
    assert result["state"] == "domain-confirmed"
    assert result["refresh_errors"] == []
    summary, = summaries(fixture.sink)
    assert summary["outbound_attempts_started"] == summary["outbound_attempts_finished"] == len(fixture.wire.sends) == physical
    assert summary["land_logical_dispatches"] == fixture.captures[0]._requests == logical
    assert summary["transport_logical_dispatches"] == physical
    events = attempts(fixture.sink)
    assert len(events) == physical
    assert sum(event["service"] == "hive" for event in events) == 1
    assert sum(event["service"] == "splinterlands" for event in events) == physical - 1
    assert {event["phase"] for event in events} >= {"chain", "game_verdict", "domain", "post_confirmation_refresh"}
    assert {event["run_id"] for event in events} == {summary["run_id"]}
    assert {event["cause"] for event in events} == {"verification"}
    persisted = [index for index, event in enumerate(fixture.timeline) if event == ("persisted",)]
    assert len(persisted) == 1
    assert persisted[0] < fixture.timeline.index(("telemetry", "run-summary"))
    assert summary["count_complete"] is True
    assert summary["attribution_complete"] is True
    for counter in ("outbound_attempts_started", "transport_logical_dispatches", "response_encoded_body_bytes", "transport_coverage"):
        assert counter not in json.dumps(result)


def test_g7_repeated_transaction_and_ordinal_create_fresh_verification_identity(monkeypatch, tmp_path):
    """G7 red: global evidence, reuse operation-key slots, or ordinal as run identity."""
    fixture = terminal_fixture(monkeypatch, tmp_path)
    assert invoke_terminal(fixture)["state"] == "domain-confirmed"
    assert invoke_terminal(fixture)["state"] == "domain-confirmed"
    first, second = summaries(fixture.sink)
    assert first["run_id"] != second["run_id"]
    assert first["verification_attempt_id"] != second["verification_attempt_id"]
    assert first["verification_attempt"] == second["verification_attempt"] == 2
    assert first["operation_key"] == second["operation_key"]
    assert first["operation_key"] != "a" * 40
    assert len(fixture.captures) == 2
    assert len(fixture.wire.sends) == 16
    assert first["outbound_attempts_started"] == second["outbound_attempts_started"] == 8


def test_g7_same_evidence_reuses_response_failure_identity_without_provenance_io(monkeypatch):
    """G2/G7 red: eager accounting fetch, replay failure send, or replace retrieval time."""
    original_error = ConnectionError("fixture failure")
    wire = FakeWire([WireResponse(json.dumps(shared._asset_payload(shared.DEED)).encode()), original_error])
    transport = get_transport(monkeypatch, wire, "pooled", retries=1)
    capture = LandCapture(land_client=LandClient(transport=transport), min_delay=0, cache=shared._NoCache())
    monkeypatch.setattr(capture_mod, "LandCapture", lambda **clients: capture)
    with accounting.run_scope("verification", sink=MemorySink()) as run:
        evidence = capture_mod.MnemosyneEvidence(shared.PLAYER, attempt=2)
        assert wire.sends == []
        response = evidence.assets(shared.DEED)
        assert evidence.assets(shared.DEED) is response
        errors = []
        for _ in range(2):
            with pytest.raises(TransportError) as error:
                evidence.bundle()
            errors.append(error.value)
        assert errors[0] is errors[1]
        provenance = evidence.provenance()
        assert provenance["resources"]["assets"][shared.DEED]["retrieved_at"] == response.retrieved_at
        assert run.snapshot()["outbound_attempts_started"] == len(wire.sends) == 2
        assert evidence.accounting_identity.run_id == run.snapshot()["run_id"]


def test_g4_pending_branch_counts_actual_early_game_lookup_and_extra_head_rpc(monkeypatch, tmp_path):
    """G4 red: derive game calls from op count or omit pending branch head RPC."""
    fixture = terminal_fixture(monkeypatch, tmp_path, pending=True, game_unknown=True)
    result = invoke_terminal(fixture)
    assert result["state"] == "domain-not-applied"
    assert len([send for send in fixture.wire.sends if send["target"].startswith("/transactions/lookup")]) == 1
    methods = [json.loads(send["body"])["method"] for send in fixture.wire.sends if send["method"] == "POST"]
    assert methods == ["condenser_api.get_transaction", "condenser_api.get_dynamic_global_properties"]
    summary, = summaries(fixture.sink)
    assert summary["outbound_attempts_started"] == len(fixture.wire.sends)
    assert any(event["phase"] == "chain_head" for event in attempts(fixture.sink))


def test_g9_hive_redirects_are_observed_per_hop_and_post_bytes_are_original(monkeypatch):
    """G8/G9 red: outer urlopen only, double-count handler, hide redirected origin."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"head_block_number": 120}}).encode()
    wire = FakeWire([
        WireResponse(status=302, headers={"Location": "https://secondary.hive.example/rpc"}),
        WireResponse(body),
    ])
    wire.install(monkeypatch)
    sink = MemorySink()
    with accounting.run_scope("verification", sink=sink) as run:
        result = fides_verify._http_rpc_call("https://api.hive.blog", "condenser_api.get_dynamic_global_properties", [])
    assert result == {"head_block_number": 120}
    summary = run.snapshot()
    assert summary["transport_logical_dispatches"] == 1
    assert summary["outbound_attempts_started"] == len(wire.sends) == 2
    events = attempts(sink)
    assert len(events) == 2
    assert {event["origin"] for event in events} == {"https://api.hive.blog:443", "https://secondary.hive.example:443"}
    assert sorted(event["redirect_hop"] for event in events) == [0, 1]
    post, = [event for event in events if event["method"] == "POST"]
    assert post["request_body_bytes_submitted"] == len(wire.sends[0]["body"])
    assert events[-1]["response_encoded_body_bytes"] == len(body)


def test_g12_board_loading_copy_uses_plot_progress_without_request_fallback():
    """G12 red: restore requests_made fallback or replace ordinary plot progress."""
    from tests.ceres.vm_context import run_in_module_context

    result = run_in_module_context("""
      const absent = boardLoadingMessage({requests_made: 99});
 const progress = boardLoadingMessage({fetched_deeds: 2, total_deeds: 3, requests_made: 99});
 console.log(JSON.stringify({absent, progress}));
    """)
    rendered = json.loads(result.stdout)
    assert rendered["absent"] == "Reading your plots…"
    assert "2" in rendered["progress"] and "3" in rendered["progress"]
    assert "99" not in rendered["progress"]


@pytest.mark.parametrize("sink_failure", ["full_queue", "writer_failed", "sink_raises", "collector_raises"])
def test_g11_telemetry_loss_preserves_dispatch_schedule_and_marks_incomplete(monkeypatch, tmp_path, sink_failure):
    """G11 red: blocking enqueue/sync I/O, drop changes result, or complete lost trace."""
    wire = FakeWire([WireResponse(status=503), WireResponse()])
    transport = get_transport(monkeypatch, wire, "pooled")
    sleeps = []
    monkeypatch.setattr("praetoria.transport.time.sleep", sleeps.append)
    if sink_failure == "full_queue":
        sink = accounting.JournalWriter(tmp_path / "mnemosyne", queue_size=1)
        assert sink.offer({"type": "occupy"})
        original_put = sink.queue.put

        def put_nowait_only(item, block=True, timeout=None):
            assert block is False, "handler telemetry must never wait for queue space"
            return original_put(item, block=block, timeout=timeout)

        monkeypatch.setattr(sink.queue, "put", put_nowait_only)
    else:
        sink = MemorySink()
        if sink_failure == "writer_failed":
            sink.failed = True
        elif sink_failure == "sink_raises":
            def rejected(record):
                raise OSError("diagnostic disk failure")
            sink.offer = rejected
        else:
            def broken(self, record):
                raise RuntimeError("collector failure")
            monkeypatch.setattr(accounting.RunCollector, "finish_attempt", broken)

    def forbidden(*args, **kwargs):
        pytest.fail("telemetry performed synchronous file I/O or writer join on caller")

    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(threading.Thread, "join", forbidden)
    with accounting.run_scope("verification", sink=sink) as run:
        response = transport.get_json("https://api.splinterlands.com/cards/get_details")
    assert response.data == {}
    assert response.attempts == len(wire.sends) == 2
    assert sleeps == [1]
    summary = run.snapshot()
    assert summary["outbound_attempts_started"] == 2
    assert summary["trace_complete"] is False
    if sink_failure == "collector_raises":
        assert summary["count_complete"] is False
    elif sink_failure != "writer_failed":
        assert summary["trace_records_lost"] > 0
    if sink_failure == "full_queue":
        assert sink.queue.qsize() == 1
        assert sink.health()["dropped"] > 0


def test_g11_handler_failure_emits_final_summary_and_resets_root(monkeypatch, tmp_path):
    """G11 red: only finalize success or leak failed verification context to next use."""
    fixture = terminal_fixture(monkeypatch, tmp_path)
    original = RuntimeError("unexpected verifier failure")

    def fail_chain(*args, **kwargs):
        raise original

    monkeypatch.setattr(routes, "verify_chain", fail_chain)
    with pytest.raises(RuntimeError) as error:
        invoke_terminal(fixture)
    assert error.value is original
    summary, = summaries(fixture.sink)
    assert summary["cause"] == "verification"
    assert accounting.active_context() is None


def test_g6_thread_roots_and_reused_client_keep_immutable_cause_and_reset(monkeypatch):
    """G6 Phase 1 red: mutable/global cause, client-bound context, or missing reset."""
    barrier = threading.Barrier(2)
    observations = []

    def reply(send):
        context = accounting.active_context()
        observations.append((context.run_id, context.cause, context.phase))
        return WireResponse()

    wire = FakeWire(reply)
    client = get_transport(monkeypatch, wire, "pooled")
    completed = []
    failures = []

    def worker(cause):
        try:
            sink = MemorySink()
            with accounting.run_scope(cause, sink=sink) as run:
                barrier.wait(timeout=5)
                with accounting.phase_scope("domain"):
                    client.get_json("https://api.splinterlands.com/cards/get_details")
                with pytest.raises(ValueError):
                    with accounting.phase_scope("failing_phase"):
                        raise ValueError("fixture exception")
                assert accounting.active_context().phase == ""
            assert accounting.active_context() is None
            completed.append((cause, run.snapshot(), sink))
        except BaseException as error:
            failures.append(error)

    threads = [threading.Thread(target=worker, args=(cause,)) for cause in ("verification", "pre_sign")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert failures == []
    assert len(completed) == len(observations) == 2
    for cause, summary, sink in completed:
        assert (summary["run_id"], cause, "domain") in observations
        event, = attempts(sink)
        assert event["run_id"] == summary["run_id"] and event["cause"] == cause
    assert accounting.active_context() is None


def test_g9_direct_combine_collection_is_inside_verification_root(monkeypatch, tmp_path):
    """G3/G9 red: scope only Land evidence or omit direct combine collection client."""
    config = CeresConfig(root=tmp_path / ".ceres", player=shared.PLAYER)
    cards = [
        {"uid": "G18-100-000001", "card_detail_id": "879", "bcx": 10, "level": 1},
        {"uid": "G18-100-000002", "card_detail_id": "879", "bcx": 5, "level": 1},
    ]
    operation = ["custom_json", {
        "id": "sm_combine_cards", "json": json.dumps({"cards": [card["uid"] for card in cards], "app": "x", "n": "abcdefghij"}),
        "required_auths": [shared.PLAYER], "required_posting_auths": [],
    }]

    def reply(send):
        if send["method"] == "POST":
            value = {"result": {"operations": [operation], "block_num": 100}}
        elif send["target"].startswith("/transactions/lookup"):
            value = {"trx_info": {"success": True, "player": shared.PLAYER, "data": operation[1]["json"]}}
        else:
            assert send["target"] == f"/cards/collection/{shared.PLAYER}"
            value = {"cards": [{**cards[0], "bcx": 15}]}
        return WireResponse(json.dumps(value).encode())

    wire = FakeWire(reply)
    wire.install(monkeypatch)
    sink = MemorySink()
    request = shared._request(config)
    request.app.state.mnemosyne_writer = sink
    monkeypatch.setattr(routes.combine_evidence, "load", lambda *args, **kwargs: cards)
    monkeypatch.setattr(routes.combine_evidence, "discard", lambda *args, **kwargs: None)
    monkeypatch.setattr(capture_mod, "is_running", lambda account: False)
    monkeypatch.setattr(capture_mod, "start_refresh", lambda *args, **kwargs: True)
    result = json.loads(routes.op_verify({"tx_id": "a" * 40, "operations": [operation]}, request, player=None).body)
    assert result["state"] == "domain-confirmed"
    summary, = summaries(sink)
    assert summary["land_logical_dispatches"] == 0
    assert summary["outbound_attempts_started"] == len(wire.sends) == 3
    assert summary["by_endpoint"]["/cards/collection/{player}"] == 1


def test_g9_sanitized_attempts_exclude_account_deed_nonce_transaction_and_payload(monkeypatch, tmp_path):
    """G9 privacy red: serialize URL/query, RPC params, body, raw account/deed or nonce."""
    fixture = terminal_fixture(monkeypatch, tmp_path)
    invoke_terminal(fixture)
    serialized = json.dumps(fixture.sink.records)
    for secret in (shared.PLAYER, *shared.DEEDS, "a" * 40, "fixture-nonce-0", "CARD-deed-1"):
        assert secret not in serialized
    for event in attempts(fixture.sink):
        assert "?" not in event["endpoint"]
        assert event["endpoint"] != "other"
        assert event["schema_version"] == 1
        assert event["session_id"] and event["span_id"]


def test_pre_sign_root_attributes_future_fresh_read_at_op_preview_boundary(monkeypatch, tmp_path):
    """Pre-sign red: omit preview root or anchor cause after its eventual fresh read."""
    wire = FakeWire([WireResponse(json.dumps(shared._asset_payload(shared.DEED)).encode())])
    wire.install(monkeypatch)
    sink = MemorySink()
    config = CeresConfig(root=tmp_path / ".ceres", player=shared.PLAYER)
    request = shared._request(config)
    request.app.state.mnemosyne_writer = sink
    identities = []

    def preview(*args, **kwargs):
        evidence = capture_mod.MnemosyneEvidence(shared.PLAYER)
        identities.append(evidence.accounting_identity)
        evidence.assets(shared.DEED)
        return {"account": shared.PLAYER, "operations": [], "notices": []}

    monkeypatch.setattr(routes, "_preview", preview)
    monkeypatch.setattr(capture_mod, "LandCapture", lambda **clients: LandCapture(min_delay=0, cache=shared._NoCache(), **clients))
    result = routes.op_preview({}, request, player=None)
    assert result.status_code == 200
    summary, = summaries(sink)
    event, = attempts(sink)
    assert accounting.ROOT_CAUSES["op_preview"] == summary["cause"] == event["cause"] == "pre_sign"
    assert identities[0].run_id == summary["run_id"] == event["run_id"]
    assert summary["outbound_attempts_started"] == summary["land_logical_dispatches"] == len(wire.sends) == 1
    assert accounting.active_context() is None


def test_pre_sign_routes_read_root_cause_table(monkeypatch):
    """Pre-sign red: any preview route can drift from the root-cause table."""
    causes = []

    class Scope:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    def fake_run_scope(cause, **kwargs):
        causes.append(cause)
        return Scope()

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    monkeypatch.setattr(routes, "run_scope", fake_run_scope)
    monkeypatch.setattr(routes, "_preview", lambda *args, **kwargs: {})
    monkeypatch.setattr(routes, "_plot_worksite_preview", lambda *args, **kwargs: {})
    expected = ["table_op_preview", "table_plot_op_preview", "table_worksite_preview"]
    for key, cause in zip(("op_preview", "plot_op_preview", "plot_worksite_preview"), expected):
        monkeypatch.setitem(accounting.ROOT_CAUSES, key, cause)

    routes.op_preview({}, request, player=None)
    routes.plot_op_preview("deed", {}, request, player=None)
    routes.plot_worksite_preview("deed", {}, request, player=None)

    assert causes == expected


def test_g11_observer_health_increments_are_serialized(monkeypatch):
    """G11 red: concurrent observer failures must not lose health increments."""
    health = LockAwareHealth()
    dict.__setitem__(health, "collector_failures", 0)
    dict.__setitem__(health, "live_run_limit_rejections", 0)
    monkeypatch.setattr(accounting, "_observer_health", health)
    barrier = threading.Barrier(8)
    failures = []

    def worker():
        try:
            barrier.wait(timeout=5)
            for _ in range(100):
                accounting.notify_attempt(object(), "finish")
        except BaseException as error:
            failures.append(error)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert failures == []
    assert accounting._observer_health_snapshot()["collector_failures"] == 800


@pytest.mark.parametrize("adapter", ["pooled", "opener", "hive"])
def test_g11_observer_construction_failure_still_dispatches_original_request(monkeypatch, adapter):
    """G11 red: construct observed urllib Attempt outside fail-open helper."""
    body = b'{"result": {"head_block_number": 120}}' if adapter == "hive" else b"{}"
    wire = FakeWire([WireResponse(body)])
    transport = get_transport(monkeypatch, wire, "opener" if adapter == "opener" else "pooled", retries=1)

    def broken(*args, **kwargs):
        raise RuntimeError("observer constructor unavailable")

    monkeypatch.setattr(accounting, "Attempt", broken)
    with accounting.run_scope("verification", sink=MemorySink()) as run:
        if adapter == "hive":
            assert fides_verify._http_rpc_call("https://api.hive.blog", "condenser_api.get_dynamic_global_properties", []) == {"head_block_number": 120}
        else:
            assert transport.get_json("https://api.splinterlands.com/cards/get_details").data == {}
    assert len(wire.sends) == 1
    assert run.snapshot()["count_complete"] is False


@pytest.mark.parametrize("adapter", ["pooled", "opener"])
def test_g11_observer_finalization_error_cannot_replace_original_read_exception(monkeypatch, adapter):
    """G11 red: CountingReader directly finishes observer and replaces stream failure."""
    class TimeoutResponse(WireResponse):
        def read(self, size=-1):
            raise TimeoutError("original stream timeout")

    wire = FakeWire([TimeoutResponse()])
    transport = get_transport(monkeypatch, wire, adapter, retries=1)

    def broken(self, error=None):
        raise RuntimeError("telemetry finish failure")

    monkeypatch.setattr(accounting.Attempt, "finish", broken)
    with accounting.run_scope("verification", sink=MemorySink()) as run:
        with pytest.raises(TransportError, match="original stream timeout"):
            transport.get_json("https://api.splinterlands.com/cards/get_details")
    assert len(wire.sends) == 1
    assert run.snapshot()["count_complete"] is False


def test_g11_blocked_writer_never_moves_disk_io_to_handler(monkeypatch, tmp_path):
    """G11 red: writer backpressure stalls handler or falls back to handler file I/O."""
    wire = FakeWire([WireResponse()])
    transport = get_transport(monkeypatch, wire, "pooled", retries=1)
    entered = threading.Event()
    release = threading.Event()
    original_mkdir = Path.mkdir
    caller = threading.get_ident()
    io_threads = []

    def delayed_mkdir(path, *args, **kwargs):
        io_threads.append(threading.get_ident())
        assert threading.get_ident() != caller
        entered.set()
        assert release.wait(timeout=5)
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", delayed_mkdir)
    writer = accounting.JournalWriter(tmp_path / "mnemosyne", queue_size=1)
    writer.start()
    try:
        assert entered.wait(timeout=5)
        with accounting.run_scope("verification", sink=writer) as run:
            assert transport.get_json("https://api.splinterlands.com/cards/get_details").data == {}
        assert len(wire.sends) == 1
        assert run.snapshot()["trace_complete"] is False
        assert writer.health()["dropped"] > 0
        assert io_threads and caller not in io_threads
    finally:
        release.set()
        writer.close()


@pytest.mark.parametrize("client_family", ["land", "collection", "hive"])
def test_g9_injected_client_transport_unknown_is_not_coverage_complete(monkeypatch, client_family):
    """G9 red: injected clients bypass observer without marking unknown coverage."""
    class OpaqueTransport:
        def get_json(self, url):
            payload = shared._batch_bundle() if client_family == "land" else {"cards": []}
            return shared.ResponseData(payload, shared.RETRIEVED_AT, 3)

    with accounting.run_scope("verification", sink=MemorySink()) as run:
        if client_family == "land":
            LandClient(transport=OpaqueTransport()).bundle(shared.PLAYER)
        elif client_family == "collection":
            SplinterlandsClient(transport=OpaqueTransport()).collection(shared.PLAYER)
        else:
            fides_verify.get_transaction("a" * 40, transport=lambda *args: {"block_num": 100})
    summary = run.snapshot()
    assert summary["transport_coverage"] == "unknown"
    assert summary["count_complete"] is False
    assert summary["outbound_attempts_started"] == 0


def test_g11_failed_final_summary_offer_leaves_counts_but_not_complete_trace(monkeypatch):
    """G11 red: ignore final-summary queue rejection after constructing complete record."""
    class SummaryRejectingSink(MemorySink):
        def offer(self, record):
            return False if record["type"] == "run-summary" else super().offer(record)

    sink = SummaryRejectingSink()
    wire = FakeWire([WireResponse()])
    transport = get_transport(monkeypatch, wire, "pooled", retries=1)
    with accounting.run_scope("verification", sink=sink) as run:
        transport.get_json("https://api.splinterlands.com/cards/get_details")
    summary = run.snapshot()
    assert summary["outbound_attempts_started"] == len(wire.sends) == 1
    assert summary["count_complete"] is True
    assert summary["trace_complete"] is False
    assert summary["trace_records_lost"] == 1
    assert summaries(sink) == []


def test_g11_real_writer_open_failure_is_developer_health_not_request_failure(monkeypatch, tmp_path):
    """G11 red: disk failure changes transport response or reports healthy journal."""
    wire = FakeWire([WireResponse()])
    transport = get_transport(monkeypatch, wire, "pooled", retries=1)
    writer = accounting.JournalWriter(tmp_path / "mnemosyne")

    def fail_open(*args, **kwargs):
        raise OSError("fixture journal denied")

    monkeypatch.setattr(accounting.os, "open", fail_open)
    writer.start()
    try:
        with accounting.run_scope("verification", sink=writer) as run:
            assert transport.get_json("https://api.splinterlands.com/cards/get_details").data == {}
    finally:
        writer.close()
    assert writer.health()["failed"] is True
    assert run.snapshot()["trace_complete"] is False
    assert len(wire.sends) == 1


@pytest.mark.parametrize("capability, physical", [("single_attempt", 1), ("synthetic", 0)])
def test_g9_explicit_injected_transport_contract_distinguishes_observed_and_synthetic(capability, physical):
    """G9 red: opaque-complete inference, synthetic as real, or reject guaranteed adapter."""
    class DeclaredTransport:
        mnemosyne_observation = capability

        def get_json(self, url):
            return shared.ResponseData({"cards": []}, shared.RETRIEVED_AT, 1)

    with accounting.run_scope("verification", sink=MemorySink()) as run:
        SplinterlandsClient(transport=DeclaredTransport()).collection(shared.PLAYER)
    summary = run.snapshot()
    assert summary["transport_logical_dispatches"] == 1
    assert summary["outbound_attempts_started"] == physical
    assert summary["transport_coverage"] == ("complete" if capability == "single_attempt" else "synthetic")
    assert summary["count_complete"] is (capability == "single_attempt")


@pytest.mark.parametrize("adapter", ["pooled", "opener"])
def test_g8_valid_json_short_of_declared_length_is_partial_even_without_read_exception(monkeypatch, adapter):
    """G8 red: infer complete from short read while Content-Length proves missing bytes."""
    wire = FakeWire([WireResponse(b"{}", headers={"Content-Length": "50"})])
    transport = get_transport(monkeypatch, wire, adapter, retries=1)
    sink = MemorySink()
    with accounting.run_scope("verification", sink=sink) as run:
        assert transport.get_json("https://api.splinterlands.com/cards/get_details").data == {}
    event, = attempts(sink)
    assert event["response_encoded_body_bytes"] == 2
    assert event["response_body_completeness"] == "partial"
    assert run.snapshot()["bytes_complete"] is False
    assert run.snapshot()["count_complete"] is True


@pytest.mark.parametrize("failure", ["full_queue", "raising_sink"])
def test_g11_real_terminal_verdict_and_persistence_are_identical_when_journal_fails(monkeypatch, tmp_path, failure):
    """G11 red: telemetry loss changes route verdict, dispatch order or save-before-return."""
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = cls(2037, 1, 1, 0, 0, 5, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

    def exercise(root, sink=None):
        with monkeypatch.context() as patch:
            patch.setattr("praetoria.transport.datetime", FixedDatetime)
            patch.setattr(shared.land_analysis, "datetime", FixedDatetime)
            patch.setattr(capture_mod, "datetime", FixedDatetime)
            fixture = terminal_fixture(patch, root)
            if sink is not None:
                fixture.request.app.state.mnemosyne_writer = sink
            sleeps = []
            patch.setattr("praetoria.transport.time.sleep", sleeps.append)
            result = invoke_terminal(fixture)
            fixture.timeline.append(("returned",))
            assert result["state"] == "domain-confirmed"
            assert result["chain"]["status"] == "chain_verified"
            assert result["refresh_errors"] == []
            assert result["domain"]["evidence"]["provenance"]["account"] == shared.PLAYER
            saved = [index for index, event in enumerate(fixture.timeline) if event == ("persisted",)]
            assert len(saved) == 1
            assert saved[0] < fixture.timeline.index(("returned",))
            sends = [(send["method"], send["host"], send["target"], send["body"]) for send in fixture.wire.sends]
            assert len(sends) == 8
            snapshot_path = capture_mod._latest_path(fixture.config, shared.PLAYER)
            snapshot = snapshot_path.read_bytes()
            cards_path = capture_mod._cards_path(fixture.config, shared.PLAYER)
            cards = cards_path.read_bytes() if cards_path.exists() else None
            with capture_mod._snapshot_cache_lock:
                cached = deepcopy(capture_mod._snapshot_cache[str(snapshot_path)][1])
            return result, sends, sleeps, snapshot, cards, cached

    expected = exercise(tmp_path / "baseline")
    if failure == "full_queue":
        sink = accounting.JournalWriter(tmp_path / "journal", queue_size=1)
        assert sink.offer({"type": "occupy"})
    else:
        class FailingSink:
            def __init__(self):
                self.calls = 0

            def offer(self, record):
                self.calls += 1
                raise OSError("journal unavailable")
        sink = FailingSink()
    assert exercise(tmp_path / "failed-journal", sink) == expected
    if failure == "full_queue":
        assert sink.health()["dropped"] > 0
    else:
        assert sink.calls > 0


def test_g9_unscoped_adapter_remains_visible_in_distinct_unattributed_runs(monkeypatch):
    """G9 red: silently drop unscoped traffic or borrow prior request's closed root."""
    sink = MemorySink()
    monkeypatch.setattr(accounting, "_default_sink", sink)
    wire = FakeWire([WireResponse(), WireResponse(), WireResponse()])
    transport = get_transport(monkeypatch, wire, "pooled", retries=1)
    with accounting.run_scope("verification", sink=sink) as explicit:
        transport.get_json("https://api.splinterlands.com/cards/get_details")
    for _ in range(2):
        transport.get_json("https://api.splinterlands.com/cards/get_details")
    explicit_summary, first, second = summaries(sink)
    assert explicit_summary["run_id"] == explicit.run_id
    assert len({explicit_summary["run_id"], first["run_id"], second["run_id"]}) == 3
    for summary in (first, second):
        assert summary["cause"] == "unattributed"
        assert summary["attribution_complete"] is False
        assert summary["outbound_attempts_started"] == 1
    assert len(attempts(sink)) == len(wire.sends) == 3
