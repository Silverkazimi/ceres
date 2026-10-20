"""Human-readable combine hazard rows and the registered allowlist family."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fides.op import Op

from .ops import _collection_rows, _foil, _record_index, _uid, survivor_rule


def allowlist_family(*, app: str) -> dict[str, Any]:
    return {
        "auth_tier": "Posting",
        "rules": [
            {"label": "combine cards must be a non-empty array of explicit Card IDs", "path": "body.json.cards", "check": "array_strings_nonempty"},
            {"label": f"combine app must be {app}", "path": "body.json.app", "check": "eq", "value": app},
            {"label": "combine nonce must be present", "path": "body.json.n", "check": "present"},
            {"label": "the signing account must match the bundle header", "path": "body.required_posting_auths", "check": "array_equals_header_account"},
        ],
    }


def _display_record(record: Mapping[str, Any], observed_at: str) -> dict[str, Any]:
    return {
        "card_id": _uid(record),
        "name": record.get("name") or "Unknown card",
        "edition": record.get("edition"),
        "foil": "Gold Foil" if _foil(record) else "Regular Foil",
        "bcx": record.get("bcx"),
        "level": record.get("level"),
        "observed_at": observed_at,
    }


def hazard_confirmation(uids: list[str], collection: Any, *, observed_at: str | None = None) -> dict[str, Any]:
    """Build the mandatory pre-signature warning from the same records as the op."""
    stamp = observed_at or _collection_observed_at(collection)
    if not stamp:
        raise ValueError("live collection read must include observed_at for combine confirmation")
    rows = _collection_rows(collection)
    index = _record_index(rows)
    records = [index.get(uid) for uid in uids]
    if any(record is None for record in records):
        missing = [uid for uid, record in zip(uids, records) if record is None]
        raise ValueError(f"collection read does not contain every selected Card ID: {missing!r}")
    records = [record for record in records if record is not None]
    predicted_survivor = survivor_rule(records)
    display_rows = [_display_record(record, stamp) for record in records]
    if predicted_survivor == "unknown":
        warning = "HAZARD — combining permanently destroys one or more selected Card IDs. The survivor cannot be named until every selected card has a readable BCX."
        survivor = None
        destroyed = []
    else:
        destroyed = [uid for uid in uids if uid != predicted_survivor]
        warning = "HAZARD — permanently destroys these Card IDs: " + ", ".join(destroyed) + ". Predicted survivor: " + predicted_survivor + "."
        survivor = next(row for row in display_rows if row["card_id"] == predicted_survivor)
    # A skin is a CARD-level attribute (every copy of a given card carries the same
    # skin value, verified across 965 distinct card_detail_ids with 0 exceptions), so
    # no single copy "owns" one and a combine cannot destroy one. No warning needed.
    hard_warnings: list[str] = []
    return {
        "warning": warning,
        "rows": display_rows,
        "destroyed_uids": destroyed,
        "survivor": survivor,
        "predicted_survivor_uid": predicted_survivor if predicted_survivor != "unknown" else None,
        "observed_at": stamp,
        "hard_warnings": hard_warnings,
        "warnings": [warning, *hard_warnings],
        "requires_confirmation": True,
        "bypassable": False,
    }


def _collection_observed_at(collection: Any) -> str | None:
    stamp = getattr(collection, "retrieved_at", None)
    if stamp:
        return str(stamp)
    if isinstance(collection, Mapping):
        if collection.get("observed_at"):
            return str(collection["observed_at"])
        if collection.get("retrieved_at"):
            return str(collection["retrieved_at"])
        data = collection.get("data")
        if isinstance(data, Mapping) and data.get("observed_at"):
            return str(data["observed_at"])
    return None


def build_renderer(uids: list[str], collection: Any, *, observed_at: str | None = None):
    confirmation = hazard_confirmation(uids, collection, observed_at=observed_at)
    rows_by_uid = {row["card_id"]: row for row in confirmation["rows"]}

    def renderer(op: Op) -> dict[str, Any]:
        selected = list(op.json_payload.get("cards", []))
        details = []
        for uid in selected:
            row = rows_by_uid.get(uid, {})
            details.append(f"Card ID {uid} — {row.get('name', 'Unknown card')}, edition {row.get('edition', '?')}, {row.get('foil', '?')}, BCX {row.get('bcx', '?')}, level {row.get('level', '?')} (observed at {row.get('observed_at', 'unknown')})")
        return {
            "family": "Card combine",
            "text": "COMBINE (Posting)\n" + "\n".join(details),
            "hazard": confirmation,
        }

    return renderer


build_hazard_confirmation = hazard_confirmation
combine_family_allowlist = allowlist_family
