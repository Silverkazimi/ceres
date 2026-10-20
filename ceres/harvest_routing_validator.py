"""Bounded public SPS validator reads at the official game-configured host."""
from __future__ import annotations

import json
import re
from decimal import Decimal
from http.client import HTTPSConnection
from urllib.parse import urlencode

from . import fixture_upstream
from .harvest_routing import RoutingError, amount

HOST = "splinterlands-validator-api.splinterlands.com"
MAX_BYTES = 262144


def _read(path: str, *, missing_ok: bool = False):
    connection = fixture_upstream.connection(HOST) if fixture_upstream.enabled() else HTTPSConnection(HOST, timeout=15)
    try:
        connection.request("GET", path, headers={"Accept": "application/json", "User-Agent": "Ceres-harvest-routing"})
        response = connection.getresponse()
        if missing_ok and response.status == 404:
            return None
        if response.status != 200:
            raise RoutingError("SPS validator data is unavailable")
        payload = response.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise RoutingError("SPS validator response exceeds the size limit")
        return json.loads(payload, parse_float=Decimal)
    except (OSError, ValueError) as exc:
        raise RoutingError("SPS validator data could not be read") from exc
    finally:
        connection.close()


def sps_balance(player: str) -> Decimal:
    if not isinstance(player, str) or not re.fullmatch(r"[a-z][a-z0-9.-]{2,15}", player):
        raise RoutingError("Invalid SPS balance account")
    rows = _read("/balances?" + urlencode({"account": player}))
    if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("player") != player for row in rows):
        raise RoutingError("SPS validator returned an unexpected account balance")
    balances = [row for row in rows if row.get("token") == "SPS"]
    if len(balances) > 1:
        raise RoutingError("SPS validator returned duplicate SPS balances")
    return amount(balances[0].get("balance")) if balances else Decimal(0)


def funding_receipt(player: str, transaction_id: str, review: dict) -> dict:
    if not isinstance(transaction_id, str) or not re.fullmatch(r"[a-f0-9]{40}", transaction_id):
        raise RoutingError("Invalid SPS funding transaction ID")
    record = _read("/transaction?" + urlencode({"id": transaction_id}), missing_ok=True)
    if record is None:
        return {"state": "pending", "seen": False}
    if not isinstance(record, dict):
        raise RoutingError("Unexpected SPS validator transaction response")
    data = record.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data, parse_float=Decimal)
        except ValueError as exc:
            raise RoutingError("Invalid SPS validator transaction payload") from exc
    expected = json.loads(json.dumps(review["payload"]["params"]), parse_float=Decimal)
    if (record.get("id") != transaction_id or record.get("player") != player
            or record.get("type") != "token_transfer" or data != expected):
        raise RoutingError("SPS funding transaction does not match the complete reviewed transfer")
    if record.get("success") is False:
        return {"state": "failed", "error": str(record.get("error") or "The validator refused this SPS transfer")}
    if record.get("success") is not True:
        return {"state": "pending", "seen": True}
    return {"state": "funded", "transaction_id": transaction_id, "block_num": record.get("block_num"),
            "confirmed_at": record.get("created_date"), "amount": review["amount"], "recipient": review["recipient"]}


def recovered_funding_receipt(player: str, transaction_id: str, review: dict) -> dict:
    if not isinstance(transaction_id, str) or not re.fullmatch(r"[a-f0-9]{40}", transaction_id):
        raise RoutingError("Invalid SPS funding transaction ID")
    record = _read("/transaction?" + urlencode({"id": transaction_id}), missing_ok=True)
    if not isinstance(record, dict) or record.get("success") is not True:
        raise RoutingError("This transfer has not been independently confirmed as successful")
    data = record.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data, parse_float=Decimal)
        except ValueError as exc:
            raise RoutingError("Invalid SPS validator transaction payload") from exc
    expected = review["payload"]["params"]
    if (record.get("id") != transaction_id or record.get("player") != player
            or record.get("type") != "token_transfer" or not isinstance(data, dict)
            or any(data.get(key) != expected.get(key) for key in ("to", "token", "memo"))
            or amount(data.get("qty"), positive=True) != amount(expected["qty"], positive=True)):
        raise RoutingError("The successful SPS transfer does not fund this exact pending operation")
    # External clients have their own app and nonce; retain their complete receipt.
    return {"state": "funded", "transaction_id": transaction_id,
            "block_num": record.get("block_num"), "confirmed_at": record.get("created_date"),
            "amount": review["amount"], "recipient": review["recipient"],
            "provenance": "verified_external_transfer", "validator_receipt": json.loads(json.dumps(record, default=lambda value: str(value) if isinstance(value, Decimal) else value)),
            "receipt_decimal_encoding": "exact_decimal_strings"}
