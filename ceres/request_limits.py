""" S1 / L1-04: one application-wide request-body limit and a controlled answer to absurdly nested JSON.

The limit counts the bytes actually received (also for chunked bodies with no Content-Length), so a client cannot stream an unbounded
body into the JSON parser. Zero outbound calls.
"""
from __future__ import annotations

import json
import os

from fastapi.responses import JSONResponse

MAX_BODY_BYTES = 4 * 1024 * 1024  # the largest legitimate body (a 5,000-rule routing preview) is well under 2 MB
_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class BodyLimitMiddleware:
    """Pure ASGI middleware: answers 413 as soon as the declared or received body is over the limit."""

    def __init__(self, app, limit: int = MAX_BODY_BYTES):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in _BODY_METHODS:
            return await self.app(scope, receive, send)
        declared = next((value for name, value in scope.get("headers", []) if name == b"content-length"), None)
        try:
            too_big = declared is not None and int(declared) > self.limit
        except ValueError:
            too_big = True
        if too_big:
            return await self._refuse(scope, receive, send)
        received = 0
        refused = False

        async def counted():
            nonlocal received, refused
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    refused = True
                    return {"type": "http.disconnect"}  # S10 / L1-02: the handler sees a broken stream, never a truncated "complete" body
            return message

        started = False

        async def guarded_send(message):
            nonlocal started
            if refused and not started:
                started = True
                return await self._refuse(scope, receive, send)
            if refused:
                return None
            started = started or message["type"] == "http.response.start"
            return await send(message)

        try:
            return await self.app(scope, counted, guarded_send)
        except Exception:
            if refused and not started:  # the app failed on the emptied body: the real answer is "too large"
                return await self._refuse(scope, receive, send)
            raise

    async def _refuse(self, scope, receive, send):
        body = json.dumps({"detail": "Request body is too large."}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


async def recursion_handler(request, exc):
    """A deeply nested JSON body is a client error, not a server crash."""
    return JSONResponse({"detail": "Request body is nested too deeply."}, status_code=422)


IP_LITERALS = "__ip_literals__"  # marker inside the allowed set: any IP address is a served Host
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "testserver"})  # "testserver" is the in-process test client's Host (a one-label name no public DNS serves)


def allowed_hosts(bind_host: str) -> frozenset[str] | None:
    """ S10 / L1-04: the Host names the server answers to, or None for "do not check".

    Bound to the loopback interface (the default), only loopback names are served, so a web page that rebinds its own domain to 127.0.0.1 cannot
    talk to the local server (its requests carry the attacker's Host). `CERES_ALLOWED_HOSTS` (comma separated) adds the public names of a hosted
    deployment behind a proxy. A server bound to a non-loopback interface still checks: only IP addresses, loopback names and the names in the variable are served.
    """
    extra = frozenset(item.strip().lower() for item in os.environ.get("CERES_ALLOWED_HOSTS", "").split(",") if item.strip())
    if bind_host in LOOPBACK_HOSTS or bind_host in {"", "localhost"}:
        return LOOPBACK_HOSTS | extra
    # S10 / L1-04: bound to a LAN interface the guard stays on: a DNS-rebinding page is always reached by a NAME, so only IP addresses
    # localhost and the names in CERES_ALLOWED_HOSTS are served (a LAN name such as laptop.local must be listed in CERES_ALLOWED_HOSTS).
    return LOOPBACK_HOSTS | extra | {IP_LITERALS}


def _host_name(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _is_ip(name: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


class HostGuardMiddleware:
    """Pure ASGI middleware: answers 421 when the Host header names a host this server was not set up to serve."""

    def __init__(self, app, allowed: frozenset[str]):
        self.app = app
        self.allowed = allowed

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            host = next((value.decode("latin-1") for name, value in scope.get("headers", []) if name == b"host"), "")
            name = _host_name(host)
            if name not in self.allowed and not (IP_LITERALS in self.allowed and _is_ip(name)):
                body = json.dumps({"detail": "This host name is not served here."}).encode()
                if scope["type"] == "http":
                    await send({"type": "http.response.start", "status": 421,
                                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                    await send({"type": "http.response.body", "body": body})
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)
