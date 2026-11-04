"""Bounded Hive RPC read and operation unpacking for the operation preview (moved from land_activity_source, )."""
from __future__ import annotations

import json
from http.client import HTTPSConnection, HTTPException
from typing import Any

from . import fixture_upstream, outbound_meter

MAX_RESPONSE_BYTES = 8_000_000


class ActivityReadError(RuntimeError):
    pass


# the Hive nodes Ceres reads from, in order. Kept identical to HIVE_RPC_NODES in
# ceres/static/transaction-queue.js (tests/ceres/test_ir9539_chain_lag.py compares them) and allowed in
# security_headers.CONNECT_SRC. A node that cannot be reached is skipped for the next; a node that answers is final.
HIVE_RPC_NODES = ("api.hive.blog", "api.openhive.network", "api.deathwing.me")


class _NodeUnavailable(ActivityReadError):
    pass


def _connect(host: str):
    return fixture_upstream.connection(host) if fixture_upstream.enabled() else HTTPSConnection(host, timeout=15)


def _rpc_at(host: str, method: str, params: dict) -> dict:
    meter_call = outbound_meter.begin_call(host, method, params.get("account"))
    connection = _connect(host)
    try:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        try:
            connection.request("POST", "/", request, {"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, HTTPException) as exc:
            raise _NodeUnavailable("Land activity could not be read") from exc
        if response.status != 200:
            raise _NodeUnavailable("Land activity read was unavailable or exceeded its size limit")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ActivityReadError("Land activity read was unavailable or exceeded its size limit")
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise _NodeUnavailable("Land activity could not be read") from exc
        if not isinstance(body, dict) or body.get("error") or body.get("id") != 1:
            raise ActivityReadError("Land activity RPC returned an invalid response")
        result = body.get("result")
        if not isinstance(result, dict):
            raise ActivityReadError("Land activity RPC returned no result")
        return result
    finally:
        outbound_meter.record(meter_call)
        connection.close()


def _rpc(method: str, params: dict) -> dict:
    if method == "block_api.get_block_range":
        raise ActivityReadError("Raw Hive block-range reads are retired")
    last: ActivityReadError | None = None
    for host in HIVE_RPC_NODES:
        try:
            return _rpc_at(host, method, params)
        except _NodeUnavailable as exc:
            last = exc
    raise ActivityReadError(str(last)) from last


def _operation(raw: Any) -> tuple[str | None, dict]:
    if isinstance(raw, dict):
        kind, value = raw.get("type"), raw.get("value")
    elif isinstance(raw, list) and len(raw) == 2:
        kind, value = raw
    else:
        raise ActivityReadError("A block contains an invalid operation")
    if not isinstance(kind, str) or not isinstance(value, dict):
        raise ActivityReadError("A block contains an invalid operation")
    return kind, value
