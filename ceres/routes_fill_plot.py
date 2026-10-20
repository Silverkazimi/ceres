""": POST /api/plots/{deed_uid}/fill-proposal -- a read-only fill proposal for one plot (see fill_plot.py)."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import capture as capture_mod
from . import capture_freshness
from .board import load_details_by_id
from .fill_plot import clean_settings, price_fill, propose_fill
from .routes_board import _config, _read_snapshot_or_503, _require_same_origin
from .security import player_for_request

router = APIRouter()
logger = logging.getLogger("ceres.routes_fill_plot")
_MOVE_FIELDS = ("group_id", "slot_index", "action", "asset_ref", "evict_asset_ref")


def _clean_moves(raw) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 200:
        raise HTTPException(status_code=422, detail="planned_moves must be a list of at most 200")
    out = []
    for move in raw:
        if not isinstance(move, dict):
            raise HTTPException(status_code=422, detail="every planned move must be an object")
        clean = {}
        for key in _MOVE_FIELDS:
            value = move.get(key)
            if key == "slot_index":
                clean[key] = int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
            else:
                if value is not None and (not isinstance(value, str) or len(value) > 200):
                    raise HTTPException(status_code=422, detail=f"planned move field {key} is invalid")
                clean[key] = value
        if clean.get("action") not in {"stake", "replace", "unstake"}:
            raise HTTPException(status_code=422, detail="planned move action must be stake, replace or unstake")
        out.append(clean)
    return out


@router.post("/api/plots/{deed_uid}/fill-proposal")
async def post_fill_proposal(deed_uid: str, request: Request, player: str | None = None):
    """Bound to the board's OWN capture (): a missing or other stamp is refused; nothing here captures."""
    _require_same_origin(request)
    account = player_for_request(request, player)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
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
    settings = clean_settings(body.get("settings"))
    planned = _clean_moves(body.get("planned_moves"))
    staged_worksite = body.get("staged_worksite")
    if staged_worksite is not None and (
        not isinstance(staged_worksite, str) or not staged_worksite.strip() or len(staged_worksite) > 32
    ):
        raise HTTPException(status_code=422, detail="staged_worksite must be a non-empty string of at most 32 characters")
    if isinstance(body.get("price_moves"), list):  # re-price an exact set, no search
        moves = _clean_moves(body["price_moves"])
        for raw, clean in zip(body["price_moves"], moves):
            for key in ("base_pp", "splinter", "source_deed_uid", "name"):
                value = raw.get(key) if isinstance(raw, dict) else None
                if isinstance(value, (int, float, str)) and not isinstance(value, bool) and len(str(value)) <= 40:
                    clean[key] = value
        try:
            return JSONResponse(content=await asyncio.to_thread(
                lambda: price_fill(report, deed_uid, planned_moves=planned, moves=moves, details_by_id=load_details_by_id(),
                                   staged_worksite_type=staged_worksite)))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown deed_uid: {deed_uid}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    excluded = list(body.get("excluded_asset_refs") or [])
    # cards already staged for any plot in the maintainer's browser, kept out of the proposal
    staged = body.get("reserved_asset_refs") or []
    if not isinstance(staged, list) or len(staged) > 1000 or any(not isinstance(ref, str) or len(ref) > 200 for ref in staged):
        raise HTTPException(status_code=422, detail="reserved_asset_refs must be a list of at most 1000 strings")
    excluded.extend(staged)
    holds = body.get("hold_slots") or []
    if not isinstance(excluded, list) or len(excluded) > 1050 or any(not isinstance(ref, str) or len(ref) > 200 for ref in excluded):
        raise HTTPException(status_code=422, detail="excluded_asset_refs must be a list of at most 50 strings")
    if not isinstance(holds, list) or len(holds) > 50:
        raise HTTPException(status_code=422, detail="hold_slots must be a list of at most 50")
    hold_slots = set()
    for hold in holds:
        if not isinstance(hold, dict) or not isinstance(hold.get("group_id"), str):
            raise HTTPException(status_code=422, detail="every held slot needs a group_id")
        index = hold.get("slot_index", 0)
        hold_slots.add((hold["group_id"], int(index) if isinstance(index, (int, float)) and not isinstance(index, bool) else 0))
    started = time.perf_counter()
    try:
        proposal = await asyncio.to_thread(
            lambda: propose_fill(report, deed_uid, player=account, settings=settings, planned_moves=planned,
                                 now=datetime.now(timezone.utc), details_by_id=load_details_by_id(),
                                 excluded_asset_refs=set(excluded), hold_slots=hold_slots,
                                 staged_worksite_type=staged_worksite))
    except KeyError as exc:
        if exc.args != (deed_uid,):
            raise
        raise HTTPException(status_code=404, detail=f"unknown deed_uid: {deed_uid}") from exc
    except ValueError as exc:
        logger.warning("fill proposal rejected for %s/%s: %s", account, deed_uid, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    search = proposal.get('search') or {}
    logger.info('fill proposal %s/%s: %d moves, %s evaluated of %s seen, %d planned, %d reserved, %.1fs', account, deed_uid, len(proposal.get('moves') or []), search.get('evaluated'), search.get('pool_seen'), len(planned), len(excluded), time.perf_counter() - started)
    return JSONResponse(content=proposal)
