"""Bounded public bridge discovery; chain inclusion alone never proves settlement."""
from __future__ import annotations

import json
import re
from http.client import HTTPSConnection
from urllib.parse import urlencode

from praetoria.transport import API_ORIGIN, FixedGetTransport
from . import fixture_upstream
from . import harvest_routing as routing
from .harvest_routing_validator import _read as validator_read
from .security import validate_player


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _history(account: str, start: int) -> list:
    account = validate_player(account)
    if isinstance(start, bool) or not isinstance(start, int) or start < -1:
        raise routing.RoutingError("Invalid bridge history cursor")
    # fixture mode answers from recorded files; never a socket.
    connection = fixture_upstream.connection("api.hive.blog") if fixture_upstream.enabled() else HTTPSConnection("api.hive.blog", timeout=15)
    try:
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "condenser_api.get_account_history",
                              "params": [account, start, 100 if start < 0 else min(100, start)]})
        connection.request("POST", "/", payload, {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(524289)
        if response.status != 200 or len(raw) > 524288:
            raise routing.RoutingError("Bridge history is unavailable or exceeds its read limit")
        result = json.loads(raw)
        rows = result.get("result")
        if result.get("error") or not isinstance(rows, list) or len(rows) > 101:
            raise routing.RoutingError("Bridge history returned an unexpected response")
        return rows
    except (OSError, ValueError) as exc:
        raise routing.RoutingError("Bridge history could not be read") from exc
    finally:
        connection.close()


def settle(player: str, step: dict, *, older: bool = False) -> dict:
    effects = step["game_result"]["result"]["data"]
    bridge = validate_player(effects.get("external_transfer_account"))
    withdrawal = effects.get("external_withdrawal_required") is True
    kind = "withdrawal" if withdrawal else "deposit" if step["kind"] == "liquidity" else "swap"
    key = {"withdrawal": "external_withdrawal_id", "deposit": "pending_deposit_id", "swap": "pending_swap_id"}[kind]
    identifier = effects.get(key)
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
        raise routing.RoutingError("The game did not return a valid bridge reference")
    amount_key = "external_withdrawal_amount" if withdrawal else "pending_external_amount"
    expected_amount = routing.amount(effects.get(amount_key), positive=True)
    if withdrawal and (effects.get("external_withdrawal_player") != player or effects.get("external_withdrawal_token") != "SPS"):
        raise routing.RoutingError("The SPS withdrawal recipient does not match this workflow")
    if not withdrawal and step.get("funding", {}).get("state") != "funded":
        return {"state": "settling"}
    cursor = step.get("bridge_next_start") if older else -1
    if cursor is None:
        return {"state": "settling", "bridge_search_note": "The retained history window has no earlier cursor"}
    rows = _history(bridge, cursor)
    indexes = [row[0] for row in rows if isinstance(row, list) and len(row) == 2 and isinstance(row[0], int)]
    refund = incomplete = None
    result = {"state": "settling", "bridge_next_start": min(indexes) - 1 if indexes and min(indexes) > 0 else None,
              "bridge_search_note": "No confirmed matching bridge operation in this bounded history window"}
    for entry in rows:
        if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[1], dict):
            continue
        record = entry[1]
        op = record.get("op")
        if not isinstance(op, list) or len(op) != 2 or op[0] != "custom_json" or not isinstance(op[1], dict):
            continue
        body = op[1]
        if bridge not in body.get("required_auths", []) or record.get("op_in_trx") != 0:
            continue
        tx = record.get("trx_id")
        if not isinstance(tx, str) or not re.fullmatch(r"[a-f0-9]{40}", tx):
            continue
        data = _object(body.get("json"))
        # the pool refunds a swap it did not execute (price moved past the slippage): a bridge-signed transfer back to the
        # player with memo "LP swap refund <id>". The chain proves the author (required_auths = the bridge), so no second read.
        # (audit #5): a refund transfer is only noted here; it closes the swap below, and only with proof of payment.
        if kind == "swap" and body.get("id") == "sm_token_transfer" and data.get("to") == player and data.get("token") == "SPS" \
                and data.get("memo") == f"LP swap refund {identifier}":
            try:
                candidate = {"tx": tx, "qty": routing.amount(data.get("qty"), positive=True)}
            except routing.RoutingError:
                continue
            refund = candidate if refund is None or candidate["qty"] == expected_amount else refund
            if refund["qty"] == expected_amount:
                break  # the full pending amount came back: proof enough, no further read
            continue
        if withdrawal:
            params = data.get("params") if body.get("id") == "sm_sps" and data.get("action") == "token_transfer" else data if body.get("id") == "sm_token_transfer" else {}
            if not isinstance(params, dict) or params.get("to") != player or params.get("token") != "SPS" or params.get("memo") != f"LP external swap withdrawal {identifier}":
                continue
            if routing.amount(params.get("qty")) != expected_amount:
                continue
            verified = validator_read("/transaction?" + urlencode({"id": tx}), missing_ok=True)
            if not isinstance(verified, dict) or verified.get("id") != tx or verified.get("player") != bridge or verified.get("type") != "token_transfer" or _object(verified.get("data")) != params or verified.get("success") is not True:
                continue
            return {"state": "confirmed", "bridge_transaction_id": tx,
                    "receipt": {"transaction_id": step["tx_id"], "bridge_transaction_id": tx,
                                "received": [{"symbol": "SPS", "amount": routing.text(expected_amount), "region_uid": routing.INVENTORY}]}}
        operation = "external_lp_deposit" if kind == "deposit" else "external_swap"
        if body.get("id") != "sm_land_operation" or data.get("op") != operation or data.get("player") != player or str(data.get(key)) != str(identifier):
            continue
        if routing.amount(data.get("external_token_amount")) != expected_amount:
            continue
        info = FixedGetTransport(timeout=15, retries=1).get_json(API_ORIGIN + "/transactions/lookup?" + urlencode({"trx_id": tx})).data
        info = info.get("trx_info") if isinstance(info, dict) else None
        if not isinstance(info, dict) or info.get("id") != tx or info.get("player") != bridge or info.get("type") != "land_operation" or _object(info.get("data")) != data or info.get("success") is not True:
            continue
        envelope = _object(info.get("result"))
        inner = _object(envelope.get("result"))
        settled = _object(inner.get("data"))
        if envelope.get("success") is not True or inner.get("success") is not True or str(settled.get(key)) != str(identifier):
            continue
        if kind == "swap" and settled.get("external_swap_completed") is not True:
            # the pool ran the swap and did not complete it; its refund transfer follows.
            # (audit #5): that verdict alone is not a refund -- the swap stays settling until the refund transfer is found.
            incomplete = incomplete or {"tx": tx, "envelope": envelope, "reason": str(inner.get("error") or settled.get("error") or "")}
            continue
        receipt = {"transaction_id": step["tx_id"], "bridge_transaction_id": tx, "received": []}
        if kind == "deposit":
            if settled.get("resource") != "SPS" or routing.amount(settled.get("resource_amount")) != expected_amount:
                continue
            receipt["deposited"] = [{"symbol": "SPS", "amount": routing.text(expected_amount)},
                                    {"symbol": "DEC", "amount": routing.text(routing.amount(settled.get("dec_amount"), positive=True))}]
        else:
            quote = step["review"]["step"].get("quote", step["review"]["step"])
            symbol = quote["destination_symbol"]
            if symbol != "DEC" and settled.get("resource") != symbol:
                continue
            value = settled.get("dec_amount") if symbol == "DEC" else settled.get("resource_amount")
            receipt["received"] = [{"symbol": symbol, "amount": routing.text(routing.amount(value, positive=True)),
                                    "region_uid": step["review"]["step"]["destination_region"]}]
        return {"state": "confirmed", "bridge_transaction_id": tx, "bridge_result": envelope, "receipt": receipt}
    # (audit #5): the swap is refunded only on proof of payment -- the bridge-signed refund transfer for the full pending
    # amount, or (with the game's own verdict that the pool did not complete this swap) the refund transfer that was paid.
    if refund and (refund["qty"] == expected_amount or incomplete):
        return {"state": "refunded", "bridge_transaction_id": refund["tx"], "bridge_result": (incomplete or {}).get("envelope"),
                "refunded": [{"symbol": "SPS", "amount": routing.text(refund["qty"])}], "expected": routing.text(expected_amount),
                "full": refund["qty"] == expected_amount, "reason": (incomplete or {}).get("reason", "")}
    if incomplete:
        result["refund_pending"] = True
        result["bridge_search_note"] = "The pool did not complete this swap; its refund transfer is not in this history window yet"
    return result
