"""Pure projection for the read-only worker-upgrade-options endpoint."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import math
from typing import Any

from .bcx_cap import resolve_bcx_max
from fides_cards.ops import survivor_rule


class WorkerUpgradeOptionsError(ValueError):
    """A snapshot cannot safely answer a worker-upgrade-options request."""

    def __init__(self, code: str, message: str, *, reasons: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.code = code
        self.reasons = reasons or []


def _facts(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, Mapping):
        return None
    facts = row.get("facts")
    result = dict(facts) if isinstance(facts, Mapping) else dict(row)
    for field in ("asset_ref", "uid", "card_uid", "classification"):
        if field not in result and row.get(field) is not None:
            result[field] = row[field]
    return result


def _foil(value: Any) -> bool | str | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"gold", "gold foil", "true", "1"}:
        return True
    if normalized in {"regular", "regular foil", "false", "0"}:
        return False
    return normalized or None


def _identity(record: Mapping[str, Any]) -> tuple[str, str, bool | str | None] | None:
    detail_id = record.get("card_detail_id")
    edition = record.get("edition")
    foil = record.get("foil")
    if foil is None:
        foil = record.get("gold", record.get("is_gold"))
    if detail_id is None or edition is None or foil is None:
        return None
    return str(detail_id), str(edition), _foil(foil)


def _record_foil(record: Mapping[str, Any]) -> bool | str | None:
    value = record.get("foil")
    if value is None:
        value = record.get("gold", record.get("is_gold"))
    return _foil(value)


def _non_combinable_foil(record: Mapping[str, Any]) -> bool:
    return str(_record_foil(record)) in {"3", "4"}


def _uid(record: Mapping[str, Any]) -> str | None:
    value = record.get("uid") or record.get("card_uid")
    return str(value) if isinstance(value, str) and value else None


def _bcx(record: Mapping[str, Any]) -> int | float | None:
    value = record.get("bcx")
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _detail(details_by_id: Mapping[Any, Any], detail_id: Any) -> Mapping[str, Any]:
    if detail_id in details_by_id and isinstance(details_by_id[detail_id], Mapping):
        return details_by_id[detail_id]
    try:
        candidate = details_by_id.get(int(detail_id), {})
    except (TypeError, ValueError):
        candidate = {}
    return candidate if isinstance(candidate, Mapping) else {}


def _bcx_max(record: Mapping[str, Any], details_by_id: Mapping[Any, Any]) -> int | float | None:
    if "bcx_max" in record:
        value = record["bcx_max"]
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return int(number) if number.is_integer() else number
    detail_id = record.get("card_detail_id")
    detail = _detail(details_by_id, detail_id)
    rarity = detail.get("rarity")
    if rarity is not None:
        rarity = {1: "common", 2: "rare", 3: "epic", 4: "legendary"}.get(rarity, str(rarity))
    return resolve_bcx_max(
        detail_id,
        record.get("edition"),
        rarity,
        _record_foil(record),
        record.get("card_set"),
        record.get("foil"),
    )


def _future(value: Any) -> bool:
    if not value:
        return False
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp > datetime.now(timezone.utc)


def _land_stake_reason(record: Mapping[str, Any]) -> str | None:
    # stake_ref_uid PERSISTS on a card record after its stake ends -- only a
    # never-staked card has it absent, so presence alone cannot gate an upgrade.
    # A live stake (stake_end_date unset) or an in-progress unstake (future
    # stake_end_date, or is_unstaking/unstaking truthy) still blocks; a past
    # stake_end_date means the card is free. Duplicated rather than shared with
    # fides_cards/ops.py::_land_stake_block and ceres/routes_combine.py's
    # _state_reasons: this module is a dependency-light pure projection (see
    # module docstring) that does not otherwise import fides_cards, and its own
    # _future() fails CLOSED on an unparseable timestamp (treats it as blocked)
    # the opposite of the other two modules' fail-OPEN _future() -- the two
    # helpers are not drop-in interchangeable, so copying the shape here (using
    # THIS module's own _future) is a deliberate choice, not an accident.
    if record.get("is_unstaking") or record.get("unstaking") or _future(record.get("stake_end_date")):
        return "card is Unstaking from Land"
    if record.get("stake_ref_uid") and record.get("stake_end_date") in (None, ""):
        return "card is Staked on Land"
    return None


def _blocked_reasons(record: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    stake_type = record.get("stake_type_uid")
    if stake_type is not None and stake_type != "STK-LND-WKR":
        reasons.append("stake class is unverified for a worker upgrade")
    classification = record.get("classification")
    bucket = classification.get("bucket") if isinstance(classification, Mapping) else None
    if bucket == "blocked":
        reasons.append(str(classification.get("reason") or "card is blocked"))
    land_reason = _land_stake_reason(record)
    if land_reason:
        reasons.append(land_reason)
    for field, reason in (
        ("market_id", "card is listed on the market"),
        ("rental_type", "card is rented"),
        ("delegated_to", "card is delegated"),
        ("delegated_to_player", "card is delegated"),
        ("in_card_set", "card is in a card set"),
        ("card_set_id", "card is in a card set"),
        ("in_guild_lending_library", "card is in the Guild Lending Library"),
        ("is_unlocking", "card is mid-unlock"),
        ("unlocking", "card is mid-unlock"),
        ("black_foil", "card is a Black Foil or Arcane Variant"),
        ("is_black_foil", "card is a Black Foil or Arcane Variant"),
        ("arcane_variant", "card is a Black Foil or Arcane Variant"),
        ("is_arcane_variant", "card is a Black Foil or Arcane Variant"),
        ("external_custody", "card is in external custody"),
        ("card_locked", "This card has a card lock, so it is not included yet."),
        ("is_locked", "This card has a card lock, so it is not included yet."),
        ("locked", "This card has a card lock, so it is not included yet."),
        ("delegated_by", "card is delegated in"),
        ("delegated_by_player", "card is delegated in"),
        ("rented_by", "card is rented"),
        ("rented_to", "card is rented"),
    ):
        if record.get(field) not in (None, False, "", 0, []):
            reasons.append(reason)
    custody = str(record.get("custody") or "").strip().lower()
    if custody in {"wax", "ethereum", "external"}:
        reasons.append("card is in external custody")
    if record.get("unlock_date") and _future(record.get("unlock_date")):
        reasons.append("card is mid-unlock")
    if record.get("lock_days"):
        reasons.append("This card has a card lock, so it is not included yet.")
    return list(dict.fromkeys(reasons))


def _maxed(record: Mapping[str, Any], bcx: int | float | None, bcx_max: int | float | None) -> bool:
    if record.get("is_max_level") is True:
        return True
    try:
        if record.get("max_level") is not None and record.get("level") is not None:
            if int(record["level"]) >= int(record["max_level"]):
                return True
    except (TypeError, ValueError):
        pass
    return bcx is not None and bcx_max is not None and bcx >= bcx_max


def _copy_projection(record: Mapping[str, Any], *, current: bool, details_by_id: Mapping[Any, Any], account: str | None = None) -> dict[str, Any]:
    bcx = _bcx(record)
    bcx_max = _bcx_max(record, details_by_id)
    # the current worker still has to pass ownership, rental and lock gates.
    blocked_reasons = [reason for reason in _blocked_reasons(record) if reason != "card is Staked on Land"] if current else _blocked_reasons(record)
    owner = record.get("player") or record.get("owner")
    if account and owner and str(owner).lower() != account.lower():
        blocked_reasons.append("card is not owned by this account")
    reasons = list(blocked_reasons)
    if _non_combinable_foil(record):
        reasons.append("card is a Black Foil or Arcane Variant")
    maxed = _maxed(record, bcx, bcx_max)
    if maxed:
        reasons.append("card is already maxed")
    uid = _uid(record)
    if uid is None:
        reasons.append("Card ID is not present in the snapshot")
    if bcx is None:
        reasons.append("CC is not present in the snapshot")
    if not current and record.get("classification") is not None:
        bucket = record["classification"].get("bucket") if isinstance(record["classification"], Mapping) else None
        if bucket not in {"unstaked_apparent", "stake_expired_or_historical", "assigned_active", "assigned_inactive", None}:
            reasons.append("card availability is unverified")
    return {
        "asset_ref": record.get("asset_ref"),
        "card_id": uid,
        "uid": uid,
        "card_detail_id": record.get("card_detail_id"),
        "edition": record.get("edition"),
        "foil": _record_foil(record),
        "skin": record.get("skin"),
        "bcx": bcx,
        "bcx_max": bcx_max,
        "is_current": current,
        "blocked": bool(blocked_reasons),
        "maxed": maxed,
        "eligible": not reasons,
        "reasons": list(dict.fromkeys(reasons)),
    }


def _coverage(report: Mapping[str, Any]) -> None:
    coverage = report.get("coverage")
    assets = coverage.get("assets") if isinstance(coverage, Mapping) else None
    collection = coverage.get("collection") if isinstance(coverage, Mapping) else None
    if not isinstance(assets, Mapping) or assets.get("complete") is not True:
        raise WorkerUpgradeOptionsError(
            "assets_incomplete",
            "the snapshot does not contain complete asset coverage; capture the account again",
        )
    if not isinstance(collection, Mapping) or collection.get("complete") is not True:
        raise WorkerUpgradeOptionsError(
            "collection_incomplete",
            "the snapshot does not contain a complete card collection; capture the account again",
        )


def project_worker_upgrade_options(
    report: Mapping[str, Any],
    deed_uid: str,
    asset_ref: str,
    details_by_id: Mapping[Any, Any] | None = None,
) -> dict[str, Any]:
    """Project upgradeability from one complete, already-captured report."""
    if not isinstance(report, Mapping):
        raise WorkerUpgradeOptionsError("snapshot_invalid", "latest snapshot is not a valid report")
    _coverage(report)
    details_by_id = details_by_id or {}
    workers = report.get("workers")
    cards = report.get("cards")
    if not isinstance(workers, list) or not isinstance(cards, list):
        raise WorkerUpgradeOptionsError("snapshot_incomplete", "the snapshot is missing worker or collection rows")
    worker_rows = [_facts(row) for row in workers]
    current_rows = [row for row in worker_rows if row and row.get("asset_ref") == asset_ref and row.get("stake_ref_uid") == deed_uid]
    if not current_rows:
        raise WorkerUpgradeOptionsError("worker_not_found", "the requested staked worker was not found on this plot")
    if len(current_rows) > 1:
        raise WorkerUpgradeOptionsError("worker_ambiguous", "the requested asset_ref identifies more than one staked worker")
    current = current_rows[0]
    if current.get("stake_type_uid") not in (None, "STK-LND-WKR"):
        raise WorkerUpgradeOptionsError("worker_wrong_class", "only a worker card can enter an upgrade cycle")
    collection_rows = [_facts(row) for row in cards]
    if any(row is None for row in collection_rows):
        raise WorkerUpgradeOptionsError("collection_incomplete", "the snapshot contains an incomplete card record")
    collection_rows = [row for row in collection_rows if row is not None]
    current_collection = next((row for row in collection_rows if row.get("asset_ref") == asset_ref), None)
    if current_collection is None:
        raise WorkerUpgradeOptionsError(
            "worker_collection_missing",
            "the complete collection does not contain the requested staked worker",
        )
    current_key = _identity(current_collection)
    if current_key is None:
        raise WorkerUpgradeOptionsError(
            "worker_identity_incomplete",
            "the requested worker is missing card_detail_id, edition, or foil",
        )
    copies = [row for row in collection_rows if _identity(row) == current_key]
    if not copies:
        raise WorkerUpgradeOptionsError("copies_missing", "the complete collection contains no matching card copies")
    projected = [
        _copy_projection(row, current=row.get("asset_ref") == asset_ref, details_by_id=details_by_id, account=report.get("player"))
        for row in copies
    ]
    projected.sort(key=lambda row: (row["card_id"] is None, row["card_id"] or "", str(row["asset_ref"] or "")))
    current_projection = next(row for row in projected if row["is_current"])
    other_copies = [row for row in projected if not row["is_current"]]
    reasons: list[dict[str, Any]] = []
    if current_projection["maxed"]:
        reasons.append({"asset_ref": asset_ref, "reason": "current worker is already maxed"})
    reasons.extend({"asset_ref": asset_ref, "reason": reason} for reason in current_projection["reasons"])
    for copy in other_copies:
        reasons.extend({"asset_ref": copy["asset_ref"], "reason": reason} for reason in copy["reasons"])
    eligible_others = [copy for copy in other_copies if copy["eligible"]]
    eligible = not current_projection["maxed"] and current_projection["eligible"] and bool(eligible_others)
    if not eligible_others:
        reasons.append({"asset_ref": asset_ref, "reason": "no other eligible copy is available"})
    candidates = [current_projection, *eligible_others]
    survivor = None
    if eligible and all(copy["card_id"] and copy["bcx"] is not None for copy in candidates):
        survivor = survivor_rule(candidates)
        if survivor == "unknown":
            survivor = None
    if survivor is None and eligible:
        reasons.append({"asset_ref": asset_ref, "reason": "the survivor cannot be predicted from the captured CC and Card IDs"})
    return {
        "deed_uid": deed_uid,
        "asset_ref": asset_ref,
        "eligible": eligible,
        "copies": projected,
        "other_copies": other_copies,
        "combinable_count": len(eligible_others) if eligible else 0,
        "predicted_survivor_uid": survivor,
        "reasons": reasons,
    }
