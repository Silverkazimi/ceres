""": pure projection of explicit worker-card upgrade inputs."""

from __future__ import annotations

from decimal import Decimal
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from fides_cards.ops import survivor_rule

from .level_cap import level_for_bcx, resolve_level_thresholds
from .worker_upgrade import (
    _bcx, _bcx_max, _blocked_reasons, _facts, _future, _identity,
    _non_combinable_foil, _uid,
)


class ChainInputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def live_ready_at(verdict: Mapping[str, Any] | None) -> str | None:
    """Only an observed game verdict may supply an input's cooldown end."""
    if not isinstance(verdict, Mapping):
        return None
    values = [verdict.get("ready_at")] if verdict.get("ready_at") else [verdict.get("stake_end_date"), verdict.get("survival_date")]
    stamps = []
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        stamps.append(stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc))
    if not stamps:
        return None
    stamp = max(stamps)
    return stamp.isoformat() if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc).isoformat()


def input_ready(input_row: Mapping[str, Any], verdict: Mapping[str, Any] | None = None) -> bool:
    """: free inputs are ready; all other readiness needs a live verdict."""
    if input_row.get("status", "active") != "active":
        return False
    if input_row.get("kind") == "free":
        return True
    if input_row.get("kind") == "staked" and not isinstance(input_row.get("unstake"), Mapping):
        return False
    if input_row.get("kind") == "staked" and not input_row["unstake"].get("tx_id"):
        return False
    return isinstance(verdict, Mapping) and verdict.get("ready") is True


def plan_ready_at(inputs: Sequence[Mapping[str, Any]]) -> str | None:
    """The last live end wins; an unknown active input keeps the plan unready."""
    active = [row for row in inputs if row.get("status", "active") == "active"]
    timed = [row for row in active if row.get("kind") != "free"]
    if len(active) < 2 or any(row.get("ready_at") is None or (row.get("kind") == "staked" and not (row.get("unstake") or {}).get("tx_id")) for row in timed):
        return None
    values = [str(row["ready_at"]) for row in timed]
    if not values:
        return None
    try:
        return max(values, key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, TypeError):
        return None


def resolve_upgrade_anchor(records: Sequence[Mapping[str, Any]], reference: str) -> str:
    """: accept a unique opaque board reference without exposing card IDs."""
    rows = [row for record in records if (row := _facts(record))]
    if any(_uid(row) == reference for row in rows):
        return reference
    matches = [row for row in rows if reference and row.get("asset_ref") == reference]
    if len(matches) > 1:
        raise ChainInputError("anchor_ambiguous", "the selected card reference is ambiguous")
    if len(matches) != 1 or not _uid(matches[0]):
        raise ChainInputError("anchor_missing", "the selected card is absent")
    return _uid(matches[0])


def upgrade_inputs(
    records: Sequence[Mapping[str, Any]], anchor_uid: str, *, account: str,
    details_by_id: Mapping[Any, Any] | None = None,
    sources_by_uid: Mapping[str, Mapping[str, Any]] | None = None,
    verdicts_by_uid: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Classify all copies of one printing from an already captured collection."""
    anchor_uid = resolve_upgrade_anchor(records, anchor_uid)
    rows = [_facts(row) for row in records]
    anchor = next((row for row in rows if row and _uid(row) == anchor_uid), None)
    if anchor is None or _identity(anchor) is None:
        raise ChainInputError("anchor_missing", "the selected card is absent or has incomplete printing facts")
    details_by_id = details_by_id or {}
    sources_by_uid = sources_by_uid or {}
    verdicts_by_uid = verdicts_by_uid or {}
    result = []
    for row in rows:
        if row is None or _identity(row) != _identity(anchor):
            continue
        uid = _uid(row)
        bcx = _bcx(row)
        maximum = _bcx_max(row, details_by_id)
        end = row.get("stake_end_date")
        unstaking = bool(row.get("is_unstaking") or row.get("unstaking"))
        cooling = bool(end and _future(end))
        staked = bool(row.get("stake_ref_uid") and end in (None, "") and not unstaking)
        kind = "cooling" if cooling else "staked" if staked else "free"
        reasons = [reason for reason in _blocked_reasons(row) if reason not in {"card is Staked on Land", "card is Unstaking from Land"}]
        if unstaking and not cooling:
            reasons.append("card is Unstaking from Land")
        owner = row.get("player") or row.get("owner")
        if owner is not None and str(owner).lower() != account.lower():
            reasons.append("card is not owned by this account")
        if row.get("delegated_by") or row.get("delegated_by_player"):
            reasons.append("card is delegated in")
        if row.get("rented_by") or row.get("rented_to") or row.get("rental_type"):
            reasons.append("card is rented")
        if row.get("card_locked") or row.get("is_locked") or row.get("lock_days") or row.get("locked"):
            reasons.append("This card has a card lock, so it is not included yet.")
        if row.get("survival_mode_disabled") or _future(row.get("survival_date")):
            reasons.append("card is unavailable in Survival")
        if _non_combinable_foil(row):
            reasons.append("card is a Black Foil or Arcane Variant")
        # max CC caps production, not stored CC (cards-and-collection).
        if row.get("stake_type_uid") not in (None, "STK-LND-WKR"):
            reasons.append("card is not a Land worker")
        if not uid:
            reasons.append("Card ID is not present in the snapshot")
        if bcx is None:
            reasons.append("CC is not present in the snapshot")
        source = sources_by_uid.get(uid or "") if staked else None
        if staked and source is None:
            source = {"deed_uid": row.get("stake_ref_uid"), "slot_index": row.get("slot_index")}
        result.append({
            "uid": uid, "asset_ref": row.get("asset_ref"), "bcx": bcx,
            "bcx_max": maximum, "kind": kind, "source": dict(source) if source else None,
            "ready_at": live_ready_at(verdicts_by_uid.get(uid or "")) if cooling else None,
            "eligible": not reasons, "reasons": list(dict.fromkeys(reasons)),
        })
    return result


def expected_card(
    selected: Sequence[Mapping[str, Any]], *, details_by_id: Mapping[Any, Any] | None = None,
    computed_at: str | None = None, capture_generated_at: str | None = None,
) -> dict[str, Any]:
    """Project exact CC, level and capped base PP for explicitly selected copies."""
    if len(selected) < 2 or any(_bcx(row) is None or not _uid(row) for row in selected):
        raise ChainInputError("inputs_incomplete", "at least two selected cards need Card IDs and CC")
    identities = {_identity(row) for row in selected}
    if None in identities or len(identities) != 1:
        raise ChainInputError("printing_mismatch", "selected cards must share card, edition and foil")
    details = details_by_id or {}
    first = selected[0]
    maximum = _bcx_max(first, details)
    detail_id = first.get("card_detail_id")
    detail = details.get(detail_id) or details.get(str(detail_id)) or {}
    if not detail and str(detail_id).isdigit():
        detail = details.get(int(detail_id)) or {}
    rarity = detail.get("rarity", first.get("rarity"))
    rarity = {1: "common", 2: "rare", 3: "epic", 4: "legendary"}.get(rarity, rarity)
    thresholds = resolve_level_thresholds(first.get("card_detail_id"), first.get("edition"), rarity,
                                           _identity(first)[2], first.get("card_set"))
    if maximum is None or not thresholds:
        raise ChainInputError("level_unknown", "maximum CC or level thresholds are unavailable")
    total = sum(_bcx(row) for row in selected)
    # A08: highest CC, lexical UID tie-break; decimal rate avoids order/binary drift.
    rate = next((Decimal(str(row["land_base_pp"])) / min(Decimal(str(_bcx(row))), Decimal(str(maximum)))
                 for row in sorted(selected, key=lambda row: (-_bcx(row), _uid(row))) if canonical_fact(row.get("land_base_pp")) and _bcx(row)), None)
    if rate is None:
        raise ChainInputError("base_pp_unknown", "no selected card has a usable base PP rate")
    survivor = survivor_rule(selected)
    if survivor == "unknown":
        raise ChainInputError("survivor_unknown", "the survivor cannot be predicted")
    return {
        "survivor_uid": survivor, "bcx": total, "bcx_max": maximum,
        "level": level_for_bcx(thresholds, total),
        "base_pp": float(rate * Decimal(str(min(total, maximum)))), "base_pp_capped": total > maximum,
        "computed_at": computed_at, "capture_generated_at": capture_generated_at,
    }


def canonical_fact(value):
    """ B01/B02: finite decimal facts only; missing/malformed stays unknown."""
    import re
    import math
    if isinstance(value, str):
        if len(value) > 64 or not re.fullmatch(r"\d+(?:\.\d+)?", value):
            return None
        value = float(value)
    return value if type(value) in (int, float) and 0 <= value <= 9007199254740991 and math.isfinite(value) else None
