"""Fresh, account-bound harvest-routing previews. No transaction submission."""
from __future__ import annotations

from .transaction_diagnostics import record_verification
import time as diagnostic_time

import asyncio
import copy
import json
import logging
import re
import time
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fides import op as fides_op
from praetoria.api import SplinterlandsClient
from praetoria.errors import SchemaError, TransportError, UpstreamError
from praetoria.transport import FixedGetTransport, LAND_ORIGIN, API_ORIGIN
from . import client_limits, harvest_evidence, routing_safety
from . import harvest_routing as routing
from . import routing_plan
from . import harvest_routing_runs as runs
from .harvest_routing_operations import build as build_operation, build_sps_funding
from .harvest_routing_validator import sps_balance, funding_receipt, recovered_funding_receipt
from .harvest_routing_bridge import settle as settle_bridge
from .routes_board import _config, _require_same_origin
from .routes_harvest import _normalize_scope, _region_rows, _snapshot, _build_preview, _strip
from .security import player_for_request

# L1-01: the first routing screen (v1) trusted a player NAME for every run action (list, stop, cancel, transaction, retry ...). The page no longer uses it
# (routing runs are the v2 ones, operated with their own token). Only the harvest-side routes the page still calls stay open; the rest answer 404 unless an
# operator turns the old routes back on for a test (CERES_LEGACY_ROUTING=1).
_OPEN_ROUTES = {"state", "preview", "harvest-plan", "harvest-transaction", "close-harvest"}


def legacy_gate(request: Request) -> None:
    import os
    name = request.url.path.rsplit("/", 1)[-1]
    if name not in _OPEN_ROUTES and os.environ.get("CERES_LEGACY_ROUTING", "").strip() != "1":
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(dependencies=[Depends(legacy_gate)])

ROUTING_TX_EXPIRY_SECONDS = 3900  # Hive transactions expire after ~1 h; allow a margin
ROUTING_REVIEW_HARD_EXPIRY_SECONDS = 15 * 60


def _projected_harvest_chunks(region_count: int) -> int:
    cap = fides_op.DEFAULT_MAX_OPS_PER_TX
    return (region_count + cap - 1) // cap


def _pools() -> dict[str, routing.Pool]:
    data = FixedGetTransport(timeout=15, retries=1).get_json(LAND_ORIGIN + "/land/liquidity/pools").data
    if not isinstance(data, dict) or data.get("status") != "success" or not isinstance(data.get("data"), list):
        raise routing.RoutingError("Liquidity pools are unavailable")
    pools = [routing.Pool.parse(row) for row in data["data"]]
    if len({p.symbol for p in pools}) != len(pools) or len({p.id for p in pools}) != len(pools):
        raise routing.RoutingError("Duplicate pool identities")
    return {p.symbol: p for p in pools}


def _require_posting_contract() -> None:
    try:
        settings = FixedGetTransport(timeout=15, retries=1).get_json(API_ORIGIN + "/settings").data
    except (SchemaError, TransportError, UpstreamError) as exc:
        raise routing.RoutingError("Current game signing requirements could not be read. Try the review again") from exc
    operations = settings.get("active_auth_ops") if isinstance(settings, dict) else None
    if not isinstance(operations, list) or any(not isinstance(op, str) for op in operations):
        raise routing.RoutingError("Current game signing requirements are unavailable. Review again before signing")
    if "land_operation" in operations:
        raise routing.RoutingError("The game now allows an account-specific Active requirement for routing. Ceres cannot verify that preference; use the game Trade Hub")


def _inventory(player: str) -> dict[str, Decimal]:
    rows = SplinterlandsClient(timeout=15, retries=1).balances(player).data
    result = {}
    for row in rows:
        if row.get("player") != player or row.get("token") not in {"DEC", "VOUCHER"}:
            continue
        symbol = row["token"]
        if symbol in result:
            raise routing.RoutingError("Duplicate inventory balance")
        result[symbol] = routing.amount(row.get("balance"))
    if "DEC" not in result:
        raise routing.RoutingError("DEC balance is unavailable")
    result["SPS"] = sps_balance(player)
    return result


def _context(request: Request, player: str) -> dict[str, Any]:
    report, ref = _snapshot(request, player)
    with ThreadPoolExecutor(max_workers=3) as pool:
        regional = pool.submit(_region_rows, player, {"kind": "all"}, report, ref, True)
        pool_read = pool.submit(_pools)
        inventory_read = pool.submit(_inventory, player)
        regions, issues = regional.result()
        pools, inventory = pool_read.result(), inventory_read.result()
    balances = {(routing.INVENTORY, token): value for token, value in inventory.items()}
    for row in regions:
        if row.get("status") in {"unavailable", "unknown"} or not isinstance(row.get("balances"), dict):
            issues.append(f"Region {row.get('region_number')}: fresh costs or balances unavailable")
            continue
        for token, value in row["balances"].items():
            if value is not None:
                balances[(row["region_uid"], token)] = routing.amount(value)
    reserves = routing.region_reserves([row for row in regions if row.get("cost_now") is not None])
    public_pools = [{"id": p.id, "symbol": p.symbol, "resource_quantity": routing.text(p.resource),
                     "dec_quantity": routing.text(p.dec), "total_shares": routing.text(p.shares),
                     "external": p.external} for p in pools.values()]
    public = {"player": player, "observed_at": datetime.now(timezone.utc).isoformat(),
              "regions": regions, "pools": public_pools,
              "inventory": {token: routing.text(value) for token, value in inventory.items()},
              "issues": issues, "blocked": bool(issues),
              "signing_enabled": bool(_config(request).harvest_routing_signing_enabled),
              "reserves": [{"region_uid": region, "symbol": token, "amount": routing.text(value)}
                           for (region, token), value in sorted(reserves.items())]}
    return {"public": public, "pools": pools, "ledger": routing.Ledger(balances, reserves)}


def _receipts(request: Request, player: str, identifiers: Any) -> dict[tuple[str, str], Decimal]:
    if not isinstance(identifiers, list) or len(identifiers) > 5000 or any(not isinstance(v, str) for v in identifiers):
        raise routing.RoutingError("Invalid harvest receipt selection")
    received: dict[tuple[str, str], Decimal] = {}
    nonces = set()
    recovered = 0
    for identifier in set(identifiers):
        record = harvest_evidence.load_attempt(_config(request).root, player, identifier)
        if record is None or not record.get("tx_id"):
            raise routing.RoutingError("A selected harvest has no verified receipt")
        missing_amounts = any(member.get("op") == "harvest_all" and member.get("game_state") == "accepted"
                              and "regional_amounts" not in (member.get("evidence") or [])
                              and not member.get("received_by_token")
                              for member in record.get("members", []))
        if missing_amounts:
            from .routes_harvest import _verify_harvest_attempt
            verified = _verify_harvest_attempt(request, player, {"attempt_id": identifier})
            if verified.get("outcome") != "success":
                raise routing.RoutingError("Harvest confirmation or resource amounts are still being read. Retry preparation; do not harvest again.")
            recovered += 1
            record = harvest_evidence.load_attempt(_config(request).root, player, identifier)
        accepted, _refused = runs.classify_harvest_attempt(record)
        for member in accepted:
            identity = (record["tx_id"], member.get("nonce"))
            if identity in nonces:
                continue
            nonces.add(identity)
            for symbol, value in (member.get("received_by_token") or {}).items():
                region = routing.INVENTORY if symbol in {"DEC", "SPS", "VOUCHER"} else member["region_uid"]
                key = (region, symbol)
                received[key] = received.get(key, routing.ZERO) + routing.amount(value)
    return received


async def _body(request: Request) -> dict[str, Any]:
    _require_same_origin(request)
    try:
        value = await request.json()
    except ValueError as exc:
        raise HTTPException(422, "Invalid routing request") from exc
    if not isinstance(value, dict):
        raise HTTPException(422, "Invalid routing request")
    return value


@router.get("/api/harvest/routing/state")
async def state(request: Request, player: str | None = None):
    """Read one legacy maintainer context and expose the v2 kinds (one context read)."""
    client_limits.limit(request, "routing")
    player = player_for_request(request, player)
    try:
        return {**(await asyncio.to_thread(_context, request, player))["public"],
                "enabled_kinds": sorted(_config(request).routing_kinds),
                "max_ops_per_tx": fides_op.DEFAULT_MAX_OPS_PER_TX}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, "Fresh routing balances and pool data are unavailable. Retry before reviewing.") from exc


def _refusal_log(player, exc) -> None:
    """ (: the preview refusal reason reaches the Ceres log (a plain RoutingError text, never a payload)."""
    logging.getLogger("ceres.routing").warning("refused path=preview player=%s reason=%s", str(player)[:40],
                                               re.sub(r"[^\x20-\x7e]", " ", str(exc))[:300])


@router.post("/api/harvest/routing/preview")
async def preview(request: Request, player: str | None = None):
    """Read one maintainer context; compile a manifest for explicit v2 previews."""
    client_limits.limit(request, "routing")
    body = await _body(request)
    manifest_version = body.get("manifest_version")
    if manifest_version is not None and (type(manifest_version) is not int or manifest_version != 2):
        raise HTTPException(422, "Unsupported routing manifest version")
    v2_preview = manifest_version == 2
    player = player_for_request(request, player)
    workflow = None
    if body.get("routing_run_id"):
        try:
            def finish():  # S1 / L1-03: the file lock and the receipt read wait on disk, never on the event loop
                with runs.locked(_config(request).root, player) as folder:
                    flow = runs.read(folder, body["routing_run_id"], player)
                    done = runs.finish_harvest(_config(request).root, player, flow)
                    runs.write(folder, flow)
                    return flow, done
            workflow, receipts = await asyncio.to_thread(finish)
            body = {**body, "scope": workflow["scope"], "rules": body.get("rules", workflow["rules"]),
                    "reserves": workflow["reserves"], "slippage": body.get("slippage", workflow["slippage"]),
                    "phase": "after", "harvest_attempt_ids": receipts}
        except routing.RoutingError as exc:
            raise HTTPException(409, str(exc)) from exc
    runs.migrate_legacy_dec_sources(body)
    scope = _normalize_scope(body)
    if scope["kind"] not in {"all", "regions"}:
        raise HTTPException(422, "Routing currently requires Harvest All or Harvest Region")
    phase = body.get("phase", "before")
    if phase not in {"before", "after"}:
        raise HTTPException(422, "Invalid routing phase")
    requested_rules = body.get("rules", [])
    if not isinstance(requested_rules, list) or any(not isinstance(rule, dict) for rule in requested_rules):
        raise HTTPException(422, "Invalid routing rules")
    if v2_preview:
        try:
            protected_wallet_dec = routing.text(routing.amount(body.get("protected_wallet_dec", "0")))
        except routing.RoutingError as exc:
            raise HTTPException(422, "Protected wallet DEC must be zero or greater") from exc
        requested_rules = [{key: value for key, value in rule.items()
                            if key not in {"reserved_dec", "protected_dec_minimum"}}
                           for rule in requested_rules]
        body["rules"] = requested_rules
    enabled_kinds = set(_config(request).routing_kinds) if request is not None else set()
    if v2_preview:
        try:
            requested_kinds = {routing_plan.rule_kind(rule) for rule in requested_rules}
        except routing.RoutingError as exc:
            _refusal_log(player, exc)
            raise HTTPException(422, str(exc)) from exc
        disabled = requested_kinds - enabled_kinds
        if disabled:
            raise HTTPException(422, "Routing kinds disabled: " + ", ".join(sorted(disabled)))
    try:
        context = await asyncio.to_thread(_context, request, player)
        public = context["public"]
        if public["blocked"]:
            return {**public, "phase": phase, "steps": []}
        ledger = context["ledger"]
        slippage = routing.text(routing.validate_slippage(body.get("slippage", "2.5")))
        selected = set(scope.get("region_uids", [])) if scope["kind"] == "regions" else {row["region_uid"] for row in public["regions"]}
        owned = {row["region_uid"] for row in public["regions"]}
        if not selected or not selected.issubset(owned):
            raise routing.RoutingError("Choose owned regions to harvest")
        harvested = None
        if phase == "after":
            # after a harvest only the regions that actually produced something take part, however many were selected.
            # these are by-id game verdict reads for already-known transaction IDs
            # never account-history scans. Unknown receipts are reserved without any UI.
            selected_attempts = body.get("harvest_attempt_ids", [])
            if v2_preview:
                def guard_attempts():
                    return routing_safety.filter_attempts(
                        _config(request).root, player, selected_attempts,
                        lambda tx_id: FixedGetTransport(timeout=15, retries=1).get_json(
                            API_ORIGIN + "/transactions/lookup?" + urlencode({"trx_id": tx_id})).data)
                selected_attempts = await asyncio.to_thread(guard_attempts)
                body = {**body, "harvest_attempt_ids": selected_attempts}
            harvested = await asyncio.to_thread(_receipts, request, player, selected_attempts)
            selected = {region for (region, _symbol), amount in harvested.items()
                        if amount > routing.ZERO and region != routing.INVENTORY} & selected
            if not selected:
                only_inventory = any(amount > routing.ZERO for amount in harvested.values())
                raise routing.RoutingError(
                    "This harvest produced only DEC, SPS or Voucher, which routing does not move yet" if only_inventory
                    else "No confirmed harvest proceeds are available for routing")
        extra = body.get("reserves", [])
        if not isinstance(extra, list) or len(extra) > 600:
            raise routing.RoutingError("Invalid reserve choices")
        if v2_preview:
            extra = [item for item in extra if not (isinstance(item, dict)
                     and item.get("region_uid") == routing.INVENTORY and item.get("symbol") == "DEC")]
        for reserve in extra:
            if not isinstance(reserve, dict):
                raise routing.RoutingError("Invalid reserve choice")
            key = (reserve.get("region_uid"), reserve.get("symbol"))
            if key not in ledger.balances:
                raise routing.RoutingError("Reserve names an unknown regional balance")
            ledger.reserves[key] = max(ledger.reserves.get(key, routing.ZERO), routing.amount(reserve.get("amount")))
        if v2_preview:
            key = (routing.INVENTORY, "DEC")
            ledger.reserves[key] = max(ledger.reserves.get(key, routing.ZERO), routing.amount(protected_wallet_dec))
        routing.validate_rules(body.get("rules", []), ledger, context["pools"], selected)
        if phase == "before":
            needs = {(row["region_uid"], symbol): routing.amount(value) + routing.amount((row.get("cost_per_hour") or {}).get(symbol, 0)) / 30
                     for row in public["regions"] if row["region_uid"] in selected
                     for symbol, value in (row.get("cost_now") or {}).items()}
            steps = routing.cover_shortfalls(ledger, needs, context["pools"], slippage)
        else:
            if not isinstance(body.get("rules", []), list):
                raise routing.RoutingError("Invalid routing rules")
            if any(not isinstance(rule, dict) or rule.get("source_region") not in selected | {routing.INVENTORY}
                   for rule in body.get("rules", [])):
                raise routing.RoutingError("A routing rule names a source outside the selected harvest regions")
            ledger.harvested = harvested
            if not ledger.harvested:
                raise routing.RoutingError("No confirmed harvest proceeds are available for routing")
            steps = routing.plan_after_harvest(ledger, body.get("rules", []), context["pools"], slippage)
        manifest = None
        if phase == "after" and v2_preview:
            manifest = routing_plan.compile_manifest(
                rules=body.get("rules", []), steps=steps, pools=context["pools"],
                account=player, regions=len(selected), enabled_kinds=enabled_kinds)
        normalized_rules = copy.deepcopy(body.get("rules", []))
        chosen_sources = {step.get("rule_id"): step.get("dec_source") for step in steps
                          if step.get("kind") == "liquidity" and step.get("dec_source_defaulted")}
        for rule in normalized_rules:
            if rule.get("id") in chosen_sources and rule.get("dec_source") is None:
                rule["dec_source"] = chosen_sources[rule["id"]]
        blocked_steps = [step for step in steps if step.get("blocked")]
        preview_issues = [step["reason"] for step in blocked_steps if step.get("reason")]
        record = harvest_evidence.save_preview(player, {"kind": "harvest_routing", "phase": phase, "scope": scope,
            "routing_run_id": workflow["run_id"] if workflow else None,
            "workflow_rules_digest": runs.plan_digest(workflow) if workflow else None,
            "slippage": str(slippage), "steps": steps, "rules": normalized_rules,
            **({"protected_wallet_dec": protected_wallet_dec} if v2_preview else {}),
            "harvest_attempt_ids": list(body.get("harvest_attempt_ids", [])), "reserves": extra,
            "state": public, "chunks": [], "blocked": bool(blocked_steps), "issues": preview_issues,
            "manifest": manifest,
            "projected_harvest_chunks": 0 if phase == "after" else _projected_harvest_chunks(len(selected))})
        return {"player": player, "phase": phase, "preview_id": record["preview_id"],
                "preview_digest": record["preview_digest"], "observed_at": public["observed_at"],
                "steps": steps, "rules": normalized_rules, "blocked": bool(blocked_steps), "issues": preview_issues,
                "signing_enabled": public["signing_enabled"], "enabled_kinds": sorted(enabled_kinds),
                **({"manifest": manifest} if manifest is not None else {}),
                "execution_status": "Ready for reviewed execution"}
    except HTTPException:
        raise
    except routing.RoutingError as exc:
        _refusal_log(player, exc)
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logging.getLogger("ceres.routing").warning("preview_failed player=%s error=%s", str(player)[:40], type(exc).__name__)
        raise HTTPException(502, "Fresh routing preview unavailable; no transaction was created") from exc


def _remaining_harvest(request: Request, player: str, record: dict) -> dict:
    budget = _receipts(request, player, record.get("harvest_attempt_ids", []))
    for done in record.get("steps", []):
        if done.get("state") != "confirmed":
            continue
        detail = (done.get("review") or {}).get("step", done)
        if done["kind"] == "liquidity":
            deposit = detail["deposit"]
            debits = [(done["source_region"], done["symbol"], deposit["resource_amount"])]
            if done.get("dec_source") != "wallet":
                debits.append((routing.INVENTORY, "DEC", deposit["dec_amount"]))
        else:
            quote = detail.get("quote", detail)
            debits = [(done["source_region"], quote["source_symbol"], quote["input"])]
        for region, symbol, value in debits:
            key = (region, symbol)
            budget[key] = budget.get(key, routing.ZERO) - routing.amount(value)
        for received in (done.get("receipt") or {}).get("received", []):
            symbol = received["symbol"]
            region = routing.INVENTORY if symbol in {"DEC", "SPS", "VOUCHER"} else done.get("destination_region")
            key = (region, symbol)
            budget[key] = budget.get(key, routing.ZERO) + routing.amount(received["amount"])
    return {key: max(routing.ZERO, value) for key, value in budget.items()}


def _dependency_proceeds(record: dict, step: dict) -> tuple[Decimal | None, bool]:
    """: the confirmed DEC amount a two-step sale's dependent leg (step B) must spend.

    Returns (amount, confirmed). `confirmed` is False when the dependency step is missing or not
    yet `confirmed` — nothing to read yet. `confirmed` is True with `amount` None when step A IS
    confirmed but neither its normalized receipt nor the raw game result exposes a DEC amount —
    an evidence gap, distinct from "not confirmed yet", that the caller must let the maintainer skip
    rather than block on forever.
    """
    parent = next((item for item in record.get("steps", []) if item.get("step_id") == step.get("depends_on_step_id")), None)
    if not parent or parent.get("state") != "confirmed":
        return None, False
    received = next((item for item in (parent.get("receipt") or {}).get("received", [])
                     if item.get("symbol") == "DEC"), None)
    if received is not None:
        return routing.amount(received["amount"], positive=True), True
    effects = ((parent.get("game_result") or {}).get("result") or {}).get("data") or {}
    raw = effects.get("dec_amount")
    if raw is not None:
        try:
            return routing.amount(raw, positive=True), True
        except routing.RoutingError:
            pass
    return None, True


def _fresh_step(request: Request, player: str, record: dict, step: dict, context=None) -> tuple[dict, dict]:
    context = context if context is not None else _context(request, player)
    if context["public"]["blocked"]:
        raise routing.RoutingError("Fresh regional requirements are incomplete; refresh before signing")
    ledger, pools = context["ledger"], context["pools"]
    harvest_only = record.get("phase") == "after"
    if harvest_only:
        ledger.harvested = _remaining_harvest(request, player, record)
    for reserve in record["reserves"]:
        key = (reserve["region_uid"], reserve["symbol"])
        ledger.reserves[key] = max(ledger.reserves.get(key, routing.ZERO), routing.amount(reserve["amount"]))
    fresh = copy.deepcopy({key: value for key, value in step.items()
                           if key not in {"review", "unsent_reviews", "game_result", "handoff_at", "error"}})
    if step["kind"] == "liquidity":
        deposit = routing.liquidity_deposit(pools[step["symbol"]], step["quantity"])
        dec_source = step.get("dec_source")
        if dec_source not in routing.DEC_SOURCES:
            raise routing.RoutingError("Review whether DEC comes from your wallet or a funding sale")
        up_to = routing.liquidity_dec_ceiling(deposit, record["slippage"])
        if dec_source == "swap_half":
            dec_available, confirmed = _dependency_proceeds(record, step)
            if not confirmed:
                raise routing.RoutingError("Confirm the funding swap first; the liquidity deposit uses its actual DEC proceeds")
            if dec_available is None:
                raise routing.RoutingError("The funding swap confirmed but its DEC proceeds are unknown; skip this deposit or check the swap again")
            dec_available = routing.floor(dec_available)
            required = routing.amount(deposit["dec_amount"])
        else:
            dec_available = ledger.available(routing.INVENTORY, "DEC")
            required = up_to
        shortfall = max(routing.ZERO, required - dec_available)
        if shortfall:
            message = (f"The selected {dec_source.replace('_', ' ')} DEC source is short by "
                       f"{routing.text(shortfall)} DEC ({routing.text(dec_available)} available; "
                       f"{routing.text(required)} required)")
            if dec_source == "swap_half":
                raise routing.ProceedsTooSmall(message)
            raise routing.RoutingError(message)
        ledger.allocate_many([(step["source_region"], step["symbol"], deposit["resource_amount"], harvest_only),
                              (routing.INVENTORY, "DEC", deposit["dec_amount"], dec_source != "wallet")])
        fresh["deposit"] = deposit
        funding = {**fresh.get("dec_funding", {}), "source": dec_source,
                   "available": routing.text(dec_available), "required": deposit["dec_amount"],
                   "up_to": routing.text(up_to), "shortfall": routing.text(shortfall)}
        if dec_source == "swap_half":
            funding.update(confirmed_dec=routing.text(dec_available),
                           remainder_dec=routing.text(max(routing.ZERO, dec_available - required)))
        fresh["dec_funding"] = funding
    else:
        original = step.get("quote", step)
        if step.get("depends_on") and not step.get("depends_on_step_id"):
            # A dependent leg whose planning-time link never resolved to a real created step
            # (its first leg was filtered as empty) must never fall back to sizing from the
            # stale planning-time estimate.
            raise routing.RoutingError("Confirm the sale to DEC first; the SPS swap uses its actual proceeds")
        if step.get("depends_on_step_id"):
            # the DEC→SPS leg of a two-step sale is never sized from its planning-time
            # estimate — it spends exactly what the first (confirmed) leg actually delivered.
            dec_amount, confirmed = _dependency_proceeds(record, step)
            if not confirmed:
                raise routing.RoutingError("Confirm the sale to DEC first; the SPS swap uses its actual proceeds")
            if dec_amount is None:
                raise routing.RoutingError("Step 1 confirmed but its DEC proceeds are unknown; skip this step or check step 1 again")
            source_input = routing.floor(dec_amount)
            if source_input <= 0:
                raise routing.ProceedsTooSmall("Proceeds too small to swap")
            # This confirmed DEC is independently proven (receipt or raw game result), so it is
            # made available on this fresh ledger snapshot even on the rare path where
            # `_remaining_harvest()`'s own receipt-only replenishment did not already credit it.
            harvest_key = (step["source_region"], "DEC")
            ledger.harvested[harvest_key] = max(ledger.harvested.get(harvest_key, routing.ZERO), source_input)
            source_input = routing.text(source_input)
        else:
            if "input" not in original:
                raise routing.RoutingError("This top-up is unfunded. Cancel and review after adding DEC")
            source_input = original["input"]
        quote = routing.swap_quote(original["source_symbol"], original["destination_symbol"], source_input, pools, step.get("slippage", record["slippage"]))
        ledger.allocate(step["source_region"], quote.source_symbol, quote.input, harvest_only=harvest_only)
        if "quote" in step:
            fresh["quote"] = quote.public()
        else:
            fresh.update(quote.public())
    return fresh, context["public"]


@router.get("/api/harvest/routing/runs")
async def list_runs(request: Request, player: str | None = None):
    player = player_for_request(request, player)
    def load():
        with runs.locked(_config(request).root, player) as folder:
            records = runs.records(folder, player)[:50]
            for record in records:
                if record.get("harvest_attempts"):
                    record["harvest_attempt_records"] = runs.harvest_attempts(_config(request).root, player, record)
                    runs.expose_signature_plan(record)
            return records
    return {"player": player, "runs": await asyncio.to_thread(load)}


@router.post("/api/harvest/routing/run")
async def create_run(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    record = harvest_evidence.load_preview(player, body.get("preview_id"), body.get("preview_digest"))
    if not record or record.get("kind") != "harvest_routing":
        raise HTTPException(409, "Routing preview expired. Review the plan again")
    if record.get("blocked"):
        raise HTTPException(409, "Resolve the routing shortages shown in the review before starting")
    try:
        operation = runs.continue_after if record.get("routing_run_id") else runs.create
        return await asyncio.to_thread(operation, _config(request).root, player, record)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/failure")
async def log_routing_failure(request: Request, player: str | None = None):
    import logging
    body = await _body(request)
    player = player_for_request(request, player)
    reason = str(body.get("reason") or "Immediate routing preparation failed")[:1000]
    reason = reason.replace("\n", " ").replace("\r", " ")
    logging.getLogger(__name__).error("CERES_ROUTING_FAILED player=%s reason=%s", player, reason)
    return {"logged": True}


@router.post("/api/harvest/routing/stop")
async def stop_run(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        return await asyncio.to_thread(runs.stop, _config(request).root, player, body.get("run_id"))
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/cancel")
async def cancel_run(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        return await asyncio.to_thread(runs.cancel, _config(request).root, player, body.get("run_id"))
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


_IN_FLIGHT_STEP_STATES = {"submitted", "pending", "settling"}


def _chainable_sale_pool(record: dict, step: dict) -> str | None:
    return runs.chainable_sale_pool(record, step)


def _review_members(record, first):
    return runs.review_members(record, first)


def _handoff_members(record, step):
    group = record.get("group_review")
    if not group or step["step_id"] not in group["member_ids"]:
        return [step]
    by_id = {item["step_id"]: item for item in record["steps"]}
    return [by_id[identifier] for identifier in group["member_ids"]]


def _next_signable_step(record: dict) -> dict | None:
    """Return the next review/sign target while earlier signed transactions reconcile."""
    by_id = {step.get("step_id"): step for step in record.get("steps", [])}
    in_flight_pools = set()
    for step in record.get("steps", []):
        state = step.get("state")
        if state in {"confirmed", "skipped"}:
            continue
        if state in _IN_FLIGHT_STEP_STATES:
            pool = _chainable_sale_pool(record, step)
            if pool is None:
                return None
            in_flight_pools.add(pool)
            continue
        if state in {"planned", "reviewed"}:
            dependency = by_id.get(step.get("depends_on_step_id"))
            if dependency and dependency.get("state") != "confirmed":
                return None
            pool = _chainable_sale_pool(record, step)
            if in_flight_pools and (pool is None or pool in in_flight_pools):
                return None
            return step
        return None
    return None


def _prefetchable_next_step(record: dict, handoff_step_id: str) -> dict | None:
    """Return only an independent sale that can be reviewed during this wallet request."""
    handed_off = next((step for step in record.get("steps", []) if step.get("step_id") == handoff_step_id), None)
    if not handed_off or handed_off.get("state") != "awaiting_wallet":
        raise routing.RoutingError("No matching wallet handoff can prefetch a routing review")
    members = _handoff_members(record, handed_off)
    if any(member.get("state") != "awaiting_wallet" or _chainable_sale_pool(record, member) is None for member in members):
        return None
    handed_off_ids = {member["step_id"] for member in members}
    used_pools = {_chainable_sale_pool(record, member) for member in members}
    passed_handoff = False
    for step in record.get("steps", []):
        if step.get("state") in {"confirmed", "skipped"}:
            continue
        if step.get("step_id") in handed_off_ids:
            passed_handoff = True
            continue
        if not passed_handoff:
            return None
        if step.get("state") not in {"planned", "reviewed"} or step.get("depends_on_step_id"):
            return None
        pool = _chainable_sale_pool(record, step)
        if pool is None or pool in used_pools:
            return None
        return step
    return None


def _review_locked(request: Request, player: str, folder, record: dict, step: dict, members: list[dict] | None = None) -> dict:
    """Build and persist a review while the caller owns the run lock."""
    from fides.op import DEFAULT_MAX_BYTES_PER_TX
    context = _context(request, player)
    members = members if members is not None else _review_members(record, step)
    reviewed = []
    operations = []
    _require_posting_contract()
    for member in members:
        try:
            fresh, public = _fresh_step(request, player, record, member, context)
        except routing.ProceedsTooSmall as exc:
            if not member.get("depends_on_step_id"):
                raise
            member["skip_metadata"] = {"prior_state": member.get("state"),
                                       "reason": "insufficient_dependency_proceeds", "skipped_at": time.time()}
            member["state"] = "skipped"
            member["error"] = str(exc)
            record.pop("group_review", None)
            if not runs.next_step(record):
                runs.advance_phase(record)
            runs.write(folder, record)
            return {"run": record, "review": None,
                    "signing_enabled": bool(_config(request).harvest_routing_signing_enabled)}
        quote = fresh.get("deposit") if fresh["kind"] == "liquidity" else fresh.get("quote", fresh)
        if fresh["kind"] == "liquidity" and quote.get("external") and quote.get("symbol") != "SPS":
            raise routing.RoutingError("This external resource has no supported funding contract")
        operation = build_operation(player, fresh, record["slippage"])
        item = {"step": fresh, **operation, "observed_at": public["observed_at"], "created": time.time()}
        item["digest"] = harvest_evidence.digest(item)
        reviewed.append(item)
        operations.extend(item["operations"])
    if len(json.dumps(operations, separators=(",", ":")).encode()) > DEFAULT_MAX_BYTES_PER_TX:
        raise routing.RoutingError("This sale group exceeds the transaction size limit")
    for member, item in zip(members, reviewed):
        member.update(state="reviewed", review=item)
    review = reviewed[0]
    record.pop("group_review", None)
    if len(members) > 1:
        review = {"step": reviewed[0]["step"], "operations": operations, "authority": "Posting",
                  "member_ids": [member["step_id"] for member in members], "created": time.time()}
        review["digest"] = harvest_evidence.digest(review)
        record["group_review"] = review
    runs.write(folder, record)
    return {"run": record, "review": review,
            "signing_enabled": bool(_config(request).harvest_routing_signing_enabled)}


def _drift_tolerant_fields(payload: dict) -> dict[str, str]:
    """ (round 2): payload fields allowed to move a little between review
    and handoff, mapped to which side of the reviewed value still protects the
    player — never the amount the player committed to spend/deposit as input.
      'floor'   — an amount the player RECEIVES; the fresh value may rise freely,
                  or fall only down to the reviewed slippage floor.
      'ceiling' — an amount the player PAYS; the fresh value may fall freely,
                  or rise only up to the reviewed slippage ceiling.
    swap_tokens already carries its own max_slippage, which these floors reuse.
    add_liquidity carries no protocol slippage field, so its live-computed
    dec_amount is bounded by the run's own record["slippage"] instead — see
    the handoff loop for where that tolerance is looked up."""
    if payload.get("op") == "add_liquidity":
        return {"dec_amount": "ceiling"}
    if payload.get("op") != "swap_tokens":
        return {}
    if "shares_out" in payload:
        return {"shares_out": "floor"}
    if "out_amount_1" in payload and "out_amount_2" in payload:
        return {"out_amount_1": "floor", "out_amount_2": "floor"}
    return {}


@router.post("/api/harvest/routing/skip-failed")
async def skip_failed_step(request: Request, player: str | None = None):
    """Explicitly skip one proven failed routing step without erasing evidence."""
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        def skip():
            with runs.locked(_config(request).root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                if record.get("state") in runs.TERMINAL:
                    raise routing.RoutingError("This routing run is already finished")
                step_id = body.get("step_id")
                current = runs.next_step(record)
                if not current or current.get("step_id") != step_id:
                    raise routing.RoutingError("Only the current failed routing step can be skipped")
                step = current
                # a dependent leg (step B) that is still "planned" is also skippable when
                # its first leg IS confirmed but exposes no DEC amount Ceres can quote from — an
                # evidence gap the maintainer must be able to walk away from rather than block on forever.
                dependency_unknown = (step.get("state") == "planned" and step.get("depends_on_step_id")
                                      and _dependency_proceeds(record, step) == (None, True))
                if step.get("state") != "failed" and not dependency_unknown:
                    raise routing.RoutingError("Only a failed routing step can be skipped")
                if dependency_unknown:
                    step["skip_metadata"] = {"prior_state": "planned", "reason": "dependency_proceeds_unknown",
                                             "skipped_at": time.time()}
                    step["state"] = "skipped"
                    step["error"] = "Step 1 confirmed but its DEC proceeds are unknown"
                    record.pop("group_review", None)
                    if not runs.next_step(record):
                        runs.advance_phase(record)
                    runs.write(folder, record)
                    return record
                # A failed step is skippable only when the wallet explicitly
                # reported no broadcast, or the game refused a transaction
                # that has a concrete transaction ID. Verification failures
                # remain pending and can never be hidden by this action.
                unsent = step.get("wallet_outcome") == "not_sent" and not step.get("tx_id")
                game_refused = bool(step.get("tx_id")) and isinstance(step.get("error"), str) and bool(step.get("error"))
                if not (unsent or game_refused):
                    raise routing.RoutingError("This failed step has no proven refusal or explicit unsent outcome")
                group = record.get("group_review") or {}
                member_ids = set(group.get("member_ids", []))
                if member_ids and step_id in member_ids:
                    members = [item for item in record.get("steps", []) if item.get("step_id") in member_ids]
                    unresolved = [item for item in members if item.get("state") not in {"failed", "confirmed", "skipped"}]
                    if unresolved:
                        raise routing.RoutingError("A grouped routing transaction still has unresolved members; check them before skipping")
                step["skip_metadata"] = {
                    "prior_state": "failed", "prior_error": step.get("error"),
                    "prior_tx_id": step.get("tx_id"), "prior_wallet_outcome": step.get("wallet_outcome"),
                    "reason": "user_skipped_proven_failed_step", "skipped_at": time.time(),
                }
                step["state"] = "skipped"
                record.pop("group_review", None)
                record["state"] = "ready"
                if not runs.next_step(record):
                    runs.advance_phase(record)
                runs.write(folder, record)
                return record
        return await asyncio.to_thread(skip)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/review")
async def review_step(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    root = _config(request).root
    try:
        def review():
            with runs.locked(root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                step = _next_signable_step(record)
                if record.get("routing_closed") or record["state"] in runs.TERMINAL:
                    raise routing.RoutingError("This step cannot be reviewed; check the saved run")
                if not step:
                    return {"run": record, "review": None, "signing_enabled": bool(_config(request).harvest_routing_signing_enabled)}
                return _review_locked(request, player, folder, record, step)
        return await asyncio.to_thread(review)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/prefetch")
async def prefetch_review(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    step_id = body.get("step_id")
    if not isinstance(step_id, str):
        raise HTTPException(422, "A routing handoff step is required for prefetch")
    try:
        def prefetch():
            with runs.locked(_config(request).root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                if record.get("routing_closed") or record["state"] in runs.TERMINAL:
                    raise routing.RoutingError("This immediate routing run has ended")
                step = _prefetchable_next_step(record, step_id)
                if not step:
                    return {"run": record, "review": None, "prefetched": False,
                            "signing_enabled": bool(_config(request).harvest_routing_signing_enabled)}
                result = _review_locked(request, player, folder, record, step, members=[step])
                result["prefetched"] = result["review"] is not None
                return result
        return await asyncio.to_thread(prefetch)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/handoff")
async def handoff(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    if not _config(request).harvest_routing_signing_enabled:
        raise HTTPException(403, "Harvest signing is disabled")
    try:
        def prepare():
            with runs.locked(_config(request).root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                step = _next_signable_step(record)
                if not step:
                    raise routing.RoutingError("No routing operation is ready")
                if record.get("routing_closed") or record.get("state") in runs.TERMINAL:
                    raise routing.RoutingError("This immediate routing run has ended")
                reviewed_members = _handoff_members(record, step)
                review = record.get("group_review") if len(reviewed_members) > 1 else step.get("review", {})
                if any(member["state"] != "reviewed" for member in reviewed_members) or review.get("digest") != body.get("review_digest"):
                    raise routing.RoutingError("This review has already been used or changed")
                review_age = time.time() - review["created"]
                if review_age > ROUTING_REVIEW_HARD_EXPIRY_SECONDS:
                    raise routing.RoutingError("The pool quote expired. Review fresh amounts before signing")
                requested_ids = body.get("member_ids")
                if requested_ids is None:
                    members = reviewed_members
                else:
                    if not isinstance(requested_ids, list):
                        raise routing.RoutingError("Selected routing members do not match this review")
                    if any(not isinstance(identifier, str) for identifier in requested_ids) or len(set(requested_ids)) != len(requested_ids):
                        raise routing.RoutingError("Selected routing members do not match this review")
                    requested = set(requested_ids)
                    reviewed_ids = {member["step_id"] for member in reviewed_members}
                    if not requested <= reviewed_ids:
                        raise routing.RoutingError("Selected routing members do not match this review")
                    members = [member for member in reviewed_members if member["step_id"] in requested]
                if not members:
                    if any(runs.chainable_sale_pool(record, member) is None for member in reviewed_members):
                        raise routing.RoutingError("Only optional reviewed sales can be excluded")
                    skipped_at = time.time()
                    for member in reviewed_members:
                        member["skip_metadata"] = {"prior_state": member.get("state"),
                                                   "reason": "user_excluded_from_reviewed_group",
                                                   "skipped_at": skipped_at}
                        member["state"] = "skipped"
                    record.pop("group_review", None)
                    record["state"] = "ready"
                    if not runs.next_step(record):
                        runs.advance_phase(record)
                    runs.write(folder, record)
                    return {"run_id": record["run_id"], "skipped": True, "run": record}
                context = _context(request, player)
                _require_posting_contract()
                comparable = lambda value: {key: item for key, item in value.items() if key != "n"}
                refreshed = []
                for member in members:
                    if member.get("kind") == "liquidity" and member.get("dec_source") != (
                            (member.get("review") or {}).get("step") or {}).get("dec_source"):
                        raise routing.RoutingError("The DEC source changed after review. Review the liquidity deposit again")
                    fresh, public = _fresh_step(request, player, record, member, context)
                    built = build_operation(player, fresh, record["slippage"])
                    fresh_c = comparable(built["payload"])
                    reviewed_c = comparable(member["review"]["payload"])
                    drift_fields = _drift_tolerant_fields(reviewed_c)
                    if fresh_c.keys() != reviewed_c.keys():
                        raise routing.RoutingError("Pool amounts changed. Review the new quote before signing")
                    if any(fresh_c[key] != reviewed_c[key] for key in fresh_c if key not in drift_fields):
                        raise routing.RoutingError("Pool amounts changed. Review the new quote before signing")
                    # add_liquidity carries no protocol max_slippage field, so its own
                    # live-derived dec_amount is bounded by the run's slippage instead.
                    swap_tolerance = routing.amount(reviewed_c.get("max_slippage", record["slippage"]))
                    liquidity_tolerance = routing.amount(record["slippage"])
                    for field, direction in drift_fields.items():
                        reviewed_value = routing.amount(reviewed_c[field])
                        fresh_value = routing.amount(fresh_c[field])
                        tolerance = swap_tolerance if built["payload"].get("op") == "swap_tokens" else liquidity_tolerance
                        if direction == "floor":
                            bound_ok = fresh_value >= reviewed_value * (Decimal(1) - tolerance / Decimal(100))
                        else:  # "ceiling" — a cost to the player; only an increase is bounded
                            bound_ok = fresh_value <= reviewed_value * (Decimal(1) + tolerance / Decimal(100))
                        if not bound_ok:
                            raise routing.RoutingError("Pool amounts changed. Review the new quote before signing")
                        if fresh_value != reviewed_value and reviewed_value:
                            drift_pct = float((fresh_value - reviewed_value) / reviewed_value * 100)
                            logging.getLogger(__name__).info(
                                "CERES_ROUTING_HANDOFF_DRIFT player=%s step=%s field=%s drift_pct=%.4f",
                                player, member["step_id"], field, drift_pct)
                    # round 2: signing the fresh payload under the SAME max_slippage would
                    # let the on-chain floor drop below what the player actually reviewed (fresh <
                    # reviewed, both discounted by the same %). Shrink max_slippage — never widen it
                    # beyond the reviewed one — just enough that the fresh floor still meets or beats
                    # the reviewed floor for every 'floor' field. Only swap_tokens carries this field.
                    floor_fields = [f for f, d in drift_fields.items() if d == "floor"]
                    if floor_fields and built["payload"].get("op") == "swap_tokens":
                        new_slippage = swap_tolerance
                        if swap_tolerance <= Decimal("0.5"):
                            # (1): already at the game's 0.5% floor, so there is no room
                            # left to shrink into. A downward drift can only be accepted when it
                            # did not actually fall below what was reviewed; any real fall here
                            # would sign a floor weaker than the one the player saw.
                            for field in floor_fields:
                                reviewed_value = routing.amount(reviewed_c[field])
                                fresh_value = routing.amount(fresh_c[field])
                                if fresh_value < reviewed_value:
                                    raise routing.RoutingError(
                                        "Pool amounts fell and the 0.5% minimum leaves no room. Review the new quote before signing")
                        else:
                            candidates = [swap_tolerance]
                            for field in floor_fields:
                                reviewed_value = routing.amount(reviewed_c[field])
                                fresh_value = routing.amount(fresh_c[field])
                                reviewed_floor = reviewed_value * (Decimal(1) - swap_tolerance / Decimal(100))
                                candidates.append((Decimal(1) - reviewed_floor / fresh_value) * Decimal(100))
                            # Round DOWN to the 0.1% step the slippage input itself accepts, so we
                            # never round back up into a floor weaker than the one just computed.
                            new_slippage = min(candidates).quantize(Decimal("0.1"), rounding=ROUND_DOWN)
                            if new_slippage < Decimal("0.5"):
                                raise routing.RoutingError("Pool amounts changed. Review the new quote before signing")
                        if new_slippage != swap_tolerance:
                            adjusted_fresh = dict(fresh, slippage=routing.text(new_slippage))
                            # (2): the quote's 'minimum' was computed under the ORIGINAL
                            # tolerance inside _fresh_step; recompute it under the shrunk slippage
                            # so the step card shows the floor actually signed, not a stale one.
                            quote_holder = adjusted_fresh.get("quote")
                            location = dict(quote_holder) if isinstance(quote_holder, dict) else adjusted_fresh
                            fresh_output = routing.amount(location["output"])
                            location["minimum"] = routing.text(
                                routing.floor(fresh_output * (Decimal(1) - new_slippage / Decimal(100))))
                            if isinstance(quote_holder, dict):
                                adjusted_fresh["quote"] = location
                            built = build_operation(player, adjusted_fresh, record["slippage"])
                            fresh = adjusted_fresh
                            logging.getLogger(__name__).info(
                                "CERES_ROUTING_HANDOFF_FLOOR_PRESERVED player=%s step=%s orig_slippage=%s new_slippage=%s",
                                player, member["step_id"], swap_tolerance, new_slippage)
                    refreshed.append((member, fresh, built, public))
                # the fresh, just-validated payload replaces the reviewed one so the
                # wallet signs exactly what was quoted here, never the stale reviewed amounts.
                # The step snapshot and digest move with it, so the step card and "Minimum
                # received" show the numbers actually signed. review_digest already did its one
                # job above (proving the client held a real review before this call); nothing
                # downstream re-checks the digest after handoff, so the client keeps using its
                # existing S.review as-is and never needs the recomputed one.
                undigested = lambda value: {key: item for key, item in value.items() if key != "digest"}
                refreshed_at = time.time()
                for member, fresh, built, public in refreshed:
                    member["review"]["step"] = fresh
                    member["review"]["payload"] = built["payload"]
                    member["review"]["operations"] = built["operations"]
                    member["review"]["observed_at"] = public["observed_at"]
                    member["review"]["created"] = refreshed_at
                    member["review"]["digest"] = harvest_evidence.digest(undigested(member["review"]))
                selected_ids = {member["step_id"] for member in members}
                if len(reviewed_members) > 1:
                    reviewed_ids = {member["step_id"] for member in reviewed_members}
                    first_index = min(index for index, item in enumerate(record["steps"])
                                      if item["step_id"] in reviewed_ids)
                    before = record["steps"][:first_index]
                    after = [item for item in record["steps"][first_index:]
                             if item["step_id"] not in reviewed_ids]
                    unselected = [member for member in reviewed_members if member["step_id"] not in selected_ids]
                    for member in unselected:
                        member["skip_metadata"] = {"prior_state": member.get("state"),
                                                   "reason": "user_excluded_from_reviewed_group",
                                                   "skipped_at": refreshed_at}
                        member["state"] = "skipped"
                    record["steps"] = before + members + unselected + after
                if len(members) > 1:
                    review = {"step": refreshed[0][1],
                              "operations": [op for _member, _fresh, built, _public in refreshed for op in built["operations"]],
                              "authority": "Posting", "member_ids": [member["step_id"] for member in members],
                              "created": refreshed_at}
                    review["digest"] = harvest_evidence.digest(review)
                    record["group_review"] = review
                else:
                    record.pop("group_review", None)
                    review = members[0]["review"]
                for index, member in enumerate(members):
                    member.update(state="awaiting_wallet", handoff_at=refreshed_at, operation_index=index)
                record["state"] = "awaiting_wallet"
                runs.write(folder, record)
                return {"run_id": record["run_id"], "step_id": members[0]["step_id"],
                        "operations": review["operations"], "authority": review["authority"]}
        return await asyncio.to_thread(prepare)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/transaction")
async def attach_transaction(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    tx = body.get("tx_id")
    if not isinstance(tx, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", tx):
        raise HTTPException(422, "Enter the transaction ID from your wallet history")
    try:
        def attach():
            with runs.locked(_config(request).root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                step = next((item for item in record["steps"] if item.get("step_id") == body.get("step_id")), None)
                if not step or step["state"] not in {"awaiting_wallet", "submitted", "pending"}:
                    raise routing.RoutingError("No matching wallet handoff is awaiting a transaction")
                members = _handoff_members(record, step)
                if any(member["state"] not in {"awaiting_wallet", "submitted", "pending"} or
                       (member.get("tx_id") and member["tx_id"] != tx.lower()) for member in members):
                    raise routing.RoutingError("A different transaction or outcome is already attached")
                for member in members:
                    member.update(tx_id=tx.lower(), state="submitted", submitted_at=time.time())
                record["state"] = "pending"
                runs.write(folder, record)
                return record
        return await asyncio.to_thread(attach)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


def _game_result(player: str, step: dict) -> dict:
    tx = step.get("tx_id")
    if not tx:
        raise routing.RoutingError("The wallet did not return a transaction ID. Recover it from wallet history")
    lookup_id = f"{tx}-{step['operation_index']}" if step.get("operation_index", 0) else tx
    response = FixedGetTransport(timeout=15, retries=1).get_json(API_ORIGIN + "/transactions/lookup?" + urlencode({"trx_id": lookup_id})).data
    info = response.get("trx_info") if isinstance(response, dict) else None
    if not info:
        return {"state": "pending", "seen": False}
    data = json.loads(info["data"]) if isinstance(info.get("data"), str) else info.get("data")
    if info.get("id") != lookup_id or info.get("player") != player or info.get("type") != "land_operation" or data != step["review"]["payload"]:
        raise routing.RoutingError("Game transaction does not match the reviewed account and complete operation")
    if info.get("success") is False:
        return {"state": "failed", "error": str(info.get("error") or "The game refused this operation")}
    result = json.loads(info["result"]) if isinstance(info.get("result"), str) else info.get("result")
    if info.get("success") is not True or not isinstance(result, dict):
        return {"state": "pending", "seen": True}
    if result.get("success") is False:
        return {"state": "failed", "error": str(result.get("error") or "The game refused this operation")}
    inner = result.get("result")
    if result.get("success") is not True or not isinstance(inner, dict):
        return {"state": "pending", "seen": True}
    if inner.get("success") is False:
        return {"state": "failed", "error": str(inner.get("error") or "The game refused this operation")}
    if inner.get("success") is not True:
        return {"state": "pending", "seen": True}
    effects = inner.get("data") or {}
    if not isinstance(effects, dict):
        return {"state": "pending", "seen": True}
    external = any(effects.get(key) for key in ("external_withdrawal_required", "external_swap_required", "pending_deposit_id"))
    receipt = {"received": [], "transaction_id": tx, "confirmed_at": info.get("created_date")}
    if not external and data.get("op") == "swap_tokens":
        reviewed = step["review"]["step"]
        quote = reviewed.get("quote", reviewed)
        symbol = quote.get("destination_symbol")
        value = effects.get("dec_amount") if symbol == "DEC" else effects.get("resource_amount")
        matches_resource = symbol == "DEC" or effects.get("resource") == symbol
        if matches_resource and value is not None:
            try:
                received = routing.amount(value)
            except routing.RoutingError:
                received = routing.ZERO
            if received > routing.ZERO:
                receipt["received"].append({"symbol": symbol, "amount": routing.text(received),
                                            "region_uid": reviewed.get("destination_region", routing.INVENTORY)})
    if not external and data.get("op") == "add_liquidity" and effects.get("resource") == data.get("resource_symbol"):
        try:
            resource = routing.amount(effects.get("resource_amount"), positive=True)
            dec = routing.amount(effects.get("dec_amount"), positive=True)
        except routing.RoutingError:
            pass
        else:
            receipt["deposited"] = [{"symbol": effects["resource"], "amount": routing.text(resource)},
                                    {"symbol": "DEC", "amount": routing.text(dec)}]
    return {"state": "settling" if external else "confirmed", "game_result": result, "receipt": receipt}


@router.post("/api/harvest/routing/verify")
async def verify_step(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        def verify():
            with runs.locked(_config(request).root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                unresolved = [step for step in record["steps"] if step["state"] in {"submitted", "pending", "settling"}]
                if not unresolved:
                    return record
                for step in unresolved:
                    diagnostic_started = diagnostic_time.monotonic()
                    try:
                        result = _game_result(player, step)
                        if result.get("seen") is False:
                            step["unseen_lookups"] = step.get("unseen_lookups", 0) + 1
                        elif result.get("seen") is True or result.get("state") != "pending":
                            step["unseen_lookups"] = 0
                        anchor = step.get("submitted_at") or step.get("handoff_at")
                        threshold = ROUTING_TX_EXPIRY_SECONDS if step.get("submitted_at") else 2 * ROUTING_TX_EXPIRY_SECONDS
                        if (result.get("state") == "pending" and result.get("seen") is False
                                and step.get("state") in {"submitted", "pending"}
                                and not step.get("game_result")
                                and step.get("unseen_lookups", 0) >= 2
                                and isinstance(anchor, (int, float))
                                and time.time() - anchor > threshold):
                            result = {"state": "failed",
                                      "error": "The signed transaction was not included on chain within an hour and has expired; nothing was executed.",
                                      "wallet_outcome": "expired"}
                        if result["state"] != "failed":
                            step.pop("error", None)
                        result.pop("seen", None)
                        step.update(result)
                        if result["state"] == "settling":
                            result = settle_bridge(player, step, older=body.get("older_bridge_history") is True)
                            step.update(result)
                    except Exception as exc:
                        step["error"] = str(exc) if isinstance(exc, routing.RoutingError) else "Confirmation is temporarily unavailable; the transaction remains unresolved."
                    record_verification(account=player, tx_id=step.get("tx_id"), attempt=None,
                                        elapsed_ms=(diagnostic_time.monotonic() - diagnostic_started) * 1000,
                                        operation_count=1, family="routing", operation_index=step.get("operation_index"),
                                        outcome={"state": step.get("state")}, error=bool(step.get("error")))
                states = {step["state"] for step in record["steps"]}
                record["state"] = ("pending" if states & {"submitted", "pending"} else "settling" if "settling" in states
                                   else "failed" if "failed" in states else "ready")
                if not states & {"submitted", "pending", "settling", "failed"}:
                    runs.advance_phase(record)
                runs.write(folder, record)
                return record
        return await asyncio.to_thread(verify)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/not-sent")
async def rejected_before_send(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    if body.get("broadcasted") is not False:
        raise HTTPException(422, "Unknown wallet outcomes must remain pending")
    try:
        def reject():
            with runs.locked(_config(request).root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                step = next((item for item in record["steps"] if item.get("step_id") == body.get("step_id")), None)
                if not step or step["state"] != "awaiting_wallet" or step.get("tx_id"):
                    raise routing.RoutingError("A submitted or ambiguous transaction cannot be marked unsent")
                members = _handoff_members(record, step)
                if any(member["state"] != "awaiting_wallet" or member.get("tx_id") for member in members):
                    raise routing.RoutingError("A submitted group cannot be marked unsent")
                for member in members:
                    member.update(state="failed", error="Wallet reported that nothing was broadcast", wallet_outcome="not_sent")
                record["state"] = "failed"
                runs.write(folder, record)
                return record
        return await asyncio.to_thread(reject)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/retry-unsent")
async def retry_unsent(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        def retry():
            with runs.locked(_config(request).root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                if record.get("routing_closed") or record.get("state") in runs.TERMINAL:
                    raise routing.RoutingError("This immediate routing run has ended")
                step = runs.next_step(record)
                if not step or step["state"] != "failed" or step.get("wallet_outcome") != "not_sent" or step.get("tx_id"):
                    raise routing.RoutingError("Only an explicitly unsent wallet request can be retried")
                members = _handoff_members(record, step)
                if any(member["state"] != "failed" or member.get("wallet_outcome") != "not_sent" or member.get("tx_id") for member in members):
                    raise routing.RoutingError("Only a wholly unsent group can be retried")
                for member in members:
                    member.setdefault("unsent_reviews", []).append(member.pop("review"))
                    for key in ("wallet_outcome", "error", "handoff_at", "operation_index"):
                        member.pop(key, None)
                    member["state"] = "planned"
                record.pop("group_review", None)
                record["state"] = "ready"
                runs.write(folder, record)
                return record
        return await asyncio.to_thread(retry)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/release-unsent")
async def release_unsent(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    tx = body.get("tx_id")
    dependency = body.get("depends_on")
    if (body.get("queue_state") != "expired" or not isinstance(tx, str)
            or not re.fullmatch(r"[a-fA-F0-9]{40}", tx) or not isinstance(dependency, str)
            or not re.fullmatch(r"[a-fA-F0-9]{40}", dependency)):
        raise HTTPException(422, "Invalid released queue transaction")
    try:
        def release():
            with runs.locked(_config(request).root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                step = next((item for item in record.get("steps", [])
                             if item.get("step_id") == body.get("step_id")), None)
                if (not step or step.get("state") not in {"submitted", "pending"}
                        or step.get("tx_id") != tx.lower()):
                    raise routing.RoutingError("No matching queued routing transaction can be released")
                members = _handoff_members(record, step)
                if any(member.get("state") not in {"submitted", "pending"}
                       or member.get("tx_id") != tx.lower() for member in members):
                    raise routing.RoutingError("The queued routing group no longer matches this release")
                for member in members:
                    member.setdefault("unsent_reviews", []).append({
                        "review": copy.deepcopy(member.get("review")), "released_tx_id": tx.lower(),
                        "depends_on": dependency.lower(), "released_at": time.time(),
                    })
                    member["state"] = "failed"
                    member["wallet_outcome"] = "expired"
                    member["error"] = ("Signed transaction expired behind an unresolved dependency. Its transaction "
                                       "identity is retained and it cannot be signed or broadcast again")
                record["state"] = "failed"
                runs.write(folder, record)
                return record
        return await asyncio.to_thread(release)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/harvest-plan")
async def harvest_plan(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        def prepare():
            root = _config(request).root
            with runs.locked(root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                if record.get("routing_closed") or record.get("state") in runs.TERMINAL:
                    raise routing.RoutingError("This immediate routing run has ended")
                if record["phase"] != "harvest" or record["state"] == "cancelled" or record.get("harvest_closed"):
                    raise routing.RoutingError("This workflow cannot start another harvest")
                stored = record.get("harvest_preview") or {}
                fresh = _build_preview(request, player, record["scope"], fresh=True)
                if fresh["blocked"] or not fresh["chunks"]:
                    raise routing.RoutingError("Harvest state is not confirmed. Refresh its costs and cooldowns before signing")
                old_members = [(member["op"], member["region_uid"], tuple(member["deed_uids"]))
                               for chunk in stored.get("chunks", []) for member in chunk.get("members", [])]
                new_members = [(member["op"], member["region_uid"], tuple(member["deed_uids"]))
                               for chunk in fresh["chunks"] for member in chunk.get("members", [])]
                if old_members and set(old_members) != set(new_members):
                    raise routing.RoutingError("Harvest state changed since routing review. Refresh before signing")
                fresh["routing_run_id"] = record["run_id"]
                saved = harvest_evidence.save_preview(player, fresh)
                record["harvest_preview"] = saved
                runs.write(folder, record)
                attempts = runs.harvest_attempts(root, player, record)
                return {"run": record, "preview": _strip(saved), "attempts": attempts}
        return await asyncio.to_thread(prepare)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/harvest-transaction")
async def recover_harvest_transaction(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    tx = body.get("tx_id")
    if not isinstance(tx, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", tx):
        raise HTTPException(422, "Enter the transaction ID from wallet history")
    try:
        def attach():
            root = _config(request).root
            with runs.locked(root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                if record["phase"] != "harvest" or record["state"] == "cancelled":
                    raise routing.RoutingError("This workflow is not awaiting harvest confirmation")
                identifier = body.get("attempt_id")
                if identifier not in {ref["attempt_id"] for ref in record.get("harvest_attempts", [])}:
                    raise routing.RoutingError("This harvest attempt is not part of the saved workflow")
                attempt = harvest_evidence.load_attempt(root, player, identifier)
                if not attempt:
                    raise routing.RoutingError("Saved harvest evidence is missing; do not repeat this transaction")
                if attempt.get("tx_id") and attempt["tx_id"] != tx.lower():
                    raise routing.RoutingError("A different transaction is already recorded")
                return harvest_evidence.update_attempt(root, player, identifier, tx_id=tx.lower(), state="submitted")
        return await asyncio.to_thread(attach)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/harvest/routing/close-harvest")
async def close_partial_harvest(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        return await asyncio.to_thread(runs.close_harvest, _config(request).root, player, body.get("run_id"))
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc


def _funding_effects(player: str, step: dict) -> dict:
    result = _game_result(player, step)
    if result["state"] != "settling":
        raise routing.RoutingError("This operation is not awaiting external SPS funding")
    effects = result["game_result"]["result"]["data"]
    if step["kind"] == "liquidity":
        if not effects.get("pending_deposit_id") or step["review"]["step"].get("deposit", {}).get("symbol") != "SPS":
            raise routing.RoutingError("No SPS liquidity funding request is available")
    elif not effects.get("external_swap_required"):
        raise routing.RoutingError("This transfer does not require SPS funding")
    return effects


def _funding_balance(request: Request, player: str, record: dict, review: dict) -> None:
    if record.get("phase") == "after" and routing.amount(review["amount"]) > _remaining_harvest(request, player, record).get((routing.INVENTORY, "SPS"), routing.ZERO):
        raise routing.RoutingError("SPS funding exceeds confirmed proceeds from this harvest")
    reserve = max((routing.amount(item["amount"]) for item in record.get("reserves", [])
                   if item.get("region_uid") == routing.INVENTORY and item.get("symbol") == "SPS"), default=routing.ZERO)
    if routing.amount(review["amount"]) > max(routing.ZERO, sps_balance(player) - reserve):
        raise routing.RoutingError("The SPS funding amount exceeds the available balance after reserves")


@router.post("/api/harvest/routing/funding")
async def funding_action(request: Request, player: str | None = None):
    body = await _body(request)
    player = player_for_request(request, player)
    action = body.get("action")
    if action not in {"review", "handoff", "transaction", "verify", "not-sent", "recover-confirmed"}:
        raise HTTPException(422, "Unknown SPS funding action")
    if action == "handoff" and not _config(request).harvest_routing_signing_enabled:
        raise HTTPException(403, "Harvest routing signing is disabled")
    try:
        def execute():
            with runs.locked(_config(request).root, player) as folder:
                record = runs.read(folder, body.get("run_id"), player)
                if action in {"review", "handoff", "recover-confirmed"} and (record.get("routing_closed") or record.get("state") in runs.TERMINAL):
                    raise routing.RoutingError("This immediate routing run has ended")
                step = runs.next_step(record)
                if not step or step["state"] != "settling" or step["step_id"] != body.get("step_id"):
                    raise routing.RoutingError("No matching routing step awaits SPS settlement")
                funding = step.get("funding")
                if action == "review":
                    if funding and funding["state"] not in {"reviewed", "not_sent", "expired"}:
                        raise routing.RoutingError("Recover the existing SPS funding request before another review")
                    review = build_sps_funding(player, _funding_effects(player, step), step["kind"])
                    _funding_balance(request, player, record, review)
                    review["created"] = time.time()
                    review["digest"] = harvest_evidence.digest(review)
                    if funding:
                        step.setdefault("funding_reviews", []).append(funding)
                    step["funding"] = {"state": "reviewed", "review": review}
                elif action == "handoff":
                    review = (funding or {}).get("review", {})
                    if not funding or funding["state"] != "reviewed" or review.get("digest") != body.get("review_digest"):
                        raise routing.RoutingError("The SPS funding review has changed or was already used")
                    if time.time() - review["created"] > ROUTING_REVIEW_HARD_EXPIRY_SECONDS:
                        raise routing.RoutingError("The SPS funding review reached its safety limit. Review fresh details before signing")
                    fresh = build_sps_funding(player, _funding_effects(player, step), step["kind"])
                    comparable = lambda value: {key: item for key, item in value["payload"]["params"].items() if key != "n"}
                    if comparable(fresh) != comparable(review):
                        raise routing.RoutingError("SPS funding details changed. Review the new request")
                    _funding_balance(request, player, record, review)
                    funding.update(state="awaiting_wallet", handoff_at=time.time())
                    runs.write(folder, record)
                    return {"run_id": record["run_id"], "step_id": step["step_id"],
                            "operations": review["operations"], "authority": review["authority"]}
                elif action == "transaction":
                    tx = body.get("tx_id")
                    if not isinstance(tx, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", tx):
                        raise routing.RoutingError("Enter the SPS funding transaction ID from wallet history")
                    if not funding or funding["state"] not in {"awaiting_wallet", "submitted", "pending"}:
                        raise routing.RoutingError("No SPS funding handoff is awaiting a transaction")
                    if funding.get("tx_id") and funding["tx_id"] != tx.lower():
                        raise routing.RoutingError("A different SPS funding transaction is already attached")
                    funding.update(state="submitted", tx_id=tx.lower(), submitted_at=time.time())
                elif action == "recover-confirmed":
                    if not funding or funding["state"] not in {"failed", "expired"}:
                        raise routing.RoutingError("Only a refused or expired funding record can adopt a separately confirmed transfer")
                    tx = body.get("tx_id")
                    if not isinstance(tx, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", tx):
                        raise routing.RoutingError("Enter the successful SPS funding transaction ID")
                    tx = tx.lower()
                    for saved in runs.records(folder, player):
                        saved_steps = saved.get("steps", []) + [item for group in saved.get("phase_steps", {}).values() for item in group]
                        for saved_step in saved_steps:
                            evidence = [saved_step.get("funding") or {}, *saved_step.get("funding_reviews", [])]
                            if any(item.get("tx_id") == tx for item in evidence):
                                raise routing.RoutingError("This funding transaction is already attached to a routing step")
                    receipt = recovered_funding_receipt(player, tx, funding["review"])
                    step.setdefault("funding_reviews", []).append(copy.deepcopy(funding))
                    step["funding"] = {"review": funding["review"], "tx_id": tx,
                                       "recovered_at": time.time(), **receipt}
                elif action == "verify":
                    if not funding or not funding.get("tx_id"):
                        raise routing.RoutingError("Recover the SPS funding transaction ID first")
                    if funding["state"] not in {"submitted", "pending", "funded", "failed", "expired"}:
                        raise routing.RoutingError("This SPS funding request cannot be checked yet")
                    # an already-terminal state (funded/failed/expired) is never re-verified.
                    if funding["state"] not in {"funded", "failed", "expired"}:
                        result = funding_receipt(player, funding["tx_id"], funding["review"])
                        if result.get("state") == "pending":
                            if result.get("seen") is False:
                                funding["unseen_lookups"] = funding.get("unseen_lookups", 0) + 1
                            else:
                                funding["unseen_lookups"] = 0
                            anchor = funding.get("submitted_at") or funding.get("handoff_at")
                            threshold = ROUTING_TX_EXPIRY_SECONDS if funding.get("submitted_at") else 2 * ROUTING_TX_EXPIRY_SECONDS
                            if (result.get("seen") is False
                                    and funding.get("state") in {"submitted", "pending"}
                                    and funding.get("unseen_lookups", 0) >= 2
                                    and isinstance(anchor, (int, float))
                                    and time.time() - anchor > threshold):
                                result = {"state": "expired",
                                          "error": "The SPS funding transfer has not been seen by the validator within an hour and is treated as expired; if it did go through, attach its transaction ID below."}
                        else:
                            funding["unseen_lookups"] = 0
                        result.pop("seen", None)
                        funding.update(result)
                else:
                    if body.get("broadcasted") is not False or not funding or funding["state"] != "awaiting_wallet" or funding.get("tx_id"):
                        raise routing.RoutingError("Only an explicitly unsent SPS funding request can be retried")
                    funding.update(state="not_sent", error="Wallet reported that nothing was broadcast")
                runs.write(folder, record)
                return record
        return await asyncio.to_thread(execute)
    except routing.RoutingError as exc:
        raise HTTPException(409, str(exc)) from exc
