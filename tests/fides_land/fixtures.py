"""Shared fixture builders for fides_land tests -- hand-built plan dicts shaped exactly like
`praetoria.land_reallocate.plan_to_dict()`'s output, so tests never need a live Praetoria
capture."""

from __future__ import annotations

from typing import Any


def card(
    name: str,
    *,
    base_pp: float = 1000.0,
    origin: str = "idle",
    asset_ref: str = "ref-x",
    card_uid: str | None = "UID-X",
    source_uid: str | None = None,
    bucket: str | None = None,
    stake_end_date: str | None = None,
    source_slot: int | None = None,
) -> dict[str, Any]:
    row = {
        "name": name, "color": "Red", "base_pp": base_pp, "current_harvest": 0.0,
        "origin": origin, "source_uid": source_uid, "source_rarity": None,
        "bucket": bucket, "dec_needed": 10000, "bcx": 10, "gold": False, "card_set": "chaos",
        "asset_ref": asset_ref, "source_slot": source_slot, "stake_end_date": stake_end_date,
    }
    if card_uid is not None:
        row["card_uid"] = card_uid
    return row


def evicted(name: str, *, base_pp: float = 500.0, asset_ref: str = "ref-evict", card_uid: str | None = "UID-EVICT", slot: int | None = 2) -> dict[str, Any]:
    row = {"name": name, "base_pp": base_pp, "current_harvest": 100.0, "asset_ref": asset_ref, "slot": slot}
    if card_uid is not None:
        row["card_uid"] = card_uid
    return row


def move(c: dict[str, Any], *, destination_uid: str, destination_plot: str = "41-1-1", new_harvest: float = 2000.0, evicted_row: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"card": c, "destination_plot": destination_plot, "destination_uid": destination_uid, "new_harvest": new_harvest, "evicted": evicted_row}


def backfill_row(c: dict[str, Any], *, plot_uid: str, plot: str = "41-1-2", harvest: float = 800.0, terrain: float = 0.1) -> dict[str, Any]:
    return {"plot": plot, "plot_uid": plot_uid, "card": c, "terrain": terrain, "harvest": harvest}


def plan_dict(moves: list[dict[str, Any]] | None = None, backfill: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"moves": moves or [], "backfill": backfill or [], "primary_net": 0.0, "backfill_gain": 0.0, "total_net": 0.0}
