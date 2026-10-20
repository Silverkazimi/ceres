""": GET /api/cards/purchases -- "My purchases" on the Cards page, read only, on the maintainer's press.

The page calls this route only when the maintainer presses "Load my purchases" (or a window button after that); nothing calls it at
import, on page load or in the background. One call reads the game's market history, looks bought copies up by id and reads
the separate non-card purchase activity (all cached 60 s in memory, ceres/purchase_history.py), plus the market's shared going-rate reads. Its own
rate-limit bucket ("purchases"). Nothing is stored, signed or written; no uid, transaction id or seller is in the answer.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import client_limits
from . import purchase_history as ph
from .board import load_details_by_id
from .security import player_for_request

router = APIRouter()
logger = logging.getLogger("ceres.routes_purchase_history")

# a press is at most three own-account GETs (history, cards by id, other activity) and two cached market reads.
client_limits.BUCKETS.setdefault("purchases", ((10, 60.0), (120, 3600.0)))


@router.get("/api/cards/purchases")
async def get_purchases(request: Request, player: str | None = None, days: str | None = None):
    player = player_for_request(request, player)
    if days not in (None, "") and (not days.isdigit() or int(days) not in ph.WINDOWS):
        raise HTTPException(status_code=400, detail=f"days must be one of {', '.join(str(d) for d in ph.WINDOWS)}")
    client_limits.limit(request, "purchases")
    details = load_details_by_id()
    if not details:
        raise HTTPException(status_code=503, detail="the card catalogue is not available yet; open the board once, then try again")
    try:
        body = await asyncio.to_thread(ph.build, player, details, days=int(days) if days else ph.DEFAULT_DAYS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("purchase history read failed for %s: %s", player, exc)
        raise HTTPException(status_code=502, detail="the Splinterlands market history could not be read right now; try again in a minute") from exc
    return JSONResponse(content=body, headers={"Cache-Control": "no-store"})
