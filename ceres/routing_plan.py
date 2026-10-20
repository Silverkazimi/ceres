"""Pure, deterministic routing preview compiler. No network or wallet calls.

`compile_manifest` calls the helpers below once per preview. Every helper makes
zero physical calls; the returned budget is a contract for the later run service.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping

from fides import op as fides_op

from . import harvest_routing as routing


KINDS = frozenset({"sale_dec", "lp_wallet", "lp_sell_half", "sps_out", "res_res",
                   "transport", "cover", "voucher", "sps_in"})
ACTION_CEILINGS = {"preview_context": 9, "harvest_receipt": 3, "segment_context": 8,
                   "hive_head": 4, "chain_probe": 16, "game_verdict": 65,
                   "bridge_history": 8, "pending_id": 1, "manual_retry": 6}
# nothing about the
# size of a plan is refused any more. The call budget is a function of the plan: these are the floors every plan keeps
# (identical to the old fixed budget for a plan of up to 8 bundles / 13 operations / 4 LP bundles), and call_budget()
# grows the per-bundle and per-operation allowances with the plan.
PLANNING_LIMITS: dict[str, int] = {}


def _is_lp_bundle(bundle: Mapping[str, Any]) -> bool:
    return any(member["kind"] in {"lp_wallet", "lp_sell_half"} and op["kind"] == "liquidity"
               for op in bundle["operations"] for member in op["members"])


def call_budget(bundles: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The run's call ceilings for one compiled plan; pure. Per-unit allowances are the old ones (2 chain probes per
    bundle, 5 game lookups per operation, 2 LP context reads per LP bundle, 1 Hive head per bundle)."""
    operations = sum(len(bundle["operations"]) for bundle in bundles)
    actions = dict(ACTION_CEILINGS)
    actions["segment_context"] = max(ACTION_CEILINGS["segment_context"], 2 * sum(_is_lp_bundle(b) for b in bundles))
    actions["hive_head"] = max(ACTION_CEILINGS["hive_head"], len(bundles))
    actions["chain_probe"] = max(ACTION_CEILINGS["chain_probe"], 2 * len(bundles))
    actions["game_verdict"] = max(ACTION_CEILINGS["game_verdict"], 5 * operations)
    actions["manual_retry"] = max(ACTION_CEILINGS["manual_retry"], len(bundles) // 2)
    return {"run_ceiling": sum(actions.values()), "action_ceilings": actions}


def rule_kind(rule: Mapping[str, Any]) -> str:
    """Classify one legacy rule; calls no services and is used once per rule."""
    kind = rule.get("kind")
    if kind == "liquidity":
        return "lp_wallet" if rule.get("dec_source") == "wallet" else "lp_sell_half"
    if kind == "transfer":
        return "transport"
    if kind == "sell":
        source, destination = rule.get("symbol"), rule.get("destination_symbol")
        if source == "SPS":
            return "sps_in"
        if destination == "DEC":
            return "sale_dec"
        if destination == "SPS" and source != "SPS":
            return "sps_out"
        if destination == "VOUCHER":
            return "voucher"
        if isinstance(destination, str) and destination not in {"DEC", "SPS", "VOUCHER"}:
            return "res_res"
    raise routing.RoutingError("Unsupported routing kind")


def _member(step: Mapping[str, Any], rule: Mapping[str, Any], route_kind: str) -> dict[str, Any]:
    """Describe one route's share; calls no services once per planned step."""
    route_id = rule["id"]
    source_amount = step.get("quantity") or rule.get("quantity")
    if route_kind == "sps_out" and step.get("depends_on"):
        source_amount = rule.get("quantity")
    quote = step.get("quote") or {}
    dependent = bool(step.get("depends_on"))
    if step["kind"] in {"sell", "liquidity_funding_swap"}:
        minimum = routing.amount(quote.get("minimum"), positive=True)
    elif step["kind"] == "transfer":
        minimum = routing.amount(source_amount)
    elif route_kind in {"lp_wallet", "lp_sell_half"} and step["kind"] == "liquidity":
        minimum = routing.floor(routing.amount(source_amount, positive=True) *
                                (routing.ONE - routing.validate_slippage(rule.get("slippage", "2.5")) / 100))
    else:
        minimum = routing.ZERO
    member = {"route_id": route_id, "kind": route_kind, "source_amount": str(source_amount),
            "allocation": None if dependent else str(quote.get("output", "0")),
            "protected_minimum": routing.text(minimum),
            "required_dec": (step.get("dec_funding") or {}).get("required", step.get("required_dec")),
            "input_pending_receipt": dependent,
            "minimum_pending_receipt": dependent and bool(quote),
            "step_id": step["id"]}
    if rule.get("quantity_mode") == "percent":
        member["source_percent"] = str(rule.get("quantity"))
    if route_kind == "lp_wallet":
        deposit = step["deposit"]
        member.update(allocation=deposit["estimated_shares"],
                      required_wallet_dec=routing.text(routing.amount(deposit["dec_amount"], positive=True)))
    return member


def _operation(step: Mapping[str, Any], rule: Mapping[str, Any], route_kind: str,
               pools: Mapping[str, routing.Pool]) -> dict[str, Any]:
    """Map one legacy step to a pool intent; calls no services once per step."""
    symbol = step.get("symbol")
    target = step.get("destination_symbol")
    if step["kind"] == "liquidity":
        pool = symbol
    elif step["kind"] == "transfer":
        pool = symbol
    elif symbol == "DEC":
        pool = target
    else:
        pool = symbol
    if pool not in pools:
        raise routing.RoutingError("A routing operation has no supported pool")
    if route_kind in {"lp_wallet", "lp_sell_half"} and pools[pool].external:
        raise routing.RoutingError("V2 liquidity requires an internal pool")
    slippage = rule.get("slippage", step.get("slippage", "2.5"))
    member = _member(step, rule, route_kind)
    if route_kind == "lp_wallet":
        if routing.amount(member["required_wallet_dec"]) <= routing.ZERO:
            raise routing.RoutingError("The LP route needs a positive DEC amount")
    return {"id": step["id"], "kind": step["kind"], "pool": pool,
            "pool_id": pools[pool].id, "authority": "posting",
            "source_region": step.get("source_region"), "source_symbol": symbol,
            "destination_symbol": target, "slippage": str(slippage),
            "depends_on": [step["depends_on"]] if step.get("depends_on") else [],
            "members": [member]}


def _merge_sales(operations: list[dict[str, Any]], pools: Mapping[str, routing.Pool]) -> list[dict[str, Any]]:
    """Merge compatible same-region resource-to-DEC sales; no physical calls."""
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    remaining = []
    for operation in operations:
        if (operation["kind"] in {"sell", "liquidity_funding_swap"}
                and operation["destination_symbol"] == "DEC"
                and not operation["depends_on"]):
            key = (operation["source_region"], operation["source_symbol"],
                   operation["destination_symbol"], operation["slippage"])
            groups[key].append(operation)
        else:
            remaining.append(operation)
    for key, group in sorted(groups.items()):
        if len(group) == 1:
            remaining.extend(group)
            continue
        members = sorted((member for item in group for member in item["members"]),
                         key=lambda member: member["route_id"])
        quantity = sum((routing.amount(member["source_amount"]) for member in members), routing.ZERO)
        quote = routing.swap_quote(key[1], "DEC", quantity, pools, key[3])
        allocated_output = allocated_minimum = routing.ZERO
        attributed = []
        for index, member in enumerate(members):
            share = routing.amount(member["source_amount"]) / quantity
            output = (quote.output - allocated_output if index == len(members) - 1
                      else routing.floor(quote.output * share))
            minimum = (quote.minimum - allocated_minimum if index == len(members) - 1
                       else routing.floor(quote.minimum * share))
            required_dec = member.get("required_dec")
            if minimum <= 0 or (required_dec is not None and minimum < routing.amount(required_dec)):
                break
            attributed.append({**member, "allocation": routing.text(output),
                               "protected_minimum": routing.text(minimum)})
            allocated_output += output
            allocated_minimum += minimum
        if len(attributed) != len(members):
            remaining.extend(group)
            continue
        combined = dict(group[0])
        combined["id"] = "+".join(sorted(item["id"] for item in group))
        combined["members"] = attributed
        combined["quoted_output"] = routing.text(quote.output)
        combined["protected_minimum"] = routing.text(quote.minimum)
        remaining.append(combined)
    return remaining


def _merge_sps(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group compatible pending DEC-to-SPS legs without sizing their input; no calls."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    remaining = []
    for operation in operations:
        if operation["source_symbol"] == "DEC" and operation["destination_symbol"] == "SPS":
            groups[(operation["slippage"], operation["authority"])].append(operation)
        else:
            remaining.append(operation)
    for group in groups.values():
        if len(group) == 1:
            remaining.extend(group)
            continue
        combined = dict(group[0])
        combined["id"] = "+".join(sorted(item["id"] for item in group))
        combined["depends_on"] = sorted({parent for item in group for parent in item["depends_on"]})
        combined["members"] = sorted((member for item in group for member in item["members"]),
                                      key=lambda member: member["route_id"])
        remaining.append(combined)
    return remaining


def _pack(operations: list[dict[str, Any]], cap: int) -> list[list[dict[str, Any]]]:
    """Find a deterministic minimum pool-distinct packing; no physical calls."""
    pool_counts = Counter(op["pool"] for op in operations)  # counted once, not once per operation
    ordered = sorted(operations, key=lambda op: (-pool_counts[op["pool"]], op["pool"], op["id"]))
    if not ordered:
        return []
    minimum = max((len(ordered) + cap - 1) // cap, max(Counter(op["pool"] for op in ordered).values()))
    if len(ordered) > 400:
        # a big plan is packed by one first-fit pass (open a new bin only when no bin fits); first-fit with a fixed bin count
        # succeeds exactly when this pass opens no more bins than that count, so this is the first count the loop below would accept.
        first_fit: list[list[dict[str, Any]]] = []
        for operation in ordered:
            for bucket in first_fit:
                if len(bucket) < cap and all(item["pool"] != operation["pool"] for item in bucket):
                    bucket.append(operation)
                    break
            else:
                first_fit.append([operation])
        return [sorted(bucket, key=lambda item: item["id"]) for bucket in first_fit]
    for count in range(minimum, len(ordered) + 1):
        bins: list[list[dict[str, Any]]] = [[] for _ in range(count)]

        # the first branch of the search below is plain first-fit. Try it WITHOUT recursion first: when it fits, it is exactly the
        # answer the search would have returned (so the browser port stays identical), and a plan of any size packs without a recursion
        # limit. Only a plan that first-fit cannot place is searched, and only while the search depth is safe.
        for operation in ordered:
            for bucket in bins:
                if len(bucket) < cap and all(item["pool"] != operation["pool"] for item in bucket):
                    bucket.append(operation)
                    break
            else:
                break
        else:
            return [sorted(bucket, key=lambda item: item["id"]) for bucket in bins if bucket]
        bins = [[] for _ in range(count)]

        nodes = [0]

        def place(index: int) -> bool:
            """Try one finite placement branch; calls itself at most once per branch."""
            nodes[0] += 1
            if nodes[0] > 200_000:  # a huge plan never searches forever; the next bin count is tried instead
                return False
            if index == len(ordered):
                return True
            seen = set()
            for bucket in bins:
                signature = tuple(item["pool"] for item in bucket)
                if signature in seen:
                    continue
                seen.add(signature)
                if len(bucket) == cap or any(item["pool"] == ordered[index]["pool"] for item in bucket):
                    continue
                bucket.append(ordered[index])
                if place(index + 1):
                    return True
                bucket.pop()
            return False

        if place(0):
            return [sorted(bucket, key=lambda item: item["id"]) for bucket in bins if bucket]
    raise routing.RoutingError("Routing operations cannot be packed")


def compile_manifest(*, rules: list[dict[str, Any]], steps: list[dict[str, Any]],
                     pools: Mapping[str, routing.Pool], account: str, regions: int,
                     enabled_kinds: set[str] | frozenset[str], cap: int | None = None) -> dict[str, Any]:
    """Compile one immutable preview snapshot; calls pure helpers, never services."""
    if cap is None:
        cap = fides_op.DEFAULT_MAX_OPS_PER_TX
    if cap != fides_op.DEFAULT_MAX_OPS_PER_TX:
        raise routing.RoutingError(f"Routing bundles carry {fides_op.DEFAULT_MAX_OPS_PER_TX} operations")
    by_id = {rule["id"]: rule for rule in rules}
    kinds = {identifier: rule_kind(rule) for identifier, rule in by_id.items()}
    disabled = sorted(set(kinds.values()) - set(enabled_kinds))
    if disabled:
        raise routing.RoutingError("Routing kinds disabled: " + ", ".join(disabled))
    operations = []
    split_by_rule: dict[str, dict[str, Any]] = {}
    for step in steps:
        if step.get("status") != "ready":
            continue
        identifier = step.get("rule_id") or (step["id"] if step["id"] in by_id
                                              else step["id"].rsplit(":", 1)[0])
        if identifier not in by_id:
            raise routing.RoutingError("A routing step has no source rule")
        compiled_step = step
        if kinds[identifier] == "lp_sell_half" and step["kind"] in {"liquidity_funding_swap", "liquidity"}:
            if identifier not in split_by_rule:
                siblings = [item for item in steps if item.get("rule_id") == identifier
                            and item.get("kind") in {"liquidity_funding_swap", "liquidity"}]
                total = sum((routing.amount(item["quantity"]) for item in siblings), routing.ZERO)
                split_by_rule[identifier] = routing.liquidity_funding_split(
                    pools[by_id[identifier]["symbol"]], total, by_id[identifier].get("slippage", "2.5"))
            split = split_by_rule[identifier]
            compiled_step = dict(step)
            if step["kind"] == "liquidity_funding_swap":
                compiled_step.update(quantity=split["swap_resource"], quote=split["quote"],
                                     required_dec=split["required_dec"])
            else:
                compiled_step.update(quantity=split["deposit_resource"], deposit=split["deposit"])
                compiled_step["required_dec"] = split["required_dec"]
        operations.append(_operation(compiled_step, by_id[identifier], kinds[identifier], pools))
    operations = _merge_sps(_merge_sales(operations, pools))
    # every sale is signed before any LP deposit.
    # The first wave holds only sales (and funding swaps) with no dependency; every liquidity deposit -- wallet-DEC ones
    # included, which depend on nothing -- and every dependent operation goes to the second wave.
    first = [op for op in operations if not op["depends_on"] and op["kind"] != "liquidity"]
    later = [op for op in operations if op["depends_on"] or op["kind"] == "liquidity"]
    waves = [_pack(first, cap), _pack(later, cap)]
    bundles = []
    for wave, packed in enumerate(waves):
        for group in packed:
            bundles.append({"wave": wave, "authority": "posting", "operations": group})
    for index, bundle in enumerate(bundles, 1):
        bundle["approval"] = index
        identity = json.dumps({"account": account, "wave": bundle["wave"],
                               "approval": index, "operations": bundle["operations"]},
                              sort_keys=True, separators=(",", ":"))
        bundle["id"] = hashlib.sha256(identity.encode()).hexdigest()[:24]
        for operation in bundle["operations"]:
            for member in operation["members"]:
                member_identity = json.dumps({"bundle": bundle["id"], "operation": operation["id"],
                                              "member": member}, sort_keys=True, separators=(",", ":"))
                member["nonce"] = hashlib.sha256(member_identity.encode()).hexdigest()[:24]
    manifest = {"version": 2, "cap": cap, "bundles": bundles,
                "approval_counts": {"routing": len(bundles), "harvest": 1, "total": len(bundles) + 1,
                                    "conditional_after_wave": 0 if later else None},
                "call_budget": {**call_budget(bundles), "account": account, "planning_limits": dict(PLANNING_LIMITS)}}
    manifest["digest"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return manifest
