"""Regional DEC reads and unsigned operation reviews; wallets alone broadcast."""
from __future__ import annotations

from .transaction_diagnostics import record_verification
import time as diagnostic_time

import json
import math
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from praetoria.api import SplinterlandsClient
from praetoria.land_analysis import LandClient
from praetoria.singleflight import singleflight
from praetoria.transport import API_ORIGIN, LAND_ORIGIN, FixedGetTransport
from .routes_board import _require_same_origin
from . import client_limits
from .security import player_for_request
from .transaction_identity import app_identity
from .game_lookup import lookup_trx_info

router = APIRouter()
_REGION = re.compile(r"PR-[A-Z]{3}-[1-9][0-9]{0,3}")
_ACTIONS = {"stake": "sm_dec_powerup_region", "unstake": "sm_dec_powerdown_region"}


def _read(path, **params):
    return FixedGetTransport(timeout=10, retries=1).get_json(
        LAND_ORIGIN + path + "?" + urlencode(params)).data


def _data(payload, kind):
    if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), kind):
        raise ValueError("Regional DEC response is unavailable")
    return payload["data"]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Regional DEC figure is unavailable")
    return value


def _region(player, uid):
    if not isinstance(uid, str) or not _REGION.fullmatch(uid):
        raise HTTPException(422, "Invalid region")
    data = _data(_read("/land/stake/dec/region", player=player, region_uid=uid), dict)
    if data.get("uid") != uid:
        raise ValueError("Regional DEC response does not match the selected region")
    staked, required, used = [_number(data.get(k)) for k in ("dec_staked", "dec_stake_needed", "dec_stake_in_use")]
    return {"region_uid": uid, "staked": staked, "required": required, "in_use": used,
            "surplus": max(0, staked - required), "shortfall": max(0, required - staked)}


def _balance(player):
    rows = singleflight(
        ("GET", "/players/balances", ("username", player)),
        lambda: SplinterlandsClient(timeout=10, retries=1).balances(player),
    ).data
    matches = [r for r in rows if r.get("token") == "DEC" and r.get("player") == player]
    if len(matches) != 1:
        raise ValueError("Liquid DEC balance is unavailable")
    return _number(matches[0].get("balance"))


@router.get("/api/dec")
def overview(request: Request, player: str | None = None):
    player = player_for_request(request, player)
    client_limits.limit(request, "preview")  # C3: per-client limit before any upstream read
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            owned_future = pool.submit(lambda: LandClient(timeout=10, retries=1).owned_deed_counts(player).data["data"])
            stakes_future = pool.submit(lambda: _data(_read("/land/stake/decstaked", player=player), list))
            balance_future = pool.submit(_balance, player)
            owned = owned_future.result()
            stakes = stakes_future.result()
            balance = balance_future.result()
        uids = {r["uid"] for r in owned if r.get("count", 0) > 0} | {r["region_uid"] for r in stakes if r.get("amount", 0) > 0}
        if len(uids) > 150 or any(not _REGION.fullmatch(uid) for uid in uids):
            raise ValueError("Invalid regional DEC inventory")
        def read_region(uid):
            try:
                return _region(player, uid)
            except Exception:
                return {"region_uid": uid, "error": "DEC figures unavailable. Refresh to retry."}
        with ThreadPoolExecutor(max_workers=4) as pool:
            regions = list(pool.map(read_region, sorted(uids, key=lambda uid: int(uid.rsplit("-", 1)[1]))))
        return {"player": player, "regions": regions, "balance": balance,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "signing_enabled": bool(request.app.state.ceres_config.harvest_signing_enabled)}
    except Exception as exc:
        raise HTTPException(502, "Regional DEC could not be loaded. Please retry.") from exc


def _intent(body):
    uid, action = body.get("region_uid"), body.get("action")
    if not isinstance(uid, str) or not _REGION.fullmatch(uid) or action not in _ACTIONS:
        raise HTTPException(422, "Choose a region and stake or unstake")
    raw = body.get("amount")
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,3})?", raw):
        raise HTTPException(422, "Enter a positive DEC amount with at most three decimal places")
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise HTTPException(422, "Invalid DEC amount") from exc
    if amount <= 0:
        raise HTTPException(422, "Enter an amount greater than zero")
    return uid, action, amount


@router.post("/api/dec/preview")
async def preview(request: Request, player: str | None = None):
    _require_same_origin(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "preview")  # C3: per-client limit before any upstream read
    # the page paces DEC previews to one per 2 s; this is the server's own limit.
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(422, "Invalid review request")
    uid, action, amount = _intent(body)
    import asyncio
    try:
        region, balance = await asyncio.gather(asyncio.to_thread(_region, player, uid), asyncio.to_thread(_balance, player))
    except Exception as exc:
        raise HTTPException(502, "Fresh DEC figures unavailable; nothing was signed") from exc
    maximum = Decimal(str(balance if action == "stake" else region["staked"]))
    if amount > maximum:
        raise HTTPException(409, "Amount exceeds the current available DEC")
    after = Decimal(str(region["staked"])) + (amount if action == "stake" else -amount)
    payload = {"amount": float(amount), "region_uid": uid, "app": app_identity(), "n": secrets.token_hex(8)}
    operation = ["custom_json", {"id": _ACTIONS[action], "json": json.dumps(payload, separators=(",", ":")),
                                 "required_auths": [], "required_posting_auths": [player]}]
    return {"player": player, "region": region, "balance": balance, "action": action,
            "amount": str(amount), "after": float(after), "shortfall_after": max(0, region["required"] - float(after)),
            "operations": [operation], "nonce": payload["n"],
            "signing_enabled": bool(request.app.state.ceres_config.harvest_signing_enabled)}



def _harvest_receipt(inner, uid):
    data = inner.get("data")
    if not isinstance(data, dict) or "harvestResults" not in data:
        return {"state": "unavailable"}
    efficiency = {key: data[key] for key in ("pre_op_efficiency", "post_op_efficiency")
                  if isinstance(data.get(key), (int, float)) and not isinstance(data[key], bool) and math.isfinite(data[key])}
    harvest = data["harvestResults"]
    if harvest is None:
        return {"state": "not_triggered", **efficiency}
    receipt = {"state": "unavailable", **efficiency, "details": harvest}
    if not isinstance(harvest, dict):
        return receipt
    payload = harvest.get("data")
    if harvest.get("success") is False or (isinstance(payload, dict) and payload.get("success") is False):
        return {**receipt, "state": "failed", "message": str(harvest.get("error") or (payload.get("error") if isinstance(payload, dict) else "") or "Harvest was not completed")}
    if harvest.get("success") is not True or not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("results"), list):
        return receipt
    def amount(value):
        try:
            n = Decimal(str(value))
            return n if n.is_finite() and n >= 0 else None
        except InvalidOperation:
            return None
    members = []
    for row in payload["results"]:
        if not isinstance(row, dict):
            return receipt
        received, costs, taxes = {}, {}, {}
        def add(target, symbol, value):
            n = amount(value)
            if isinstance(symbol, str) and n is not None:
                target[symbol] = str(Decimal(target.get(symbol, "0")) + n)
        add(received, row.get("resource_symbol"), row.get("received_amount"))
        add(taxes, row.get("resource_symbol"), row.get("tax_amount"))
        add(costs, "GRAIN", row.get("grain_eaten"))
        add(costs, "GRAIN", row.get("grain_rewards_eaten"))
        add(costs, "DEC", row.get("dec_spent"))
        for item in row.get("inputs_consumed") or []:
            if isinstance(item, dict):
                add(costs, item.get("symbol"), item.get("qty"))
        finds = []
        roll = row.get("fragment_roll")
        if isinstance(roll, dict):
            if roll.get("fragment_found"):
                finds.append({"kind": "fragment", "type": roll.get("fragment_type")})
            if roll.get("labors_luck_uid"):
                finds.append({"kind": "labors_luck", "uid": roll["labors_luck_uid"]})
        members.append({"region_uid": uid, "deed_uids": [row["deed_uid"]] if row.get("deed_uid") else [],
                        "outcome": "success" if row.get("result_message") == "Successful" else "failed",
                        "error": row.get("result_message"), "received_by_token": received,
                        "costs_by_token": costs, "taxes_by_token": taxes, "finds": finds,
                        "evidence": ["dec_game_result"]})
    if not members:
        return {**receipt, "state": "empty", "members": []}
    try:
        from .routes_harvest import _reward_card_art
        _reward_card_art(members)
    except Exception:
        pass
    return {**receipt, "state": "recorded", "members": members}

@router.post("/api/dec/verify")
async def verify(request: Request, player: str | None = None):
    _require_same_origin(request)
    client_limits.limit(request, "verify")
    player = player_for_request(request, player)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(422, "Invalid confirmation request")
    uid, action, amount = _intent(body)
    tx, nonce = body.get("tx_id"), body.get("nonce")
    if not isinstance(tx, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", tx) or not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{16}", nonce):
        raise HTTPException(422, "Invalid transaction reference")
    started = diagnostic_time.monotonic()
    def result_log(value):
        record_verification(account=player, tx_id=tx, attempt=None,
                            elapsed_ms=(diagnostic_time.monotonic() - started) * 1000,
                            operation_count=1, family="dec", outcome={"state": value["status"]})
        return value
    import asyncio
    try:
        # the game's "Transaction [..] not found." body is "no record", not an outage.
        info = await asyncio.to_thread(lookup_trx_info, FixedGetTransport(timeout=10, retries=1).get_json, tx)
        if not info:
            # (stuck states; parity): a transaction the game does not know is `not_found`, never an
            # open-ended `pending` -- the client ends it after the claims window with a clear message and a dismiss.
            return result_log({"status": "not_found"})
        data = json.loads(info["data"]) if isinstance(info.get("data"), str) else info.get("data")
        if (info.get("id") != tx or info.get("player") != player or info.get("type") != _ACTIONS[action][3:]
                or not isinstance(data, dict) or data.get("region_uid") != uid or data.get("n") != nonce
                or Decimal(str(data.get("amount"))) != amount):
            raise HTTPException(409, "Game result does not match this DEC request")
        if info.get("success") is False:
            return result_log({"status": "failed", "message": str(info.get("error") or "Game rejected the DEC change")})
        result = json.loads(info["result"]) if isinstance(info.get("result"), str) else info.get("result")
        if isinstance(result, dict) and result.get("success") is False:
            return result_log({"status": "failed", "message": str(result.get("error") or "Game rejected the DEC change")})
        if info.get("success") is not True or not isinstance(result, dict) or result.get("success") is not True:
            return result_log({"status": "pending"})
        inner = result.get("result")
        if isinstance(inner, dict) and inner.get("success") is False:
            return result_log({"status": "failed", "message": str(inner.get("error") or "Game rejected the DEC change")})
        if not isinstance(inner, dict) or inner.get("success") is not True:
            return result_log({"status": "pending"})
        return result_log({"status": "confirmed", "tx_id": tx, "harvest": _harvest_receipt(inner, uid)})
    except HTTPException:
        raise
    except Exception as exc:
        record_verification(account=player, tx_id=tx, attempt=None,
                            elapsed_ms=(diagnostic_time.monotonic() - started) * 1000,
                            operation_count=1, family="dec", error=True)
        raise HTTPException(502, "Game confirmation unavailable; check again without signing again") from exc
