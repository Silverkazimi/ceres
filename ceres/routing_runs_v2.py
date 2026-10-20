"""Account-locked v2 routing runs with guarded outbound reads.

Creation, attach and stop are local; LP claim reads fresh pool and DEC state.
One reconcile request dispatches at most one Hive probe or game lookup.
Every physical attempt is charged before dispatch.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import hmac
import threading
import json
import logging
from pathlib import Path
import re
import secrets
import time
from typing import Any, Callable
from urllib.parse import quote, urlencode

from fides import verify as chain_verify
from fides import op as fides_op
from fides.op import DEFAULT_MAX_BYTES_PER_TX
from praetoria.transport import API_ORIGIN, LAND_ORIGIN, FixedGetTransport

from . import harvest_evidence, harvest_routing as routing, routing_plan, routing_safety
from .harvest_routing_operations import build as build_operation
from .security import resolve_under, validate_player


_log = logging.getLogger("ceres.routing")
_RUN_ID = re.compile(r"[a-f0-9]{32}")
_TX_ID = re.compile(r"[a-fA-F0-9]{40}")
_TERMINAL = {"confirmed", "refused", "skipped", "stopped"}
# Hive refuses a transaction whose expiration is over one hour ahead; a margin as in .
TX_EXPIRY_SECONDS = 3900
_CALLS = {"preview_context": 9, "harvest_receipt": 3, "segment_context": 8,
          "hive_head": 4, "chain_probe": 16, "game_verdict": 65,
          "bridge_history": 8, "pending_id": 1, "manual_retry": 6}


# (stage 2): a one-time run lives in the memory of this server process ONLY -- no run file exists. A restart ends every run by nature; what may have been
# sent is kept as the tiny safety record, which is written to disk the moment a bundle reaches a wallet (see routing_safety.sync_run). Each run is operated
# with its own unguessable token (shown once, at creation), so another caller who knows an account name or a run id can neither read nor change it.
_RUNS: dict[tuple[str, str], dict] = {}
_MEM = threading.RLock()
_ACCOUNT_LOCKS: dict[str, threading.RLock] = {}
MAX_LIVE_RUNS = 5
IDLE_LEASE_SECONDS = 2 * 3600


class Handle:
    """What `folder` used to be: the data root and the account of one locked section."""
    __slots__ = ("root", "player")

    def __init__(self, root: Path, player: str):
        self.root, self.player = Path(root), player


@contextmanager
def locked(root: Path, player: str):
    """Serialize all v2 run changes for one account; makes zero outbound calls."""
    player = validate_player(player)
    with _MEM:
        lock = _ACCOUNT_LOCKS.setdefault(player, threading.RLock())
    with lock:
        yield Handle(root, player)


def _check_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise routing.RoutingError("Invalid routing run")
    return run_id


def read(folder: Handle, player: str, run_id: str) -> dict:
    """Read one run of this account (a private copy); zero outbound calls."""
    _check_id(run_id)
    with _MEM:
        record = _RUNS.get((player, run_id))
        record = copy.deepcopy(record) if record is not None else None
    if record is None:
        raise routing.RoutingError("Routing run not found")
    return record


def token_hash(token: Any) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def verify_token(record: dict, token: Any) -> None:
    """The run's own token (shown once at creation) is required for everything that reads or changes the run."""
    if not isinstance(token, str) or not 20 <= len(token) <= 200 or not hmac.compare_digest(token_hash(token), str(record.get("token_hash") or "")):
        raise routing.RoutingError("Routing run not found")   # the same answer as a missing run: nothing is revealed


def public(value: Any) -> Any:
    """What the page may see: never the token's hash."""
    if isinstance(value, dict) and "token_hash" in value:
        value = {key: item for key, item in value.items() if key != "token_hash"}
    return value


_UNRESOLVED = {"claimed", "attached", "checking", "outcome_unknown"}


def _root(folder: Handle) -> Path:
    return folder.root


def _unresolved(record: dict) -> bool:
    return any(item.get("state") in _UNRESOLVED for item in record.get("bundles", {}).values())


def _delete(folder: Handle, record: dict) -> None:
    """: a one-time run is deleted. If any of its transactions reached the chain, the harvest receipts it consumed are never routed again."""
    if any(item.get("state") == "confirmed" for item in record.get("bundles", {}).values()):
        routing_safety.mark_routed(_root(folder), record["player"], record.get("harvest_attempt_ids") or [])
    with _MEM:
        _RUNS.pop((record["player"], record["run_id"]), None)


def retire(folder: Handle, record: dict) -> None:
    """Delete a run NOW (page refresh, the idle lease, server start). Whatever may already have been sent is kept as a tiny safety record first."""
    routing_safety.sync_run(_root(folder), record["player"], record)
    _delete(folder, record)


def write(folder: Handle, record: dict) -> None:
    """Keep the run's state before any read; zero outbound calls. : a run that is done or failed with nothing in doubt is deleted here."""
    record["updated"] = time.time()
    routing_safety.sync_run(_root(folder), record["player"], record)   # what reached a wallet and is not settled is on disk the moment it exists
    if record.get("state") in {"complete", "stopped"} and not _unresolved(record):
        _delete(folder, record)
        return
    stored = copy.deepcopy({key: item for key, item in record.items() if key != "run_token"})
    with _MEM:
        _RUNS[(record["player"], record["run_id"])] = stored


def purge_all(root: Path, *, now: float | None = None, idle_only: bool = False) -> int:
    """Delete runs: every one (server start; memory is empty then, so this clears LEGACY run files of releases before 0.15) or, with `idle_only`, those idle past the lease."""
    now = time.time() if now is None else now
    count = 0
    if not idle_only:
        base = Path(root) / "routing_runs_v2"
        if base.is_dir():
            for account in list(base.iterdir()):
                if not account.is_dir():
                    continue
                for path in list(account.glob("run-*.json")):
                    try:
                        record = json.loads(path.read_text(encoding="utf-8"))
                        routing_safety.sync_run(Path(root), record["player"], record)
                        path.unlink()
                        count += 1
                    except (OSError, ValueError, KeyError):
                        continue   # an unreadable file is left alone; it never blocks anything
                for leftover in (account / ".lock",):
                    try:
                        leftover.unlink()
                    except OSError:
                        pass
                try:
                    account.rmdir()
                except OSError:
                    pass
    with _MEM:
        stale = [(key, copy.deepcopy(record)) for key, record in _RUNS.items()
                 if not idle_only or now - float(record.get("updated") or record.get("created") or now) > IDLE_LEASE_SECONDS]
    for (player, _run_id), record in stale:
        with locked(root, player) as folder:
            retire(folder, record)
        count += 1
    return count


def _validate_manifest(manifest: Any, player: str, enabled: set[str]) -> None:
    """Reject a preview outside the signed fixed budget; zero outbound calls."""
    if not isinstance(manifest, dict) or manifest.get("version") != 2 or manifest.get("cap") != fides_op.DEFAULT_MAX_OPS_PER_TX:
        raise routing.RoutingError("A current v2 routing preview is required")
    budget = manifest.get("call_budget") or {}
    bundles = manifest.get("bundles")
    if not isinstance(bundles, list) or not bundles:
        raise routing.RoutingError("A routing preview needs at least one bundle")
    expected = routing_plan.call_budget(bundles)  # the budget is a function of the plan, not a fixed number
    if (budget.get("account") != player or budget.get("run_ceiling") != expected["run_ceiling"]
            or budget.get("action_ceilings") != expected["action_ceilings"]):
        raise routing.RoutingError("Routing call budget changed; preview again")
    operations = [op for bundle in bundles for op in bundle.get("operations", [])]
    members = [member for op in operations for member in op.get("members", [])]
    if (any(not isinstance(member.get("nonce"), str) or not member["nonce"] for member in members)
            or len({member["nonce"] for member in members}) != len(members)):
        raise routing.RoutingError("Routing members need distinct nonces")
    if any(len(bundle.get("operations", [])) > fides_op.DEFAULT_MAX_OPS_PER_TX or len({op.get("pool") for op in bundle["operations"]}) != len(bundle["operations"])
           or bundle.get("authority") != "posting" for bundle in bundles):
        raise routing.RoutingError(f"Routing bundle exceeds the {fides_op.DEFAULT_MAX_OPS_PER_TX}-operation pool rule")
    kinds = {member.get("kind") for member in members}
    if not kinds.issubset(enabled):
        raise routing.RoutingError("A route kind is disabled")
    unsigned = {key: value for key, value in manifest.items() if key != "digest"}
    if harvest_evidence.digest(unsigned) != manifest.get("digest"):
        raise routing.RoutingError("Routing manifest digest changed")


def create(folder: Handle, player: str, preview: dict, enabled: set[str], token: str | None = None) -> dict:
    """Persist one immutable server preview once; calls no outbound endpoint."""
    if (preview.get("player") != player or preview.get("phase") != "after"
            or preview.get("blocked") or not preview.get("manifest")):
        raise routing.RoutingError("A ready post-harvest preview is required")
    if (not isinstance(preview.get("steps"), list) or not isinstance(preview.get("rules"), list)
            or not isinstance((preview.get("state") or {}).get("pools"), list)):
        raise routing.RoutingError("Routing preview lacks its server context")
    manifest = copy.deepcopy(preview["manifest"])
    _validate_manifest(manifest, player, enabled)
    root = _root(folder)
    with _MEM:
        priors = [copy.deepcopy(record) for (owner, _id), record in _RUNS.items() if owner == player]
    for prior in priors:
        if prior.get("preview_id") == preview["preview_id"]:
            if prior["manifest"]["digest"] != manifest["digest"]:
                raise routing.RoutingError("This preview already belongs to a different run")
            verify_token(prior, token)   # only the page that created the run gets it back
            return prior
    live = sorted(priors, key=lambda record: float(record.get("created") or 0))
    while len(live) >= MAX_LIVE_RUNS:   # an earlier run never blocks a new one: the oldest run that never reached a wallet makes room
        idle = next((record for record in live if not _unresolved(record) and all(item.get("state") == "planned" for item in record["bundles"].values())), None)
        if idle is None:
            raise routing.RoutingError("Too many routing runs are open; finish or refresh one first")
        retire(folder, idle)
        live.remove(idle)
    attempt_ids = preview.get("harvest_attempt_ids") or []
    if routing_safety.is_routed(root, player, attempt_ids):
        raise routing.RoutingError("These harvest receipts were already routed")
    run_id = secrets.token_hex(16)
    step_to_operation = {member["step_id"]: op["id"] for bundle in manifest["bundles"]
                         for op in bundle["operations"] for member in op["members"]}
    step_to_member = {member["step_id"]: member["nonce"] for bundle in manifest["bundles"]
                      for op in bundle["operations"] for member in op["members"]}
    operations = {op["id"]: {"state": "planned", "polls": 0,
                            "members": {member["nonce"]: {"state": "planned", "route_id": member["route_id"]}
                                        for member in op["members"]}}
                  for bundle in manifest["bundles"] for op in bundle["operations"]}
    bundles = {bundle["id"]: {"state": "planned", "revision": 1}
               for bundle in manifest["bundles"]}
    reservations = {member["nonce"]: {"route_id": member["route_id"],
                    "region": op["source_region"], "symbol": op["source_symbol"],
                    "amount": member["source_amount"], "state": "held"}
                    for bundle in manifest["bundles"] for op in bundle["operations"]
                    for member in op["members"]}
    now = time.time()
    record = {"run_id": run_id, "revision": 1, "player": player,
              "preview_id": preview["preview_id"], "preview_digest": preview["preview_digest"],
              "harvest_attempt_ids": copy.deepcopy(preview.get("harvest_attempt_ids", [])),
              "manifest": manifest, "source_steps": copy.deepcopy(preview["steps"]),
              "source_pools": copy.deepcopy(preview["state"]["pools"]),
              "source_rules": copy.deepcopy(preview["rules"]),
              "source_reserves": copy.deepcopy(preview.get("reserves", [])),
              "protected_wallet_dec": routing.text(routing.amount(preview.get("protected_wallet_dec", "0"))),
              "step_to_operation": step_to_operation,
              "step_to_member": step_to_member,
              "operations": operations, "bundles": bundles,
              "reservations": reservations,
              "call_budget": {"ceiling": manifest["call_budget"]["run_ceiling"], "total": 0, "actions": {}, "endpoints": {},
                              "log": [], "stopped": False},
              "state": "ready", "created": now, "updated": now}
    run_token = secrets.token_urlsafe(32)
    record["token_hash"] = token_hash(run_token)
    write(folder, record)
    return {**record, "run_token": run_token}


def _bundle(record: dict, bundle_id: str) -> dict:
    """Resolve one manifest bundle; makes zero outbound calls."""
    bundle = next((item for item in record["manifest"]["bundles"] if item["id"] == bundle_id), None)
    if bundle is None:
        raise routing.RoutingError("Bundle is not in this run")
    return bundle


def _parent_state(record: dict, step_id: str) -> str | None:
    """Resolve a step dependency through its coalesced operation; zero calls."""
    operation_id = record["step_to_operation"].get(step_id, step_id)
    operation = record["operations"].get(operation_id, {})
    member_nonce = record["step_to_member"].get(step_id)
    return operation.get("members", {}).get(member_nonce, {}).get("state") if member_nonce else operation.get("state")


def _claim_operation(record: dict, op: dict) -> tuple[list, dict]:
    """Build one exact Posting payload from saved review; makes zero outbound calls."""
    if not {member["kind"] for member in op["members"]}.issubset({"sale_dec", "lp_sell_half"}) or op["kind"] not in {"sell", "liquidity_funding_swap"}:
        raise routing.RoutingError("This route kind has no released v2 operation builder")
    by_id = {step["id"]: step for step in record["source_steps"]}
    member_steps = [by_id[member["step_id"]] for member in op["members"]]
    if op["depends_on"]:
        raise routing.RoutingError("Dependent amounts require a receipt-backed review in their release slice")
    step = copy.deepcopy(member_steps[0])
    if len(member_steps) > 1 or any(member["kind"] == "lp_sell_half" for member in op["members"]):
        if not all(item["kind"] in {"sell", "liquidity_funding_swap"} for item in member_steps):
            raise routing.RoutingError("Only compatible resource sales can be coalesced")
        quantity = sum((routing.amount(item["source_amount"]) for item in op["members"]), routing.ZERO)
        pools = {item["symbol"]: routing.Pool(item["id"], item["symbol"],
                 routing.amount(item["resource_quantity"]), routing.amount(item["dec_quantity"]),
                 routing.amount(item["total_shares"]), bool(item["external"]))
                 for item in record["source_pools"]}
        quote = routing.swap_quote(step["symbol"], "DEC", quantity, pools, op["slippage"])
        step.update(quantity=routing.text(quantity), quote=quote.public())
    built = build_operation(record["player"], step, op["slippage"])
    payload = built["payload"]
    payload["n"] = op["members"][0]["nonce"]
    operation = built["operations"][0]
    operation[1]["json"] = json.dumps(payload, separators=(",", ":"))
    return operation, payload


def _lp_context(folder: Path, record: dict) -> tuple[dict[str, routing.Pool], Any]:
    """Charge exactly one fresh pool request and one maintainer DEC balance request."""
    transport = FixedGetTransport(timeout=15, retries=0)
    try:
        _charge(folder, record, "segment_context", "/land/liquidity/pools", "global")
        response = transport.get_json(LAND_ORIGIN + "/land/liquidity/pools").data
        if (not isinstance(response, dict) or response.get("status") != "success"
                or not isinstance(response.get("data"), list)):
            raise routing.RoutingError("Fresh liquidity pools are unavailable")
        pools = [routing.Pool.parse(row) for row in response["data"]]
        if len({pool.symbol for pool in pools}) != len(pools) or len({pool.id for pool in pools}) != len(pools):
            raise routing.RoutingError("Fresh liquidity pool identities are ambiguous")
        _charge(folder, record, "segment_context", "/players/balances", record["player"])
        balances = transport.get_json(API_ORIGIN + "/players/balances?username=" + quote(record["player"], safe="")).data
        if not isinstance(balances, list):
            raise routing.RoutingError("Fresh wallet DEC balance is unavailable")
        dec = [row for row in balances if isinstance(row, dict) and row.get("player") == record["player"]
               and row.get("token") == "DEC"]
        if len(dec) != 1:
            raise routing.RoutingError("Fresh wallet DEC balance is unavailable")
        return {pool.symbol: pool for pool in pools}, routing.amount(dec[0].get("balance"))
    except routing.RoutingError:
        raise
    except Exception as exc:
        raise routing.RoutingError("Fresh LP pool or DEC balance unavailable; no wallet claim was made") from exc


def _claim_lp_operation(record: dict, op: dict, pools: dict[str, routing.Pool], wallet: dict) -> tuple[list, dict, dict]:
    """Reprice one receipt-backed LP share within its saved DEC reservation."""
    if len(op["members"]) != 1 or op["depends_on"] or op["source_symbol"] == "DEC":
        raise routing.RoutingError("Wallet LP requires one independent harvested resource share")
    member = op["members"][0]
    step = next((item for item in record["source_steps"] if item["id"] == member["step_id"]), None)
    if not step or step.get("kind") != "liquidity" or step.get("dec_source") != "wallet":
        raise routing.RoutingError("Wallet LP source review is unavailable")
    pool = pools.get(op["pool"])
    if pool is None or pool.external or pool.id != op["pool_id"]:
        raise routing.RoutingError("The reviewed internal LP pool changed; review again")
    available = routing.floor(max(routing.ZERO, wallet["balance"] - wallet["floor"] - wallet["held"]))
    source = routing.amount(member["source_amount"], positive=True)
    deposit = routing.liquidity_deposit(pool, source)
    required = routing.amount(deposit["dec_amount"], positive=True)
    # S4 / L2-04: the maintainer approved a DEC amount. A fresh price may cost a little more (the same tolerance the swap carries), never
    # a different order of magnitude: above the reviewed amount plus that tolerance the maintainer reviews again, and nothing is signed.
    reviewed = member.get("required_wallet_dec")
    if reviewed is not None:
        ceiling = routing.floor(routing.amount(reviewed, positive=True) * (1 + routing.validate_slippage(op["slippage"]) / 100))
        if required > ceiling:
            raise routing.RoutingError(
                f"This LP route now needs {routing.text(required)} DEC; you reviewed {routing.text(routing.amount(reviewed))} DEC. Review the route again. No signature was requested")
    if required > available:
        raise routing.RoutingError(
            f"This LP route needs {routing.text(required)} DEC; wallet DEC above Protected wallet DEC is {routing.text(available)} DEC. No signature was requested")
    if routing.amount(deposit["resource_amount"]) < routing.amount(member["protected_minimum"]):
        raise routing.RoutingError("Fresh LP price cannot keep the protected resource minimum")
    fresh_step = {**step, "quantity": deposit["resource_amount"], "deposit": deposit}
    built = build_operation(record["player"], fresh_step, op["slippage"])
    payload = built["payload"]
    payload["n"] = member["nonce"]
    operation = built["operations"][0]
    operation[1]["json"] = json.dumps(payload, separators=(",", ":"))
    wallet["held"] += routing.amount(deposit["dec_amount"])
    return operation, payload, deposit


def _claim_sell_half_operation(record: dict, op: dict, pools: dict[str, routing.Pool], wallet: dict) -> tuple[list, dict, dict]:
    """Size one dependent LP deposit from its confirmed sale share only."""
    if len(op["members"]) != 1 or len(op["depends_on"]) != 1:
        raise routing.RoutingError("Sell-half LP requires one confirmed sale parent")
    member = op["members"][0]
    parent_nonce = record["step_to_member"].get(op["depends_on"][0])
    parent_id = record["step_to_operation"].get(op["depends_on"][0])
    parent = record["operations"].get(parent_id, {}).get("members", {}).get(parent_nonce, {})
    if (parent.get("state") != "confirmed" or parent.get("route_id") != member["route_id"]
            or not parent.get("received_dec")):
        raise routing.RoutingError("A confirmed sale receipt with attributed DEC is required")
    step = next((item for item in record["source_steps"] if item["id"] == member["step_id"]), None)
    if not step or step.get("kind") != "liquidity" or step.get("dec_source") != "swap_half":
        raise routing.RoutingError("Sell-half LP source review is unavailable")
    pool = pools.get(op["pool"])
    if pool is None or pool.external or pool.id != op["pool_id"]:
        raise routing.RoutingError("The reviewed internal LP pool changed; review again")
    proceeds = routing.amount(parent["received_dec"], positive=True)
    available = routing.floor(max(routing.ZERO, wallet["balance"] - wallet["floor"] - wallet["held"]))
    cap = min(proceeds, available)
    if cap <= 0:
        raise routing.RoutingError("Confirmed sale DEC is unavailable for the LP deposit")
    source = routing.amount(member["source_amount"], positive=True)
    quantity = min(source, routing.floor(cap * pool.resource / pool.dec))
    deposit = routing.liquidity_deposit(pool, quantity)
    if routing.amount(deposit["dec_amount"]) > cap:
        deposit = routing.liquidity_deposit(pool, quantity - routing.QUANTUM)
    if (routing.amount(deposit["dec_amount"]) > cap or
            routing.amount(deposit["resource_amount"]) < routing.amount(member["protected_minimum"])):
        raise routing.RoutingError("Confirmed sale proceeds cannot keep the protected LP resource minimum")
    fresh_step = {**step, "quantity": deposit["resource_amount"], "deposit": deposit}
    built = build_operation(record["player"], fresh_step, op["slippage"])
    payload = built["payload"]
    payload["n"] = member["nonce"]
    operation = built["operations"][0]
    operation[1]["json"] = json.dumps(payload, separators=(",", ":"))
    wallet["held"] += routing.amount(deposit["dec_amount"])
    return operation, payload, deposit


def claim(folder: Path, record: dict, bundle_id: str, enabled: set[str], signing: bool,
          excluded_route_ids: list[str] | None = None) -> dict:
    """Claim one fixed bundle once; meter LP context, never call a wallet."""
    _validate_manifest(record["manifest"], record["player"], enabled)
    if not signing or record["state"] != "ready" or record["call_budget"]["stopped"]:
        raise routing.RoutingError("Routing signing is disabled or this run is stopped")
    bundle = _bundle(record, bundle_id)
    status = record["bundles"][bundle_id]
    if status["state"] == "claimed":
        return status["claim"]
    if status["state"] != "planned":
        raise routing.RoutingError("This bundle has already been handed to the wallet")
    if excluded_route_ids is None:
        excluded_route_ids = []
    bundle_members = [member for op in bundle["operations"] for member in op["members"]]
    by_route = {member["route_id"]: member for member in bundle_members}
    if (not isinstance(excluded_route_ids, list)
            or any(not isinstance(route_id, str) for route_id in excluded_route_ids)
            or len(set(excluded_route_ids)) != len(excluded_route_ids)
            or any(route_id not in by_route or by_route[route_id]["kind"] != "sale_dec"
                   for route_id in excluded_route_ids)):
        raise routing.RoutingError("Only sale members in this bundle can be excluded")
    excluded = set(excluded_route_ids)
    selected = []
    for op in bundle["operations"]:
        if record["operations"][op["id"]]["state"] != "planned":
            continue
        included = [member for member in op["members"] if member["route_id"] not in excluded]
        if included:
            selected.append({**op, "members": included})
    if not selected:
        if not excluded:
            raise routing.RoutingError("Select at least one route before approval")
        # Every share left in this bundle was unticked: nothing goes to a wallet, the bundle is skipped.
        for member in bundle_members:
            member_state = record["operations"][record["step_to_operation"][member["step_id"]]]["members"][member["nonce"]]
            if member_state["state"] == "planned":
                member_state["state"] = "skipped"
                record["reservations"][member["nonce"]]["state"] = "released"
        for op in bundle["operations"]:
            if record["operations"][op["id"]]["state"] == "planned":
                record["operations"][op["id"]]["state"] = "skipped"
        status["state"] = "skipped"
        _refresh_state(record)
        write(folder, record)
        return {"skipped": True, "bundle_id": bundle_id, "member_nonces": [], "operations": []}
    pre_claim = {"operations": {op["id"]: copy.deepcopy(record["operations"][op["id"]]) for op in bundle["operations"]},
                 "reservations": {member["nonce"]: copy.deepcopy(record["reservations"][member["nonce"]])
                                  for member in bundle_members}}
    for op in selected:
        for parent in op["depends_on"]:
            parent_state = _parent_state(record, parent)
            if parent_state != "confirmed":
                raise routing.RoutingError("Wait for the parent game receipt before this approval")
    lp_selected = [op for op in selected if op["kind"] == "liquidity" and
                   {member["kind"] for member in op["members"]}.issubset({"lp_wallet", "lp_sell_half"})]
    pools, balance = _lp_context(folder, record) if lp_selected else ({}, routing.ZERO)
    held = sum((routing.amount(item.get("dec_amount", "0")) for item in record["reservations"].values()
                if item.get("state") == "held" and item.get("kind") in {"lp_wallet", "lp_sell_half"}), routing.ZERO)
    floor = routing.amount(record.get("protected_wallet_dec", "0"))
    wallet = {"balance": balance, "held": held, "floor": floor}
    operations, payloads, deposits = [], [], {}
    for op in selected:
        if op in lp_selected:
            if op["members"][0]["kind"] == "lp_sell_half":
                operation, payload, deposits[op["id"]] = _claim_sell_half_operation(record, op, pools, wallet)
            else:
                operation, payload, deposits[op["id"]] = _claim_lp_operation(record, op, pools, wallet)
        else:
            operation, payload = _claim_operation(record, op)
        operations.append(operation)
        payloads.append(payload)
    if len(json.dumps(operations, separators=(",", ":")).encode("utf-8")) > DEFAULT_MAX_BYTES_PER_TX:
        raise routing.RoutingError("Routing bundle exceeds the signed transaction byte limit")
    handoff = {"run_id": record["run_id"], "revision": record["revision"],
               "bundle_id": bundle_id, "manifest_digest": record["manifest"]["digest"],
               "authority": "Posting", "approval": bundle["approval"],
               "operations": operations, "operation_ids": [op["id"] for op in selected],
               "operation_nonces": [payload["n"] for payload in payloads],
               "member_nonces": [member["nonce"] for op in selected for member in op["members"]]}
    handoff["changed_amount_review"] = [
        {"operation_id": op["id"], "route_id": op["members"][0]["route_id"],
         "resource_amount": deposits[op["id"]]["resource_amount"],
         "dec_amount": deposits[op["id"]]["dec_amount"],
         "confirmed_sale_dec": record["operations"][record["step_to_operation"][op["depends_on"][0]]]["members"][record["step_to_member"][op["depends_on"][0]]]["received_dec"]}
        for op in selected if op["id"] in deposits and op["members"][0]["kind"] == "lp_sell_half"]
    for member in bundle_members:
        if member["route_id"] in excluded:
            member_state = record["operations"][record["step_to_operation"][member["step_id"]]]["members"][member["nonce"]]
            member_state["state"] = "skipped"
            record["reservations"][member["nonce"]]["state"] = "released"
    for op in bundle["operations"]:
        if op["id"] not in handoff["operation_ids"] and record["operations"][op["id"]]["state"] == "planned":
            record["operations"][op["id"]]["state"] = "skipped"
    status.update(state="claimed", claim=handoff, payloads=payloads,
                  operation_ids=handoff["operation_ids"], claimed_at=time.time(), pre_claim=pre_claim)
    for op in selected:
        operation_state = record["operations"][op["id"]]
        operation_state["claimed_member_nonces"] = [member["nonce"] for member in op["members"]]
        if op["id"] in deposits:
            operation_state["deposit"] = deposits[op["id"]]
            member = op["members"][0]
            record["reservations"][member["nonce"]].update(kind=member["kind"], dec_amount=deposits[op["id"]]["dec_amount"])
        operation_state["state"] = "outcome_unknown"
        for member in op["members"]:
            operation_state["members"][member["nonce"]]["state"] = "outcome_unknown"
    write(folder, record)
    return handoff


def attach(folder: Path, record: dict, bundle_id: str, tx_id: str) -> dict:
    """Bind one transaction id to every member, never to a second id; zero calls."""
    if not isinstance(tx_id, str) or not _TX_ID.fullmatch(tx_id):
        raise routing.RoutingError("Invalid transaction ID")
    tx_id = tx_id.lower()
    _bundle(record, bundle_id)
    status = record["bundles"][bundle_id]
    if status["state"] not in {"claimed", "attached", "checking", "confirmed", "refused", "outcome_unknown"}:
        raise routing.RoutingError("This bundle has not been claimed")
    if status.get("tx_id") and status["tx_id"] != tx_id:
        raise routing.RoutingError("A different transaction is already attached")
    nonces = set(status["claim"]["member_nonces"])
    for other_id, other in record["bundles"].items():
        if other_id == bundle_id:
            continue
        if other.get("tx_id") == tx_id:
            raise routing.RoutingError("This transaction is already bound to another bundle")
        other_nonces = set((other.get("claim") or {}).get("member_nonces", []))
        if nonces & other_nonces and other.get("tx_id") not in {None, tx_id}:
            raise routing.RoutingError("A member nonce is bound to another transaction")
    status["tx_id"] = tx_id
    if status["state"] == "claimed":
        status["state"] = "attached"
    status.setdefault("attached_at", time.time())
    write(folder, record)
    return record


def _history_transaction(rows: list, expected: list) -> str | None:
    """The id of the newest transaction in recent account history whose custom_json ops are exactly the claimed ones; zero calls."""
    def canonical(item: dict) -> str:
        payload = item.get("json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return ""
        return json.dumps({"id": item.get("id"), "json": payload,
                           "required_auths": item.get("required_auths"),
                           "required_posting_auths": item.get("required_posting_auths")}, sort_keys=True)
    wanted = [canonical(op[1]) for op in expected]
    grouped: dict[str, list[str]] = {}
    for row in rows:
        entry = row[1] if isinstance(row, list) and len(row) == 2 and isinstance(row[1], dict) else {}
        op, tx_id = entry.get("op"), entry.get("trx_id")
        if isinstance(op, list) and len(op) == 2 and op[0] == "custom_json" and isinstance(op[1], dict) \
                and isinstance(tx_id, str) and _TX_ID.fullmatch(tx_id):
            grouped.setdefault(tx_id.lower(), []).append(canonical(op[1]))
    found = [tx_id for tx_id, ops in grouped.items() if ops == wanted]
    return found[-1] if found else None


def find_signed(folder: Path, record: dict, bundle_id: str, *, history_get: Callable[[str], list] | None = None) -> dict:
    """: a wallet signed but its answer never reached the page. On the maintainer's button, ONE account-history read finds the
    transaction by its exact claimed operations and attaches it, so the normal checks can carry on. Never automatic ()."""
    _bundle(record, bundle_id)
    status = record["bundles"][bundle_id]
    if status.get("tx_id"):
        return record
    if status["state"] != "claimed":
        raise routing.RoutingError("Nothing is waiting for a wallet answer")
    _charge(folder, record, "chain_probe", "condenser_api.get_account_history", record["player"], manual=True)
    if history_get is None:
        from .harvest_routing_bridge import _history
        history_get = lambda account: _history(account, -1)
    try:
        rows = history_get(record["player"])
    except routing.RoutingError:
        raise
    except Exception as exc:
        _log.warning("find_signed_failed run=%s bundle=%s error=%s", record.get("run_id"), bundle_id, type(exc).__name__)
        raise routing.RoutingError("Your wallet history could not be read. Try again in a moment") from exc
    tx_id = _history_transaction(rows, status["claim"]["operations"])
    if tx_id is None:
        write(folder, record)
        raise routing.RoutingError("Your wallet history does not show this transaction yet. If you approved it, wait a few seconds and press the button again; if you did not, stop here and start again")
    return attach(folder, record, bundle_id, tx_id)


def release(folder: Path, record: dict, bundle_id: str) -> dict:
    """Return a claimed bundle to planned when the wallet produced no signature; zero calls.

    Only a claim with no attached transaction qualifies, and only while the run is ready.
    The browser calls this after the wallet refused before signing (``broadcasted === false``)
    and after its own queue shows no signed copy of any member nonce.
    """
    _bundle(record, bundle_id)
    status = record["bundles"][bundle_id]
    if status["state"] == "planned":
        return record
    snapshot = status.get("pre_claim")
    if (status["state"] != "claimed" or status.get("tx_id") or record["state"] != "ready"
            or not isinstance(snapshot, dict)):
        raise routing.RoutingError("This bundle already has a wallet outcome")
    record["operations"].update(copy.deepcopy(snapshot["operations"]))
    record["reservations"].update(copy.deepcopy(snapshot["reservations"]))
    record["bundles"][bundle_id] = {"state": "planned", "revision": status.get("revision", record["revision"])}
    write(folder, record)
    return record


def head(folder: Path, record: dict, bundle_id: str) -> dict:
    """Read one counted Hive head after a Posting bundle is claimed."""
    _bundle(record, bundle_id)
    if record["bundles"][bundle_id]["state"] != "claimed":
        raise routing.RoutingError("Claim the bundle before preparing its signature")
    _charge(folder, record, "hive_head", "condenser_api.get_dynamic_global_properties", record["player"])
    try:
        props = chain_verify._http_rpc_call(chain_verify.HIVE_NODE_DEFAULT,
                                            "condenser_api.get_dynamic_global_properties", [])
    except Exception as exc:
        raise routing.RoutingError("Hive head unavailable; outcome remains unknown") from exc
    if (not isinstance(props, dict) or type(props.get("head_block_number")) is not int
            or not isinstance(props.get("head_block_id"), str)
            or not isinstance(props.get("time"), str)):
        raise routing.RoutingError("Hive head response is incomplete; outcome remains unknown")
    return {"head_block_number": props["head_block_number"],
            "head_block_id": props["head_block_id"], "time": props["time"]}


def _charge(folder: Path, record: dict, action: str, endpoint: str, account: str,
            *, manual: bool = False, probe_status: dict | None = None,
            poll_status: dict | None = None) -> None:
    """Persist one physical attempt before dispatch; calls no endpoint itself."""
    budget = record["call_budget"]
    actions = budget["actions"]
    charged_action = "manual_retry" if manual else action
    if budget["stopped"] or budget["total"] >= budget["ceiling"]:
        budget["stopped"] = True
        record["state"] = "checking_stopped"
        for status in record["bundles"].values():
            if status["state"] in {"claimed", "attached", "checking", "outcome_unknown"}:
                status["state"] = "outcome_unknown"
        write(folder, record)
        raise routing.RoutingError("Run call budget reached; unresolved outcomes remain unknown")
    ceilings = (record.get("manifest", {}).get("call_budget") or {}).get("action_ceilings") or _CALLS
    if actions.get(charged_action, 0) >= ceilings.get(charged_action, _CALLS[charged_action]):
        raise routing.RoutingError("Action call budget reached; unresolved outcome remains unknown")
    budget["total"] += 1
    actions[charged_action] = actions.get(charged_action, 0) + 1
    if probe_status is not None:
        probe_status["chain_probes"] = probe_status.get("chain_probes", 0) + 1
    if poll_status is not None and not manual:
        poll_status["polls"] += 1
    key = f"{endpoint} | {account}"
    budget["endpoints"][key] = budget["endpoints"].get(key, 0) + 1
    budget["log"].append({"endpoint": endpoint, "account": account, "action": charged_action,
                          "request_action": action,
                          "run": record["run_id"], "attempt": actions[charged_action],
                          "count": budget["total"], "manual": manual, "at": time.time()})
    write(folder, record)


def _chain_match(observed: dict, expected: list) -> bool:
    """Compare exact claimed Posting ops with included chain ops; zero calls."""
    actual = chain_verify._extract_custom_json_ops(observed)
    if len(actual) != len(expected):
        return False
    def canonical(item: dict) -> str:
        payload = item.get("json")
        if isinstance(payload, str):
            payload = json.loads(payload)
        return json.dumps({"id": item.get("id"), "json": payload,
                           "required_auths": item.get("required_auths"),
                           "required_posting_auths": item.get("required_posting_auths")}, sort_keys=True)
    return list(map(canonical, actual)) == [canonical(op[1]) for op in expected]


def _game_state(response: Any, player: str, lookup_id: str, payload: dict) -> tuple[str, dict]:
    """Attribute one game verdict to exact account and payload; zero calls."""
    info = response.get("trx_info") if isinstance(response, dict) else None
    if not isinstance(info, dict):
        return "outcome_unknown", {}
    try:
        data = json.loads(info["data"]) if isinstance(info.get("data"), str) else info.get("data")
        result = json.loads(info["result"]) if isinstance(info.get("result"), str) else info.get("result")
    except (ValueError, TypeError):
        return "outcome_unknown", {}
    if (info.get("id") != lookup_id or info.get("player") != player
            or info.get("type") != "land_operation" or data != payload):
        return "outcome_unknown", {}
    if info.get("success") is False:
        return "refused", {"error": str(info.get("error") or "Game refused this operation")}
    if info.get("success") is not True or not isinstance(result, dict):
        return "outcome_unknown", {}
    inner = result.get("result")
    if result.get("success") is False or (isinstance(inner, dict) and inner.get("success") is False):
        return "refused", {"error": str(result.get("error") or (inner or {}).get("error") or "Game refused this operation")}
    if result.get("success") is not True or not isinstance(inner, dict) or inner.get("success") is not True:
        return "outcome_unknown", {}
    effects = inner.get("data")
    if not isinstance(effects, dict):
        return "outcome_unknown", {}
    if any(effects.get(key) for key in ("external_withdrawal_required", "external_swap_required", "pending_deposit_id")):
        return "outcome_unknown", {"bridge_pending": True, "effects": effects}
    return "confirmed", {"effects": effects, "confirmed_at": info.get("created_date")}


def _attribute_members(op: dict, operation_state: dict, state: str, detail: dict) -> str:
    """Apply one game verdict to every saved route share; zero outbound calls."""
    active_nonces = set(operation_state.get("claimed_member_nonces",
                         [item["nonce"] for item in op["members"]]))
    members = [item for item in op["members"] if item["nonce"] in active_nonces]
    attributed = False
    if state == "confirmed" and op.get("destination_symbol") == "DEC":
        try:
            amount = routing.amount(detail["effects"]["dec_amount"], positive=True)
            total = sum((routing.amount(item["source_amount"], positive=True) for item in members), routing.ZERO)
        except (KeyError, TypeError, routing.RoutingError):
            state = "outcome_unknown"
        else:
            attributed = True
            allocated = routing.ZERO
            for index, item in enumerate(members):
                share = (amount - allocated if index == len(members) - 1 else
                         routing.floor(amount * routing.amount(item["source_amount"]) / total))
                allocated += share
                member_state = operation_state["members"][item["nonce"]]
                member_state["received_dec"] = routing.text(share)
                member_state["state"] = ("confirmed" if share >= routing.amount(item["protected_minimum"])
                                         else "outcome_unknown")
            if any(operation_state["members"][nonce]["state"] != "confirmed" for nonce in active_nonces):
                state = "outcome_unknown"
    if state == "confirmed" and {member["kind"] for member in members}.issubset({"lp_wallet", "lp_sell_half"}) and op["kind"] == "liquidity":
        try:
            effects = detail["effects"]
            actual_resource = routing.amount(effects["resource_amount"], positive=True)
            actual_dec = routing.amount(effects["dec_amount"], positive=True)
            deposit = operation_state["deposit"]
            member = members[0]
            if (effects.get("resource") != op["source_symbol"] or
                    actual_resource < routing.amount(member["protected_minimum"]) or
                    actual_resource > routing.amount(deposit["resource_amount"]) or
                    actual_dec > routing.amount(deposit["dec_amount"])):
                state = "outcome_unknown"
            else:
                operation_state["members"][member["nonce"]].update(
                    deposited_resource=routing.text(actual_resource), deposited_dec=routing.text(actual_dec))
                attributed = True
        except (KeyError, TypeError, routing.RoutingError):
            state = "outcome_unknown"
    if not attributed:
        for nonce in active_nonces:
            operation_state["members"][nonce]["state"] = state
    return state


def _mark_expired_absent(record: dict, bundle_id: str) -> None:
    """A transaction the node definitively lacks past Hive's expiry can never be included: nothing was
    sent, so the bundle stops and its reservations are released (plan: definitive absence under expiry)."""
    record["bundles"][bundle_id]["state"] = "stopped"
    for op in _bundle(record, bundle_id)["operations"]:
        operation = record["operations"][op["id"]]
        if operation["state"] in _TERMINAL:
            continue
        operation["state"] = "stopped"
        for nonce, member in operation["members"].items():
            if member["state"] not in _TERMINAL:
                member["state"] = "stopped"
                record["reservations"][nonce]["state"] = "released"


def _refresh_state(record: dict) -> None:
    """Resolve bundle/run states after one member verdict; zero outbound calls."""
    for bundle in record["manifest"]["bundles"]:
        status = record["bundles"][bundle["id"]]
        if status["state"] != "planned":
            continue
        for op in bundle["operations"]:
            operation = record["operations"][op["id"]]
            if operation["state"] != "planned":
                continue
            if any(_parent_state(record, parent) in {"refused", "skipped", "stopped"}
                   for parent in op["depends_on"]):
                operation["state"] = "skipped"
                for nonce, member in operation["members"].items():
                    member["state"] = "skipped"
                    record["reservations"][nonce]["state"] = "released"
        if all(record["operations"][op["id"]]["state"] == "skipped" for op in bundle["operations"]):
            status["state"] = "skipped"
    states = {item["state"] for item in record["bundles"].values()}
    if states <= {"confirmed", "refused", "skipped", "stopped"}:
        record["state"] = "complete" if states == {"confirmed"} else "stopped"


def reconcile(folder: Path, record: dict, bundle_id: str, mode: str,
              operation_index: int | None = None, *, manual: bool = False,
              game_get: Callable[[str], Any] | None = None,
              chain_get: Callable[[str], tuple[dict | None, str | None]] | None = None) -> dict:
    """Issue at most one metered chain or game call per request, with zero retries."""
    _bundle(record, bundle_id)
    status = record["bundles"][bundle_id]
    if status["state"] in _TERMINAL:
        return record
    if status["state"] not in {"attached", "checking", "outcome_unknown"} or not status.get("tx_id"):
        raise routing.RoutingError("Attach the wallet transaction ID before checking")
    if mode == "chain":
        if status.get("chain") == "included":
            return record
        if status.get("chain_probes", 0) >= 2 and not manual:
            raise routing.RoutingError("Chain probe allowance reached; outcome remains unknown")
        _charge(folder, record, "chain_probe", "condenser_api.get_transaction", record["player"],
                manual=manual, probe_status=status)
        try:
            tx, reason = (chain_get or chain_verify.get_transaction)(status["tx_id"])
            if tx is not None and _chain_match(tx, status["claim"]["operations"]):
                status["chain"] = "included"
            elif tx is not None:
                status["chain"] = "mismatch"
            else:
                status["chain"] = "outcome_unknown"
                status["chain_reason"] = reason
                if reason == "not_found" and time.time() - status.get("attached_at", time.time()) > TX_EXPIRY_SECONDS:
                    status["chain"] = "absent"
        except Exception as exc:
            _log.warning("chain_probe_failed run=%s bundle=%s error=%s", record.get("run_id"), bundle_id, type(exc).__name__)
            status["chain"] = "outcome_unknown"
        if status["chain"] == "absent":
            _mark_expired_absent(record, bundle_id)
        else:
            status["state"] = "checking" if status["chain"] == "included" else "outcome_unknown"
    elif mode == "game":
        operation_ids = status.get("operation_ids", [])
        if type(operation_index) is not int or not 0 <= operation_index < len(operation_ids):
            raise routing.RoutingError("Invalid operation index")
        op = next(item for item in _bundle(record, bundle_id)["operations"]
                  if item["id"] == operation_ids[operation_index])
        member = record["operations"][op["id"]]
        if member["state"] in _TERMINAL:
            return record
        if member["polls"] >= 5 and not manual:
            raise routing.RoutingError("Scheduled verdict checks ended; use Check again")
        if status.get("chain") != "included":
            raise routing.RoutingError("Chain inclusion is not confirmed")
        lookup_id = status["tx_id"] if operation_index == 0 else f"{status['tx_id']}-{operation_index}"
        _charge(folder, record, "game_verdict", "/transactions/lookup", record["player"],
                manual=manual, poll_status=member)
        try:
            response = (game_get or (lambda url: FixedGetTransport(timeout=15, retries=1).get_json(url).data))(
                API_ORIGIN + "/transactions/lookup?" + urlencode({"trx_id": lookup_id}))
            state, detail = _game_state(response, record["player"], lookup_id, status["payloads"][operation_index])
        except Exception as exc:
            _log.warning("game_lookup_failed run=%s lookup=%s error=%s", record.get("run_id"), lookup_id, type(exc).__name__)
            state, detail = "outcome_unknown", {}
        state = _attribute_members(op, member, state, detail)
        member.update(state=state, **detail)
        for nonce, member_state in member["members"].items():
            if member_state["state"] == "confirmed":
                record["reservations"][nonce]["state"] = "consumed"
            elif member_state["state"] in {"refused", "skipped"}:
                record["reservations"][nonce]["state"] = "released"
        states = {record["operations"][item_id]["state"] for item_id in operation_ids}
        status["state"] = ("confirmed" if states == {"confirmed"} else "refused" if states <= _TERMINAL
                           else "checking" if "outcome_unknown" not in states else "outcome_unknown")
    else:
        raise routing.RoutingError("Unknown reconciliation mode")
    _refresh_state(record)
    if record["call_budget"]["total"] >= record["call_budget"]["ceiling"]:
        record["call_budget"]["stopped"] = True
        if record["state"] == "ready":
            record["state"] = "checking_stopped"
        for unresolved in record["bundles"].values():
            if unresolved["state"] in {"claimed", "attached", "checking"}:
                unresolved["state"] = "outcome_unknown"
    write(folder, record)
    return record


def stop(folder: Path, record: dict) -> dict:
    """Stop unsent work and retain every uncertain reservation; zero calls."""
    if record["state"] in {"complete", "stopped"}:
        return record
    record["state"] = "stopped"
    for status in record["bundles"].values():
        if status["state"] == "planned":
            status["state"] = "stopped"
        elif status["state"] not in _TERMINAL:
            status["state"] = "outcome_unknown"
    for operation in record["operations"].values():
        if operation["state"] == "planned":
            operation["state"] = "stopped"
            for nonce, member in operation["members"].items():
                member["state"] = "stopped"
                record["reservations"][nonce]["state"] = "released"
    write(folder, record)
    return record
