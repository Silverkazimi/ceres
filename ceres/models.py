"""Pydantic response models for the small, strictly-typed endpoints.

The board/plot/worker/idle-card projection (`board.py`) is deliberately returned as a
plain dict rather than mirrored into a strict pydantic schema here: the projection
already enumerates and types every field itself (see `board.py`, `metrics.py`), and a
second hand-maintained pydantic mirror of the same ~40-field contract would only be a
second place for the two to drift -- the `ceres.board.v1` contract is enforced by
`tests/ceres/test_board_projection.py` against real fixture data instead. This is a
documented simplification against the plan's original module map, not an omission.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class SnapshotRefModel(BaseModel):
    path: str
    generated_at: str
    player: str


class CaptureStatusModel(BaseModel):
    state: Literal["idle", "running", "failed"]
    started_at: str | None
    finished_at: str | None
    requests_made: int | None
    error: str | None
    latest_snapshot: SnapshotRefModel | None
    age_seconds: int | None
    stale: bool


