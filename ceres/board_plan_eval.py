"""IN-0267 phase 1: planned values for a virtual board plan, computed on the board's own capture.

A board plan is a virtual clean sheet: a changed plot holds exactly its planned assets (workers, Runi,
Power Core, Totem, Title) and nothing else. This module prices that target roster with the one shared
roster evaluator (`candidates.stage_impact` and its `_project_staged_plot_state`) by expressing the target
as "remove every live asset of the plot, then stake the planned ones". It never reads the network, the
chain or the game API: input is the already-loaded capture, output is plain numbers or an explicit
`unknown` with a reason. A plot whose live assets are not fully known (quick capture, failed detail read,
worker count larger than the known roster) is `unknown`, never priced from partial data.

Per planned asset it also returns the asset's real state from the same capture: where it is staked now,
and the server's cooldown vocabulary with the Survival gate (`cooldown_state(..., survival_date=...)`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .board import load_details_by_id
from .candidates import (
    _plots_cached,
    _project_staged_plot_state,
    _projected_grain_food,
    _stage_asset_record,
    cooldown_state,
    stage_impact,
)
from .capacity import _structure_identity, potential_worker_capacity, structure_capacity_index
from .harvest import hourly_resource_costs
from .op_preview import GROUP_STAKE_TYPES, AssetIndex, _asset_record
from praetoria.land_reallocate import _scoring_allocation

MAX_ROWS = 50
MAX_PLACEMENTS_PER_ROW = 60
GROUPS = tuple(GROUP_STAKE_TYPES)
_GROUP_BY_STAKE_TYPE = {stake_type: group for group, stake_type in GROUP_STAKE_TYPES.items()}
_INCOMPLETE_FETCH = {"not_requested", "failed", "dropped_stale"}


def _num(value: Any, default: float | None = 0.0) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _unknown(deed_uid: str, reason: str) -> dict[str, Any]:
    return {"deed_uid": deed_uid, "coverage": "unknown", "reason": reason}


def _live_removals(report: dict[str, Any], deed_uid: str, plot, plot_row: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Unstake moves for every live asset of the plot, or None when the live roster is not fully known."""
    moves: list[dict[str, Any]] = []
    for card in plot.cards:
        moves.append({"deed_uid": deed_uid, "group_id": "worker", "action": "unstake", "asset_ref": card.get("asset_ref"),
                      "slot_index": (int(card["slot"]) - 1) if isinstance(card.get("slot"), int) and card["slot"] > 0 else None})
    runis = [worker for worker in report.get("workers", [])
             if str(worker.get("stake_ref_uid")) == deed_uid and worker.get("stake_type_uid") == "STK-LND-RUNI"]
    moves.extend({"deed_uid": deed_uid, "group_id": "runi", "action": "unstake", "asset_ref": runi.get("asset_ref"), "slot_index": 0}
                 for runi in runis)
    for item in report.get("bonus_assets", []):
        if str(item.get("stake_ref_uid")) != deed_uid:
            continue
        group = _GROUP_BY_STAKE_TYPE.get(str(item.get("stake_type_uid") or ""))
        if group is None:
            return None
        moves.append({"deed_uid": deed_uid, "group_id": group, "action": "unstake", "asset_ref": item.get("asset_ref"), "slot_index": 0})
    if any(not move.get("asset_ref") for move in moves):
        return None
    known_workers = len(plot.cards) + len(runis)
    live_count = _num((plot_row.get("staking_facts") or {}).get("worker_count"), None)
    if live_count is not None and live_count > known_workers:
        return None
    return moves


def asset_state(report: dict[str, Any], asset_ref: str, group_id: str, *, index: AssetIndex, now: datetime) -> dict[str, Any]:
    """The asset's real state from the capture: location and the server's cooldown verdict (Survival included)."""
    record = _asset_record(report, asset_ref, group_id, index) if group_id in GROUP_STAKE_TYPES else None
    if record is None:
        return {"asset_ref": asset_ref, "known": False}
    facts = record.get("facts") or {}
    bucket = record.get("bucket") or ("staked" if record.get("staked") else "unstaked_apparent")
    stake_type = str(record.get("stake_type_uid") or GROUP_STAKE_TYPES[group_id])
    cooldown = cooldown_state(bucket, facts.get("stake_end_date"), stake_type, now, survival_date=facts.get("survival_date"))
    return {
        "asset_ref": asset_ref, "known": True, "staked": bool(record.get("staked")),
        "location_deed_uid": record.get("deed_uid") if record.get("staked") else None,
        "location_slot": record.get("slot") if record.get("staked") else None,
        "cooldown": cooldown,
    }


def evaluate_target_row(report: dict[str, Any], deed_uid: str, placements: list[dict[str, Any]], *,
                        details_by_id: dict[int, dict[str, Any]], plots: dict[str, Any]) -> dict[str, Any]:
    plot = plots.get(deed_uid)
    plot_row = next((row for row in report.get("plots", []) if str(row.get("deed_uid")) == deed_uid), None)
    if plot is None or plot_row is None:
        return _unknown(deed_uid, "plot_gone")
    if (plot_row.get("asset_fetch") or {}).get("status") in _INCOMPLETE_FETCH:
        return _unknown(deed_uid, "plot_detail_missing")
    removals = _live_removals(report, deed_uid, plot, plot_row)
    if removals is None:
        return _unknown(deed_uid, "plot_detail_missing")
    stakes = []
    for placement in placements:
        if _stage_asset_record(report, str(placement["asset_ref"])) is None:
            return _unknown(deed_uid, "asset_unknown")
        stakes.append({"deed_uid": deed_uid, "group_id": placement["group_id"], "action": "stake",
                       "asset_ref": str(placement["asset_ref"]), "slot_index": placement["slot_index"]})
    moves = removals + stakes
    try:
        if moves:
            impact = stage_impact(report, deed_uid, moves, details_by_id=details_by_id, plots=plots)
        else:
            impact = None
        state = _project_staged_plot_state(report, deed_uid, moves, details_by_id, plot, plot_row)
    except (KeyError, ValueError, LookupError):
        return _unknown(deed_uid, "not_priceable")
    powered_slots = state["worker_capacity"]
    cards = list(state["after_cards"])
    if powered_slots > 0:
        cards = [card for card in cards if (int(card.get("slot") or 0)) <= powered_slots]
    allocation = _scoring_allocation(cards, plot.base_cap)
    if allocation is None:
        return _unknown(deed_uid, "not_priceable")
    runi_base = sum(_num(runi.get("land_base_pp")) or 0.0 for runi in state["after_runis"])
    base_pp = sum(_num(card.get("base")) or 0.0 for card in cards) + runi_base
    base_after_cap = sum(float(value) for value in allocation.values()) + runi_base
    construction = sum(float(allocation[str(card["asset_ref"])]) * (1 + plot.terrain(card.get("color") or "Gray", card.get("secondary_color")))
                       for card in cards) + runi_base
    harvest = _num(impact.get("resulting_harvest")) if impact else 0.0
    work = impact.get("resulting_work_per_hour") if impact else 0.0
    if not state["after_powered"]:
        harvest, work = 0.0, (0.0 if work is not None else None)
    runi_staked = bool(state["after_runis"])
    regular = len(state["after_cards"])
    _identity, family = _structure_identity(plot_row)
    capacity_index = structure_capacity_index(report)
    card_capacity = potential_worker_capacity(plot_row, capacity_index) if family == "worksite" else int(powered_slots)
    food = _projected_grain_food(cards, state["after_runis"], allocation, details_by_id)
    materials = hourly_resource_costs(plot_row.get("worksite_facts"), {**(plot_row.get("staking_facts") or {}),
                                                                      "total_base_pp_after_cap": base_after_cap})
    cap = _num((plot_row.get("staking_facts") or {}).get("total_base_pp_cap"), None)
    return {
        "deed_uid": deed_uid, "coverage": "complete",
        "harvest_pp": harvest, "construction_pp": construction, "base_pp": base_pp, "base_pp_after_cap": base_after_cap,
        "base_pp_cap": cap, "base_pp_cap_reached": bool(cap is not None and base_pp - runi_base >= cap),
        "work_per_hour": work, "multiplier": state["after_mult"], "powered": bool(state["after_powered"]),
        "grain_cost_per_hour": food,
        "wood_cost_per_hour": (materials or {}).get("WOOD"), "stone_cost_per_hour": (materials or {}).get("STONE"),
        "iron_cost_per_hour": (materials or {}).get("IRON"),
        "capacity": {"worker_count": regular + (1 if runi_staked else 0), "runi_staked": runi_staked, "card_capacity": card_capacity,
                     "display": f"{regular}/{card_capacity}+R" if runi_staked else f"{regular}/{card_capacity}"},
    }


def evaluate_targets(report: dict[str, Any], rows: list[dict[str, Any]], *, now: datetime | None = None,
                     details_by_id: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Planned values per requested plot plus the real state of every planned asset. Pure; no I/O beyond the
    card-detail catalogue already used by the evaluator."""
    now = now or datetime.now(timezone.utc)
    details_by_id = details_by_id if details_by_id is not None else load_details_by_id()
    plots = _plots_cached(report, details_by_id)
    index = AssetIndex(report)
    out_rows = []
    assets: dict[str, dict[str, Any]] = {}
    for row in rows:
        deed_uid = str(row["deed_uid"])
        placements = row["placements"]
        for placement in placements:
            ref = str(placement["asset_ref"])
            if ref not in assets:
                assets[ref] = asset_state(report, ref, placement["group_id"], index=index, now=now)
        out_rows.append(evaluate_target_row(report, deed_uid, placements, details_by_id=details_by_id, plots=plots))
    return {"generated_at": report.get("generated_at"), "rows": out_rows, "assets": assets}
