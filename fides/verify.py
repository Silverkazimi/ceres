"""Generic chain-side re-verification -- the only network call the fides core makes itself.
`POST https://api.hive.blog` `condenser_api.get_transaction` for a claimed tx id, confirming
the ops that actually landed byte-for-byte match the bundle's ops (comparing PARSED
payloads, not raw strings -- key order in a JSON string is not guaranteed on either side).
Any divergence is `chain_absent`. Domain-level ("did the intended real-world effect occur")
verification is NOT here -- it is delegated entirely to the caller-supplied
`VerificationSpec` (see `ledger.py`).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable

from mnemosyne.accounting import attempt, mark_coverage, observed_opener, transport_dispatch

from .op import Op

HIVE_NODE_DEFAULT = "https://api.hive.blog"
_rpc_opener = observed_opener()
# set by `ceres.fixture_upstream.install` only; answers the RPC from recorded files.
FIXTURE_URLOPEN: Any = None

# transport(node, method, params) -> result. Overridable so fides core never needs a live
# network call in a test process.
Transport = Callable[[str, str, Any], Any]


class _RpcError(RuntimeError):
    def __init__(self, method: str, error: Any) -> None:
        self.method = method
        self.error = error
        super().__init__(f"{method} error: {error}")


def _is_transaction_not_found_error(error: Any) -> bool:
    try:
        text = json.dumps(error, sort_keys=True, default=str).lower()
    except (TypeError, ValueError):
        text = str(error).lower()
    if any(marker in text for marker in ("method", "plugin", "rate limit", "too many requests", "syncing", "timeout", "unavailable", "malformed")):
        return False
    return bool(
        re.search(r"\b(?:unable to|could not|cannot|can't|failed to) find (?:the )?transaction\b", text)
        or re.search(r"\btransaction (?:not found|does not exist|doesn't exist)\b", text)
        or re.search(r"\bunknown transaction\b", text)
    )


def _http_rpc_call(node: str, method: str, params: Any, *, timeout: float = 15.0) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode("utf-8")
    req = urllib.request.Request(node, data=payload, headers={"Content-Type": "application/json"})
    with transport_dispatch(node, "POST", endpoint=method):
        opened = FIXTURE_URLOPEN(req, timeout) if FIXTURE_URLOPEN is not None else _rpc_opener.open(req, timeout=timeout)
        with opened as resp:
            raw = resp.read()
            if hasattr(resp, "observer"):
                resp.observer.record["response_decoded_body_bytes"] = len(raw)
            body = json.loads(raw.decode("utf-8"))
        if "error" in body:
            raise _RpcError(method, body["error"])
        return body.get("result")


def _injected_rpc_call(transport, node, method, params):
    capability = getattr(transport, "mnemosyne_observation", "unknown")
    if capability == "per_hop":
        return transport(node, method, params)
    with transport_dispatch(node, "POST", endpoint=method):
        mark_coverage("synthetic" if capability == "synthetic" else "complete" if capability == "single_attempt" else "unknown")
        if capability == "single_attempt":
            with attempt(node, "POST"):
                return transport(node, method, params)
        return transport(node, method, params)


def get_transaction(
    tx_id: str, *, node: str = HIVE_NODE_DEFAULT, transport: Transport | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Returns `(tx, reason_kind)`. `tx` is the transaction dict on success. On failure
    `tx` is None and `reason_kind` distinguishes two DIFFERENT situations that used to be
    collapsed into one silent `None`, which made `domain-confirmed` unreachable in a real
    live-broadcast case for reasons that were otherwise invisible:

    - `"not_found"`: the node round-tripped successfully and the RPC error payload
      specifically says that this transaction is unknown. Other JSON-RPC errors are
      not evidence that the transaction is absent.
    - `"lookup_failed"`: the request failed at the transport layer or the node returned
      an error whose meaning is not specifically transaction-not-found (for example a
      rate limit, disabled plugin, or syncing node). Keychain returns as soon as a node
      accepts the tx into the mempool, well before `get_transaction` can necessarily see
      it in a block, so uncertainty must never be reported as "your transaction is not on
      chain."

    `verify_chain` preserves `reason_kind` in its `evidence` rather than discarding it.
    """
    try:
        if transport is None:
            result = _http_rpc_call(node, "condenser_api.get_transaction", [tx_id])
        else:
            result = _injected_rpc_call(transport, node, "condenser_api.get_transaction", [tx_id])
    except _RpcError as exc:
        return None, "not_found" if _is_transaction_not_found_error(exc.error) else "lookup_failed"
    except RuntimeError as exc:
        return None, "not_found" if _is_transaction_not_found_error(str(exc)) else "lookup_failed"
    except (urllib.error.URLError, OSError, TypeError, AttributeError, ValueError):
        return None, "lookup_failed"
    # A clean call that itself returns nothing (a bare `None`/empty result, no RPC error
    # raised) is also an affirmative not-found, not a lookup failure.
    return (result, None) if result else (None, "not_found")


def _extract_custom_json_ops(tx: dict[str, Any]) -> list[dict[str, Any]]:
    ops = []
    for entry in tx.get("operations", []):
        # Hive nodes have returned both the legacy [name, body] tuple shape and a
        # {"type": "...", "value": {...}} shape historically -- handle both.
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            name, body = entry
        elif isinstance(entry, dict):
            name, body = entry.get("type", ""), entry.get("value", {})
            name = name[: -len("_operation")] if name.endswith("_operation") else name
        else:
            continue
        if name == "custom_json":
            ops.append(body)
    return ops


def ops_match(expected: list[Op], observed: list[dict[str, Any]]) -> bool:
    """Order-independent, parsed-payload comparison of `expected` bundle ops against the
    `observed` on-chain custom_json op bodies."""
    if len(expected) != len(observed):
        return False

    def norm_expected(op: Op) -> tuple:
        return (
            op.operation_id,
            json.dumps(op.json_payload, sort_keys=True),
            tuple(sorted(op.required_auths)),
            tuple(sorted(op.required_posting_auths)),
        )

    def norm_observed(row: dict[str, Any]) -> tuple:
        raw = row.get("json")
        parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return (
            row.get("id"),
            json.dumps(parsed, sort_keys=True),
            tuple(sorted(row.get("required_auths", []))),
            tuple(sorted(row.get("required_posting_auths", []))),
        )

    return sorted(norm_expected(op) for op in expected) == sorted(norm_observed(row) for row in observed)


def verify_chain(
    expected_ops: list[Op],
    tx_id: str,
    *,
    node: str = HIVE_NODE_DEFAULT,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Return chain evidence, including the transaction's inclusion block when present."""
    tx, reason_kind = get_transaction(tx_id, node=node, transport=transport)
    if tx is None:
        reason_kind = reason_kind or "not_found"
        reason = (
            "transaction not found on chain" if reason_kind == "not_found"
            else "transaction lookup failed (network/node error -- NOT evidence the transaction is absent)"
        )
        # `status` stays exactly "chain_absent" -- fides/ledger.py's VALID_STATES and
        # ALLOWED_TRANSITIONS (and its one production consumer, fides/cli.py's
        # `record_chain_result`) only ever accept "chain_verified"/"chain_absent" here
        # `reason_kind` is new evidence detail, not a new top-level status, so the
        # ledger's state machine is unaffected by this distinction.
        return {"status": "chain_absent", "evidence": {"reason": reason, "reason_kind": reason_kind, "tx_id": tx_id}}
    observed = _extract_custom_json_ops(tx)
    if not ops_match(expected_ops, observed):
        return {
            "status": "chain_absent",
            "evidence": {
                "reason": "on-chain ops do not match bundle ops",
                "reason_kind": "mismatch",
                "tx_id": tx_id,
                "block_num": tx.get("block_num"),
                "observed": observed,
            },
        }
    return {
        "status": "chain_verified",
        "evidence": {"tx_id": tx_id, "block_num": tx.get("block_num"), "observed": observed},
    }
