"""Pure Posting-only wallet-LP operation builders and exact-shape validation."""
from __future__ import annotations

import json
import re
import secrets
from decimal import Decimal

from . import harvest_routing as routing
from . import resources as _res
from .security import validate_player
from .transaction_identity import app_identity

OP_ID = "sm_land_operation"
RESOURCE_SYMBOLS = frozenset(_res.symbols_all("regional", "poolable"))  # registry view: GRAIN WOOD STONE IRON
# the two DEC pools whose token lives in the account wallet, not in a region. The game swaps them with
# `region_uid: ""` (maintainer txs 2026-09-28: DEC->SPS <tx id>, DEC->VOUCHER <tx id>, VOUCHER->DEC <tx id>
# SPS->DEC <tx id> + the Active `sm_sps` transfer <tx id>).
ACCOUNT_SYMBOLS = frozenset({"VOUCHER", "SPS"})
_REGION = re.compile(r"PR-[A-Z]{3}-[1-9][0-9]{0,3}")
_NONCE = re.compile(r"[a-f0-9]{16}")
_APP = re.compile(r"ceres/\d+\.\d+\.\d+")


def _region(value: str) -> str:
    if not isinstance(value, str) or not _REGION.fullmatch(value):
        raise routing.RoutingError("Invalid wallet LP region")
    return value


def _swap_symbol(value: str) -> str:
    if value not in RESOURCE_SYMBOLS | ACCOUNT_SYMBOLS:
        raise routing.RoutingError("Unsupported wallet LP resource")
    return value


def _swap_region(value: str, symbol: str) -> str:
    """A land resource swaps in a region; a wallet token (Voucher, SPS) swaps with an empty region_uid."""
    if _swap_symbol(symbol) in ACCOUNT_SYMBOLS:
        if value != "":
            raise routing.RoutingError("Voucher and SPS pool operations carry no region")
        return value
    return _region(value)


def _recipient(value) -> str:
    try:
        return validate_player(value)
    except ValueError as exc:
        raise routing.RoutingError("Invalid recipient account") from exc


def _symbol(value: str) -> str:
    if value not in RESOURCE_SYMBOLS:
        raise routing.RoutingError("Unsupported wallet LP resource")
    return value


def _amount(value, *, positive: bool = True) -> Decimal:
    number = routing.amount(value, positive=positive)
    if number != routing.floor(number):
        raise routing.RoutingError("Use at most three decimal places")
    return number


def _fraction(value) -> Decimal:
    number = routing.amount(value, positive=True)
    if number > 1 or number != number.quantize(Decimal("0.001")):
        raise routing.RoutingError("Removal fraction must be in (0, 1] with at most three decimal places")
    return number


def _build(player: str, fields: dict, *, nonce: str | None = None) -> dict:
    player = validate_player(player)
    app = app_identity()
    nonce = secrets.token_hex(8) if nonce is None else nonce
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise routing.RoutingError("Wallet LP nonce must be 16 lowercase hexadecimal characters")
    payload = {**fields, "app": app, "n": nonce}
    validate_payload(payload)
    operation = ["custom_json", {"id": OP_ID, "json": json.dumps(payload, separators=(",", ":")),
                                  "required_auths": [], "required_posting_auths": [player]}]
    return {"operations": [operation], "payload": payload, "nonce": nonce, "authority": "Posting"}


def add(player: str, region_uid: str, symbol: str, resource_amount, dec_amount, *, nonce: str | None = None) -> dict:
    """: every pool token. A land resource comes from a region; Voucher and SPS from the account wallet
    (region_uid "", as the game's own Voucher hub sends it). SPS settles through the bridge (a second transfer)."""
    return _build(player, {"op": "add_liquidity", "region_uid": _swap_region(region_uid, symbol),
                           "resource_amount": float(_amount(resource_amount)), "dec_amount": float(_amount(dec_amount)),
                           "shares_out": 0, "resource_symbol": _swap_symbol(symbol)}, nonce=nonce)


def sell(player: str, region_uid: str, symbol: str, resource_amount, expected_dec, slippage,
         *, nonce: str | None = None) -> dict:
    return _build(player, {"op": "swap_tokens", "region_uid": _swap_region(region_uid, symbol),
                           "resource_amount": float(_amount(resource_amount)), "shares_out": float(_amount(expected_dec)),
                           "max_slippage": float(routing.validate_slippage(slippage)), "resource_symbol": _swap_symbol(symbol)}, nonce=nonce)


def resource_swap(player: str, from_region_uid: str, to_region_uid: str, from_symbol: str, to_symbol: str,
                  in_amount, out_amount_1, out_amount_2, slippage, *, nonce: str | None = None) -> dict:
    return _build(player, {"op": "swap_tokens", "from_region_uid": _region(from_region_uid),
                           "to_region_uid": _region(to_region_uid), "in_amount": float(_amount(in_amount)),
                           "out_amount_1": float(_amount(out_amount_1)), "out_amount_2": float(_amount(out_amount_2)),
                           "from_symbol": _symbol(from_symbol), "to_symbol": _symbol(to_symbol),
                           "max_slippage": float(routing.validate_slippage(slippage))}, nonce=nonce)


def transfer(player: str, from_region_uid: str, to_player: str, to_region_uid: str, symbol: str,
             in_amount, out_amount_1, out_amount_2, slippage, *, nonce: str | None = None) -> dict:
    """: a land resource sent to ANOTHER player's region. The game's own transaction (maintainer player0005 -> player0004,
    <tx id>, 2026-10-13, Posting) is the region-to-region swap through the resource's pool -- two legs, 5 % each -- with
    from_symbol = to_symbol and `to_player`. The fields come from the routing builder (`region_swap_fields`)."""
    from .harvest_routing_operations import region_swap_fields
    recipient = _recipient(to_player)
    if recipient == validate_player(player):
        raise routing.RoutingError("Choose another player to transfer to")
    fields = region_swap_fields(_symbol(symbol), symbol, _region(from_region_uid), _region(to_region_uid),
                                _amount(in_amount), _amount(out_amount_1), _amount(out_amount_2))
    return _build(player, {"op": "swap_tokens", "max_slippage": float(routing.validate_slippage(slippage)), **fields,
                           "to_player": recipient}, nonce=nonce)


def remove(player: str, region_uid: str, symbol: str, fraction, *, nonce: str | None = None) -> dict:
    """, G1 proven: `shares_out` is the fraction of the player's OWN position, three decimals -- the game's
    client sends `parseFloat((percent / 100).toFixed(3))` (Manage view, splinterlands.com bundle 2026-10-04), and
    cryptomancer's removals <tx id> (0.03) and <tx id> (0.2) paid out in the pool's ratio. The resource lands in
    `region_uid` (empty for Voucher and SPS); DEC goes to the account."""
    return _build(player, {"op": "remove_liquidity", "region_uid": _swap_region(region_uid, symbol),
                           "resource_amount": 0, "dec_amount": 0, "shares_out": float(_fraction(fraction)),
                           "resource_symbol": _swap_symbol(symbol)}, nonce=nonce)


def buy(player: str, region_uid: str, symbol: str, dec_amount, expected_resource, slippage,
        *, nonce: str | None = None) -> dict:
    """DEC -> resource. Matches the game's own swap transaction shape (Posting, sm_land_operation): {"op":"swap_tokens","region_uid":"PR-BRL-41","dec_amount":1,"shares_out":104.201,
    "max_slippage":2.5,"resource_symbol":"GRAIN",...}: region_uid is where the resource lands, shares_out the expected
    resource out."""
    return _build(player, {"op": "swap_tokens", "region_uid": _swap_region(region_uid, symbol),
                           "dec_amount": float(_amount(dec_amount)), "shares_out": float(_amount(expected_resource)),
                           "max_slippage": float(routing.validate_slippage(slippage)), "resource_symbol": _swap_symbol(symbol)},
                  nonce=nonce)


def validate_payload(payload: dict, *, current_app: bool = True) -> None:
    """Reject every wallet-LP JSON shape except the six documented transaction forms ( added the transfer).
    : confirming an already-signed swap passes current_app=False -- a Ceres update between signing and
    confirming must not make the swap unconfirmable; any valid ceres/x.y.z identity is accepted then."""
    if (not isinstance(payload, dict) or not isinstance(payload.get("app"), str)
            or not _APP.fullmatch(payload["app"]) or (current_app and payload["app"] != app_identity())):
        raise routing.RoutingError("Invalid wallet LP app identity")
    if not isinstance(payload.get("n"), str) or not _NONCE.fullmatch(payload["n"]):
        raise routing.RoutingError("Invalid wallet LP nonce")
    op = payload.get("op")
    add_keys = {"op", "region_uid", "resource_amount", "dec_amount", "shares_out", "resource_symbol", "app", "n"}
    remove_keys = add_keys
    sell_keys = {"op", "region_uid", "resource_amount", "shares_out", "max_slippage", "resource_symbol", "app", "n"}
    buy_keys = {"op", "region_uid", "dec_amount", "shares_out", "max_slippage", "resource_symbol", "app", "n"}
    swap_keys = {"op", "from_region_uid", "to_region_uid", "in_amount", "out_amount_1", "out_amount_2",
                 "from_symbol", "to_symbol", "max_slippage", "app", "n"}
    if op == "add_liquidity":
        if set(payload) != add_keys or payload["shares_out"] != 0:
            raise routing.RoutingError("Invalid add-liquidity payload shape")
        _swap_region(payload["region_uid"], payload["resource_symbol"])
        _amount(payload["resource_amount"]); _amount(payload["dec_amount"])
        return
    if op == "remove_liquidity":
        if set(payload) != remove_keys or payload["resource_amount"] != 0 or payload["dec_amount"] != 0:
            raise routing.RoutingError("Invalid remove-liquidity payload shape")
        _swap_region(payload["region_uid"], payload["resource_symbol"]); _fraction(payload["shares_out"])
        return
    if op != "swap_tokens":
        raise routing.RoutingError("Unsupported wallet LP operation")
    if set(payload) == sell_keys:
        _swap_region(payload["region_uid"], payload["resource_symbol"])
        _amount(payload["resource_amount"]); _amount(payload["shares_out"]); routing.validate_slippage(payload["max_slippage"])
        return
    if set(payload) == buy_keys:
        _swap_region(payload["region_uid"], payload["resource_symbol"])
        _amount(payload["dec_amount"]); _amount(payload["shares_out"]); routing.validate_slippage(payload["max_slippage"])
        return
    if set(payload) == swap_keys | {"to_player"}:  # player-to-player transfer of one land resource
        _region(payload["from_region_uid"]); _region(payload["to_region_uid"])
        _symbol(payload["from_symbol"])
        if payload["from_symbol"] != payload["to_symbol"]:
            raise routing.RoutingError("A transfer sends one resource")
        _recipient(payload["to_player"])
        for key in ("in_amount", "out_amount_1", "out_amount_2"):
            _amount(payload[key])
        routing.validate_slippage(payload["max_slippage"])
        return
    if set(payload) == swap_keys:
        _region(payload["from_region_uid"]); _region(payload["to_region_uid"])
        _symbol(payload["from_symbol"]); _symbol(payload["to_symbol"])
        if payload["from_symbol"] == payload["to_symbol"]:
            raise routing.RoutingError("Resource swap must use two different resources")
        for key in ("in_amount", "out_amount_1", "out_amount_2"):
            _amount(payload[key])
        routing.validate_slippage(payload["max_slippage"])
        return
    raise routing.RoutingError("Wallet LP payload keys do not match an approved operation")


def validate_operation(operation, player: str) -> dict:
    player = validate_player(player)
    if not isinstance(operation, list) or len(operation) != 2 or operation[0] != "custom_json":
        raise routing.RoutingError("Wallet LP operation must be one custom_json")
    envelope = operation[1]
    if not isinstance(envelope, dict) or set(envelope) != {"id", "json", "required_auths", "required_posting_auths"}:
        raise routing.RoutingError("Invalid wallet LP authority envelope")
    if envelope["id"] != OP_ID or envelope["required_auths"] != [] or envelope["required_posting_auths"] != [player]:
        raise routing.RoutingError("Wallet LP operations require Posting authority for the reviewed player")
    try:
        payload = json.loads(envelope["json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise routing.RoutingError("Invalid wallet LP JSON") from exc
    validate_payload(payload)
    return payload
