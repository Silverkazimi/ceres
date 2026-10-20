"""Small, JSON-friendly data structures used by Praetoria."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class QuerySpec:
    player: str
    set_name: str | None = None
    api_card_set: str | None = None
    id_range: tuple[int, int] | None = None
    edition: int | None = None
    include_zero: bool = False
    foil: str = "any"
    stake_status: str = "any"
    delegation_status: str = "any"
    as_of: datetime = field(default_factory=utc_now)
    sort: str = "id"


@dataclass
class CacheInfo:
    state: str
    retrieved_at: str | None = None
    age_seconds: int | None = None
    sha256: str | None = None
    attempts: int = 0


@dataclass
class SourceInfo:
    collection: CacheInfo
    details: CacheInfo


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def json_number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    number = float(value)
    return int(number) if number.is_integer() else number

