""" / : checkout routes for the Cards page cart ("Buy from Ceres"). Helpers in ceres/market_purchase.py.

POST /api/market/checkout/quote    the cart re-checked BY ID: per listing ok / price_changed / sold..., totals in $, DEC and
                                   credits, balances, the split into transactions of at most 150 listings
POST /api/market/checkout/preview  a fresh re-check against the confirmed quote; unchanged -> the exact operation(s) to sign
                                   (Active), changed -> 409 with the new quote (the page asks again)
POST /api/market/checkout/verify   the game's record of one signed purchase (by transaction id): per listing bought / not
POST /api/market/checkout/find     the wallet gave no transaction id: the player's OWN newest 100 operations, by nonce
POST /api/market/checkout/test-buy : the single cheapest listing on the market, quoted (maintainer account, local build only)
POST /api/market/checkout/combine   Buy & max: once EVERY listing of a maxed-card group is confirmed bought, the combine of
                                   exactly those cards (sm_combine_cards, Posting) through the existing /api/combine/build path;
                                   the maintainer signs it as a second, separate signature
POST /api/market/checkout/combine-verify  : the game's record of that signed combine, by id (success + updated_card)

Nothing is signed or broadcast here; the page asks the player's wallet. Signing is gated by ``CERES_MARKET_BUY``:
``maintainer`` (default -- only the operator's own account, ``--player``, until the maintainer's two test buys), ``on`` (everyone)
or ``off``. Reads are limited per client (bucket "market_buy"; verify uses the by-id "verify" bucket).
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from praetoria.transport import FixedGetTransport

from . import client_limits
from . import market_purchase as mp
from .game_lookup import lookup_trx_info
from .routes_board import _require_same_origin, dev_watch_enabled
from .security import player_for_request

router = APIRouter()

# its own bucket -- a quote is a handful of public GETs (by-id status, settings, balances); a preview repeats them.
client_limits.BUCKETS.setdefault("market_buy", ((20, 60.0), (300, 3600.0)))

ENV_SIGNING = "CERES_MARKET_BUY"
_TX = re.compile(r"[a-fA-F0-9]{40}")
NO_STORE = {"Cache-Control": "no-store"}


def signing_mode() -> str:
    value = os.environ.get(ENV_SIGNING, "owner").strip().lower()
    return value if value in ("owner", "on", "off") else "off"


def signing_state(request: Request, player: str) -> dict[str, Any]:
    mode = signing_mode()
    owner = (request.app.state.ceres_config.player or "").lower()
    if mode == "on" or (mode == "owner" and owner and player == owner):
        return {"enabled": True, "reason": ""}
    if mode == "off":
        return {"enabled": False, "reason": "Buying from Ceres is switched off on this server."}
    return {"enabled": False, "reason": "Buying from Ceres is not open yet: the owner's test purchases come first."}


def test_buy_allowed(request: Request, player: str) -> bool:
    """: the test-buy control exists only for the operator's own account on a local build (dev loop on)."""
    owner = (request.app.state.ceres_config.player or "").lower()
    return bool(owner) and player == owner and dev_watch_enabled()


async def _body(request: Request) -> dict:
    _require_same_origin(request)
    try:
        value = await request.json()
    except ValueError as exc:
        raise HTTPException(422, "Invalid checkout request") from exc
    if not isinstance(value, dict):
        raise HTTPException(422, "Invalid checkout request")
    return value


def _getter():
    return mp._default_get


def _quote_response(request: Request, player: str, quote: dict, status: int = 200, extra: dict | None = None) -> JSONResponse:
    qid = mp.QUOTES.put(quote)
    body = {**mp.public_quote(quote, qid), "signing": signing_state(request, player),
            "test_buy": test_buy_allowed(request, player), **(extra or {})}
    return JSONResponse(content=body, status_code=status, headers=NO_STORE)


async def _build(player: str, items: list[dict]) -> dict:
    try:
        return await asyncio.to_thread(mp.build_quote, player, items, _getter())
    except mp.PurchaseError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 -- one honest answer; nothing was signed
        raise HTTPException(502, "The Splinterlands market could not be read right now; nothing was bought. Try again in a minute.") from exc


@router.post("/api/market/checkout/quote")
async def checkout_quote(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "market_buy")
    try:
        items = mp.parse_items(body.get("items"))
    except mp.PurchaseError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _quote_response(request, player, await _build(player, items))


# Purchases Ceres prepared (nonce -> player, payload), so verify knows its own builds; server memory, bounded.
_PREPARED: dict[str, dict] = {}
_PREPARED_LOCK = threading.Lock()
_PREPARED_TTL_S = 2 * 3600


def _remember(player: str, txs: list[dict]) -> None:
    now = time.time()
    with _PREPARED_LOCK:
        for key in [k for k, v in _PREPARED.items() if now - v["at"] > _PREPARED_TTL_S]:
            _PREPARED.pop(key, None)
        for tx in txs:
            _PREPARED[tx["nonce"]] = {"player": player, "payload": tx["payload"], "at": now}


@router.post("/api/market/checkout/preview")
async def checkout_preview(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "market_buy")
    signing = signing_state(request, player)
    if not signing["enabled"]:
        raise HTTPException(409, signing["reason"])
    currency = body.get("currency")
    if currency not in mp.CURRENCIES:
        raise HTTPException(422, "Choose DEC or credits")
    confirmed = mp.QUOTES.get(body.get("quote_id"), player)
    if confirmed is None:
        raise HTTPException(410, "This checkout is out of date; Ceres checks the cart again.")
    # The fresh re-check compares against the prices the maintainer CONFIRMED (the quote's), not the ones first seen in the cart.
    items = [{"lkey": r["lkey"], "pid": r["pid"], "seen_usd": r["price_usd"] if r["status"] in mp.BUYABLE else r.get("seen_usd"),
              "group": r.get("group")} for r in confirmed["items"]]
    fresh = await _build(player, items)
    reasons = mp.differences(confirmed, fresh, currency, body.get("confirmed_total"))
    if reasons:
        # anything sold or dearer -> the page shows the new quote (struck / amber) and asks again; nothing to sign.
        return _quote_response(request, player, fresh, status=409,
                               extra={"detail": "The cart changed since you confirmed it; check the new total and confirm again.",
                                      "reasons": reasons})
    try:
        txs = mp.build_transactions(player, fresh, currency)
    except mp.PurchaseError as exc:
        raise HTTPException(422, str(exc)) from exc
    _remember(player, txs)
    return JSONResponse(content={"player": player, "currency": currency, "transactions": txs, "total": fresh["totals"][currency],
                                 "total_usd": fresh["totals"]["usd"], "dec_price": fresh["dec_price"], "authority": "Active",
                                 "upstream_calls": fresh["upstream_calls"]}, headers=NO_STORE)


def _expected(body: dict, player: str) -> dict:
    expected = body.get("expected_payload")
    try:
        mp.validate_payload(expected, player, current_app=False)
    except mp.PurchaseError as exc:
        raise HTTPException(422, "Invalid purchase reference") from exc
    if expected["n"] != body.get("nonce"):
        raise HTTPException(422, "Invalid purchase reference")
    with _PREPARED_LOCK:
        known = _PREPARED.get(expected["n"])
    if known is not None and (known["player"] != player or known["payload"] != expected):
        raise HTTPException(409, "This is not the purchase Ceres prepared")
    return expected


@router.post("/api/market/checkout/verify")
async def checkout_verify(request: Request, player: str | None = None):
    """One by-id lookup of a signed purchase. Unknown is never permission to sign again ( S3): the page keeps the
    listings locked and keeps checking."""
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "verify")
    tx, nonce = body.get("tx_id"), body.get("nonce")
    if not isinstance(tx, str) or not _TX.fullmatch(tx) or not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{16}", nonce):
        raise HTTPException(422, "Invalid purchase reference")
    expected = _expected(body, player)
    try:
        info = await asyncio.to_thread(lookup_trx_info, FixedGetTransport(timeout=10, retries=1).get_json, tx.lower())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "The game's confirmation is unavailable; check again without buying again") from exc
    verdict = mp.outcome(info, player, tx.lower(), expected)
    if verdict["status"] == "confirmed" and verdict.get("bought"):
        # the bought listings must leave the Market at its next read (the page reloads it), not after the 60 s cache.
        mp.card_market.CACHE.clear()
    return JSONResponse(content={"player": player, **verdict}, headers=NO_STORE)


@router.post("/api/market/checkout/find")
async def checkout_find(request: Request, player: str | None = None):
    """ pattern: the wallet returned no transaction id. One read of the player's OWN newest 100 operations finds the
    purchase by its nonce; `covered` says whether that window reaches back to when the wallet was asked ()."""
    from .harvest_routing_bridge import _history
    from .routes_wallet_lp import _parse_time, history_covers

    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "market_buy")
    nonce = body.get("nonce")
    if not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{16}", nonce):
        raise HTTPException(422, "Invalid purchase reference")
    since = body.get("since")
    if since is not None and _parse_time(since) is None:
        raise HTTPException(422, "Invalid purchase time")
    try:
        rows = await asyncio.to_thread(_history, player, -1)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "Your account history could not be read right now") from exc
    tx = mp.find_in_history(rows, player, nonce)
    content = {"status": "found", "tx_id": tx} if tx else {"status": "not_found", "covered": history_covers(rows, since)}
    return JSONResponse(content=content, headers=NO_STORE)


@router.post("/api/market/checkout/test-buy")
async def checkout_test_buy(request: Request, player: str | None = None):
    """ (maintainer: two real test buys of the cheapest card, one DEC, one credits, signed by the maintainer): a quote for the
    single cheapest listing on the market. The maintainer chooses the currency and signs, as for any cart."""
    from .board import load_details_by_id

    await _body(request)
    player = player_for_request(request, player)
    if not test_buy_allowed(request, player):
        raise HTTPException(404, "Not found")
    client_limits.limit(request, "market_buy")
    details = load_details_by_id()
    if not details:
        raise HTTPException(503, "the card catalogue is not available yet; open the board once, then try again")
    try:
        item = await asyncio.to_thread(mp.cheapest_listing, details, player, _getter())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "The Splinterlands market could not be read right now") from exc
    if item is None:
        raise HTTPException(404, "No listing found")
    display = {k: item[k] for k in ("lkey", "pid", "name", "level", "bcx", "card_set", "rarity", "foil", "card_detail_id", "edition", "seen_usd")}
    quote = await _build(player, [{"lkey": item["lkey"], "pid": item["pid"], "seen_usd": item["seen_usd"]}])
    return _quote_response(request, player, quote, extra={"test_item": display})


@router.post("/api/market/checkout/combine")
async def checkout_combine(request: Request, player: str | None = None):
    """. Reads, in order: the game's record of the signed purchase (by id) --
    every listing of the group must be bought; the bought listings by id (``/market/status``) for their card uids; then the
    existing combine build (``routes_combine.combine_build``: live collection, combinability checks, evidence for
    /api/op-verify). A purchase and a combine cannot share one transaction (the cards are not the buyer's before the purchase
    is applied), so this is always a second signature. Answers ``ready`` (with the operation), ``pending`` (the purchase is
    not confirmed yet), ``partial`` (not every listing was bought: no combine), ``not_ready`` (the cards are not in the
    collection yet; asked again on the minute cadence) or ``failed``. Nothing is signed here."""
    from .routes_combine import combine_build

    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "market_buy")
    tx, nonce = body.get("tx_id"), body.get("nonce")
    if not isinstance(tx, str) or not _TX.fullmatch(tx) or not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{16}", nonce):
        raise HTTPException(422, "Invalid purchase reference")
    expected = _expected(body, player)
    by_key = {mp.listing_key({"market_id": m}): m for m in expected["items"]}
    lkeys = body.get("lkeys")
    if (not isinstance(lkeys, list) or len(lkeys) < 2 or len(set(lkeys)) != len(lkeys)
            or not all(isinstance(k, str) and k in by_key for k in lkeys)):
        raise HTTPException(422, "A Buy & max group is two or more listings of this purchase")
    try:
        info = await asyncio.to_thread(lookup_trx_info, FixedGetTransport(timeout=10, retries=1).get_json, tx.lower())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "The game's confirmation is unavailable; check again") from exc
    verdict = mp.outcome(info, player, tx.lower(), expected)
    if verdict["status"] != "confirmed":
        # only the game's success:false is "failed"; a record that differs is not a failure (the page stays amber).
        return JSONResponse(content={"status": "failed" if verdict["status"] == "failed" else "pending",
                                     "message": verdict.get("message")}, headers=NO_STORE)
    bought = {r["lkey"] for r in verdict["items"] if r["outcome"] == "bought"}
    missing = [k for k in lkeys if k not in bought]
    if missing:
        return JSONResponse(content={"status": "partial", "missing": missing, "bought": [k for k in lkeys if k in bought]}, headers=NO_STORE)
    group_ids = [by_key[k] for k in lkeys]
    try:
        rows = await asyncio.to_thread(mp.read_status, group_ids, _getter())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "The bought listings could not be read right now; Ceres tries again") from exc
    uids, waiting = mp.group_uids(group_ids, rows, player)
    if waiting:
        return JSONResponse(content={"status": "not_ready", "message": "The game has not finished handing over every card yet."}, headers=NO_STORE)
    try:
        response = await asyncio.to_thread(combine_build, {"uids": uids}, request, player)
    except HTTPException as exc:
        if exc.status_code in (409, 503):
            detail = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
            return JSONResponse(content={"status": "not_ready", "message": str(detail or "The cards cannot be combined yet.")}, headers=NO_STORE)
        raise
    import json as _json
    built = _json.loads(response.body)
    preview = built.get("preview") or {}
    return JSONResponse(content={"status": "ready", "operation": built["operation"], "count": len(uids),
                                 "resulting_bcx": preview.get("resulting_bcx"), "witness": {
                                     "uids": preview.get("uids"),
                                     "cards": preview.get("witness"),
                                     "survivor": preview.get("predicted_survivor_uid"),
                                     "destroyed": (preview.get("hazard") or {}).get("destroyed_uids", []),
                                     "resulting_bcx": preview.get("resulting_bcx"),
                                 }, "authority": "Posting"}, headers=NO_STORE)


@router.post("/api/market/checkout/combine-verify")
async def checkout_combine_verify(request: Request, player: str | None = None):
    """: the game's record of the signed Buy & max combine, by its transaction id (one lookup, the by-id "verify" bucket).
    The expected CC is the sum of the pre-combine records the combine build saved (combine_evidence), when still there. Unknown is
    never permission to combine again ( S3)."""
    from fides_cards.ops import _bcx

    from . import combine_evidence

    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "verify")
    tx = body.get("tx_id")
    if not isinstance(tx, str) or not _TX.fullmatch(tx):
        raise HTTPException(422, "Invalid combine reference")
    try:
        payload = mp.combine_payload(body.get("operation"), player)
    except mp.PurchaseError as exc:
        raise HTTPException(422, "Invalid combine reference") from exc
    config = request.app.state.ceres_config
    records = combine_evidence.load(config, payload["n"], account=player)
    expected_bcx = None
    if isinstance(records, list):
        by_uid = {str(r.get("uid")): r for r in records if isinstance(r, dict)}
        values = [_bcx(by_uid[c]) if c in by_uid else None for c in payload["cards"]]
        expected_bcx = float(sum(values)) if all(v is not None for v in values) else None
    try:
        info = await asyncio.to_thread(lookup_trx_info, FixedGetTransport(timeout=10, retries=1).get_json, tx.lower())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "The game's confirmation is unavailable; check again without combining again") from exc
    verdict = mp.combine_outcome(info, player, tx.lower(), payload, expected_bcx)
    if verdict["status"] == "confirmed":
        combine_evidence.discard(config, payload["n"])
    return JSONResponse(content={"player": player, **verdict}, headers=NO_STORE)
