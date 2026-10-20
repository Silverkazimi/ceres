"""Bounded Hive RPC read and operation unpacking for the operation preview (moved from land_activity_source, )."""
from __future__ import annotations

import json
from http.client import HTTPSConnection, HTTPException
from typing import Any

from . import fixture_upstream, outbound_meter

MAX_RESPONSE_BYTES = 8_000_000


class ActivityReadError(RuntimeError):
    pass


def _rpc(method: str, params: dict) -> dict:
    if method == "block_api.get_block_range":
        raise ActivityReadError("Raw Hive block-range reads are retired")
    meter_call = outbound_meter.begin_call("api.hive.blog", method, params.get("account"))
    connection = fixture_upstream.connection("api.hive.blog") if fixture_upstream.enabled() else HTTPSConnection("api.hive.blog", timeout=15)
    try:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        connection.request("POST", "/", request, {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if response.status != 200 or len(raw) > MAX_RESPONSE_BYTES:
            raise ActivityReadError("Land activity read was unavailable or exceeded its size limit")
        body = json.loads(raw)
        if not isinstance(body, dict) or body.get("error") or body.get("id") != 1:
            raise ActivityReadError("Land activity RPC returned an invalid response")
        result = body.get("result")
        if not isinstance(result, dict):
            raise ActivityReadError("Land activity RPC returned no result")
        return result
    except (OSError, ValueError, HTTPException) as exc:
        raise ActivityReadError("Land activity could not be read") from exc
    finally:
        outbound_meter.record(meter_call)
        connection.close()


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
