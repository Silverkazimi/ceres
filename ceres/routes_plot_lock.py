""" (stuck states): the game's verdict on one signed deed lock / unlock, read by its transaction id.

A signed lock used to wait for the board's deed state alone and never looked its transaction up, so a lock the game
refused left the button disabled forever. This reads `/transactions/lookup` for that one KNOWN transaction (no history
scan): accepted -> the client keeps waiting for the live deed state; refused -> the reason, and the entry is cleared;
unknown -> `not_found` ( parity), which the client ends after its bounded window with a dismiss.
"""
from __future__ import annotations

import json
import re
import time as diagnostic_time

from fastapi import APIRouter, HTTPException, Request
from praetoria.transport import FixedGetTransport

from . import client_limits
from .game_lookup import lookup_trx_info
from .routes_board import _require_same_origin
from .security import player_for_request
from .transaction_diagnostics import record_verification

router = APIRouter()
_TX = re.compile(r"[a-fA-F0-9]{40}")
_DEED = re.compile(r"I-[0-9]+-[A-Za-z0-9]+")
_TYPES = {"lock": "lock_assets", "unlock": "unlock_assets"}


def lock_result(info, player: str, tx: str, deed_uid: str, action: str) -> dict:
    """One `/transactions/lookup` trx_info for our lock/unlock -> accepted / failed / not_found / pending."""
    if not info:
        return {"status": "not_found"}
    data = json.loads(info["data"]) if isinstance(info.get("data"), str) else info.get("data")
    assets = data.get("asset_ids") if isinstance(data, dict) else None
    if (info.get("id") != tx or info.get("player") != player or info.get("type") != _TYPES[action]
            or not isinstance(assets, list) or deed_uid not in assets):
        raise ValueError("Game result does not match this deed lock request")
    if info.get("success") is False:
        return {"status": "failed", "message": str(info.get("error") or "The game refused this deed lock change")}
    if info.get("success") is True:
        return {"status": "accepted"}
    return {"status": "pending"}


@router.post("/api/plot-lock/verify")
async def verify(request: Request, player: str | None = None):
    _require_same_origin(request)
    client_limits.limit(request, "verify")
    player = player_for_request(request, player)
    body = await request.json()
    tx, deed_uid, action = (body.get("tx_id"), body.get("deed_uid"), body.get("action")) if isinstance(body, dict) else (None, None, None)
    if not isinstance(tx, str) or not _TX.fullmatch(tx) or not isinstance(deed_uid, str) or not _DEED.fullmatch(deed_uid) or action not in _TYPES:
        raise HTTPException(422, "Invalid deed lock reference")
    started = diagnostic_time.monotonic()
    import asyncio
    try:
        info = await asyncio.to_thread(lookup_trx_info, FixedGetTransport(timeout=10, retries=1).get_json, tx)
        verdict = lock_result(info, player, tx, deed_uid, action)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        record_verification(account=player, tx_id=tx, attempt=None, elapsed_ms=(diagnostic_time.monotonic() - started) * 1000,
                            operation_count=1, family="plot_lock", error=True)
        raise HTTPException(502, "Game confirmation unavailable; check again without signing again") from exc
    record_verification(account=player, tx_id=tx, attempt=None, elapsed_ms=(diagnostic_time.monotonic() - started) * 1000,
                        operation_count=1, family="plot_lock", outcome={"state": verdict["status"]})
    return verdict
