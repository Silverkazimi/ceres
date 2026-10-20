"""Shared fixed-origin, read-only JSON transport."""

from __future__ import annotations

import gzip
import http.client
import io
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mnemosyne.accounting import CountingReader, attempt as observed_attempt, mark_coverage, notify_attempt, transport_dispatch

from . import fastjson
from .errors import SchemaError, TransportError, UpstreamError

API_ORIGIN = "https://api.splinterlands.com"
LAND_ORIGIN = "https://vapi.splinterlands.com"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
LARGE_RESPONSE_BYTES = 256 * 1024 * 1024
_RESPONSE_CHUNK_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_RETRIES = 3
RETRYABLE_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})
# set by `ceres.fixture_upstream.install` only (fixture mode, loopback, never production).
# `factory(host, port, timeout)` returns a socket-free connection that answers from recorded files.
FIXTURE_CONNECTION_FACTORY: Any = None


@dataclass(frozen=True)
class ResponseData:
    data: Any
    retrieved_at: str
    attempts: int
    # the response's ETag (weak `W/"..."` kept verbatim) so a caller can tell an
    # unchanged body from a changed one; `reused` is set by a caller that handed back its
    # previous parse because the ETag matched. The fetch itself still happened ().
    etag: str | None = None
    reused: bool = False


class _BodyStream:
    """: the decoded body as a `read(size)` stream for a streaming parser -- same byte
    limit and partial-body accounting as `_read_limited`, plus whether it was read to the end."""

    def __init__(self, transport: Any, reader: Any, response: Any, url: str, trace_id: str, limit: int):
        self._transport, self._reader, self._response = transport, reader, response
        self._url, self._trace_id, self._limit = url, trace_id, limit
        self.total = 0
        self.drained = False

    def read(self, size: int = _RESPONSE_CHUNK_BYTES) -> bytes:
        if self.drained:
            return b""
        chunk = self._reader.read(min(max(1, size), self._limit - self.total + 1))
        if not chunk:
            self.drained = True
            return b""
        self.total += len(chunk)
        if self.total > self._limit:
            self._transport._mark_partial(self._response)
            raise self._transport._too_large(self._url, self._trace_id, self._limit, self.total)
        return chunk


class ResponseTooLarge(TransportError):
    code = "PRAETORIA_E_RESPONSE_TOO_LARGE"
    exit_code = 8

    def __init__(self, endpoint: str, trace_id: str, limit_bytes: int, observed_bytes: int):
        self.endpoint = endpoint
        self.trace_id = trace_id
        self.limit_bytes = limit_bytes
        self.observed_bytes = observed_bytes
        self.result = {
            "status": "response_too_large",
            "endpoint": endpoint,
            "trace_id": trace_id,
            "freshness": {
                "status": "unavailable",
                "retrieved_at": None,
                "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
            "limit_bytes": limit_bytes,
            "observed_bytes": observed_bytes,
        }
        super().__init__(
            f"response_too_large: {endpoint} exceeded {limit_bytes} decoded bytes "
            f"(trace {trace_id})"
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UpstreamError("redirects are not permitted by Praetoria")


def validate_fixed_url(url: str, origins: tuple[str, ...] = (API_ORIGIN, LAND_ORIGIN)) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname is None:
        raise UpstreamError("URL is outside Praetoria's fixed API allowlist")
    allowed = {urllib.parse.urlsplit(origin).hostname for origin in origins}
    if parsed.hostname not in allowed or parsed.port not in (None, 443):
        raise UpstreamError("URL is outside Praetoria's fixed API allowlist")


class FixedGetTransport:
    mnemosyne_observation = "per_hop"

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        max_response_bytes: int | None = None,
        user_agent: str = "Praetoria/0.1",
        opener: Any | None = None,
    ):
        self.timeout = timeout
        self.retries = max(1, min(int(retries), 5))
        self.max_response_bytes = MAX_RESPONSE_BYTES if max_response_bytes is None else int(max_response_bytes)
        self._custom_response_limit = max_response_bytes is not None
        self.user_agent = user_agent
        self.opener = opener
        # (2026-09-07): connections and their locks are PER THREAD, not per origin.
        # One shared connection per origin behind one lock serialised every concurrent
        # request to the same host across the whole round trip -- measured on a 6,303-deed
        # account: `players/item_details` waited 26.8 s on the `api.splinterlands.com` lock
        # behind the 45 MB collection sweep, and a full capture accumulated 72 s of lock wait
        # across 129 `/assets` calls. A thread never contends with itself, so a per-thread
        # connection keeps keep-alive reuse (2.5x, measured) without the queue. Pools are
        # bounded (8 workers), so at most 8 connections per origin exist at once, and a
        # thread's connections die with the thread (`threading.local`), nothing leaks.
        self._local = threading.local()
        self._free_connections: dict[tuple[str, int], list[tuple[http.client.HTTPSConnection, float]]] = {}
        self._connections_guard = threading.Lock()

    # a caller may hand in `body_parser(headers, stream) -> payload` and receive the
    # decoded body as a stream (one `read(size)` at a time, gzip already inflated, the endpoint's
    # byte limit still enforced) instead of one bytes object + one `loads`. `headers` is the
    # response's header mapping, read before a single body byte. A parser that returns without
    # draining the stream is allowed; the connection is then discarded, never reused.
    supports_body_parser = True

    def get_json(self, url: str, body_parser: Any = None) -> ResponseData:
        validate_fixed_url(url)
        with transport_dispatch(url) as dispatch:
            return self._dispatch_json(url, dispatch, body_parser)

    def _dispatch_json(self, url: str, dispatch: dict, body_parser: Any = None) -> ResponseData:
        origin = self._origin_key(url)
        trace_id = uuid.uuid4().hex
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            dispatch["retry_ordinal"] = attempt
            dispatch["redirect_hop"] = 0
            try:
                with self._connection_lock(origin):
                    if self.opener is not None:
                        body, etag = self._get_with_opener(url, trace_id, body_parser)
                    else:
                        body, etag = self._get_with_connection(url, origin, trace_id, body_parser)
                if body_parser is None:
                    payload = fastjson.loads(body)  # 45 MB collection, 10 s -> ~1 s
                else:
                    payload = body  # already parsed by the caller's streaming parser
                self.check_envelope(payload)
                return ResponseData(payload, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), attempt, etag)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in RETRYABLE_HTTP_STATUS:
                    raise UpstreamError(f"HTTP {exc.code} from Splinterlands API") from exc
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as exc:
                last_error = exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise SchemaError("Splinterlands API returned invalid JSON") from exc
            except (SchemaError, UpstreamError):
                raise
            if attempt < self.retries:
                time.sleep(min(2 ** (attempt - 1), 8))
        raise TransportError(f"Splinterlands API request failed after {self.retries} attempts: {last_error}")

    @staticmethod
    def _origin_key(url: str) -> tuple[str, int]:
        parsed = urllib.parse.urlsplit(url)
        return parsed.hostname or "", parsed.port or 443

    def _thread_state(self) -> tuple[dict, dict[tuple[str, int], threading.Lock]]:
        local = self._local
        if not hasattr(local, "locks"):
            local.locks = {}
        return {}, local.locks

    def _connection_lock(self, origin: tuple[str, int]) -> threading.Lock:
        # Per thread: guards only against re-entrant use on the same thread. Cross-thread
        # requests never queue here; the connection itself comes from the shared free-list.
        _, locks = self._thread_state()
        return locks.setdefault(origin, threading.Lock())

    # (2026-09-07), v0.9.27: a small FREE-LIST of warm connections per origin, shared
    # by every thread. v0.9.22 gave each pool thread its own connection, which removed the
    # per-origin queue but paid a TLS handshake per thread per origin -- 0.5-0.6 s per tail
    # call on player0005 where a reused connection answers in 0.13 s. A connection is checked out
    # for exactly one round trip (the guard is held for the pop/push only, never across the
    # request), returned when the body was read completely, and closed -- not returned -- on
    # any failure, so a half-read socket can never be reused ().
    _FREE_LIST_MAX = 8
    # the game closes an idle keep-alive connection on its side; reusing one failed a read in
    # 3 ms ("Tax read unavailable", "region inventory unavailable (TransportError)"), and the retry popped the
    # next dead connection off the same list. A connection idle longer than this is closed, never reused.
    _IDLE_MAX_SECONDS = 20.0

    def _connection_for(self, origin: tuple[str, int]) -> http.client.HTTPSConnection:
        if FIXTURE_CONNECTION_FACTORY is not None:  # never pooled, never a socket
            return FIXTURE_CONNECTION_FACTORY(origin[0], origin[1], self.timeout)
        stale: list[http.client.HTTPSConnection] = []
        chosen = None
        with self._connections_guard:
            free = self._free_connections.setdefault(origin, [])
            now = time.monotonic()
            while free:
                connection, idle_since = free.pop()
                if now - idle_since <= self._IDLE_MAX_SECONDS:
                    chosen = connection
                    break
                stale.append(connection)
        for connection in stale:
            connection.close()
        return chosen or http.client.HTTPSConnection(origin[0], origin[1], timeout=self.timeout)

    def _release_connection(self, origin: tuple[str, int], connection: http.client.HTTPSConnection) -> None:
        with self._connections_guard:
            free = self._free_connections.setdefault(origin, [])
            if len(free) < self._FREE_LIST_MAX:
                free.append((connection, time.monotonic()))
                return
        connection.close()

    def _discard_connection(self, origin: tuple[str, int], connection: http.client.HTTPSConnection) -> None:
        connection.close()
        # A failed connection means its idle siblings are likely dead too: the retry must open a fresh one.
        with self._connections_guard:
            free = self._free_connections.pop(origin, [])
        for other, _idle_since in free:
            other.close()

    @staticmethod
    def _etag(response: Any) -> str | None:
        headers = getattr(response, "headers", None)
        try:
            value = headers.get("ETag") if headers is not None else None
        except Exception:
            return None
        return str(value).strip() if value else None

    def _get_with_connection(self, url: str, origin: tuple[str, int], trace_id: str, body_parser: Any = None) -> tuple[Any, str | None]:
        parsed = urllib.parse.urlsplit(url)
        target = parsed.path or "/"
        if parsed.query:
            target += f"?{parsed.query}"
        connection = self._connection_for(origin)
        capability = getattr(connection, "mnemosyne_observation", None)
        coverage = "complete" if type(connection) is http.client.HTTPSConnection or capability == "single_attempt" else "unknown"
        response = None
        completed = False
        with observed_attempt(url, coverage=coverage) as event:
            try:
                connection.request(
                    "GET",
                    target,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "User-Agent": self.user_agent,
                    },
                )
                notify_attempt(event, "submitted")
                response = connection.getresponse()
                event.record["status"] = response.status
                if 300 <= response.status < 400:
                    raise UpstreamError("redirects are not permitted by Praetoria")
                if response.status >= 400:
                    raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, None)
                etag = self._etag(response)
                if body_parser is None:
                    body = self._read_response_body(CountingReader(response, event), url, trace_id)
                    completed = True
                else:
                    body, completed = self._stream_response_body(CountingReader(response, event), url, trace_id, body_parser)
                return body, etag
            except Exception:
                self._discard_connection(origin, connection)
                raise
            finally:
                if response is not None:
                    response.close()
                if completed:
                    self._release_connection(origin, connection)
                elif response is not None:
                    self._discard_connection(origin, connection)  # body left unread: keep-alive stream is out of step

    def _get_with_opener(self, url: str, trace_id: str, body_parser: Any = None) -> tuple[Any, str | None]:
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "User-Agent": self.user_agent,
            },
        )
        capability = getattr(self.opener, "mnemosyne_observation", "unknown")
        request._mnemosyne_redirect_error = UpstreamError
        if capability == "single_attempt":
            with observed_attempt(url) as event:
                try:
                    with self.opener.open(request, timeout=self.timeout) as response:
                        notify_attempt(event, "submitted")
                        event.record["status"] = getattr(response, "status", None)
                        return self._consume_opener_response(CountingReader(response, event), url, trace_id, body_parser)
                except urllib.error.HTTPError as exc:
                    event.record["status"] = exc.code
                    raise
        if capability != "per_hop":
            mark_coverage("synthetic" if capability == "synthetic" else "unknown")
        with self.opener.open(request, timeout=self.timeout) as response:
            return self._consume_opener_response(response, url, trace_id, body_parser)

    def _consume_opener_response(self, response, url: str, trace_id: str, body_parser: Any = None) -> tuple[Any, str | None]:
        getcode = getattr(response, "getcode", None)
        status = getcode() if callable(getcode) else getattr(response, "status", None)
        if status is not None and 300 <= status < 400:
            raise UpstreamError("redirects are not permitted by Praetoria")
        etag = self._etag(response)
        if body_parser is None:
            return self._read_response_body(response, url, trace_id), etag
        payload, _drained = self._stream_response_body(response, url, trace_id, body_parser)
        return payload, etag

    def _response_limit(self, url: str) -> int:
        if self._custom_response_limit:
            return self.max_response_bytes
        path = urllib.parse.urlsplit(url).path
        if path.startswith("/cards/collection/") or path == "/cards/collection":
            return LARGE_RESPONSE_BYTES
        if path == "/land/deeds":
            return LARGE_RESPONSE_BYTES
        return self.max_response_bytes

    @staticmethod
    def _endpoint(url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        endpoint = parsed.path or "/"
        if parsed.query:
            endpoint += f"?{parsed.query}"
        return endpoint

    def _too_large(self, url: str, trace_id: str, limit: int, observed: int) -> ResponseTooLarge:
        return ResponseTooLarge(self._endpoint(url), trace_id, limit, observed)

    @staticmethod
    def _mark_partial(response: Any) -> None:
        if isinstance(response, CountingReader):
            response.observer.record["response_body_completeness"] = "partial"

    def _read_response_body(self, response: Any, url: str, trace_id: str) -> bytes:
        limit = self._response_limit(url)
        content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
        length = response.headers.get("Content-Length")
        if length:
            try:
                declared_length = int(length)
            except ValueError as exc:
                raise SchemaError("Splinterlands API returned an invalid Content-Length") from exc
            if content_encoding != "gzip" and declared_length > limit:
                self._mark_partial(response)
                raise self._too_large(url, trace_id, limit, declared_length)
        if content_encoding == "gzip":
            try:
                with gzip.GzipFile(fileobj=response) as compressed:
                    body = self._read_limited(compressed, response, url, trace_id, limit)
            except (EOFError, OSError) as exc:
                self._mark_partial(response)
                raise SchemaError("Splinterlands API returned invalid gzip data") from exc
        elif content_encoding:
            raise SchemaError(f"Splinterlands API returned unsupported content encoding: {content_encoding}")
        else:
            body = self._read_limited(response, response, url, trace_id, limit)
        if isinstance(response, CountingReader):
            response.observer.record["response_decoded_body_bytes"] = len(body)
        return body

    def _stream_response_body(self, response: Any, url: str, trace_id: str, body_parser: Any) -> tuple[Any, bool]:
        """: hand the decoded body to `body_parser` as a stream. Returns (payload, drained)."""
        limit = self._response_limit(url)
        content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
        if content_encoding == "gzip":
            reader = gzip.GzipFile(fileobj=response)
        elif content_encoding:
            raise SchemaError(f"Splinterlands API returned unsupported content encoding: {content_encoding}")
        else:
            reader = response
        stream = _BodyStream(self, reader, response, url, trace_id, limit)
        try:
            payload = body_parser(response.headers, stream)
        except (EOFError, OSError) as exc:
            self._mark_partial(response)
            raise SchemaError("Splinterlands API returned invalid gzip data") from exc
        if isinstance(response, CountingReader):
            response.observer.record["response_decoded_body_bytes"] = stream.total
        return payload, stream.drained

    def _read_limited(self, reader: Any, response: Any, url: str, trace_id: str, limit: int) -> bytes:
        collected = io.BytesIO()
        total = 0
        while True:
            chunk = reader.read(min(_RESPONSE_CHUNK_BYTES, limit - total + 1))
            if not chunk:
                return collected.getvalue()
            next_total = total + len(chunk)
            if next_total > limit:
                self._mark_partial(response)
                raise self._too_large(url, trace_id, limit, next_total)
            collected.write(chunk)
            total = next_total

    @staticmethod
    def check_envelope(payload: Any) -> None:
        if not isinstance(payload, (dict, list)):
            raise SchemaError("Splinterlands API returned an unsupported top-level JSON type")
        lookup_refusal = (
            isinstance(payload, dict)
            and isinstance(payload.get("trx_info"), dict)
            and payload["trx_info"].get("success") is False
        )
        if isinstance(payload, dict) and payload.get("error") and not lookup_refusal:
            raise UpstreamError(f"Splinterlands API body error: {payload.get('error') or payload.get('message') or payload.get('status')}")
        if (
            isinstance(payload, dict)
            and (payload.get("success") is False or payload.get("status") in {"fail", "error"})
            and not lookup_refusal
        ):
            raise UpstreamError(f"Splinterlands API body error: {payload.get('error') or payload.get('message') or payload.get('status')}")
