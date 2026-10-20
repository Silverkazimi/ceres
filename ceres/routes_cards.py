""": GET /api/cards -- the player's own collection for the read-only card browser.

Reads the stored board snapshot only (`capture.latest_snapshot` + its card-pool sidecar):
no network, no chain read, nothing written.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import capture as capture_mod
from . import client_limits
from .board import load_details_by_id
from .card_actions import ACTIONS, action_eligibility, card_uid, copy_ref, reservation_refs
from .card_browser import build_card_rows
from .routes_combine import _live_collection
from .routes_board import _require_same_origin
from .security import player_for_request
from .logging_setup import redact_account_text

router = APIRouter()
logger = logging.getLogger("ceres.routes_cards")

# Rows depend only on the snapshot, the card catalogue and the player; a whale pool takes
# ~0.6 s to build, so one build per (player, snapshot token, catalogue mtime) is kept.
_ROWS_CACHE: OrderedDict[tuple, list] = OrderedDict()
_ROWS_CACHE_LOCK = threading.Lock()
_ROWS_CACHE_SIZE = 4

# Eligibility checks may repeat while the cards page is open. Keep their short
# reuse window here so combine operation builds always call upstream directly.
_ELIGIBILITY_COLLECTION_CACHE: OrderedDict[str, tuple[float, dict, str]] = OrderedDict()
_ELIGIBILITY_COLLECTION_CACHE_LOCK = threading.Lock()
_ELIGIBILITY_COLLECTION_CACHE_TTL = 5.0
_ELIGIBILITY_COLLECTION_CACHE_SIZE = 32


def _live_collection_recent(account: str):
    now = time.monotonic()
    with _ELIGIBILITY_COLLECTION_CACHE_LOCK:
        cached = _ELIGIBILITY_COLLECTION_CACHE.get(account)
        if cached and now - cached[0] < _ELIGIBILITY_COLLECTION_CACHE_TTL:
            _ELIGIBILITY_COLLECTION_CACHE.move_to_end(account)
            return cached[1], cached[2]
    result, stamp = _live_collection(account)
    with _ELIGIBILITY_COLLECTION_CACHE_LOCK:
        _ELIGIBILITY_COLLECTION_CACHE[account] = (time.monotonic(), result, stamp)
        _ELIGIBILITY_COLLECTION_CACHE.move_to_end(account)
        while len(_ELIGIBILITY_COLLECTION_CACHE) > _ELIGIBILITY_COLLECTION_CACHE_SIZE:
            _ELIGIBILITY_COLLECTION_CACHE.popitem(last=False)
    return result, stamp


@router.get("/api/cards")
def get_cards(request: Request, player: str | None = None):
    config = request.app.state.ceres_config
    player = player_for_request(request, player)
    try:
        report, ref = capture_mod.latest_snapshot(config, player)
    except Exception as exc:  # noqa: BLE001 -- an unreadable snapshot is "no board yet" to the page
        logger.warning("%s", redact_account_text(f"card browser snapshot read failed player={player}: {exc}", player))
        report, ref = None, None
    if report is None:
        raise HTTPException(status_code=503, detail="no stored board is available; open the board for this player first")
    key = (player, getattr(ref, "token", None), getattr(ref, "details_mtime_ns", None))
    with _ROWS_CACHE_LOCK:
        rows = _ROWS_CACHE.get(key) if key[1] else None
    if rows is None:
        try:
            report = capture_mod.load_card_pool(report)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s", redact_account_text(f"card browser card pool unavailable player={player}: {exc}", player))
            raise HTTPException(status_code=503, detail="the card collection could not be read; refresh the board and try again") from exc
        rows = build_card_rows(report, load_details_by_id(), player=player)
        if key[1]:
            with _ROWS_CACHE_LOCK:
                _ROWS_CACHE[key] = rows
                while len(_ROWS_CACHE) > _ROWS_CACHE_SIZE:
                    _ROWS_CACHE.popitem(last=False)
    return JSONResponse(content={
        "player": player,
        "generated_at": getattr(ref, "generated_at", None) or report.get("generated_at"),
        "count": len(rows),
        "cards": rows,
    })


@router.post("/api/cards/eligibility")
async def get_card_eligibility(request: Request, player: str | None = None):
    """One fresh collection read, plus caller's current  staged-plan refs.

    The body contains only opaque copy refs and existing Land stage refs. It never
    requests a UID and never returns one. No polling, chain read, or mutation.
    """
    _require_same_origin(request)
    client_limits.limit(request, "preview")
    account = player_for_request(request, player)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid request") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="request must be an object")
    refs = body.get("asset_refs")
    if not isinstance(refs, list) or len(refs) > 1000 or any(not isinstance(ref, str) or re.fullmatch(r"cc1_[0-9a-f]{48}", ref) is None for ref in refs):
        raise HTTPException(status_code=422, detail="asset_refs must be a list of at most 1000 opaque refs")
    if len(set(refs)) != len(refs):
        raise HTTPException(status_code=422, detail="asset_refs must be unique")
    plans = body.get("staged_plans")
    if not isinstance(plans, list) or len(plans) > 200 or any(not isinstance(plan, dict) for plan in plans):
        raise HTTPException(status_code=422, detail="staged_plans must be a list of at most 200 plans")
    if any(len(str(plan)) > 2000 for plan in plans):
        raise HTTPException(status_code=422, detail="staged plan too large")
    if not refs and not plans:
        return JSONResponse(content={"player": account, "observed_at": None, "actions": list(ACTIONS), "copies": []}, headers={"Cache-Control": "no-store"})
    try:
        snapshot, _ref = await asyncio.to_thread(capture_mod.latest_snapshot, request.app.state.ceres_config, account)
        if snapshot is None:
            raise HTTPException(status_code=503, detail="no stored board is available; open the board first")
        snapshot = await asyncio.to_thread(capture_mod.load_card_pool, snapshot)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s", redact_account_text(f"card eligibility snapshot unavailable player={account}: {exc}", account))
        raise HTTPException(status_code=503, detail="the card collection could not be checked") from exc
    fresh, observed_at = await asyncio.to_thread(_live_collection_recent, account)
    current = {}
    for card in fresh["cards"]:
        if isinstance(card, dict):
            uid = card_uid(card)
            if uid:
                current[uid] = card
    old = {}
    for card in snapshot.get("cards") or []:
        if isinstance(card, dict):
            uid = card_uid(card)
            if uid:
                old[copy_ref(account, uid)] = (uid, card.get("asset_ref"))
    plots = {}
    for plot in snapshot.get("plots") or []:
        if isinstance(plot, dict) and plot.get("deed_uid"):
            from .card_browser import plot_label
            label = plot_label((plot.get("facts") or {}).get("plot"))
            if label:
                plots[str(plot["deed_uid"])] = label
    reserved = reservation_refs(plans)
    now = datetime.now(timezone.utc)
    copies = []
    for ref in refs:
        old_item = old.get(ref)
        uid = old_item[0] if old_item else None
        card = current.get(uid) if uid else None
        staged_at = reserved.get(str(old_item[1])) if old_item else None
        copies.append({"asset_ref": ref, "actions": action_eligibility(card, player=account, plots=plots, now=now, reserved_plot=staged_at)})
    return JSONResponse(content={"player": account, "observed_at": observed_at, "actions": list(ACTIONS), "copies": copies}, headers={"Cache-Control": "no-store"})
