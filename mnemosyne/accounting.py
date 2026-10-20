"""Fail-open terminal-attempt accounting; no application or network dependencies."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
PROCESS_SESSION_ID = uuid.uuid4().hex
MAX_BUCKETS = 128
EXCLUSIONS = (
    "browser/CDN requests", "wallet extension internals and broadcasts", "other processes",
    "ad-hoc scripts and unapproved adapters", "inclusion polls outside this invocation",
    "background capture, capture/CLI/thread attribution (phase 2)",
)
ROOT_CAUSES = {"op_verify": "verification", "op_preview": "pre_sign",
               "plot_op_preview": "pre_sign", "plot_worksite_preview": "pre_sign"}
_context = ContextVar("mnemosyne_context", default=None)
_dispatch = ContextVar("mnemosyne_dispatch", default=None)
_land = ContextVar("mnemosyne_land_dispatch", default=None)
_default_sink = None
_live_runs = threading.BoundedSemaphore(256)
_observer_health = {"collector_failures": 0, "live_run_limit_rejections": 0}
_observer_health_lock = threading.Lock()


def _observer_health_snapshot():
    with _observer_health_lock:
        return dict(_observer_health)


def _record_observer_health(name):
    with _observer_health_lock:
        _observer_health[name] += 1


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def operation_digest(account, transaction_id):
    if len(transaction_id) != 40 or not all(character in "0123456789abcdefABCDEF" for character in transaction_id):
        return None
    return hashlib.sha256((account + "\0" + transaction_id.lower()).encode()).hexdigest()


def destination(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or "unknown"
        if len(host) > 253:
            return "unknown", "other", "other"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        origin = f"{parsed.scheme}://{host}:{port}"
        service = "splinterlands" if host in {"api.splinterlands.com", "vapi.splinterlands.com"} else "other"
        if host == "api.hive.blog":
            service = "hive"
        path = parsed.path
        fixed = {"/land/deeds", "/cards/get_details", "/players/item_details", "/players/balances", "/transactions/lookup"}
        patterns = (
            (r"/cards/collection/[^/]+", "/cards/collection/{player}"),
            (r"/land/stake/deeds/[^/]+/assets", "/land/stake/deeds/{deed_uid}/assets"),
            (r"/land/projects/deed/[^/]+/list", "/land/projects/deed/{deed_uid}/list"),
            (r"/land/deeds/owned/[^/]+", "/land/deeds/owned/{player}"),
            (r"/land/stake/items/[^/]+/available", "/land/stake/items/{stake_type_uid}/available"),
        )
        endpoint = path if path in fixed else "other"
        for pattern, template in patterns:
            if re.fullmatch(pattern, path):
                endpoint = template
                break
        return origin, service, endpoint
    except Exception:
        return "unknown", "other", "other"


@dataclass(frozen=True)
class Context:
    collector: object
    run_id: str
    span_id: str
    cause: str
    phase: str


def active_context():
    return _context.get()


def configure_sink(sink):
    global _default_sink
    previous = _default_sink
    _default_sink = sink
    return previous


def release_sink(sink, previous):
    global _default_sink
    if _default_sink is sink:
        stopping = getattr(previous, "stopping", None)
        _default_sink = None if getattr(previous, "failed", False) or (stopping is not None and stopping.is_set()) else previous


class RunCollector:
    def __init__(self, cause, phase="", sink=None, operation_key=None, verification_attempt=None):
        self.run_id = uuid.uuid4().hex
        self.cause = cause
        self.phase = phase
        self.sink = sink if sink is not None else _default_sink
        self.start = utc_now()
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.sequence = 0
        self.counts = dict(land_logical_dispatches=0, transport_logical_dispatches=0,
                           outbound_attempts_started=0, outbound_attempts_finished=0,
                           request_body_bytes_submitted=0, response_encoded_body_bytes=0,
                           response_decoded_body_bytes=0, bytes_partial_attempts=0,
                           bytes_unknown_attempts=0, trace_records_lost=0)
        self.groups = {"by_cause": {}, "by_origin": {}, "by_endpoint": {}}
        self.coverage = "complete"
        self.degraded = False
        self.attributed = cause != "unattributed"
        self.closed = False
        self.initial_health = _observer_health_snapshot()
        self.identity = dict(operation_key=operation_key, verification_attempt=verification_attempt,
                             verification_attempt_id=uuid.uuid4().hex if cause == "verification" else None)
        self.emit("run-start", cause=cause, phase=phase, started_at=self.start, **self.identity)

    def next_id(self, family):
        with self.lock:
            self.sequence += 1
            return f"{family}-{self.sequence}"

    def add(self, name, value=1):
        with self.lock:
            self.counts[name] += value

    def mark_coverage(self, coverage):
        with self.lock:
            if coverage == "unknown" or (coverage == "synthetic" and self.coverage == "complete"):
                self.coverage = coverage

    def emit(self, kind, **fields):
        record = dict(schema_version=SCHEMA_VERSION, process_id=os.getpid(), session_id=PROCESS_SESSION_ID,
                      run_id=self.run_id, parent_run_id=None, type=kind, **fields)
        try:
            accepted = self.sink is not None and self.sink.offer(record)
        except Exception:
            accepted = False
        if not accepted:
            self.add("trace_records_lost")

    def finish_attempt(self, record):
        with self.lock:
            self.counts["outbound_attempts_finished"] += 1
            for key in ("request_body_bytes_submitted", "response_encoded_body_bytes", "response_decoded_body_bytes"):
                self.counts[key] += record[key] or 0
            if record["response_body_completeness"] == "partial":
                self.counts["bytes_partial_attempts"] += 1
            elif record["response_body_completeness"] != "complete":
                self.counts["bytes_unknown_attempts"] += 1
            if record["request_body_completeness"] != "complete":
                self.counts["bytes_unknown_attempts"] += 1
            for group, key in (("by_cause", "cause"), ("by_origin", "origin"), ("by_endpoint", "endpoint")):
                bucket = record[key]
                values = self.groups[group]
                if bucket not in values and len(values) >= MAX_BUCKETS:
                    bucket = "overflow"
                    self.attributed = False
                values[bucket] = values.get(bucket, 0) + 1
        self.emit("attempt", **record)

    def snapshot(self):
        with self.lock:
            result = dict(self.counts)
            result.update({key: dict(value) for key, value in self.groups.items()})
            healthy = self.sink is not None and not getattr(self.sink, "failed", False)
            result.update(run_id=self.run_id, cause=self.cause, phase=self.phase, **self.identity,
                          transport_coverage=self.coverage,
                          count_complete=not self.degraded and self.coverage == "complete" and
                          self.counts["outbound_attempts_started"] == self.counts["outbound_attempts_finished"],
                          attribution_complete=self.attributed,
                          bytes_complete=self.coverage == "complete" and
                          self.counts["outbound_attempts_started"] == self.counts["outbound_attempts_finished"] and
                          not self.counts["bytes_partial_attempts"] and not self.counts["bytes_unknown_attempts"] and not self.degraded,
                          trace_complete=healthy and not self.counts["trace_records_lost"] and not self.degraded,
                          started_at=self.start, ended_at=utc_now(), elapsed_seconds=time.monotonic() - self.started,
                          wire_bytes=None, byte_basis="application body bytes; encoded and decoded are separate",
                          scope="exclusive synchronous invocation; phase 1", exclusions=EXCLUSIONS,
                          linked_child_runs=[], pending_children=[])
            if self.initial_health != _observer_health_snapshot():
                result.update(count_complete=False, bytes_complete=False, trace_complete=False)
            return result

    def finish(self):
        if not self.closed:
            self.closed = True
            result = self.snapshot()
            result.pop("run_id")
            self.emit("run-summary", **result)


def _safe(collector, method, *args, **kwargs):
    try:
        return getattr(collector, method)(*args, **kwargs)
    except Exception:
        if collector is not None:
            collector.degraded = True
        return None


@contextmanager
def run_scope(cause, phase="", sink=None, operation_key=None, verification_attempt=None):
    collector = None
    token = None
    admitted = _live_runs.acquire(blocking=False)
    try:
        if admitted:
            collector = RunCollector(cause, phase, sink, operation_key, verification_attempt)
            token = _context.set(Context(collector, collector.run_id, "span-0", cause, phase))
        else:
            _record_observer_health("live_run_limit_rejections")
            token = _context.set(None)
    except Exception:
        _record_observer_health("collector_failures")
        token = _context.set(None)
    try:
        yield collector
    finally:
        if token is not None:
            _context.reset(token)
        if collector is not None:
            _safe(collector, "finish")
        if admitted:
            _live_runs.release()


@contextmanager
def phase_scope(phase):
    context = active_context()
    token = None
    if context is not None:
        token = _context.set(replace(context, phase=phase, span_id=_safe(context.collector, "next_id", "span")))
    try:
        yield
    finally:
        if token is not None:
            _context.reset(token)


@contextmanager
def _ensure_scope(family):
    if active_context() is None:
        with run_scope("unattributed", phase=family):
            yield
    else:
        yield


@contextmanager
def land_dispatch():
    with _ensure_scope("land"):
        context = active_context()
        collector = context.collector if context else None
        identifier = _safe(collector, "next_id", "land")
        _safe(collector, "add", "land_logical_dispatches")
        token = _land.set(identifier)
        try:
            yield identifier
        finally:
            _land.reset(token)


@contextmanager
def transport_dispatch(url, method="GET", endpoint=None):
    with _ensure_scope("hive" if method == "POST" else "get"):
        context = active_context()
        collector = context.collector if context else None
        identifier = _safe(collector, "next_id", "dispatch")
        _safe(collector, "add", "transport_logical_dispatches")
        dispatch = dict(dispatch_id=identifier, retry_ordinal=1, redirect_hop=0, endpoint=endpoint)
        token = _dispatch.set(dispatch)
        error = None
        try:
            yield dispatch
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            _safe(collector, "emit", "transport-dispatch", dispatch_id=identifier,
                  land_dispatch_id=_land.get(), error_class=error)
            _dispatch.reset(token)


def mark_coverage(coverage):
    context = active_context()
    if context:
        _safe(context.collector, "mark_coverage", coverage)


class Attempt:
    def __init__(self, url, method="GET", request_bytes=0, coverage="complete"):
        self.context = active_context()
        self.collector = self.context.collector if self.context else None
        dispatch = _dispatch.get() or {}
        origin, service, endpoint = destination(url)
        rpc = dispatch.get("endpoint")
        if rpc:
            endpoint = rpc if rpc in {"condenser_api.get_transaction", "condenser_api.get_dynamic_global_properties"} else "rpc.other"
            service = "hive"
        if endpoint in {"other", "rpc.other"} and self.collector is not None:
            self.collector.attributed = False
        self.record = dict(span_id=self.context.span_id if self.context else None,
                           cause=self.context.cause if self.context else "unattributed",
                           phase=self.context.phase if self.context else "", dispatch_id=dispatch.get("dispatch_id"),
                           land_dispatch_id=_land.get(), attempt_id=_safe(self.collector, "next_id", "attempt"),
                           retry_ordinal=dispatch.get("retry_ordinal", 1), redirect_hop=dispatch.get("redirect_hop", 0),
                           started_at=utc_now(), origin=origin, service=service, method=method, endpoint=endpoint,
                           status=None, error_class=None, send_progress="unknown",
                           request_body_bytes_submitted=0 if method == "GET" else None,
                           request_body_completeness="complete" if method == "GET" else "unknown",
                           response_encoded_body_bytes=0, response_decoded_body_bytes=None,
                           response_body_completeness="unknown", wire_bytes=None, transport_coverage=coverage)
        self.request_bytes = request_bytes
        self.started = time.monotonic()
        self.closed = False
        _safe(self.collector, "mark_coverage", coverage)
        _safe(self.collector, "add", "outbound_attempts_started")

    def submitted(self):
        self.record.update(send_progress="submitted", request_body_bytes_submitted=self.request_bytes,
                           request_body_completeness="complete")

    def finish(self, error=None):
        if self.closed:
            return
        self.closed = True
        if error is not None:
            self.record["error_class"] = type(error).__name__
        self.record.update(ended_at=utc_now(), elapsed_seconds=time.monotonic() - self.started)
        self.record["outcome"] = "error" if self.record["error_class"] or (self.record["status"] or 0) >= 400 else "response"
        _safe(self.collector, "finish_attempt", self.record)


def new_attempt(url, method="GET", request_bytes=0, coverage="complete"):
    try:
        return Attempt(url, method, request_bytes, coverage)
    except Exception:
        _record_observer_health("collector_failures")
        context = active_context()
        if context:
            context.collector.degraded = True
        return _NullAttempt()


def notify_attempt(observer, method, *args):
    try:
        getattr(observer, method)(*args)
    except Exception:
        _record_observer_health("collector_failures")
        context = active_context()
        if context:
            context.collector.degraded = True


@contextmanager
def attempt(url, method="GET", request_bytes=0, coverage="complete"):
    observer = new_attempt(url, method, request_bytes, coverage)
    error = None
    try:
        yield observer
    except BaseException as exc:
        error = exc
        raise
    finally:
        notify_attempt(observer, "finish", error)


class _NullAttempt:
    def __init__(self):
        self.record = {}

    def submitted(self):
        pass

    def finish(self, error=None):
        pass


class CountingReader:
    def __init__(self, stream, observer, *, finish_on_close=False):
        self.stream = stream
        self.observer = observer
        self.finish_on_close = finish_on_close

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def read(self, amount=-1):
        try:
            value = self.stream.read(amount)
        except http.client.IncompleteRead as exc:
            self.observer.record["response_encoded_body_bytes"] = self.observer.record.get("response_encoded_body_bytes", 0) + len(exc.partial)
            self.observer.record["response_body_completeness"] = "partial"
            notify_attempt(self.observer, "finish", exc) if self.finish_on_close else None
            raise
        except BaseException as exc:
            self.observer.record["response_body_completeness"] = "partial" if self.observer.record.get("response_encoded_body_bytes") else "unknown"
            notify_attempt(self.observer, "finish", exc) if self.finish_on_close else None
            raise
        self.observer.record["response_encoded_body_bytes"] = self.observer.record.get("response_encoded_body_bytes", 0) + len(value)
        complete = amount is None or amount < 0 or (amount > 0 and len(value) < amount)
        remaining = getattr(self.stream, "length", None)
        if isinstance(remaining, int) and remaining > 0:
            complete = False
        declared = getattr(self.stream, "headers", {}).get("Content-Length")
        if declared is not None:
            try:
                if self.observer.record["response_encoded_body_bytes"] < int(declared):
                    complete = False
            except (TypeError, ValueError):
                complete = False
        self.observer.record["response_body_completeness"] = "complete" if complete else "partial"
        return value

    def close(self):
        try:
            return self.stream.close()
        finally:
            if self.finish_on_close:
                notify_attempt(self.observer, "finish")

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if error is not None and self.finish_on_close:
            self.observer.record["error_class"] = type(error).__name__
        self.close()


class _ObservedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return _observed_open(self, http.client.HTTPConnection, req)


class _ObservedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        # Python 3.12 dropped HTTPSHandler._check_hostname (the setting lives on the context since 3.4); reading it
        # made every HTTPS call through this opener fail on the production server (3.12) while working on 3.10.
        kwargs = {"context": self._context}
        if hasattr(self, "_check_hostname"):
            kwargs["check_hostname"] = self._check_hostname
        return _observed_open(self, http.client.HTTPSConnection, req, **kwargs)


class _ObservedHTTPErrorProcessor(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        try:
            redirect_error = getattr(request, "_mnemosyne_redirect_error", None)
            if redirect_error is not None and 300 <= response.status < 400:
                raise redirect_error("redirects are not permitted by Praetoria")
            return super().http_response(request, response)
        except BaseException as exc:
            if isinstance(response, CountingReader):
                response.observer.record["error_class"] = type(exc).__name__
                notify_attempt(response.observer, "finish", exc)
            raise

    https_response = http_response


def _observed_open(handler, connection_class, req, **kwargs):
    dispatch = _dispatch.get()
    observer = new_attempt(req.full_url, req.get_method(), len(req.data or b""))
    try:
        response = handler.do_open(connection_class, req, **kwargs)
        notify_attempt(observer, "submitted")
        observer.record["status"] = response.status
        if dispatch is not None:
            dispatch["redirect_hop"] += 1
        return CountingReader(response, observer, finish_on_close=True)
    except BaseException as exc:
        notify_attempt(observer, "finish", exc)
        raise


def observed_opener(*handlers):
    opener = urllib.request.build_opener(_ObservedHTTPHandler(), _ObservedHTTPSHandler(), _ObservedHTTPErrorProcessor(), *handlers)
    opener.mnemosyne_observation = "per_hop"
    return opener


class _InjectedTransport:
    def __init__(self, transport):
        self.transport = transport

    def __getattr__(self, name):
        return getattr(self.transport, name)

    def get_json(self, url):
        callback = self.transport.get_json if hasattr(self.transport, "get_json") else self.transport
        capability = getattr(self.transport, "mnemosyne_observation", "unknown")
        if capability == "per_hop":
            return callback(url)
        with transport_dispatch(url):
            mark_coverage("synthetic" if capability == "synthetic" else "complete" if capability == "single_attempt" else "unknown")
            if capability == "single_attempt":
                with attempt(url) as event:
                    result = callback(url)
                    notify_attempt(event, "submitted")
                    return result
            return callback(url)


def wrap_injected_transport(transport):
    return transport if getattr(transport, "mnemosyne_observation", None) == "per_hop" else _InjectedTransport(transport)


class JournalWriter:
    def __init__(self, root, queue_size=2048, version=None, segment_bytes=8 * 1024 * 1024, max_segments=4):
        self.root = Path(root)
        self.queue = queue.Queue(maxsize=max(1, queue_size))
        self.version = version
        self.segment_bytes = max(1024, segment_bytes)
        self.max_segments = max(1, max_segments)
        self.failed = False
        self.dropped = 0
        self.written = 0
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._write, name="Mnemosyne", daemon=True)

    def start(self):
        try:
            self.thread.start()
        except Exception:
            self.failed = True
        return self

    def offer(self, record):
        if self.failed or self.stopping.is_set():
            self.dropped += 1
            return False
        try:
            self.queue.put_nowait(record)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def health(self):
        return dict(failed=self.failed, dropped=self.dropped, queued=self.queue.qsize(), written=self.written,
                    **_observer_health_snapshot())

    def close(self):
        self.stopping.set()
        if self.thread.ident is not None:
            try:
                self.thread.join(timeout=2)
            except Exception:
                self.failed = True

    def _write(self):
        stream = None
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            segment = 0
            paths = []

            def open_segment():
                path = self.root / f"{os.getpid()}-{PROCESS_SESSION_ID}-{id(self):x}-{segment:04d}.jsonl"
                paths.append(path)
                while len(paths) > self.max_segments:
                    paths.pop(0).unlink(missing_ok=True)
                return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8")

            stream = open_segment()
            manifest = dict(type="session-start", schema_version=SCHEMA_VERSION, session_id=PROCESS_SESSION_ID,
                            process_id=os.getpid(), started_at=utc_now(), version=self.version,
                            revision=os.environ.get("CERES_REVISION", "unavailable"), exclusions=EXCLUSIONS,
                            scope="phase 1 synchronous verification/pre_sign; other roots unattributed",
                            wire_bytes=None, historical_physical_totals="unavailable")
            stream.write(json.dumps(manifest) + "\n")
            stream.flush()
            while not self.stopping.is_set() or not self.queue.empty():
                try:
                    record = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
                stream.flush()
                self.written += 1
                if stream.tell() >= self.segment_bytes:
                    stream.close()
                    segment += 1
                    stream = open_segment()
                    stream.write(json.dumps(dict(manifest, type="session-segment", retained_history_incomplete=segment >= self.max_segments)) + "\n")
            stream.write(json.dumps(dict(type="clean-session-end", schema_version=SCHEMA_VERSION,
                                         session_id=PROCESS_SESSION_ID, ended_at=utc_now(), **self.health())) + "\n")
        except Exception:
            self.failed = True
        finally:
            if stream is not None:
                stream.close()
