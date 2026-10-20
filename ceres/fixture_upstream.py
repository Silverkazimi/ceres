"""Fixture-backed upstream (): answer every outbound game/Hive request from recorded files.

Switched on only by ``CERES_FIXTURE_UPSTREAM=<set dir>``. When on, each outbound chokepoint gets a fake
connection from :func:`connection` instead of a real ``HTTPSConnection``: nothing opens a socket, and a
request with no recording answers status 599 (fail closed, never a network fallback). The page still
runs a real capture, so a fixture board obeys the live-capture-only rule; only the answers are recorded.

Refusals keep it off production: :func:`check_startup` refuses a non-loopback bind, a missing or unknown
manifest, and ``CERES_DEV_WATCH=0`` (the production marker) unless the CI-only override is set;
:func:`forwarded_refusal_middleware` answers 503 to any proxied request.

Set layout (see work/notes/-fixture-upstream-spec.md §3): ``fixture-manifest.json`` plus one JSON
entry per request anywhere below the set directory::

    {"request": {"method": "GET", "host": "vapi.splinterlands.com", "path": "/land/deeds",
                 "query": "player=sample01"},
     "status": 200, "headers": {"Content-Type": "application/json"}, "body": {...}}

JSON-RPC entries carry ``"rpc": {"method": ..., "params": ...}`` in ``request`` instead of a query. The
entry's file name is cosmetic; the key is computed from ``request``. Makes no outbound call itself.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import logging
import os
import re
import tempfile
import fnmatch
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ENV = "CERES_FIXTURE_UPSTREAM"
ALLOW_PRODUCTION_FLAGS_ENV = "CERES_FIXTURE_UPSTREAM_ALLOW_PRODUCTION_FLAGS"
OVERLAYS_ENV = "CERES_FIXTURE_OVERLAYS"  # comma-separated overlay names ( slice 3)
CACHE_ENV = "PRAETORIA_CACHE_DIR"  # praetoria/cache.py: JsonCache default root
MANIFEST = "fixture-manifest.json"
FORMAT = 1
MISSING_STATUS = 599
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
FORWARDED_HEADERS = ("x-forwarded-for", "x-forwarded-host", "forwarded")

_LOG = logging.getLogger("ceres.fixture_upstream")
_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|\+00:00)$")


class FixtureRefusal(RuntimeError):
    """Fixture mode was requested where it must not run."""


def request_key(method: str, host: str, path: str, query: str = "", rpc: dict | None = None) -> str:
    method = method.upper()
    if rpc is not None:
        canon = json.dumps([rpc.get("method"), rpc.get("params")], sort_keys=True, separators=(",", ":"))
    else:
        canon = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(query, keep_blank_values=True)))
    raw = "\x1f".join((method, host.lower(), path or "/", canon))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _shift_iso(value: str, delta: timedelta) -> str:
    match = _ISO.match(value)
    if not match:
        return value
    stamp = datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc) + delta
    fraction = match.group(2) or ""
    return stamp.strftime("%Y-%m-%dT%H:%M:%S") + fraction + match.group(3)


def _shift(value: Any, delta: timedelta, epoch_ms_keys: frozenset[str], key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: _shift(v, delta, epoch_ms_keys, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_shift(v, delta, epoch_ms_keys, key) for v in value]
    if isinstance(value, str):
        return _shift_iso(value, delta)
    if key in epoch_ms_keys and isinstance(value, int) and not isinstance(value, bool):
        return value + int(delta.total_seconds() * 1000)
    return value


_PATH_TOKEN = re.compile(r"\.([A-Za-z0-9_]+)|\[(\d+)\]|\[(\*)\]|\[\?\(@\.([A-Za-z0-9_]+)\s*==\s*'([^']*)'\)\]")


def _select(document: Any, path: str) -> list[tuple[Any, Any]]:
    """Resolve a small JSONPath subset ($ . key [n] [*] [?(@.k=='v')]) to (container, key) slots."""
    if not path.startswith("$"):
        raise FixtureRefusal(f"overlay path must start with $: {path}")
    slots: list[tuple[Any, Any]] = [(None, None)]
    values = [document]
    rest = path[1:]
    while rest:
        match = _PATH_TOKEN.match(rest)
        if not match:
            raise FixtureRefusal(f"unsupported overlay path syntax at {rest!r}")
        rest = rest[match.end():]
        key, index, star, field, wanted = match.groups()
        next_slots, next_values = [], []
        for value in values:
            if key is not None and isinstance(value, dict) and key in value:
                next_slots.append((value, key)); next_values.append(value[key])
            elif index is not None and isinstance(value, list) and int(index) < len(value):
                next_slots.append((value, int(index))); next_values.append(value[int(index)])
            elif (star or field) and isinstance(value, list):
                for position, item in enumerate(value):
                    if star or (isinstance(item, dict) and str(item.get(field)) == wanted):
                        next_slots.append((value, position)); next_values.append(item)
        slots, values = next_slots, next_values
    return slots


def _resolve_tokens(value: Any, now: datetime) -> Any:
    """`{"$now_offset_seconds": n}` becomes an ISO-8601 UTC time n seconds from now."""
    if isinstance(value, dict):
        if set(value) == {"$now_offset_seconds"}:
            stamp = now + timedelta(seconds=float(value["$now_offset_seconds"]))
            return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"
        return {k: _resolve_tokens(v, now) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_tokens(v, now) for v in value]
    return value


def _matches(request: dict[str, Any], match: dict[str, Any]) -> bool:
    for field in ("method", "host"):
        if field in match and str(request.get(field, "")).upper() != str(match[field]).upper():
            return False
    if "path" in match and not fnmatch.fnmatchcase(str(request.get("path", "")), str(match["path"])):
        return False
    if "query_contains" in match and str(match["query_contains"]) not in str(request.get("query", "")):
        return False
    return True


class FixtureSet:
    """A loaded, read-only fixture set. Bodies are time-shifted once, at load; overlays apply after."""

    def __init__(self, root: Path, *, now: datetime | None = None, overlays: list[str] | None = None):
        self.root = Path(root)
        manifest_path = self.root / MANIFEST
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FixtureRefusal(f"fixture manifest unreadable: {manifest_path}") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise FixtureRefusal(f"fixture manifest format must be {FORMAT}: {manifest_path}")
        self.name = str(manifest.get("set") or self.root.name)
        delta = timedelta(0)
        if manifest.get("time_shift"):
            recorded = datetime.fromisoformat(str(manifest["recorded_at"]).replace("Z", "+00:00"))
            delta = timedelta(seconds=int(((now or datetime.now(timezone.utc)) - recorded).total_seconds()))
        epoch_keys = frozenset(str(k) for k in manifest.get("epoch_ms_keys") or ())
        self.entries: dict[str, dict[str, Any]] = {}
        for path in sorted(self.root.rglob("*.json")):
            if path.name == MANIFEST or path.parent.name == "overlays":
                continue
            entry = json.loads(path.read_text(encoding="utf-8"))
            request = entry.get("request") if isinstance(entry, dict) else None
            if not isinstance(request, dict):
                continue
            key = request_key(request.get("method", "GET"), request.get("host", ""), request.get("path", "/"),
                              request.get("query", ""), request.get("rpc"))
            if key in self.entries:
                raise FixtureRefusal(f"duplicate fixture request in {path}")
            body = _shift(entry.get("body"), delta, epoch_keys) if delta else entry.get("body")
            self.entries[key] = {"status": int(entry.get("status", 200)), "headers": dict(entry.get("headers") or {}),
                                 "body": body, "request": request}
        if overlays is None:
            env = os.environ.get(OVERLAYS_ENV, "").strip()
            overlays = [n.strip() for n in env.split(",") if n.strip()] if env else list(manifest.get("overlays") or [])
        self.overlays = list(overlays)
        for name in self.overlays:
            self._apply_overlay(name, now or datetime.now(timezone.utc))
        self.missing: list[str] = []
        self._missing_lock = threading.Lock()

    def _apply_overlay(self, name: str, now: datetime) -> None:
        """An overlay (`<set>/overlays/<name>.json`) adds entries and edits recorded bodies, in order. An edit that
        matches no entry or selects nothing refuses the whole set: a stale overlay must fail loudly."""
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise FixtureRefusal(f"invalid overlay name {name!r}")
        path = self.root / "overlays" / f"{name}.json"
        try:
            overlay = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FixtureRefusal(f"overlay unreadable: {path}") from exc
        for added in overlay.get("add") or []:
            request = added["request"]
            key = request_key(request.get("method", "GET"), request.get("host", ""), request.get("path", "/"),
                              request.get("query", ""), request.get("rpc"))
            if key in self.entries and not added.get("replace"):
                raise FixtureRefusal(f"overlay {name} adds a request the set already has; set \"replace\": true")
            self.entries[key] = {"status": int(added.get("status", 200)), "headers": dict(added.get("headers") or {}),
                                 "body": _resolve_tokens(added.get("body"), now), "request": request}
        for number, edit in enumerate(overlay.get("edits") or [], start=1):
            targets = [e for e in self.entries.values() if _matches(e["request"], edit.get("match") or {})]
            slots = [slot for e in targets for slot in _select(e["body"], str(edit.get("select", "$")))]
            if not slots:
                raise FixtureRefusal(f"overlay {name} edit {number} selects nothing")
            for container, slot in slots:
                if container is None:
                    raise FixtureRefusal(f"overlay {name} edit {number}: select a node below $, not the body itself")
                if "set" in edit:
                    current = container[slot]
                    if not isinstance(current, dict):
                        raise FixtureRefusal(f"overlay {name} edit {number}: `set` needs an object")
                    current.update(_resolve_tokens(edit["set"], now))
                elif "replace" in edit:
                    container[slot] = _resolve_tokens(edit["replace"], now)
                elif "append" in edit:
                    if not isinstance(container[slot], list):
                        raise FixtureRefusal(f"overlay {name} edit {number}: `append` needs a list")
                    container[slot].append(_resolve_tokens(edit["append"], now))
                else:
                    raise FixtureRefusal(f"overlay {name} edit {number} has no set/replace/append")

    def lookup(self, method: str, host: str, target: str, body: bytes | None) -> tuple[int, dict[str, str], bytes]:
        parsed = urllib.parse.urlsplit(target)
        rpc = None
        if method.upper() == "POST" and body:
            try:
                payload = json.loads(body)
                rpc = {"method": payload.get("method"), "params": payload.get("params")}
            except (ValueError, AttributeError):
                rpc = None
        key = request_key(method, host, parsed.path or "/", parsed.query, rpc)
        entry = self.entries.get(key)
        if entry is None:
            label = f"{method.upper()} {host}{target}" + (f" rpc={rpc['method']}" if rpc else "")
            with self._missing_lock:
                if label not in self.missing:
                    self.missing.append(label)
                    _LOG.warning("[FIXTURE] fixture missing: %s (key %s)", label, key)
            return MISSING_STATUS, {"Content-Type": "application/json"}, json.dumps({"fixture_missing": label}).encode()
        raw = entry["body"]
        data = raw.encode("utf-8") if isinstance(raw, str) else json.dumps(raw, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", **entry["headers"]}
        headers["Content-Length"] = str(len(data))
        headers.pop("Content-Encoding", None)
        return entry["status"], headers, data


class FixtureResponse(io.BytesIO):
    """Enough of ``http.client.HTTPResponse`` for every chokepoint: status, reason, headers, read."""

    def __init__(self, status: int, headers: dict[str, str], data: bytes):
        super().__init__(data)
        self.status = status
        self.code = status
        self.reason = "OK" if status == 200 else "Fixture"
        self.headers = http.client.HTTPMessage()
        for name, value in headers.items():
            self.headers[name] = value
        self.msg = self.headers

    def getheader(self, name: str, default: Any = None) -> Any:
        return self.headers.get(name, default)

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self.headers.items())

    def getcode(self) -> int:
        return self.status


class FixtureConnection:
    """Drop-in for ``http.client.HTTPSConnection`` that never opens a socket."""

    mnemosyne_observation = "single_attempt"

    def __init__(self, host: str, fixtures: FixtureSet):
        self.host = host
        self._fixtures = fixtures
        self._pending: tuple[str, str, bytes | None] | None = None

    def request(self, method: str, url: str, body: Any = None, headers: Any = None, **_: Any) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self._pending = (method, url, data)

    def getresponse(self) -> FixtureResponse:
        if self._pending is None:
            raise http.client.ResponseNotReady("no request sent")
        method, url, data = self._pending
        self._pending = None
        return FixtureResponse(*self._fixtures.lookup(method, self.host, url, data))

    def close(self) -> None:
        self._pending = None


_active: FixtureSet | None = None
_saved_cache_env: tuple[bool, str | None] | None = None
_install_lock = threading.Lock()


def active() -> FixtureSet | None:
    return _active


def enabled() -> bool:
    return _active is not None


def connection(host: str, *, timeout: float | None = None, port: int | None = None) -> Any:
    """A fixture connection in fixture mode, otherwise a real ``HTTPSConnection``."""
    fixtures = _active
    if fixtures is not None:
        return FixtureConnection(host, fixtures)
    if port is None:
        return http.client.HTTPSConnection(host, timeout=timeout)
    return http.client.HTTPSConnection(host, port, timeout=timeout)


def fixture_urlopen(request: Any, timeout: float | None = None) -> FixtureResponse:
    """urllib-style open for ``fides.verify``'s RPC in fixture mode."""
    fixtures = _active
    if fixtures is None:
        raise RuntimeError("fixture mode is not active")
    parsed = urllib.parse.urlsplit(request.full_url)
    target = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    return FixtureResponse(*fixtures.lookup(request.get_method(), parsed.hostname or "", target, request.data))


def configured_dir() -> Path | None:
    value = os.environ.get(ENV, "").strip()
    return Path(value) if value else None


def check_startup(host: str, set_dir: Path | None = None) -> Path | None:
    """Refuse fixture mode where it must not run; return the set dir when it may."""
    set_dir = set_dir if set_dir is not None else configured_dir()
    if set_dir is None:
        return None
    if host not in LOOPBACK_HOSTS:
        raise FixtureRefusal(f"{ENV} is set but the bind host {host!r} is not loopback; refusing to start")
    if os.environ.get("CERES_DEV_WATCH", "").strip() == "0" and os.environ.get(ALLOW_PRODUCTION_FLAGS_ENV) != "1":
        raise FixtureRefusal(f"{ENV} is set on a production-configured process (CERES_DEV_WATCH=0); refusing to start")
    if not (Path(set_dir) / MANIFEST).is_file():
        raise FixtureRefusal(f"{ENV} points at {set_dir}, which has no {MANIFEST}; refusing to start")
    return Path(set_dir)


def install(set_dir: Path) -> FixtureSet:
    """Load the set and route every chokepoint through it, in this process and in capture children."""
    global _active
    fixtures = FixtureSet(Path(set_dir))
    with _install_lock:
        _active = fixtures
        os.environ[ENV] = str(Path(set_dir))  # capture worker children inherit the switch
        isolate_cache()
        os.environ["CERES_CARD_ART_CACHE"] = "0"  # the card-art cache would fetch from the CDN
        from praetoria import transport as praetoria_transport
        from fides import verify as fides_verify

        praetoria_transport.FIXTURE_CONNECTION_FACTORY = lambda host, port, timeout: FixtureConnection(host, fixtures)
        fides_verify.FIXTURE_URLOPEN = fixture_urlopen
    _LOG.warning("[FIXTURE] upstream answered from fixture set %r (%s); no network calls", fixtures.name, set_dir)
    return fixtures


def uninstall() -> None:
    """Tests only: restore real connections."""
    global _active
    with _install_lock:
        _active = None
        os.environ.pop(ENV, None)
        restore_cache()
        from praetoria import transport as praetoria_transport
        from fides import verify as fides_verify

        praetoria_transport.FIXTURE_CONNECTION_FACTORY = None
        fides_verify.FIXTURE_URLOPEN = None


def isolate_cache() -> None:
    """Point praetoria's JsonCache at a private temp dir: a fixture run must neither read the real
    ~/.praetoria cache (catalogues are cached for a day) nor write fixture accounts into it."""
    global _saved_cache_env
    if _saved_cache_env is None:
        _saved_cache_env = (CACHE_ENV in os.environ, os.environ.get(CACHE_ENV))
        if "ceres-fixture-cache-" not in os.environ.get(CACHE_ENV, ""):  # a capture child shares its parent's
            os.environ[CACHE_ENV] = tempfile.mkdtemp(prefix="ceres-fixture-cache-")


def restore_cache() -> None:
    global _saved_cache_env
    if _saved_cache_env is not None:
        had, value = _saved_cache_env
        if had and value is not None:
            os.environ[CACHE_ENV] = value
        else:
            os.environ.pop(CACHE_ENV, None)
        _saved_cache_env = None


def install_from_env() -> FixtureSet | None:
    """For the capture worker child: install when the parent ran in fixture mode."""
    set_dir = configured_dir()
    return install(set_dir) if set_dir is not None else None


async def forwarded_refusal_middleware(request, call_next):
    if _active is not None and any(request.headers.get(name) for name in FORWARDED_HEADERS):
        from fastapi.responses import JSONResponse

        _LOG.warning("[FIXTURE] refused a proxied request to %s", request.url.path)
        return JSONResponse(status_code=503, content={"detail": "fixture mode refuses proxied requests"})
    return await call_next(request)
