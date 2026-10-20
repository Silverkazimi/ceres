"""Metadata universes and validated friendly set aliases."""

from __future__ import annotations

from typing import Any

from .errors import SchemaError

VERICO_IDS = tuple(range(985, 1039))

# The original 4-card "Land" set, edition 19: Tireless Farmhand (common, 866)
# Kinbound Druid (rare, 867), Toiling Ox (epic, 868), Undying Miner (legendary, 869).
# Lumina Lightweaver (1051) also belongs to edition 19; verified 2026-09-18.
# See docs/land-worker-abilities.md §1.
LAND_EDITION_19_IDS = (*range(866, 870), 1051)


def is_land_or_verico(card_detail_id: int | None) -> bool:
    """Known Land and Visions-of-Verico set identities, regardless of ability level.

    Ability capability discovery uses the metadata table separately.
    """
    if card_detail_id is None:
        return False
    return card_detail_id in LAND_EDITION_19_IDS or card_detail_id in VERICO_IDS


def detail_id(detail: dict[str, Any]) -> int:
    try:
        return int(detail.get("id", detail.get("card_detail_id")))
    except (TypeError, ValueError) as exc:
        raise SchemaError("card details contain a non-numeric id") from exc


def editions(detail: dict[str, Any]) -> set[int]:
    value = detail.get("editions", detail.get("edition", []))
    if isinstance(value, str):
        values = value.replace("[", "").replace("]", "").split(",")
    elif isinstance(value, list | tuple):
        values = value
    elif value is None:
        values = []
    else:
        values = [value]
    result: set[int] = set()
    for item in values:
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def resolve_universe(details: list[dict[str, Any]], query, collection_cards: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    by_id = {detail_id(item): item for item in details}
    if len(by_id) != len(details):
        raise SchemaError("card details contain duplicate ids")
    warnings: list[str] = []
    if query.set_name:
        if query.set_name.lower() not in {"verico", "visions-of-verico", "visions_of_verico"}:
            raise SchemaError(f"unknown set alias: {query.set_name}")
        ids = set(VERICO_IDS)
        missing = sorted(ids - set(by_id))
        if missing or len(ids) != 54:
            raise SchemaError("Verico metadata universe is incomplete")
        if any(21 not in editions(by_id[item]) for item in ids):
            raise SchemaError("Verico metadata failed edition 21 validation")
        observed = [card for card in collection_cards if int(card.get("card_detail_id", -1)) in ids]
        if observed and any(str(card.get("card_set", "")).lower() != "land" or int(card.get("edition", -1)) != 21 for card in observed):
            raise SchemaError("Verico collection evidence failed land/edition validation")
        if not observed:
            warnings.append("Verico card_set=land was not independently observed in this collection response")
        selected = [by_id[item] for item in VERICO_IDS]
        return selected, {"label": "visions-of-verico", "api_card_set": "land", "edition": 21, "id_min": 985, "id_max": 1038, "count": 54, "validated": True}, warnings
    if query.api_card_set:
        selected = [item for item in details if str(item.get("card_set", "")).lower() == query.api_card_set.lower()]
        if not selected:
            observed = {str(card.get("card_set", "")).lower() for card in collection_cards if card.get("card_set")}
            if query.api_card_set.lower() not in observed:
                raise SchemaError("requested API card_set is unavailable in current metadata")
            selected = [by_id[int(card["card_detail_id"])] for card in collection_cards if str(card.get("card_set", "")).lower() == query.api_card_set.lower() and int(card["card_detail_id"]) in by_id]
        return selected, {"label": query.api_card_set, "api_card_set": query.api_card_set, "count": len(selected), "validated": True}, warnings
    if query.id_range:
        start, end = query.id_range
        selected = [item for item in details if start <= detail_id(item) <= end]
        if not selected:
            raise SchemaError("requested card-detail range resolved to no metadata")
        return selected, {"label": f"ids-{start}-{end}", "id_min": start, "id_max": end, "count": len(selected), "validated": True}, warnings
    selected = list(details)
    if query.edition is not None:
        selected = [item for item in selected if query.edition in editions(item)]
    if not selected:
        raise SchemaError("requested edition resolved to no metadata")
    return selected, {"label": "all-details", "edition": query.edition, "count": len(selected), "validated": True}, warnings

