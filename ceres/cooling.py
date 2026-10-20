"""Pure projection of one live capture for the account-wide Cooling read.

 phase 1 (stage planner): the Survival term is
passed through, ready cards are counts (not rows), and cooling rows are grouped by their ready time. The route binds
this to the page load's own capture ().
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .bcx_cap import resolve_bcx_max
from .board import RUNI_CARD_DETAIL_ID, _num, _rarity_name, _splinter_name
from .candidates import COOLING_BUCKETS, IDLE_BUCKETS, cooldown_state
from .level_cap import resolve_level_thresholds
from .metrics import classify_idle_availability

READY_WINDOW_DAYS = 7
_COOLING_STATES = {"cooling", "unknown", "unverified"}


def _stake_type_uid(facts: dict[str, Any]) -> str:
    if facts.get("card_detail_id") == RUNI_CARD_DETAIL_ID:
        return "STK-LND-RUNI"
    return facts.get("stake_type_uid") or "STK-LND-WKR"


def _row(
    card: dict[str, Any],
    facts: dict[str, Any],
    detail: dict[str, Any],
    cooldown: dict[str, Any],
    available: bool,
    blocked_reason: str | None,
) -> dict[str, Any]:
    card_detail_id = facts.get("card_detail_id")
    rarity = _rarity_name(detail.get("rarity"))
    return {
        "asset_ref": card.get("asset_ref"),
        "uid": facts.get("uid"),
        "name": detail.get("name") or facts.get("name") or "Unknown card",
        "card_detail_id": card_detail_id,
        "card_set": facts.get("card_set"),
        "splinter": _splinter_name(detail.get("color")),
        "rarity": rarity,
        "edition": facts.get("edition"),
        "foil": facts.get("foil"),
        "bcx": facts.get("bcx"),
        "bcx_max": resolve_bcx_max(
            card_detail_id,
            facts.get("edition"),
            rarity,
            facts.get("gold"),
            facts.get("card_set"),
            facts.get("foil"),
        ),
        "level_bcx_thresholds": resolve_level_thresholds(
            card_detail_id,
            facts.get("edition"),
            rarity,
            facts.get("gold"),
            facts.get("card_set"),
        ),
        "gold": facts.get("gold"),
        "level": facts.get("level"),
        "base_pp": _num(facts.get("land_base_pp")),
        "skin": facts.get("skin"),
        "bucket": (card.get("classification") or {}).get("bucket"),
        "available": available,
        "blocked_reason": blocked_reason,
        "stake_end_date": facts.get("stake_end_date"),
        "cooling_until": facts.get("stake_end_date"),
        "dec_stake_needed": facts.get("land_dec_stake_needed"),
        "cooldown": cooldown,
    }


def _sort_key(row: dict[str, Any]) -> tuple[int, str]:
    ready_at = (row.get("cooldown") or {}).get("ready_at")
    return (0, str(ready_at)) if ready_at else (1, "")


def build_cooling_view(
    report: dict[str, Any],
    details_by_id: dict[int, dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Derive the Cooling-only view from one captured report.

    The empty readiness fields are an intentional seam for R-96: until its
    survival term lands, this endpoint must not call any card Ready or eligible
    for restaking. ``survival_locked`` is accepted from the shared classifier
    without implementing a second survival rule here.
    """
    generated_at = report.get("generated_at")
    result = {
        "generated_at": generated_at,
        "cooling": [],
        "groups": [],
        "locked": [],
        "survival_locked_count": 0,
        "ready_recent_count": 0,
        "ready_older_count": 0,
        "loaded": bool(report.get("coverage", {}).get("assets", {}).get("complete")),
    }
    if not result["loaded"]:
        return result

    now = now or datetime.now(timezone.utc)
    buckets = set(IDLE_BUCKETS) | set(COOLING_BUCKETS)
    player = (report.get("query") or {}).get("player")
    if not isinstance(report.get("cards"), list):
        try:
            from .capture import load_card_pool
            report = load_card_pool(report)
        except LookupError as exc:
            result["loaded"] = False
            result["reason"] = str(exc)
            return result
    cards = report.get("cards", [])
    if not isinstance(cards, list):
        return result

    for card in cards:
        if not isinstance(card, dict):
            continue
        classification = card.get("classification")
        facts = card.get("facts")
        if not isinstance(classification, dict) or not isinstance(facts, dict):
            continue
        bucket = classification.get("bucket")
        if bucket not in buckets:
            continue
        card_detail_id = facts.get("card_detail_id")
        if card_detail_id == RUNI_CARD_DETAIL_ID:
            continue
        if not facts.get("stake_end_date"):
            # Dead from Survival with no unstake cooldown: counted, never a row (no end date to show).
            no_end = cooldown_state(bucket, None, _stake_type_uid(facts), now, survival_date=facts.get("survival_date"))
            if no_end.get("state") == "survival_locked":
                # listed too, so the Planner can say WHICH cards are dead from Survival.
                result["survival_locked_count"] += 1
                detail = details_by_id.get(card_detail_id, {}) if card_detail_id is not None else {}
                available, blocked_reason = classify_idle_availability(facts, now, player)
                result["locked"].append(_row(card, facts, detail, no_end, available, blocked_reason))
            continue
        cooldown = cooldown_state(bucket, facts.get("stake_end_date"), _stake_type_uid(facts), now,
                                  survival_date=facts.get("survival_date"))
        state = cooldown.get("state")
        if state == "staked":
            continue
        if state == "ready":
            ready_at = _parse(cooldown.get("ready_at") or facts.get("stake_end_date"))
            if ready_at is not None and now - ready_at <= timedelta(days=READY_WINDOW_DAYS):
                result["ready_recent_count"] += 1
            else:
                result["ready_older_count"] += 1
            continue
        detail = details_by_id.get(card_detail_id, {}) if card_detail_id is not None else {}
        available, blocked_reason = classify_idle_availability(facts, now, player)
        row = _row(card, facts, detail, cooldown, available, blocked_reason)
        if state == "survival_locked":
            result["locked"].append(row)
        elif state in _COOLING_STATES:
            result["cooling"].append(row)

    result["cooling"].sort(key=_sort_key)
    result["locked"].sort(key=_sort_key)
    groups: dict[str, list[str]] = {}
    for row in result["cooling"]:
        groups.setdefault(str((row.get("cooldown") or {}).get("ready_at") or ""), []).append(row["asset_ref"])
    result["groups"] = [{"ready_at": ready_at or None, "count": len(refs), "asset_refs": refs} for ready_at, refs in groups.items()]
    return result


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
