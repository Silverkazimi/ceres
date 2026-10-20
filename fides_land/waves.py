"""fides_land/waves.py -- three-wave cooldown-gated wave assignment (plan §4).

Wave 1 (UNSTAKE): every UNSTAKE[*] op -- no gate, broadcast immediately.
Wave 2 (STAKE NOW): stakes of cooldown-clear cards -- idle `unstaked_apparent` cards (never
had a timer) plus `stake_expired_or_historical` cards already past their cooldown, plus
every backfill that qualifies the same way. Gated on wave 1 being domain-verified, since the
destination slots wave 2 targets are the ones wave 1 vacates.
Wave 3 (STAKE DEFERRED): stakes of every card wave 1 unstakes -- a same-card round trip is
*exactly* the case the ~3-day cooldown targets, and is blocked regardless of anything else --
plus any idle card still cooling. Gated on a per-card `ready_at`, human-triggered, never
scheduled (see `fides_land/verify.py` for the live gate check).

This module calls `fides_land.ops` for op construction and `fides_land.slots` for slot
assignment; it adds no op-construction logic of its own, only the wave split.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fides.op import Op

from . import ops as land_ops
from .intent import StakeIntent
from .slots import assign_slots

DEFAULT_COOLDOWN_HOURS = 72.0
DEFAULT_MARGIN_HOURS = 6.0

# Current official unstaking guide (checked 2026-09-18): items are immediately usable.
# https://support.splinterlands.com/hc/en-us/articles/17980738551572
# Titles remain subject to separate inventory unlock restrictions, not worker cooldown.
_CONFIRMED_NON_COOLING_STAKE_TYPES = frozenset({land_ops.STAKE_TYPE_POWER_CORE, land_ops.STAKE_TYPE_TOTEM, land_ops.STAKE_TYPE_RUNI, land_ops.STAKE_TYPE_TITLE})
_NON_COOLING_STAKE_TYPES = _CONFIRMED_NON_COOLING_STAKE_TYPES


def cooldown_class(stake_type_uid: str) -> str:
    """Return the cooldown class, failing closed for unknown stake types."""
    if stake_type_uid in _NON_COOLING_STAKE_TYPES:
        return "items"
    # Being wrong toward cooling costs one needless wave; being wrong the other way ships a
    # batch that assumes an instant restake and leaves the user half-applied.
    return "cards"


def stake_type_is_cooling(stake_type_uid: str) -> bool:
    """Whether an asset class must wait for the card cooldown."""
    return cooldown_class(stake_type_uid) == "cards"


def classify_intents(intents: list[StakeIntent] | tuple[StakeIntent, ...]) -> dict[str, list[StakeIntent]]:
    """Partition intent operations into immediate item and cooling card waves.

    Unstakes remain in the first wave. Item stakes are immediate; card stakes are deferred
    because this intent-only API has no source cooldown evidence to clear them safely.
    """
    waves: dict[str, list[StakeIntent]] = {"unstake": [], "stake_now": [], "stake_deferred": []}
    for intent in intents:
        land_ops._validate_intent(intent)
        if intent.direction == "unstake":
            waves["unstake"].append(intent)
        elif intent.direction == "stake":
            target = "stake_deferred" if stake_type_is_cooling(intent.stake_type_uid) else "stake_now"
            waves[target].append(intent)
        else:
            raise land_ops.LandOpError(f"validated intent direction has no wave assignment: {intent.direction!r}")
    return waves


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def card_ready_now(
    card: dict[str, Any],
    *,
    now: datetime,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    margin_hours: float = DEFAULT_MARGIN_HOURS,
) -> tuple[bool, datetime | None]:
    """Is this idle-origin card's stake cooldown-clear right now? `unstaked_apparent` cards
    never had a timer. `stake_expired_or_historical` cards gate on
    `stake_end_date + margin`. Returns `(ready, ready_at)` -- `ready_at` is `None`
    when there was never a cooldown to wait out. Missing `stake_end_date` evidence for a
    supposedly-expired card is treated conservatively as NOT ready (never optimistically
    assumed clear)."""
    if card.get("origin") != "idle":
        raise ValueError("card_ready_now only applies to idle-origin cards")
    if card.get("bucket") == "unstaked_apparent":
        return True, None
    end = _parse_time(card.get("stake_end_date"))
    if end is None:
        return False, None
    # The displayed cooldown END is `stake_end_date`; a margin belongs only where an action is initiated, never in a figure presented to the user as when the card is ready.
    ready_at = end + timedelta(hours=margin_hours)
    return now >= ready_at, ready_at


class WaveSplit:
    def __init__(self) -> None:
        self.unstake: dict[str, list[dict[str, Any]]] = {}
        self.stake_now: dict[str, list[dict[str, Any]]] = {}
        self.stake_deferred: dict[str, list[dict[str, Any]]] = {}
        self.ready_at_by_ref: dict[str, str | None] = {}


def classify_plan(
    plan_dict: dict[str, Any],
    *,
    now: datetime,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    margin_hours: float = DEFAULT_MARGIN_HOURS,
) -> WaveSplit:
    """Splits `plan_dict`'s `moves[]`/`backfill[]` into the three waves' stake/unstake
    accumulation targets (plan §4's table)."""
    split = WaveSplit()

    for move in plan_dict.get("moves", []):
        card = move["card"]
        dest = move["destination_uid"]

        evicted = move.get("evicted")
        if evicted is not None:
            split.unstake.setdefault(dest, []).append(land_ops.card_entry(evicted, context="an eviction"))

        if card.get("origin") == "staked":
            source = card.get("source_uid")
            if not source:
                raise land_ops.LandOpError(f"staked-origin card is missing source_uid: {card!r}")
            split.unstake.setdefault(source, []).append(land_ops.card_entry(card, context="a staked-origin unstake"))
            # same-card round trip -- always wave 3, regardless of anything else
            split.stake_deferred.setdefault(dest, []).append(land_ops.card_entry(card, context="a round-trip stake"))
            continue

        ready, ready_at = card_ready_now(card, now=now, cooldown_hours=cooldown_hours, margin_hours=margin_hours)
        split.ready_at_by_ref[card.get("asset_ref")] = ready_at.isoformat() if ready_at else None
        target = split.stake_now if ready else split.stake_deferred
        target.setdefault(dest, []).append(land_ops.card_entry(card, context="an idle-origin stake"))

    for row in plan_dict.get("backfill", []):
        card = row["card"]
        plot_uid = row["plot_uid"]
        ready, ready_at = card_ready_now(card, now=now, cooldown_hours=cooldown_hours, margin_hours=margin_hours)
        split.ready_at_by_ref[card.get("asset_ref")] = ready_at.isoformat() if ready_at else None
        target = split.stake_now if ready else split.stake_deferred
        target.setdefault(plot_uid, []).append(land_ops.card_entry(card, context="a backfill stake"))

    return split


def build_wave_ops(
    split: WaveSplit,
    *,
    account: str,
    assignments: list[dict[str, Any]],
    capacity_by_deed: dict[str, int],
    app: str,
) -> dict[str, list[Op]]:
    """Assigns slots ONCE across the combined wave-2 + wave-3 stake targets (both waves draw
    on the same deed capacity that wave 1's unstakes free up -- a slot handed to a wave-3
    card must not collide with one a wave-2 card already claimed), then emits each wave's
    ops separately. Returns `{"unstake": [...], "stake_now": [...], "stake_deferred": [...]}`
    -- any wave with no ops is an empty list (a plan with nothing cooling has no wave 3)."""
    combined_stake: dict[str, list[dict[str, Any]]] = {}
    for deed, cards in split.stake_now.items():
        combined_stake.setdefault(deed, []).extend(cards)
    for deed, cards in split.stake_deferred.items():
        combined_stake.setdefault(deed, []).extend(cards)

    assign_slots(combined_stake, split.unstake, assignments, capacity_by_deed)
    # assign_slots mutates each card dict in place, adding "slot" -- split.stake_now and
    # split.stake_deferred hold the SAME dict objects (they were extended by reference into
    # combined_stake above), so they already carry the assigned slot too.

    return {
        "unstake": land_ops.to_ops({}, split.unstake, account=account, app=app),
        "stake_now": land_ops.to_ops(split.stake_now, {}, account=account, app=app),
        "stake_deferred": land_ops.to_ops(split.stake_deferred, {}, account=account, app=app),
    }
