"""Unsigned land operations using the official Trade Hub payload contracts."""
from __future__ import annotations

import json
import re
import secrets

from . import harvest_routing as routing
from .security import validate_player
from .transaction_identity import app_identity

_REGION = re.compile(r"PR-[A-Z]{3}-[1-9][0-9]{0,3}")


def region(value: str) -> str:
    if value == routing.INVENTORY:
        return ""
    if not isinstance(value, str) or not _REGION.fullmatch(value):
        raise routing.RoutingError("Invalid routing region")
    return value


def region_swap_fields(source: str, destination: str, from_region_uid: str, to_region_uid: str,
                       in_amount, intermediate_dec, output) -> dict:
    """The two-leg region-to-region `swap_tokens` fields (resource -> DEC -> resource), in the game's key order.
    Shared by harvest routing (same player) and the wallet's player-to-player transfer (), which adds only
    `to_player` (a tx <tx id>, 2026-10-13). Regions are validated by the caller."""
    return {"from_symbol": source, "to_symbol": destination, "from_region_uid": from_region_uid, "to_region_uid": to_region_uid,
            "in_amount": float(routing.amount(in_amount, positive=True)),
            "out_amount_1": float(routing.amount(intermediate_dec, positive=True)),
            "out_amount_2": float(routing.amount(output, positive=True))}


def build(player: str, step: dict, slippage: str) -> dict:
    player = validate_player(player)
    payload = {"app": app_identity(), "n": secrets.token_hex(8)}
    if step["kind"] == "liquidity":
        deposit = step["deposit"]
        payload.update(op="add_liquidity", resource_symbol=deposit["symbol"],
                       region_uid=region(step["source_region"]),
                       resource_amount=float(routing.amount(deposit["resource_amount"], positive=True)),
                       dec_amount=float(routing.amount(deposit["dec_amount"], positive=True)), shares_out=0)
    else:
        quote = step.get("quote", step)
        source, destination = quote["source_symbol"], quote["destination_symbol"]
        payload.update(op="swap_tokens", max_slippage=float(routing.validate_slippage(step.get("slippage", slippage))))
        if source == "DEC" or destination == "DEC":
            symbol = destination if source == "DEC" else source
            payload.update(resource_symbol=symbol,
                           region_uid=region(step["destination_region"] if source == "DEC" else step["source_region"]),
                           shares_out=float(routing.amount(quote["output"], positive=True)))
            payload["dec_amount" if source == "DEC" else "resource_amount"] = float(routing.amount(quote["input"], positive=True))
        else:
            if quote.get("external"):
                raise routing.RoutingError(
                    "External SPS swaps must use the DEC/resource contract and bridge settlement"
                )
            payload.update(region_swap_fields(source, destination, region(step["source_region"]), region(step["destination_region"]),
                                              quote["input"], quote["intermediate_dec"], quote["output"]))
    operation = ["custom_json", {"id": "sm_land_operation", "json": json.dumps(payload, separators=(",", ":")),
                                 "required_auths": [], "required_posting_auths": [player]}]
    return {"operations": [operation], "payload": payload, "nonce": payload["n"], "authority": "Posting"}


def build_sps_funding(player: str, effects: dict, kind: str) -> dict:
    if kind not in {"sell", "liquidity", "liquidity_funding_swap"}:
        raise routing.RoutingError("Unsupported SPS funding action")
    player = validate_player(player)
    recipient = validate_player(effects.get("external_transfer_account"))
    key, prefix = ("pending_deposit_id", "lpdid") if kind == "liquidity" else ("pending_swap_id", "lpsid")
    identifier = effects.get(key)
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
        raise routing.RoutingError("The game did not return a valid SPS funding identifier")
    if kind != "liquidity" and effects.get("external_swap_token") != "SPS":
        raise routing.RoutingError("The game did not request an SPS funding transfer")
    quantity = routing.amount(effects.get("pending_external_amount"), positive=True)
    if quantity != routing.floor(quantity):
        raise routing.RoutingError("The SPS funding amount exceeds supported precision")
    params = {"to": recipient, "qty": float(quantity), "token": "SPS", "memo": f"{prefix}:{identifier}",
              "app": app_identity(), "n": secrets.token_hex(8)}
    payload = {"action": "token_transfer", "params": params}
    operation = ["custom_json", {"id": "sm_sps", "json": json.dumps(payload, separators=(",", ":")),
                                 "required_auths": [player], "required_posting_auths": []}]
    return {"operations": [operation], "payload": payload, "nonce": params["n"], "authority": "Active",
            "pending_id": identifier, "amount": routing.text(quantity), "recipient": recipient}
