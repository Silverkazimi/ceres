"""Read the Splinterlands game verdict for each operation in a transaction."""

from __future__ import annotations

import json
import re
import urllib.parse
from mnemosyne.accounting import wrap_injected_transport
from collections.abc import Iterable
from typing import Any

from praetoria.transport import API_ORIGIN, FixedGetTransport, ResponseData


def _lookup_url(lookup_id: str) -> str:
    query = urllib.parse.urlencode({"trx_id": lookup_id})
    return f"{API_ORIGIN}/transactions/lookup?{query}"


def _same_nonce(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _nonce_index(nonce: Any, expected: list[Any]) -> int | None:
    for index, expected_nonce in enumerate(expected):
        if _same_nonce(nonce, expected_nonce):
            return index
    return None


def _deed_uid(data: dict[str, Any]) -> str | None:
    value = data.get("deed_uid")
    if isinstance(value, str) and value:
        return value
    values = data.get("deed_uids")
    if isinstance(values, list) and len(values) == 1 and isinstance(values[0], str) and values[0]:
        return values[0]
    return None


def _payload(response: Any) -> Any:
    if isinstance(response, ResponseData):
        return response.data
    return response


def _get_json(transport: Any, url: str) -> Any:
    if hasattr(transport, "get_json") or callable(transport):
        return wrap_injected_transport(transport).get_json(url)
    raise TypeError("game verdict transport must provide get_json(url)")


def _inner_refusal(raw_result: Any) -> str | None:
    """The refusal text when a game result envelope (nested, and with JSON-encoded strings at any level) says it failed, else None. No calls."""
    result = raw_result
    for _ in range(8):
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                return None
        if not isinstance(result, dict):
            return None
        if result.get("success") is False:
            error = result.get("error")
            return error if isinstance(error, str) and error else "The game refused the operation"
        result = result.get("result")
    return None


# for an id it has no record of, the game answers `{"error": "Transaction [<id>] not found.", "error_code": 1}`
# the Praetoria transport raises that body as an UpstreamError, an injected transport may return it as the body.
_NOT_FOUND = re.compile(r"Transaction \[?[0-9A-Za-z-]+\]? not found", re.IGNORECASE)


def _is_not_found(value: Any) -> bool:
    if isinstance(value, dict):
        value = value.get("error") if "trx_info" not in value else None
    return isinstance(value, (str, BaseException)) and bool(_NOT_FOUND.search(str(value)))


def _op_outcomes(evidence: dict[str, Any], nonces: list[Any]) -> list[dict[str, Any]]:
    """: one row per expected game operation -- done / refused (the game's reason verbatim) / unknown. No calls."""
    by_index = {operation["index"]: operation for operation in evidence.get("operations", [])}
    rows = []
    for index, nonce in enumerate(nonces):
        operation = by_index.get(index)
        if operation is None:
            rows.append({"index": index, "nonce": nonce, "outcome": "unknown"})
            continue
        row = {"index": index, "nonce": nonce, "lookup_id": operation["lookup_id"],
               "outcome": "done" if operation["success"] is True else "refused"}
        if operation["success"] is False:
            row["error"] = operation["error"]
        rows.append(row)
    return rows


def _unknown(evidence: dict[str, Any], reason: str, detail: Any = None, nonces: list[Any] | None = None) -> dict[str, Any]:
    evidence["unknown_reason"] = reason
    if detail is not None:
        evidence["detail"] = str(detail)
    if nonces is not None:
        evidence["operations"].sort(key=lambda operation: operation["index"])
        evidence["op_outcomes"] = _op_outcomes(evidence, nonces)
    return {"status": "game_unknown", "evidence": evidence}


def game_verdict(
    tx_id: str,
    *,
    op_count: int,
    expected_nonces: Iterable[Any],
    account: str,
    transport: Any | None = None,
    include_result: bool = False,
) -> dict[str, Any]:
    """Return the game's accepted, refused, or genuinely unknown verdict.

    The lookup endpoint addresses the first operation with the bare transaction id and later
    operations by appending their dense zero-based index. A record is usable only when its
    player and JSON nonce identify this caller's operation.

    : every operation's own record is looked up and reported in `evidence.op_outcomes`
    (done / refused with the game's reason verbatim / unknown). A missing later record does not
    hide the records after it; it stays unknown, so the whole transaction stays `game_unknown`
    (never a verdict that permits signing again,  S3). When the bare id itself is not
    found the game has not processed the transaction yet and no further id is read.
    """
    evidence: dict[str, Any] = {
        "tx_id": tx_id,
        "account": account,
        "op_count": op_count,
        "lookups": [],
        "operations": [],
    }
    if (
        not isinstance(tx_id, str)
        or not tx_id
        or not isinstance(account, str)
        or not account
        or isinstance(op_count, bool)
        or not isinstance(op_count, int)
        or op_count < 1
    ):
        return _unknown(evidence, "invalid_request")
    try:
        nonces = list(expected_nonces)
    except (TypeError, ValueError):
        return _unknown(evidence, "invalid_expected_nonces")
    if (
        isinstance(expected_nonces, (str, bytes, bytearray))
        or len(nonces) != op_count
        or any(nonce is None for nonce in nonces)
        or any(
            any(_same_nonce(nonce, other) for other in nonces[index + 1 :])
            for index, nonce in enumerate(nonces)
        )
    ):
        return _unknown(evidence, "invalid_expected_nonces")

    client = transport if transport is not None else FixedGetTransport()
    attributed: set[int] = set()
    refused: list[dict[str, Any]] = []

    missing: list[str] = []

    def unknown(reason: str, detail: Any = None) -> dict[str, Any]:
        return _unknown(evidence, reason, detail, nonces)

    for lookup_index in range(op_count):
        lookup_id = tx_id if lookup_index == 0 else f"{tx_id}-{lookup_index}"
        evidence["lookups"].append(lookup_id)
        try:
            response = _payload(_get_json(client, _lookup_url(lookup_id)))
        except Exception as exc:
            if not _is_not_found(exc):
                return unknown("transport_error", exc)
            response = {"error": str(exc)}

        if _is_not_found(response):
            missing.append(lookup_id)
            if lookup_index == 0:
                evidence["missing"] = missing
                return unknown("not_found")
            continue
        if not isinstance(response, dict):
            return unknown("malformed_response")
        trx_info = response.get("trx_info")
        if not isinstance(trx_info, dict):
            return unknown("missing_transaction_info")
        if response.get("error") is not None and trx_info.get("success") is not False:
            return unknown("malformed_refusal")

        player = trx_info.get("player")
        raw_data = trx_info.get("data")
        try:
            data = json.loads(raw_data) if isinstance(raw_data, str) else None
        except (json.JSONDecodeError, TypeError):
            data = None
        if not isinstance(data, dict) or "n" not in data:
            return unknown("attribution_uncertain")
        nonce = data["n"]
        expected_index = _nonce_index(nonce, nonces)
        if player != account or expected_index is None:
            return unknown("attribution_uncertain")
        if expected_index in attributed:
            return unknown("duplicate_operation")
        success = trx_info.get("success")
        if success is not True and success is not False:
            return unknown("malformed_success")
        # S10 / L2-05: the game can answer "success" for the transaction and carry a refusal inside its result envelope. A refusal
        # anywhere in the answer is a refusal; it is never read as accepted.
        inner_error = _inner_refusal(trx_info.get("result"))
        if success is True and inner_error is not None:
            success = False

        operation: dict[str, Any] = {
            "index": expected_index,
            "lookup_index": lookup_index,
            "lookup_id": lookup_id,
            "nonce": nonce,
            "player": player,
            "success": success,
        }
        if include_result:
            operation["data"] = data
            try:
                raw_result = trx_info.get("result")
                operation["result"] = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
            except (json.JSONDecodeError, TypeError):
                operation["result"] = None
        deed_uid = _deed_uid(data)
        if deed_uid is not None:
            operation["deed_uid"] = deed_uid
        if success is False:
            error = trx_info.get("error") if trx_info.get("error") is not None else response.get("error")
            if error is None and inner_error is not None:
                error = inner_error
            if not isinstance(error, str):
                return unknown("missing_refusal_reason")
            operation["error"] = error
            refused.append(operation)
        attributed.add(expected_index)
        evidence["operations"].append(operation)

    evidence["operations"].sort(key=lambda operation: operation["index"])
    if refused:
        evidence["refused"] = refused
        evidence["error"] = refused[0]["error"]
    if missing or len(attributed) != op_count:
        # a refusal is reported for its own operation, but while another record is missing the
        # transaction's verdict stays unknown -- never a terminal answer that would release it for signing again.
        evidence["missing"] = missing
        return unknown("incomplete_attribution")
    evidence["op_outcomes"] = _op_outcomes(evidence, nonces)
    if refused:
        return {"status": "game_rejected", "evidence": evidence}
    return {"status": "game_accepted", "evidence": evidence}
