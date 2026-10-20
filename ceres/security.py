"""Validation and filesystem boundaries for account-scoped Ceres data."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

PLAYER_MIN_LENGTH = 3
PLAYER_MAX_LENGTH = 16
PLAYER_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")


class InvalidPlayer(ValueError):
    pass


class PathOutsideBase(ValueError):
    pass


def validate_player(player: Any) -> str:
    if not isinstance(player, str):
        raise InvalidPlayer("player must be a Hive account name")
    if not PLAYER_MIN_LENGTH <= len(player) <= PLAYER_MAX_LENGTH:
        raise InvalidPlayer("player must be 3 to 16 characters")
    if PLAYER_PATTERN.fullmatch(player) is None:
        raise InvalidPlayer("player must be a lowercase Hive account name")
    return player


def player_for_request(request: Request, player: str | None) -> str:
    config = request.app.state.ceres_config
    candidate = config.player if player is None else player
    if candidate is None:
        # no built-in account -- a request that names no player, on a server
        # started without `--player`, is refused the same way as a malformed one.
        raise HTTPException(status_code=422, detail="player required")
    try:
        return validate_player(candidate)
    except InvalidPlayer as exc:
        # 422 rather than 400: this is a request-validation refusal, matching FastAPI's
        # own shape for a malformed query parameter, and the external verification
        # against the public deployment asserts 422 for a rejected player value.
        raise HTTPException(status_code=422, detail="invalid player") from exc


def resolve_under(base: Path, path: Path) -> Path:
    resolved_base = base.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_base)
    except ValueError as exc:
        raise PathOutsideBase("requested storage path is outside its base directory") from exc
    return resolved_path
