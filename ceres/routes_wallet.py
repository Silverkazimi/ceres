"""Multi-resource wallet reads —  /  /  /  / .

Read-only. Independent upstream reads (account balances, regional land resources, liquidity
pools, and staked DEC overall/by-region) each fail on
their own: a failed section is ``None`` plus an issue line, never a zero. DEC staking actions
stay in routes_dec.py; no transaction is built here.
"""
from __future__ import annotations

import json
import math
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from praetoria.api import SplinterlandsClient
from praetoria.land_analysis import LandClient
from praetoria.singleflight import singleflight
from praetoria.transport import API_ORIGIN, FixedGetTransport

from .routes_board import _require_same_origin
from .routes_harvest_routing import _pools
from . import client_limits
from .security import player_for_request
from .transaction_identity import app_identity
from .transaction_diagnostics import record_verification
import time as diagnostic_time

router = APIRouter()

ACCOUNT_TOKENS = ("DEC", "SPS", "SPSP", "VOUCHER")          # SPSP = staked SPS, as /players/balances names it
LAND_RESOURCES = ("GRAIN", "WOOD", "STONE", "IRON", "RESEARCH", "AURA")
SOULBOUND = frozenset({"AURA", "RESEARCH"})                   # never leave their region; no DEC pool exists
LP_KINDS = ("add", "remove", "swap_sell", "swap_resource", "swap_buy")
LP_GATE_REASONS: dict[str, str] = {}  # G2 (swap_buy) PROVEN 2026-09-28, tx <tx id>; G1 (remove) PROVEN 2026-10-04 ()
SWAP_SYMBOLS = ("GRAIN", "WOOD", "STONE", "IRON", "VOUCHER", "SPS")  # every non-soulbound token with a DEC pool
ACCOUNT_SWAP_SYMBOLS = ("VOUCHER", "SPS")                     # live in the account wallet: no region, region_uid ""
TWO_STEP_SELL = ("SPS",)                                      # external pool: selling needs the Active sm_sps transfer after the swap
SWAP_DIRECTIONS = ("swap_sell", "swap_buy")                   # both ways; a kind listed in LP_GATE_REASONS stays hidden
SWAP_SLIPPAGE = "2.5"                                         # fixed tolerance; no user slippage field ( lesson)
LP_TOKEN_PREFIX = "DEC-"                                      # players/balances names a pool position DEC-<SYMBOL> (its shares)


def lp_action_capabilities(signing_enabled: bool) -> dict[str, dict[str, object]]:
    return {kind: {"enabled": bool(signing_enabled) and kind not in LP_GATE_REASONS,
                   "reason": LP_GATE_REASONS.get(kind) or
                   ("Wallet LP signing is disabled by CERES_WALLET_LP_SIGNING." if not signing_enabled else "Available.")}
            for kind in LP_KINDS}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Wallet figure is unavailable")
    return value


def _text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.001")), "f")


def _balances(player: str) -> list[dict]:
    rows = singleflight(
        ("GET", "/players/balances", ("username", player)),
        lambda: SplinterlandsClient(timeout=10, retries=1).balances(player),
    ).data
    return [row for row in rows if isinstance(row, dict) and row.get("player") == player]


def account_section(rows: list[dict]) -> dict:
    """Liquid DEC / SPS / VOUCHER plus staked SPS. A token the game does not list is absent from the
    account's balance list, which the game reports as 0 (observed: GRAIN row with balance 0)."""
    out = {}
    for token in ACCOUNT_TOKENS:
        matches = [row for row in rows if row.get("token") == token]
        if len(matches) > 1:
            raise ValueError(f"Duplicate {token} balance")
        out[token] = _number(matches[0].get("balance")) if matches else 0
    return out


def regions_section(payload) -> list[dict]:
    """`/land/liquidity/region/{player}` rows -> per-region balances for all six resources, AURA/RESEARCH included."""
    if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), list):
        raise ValueError("Regional balances are unavailable")
    regions = []
    for row in payload["data"]:
        if not isinstance(row, dict) or not isinstance(row.get("uid"), str):
            raise ValueError("Regional balance row without uid")
        balances = {symbol: _number(row.get(symbol.lower())) for symbol in LAND_RESOURCES}
        plots = row.get("plots_owned")
        regions.append({"region_uid": row["uid"], "name": str(row.get("name") or ""),
                        "plots_owned": plots if isinstance(plots, int) and not isinstance(plots, bool) else None,
                        "balances": balances})
    regions.sort(key=lambda r: int(r["region_uid"].rsplit("-", 1)[1]) if r["region_uid"].rsplit("-", 1)[1].isdigit() else 0)
    return regions


def _pool_number(value, label: str) -> Decimal:
    """: shared Decimal-safe reader for the stake/liquidity-rewards families -- same
    contract as `_number` (finite, non-negative) but returns a `Decimal`, since these figures
    are combined/quantized (`_text`), never compared as raw floats. `label` names the family."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{label} is unavailable")
    if not number.is_finite() or number < 0:
        raise ValueError(f"{label} is unavailable")
    return number


def stake_overall_section(payload) -> str:
    """`/land/stake/dec/overall` -> the account's total staked DEC. `data` is a bare number,
    the only scalar-payload route among the wallet's upstream calls."""
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError("Staked DEC total is unavailable")
    return _text(_pool_number(payload.get("data"), "Staked DEC total"))


def stake_regions_section(payload) -> list[dict]:
    """`/land/stake/decstaked` -> one row per region WITH a staked amount (review fix: the
    upstream lists every region the account has ever touched, most at 0 -- a 0-amount row is
    dropped here so the tooltip only ever names regions actually carrying a stake)."""
    if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), list):
        raise ValueError("Per-region staked DEC is unavailable")
    out = []
    for row in payload["data"]:
        if not isinstance(row, dict) or not isinstance(row.get("region_uid"), str):
            raise ValueError("Staked DEC row without region_uid")
        amount = _pool_number(row.get("amount"), "Staked DEC figure")
        if amount > 0:
            out.append({"region_uid": row["region_uid"], "amount": _text(amount)})
    out.sort(key=lambda r: int(r["region_uid"].rsplit("-", 1)[1]) if r["region_uid"].rsplit("-", 1)[1].isdigit() else 0)
    return out


def swap_section(pools: dict, signing_enabled: bool) -> dict:
    """: which resource tabs get the pool swap card. Only the four DEC-paired resource pools
    Ceres can sign for (internal settlement); SELL (resource -> DEC) only until G2 proves BUY from a real
    game transaction. The fixed tolerance is the builders' default -- there is no slippage field."""
    symbols = [symbol for symbol in SWAP_SYMBOLS if symbol in pools and (not pools[symbol].external or symbol in TWO_STEP_SELL)]
    directions = [kind for kind in SWAP_DIRECTIONS if kind not in LP_GATE_REASONS]
    return {"symbols": symbols, "directions": directions, "slippage": SWAP_SLIPPAGE,
            "signing_enabled": bool(signing_enabled),
            "account_symbols": [symbol for symbol in symbols if symbol in ACCOUNT_SWAP_SYMBOLS],
            "two_step_sell": [symbol for symbol in symbols if symbol in TWO_STEP_SELL],
            "fees": {symbol: fee_percent(pools[symbol]) for symbol in symbols},
            "rates": {symbol: {kind: swap_rates(kind, symbol, pools) for kind in directions} for symbol in symbols}}


def share_percent(fraction: Decimal) -> str:
    """4 significant digits; a position too small to clear 0.001 % still shows as present, and a sub-1.0 share
    never rounds up to a false "100" ( / )."""
    percent = fraction * 100
    text = "<0.001" if 0 < percent < Decimal("0.001") else format(float(percent), ".4g")
    return "99.99" if fraction < 1 and text == "100" else text


def pool_section(rows: list[dict] | None, pools: dict) -> dict:
    """: the player's own position in every DEC pool (Grain, Wood, Stone, Iron, Voucher, SPS) and the pool
    reserves the Add form prices from. The position is the DEC-<SYMBOL> balance row of the balances read the wallet
    already makes (no extra call); `positions` is None when that read failed (never shown as 0)."""
    symbols = [symbol for symbol in SWAP_SYMBOLS if symbol in pools]
    positions = None
    if rows is not None:
        positions = {}
        for symbol in symbols:
            pool = pools[symbol]
            matches = [row for row in rows if row.get("token") == LP_TOKEN_PREFIX + symbol]
            if len(matches) > 1:
                raise ValueError(f"Duplicate pool position for {symbol}")
            shares = Decimal(str(_number(matches[0].get("balance")))) if matches else Decimal(0)
            fraction = shares / pool.shares if pool.shares else Decimal(0)
            positions[symbol] = {"shares": _text(shares), "share_percent": share_percent(fraction),
                                 "resource_amount": _text(fraction * pool.resource), "dec_amount": _text(fraction * pool.dec)}
    return {"symbols": symbols, "account_symbols": [s for s in symbols if s in ACCOUNT_SWAP_SYMBOLS],
            "external": [s for s in symbols if pools[s].external],
            "reserves": {s: {"resource": _text(pools[s].resource), "dec": _text(pools[s].dec), "shares": _text(pools[s].shares)} for s in symbols},
            "positions": positions}


def fee_percent(pool) -> str:
    """The pool's single-leg fee as the card shows it: "10" for the resource/Voucher pools, "0.5" for SPS."""
    return format(((1 - pool.factor(False)) * 100).normalize(), "f")


def swap_rates(kind: str, symbol: str, pools: dict) -> dict[str, str]:
    """The game's per-unit lines, from the same pool maths as the quote (single leg, 10 % fee):
    `unit` = what 1 paid token returns ("Sell 1 GRAIN for 0.008 DEC"), `inverse` = how many paid tokens
    one received token costs at that rate ("Buy 1 DEC for 128.643 GRAIN"). Unrounded until display."""
    pool = pools[symbol]
    source, target = (pool.resource, pool.dec) if kind == "swap_sell" else (pool.dec, pool.resource)
    factor = pool.factor(False)
    with localcontext() as ctx:
        ctx.prec = 50
        unit = factor * target / (source + factor)
        inverse = 1 / unit
    return {"unit": format(unit.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), "f"),
            "inverse": format(inverse.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), "f")}


def build_wallet(player: str, balances_read, regions_read, pools_read,
                 dec_overall_read=lambda p: None, dec_staked_read=lambda p: None,
                 signing_enabled: bool = False) -> dict:
    """: five independent reads per wallet open. The LP position and pool-wide reward cards are
    gone ("100% noise"), so `/land/liquidity/allrewards` and `all-no-vesting` are no longer read; the
    pools read only decides which tabs show the swap card. A failed pools read hides the swap card
    silently () -- it adds no issue line."""
    issues = []
    readers = {"balances": (balances_read, (player,)), "regions": (regions_read, (player,)), "pools": (pools_read, ()),
               "dec_overall": (dec_overall_read, (player,)), "dec_staked": (dec_staked_read, (player,))}
    with ThreadPoolExecutor(max_workers=len(readers)) as executor:
        futures = {name: executor.submit(fn, *args) for name, (fn, args) in readers.items()}
        results = {}
        for name, future in futures.items():
            try:
                results[name] = future.result()
            except Exception:  # each section fails on its own; never read as zero
                results[name] = None
                if name != "pools":
                    issues.append({"balances": "Account balances are unavailable.", "regions": "Regional land resources are unavailable.",
                                   "dec_overall": "Staked DEC total is unavailable.", "dec_staked": "Per-region staked DEC is unavailable."}[name])

    account = regions = swap = pool = staked_dec_overall = staked_dec_regions = None
    try:
        if results["balances"] is not None:
            account = account_section(results["balances"])
    except ValueError as exc:
        issues.append(str(exc))
    try:
        if results["regions"] is not None:
            regions = regions_section(results["regions"])
    except ValueError as exc:
        issues.append(str(exc))
    if results["pools"] is not None:
        swap = swap_section(results["pools"], signing_enabled)
        try:
            pool = pool_section(results["balances"] if account is not None else None, results["pools"])
        except ValueError as exc:
            issues.append(str(exc))
    try:
        if results["dec_overall"] is not None:
            staked_dec_overall = stake_overall_section(results["dec_overall"])
    except ValueError as exc:
        issues.append(str(exc))
    try:
        if results["dec_staked"] is not None:
            staked_dec_regions = stake_regions_section(results["dec_staked"])
    except ValueError as exc:
        issues.append(str(exc))
    return {"player": player, "observed_at": datetime.now(timezone.utc).isoformat(),
            "account": account, "regions": regions, "swap": swap, "pool": pool,
            "staked_dec": {"overall": staked_dec_overall, "by_region": staked_dec_regions},
            "lp_actions": lp_action_capabilities(signing_enabled),
            "soulbound": sorted(SOULBOUND), "resources": list(LAND_RESOURCES), "issues": issues}


@router.get("/api/wallet")
def wallet(request: Request, player: str | None = None):
    player = player_for_request(request, player)
    client_limits.limit(request, "preview")  # C3: per-client limit before any upstream read
    land = LandClient(timeout=10, retries=1)
    try:
        return build_wallet(player, _balances,
                            lambda p: land.regional_balances(p).data, _pools,
                            lambda p: land.stake_dec_overall(p).data,
                            lambda p: land.stake_dec_staked(p).data,
                            bool(request.app.state.ceres_config.wallet_lp_signing_enabled))
    except Exception as exc:
        raise HTTPException(502, "The wallet could not be loaded. Please retry.") from exc


# (read side): claimable SPS rewards by type. Evidence 2026-09-19 (player0005)
# `/players/unclaimed_balances?username=&token_type=SPS` lists one row per reward type
# (land_mining, wild, modern, brawl, focus, season, land, tax_collection, ...) with a string
# balance and last_claim_date; the game's own client claims ALL of them with one Posting
# `sm_claim_rewards` (tx <tx id>, result balances by type). SPS *staking* rewards are a
# separate `claim_staking_rewards` op that settings.active_auth_ops lists as Active-key only
# so Ceres (Posting-only signing) shows the stake and points to the game for that claim.
REWARD_LABELS = {"land_mining": "Land mining", "land": "Land", "tax_collection": "Tax collection", "wild": "Wild battles",
                 "modern": "Modern battles", "survival": "Survival battles", "survival_bracket": "Survival bracket",
                 "brawl": "Brawls", "focus": "Daily focus", "season": "Season", "license": "Validator license",
                 "nightmare": "Nightmare", "soulkeep": "Soulkeep", "surveyed_land": "Surveyed land"}
LAND_TYPES = frozenset({"land_mining", "land", "tax_collection", "surveyed_land"})


def _decimal(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Reward figure is unavailable")
    if not number.is_finite() or number < 0:
        raise ValueError("Reward figure is unavailable")
    return number


def reward_rows(payload, player: str) -> dict:
    """`/players/unclaimed_balances` -> claimable SPS by reward type, largest first, plus total."""
    rows = payload.get("unclaimed_balances") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Unclaimed rewards are unavailable")
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("player") != player or row.get("token") != "SPS" or not isinstance(row.get("type"), str):
            raise ValueError("Unclaimed rewards response does not match the account")
        amount = _decimal(row.get("balance"))
        out.append({"type": row["type"], "label": REWARD_LABELS.get(row["type"], row["type"].replace("_", " ").capitalize()),
                    "source": "land" if row["type"] in LAND_TYPES else "battle", "balance": _text(amount),
                    "last_updated": row.get("last_updated_date")})
    out.sort(key=lambda r: (-Decimal(r["balance"]), r["type"]))
    total = sum((Decimal(r["balance"]) for r in out), Decimal(0))
    return {"rows": out, "total": _text(total), "last_claim_date": payload.get("last_claim_date")}


def unclaimed_sps(player: str) -> dict:
    payload = FixedGetTransport(timeout=10, retries=1).get_json(
        API_ORIGIN + "/players/unclaimed_balances?" + urlencode({"username": player, "token_type": "SPS"})).data
    return reward_rows(payload, player)


def next_claim_at(last_claim_date, settings) -> str | None:
    """: the game allows `reward_pools.claim_limit` (1) claim per `claim_limit_time` ms (82,800,000 = 23 h on 2026-10-14)
    after `last_claim_date`. The instant the next claim is allowed, or None when it is allowed now or the rule is unreadable."""
    pools = settings.get("reward_pools") if isinstance(settings, dict) else None
    window = pools.get("claim_limit_time") if isinstance(pools, dict) else None
    if pools is None or pools.get("claim_limit") != 1 or isinstance(window, bool) or not isinstance(window, (int, float)) or window <= 0 or not last_claim_date:
        return None
    try:
        last = datetime.fromisoformat(str(last_claim_date).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    ready = last.timestamp() + window / 1000
    if ready <= datetime.now(timezone.utc).timestamp():
        return None
    return datetime.fromtimestamp(ready, timezone.utc).isoformat().replace("+00:00", "Z")


@router.get("/api/wallet/rewards")
def rewards(request: Request, player: str | None = None):
    player = player_for_request(request, player)
    client_limits.limit(request, "preview")  # C3: per-client limit before any upstream read
    try:
        current = unclaimed_sps(player)
    except Exception as exc:
        raise HTTPException(502, "Claimable SPS rewards are unavailable. Please retry.") from exc
    try:
        ready_at = next_claim_at(current.get("last_claim_date"), _settings()) if current.get("last_claim_date") else None
    except Exception:
        ready_at = None  # the claim itself re-checks with the game; an unreadable rule shows no timer
    return {"player": player, "observed_at": datetime.now(timezone.utc).isoformat(), **current, "next_claim_at": ready_at}


# (sign side): the Ceres Claim button. Contract per library/ceres-wallet.md — one Posting
# `sm_claim_rewards` {app, n}; the game answers type "claim_rewards" with result.balances by type.
# Ceres builds the unsigned envelope and reads the game's own result; the wallet alone signs.
CLAIM_OP = "sm_claim_rewards"
CLAIM_TYPE = "claim_rewards"


def claim_operation(player: str) -> tuple[list, str]:
    payload = {"app": app_identity(), "n": secrets.token_hex(8)}
    return ["custom_json", {"id": CLAIM_OP, "json": json.dumps(payload, separators=(",", ":")),
                            "required_auths": [], "required_posting_auths": [player]}], payload["n"]


def require_posting_claim(settings) -> None:
    """The game lists Active-key operations in settings.active_auth_ops; a reward claim must not be one."""
    operations = settings.get("active_auth_ops") if isinstance(settings, dict) else None
    if not isinstance(operations, list):
        raise ValueError("Current game signing requirements are unavailable")
    if CLAIM_TYPE in operations:
        raise ValueError("The game now requires the Active key for reward claims; claim in the game instead")


def claim_result(info, player: str, nonce: str, tx: str) -> dict:
    """One `/transactions/lookup` trx_info for our claim -> confirmed (with what was paid) / failed / pending.

    : a transaction the game does not know is `not_found`, never an open-ended `pending`."""
    if not info:
        return {"status": "not_found"}
    data = json.loads(info["data"]) if isinstance(info.get("data"), str) else info.get("data")
    if info.get("id") != tx or info.get("player") != player or info.get("type") != CLAIM_TYPE or not isinstance(data, dict) or data.get("n") != nonce:
        raise ValueError("Game result does not match this claim request")
    if info.get("success") is False:
        return {"status": "failed", "message": str(info.get("error") or "The game rejected the claim")}
    result = json.loads(info["result"]) if isinstance(info.get("result"), str) else info.get("result")
    if info.get("success") is not True or not isinstance(result, dict):
        return {"status": "pending"}
    if result.get("success") is False:
        return {"status": "failed", "message": str(result.get("error") or "The game rejected the claim")}
    balances = result.get("balances")
    if not isinstance(balances, list):
        return {"status": "pending"}
    claimed = []
    for row in balances:
        if not isinstance(row, dict) or row.get("token") != "SPS":
            continue
        kind = str(row.get("type"))
        claimed.append({"type": kind, "label": REWARD_LABELS.get(kind, kind.replace("_", " ").capitalize()), "balance": _text(_decimal(row.get("balance")))})
    total = sum((Decimal(r["balance"]) for r in claimed), Decimal(0))
    return {"status": "confirmed", "tx_id": tx, "claimed": claimed, "total": _text(total)}


def _settings():
    return FixedGetTransport(timeout=10, retries=1).get_json(API_ORIGIN + "/settings").data


@router.post("/api/wallet/claim/preview")
async def claim_preview(request: Request, player: str | None = None):
    _require_same_origin(request)
    client_limits.limit(request, "preview")
    player = player_for_request(request, player)
    import asyncio
    try:
        current, settings = await asyncio.gather(asyncio.to_thread(unclaimed_sps, player), asyncio.to_thread(_settings))
        require_posting_claim(settings)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Fresh reward figures unavailable; nothing was signed") from exc
    if Decimal(current["total"]) <= 0:
        raise HTTPException(409, "There are no SPS rewards to claim right now")
    operation, nonce = claim_operation(player)
    return {"player": player, **current, "operations": [operation], "nonce": nonce,
            "signing_enabled": bool(request.app.state.ceres_config.harvest_signing_enabled)}


@router.post("/api/wallet/claim/verify")
async def claim_verify(request: Request, player: str | None = None):
    _require_same_origin(request)
    client_limits.limit(request, "verify")
    player = player_for_request(request, player)
    body = await request.json()
    tx, nonce = (body.get("tx_id"), body.get("nonce")) if isinstance(body, dict) else (None, None)
    if not isinstance(tx, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", tx) or not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{16}", nonce):
        raise HTTPException(422, "Invalid transaction reference")
    started = diagnostic_time.monotonic()
    import asyncio
    try:
        # the game's "Transaction [..] not found." body is "no record" (`not_found`), not an outage (502).
        from .game_lookup import lookup_trx_info
        info = await asyncio.to_thread(lookup_trx_info, FixedGetTransport(timeout=10, retries=1).get_json, tx)
        verdict = claim_result(info, player, nonce, tx)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        record_verification(account=player, tx_id=tx, attempt=None, elapsed_ms=(diagnostic_time.monotonic() - started) * 1000,
                            operation_count=1, family="wallet_claim", error=True)
        raise HTTPException(502, "Game confirmation unavailable; check again without signing again") from exc
    record_verification(account=player, tx_id=tx, attempt=None, elapsed_ms=(diagnostic_time.monotonic() - started) * 1000,
                        operation_count=1, family="wallet_claim", outcome={"state": verdict["status"]})
    return verdict
