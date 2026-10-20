""": GET /api/market -- read-only market listings for the Cards page's Market mode.

Called by the Cards page's Market mode on entry and after each filter change that changes the prints in scope (,; this supersedes the press-only rule for the market).
``maxed=1`` returns only max-level offers, including the cheapest combination of listings per print. Public market
reads through praetoria's fixed transport (ceres/card_market.py), limited per client like every other upstream
route (client_limits bucket "market") and cached 60 s. Nothing is bought, signed or written.

: /api/market/print (one print in depth for the card menu) and /api/market/check (the cart's re-check) share the
"market" bucket and the cache.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import card_market, client_limits
from .board import load_details_by_id
from .security import player_for_request

router = APIRouter()
logger = logging.getLogger("ceres.routes_market")

_FILTER_KEYS = ("set", "rarity", "foil", "splinter", "q", "min_pp", "max_pp")


def _clean(value: str | None, limit: int = 40) -> str:
    return str(value or "").strip()[:limit]


@router.get("/api/market")
def get_market(request: Request, player: str | None = None, set: str | None = None, rarity: str | None = None,  # noqa: A002
               foil: str | None = None, splinter: str | None = None, q: str | None = None, sort: str | None = None,
               maxed: str | None = None, min_pp: str | None = None, max_pp: str | None = None, page: int = 1,
               start: int | None = None):
    player = player_for_request(request, player)
    client_limits.limit(request, "market")
    # set / rarity / foil / splinter may be comma lists (any of them)
    filters = {"set": _clean(set, 120).lower(), "rarity": _clean(rarity, 60).lower(), "foil": _clean(foil, 12),
               "splinter": _clean(splinter, 80).lower(), "q": _clean(q, 60),
               # the PP range picks the prints in scope before the 100-print cap (card_market.print_matches)
               "min_pp": _clean(min_pp, 12).replace(",", ""), "max_pp": _clean(max_pp, 12).replace(",", "")}
    for key in ("min_pp", "max_pp"):
        if filters[key] and card_market._number(filters[key]) is None:
            raise HTTPException(status_code=400, detail=f"{key} must be a number")
    if filters["foil"] and not all(part.strip().isdigit() for part in filters["foil"].split(",") if part.strip()):
        raise HTTPException(status_code=400, detail="foil must be a foil code (0-4) or a comma list of them")
    details = _detail_or_503()
    try:
        body = card_market.read_market(details, filters, sort=_clean(sort) or "cost_per_pp", player=player,
                                       maxed=_clean(maxed, 5).lower() in ("1", "true", "yes"),
                                       page=min(max(1, page), 50),  # Load more reads the next prints
                                       # where the previous page's window ended (its next_start)
                                       start=None if start is None else min(max(0, start), 5000))
    except Exception as exc:  # noqa: BLE001 -- any upstream failure is one honest answer to the page
        logger.warning("market read failed: %s", exc)
        raise HTTPException(status_code=502, detail="the Splinterlands market could not be read right now; try again in a minute") from exc
    return JSONResponse(content={
        "player": player,
        "read_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "filters": {k: v for k, v in filters.items() if v},
        **body,
    }, headers={"Cache-Control": "no-store"})


def _detail_or_503() -> dict:
    details = load_details_by_id()
    if not details:
        raise HTTPException(status_code=503, detail="the card catalogue is not available yet; open the board once, then try again")
    return details


@router.get("/api/market/print")
def get_market_print(request: Request, card_detail_id: int, edition: int, foil: int, player: str | None = None):
    """: one print in depth for the card menu. Called only when a menu opens (or "Add maxed card" is pressed);
    same "market" rate-limit bucket and 60 s upstream cache as /api/market. Read only."""
    player = player_for_request(request, player)
    client_limits.limit(request, "market")
    if foil not in card_market.FOIL_FRAME or card_detail_id < 0 or edition < 0:
        raise HTTPException(status_code=400, detail="unknown print")
    details = _detail_or_503()
    try:
        body = card_market.read_print(details, card_detail_id, edition, foil, player=player)
    except Exception as exc:  # noqa: BLE001
        logger.warning("market print read failed: %s", exc)
        raise HTTPException(status_code=502, detail="the Splinterlands market could not be read right now; try again in a minute") from exc
    if body is None:
        raise HTTPException(status_code=404, detail="this card is not a Land worker in the catalogue")
    return JSONResponse(content={"player": player, "read_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), **body},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/market/check")
def get_market_check(request: Request, prints: str = "", player: str | None = None):
    """: the cart's re-check on Market load -- which cart listings are still on sale and at what price. `prints` is a
    comma list of card_detail_id-edition-foil, at most card_market.CHECK_PRINTS. One hit in the "market" bucket. Read only."""
    player = player_for_request(request, player)
    client_limits.limit(request, "market")
    wanted = card_market.parse_print_ids(prints)
    if not wanted:
        raise HTTPException(status_code=400, detail="no prints to check")
    details = _detail_or_503()
    try:
        body = card_market.check_listings(details, wanted, player=player)
    except Exception as exc:  # noqa: BLE001
        logger.warning("market check failed: %s", exc)
        raise HTTPException(status_code=502, detail="the Splinterlands market could not be read right now; try again in a minute") from exc
    return JSONResponse(content={"player": player, "read_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), **body},
                        headers={"Cache-Control": "no-store"})
