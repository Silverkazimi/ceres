""" /  Fill plot, phase 2 (ready assets): propose the best fill of ONE plot from the account's
ready, unreserved assets, priced by the one shared roster evaluator (`stage_impact`).

Owner rulings (): the objective is the target plot's own final Total PP ("best for this plot"); the
default switches allow every asset class and beneficial replacement; cooling, staked-elsewhere, Survival
and rented-in assets are off (and not searched in this phase). The search is a deterministic, bounded
greedy: slot by slot, the top candidates by net gain are each priced as a complete roster and the best
kept. It is honest about being "best found", never claims exhaustiveness. Read-only: nothing is staged
here; the browser stages the accepted proposal through its ordinary stage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .board import load_details_by_id
from .candidates import (
    SPLINTER_NAMES,
    _plots_cached,
    _projected_worksite_row,
    _stage_asset_record,
    plot_removal_delta,
    power_satisfied,
    rank_candidates,
    stage_impact,
)
from .capacity import potential_worker_capacity, powered_worker_slots, structure_capacity_index
from praetoria.land_reallocate import _scoring_allocation

ALGORITHM = "greedy-full-roster-v1"
CANDIDATES_PER_SLOT = 12
ITEM_CANDIDATES = 6
ITEM_GROUPS = ("power_core", "runi", "totem", "title")
DEFAULT_SETTINGS = {
    "land_cards": True, "workers": True, "runis": True, "totems": True, "titles": True, "power_cores": True,
    "keep_current": False, "min_gain": 0.0,
    "terrain_bonus_only": False,  # only workers whose splinter this plot boosts
    "allow_cooling": False, "allow_staked": False, "allow_survival": False,
    "allow_rented": False,  # never offered; Fill does not use rented-in cards
}
# Base PP (raw) and Boostable PP (after the cap, with terrain) of a worker roster -- the plot view's own two figures.
def _roster_figures(plot, cards: list[dict[str, Any]]) -> tuple[float, float | None, dict[str, float]]:
    base_total = sum(_num(card.get("base")) for card in cards)
    allocation = _scoring_allocation(cards, plot.base_cap)
    if allocation is None:
        return base_total, None, {}
    boostable = sum(allocation[str(card["asset_ref"])] * (1 + plot.terrain(card.get("color") or "Gray", card.get("secondary_color"))) for card in cards)
    return base_total, boostable, {str(ref): float(value) for ref, value in allocation.items()}


_COLOR_BY_SPLINTER = {name: colour.capitalize() for colour, name in SPLINTER_NAMES.items()}
_SETTING_GROUP = {"runi": "runis", "totem": "totems", "title": "titles", "power_core": "power_cores"}


def clean_settings(raw: Any) -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        for key in out:
            if key == "min_gain":
                try:
                    out[key] = max(0.0, float(raw.get(key, out[key])))
                except (TypeError, ValueError):
                    pass
            elif key in raw and key != "allow_rented":
                out[key] = bool(raw[key])
    return out


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def candidate_rank_key(candidate: dict[str, Any], total: float, asset_ref: str) -> tuple[float, float, float, str, str]:
    """ (2026-09-14): highest priced total first, then base PP, then the plot's terrain
    figure for the card's splinter -- boost before neutral before penalty (his words) -- then
    name and asset ref so the order is deterministic."""
    return (total, _num(candidate.get("base_pp")), _num(candidate.get("terrain_boost")), str(candidate.get("name") or "").lower(), str(asset_ref))


def _priced(report, deed_uid, moves, details_by_id, plots) -> float | None:
    if not moves:
        return None
    try:
        return _num(stage_impact(report, deed_uid, moves, details_by_id=details_by_id, plots=plots).get("resulting_harvest"))
    except (KeyError, ValueError):
        return None


def _has_land_abilities(candidate: dict[str, Any]) -> bool:
    return bool(candidate.get("land_abilities"))


# what the plots a proposal takes cards FROM would lose -- each donor plot priced as one removal batch by the
# shared removal pricing (abilities and bloodlines included), never a sum of card figures.
def donor_losses(report: dict[str, Any], moves: list[dict[str, Any]], details_by_id: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for move in moves:
        source = move.get("source_deed_uid") or (move.get("candidate") or {}).get("source_deed_uid")
        if source and move.get("asset_ref"):
            by_source.setdefault(str(source), []).append(move)
    plot_number = {str(row.get("deed_uid")): (row.get("facts") or {}).get("plot") for row in report.get("plots", [])}
    out = []
    for source, taken in sorted(by_source.items()):
        try:
            delta = plot_removal_delta(report, source, [str(move["asset_ref"]) for move in taken], details_by_id=details_by_id)
            loss = _num(delta.get("harvest_pp_delta"))
            known = True
        except (KeyError, ValueError, LookupError):
            loss, known = 0.0, False
        out.append({"deed_uid": source, "plot": plot_number.get(source), "names": [move.get("name") or (move.get("candidate") or {}).get("name") for move in taken],
                    "asset_refs": [str(move["asset_ref"]) for move in taken], "harvest_pp_delta": loss, "known": known})
    return out


def _fill_report(
    report: dict[str, Any], deed_uid: str, staged_worksite_type: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
    plot_row = next((row for row in report.get("plots", []) if str(row.get("deed_uid")) == str(deed_uid)), None)
    if plot_row is None:
        return report, None, False
    diagnostics = {item.get("type") for item in plot_row.get("diagnostics", []) if isinstance(item, dict)}
    first_clear = "no_worksite" in diagnostics
    if not first_clear:
        return report, plot_row, False
    if staged_worksite_type:
        projected, target = _projected_worksite_row(plot_row, staged_worksite_type)
        projected = {**projected, "facts": {**(projected.get("facts") or {}), "resource": target}}
    else:
        projected = {
            **plot_row,
            "worksite_facts": {**(plot_row.get("worksite_facts") or {}), "project_type": "worksite_pending"},
        }
    plots = [projected if row is plot_row else row for row in report.get("plots", [])]
    return {**report, "plots": plots}, projected, True


# price one exact set of moves (the proposal minus what the maintainer left out) -- no search.
def price_fill(report: dict[str, Any], deed_uid: str, *, planned_moves: list[dict[str, Any]], moves: list[dict[str, Any]],
               details_by_id: dict[int, dict[str, Any]] | None = None,
               staged_worksite_type: str | None = None) -> dict[str, Any]:
    details_by_id = details_by_id if details_by_id is not None else load_details_by_id()
    report, _projected_row, _first_clear = _fill_report(report, deed_uid, staged_worksite_type)
    plots = _plots_cached(report, details_by_id)
    plot = plots.get(deed_uid)
    plot_row = next((row for row in report.get("plots", []) if str(row.get("deed_uid")) == str(deed_uid)), None)
    if plot is None or plot_row is None:
        raise KeyError(deed_uid)
    facts = plot_row.get("staking_facts") or {}
    current_total = _num(facts.get("total_harvest_pp"))
    planned = [dict(move, deed_uid=deed_uid) for move in planned_moves if isinstance(move, dict)]
    chosen = [dict(move, deed_uid=deed_uid) for move in moves if isinstance(move, dict)]
    planned_total = _priced(report, deed_uid, planned, details_by_id, plots)
    baseline = planned_total if planned_total is not None else current_total
    proposed_total = _priced(report, deed_uid, planned + chosen, details_by_id, plots)
    if proposed_total is None:
        if chosen:  # S10 / B5-05: a roster that cannot be priced is an error, never "no change"
            raise ValueError("the staged roster cannot be priced safely")
        proposed_total = baseline
    removed = {str(move.get("evict_asset_ref")) for move in planned + chosen if move.get("evict_asset_ref")}
    removed.update(str(move.get("asset_ref")) for move in planned if move.get("action") == "unstake")
    final_cards = [dict(card) for card in plot.cards if str(card.get("asset_ref")) not in removed]
    for move in planned + chosen:  # S5 / L3-02: staged incoming workers are part of the final roster, not only the new ones
        if move.get("group_id") != "worker" or move.get("action") == "unstake" or move.get("slot_index") is None:
            continue
        final_cards.append({"asset_ref": move["asset_ref"], "base": _incoming_base(move, report) or None, "slot": int(move["slot_index"]) + 1,
                            "color": _incoming_colors(move, report)[0], "secondary_color": _incoming_colors(move, report)[1]})
    base_after, boostable_after, after_cap = _roster_figures(plot, final_cards)
    cap = _num(plot.base_cap) or 100000.0
    donors = donor_losses(report, chosen, details_by_id)
    return {"deed_uid": deed_uid, "generated_at": report.get("generated_at"), "planned_total": baseline, "proposed_total": proposed_total,
            "gain": proposed_total - baseline, "base_after": base_after, "boostable_after": boostable_after, "after_cap": after_cap,
            "base_cap": cap, "cap_room": max(0.0, cap - base_after), "donors": donors}


def _incoming_colors(move: dict[str, Any], report: dict[str, Any] | None) -> tuple[str, str | None]:
    """ S10  / L3-03: terrain colours of an incoming worker, from the server's own card record (primary and secondary), never the client's."""
    record = (_stage_asset_record(report, str(move["asset_ref"])) if report is not None and move.get("asset_ref") is not None else None) or {}
    candidate = move.get("candidate") or {}
    primary = record.get("color") or _COLOR_BY_SPLINTER.get(str(candidate.get("splinter") or move.get("splinter") or "").lower()) or "Gray"
    return str(primary), record.get("secondary_color") or candidate.get("secondary_color")


def _incoming_base(move: dict[str, Any], report: dict[str, Any] | None = None) -> float:
    """Base PP a planned or chosen worker move brings onto the plot (0 for an unstake).

     S10 / L3-02: the browser's staged move carries no PP (the route keeps only group, slot, action and asset reference), so a staged
    worker is resolved from the SERVER's own card data by asset reference. A client-supplied figure is never trusted when the server knows the card.
    """
    if move.get("group_id") != "worker" or move.get("action") == "unstake":
        return 0.0
    if report is not None and move.get("asset_ref") is not None:
        record = _stage_asset_record(report, str(move["asset_ref"])) or {}
        for key in ("base_pp", "land_base_pp", "base"):
            value = _num(record.get(key))
            if value:
                return value
    return _num(move.get("base_pp")) or _num((move.get("candidate") or {}).get("base_pp")) or 0.0


def propose_fill(report: dict[str, Any], deed_uid: str, *, player: str, settings: dict[str, Any],
                 planned_moves: list[dict[str, Any]], now: datetime | None = None,
                 details_by_id: dict[int, dict[str, Any]] | None = None,
                 excluded_asset_refs: set[str] | None = None, hold_slots: set[tuple[str, int]] | None = None,
                 staged_worksite_type: str | None = None) -> dict[str, Any]:
    # an asset the maintainer clicked away is never proposed again, and its slot is held empty (not re-filled with
    # another card) until he brings it back.
    excluded_asset_refs = {str(ref) for ref in (excluded_asset_refs or set())}
    hold_slots = set(hold_slots or set())
    now = now or datetime.now(timezone.utc)
    details_by_id = details_by_id if details_by_id is not None else load_details_by_id()
    report, projected_row, first_clear = _fill_report(report, deed_uid, staged_worksite_type)
    plots = _plots_cached(report, details_by_id)
    plot = plots.get(deed_uid)
    plot_row = projected_row or next((row for row in report.get("plots", []) if str(row.get("deed_uid")) == str(deed_uid)), None)
    if plot is None or plot_row is None:
        raise KeyError(deed_uid)
    facts = plot_row.get("staking_facts") or {}
    current_total = _num(facts.get("total_harvest_pp"))
    planned = [dict(move, deed_uid=deed_uid) for move in planned_moves if isinstance(move, dict)]
    planned_total = _priced(report, deed_uid, planned, details_by_id, plots)
    baseline = planned_total if planned_total is not None else current_total

    capacity_index = structure_capacity_index(report)
    capacity = potential_worker_capacity(plot_row, capacity_index)
    powered_slots = powered_worker_slots(plot_row, capacity_index)
    occupied: dict[int, str | None] = {}  # 0-based slot -> asset_ref (None = empty by a planned unstake)
    for card in plot.cards:
        slot = card.get("slot")
        if isinstance(slot, int) and slot >= 1:
            occupied[slot - 1] = str(card.get("asset_ref"))
    for move in planned:
        if move.get("group_id") != "worker" or move.get("slot_index") is None:
            continue
        index = int(move["slot_index"])
        if move.get("action") == "unstake":
            occupied[index] = None
        else:
            occupied[index] = str(move.get("asset_ref"))
    reserved = {str(move.get("asset_ref")) for move in planned if move.get("asset_ref")}
    reserved.update(ref for ref in occupied.values() if ref)
    reserved.update(excluded_asset_refs)
    # (rule: Fill never suggests more raw Base PP than the plot cap): the raw Base PP of the final worker roster never exceeds
    # the plot's cap. Tracked here as the live roster (minus what is evicted or planned away) plus what is chosen.
    live_base_by_ref = {str(card.get("asset_ref")): _num(card.get("base")) for card in plot.cards}
    cap = _num(plot.base_cap) or 100000.0

    def raw_base_now() -> float:
        gone = {str(move.get("evict_asset_ref")) for move in planned if move.get("evict_asset_ref")}
        gone.update(str(move.get("asset_ref")) for move in planned if move.get("action") == "unstake")
        gone.update(str(move.get("evict_asset_ref")) for move in chosen if move.get("evict_asset_ref"))
        total = sum(base for ref, base in live_base_by_ref.items() if ref not in gone)
        total += sum(_num((move.get("candidate") or {}).get("base_pp")) for move in chosen if move["group_id"] == "worker")
        # S5 / L3-02: workers already staged (planned) on this plot count against the cap too
        total += sum(_incoming_base(move, report) for move in planned)
        return total

    chosen: list[dict[str, Any]] = []
    evaluated = 0
    pool_seen = 0
    notes: list[str] = []
    workers_on = settings["workers"] or settings["land_cards"]

    def current_moves() -> list[dict[str, Any]]:
        return planned + [{k: move[k] for k in ("deed_uid", "group_id", "slot_index", "action", "asset_ref", "evict_asset_ref")} for move in chosen]

    live_powered = power_satisfied(facts)

    def try_slot(group_id: str, slot_index: int, evict_ref: str | None, require_gain: bool = True) -> bool:
        nonlocal evaluated, pool_seen
        if (group_id, slot_index if group_id == "worker" else 0) in hold_slots:
            return False
        limit = CANDIDATES_PER_SLOT if group_id == "worker" else ITEM_CANDIDATES
        # On an unpowered plot every worker's net gain against the LIVE plot is 0 (nothing produces), so the
        # candidates are ordered by their own construction PP instead; the full-roster pricing below decides.
        # items carry no net-gain score, so that order was arbitrary (15 Rare Totems before the two Epic
        # ones, which never reached the search). Items are fetched by boost, highest first.
        sort = "boost" if group_id in {"totem", "title", "power_core"} else "net_gain" if live_powered or group_id != "worker" else "construction_pp"
        # the "also use" switches -- cooling cards (red, wait for their cooldown), cards on other plots (orange
        # unstake first), Survival-dead cards (red) and rented-in cards; each off by default.
        include_cooling = bool(settings["allow_cooling"] or settings["allow_survival"])
        bucket = "all" if settings["allow_staked"] else "idle"
        overlay = {
            "staged_moves": current_moves(), "staged_worksite_type": staged_worksite_type,
        } if first_clear else {}
        try:
            ranked = rank_candidates(
                report, deed_uid, group_id, player=player, now=now, sort=sort, limit=limit * 2, bucket=bucket,
                include_cooling=include_cooling, include_rented_in=bool(settings["allow_rented"]), include_terrain_penalised=False,
                slot=slot_index if group_id == "worker" else None, reserved_asset_refs=set(reserved), details_by_id=details_by_id,
                **overlay,
            )
        except (KeyError, ValueError, LookupError) as exc:
            notes.append(f"{group_id}: {exc}")
            return False
        pool_seen += int(ranked.get("total") or 0)
        pool = list(ranked.get("candidates", []))
        if group_id in {"totem", "title", "power_core"}:
            # one of each kind is enough -- fifteen identical Rare Totems price identically
            seen_kinds = set()
            kept = []
            for candidate in pool:
                kind = (candidate.get("item_detail_id"), candidate.get("name"))
                if kind in seen_kinds:
                    continue
                seen_kinds.add(kind)
                kept.append(candidate)
            pool = kept
        if group_id == "worker" and settings["land_cards"]:
            # a card with land abilities can raise the whole plot even when the Base PP cap is full, and
            # rarely tops the construction-PP order -- so the strongest ability holders are always tried too.
            try:
                land = rank_candidates(
                    report, deed_uid, group_id, player=player, now=now, sort=sort, limit=limit, bucket=bucket,
                    include_cooling=include_cooling, include_rented_in=bool(settings["allow_rented"]), include_terrain_penalised=False,
                    land_only=True, slot=slot_index, reserved_asset_refs=set(reserved), details_by_id=details_by_id,
                    **overlay,
                )
                seen = {str(c.get("asset_ref")) for c in pool}
                pool.extend(c for c in land.get("candidates", []) if str(c.get("asset_ref")) not in seen)
            except (KeyError, ValueError, LookupError):
                pass
        candidates = []
        for candidate in pool:
            if group_id == "worker":
                # the plot's own terrain figure for this card's splinter -- set here because an
                # unscored candidate carries none (the tile then showed as neutral) -- and a penalised splinter is
                # never proposed (maintainer rule).
                terrain = max(plot.terrain(_COLOR_BY_SPLINTER.get(str(splinter).lower(), "Gray")) for splinter in (candidate.get("splinters") or [candidate.get("splinter")]))
                candidate["terrain_boost"] = terrain
                if terrain < 0 or (settings["terrain_bonus_only"] and terrain <= 0):
                    continue
                land = _has_land_abilities(candidate)
                if land and not settings["land_cards"]:
                    continue
                if not land and not settings["workers"]:
                    continue
                headroom = cap - raw_base_now() + (live_base_by_ref.get(str(evict_ref), 0.0) if evict_ref else 0.0)
                if _num(candidate.get("base_pp")) > headroom + 1e-6:
                    continue  # would push the plot over its Base PP cap
            cooldown = candidate.get("cooldown") or {}
            if not cooldown.get("ready", False):
                state = cooldown.get("state")
                if state == "survival_locked":
                    if not settings["allow_survival"]:
                        continue
                elif state == "staked":
                    if not settings["allow_staked"]:
                        continue
                elif not settings["allow_cooling"]:
                    continue
            candidates.append(candidate)
            if len(candidates) >= limit * 2:
                break
        # the top candidates are the big cards; when none fits under the plot's cap any more, look for the
        # best card small enough for the room that is left rather than leaving the slot empty.
        if group_id == "worker" and not candidates:
            headroom = cap - raw_base_now() + (live_base_by_ref.get(str(evict_ref), 0.0) if evict_ref else 0.0)
            if headroom > 0:
                try:
                    small = rank_candidates(
                        report, deed_uid, group_id, player=player, now=now, sort="net_gain", limit=limit, bucket=bucket, max_pp=headroom,
                        include_cooling=include_cooling, include_rented_in=False, include_terrain_penalised=False,
                        slot=slot_index, reserved_asset_refs=set(reserved), details_by_id=details_by_id,
                        **overlay,
                    )
                    for candidate in small.get("candidates", []):
                        terrain = max(plot.terrain(_COLOR_BY_SPLINTER.get(str(splinter).lower(), "Gray")) for splinter in (candidate.get("splinters") or [candidate.get("splinter")]))
                        candidate["terrain_boost"] = terrain
                        cooldown = candidate.get("cooldown") or {}
                        if terrain < 0 or (settings["terrain_bonus_only"] and terrain <= 0) or _num(candidate.get("base_pp")) > headroom + 1e-6:
                            continue
                        if not cooldown.get("ready", False):
                            state = cooldown.get("state")
                            if (state == "survival_locked" and not settings["allow_survival"]) or (state == "staked" and not settings["allow_staked"]) or (state not in {"survival_locked", "staked"} and not settings["allow_cooling"]):
                                continue
                        candidates.append(candidate)
                except (KeyError, ValueError, LookupError):
                    pass
        base_total = _priced(report, deed_uid, current_moves(), details_by_id, plots)
        if base_total is None:
            base_total = baseline
        best = None
        for candidate in candidates:
            move = {
                "deed_uid": deed_uid, "group_id": group_id, "slot_index": slot_index,
                "action": "replace" if evict_ref else "stake",
                "asset_ref": str(candidate.get("asset_ref")), "evict_asset_ref": evict_ref,
            }
            total = _priced(report, deed_uid, current_moves() + [move], details_by_id, plots)
            evaluated += 1
            if total is None:
                continue
            key = candidate_rank_key(candidate, total, move["asset_ref"])
            if best is None or key > best[0]:
                best = (key, move, candidate, total)
        if best is None:
            return False
        key, move, candidate, total = best
        gain = total - base_total
        if require_gain and gain <= max(0.0, _num(settings["min_gain"])) + 1e-9:
            return False
        chosen.append({**move, "candidate": candidate, "name": candidate.get("name"), "gain": gain, "total_after": total})
        reserved.add(move["asset_ref"])
        return True

    # the raw capture carries no `bonus` on a plot row; staked items live in `bonus_assets` by deed (the
    # source stage_impact prices from). Every item slot looked empty before, and a filled one was "filled" again.
    staked_types = {str(item.get("stake_type_uid")) for item in report.get("bonus_assets", []) if str(item.get("stake_ref_uid")) == str(deed_uid)}
    live_item = {"runi": bool(facts.get("is_runi_staked")), "power_core": "STK-LND-PCR" in staked_types or bool(facts.get("is_power_core_staked")),
                 "totem": "STK-LND-TOT" in staked_types, "title": "STK-LND-TTL" in staked_types}
    # an unpowered plot produces nothing whatever workers it gets, so a power source comes FIRST -- a Power
    # Core if one is free, else a Runi -- even though on its own it shows no gain yet. Dropped again at the end if
    # no worker followed it.
    power_first = None
    first_clear_needs_core = first_clear and not live_item["power_core"] and not any(
        move.get("group_id") == "power_core" for move in planned
    )
    ordinary_plot_needs_power = not first_clear and not live_powered and not any(
        move.get("group_id") in {"power_core", "runi"} for move in planned
    )
    if first_clear_needs_core or ordinary_plot_needs_power:
        for group_id in (("power_core",) if first_clear else ("power_core", "runi")):
            if settings[_SETTING_GROUP[group_id]] and try_slot(group_id, 0, None, require_gain=False):
                power_first = group_id
                break
    if workers_on:
        slot_order = sorted(range(capacity), key=lambda slot_index: (slot_index >= powered_slots, slot_index))
        for slot_index in slot_order:
            if occupied.get(slot_index) is None:
                try_slot("worker", slot_index, None)
        if not settings["keep_current"]:
            for slot_index in slot_order:
                ref = occupied.get(slot_index)
                if ref and not any(move["group_id"] == "worker" and move["slot_index"] == slot_index for move in chosen):
                    try_slot("worker", slot_index, ref)
    # (maintainer preference): the proposed worker cards sit by Base PP, the largest in the lowest of the slots the
    # proposal fills. Same cards, same slots, same sum -- only their order changes; live cards keep their seats.
    worker_moves = [move for move in chosen if move["group_id"] == "worker"]
    if len(worker_moves) > 1:
        slots = sorted(((move["slot_index"], move.get("evict_asset_ref")) for move in worker_moves), key=lambda pair: pair[0])
        # the seat order uses the pick's own secondary ranking -- base PP, then terrain boost, then name, then asset ref.
        ordered = sorted(
            worker_moves,
            key=lambda move: (
                -_num((move.get("candidate") or {}).get("base_pp")),
                -_num((move.get("candidate") or {}).get("terrain_boost")),
                *candidate_rank_key(move.get("candidate") or {}, 0.0, move["asset_ref"])[3:],
            ),
        )
        for move, (slot_index, evict_ref) in zip(ordered, slots):
            move["slot_index"] = slot_index
            move["evict_asset_ref"] = evict_ref
            move["action"] = "replace" if evict_ref else "stake"
    live_item_ref = {}
    for item in report.get("bonus_assets", []):
        if str(item.get("stake_ref_uid")) == str(deed_uid):
            kind = {"STK-LND-TOT": "totem", "STK-LND-TTL": "title", "STK-LND-PCR": "power_core"}.get(str(item.get("stake_type_uid")))
            if kind:
                live_item_ref[kind] = str(item.get("asset_ref"))
    # a first clear may include both its required Power Core and a Runi in one stake change.
    for group_id in ITEM_GROUPS:
        if not settings[_SETTING_GROUP[group_id]]:
            continue
        planned_here = any(move.get("group_id") == group_id for move in planned)
        chosen_here = any(move.get("group_id") == group_id for move in chosen)
        if planned_here or chosen_here:
            continue
        if live_item[group_id]:
            # (plan phase 4): a stronger totem or title replaces a weaker live one -- only when it gains, never
            # with Keep current on. Runi and Power Core are not replaced (no boost to gain).
            if group_id in {"totem", "title"} and not settings["keep_current"] and live_item_ref.get(group_id):
                try_slot(group_id, 0, live_item_ref[group_id])
            continue
        try_slot(group_id, 0, None)
    if power_first and not any(move["group_id"] == "worker" for move in chosen):
        chosen[:] = [move for move in chosen if move["group_id"] != power_first]  # a power source alone changes nothing

    # what the account has to stake for each item slot, so an empty tile can say "unavailable".
    item_available = {}
    for group_id in ITEM_GROUPS:
        try:
            ranked = rank_candidates(report, deed_uid, group_id, player=player, now=now, sort="name", limit=1, bucket="all" if settings["allow_staked"] else "idle",
                                     include_cooling=bool(settings["allow_cooling"] or settings["allow_survival"]), include_rented_in=False,
                                     include_terrain_penalised=True, details_by_id=details_by_id)
            item_available[group_id] = int(ranked.get("total") or 0)
        except (KeyError, ValueError, LookupError):
            item_available[group_id] = None
    proposed_total = _priced(report, deed_uid, current_moves(), details_by_id, plots)
    if proposed_total is None:
        proposed_total = baseline
    empty_left = sum(1 for slot_index in range(capacity)
                     if occupied.get(slot_index) is None and not any(m["group_id"] == "worker" and m["slot_index"] == slot_index for m in chosen))
    # The final worker roster: live cards minus the ones evicted or planned away, plus every planned and proposed card.
    removed = {str(move.get("evict_asset_ref")) for move in planned + chosen if move.get("evict_asset_ref")}
    removed.update(str(move.get("asset_ref")) for move in planned if move.get("action") == "unstake")
    final_cards = [dict(card) for card in plot.cards if str(card.get("asset_ref")) not in removed]
    for move in chosen:
        if move["group_id"] != "worker":
            continue
        candidate = move.get("candidate") or {}
        final_cards.append({"asset_ref": move["asset_ref"], "base": _num(candidate.get("base_pp")), "slot": int(move["slot_index"]) + 1,
                            "color": _COLOR_BY_SPLINTER.get(str(candidate.get("splinter") or "").lower(), "Gray")})
    for move in planned:  # S10 round 2 / L3-02: staged incoming workers are part of the displayed final roster too
        if move.get("group_id") != "worker" or move.get("action") == "unstake" or move.get("slot_index") is None:
            continue
        final_cards.append({"asset_ref": move.get("asset_ref"), "base": _incoming_base(move, report) or None, "slot": int(move["slot_index"]) + 1,
                            "color": _incoming_colors(move, report)[0], "secondary_color": _incoming_colors(move, report)[1]})
    base_now, boostable_now, _now_alloc = _roster_figures(plot, [dict(card) for card in plot.cards])
    base_after, boostable_after, after_cap = _roster_figures(plot, final_cards)
    return {
        "base_now": base_now, "base_after": base_after, "boostable_now": boostable_now, "boostable_after": boostable_after,
        "base_cap": cap, "after_cap": after_cap, "item_available": item_available,
        "donors": donor_losses(report, chosen, details_by_id),
        "cap_reached": bool(empty_left) and base_after >= _num(plot.base_cap) - 1e-6,
        "cap_room": max(0.0, _num(plot.base_cap) - base_after),
        "deed_uid": deed_uid, "generated_at": report.get("generated_at"), "algorithm": ALGORITHM,
        "settings": settings, "current_total": current_total, "planned_total": baseline, "proposed_total": proposed_total,
        "gain": proposed_total - baseline, "moves": chosen, "empty_slots_left": empty_left, "worker_capacity": capacity,
        "worksite_prompt": "Choose a worksite to start the build" if first_clear and not staged_worksite_type else None,
        "search": {"evaluated": evaluated, "pool_seen": pool_seen, "candidates_per_slot": CANDIDATES_PER_SLOT, "exact": False},
        "notes": notes,
    }
