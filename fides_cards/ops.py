"""Explicit ``sm_combine_cards`` construction from a live card collection.

Exactly one function in ``fides_cards`` decides a survivor: ``survivor_rule``.
"""

from __future__ import annotations

import random
import re
import string
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fides.op import Op

COMBINE_OP_ID = "sm_combine_cards"
EXCLUDED_COMBINE_OP_ID = "sm_combine_all"
class CombineError(ValueError):
    """A combine cannot be safely constructed from the supplied collection state."""


def _validate_app(app: str) -> str:
    if not isinstance(app, str) or re.fullmatch(r"ceres/\d+\.\d+\.\d+", app) is None:
        raise CombineError(f"refusing third-party app identifier {app!r}; app must match 'ceres/<semver>'")
    return app


def _nonce() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=10))


def _collection_rows(collection: Any) -> list[dict[str, Any]]:
    if hasattr(collection, "data"):
        collection = collection.data
    if isinstance(collection, Mapping) and isinstance(collection.get("data"), Mapping):
        collection = collection["data"]
    if isinstance(collection, Mapping) and isinstance(collection.get("cards"), list):
        rows = collection["cards"]
    elif isinstance(collection, Mapping):
        rows = list(collection.values())
    elif isinstance(collection, list):
        rows = collection
    else:
        raise CombineError("live collection read must contain a cards array")
    if not all(isinstance(row, dict) for row in rows):
        raise CombineError("live collection contains a non-object card record")
    return list(rows)


def _uid(row: Mapping[str, Any]) -> str | None:
    value = row.get("uid") or row.get("card_uid")
    return str(value) if value else None


def _print_id(row: Mapping[str, Any]) -> str | None:
    value = row.get("card_detail_id") or row.get("card_id")
    if value is None and isinstance(row.get("card_detail"), Mapping):
        value = row["card_detail"].get("id")
    if value is None and row.get("id") is not None and row.get("id") != row.get("uid"):
        value = row.get("id")
    return str(value) if value is not None else None


def _foil(row: Mapping[str, Any]) -> bool | None:
    value = row.get("gold")
    if value is None:
        value = row.get("is_gold")
    if value is None:
        value = row.get("foil")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"gold", "gold foil", "true", "1"}:
            return True
        if normalized in {"regular", "regular foil", "false", "0"}:
            return False
    return None


def _bcx(row: Mapping[str, Any]) -> int | float | None:
    value = row.get("bcx")
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _record_index(collection: Any) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in _collection_rows(collection):
        uid = _uid(row)
        if uid:
            if uid in index:
                raise CombineError(f"live collection contains duplicate Card ID {uid!r}")
            index[uid] = row
    return index


def _resolve_records(uids: Iterable[Any], collection: Any = None) -> list[dict[str, Any]] | None:
    values = list(uids)
    if any(isinstance(value, Mapping) for value in values):
        if not all(isinstance(value, Mapping) for value in values):
            return None
        rows = [dict(value) for value in values]
    elif collection is not None:
        index = _record_index(collection)
        rows = [index.get(str(value)) for value in values]
        if any(row is None for row in rows):
            return None
        rows = [row for row in rows if row is not None]
    else:
        return None
    return rows


def survivor_rule(uids: Iterable[Any]) -> str:
    """Return the predicted surviving Card ID, or ``unknown`` without live BCX.

    The maximum BCX wins; equal BCX is resolved by the alphabetically first Card ID.
    Input order is deliberately absent from the comparison.
    """
    values = list(uids)
    if not values or any(isinstance(value, Mapping) and not _uid(value) for value in values):
        return "unknown"
    rows = _resolve_records(values)
    if rows is None:
        return "unknown"
    candidates: list[tuple[str, str, int | float]] = []
    for row in rows:
        uid = _uid(row)
        print_id = _print_id(row)
        bcx = _bcx(row)
        if uid is None or print_id is None or bcx is None:
            return "unknown"
        candidates.append((uid, print_id, bcx))
    return sorted(candidates, key=lambda item: (-item[2], item[0]))[0][0]


def _future(value: Any) -> bool:
    if not value:
        return False
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp > datetime.now(timezone.utc)


def _land_stake_block(record: Mapping[str, Any]) -> str | None:
    # stake_ref_uid PERSISTS after a stake ends -- only a never-staked card has it
    # absent/None, so presence alone cannot gate a combine. Only a live stake
    # (stake_end_date unset) or an in-progress unstake (future stake_end_date)
    # blocks; a past stake_end_date means the card is free to combine.
    if record.get("is_unstaking") or record.get("unstaking") or _future(record.get("stake_end_date")):
        return "is Unstaking from Land"
    if record.get("stake_ref_uid") and record.get("stake_end_date") in (None, ""):
        return "is Staked on Land"
    return None


def _known_block(record: Mapping[str, Any]) -> str | None:
    land_reason = _land_stake_block(record)
    if land_reason:
        return land_reason
    checks = (
        (("market_id", "rental_type"), "is listed, rented, or delegated"),
        (("delegated_to", "delegated_to_player"), "is delegated"),
        (("in_card_set", "card_set_id"), "is in a card set"),
        (("in_guild_lending_library",), "is in the Guild Lending Library"),
        (("is_unlocking", "unlocking"), "is mid-unlock"),
        (("is_max_level",), "is already at max level"),
        (("black_foil", "is_black_foil", "arcane_variant", "is_arcane_variant"), "is a Black Foil or Arcane Variant"),
        (("external_custody",), "is in external custody"),
    )
    for fields, reason in checks:
        if any(record.get(field) not in (None, False, "", 0, []) for field in fields):
            return reason
    custody = str(record.get("custody") or "").strip().lower()
    if custody in {"wax", "ethereum", "external"}:
        return "is in external custody"
    max_level = record.get("max_level")
    level = record.get("level")
    if max_level is not None and level is not None:
        try:
            if int(level) >= int(max_level):
                return "is already at max level"
        except (TypeError, ValueError):
            pass
    unlock_date = record.get("unlock_date")
    if unlock_date:
        try:
            unlock = datetime.fromisoformat(str(unlock_date).replace("Z", "+00:00"))
            if unlock > datetime.now(timezone.utc):
                return "is mid-unlock"
        except (TypeError, ValueError):
            return "has an unreadable unlock state"
    return None


@dataclass(frozen=True)
class CombineIntent:
    """The selected input set; the game, not the caller, determines the survivor."""

    uids: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.uids, (str, bytes)):
            raise CombineError("CombineIntent requires an array of Card IDs, not a selector")
        try:
            values = tuple(self.uids)
        except TypeError as exc:
            raise CombineError("CombineIntent requires an iterable of Card IDs") from exc
        if len(values) < 2:
            raise CombineError("a combine requires at least two Card IDs")
        if any(isinstance(value, Mapping) for value in values):
            raise CombineError("sm_combine_all selector shapes are excluded; select explicit Card IDs")
        if any(not isinstance(value, str) or not value for value in values):
            raise CombineError("every combine input must be a non-empty Card ID")
        if len(set(values)) != len(values):
            raise CombineError("combine input Card IDs must be unique")
        object.__setattr__(self, "uids", values)

    def to_op(self, collection: Any, *, account: str, app: str, nonce: str | None = None) -> Op:
        return build_combine_op(self.uids, collection, account=account, app=app, nonce=nonce)

    def build_op(self, collection: Any, *, account: str, app: str, nonce: str | None = None) -> Op:
        return self.to_op(collection, account=account, app=app, nonce=nonce)


def build_combine_op(
    uids: Iterable[str],
    collection: Any,
    *,
    account: str,
    app: str,
    nonce: str | None = None,
) -> Op:
    """Build one Posting-tier op after checking the same live records shown to the user."""
    intent = CombineIntent(tuple(uids))
    if not isinstance(account, str) or not account:
        raise CombineError("account must be a non-empty string")
    app = _validate_app(app)
    records = _resolve_records(intent.uids, collection)
    if records is None:
        missing = [uid for uid in intent.uids if uid not in _record_index(collection)]
        raise CombineError(f"live collection does not contain every selected Card ID; missing {missing!r}")
    card_ids = {_print_id(record) for record in records}
    editions = {record.get("edition") for record in records}
    foils = {_foil(record) for record in records}
    if None in card_ids or len(card_ids) != 1:
        raise CombineError("combine inputs must be the same card")
    if len(editions) != 1 or None in editions:
        raise CombineError("combine inputs must have the same edition")
    if len(foils) != 1 or None in foils:
        raise CombineError("combine inputs must have the same foil")
    blocked = {uid: reason for uid, reason in ((_uid(record), _known_block(record)) for record in records) if reason}
    if blocked:
        raise CombineError("cannot combine selected Card IDs: " + "; ".join(f"{uid} {reason}" for uid, reason in blocked.items()))
    if survivor_rule(records) == "unknown":
        raise CombineError("each live collection record needs Card ID and BCX to predict the survivor")
    return Op(
        operation_id=COMBINE_OP_ID,
        required_auths=[],
        required_posting_auths=[account],
        json_payload={"cards": list(intent.uids), "app": app, "n": nonce or _nonce()},
    )
