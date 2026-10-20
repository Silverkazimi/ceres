""": read-only card browser rows for the /cards page.

One row per owned copy, built from the stored board snapshot's card pool (no network
I/O, no chain read). Every Land fact the browser filters on is derived here once so the
page can filter, sort and group client-side after a single fetch.

Max-level Land base PP per print (`max_level_land_pp`): the Chaos Legion baseline at max
level (regular common 1,000 / rare 1,100 / epic 1,250 / legendary 1,500; gold foil
2,000 / 4,000 / 6,000 / 10,000, all measured) times the set multiplier
(library/cards-and-collection.md "Base PP set multipliers": Alpha 10x, Beta 5x,
Untamed 2x, Chaos 1x, Rebellion and Conclave 0.5x, Land/Verico 2x). Any other print
class -- gold arcane, black foils (foil codes 2/3/4), an unmapped card_set -- has no
formula and returns None; the page shows a dash. A copy that is already maxed and
reports a different, positive Land PP wins over the formula (`max_pp_source: "copy"`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .bcx_cap import resolve_bcx_max
from .card_actions import card_uid, copy_ref
from .board import card_splinters
from .candidates import COOLING_BUCKETS, _is_soulbound, cooldown_state
from .level_cap import resolve_level_thresholds
from .metrics import classify_idle_availability, land_ability_row, rental_direction

RARITY_NAMES = {1: "common", 2: "rare", 3: "epic", 4: "legendary"}

# Chaos Legion = 1.00x; measured at max level on the maintainer's collection (2026-08-21) and
# re-checked against every maxed copy of the 2026-10-14 snapshot ().
SET_MULTIPLIER = {
    "alpha": 10.0, "beta": 5.0, "untamed": 2.0, "chaos": 1.0,
    "rebellion": 0.5, "conclave": 0.5, "land": 2.0,
}
CHAOS_MAX_PP = {
    "regular": {"common": 1000.0, "rare": 1100.0, "epic": 1250.0, "legendary": 1500.0},
    "gold": {"common": 2000.0, "rare": 4000.0, "epic": 6000.0, "legendary": 10000.0},
}
STAKED_BUCKET = "stake_active_unconfirmed"


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def foil_code(facts: dict[str, Any]) -> int | None:
    """0 regular, 1 gold, 2 gold arcane, 3 black foil, 4 black foil arcane; None unknown."""
    foil = facts.get("foil")
    if isinstance(foil, bool):
        return int(foil)
    if isinstance(foil, (int, float)) or (isinstance(foil, str) and foil.strip().isdigit()):
        return int(float(foil))
    gold = facts.get("gold")
    if isinstance(gold, bool):
        return 1 if gold else 0
    return None


def max_level_land_pp(card_set: Any, rarity: Any, foil: Any, *, no_pp: bool = False) -> float | None:
    """Land base PP of this print at max level, or None when no measured formula covers it."""
    if no_pp:
        return 0.0
    multiplier = SET_MULTIPLIER.get(str(card_set or "").strip().lower())
    if multiplier is None or rarity not in CHAOS_MAX_PP["regular"]:
        return None
    if foil == 0:
        return CHAOS_MAX_PP["regular"][rarity] * multiplier
    if foil == 1:
        return CHAOS_MAX_PP["gold"][rarity] * multiplier
    return None


def plot_label(plot: Any) -> str | None:
    """The board's plot label ("065-10-088"), never a deed UID (formatPlotLabel in format.js)."""
    parts = str(plot or "").split("-")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return "-".join(part.zfill(width) for part, width in zip(parts, (3, 2, 3)))


def _plot_index(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index = {}
    for plot in report.get("plots") or []:
        facts = plot.get("facts") or {}
        label = plot_label(facts.get("plot"))
        if plot.get("deed_uid") is not None and label:
            index[str(plot["deed_uid"])] = {"label": label, "territory": facts.get("territory")}
    return index


def card_row(
    card: dict[str, Any],
    details_by_id: dict[int, dict[str, Any]],
    plots: dict[str, dict[str, Any]],
    *,
    player: str,
    now: datetime,
) -> dict[str, Any]:
    facts = card.get("facts") or {}
    bucket = (card.get("classification") or {}).get("bucket")
    detail_id = facts.get("card_detail_id")
    detail = details_by_id.get(detail_id, {}) if detail_id is not None else {}
    rarity_raw = detail.get("rarity", facts.get("rarity"))
    rarity = RARITY_NAMES.get(rarity_raw) if isinstance(rarity_raw, int) else (str(rarity_raw).lower() if rarity_raw else None)
    foil = foil_code(facts)
    gold = foil in (1, 2)
    card_set = facts.get("card_set") or detail.get("card_set")
    edition = facts.get("edition")
    level = facts.get("level")
    bcx = _number(facts.get("bcx"))
    pp = _number(facts.get("land_base_pp"))
    no_pp = bool(detail.get("no_pp"))

    bcx_max = resolve_bcx_max(detail_id, edition, rarity, gold, card_set, foil)
    thresholds = resolve_level_thresholds(detail_id, edition, rarity, gold, card_set)
    if foil in (3, 4):
        maxed: bool | None = True  # black foils are always max level (bcx_cap sentinel)
    elif bcx_max is None or bcx is None:
        maxed = None
    else:
        maxed = bcx >= bcx_max

    max_pp = max_level_land_pp(card_set, rarity, foil, no_pp=no_pp)
    max_source = "formula" if max_pp is not None else None
    if maxed and pp is not None and pp > 0 and pp != max_pp:
        # A maxed copy is the measurement: trust it over the formula (and cover the
        # always-maxed black foils, which have no formula of their own).
        max_pp, max_source = pp, "copy"

    available, reason = classify_idle_availability(facts, now, player)
    direction, _rental_reason, rental_expiry = rental_direction(facts, player)
    soulbound = _is_soulbound(facts)  # Soulkeep (editions 9, 11) is excluded from Land, not soulbound

    plot = None
    ready_at = None
    if bucket == STAKED_BUCKET:
        status = "staked"
        plot = plots.get(str(facts.get("stake_ref_uid")))
    elif bucket in COOLING_BUCKETS:
        cooldown = cooldown_state(bucket, facts.get("stake_end_date"), facts.get("stake_type_uid") or "STK-LND-WKR", now,
                                  survival_date=facts.get("survival_date"))
        ready_at = cooldown.get("ready_at")
        status = "cooling" if not cooldown.get("ready") else "idle"
        plot = plots.get(str(facts.get("stake_ref_uid"))) if status == "cooling" else None
    else:
        status = "idle"
        survival = cooldown_state(bucket or "unstaked_apparent", None, "STK-LND-WKR", now, survival_date=facts.get("survival_date"))
        if survival.get("state") == "survival_locked":
            status, ready_at = "cooling", survival.get("ready_at")
            reason = reason or "dead from Survival mode"

    abilities = land_ability_row(detail_id, level, details_by_id) or []
    return {
        "asset_ref": copy_ref(player, card_uid(card)) if card_uid(card) else None,
        "card_detail_id": detail_id,
        "name": detail.get("name") or facts.get("name") or "Unknown card",
        "edition": edition,
        "card_set": card_set,
        "rarity": rarity,
        "foil": foil,
        "gold": gold,
        "level": level,
        "max_level": len(thresholds) if thresholds else None,
        "bcx": bcx,
        "bcx_max": bcx_max,
        "land_base_pp": pp,
        "max_level_pp": max_pp,
        "max_pp_source": max_source,
        "maxed": maxed,
        "no_pp": no_pp,
        "splinters": card_splinters(detail, facts),
        "available": bool(available),
        "unavailable_reason": None if available else reason,
        "soulbound": soulbound,
        "rented_in": direction == "in",
        "rental_expires_at": rental_expiry if direction == "in" else None,
        "status": status,
        "plot": (plot or {}).get("label"),
        "plot_territory": (plot or {}).get("territory"),
        "ready_at": ready_at,
        "land_abilities": abilities,
        "skin": facts.get("skin"),
    }


def build_card_rows(report: dict[str, Any], details_by_id: dict[int, dict[str, Any]], *, player: str, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    plots = _plot_index(report)
    return [card_row(card, details_by_id, plots, player=player, now=now) for card in report.get("cards") or [] if isinstance(card, dict)]
