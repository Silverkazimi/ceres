""": GET /api/cards/combinables and GET /api/cards/combinables/upgrade -- "Find combinables" on the Cards page, read only.

/api/cards/combinables reads the stored board snapshot (no network) and returns every owned print with a proposed combine set
(blocked copies with their reasons, before/after level, CC and PP). With ``market=1`` it also reads the market's going rate and
the one grouped market list (both through card_market's 60 s cache) and marks the prints worth an upgrade price check.
/api/cards/combinables/upgrade prices one print level by level against the going rate (ceres/card_combinables.py). Both spend
the "market" rate-limit bucket (the list only with market=1). Nothing is signed, bought or written: a proposed set is signed
through the existing /api/combine/preview and /api/combine/build with its explicit Card IDs, and a suggestion goes to the
shop cart by its listing keys.

Options ( defaults first): ``basis=all_prints|best100`` picks the going-rate population, ``rule=lookahead|next`` picks
how a step is formed (card_combinables.going_rate / upgrade_steps).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import capture as capture_mod
from . import card_combinables, card_market, client_limits
from .board import load_details_by_id
from .security import player_for_request

router = APIRouter()
logger = logging.getLogger("ceres.routes_combinables")


def _clean(value: str | None, limit: int = 40) -> str:
    return str(value or "").strip()[:limit]


def _choice(value: str | None, allowed: tuple[str, ...]) -> str:
    """An option value, or the first (the maintainer's default) when it is absent; an unknown value is a 400."""
    value = _clean(value, 20).lower()
    if not value:
        return allowed[0]
    if value not in allowed:
        raise HTTPException(status_code=400, detail=f"must be one of {', '.join(allowed)}")
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _details() -> dict:
    details = load_details_by_id()
    if not details:
        raise HTTPException(status_code=503, detail="the card catalogue is not available yet; open the board once, then try again")
    return details


def _report(request: Request, player: str) -> tuple[dict, object]:
    config = request.app.state.ceres_config
    try:
        report, ref = capture_mod.latest_snapshot(config, player)
    except Exception as exc:  # noqa: BLE001 -- an unreadable snapshot is "no board yet" to the page
        logger.warning("combinables snapshot read failed for %s: %s", player, exc)
        report, ref = None, None
    if report is None:
        raise HTTPException(status_code=503, detail="no stored board is available; open the board for this player first")
    try:
        report = capture_mod.load_card_pool(dict(report))
    except Exception as exc:  # noqa: BLE001
        logger.warning("combinables card pool unavailable for %s: %s", player, exc)
        raise HTTPException(status_code=503, detail="the card collection could not be read; refresh the board and try again") from exc
    return report, ref


@router.get("/api/cards/combinables")
def get_combinables(request: Request, player: str | None = None, set: str | None = None, rarity: str | None = None,  # noqa: A002
                    foil: str | None = None, splinter: str | None = None, q: str | None = None, market: str | None = None,
                    basis: str | None = None, print: str | None = None):  # noqa: A002
    """``print=<card_detail_id>-<edition>-<foil>`` (, the board's combine dialog opened from the Cards page) limits
    the answer to that one print; only its own copies are read."""
    player = player_for_request(request, player)
    print_id = _clean(print, 24)
    if print_id and not re.fullmatch(r"\d+-\d+-[0-4]", print_id):
        raise HTTPException(status_code=400, detail="print must be <card_detail_id>-<edition>-<foil>")
    with_market = _clean(market, 5).lower() in ("1", "true", "yes")
    if with_market:
        client_limits.limit(request, "market")
    filters = {"set": _clean(set, 120).lower(), "rarity": _clean(rarity, 60).lower(), "foil": _clean(foil, 12),
               "splinter": _clean(splinter, 80).lower(), "q": _clean(q, 60)}
    if filters["foil"] and not all(part.strip().isdigit() for part in filters["foil"].split(",") if part.strip()):
        raise HTTPException(status_code=400, detail="foil must be a foil code (0-4) or a comma list of them")
    basis_value = _choice(basis, card_combinables.GOING_RATE_BASES)
    report, ref = _report(request, player)
    details = _details()
    if print_id:
        detail_id, edition, _foil = (int(part) for part in print_id.split("-"))
        report = {**report, "cards": [c for c in report.get("cards") or [] if isinstance(c, dict)
                                      and (c.get("facts") or {}).get("card_detail_id") == detail_id
                                      and (c.get("facts") or {}).get("edition") == edition]}
    try:
        body = card_combinables.build_combinables(report, details, player=player, filters=filters, market=with_market,
                                                  basis=basis_value)
        if print_id:
            body = {**body, "groups": [g for g in body["groups"] if g["print"]["id"] == print_id]}
            body["count"] = len(body["groups"])
    except Exception as exc:  # noqa: BLE001 -- only the market part reaches upstream
        if not with_market:
            raise
        logger.warning("combinables market read failed: %s", exc)
        raise HTTPException(status_code=502, detail="the Splinterlands market could not be read right now; try again in a minute") from exc
    return JSONResponse(content={
        "player": player,
        "generated_at": getattr(ref, "generated_at", None) or report.get("generated_at"),
        "read_at": _now_iso(),
        "filters": {k: v for k, v in {**filters, "print": print_id}.items() if v},
        "market": with_market,
        **body,
    }, headers={"Cache-Control": "no-store"})


@router.get("/api/cards/combinables/upgrade")
def get_combinable_upgrade(request: Request, card_detail_id: int, edition: int, foil: int, player: str | None = None,
                           basis: str | None = None, rule: str | None = None):
    player = player_for_request(request, player)
    client_limits.limit(request, "market")
    if foil not in card_market.FOIL_FRAME or card_detail_id < 0 or edition < 0:
        raise HTTPException(status_code=400, detail="unknown print")
    basis_value = _choice(basis, card_combinables.GOING_RATE_BASES)
    rule_value = _choice(rule, card_combinables.UPGRADE_RULES)
    report, ref = _report(request, player)
    details = _details()
    try:
        body = card_combinables.analyse_upgrade(details, report, player=player, card_detail_id=card_detail_id, edition=edition, foil=foil,
                                                basis=basis_value, rule=rule_value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("combinables upgrade read failed: %s", exc)
        raise HTTPException(status_code=502, detail="the Splinterlands market could not be read right now; try again in a minute") from exc
    if body is None:
        raise HTTPException(status_code=404, detail="this card is not a Land worker in the catalogue")
    return JSONResponse(content={"player": player, "generated_at": getattr(ref, "generated_at", None) or report.get("generated_at"),
                                 "read_at": _now_iso(), **body}, headers={"Cache-Control": "no-store"})
