"""Durable routing plans and one-shot wallet handoffs, serialized per player."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import json
import logging
from pathlib import Path
import re
import secrets
import time
from typing import Any

from .harvest_evidence import _atomic_write
from .harvest_routing import RoutingError
from . import resources as _res
from .security import resolve_under, validate_player

_ID = re.compile(r"[a-f0-9]{32}")
TERMINAL = {"complete", "cancelled", "failed", "stopped"}
# A step whose transaction may already be on chain; such a run is never closed until it is reconciled.
IN_FLIGHT = {"awaiting_wallet", "pending", "submitted", "settling"}
_LOG = logging.getLogger(__name__)
_CHAINABLE_SALE_SYMBOLS = set(_res.symbols("regional"))  # registry view: GRAIN WOOD STONE IRON RESEARCH AURA
_IN_FLIGHT_STEP_STATES = {"submitted", "pending", "settling"}


def chainable_sale_pool(record: dict, step: dict) -> str | None:
    quote = step.get("quote", step)
    symbol = quote.get("source_symbol")
    eligible = (record.get("phase") == "after" and step.get("kind") == "sell"
                and quote.get("destination_symbol") == "DEC" and symbol in _CHAINABLE_SALE_SYMBOLS
                and not quote.get("external") and not step.get("two_step")
                and not step.get("depends_on_step_id"))
    return symbol if eligible else None


def review_members(record: dict, first: dict) -> list[dict]:
    if first.get("depends_on_step_id") or first.get("two_step"):
        return [first]
    from fides.op import DEFAULT_MAX_OPS_PER_TX
    members, symbols = [], set()
    started = False
    for step in record["steps"]:
        if step is first:
            started = True
        if not started:
            if step.get("state") in _IN_FLIGHT_STEP_STATES:
                symbol = chainable_sale_pool(record, step)
                if symbol is None:
                    return [first]
                symbols.add(symbol)
            continue
        if step["state"] in {"confirmed", "skipped"}:
            continue
        symbol = chainable_sale_pool(record, step)
        eligible = step["state"] in {"planned", "reviewed"} and symbol is not None and symbol not in symbols
        if not eligible:
            break
        members.append(step)
        symbols.add(symbol)
        if len(members) >= DEFAULT_MAX_OPS_PER_TX:
            break
    return members or [first]


def _approval_groups(record: dict) -> list[dict]:
    from fides.op import DEFAULT_MAX_OPS_PER_TX
    groups: list[dict] = []
    pending: list[dict] = []
    symbols: set[str] = set()
    pending_identity: str | None = None

    def flush() -> None:
        nonlocal pending, symbols, pending_identity
        if pending:
            groups.append({"kind": "routing", "member_ids": [step.get("step_id") for step in pending],
                           "steps": pending})
        pending, symbols, pending_identity = [], set(), None

    before_steps = record.get("phase_steps", {}).get("before", [])
    steps = [*before_steps, *record.get("steps", [])]
    for step in before_steps:
        if step.get("state") != "skipped":
            groups.append({"kind": "routing", "member_ids": [step.get("step_id")], "steps": [step]})
    active_group = set((record.get("group_review") or {}).get("member_ids", []))
    for step in record.get("steps", []):
        if step.get("state") == "skipped":
            continue
        symbol = chainable_sale_pool(record, step)
        if symbol is None:
            flush()
            groups.append({"kind": "routing", "member_ids": [step.get("step_id")], "steps": [step]})
            continue
        identity = (f"tx:{step['tx_id']}" if step.get("tx_id") else
                    "review" if step.get("step_id") in active_group else "future")
        if pending and identity != pending_identity:
            flush()
        if symbol in symbols or len(pending) >= DEFAULT_MAX_OPS_PER_TX:
            flush()
        pending_identity = identity
        pending.append(step)
        symbols.add(symbol)
    flush()

    if record.get("phase") != "after":
        projected: list[dict] = []
        projected_symbols: set[str] = set()

        def flush_projected() -> None:
            nonlocal projected, projected_symbols
            if projected:
                groups.append({"kind": "routing", "member_ids": [item["id"] for item in projected],
                               "steps": []})
            projected, projected_symbols = [], set()

        for rule in record.get("rules", []):
            identifier = f"rule:{rule.get('id')}"
            kind, symbol = rule.get("kind"), rule.get("symbol")
            destination = rule.get("destination_symbol")
            chainable = kind == "sell" and destination == "DEC" and symbol in _CHAINABLE_SALE_SYMBOLS
            if chainable:
                if symbol in projected_symbols or len(projected) >= DEFAULT_MAX_OPS_PER_TX:
                    flush_projected()
                projected.append({"id": identifier})
                projected_symbols.add(symbol)
                continue
            flush_projected()
            groups.append({"kind": "routing", "member_ids": [identifier], "steps": []})
            if kind == "sell" and destination == "SPS" and symbol != "DEC":
                groups.append({"kind": "routing", "member_ids": [f"{identifier}:sps"], "steps": []})
            if (kind == "sell" and destination == "SPS") or (kind == "liquidity" and symbol == "SPS"):
                groups.append({"kind": "funding", "member_ids": [identifier], "steps": []})
        flush_projected()

    for step in steps:
        if step.get("state") == "skipped":
            continue
        quote = step.get("quote", step)
        deposit = step.get("deposit") or {}
        needs_funding = (step.get("kind") == "sell" and quote.get("external")
                         and quote.get("destination_symbol") == "SPS") or (
                             step.get("kind") == "liquidity" and deposit.get("external")
                             and deposit.get("symbol") == "SPS")
        if needs_funding:
            groups.append({"kind": "funding", "member_ids": [step.get("step_id")], "steps": [step]})

    chunks = (record.get("harvest_preview") or {}).get("chunks")
    chunk_count = len(chunks) if isinstance(chunks, list) else int(record.get("projected_harvest_chunks", 0))
    skipped_chunks = set(record.get("harvest_skipped_chunks", []))
    for index in range(chunk_count):
        if index not in skipped_chunks:
            groups.append({"kind": "harvest", "member_ids": [f"harvest:{index}"], "steps": []})
    return groups


def signature_plan(record: dict) -> dict:
    groups = _approval_groups(record)
    calculated_total = len(groups)
    stored_total = record.get("signatures_total")
    total = min(stored_total, calculated_total) if isinstance(stored_total, int) else calculated_total
    signed = confirmed = 0
    public_groups = []
    attempts = {item.get("chunk_index"): item for item in record.get("harvest_attempt_records", [])}
    persisted_attempts = {item.get("chunk_index"): item for item in record.get("harvest_signature_progress", [])}
    for group in groups:
        kind, steps = group["kind"], group["steps"]
        if kind == "funding":
            funding = (steps[0].get("funding") or {}) if steps else {}
            state = funding.get("state")
            is_signed = bool(funding.get("tx_id"))
            is_confirmed = state == "funded"
        elif kind == "harvest":
            index = int(group["member_ids"][0].split(":", 1)[1])
            progress = persisted_attempts.get(index) or {}
            evidence = (attempts.get(index) or {}).get("evidence") or {}
            is_signed = bool(progress.get("signed") or evidence.get("tx_id"))
            is_confirmed = bool(progress.get("confirmed") or evidence.get("state") == "reconciled")
        else:
            is_signed = bool(steps) and all(step.get("tx_id") for step in steps)
            is_confirmed = bool(steps) and all(step.get("state") == "confirmed" for step in steps)
        signed += int(is_signed)
        confirmed += int(is_confirmed)
        public_groups.append({"kind": kind, "member_ids": group["member_ids"],
                              "signed": is_signed, "confirmed": is_confirmed})
    return {"total": total, "signed": min(signed, total), "confirmed": min(confirmed, total),
            "groups": public_groups[:total]}


def expose_signature_plan(record: dict) -> dict:
    plan = signature_plan(record)
    record["signatures_total"] = plan["total"]
    record["signed_signatures"] = plan["signed"]
    record["confirmed_signatures"] = plan["confirmed"]
    record["signature_groups"] = plan["groups"]
    return record


def directory(root: Path, player: str) -> Path:
    base = root / "harvest_routing_runs"
    base.mkdir(parents=True, exist_ok=True)
    folder = resolve_under(base, base / validate_player(player))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@contextmanager
def locked(root: Path, player: str):
    folder = directory(root, player)
    with (folder / ".lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield folder
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read(folder: Path, identifier: Any, player: str) -> dict:
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise RoutingError("Invalid routing run")
    path = folder / f"run-{identifier}.json"
    if not path.exists():
        raise RoutingError("Routing run not found")
    record = json.loads(path.read_text())
    if record.get("player") != player:
        raise RoutingError("Routing run does not belong to this player")
    migrate_legacy_dec_sources(record)
    return expose_signature_plan(record)


def migrate_legacy_dec_sources(record: dict) -> None:
    """Keep durable pre- plans executable without reviving the retired source."""
    def visit(value):
        if isinstance(value, dict):
            if value.get("dec_source") == "harvest":
                value["dec_source"] = "wallet"
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(record)


def cascade_dependents(record: dict) -> None:
    """: a dependent step (the DEC→SPS leg of a two-step sale) can never be prepared once the
    step funding it has skipped or failed — it has no DEC proceeds to swap. Called from write() so
    every path that ends a step in that state (skip-failed, a verify failure, cancel, stop) cascades."""
    by_id = {step.get("step_id"): step for step in record.get("steps", []) if step.get("step_id")}
    for step in record.get("steps", []):
        parent = by_id.get(step.get("depends_on_step_id"))
        if not parent or parent.get("state") not in {"skipped", "failed"}:
            continue
        if step.get("state") in IN_FLIGHT | {"confirmed", "skipped", "failed"}:
            continue
        step["skip_metadata"] = {"prior_state": step.get("state"), "reason": "dependency_unfunded", "skipped_at": time.time()}
        step["state"] = "skipped"
        step["error"] = "No DEC proceeds from the first step"


def write(folder: Path, record: dict) -> None:
    migrate_legacy_dec_sources(record)
    cascade_dependents(record)
    # only post-harvest routing is immediate-or-never; a before-harvest failure stays reviewable.
    if record.get("state") == "failed" and record.get("phase") == "after" and not record.get("routing_closed"):
        record["routing_closed"] = True
        record["failure"] = {"code": "CERES_ROUTING_FAILED", "at": time.time(),
                             "message": next((step.get("error") for step in record.get("steps", []) if step.get("error")), "Immediate routing failed. No later routing will be attempted.")}
        _LOG.error("CERES_ROUTING_FAILED player=%s run=%s reason=%s", record.get("player"), record.get("run_id"), record["failure"]["message"])
    elif record.get("state") == "failed" and record.get("phase") != "after" and not record.get("routing_closed"):
        # continued: a before-harvest (or harvest-phase) failure stays reviewable — only the
        # run state recovers to "ready" so skip-failed / retry-unsent / review are accepted; the
        # failed STEP itself keeps its "failed" state as the evidence of what happened.
        record["state"] = "ready"
    if record.get("routing_closed") and record.get("state") not in TERMINAL:
        record["state"] = "stopped"
    expose_signature_plan(record)
    record["updated"] = time.time()
    _atomic_write(folder / f"run-{record['run_id']}.json", record)


def records(folder: Path, player: str) -> list[dict]:
    result = []
    for path in folder.glob("run-*.json"):
        record = read(folder, path.stem[4:], player)
        result.append(record)
    return sorted(result, key=lambda row: row["created"], reverse=True)


def _materialize_steps(preview_steps: list[dict]) -> list[dict]:
    """Assign a durable step_id to each planned step and, for a two-step sale's dependent leg
    (), translate its planning-time `depends_on` rule-step id into the created
    `depends_on_step_id` of the step it actually depends on."""
    steps: list[dict] = []
    by_rule_id: dict[str, str] = {}
    for step in preview_steps:
        rule_id = step.get("id")
        if step.get("status") == "empty":
            continue
        entry = dict(copy.deepcopy(step), step_id=secrets.token_hex(16), state="planned")
        if "signature_cost" not in entry:
            detail = entry.get("deposit") or entry.get("quote") or entry
            entry["signature_cost"] = 2 if detail.get("external") else 1
        if rule_id:
            by_rule_id[rule_id] = entry["step_id"]
        depends_on = step.get("depends_on")
        if depends_on and depends_on in by_rule_id:
            entry["depends_on_step_id"] = by_rule_id[depends_on]
        steps.append(entry)
    return steps


def _update_signature_totals(record: dict) -> None:
    if record.get("phase") != "after":
        for key in ("signatures_total", "total_signatures", "signed_signatures"):
            record.pop(key, None)
        return
    from fides.op import DEFAULT_MAX_OPS_PER_TX
    phase_steps = [step for group in record.get("phase_steps", {}).values() for step in group]
    after_steps = list(record.get("steps", []))
    harvest_total = len(record.get("harvest_signature_ids", []))
    routing_total = sum(max(1, int(step.get("signature_cost", 1))) for step in phase_steps)
    index = 0
    while index < len(after_steps):
        first = after_steps[index]
        quote = first.get("quote", first)
        eligible = (first.get("kind") == "sell" and quote.get("destination_symbol") == "DEC"
                    and quote.get("source_symbol") in _CHAINABLE_SALE_SYMBOLS
                    and not quote.get("external") and not first.get("two_step") and not first.get("depends_on_step_id"))
        group = [first]
        if eligible:
            symbols = {quote["source_symbol"]}
            for candidate in after_steps[index + 1:]:
                candidate_quote = candidate.get("quote", candidate)
                symbol = candidate_quote.get("source_symbol")
                candidate_eligible = (candidate.get("kind") == "sell" and candidate_quote.get("destination_symbol") == "DEC"
                                      and symbol in _CHAINABLE_SALE_SYMBOLS
                                      and not candidate_quote.get("external") and not candidate.get("two_step")
                                      and not candidate.get("depends_on_step_id") and symbol not in symbols)
                if not candidate_eligible or len(group) >= DEFAULT_MAX_OPS_PER_TX:
                    break
                group.append(candidate)
                symbols.add(symbol)
        routing_total += 1 + sum(max(0, int(step.get("signature_cost", 1)) - 1) for step in group)
        index += len(group)
    routing_tx_ids = {step.get("tx_id") for step in phase_steps + after_steps if step.get("tx_id")}
    funding_tx_ids = {(step.get("funding") or {}).get("tx_id") for step in phase_steps + after_steps
                      if (step.get("funding") or {}).get("tx_id")}
    signed = harvest_total + len(routing_tx_ids) + len(funding_tx_ids)
    total = harvest_total + routing_total
    record["signatures_total"] = total
    record["total_signatures"] = total
    record["signed_signatures"] = min(total, signed)


def reward_members(root: Path, player: str, identifiers: list[str]) -> list[dict]:
    from .harvest_evidence import load_attempt
    members = []
    for identifier in dict.fromkeys(identifiers):
        attempt = load_attempt(root, player, identifier) or {}
        for member in attempt.get("members", []):
            if member.get("outcome") == "success" and member.get("finds"):
                members.append({"region_uid": member["region_uid"], "finds": copy.deepcopy(member["finds"])})
    return members


def harvest_signature_ids(root: Path, player: str, identifiers: list[str]) -> list[str]:
    from .harvest_evidence import load_attempt
    return sorted({attempt["tx_id"] for identifier in dict.fromkeys(identifiers)
                   if (attempt := load_attempt(root, player, identifier)) and attempt.get("tx_id")})


def create(root: Path, player: str, preview: dict) -> dict:
    with locked(root, player) as folder:
        history = records(folder, player)
        existing = next((row for row in history if row.get("preview_id") == preview["preview_id"]), None)
        if existing:
            return existing
        for row in history:
            if row["state"] not in TERMINAL:
                if in_flight(row):
                    raise RoutingError(
                        "A routing transaction is still awaiting confirmation; check it before starting another run: reopen "
                        + _scope_label(row.get("scope", {}))
                    )
                _stop(folder, row, "The immediate routing session ended before completion.")
        claimed = {identifier for row in history for identifier in row.get("harvest_attempt_ids", [])}
        if claimed.intersection(preview.get("harvest_attempt_ids", [])):
            # A receipt remains bound to its original run even after cancellation
            # recreating percentage rules must never spend that same harvest again.
            raise RoutingError("These harvest receipts already belong to a saved routing run")
        steps = _materialize_steps(preview["steps"])
        record = {key: copy.deepcopy(preview[key]) for key in
                  ("player", "preview_id", "preview_digest", "phase", "scope", "slippage", "rules", "reserves", "harvest_attempt_ids")}
        record["projected_harvest_chunks"] = int(preview.get("projected_harvest_chunks", 0))
        record.update(run_id=secrets.token_hex(16), created=time.time(), state="ready" if steps else "complete", steps=steps)
        record["harvest_signature_ids"] = harvest_signature_ids(root, player, record.get("harvest_attempt_ids", []))
        record["harvest_reward_members"] = reward_members(root, player, record.get("harvest_attempt_ids", []))
        advance_phase(record)
        write(folder, record)
        return record


def in_flight(record: dict) -> bool:
    return record.get("state") in IN_FLIGHT or any(step.get("state") in IN_FLIGHT for step in record.get("steps", []))


def _scope_label(scope: dict) -> str:
    if scope.get("kind") == "regions":
        uids = scope.get("region_uids") or []
        if uids:
            return ", ".join(uids)
    return "All regions"


def _stop(folder: Path, record: dict, reason: str) -> dict:
    # Never close a run whose transaction may be on chain: it stays open until verified (uncertain-broadcast tracking).
    if record.get("state") in TERMINAL or in_flight(record):
        return record
    record["routing_closed"] = True
    record["state"] = "stopped"
    record["failure"] = {"code": "CERES_ROUTING_INTERRUPTED", "at": time.time(), "message": reason}
    _LOG.error("CERES_ROUTING_INTERRUPTED player=%s run=%s reason=%s", record.get("player"), record.get("run_id"), reason)
    write(folder, record)
    return record


def stop(root: Path, player: str, identifier: str) -> dict:
    with locked(root, player) as folder:
        return _stop(folder, read(folder, identifier, player), "Immediate routing ended. Remaining steps will not be routed later.")


def next_step(record: dict) -> dict | None:
    return next((step for step in record["steps"] if step["state"] not in {"confirmed", "skipped"}), None)


def cancel(root: Path, player: str, identifier: str) -> dict:
    with locked(root, player) as folder:
        record = read(folder, identifier, player)
        if any(step["state"] in {"awaiting_wallet", "submitted", "pending", "settling"} for step in record["steps"]):
            raise RoutingError("A wallet handoff is unresolved. Check its transaction before cancelling")
        if record.get("phase") == "harvest":
            for item in harvest_attempts(root, player, record):
                classify_harvest_attempt(item["evidence"])
        if record["state"] == "complete":
            return record
        for step in record["steps"]:
            if step["state"] not in {"confirmed", "skipped"}:
                step["state"] = "skipped"
        record["state"] = "cancelled"
        write(folder, record)
        return record


def advance_phase(record: dict) -> None:
    if next_step(record) is not None:
        return
    if record["phase"] == "before":
        record.setdefault("phase_steps", {})["before"] = record["steps"]
        record.update(phase="harvest", state="ready_harvest", steps=[], harvest_attempts=[])
    else:
        record["state"] = "complete"


def harvest_attempts(root: Path, player: str, record: dict) -> list[dict]:
    from .harvest_evidence import load_attempt
    return [dict(reference, evidence=load_attempt(root, player, reference["attempt_id"]))
            for reference in record.get("harvest_attempts", [])]


def begin_harvest(root: Path, player: str, preview: dict, index: int, attempt_id: str) -> dict:
    from .harvest_evidence import begin_attempt
    with locked(root, player) as folder:
        record = read(folder, preview.get("routing_run_id"), player)
        if record["phase"] != "harvest" or record["state"] == "cancelled" or record.get("harvest_closed"):
            raise RoutingError("The routing workflow is not accepting more harvest attempts")
        expected = (record.get("harvest_preview") or {}).get("chunks", [])
        if not 0 <= index < len(expected) or expected[index] != preview["chunks"][index]:
            raise RoutingError("Harvest operations do not match the saved workflow")
        refs = record.setdefault("harvest_attempts", [])
        existing = next((ref for ref in refs if ref["chunk_index"] == index), None)
        if existing and existing["attempt_id"] != attempt_id:
            raise RoutingError("This harvest already has a wallet attempt. Recover it instead of signing again")
        for item in harvest_attempts(root, player, record):
            if item["chunk_index"] == index:
                continue
            classify_harvest_attempt(item["evidence"])
        if not existing:
            refs.append({"chunk_index": index, "attempt_id": attempt_id})
            record["state"] = "harvesting"
            write(folder, record)
        attempt = begin_attempt(root, player, preview, index, attempt_id)
        if attempt.get("state") == "rejected_before_send" and not attempt.get("tx_id"):
            from .harvest_evidence import update_attempt
            attempt = update_attempt(root, player, attempt_id, state="awaiting_keychain")
        return attempt


def prepare_harvest_batch(root: Path, player: str, preview: dict, attempts: list[dict]) -> list[dict]:
    from .harvest_evidence import begin_attempt, update_attempt
    with locked(root, player) as folder:
        record = read(folder, preview.get("routing_run_id"), player)
        if record["phase"] != "harvest" or record["state"] == "cancelled" or record.get("harvest_closed"):
            raise RoutingError("The routing workflow is not accepting more harvest attempts")
        if (record.get("harvest_preview") or {}).get("chunks") != preview["chunks"]:
            raise RoutingError("Harvest operations do not match the saved workflow")
        refs = record.setdefault("harvest_attempts", [])
        for item in harvest_attempts(root, player, record):
            classify_harvest_attempt(item["evidence"])
        for item in attempts:
            existing = next((ref for ref in refs if ref["chunk_index"] == item["chunk_index"]), None)
            if existing and existing["attempt_id"] != item["attempt_id"]:
                raise RoutingError("This harvest already has a wallet attempt. Recover it instead of signing again")
        records = []
        for item in attempts:
            if not any(ref["chunk_index"] == item["chunk_index"] for ref in refs):
                refs.append(dict(item))
            attempt = begin_attempt(root, player, preview, item["chunk_index"], item["attempt_id"])
            if attempt.get("state") == "rejected_before_send" and not attempt.get("tx_id"):
                attempt = update_attempt(root, player, item["attempt_id"], state="awaiting_keychain")
            records.append(attempt)
        record["state"] = "harvesting"
        write(folder, record)
        return records


def classify_harvest_attempt(attempt: dict | None) -> tuple[list[dict], list[dict]]:
    if not attempt:
        raise RoutingError("Saved harvest evidence is missing")
    if not attempt.get("tx_id"):
        if attempt.get("state") == "rejected_before_send":
            return [], list(attempt.get("members", []))
        raise RoutingError("A harvest wallet request has an unknown outcome")
    members = attempt.get("members") or []
    if not members:
        raise RoutingError("Harvest operation evidence is missing")
    accepted, refused = [], []
    for member in members:
        if member.get("outcome") == "success" and member.get("game_state") == "accepted" and member.get("evidence"):
            accepted.append(member)
        elif member.get("outcome") == "failed" and member.get("game_state") == "refused":
            refused.append(member)
        else:
            raise RoutingError("A harvest operation still needs game confirmation")
    return accepted, refused


def finish_harvest(root: Path, player: str, record: dict) -> list[str]:
    expected = (record.get("harvest_preview") or {}).get("chunks", [])
    evidence = harvest_attempts(root, player, record)
    covered = {item["chunk_index"] for item in evidence} | set(record.get("harvest_skipped_chunks", []))
    if not expected or covered != set(range(len(expected))):
        raise RoutingError("Finish harvesting or explicitly route only the confirmed portion")
    receipts, accepted_regions, refused_regions, signature_progress = [], [], [], []
    for item in evidence:
        accepted, refused = classify_harvest_attempt(item["evidence"])
        signature_progress.append({"chunk_index": item["chunk_index"], "signed": True, "confirmed": True})
        if accepted:
            receipts.append(item["attempt_id"])
        accepted_regions.extend(member["region_uid"] for member in accepted)
        refused_regions.extend(member["region_uid"] for member in refused)
    record["harvest_summary"] = {"accepted_regions": accepted_regions, "refused_regions": refused_regions,
                                 "skipped_chunks": record.get("harvest_skipped_chunks", [])}
    record["harvest_signature_progress"] = signature_progress
    return receipts


def close_harvest(root: Path, player: str, identifier: str) -> dict:
    with locked(root, player) as folder:
        record = read(folder, identifier, player)
        if record["phase"] != "harvest" or record["state"] in TERMINAL or record.get("routing_closed"):
            raise RoutingError("This workflow is not in its harvest phase")
        expected = (record.get("harvest_preview") or {}).get("chunks", [])
        attempts = harvest_attempts(root, player, record)
        if not expected or not attempts:
            raise RoutingError("No harvest has been attempted yet")
        accepted_count = 0
        for item in attempts:
            accepted, _refused = classify_harvest_attempt(item["evidence"])
            accepted_count += len(accepted)
        if not accepted_count:
            raise RoutingError("No confirmed harvest proceeds are available to route")
        attempted = {item["chunk_index"] for item in attempts}
        record["harvest_skipped_chunks"] = sorted(set(range(len(expected))) - attempted)
        record["harvest_closed"] = True
        record["state"] = "harvest_partial"
        finish_harvest(root, player, record)
        write(folder, record)
        return record


def continue_after(root: Path, player: str, preview: dict) -> dict:
    with locked(root, player) as folder:
        record = read(folder, preview.get("routing_run_id"), player)
        if record.get("after_preview_id") == preview["preview_id"]:
            return record
        if record["phase"] != "harvest" or record["state"] in TERMINAL or record.get("routing_closed"):
            raise RoutingError("The saved workflow cannot start post-harvest routing now")
        receipts = finish_harvest(root, player, record)
        if set(receipts) != set(preview["harvest_attempt_ids"]) or preview.get("workflow_rules_digest") != plan_digest(record):
            raise RoutingError("The routing review no longer matches the saved harvest plan")
        record.update(phase="after", state="ready", after_preview_id=preview["preview_id"],
                      harvest_attempt_ids=receipts, rules=copy.deepcopy(preview["rules"]), slippage=preview["slippage"],
                      steps=_materialize_steps(preview["steps"]))
        record["harvest_signature_ids"] = harvest_signature_ids(
            root, player, [item["attempt_id"] for item in record.get("harvest_attempts", [])])
        record["harvest_reward_members"] = reward_members(root, player, record.get("harvest_attempt_ids", []))
        advance_phase(record)
        write(folder, record)
        return record


def plan_digest(record: dict) -> str:
    from .harvest_evidence import digest
    return digest({key: record[key] for key in ("rules", "slippage", "reserves", "scope")})
