"""Pure projection: a Praetoria land-analysis report -> the `ceres.board.v1` board.

No network I/O, no state -- `project_board(report, details_by_id)` is a pure function
of its two inputs, per the plan's §6.3/§6.4. `details_by_id` is the `/cards/get_details`
lookup (id -> detail dict); Ceres reads it from Praetoria's own on-disk cache, offline
only -- see `load_details_by_id` below.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping

from praetoria.cache import JsonCache
from praetoria.errors import CacheError, SchemaError
from praetoria.sets import is_land_or_verico

from . import SCHEMA_BOARD
from .bcx_cap import resolve_bcx_max
from .harvest import hourly_resource_costs
from .level_cap import resolve_level_thresholds
from .capacity import (
    _structure_identity,
    potential_worker_capacity,
    powered_worker_capacity,
    powered_worker_slots,
    structure_capacity_index,
)
from .metrics import (
    ability_codes,
    annotate_land_abilities,
    classify_idle_availability,
    dec_for_plot,
    land_ability_row,
    mark_bloodline_applied,
    plot_dec_efficiency,
    sole_holder_for,
)
from .notices import BADGE_ORDER, badge_meta_projection, badge_rank, format_notice, notice_catalog, notices_for_badges
from .worksite_swap import build_pending_change, build_swap_targets, ready_worksite_change, swap_control_available, swap_eligibility
from .card_pool_index import get_card_pool_index
from .worker_upgrade import (
    WorkerUpgradeOptionsError,
    _coverage,
    _facts,
    _identity as _upgrade_identity,
    project_worker_upgrade_options as _project_worker_upgrade_options,
)

logger = logging.getLogger("ceres.board")



# a "PARTIAL DATA: deed X: reason; deed Y: reason; ..." warning listed every failed plot (hundreds on a
# large account) and buried the board under a wall of text. The page notice now says how many plots and why, in
# one line; the full list stays in `warnings` for diagnosis.
def _condense_warning(warning: str) -> str:
    prefix = "PARTIAL DATA: "
    if not warning.startswith(prefix) or "; deed " not in warning:
        return warning
    parts = [part.strip() for part in warning[len(prefix):].split("; ") if part.strip()]
    reasons: dict[str, int] = {}
    for part in parts:
        reason = part.split(": ", 1)[1] if part.startswith("deed ") and ": " in part else part
        reasons[reason] = reasons.get(reason, 0) + 1
    summary = "; ".join(f"{count} plot{'s' if count != 1 else ''} — {reason}" for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]))
    return f"PARTIAL DATA: {len(parts)} plot reads incomplete ({summary})."

def project_worker_upgrade_options(
    report: Mapping[str, Any],
    deed_uid: str,
    asset_ref: str,
    details_by_id: Mapping[Any, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(report.get("cards"), list):
        from .capture import load_card_pool
        try:
            report = load_card_pool(dict(report))
        except LookupError as exc:
            raise WorkerUpgradeOptionsError("collection_incomplete", str(exc)) from exc
    _coverage(report)
    workers = report.get("workers")
    cards = report.get("cards")
    if not isinstance(workers, list) or not isinstance(cards, list):
        raise WorkerUpgradeOptionsError("snapshot_incomplete", "the snapshot is missing worker or collection rows")
    worker_rows = [_facts(row) for row in workers]
    current_worker_rows = [
        row for row in worker_rows
        if row and row.get("asset_ref") == asset_ref and row.get("stake_ref_uid") == deed_uid
    ]
    if not current_worker_rows:
        raise WorkerUpgradeOptionsError("worker_not_found", "the requested staked worker was not found on this plot")
    if len(current_worker_rows) > 1:
        raise WorkerUpgradeOptionsError("worker_ambiguous", "the requested asset_ref identifies more than one staked worker")
    if current_worker_rows[0].get("stake_type_uid") not in (None, "STK-LND-WKR"):
        raise WorkerUpgradeOptionsError("worker_wrong_class", "only a worker card can enter an upgrade cycle")
    index = get_card_pool_index(report)
    if index.invalid_count:
        raise WorkerUpgradeOptionsError("collection_incomplete", "the snapshot contains an incomplete card record")
    current_rows = index.by_asset_ref.get(asset_ref, [])
    relevant_cards = list(current_rows)
    if current_rows:
        current_key = _upgrade_identity(current_rows[0])
        if current_key is not None:
            relevant_cards = list(index.matching(current_key))
    indexed_report = dict(report)
    indexed_report["cards"] = relevant_cards
    return _project_worker_upgrade_options(indexed_report, deed_uid, asset_ref, details_by_id)

BIOME_COLORS = ("red", "blue", "green", "white", "black", "gold")

SORTABLE = [
    "harvest_pp", "base_pp", "construction_pp", "work_per_hour", "work_per_hour_unscaled", "region_efficiency", "boost", "multiplier",
    "dec_needed", "dec_shortfall", "plot_dec_efficiency", "food_per_hour",
    # the three material-input columns, always numeric/None -- see wood_cost_per_hour etc.
    "wood_cost_per_hour", "stone_cost_per_hour", "iron_cost_per_hour",
    "totem_boost", "title_boost", "open_slots", "worker_count", "rarity_rank", "plot_status_rank", "terrain", "resource", "lock_state",
]

BADGE_META = badge_meta_projection()

# Mythic ranks ABOVE legendary ( board-fix batch: mythic is rarer than
# legendary -- observed live on player0001's 2 kingdom/Keep-Castle plots, absent from
# player0005's collection). No mythic rarity gem asset exists on the CDN (checked
# common/rare/epic/legendary is the full gem set), so mythic renders text-only in
# the UI (see app.js:rarityGemHtml) rather than a broken <img>; the rank here is
# independent of that and still needs to sort correctly above legendary.
_RARITY_RANK = {"common": 0, "rare": 1, "epic": 2, "legendary": 3, "mythic": 4}
_RARITY_ID_NAME = {1: "common", 2: "rare", 3: "epic", 4: "legendary"}
_TOTEM_BY_BOOST = {
    0.1: ("Common Totem Land", "common"),
    0.25: ("Rare Totem Land", "rare"),
    0.5: ("Epic Totem Land", "epic"),
    1.0: ("Legendary Totem Land", "legendary"),
}
_TITLE_BY_BOOST = {
    0.1: ("Rare Title", "rare"),
    0.25: ("Epic Title", "epic"),
    0.5: ("Legendary Title", "legendary"),
}
# Occupied > Magical > Natural, per land.md's Land Type taxonomy (Occupied +100% boost
# Magical next, Natural no inherent boost) -- 2026-08-20 board-polish pass.
# Kingdom (Keep/Castle) ranks ABOVE Occupied ( board-fix batch: it is the
# highest Land Type) -- Keep and Castle share the `kingdom`
# plot_status and therefore this one rank; the Type-cell label itself is split by
# `worksite_type` (KEEP vs CASTLE), not by a second status value -- see app.js.
_PLOT_STATUS_RANK = {"kingdom": 3, "occupied": 2, "magical": 1, "natural": 0}

# card_details' raw `color` field -> Splinterlands splinter name. Verified 2026-08-20
# against seven known summoners in ~/.praetoria/cache/card_details.json (Malric
# Inferno/Red, Alric Stormbringer/Blue, Lyanna Natura/Green, Tyrus Paladium/White
# Zintar Mortalis/Black, Daria Dragonscale/Gold, Furious Chicken/Gray) -- these are the
# only colour values present across all 1,100 rows in that file.
_SPLINTER_BY_COLOR = {
    "Red": "Fire",
    "Blue": "Water",
    "Green": "Earth",
    "White": "Life",
    "Black": "Death",
    "Gold": "Dragon",
    "Gray": "Neutral",
}


def _rarity_name(rarity_id: Any) -> str | None:
    if rarity_id is None:
        return None
    return _RARITY_ID_NAME.get(rarity_id, str(rarity_id))


def _splinter_name(color: Any) -> Any:
    """Map card_details' raw `color` field to its Splinterlands splinter name.

    Unknown/missing values pass through unchanged rather than being silently
    mislabelled -- a colour outside the known seven must stay visible as itself,
    not get mapped to something plausible."""
    if color is None:
        return None
    return _SPLINTER_BY_COLOR.get(color, color)


def card_splinters(detail: dict[str, Any], facts: dict[str, Any] | None = None) -> list[str]:
    """Preserve the API's ordered primary/secondary colors as unique splinter names."""
    facts = facts or {}
    colors = (detail.get(key, facts.get(key)) for key in ("color", "secondary_color"))
    values = (_splinter_name(color.strip().capitalize()) for color in colors if isinstance(color, str) and color.strip())
    return list(dict.fromkeys(values))


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_details_cache: dict[str, tuple[int, dict[int, dict[str, Any]]]] = {}
_details_cache_lock = threading.Lock()


def details_cache_mtime(cache: JsonCache | None = None) -> int | None:
    """The `card_details.json` cache file's current `mtime_ns`, or None if it does
    not exist -- lets a caller (routes_board's board-projection cache) fold this
    file's freshness into its own cache key without duplicating `load_details_by_id`'s
    path logic."""
    cache = cache or JsonCache()
    try:
        return (cache.root / "card_details.json").stat().st_mtime_ns
    except OSError:
        return None


def load_details_by_id(cache: JsonCache | None = None) -> dict[int, dict[str, Any]]:
    """Read the `card_details` cache Praetoria's CLI already populates (TTL 86400,
    `praetoria/cli.py:214`). Offline read only -- `/api/board` performs no network I/O.
    Returns {} (degraded, not an error) if the cache entry does not exist yet.

    In-process cached by (cache-file path, mtime_ns) -- same pattern as
    `capture.latest_snapshot()`'s `_snapshot_cache`. Without this, every call paid
    `JsonCache.read()`'s full cost unconditionally: re-serialize the entire parsed
    payload with `sort_keys=True` plus a sha256 checksum over it, purely to verify
    integrity -- measured 2026-08-24 at 100-600ms for the current ~1.3MB
    `card_details.json`, on EVERY `/api/board` request even though the file changes
    at most once a day (TTL 86400). This was the single largest component of
    `/api/board`'s ~0.7-1s response time for a snapshot that was ALREADY cached."""
    cache = cache or JsonCache()
    # `safe_name("card_details")` is a no-op (no special chars), so this is exactly
    # the path `cache.read("card_details", ...)` below would resolve internally
    # duplicated here (not calling the cache's private `_path()`) just to stat it
    # before paying for a full read.
    path = cache.root / "card_details.json"
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = None
    if mtime_ns is not None:
        with _details_cache_lock:
            cached = _details_cache.get(str(path))
            if cached is not None and cached[0] == mtime_ns:
                return cached[1]
    try:
        data, _info = cache.read("card_details", ttl=0, offline=True)
    except CacheError:
        logger.warning("card_details cache missing/unreadable; ability-holder detail will be degraded")
        return {}
    if not isinstance(data, list):
        return {}
    result = {item["id"]: item for item in data if isinstance(item, dict) and "id" in item}
    if mtime_ns is not None:
        with _details_cache_lock:
            _details_cache[str(path)] = (mtime_ns, result)
    return result


def _level_by_asset_ref(report: dict[str, Any]) -> dict[str, int | None]:
    """Staked workers carry no `level` field of their own -- cross-reference via
    `report["cards"]` (the full collection sweep), same technique as
    `land_reallocate._level_by_asset_ref` (docs/land-worker-abilities.md §4)."""
    out: dict[str, int | None] = {}
    # `or`, never a dict default: a PRESENT but empty `card_index` used to
    # shadow the hydrated sidecar, so a single damaged snapshot blanked every ability on
    # the board until a fresh capture rebuilt the index. An empty index now falls through
    # to the collection the sidecar already holds.
    cards = report.get("card_index") or report.get("cards") or []
    if not isinstance(cards, list):
        return out
    for row in cards:
        if not isinstance(row, dict):
            continue
        ref = row.get("asset_ref")
        facts = row.get("facts", row)
        if isinstance(ref, str):
            out[ref] = facts.get("level") if isinstance(facts, dict) else None
    return out


def _skin_by_asset_ref(report: dict[str, Any]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    cards = report.get("card_index", report.get("cards", []))
    if not isinstance(cards, list):
        return out
    for row in cards:
        if not isinstance(row, dict):
            continue
        ref = row.get("asset_ref")
        if ref is not None:
            facts = row.get("facts", row)
            if isinstance(ref, str):
                out[ref] = facts.get("skin") if isinstance(facts, dict) else None
    return out


def _scaled_work(sf: dict[str, Any], runi_staked: bool) -> float | None:
    """: work_per_hour x region DEC efficiency, unless a Runi exempts the plot."""
    work = _num(sf.get("total_work_per_hour"))
    efficiency = _num(sf.get("efficiency"))
    if work is None or efficiency is None or runi_staked or efficiency >= 1:
        return work
    return work * efficiency


def _multiplier_parts(sf: dict[str, Any]) -> dict[str, float]:
    return {
        "deed_rarity_boost": _num(sf.get("deed_rarity_boost")) or 0.0,
        "totem_boost": _num(sf.get("totem_boost")) or 0.0,
        "title_boost": _num(sf.get("title_boost")) or 0.0,
        "runi_boost": _num(sf.get("runi_boost")) or 0.0,
        "deed_status_token_boost": _num(sf.get("deed_status_token_boost")) or 0.0,
        # (maintainer: "The 4.25x Boost excludes resource and bloodline bonuses. Including those gives 5.30x
        # correctly producing 584,325 PP"): the plot's card-ability and bloodline boosts are part of the multiplier.
        "card_abilities_boost": _num(sf.get("card_abilities_boost")) or 0.0,
        "card_bloodlines_boost": _num(sf.get("card_bloodlines_boost")) or 0.0,
    }


def _runi_number(worker: dict[str, Any]) -> int | None:
    """: the RUNI's own number from its card uid (C2-505-0000003149 -> 3149); None for any other card."""
    if worker.get("card_detail_id") != RUNI_CARD_DETAIL_ID:
        return None
    parts = str(worker.get("uid") or "").split("-")
    if len(parts) != 3 or parts[1] != "505" or not parts[2].isdigit():
        return None
    return int(parts[2])


def _worker_row(worker: dict[str, Any], level: int | None, skin: str | None, level_state: str,
                details_by_id: dict[int, dict[str, Any]], other_codes: list[list[str]]) -> dict[str, Any]:
    card_detail_id = worker.get("card_detail_id")
    row = land_ability_row(card_detail_id, level, details_by_id)
    codes = ability_codes(row)
    detail = details_by_id.get(card_detail_id, {}) if card_detail_id is not None else {}
    bcx = worker.get("bcx")
    rarity = _rarity_name(detail.get("rarity"))
    bcx_max = resolve_bcx_max(
        card_detail_id, worker.get("edition"), rarity, worker.get("gold"), worker.get("card_set"), worker.get("foil")
    )
    return {
        "asset_ref": worker.get("asset_ref"),
        # a plot's worker rows carry only the hashed asset_ref, so the RUNI number (the tail of its public card
        # uid) is sent for RUNIs only -- the page builds the per-RUNI card art address from it.
        "runi_number": _runi_number(worker),
        "slot": worker.get("slot"),
        "detail_state": worker.get("detail_state") or "complete",
        "detail_pending_reason": worker.get("detail_pending_reason"),
        "name": worker.get("name"),
        "card_name": detail.get("name"),
        "card_detail_id": card_detail_id,
        "editions": detail.get("editions"),
        "card_set": worker.get("card_set"),
        "splinter": _splinter_name(detail.get("color")), "splinters": card_splinters(detail),
        "rarity": rarity,
        "edition": worker.get("edition"),
        "foil": worker.get("foil"),
        "bcx": bcx,
        "bcx_max": bcx_max,
        "gold": worker.get("gold"),
        "level": level,
        "level_state": level_state,
        "skin": skin,
        "sub_type": detail.get("sub_type"),
        "is_powered": worker.get("is_powered"),
        "base_pp": _num(worker.get("land_base_pp")),
        "base_pp_after_cap": _num(worker.get("base_pp_after_cap")),
        "terrain_boost": _num(worker.get("terrain_boost")),
        "construction_pp": _num(worker.get("total_construction_pp")),
        "harvest_pp": _num(worker.get("total_harvest_pp")),
        "work_per_hour": _num(worker.get("work_per_hour")),
        "dec_stake_needed": _num(worker.get("dec_stake_needed")),
        "abilities_boost": _num(worker.get("card_abilities_boost")),
        "bloodlines_boost": _num(worker.get("card_bloodlines_boost")),
        "land_abilities": row,
        "is_ability_holder": row is not None,
        "sole_holder_for": sole_holder_for(codes, other_codes),
    }


def _bonus_for_plot(
    bonus_by_deed: dict[str, list[dict[str, Any]]], deed_uid: str,
    staking_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bonus: dict[str, Any] = {"totem": None, "title": None, "power_core": None, "runi": None, "land_card": None}
    staking_facts = staking_facts or {}
    scalar_boosts = {
        "totem": _num(staking_facts.get("totem_boost")),
        "title": _num(staking_facts.get("title_boost")),
    }
    mappings = {"totem": _TOTEM_BY_BOOST, "title": _TITLE_BY_BOOST}
    for kind, boost in scalar_boosts.items():
        if boost is None or boost <= 0:
            continue
        identity = mappings[kind].get(round(boost, 2))
        if identity is None:
            name, rarity = (f"{kind.title()} present — identity unknown", None)
        else:
            name, rarity = identity
        bonus[kind] = {
            "name": name, "rarity": rarity, "boost": boost,
            "asset_ref": None, "uid": None, "resolution": "derived",
            "source": "staking_facts",
        }
    for item in bonus_by_deed.get(deed_uid, []):
        kind = item.get("asset_kind")
        if kind == "totem":
            name = item.get("name")
            rarity = str(name).split(" ", 1)[0].lower() if name else None
            resolved = {
                "name": name,
                "rarity": rarity,
                "boost": _num(item.get("boost")),
                "asset_ref": item.get("asset_ref"),
                "uid": item.get("uid"),
                "resolution": "resolved",
                "source": "bonus_assets",
            }
            bonus["totem"] = resolved
        elif kind == "title":
            resolved = {
                "name": item.get("name"),
                "rarity": _TITLE_BY_BOOST.get(round(_num(item.get("boost")) or 0, 2), (None, None))[1],
                "boost": _num(item.get("boost")),
                "asset_ref": item.get("asset_ref"),
                "uid": item.get("uid"),
                "resolution": "resolved",
                "source": "bonus_assets",
            }
            bonus["title"] = resolved
        elif kind == "power_core":
            bonus["power_core"] = {
                "name": item.get("name"),
                "asset_ref": item.get("asset_ref"),
                "uid": item.get("uid"),
            }
        elif kind == "runi":
            bonus["runi"] = True
    for kind in ("totem", "title"):
        resolved = bonus[kind]
        bulk_boost = scalar_boosts[kind]
        asset_boost = _num(resolved.get("boost")) if resolved else None
        if resolved and bulk_boost is not None and asset_boost is not None and abs(bulk_boost - asset_boost) > 1e-9:
            resolved["resolution"] = "conflict"
            resolved["conflict"] = {"bulk_boost": bulk_boost, "asset_boost": asset_boost}
    return bonus


def _row_notices(badges: list[str], deed_uid: str | None, *, messages: dict[str, str] | None, lean: bool) -> list[dict[str, Any]]:
    """ (2026-09-07), v0.9.32: the lean list row carries `{code, context}` per notice and
    lets the page hydrate kind/severity/blocking/message from the `notice_catalog` it already
    ships (`catalogNotice`/`noticeMessage` in board.js). Measured on a 6,303-plot board: 5.9 MB
    of an 18.8 MB body was the catalogue's own text repeated three times per row. A notice
    whose message was overridden for this row (`messages`) keeps its message; the full row
    (`lean=False`, `GET /api/plots/{uid}`) is unchanged."""
    full = notices_for_badges(badges, deed_uid, messages=messages)
    if not lean:
        return full
    compact: list[dict[str, Any]] = []
    for notice in full:
        entry: dict[str, Any] = {"code": notice.get("code"), "context": notice.get("context") or {}}
        if messages and notice.get("code") in messages:
            entry["message"] = notice.get("message")
        compact.append(entry)
    return compact


_RESOURCE_ABILITY_CODES = frozenset({"GRAIN", "WOOD", "STONE", "IRON", "AURA", "RESEARCH", "SPS"})


def _resource_ability_rates(
    ability_holders: list[dict[str, Any]], current_resource: Any, card_abilities_boost: Any,
) -> dict[str, tuple[float, str]] | None:
    """ -- strongest resource-match ability per resource code on this plot, with its
    holder's name: `{"AURA": (1.0, "Undying Miner"), ...}`. Returns None (the worksite
    projection then keeps the plot's measured boost) when these rates cannot explain the
    game's own `card_abilities_boost` for the current worksite -- e.g. a holder's level
    was unreadable -- so a missing card record never strips a real boost."""
    rates: dict[str, tuple[float, str]] = {}
    for holder in ability_holders:
        for entry in holder.get("land_abilities") or []:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2 or entry[0] not in _RESOURCE_ABILITY_CODES:
                continue
            rate = _num(entry[1])
            if rate is not None and rate > (rates.get(entry[0]) or (0.0, ""))[0]:
                rates[entry[0]] = (rate, str(holder.get("name") or "A worker"))
    measured = _num(card_abilities_boost) or 0.0
    current = (rates.get(str(current_resource or "").upper()) or (0.0, ""))[0]
    return rates if abs(current - measured) < 1e-6 else None


def _worker_power_facts_current(worker: dict[str, Any], plot: dict[str, Any]) -> bool:
    if worker.get("detail_state") in {"pending", "stale", "unavailable"} or worker.get("detail_pending_reason"):
        return False
    try:
        stamps = [datetime.fromisoformat(value.replace("Z", "+00:00"))
                  for value in (worker.get("observed_at"), plot.get("observed_at"))]
        stamps = [stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc) for stamp in stamps]
    except (AttributeError, TypeError, ValueError):
        return False
    return stamps[0] >= stamps[1]


def _plot_row(plot: dict[str, Any], workers: list[dict[str, Any]], bonus_by_deed: dict[str, list[dict[str, Any]]],
              level_by_asset: dict[str, int | None], skin_by_asset: dict[str, str | None],
              details_by_id: dict[int, dict[str, Any]],
              capacity_index: tuple[dict[str, int], dict[str, int]],
              lean: bool = False) -> dict[str, Any]:
    # `lean` ( board-fix batch: the list view needs plot-level fields only
    # not per-worker data) controls ONLY
    # the OUTPUT "workers" field below -- every computation that's DERIVED from
    # worker_rows (ability_holders, dec, badges, multiplier_parts) still runs exactly
    # as before, since the frontend list/main-table view needs those, just not the
    # raw per-worker identity rows (name/edition/foil/bcx/land_abilities/etc.) that
    # only the expanded row's worker sub-table renders. Measured against player0005's
    # 130-plot fully-captured board (2026-08-21): "workers" is 41.6% of total board
    # payload size, the single largest field by a wide margin (next is "capacity" at
    # 7.9%) -- see CHANGELOG.md's entry for the full per-field breakdown.
    # Default stays `lean=False` (full detail) so the existing
    # `tests/ceres/test_board_projection.py` contract (which calls `project_board()`
    # with no `lean=` argument) is unchanged -- only `GET /api/board` (the list view)
    # opts into `lean=True`; `GET /api/plots/{deed_uid}` and the expand endpoint
    # (single-plot detail) always get the full row.
    facts = plot["facts"]
    sf = plot["staking_facts"]
    deed_uid = plot["deed_uid"]

    # `indexed_refs = set(level_by_asset) | set(skin_by_asset)` used to be built
    # HERE, and `_plot_row` runs once per plot (:948) -- so two sets over the whole card
    # index were rebuilt and unioned 6,303 times to produce a value identical for every
    # plot. Measured at 48.91s of a 140.66s `project_board` on a 6,303-plot account whose
    # index holds 22,476 entries (a normal account: 0.01s). It is gone rather than hoisted
    # because both of its uses were already redundant
    # `levels` -- `.get()` returns None for a ref that is absent, and returns None
    # just the same for a ref present only in `skin_by_asset`, so the
    # membership test could never change the result.
    # `level_states` -- `ref in level_by_asset` already implies `ref in indexed_refs`
    # so the extra conjunct could never be the deciding one.
    # Behaviour-identical by construction, not by measurement.
    levels = [level_by_asset.get(w.get("asset_ref")) for w in workers]
    level_states = [
        "resolved" if w.get("asset_ref") in level_by_asset else "unavailable"
        for w in workers
    ]
    codes_per_worker = [ability_codes(land_ability_row(w.get("card_detail_id"), lvl, details_by_id)) for w, lvl in zip(workers, levels)]
    worker_rows = []
    for i, (worker, level, level_state) in enumerate(zip(workers, levels, level_states)):
        other_codes = [c for j, c in enumerate(codes_per_worker) if j != i]
        worker_rows.append(_worker_row(worker, level, skin_by_asset.get(worker.get("asset_ref")), level_state, details_by_id, other_codes))

    # BLOODLINE ("Toil and Kin") companion check (board-polish batch 16, 2026-08-21)
    # annotate each worker's own land_abilities row with whether its BLOODLINE entry
    # if any, is actually active on THIS plot (>=1 other worker shares its sub_type).
    # Must run after every worker_row exists (needs the full roster), before the
    # display sort below (order doesn't matter for the companion check itself).
    # Every worker's own `construction_pp` (base_pp_after_cap x (1 + terrain_boost))
    # passed to `annotate_land_abilities()` so it can compute each BLOODLINE entry's
    # exact plot-wide PP impact (`bloodline_plot_pp_impact()`) -- the broadcast is
    # summed across the WHOLE roster, not just the holder's own card.
    plot_construction_pp = [w.get("construction_pp") for w in worker_rows]
    for i, w in enumerate(worker_rows):
        other_workers = [(w2.get("card_detail_id"), w2.get("name")) for j, w2 in enumerate(worker_rows) if j != i]
        w["land_abilities"] = annotate_land_abilities(w["land_abilities"], other_workers, details_by_id, plot_construction_pp)
    mark_bloodline_applied([w["land_abilities"] for w in worker_rows], plot_construction_pp)

    worker_rows.sort(key=lambda w: (w.get("slot") is None, w.get("slot")))

    ability_holders = [w for w in worker_rows if w["is_ability_holder"]]

    worker_dec = [w["dec_stake_needed"] for w in worker_rows if w["dec_stake_needed"] is not None]
    dec = dec_for_plot(sf, worker_dec)
    harvest_pp = _num(sf.get("total_harvest_pp"))

    # Grain (food) cost, established 2026-08-21 (item 3, board-polish): the Phase 1.5
    # whitepaper's Food page (splinterlands.gitbook.io/the-secret-of-praetoria
    # resource-production/food.md, fetched 2026-08-21) states workers consume "Grain
    # at a rate of 0.01 Grain per base production point ... per hour", regardless of
    # what resource the worksite itself produces. `worksite_facts.grain_req_per_hour`
    # is exactly that hourly rate -- verified against ALL 130 live plots in player0005's
    # account with zero exceptions: grain_req_per_hour == total_base_pp_after_cap *
    # 0.01 * (1 + grain_food_discount + lite_food_discount) (the two known cap
    # residue plots, 41-2-27/41-9-56, only match on base_pp_AFTER_cap, confirming food
    # is priced off the capped total like harvest is). This also matches the exact
    # manually-tracked formula (base_pp * 0.01 = 400 for a 40,000-base_pp plot).
    # `worksite_facts.grain_required` (a separate, larger figure -- 8840 for that same
    # plot) is NOT this: it is a constant ~22.1x multiple of grain_req_per_hour across
    # every plot observed, cause not yet understood -- deliberately NOT surfaced here
    # per the "don't render an unestablished number" instruction. `wood_required`
    # `stone_required`/`iron_required` are nonzero only on the 9 SPS-mining plots and
    # have no corresponding `_req_per_hour` field or documented formula found (checked
    # the Mining SPS whitepaper page, which does not cover resource inputs) -- also
    # not surfaced. See library/land-query.md for the full writeup.
    wf = plot.get("worksite_facts") or {}
    grain_cost_per_hour = _num(wf.get("grain_req_per_hour"))
    # Wood/Stone/Iron consumed per hour by the current worksite's recipe, shared with
    # `ceres/harvest.py::project_harvest_plot` via hourly_resource_costs() -- see that function's
    # docstring for the None-vs-zero rule. Zero new requests: wf/sf are already on this plot.
    material_costs_per_hour = hourly_resource_costs(wf, sf)

    runi_staked = bool(sf.get("is_runi_staked"))

    # plot_dec_efficiency defect fix (board-review pass, 2026-08-20): this used
    # to divide by dec["committed"] (staking_facts.total_dec_staked), the SAME field
    # flagged elsewhere as unreliable per-plot. metrics.plot_dec_efficiency now
    # denominates on dec["needed"]. Precision fix (2026-08-21): Dark Discount is now
    # SETTLED (library/land-worker-abilities.md §9) -- `needed` already reflects it
    # exactly, so a non-Runi discount plot is priced normally. Only Runi-staked plots
    # stay null: Runi zeroes the plot's whole DEC requirement independently, which is
    # what the three apparent "Dark Discount" null plots (41-2-27/41-9-56/65-9-83) turn
    # out to actually be. See that function's docstring for the full reasoning.
    _dec_efficiency_value, _dec_efficiency_flag = plot_dec_efficiency(harvest_pp, dec["needed"], dec["needed_discount"], runi_staked)

    base_pp = _num(sf.get("total_base_pp"))
    base_pp_after_cap = _num(sf.get("total_base_pp_after_cap"))
    base_pp_cap = _num(sf.get("total_base_pp_cap"))
    # R-56: comparing post-cap total with its clamp is unsatisfiable; only raw Base PP answers whether the cap took anything.
    runi_base_pp = next(
        (
            row["base_pp"]
            for row in worker_rows
            if row.get("card_detail_id") == RUNI_CARD_DETAIL_ID and row.get("base_pp") is not None
        ),
        0.0,
    )
    # The editor and the badge answer the same question about the same quantity
    # a staked Runi is additive outside the worker cap. A second rule here is how
    # the two surfaces drift apart.
    over_cap = base_pp is not None and base_pp_cap is not None and base_pp - runi_base_pp > base_pp_cap
    base_pp_cap_reached = base_pp is not None and base_pp_cap is not None and base_pp - runi_base_pp >= base_pp_cap
    multiplier_parts = _multiplier_parts(sf)

    worker_count = _num(sf.get("worker_count")) or 0
    max_workers_allowed = _num(sf.get("max_workers_allowed")) or 0
    # a worksite always has five regular slots. The live
    # max_workers_allowed value describes how many are powered, not how many exist.
    potential_card_capacity = potential_worker_capacity(plot, capacity_index)
    _capacity_identity, capacity_family = _structure_identity(plot)
    card_capacity = potential_card_capacity if capacity_family == "worksite" else int(max(max_workers_allowed, 0))
    powered_card_capacity = powered_worker_capacity(plot, capacity_index)
    powered_slots = powered_worker_slots(plot, capacity_index)
    regular_worker_count = int(max(worker_count - (1 if runi_staked else 0), 0))
    open_slots = max(card_capacity - regular_worker_count, 0)
    display = f"{regular_worker_count}/{card_capacity}+R" if runi_staked else f"{regular_worker_count}/{card_capacity}"

    diagnostics = [item.get("type") for item in plot.get("diagnostics", [])]
    assets_loaded = (plot.get("asset_fetch") or {}).get("status") == "retrieved"

    powered_worker_count = _num(sf.get("powered_worker_count"))
    worker_detail_pending = bool((plot.get("asset_fetch") or {}).get("worker_detail_pending"))
    power_fact_workers = [worker for worker in workers if _worker_power_facts_current(worker, plot)]
    unpowered_workers = [
        worker for worker in power_fact_workers
        if worker.get("stake_type_uid") != "STK-LND-RUNI"
        and (
            worker.get("is_powered") is False
            or (isinstance(worker.get("slot"), int) and worker.get("slot") > powered_slots)
        )
    ]
    unpowered_count = (
        max(regular_worker_count - powered_slots, len(unpowered_workers))
        if capacity_family == "worksite" and not worker_detail_pending
        else max(worker_count - powered_worker_count, 0)
        if powered_worker_count is not None and not worker_detail_pending
        else None
    )
    if unpowered_count is None and assets_loaded:
        unpowered_count = len(unpowered_workers)
    has_unpowered_worker = bool(unpowered_workers) or (unpowered_count is not None and unpowered_count > 0)
    energized_source = bool(sf.get("is_energized"))
    redundant_core = bool(sf.get("is_power_core_staked")) and energized_source and not runi_staked
    has_dark_discount = any(
        "DD" in (ability_codes(worker.get("land_abilities")) if worker.get("land_abilities") else codes)
        for worker, codes in zip(workers, codes_per_worker)
    )
    # The initial board already resolves worker abilities and Runi independently of item-detail expansion.
    wasted_dark_discount = runi_staked and has_dark_discount

    unpowered_worker_message = None
    if has_unpowered_worker:
        slots = sorted({worker.get("slot") for worker in unpowered_workers if worker.get("slot") is not None})
        # name the slot AND the card ("Worker slot 5 (Toiling Ox)"), so the WKR badge
        # says exactly which worker earns nothing.
        name_by_slot = {worker.get("slot"): worker.get("name") for worker in unpowered_workers if worker.get("slot") is not None}
        slot_names = ", ".join(
            f"Worker slot {slot} ({name_by_slot[slot]})" if name_by_slot.get(slot) else f"Worker slot {slot}" for slot in slots
        )
        if slot_names:
            pp_values = [_num(worker.get("total_harvest_pp")) for worker in unpowered_workers]
            lost_pp = sum(pp_values) if pp_values and all(value is not None and value > 0 for value in pp_values) else None
            pp_text = f" Its live PP figure is {lost_pp:,.0f} PP, now earned as zero." if lost_pp is not None else ""
            unpowered_total = len(unpowered_workers)
            subject = "it" if unpowered_total == 1 else "they"
            unpowered_worker_message = (
                f"{slot_names} {'is' if unpowered_total == 1 else 'are'} unpowered and inactive; "
                f"{subject} {'produces' if unpowered_total == 1 else 'produce'} nothing (0 PP). "
                f"{'This is' if unpowered_total == 1 else 'These are'} {unpowered_total} of {regular_worker_count} filled worker slots.{pp_text} To power "
                f"{'it' if unpowered_total == 1 else 'them'}, add a Power Core "
                f"to the Power slot, or use a card with the Energized ability as one of your workers."
            )
        else:
            # without per-worker power evidence, the staked cards beyond the plot's
            # powered capacity are the unpowered ones (065-09-083: Toiling Ox in slot 5, capacity 4).
            beyond = sorted(
                (worker for worker in power_fact_workers
                 if worker.get("stake_type_uid") != "STK-LND-RUNI" and isinstance(worker.get("slot"), int)
                 and worker.get("slot") > powered_slots),
                key=lambda worker: worker.get("slot"),
            )
            if beyond and unpowered_count is not None and len(beyond) == int(unpowered_count):
                who = ", ".join(
                    f"Worker slot {worker.get('slot')} ({worker.get('name')})" if worker.get("name") else f"Worker slot {worker.get('slot')}"
                    for worker in beyond
                )
                unpowered_total = len(beyond)
                unpowered_worker_message = (
                    f"{who} {'is' if unpowered_total == 1 else 'are'} unpowered and inactive; "
                    f"{'it produces' if unpowered_total == 1 else 'they produce'} nothing (0 PP). "
                    f"{'This is' if unpowered_total == 1 else 'These are'} {unpowered_total} of {regular_worker_count} filled worker slots. "
                    f"To power {'it' if unpowered_total == 1 else 'them'}, add a Power Core to the Power slot, or use a card "
                    f"with the Energized ability as one of your workers."
                )
            elif (
                capacity_family == "worksite"
                and unpowered_count is not None
                and regular_worker_count == card_capacity
                and int(unpowered_count) == card_capacity - powered_slots
            ):
                inferred_slots = range(powered_slots + 1, card_capacity + 1)
                who = ", ".join(f"Worker slot {slot}" for slot in inferred_slots)
                unpowered_total = int(unpowered_count)
                unpowered_worker_message = (
                    f"{who} {'is' if unpowered_total == 1 else 'are'} unpowered and inactive; "
                    f"{'it produces' if unpowered_total == 1 else 'they produce'} nothing (0 PP). "
                    f"{'This is' if unpowered_total == 1 else 'These are'} {unpowered_total} of {regular_worker_count} filled worker slots. "
                    f"To power {'it' if unpowered_total == 1 else 'them'}, add a Power Core to the Power slot, or use a card "
                    f"with the Energized ability as one of your workers."
                )
            else:
                unpowered_total = len(unpowered_workers) if unpowered_workers else unpowered_count
                unpowered_worker_message = (
                    f"{int(unpowered_total)} of {regular_worker_count} filled worker slots are unpowered and inactive: "
                    f"they produce nothing (0 PP), and "
                    f"recovering them costs the 72-hour worker-card unstake cooldown. In order to power these slots, you can "
                    f"either add a Power Core to the Power slot, or use cards with the Energized ability as workers."
                )

    badges = []
    if worker_count == 0:
        badges.append("idle")
    # (maintainer: "Remove the PWR badge on plots that have no workers. These plots are just inactive and have
    # no problem."): a plot with no worker gets IDL only -- missing power only matters once a worker is staked.
    if worker_count > 0 and powered_slots == 0 and not has_unpowered_worker:
        badges.append("unpowered")
    if has_unpowered_worker:
        badges.append("unpowered_worker")
    if redundant_core:
        badges.append("redundant_power_core")
    if wasted_dark_discount:
        badges.append("dark_discount_wasted")
    if worker_count > 0 and open_slots > 0:
        badges.append("underfilled")
    # Split "incomplete" into two materially different waits (2026-08-25
    # worksite ruling): a worksite CANNOT be removed once built, so its total
    # absence means clearing has never finished (open-ended, depends on rarity/PP)
    # if a worksite record exists, it's mid-build or mid-change, which ALWAYS takes
    # exactly 24 hours. `no_worksite` takes priority when both diagnostics are
    # present (measured live 2026-08-25: all 8 currently-affected plots carry both
    # `no_worksite` and the deed-level `construction_incomplete` flag together
    # `no_worksite` is the more specific fact and wins).
    if "no_worksite" in diagnostics:
        badges.append("no_worksite")
    elif any(code in diagnostics for code in ("construction_incomplete", "unfinished_worksite")):
        badges.append("worksite_incomplete")
    # "dec-short" was REMOVED 2026-08-20 (board-review pass): it fired on
    # dec.shortfall = needed - in_use, but DEC is staked at the moment a card is
    # placed, so those two are equal by construction on every occupied plot (and both
    # zero on empty ones) -- the badge was structurally dead, could never fire, and is
    # not being replaced with a forward-looking version (that is Phase 2 territory
    # explicitly deferred).
    if ability_holders:
        badges.append("land_ability_card")
    if over_cap:
        badges.append("over_cap")
    deed_stats = facts.get("deed_stats") or {}
    if deed_stats.get("unstable_totem") is True:
        badges.append("unstable_totem")
    if deed_stats.get("building_in_box") is True:
        badges.append("building_in_box")
    # Keep a stable API transport order; badges.js applies 's visible
    # group/order table after it combines backend and synthesized chips.
    badges.sort(key=badge_rank)

    # Phase 1 -- worksite-change modal (read-only): swap_targets
    # swap_eligibility are only meaningful once a control can render at all
    # (swap_control_available); pending_change is emitted independently -- a
    # mid-change plot is exactly the case swap_control_available suppresses the
    # control for, and it's still the case needs `pending_change` for.
    worksite_completion = ready_worksite_change(wf, sf.get("total_work_per_hour"))
    swap_targets = None
    swap_eligibility_row = None
    if swap_control_available(
        plot_status=facts.get("status"), diagnostics=diagnostics, project_type=wf.get("project_type"),
        ready=worksite_completion is not None, allow_first_clear=True,
    ):
        swap_targets = build_swap_targets(
            terrain=facts.get("terrain"), current_resource=facts.get("resource"), status=facts.get("status"),
            total_construction_pp=sf.get("total_construction_pp"), total_boost=sf.get("total_boost"),
            deed_status_token_boost=sf.get("deed_status_token_boost"), total_work_per_hour=_scaled_work(sf, runi_staked),
            card_abilities_boost=sf.get("card_abilities_boost"), efficiency=None if runi_staked else _num(sf.get("efficiency")),
            resource_ability_rates=_resource_ability_rates(ability_holders, facts.get("resource"), sf.get("card_abilities_boost")),
            first_clear="no_worksite" in diagnostics, rarity=facts.get("rarity"),
        )
        swap_eligibility_row = swap_eligibility(is_powered=sf.get("is_powered"), worker_count=worker_count,
                                                first_clear="no_worksite" in diagnostics)
        if not swap_eligibility_row["eligible"]:
            # `reason=` deliberately not passed here: the template already folds both
            # `reason` and `remedy` into `message`, and format_notice's separate
            # `result["reason"]` field would otherwise duplicate the same sentence a
            # second time under the rendered notice (app.js's renderNotice() shows
            # both `message` and a distinct `.notice-reason` line when present).
            swap_eligibility_row["notice"] = format_notice(
                "worksite_swap_refused_no_pp",
                context={"reason": swap_eligibility_row["reason"], "remedy": swap_eligibility_row["remedy"], "deed_uid": deed_uid},
            )
        elif swap_targets:
            # Explain the deed_status_token_boost 2x trap on the tile it actually
            # affects, per target -- a target gaining the current plot's land-type
            # bonus (dstb_delta > 0) or the current tile losing it by leaving
            # (dstb_delta < 0) is otherwise an unexplained jump/drop in the hero
            # number. `projected_rate` already computed `dstb_delta`; this only
            # turns a non-zero one into a rendered notice.
            for tile in swap_targets:
                if tile["is_current"]:
                    continue
                delta = (tile.get("rate") or {}).get("dstb_delta") or 0
                if delta > 0:
                    tile["dstb_notice"] = format_notice(
                        "worksite_swap_bonus_gained",
                        context={"status": facts.get("status"), "worksite": tile["worksite_name"]},
                    )
                elif delta < 0:
                    tile["dstb_notice"] = format_notice(
                        "worksite_swap_bonus_lost",
                        context={"status": facts.get("status"), "current_worksite": facts.get("worksite_type")},
                    )
    pending_change = build_pending_change(deed_uid, wf, sf.get("total_work_per_hour"), facts.get("prior_resource"))

    bonus = _bonus_for_plot(bonus_by_deed, deed_uid, sf)
    # Runi arrives in report["workers"] (asset_kind "runi_worker_card"), not
    # report["bonus_assets"] -- read presence from staking_facts directly, per the
    # the internal rules durable fact ("Runi ... is counted inside worker_count").
    bonus["runi"] = runi_staked
    if ability_holders:
        top = ability_holders[0]
        detail = details_by_id.get(top["card_detail_id"], {})
        bonus["land_card"] = {"rarity": _rarity_name(detail.get("rarity")), "name": top["name"]}

    row = {
        "deed_uid": deed_uid,
        "plot": facts.get("plot"),
        "plot_id": facts.get("plot_id"),
        "lock_days": facts.get("lock_days"),
        "unlock_date": facts.get("unlock_date"),
        "rarity": facts.get("rarity"),
        "plot_status": facts.get("status"),
        "occupied": worker_count > 0,
        "terrain": facts.get("terrain"),
        "resource": facts.get("resource"),
        "worksite_type": facts.get("worksite_type"),
        "magic_type": facts.get("magic_type"),
        # is_construction (board-polish batch 19, item 17): drives the terrain
        # background's `_build` suffix (worksite still under construction shows
        # the construction-site photo, not the finished one) -- a distinct field
        # from `deed_stats.building_in_box`, which is near-universally True in the
        # live account (120/130 plots) and does not correlate with this one
        # (measured: (False,True) x120, (True,True) x8, (False,None) x2).
        "is_construction": facts.get("is_construction"),
        "deed_stats": deed_stats,
        "tax_rate": facts.get("tax_rate"),
        "time_crystal_value": facts.get("time_crystal_value"),
        "territory": facts.get("territory"),
        "region": facts.get("region"),
        "region_name": facts.get("region_name"),  # the game's own region name for the Region filter label
        "harvest_pp": harvest_pp,
        "base_pp": base_pp,
        "base_pp_after_cap": base_pp_after_cap,
        "base_pp_cap": base_pp_cap,
        "over_cap": over_cap,
        "base_pp_cap_reached": base_pp_cap_reached,
        "construction_pp": _num(sf.get("total_construction_pp")),
        # the game's
        # total_work_per_hour is the rate BEFORE the region's DEC efficiency; the game UI multiplies by it. A Runi
        # plot needs no DEC and is exempt (the game shows it unscaled).
        "work_per_hour": _scaled_work(sf, runi_staked),
        "work_per_hour_unscaled": _num(sf.get("total_work_per_hour")),
        "region_efficiency": _num(sf.get("efficiency")),
        "boost": _num(sf.get("total_boost")),
        "multiplier": 1.0 + sum(multiplier_parts.values()),
        "multiplier_parts": multiplier_parts,
        "rarity_production_bonus": multiplier_parts["deed_rarity_boost"],
        "abilities_boost": _num(sf.get("card_abilities_boost")),
        "bloodlines_boost": _num(sf.get("card_bloodlines_boost")),
        # `biome` in lean mode ( board-fix batch): grepped `ceres/static/app.js`
        # for every field this row carries before trimming any of them further than
        # `workers`/`idle_cards` above -- `biome` and `diagnostics` (below) are the
        # only two with ZERO references anywhere in the frontend, list or expanded
        # view alike (confirmed 2026-08-21, not merely "unused in the list"). Measured
        # against player0016's current bulk-only board: together they're 17.7% of the
        # already-lean per-plot payload (biome 8.1%, diagnostics 9.6%) -- real weight
        # for genuinely dead data. Both stay in the full (non-lean) contract
        # unchanged -- `tests/ceres/test_board_projection.py` still requires the KEYS
        # to be present, just empty in lean mode, so `GET /api/plots/{deed_uid}`'s
        # response is untouched.
        # R-117 prerequisite, R-50's ruling in code: `or 0.0` collapsed a MISSING modifier
        # and a genuinely-zero one into the same 0.0. His rule is that "0% terrain is a value
        # not a fallback -- it may ONLY be shown for a splinter that genuinely has no bonus on
        # that plot", so the two must stay distinguishable all the way to the display: null
        # means we do not have the number, 0.0 means the game says there is no bonus. A client
        # that cannot tell them apart cannot obey the rule.
        "biome": {} if lean else {color: _num(sf.get(f"{color}_biome_modifier")) for color in BIOME_COLORS},
        "capacity": {
            "worker_count": worker_count, "powered_worker_count": _num(sf.get("powered_worker_count")),
            "max_workers_allowed": max_workers_allowed, "runi_staked": runi_staked,
            "card_capacity": card_capacity, "potential_card_capacity": potential_card_capacity,
            "powered_card_capacity": powered_card_capacity,
            "powered_slots": powered_slots,
            "open_slots": open_slots, "display": display,
        },
        "is_powered": sf.get("is_powered"),
        "is_power_core_staked": sf.get("is_power_core_staked"),
        "is_energized": sf.get("is_energized"),
        "has_labors_luck": sf.get("has_labors_luck"),
        "dec": dec,
        # the card(s) whose Dark Discount sets this plot's discount, sent on EVERY
        # row (lean list rows carry no workers), so the DEC stake tooltip can name them.
        "dec_discount_cards": [
            w["name"] for w in worker_rows
            if w.get("name") and dec.get("needed_discount") is not None and any(
                isinstance(e, (list, tuple)) and len(e) > 1 and e[0] == "DD"
                and isinstance(e[1], (int, float)) and abs(e[1] - dec["needed_discount"]) < 1e-6
                for e in (w.get("land_abilities") or [])
            )
        ],
        "grain_cost_per_hour": grain_cost_per_hour,
        # additive material-input fields -- see hourly_resource_costs() above.
        # "input_costs_per_hour" is popped below when material_costs_per_hour is None (the key
        # is OMITTED, never set to null); the three flat sort keys stay None/absent instead
        # since board.js::sortRows needs a stable key to read even when the value is unknown.
        "input_costs_per_hour": material_costs_per_hour,
        "wood_cost_per_hour": (material_costs_per_hour or {}).get("WOOD"),
        "stone_cost_per_hour": (material_costs_per_hour or {}).get("STONE"),
        "iron_cost_per_hour": (material_costs_per_hour or {}).get("IRON"),
        # (): the Rationing/Light Rationing discount already inside
        # grain_cost_per_hour (a negative fraction, e.g. -0.3), so the tooltip can say so.
        "food_discount": (_num(sf.get("grain_food_discount")) or 0.0) + (_num(sf.get("lite_food_discount")) or 0.0),
        # which card applies each food discount, sent on EVERY row (lean list rows
        # carry no workers), so the food tooltip can name the worker with the Rationing icon.
        "food_discount_cards": [
            {"name": w["name"], "code": code, "rate": rate}
            for code, plot_rate in (("RATIONING", _num(sf.get("grain_food_discount"))), ("RATIONING_LITE", _num(sf.get("lite_food_discount"))))
            if plot_rate
            for w in worker_rows if w.get("name")
            for e in (w.get("land_abilities") or [])
            if isinstance(e, (list, tuple)) and len(e) > 1 and e[0] == code
            and isinstance(e[1], (int, float)) and abs(e[1] - plot_rate) < 1e-6
            for rate in (e[1],)
        ],
        "plot_dec_efficiency": _dec_efficiency_value,
        "plot_dec_efficiency_flag": _dec_efficiency_flag,
        "ability_holders": [
            {
                "asset_ref": w["asset_ref"], "name": w["name"], "card_detail_id": w["card_detail_id"],
                "level": w["level"], "codes": ability_codes(w["land_abilities"]), "sole_holder_for": w["sole_holder_for"],
                "land_abilities": w["land_abilities"], "sub_type": w["sub_type"],
            }
            for w in ability_holders
        ],
        "bonus": bonus,
        "badges": badges,
        "notices": _row_notices(
            badges,
            deed_uid,
            messages={"unpowered_worker": unpowered_worker_message} if unpowered_worker_message else None,
            lean=lean,
        ),
        "assets_loaded": assets_loaded,
        "worker_names": list(dict.fromkeys(w["name"] for w in worker_rows if w.get("name"))),
        "pp_tooltip": {
            "workers": list(dict.fromkeys(w["name"] for w in worker_rows if w.get("name"))),
            "terrain_boost": _num(sf.get("total_terrain_boost")),
            "production_boost": _num(sf.get("total_boost")),
            "production_modifiers": multiplier_parts,
        },
        "workers": [] if lean else worker_rows,
        "asset_fetch": plot.get("asset_fetch"),
        "diagnostics": [] if lean else diagnostics,
        # Lean-gated like `workers`/`diagnostics` above (2026-08-28, ): only
        # the expanded row (`GET /api/plots/{deed_uid}`, never lean) and the worksite
        # modal it feeds actually read this field (`app.js` worksiteChangeButtonHtml
        # and its two other call sites) -- yet it was 34.7% of a whale board's bytes
        # on the wire in both modes. `swap_eligibility_row`/the notices above still
        # depend on `swap_targets` being computed, so only the OUTPUT is gated here.
        "swap_targets": [] if lean else swap_targets,
        "swap_eligibility": swap_eligibility_row,
        "worksite_completion": None if lean else worksite_completion,
        "pending_change": pending_change,
        "rarity_rank": _RARITY_RANK.get(str(facts.get("rarity")).lower(), -1),
        "plot_status_rank": _PLOT_STATUS_RANK.get(str(facts.get("status") or "").lower(), -1),
    }
    if material_costs_per_hour is None:
        del row["input_costs_per_hour"]
    if lean:
        # part 1 ( audit finding #5): the lean board row (`GET /api/board`, the
        # artifact) drops keys no frontend code reads. Verified 2026-09-14 by exact-access grep
        # over ceres/static (`.key`, `['key']`, `"key"`): `asset_fetch`, `has_labors_luck`
        # `abilities_boost`, `bloodlines_boost` have zero JS reads (the similarly named
        # `multiplier_parts.card_*_boost` are distinct); Python consumers of these names read
        # the raw snapshot plot, never the projected row. The lean-empty placeholders are
        # omitted when empty/null -- every JS read is guarded (`?.`, `|| []`, `Array.isArray`
        # truthiness), and `sps_rate.measure` uses `row.get("pending_change")`. The full row
        # (`GET /api/plots/{deed_uid}`) is unchanged. ~1.35 MB (9.3%) off a 6,303-plot board.
        # `input_costs_per_hour` () added the same way -- the frontend reads
        # only the three flat `*_cost_per_hour` sort keys (board.js sortRows, rows.js/table.js
        # cell rendering), never the dict itself, on either the lean or the full row. Dropped
        # from lean only; the full row (`GET /api/plots/{deed_uid}`) keeps it as an API field
        # for other consumers (see library/api-reference.md), even though no frontend reads it.
        for key in _LEAN_DEAD_KEYS:
            row.pop(key, None)
        for key in _LEAN_OMIT_WHEN_EMPTY:
            if key in row and row[key] in (None, [], {}):
                del row[key]
    return row


# see the lean block at the end of `_plot_row`.
# `swap_eligibility` (worksite.js worksiteModalState/worksiteModalBodyHtml) and
# `multiplier_parts` (editor.js editorRuniTip) are read only on the expanded plot, whose row is
# REPLACED by the `GET /api/plots/{deed_uid}` detail (rows.js loadPlotDetail: `BOARD.plots[idx] =
# updated`); the worksite modal button itself needs `swap_targets`, already lean-empty, and the
# Runi tip is `?.`-guarded with a wording fallback. ~2.5 MB off a 6,303-plot board.
_LEAN_DEAD_KEYS = ("asset_fetch", "has_labors_luck", "abilities_boost", "bloodlines_boost", "swap_eligibility", "multiplier_parts", "input_costs_per_hour")
_LEAN_OMIT_WHEN_EMPTY = ("biome", "workers", "diagnostics", "swap_targets", "worksite_completion", "pending_change")


def build_idle_cards(report: dict[str, Any], details_by_id: dict[int, dict[str, Any]], *, load_pool: bool = True) -> tuple[list[dict[str, Any]], bool]:
    """Extracted  board-fix batch (was inline in `project_board()`) so it can run
    standalone -- it depends only on `report["coverage"]`/`report["cards"]` and
    `details_by_id`, NEVER on `plot_rows`, so it costs nothing extra to call from the
    new `GET /api/idle-cards` lazy endpoint without also paying for a full board
    build. Same honesty-gate behaviour as before, unchanged: idle-card classification
    needs EVERY deed's assets cross-checked to be trustworthy -- gate on full
    coverage; return "not loaded" (empty list, `loaded=False`) rather than a wrong or
    incomplete zero."""
    idle_cards_loaded = bool(report.get("coverage", {}).get("assets", {}).get("complete"))
    idle_cards: list[dict[str, Any]] = []
    cards: Any = []
    if idle_cards_loaded and load_pool:
        try:
            from .capture import load_card_pool
            cards = load_card_pool(report).get("cards", [])
        except LookupError:
            idle_cards_loaded = False
    elif idle_cards_loaded:
        if not isinstance(report.get("cards"), list):
            return idle_cards, False
        cards = report.get("cards", [])
    if not isinstance(cards, list):
        cards = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        classification = card.get("classification", {})
        if not isinstance(classification, dict):
            continue
        bucket = classification.get("bucket")
        if bucket not in ("unstaked_apparent", "stake_expired_or_historical"):
            continue
        facts = card.get("facts", {})
        if not isinstance(facts, dict):
            continue
        # facts["name"] is only ever populated on STAKED records (the Land API
        # returns it); collection-sweep cards -- which is what every idle card is
        # never carry it, which is why every idle row rendered `null` (board-review
        # pass, 2026-08-20). Join name/splinter/rarity from the same
        # offline card_details lookup the worker rows already use.
        card_detail_id = facts.get("card_detail_id")
        detail = details_by_id.get(card_detail_id, {}) if card_detail_id is not None else {}
        rarity = _rarity_name(detail.get("rarity"))
        available, blocked_reason = classify_idle_availability(facts, player=report.get("query", {}).get("player"))
        idle_cards.append({
            # `uid` is the explicit combine Card ID (facts.uid, e.g. "C3-337-01SRQJXN68")
            # not `asset_ref` -- exposing it here lets the combine dialog gather every
            # copy of a card straight from this cached list instead of a per-card
            # candidates round trip to resolve it (the old resolveCombineCardUid()).
            "asset_ref": card.get("asset_ref"), "uid": facts.get("uid"), "name": detail.get("name"),
            "card_detail_id": card_detail_id,
            "card_set": facts.get("card_set"), "splinter": _splinter_name(detail.get("color")), "splinters": card_splinters(detail), "rarity": rarity,
            "edition": facts.get("edition"), "foil": facts.get("foil"), "bcx": facts.get("bcx"),
            "bcx_max": resolve_bcx_max(
                card_detail_id, facts.get("edition"), rarity, facts.get("gold"), facts.get("card_set"), facts.get("foil")
            ),
            # Full per-level BCX ladder, not just the max -- lets the combine modal's
            # outcome ledger (ceres/static/app.js::combineLevelForBcx) project the LEVEL
            # a chosen quantity reaches, not only whether it hits max. Same table lookup
            # family as bcx_max above; see ceres/level_cap.py.
            "level_bcx_thresholds": resolve_level_thresholds(card_detail_id, facts.get("edition"), rarity, facts.get("gold"), facts.get("card_set")),
            "gold": facts.get("gold"), "level": facts.get("level"), "base_pp": _num(facts.get("land_base_pp")),
            # Skin is a card-level attribute (0 per-copy variation observed across 965
            # card ids, 2026-08-28) -- staked worker rows already carry it via
            # `_skin_by_asset_ref`; idle cards read the same `facts` dict directly so no
            # extra lookup is needed. Without it editorCardImage() silently falls back to
            # the unskinned CDN path for every combine-arrival/idle-card render.
            "skin": facts.get("skin"),
            "bucket": bucket, "available": available, "blocked_reason": blocked_reason,
            "stake_end_date": facts.get("stake_end_date"), "cooling_until": facts.get("stake_end_date"),
            "dec_stake_needed": facts.get("land_dec_stake_needed"),
        })
    return idle_cards, idle_cards_loaded


RUNI_CARD_DETAIL_ID = 505  # confirmed against /cards/get_details, 2026-08-21: id 505, name "Runi"


def _unassigned_item_family(report: dict[str, Any], family: str, orphaned: list[dict[str, Any]]) -> dict[str, Any]:
    """One family's slice of report["unassigned_items"] (Praetoria's Source A capture
    against /land/stake/items/{code}/available), with any orphaned already-assigned
    bonus_assets rows for the same family folded in as extra entries. A family that
    Praetoria could not capture (STK-LND-RUNI's documented P0001-wrapped-in-200
    failure, or no owned deed to anchor the query) stays explicitly unfetchable --
    never silently zero."""
    raw = (report.get("unassigned_items") or {}).get(family)
    if not isinstance(raw, dict) or not raw.get("capturable"):
        reason = raw.get("reason") if isinstance(raw, dict) else "not captured"
        entry: dict[str, Any] = {"capturable": False, "count": None, "items": [], "reason": reason}
    else:
        entry = {"capturable": True, "count": raw.get("count", 0), "items": list(raw.get("items", [])), "reason": None}
    if orphaned:
        entry["orphaned"] = [
            {"asset_ref": o.get("asset_ref"), "name": o.get("name"), "boost": _num(o.get("boost"))}
            for o in orphaned
        ]
    return entry


def _unassigned_bonus_families(
    report: dict[str, Any], idle_cards: list[dict[str, Any]], orphaned_bonus: dict[str, list[dict[str, Any]]],
    idle_cards_loaded: bool,
) -> dict[str, Any]:
    """Five bonus-asset families for the Ceres board's "Unassigned bonus assets"
    section. Totems/titles/power cores are ITEMS (Praetoria's Source A, a real fetch
    against /available, independent of per-deed asset coverage -- captured by both
    `capture_quick()` and `capture()`); Runi and Land/Verico cards are CARDS already
    present in idle_cards (Source B, no new fetch -- Runi's own /available call always
    fails, and Land/Verico eligibility is the same idle_cards classification the
    idle-cards section already uses). Selected from idle_cards, never duplicated as new
    records, so a card appearing here is the same object idle_cards already lists.
    `runi`/`land_card` inherit idle_cards' own full-coverage gate (2026-08-21) -- they
    render as explicitly not-yet-capturable, never a false zero, until a full capture
    or a complete set of row-expands has cross-checked every deed."""
    runi_cards = [c for c in idle_cards if c.get("card_detail_id") == RUNI_CARD_DETAIL_ID]
    land_cards = [
        c for c in idle_cards
        if c.get("card_detail_id") != RUNI_CARD_DETAIL_ID and is_land_or_verico(c.get("card_detail_id"))
    ]
    if idle_cards_loaded:
        # 2026-08-21 (bundling-defect fix): the client's per-type bundling key
        # was `card_detail_id + rarity` only, because those were the only
        # type-identifying fields this trimmed payload forwarded. Measured
        # against the live snapshot: 436 of 870 idle-eligible card_detail_ids
        # span more than one distinct (level, bcx, gold, foil, edition) combo
        # level/bcx/gold/foil/edition are added here (already present on
        # every idle_cards record, `build_idle_cards()` above) so the client
        # can group on the FULL instance identity per the internal rules's rule ("cards
        # differing in level, BCX, gold/regular foil, or edition are NOT the
        # same asset"). Kept to exactly these five scalar fields -- no other
        # idle_cards field is forwarded -- to keep the growth proportionate to
        # the fix rather than re-widening the payload this endpoint was
        # deliberately trimmed to avoid (idle-cards array was once 89% of a
        # 3.4 MB response).
        runi_family = {
            "capturable": True, "count": len(runi_cards),
            "cards": [
                {
                    "asset_ref": c["asset_ref"], "name": c["name"], "bucket": c["bucket"],
                    "level": c["level"], "bcx": c["bcx"], "gold": c["gold"], "foil": c["foil"],
                    "edition": c["edition"],
                }
                for c in runi_cards
            ],
        }
        land_family = {
            "capturable": True, "count": len(land_cards),
            "cards": [
                {
                    "asset_ref": c["asset_ref"], "name": c["name"], "rarity": c["rarity"],
                    "card_detail_id": c["card_detail_id"], "bucket": c["bucket"],
                    "level": c["level"], "bcx": c["bcx"], "gold": c["gold"], "foil": c["foil"],
                    "edition": c["edition"],
                }
                for c in land_cards
            ],
        }
    else:
        not_loaded = {"capturable": False, "count": None, "cards": [], "reason": "requires a full capture (or every plot expanded) to cross-check idle-card ownership"}
        runi_family = dict(not_loaded)
        land_family = dict(not_loaded)
    return {
        "totem": _unassigned_item_family(report, "totem", orphaned_bonus.get("totem", [])),
        "title": _unassigned_item_family(report, "title", orphaned_bonus.get("title", [])),
        "power_core": _unassigned_item_family(report, "power_core", orphaned_bonus.get("power_core", [])),
        "runi": runi_family,
        "land_card": land_family,
    }


# (2026-09-07): one board row per plot, memoised on the identity of everything it is
# computed from. `project_board` rebuilt all 6,303 rows of a whale account (3.3 s) whenever the
# snapshot changed -- and every plot open changes the snapshot. The parsed snapshot lives in
# `ceres.capture`'s cache, and a merge reuses the plot, worker and bonus dicts it did not touch
# so the object identities ARE the change signal: a row is recomputed only when its own plot
# workers, bonus assets, levels/skins or capacity entries changed. The value pins the objects
# it was keyed on, so an id can never be recycled under a live entry. Bounded LRU.
_PLOT_ROW_MEMO_MAX = 20000
_plot_row_memo_entries: "OrderedDict[tuple, tuple[dict[str, Any], tuple]]" = OrderedDict()
_plot_row_memo_lock = threading.Lock()


def _plot_row_memo(plot: dict[str, Any], workers: list[dict[str, Any]], bonus_by_deed: dict[str, list[dict[str, Any]]],
                   level_by_asset: dict[str, int | None], skin_by_asset: dict[str, str | None],
                   details_by_id: dict[int, dict[str, Any]],
                   capacity_index: tuple[dict[str, int], dict[str, int]],
                   lean: bool = False) -> dict[str, Any]:
    deed_uid = plot.get("deed_uid")
    bonus = bonus_by_deed.get(deed_uid, [])
    # The capacity index is keyed by structure identity/family, not by deed: resolve the two
    # entries this plot would read, so a change to a peer structure's capacity reaches the key.
    identity, family = _structure_identity(plot)
    exact, families = capacity_index if capacity_index else ({}, {})
    key = (
        id(plot), lean, id(details_by_id),
        tuple(id(w) for w in workers),
        tuple((level_by_asset.get(w.get("asset_ref")), skin_by_asset.get(w.get("asset_ref"))) for w in workers),
        tuple(id(b) for b in bonus),
        exact.get(identity) if identity else None,
        families.get(family) if family else None,
    )
    with _plot_row_memo_lock:
        hit = _plot_row_memo_entries.get(key)
        if hit is not None:
            _plot_row_memo_entries.move_to_end(key)
            return dict(hit[0])
    row = _plot_row(plot, workers, bonus_by_deed, level_by_asset, skin_by_asset, details_by_id, capacity_index, lean=lean)
    with _plot_row_memo_lock:
        _plot_row_memo_entries[key] = (row, (plot, tuple(workers), tuple(bonus), details_by_id))
        while len(_plot_row_memo_entries) > _PLOT_ROW_MEMO_MAX:
            _plot_row_memo_entries.popitem(last=False)
    return dict(row)



def _with_unpowered_loss(report: dict[str, Any], rows: list[dict[str, Any]],
                         details_by_id: dict[int, dict[str, Any]] | None,
                         level_by_asset: dict[str, int | None] | None = None) -> list[dict[str, Any]]:
    """: an unpowered staked card also switches off its land abilities for every worker
    on the plot. Price each such plot as if it had full worker-slot power and add what the
    card costs the whole plot to its WKR message ("lowering the whole plot by X PP").
    Only rows whose message names a slot are priced; the plot data is built once. Rows come
    from a shared memo, so the notices list is replaced, never edited in place. A plot that
    cannot be priced simply keeps its message."""
    targets = [
        row for row in rows
        if "unpowered_worker" in (row.get("badges") or [])
        and any(n.get("code") == "unpowered_worker" and str(n.get("message") or "").startswith("Worker slot")
                for n in row.get("notices") or [])
    ]
    if not targets:
        return rows
    try:
        from praetoria.land_reallocate import attach_colors, build_plots
        from .candidates import stage_impact
        details = details_by_id or load_details_by_id()
        plots = build_plots(report)
        attach_colors(plots, details)
        # Card levels drive every ability's strength: without them each ability prices at its
        # weakest (065-09-083: 11,688 PP instead of 127,450). The saved board is built from a
        # snapshot that carries no card collection, so take the levels the board already
        # shows on its tiles (`_level_by_asset_ref`) instead of loading the collection.
        levels = level_by_asset if level_by_asset is not None else _level_by_asset_ref(report)
        for target in targets:
            plot = plots.get(str(target["deed_uid"]))
            for card in (plot.cards if plot is not None else []):
                if card.get("level") is None:
                    card["level"] = levels.get(card.get("asset_ref"))
    except Exception as exc:
        logger.warning("unpowered-loss pricing unavailable: %s", exc)
        return rows
    priced: dict[str, float] = {}
    for row in targets:
        plot = plots.get(str(row["deed_uid"]))
        # Never publish an understated figure: skip a plot whose card levels are unknown.
        if plot is None or any(card.get("level") is None for card in plot.cards):
            continue
        try:
            impact = stage_impact(report, str(row["deed_uid"]), [], details, force_full_power=True, plots=plots)
            gain = float(impact.get("harvest_pp_delta") or 0.0)
        except Exception:
            continue
        if gain >= 1:
            priced[str(row["deed_uid"])] = gain
    if not priced:
        return rows
    out = []
    for row in rows:
        gain = priced.get(str(row.get("deed_uid")))
        if gain is None:
            out.append(row)
            continue
        sentence = f" Its abilities are switched off too, lowering the whole plot by {gain:,.0f} PP."
        notices = [
            {**n, "message": str(n.get("message")) + sentence}
            if n.get("code") == "unpowered_worker" and "lowering the whole plot by" not in str(n.get("message") or "") else n
            for n in row.get("notices") or []
        ]
        out.append({**row, "notices": notices})
    return out

def project_board(report: dict[str, Any], details_by_id: dict[int, dict[str, Any]] | None = None,
                   lean: bool = False) -> dict[str, Any]:
    """`lean=True` ( board-fix batch) drops each row's `workers` array from the
    OUTPUT only -- see `_plot_row`'s own comment for what stays computed either way.
    Used by `GET /api/board` (the list view); every other caller (including the
    `test_board_projection.py` contract, which calls this with no `lean=` argument)
    keeps the full default."""
    details_by_id = details_by_id or {}
    player = report.get("query", {}).get("player")
    level_by_asset = _level_by_asset_ref(report)
    skin_by_asset = _skin_by_asset_ref(report)
    capacity_index = structure_capacity_index(report)

    workers_by_deed: dict[str, list[dict[str, Any]]] = {}
    for worker in report.get("workers", []):
        ref = worker.get("stake_ref_uid")
        if ref is not None:
            workers_by_deed.setdefault(ref, []).append(worker)

    bonus_by_deed: dict[str, list[dict[str, Any]]] = {}
    plot_uids = {plot["deed_uid"] for plot in report.get("plots", [])}
    # bonus_assets is Praetoria's per-deed asset fetch -- every row observed so far
    # carries a stake_ref_uid matching one of the player's own plots. A row that
    # doesn't is kept, not dropped, and folded into the unassigned-bonus section
    # below as an "orphaned" entry per family -- genuinely unexpected, worth seeing.
    orphaned_bonus: dict[str, list[dict[str, Any]]] = {}
    for item in report.get("bonus_assets", []):
        ref = item.get("stake_ref_uid")
        if ref in plot_uids:
            bonus_by_deed.setdefault(ref, []).append(item)
        else:
            orphaned_bonus.setdefault(item.get("asset_kind") or "unknown", []).append(item)

    plot_rows = [
        _plot_row_memo(plot, workers_by_deed.get(plot["deed_uid"], []), bonus_by_deed, level_by_asset, skin_by_asset, details_by_id, capacity_index, lean=lean)
        for plot in sorted(report.get("plots", []), key=lambda p: -( _num(p["staking_facts"].get("total_harvest_pp")) or 0.0))
    ]

    plot_rows = _with_unpowered_loss(report, plot_rows, details_by_id, level_by_asset)
    idle_plots = [row["deed_uid"] for row in plot_rows if row["capacity"]["worker_count"] == 0]

    idle_cards, idle_cards_loaded = build_idle_cards(report, details_by_id, load_pool=False)

    unassigned_bonus = _unassigned_bonus_families(report, idle_cards, orphaned_bonus, idle_cards_loaded)

    total_harvest = sum(row["harvest_pp"] or 0.0 for row in plot_rows)
    total_construction = sum(row["construction_pp"] or 0.0 for row in plot_rows)
    total_base = sum(row["base_pp"] or 0.0 for row in plot_rows)
    dec_committed_total = sum(row["dec"]["committed"] or 0.0 for row in plot_rows)
    dec_needed_total = sum(row["dec"]["needed"] or 0.0 for row in plot_rows)
    dec_shortfall_total = sum(row["dec"]["shortfall"] or 0.0 for row in plot_rows)
    workers_staked = sum(row["capacity"]["worker_count"] for row in plot_rows)

    # Portfolio-level plot_dec_efficiency defect fix, same treatment as the per-plot
    # value above: sum only over plots whose OWN efficiency is trustworthy (flag is
    # None) rather than dividing the full-portfolio harvest by the unreliable
    # dec_committed_total. A plot is excluded here for the same two reasons it's
    # flagged per-plot -- Dark-Discount-unsettled, or a zero dec.needed denominator.
    _trusted_rows = [row for row in plot_rows if row["plot_dec_efficiency_flag"] is None]
    _trusted_harvest = sum(row["harvest_pp"] or 0.0 for row in _trusted_rows)
    _trusted_dec_needed = sum(row["dec"]["needed"] or 0.0 for row in _trusted_rows)
    portfolio_dec_efficiency = (_trusted_harvest / _trusted_dec_needed) if _trusted_dec_needed else None
    portfolio_dec_efficiency_excluded_plots = len(plot_rows) - len(_trusted_rows)

    generated_at = report.get("generated_at")

    owner_warnings = list(report.get("warnings", []))
    indexed_worker_refs = set(level_by_asset) | set(skin_by_asset)
    missing_worker_refs = sum(
        1 for worker in report.get("workers", [])
        if worker.get("asset_ref") is not None and worker.get("asset_ref") not in indexed_worker_refs
    )
    if missing_worker_refs:
        owner_warnings.append(
            f"PARTIAL DATA: card level/skin cross-reference unavailable for {missing_worker_refs} staked workers; "
            "the board shows level and skin as unavailable, not as a genuine card with no level."
        )
    board = {
        "schema": SCHEMA_BOARD,
        "player": player,
        "generated_at": generated_at,
        # Fetched during capture only (`ceres/capture.py::_fetch_balances`
        # `_tc_balance` / `_permit_balance`) -- `GET /api/board` itself performs no
        # network call, so this rides the snapshot like everything else. `{"value"
        # float, ...}` or None when never fetched or the fetch failed; the frontend
        # renders an honest "unknown" state for None, never a silent zero.
        "tc_balance": report.get("tc_balance"),
        # Aura Lab Permit balance (`PERMIT.AL` token) -- account-bound, required to
        # build or change into an Aura Lab. Same fetch/shape/honesty rules as
        # tc_balance above.
        "permit_balance": report.get("permit_balance"),
        "coverage": report.get("coverage", {}),
        "warnings": owner_warnings,
        "notices": [
            {**notice, "context": {**notice.get("context", {}), "deed_uid": None}}
            for warning in owner_warnings
            # per-plot read failures are already counted by the coverage notice ("N of M plots fully
            # loaded ... failed ... not read yet"); a second PARTIAL DATA line only contradicted it.
            if not str(warning).startswith("PARTIAL DATA: deed ")
            for notice in [format_notice("board_warning", context={"reason": _condense_warning(str(warning))})]
        ],
        "notice_catalog": notice_catalog(),
        "unknowns": ["upstream_efficiency", "site_efficiency", "dec_relationship"],
        "sortable": list(SORTABLE),
        "badge_meta": BADGE_META,
        # Retained for API compatibility; badges.js no longer reads it for display.
        "badge_order": BADGE_ORDER,
        "totals": {
            "plots": len(plot_rows),
            "plots_idle": len(idle_plots),
            "plots_unpowered": sum(1 for row in plot_rows if row["is_powered"] is False),
            "plots_incomplete": sum(1 for row in plot_rows if "no_worksite" in row["badges"] or "worksite_incomplete" in row["badges"]),
            "harvest_pp": total_harvest,
            "construction_pp": total_construction,
            "base_pp": total_base,
            "dec_committed": dec_committed_total,
            "dec_needed": dec_needed_total,
            "dec_shortfall": dec_shortfall_total,
            "portfolio_dec_efficiency": portfolio_dec_efficiency,
            "portfolio_dec_efficiency_excluded_plots": portfolio_dec_efficiency_excluded_plots,
            "workers_staked": workers_staked,
            "idle_cards_available": sum(1 for card in idle_cards if card["available"]),
            # Real count even in `lean` mode, where `idle_cards` itself is trimmed to
            # `[]` below -- so the frontend's "Idle cards (N)" summary is accurate
            # without shipping the array; `GET /api/idle-cards` fetches the real list
            # lazily, only when that <details> section is actually opened (
            # board-fix batch). Measured live against `player0005`'s fully-captured
            # account, 2026-08-21: `idle_cards` alone was 3.04 MB of a 3.4 MB total
            # board payload (89%) -- by a wide margin the single biggest field in the
            # ENTIRE response, bigger than every plot row's own "workers" field
            # combined (41.6% of the "plots" sub-total, itself much smaller than
            # "idle_cards"). Discovered while implementing the `workers` trim
            # team-lead named explicitly; not itself named in the original dispatch
            # but the same "list needs plot-level fields only, fetch detail on
            # demand" principle applies -- flagged prominently in the report rather
            # than silently expanded in scope.
            "idle_cards_count": len(idle_cards),
        },
        "plots": plot_rows,
        "idle_plots": idle_plots,
        "idle_cards": [] if lean else idle_cards,
        "idle_cards_loaded": idle_cards_loaded,
        "unassigned_bonus": unassigned_bonus,
    }
    return board


def project_single_plot(report: dict[str, Any], deed_uid: str,
                         details_by_id: dict[int, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """O(1)-ish single-plot detail, always full (never `lean`) -- for `GET
    /api/plots/{deed_uid}` and `POST /api/plots/{deed_uid}/expand` ( board-fix
    batch: opening a plot to see its workers was reported as extremely slow).
    Both routes used to call the full `project_board()` (building and
    discarding a complete PlotRow for every OTHER plot in the account) just to read
    ONE row back out -- on a 6,303-plot whale that meant 6,302 wasted `_plot_row()`
    calls per click, each doing real work (ability annotation, DEC calc, badge
    computation). Measured against player0016 (2026-08-21): `project_board()` on the
    full report takes ~0.37s of CPU for all 6,303 plots; this function does the
    equivalent of ONE of those 6,303 `_plot_row()` calls, not all of them, so it
    stays fast regardless of account size. `workers`/`bonus_assets` are still
    scanned in full (O(total workers)/O(total bonus assets) across the WHOLE
    account, not deed-scoped queries) since Praetoria's report has no per-deed
    index -- but that scan is cheap (simple dict.get comparisons) next to the
    per-plot computation `project_board()` was paying 6,303 times over. Returns
    `None` if `deed_uid` isn't found (caller raises 404)."""
    details_by_id = details_by_id or {}
    plot = next((p for p in report.get("plots", []) if p["deed_uid"] == deed_uid), None)
    if plot is None:
        return None
    level_by_asset = _level_by_asset_ref(report)
    skin_by_asset = _skin_by_asset_ref(report)
    capacity_index = structure_capacity_index(report)
    workers = [w for w in report.get("workers", []) if w.get("stake_ref_uid") == deed_uid]
    bonus_by_deed: dict[str, list[dict[str, Any]]] = {}
    for item in report.get("bonus_assets", []):
        if item.get("stake_ref_uid") == deed_uid:
            bonus_by_deed.setdefault(deed_uid, []).append(item)
    row = _plot_row(plot, workers, bonus_by_deed, level_by_asset, skin_by_asset, details_by_id, capacity_index, lean=False)
    result = _with_unpowered_loss(report, [row], details_by_id, level_by_asset)[0]
    # compact harvest projection on the detailed row only (never the lean board).
    from .harvest import project_harvest_plot
    result["harvest"] = project_harvest_plot(plot)
    return result
