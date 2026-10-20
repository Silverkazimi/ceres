""" phase 2: POST /api/stage-plans/check -- the live verdict on the maintainer's kept stage plans."""

from __future__ import annotations

import asyncio

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import capture as capture_mod
from . import capture_freshness
from .routes_board import _config, _read_snapshot_or_503, _require_same_origin
from .security import player_for_request
from .stage_plans import MAX_PLANS, check_plans

router = APIRouter()
_FIELDS = ("key", "deed_uid", "group_id", "action", "asset_ref", "evict_asset_ref")


def _clean(plans: Any) -> list[dict[str, Any]]:
    if not isinstance(plans, list) or len(plans) > MAX_PLANS:
        raise HTTPException(status_code=422, detail=f"plans must be a list of at most {MAX_PLANS}")
    out = []
    for plan in plans:
        if not isinstance(plan, dict):
            raise HTTPException(status_code=422, detail="every plan must be an object")
        cleaned: dict[str, Any] = {}
        for field in _FIELDS:
            value = plan.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 200):
                raise HTTPException(status_code=422, detail=f"plan field {field} must be a short string")
            cleaned[field] = value
        slot = plan.get("slot_index")
        if slot is not None and (isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < 100):
            raise HTTPException(status_code=422, detail="plan slot_index must be a small integer")
        cleaned["slot_index"] = slot
        out.append(cleaned)
    return out


@router.post("/api/stage-plans/check")
async def check_stage_plans(request: Request, player: str | None = None):
    """Judge kept plans against the board's OWN capture (): a missing or other stamp is refused, and nothing
    here starts a capture or reads the network."""
    _require_same_origin(request)
    account = player_for_request(request, player)
    body = await request.json()
    plans = _clean(body.get("plans") if isinstance(body, dict) else None)
    report, _ref = await asyncio.to_thread(_read_snapshot_or_503, capture_mod, _config(request), account)  # S10 / L1-01: disk and board work off the event loop
    snapshot_at = report.get("generated_at") if isinstance(report, dict) else None
    stamp = body.get("capture_generated_at") if isinstance(body, dict) else None
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
    return JSONResponse(content={"generated_at": snapshot_at, "verdicts": await asyncio.to_thread(check_plans, report, plans, account=account)})
