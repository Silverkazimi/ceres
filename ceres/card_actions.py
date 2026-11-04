""": opaque card-copy identity and conservative, read-only action eligibility.

The secret lives only for this server process. A ref is account-bound and cannot be
reversed into a UID. Eligibility always compares it with a new collection read;
this module never builds a transaction or mutates a card.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Any, Mapping

from .candidates import _is_soulbound, cooldown_state
from .metrics import rental_direction

_SECRET = secrets.token_bytes(32)
# maintainer decisions 2026-10-28: Phoenix Ash is left out entirely; delegation is split into the maintainer's
# "Recall delegation" and the borrower's "Return card" (paid rentals keep the market flow); each action is released
# only once its operation is verified, the rest stay greyed with a one-line reason (Burn for DEC until its reward
# handling is proven on a live transaction).
ACTIONS = ("stake", "unstake", "plot_link", "delegate", "recall_delegation", "return_card", "transfer", "burn")
UNVERIFIED = frozenset({"delegate", "recall_delegation", "return_card", "transfer", "burn"})
# Why each unverified operation is still greyed for a copy that otherwise qualifies (research.md, 2026-10-18).
UNVERIFIED_REASONS = {
    "delegate": "Not available yet — the delegation operation is not verified",
    "recall_delegation": "Not available yet — the recall operation is not verified",
    "return_card": "Not available yet — the return operation is not verified",
    "transfer": "Not available yet — the signing key for giving cards is not verified",
    "burn": "Not available yet — DEC reward handling is not proven on a live transaction",
}


def copy_ref(player: str, uid: str) -> str:
    """Stable within this process, even when snapshots are refreshed."""
    digest = hmac.new(_SECRET, f"card-copy\0{player}\0{uid}".encode(), hashlib.sha256).hexdigest()
    return f"cc1_{digest[:48]}"


def card_uid(card: Mapping[str, Any]) -> str | None:
    facts = card.get("facts") if isinstance(card.get("facts"), Mapping) else card
    uid = card.get("uid") or facts.get("uid")
    return uid if isinstance(uid, str) and uid else None


def resolve_ref(player: str, ref: str, cards: list[dict[str, Any]]) -> str | None:
    """Only resolve refs for copies present in the current server-side collection."""
    if not isinstance(ref, str) or not ref.startswith("cc1_") or len(ref) != 52:
        return None
    for card in cards:
        if not isinstance(card, dict):
            continue
        uid = card_uid(card)
        if uid and hmac.compare_digest(copy_ref(player, uid), ref):
            return uid
    return None


def _result(code: str | None = None, reason: str | None = None, *, ready_at: str | None = None, href: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"available": code is None, "reason_code": code, "reason": reason}
    if ready_at:
        result["ready_at"] = ready_at
    if href:
        result["href"] = href
    return result


def _unknown() -> dict[str, Any]:
    return _result("unknown", "Card status could not be checked")


def _date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _plot_label(plot: Any) -> str | None:
    parts = str(plot or "").split("-")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return "-".join(part.zfill(width) for part, width in zip(parts, (3, 2, 3)))


def reservation_refs(plans: list[dict[str, Any]], *, removed: Mapping[str, Any] | None = None,
                     plots: Mapping[str, str] | None = None) -> dict[str, str]:
    """ raw Land refs remain server-side; caller supplies local plans too.

    A replaced worker and a staged unstake are reserved along with the incoming card.
    Only an explicit removal tombstone releases a saved plan.
    """
    result = {}
    for plan in plans:
        if not isinstance(plan, dict) or plan.get("action") not in {"stake", "unstake", "replace"}:
            continue
        key = plan.get("key")
        if key is not None and isinstance(removed, Mapping) and str(key) in removed:
            continue
        # A board plan saved in browser storage names its deed, not its label; the label comes from the snapshot.
        label = _plot_label(plan.get("plot")) or (plots or {}).get(str(plan.get("deed_uid") or "")) or "a plot"
        for field in ("asset_ref", "evict_asset_ref"):
            ref = plan.get(field)
            if isinstance(ref, str) and ref:
                result[ref] = label
    return result


def unverified_eligibility(facts: Mapping[str, Any], player: str) -> dict[str, dict[str, Any]]:
    """Greyed reasons for the operations that are not verified yet. Never available.

    Only rules with documented evidence are stated as prohibitions (maintainer-only actions, soulbound copies cannot
    be delegated, given or burned for DEC, a paid rental is not a delegation); every other case reads as the
    operation itself not being verified, so no unverified game rule is presented as proven.
    """
    owner = facts.get("player") or facts.get("owner")
    if not isinstance(owner, str) or not owner:
        return {action: _unknown() for action in UNVERIFIED}
    owned = owner == player
    delegated = facts.get("delegated_to")
    rental = bool(facts.get("rental_type"))
    soulbound = _is_soulbound(dict(facts))
    out: dict[str, dict[str, Any]] = {}

    def pending(action: str) -> dict[str, Any]:
        return _result("unverified", UNVERIFIED_REASONS[action])

    if not owned:
        out["delegate"] = _result("not_owner", "Only the owner can delegate this card")
        out["transfer"] = _result("not_owner", "Only the owner can give this card")
        out["burn"] = _result("not_owner", "Only the owner can burn this card")
    elif soulbound:
        out["delegate"] = _result("soulbound", "Soulbound cards cannot be delegated")
        out["transfer"] = _result("soulbound", "Soulbound cards cannot be given")
        out["burn"] = _result("soulbound", "Soulbound cards cannot be burned for DEC")
    else:
        if rental and delegated and delegated != player:
            out["delegate"] = _result("rented", "Rented out — managed on the market")
        elif delegated and delegated != player:
            out["delegate"] = _result("delegated", f"Already delegated to {delegated}")
        else:
            out["delegate"] = pending("delegate")
        out["transfer"] = pending("transfer")
        out["burn"] = pending("burn")

    if not owned:
        out["recall_delegation"] = _result("not_owner", "Only the owner can recall a delegation")
    elif not delegated or delegated == player:
        out["recall_delegation"] = _result("not_delegated", "Not delegated to anyone")
    elif rental:
        out["recall_delegation"] = _result("rented", "Paid rental — managed on the market")
    else:
        out["recall_delegation"] = pending("recall_delegation")

    if owned:
        out["return_card"] = _result("not_borrowed", "Your own card — nothing to return")
    elif delegated != player:
        out["return_card"] = _result("not_borrowed", "Not delegated to you")
    elif rental:
        out["return_card"] = _result("rented", "Paid rental — managed on the market")
    else:
        out["return_card"] = pending("return_card")
    return out


def action_eligibility(card: Mapping[str, Any] | None, *, player: str, plots: Mapping[str, str],
                       now: datetime, reserved_plot: str | None = None) -> dict[str, dict[str, Any]]:
    """The action is available only where current evidence proves its basic prerequisite.

    Destination/slot validation and all writes belong to later steps. Unsupported
    contract families stay unavailable even when a copy looks otherwise usable.
    """
    if card is None:
        return {action: _unknown() for action in ACTIONS}
    facts = card.get("facts") if isinstance(card.get("facts"), Mapping) else card
    if not isinstance(facts, Mapping):
        return {action: _unknown() for action in ACTIONS}
    result = unverified_eligibility(facts, player)
    if reserved_plot:
        refusal = _result("reserved", f"Already staged for plot {reserved_plot}")
        result.update({action: dict(refusal) for action in ("stake", "unstake")})
        for action in UNVERIFIED:
            result[action].update({"reserved": True, "reservation_reason": refusal["reason"]})
        # Navigation to a currently occupied plot never consumes the card.
        result["plot_link"] = _result("not_staked", "Card is not currently staked")
        stake_ref = facts.get("stake_ref_uid")
        if stake_ref and not facts.get("stake_end_date") and plots.get(str(stake_ref)):
            result["plot_link"] = _result(href=f"/?plot={plots[str(stake_ref)]}")
        return result

    owner = facts.get("player") or facts.get("owner")
    if not isinstance(owner, str) or not owner:
        result.update({action: _unknown() for action in ("stake", "unstake", "plot_link")})
        return result
    delegated = facts.get("delegated_to")
    direction, _reason, _expiry = rental_direction(dict(facts), player)
    stake_ref = facts.get("stake_ref_uid")
    end_raw = facts.get("stake_end_date")
    end = _date(end_raw)
    if end_raw and end is None:
        result.update({action: _unknown() for action in ("stake", "unstake", "plot_link")})
        return result
    staked = bool(stake_ref) and not end_raw
    cooling = bool(end and end > now)
    active_plot = plots.get(str(stake_ref)) if staked else None
    if staked:
        result["stake"] = _result("staked", "Currently staked")
        result["unstake"] = _result() if active_plot else _unknown()
        result["plot_link"] = _result(href=f"/?plot={active_plot}") if active_plot else _unknown()
        return result
    result["unstake"] = _result("not_staked", "Card is not currently staked")
    result["plot_link"] = _result("not_staked", "Card is not currently staked")

    if cooling:
        result["stake"] = _result("cooldown", f"Recently unstaked — ready {end_raw}", ready_at=end_raw)
        return result
    survival_raw = facts.get("survival_date")
    survival = _date(survival_raw)
    if survival_raw and survival is None:
        result["stake"] = _unknown()
        return result
    if survival and survival > now:
        result["stake"] = _result("survival_lock", f"Survival cooldown — ready {survival_raw}", ready_at=survival_raw)
        return result
    if _is_soulbound(dict(facts)):
        result["stake"] = _result("soulbound", "Soulbound")
        return result
    if str(facts.get("edition")) in {"9", "11"}:
        result["stake"] = _result("not_land_card", "Soulkeep cards cannot work on Land")
        return result
    if owner != player and delegated != player and direction != "in":
        result["stake"] = _result("not_controlled", "No longer owned or controlled")
        return result
    if direction == "unknown":
        result["stake"] = _unknown()
        return result
    if direction == "out":
        result["stake"] = _result("rented", "Rental still active")
        return result
    if delegated and delegated != player:
        result["stake"] = _result("delegated", "Delegated to another player")
        return result
    if facts.get("market_id") and direction != "in":
        result["stake"] = _result("listed", "Listed on market")
        return result
    unlock_raw = facts.get("unlock_date")
    unlock = _date(unlock_raw)
    if unlock_raw and unlock is None:
        result["stake"] = _unknown()
        return result
    if unlock and unlock > now:
        result["stake"] = _result("security_lock", f"Locked until {unlock_raw}", ready_at=unlock_raw)
        return result
    stake_type = facts.get("stake_type_uid") or "STK-LND-WKR"
    state = cooldown_state("unstaked_apparent", end_raw, str(stake_type), now, survival_date=survival_raw)
    result["stake"] = _result() if state.get("ready") and state.get("state") == "ready" else _unknown()
    return result
