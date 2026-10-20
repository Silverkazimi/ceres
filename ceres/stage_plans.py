""" phase 2 -- live check of the maintainer's kept stage plans.

A plan is the maintainer's intent (which card goes where); it is never game data. Every kept plan is judged here
against the page load's own capture () before the page draws it. Verdicts: `ok` (with the live cooldown
and, for a card staked elsewhere, its live location), `done` (the game already shows it), `needs_assets` when plot detail must be fetched, or `drop` with a
reason (the live read makes it impossible). Pure: no network, no writes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .candidates import cooldown_state
from .metrics import classify_idle_availability
from .op_preview import GROUP_STAKE_TYPES, AssetIndex, _asset_record, _facts_for_eligibility, _plot_state

MAX_PLANS = 200
_ACTIONS = {"stake", "unstake", "replace"}


def _drop(key: Any, reason: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "verdict": "drop", "reason": reason, **extra}


def _slot_occupant(workers_by_deed: dict[str, list[dict[str, Any]]], deed_uid: str, slot_index: Any) -> dict[str, Any] | None:
    try:
        slot = int(slot_index) + 1
    except (TypeError, ValueError):
        return None
    return next((worker for worker in workers_by_deed.get(deed_uid, []) if str(worker.get("slot")) == str(slot)), None)


def _verdict(report, plan, plots, workers_by_deed, account, now, index: AssetIndex | None = None) -> dict[str, Any]:
    key = plan.get("key")
    deed = str(plan.get("deed_uid") or "")
    group = plan.get("group_id")
    action = plan.get("action")
    ref = str(plan.get("asset_ref") or "")
    if group not in GROUP_STAKE_TYPES or action not in _ACTIONS or not ref:
        return _drop(key, "invalid_plan")
    if deed not in plots:
        return _drop(key, "plot_gone")
    record = _asset_record(report, ref, group, index)
    asset_fetch = plots[deed].get("asset_fetch") or {}
    if asset_fetch.get("status") in {"not_requested", "failed", "dropped_stale"} and (
        action in {"unstake", "replace"} or record is None
    ):
        # A quick capture has plot flags, not item identities; absence is not evidence of removal.
        return {"key": key, "verdict": "needs_assets", "deed_uid": deed}
    if record is None:
        return _drop(key, "card_gone")
    facts = record.get("facts") or {}
    live = {"name": facts.get("name"), "level": facts.get("level"), "bcx": facts.get("bcx"), "card_detail_id": facts.get("card_detail_id")}
    staked_here = bool(record.get("staked")) and str(record.get("deed_uid")) == deed
    if action == "unstake":
        if staked_here:
            return {"key": key, "verdict": "ok", "cooldown": None, "location": None, "live": live}
        if record.get("staked"):
            return _drop(key, "card_moved")
        return {"key": key, "verdict": "done"}
    if staked_here:
        occupant = _slot_occupant(workers_by_deed, deed, plan.get("slot_index")) if group == "worker" else None
        if group != "worker" or (occupant and str(occupant.get("asset_ref")) == ref):
            return {"key": key, "verdict": "done"}
    available, reason = classify_idle_availability(_facts_for_eligibility(record), now, account)
    if not available:
        return _drop(key, "card_unavailable", detail=reason)
    healed_action = None
    if action == "replace" and plan.get("evict_asset_ref"):
        evict = _asset_record(report, str(plan["evict_asset_ref"]), group, index)
        if not evict or not evict.get("staked") or str(evict.get("deed_uid")) != deed:
            # a plan that replaced a card which was itself only STAGED (never live, v0.9.331 and before)
            # is a plain stake of the slot while nothing live holds it; the client keeps the healed action.
            if evict is not None and not evict.get("staked") and _live_slot_holder(report, workers_by_deed, deed, group, plan.get("slot_index")) is None:
                healed_action = "stake"
            else:
                return _drop(key, "evict_gone")
    if action == "stake" and group == "worker":
        occupant = _slot_occupant(workers_by_deed, deed, plan.get("slot_index"))
        if occupant and str(occupant.get("asset_ref")) != ref:
            return _drop(key, "slot_taken")
    bucket = record.get("bucket") or ("staked" if record.get("staked") else "unstaked_apparent")
    stake_type = str(record.get("stake_type_uid") or GROUP_STAKE_TYPES[group])
    cooldown = cooldown_state(bucket, facts.get("stake_end_date"), stake_type, now, survival_date=facts.get("survival_date"))
    location = {"deed_uid": record.get("deed_uid"), "slot": record.get("slot")} if record.get("staked") and not staked_here else None
    verdict = {"key": key, "verdict": "ok", "cooldown": cooldown, "location": location, "live": live}
    if healed_action:
        verdict["action"] = healed_action
    return verdict


def _live_slot_holder(report, workers_by_deed, deed_uid: str, group: str, slot_index: Any) -> dict[str, Any] | None:
    """The live asset holding this slot on the deed: a worker by slot, an item/Runi by stake type."""
    if group == "worker":
        return _slot_occupant(workers_by_deed, deed_uid, slot_index)
    wanted = GROUP_STAKE_TYPES[group]
    for row in list(report.get("workers", [])) + list(report.get("bonus_assets", [])):
        if str(row.get("stake_ref_uid")) == deed_uid and row.get("stake_type_uid", wanted) == wanted:
            return row
    return None


def check_plans(report: dict[str, Any], plans: list[dict[str, Any]], *, account: str | None, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    plots, workers_by_deed = _plot_state(report)
    # one collection pass per request instead of a linear search per plan.
    index = AssetIndex(report)
    verdicts = [_verdict(report, plan if isinstance(plan, dict) else {}, plots, workers_by_deed, account, now, index) for plan in plans]
    seen: set[str] = set()
    for plan, verdict in zip(plans, verdicts):
        if not isinstance(plan, dict) or plan.get("action") not in {"stake", "replace"} or not plan.get("asset_ref"):
            continue
        ref = str(plan["asset_ref"])
        if ref in seen:
            verdict.clear()
            verdict.update(_drop(plan.get("key"), "duplicate_staged"))
        else:
            seen.add(ref)
    return verdicts
