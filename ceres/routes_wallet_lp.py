"""Fresh wallet-LP previews and lookup-only game verification."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from decimal import ROUND_HALF_DOWN, Decimal, localcontext
from urllib.parse import quote as url_quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from praetoria.land_analysis import LandClient
from praetoria.singleflight import singleflight
from praetoria.transport import API_ORIGIN, LAND_ORIGIN, FixedGetTransport

from fides.game_verdict import _inner_refusal
from . import client_limits, harvest_routing as routing
from . import harvest_routing_operations as harvest_operations
from . import wallet_lp_operations as operations
from .game_lookup import lookup_trx_info
from .routes_board import _require_same_origin
from .routes_harvest_routing import _drift_tolerant_fields, _pools
from .routes_wallet import ACCOUNT_SWAP_SYMBOLS, LP_GATE_REASONS, SWAP_SLIPPAGE, TWO_STEP_SELL, fee_percent, _balances, account_section, lp_action_capabilities, regions_section, swap_rates
from .security import player_for_request

router = APIRouter()
_TX = re.compile(r"[a-fA-F0-9]{40}")
_KINDS = ("add", "remove", "swap_sell", "swap_resource", "swap_buy")


def capabilities(signing_enabled: bool) -> dict[str, dict[str, object]]:
    return lp_action_capabilities(signing_enabled)


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def _body(request: Request) -> dict:
    _require_same_origin(request)
    try:
        value = await request.json()
    except ValueError as exc:
        raise HTTPException(422, "Invalid wallet LP request") from exc
    if not isinstance(value, dict):
        raise HTTPException(422, "Invalid wallet LP request")
    return value


def _kind(payload: dict) -> str:
    kind = payload.get("kind")
    if kind not in _KINDS:
        raise HTTPException(422, "Unsupported wallet LP action")
    return kind


def _promised_output(body: dict, quote) -> Decimal:
    """ S4 / L2-05: the output a one-click swap is signed for.

    The card promises "You get at least <seen x (1 - slippage)>". The game applies the slippage to the expected output written in the
    transaction, so signing for the FRESH output (which may be a little lower) would apply the tolerance a second time and weaken the promise
    (shown 97.500, protected only to 95.062). The transaction therefore carries the output the maintainer SAW; the fresh quote only has to be able to
    meet that promise (checked by the caller). Without a seen figure the fresh output is used. Zero outbound calls.
    """
    if body.get("sign") is not True or "reviewed_payload" in body or body.get("seen_output") is None:
        return quote.output
    try:
        seen = routing.amount(body.get("seen_output"), positive=True)
    except routing.RoutingError:
        return quote.output
    if seen != routing.floor(seen):  # the card shows three decimals; anything finer is not a figure the maintainer saw
        return quote.output
    tolerance = routing.validate_slippage(body.get("max_slippage", "2.5"))
    return seen if quote.output >= routing.floor(seen * (1 - tolerance / 100)) else quote.output


def position_row(payload, symbol: str) -> tuple[Decimal, Decimal]:
    """: `/land/liquidity/pools/{player}/{token}` -- the read the game's own Manage view makes -- as
    (shares, vesting). `VESTING-<SYMBOL>` is the part still inside its 30-day window; the game's client takes a
    10 % fee on whatever a withdrawal takes beyond the unlocked rest. A missing or null row is 0."""
    data = payload.get("data") if isinstance(payload, dict) and payload.get("status") == "success" else None
    rows = data.get("single") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise routing.RoutingError("Your pool position is unavailable")
    found = {}
    for row in rows:
        if isinstance(row, dict) and row.get("token") in (f"DEC-{symbol}", f"VESTING-{symbol}"):
            value = row.get("balance")
            found[row["token"]] = Decimal(0) if value is None else routing.amount(value)
    return found.get(f"DEC-{symbol}", Decimal(0)), found.get(f"VESTING-{symbol}", Decimal(0))


def _position(player: str, symbol: str) -> tuple[Decimal, Decimal]:
    path = f"/land/liquidity/pools/{url_quote(player, safe='')}/{url_quote(symbol, safe='')}"
    return position_row(FixedGetTransport(timeout=10, retries=1).get_json(LAND_ORIGIN + path).data, symbol)


def withdraw_view(pool, shares: Decimal, vesting: Decimal, fraction) -> dict:
    """What a withdrawal of `fraction` of the position pays, with the game's own client maths (Manage view):
    shares x reserve / total shares, three decimals, times the early-withdrawal factor -- 0.9 when nothing is
    unlocked, otherwise 1 - 0.1 x (the part taken beyond the unlocked shares) / (the shares taken)."""
    fraction = operations._fraction(fraction)
    if shares <= 0:
        raise routing.RoutingError("You have no position in this pool")
    taken = shares * fraction
    unlocked = max(shares - vesting, Decimal(0))
    factor = Decimal(1)
    if unlocked < taken:
        factor = Decimal("0.9") if unlocked == 0 else max(Decimal(0), 1 - (taken - unlocked) / taken * Decimal("0.1"))
    with localcontext() as ctx:
        ctx.prec = 50
        resource = routing.floor((taken * pool.resource / pool.shares).quantize(routing.QUANTUM, rounding=ROUND_HALF_DOWN) * factor)
        dec = routing.floor((taken * pool.dec / pool.shares).quantize(routing.QUANTUM, rounding=ROUND_HALF_DOWN) * factor)
    if resource <= 0 or dec <= 0:
        raise routing.RoutingError("This share is too small to withdraw")
    return {"fraction": routing.text(fraction), "shares": routing.text(routing.floor(taken)), "resource_amount": routing.text(resource),
            "dec_amount": routing.text(dec), "fee_percent": format(((1 - factor) * 100).quantize(Decimal("0.01")).normalize(), "f"),
            "early_fee": factor < 1, "position_shares": routing.text(shares), "locked_shares": routing.text(min(vesting, shares))}


def _build(player: str, body: dict, pools: dict, balances: list[dict], regions_payload: dict, position=None) -> dict:
    kind = _kind(body)
    region = body.get("region_uid")
    region_rows = regions_section(regions_payload)
    region_map = {row["region_uid"]: row["balances"] for row in region_rows}
    token = body.get("resource_symbol")
    wallet_token = kind in {"swap_sell", "swap_buy", "add", "remove"} and token in ACCOUNT_SWAP_SYMBOLS
    if kind in {"add", "remove", "swap_sell", "swap_buy"} and not wallet_token and region not in region_map:
        raise routing.RoutingError("Fresh balance for the selected region is unavailable")
    nonce = body.get("nonce")
    slippage = body.get("max_slippage", "2.5")
    if kind == "add":
        pool = pools.get(operations._swap_symbol(token))
        if pool is None:
            raise routing.RoutingError("This pool is unavailable")
        deposit = routing.liquidity_deposit(pool, body.get("resource_amount"))
        account = account_section(balances)
        held = account[token] if wallet_token else region_map[region][token]
        if Decimal(str(held)) < Decimal(deposit["resource_amount"]):
            raise routing.RoutingError("Fresh balance is too low")
        if Decimal(str(account["DEC"])) < Decimal(deposit["dec_amount"]):
            raise routing.RoutingError("Fresh DEC balance is too low")
        return {**operations.add(player, region, token, deposit["resource_amount"], deposit["dec_amount"], nonce=nonce), "deposit": deposit}
    if kind == "remove":
        pool = pools.get(operations._swap_symbol(token))
        if pool is None or position is None:
            raise routing.RoutingError("This pool is unavailable")
        view = withdraw_view(pool, *position, body.get("shares_out"))
        return {**operations.remove(player, region, token, view["fraction"], nonce=nonce), "withdraw": view}
    if kind == "swap_sell":
        quote = routing.swap_quote(operations._swap_symbol(token), "DEC", body.get("resource_amount"), pools, slippage)
        held = account_section(balances)[token] if wallet_token else region_map[region][token]
        if Decimal(str(held)) < quote.input:
            raise routing.RoutingError("Fresh balance is too low")
        return operations.sell(player, region, token, quote.input, _promised_output(body, quote), slippage, nonce=nonce)
    if kind == "swap_buy":
        quote = routing.swap_quote("DEC", operations._swap_symbol(token), body.get("dec_amount"), pools, slippage)
        if Decimal(str(account_section(balances)["DEC"])) < quote.input:
            raise routing.RoutingError("Fresh DEC balance is too low")
        return operations.buy(player, region, token, quote.input, _promised_output(body, quote), slippage, nonce=nonce)
    source, destination = operations._symbol(body.get("from_symbol")), operations._symbol(body.get("to_symbol"))
    from_region, to_region = body.get("from_region_uid"), body.get("to_region_uid")
    if from_region not in region_map or to_region not in region_map:
        raise routing.RoutingError("Fresh balance for both selected regions is required")
    quote = routing.swap_quote(source, destination, body.get("in_amount"), pools, slippage)
    if quote.external:
        raise routing.RoutingError("External resource pools are unavailable for wallet LP swaps")
    if Decimal(str(region_map[from_region][source])) < quote.input:
        raise routing.RoutingError("Fresh source-region balance is too low")
    return operations.resource_swap(player, from_region, to_region, source, destination, quote.input,
                                    quote.intermediate_dec, quote.output, slippage, nonce=nonce)


def swap_view(kind: str, symbol, value, pools: dict) -> dict:
    """: the figures the wallet swap card shows for one amount -- from one fresh pools read, with
    the fixed tolerance. `pool_cost` is the pool's single-leg fee on the input, in the paid token
    ("Estimated pool cost"). Builds no operation."""
    symbol = operations._swap_symbol(symbol)
    pay, receive = (symbol, "DEC") if kind == "swap_sell" else ("DEC", symbol)
    quote = routing.swap_quote(pay, receive, value, pools, SWAP_SLIPPAGE)
    if quote.external and symbol not in TWO_STEP_SELL:
        raise routing.RoutingError("External resource pools are unavailable for wallet LP swaps")
    factor = pools[symbol].factor(False)
    return {"kind": kind, "resource_symbol": symbol, "pay_symbol": pay, "receive_symbol": receive,
            "input": routing.text(quote.input), "output": routing.text(quote.output),
            "minimum": routing.text(quote.minimum), "pool_cost": routing.text(routing.floor(quote.input * (1 - factor))),
            "slippage": SWAP_SLIPPAGE, "rates": swap_rates(kind, symbol, pools), "fee_percent": fee_percent(pools[symbol]),
            "two_step": kind == "swap_sell" and symbol in TWO_STEP_SELL}


def input_for_output(kind: str, symbol, target, maximum, pools: dict) -> tuple[Decimal, bool]:
    """: the
    smallest three-decimal amount to pay whose quoted "You receive" is at least `target`, never more than `maximum`
    (the live balance the card shows). Returns (input, capped); capped means even `maximum` gives less."""
    symbol = operations._swap_symbol(symbol)
    pay, receive = (symbol, "DEC") if kind == "swap_sell" else ("DEC", symbol)
    want = routing.amount(target, positive=True)
    limit = routing.floor(routing.amount(maximum, positive=True))
    quantum = routing.QUANTUM

    def output(units: int) -> Decimal | None:
        try:
            return routing.swap_quote(pay, receive, Decimal(units) * quantum, pools, SWAP_SLIPPAGE).output
        except routing.RoutingError:
            return None

    high = int(limit / quantum)
    top = output(high)
    if top is None:
        raise routing.RoutingError("No quote for this amount")
    if top < want:
        return limit, True
    low = 1
    while low < high:
        middle = (low + high) // 2
        got = output(middle)
        if got is not None and got >= want:
            high = middle
        else:
            low = middle + 1
    return Decimal(low) * quantum, False


def _projected(kind: str, view: dict, balances: list[dict], regional: dict, region: str) -> dict:
    """After-swap wallet DEC and region resource, from the same fresh reads the preview used."""
    account = account_section(balances)
    dec = Decimal(str(account["DEC"]))
    symbol = view["resource_symbol"]
    held = Decimal(str(account[symbol] if symbol in ACCOUNT_SWAP_SYMBOLS else
                       {row["region_uid"]: row["balances"] for row in regions_section(regional)}[region][symbol]))
    spent, received = Decimal(view["input"]), Decimal(view["output"])
    if kind == "swap_sell":
        return {"dec_after": routing.text(routing.floor(dec + received)), "region_after": routing.text(routing.floor(held - spent))}
    return {"dec_after": routing.text(routing.floor(dec - spent)), "region_after": routing.text(routing.floor(held + received))}


def _fresh_state(player: str):
    def fetch():
        with ThreadPoolExecutor(max_workers=3) as pool:
            pools = pool.submit(_pools)
            balances = pool.submit(_balances, player)
            regions = pool.submit(lambda: LandClient(timeout=10, retries=1).regional_balances(player).data)
            return pools.result(), balances.result(), regions.result()

    return singleflight(("wallet_lp_preview", player), fetch)


def _check_drift(reviewed: dict, fresh: dict) -> None:
    allowed = _drift_tolerant_fields(reviewed)
    tolerance = routing.validate_slippage(reviewed.get("max_slippage", "2.5"))
    for key in set(reviewed) | set(fresh):
        if key in {"app", "n"}:
            continue
        if reviewed.get(key) == fresh.get(key):
            continue
        direction = allowed.get(key)
        if direction is None:
            raise HTTPException(409, "Wallet LP quote changed; review it again before signing")
        old, new = Decimal(str(reviewed[key])), Decimal(str(fresh[key]))
        minimum = routing.floor(old * (1 - tolerance / 100))
        maximum = routing.ceiling(old * (1 + tolerance / 100))
        if (direction == "floor" and new < minimum) or (direction == "ceiling" and new > maximum):
            raise HTTPException(409, "Wallet LP quote moved outside the reviewed limit")


@router.post("/api/wallet/lp/quote")
async def quote(request: Request, player: str | None = None):
    """: a live quote for the swap card while the amount is typed -- exactly one pools read, no
    operation, no stored figure. Both directions (G2 proven 2026-09-28); a gated kind is refused."""
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "wallet_lp")
    kind = body.get("kind")
    if kind not in {"swap_sell", "swap_buy"}:
        raise HTTPException(422, "Unsupported wallet LP quote")
    if kind in LP_GATE_REASONS:
        raise HTTPException(409, LP_GATE_REASONS[kind])
    try:
        pools = await asyncio.to_thread(_pools)
    except Exception as exc:
        raise HTTPException(502, "Liquidity pools are unavailable") from exc
    try:
        target = {}
        amount = body.get("amount")
        if body.get("target_output") is not None:  # "You receive" was typed; work out "You pay"
            paid, capped = input_for_output(kind, body.get("resource_symbol"), body.get("target_output"), body.get("max_input"), pools)
            amount = routing.text(paid)
            target = {"target_output": routing.text(routing.amount(body.get("target_output"), positive=True)), "capped": capped}
        view = swap_view(kind, body.get("resource_symbol"), amount, pools)
    except routing.RoutingError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"player": player, **view, **target, "upstream_calls": {"pools": 1}}


@router.post("/api/wallet/lp/preview")
async def preview(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "wallet_lp")
    kind = _kind(body)
    settings = request.app.state.ceres_config
    action = capabilities(bool(settings.wallet_lp_signing_enabled))[kind]
    signing = body.get("sign", False)
    if type(signing) is not bool:
        raise HTTPException(422, "sign must be a boolean")
    if kind in LP_GATE_REASONS:
        raise HTTPException(409, str(action["reason"]))
    if signing and not action["enabled"]:
        raise HTTPException(409, str(action["reason"]))
    _check_body_shapes(body)  # (audit #9): wrong input types are refused before any upstream read
    try:
        pools, balances, regional = await asyncio.to_thread(_fresh_state, player)
        position = None
        if kind == "remove":  # the game's own position read, for the early-withdrawal fee (one call)
            position = await asyncio.to_thread(_position, player, operations._swap_symbol(body.get("resource_symbol")))
    except routing.RoutingError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Fresh wallet LP state is unavailable; nothing was signed") from exc
    try:
        built = _build(player, body, pools, balances, regional, position)
    except routing.RoutingError as exc:
        raise HTTPException(422, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, "Fresh wallet LP state is unavailable; nothing was signed") from exc
    one_click = signing and kind in {"swap_sell", "swap_buy", "add", "remove"} and "reviewed_payload" not in body
    if signing and not one_click:
        reviewed = body.get("reviewed_payload")
        if not isinstance(reviewed, dict):
            raise HTTPException(422, "Reviewed payload is required before signing")
        operations.validate_payload(reviewed)
        if reviewed.get("n") != built["nonce"]:
            raise HTTPException(409, "Wallet LP intent nonce changed; review it again")
        _check_drift(reviewed, built["payload"])
    extra = {}
    if kind in {"swap_sell", "swap_buy"}:
        try:
            view = swap_view(kind, body.get("resource_symbol"),
                             body.get("resource_amount" if kind == "swap_sell" else "dec_amount"), pools)
            extra = {"quote": view, "projected": _projected(kind, view, balances, regional, body.get("region_uid"))}
        except (routing.RoutingError, ValueError, KeyError) as exc:
            raise HTTPException(422, "Fresh wallet LP figures are unavailable") from exc
    if kind == "add":
        extra = {"deposit": built["deposit"]}
    if kind == "remove":
        extra = {"withdraw": built["withdraw"]}
    if one_click and kind in {"add", "remove"}:
        _check_seen_pool(kind, body, extra)
    elif one_click:
        # no Review stage. The click sends the
        # "You receive" figure the maintainer saw; the fresh build must still give at least that minus the fixed tolerance.
        try:
            seen = routing.amount(body.get("seen_output"), positive=True)
        except routing.RoutingError as exc:
            raise HTTPException(422, "The swap figure the card showed is required") from exc
        floor_seen = routing.floor(seen * (1 - routing.validate_slippage(SWAP_SLIPPAGE) / 100))
        if Decimal(extra["quote"]["output"]) < floor_seen:
            raise HTTPException(409, "Wallet LP quote moved outside the shown figure")
    return {**extra, "player": player, "kind": kind, "enabled": action["enabled"], "reason": action["reason"],
            "signing_enabled": bool(settings.wallet_lp_signing_enabled), "operations": built["operations"],
            "payload": built["payload"], "nonce": built["nonce"], "digest": _digest(built["payload"]),
            "authority": "Posting", "upstream_calls": {"pools": 1, "balances": 1, "regional_balances": 1}}


def _figure_ok(value) -> bool:
    return value is None or (not isinstance(value, bool) and isinstance(value, (str, int, float)))


def _check_body_shapes(body: dict) -> None:
    """ (audit #9): the review fields a preview reads deep in its builders are checked for type up front, so a malformed
    body is a 422 with no upstream call instead of an exception after the fresh reads."""
    seen = body.get("seen")
    if seen is not None and (not isinstance(seen, dict) or not all(_figure_ok(seen.get(key)) for key in ("resource_amount", "dec_amount"))):
        raise HTTPException(422, "The figures the card showed are required")
    if not all(_figure_ok(body.get(key)) for key in ("seen_output", "seen_dec", "resource_amount", "dec_amount", "shares_out", "amount")):
        raise HTTPException(422, "Invalid wallet LP figures")
    if "reviewed_payload" in body and not isinstance(body.get("reviewed_payload"), dict):
        raise HTTPException(422, "Reviewed payload is required before signing")
    for key in ("resource_symbol", "region_uid"):
        if body.get(key) is not None and not isinstance(body.get(key), str):
            raise HTTPException(422, "Invalid wallet LP request")


def _check_seen_pool(kind: str, body: dict, extra: dict) -> None:
    """, one click like the swap: the click sends the figures the card showed; the fresh build must not ask
    for more DEC than shown (add) or pay less than shown (withdraw), beyond the fixed 2.5 % tolerance."""
    tolerance = routing.validate_slippage(SWAP_SLIPPAGE) / 100
    try:
        if kind == "add":
            seen = routing.amount(body.get("seen_dec"), positive=True)
            if Decimal(extra["deposit"]["dec_amount"]) > routing.ceiling(seen * (1 + tolerance)):
                raise HTTPException(409, "Pool moved: more DEC is needed than the card showed")
            return
        for key in ("resource_amount", "dec_amount"):
            seen = routing.amount((body.get("seen") or {}).get(key), positive=True)
            if Decimal(extra["withdraw"][key]) < routing.floor(seen * (1 - tolerance)):
                raise HTTPException(409, "Pool moved: the withdrawal would pay less than the card showed")
    except routing.RoutingError as exc:
        raise HTTPException(422, "The figures the card showed are required") from exc


@router.post("/api/wallet/lp/position")
async def position(request: Request, player: str | None = None):
    """: opening Withdraw reads the player's position the way the game does (one call), so the card can show
    the early-withdrawal fee before anything is signed. Nothing is stored."""
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "wallet_lp")
    try:
        symbol = operations._swap_symbol(body.get("resource_symbol"))
        shares, vesting = await asyncio.to_thread(_position, player, symbol)
    except routing.RoutingError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Your pool position is unavailable") from exc
    return {"player": player, "resource_symbol": symbol, "shares": routing.text(shares),
            "locked_shares": routing.text(min(vesting, shares)), "upstream_calls": {"position": 1}}


def _verify(info, player: str, tx: str, body: dict) -> dict:
    # parity: a transaction the game does not know is `not_found`, never an open-ended `pending`.
    if not isinstance(info, dict):
        return {"status": "not_found"}
    if info.get("player") != player:
        raise HTTPException(409, "Transaction belongs to another account")
    if info.get("id") != tx or info.get("type") != "land_operation":
        raise HTTPException(409, "Transaction does not match a wallet LP land operation")
    try:
        data = info.get("data")
        payload = json.loads(data) if isinstance(data, str) else data
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(409, "Transaction payload is unavailable") from exc
    if not isinstance(payload, dict) or payload.get("n") != body.get("nonce"):
        raise HTTPException(409, "Transaction nonce does not match the wallet LP intent")
    try:
        operations.validate_payload(payload, current_app=False)
    except routing.RoutingError as exc:
        raise HTTPException(409, "Transaction is outside the wallet LP operation allowlist") from exc
    expected = body.get("expected_payload")
    if not isinstance(expected, dict):
        raise HTTPException(422, "Expected wallet LP payload is required for verification")
    operations.validate_payload(expected, current_app=False)
    if expected.get("n") != body.get("nonce"):
        raise HTTPException(409, "Expected payload nonce does not match the wallet LP intent")
    expected_kind = body.get("kind")
    expected_op = {"add": "add_liquidity", "remove": "remove_liquidity", "swap_sell": "swap_tokens",
                   "swap_resource": "swap_tokens", "swap_buy": "swap_tokens", "transfer": "swap_tokens"}.get(expected_kind)
    if payload.get("op") != expected_op:
        raise HTTPException(409, "Transaction operation does not match the wallet LP intent")
    if ((expected_kind == "swap_sell" and ("resource_amount" not in payload or "from_symbol" in payload))
            or (expected_kind == "swap_resource" and "from_symbol" not in payload)
            or (expected_kind == "swap_buy" and "dec_amount" not in payload)
            or ((expected_kind == "transfer") != ("to_player" in payload))):  # only a transfer names another player
        raise HTTPException(409, "Transaction swap direction does not match the wallet LP intent")
    if {key: value for key, value in payload.items() if key != "app"} != {key: value for key, value in expected.items() if key != "app"}:
        raise HTTPException(409, "Transaction operation, pool, region or nonce differs from the wallet LP intent")
    success = info.get("success")
    if success is False:
        return {"status": "failed", "message": str(info.get("error") or "The game refused the wallet LP operation")}
    result = info.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = None
    # the game nests its answer one level deeper ({"success", "result": {"success", "error", "data"}})
    # tx <tx id> (the maintainer's SPS sale) came back this way. A refusal inside that envelope is a failure.
    # S3 / L2-06: a refusal anywhere in the answer is a failure, even when a "data" block is also present.
    if isinstance(result, dict):
        inner = result.get("result") if isinstance(result.get("result"), dict) else None
        refusal = _inner_refusal(result)  # S10 / L2-04: the same deep, JSON-text-aware reading the shared verdict uses
        if result.get("success") is False or refusal is not None:
            return {"status": "failed", "message": str(refusal or result.get("error") or "The game refused the wallet LP operation")}
        if inner is not None and "data" not in result:
            result = inner
    if success is not True or not isinstance(result, dict) or not isinstance(result.get("data"), dict):
        return {"status": "pending"}
    return {"status": "confirmed", "game_result": result["data"]}


@router.post("/api/wallet/lp/verify")
async def verify(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "wallet_lp")
    tx, nonce = body.get("tx_id"), body.get("nonce")
    if not isinstance(tx, str) or not _TX.fullmatch(tx) or not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{16}", nonce):
        raise HTTPException(422, "Invalid wallet LP transaction reference")
    try:
        # the game's "Transaction [..] not found." body is `not_found`, not an outage (502).
        info = await asyncio.to_thread(lookup_trx_info, FixedGetTransport(timeout=10, retries=1).get_json, tx)
        return _verify(info, player, tx, body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, "Game confirmation unavailable; check again without signing again") from exc


def find_in_history(rows: list, player: str, nonce: str) -> str | None:
    """: the newest `sm_land_operation` in the player's own recent history carrying this intent's nonce; zero calls."""
    found = None
    for row in rows if isinstance(rows, list) else []:
        entry = row[1] if isinstance(row, list) and len(row) == 2 and isinstance(row[1], dict) else {}
        op, tx_id = entry.get("op"), entry.get("trx_id")
        if not (isinstance(op, list) and len(op) == 2 and op[0] == "custom_json" and isinstance(op[1], dict)
                and isinstance(tx_id, str) and _TX.fullmatch(tx_id)):
            continue
        body = op[1]
        if body.get("id") != operations.OP_ID or body.get("required_posting_auths") != [player]:
            continue
        try:
            payload = json.loads(body.get("json")) if isinstance(body.get("json"), str) else body.get("json")
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("n") == nonce:
            found = tx_id.lower()
    return found


# a broadcast can land a little before the client's clock says the wallet was asked (clock skew, block time).
HISTORY_COVER_MARGIN_S = 120


def _parse_time(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)  # Hive timestamps are UTC without a zone


def history_covers(rows: list, since) -> bool:
    """: did this bounded window reach back to `since`? True when the account's history starts inside it (index 0) or its
    oldest row is older than `since` minus a margin. Without a readable `since` nothing is proven."""
    entries = [row for row in rows if isinstance(row, list) and len(row) == 2 and isinstance(row[0], int) and isinstance(row[1], dict)] if isinstance(rows, list) else []
    if not entries:
        return False
    oldest = min(entries, key=lambda row: row[0])
    if oldest[0] == 0:
        return True
    start, at = _parse_time(since), _parse_time(oldest[1].get("timestamp"))
    return bool(start and at and at <= start - timedelta(seconds=HISTORY_COVER_MARGIN_S))


@router.post("/api/wallet/lp/find")
async def find(request: Request, player: str | None = None):
    """: the wallet returned no transaction id. One read of the
    player's OWN last 100 account operations finds the signed swap by its intent nonce; nothing else is read."""
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "wallet_lp")
    nonce = body.get("nonce")
    if not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{16}", nonce):
        raise HTTPException(422, "Invalid wallet LP intent")
    since = body.get("since")
    if since is not None and _parse_time(since) is None:
        raise HTTPException(422, "Invalid wallet LP intent time")
    from .harvest_routing_bridge import _history
    try:
        rows = await asyncio.to_thread(_history, player, -1)
    except Exception as exc:
        raise HTTPException(502, "Your wallet history could not be read right now") from exc
    tx = find_in_history(rows, player, nonce)
    # only the newest 100 operations are read; `covered` says whether they reach back to when the wallet was asked.
    # A not_found that is not covered proves nothing (the client treats it as unknown).
    return {"status": "found", "tx_id": tx} if tx else {"status": "not_found", "covered": history_covers(rows, since)}


def find_funding_in_history(rows: list, player: str, recipient: str, memo: str, quantity) -> str | None:
    """ (audit #4): the SPS funding transfer whose wallet answer was lost, found in the player's OWN history by its
    memo (lpsid:<id> / lpdid:<id>, unique per pending swap or deposit): an Active `sm_sps` token_transfer to the pool account
    for exactly the pending amount. Zero calls."""
    found = None
    for row in rows if isinstance(rows, list) else []:
        entry = row[1] if isinstance(row, list) and len(row) == 2 and isinstance(row[1], dict) else {}
        op, tx_id = entry.get("op"), entry.get("trx_id")
        if not (isinstance(op, list) and len(op) == 2 and op[0] == "custom_json" and isinstance(op[1], dict)
                and isinstance(tx_id, str) and _TX.fullmatch(tx_id)):
            continue
        body = op[1]
        if body.get("id") != "sm_sps" or body.get("required_auths") != [player]:
            continue
        try:
            payload = json.loads(body.get("json")) if isinstance(body.get("json"), str) else body.get("json")
        except ValueError:
            continue
        params = payload.get("params") if isinstance(payload, dict) and payload.get("action") == "token_transfer" else None
        if not isinstance(params, dict) or params.get("to") != recipient or params.get("token") != "SPS" or params.get("memo") != memo:
            continue
        try:
            if routing.amount(params.get("qty"), positive=True) != quantity:
                continue
        except routing.RoutingError:
            continue
        found = tx_id.lower()
    return found


def _pending_id(effects: dict, key: str):
    value = effects.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


SPS_BRIDGE_ACCOUNT = "player0016"  # the game's SPS/DEC pool account (a tx <tx id>: params.to)


def sps_funding_operation(player: str, info, tx: str, body: dict) -> dict:
    """ step 2 of an SPS sale: the game answered the Posting swap with a pending swap id; the SPS itself
    must then be sent to the pool account with the Active key (`sm_sps` token_transfer, memo `lpsid:<id>`).
    Built by the shared harvest-routing funding builder, only from a confirmed wallet-LP SPS sale."""
    deposit = body.get("kind") == "add"  # an SPS deposit is paid the same way (memo lpdid:<pending_deposit_id>)
    verdict = _verify(info, player, tx, {**body, "kind": "add" if deposit else "swap_sell"})
    if verdict.get("status") != "confirmed":
        return {"status": verdict.get("status"), "operations": None}
    expected = body.get("expected_payload") or {}
    effects = verdict["game_result"]
    if deposit:
        if expected.get("resource_symbol") != "SPS" or not effects.get("pending_deposit_id"):
            raise HTTPException(409, "This deposit needs no SPS transfer")
        if effects.get("external_transfer_account") != SPS_BRIDGE_ACCOUNT:
            raise HTTPException(409, "The game named an unexpected SPS pool account")
        if routing.amount(effects.get("pending_external_amount") or effects.get("resource_amount"), positive=True) != routing.amount(expected.get("resource_amount"), positive=True):
            raise HTTPException(409, "The pending SPS amount differs from the reviewed deposit")
        try:
            funding = harvest_operations.build_sps_funding(player, {**effects, "pending_external_amount": effects.get("pending_external_amount") or effects.get("resource_amount")}, "liquidity")
        except routing.RoutingError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"status": "ready", "operations": funding["operations"], "payload": funding["payload"], "authority": "Active",
                "pending_deposit_id": funding["pending_id"], "amount": funding["amount"], "recipient": funding["recipient"]}
    if expected.get("resource_symbol") != "SPS" or effects.get("external_swap_required") is not True:
        raise HTTPException(409, "This swap needs no SPS transfer")
    if effects.get("external_transfer_account") != SPS_BRIDGE_ACCOUNT:
        raise HTTPException(409, "The game named an unexpected SPS pool account")
    if routing.amount(effects.get("pending_external_amount"), positive=True) != routing.amount(expected.get("resource_amount"), positive=True):
        raise HTTPException(409, "The pending SPS amount differs from the reviewed sale")
    try:
        funding = harvest_operations.build_sps_funding(player, effects, "sell")
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "ready", "operations": funding["operations"], "payload": funding["payload"], "authority": "Active",
            "pending_swap_id": funding["pending_id"], "amount": funding["amount"], "recipient": funding["recipient"]}


@router.post("/api/wallet/lp/sps-funding")
async def sps_funding(request: Request, player: str | None = None):
    """One lookup of the signed SPS sale, then the Active transfer it asks for (never signed here)."""
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "wallet_lp")
    if not request.app.state.ceres_config.wallet_lp_signing_enabled:
        raise HTTPException(409, "Wallet LP signing is disabled by CERES_WALLET_LP_SIGNING.")
    tx, nonce = body.get("tx_id"), body.get("nonce")
    if not isinstance(tx, str) or not _TX.fullmatch(tx) or not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{16}", nonce):
        raise HTTPException(422, "Invalid wallet LP transaction reference")
    try:
        response = await asyncio.to_thread(FixedGetTransport(timeout=10, retries=1).get_json,
                                           API_ORIGIN + "/transactions/lookup?" + urlencode({"trx_id": tx}))
    except Exception as exc:
        raise HTTPException(502, "Game confirmation unavailable; nothing was signed") from exc
    return {"player": player, **sps_funding_operation(player, response.data.get("trx_info"), tx, body)}


def settlement_step(tx: str, kind: str, symbol: str, effects: dict, funded: bool, cursor=None) -> dict:
    """: the routing-bridge `settle()` step for one confirmed wallet-LP swap whose value moves outside the game:
    a DEC->SPS buy paid by an external SPS withdrawal, or an SPS sale settled by an external swap once funded."""
    return {"kind": "liquidity" if kind == "add" else "swap", "tx_id": tx, "game_result": {"result": {"data": effects}},
            "funding": {"state": "funded" if funded else "unfunded"},
            "review": {"step": {"quote": {"destination_symbol": "DEC" if kind == "swap_sell" else symbol},
                                "destination_region": routing.INVENTORY}},
            "bridge_next_start": cursor}


def settlement_view(settled: dict) -> dict:
    if settled.get("state") == "refunded":
        return {"status": "refunded", "bridge_tx_id": settled.get("bridge_transaction_id"), "refunded": settled.get("refunded") or [],
                "expected": settled.get("expected"), "full": settled.get("full") is True, "reason": settled.get("reason") or ""}
    if settled.get("state") != "confirmed":
        view = {"status": "settling", "next_cursor": settled.get("bridge_next_start"), "note": settled.get("bridge_search_note")}
        return {**view, "refund_pending": True} if settled.get("refund_pending") else view
    receipt = settled.get("receipt") or {}
    return {"status": "settled", "bridge_tx_id": settled.get("bridge_transaction_id"), "received": receipt.get("received") or [],
            "deposited": receipt.get("deposited") or []}


@router.post("/api/wallet/lp/settle")
async def settle_swap(request: Request, player: str | None = None):
    """ (stuck states): has the pool settled this confirmed swap? ONLY on the maintainer's explicit "Check again" -- `settle()` reads one bounded window of the
    pool account's public Hive history, so no client ever calls this on a timer or on open."""
    from .harvest_routing_bridge import settle
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "wallet_lp")
    tx, nonce, kind = body.get("tx_id"), body.get("nonce"), body.get("kind")
    if not isinstance(tx, str) or not _TX.fullmatch(tx) or not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{16}", nonce) or kind not in ("swap_sell", "swap_buy", "add", "remove"):
        raise HTTPException(422, "Invalid wallet LP transaction reference")
    cursor = body.get("cursor")
    if cursor is not None and (isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0):
        raise HTTPException(422, "Invalid history cursor")
    funding_tx = body.get("funding_tx")
    try:
        info = await asyncio.to_thread(lookup_trx_info, FixedGetTransport(timeout=10, retries=1).get_json, tx)
        verdict = _verify(info, player, tx, body)
        if verdict.get("status") != "confirmed":
            return {"status": verdict.get("status")}
        effects = verdict["game_result"]
        withdrawal = effects.get("external_withdrawal_required") is True
        # (audit #3): a pending SPS deposit is paid by the pool bridge too; it is never "settled" before the bridge confirms.
        deposit = kind == "add" and _pending_id(effects, "pending_deposit_id") is not None
        if not withdrawal and effects.get("external_swap_required") is not True and not deposit:
            return {"status": "settled", "received": []}
        symbol = operations._swap_symbol((body.get("expected_payload") or {}).get("resource_symbol"))
        funded = isinstance(funding_tx, str) and bool(_TX.fullmatch(funding_tx))
        found_funding = None
        if not funded and not withdrawal:
            # (audit #4): the wallet's answer to the SPS transfer was lost. Look for that transfer in the player's OWN recent
            # history by its memo (/: only the maintainer's own transfer, by its reference). Not found proves nothing
            # the swap stays settling and nothing is offered for signing again.
            pending = _pending_id(effects, "pending_deposit_id" if deposit else "pending_swap_id")
            amount_text = effects.get("pending_external_amount") or (effects.get("resource_amount") if deposit else None)
            if pending is not None and amount_text is not None:
                from .harvest_routing_bridge import _history
                rows = await asyncio.to_thread(_history, player, -1)
                found_funding = find_funding_in_history(rows, player, str(effects.get("external_transfer_account") or ""),
                                                        f"{'lpdid' if deposit else 'lpsid'}:{pending}", routing.amount(amount_text, positive=True))
            funded = found_funding is not None
            if not funded:
                return {"status": "settling", "funding": "not_found"}
        step = settlement_step(tx, kind, symbol, effects, funded, cursor)
        settled = await asyncio.to_thread(settle, player, step, older=cursor is not None)
        view = settlement_view(settled)
        return {**view, "funding_tx": found_funding} if found_funding else view
    except HTTPException:
        raise
    except routing.RoutingError as exc:
        return {"status": "settling", "note": str(exc)}
    except Exception as exc:
        raise HTTPException(502, "The pool settlement could not be read; check again later") from exc
