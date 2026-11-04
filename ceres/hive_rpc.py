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


def _answer_at(host: str, method: str, params) -> tuple[str, Any]:
    """One node's JSON-RPC answer: ("result", value) or ("error", rpc_error). Raises _NodeUnavailable when the node
    could not be read at all (transport, status, size, not JSON-RPC): the caller may then ask the next node."""
    meter_call = outbound_meter.begin_call(host, method, None)
    connection = _connect(host)
    try:
        try:
            connection.request("POST", "/", json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(65537)
        except (OSError, HTTPException) as exc:
            raise _NodeUnavailable("Hive could not be read") from exc
        if response.status != 200 or len(raw) > 65536:
            raise _NodeUnavailable("Hive read unavailable")
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise _NodeUnavailable("Hive read unavailable") from exc
        if not isinstance(body, dict) or body.get("id") != 1:
            raise _NodeUnavailable("Hive read unavailable")
        if body.get("error") is not None:
            return "error", body["error"]
        return "result", body.get("result")
    finally:
        outbound_meter.record(meter_call)
        connection.close()


def known_transaction_status(tx_id: str, expiration_bound: str | None, *, nodes=HIVE_RPC_NODES, answer_at=None) -> tuple[str, int | None]:
    """ fix round 3 (astra-review-r3 N3, aletheia-verdict-r3 L4): may a KNOWN transaction id be called "never
    sent"? Only on an expired-irreversible proof, never on a bare "not found". Per node, in failover order, ONE node
    must answer both reads itself: `condenser_api.get_transaction` says the id is unknown AND
    `transaction_status_api.find_transaction` with an expiration no earlier than the real one (`expiration_bound`,
    ISO without zone) says `expired_irreversible`. Returns ("included", block_num) | ("expired", None) | ("unknown",
    None). A node that cannot be read, lacks the status plugin or answers anything else is not proof; the next node is
    asked. A node that contradicts itself (status says in a block, lookup says unknown) ends the check as unknown.
    With `expiration_bound` None (fix round 4, : inclusion failover only) no status is read: ("included", n) or
    ("unknown", None), never "expired"."""
    from fides.verify import _is_transaction_not_found_error
    ask = answer_at or _answer_at
    for host in nodes:
        try:
            kind, value = ask(host, "condenser_api.get_transaction", [tx_id])
            if kind == "result" and isinstance(value, dict) and isinstance(value.get("block_num"), int) and value["block_num"] > 0:
                return "included", value["block_num"]
            if not ((kind == "error" and _is_transaction_not_found_error(value)) or (kind == "result" and not value)):
                continue
            if expiration_bound is None:
                continue
            kind, value = ask(host, "transaction_status_api.find_transaction", {"transaction_id": tx_id, "expiration": expiration_bound})
        except _NodeUnavailable:
            continue
        status = value.get("status") if kind == "result" and isinstance(value, dict) else None
        if status == "expired_irreversible":
            return "expired", None
        if status in ("within_mempool", "within_reversible_block", "within_irreversible_block", "expired_reversible"):
            return "unknown", None
    return "unknown", None


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
