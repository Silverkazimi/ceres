""": player-to-player transfer of a land resource (Grain, Wood, Stone, Iron) from the wallet's Transfer tab.

The game's own transfer (maintainer player0005 -> player0004, trx <tx id>, 2026-10-13, Posting) is the region-to-region
`swap_tokens` through the resource's pool -- resource -> DEC -> resource, two legs at 5 % each -- with
from_symbol = to_symbol and `to_player`. The quote is the routing quote (`harvest_routing.swap_quote`), the fields
come from the routing builder (`harvest_routing_operations.region_swap_fields`); signing, the intent record and the
game confirmation are the wallet swap's own (`wallet-lp.js signIntent/verify`, `POST /api/wallet/lp/verify`).

Reads happen only on the maintainer's press, by account name (no history, no scan -- ):
  recipient -> 3 calls (the Hive account, the Splinterlands player, the recipient's land regions)
  quote     -> 1 call  (the pools)
  preview   -> 3 calls (the pools, the sender's regions, the recipient's regions)
Nothing is stored server-side."""
from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from praetoria.errors import UpstreamError
from praetoria.land_analysis import LandClient
from praetoria.transport import API_ORIGIN, FixedGetTransport

from . import client_limits, harvest_routing as routing
from . import wallet_lp_operations as operations
from .hive_authority import AuthorityReadError, _get_accounts
from .routes_harvest_routing import _pools
from .routes_wallet import SWAP_SLIPPAGE, regions_section
from .routes_wallet_lp import _body, _check_drift, _digest
from .security import player_for_request, validate_player

router = APIRouter()
LEG_FEE_PERCENT = "5"      # each of the two pool legs (maintainer fact, )
TOTAL_FEE_PERCENT = "10"


def _recipient_name(value, player: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(422, "Enter a Hive account name")
    name = value.strip().lstrip("@").lower()
    try:
        validate_player(name)
    except ValueError as exc:
        raise HTTPException(422, "That is not a valid Hive account name") from exc
    if name == player:
        raise HTTPException(422, "Choose another player; moving between your own regions is a swap or a route")
    return name


def recipient_regions(payload) -> list[dict]:
    """The recipient's land regions from `/land/liquidity/region/{player}` (rows exist where they own plots or hold
    resources), most plots first. Names and plot counts only; the recipient's balances are not shown."""
    rows = regions_section(payload)
    rows.sort(key=lambda row: (-(row["plots_owned"] or 0), int(row["region_uid"].rsplit("-", 1)[1])))
    return [{"region_uid": row["region_uid"], "name": row["name"], "plots_owned": row["plots_owned"]} for row in rows]


@lru_cache(maxsize=1)
def all_regions() -> tuple[dict, ...]:
    """ (maintainer: a recipient without land gets every region): the static list of the 143 Praetoria regions
    (ceres/data/praetoria_regions.json, read once from the game API by region number). No upstream call."""
    data = json.loads((Path(__file__).parent / "data" / "praetoria_regions.json").read_text(encoding="utf-8"))
    return tuple({"region_uid": row["region_uid"], "name": row["name"], "plots_owned": 0} for row in data["regions"])


def _hive_exists(name: str) -> bool:
    return any(row.get("name") == name for row in _get_accounts([name]))


def _splinterlands_exists(name: str) -> bool:
    try:
        data = FixedGetTransport(timeout=10, retries=1).get_json(API_ORIGIN + "/players/details?" + urlencode({"name": name})).data
    except UpstreamError as exc:  # the game answers {"error": "Player @<name> not found."} for an unknown player
        if f"@{name} not found" in str(exc):
            return False
        raise
    if isinstance(data, dict) and data.get("name") == name:
        return True
    if isinstance(data, dict) and "not found" in str(data.get("error") or "").lower():
        return False
    raise RuntimeError("player details unavailable")


def _regions(name: str):
    return LandClient(timeout=10, retries=1).regional_balances(name).data


def read_recipient(name: str) -> dict:
    with ThreadPoolExecutor(max_workers=3) as pool:
        hive, game, regions = pool.submit(_hive_exists, name), pool.submit(_splinterlands_exists, name), pool.submit(_regions, name)
        hive, game, regions = hive.result(), game.result(), regions.result()
    if not hive:
        return {"status": "no_hive_account", "account": name}
    if not game:
        return {"status": "no_splinterlands_account", "account": name}
    rows = recipient_regions(regions)
    if not rows:  # no land and no resources anywhere -> the maintainer picks any region
        return {"status": "ok", "account": name, "no_land": True, "regions": list(all_regions())}
    return {"status": "ok", "account": name, "no_land": False, "regions": rows}


def transfer_view(symbol, value, pools: dict) -> dict:
    """The two-leg quote the Transfer tab shows: leg 1 resource -> DEC, leg 2 DEC -> resource, 5 % fee on each,
    with the fixed 2.5 % slippage. One pools read; builds no operation."""
    symbol = operations._symbol(symbol)
    quote = routing.swap_quote(symbol, symbol, value, pools, SWAP_SLIPPAGE)
    if quote.external:
        raise routing.RoutingError("This resource has no internal pool")
    leg = Decimal(1) - pools[symbol].factor(True)
    return {"resource_symbol": symbol, "input": routing.text(quote.input), "intermediate_dec": routing.text(quote.intermediate_dec),
            "output": routing.text(quote.output), "minimum": routing.text(quote.minimum), "slippage": SWAP_SLIPPAGE,
            "legs": [{"pay": symbol, "receive": "DEC", "fee_percent": LEG_FEE_PERCENT, "fee": routing.text(routing.floor(quote.input * leg)), "fee_symbol": symbol},
                     {"pay": "DEC", "receive": symbol, "fee_percent": LEG_FEE_PERCENT, "fee": routing.text(routing.floor(quote.intermediate_dec * leg)), "fee_symbol": "DEC"}],
            "fee_percent": TOTAL_FEE_PERCENT}


def build_transfer(player: str, body: dict, pools: dict, own_regions: dict, their_regions: dict) -> dict:
    symbol = operations._symbol(body.get("resource_symbol"))
    to_player = body.get("to_player")
    from_region, to_region = body.get("from_region_uid"), body.get("to_region_uid")
    mine = {row["region_uid"]: row["balances"] for row in regions_section(own_regions)}
    if from_region not in mine:
        raise routing.RoutingError("Fresh balance for the selected region is unavailable")
    theirs = {row["region_uid"] for row in recipient_regions(their_regions)}
    allowed = theirs or {row["region_uid"] for row in all_regions()}  # no land -> any existing region
    if to_region not in allowed:
        raise routing.RoutingError("The recipient has no land in the selected region" if theirs else "That region does not exist")
    view = transfer_view(symbol, body.get("in_amount"), pools)
    if Decimal(str(mine[from_region][symbol])) < Decimal(view["input"]):
        raise routing.RoutingError("Fresh balance is too low")
    intermediate_dec, output = view["intermediate_dec"], view["output"]
    reviewed = body.get("reviewed_payload") if body.get("sign") is True else None
    if isinstance(reviewed, dict):
        # The game applies max_slippage to both expected-output legs. Keep the two
        # reviewed targets in the final payload; the fresh quote only proves that
        # each reviewed floor remains attainable.
        tolerance = routing.validate_slippage(reviewed.get("max_slippage", SWAP_SLIPPAGE))
        reviewed_intermediate = routing.amount(reviewed.get("out_amount_1"), positive=True)
        reviewed_output = routing.amount(reviewed.get("out_amount_2"), positive=True)
        if (Decimal(view["intermediate_dec"]) < routing.floor(reviewed_intermediate * (1 - tolerance / 100))
                or Decimal(view["output"]) < routing.floor(reviewed_output * (1 - tolerance / 100))):
            raise routing.RoutingError("Pool amounts changed; review the transfer again")
        intermediate_dec, output = routing.text(reviewed_intermediate), routing.text(reviewed_output)
    built = operations.transfer(player, from_region, to_player, to_region, symbol, view["input"], intermediate_dec,
                                output, SWAP_SLIPPAGE, nonce=body.get("nonce"))
    after = routing.floor(Decimal(str(mine[from_region][symbol])) - Decimal(view["input"]))
    return {**built, "quote": view, "projected": {"region_after": routing.text(after)}}


@router.post("/api/wallet/transfer/recipient")
async def recipient(request: Request, player: str | None = None):
    """The maintainer pressed Find: is this an existing Hive and Splinterlands account, and in which regions does it have land?"""
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "wallet_lp")
    name = _recipient_name(body.get("to_player"), player)
    try:
        found = await asyncio.to_thread(read_recipient, name)
    except (AuthorityReadError, Exception) as exc:
        raise HTTPException(502, "The account could not be checked right now") from exc
    return {"player": player, **found, "upstream_calls": {"hive_account": 1, "player_details": 1, "regions": 1}}


@router.post("/api/wallet/transfer/quote")
async def quote(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "wallet_lp")
    try:
        pools = await asyncio.to_thread(_pools)
    except Exception as exc:
        raise HTTPException(502, "Liquidity pools are unavailable") from exc
    try:
        view = transfer_view(body.get("resource_symbol"), body.get("amount"), pools)
    except routing.RoutingError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"player": player, **view, "upstream_calls": {"pools": 1}}


def _fresh(player: str, to_player: str):
    with ThreadPoolExecutor(max_workers=3) as pool:
        pools, own, theirs = pool.submit(_pools), pool.submit(_regions, player), pool.submit(_regions, to_player)
        return pools.result(), own.result(), theirs.result()


@router.post("/api/wallet/transfer/preview")
async def preview(request: Request, player: str | None = None):
    """Review (sign false) and the build handed to the wallet (sign true). Fresh pool legs must still meet the
    reviewed floors, while the signed payload keeps the reviewed output targets."""
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "wallet_lp")
    signing = body.get("sign", False)
    if type(signing) is not bool:
        raise HTTPException(422, "sign must be a boolean")
    enabled = bool(request.app.state.ceres_config.wallet_lp_signing_enabled)
    if signing and not enabled:
        raise HTTPException(409, "Wallet signing is disabled by CERES_WALLET_LP_SIGNING.")
    to_player = _recipient_name(body.get("to_player"), player)
    for key in ("resource_symbol", "from_region_uid", "to_region_uid"):
        if not isinstance(body.get(key), str):
            raise HTTPException(422, "Invalid transfer request")
    if body.get("in_amount") is None or isinstance(body.get("in_amount"), (bool, dict, list)):
        raise HTTPException(422, "Invalid transfer amount")
    reviewed = body.get("reviewed_payload")
    if signing:
        if not isinstance(reviewed, dict):
            raise HTTPException(422, "Review the transfer before signing")
        try:
            operations.validate_payload(reviewed)
        except routing.RoutingError as exc:
            raise HTTPException(422, "Review the transfer again") from exc
        body = {**body, "nonce": reviewed.get("n")}
    try:
        pools, own, theirs = await asyncio.to_thread(_fresh, player, to_player)
    except Exception as exc:
        raise HTTPException(502, "Fresh balances are unavailable; nothing was signed") from exc
    try:
        built = build_transfer(player, {**body, "to_player": to_player}, pools, own, theirs)
    except (routing.RoutingError, ValueError, KeyError) as exc:
        if signing and str(exc) == "Pool amounts changed; review the transfer again":
            raise HTTPException(409, "Pool amounts changed; review the transfer again") from exc
        raise HTTPException(422, str(exc)) from exc
    if signing:
        if reviewed.get("n") != built["nonce"] or reviewed.get("to_player") != to_player:
            raise HTTPException(409, "The transfer changed; review it again")
        _check_drift(reviewed, built["payload"])
    return {"player": player, "kind": "transfer", "signing_enabled": enabled, "operations": built["operations"],
            "payload": built["payload"], "nonce": built["nonce"], "digest": _digest(built["payload"]), "authority": "Posting",
            "quote": built["quote"], "projected": built["projected"], "to_player": to_player,
            "upstream_calls": {"pools": 1, "regional_balances": 1, "recipient_regions": 1}}
