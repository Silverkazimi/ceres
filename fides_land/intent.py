"""Asset-class-agnostic land staking intents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StakeIntent:
    deed_uid: str
    stake_type_uid: str
    direction: str
    asset_uid: str
    asset_ref: str | None = None
    slot: int | None = None
