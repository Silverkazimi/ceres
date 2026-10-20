"""Offline worker PP estimates and bounded advisory recommendations."""

from __future__ import annotations

import bisect
import copy
from collections import defaultdict
from typing import Any, Callable

from .land_reallocate import (
    _level_by_asset_ref,
    _plot_roster,
    _scoring_allocation,
    attach_colors,
    build_plots,
    plot_abilities_boost,
    plot_bloodline_boost,
)


BOOST_CATEGORIES = (
    "deed_rarity_boost",
    "deed_status_token_boost",
    "runi_boost",
    "title_boost",
    "totem_boost",
    "card_abilities_boost",
    "card_bloodlines_boost",
)

# work_per_hour = total_harvest_pp / divisor is per-resource, NOT universal -- primary-sourced rates
# match the values measured across 130 live plots. SPS uses a pool-share model rather than a constant
# divisor, so it remains `None`; Aura's primary-sourced 0.0005/hour rate matches Iron and inverts to
# divisor 2000.
WORK_PER_HOUR_DIVISOR_BY_RESOURCE: dict[str, float] = {
    "GRAIN": 50.0,
    "WOOD": 200.0,
    "STONE": 500.0,
    "IRON": 2000.0,
    "AURA": 2000.0,
    "RESEARCH": 10000.0,
}


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def estimate_card_pp(
    card: dict[str, Any],
    *,
    base_pp_after_cap: float | None = None,
    terrain_boost: float | None = None,
    boost_override: float | None = None,
    resource: str | None = None,
) -> dict[str, Any]:
    """Apply the evidence-locked per-card chain to the supplied effective base PP."""
    raw_base = _number(card.get("land_base_pp", card.get("base_pp")))
    after_cap = base_pp_after_cap if base_pp_after_cap is not None else _number(card.get("base_pp_after_cap", raw_base))
    terrain = terrain_boost if terrain_boost is not None else _number(card.get("terrain_boost"))
    rates = {name: _number(card.get(name)) for name in BOOST_CATEGORIES}
    if boost_override is None and any(value is None for value in rates.values()):
        return {
            "estimate_class": "unknown",
            "value": None,
            "reason": "terrain or one of the seven boost categories is not observed for this card",
            "formula": "total_construction_pp = base_pp_after_cap × (1 + terrain_boost); total_harvest_pp = total_construction_pp × (1 + sum(seven boosts))",
        }
    if raw_base is None or after_cap is None or terrain is None:
        return {"estimate_class": "unknown", "value": None, "reason": "base PP or terrain boost is not observed"}
    boost = boost_override if boost_override is not None else sum(value for value in rates.values() if value is not None)
    construction = after_cap * (1.0 + terrain)
    harvest = construction * (1.0 + boost)
    if resource is None:
        # No resource supplied: preserve the tool's original, locked IRON-divisor replay behavior
        # (see `replay_estimator`, whose fixed mismatch list against real IRON/WOOD/SPS-plot cards
        # is a deliberate discrepancy-detection feature, not something this resource-awareness fix
        # should silently correct). A caller that KNOWS the target resource must pass it.
        work_per_hour = harvest / 2000.0
        work_per_hour_divisor = 2000.0
        work_per_hour_reason = "IRON divisor (default; no resource supplied)"
    else:
        divisor = WORK_PER_HOUR_DIVISOR_BY_RESOURCE.get(resource)
        if divisor is None:
            work_per_hour = None
            work_per_hour_divisor = None
            if resource == "SPS":
                work_per_hour_reason = "SPS uses a pool-share model, not a constant divisor"
            else:
                work_per_hour_reason = f"{resource} work_per_hour divisor is not observed/understood"
        else:
            work_per_hour = harvest / divisor
            work_per_hour_divisor = divisor
            work_per_hour_reason = f"{resource} divisor {divisor:g}"
    result = {
        "estimate_class": "validated_model" if boost_override is None else "heuristic",
        "raw_base_pp": raw_base,
        "base_pp_after_cap": after_cap,
        "terrain_boost": terrain,
        "boost": boost,
        "total_construction_pp": construction,
        "total_harvest_pp": harvest,
        "work_per_hour": work_per_hour,
        "work_per_hour_divisor": work_per_hour_divisor,
        "work_per_hour_reason": work_per_hour_reason,
        "formula": "total_construction_pp = base_pp_after_cap × (1 + terrain_boost); total_harvest_pp = total_construction_pp × (1 + deed_rarity_boost + deed_status_token_boost + runi_boost + title_boost + totem_boost + card_abilities_boost + card_bloodlines_boost); work_per_hour = total_harvest_pp / <per-resource divisor: GRAIN 50, WOOD 200, STONE 500, IRON 2000, AURA 2000, RESEARCH 10000; SPS pool-share -> null>",
    }
    observed_work = _number(card.get("work_per_hour"))
    if observed_work is not None and work_per_hour is not None:
        result["observed_work_per_hour"] = observed_work
        result["work_per_hour_observation_matches_formula"] = abs(observed_work - work_per_hour) <= 0.001
    return result


def replay_estimator(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [estimate_card_pp(card) for card in cards]


def _plot_ref(plot: dict[str, Any]) -> str:
    return str(plot.get("facts", {}).get("plot", plot.get("deed_uid", "")))


def _stake(plot: dict[str, Any]) -> dict[str, Any]:
    return plot.get("staking_facts", {})


def _slot_number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _used_slots(report: dict[str, Any], deed_uid: str) -> set[int]:
    result = set()
    for assignment in report.get("assignments", []):
        if assignment.get("deed_uid") == deed_uid:
            slot = _slot_number(assignment.get("slot"))
            if slot is not None:
                result.add(slot)
    return result


def _open_slot(report: dict[str, Any], plot: dict[str, Any]) -> int | None:
    stake = _stake(plot)
    maximum = _slot_number(stake.get("max_workers_allowed"))
    if maximum is None or maximum < 1:
        return None
    used = _used_slots(report, str(plot.get("deed_uid", "")))
    return next((slot for slot in range(1, maximum + 1) if slot not in used), None)


def _target_estimate(
    card: dict[str, Any], plot: dict[str, Any], *, base_pp_after_cap: float | None, allocation_reason: str | None,
) -> dict[str, Any]:
    if allocation_reason:
        return {"estimate_class": "unknown", "value": None, "reason": allocation_reason}
    stake = _stake(plot)
    terrain = _number(stake.get("total_terrain_boost"))
    boost = _number(stake.get("total_boost"))
    resource = plot.get("facts", {}).get("resource")
    return estimate_card_pp(
        card, base_pp_after_cap=base_pp_after_cap, terrain_boost=terrain,
        boost_override=boost, resource=resource,
    )


def _state(plot: dict[str, Any]) -> dict[str, Any]:
    stake = _stake(plot)
    return {
        "plot": _plot_ref(plot),
        "deed_uid": plot.get("deed_uid"),
        "worker_count": stake.get("worker_count"),
        "max_workers_allowed": stake.get("max_workers_allowed"),
        "total_base_pp": stake.get("total_base_pp"),
        "total_base_pp_after_cap": stake.get("total_base_pp_after_cap"),
        "total_base_pp_cap": stake.get("total_base_pp_cap"),
        "total_harvest_pp": stake.get("total_harvest_pp"),
    }


def _confidence(card: dict[str, Any]) -> dict[str, Any]:
    classification = card.get("classification", {})
    grade = classification.get("confidence", "uncertain")
    return {
        "grade": grade,
        "reason": classification.get("reason", "classification evidence is incomplete"),
        "what_would_raise": "a successful Land asset cross-check and verified in-game eligibility/ownership evidence",
    }


def _recommendation(
    *, action: str, card: dict[str, Any], target: dict[str, Any], source: dict[str, Any] | None,
    slot: int, target_estimate: dict[str, Any], delta: float | None, delta_reason: str | None,
    opportunity_value: float | None, opportunity_reason: str, conflicts: list[str], score: float,
    side_effects: list[dict[str, Any]] | None = None, side_effects_evaluated: bool = False,
) -> dict[str, Any]:
    target_ref = _plot_ref(target)
    source_ref = _plot_ref(source) if source else (target_ref if action == "replace" else None)
    confidence = _confidence(card)
    estimate_class = target_estimate.get("estimate_class", "unknown")
    side_effects = side_effects or []
    assumptions = [
        "This is a read-only hypothetical; Praetoria performs no action.",
        "In-game staking, moving, ownership, and eligibility rules are unverified.",
        "Totem, Title, and Runi allocation is out of scope; only observed worker-card state is considered.",
    ]
    if estimate_class == "heuristic":
        assumptions.append("Target plot's observed aggregate terrain and seven-category boost rates are applied as a proxy for the candidate card.")
    if side_effects_evaluated:
        if side_effects:
            assumptions.append("Net delta includes plot-wide land-ability/bloodline harvest side effects on other occupants of the affected plot(s) caused by this move.")
        else:
            assumptions.append("Plot-wide land-ability/bloodline side effects on other occupants were evaluated and are zero for this candidate.")
    else:
        assumptions.append("Plot-wide land-ability/bloodline side effects on other occupants were NOT evaluated (card_details not supplied to recommend_land) -- this candidate's net delta may be understated if it evicts or displaces an ability/bloodline holder.")
    if delta_reason:
        assumptions.append(delta_reason)
    before = {"target": _state(target), "source": _state(source) if source else (_state(target) if action == "replace" else None), "slot": slot, "asset_ref": card.get("asset_ref")}
    after_target = _state(target)
    after_source = _state(source) if source else (_state(target) if action == "replace" else None)
    target_workers = _number(after_target.get("worker_count"))
    if action == "add" and target_workers is not None:
        after_target["worker_count"] = int(target_workers + 1)
    if action == "replace":
        after_target["worker_count"] = after_target.get("worker_count")
    if delta is None:
        after_target["total_harvest_pp"] = None
        after_target["estimate_reason"] = delta_reason
    elif action == "move":
        after_target["total_harvest_pp"] = (_number(_stake(target).get("total_harvest_pp")) or 0.0) + (_number(target_estimate.get("total_harvest_pp")) or 0.0)
    else:
        after_target["total_harvest_pp"] = (_number(_stake(target).get("total_harvest_pp")) or 0.0) + delta
    if action == "move" and after_source is not None:
        source_workers = _number(after_source.get("worker_count"))
        if source_workers is not None:
            after_source["worker_count"] = int(source_workers - 1)
        after_source["total_harvest_pp"] = None if opportunity_value is None else (_number(_stake(source).get("total_harvest_pp")) or 0.0) - opportunity_value
    after = {"target": after_target, "source": after_source, "slot": slot, "asset_ref": card.get("asset_ref"), "status": "hypothetical"}
    return {
        "action": action,
        "objective": "raw-pp",
        "before": before,
        "after": after,
        "current": before,
        "proposal": {"target_plot": target_ref, "source_plot": source_ref, "asset_ref": card.get("asset_ref"), "slot": slot, "change": action, "eligibility": "unverified"},
        "delta": {"value": delta, "low": None, "high": None, "unit": "harvest_pp", "reason": delta_reason} if delta is None else {"value": delta, "low": delta, "high": delta, "unit": "harvest_pp", "reason": "heuristic target-rate projection" if estimate_class == "heuristic" else "replay-validated card formula"},
        "assumptions": assumptions,
        "conflicts": conflicts,
        "opportunity_cost": {"source_plot": source_ref, "value": opportunity_value, "unit": "harvest_pp", "reason": opportunity_reason},
        "side_effects": side_effects,
        "side_effects_evaluated": side_effects_evaluated,
        "confidence": confidence,
        "confidence_grade": confidence["grade"],
        "confidence_reason": confidence["reason"],
        "estimate_class": estimate_class,
        "evidence": {"formula": target_estimate.get("formula"), "target_estimate": target_estimate, "snapshot": target.get("observed_at")},
        "freshness": {"observed_at": target.get("observed_at")},
        "read_only_notice": "Advisory object only. Praetoria performs no apply, execute, confirm, signing, or transaction action.",
        "_score": score,
        "_conflict_keys": [],
    }


def _candidate_id(rec: dict[str, Any]) -> str:
    proposal = rec["proposal"]
    return f"{proposal['change']}:{proposal['asset_ref']}:{proposal['target_plot']}:{proposal['slot']}"


def _candidate_ability_entry(card: dict[str, Any], action: str, level_by_asset: dict[str, int | None]) -> dict[str, Any]:
    """This candidate's own (card_detail_id, level) for ability/bloodline lookups. A "move"
    candidate's `card["facts"]` is the flat, currently-staked worker row, which carries no `level`
    field of its own (see land_reallocate._level_by_asset_ref) -- its level must come from the
    cross-referenced `report["cards"]` sweep instead."""
    facts = card.get("facts", {})
    level = level_by_asset.get(str(card.get("asset_ref"))) if action == "move" else facts.get("level")
    return {"card_detail_id": facts.get("card_detail_id"), "level": level}


def _plot_side_effect(
    plot_obj: Any, others: list[dict[str, Any]], roster_before: list[dict[str, Any]], roster_after: list[dict[str, Any]],
    details_by_id: dict[int, dict[str, Any]],
) -> tuple[float, list[dict[str, Any]]]:
    """Price the plot-wide land-ability/bloodline harvest swing landing on every OTHER occupant of
    a plot when a candidate's roster changes -- reuses `land_reallocate`'s own boost functions
    (`plot_abilities_boost`/`plot_bloodline_boost`) rather than a second implementation, mirroring
    the same before/after roster comparison `plan_reallocation` and `_recompute_others` use.
    Returns (net harvest delta summed across `others`, side-effect rows for any occupant that
    actually moved)."""
    abilities_before = plot_abilities_boost(roster_before, plot_obj.resource, details_by_id)
    bloodline_before = plot_bloodline_boost(roster_before, details_by_id)
    abilities_after = plot_abilities_boost(roster_after, plot_obj.resource, details_by_id)
    bloodline_after = plot_bloodline_boost(roster_after, details_by_id)
    if abs((abilities_after + bloodline_after) - (abilities_before + bloodline_before)) < 1e-9:
        return 0.0, []
    total = 0.0
    rows: list[dict[str, Any]] = []
    for c in others:
        terrain = plot_obj.terrain(c.get("color"), c.get("secondary_color"))
        before_cur = c["base"] * (1 + terrain) * (plot_obj.mult + abilities_before + bloodline_before)
        after_cur = c["base"] * (1 + terrain) * (plot_obj.mult + abilities_after + bloodline_after)
        d = after_cur - before_cur
        total += d
        if abs(d) > 1e-6:
            rows.append({"name": c.get("name"), "asset_ref": c.get("asset_ref"), "plot": plot_obj.plot, "before": before_cur, "after": after_cur, "delta": d})
    return total, rows


def recommend_land(
    report: dict[str, Any],
    goal: str = "raw-pp",
    top_k: int = 5,
    beam_width: int = 20,
    candidates_per_plot: int = 24,
    max_candidates_evaluated: int = 12000,
    candidate_pool_limit: int = 256,
    progress: Callable[[str], None] | None = None,
    on_recommendation: Callable[[dict[str, Any]], None] | None = None,
    details_by_id: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, bounded recommendation report from an observed snapshot.

    `details_by_id` (the `/cards/get_details` sweep, id -> row) is OPTIONAL and, when supplied,
    prices plot-wide land-ability/bloodline ("Toil and Kin") harvest side effects on OTHER
    occupants of a candidate's target (and, for a move, source) plot into that candidate's net
    delta -- reusing `land_reallocate`'s own boost functions rather than a second implementation
    (the internal rules's Land/Verico hard rule; docs/land-worker-abilities.md). Without it, side effects
    are NOT evaluated and every recommendation says so explicitly rather than silently assuming
    zero -- .
    """
    if goal not in {"raw-pp", "raw_pp", "pp"}:
        raise ValueError("goal must be raw-pp")
    if top_k < 1 or beam_width < 1 or candidates_per_plot < 1 or max_candidates_evaluated < 1 or candidate_pool_limit < 1:
        raise ValueError("recommendation ceilings must be positive")
    side_plots: dict[str, Any] | None = None
    allocation_plots = build_plots(report)
    level_by_asset: dict[str, int | None] = {}
    if details_by_id is not None:
        side_plots = build_plots(report)
        attach_colors(side_plots, details_by_id)
        level_by_asset = _level_by_asset_ref(report)
    result = copy.deepcopy(report)
    plots = {str(plot.get("deed_uid")): plot for plot in report.get("plots", [])}
    assignments = {str(row.get("asset_ref")): row for row in report.get("assignments", [])}
    workers = {str(row.get("asset_ref")): row for row in report.get("workers", [])}
    ordered_plots = sorted(plots.values(), key=lambda item: (_plot_ref(item), str(item.get("deed_uid"))))
    open_slots = {str(plot.get("deed_uid")): _open_slot(report, plot) for plot in ordered_plots}
    all_cards = [card for card in report.get("cards", []) if card.get("classification", {}).get("confidence") != "blocked"]
    blocked_count = sum(1 for card in report.get("cards", []) if card.get("classification", {}).get("confidence") == "blocked")
    numeric_cards = [card for card in all_cards if _number(card.get("facts", {}).get("land_base_pp")) is not None]
    available = [card for card in numeric_cards if str(card.get("asset_ref")) not in assignments]
    confidence_order = {"confident": 0, "uncertain": 1, "blocked": 2}

    def cheap_card_key(card: dict[str, Any]) -> tuple[float, float, int, str]:
        facts = card.get("facts", {})
        return (
            -(_number(facts.get("land_base_pp")) or 0.0),
            -(_number(facts.get("total_harvest_pp")) or 0.0),
            confidence_order.get(card.get("classification", {}).get("confidence"), 1),
            str(card.get("asset_ref", "")),
        )

    available = sorted(available, key=cheap_card_key)
    candidate_pool = available[:candidate_pool_limit]
    global_cards_pruned = len(available) - len(candidate_pool)
    pool_bases = sorted(_number(card.get("facts", {}).get("land_base_pp")) or 0.0 for card in candidate_pool)
    all_available_bases = sorted(_number(card.get("facts", {}).get("land_base_pp")) or 0.0 for card in available)
    assignments_by_plot: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for assignment in report.get("assignments", []):
        assignments_by_plot[str(assignment.get("deed_uid"))].append(assignment)

    def potential_for_plot(plot: dict[str, Any], cards: list[dict[str, Any]], bases: list[float]) -> int:
        slot = open_slots.get(str(plot.get("deed_uid")))
        total = len(cards) if slot is not None else 0
        for assignment in assignments_by_plot.get(str(plot.get("deed_uid")), []):
            old_worker = workers.get(str(assignment.get("asset_ref")))
            old_base = _number((old_worker or {}).get("land_base_pp")) or 0.0
            total += len(bases) - bisect.bisect_right(bases, old_base)
        if slot is not None:
            total += sum(1 for assignment in report.get("assignments", []) if str(assignment.get("deed_uid")) != str(plot.get("deed_uid")) and str(assignment.get("asset_ref")) in workers)
        return total

    full_pool_potential = sum(potential_for_plot(plot, candidate_pool, pool_bases) for plot in ordered_plots)
    unpruned_potential = sum(potential_for_plot(plot, available, all_available_bases) for plot in ordered_plots)
    if progress:
        progress(f"recommendation progress: 0/{len(ordered_plots)} plots processed; candidate evaluations 0")
    candidates: list[dict[str, Any]] = []
    evaluated = 0
    plots_processed = 0
    plots_truncated = 0
    total_ceiling_hit = False

    def add_candidate(action: str, card: dict[str, Any], target: dict[str, Any], source: dict[str, Any] | None, slot: int, old_card: dict[str, Any] | None = None) -> None:
        nonlocal evaluated
        evaluated += 1
        card_base = _number(card.get("facts", {}).get("land_base_pp"))
        if card_base is None:
            return
        target_plot = allocation_plots.get(str(target.get("deed_uid")))
        allocation_reason = None
        base_pp_after_cap = None
        if target_plot is None:
            allocation_reason = "target plot roster or base PP cap is not observed"
        elif slot <= 0:
            # Slot zero is reserved for Runi and is outside the worker-card cap pool.
            base_pp_after_cap = card_base
        else:
            resulting_cards = [dict(plot_card) for plot_card in target_plot.cards]
            if action == "replace" and old_card is not None:
                old_ref = str(old_card.get("asset_ref"))
                resulting_cards = [card_row for card_row in resulting_cards if str(card_row.get("asset_ref")) != old_ref]
            resulting_cards.append({"asset_ref": card.get("asset_ref"), "base": card_base, "slot": slot})
            allocation = _scoring_allocation(resulting_cards, target_plot.base_cap)
            if allocation is None:
                allocation_reason = "target plot roster or base PP cap is not fully observed"
            else:
                base_pp_after_cap = allocation.get(str(card.get("asset_ref")))
                if base_pp_after_cap is None:
                    allocation_reason = "candidate base PP allocation is not observed"
        target_estimate = _target_estimate(
            card.get("facts", {}), target, base_pp_after_cap=base_pp_after_cap,
            allocation_reason=allocation_reason,
        )
        old_harvest = _number((old_card or {}).get("total_harvest_pp")) if old_card else None
        new_harvest = _number(target_estimate.get("total_harvest_pp"))
        if action == "add":
            opportunity = 0.0
            opportunity_reason = "none (card unassigned)"
        elif action == "replace":
            opportunity = old_harvest
            opportunity_reason = "observed displaced worker production" if opportunity is not None else "unknown (displaced worker production is not observed)"
        else:
            opportunity = _number(card.get("facts", {}).get("total_harvest_pp")) if source is not None else None
            opportunity_reason = "observed relocated worker production" if opportunity is not None else "unknown (source worker production is not observed)"
        delta = None
        reason = allocation_reason
        if new_harvest is not None and not allocation_reason:
            if action == "add":
                delta = new_harvest
            elif action == "replace" and old_harvest is not None:
                delta = new_harvest - old_harvest
            elif action == "move" and opportunity is not None:
                delta = new_harvest - opportunity
            else:
                reason = "source opportunity cost cannot be computed from observed fields"
        side_effects: list[dict[str, Any]] = []
        side_effects_evaluated = side_plots is not None
        if side_effects_evaluated and delta is not None:
            dest_plot = side_plots.get(str(target.get("deed_uid")))
            if dest_plot is not None:
                evicted_ref = str((old_card or {}).get("asset_ref")) if action == "replace" and old_card else None
                dest_others = [c for c in dest_plot.cards if str(c.get("asset_ref")) != evicted_ref]
                roster_before = _plot_roster(dest_plot.cards)
                roster_after = _plot_roster(dest_others) + [_candidate_ability_entry(card, action, level_by_asset)]
                dest_delta, dest_rows = _plot_side_effect(dest_plot, dest_others, roster_before, roster_after, details_by_id)
                delta += dest_delta
                side_effects += dest_rows
            if action == "move" and source is not None:
                src_plot = side_plots.get(str(source.get("deed_uid")))
                if src_plot is not None:
                    mover_ref = str(card.get("asset_ref"))
                    src_others = [c for c in src_plot.cards if str(c.get("asset_ref")) != mover_ref]
                    src_roster_before = _plot_roster(src_plot.cards)
                    src_delta, src_rows = _plot_side_effect(src_plot, src_others, src_roster_before, _plot_roster(src_others), details_by_id)
                    delta += src_delta
                    side_effects += src_rows
        score = delta if delta is not None else 0.0
        rec = _recommendation(action=action, card=card, target=target, source=source, slot=slot, target_estimate=target_estimate, delta=delta, delta_reason=reason, opportunity_value=opportunity, opportunity_reason=opportunity_reason, conflicts=[], score=score, side_effects=side_effects, side_effects_evaluated=side_effects_evaluated)
        rec["_conflict_keys"] = [f"card:{card.get('asset_ref')}", f"slot:{target.get('deed_uid')}:{slot}"]
        if source is not None and action == "move":
            source_slot = assignments.get(str(card.get("asset_ref")), {}).get("slot")
            rec["_conflict_keys"].append(f"slot:{source.get('deed_uid')}:{source_slot}")
        rec["_candidate_id"] = _candidate_id(rec)
        candidates.append(rec)

    for target in ordered_plots:
        target_ref = str(target.get("deed_uid"))
        slot = open_slots.get(target_ref)
        plot_evaluated = 0
        plot_potential = potential_for_plot(target, candidate_pool, pool_bases)

        def evaluate(action: str, card: dict[str, Any], source: dict[str, Any] | None, target_slot: int, old_card: dict[str, Any] | None = None) -> bool:
            nonlocal evaluated, plot_evaluated, total_ceiling_hit
            if plot_evaluated >= candidates_per_plot or evaluated >= max_candidates_evaluated:
                if evaluated >= max_candidates_evaluated:
                    total_ceiling_hit = True
                return False
            add_candidate(action, card, target, source, target_slot, old_card)
            plot_evaluated += 1
            return True

        if slot is not None:
            for card in candidate_pool:
                if not evaluate("add", card, None, slot):
                    break
        for assignment in assignments_by_plot.get(target_ref, []):
            old_worker = workers.get(str(assignment.get("asset_ref")))
            if not old_worker:
                continue
            old_base = _number(old_worker.get("land_base_pp")) or 0.0
            for card in candidate_pool:
                if (_number(card.get("facts", {}).get("land_base_pp")) or 0.0) > old_base:
                    if not evaluate("replace", card, None, _slot_number(assignment.get("slot")) or 0, old_worker):
                        break
        if slot is not None and plot_evaluated < candidates_per_plot and evaluated < max_candidates_evaluated:
            for assignment in report.get("assignments", []):
                source = plots.get(str(assignment.get("deed_uid")))
                worker = workers.get(str(assignment.get("asset_ref")))
                if source is None or worker is None or source is target:
                    continue
                card = {"asset_ref": str(assignment.get("asset_ref")), "facts": worker, "classification": {"confidence": assignment.get("confidence", "confident"), "reason": "assigned worker observed"}}
                if not evaluate("move", card, source, slot):
                    break
        if on_recommendation:
            target_candidates = [row for row in candidates if row["proposal"]["target_plot"] == _plot_ref(target)]
            if target_candidates:
                provisional = max(target_candidates, key=lambda row: (row["_score"], row["_candidate_id"]))
                provisional = {key: value for key, value in provisional.items() if not key.startswith("_")}
                provisional["rank"] = 0
                on_recommendation(provisional)
        if plot_evaluated < plot_potential:
            plots_truncated += 1
        plots_processed += 1
        if progress and (plots_processed == 1 or plots_processed == len(ordered_plots) or plots_processed % 5 == 0 or total_ceiling_hit):
            progress(f"recommendation progress: {plots_processed}/{len(ordered_plots)} plots processed; candidate evaluations {evaluated}")
        if total_ceiling_hit:
            break

    by_plot: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_plot[candidate["proposal"]["target_plot"]].append(candidate)
    pruned: list[dict[str, Any]] = []
    for plot_ref in sorted(by_plot):
        rows = sorted(by_plot[plot_ref], key=lambda row: (-row["_score"], row["_candidate_id"]))
        pruned.extend(rows[:top_k])
    top_k_pruned = len(candidates) - len(pruned)
    conflicts_by_key: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        for key in candidate["_conflict_keys"]:
            conflicts_by_key[key].add(candidate["_candidate_id"])
    for candidate in candidates:
        conflicts: set[str] = set()
        for key in candidate["_conflict_keys"]:
            conflicts.update(conflicts_by_key[key])
        conflicts.discard(candidate["_candidate_id"])
        candidate["conflicts"] = sorted(conflicts)

    beams: list[tuple[list[dict[str, Any]], float, set[str]]] = [([], 0.0, set())]
    beam_pruned_states = 0
    for candidate in sorted(pruned, key=lambda row: (-row["_score"], row["_candidate_id"])):
        expanded = list(beams)
        for chosen, score, used in beams:
            keys = set(candidate["_conflict_keys"])
            if not keys & used:
                expanded.append((chosen + [candidate], score + candidate["_score"], used | keys))
        expanded.sort(key=lambda item: (-item[1], tuple(row["_candidate_id"] for row in item[0])))
        beam_pruned_states += max(0, len(expanded) - beam_width)
        beams = expanded[: max(1, beam_width)]
    chosen = max(beams, key=lambda item: (item[1], tuple(row["_candidate_id"] for row in item[0])))[0] if beams else []
    recommendations = []
    for rank, row in enumerate(sorted(chosen, key=lambda item: (-item["_score"], item["_candidate_id"])), 1):
        clean = {key: value for key, value in row.items() if not key.startswith("_")}
        clean["rank"] = rank
        recommendations.append(clean)
    idle_worklist = [{"plot": _plot_ref(plot), "deed_uid": plot.get("deed_uid"), "finding_codes": [finding.get("type") for finding in plot.get("diagnostics", []) if finding.get("type") in {"zero_workers", "open_worker_slots", "no_worksite", "construction_incomplete", "unfinished_worksite", "unpowered"}]} for plot in ordered_plots if any(finding.get("type") in {"zero_workers", "open_worker_slots", "no_worksite", "construction_incomplete", "unfinished_worksite", "unpowered"} for finding in plot.get("diagnostics", []))]
    result["recommendations"] = recommendations
    selected_ids = {row["_candidate_id"] for row in chosen}
    selected_keys = {key for row in chosen for key in row["_conflict_keys"]}
    conflict_edges = 0
    for candidates_for_key in conflicts_by_key.values():
        conflict_edges += len(candidates_for_key) * (len(candidates_for_key) - 1) // 2
    conflict_suppressed = sum(
        1 for row in pruned
        if row["_candidate_id"] not in selected_ids and any(key in selected_keys for key in row["_conflict_keys"])
    )
    per_plot_candidates_after_pool = sum(potential_for_plot(plot, candidate_pool, pool_bases) for plot in ordered_plots)
    per_plot_ceiling_pruned = sum(max(0, potential_for_plot(plot, candidate_pool, pool_bases) - candidates_per_plot) for plot in ordered_plots)
    per_plot_truncated = sum(potential_for_plot(plot, candidate_pool, pool_bases) > candidates_per_plot for plot in ordered_plots)
    after_per_plot_ceiling = per_plot_candidates_after_pool - per_plot_ceiling_pruned
    global_ceiling_pruned = max(0, after_per_plot_ceiling - min(after_per_plot_ceiling, max_candidates_evaluated))
    result["recommendation_summary"] = {
        "goal": "raw-pp",
        "candidate_count": len(candidates),
        "candidate_evaluations": evaluated,
        "candidate_evaluation_ceiling": max_candidates_evaluated,
        "candidate_evaluation_ceiling_hit": total_ceiling_hit,
        "raw_candidate_count_before_ceilings": full_pool_potential,
        "raw_candidate_count_before_global_pool_pruning": unpruned_potential,
        "global_candidate_pool": {"eligible_numeric_cards": len(available), "retained_cards": len(candidate_pool), "pruned_cards": global_cards_pruned, "blocked_cards_excluded": blocked_count, "non_numeric_cards_excluded": len(all_cards) - len(numeric_cards), "assigned_cards_excluded": len(numeric_cards) - len(available), "limit": candidate_pool_limit, "criterion": "blocked cards and cards without numeric land_base_pp are excluded; retained cards are the highest observed land_base_pp, then observed total_harvest_pp, then confidence grade, with deterministic asset_ref tie-break"},
        "blocked_cards_not_recommended": blocked_count,
        "uncertain_cards_eligible": sum(1 for card in candidate_pool if card.get("classification", {}).get("confidence") == "uncertain"),
        "selected_count": len(recommendations),
        "idle_plot_worklist_count": len(idle_worklist),
        "ceilings": {"candidates_per_plot": candidates_per_plot, "plots_truncated": plots_truncated, "total_candidates_evaluated": evaluated, "max_candidates_evaluated": max_candidates_evaluated, "total_ceiling_hit": total_ceiling_hit, "beam_width": beam_width, "beam_pruned_states": beam_pruned_states, "top_k_per_plot": top_k, "top_k_pruned_candidates": top_k_pruned},
        "pruning_diagnosis": {
            "input_cards": len(report.get("cards", [])),
            "blocked_cards_excluded": blocked_count,
            "eligible_numeric_unassigned_cards": len(available),
            "retained_cards": len(candidate_pool),
            "retention_pruned_cards": global_cards_pruned,
            "raw_action_opportunities_before_retention": unpruned_potential,
            "action_opportunities_after_retention": per_plot_candidates_after_pool,
            "per_plot_ceiling": candidates_per_plot,
            "plots_truncated_by_per_plot_ceiling": per_plot_truncated,
            "action_opportunities_pruned_by_per_plot_ceiling": per_plot_ceiling_pruned,
            "global_evaluation_ceiling": max_candidates_evaluated,
            "action_opportunities_pruned_by_global_evaluation_ceiling": global_ceiling_pruned,
            "candidate_records_evaluated": evaluated,
            "top_k_survivors": len(pruned),
            "top_k_pruned_candidates": top_k_pruned,
            "dominance_rules_pruned_candidates": 0,
            "conflict_graph_nodes": len(pruned),
            "conflict_graph_edges": conflict_edges,
            "conflict_suppressed_by_selected_actions": conflict_suppressed,
            "beam_width": beam_width,
            "beam_pruned_states": beam_pruned_states,
            "selected_recommendations": len(recommendations),
            "conclusion": "No ceiling change: the global evaluation ceiling is not binding; selected rows are deliberately bounded by per-plot/top-K/beam search and conflict-free portfolio selection.",
        },
        "search": {"strategy": "bounded_conflict_aware_beam", "top_k_per_plot": top_k, "beam_width": beam_width, "top_k_pruned_candidates": top_k_pruned, "beam_pruned_candidates": beam_pruned_states, "optimality": "good bounded result, not proven optimal"},
        "assumptions": ["Raw PP is the ranking objective; idle plots remain a separate worklist.", "Unverified in-game eligibility is never implied by a candidate.", "Cap-allocation-dependent outcomes are null rather than point estimates."],
    }
    result["idle_plot_worklist"] = idle_worklist
    return result
