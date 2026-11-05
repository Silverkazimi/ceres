"""Pure projection for  Phase 1 -- the read-only worksite-change modal.

Every function here derives from fields `ceres/board.py` already reads off a
Praetoria plot record; no network I/O, no state, nothing broadcast.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import resources as _res

TC_COST = 50  # flat cost to convert an EXISTING worksite; a fresh build is PP-scaled and out of scope
CHANGE_HOURS = 24

# a FIRST clear (a never-cleared plot getting its first worksite) is a construction project gated on
# base PP only -- required production by rarity x land type (library/land.md, GitBook 1.5 + 2.0 table).
CLEARING_PP = {
    "common": {"natural": 1_500_000, "magical": 4_500_000, "occupied": 9_000_000},
    "rare": {"natural": 1_650_000, "magical": 4_950_000, "occupied": 9_900_000},
    "epic": {"natural": 2_100_000, "magical": 6_300_000, "occupied": 12_600_000},
    "legendary": {"natural": 3_000_000, "magical": 9_000_000, "occupied": 18_000_000},
}


def clearing_pp_required(rarity: Any, status: Any) -> int | None:
    """Required base PP to clear a plot for its first worksite; None when rarity/land type is unknown."""
    return CLEARING_PP.get(str(rarity or "").strip().lower(), {}).get(str(status or "").strip().lower())

# Every terrain supports exactly one natural resource; the three higher-tier
# worksites are terrain-free. Source: support.splinterlands.com's per-resource FAQs
# (2026-08-26). The four natural-resource rows partition all 14 live terrains
# exactly (4+4+3+3) -- this table, never observed distribution, is the only
# legitimate source of legality: a grain farm observed on a non-grain terrain is
# pre-1.75 legacy state the game auto-migrates after the first harvest, not a
# second legal grain terrain.
# the four tables below are views of the resource registry (ceres/resources.py), same values as before.
# plains/river/bog/lake -> grain; swamp/forest/tundra/jungle -> wood; desert/canyon/hills -> stone
# mountain/badlands/caldera -> iron.
_NATURAL_TERRAIN_RESOURCE = _res.natural_terrain_resource()

# Per-PP-per-hour rate, primary-sourced from support.splinterlands.com's per-resource
# FAQs (2026-08-26). SPS deliberately has no entry -- it is a pool-share model with no
# fixed divisor (see `projected_rate` below), never a fixed rate.
# grain 0.02, wood 0.005, stone 0.002, iron 0.0005, aura 0.0005, research 0.0001.
_RATE_PER_PP = _res.rates_per_pp()

# grain Grain Farm, wood Logging Camp, stone Quarry, iron Ore Mine, research Research Hut, sps Shard Mine, aura Aura Lab.
WORKSITE_NAME = _res.worksite_names()

ALL_TARGETS = frozenset(WORKSITE_NAME)

_KINGDOM_PROJECT_TYPES = {"castle_operation", "keep_operation"}


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def construction_target(project_type: Any) -> str | None:
    """The worksite target named by a `worksite_<x>_construction` project type, else None.

     (Land 2.0 readiness): only that exact shape names a worksite. Any other project type ending in
    `_construction` -- a Land 2.0 building such as `building_sawmill_construction` -- used to be sliced into a
    fake target ("ng_sawmill"); now it yields None, and the screens show their generic text ("the worksite",
    "a new worksite") exactly as they already do for a construction with no named target."""
    text = str(project_type or "")
    if not (text.startswith("worksite_") and text.endswith("_construction")):
        return None
    return text[len("worksite_"):-len("_construction")] or None


def resource_key(raw: Any) -> str | None:
    """Normalise a `facts.resource` value ('GRAIN', 'grain', ...) to one of this
    module's seven lowercase target keys, or None if unrecognised."""
    if not raw:
        return None
    key = str(raw).strip().lower()
    return key if key in ALL_TARGETS else None


def _time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _elapsed_progress(worksite_facts: dict[str, Any]) -> float | None:
    remaining = _num(worksite_facts.get("hours_to_completion"))
    projected_hours = _num(worksite_facts.get("projected_hours"))
    start = _time(worksite_facts.get("start_date"))
    end = _time(worksite_facts.get("projected_end"))
    if projected_hours is None and start is not None and end is not None:
        projected_hours = (end - start).total_seconds() / 3600
    if remaining is not None and projected_hours and projected_hours > 0:
        progress = 1.0 - (remaining / projected_hours)
    elif start is not None and end is not None and end > start:
        elapsed_hours = (datetime.now(timezone.utc) - start).total_seconds() / 3600
        progress = elapsed_hours / ((end - start).total_seconds() / 3600)
    else:
        return None
    return max(0.0, min(1.0, progress))


def legal_targets(terrain: Any, current_resource: Any = None) -> list[str] | None:
    """The four legal worksite targets for `terrain`: its native resource plus
    Research Hut, Shard Mine and Aura Lab. Returns None for an unrecognised
    terrain -- never a partial or guessed list.

    `current_resource` remains accepted for callers that already provide it;
    legality is independent of the observed current worksite. A known current
    worksite outside this four-target set is added separately by
    `build_swap_targets` so it can remain visibly current without displacing
    the terrain-native target."""
    native = _NATURAL_TERRAIN_RESOURCE.get(str(terrain or "").strip().lower())
    if native is None:
        return None
    return [native, "research", "sps", "aura"]


_NATURAL_TARGETS = frozenset(_NATURAL_TERRAIN_RESOURCE.values())  # {"grain", "wood", "stone", "iron"}


def input_requirements(target: str) -> list[str]:
    """What a worksite consumes to harvest. The four natural worksites need grain
    only; Research Hut, Shard Mine and Aura Lab all need grain, wood, stone AND iron
    (support.splinterlands.com's Research/Mining-SPS/Aura-Lab FAQs) -- a structural
    change in what the plot costs to run, invisible in a rate number alone."""
    return ["grain"] if target in _NATURAL_TARGETS else ["grain", "wood", "stone", "iron"]


def deed_status_token_boost_for(target: str, status: Any) -> float:
    """`deed_status_token_boost` as it would read AFTER a change to `target`. Reads 1
    iff Research Hut on a `magical` plot, Shard Mine on an `occupied` plot, or Aura
    Lab on a `magical` OR `occupied` plot; 0 otherwise. Primary-sourced three ways
    and measured across 6,603 worksited plots with zero exceptions -- see the plan's
    §3.5. This is what makes `total_harvest_pp` NOT swap-invariant on those plots."""
    s = str(status or "").lower()
    if target == "research" and s == "magical":
        return 1.0
    if target == "sps" and s == "occupied":
        return 1.0
    if target == "aura" and s in ("magical", "occupied"):
        return 1.0
    return 0.0


def projected_rate(
    target: str, *, total_construction_pp: Any, total_boost: Any, deed_status_token_boost: Any, status: Any,
) -> dict[str, Any]:
    """Absolute projected `work_per_hour` for `target` on THIS plot -- the plan's
    §3.5 dstb-corrected formula, never a delta (a change alters WHICH resource is
    produced, so a delta would compare grain/hour against stone/hour, meaningless).

    Every branch also returns `projected_harvest_pp` -- the dstb-corrected
    `total_harvest_pp` this target would carry -- and `dstb_delta` (target dstb
    minus current dstb, in {-1, 0, 1}), so a caller can explain a jump/drop in
    the hero number instead of leaving it unexplained. Neither was returned
    before this was added even though both were already computed internally
    and thrown away.

    SPS is the documented exception: it is a pool-share model against a monthly
    pool, not a fixed per-PP divisor, so no per-plot conversion from PP to a network
    share exists or is documented. Per the standing rule that an underivable
    quantity emits a range or an explicit null, never a fabricated point estimate,
    the SPS tile carries `value: None` (not a guessed number) alongside a
    `worksite_swap_sps_pool_share` advisory. `projected_harvest_pp` is still real
    for SPS -- it is the plot's projected MINING PP, the one quantity that
    actually determines its pool share, so it becomes that tile's hero number."""
    tcp = _num(total_construction_pp) or 0.0
    tb = _num(total_boost) or 0.0
    current_dstb = _num(deed_status_token_boost) or 0.0
    target_dstb = deed_status_token_boost_for(target, status)
    harvest_pp = tcp * (1.0 + tb - current_dstb + target_dstb)
    dstb_delta = target_dstb - current_dstb
    if target == "sps":
        return {
            "kind": "pool_share", "value": None, "low": None, "high": None, "unit": "SPS/hr",
            "projected_harvest_pp": harvest_pp, "dstb_delta": dstb_delta,
        }
    return {
        "kind": "point", "value": harvest_pp * _RATE_PER_PP[target], "low": None, "high": None, "unit": f"{target}/hr",
        "projected_harvest_pp": harvest_pp, "dstb_delta": dstb_delta,
    }


def build_swap_targets(
    *, terrain: Any, current_resource: Any, status: Any, total_construction_pp: Any, total_boost: Any,
    deed_status_token_boost: Any, total_work_per_hour: Any,
    card_abilities_boost: Any = None, resource_ability_rates: dict[str, tuple[float, str]] | None = None,
    efficiency: Any = None, first_clear: bool = False, rarity: Any = None,
) -> list[dict[str, Any]] | None:
    """Build the four legal targets plus a separate current tile when needed.
    `efficiency` (): the region's DEC efficiency; a projected rate is scaled by it (None: no scaling).
    The current worksite is pinned first and flagged `is_current` -- not
    selectable, with no time/TC cost, and its rate read directly from
    `staking_facts.total_work_per_hour` rather than re-projected. Returns None
    if `terrain` is unrecognised."""
    targets = legal_targets(terrain, current_resource)
    if targets is None:
        return None
    current_key = resource_key(current_resource)
    if current_key is not None and current_key not in targets:
        targets = [current_key, *targets]
    tiles = []
    for target in targets:
        is_current = target == current_key
        tile: dict[str, Any] = {
            "target": target,
            "worksite_name": WORKSITE_NAME[target],
            "is_current": is_current,
            "needs_inputs": input_requirements(target),
            "requires_permit": target == "aura",
        }
        # the +100% land-type bonus this worksite gets on THIS plot (Shard Mine on
        # occupied, Aura Lab on magical/occupied, Research Hut on magical) -- the same rule
        # projected_rate already counts; the modal shows it as a badge on the tile.
        if deed_status_token_boost_for(target, status):
            tile["land_bonus"] = str(status).lower()
        # resource-match worker abilities (Bountiful X, e.g. Undying Miner's AURA 1)
        # are plot-wide but apply only while the worksite produces that resource -- the
        # strongest matching instance wins. `resource_ability_rates` is None when the
        # plot's own abilities could not be read; the projection then keeps today's boost.
        ability = (resource_ability_rates or {}).get(target.upper())
        if ability:
            tile["ability_bonus"] = {"rate": ability[0], "holder": ability[1]}
        if is_current:
            tile["rate"] = {"kind": "point", "value": _num(total_work_per_hour), "low": None, "high": None, "unit": f"{target}/hr"}
            tile["time_hours"] = None
            tile["tc_cost"] = None
        else:
            target_boost = total_boost
            if resource_ability_rates is not None:
                target_boost = (_num(total_boost) or 0.0) - (_num(card_abilities_boost) or 0.0) + (ability[0] if ability else 0.0)
            tile["rate"] = projected_rate(
                target, total_construction_pp=total_construction_pp, total_boost=target_boost,
                deed_status_token_boost=deed_status_token_boost, status=status,
            )
            if tile["rate"].get("kind") == "point" and _num(efficiency) is not None and _num(efficiency) < 1:
                tile["rate"]["value"] = tile["rate"]["value"] * _num(efficiency)
            tile["time_hours"] = CHANGE_HOURS
            tile["tc_cost"] = TC_COST
            if first_clear:
                # PP-scaled, not the 24 h change -- required PP / the workers' base PP per hour
                # (total_construction_pp excludes production boosts); no Time Crystal shortcut.
                required = clearing_pp_required(rarity, status)
                base = _num(total_construction_pp) or 0.0
                tile["first_clear"] = True
                tile["clear_pp_required"] = required
                tile["time_hours"] = (required / base) if required and base > 0 else None
                tile["tc_cost"] = None
        tiles.append(tile)
    # Invariant: current tile is pinned first; selectable tiles keep legal-target order.
    tiles.sort(key=lambda t: (not t["is_current"]))
    return tiles


def swap_control_available(*, plot_status: Any, diagnostics: list[str] | None, project_type: Any, ready: bool = False, allow_first_clear: bool = False) -> bool:
    """Whether the swap hit-button/modal should render at all. Suppressed for
    Keep/Castle plots (no worker task to change), a never-cleared plot
    (`no_worksite`), and a plot already mid-build/mid-change
    (`construction_incomplete`/`unfinished_worksite`, or a `project_type` still
    ending in `_construction`) -- a second change can't stack on an unfinished one."""
    if str(plot_status or "").lower() == "kingdom":
        return False
    if str(project_type or "") in _KINGDOM_PROJECT_TYPES:
        return False
    diags = set(diagnostics or [])
    if "no_worksite" in diags:
        # (his decision 2026-09-14): a never-cleared plot may open the picker to START its first
        # clear -- only when the caller opts in, and never while a clear is already under way.
        return allow_first_clear and not str(project_type or "").endswith("_construction")
    if not ready and any(code in diags for code in ("construction_incomplete", "unfinished_worksite")):
        return False
    if str(project_type or "").endswith("_construction"):
        return ready
    return True


def ready_worksite_change(
    worksite_facts: dict[str, Any] | None, total_work_per_hour: Any,
) -> dict[str, Any] | None:
    """Returns the server-observed completion state for a finished construction.

    A ready construction has a past projected end and a positive current production rate.
    `projected_end` is authoritative here: `hours_to_completion` can be stale in a captured
    board row. The Grain amount is copied from the live project row; it is never inferred
    from PP or from the player's wallet balance.
    """
    wf = worksite_facts or {}
    project_type = str(wf.get("project_type") or "")
    if not project_type.endswith("_construction") or (_num(total_work_per_hour) or 0) <= 0:
        return None
    end = _time(wf.get("projected_end"))
    grain = _num(wf.get("grain_required"))
    if end is None or end > datetime.now(timezone.utc) or grain is None or grain < 0:
        return None
    to_resource = construction_target(project_type)
    return {
        "kind": "worksite_completion",
        "project_id": wf.get("project_id", wf.get("id")),
        "to": to_resource,
        "to_label": WORKSITE_NAME.get(to_resource),
        "grain_cost": int(grain) if grain.is_integer() else grain,
        "ready_at": wf.get("projected_end"),
    }


def swap_eligibility(*, is_powered: Any, worker_count: Any, first_clear: bool = False) -> dict[str, Any]:
    """H1 -- a change on a zero-PP plot is a hard REFUSAL, not a warning:
    construction is funded by the plot's own production, so a plot producing zero PP
    can never complete a change (two real plots have been stuck since January 2024).
    Derived server-side from fields already on the row; states BOTH cause and
    remedy, per the standing rule that a refusal with no stated reason is a failure
    mode this project has already been bitten by. Recomputed on every board build,
    so a plot that becomes powered/staffed clears the refusal on the next build with
    no restart."""
    count = _num(worker_count) or 0
    if is_powered is False:
        return {
            "eligible": False,
            "reason": "This plot has no power source, so it produces zero PP and could never fund a worksite change.",
            "remedy": "Add a Power Core, ENERGIZED, or a staked Runi, then try again.",
        }
    # a FIRST clear costs nothing and
    # simply progresses once workers are seated, so it needs power only; a change of an existing worksite still needs
    # a worker (it is funded by the plot's own production).
    if count == 0 and not first_clear:
        return {
            "eligible": False,
            "reason": "This plot has no workers seated, so it produces zero PP and could never fund a worksite change.",
            "remedy": "Seat at least one worker, then try again.",
        }
    return {"eligible": True, "reason": None, "remedy": None}


def build_pending_change(
    deed_uid: str | None, worksite_facts: dict[str, Any] | None, total_work_per_hour: Any, from_resource: Any,
) -> dict[str, Any] | None:
    """The in-flight worksite change as a structured, backend-owned shape (§7),
    reusable by  without rework. None when nothing is in flight.

    `stalled` reads off the plot's OWN current production rate, not
    `worksite_facts` -- a zero-PP plot's `projected_end` is a year-116104 overflow
    (dividing by a zero rate), and rendering that date verbatim is the bug this
    exists to pre-empt. When stalled, `ready_at` is None -- "stalled", never a
    countdown to an overflowed date."""
    wf = worksite_facts or {}
    project_type = str(wf.get("project_type") or "")
    if not project_type.endswith("_construction"):
        return None
    to_resource = construction_target(project_type)
    rate = _num(total_work_per_hour)
    stalled = not rate or rate <= 0.0
    # A stalled plot never receives wall-clock progress because zero rate cannot complete it.
    progress_pct = None if stalled else _elapsed_progress(wf)
    return {
        "deed_uid": deed_uid,
        "kind": "worksite_change",
        "project_id": wf.get("project_id", wf.get("id")),
        "from": from_resource,
        "to": to_resource,
        # Human-readable names, backend-owned (this module's own WORKSITE_NAME table)
        # so a row/receipt renderer never needs its own copy of the resource->name
        # mapping -- Phase 0's row chip and modal copy both read these rather
        # than re-deriving "Quarry" from "stone" client-side.
        "to_label": WORKSITE_NAME.get(to_resource) if to_resource else None,
        "from_label": WORKSITE_NAME.get(resource_key(from_resource)) if from_resource else None,
        "started_at": wf.get("start_date"),
        "ready_at": None if stalled else wf.get("projected_end"),
        "progress_pct": progress_pct,
        "pp_required": _num(wf.get("pp_required")),
        "pp_spent": _num(wf.get("pp_spent")),
        "hours_to_completion": _num(wf.get("hours_to_completion")),
        "projected_end": wf.get("projected_end"),
        "stalled": stalled,
    }
