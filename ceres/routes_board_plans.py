"""IN-0267 phase 1: POST /api/board-plans/evaluate -- planned values for changed plots of a virtual board plan.

Bound to the board's OWN capture (), like /api/stage-plans/check: a missing or other stamp is refused, and
nothing here starts a capture, reads the game API, or reads the chain (). It is local arithmetic on the
already-loaded capture through the shared roster evaluator (see board_plan_eval.py).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import capture as capture_mod
from . import capture_freshness
from .board_plan_eval import GROUPS, MAX_PLACEMENTS_PER_ROW, MAX_ROWS, evaluate_targets
from .routes_board import _config, _read_snapshot_or_503, _require_same_origin
from .security import player_for_request

router = APIRouter()


def _short(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise HTTPException(status_code=422, detail=f"{field} must be a short non-empty string")
    return value


def _clean_rows(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw or len(raw) > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"rows must be a list of 1 to {MAX_ROWS} plots")
    rows: list[dict[str, Any]] = []
    seen_deeds: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            raise HTTPException(status_code=422, detail="every row must be an object")
        deed_uid = _short(row.get("deed_uid"), "deed_uid")
        if deed_uid in seen_deeds:
            raise HTTPException(status_code=422, detail="a plot may appear only once")
        seen_deeds.add(deed_uid)
        placements = row.get("placements")
        if not isinstance(placements, list) or len(placements) > MAX_PLACEMENTS_PER_ROW:
            raise HTTPException(status_code=422, detail=f"placements must be a list of at most {MAX_PLACEMENTS_PER_ROW}")
        clean: list[dict[str, Any]] = []
        slots: set[tuple[str, int]] = set()
        for placement in placements:
            if not isinstance(placement, dict):
                raise HTTPException(status_code=422, detail="every placement must be an object")
            group = placement.get("group_id")
            if group not in GROUPS:
                raise HTTPException(status_code=422, detail="placement group_id is unknown")
            slot = placement.get("slot_index")
            if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < 100:
                raise HTTPException(status_code=422, detail="placement slot_index must be a small integer")
            if (group, slot) in slots:
                raise HTTPException(status_code=422, detail="a slot may hold only one planned asset")
            slots.add((group, slot))
            clean.append({"group_id": group, "slot_index": slot, "asset_ref": _short(placement.get("asset_ref"), "asset_ref")})
        rows.append({"deed_uid": deed_uid, "placements": clean})
    refs = [placement["asset_ref"] for row in rows for placement in row["placements"]]
    if len(refs) != len(set(refs)):
        raise HTTPException(status_code=422, detail="an asset may be planned only once")
    return rows


@router.post("/api/board-plans/evaluate")
async def evaluate_board_plan(request: Request, player: str | None = None):
    _require_same_origin(request)
    account = player_for_request(request, player)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    rows = _clean_rows(body.get("rows"))
    report, _ref = await asyncio.to_thread(_read_snapshot_or_503, capture_mod, _config(request), account)
    snapshot_at = report.get("generated_at") if isinstance(report, dict) else None
    stamp = body.get("capture_generated_at")
    if not stamp:
        raise HTTPException(status_code=409, detail={"code": "capture_unknown", "snapshot_generated_at": snapshot_at})
    if stamp != snapshot_at:
        try:
            newer = capture_freshness.capture_is_newer_than(snapshot_at, stamp)
        except capture_freshness.ValidationError:
            newer = False
        raise HTTPException(status_code=409, detail={"code": "capture_superseded" if newer else "capture_unknown", "snapshot_generated_at": snapshot_at})
    try:
        report = await asyncio.to_thread(capture_mod.load_card_pool, dict(report))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail={"code": "collection_incomplete", "message": str(exc)}) from exc
    return JSONResponse(content=await asyncio.to_thread(evaluate_targets, report, rows))
