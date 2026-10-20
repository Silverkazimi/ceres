"""Pure normalization of one harvest transaction result (game lookup plus fragment history); zero outbound calls."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any

TX_ID = re.compile(r"[0-9a-f]{40}")
_RESOURCE_COSTS = ("grain_eaten", "dec_spent")


def _decoded(value: Any) -> Any:
    """Decode one game JSON field; zero outbound calls."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _number(value: Any) -> Decimal:
    """Accept a finite nonnegative game amount; zero outbound calls."""
    if isinstance(value, bool):
        raise ValueError("invalid amount")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("invalid amount") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("invalid amount")
    return result


def _data(result: Any) -> Any:
    """Unwrap the game's success/result/data envelope; zero outbound calls."""
    value = _decoded(result)
    for _ in range(4):
        if not isinstance(value, dict) or value.get("success") is False:
            return None
        if "result" in value:
            value = _decoded(value["result"])
        elif "data" in value:
            return value["data"]
        else:
            return value
    return value


def harvest_rows(result: Any, region_uid: str | None = None) -> list[dict[str, Any]]:
    """Read successful lookup harvest rows in either result shape; zero outbound calls."""
    data = _data(result)
    if isinstance(data, dict) and "results" in data:
        rows = data.get("results")
    elif isinstance(data, dict):
        rows = [data]
    else:
        rows = data
    if not isinstance(rows, list):
        return []
    region = None
    if isinstance(region_uid, str):
        try:
            region = int(region_uid.rsplit("-", 1)[-1])
        except ValueError:
            return []
    valid = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("deed_uid") or (region is not None and row.get("region_number") != region):
            return []
        if row.get("result_message") == "Successful":
            valid.append(row)
    return valid


def tax_rows(result: Any, region_uid: str | None = None) -> list[dict[str, Any]]:
    """Read tax token rows and upkeep from one lookup result; zero outbound calls."""
    data = _data(result)
    rows = data if isinstance(data, list) else data.get("results", [data]) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return []
    region = None
    if isinstance(region_uid, str):
        try:
            region = int(region_uid.rsplit("-", 1)[-1])
        except ValueError:
            return []
    if any(not isinstance(row, dict) or not row.get("deed_uid") or
           (region is not None and row.get("region_number") != region) for row in rows):
        return []
    return [row for row in rows if row.get("result_message") == "Successful" and
            isinstance(row.get("tokens"), dict) and isinstance(row["tokens"].get("tokens"), list)]


def amounts(rows: list[dict[str, Any]], *, tax_collection: bool = False) -> dict[str, dict[str, float]]:
    """Sum game lookup amounts with Decimal, converting to floats only at the edge; zero calls."""
    totals: dict[str, dict[str, Decimal]] = {key: {} for key in ("before_tax", "tax", "received", "costs")}

    def add(bucket: str, symbol: Any, value: Any) -> None:
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("missing resource symbol")
        number = _number(value)
        if number:
            name = symbol.upper()
            totals[bucket][name] = totals[bucket].get(name, Decimal(0)) + number

    for row in rows:
        if tax_collection:
            for token in row["tokens"]["tokens"]:
                if not isinstance(token, dict):
                    raise ValueError("invalid tax token")
                add("received", token.get("token"), token.get("received"))
            if "kingdom_grain_cost" in row:
                add("costs", "GRAIN", row["kingdom_grain_cost"])
        else:
            symbol = row.get("resource_symbol")
            is_grain = isinstance(symbol, str) and symbol.upper() == "GRAIN"
            if "received_amount" not in row:
                raise ValueError("harvest receipt is missing received amount")
            for field, bucket in (("resource_amount", "before_tax"), ("tax_amount", "tax"),
                                  ("received_amount", "received")):
                if field in row:
                    add(bucket, symbol, row[field])
            if all(field in row for field in ("resource_amount", "tax_amount", "received_amount")):
                rewards_eaten = _number(row["grain_rewards_eaten"]) if is_grain and "grain_rewards_eaten" in row else Decimal(0)
                if abs(_number(row["resource_amount"]) - _number(row["tax_amount"]) -
                       rewards_eaten - _number(row["received_amount"])) > Decimal("0.001"):
                    raise ValueError("game harvest amounts do not reconcile")
            elif "grain_rewards_eaten" in row:
                _number(row["grain_rewards_eaten"])
            for field in _RESOURCE_COSTS:
                if field in row:
                    add("costs", "DEC" if field == "dec_spent" else "GRAIN", row[field])
            if not is_grain and "grain_rewards_eaten" in row:
                add("costs", "GRAIN", row["grain_rewards_eaten"])
            for item in row.get("inputs_consumed", []):
                if not isinstance(item, dict):
                    raise ValueError("invalid input cost")
                add("costs", item.get("symbol"), item.get("qty"))
        if tax_collection and "dec_spent" in row:
            add("costs", "DEC", row["dec_spent"])
    net = {symbol: totals["received"].get(symbol, Decimal(0)) - totals["costs"].get(symbol, Decimal(0))
           for symbol in totals["received"].keys() | totals["costs"].keys()}
    totals["net"] = net
    return {bucket: {symbol: float(value) for symbol, value in entries.items()} for bucket, entries in totals.items()}


def finds(fragment_history_rows: Any, tx_id: str | None = None) -> list[dict[str, Any]]:
    """Count only distinct real fragment or Labor's Luck hits; zero outbound calls."""
    rows = fragment_history_rows.get("data") if isinstance(fragment_history_rows, dict) else fragment_history_rows
    if not isinstance(rows, list):
        return []
    found, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or (tx_id is not None and row.get("trx_id") != tx_id):
            continue
        key = row.get("reward_action_id")
        if key is None:
            continue
        for kind, hit in (("fragment", row.get("fragment_found") is True),
                          ("labors_luck", bool(row.get("labors_luck_uid")))):
            identity = (key, kind)
            if not hit or identity in seen:
                continue
            seen.add(identity)
            entry = {"kind": kind, "deed_uid": row.get("deed_uid"), "reward_action_id": key}
            if kind == "fragment":
                entry["type"] = row.get("fragment_type")
            else:
                entry["uid"] = row["labors_luck_uid"]
            found.append(entry)
    return found


def receipt(header: dict[str, Any], lookup: Any, extras: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one normalized receipt from signed history and one game lookup; zero outbound calls."""
    extras = extras or {}
    tx_id, index = header.get("tx_id"), header.get("operation_index", 0)
    if not isinstance(tx_id, str) or not TX_ID.fullmatch(tx_id) or type(index) is not int or index < 0:
        raise ValueError("invalid transaction identity")
    lookup_id = tx_id + (f"-{index}" if index else "")
    payload = header.get("payload") if isinstance(header.get("payload"), dict) else {}
    custom_id = header.get("custom_json_id")
    operation = payload.get("op") if custom_id == "sm_land_operation" else custom_id
    family = "taxes" if operation == "tax_collection" else "harvests"
    direction = header.get("direction", "outgoing")
    signer = header.get("signer", header.get("player"))
    result = {"id": f"{tx_id}:{index}", "tx_id": tx_id, "op_index": index,
              "link": f"https://hivehub.dev/tx/{tx_id}", "at": header.get("timestamp"),
              "family": family, "kind": operation, "state": "pending", "region": payload.get("region_uid"),
              "direction": direction, "signer": signer, "discovery": header.get("discovery_source", "hive.account_history"),
              "plots": [], "received": {}, "costs": {}, "net": {}, "before_tax": {}, "tax": {},
              "finds": [], "identities": {}, "gaps": [], "provenance": {}}
    result["provenance"].update({"tx_id": "hive.account_history.trx_id", "op_index": "hive.account_history.op_in_trx",
                                  "id": "derived: tx_id plus op_index", "link": "derived: tx_id via hivehub.dev/tx",
                                  "at": "hive.account_history.timestamp", "kind": "hive.account_history.custom_json",
                                  "family": "derived: signed custom_json kind",
                                  "region": "hive.account_history.custom_json.region_uid",
                                  "direction": "hive.account_history.authority",
                                  "signer": "hive.account_history.required_auths/required_posting_auths",
                                  "discovery": "hive.account_history",
                                  "state": "game.transactions.lookup.trx_info.success",
                                  "plots": "hive.account_history.custom_json.deed_uids"})
    info = lookup.get("trx_info") if isinstance(lookup, dict) else None
    if not isinstance(info, dict):
        result["gaps"].append("game_record_unavailable")
        return result
    if (direction == "incoming" and payload.get("to") != header.get("player")) or (
            info.get("id") != lookup_id or info.get("player") != signer
            or (header.get("block") is not None and info.get("block_num") != header["block"])
            or info.get("type") != str(custom_id).removeprefix("sm_")
            or _decoded(info.get("data")) != payload):
        result["state"] = "unverified"
        result["gaps"].append("game_record_mismatch")
        return result
    raw_result = _decoded(info.get("result"))
    inner_result = raw_result.get("result") if isinstance(raw_result, dict) else None
    if info.get("success") is False or (isinstance(raw_result, dict) and raw_result.get("success") is False) or (
            isinstance(inner_result, dict) and inner_result.get("success") is False):
        result["state"] = "failed"
        return result
    if info.get("success") is not True:
        return result
    result["state"] = "confirmed"
    game = raw_result
    if operation in ("harvest_all", "harvest_resources", "tax_collection"):
        rows = tax_rows(game, result["region"]) if operation == "tax_collection" else harvest_rows(game, result["region"])
        if not rows:
            result["gaps"].append("game_amounts_unavailable")
        else:
            try:
                values = amounts(rows, tax_collection=operation == "tax_collection")
            except (ValueError, TypeError, AttributeError):
                result["gaps"].append("game_amounts_unavailable")
            else:
                result.update(values)
                result["plots"] = list(dict.fromkeys(row["deed_uid"] for row in rows))
                result["provenance"]["plots"] = "game.transactions.lookup.result.data.deed_uid"
                fields = {"received": "tokens.tokens[].received" if operation == "tax_collection" else "received_amount",
                          "costs": "kingdom_grain_cost/dec_spent" if operation == "tax_collection" else "grain_eaten/grain_rewards_eaten(non-GRAIN rows)/inputs_consumed/dec_spent",
                          "before_tax": "resource_amount", "tax": "tax_amount"}
                for field in ("received", "costs", "before_tax", "tax"):
                    result["provenance"][field] = {symbol: f"game.transactions.lookup.result.data.{fields[field]}"
                                                    for symbol in values[field]}
                result["provenance"]["net"] = {symbol: "derived: received minus same-resource costs" for symbol in values["net"]}
        fragment_history = extras.get("fragment_history")
        fragment_rows = fragment_history.get("data") if isinstance(fragment_history, dict) else fragment_history
        if isinstance(fragment_rows, list) and (not isinstance(fragment_history, dict) or
                                                fragment_history.get("status", "success") == "success"):
            result["finds"] = finds(fragment_rows, lookup_id)
            result["provenance"]["finds"] = "vapi.land.resources.fragment_history.reward_action_id"
        else:
            result["gaps"].append("finds_unavailable")
    result["provenance"].setdefault("received", {})
    result["provenance"].setdefault("costs", {})
    result["provenance"].setdefault("net", {})
    result["provenance"].setdefault("before_tax", {})
    result["provenance"].setdefault("tax", {})
    result["provenance"].setdefault("finds", "vapi.land.resources.fragment_history.reward_action_id" if result["finds"] else "none")
    result["provenance"].setdefault("identities", "hive.account_history.custom_json")
    return result
