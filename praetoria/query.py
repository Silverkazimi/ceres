"""Collection normalization, filtering, and BCX aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .api import SplinterlandsClient
from .cache import JsonCache, safe_name
from .errors import SchemaError
from .models import QuerySpec, SourceInfo, iso, json_number
from .sets import detail_id, resolve_universe


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise SchemaError(f"{field} must be numeric, not boolean")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"{field} must be numeric") from exc
    if result < 0:
        raise SchemaError(f"{field} must not be negative")
    return result


def _number(value: Any, field: str) -> int | float:
    if isinstance(value, bool):
        raise SchemaError(f"{field} must be numeric, not boolean")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"{field} must be numeric") from exc
    if number < 0:
        raise SchemaError(f"{field} must not be negative")
    return int(number) if number.is_integer() else number


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def stake_status(card: dict[str, Any], as_of: datetime) -> str:
    ref = card.get("stake_ref_uid")
    start = _time(card.get("stake_start_date"))
    end = _time(card.get("stake_end_date"))
    evidence = ref is not None or card.get("stake_start_date") is not None or card.get("stake_end_date") is not None
    if not evidence:
        return "unstaked"
    if not start and card.get("stake_start_date"):
        return "unknown"
    if end and end <= as_of:
        return "historical"
    if start and start > as_of:
        return "unknown"
    if ref is not None and (not start or start <= as_of) and (not end or end > as_of):
        return "active"
    return "unknown"


def delegation_status(card: dict[str, Any]) -> str:
    fields = ("delegated_to", "delegated_to_display_name", "delegation_tx")
    present = any(card.get(field) not in (None, "", False) for field in fields)
    return "present" if present else "none"


def normalize_card(card: dict[str, Any]) -> dict[str, Any]:
    required = ("uid", "card_detail_id", "bcx", "gold", "edition", "card_set", "collection_power")
    missing = [key for key in required if key not in card]
    if missing:
        raise SchemaError(f"collection card missing fields: {', '.join(missing)}")
    return {
        "uid": str(card["uid"]),
        "card_detail_id": _int(card["card_detail_id"], "card_detail_id"),
        "bcx": _int(card["bcx"], "bcx"),
        "gold": bool(card["gold"]),
        "edition": _int(card["edition"], "edition"),
        "card_set": str(card["card_set"]),
        "collection_power": _number(card["collection_power"], "collection_power"),
        "stake_status": None,
        "delegation_status": delegation_status(card),
        "raw": card,
    }


def _matches(card: dict[str, Any], query: QuerySpec) -> bool:
    if query.edition is not None and card["edition"] != query.edition:
        return False
    if query.foil != "any" and ((query.foil == "gold") != card["gold"]):
        return False
    if query.stake_status != "any" and card["stake_status"] != query.stake_status:
        return False
    if query.delegation_status != "any" and card["delegation_status"] != query.delegation_status:
        return False
    return True


def _sort(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    if key == "name":
        return sorted(rows, key=lambda row: (row["name"].casefold(), row["card_detail_id"]))
    if key in {"bcx", "instances", "power"}:
        field = {"bcx": "total_bcx", "instances": "matched_instances", "power": "total_collection_power"}[key]
        return sorted(rows, key=lambda row: (-row[field], row["card_detail_id"]))
    return sorted(rows, key=lambda row: row["card_detail_id"])


def run_query(query: QuerySpec, client: SplinterlandsClient | None = None, cache: JsonCache | None = None, refresh: bool = False, offline: bool = False, details_ttl: int = 86400, collection_ttl: int = 300) -> dict[str, Any]:
    client = client or SplinterlandsClient()
    cache = cache or JsonCache()
    collection_key = f"collection_{safe_name(query.player)}"
    details, details_info = cache.get_or_fetch("card_details", details_ttl, offline, refresh, client.details)
    collection, collection_info = cache.get_or_fetch(collection_key, collection_ttl, offline, refresh, lambda: client.collection(query.player))
    if not isinstance(collection, dict) or not isinstance(collection.get("cards"), list):
        raise SchemaError("cached collection response has invalid shape")
    if not isinstance(details, list):
        raise SchemaError("cached details response has invalid shape")
    cards = [normalize_card(item) if isinstance(item, dict) else (_ for _ in ()).throw(SchemaError("collection cards must be objects")) for item in collection["cards"]]
    uids = [card["uid"] for card in cards]
    if len(set(uids)) != len(uids):
        raise SchemaError("collection response contains duplicate UIDs")
    for card in cards:
        card["stake_status"] = stake_status(card["raw"], query.as_of)
    universe, resolution, warnings = resolve_universe(details, query, [card["raw"] for card in cards])
    meta = {detail_id(item): item for item in universe}
    selected_ids = set(meta)
    matched = [card for card in cards if card["card_detail_id"] in selected_ids and _matches(card, query)]
    rows: dict[int, dict[str, Any]] = {}
    for card_id, detail in meta.items():
        rows[card_id] = {
            "card_detail_id": card_id,
            "name": str(detail.get("name", f"Card {card_id}")),
            "edition": detail.get("edition", query.edition),
            "api_card_set": detail.get("card_set", "land" if query.set_name else None),
            "rarity": detail.get("rarity"),
            "color": detail.get("color"),
            "type": detail.get("type"),
            "present": False,
            "zero_filled": True,
            "matched_instances": 0,
            "total_bcx": 0,
            "total_collection_power": 0,
            "foil": {"regular": {"instances": 0, "bcx": 0, "collection_power": 0}, "gold": {"instances": 0, "bcx": 0, "collection_power": 0}},
            "status": {"active": 0, "historical": 0, "unstaked": 0, "unknown": 0},
            "delegation": {"none": 0, "present": 0},
        }
    for card in matched:
        row = rows[card["card_detail_id"]]
        row["present"] = True
        row["zero_filled"] = False
        row["matched_instances"] += 1
        row["total_bcx"] += card["bcx"]
        row["total_collection_power"] += card["collection_power"]
        foil = "gold" if card["gold"] else "regular"
        row["foil"][foil]["instances"] += 1
        row["foil"][foil]["bcx"] += card["bcx"]
        row["foil"][foil]["collection_power"] += card["collection_power"]
        row["status"][card["stake_status"]] += 1
        row["delegation"][card["delegation_status"]] += 1
    result_rows = list(rows.values()) if query.include_zero else [row for row in rows.values() if row["present"]]
    result_rows = _sort(result_rows, query.sort)
    total_bcx = sum(row["total_bcx"] for row in result_rows)
    total_power = sum(row["total_collection_power"] for row in result_rows)
    sources = {"collection": details_info.__dict__, "details": collection_info.__dict__}
    return {
        "schema_version": 1,
        "query": {"player": query.player, "set": query.set_name, "api_card_set": query.api_card_set, "id_range": query.id_range, "edition": query.edition, "include_zero": query.include_zero, "foil": query.foil, "stake_status": query.stake_status, "delegation_status": query.delegation_status, "as_of": iso(query.as_of), "sort": query.sort},
        "sources": {"collection_url": "https://api.splinterlands.com/cards/collection/{player}", "details_url": "https://api.splinterlands.com/cards/get_details", "cache": sources},
        "set_resolution": resolution,
        "summary": {"source_instances": len(cards), "matched_instances": len(matched), "matched_distinct_cards": sum(1 for row in result_rows if row["present"]), "universe_cards": len(universe), "total_bcx": total_bcx, "total_collection_power": json_number(total_power), "completion": {"present": sum(1 for row in rows.values() if row["present"]), "total": len(universe), "percent": round(100 * sum(1 for row in rows.values() if row["present"]) / len(universe), 2)}},
        "cards": result_rows,
        "warnings": warnings,
        "generated_at": iso(__import__("datetime").datetime.now(__import__("datetime").timezone.utc)),
    }

