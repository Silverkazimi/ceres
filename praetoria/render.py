"""Deterministic Praetoria output renderers."""

from __future__ import annotations

import csv
import io
import json
from typing import Any


CSV_FIELDS = ["schema_version", "generated_at", "player", "scope_label", "card_detail_id", "name", "edition", "api_card_set", "rarity", "color", "type", "present", "zero_filled", "matched_instances", "total_bcx", "total_collection_power", "regular_instances", "regular_bcx", "regular_collection_power", "gold_instances", "gold_bcx", "gold_collection_power", "stake_active_instances", "stake_historical_instances", "stake_unstaked_instances", "stake_unknown_instances", "delegation_none_instances", "delegation_present_instances"]


def to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"


def to_csv(result: dict[str, Any]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in result["cards"]:
        writer.writerow({
            "schema_version": result["schema_version"], "generated_at": result["generated_at"], "player": result["query"]["player"], "scope_label": result["set_resolution"]["label"],
            "card_detail_id": row["card_detail_id"], "name": row["name"], "edition": row.get("edition"), "api_card_set": row.get("api_card_set"), "rarity": row.get("rarity"), "color": row.get("color"), "type": row.get("type"), "present": row["present"], "zero_filled": row["zero_filled"], "matched_instances": row["matched_instances"], "total_bcx": row["total_bcx"], "total_collection_power": row["total_collection_power"],
            "regular_instances": row["foil"]["regular"]["instances"], "regular_bcx": row["foil"]["regular"]["bcx"], "regular_collection_power": row["foil"]["regular"]["collection_power"], "gold_instances": row["foil"]["gold"]["instances"], "gold_bcx": row["foil"]["gold"]["bcx"], "gold_collection_power": row["foil"]["gold"]["collection_power"],
            "stake_active_instances": row["status"]["active"], "stake_historical_instances": row["status"]["historical"], "stake_unstaked_instances": row["status"]["unstaked"], "stake_unknown_instances": row["status"]["unknown"], "delegation_none_instances": row["delegation"]["none"], "delegation_present_instances": row["delegation"]["present"],
        })
    return stream.getvalue()


def to_table(result: dict[str, Any]) -> str:
    headers = ["ID", "Name", "BCX", "Instances", "Power", "Status"]
    values = [[str(row["card_detail_id"]), row["name"], str(row["total_bcx"]), str(row["matched_instances"]), str(row["total_collection_power"]), "owned" if row["present"] else "missing"] for row in result["cards"]]
    widths = [max(len(headers[i]), *(len(row[i]) for row in values)) if values else len(headers[i]) for i in range(len(headers))]
    line = " | ".join(headers[i].ljust(widths[i]) for i in range(len(headers)))
    divider = "-+-".join("-" * width for width in widths)
    body = [" | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) for row in values]
    summary = result["summary"]
    body.append("")
    body.append(f"Total BCX: {summary['total_bcx']} | Cards: {summary['matched_distinct_cards']}/{summary['universe_cards']} | Instances: {summary['matched_instances']}")
    if result.get("warnings"):
        body.append("Warnings: " + "; ".join(result["warnings"]))
    return "\n".join([line, divider, *body]) + "\n"

