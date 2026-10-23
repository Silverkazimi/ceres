"""Snapshot-backed candidate filtering and plot ranking."""

from __future__ import annotations

import heapq
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from praetoria.land_reallocate import (
    MovableCard,
    Plot,
    _cap_allocation,
    _harvest,
    attach_colors,
    bloodline_holder,
    build_plots,
    evaluate_candidates_for_plot,
    plot_abilities_boost,
    plot_bloodline_boost,
    _scoring_allocation,
)

from .board import RUNI_CARD_DETAIL_ID, card_splinters, load_details_by_id
from .capacity import (
    _structure_identity,
    potential_worker_capacity,
    powered_worker_slots,
    structure_capacity_index,
)
from .metrics import classify_idle_availability, rental_direction
from .metrics import ability_codes, land_ability_row
from .notices import candidate_notices, format_notice, notice_catalog
from praetoria.land_analysis import item_catalogue_lookup
from .worksite_swap import _RATE_PER_PP, resource_key
from .combine_chain import upgrade_inputs
from .worker_upgrade import _facts as _upgrade_facts, _identity as _upgrade_identity, _uid as _upgrade_uid

IDLE_BUCKETS = ("unstaked_apparent", "stake_expired_or_historical")
SOULKEEP_EDITIONS = frozenset({9, 11})
# `stake_cooling_down` is the new bucket for a card that has already been unstaked
# and is counting down (`stake_end_date` set and in the future
# praetoria.land_analysis.classify_card). It is ADDED here, not swapped in for
# `stake_active_unconfirmed` -- that bucket is now reserved for a card with NO end date
# at all (genuinely staked, just not cross-checked against its deed's assets), and its
# existing browse-only visibility here (it never clears the cooldown-ready gate; see
# `cooldown_state`'s "unknown" state for a null end date) is unrelated to and must
# not regress.
COOLING_BUCKETS = ("stake_active_unconfirmed", "stake_cooling_down")
GROUP_TO_SOURCE = {
    "worker": ("cards", "collection_derived", "STK-LND-WKR"),
    "runi": ("cards", "collection_derived", "STK-LND-RUNI"),
    "totem": ("items", "game_available_list", "STK-LND-TOT"),
    "power_core": ("items", "game_available_list", "STK-LND-PCR"),
    "title": ("items", "game_available_list", "STK-LND-TTL"),
}
POWER_SOURCE_RULES = {
    "runi": ("STK-LND-RUNI", "is_runi_staked", "runi_boost"),
    "power_core": ("STK-LND-PCR", "is_power_core_staked", None),
    "energized": ("STK-LND-ENERGIZED", "is_energized", None),
}
# Plot-level (non-`plot.cards`) stake types that shift the plot's flat multiplier or its
# powered state when removed -- the counterpart to `POWER_SOURCE_RULES` above, generalized
# for `plot_removal_delta` below which must handle ANY asset a removal batch might name
# not just worker cards. Title/Totem never gate power (see the internal rules's "one plot-wide power
# source" rule -- only Runi/Power Core/Energized do), so they appear only here.
# Runi contributes a fixed +100% plot boost (official Power Sources FAQ).
RUNI_PRODUCTION_BOOST = 1.0
REMOVAL_MULT_FIELD_BY_TYPE = {
    "STK-LND-RUNI": "runi_boost",
    "STK-LND-TOT": "totem_boost",
    "STK-LND-TTL": "title_boost",
}
REMOVAL_POWER_FLAG_BY_TYPE = {
    "STK-LND-RUNI": "is_runi_staked",
    "STK-LND-PCR": "is_power_core_staked",
}
# Corrected 2026-08-26 (primary-sourced from support.splinterlands.com, verbatim across
# three articles): Runi carries no cooldown at all ("Runi are NOT subject to a 3-day
# cooldown once removed from land"). The official unstaking guide confirms immediate
# item reuse, including Titles (, checked 2026-09-18).
COOLDOWN_CLASS = {
    "STK-LND-WKR": "cards",
    "STK-LND-RUNI": "items",
    "STK-LND-TOT": "items",
    "STK-LND-PCR": "items",
    "STK-LND-TTL": "items",
}
RARITY_NAMES = {1: "common", 2: "rare", 3: "epic", 4: "legendary", 5: "mythic"}
ITEM_RARITY_BY_BOOST = {
    "STK-LND-TOT": {0.10: "common", 0.25: "rare", 0.50: "epic", 1.00: "legendary"},
    "STK-LND-TTL": {0.10: "rare", 0.25: "epic", 0.50: "legendary"},
}
SPLINTER_NAMES = {
    "red": "fire", "blue": "water", "green": "earth", "white": "life",
    "black": "death", "gold": "dragon", "gray": "neutral",
}
PRESET_INFO = {
    "construction_pp": ("Boostable", "construction_pp"),
    "net_gain_default": ("Best for this plot", "net_gain"),
    "highest_pp": ("Highest PP", "base_pp"),
    "raw_highest_pp": ("Highest PP", "base_pp"),
    "cooldown_free_only": ("Cooldown-free only", "net_gain"),
    "cooldown_free": ("Cooldown-free only", "net_gain"),
    "everything": ("Show everything", "net_gain"),
}


@dataclass
class CandidateRow:
    card: MovableCard
    details: dict[str, Any]
    facts: dict[str, Any]
    bucket: str
    stake_type_uid: str
    cooldown: dict[str, Any]
    rental_kind: str | None = None
    rental_expiry: str | None = None
    score: float | None = 0.0
    breakdown: dict[str, Any] | None = None
    terrain: float | None = 0.0
    evicted: dict[str, Any] | None = None
    construction_pp: dict[str, Any] | None = None
    # Stage-one (Base PP / construction) net delta this candidate would cause -- see
    # `evaluate_candidates_for_plot`'s `construction_pp_delta`. Distinct from
    # `construction_pp` (the candidate's own value) and from `plot_impact` (stage-two
    # harvest delta) -- the internal rules's two-stage chain must never be collapsed.
    construction_pp_delta: dict[str, Any] | None = None
    base_pp_delta: dict[str, Any] | None = None
    plot_impact: dict[str, Any] | None = None


# (2026-09-07): every picker request -- open, page, filter, sort -- rebuilt the eligible
# list from all ~7,900 collection rows and re-scored every candidate (~3,900) against the plot
# from scratch: 0.33-0.85 s of CPU per request on the maintainer's account, profiled. Neither step
# depends on the request's filters. The eligible list depends on the snapshot, the player, the
# bucket and (through cooldown readiness) the clock; a candidate's score depends on the
# snapshot, the plot, the group and the clicked slot's occupant. Both are memoised here
# keyed on the snapshot's CONTENT identity (`_snapshot_identity`) so any board write -- a
# capture, an expand, a staging refresh -- invalidates them, and on the identity of the
# functions themselves so a test that monkeypatches either never reads a stale answer.
# Filter, sort and pagination still run per request, unchanged.
# The eligible list lives with the snapshot (any board write replaces it), with a ceiling
# because two of its inputs are clocks: a rental or market listing that expires, and an
# unlock date that passes, only become visible at the next rebuild. Cooldown readiness is
# the one clock that moves within a session, and it is re-checked per request from each
# row's `ready_at` () -- on a 124,974-card pool the rebuild was 5-7 s and, keyed to a
# 20 s window, it hit every third click.
_ELIGIBLE_ROWS_TTL_SECONDS = 600.0
_MATCHING_WINDOW_SECONDS = 60.0
# rows scored beyond the page, so a tie-break the score-free key cannot see (an estimate
# whose status ranks it lower) still lands the right rows on the page.
_PAGE_SCORING_MARGIN = 25
_CANDIDATE_CACHE_MAX_ENTRIES = 32
_eligible_rows_cache: dict[tuple, tuple[float, list[CandidateRow], dict[str, int]]] = {}
_evaluation_cache: dict[tuple, dict[tuple[Any, str], Any]] = {}
_plots_cache: dict[tuple, dict[str, Plot]] = {}
_plots_cache_pins: dict[tuple, tuple] = {}
# Fully filtered-and-scored candidate lists per (deed, group, slot, filters, snapshot): a page
# or sort change on the same picker view re-sorts a cached list instead of re-filtering the
# pool and re-scoring. Bounded like the others; invalidated by any snapshot write.
_matching_cache: dict[tuple, tuple[list[CandidateRow], dict[str, int]]] = {}
_upgrade_printings_cache: dict[tuple, tuple[dict[tuple[Any, ...], list[dict[str, Any]]], tuple]] = {}
_candidate_cache_lock = threading.Lock()
# the heavy per-snapshot pool builds (`build_plots`, `_eligible_card_rows` over a
# 124,974-card whale pool) run in ONE bounded worker. Two picker opens arriving together
# queue behind each other instead of building the same pool twice in parallel; the second
# re-checks the cache once it is admitted and usually finds the first one's result. The
# automatic post-publish prewarm is gone -- the first explicit picker open pays this once.
_heavy_lane = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ceres-picker-build")
_heavy_lane_local = threading.local()


def _in_heavy_lane(cached: Any, build: Any) -> Any:
    """Run `build()` on the heavy queue unless `cached()` already has the answer. Re-entrant:
    a build that runs on the queue itself executes inline instead of waiting on its own worker."""
    hit = cached()
    if hit is not None:
        return hit
    if getattr(_heavy_lane_local, "on_lane", False):
        return build()

    def job() -> Any:
        _heavy_lane_local.on_lane = True
        try:
            hit = cached()
            return hit if hit is not None else build()
        finally:
            _heavy_lane_local.on_lane = False

    return _heavy_lane.submit(job).result()


def _snapshot_identity(report: dict[str, Any]) -> tuple:
    """Identity of the parts of a snapshot the picker reads: the `plots` and `cards` LIST
    objects (a hydrated copy of a snapshot is a new dict around the same lists, so these are
    stable across requests and distinct across snapshots), plus a cheap content fingerprint.
    Callers that cache under this key also pin the two lists in the cached value, so an id
    can never be recycled under a live entry -- the first cut keyed on content alone and two
    test fixtures with the same timestamps and counts read each other's scores."""
    plots = report.get("plots") or []
    cards = report.get("cards") or []
    coverage = report.get("coverage") or {}
    collection = coverage.get("collection") or {}
    pool = report.get("card_pool") or {}
    plot_marks = tuple(
        (str(p.get("deed_uid")), (p.get("asset_fetch") or {}).get("status"), p.get("observed_at"))
        for p in plots if isinstance(p, dict)
    )
    first = cards[0] if cards and isinstance(cards[0], dict) else {}
    last = cards[-1] if cards and isinstance(cards[-1], dict) else {}
    return (
        id(plots), id(cards),
        str(report.get("generated_at")), len(plots), plot_marks,
        len(cards), str(first.get("asset_ref")), str(last.get("asset_ref")),
        str(collection.get("fetched_at") or pool.get("fetched_at")),
    )


def _snapshot_pins(report: dict[str, Any]) -> tuple:
    """The objects a `_snapshot_identity` key is made of; stored beside every cached value."""
    return (report.get("plots"), report.get("cards"))


def _evict_oldest_locked(cache: dict) -> None:
    while len(cache) > _CANDIDATE_CACHE_MAX_ENTRIES:
        cache.pop(next(iter(cache)), None)


def _clone_row(row: CandidateRow) -> CandidateRow:
    """Shallow copy of a CandidateRow. `dataclasses.replace` was 40% of a warm picker request
    at ~4,000 rows (profiled); a `__dict__` copy is the same object, ten times cheaper."""
    clone = object.__new__(CandidateRow)
    clone.__dict__.update(row.__dict__)
    return clone


def _eligible_card_rows_cached(
    report: dict[str, Any], details_by_id: dict[int, dict[str, Any]], player: str, now: datetime,
    group_id: str, bucket: str, plots: dict[str, Plot],
) -> tuple[list[CandidateRow], dict[str, int]]:
    """`_eligible_card_rows`, memoised for `_ELIGIBLE_ROWS_TTL_SECONDS`. The returned rows are
    SHARED and must not be written to: `rank_candidates` clones a row the moment it selects
    it for scoring ( -- cloning all 91,798 rows of a whale pool up front cost 0.9 s per
    request; cloning only the selected ones is proportional to the answer, not the pool)."""
    key = (_snapshot_identity(report), str(player), group_id, bucket, id(_eligible_card_rows))

    def cached() -> tuple[list[CandidateRow], dict[str, int]] | None:
        with _candidate_cache_lock:
            hit = _eligible_rows_cache.get(key)
        if hit is not None and time.monotonic() - hit[0] <= _ELIGIBLE_ROWS_TTL_SECONDS:
            return hit[1], dict(hit[2])
        return None

    def build() -> tuple[list[CandidateRow], dict[str, int]]:
        rows, excluded = _eligible_card_rows(report, details_by_id, player, now, group_id, bucket, plots)
        with _candidate_cache_lock:
            _eligible_rows_cache[key] = (time.monotonic(), rows, dict(excluded), _snapshot_pins(report))
            _evict_oldest_locked(_eligible_rows_cache)
        return rows, excluded

    return _in_heavy_lane(cached, build)


def _plots_cached(report: dict[str, Any], details_by_id: dict[int, dict[str, Any]]) -> dict[str, Plot]:
    """`build_plots` + `attach_colors`, memoised per snapshot (0.6 s on 6,303 plots)."""
    key = (_snapshot_identity(report), id(build_plots), id(attach_colors))

    def cached() -> dict[str, Plot] | None:
        with _candidate_cache_lock:
            return _plots_cache.get(key)

    def build() -> dict[str, Plot]:
        plots = build_plots(report)
        attach_colors(plots, details_by_id)
        with _candidate_cache_lock:
            _plots_cache[key] = plots
            _plots_cache_pins[key] = _snapshot_pins(report)
            _evict_oldest_locked(_plots_cache)
            for stale in [k for k in _plots_cache_pins if k not in _plots_cache]:
                _plots_cache_pins.pop(stale, None)
        return plots

    return _in_heavy_lane(cached, build)


def _upgrade_printings_cached(report: dict[str, Any]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    """: a printing index built once per snapshot for picker copy counts."""
    key = _snapshot_identity(report)

    def cached():
        with _candidate_cache_lock:
            hit = _upgrade_printings_cache.get(key)
        return hit[0] if hit is not None else None

    def build():
        printings: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for raw in report.get("cards", []):
            facts = _upgrade_facts(raw)
            if facts is not None:
                identity = _upgrade_identity(facts)
                if identity is not None:
                    printings.setdefault(identity, []).append(facts)
        with _candidate_cache_lock:
            _upgrade_printings_cache[key] = (printings, _snapshot_pins(report))
            _evict_oldest_locked(_upgrade_printings_cache)
        return printings

    return _in_heavy_lane(cached, build)


def clear_candidate_caches() -> None:
    with _candidate_cache_lock:
        _upgrade_printings_cache.clear()
        _eligible_rows_cache.clear()
        _evaluation_cache.clear()
        _plots_cache.clear()
        _matching_cache.clear()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return default


def _validated_estimate(estimate: dict[str, Any]) -> dict[str, Any]:
    result = dict(estimate)
    status = result.get("status")
    if status not in {"point", "range", "unknown", "not_applicable"}:
        raise ValueError("candidate estimate has an invalid status")
    value, low, high = result.get("value"), result.get("low"), result.get("high")
    numbers = (value, low, high)
    if any(number is not None and (not isinstance(number, (int, float)) or not math.isfinite(float(number))) for number in numbers):
        raise ValueError("candidate estimates must contain only finite numbers or null")
    if status == "point" and (value is None or low != value or high != value):
        raise ValueError("point estimates require identical finite value and bounds")
    if status == "range" and (value is not None or low is None or high is None or low > high):
        raise ValueError("range estimates require ordered finite bounds and a null value")
    if status in {"unknown", "not_applicable"} and any(number is not None for number in numbers):
        raise ValueError("non-point estimates cannot contain numbers")
    result.setdefault("reason", None)
    return result


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _true(value: Any) -> bool:
    return value is True or value == 1 or (isinstance(value, str) and value.lower() == "true")


def _rarity(detail: dict[str, Any], facts: dict[str, Any]) -> str | None:
    value = detail.get("rarity", facts.get("rarity"))
    if isinstance(value, int):
        return RARITY_NAMES.get(value, str(value))
    return str(value).lower() if value not in (None, "") else None


def _picker_item_rarity(row: CandidateRow) -> str | None:
    explicit = _rarity(row.details, {}) or _rarity({}, row.facts)
    if explicit:
        return explicit
    raw_boost = row.facts.get("boost", row.details.get("boost"))
    if raw_boost in (None, ""):
        return None
    boost = round(_number(raw_boost), 6)
    return ITEM_RARITY_BY_BOOST.get(row.stake_type_uid, {}).get(boost)


def _splinter(detail: dict[str, Any], facts: dict[str, Any]) -> str | None:
    value = detail.get("color", facts.get("color"))
    if value in (None, ""):
        return None
    return SPLINTER_NAMES.get(str(value).lower(), str(value).lower())


def cooldown_state(
    bucket: str,
    stake_end_date: Any,
    stake_type_uid: str,
    now: datetime,
    *,
    survival_date: Any = None,
) -> dict[str, Any]:
    """Classify survival and unstake locks conservatively; unknown AND unverified stake classes are cooling.

    `ready` is the unchanged gating boolean (a hard refusal at staging time when
    False -- fail safe, not open, for any stake class without a confirmed zero-cooldown
    ruling). A future `survival_date` is an independent lock and its timestamp is an
    expected upper bound, not an appointment. `state` is separate: it names what the
    UI should tell the user, which is NOT the same question. A currently-staked
    card has never been unstaked, so it has no `stake_end_date` -- that used to
    collapse into the same `ready: False, reason: "cooldown evidence missing"`
    shape as a genuinely unrecognised stake class, and the UI rendered both as
    "cooling", which is a lie for the staked card (it isn't cooling; a cooldown
    just isn't applicable yet). `state` distinguishes: "ready" (no cooldown
    applies, or it has elapsed), "cooling" (a real `stake_end_date` is counting
    down), "staked" (currently staked, no cooldown evidence needed), "unknown"
    (an unrecognised stake class -- still fails safe, but the label must say
    UNKNOWN, not assert a countdown that does not exist), or "unverified" (a
    recognised stake class whose cooldown DURATION has never been published --
    same fail-safe result as "unknown", but a distinct label so the UI can say
    that a duration is missing rather than that the class itself is unrecognised).
    """
    kind = COOLDOWN_CLASS.get(stake_type_uid)
    survival_ends = _time(survival_date)
    if survival_ends is not None and survival_ends > now:
        ready_at = survival_ends.isoformat().replace("+00:00", "Z")
        return {
            "ready": False,
            "ready_at": ready_at,
            "class": kind or "unknown",
            "state": "survival_locked",
            "reason": f"dead from Survival mode until {ready_at}",
        }
    if kind == "items":
        return {"ready": True, "ready_at": None, "class": "items", "state": "ready"}
    if kind is None:
        return {"ready": False, "ready_at": None, "class": "unknown", "reason": "unrecognised stake class", "state": "unknown"}
    if bucket == "unstaked_apparent":
        return {"ready": True, "ready_at": None, "class": kind, "state": "ready"}
    if kind == "unverified":
        # Ready, with the missing duration carried as a warning. No asset is locked onto
        # a plot: an unpublished cooldown means Ceres cannot PROMISE when a restake will
        # be accepted, which the user is told -- it is not evidence that the move is
        # unsafe. The game rejects a too-early restake harmlessly, and every broadcast is
        # domain-verified afterwards regardless.
        return {"ready": True, "ready_at": None, "class": "unverified", "reason": "no published cooldown duration for this stake class; restake timing cannot be promised", "state": "unverified"}
    ended = _time(stake_end_date)
    if ended is None:
        if bucket == "staked":
            return {"ready": False, "ready_at": None, "class": kind, "reason": "currently staked", "state": "staked"}
        return {"ready": False, "ready_at": None, "class": kind, "reason": "cooldown evidence missing", "state": "unknown"}
    # The displayed cooldown end is exact; action safety margins belong only at initiation.
    ready_at = ended
    ready = ready_at <= now
    return {"ready": ready, "ready_at": ready_at.isoformat().replace("+00:00", "Z"), "class": kind, "state": "ready" if ready else "cooling"}


def power_satisfied(staking_facts: dict[str, Any]) -> bool:
    return any(_true(staking_facts.get(key)) for key in ("is_power_core_staked", "is_energized", "is_runi_staked"))


def _model_card(facts: dict[str, Any], detail: dict[str, Any], *, origin: str, bucket: str, worker: dict[str, Any] | None = None) -> MovableCard:
    worker = worker or {}
    return MovableCard(
        name=detail.get("name") or facts.get("name") or worker.get("name") or "Unknown card",
        color=detail.get("color") or facts.get("color") or "Gray",
        secondary_color=detail.get("secondary_color") or facts.get("secondary_color"),
        base=_number(worker.get("land_base_pp", facts.get("land_base_pp"))),
        current_harvest=_number(worker.get("total_harvest_pp")) if origin == "staked" else 0.0,
        origin=origin,
        source_uid=worker.get("stake_ref_uid"),
        source_rarity=None,
        bucket=bucket,
        dec_needed=facts.get("land_dec_stake_needed", worker.get("land_dec_stake_needed")),
        bcx=facts.get("bcx", worker.get("bcx")),
        gold=facts.get("gold", worker.get("gold")),
        card_set=facts.get("card_set", worker.get("card_set")),
        card_detail_id=facts.get("card_detail_id", worker.get("card_detail_id")),
        level=facts.get("level", worker.get("level")),
        asset_ref=worker.get("asset_ref", facts.get("asset_ref")),
        card_uid=worker.get("uid", facts.get("uid")),
        source_slot=worker.get("slot"),
        stake_end_date=facts.get("stake_end_date", worker.get("stake_end_date")),
    )


def _foil(detail: dict[str, Any], facts: dict[str, Any], card: MovableCard) -> Any:
    for source in (facts, detail):
        if source.get("foil") not in (None, ""):
            return source["foil"]
    return None


def _is_soulbound(facts: dict[str, Any]) -> bool:
    edition = _number(facts.get("edition"), -1)
    if edition not in {6, 10, 13, 16, 18}:
        return False
    if edition == 18 and _number(facts.get("foil"), 1 if facts.get("gold") else 0) > 0:
        return False
    if edition in {6, 16}:
        return True
    # Reward instances must be fully unbound; positive Land PP alone is insufficient.
    bcx = _number(facts.get("bcx"))
    unbound = _number(facts.get("bcx_unbound"))
    return not math.isfinite(bcx) or not math.isfinite(unbound) or bcx <= 0 or unbound < bcx


def _eligible_card_rows(
    report: dict[str, Any], details_by_id: dict[int, dict[str, Any]], player: str, now: datetime,
    group_id: str, requested_bucket: str = "all", plots: dict[str, Plot] | None = None,
) -> tuple[list[CandidateRow], dict[str, int]]:
    excluded = {"soulbound": 0, "soulkeep": 0, "market_listed": 0, "delegated": 0, "rented": 0, "unlock_locked": 0, "cooling": 0, "survival_locked": 0, "unknown_direction": 0, "missing_base": 0, "capacity": 0, "base_cap": 0, "same_plot": 0, "negative_net_gain": 0, "terrain_penalty": 0}
    rows: list[CandidateRow] = []
    if plots is None:
        plots = build_plots(report)
        attach_colors(plots, details_by_id)
    facts_by_ref = {} if requested_bucket == "idle" else {
        str(row.get("asset_ref")): row.get("facts", {}) for row in report.get("cards", []) if row.get("asset_ref")
    }
    wanted_type = GROUP_TO_SOURCE[group_id][2]

    def count(key: str, bucket: str) -> None:
        selected = requested_bucket == "all" or (requested_bucket == "idle" and bucket in IDLE_BUCKETS) or (requested_bucket == "staked" and bucket == "staked")
        if selected:
            excluded[key] += 1

    def add(facts: dict[str, Any], worker: dict[str, Any] | None, bucket: str) -> None:
        if worker is not None:
            stake_type_uid = worker.get("stake_type_uid", facts.get("stake_type_uid", "STK-LND-WKR"))
        else:
            # Unstaked collection cards never carry a stake_type_uid of their own -- the
            # snapshot only ever puts that field on staked/staged rows. Classify from the
            # card's own identity (card_detail_id) instead of defaulting to whatever group
            # the caller asked for: an unknown class must be refused, never relabelled as
            # the wanted type, or a plain monster card can be staged as a Runi.
            card_detail_id = facts.get("card_detail_id")
            stake_type_uid = "STK-LND-RUNI" if card_detail_id == RUNI_CARD_DETAIL_ID else facts.get("stake_type_uid", "STK-LND-WKR")
        if group_id == "runi" and stake_type_uid != wanted_type:
            return
        if group_id == "worker" and stake_type_uid == "STK-LND-RUNI":
            return
        if _number(facts.get("edition"), -1) in SOULKEEP_EDITIONS:
            count("soulkeep", bucket)
            return
        if _is_soulbound(facts):
            count("soulbound", bucket)
            return
        available, blocked_reason = classify_idle_availability(facts, now, player)
        direction, rental_reason, expiry = rental_direction(facts, player)
        if not available:
            if direction in {"out", "unknown"}:
                count("rented", bucket)
                if direction == "unknown":
                    count("unknown_direction", bucket)
            elif blocked_reason and blocked_reason.startswith("listed"):
                count("market_listed", bucket)
            elif blocked_reason and blocked_reason.startswith("delegated"):
                count("delegated", bucket)
            elif blocked_reason and blocked_reason.startswith("locked"):
                count("unlock_locked", bucket)
            else:
                count("rented", bucket)
            return
        unlock = _time(facts.get("unlock_date"))
        if unlock and unlock > now:
            count("unlock_locked", bucket)
            return
        cooling = cooldown_state(
            bucket,
            facts.get("stake_end_date", (worker or {}).get("stake_end_date")),
            stake_type_uid,
            now,
            survival_date=facts.get("survival_date"),
        )
        detail_id = facts.get("card_detail_id", (worker or {}).get("card_detail_id"))
        detail = details_by_id.get(detail_id, {}) if detail_id is not None else {}
        card = _model_card(facts, detail, origin="staked" if bucket == "staked" else "idle", bucket=bucket, worker=worker)
        if card.base <= 0:
            count("missing_base", bucket)
            return
        rows.append(CandidateRow(card, detail, facts, bucket, stake_type_uid, cooling, "in" if direction == "in" else None, expiry))

    if group_id in ("worker", "runi"):
        for row in report.get("cards", []):
            bucket = row.get("classification", {}).get("bucket")
            if bucket in IDLE_BUCKETS or bucket in COOLING_BUCKETS:
                facts = dict(row.get("facts", {}))
                facts["asset_ref"] = row.get("asset_ref")
                add(facts, None, bucket)
        if requested_bucket != "idle":
            for worker in report.get("workers", []):
                if group_id == "runi" and worker.get("stake_type_uid", "STK-LND-WKR") != wanted_type:
                    continue
                if group_id == "worker" and worker.get("stake_type_uid") == "STK-LND-RUNI":
                    continue
                facts = dict(facts_by_ref.get(str(worker.get("asset_ref")), {}))
                for key, value in worker.items():
                    if value is not None and facts.get(key) in (None, ""):
                        facts[key] = value
                add(facts, worker, "staked")
    staked_card_uids = {
        row.card.card_uid
        for row in rows
        if row.bucket == "staked" and row.card.card_uid is not None
    }
    rows = [
        row
        for row in rows
        if row.bucket == "staked"
        or row.card.card_uid is None
        or row.card.card_uid not in staked_card_uids
    ]
    return rows, excluded


def _item_rows(report: dict[str, Any], group_id: str, now: datetime, released_asset_refs: set[str] | None = None) -> tuple[list[CandidateRow], dict[str, int]]:
    family_name = group_id
    family = (report.get("unassigned_items") or {}).get(family_name)
    if not isinstance(family, dict) or family.get("capturable") is not True:
        raise LookupError(f"snapshot item family {family_name!r} is unavailable")
    aggregates = {
        item.get("item_detail_id"): item
        for item in (family.get("items") or family.get("cards") or [])
        if isinstance(item, dict) and item.get("item_detail_id") is not None
    }
    uids = family.get("uids")
    if uids is not None:
        entries = []
        for raw_uid in uids:
            uid = raw_uid.get("uid") if isinstance(raw_uid, dict) else raw_uid
            text_uid = str(uid or "")
            parts = text_uid.split("-")
            try:
                detail_id = int(parts[1]) if len(parts) > 1 else None
            except ValueError:
                detail_id = None
            metadata = dict(aggregates.get(detail_id) or {})
            metadata.update({"uid": uid, "asset_ref": uid, "item_detail_id": detail_id})
            entries.append(metadata)
    else:
        entries = family.get("items") or family.get("cards") or []
    entries = list(entries)
    idle_refs = {str(item.get("asset_ref") or item.get("uid")) for item in entries if isinstance(item, dict)}
    for item in report.get("bonus_assets", []):
        ref = str(item.get("asset_ref") or "")
        if (ref in (released_asset_refs or set()) and ref not in idle_refs
                and item.get("stake_type_uid") == GROUP_TO_SOURCE[group_id][2]
                and item.get("stake_ref_uid")):
            entries.append(dict(item))
    catalogue = item_catalogue_lookup(report)
    rows: list[CandidateRow] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        facts = item.get("facts", item)
        if not isinstance(facts, dict):
            continue
        detail_id = item.get("item_detail_id", facts.get("item_detail_id"))
        catalogue_item = catalogue.get(detail_id, {})
        ref = item.get("asset_ref") or facts.get("asset_ref") or item.get("uid") or facts.get("uid")
        facts = dict(facts)
        for key in ("name", "boost", "description", "image_filename"):
            if facts.get(key) in (None, "") and catalogue_item.get(key) not in (None, ""):
                facts[key] = catalogue_item[key]
        facts.setdefault("card_detail_id", detail_id)
        facts.setdefault("level", item.get("level", 1))
        facts.setdefault("asset_ref", ref)
        facts.setdefault("uid", item.get("uid"))
        card = MovableCard(
            name=(catalogue_item.get("name") if str(item.get("name", "")).startswith("Item ") else item.get("name")) or facts.get("name") or group_id,
            color="Gray", base=_number(item.get("base_pp", facts.get("land_base_pp"))), current_harvest=0.0,
            origin="staked" if item.get("stake_ref_uid") else "idle", source_uid=item.get("stake_ref_uid"), asset_ref=ref, card_uid=item.get("uid", facts.get("uid")),
            bucket="staked" if item.get("stake_ref_uid") else "unstaked_apparent", card_detail_id=detail_id,
            level=item.get("level", facts.get("level")),
        )
        stake_type_uid = item.get("stake_type_uid", facts.get("stake_type_uid", GROUP_TO_SOURCE[group_id][2]))
        rows.append(CandidateRow(card, catalogue_item, facts, card.bucket, stake_type_uid, cooldown_state(card.bucket, None, stake_type_uid, now, survival_date=None)))
    return rows, {"market_listed": 0, "delegated": 0, "rented": 0, "unlock_locked": 0, "cooling": 0, "survival_locked": 0, "unknown_direction": 0, "missing_base": 0, "capacity": 0, "base_cap": 0, "same_plot": 0, "negative_net_gain": 0, "terrain_penalty": 0}


def _matches(row: CandidateRow, q: str | None, rarity: str | None, splinter: str | None, edition: int | None, min_pp: float | None, bucket: str, max_pp: float | None = None) -> bool:
    facts, detail, card = row.facts, row.details, row.card
    if bucket == "idle" and row.bucket not in IDLE_BUCKETS:
        return False
    if bucket not in {"all", "idle"} and row.bucket != bucket:
        return False
    name = card.name or ""
    if q and q.lower() not in name.lower():
        return False
    if rarity and _picker_item_rarity(row) != rarity.lower():
        return False
    # several splinters may be chosen at once, sent comma-separated ("fire,water").
    wanted = {SPLINTER_NAMES.get(part.strip(), part.strip()) for part in (splinter or "").lower().split(",") if part.strip()}
    if wanted and not wanted.intersection(value.lower() for value in card_splinters(detail, facts)):
        return False
    if edition is not None and _number(facts.get("edition"), _number(detail.get("edition"))) != edition:
        return False
    if min_pp is not None and card.base < min_pp:
        return False
    return max_pp is None or card.base <= max_pp


def _bucket_selected(row_bucket: str, requested_bucket: str) -> bool:
    return requested_bucket == "all" or (requested_bucket == "idle" and row_bucket in IDLE_BUCKETS) or (requested_bucket == "staked" and row_bucket == "staked")


def _has_terrain_penalty(row: CandidateRow) -> bool:
    return row.stake_type_uid == "STK-LND-WKR" and row.terrain is not None and math.isfinite(row.terrain) and row.terrain < 0


def construction_sort_key(row: CandidateRow) -> tuple[Any, ...]:
    # Magnitude leads the tuple, NOT estimate status -- an above-cap card's row was previously
    # demoted behind every point-status row regardless of its own PP (measured: the 6 highest
    # base-PP cards on a live plot were previously buried at positions 2503-2508 of 4,060
    # unreachable at the picker's 100-row page size. `rank` now only breaks a tie between rows
    # of equal magnitude, so a known point estimate still wins that tie.
    estimate = row.construction_pp or {}
    status = estimate.get("status", "unknown")
    rank = {"point": 0, "range": 1, "unknown": 2, "not_applicable": 3}.get(status, 2)
    if status == "point":
        magnitude = _number(estimate.get("value"), float("-inf"))
    elif status == "range":
        magnitude = _number(estimate.get("low"), float("-inf"))
    elif row.terrain is not None and math.isfinite(row.terrain):
        # No point/range estimate exists, so fall back to this row's own stage-one figure
        # base_pp * (1 + terrain_boost), the terrain-adjusted picker ordering value.
        magnitude = row.card.base * (1 + row.terrain)
    else:
        magnitude = row.card.base
    primary = -magnitude
    impact = row.plot_impact or {}
    impact_tie = -_number(impact.get("value"), float("-inf")) if impact.get("status") == "point" else float("inf")
    return (primary, rank, impact_tie, row.card.name.casefold(), str(row.card.asset_ref or ""))


def boost_sort_key(row: CandidateRow) -> tuple[Any, ...]:
    """Descending production-boost order for item rows (Totem/Title) -- the only
    sort the redesigned item pickers offer besides Name. Boost is a flat catalogue
    fact, never derived from plot_impact/construction_pp (those are `not_applicable`
    for items, per the internal rules's item-scoring rule), so this is its own key rather
    than a variant of construction_sort_key/net_gain."""
    boost = row.facts.get("boost")
    try:
        boost_value = float(boost) if boost is not None else None
    except (TypeError, ValueError):
        boost_value = None
    primary = -boost_value if boost_value is not None else float("inf")
    return (primary, row.card.name.casefold(), str(row.card.asset_ref or ""))


def _resolve_real_slot(raw_slot: int | None) -> int | None:
    """The picker sends the 0-based UI index into `group.slots[]`
    (`app.js:2361`'s `candidateQuery`); `editorGroups` (`app.js:2179-2183`) builds that
    array so `workerSlots[index]` is the worker whose REAL, Land-API `slot` field
    (1-based; Runi is reserved at 0, never a worker) equals `index + 1`. Returns `None`
    -- "does not resolve to a real slot" -- for anything that isn't a genuine
    non-negative integer, so the caller can fail safe rather than guess."""
    if raw_slot is None:
        return None
    try:
        value = int(raw_slot)
    except (TypeError, ValueError):
        return None
    if isinstance(raw_slot, bool) or value < 0:
        return None
    return value + 1


def _donor_delta(
    row: CandidateRow,
    plots: dict[str, Plot],
    plot_rows_by_id: dict[str, dict[str, Any]],
    details_by_id: dict[int, dict[str, Any]],
    group_id: str | None = None,
) -> tuple[float | None, str | None] | None:
    """The donor plot's own PP change when a staked candidate leaves it.

    None: there is no donor plot to price (not a staked card, or its plot is unknown).
    `(value, None)`: the donor-side change is a point value.
    `(None, reason)`: the donor plot holds the card but its change cannot be derived
    (e.g. `cap_allocation_failed`); the move's plot impact is then unknown, never the
    destination gain alone."""
    if row.bucket != "staked" or not row.card.source_uid or row.card.source_uid not in plots:
        return None
    donor = plots[row.card.source_uid]
    donor_row = plot_rows_by_id.get(row.card.source_uid)
    if donor_row is None:
        return None
    staking_facts = donor_row.get("staking_facts", {})
    before_powered = power_satisfied(staking_facts)
    after_powered = before_powered
    before_mult = donor.mult
    after_mult = donor.mult
    source_group = group_id or ("runi" if row.stake_type_uid == "STK-LND-RUNI" else None)
    source_rule = POWER_SOURCE_RULES.get(source_group)
    if source_rule and row.stake_type_uid == source_rule[0]:
        after_facts = dict(staking_facts)
        after_facts[source_rule[1]] = False
        after_powered = power_satisfied(after_facts)
        if source_rule[2] is not None:
            after_mult -= _number(staking_facts.get(source_rule[2]))
    evaluations = evaluate_candidates_for_plot(
        donor,
        [replace(row.card, base=0.0, current_harvest=0.0)],
        target_slot_occupant=row.card,
        details_by_id=details_by_id,
        base_pp_ceiling=donor.base_cap,
        before_powered=before_powered,
        after_powered=after_powered,
        before_mult=before_mult,
        after_mult=after_mult,
    )
    if not evaluations:
        return None
    evaluation = evaluations[0]
    if evaluation.compatibility_score is None:
        return None, (evaluation.plot_impact or {}).get("reason") or "donor_plot_impact_unknown"
    return evaluation.compatibility_score, None


def plot_removal_delta(
    report: dict[str, Any],
    deed_uid: str,
    asset_refs: list[str],
    details_by_id: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The TRUE plot-wide harvest-PP delta of removing one or more staked assets from a
    single plot in one batch (e.g. `clear_plot`). It shares the swap path's authoritative
    `plot_abilities_boost`/`plot_bloodline_boost` roster functions while grounding the
    before-state in observed snapshot PP so capped plots are not repriced from raw base PP.

    The bug this fixes: the client priced a bare removal at only the removed card's own
    `current_harvest_pp`, ignoring that `card_abilities_boost` and `card_bloodlines_boost`
    are PLOT-WIDE broadcasts (the internal rules, validated 0 exceptions / 238 workers / 107 plots)
    -- removing a sole ability/bloodline holder costs EVERY other worker on the plot too.

    Multiple simultaneous removals are evaluated against the SAME final remaining roster
    (not summed independently) so an active bloodline pair broken by removing either half
    is only charged once, and cross-removal interactions are never double-counted.

    Handles two kinds of removed assets:
      - numbered-slot worker cards living in `plot.cards` (the ability holders/companions).
      - plot-level items (Totem/Title/Power Core) that shift the plot's flat
        multiplier or its powered state instead of occupying a card slot.

    Runi is stageable () and is priced here as an "item"-kind removal (it is
    excluded from `plot.cards` by `build_plots`, so it never occupies a numbered worker
    slot) that ALSO carries its own harvest/construction PP -- unlike Totem/Title/Power
    Core, which shift the plot's flat multiplier or powered state but produce no PP of
    their own. A Runi still retained (not requested for removal) keeps contributing to
    flat multiplier/power losses exactly as before.

    The observed pre-removal harvest and construction PP are authoritative here. The measured
    descending-slot cap cascade supplies any survivor relief caused by the removal.
    """
    if not asset_refs:
        raise ValueError("asset_refs must be a non-empty list")
    details_by_id = details_by_id if details_by_id is not None else load_details_by_id()
    plots = build_plots(report)
    attach_colors(plots, details_by_id)
    plot = plots.get(deed_uid)
    if plot is None:
        raise KeyError(deed_uid)
    # the route prices from the saved snapshot, which carries no card collection, so
    # `build_plots` leaves card levels empty and every ability prices at its weakest (removing
    # Toiling Ox on 065-09-083 priced at -14,575 instead of -125,875). Take the levels the board
    # already shows on its tiles instead of loading the collection (maintainer ruling ).
    from .board import _level_by_asset_ref
    board_levels = _level_by_asset_ref(report)
    for card in plot.cards:
        if card.get("level") is None:
            card["level"] = board_levels.get(card.get("asset_ref"))
    plot_row = next((row for row in report.get("plots", []) if str(row.get("deed_uid")) == str(deed_uid)), None)
    if plot_row is None:
        raise KeyError(deed_uid)
    staking_facts = plot_row.get("staking_facts", {})

    requested = {str(ref) for ref in asset_refs}
    card_by_ref = {str(c.get("asset_ref")): c for c in plot.cards if c.get("asset_ref") is not None}
    worker_by_ref = {
        str(w.get("asset_ref")): w
        for w in report.get("workers", [])
        if w.get("asset_ref") is not None and str(w.get("stake_ref_uid")) == str(deed_uid)
    }
    item_by_ref = {
        str(item.get("asset_ref")): item
        for item in report.get("bonus_assets", [])
        if item.get("asset_ref") is not None and str(item.get("stake_ref_uid")) == str(deed_uid)
    }
    # The Runi lives in `report["workers"]` on reserved slot 0, never in `bonus_assets`
    # `worker_by_ref` already carries it, so it needs its own lookup rather than falling
    # into `item_by_ref`.
    runi_by_ref = {
        ref: worker for ref, worker in worker_by_ref.items()
        if worker.get("stake_type_uid") == "STK-LND-RUNI"
    }

    removed_cards: list[dict[str, Any]] = []
    removed_items: list[dict[str, Any]] = []
    not_found: list[str] = []
    for ref in requested:
        if ref in card_by_ref:
            removed_cards.append(card_by_ref[ref])
        elif ref in item_by_ref:
            removed_items.append(item_by_ref[ref])
        elif ref in runi_by_ref:
            removed_items.append(runi_by_ref[ref])
        else:
            not_found.append(ref)
    if not_found:
        raise ValueError(f"requested asset_refs are not staked on deed {deed_uid}: {', '.join(sorted(not_found))}")
    removed_item_refs = {str(item.get("asset_ref")) for item in removed_items}

    before_powered = power_satisfied(staking_facts)
    after_flags = dict(staking_facts)
    after_mult = plot.mult
    for item in removed_items:
        stake_type = str(item.get("stake_type_uid") or "")
        field = REMOVAL_MULT_FIELD_BY_TYPE.get(stake_type)
        if field is not None:
            after_mult -= _number(staking_facts.get(field))
        _apply_staged_power_flag(after_flags, item, -1.0)
    after_powered = power_satisfied(after_flags)

    removed_refs = {str(c.get("asset_ref")) for c in removed_cards}
    remaining_cards = [c for c in plot.cards if str(c.get("asset_ref")) not in removed_refs]

    # slot power. A Runi is outside the five regular worker slots and powers
    # four of them. Removing the plot's Power Core -- or its only
    # Energized worker -- therefore unpowers the last filled slot even though the plot as a
    # whole stays "powered" by its Runi. That card then earns nothing and its abilities and
    # bloodline stop broadcasting; it is priced as lost here instead of as zero change.
    def _energized(card: dict[str, Any]) -> bool:
        try:
            row = land_ability_row(int(card.get("card_detail_id")), int(card.get("level") or 1), details_by_id)
        except (TypeError, ValueError):
            return False
        return "ENERGIZED" in ability_codes(row)

    if _true(after_flags.get("is_energized")) and any(_energized(c) for c in removed_cards) \
            and not any(_energized(c) for c in remaining_cards):
        after_flags["is_energized"] = False
        after_powered = power_satisfied(after_flags)
    capacity_index = structure_capacity_index(report)

    def _slot(card: dict[str, Any]) -> int:
        try:
            return int(card.get("slot"))
        except (TypeError, ValueError):
            return 0

    powered_before = _worker_slot_capacity(plot_row, staking_facts, capacity_index)
    powered_after = _worker_slot_capacity(plot_row, after_flags, capacity_index)
    power_lost_cards = [
        c for c in remaining_cards
        if after_powered and powered_after < _slot(c) <= powered_before
    ]
    power_lost_refs = {str(c.get("asset_ref")) for c in power_lost_cards}
    producing_cards = [c for c in remaining_cards if str(c.get("asset_ref")) not in power_lost_refs]
    # Only a Runi NOT itself being removed in this batch still contributes to flat
    # multiplier/power losses below -- one being removed is priced through
    # `own_harvest_pp_delta`/`own_construction_pp_delta` instead, exactly once.
    retained_runis = [
        worker for ref, worker in runi_by_ref.items()
        if ref not in removed_item_refs
    ]
    removed_runis = [worker for ref, worker in runi_by_ref.items() if ref in removed_item_refs]

    before_abilities = plot_abilities_boost(plot.cards, plot.resource, details_by_id)
    before_bloodline = plot_bloodline_boost(plot.cards, details_by_id)
    after_abilities = plot_abilities_boost(producing_cards, plot.resource, details_by_id)
    after_bloodline = plot_bloodline_boost(producing_cards, details_by_id)
    cap_relief_harvest = 0.0
    cap_relief_base = 0.0
    before_allocation = _cap_allocation(plot.cards, plot.base_cap)
    after_allocation = _cap_allocation(remaining_cards, plot.base_cap)
    cap_derivation_failed = before_allocation is None or after_allocation is None
    if not cap_derivation_failed and plot.base_cap is not None:
        relief_factor = (plot.mult + after_abilities + after_bloodline) if after_powered else 0.0
        for card in remaining_cards:
            ref = str(card["asset_ref"])
            increase = max(0.0, after_allocation[ref] - before_allocation[ref])
            cap_relief_base += increase
            cap_relief_harvest += increase * (1 + plot.terrain(card["color"], card.get("secondary_color"))) * relief_factor
    # A Runi produces its own harvest/construction PP, unlike Totem/Title/Power Core
    # (which shift the plot's flat multiplier or powered state but produce none of
    # their own) -- so a removed Runi's own PP must be subtracted here too, exactly
    # like a removed worker card's `cur`/construction PP.
    own_harvest_pp_delta = -sum(_number(c.get("cur")) for c in removed_cards) - sum(_number(r.get("total_harvest_pp")) for r in removed_runis)

    def _observed_construction(card: dict[str, Any]) -> float:
        worker = worker_by_ref.get(str(card.get("asset_ref")), {})
        construction_pp = _number(worker.get("total_construction_pp"), default=float("nan"))
        if construction_pp != construction_pp:
            return _number(card.get("base")) * (1 + plot.terrain(card.get("color", "Gray"), card.get("secondary_color")))
        return construction_pp

    broadcast_rate_delta = (after_abilities - before_abilities) + (after_bloodline - before_bloodline)
    plot_ability_delta = sum(
        _observed_construction(card) * broadcast_rate_delta
        for card in producing_cards
        if worker_by_ref.get(str(card.get("asset_ref")), {}).get("stake_type_uid", "STK-LND-WKR") != "STK-LND-RUNI"
    )
    power_loss_delta = -sum(_number(c.get("cur")) for c in power_lost_cards)
    if not after_powered:
        observed_total = (
            sum(_number(c.get("cur")) for c in plot.cards)
            + sum(_number(runi.get("total_harvest_pp")) for runi in retained_runis)
            + sum(_number(runi.get("total_harvest_pp")) for runi in removed_runis)
        )
        harvest_pp_delta = -observed_total if before_powered else 0.0
    else:
        survivor_delta = cap_relief_harvest + power_loss_delta
        for card in producing_cards:
            worker = worker_by_ref.get(str(card.get("asset_ref")), {})
            construction_pp = _observed_construction(card)
            fixed_multiplier_delta = after_mult - plot.mult
            broadcast_delta = 0.0
            # The editor's worker-removal total is defined over the numbered worker-slot pool
            # Runi occupies its reserved slot and retains its observed contribution here.
            if worker.get("stake_type_uid", "STK-LND-WKR") != "STK-LND-RUNI":
                broadcast_delta = broadcast_rate_delta
            survivor_delta += construction_pp * (fixed_multiplier_delta + broadcast_delta)
        survivor_delta += sum(_number(runi.get("total_construction_pp")) * (after_mult - plot.mult) for runi in retained_runis)
        harvest_pp_delta = own_harvest_pp_delta + survivor_delta
    plot_residual_delta = harvest_pp_delta - own_harvest_pp_delta

    # Stage-one (Base PP / construction) delta is distinct from the harvest-stage relief.
    own_construction_pp_delta = -sum(_observed_construction(card) for card in removed_cards) - sum(_observed_construction(r) for r in removed_runis)
    own_base_pp_delta = (
        -sum(_number(c.get("base")) for c in removed_cards)
        -sum(_number(r.get("land_base_pp")) for r in removed_runis)
    )

    base_filled = sum(_number(c.get("base")) for c in plot.cards)
    base_remaining = sum(_number(c.get("base")) for c in remaining_cards)
    over_cap_before = plot.base_cap is not None and base_filled > plot.base_cap
    over_cap_after = plot.base_cap is not None and base_remaining > plot.base_cap
    if not over_cap_before:
        cap_relief = {
            "status": "not_applicable", "value": None,
            "plot_over_cap_before": False, "plot_over_cap_after_removal": over_cap_after,
            "note": "plot's base PP pool is not over cap; no cap-allocation ambiguity applies",
        }
    else:
        cap_relief = {
            "status": "unknown" if cap_derivation_failed else "point",
            "value": None if cap_derivation_failed else cap_relief_harvest,
            "low": None if cap_derivation_failed else cap_relief_harvest,
            "high": None if cap_derivation_failed else cap_relief_harvest,
            "base_pp": None if cap_derivation_failed else cap_relief_base,
            "plot_over_cap_before": True, "plot_over_cap_after_removal": over_cap_after,
            "reason": "cap_derivation_failed" if cap_derivation_failed else None,
            "evidence": {"tier": "measurement-sourced", "source": "library/land-worker-abilities.md §10a", "cascade": "n=1"},
            "note": "the measured descending-slot, floored-at-zero cap cascade supplies survivor relief",
        }

    def _describe(record: dict[str, Any], kind: str) -> dict[str, Any]:
        # Stage-one (Base PP) own value -- cards and the Runi both carry real construction
        # PP; Totem/Title/Power Core shift the plot's flat multiplier instead and never
        # contribute to the construction stage, so this stays null for them.
        is_runi_item = kind == "item" and record.get("stake_type_uid") == "STK-LND-RUNI"
        return {
            "asset_ref": record.get("asset_ref"), "name": record.get("name"), "kind": kind,
            "stake_type_uid": record.get("stake_type_uid", "STK-LND-WKR" if kind == "card" else None),
            "own_harvest_pp": _number(record.get("cur") if kind == "card" else record.get("total_harvest_pp")),
            "own_construction_pp": _observed_construction(record) if (kind == "card" or is_runi_item) else None,
            "own_base_pp": _number(record.get("base")) if kind == "card" else _number(record.get("land_base_pp")) if is_runi_item else None,
        }

    def active_bloodlines(roster: list[dict[str, Any]]) -> set[str]:
        sub_types = [details_by_id.get(card.get("card_detail_id"), {}).get("sub_type") for card in roster]
        active: set[str] = set()
        for index, card in enumerate(roster):
            holder = bloodline_holder(card.get("card_detail_id"), card.get("level"), details_by_id)
            if holder is not None and any(other != index and sub_types[other] == holder[1] for other in range(len(roster))):
                active.add(str(holder[1]))
        return active

    broken = sorted(active_bloodlines(plot.cards) - active_bloodlines(remaining_cards))
    # (maintainer: "The toil and kin warning is wrong and cannot grow smaller, when TWO cards instead of 1 is
    # unstaked"): the pair's loss is the Toil and Kin bonus the plot carries today, over every worker it lifts
    # including the ones this batch removes. Counting survivors only made the figure shrink as more cards left.
    bloodline_rate_loss = before_bloodline - plot_bloodline_boost(remaining_cards, details_by_id)
    bloodline_pp_delta = -sum(
        _observed_construction(card) * bloodline_rate_loss
        for card in plot.cards
        if worker_by_ref.get(str(card.get("asset_ref")), {}).get("stake_type_uid", "STK-LND-WKR") != "STK-LND-RUNI"
    )
    removed_holder_types = {
        str(holder[1])
        for card in removed_cards
        for holder in [bloodline_holder(card.get("card_detail_id"), card.get("level"), details_by_id)]
        if holder is not None
    }
    notices = [
        format_notice(
            "bloodline_pair_deactivated",
            context={
                "deed_uid": deed_uid, "bloodline": "Toil and Kin", "sub_type": sub_type,
                "pp_delta": bloodline_pp_delta,
            },
            reason="the active pair is broken by this removal",
            action={"id": "open_bloodline_pairs", "label": f"Find a matching {sub_type} {'holder' if sub_type in removed_holder_types else 'companion'}", "role": "replacement_holder" if sub_type in removed_holder_types else "companion_for_holder", "sub_type": sub_type},
        )
        for sub_type in broken
    ]
    # same rule for the plot-wide abilities (DD, resource boosts, ...): the value they carry today over every
    # worker, the Toil and Kin part excluded (it has its own line), so removing more cards never shows a smaller loss.
    ability_pp_delta = -sum(
        _observed_construction(card) * (before_abilities - after_abilities)
        for card in plot.cards
        if worker_by_ref.get(str(card.get("asset_ref")), {}).get("stake_type_uid", "STK-LND-WKR") != "STK-LND-RUNI"
    )
    if ability_pp_delta < 0:
        notices.append(format_notice("plot_ability_loss", context={"deed_uid": deed_uid, "pp_delta": ability_pp_delta}, reason="plot-wide ability value changed"))
    current_workers = _number(staking_facts.get("worker_count")) or 0
    current_pp = _number(staking_facts.get("total_harvest_pp")) or 0
    if before_powered and not after_powered and (current_workers > 0 or current_pp > 0):
        notices.append(format_notice("power_source_removed_warning", context={"deed_uid": deed_uid}, reason="the plot loses its last proven power source"))

    return {
        "deed_uid": deed_uid,
        "removed": [_describe(c, "card") for c in removed_cards] + [_describe(w, "item") for w in removed_items],
        "not_found": sorted(not_found),
        "own_harvest_pp_delta": own_harvest_pp_delta,
        "plot_residual_delta": plot_residual_delta,
        "plot_ability_delta": plot_ability_delta,
        "harvest_pp_delta": harvest_pp_delta,
        # Stage-one (Base PP) delta -- see the comment above `own_construction_pp_delta`.
        # Always a "point" estimate (construction has no plot-wide broadcast, so removal
        # never leaves this figure ambiguous when cap relief is reported separately).
        "own_construction_pp_delta": _validated_estimate({
            "status": "point", "value": own_construction_pp_delta,
            "low": own_construction_pp_delta, "high": own_construction_pp_delta, "reason": None,
        }),
        "own_base_pp_delta": _validated_estimate({
            "status": "point", "value": own_base_pp_delta,
            "low": own_base_pp_delta, "high": own_base_pp_delta, "reason": None,
        }),
        "before_powered": before_powered, "after_powered": after_powered,
        "powered_slots_before": powered_before, "powered_slots_after": powered_after,
        "power_lost": [{"asset_ref": c.get("asset_ref"), "name": c.get("name"), "slot": c.get("slot"),
                        "own_harvest_pp": _number(c.get("cur"))} for c in power_lost_cards],
        "before_mult": plot.mult, "after_mult": after_mult,
        "cap_relief": cap_relief,
        "notices": notices,
        "notice_catalog": notice_catalog(),
    }


def _stage_asset_record(report: dict[str, Any], asset_ref: str) -> dict[str, Any] | None:
    for collection in ("workers", "bonus_assets"):
        for record in report.get(collection, []):
            if str(record.get("asset_ref")) == str(asset_ref):
                return dict(record)
    for record in report.get("cards", []):
        if str(record.get("asset_ref")) == str(asset_ref):
            facts = dict(record.get("facts") or {})
            facts.update({key: value for key, value in record.items() if key != "facts" and value is not None})
            if facts.get("card_detail_id") == RUNI_CARD_DETAIL_ID:
                facts["stake_type_uid"] = "STK-LND-RUNI"
            return facts
    for family_name, family in (report.get("unassigned_items") or {}).items():
        if not isinstance(family, dict):
            continue
        for record in family.get("items", family.get("cards", [])):
            if str(record.get("asset_ref")) == str(asset_ref) or str(record.get("uid")) == str(asset_ref):
                return dict(record)
        # unstaked Power Cores/Totems/Titles are listed one-per-item in `uids` (the
        # picker offers exactly these, see `_item_rows`); `items` holds only one catalogue
        # entry per type with no id, so a staged item was never found and every staged Power
        # Core failed the plot recalculation (no badges). Rebuild the record the same way the
        # picker does, tagged with its stake type so the power/boost rules apply.
        for raw_uid in family.get("uids") or []:
            uid = raw_uid.get("uid") if isinstance(raw_uid, dict) else raw_uid
            if str(uid) != str(asset_ref):
                continue
            parts = str(uid).split("-")
            try:
                detail_id = int(parts[1]) if len(parts) > 1 else None
            except ValueError:
                detail_id = None
            aggregate = next((item for item in (family.get("items") or family.get("cards") or [])
                              if isinstance(item, dict) and item.get("item_detail_id") == detail_id), {})
            record = dict(aggregate)
            record.update({"uid": uid, "asset_ref": uid, "item_detail_id": detail_id})
            stake_type = GROUP_TO_SOURCE.get(family_name, (None, None, None))[2]
            if stake_type:
                record.setdefault("stake_type_uid", stake_type)
            # the title aggregate carries no `boost` (totems and cores do) -- the catalogue has it, the same
            # way _item_rows fills it for the picker; without it a staged title priced at 0.
            if record.get("boost") in (None, ""):
                catalogue_item = item_catalogue_lookup(report).get(detail_id, {})
                if catalogue_item.get("boost") not in (None, ""):
                    record["boost"] = catalogue_item["boost"]
            return record
    return None


def _apply_staged_power_flag(flags: dict[str, Any], record: dict[str, Any], sign: float) -> None:
    flag = REMOVAL_POWER_FLAG_BY_TYPE.get(str(record.get("stake_type_uid") or ""))
    if flag is not None:
        flags[flag] = sign > 0


def _validate_staged_asset_group(record: dict[str, Any], move: dict[str, Any]) -> None:
    group_id = move.get("group_id")
    if group_id is None:
        return
    if group_id not in GROUP_TO_SOURCE:
        raise ValueError(f"unsupported staged group_id: {group_id}")
    expected = GROUP_TO_SOURCE[group_id][2]
    actual = str(record.get("stake_type_uid") or "STK-LND-WKR")
    if actual != expected:
        raise ValueError(
            f"staged asset {record.get('asset_ref')} has stake type {actual}, not {group_id}"
        )


def _validate_staged_move_contract(
    deed_uid: str,
    moves: list[dict[str, Any]],
    *,
    filter_other_deeds: bool = False,
    max_moves: int | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for move in moves:
        if not isinstance(move, dict):
            raise ValueError("every staged move must be an object")
        move_deed = move.get("deed_uid", deed_uid)
        if str(move_deed) != str(deed_uid):
            if filter_other_deeds:
                continue
            raise ValueError("stage-impact accepts moves for one deed_uid only")
        action = str(move.get("action") or "")
        if action not in {"stake", "replace", "unstake"}:
            raise ValueError(f"unsupported staged action: {action}")
        result.append(dict(move))
    if max_moves is not None and len(result) > max_moves:
        raise ValueError(f"staged_moves must be a list of at most {max_moves} objects")
    return result


def _validated_staged_moves(deed_uid: str, moves: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if moves is None:
        return []
    if not isinstance(moves, list):
        raise ValueError("staged_moves must be a list of at most 50 objects")
    return _validate_staged_move_contract(deed_uid, moves, filter_other_deeds=True, max_moves=50)


_CAPACITY_OVERLAY_GROUPS = frozenset({"worker", "runi", "power_core"})


def _capacity_overlay_moves(group_id: str, moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return staged entries that can affect a card picker's capacity or power.

    Totems and Titles affect the plot's harvest multiplier, but not worker capacity.
    More importantly, their staged records can be absent from a production snapshot's
    card pool.  Item pickers do not need this overlay at all, so ignoring those entries
    keeps every picker usable with the same account-wide staged plan.
    """
    if GROUP_TO_SOURCE[group_id][0] != "cards":
        return []
    return [move for move in moves if move.get("group_id") in _CAPACITY_OVERLAY_GROUPS or move.get("group_id") is None]


def _staged_moves_signature(moves: list[dict[str, Any]]) -> tuple:
    return tuple(tuple(sorted((str(key), repr(value)) for key, value in move.items())) for move in moves)


def _projected_worksite_row(plot_row: dict[str, Any], staged_worksite_type: str | None) -> tuple[dict[str, Any], str | None]:
    raw_target = str(staged_worksite_type or "").strip().lower()
    if raw_target.startswith("worksite_"):
        raw_target = raw_target[len("worksite_"):]
        if raw_target.endswith("_construction"):
            raw_target = raw_target[:-len("_construction")]
    target = resource_key(raw_target) if raw_target else None
    if staged_worksite_type is not None and target is None:
        raise ValueError("staged_worksite must be a known worksite target")
    if target is None:
        return plot_row, None

    projected_row = dict(plot_row)
    facts = dict(projected_row.get("facts") or {})
    worksite_facts = dict(projected_row.get("worksite_facts") or {})
    staking_facts = dict(projected_row.get("staking_facts") or {})
    projected_row["facts"] = facts
    projected_row["worksite_facts"] = worksite_facts
    projected_row["staking_facts"] = staking_facts
    worksite_facts["project_type"] = f"worksite_{target}"
    facts["worksite_type"] = target
    return projected_row, target


def _worker_slot_capacity(
    plot_row: dict[str, Any], flags: dict[str, Any], capacity_index: tuple[dict[str, int], dict[str, int]],
) -> int:
    return powered_worker_slots(plot_row, capacity_index, flags=flags)


def _energized_card(card: dict[str, Any], details_by_id: dict[int, dict[str, Any]]) -> bool:
    try:
        row = land_ability_row(int(card.get("card_detail_id")), int(card.get("level") or 1), details_by_id)
    except (TypeError, ValueError):
        return False
    return "ENERGIZED" in ability_codes(row)


def _project_staged_plot_state(
    report: dict[str, Any], deed_uid: str, moves: list[dict[str, Any]], details_by_id: dict[int, dict[str, Any]],
    plot: Plot, plot_row: dict[str, Any], *, force_full_power: bool = False,
) -> dict[str, Any]:
    facts = plot_row.get("staking_facts") or {}
    current_cards = [dict(card) for card in plot.cards]
    current_runis = [dict(worker) for worker in report.get("workers", [])
                     if str(worker.get("stake_ref_uid")) == str(deed_uid)
                     and worker.get("stake_type_uid") == "STK-LND-RUNI"]
    current_items = [dict(item) for item in report.get("bonus_assets", [])
                     if str(item.get("stake_ref_uid")) == str(deed_uid)]
    current_by_ref = {str(card.get("asset_ref")): card for card in current_cards}
    current_by_ref.update({str(card.get("asset_ref")): card for card in current_runis})
    current_by_ref.update({str(item.get("asset_ref")): item for item in current_items})
    after_cards = list(current_cards)
    after_runis = list(current_runis)
    after_items = list(current_items)
    after_mult = plot.mult
    after_flags = dict(facts)
    current_item_refs = {str(item.get("asset_ref")) for item in [*current_items, *current_runis]}
    incoming_refs: set[str] = set()

    def remove_asset(asset_ref: str | None) -> dict[str, Any] | None:
        if not asset_ref:
            return None
        for collection in (after_cards, after_runis, after_items):
            for index, record in enumerate(collection):
                if str(record.get("asset_ref")) == str(asset_ref):
                    return collection.pop(index)
        return None

    def apply_item(record: dict[str, Any], sign: float) -> None:
        nonlocal after_mult
        stake_type = str(record.get("stake_type_uid") or "")
        field = REMOVAL_MULT_FIELD_BY_TYPE.get(stake_type)
        if field is not None:
            boost = RUNI_PRODUCTION_BOOST if stake_type == "STK-LND-RUNI" else _number(record.get(field, record.get("boost")))
            after_mult += sign * boost
        _apply_staged_power_flag(after_flags, record, sign)

    def card_record(record: dict[str, Any], move: dict[str, Any]) -> dict[str, Any]:
        detail_id = record.get("card_detail_id")
        detail = details_by_id.get(detail_id, {}) if detail_id is not None else {}
        return {
            "name": record.get("name") or detail.get("name") or "Unknown card",
            "color": detail.get("color") or record.get("color") or "Gray",
            "secondary_color": detail.get("secondary_color") or record.get("secondary_color"),
            "card_detail_id": detail_id,
            "level": record.get("level") or detail.get("level"),
            "base": _number(record.get("land_base_pp"), _number(record.get("base"))),
            "cur": _number(record.get("total_harvest_pp"), _number(record.get("cur"))),
            "construction_pp": _number(record.get("total_construction_pp")),
            "asset_ref": record.get("asset_ref"),
            "card_uid": record.get("uid") or record.get("card_uid"),
            "slot": int(move.get("slot_index")) + 1 if move.get("slot_index") is not None else record.get("slot"),
        }

    for move in moves:
        action = move["action"]
        removed_ref = move.get("evict_asset_ref") if action == "replace" else move.get("asset_ref") if action == "unstake" else None
        removed = remove_asset(removed_ref)
        if action in {"replace", "unstake"} and removed is None:
            raise ValueError(f"staged asset is not present on deed {deed_uid}: {removed_ref}")
        if removed is not None:
            _validate_staged_asset_group(removed, move)
        if removed is not None and str(removed.get("asset_ref")) in current_item_refs:
            apply_item(removed, -1.0)
        if action not in {"stake", "replace"}:
            continue
        incoming = _stage_asset_record(report, str(move.get("asset_ref")))
        if incoming is None:
            raise ValueError(f"staged incoming asset is missing from the snapshot: {move.get('asset_ref')}")
        _validate_staged_asset_group(incoming, move)
        incoming_refs.add(str(incoming.get("asset_ref")))
        stake_type = str(incoming.get("stake_type_uid") or "STK-LND-WKR")
        if stake_type == "STK-LND-RUNI" or move.get("group_id") == "runi":
            after_runis.append(dict(incoming))
            apply_item(incoming, 1.0)
        elif move.get("group_id") in {"totem", "title", "power_core"}:
            after_items.append(dict(incoming))
            apply_item(incoming, 1.0)
        else:
            after_cards.append(card_record(incoming, move))

    if force_full_power:
        after_flags["is_power_core_staked"] = True
    if any(_energized_card(card, details_by_id) for card in after_cards):
        after_flags["is_energized"] = True
    elif _true(after_flags.get("is_energized")) and any(_energized_card(card, details_by_id) for card in current_cards):
        after_flags["is_energized"] = False
    return {
        "facts": facts, "current_cards": current_cards, "current_runis": current_runis,
        "current_items": current_items, "current_by_ref": current_by_ref,
        "after_cards": after_cards, "after_runis": after_runis, "after_items": after_items,
        "after_mult": after_mult, "after_flags": after_flags, "incoming_refs": incoming_refs,
        "before_powered": power_satisfied(facts), "after_powered": power_satisfied(after_flags),
        "worker_capacity": _worker_slot_capacity(plot_row, after_flags, structure_capacity_index(report)),
    }


def _projected_grain_food(cards, runis, allocation, details_by_id):
    base = sum(allocation.values()) + sum(_number(card.get("land_base_pp")) for card in runis)
    discounts = {"RATIONING": 0.0, "RATIONING_LITE": 0.0}
    for card in cards:
        detail = details_by_id.get(card.get("card_detail_id"))
        if not detail:
            return None
        table = (detail.get("stats") or {}).get("land_abilities")
        abilities = land_ability_row(card.get("card_detail_id"), card.get("level"), details_by_id)
        if table and abilities is None:
            return None
        for ability in abilities or []:
            if isinstance(ability, list) and ability and ability[0] in discounts:
                rate = _number(ability[1], None) if len(ability) > 1 else None
                if rate is None or not math.isfinite(rate) or not -1 <= rate <= 0:
                    return None
                discounts[ability[0]] = min(discounts[ability[0]], rate)
    discount = discounts["RATIONING"] + (discounts["RATIONING_LITE"] if base <= 20000 else 0)
    return base * 0.01 * max(0.0, 1 + discount)


def stage_impact(
    report: dict[str, Any],
    deed_uid: str,
    moves: list[dict[str, Any]],
    details_by_id: dict[int, dict[str, Any]] | None = None,
    *,
    force_full_power: bool = False,
    plots: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Price all staged moves against one final plot roster.

    Incoming and evicted assets contribute their own final-state harvest once; the
    remaining whole-plot residual is assigned to the first move in slot order. The
    returned attribution therefore sums exactly to the server-computed plot delta.
    """
    # `force_full_power` prices the plot as if it had full worker-slot power and no
    # staged moves -- what an unpowered card (and its abilities) costs the whole plot. No
    # incoming asset is looked up, so the card pool is not needed; `plots` lets a caller
    # pricing many plots build them once.
    if not moves and not force_full_power:
        raise ValueError("moves must be a non-empty list")
    if moves and not isinstance(report.get("cards"), list):
        from .capture import load_card_pool
        report = load_card_pool(report)
    details_by_id = details_by_id if details_by_id is not None else load_details_by_id()
    if plots is None:
        plots = build_plots(report)
        attach_colors(plots, details_by_id)
    plot = plots.get(deed_uid)
    if plot is None:
        raise KeyError(deed_uid)
    plot_row = next((row for row in report.get("plots", []) if str(row.get("deed_uid")) == str(deed_uid)), None)
    if plot_row is None:
        raise KeyError(deed_uid)
    facts = plot_row.get("staking_facts") or {}
    staged = _validate_staged_move_contract(deed_uid, moves)

    state = _project_staged_plot_state(
        report, deed_uid, staged, details_by_id, plot, plot_row, force_full_power=force_full_power,
    )
    current_by_ref = state["current_by_ref"]
    after_cards = state["after_cards"]
    after_runis = state["after_runis"]
    after_mult = state["after_mult"]
    after_powered = state["after_powered"]
    # a card beyond the plot's powered worker slots produces nothing and lends no
    # abilities or bloodline to the plot. A Runi alone powers fewer slots than a Power Core or
    # an Energized worker (plot 065-09-083: 4 vs 5), so staging a Power Core there unlocks the
    # slot-5 card -- its own PP and its abilities on every tile -- and staging anything else
    # must not count that card as producing.
    powered_slots = state["worker_capacity"]
    slot_by_ref = {str(worker.get("asset_ref")): worker.get("slot") for worker in report.get("workers", [])
                   if str(worker.get("stake_ref_uid")) == str(deed_uid)}

    def _card_slot(card: dict[str, Any]) -> int | None:
        raw = card.get("slot") if card.get("slot") is not None else slot_by_ref.get(str(card.get("asset_ref")))
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    if powered_slots > 0:
        after_cards = [card for card in after_cards if (_card_slot(card) or 0) <= powered_slots]
    allocation = _scoring_allocation(after_cards, plot.base_cap)
    if allocation is None:
        raise ValueError("staged roster cannot be priced safely")
    abilities = plot_abilities_boost(after_cards, plot.resource, details_by_id)
    bloodline = plot_bloodline_boost(after_cards, details_by_id)
    resulting_harvest = sum(
        _harvest(allocation[str(card["asset_ref"])], plot.terrain(card.get("color", "Gray"), card.get("secondary_color")), after_mult, abilities, bloodline)
        for card in after_cards
    )
    resulting_harvest += sum(
        _harvest(_number(runi.get("land_base_pp")), 0.0, after_mult, abilities, bloodline)
        for runi in after_runis
    )
    if not after_powered:
        resulting_harvest = 0.0
    observed_current = facts.get("total_harvest_pp")
    current_harvest = _number(observed_current) if observed_current not in (None, "") else 0.0
    harvest_delta = resulting_harvest - current_harvest
    per_move = []
    for move in staged:
        incoming = _stage_asset_record(report, str(move.get("asset_ref"))) if move["action"] in {"stake", "replace"} else None
        incoming_own = 0.0
        if incoming is not None:
            stake_type = str(incoming.get("stake_type_uid") or "STK-LND-WKR")
            if stake_type == "STK-LND-RUNI" or move.get("group_id") == "runi":
                incoming_own = _harvest(_number(incoming.get("land_base_pp")), 0.0, after_mult, abilities, bloodline) if after_powered else 0.0
            elif move.get("group_id") not in {"totem", "title", "power_core"}:
                ref = str(incoming.get("asset_ref"))
                incoming_card = next((card for card in after_cards if str(card.get("asset_ref")) == ref), None)
                if incoming_card is not None:
                    incoming_own = _harvest(allocation[ref], plot.terrain(incoming_card.get("color", "Gray"), incoming_card.get("secondary_color")), after_mult, abilities, bloodline) if after_powered else 0.0
        evicted = current_by_ref.get(str(move.get("evict_asset_ref"))) if move["action"] == "replace" else current_by_ref.get(str(move.get("asset_ref"))) if move["action"] == "unstake" else None
        evicted_own = _number(evicted.get("total_harvest_pp"), _number(evicted.get("cur"))) if evicted else 0.0
        per_move.append({
            "slot_index": move.get("slot_index"), "group_id": move.get("group_id"),
            "asset_ref": move.get("asset_ref"), "attributed_delta": incoming_own - evicted_own,
        })
    if per_move:
        first = min(range(len(per_move)), key=lambda index: (per_move[index]["slot_index"] is None, per_move[index]["slot_index"] or 0, index))
        per_move[first]["attributed_delta"] += harvest_delta - sum(item["attributed_delta"] for item in per_move)
        assert abs(sum(item["attributed_delta"] for item in per_move) - harvest_delta) < 1e-6
    # (maintainer: the change badges show what the staged changes WILL produce): the resulting production from the
    # worksite model's rate per PP -- so an empty plot gets a figure too. SPS is a pool share with no per-PP rate: None.
    rate_key = resource_key(plot.resource)
    resulting_work = resulting_harvest * _RATE_PER_PP[rate_key] if rate_key in _RATE_PER_PP else None
    # the region's DEC efficiency scales the rate unless a Runi (live or staged) exempts the plot.
    if resulting_work is not None:
        facts = next((p.get("staking_facts") or {} for p in report.get("plots", []) if str(p.get("deed_uid")) == str(deed_uid)), {})
        efficiency = _number(facts.get("efficiency"), None)
        runi = _true(facts.get("is_runi_staked")) or any(m.get("group_id") == "runi" and m.get("action") != "unstake" for m in moves)
        if efficiency is not None and efficiency < 1 and not runi:
            resulting_work *= efficiency
    grain_economics = None
    if rate_key == "grain" and after_powered and resulting_work is not None:
        current_food = _number((plot_row.get("worksite_facts") or {}).get("grain_req_per_hour"), None)
        current_allocation = _scoring_allocation(plot.cards, plot.base_cap)
        current_runis = [worker for worker in report.get("workers", [])
                         if str(worker.get("stake_ref_uid")) == str(deed_uid)
                         and (worker.get("stake_type_uid") == "STK-LND-RUNI" or worker.get("card_detail_id") == RUNI_CARD_DETAIL_ID)]
        expected_food = _projected_grain_food(plot.cards, current_runis, current_allocation, details_by_id) if current_allocation is not None else None
        after_food = _projected_grain_food(after_cards, after_runis, allocation, details_by_id)
        efficiency = 1.0 if current_runis else _number(facts.get("efficiency"), None)
        if (current_food is not None and expected_food is not None and after_food is not None
                and efficiency is not None and math.isclose(current_food, expected_food, abs_tol=0.01)):
            gross_delta = resulting_work - current_harvest * _RATE_PER_PP[rate_key] * efficiency
            food_saved = current_food - after_food
            grain_economics = {"gross_delta": gross_delta, "food_saved": food_saved,
                               "current_food": current_food, "resulting_food": after_food,
                               "net_delta_before_tax": gross_delta + food_saved}
    return {
        "deed_uid": deed_uid, "current_harvest": current_harvest,
        "resulting_harvest": resulting_harvest, "harvest_pp_delta": harvest_delta,
        "resulting_work_per_hour": resulting_work, "grain_economics": grain_economics,
        "per_move": per_move, "notices": [],
    }


def rank_candidates(
    report: dict[str, Any], deed_uid: str, group_id: str, *, player: str, now: datetime | None = None,
    q: str | None = None, rarity: str | None = None, splinter: str | None = None, edition: int | None = None,
    min_pp: float | None = None, bucket: str = "all", sort: str = "construction_pp", preset: str | None = None,
    max_pp: float | None = None,
    include_cooling: bool = False, include_rented_in: bool = False, include_terrain_penalised: bool = False,
    limit: int = 25, offset: int = 0,
    details_by_id: dict[int, dict[str, Any]] | None = None, slot: int | None = None,
    reserved_asset_refs: set[str] | None = None,
    released_asset_refs: set[str] | None = None,
    bloodline_sub_types: set[str] | None = None,
    land_only: bool = False,
    ability_filter: frozenset[str] | None = None,
    include_on_land: bool = False,  # staked-elsewhere cards hidden by default
    staged_moves: list[dict[str, Any]] | None = None,
    staged_worksite_type: str | None = None,
) -> dict[str, Any]:
    if group_id not in GROUP_TO_SOURCE:
        raise ValueError(f"unknown group_id: {group_id}")
    if bucket not in {"idle", "staked", "all"}:
        raise ValueError("bucket must be idle, staked, or all")
    if GROUP_TO_SOURCE[group_id][0] == "cards" and not isinstance(report.get("cards"), list):
        from .capture import load_card_pool
        report = load_card_pool(report)
    now = now or datetime.now(timezone.utc)
    details_by_id = details_by_id if details_by_id is not None else load_details_by_id()
    plot_row = next((row for row in report.get("plots", []) if str(row.get("deed_uid")) == str(deed_uid)), None)
    if plot_row is None:
        raise KeyError(deed_uid)
    staged_moves = _validated_staged_moves(deed_uid, staged_moves)
    plots = _plots_cached(report, details_by_id)
    plot = plots.get(deed_uid)
    if plot is None:
        raise KeyError(deed_uid)
    staged_incoming_refs: set[str] = set()
    overlay_moves = _capacity_overlay_moves(group_id, staged_moves)
    if overlay_moves or (staged_worksite_type is not None and GROUP_TO_SOURCE[group_id][0] == "cards"):
        plot_row, staged_target = _projected_worksite_row(plot_row, staged_worksite_type)
        state = _project_staged_plot_state(report, deed_uid, overlay_moves, details_by_id, plot, plot_row)
        projected_staking = dict(plot_row.get("staking_facts") or {})
        projected_staking.update(state["after_flags"])
        plot_row = {**plot_row, "staking_facts": projected_staking}
        plot = replace(
            plot, cards=state["after_cards"], card_capacity=state["worker_capacity"],
            mult=state["after_mult"], resource=staged_target or plot.resource,
        )
        staged_incoming_refs = state["incoming_refs"]
    _capacity_identity, capacity_family = _structure_identity(plot_row)
    if group_id == "worker" and (capacity_family == "worksite" or plot.card_capacity == 0):
        # The picker is shaped by structural capacity, including slots that are currently
        # unpowered. Live max_workers_allowed remains the board's powered-state truth.
        plot.card_capacity = potential_worker_capacity(plot_row, structure_capacity_index(report))
    preset_id = preset or "construction_pp"
    if preset_id not in PRESET_INFO:
        raise ValueError(f"unknown preset: {preset_id}")
    preset_label, preset_sort = PRESET_INFO[preset_id]
    cooldown_free_only = preset_id in {"cooldown_free_only", "cooldown_free"}
    if preset:
        sort = preset_sort
    if preset_id == "everything":
        include_on_land = True  # the legacy 'Show everything' preset means everything
    source_kind, source_name, _ = GROUP_TO_SOURCE[group_id]
    source_label = {"collection_derived": "Owned card collection", "game_available_list": "Available item catalogue"}.get(source_name, "Candidate pool")
    # (2026-09-07), v0.9.33, his decision: on every sort whose ORDER does not depend on
    # the score, only the page being shown (plus a margin) is scored. `net_gain` orders by the
    # score and keeps full scoring. On a whale pool the first click scored ~45,000 cards to show
    # 25 (~5 s per deed and slot); the page needs 25-50.
    page_mode = source_kind == "cards" and sort != "net_gain"
    if source_kind == "cards":
        rows, excluded = _eligible_card_rows_cached(report, details_by_id, player, now, group_id, bucket, plots)
    else:
        rows, excluded = _item_rows(report, group_id, now, released_asset_refs)
    available_editions = sorted({int(row.facts["edition"]) for row in rows if str(row.facts.get("edition", "")).isdigit()}) if source_kind == "cards" else []
    matching: list[CandidateRow] = []
    evaluated_rows: list[CandidateRow] = []
    to_score: list[CandidateRow] = []
    pool_total: int | None = None  # page mode: the whole matching pool, of which only a page is scored
    plot_rows_by_id = {str(row.get("deed_uid")): row for row in report.get("plots", [])}
    # Human-readable plot numbers for the "staked on ..." display (see
    # serialize_candidate's `source_plot`) -- never the internal `deed_uid`.
    plot_number_by_deed = {uid: row.get("facts", {}).get("plot") for uid, row in plot_rows_by_id.items()}
    reserved_asset_refs = {str(ref) for ref in (reserved_asset_refs or set())} | staged_incoming_refs
    # the whole filter-and-score result for this exact picker view is memoised, so a
    # page or sort change re-sorts it instead of walking the pool again.
    match_key = None
    match_hit = None
    if source_kind == "cards":
        match_key = (
            _snapshot_identity(report), str(deed_uid), group_id, _resolve_real_slot(slot) if group_id == "worker" else None,
            (q, rarity, splinter, edition, min_pp, max_pp, bucket, cooldown_free_only, include_cooling, include_rented_in,
             include_terrain_penalised, land_only, ability_filter, include_on_land, None if bloodline_sub_types is None else frozenset(bloodline_sub_types),
             frozenset(reserved_asset_refs), _staged_moves_signature(staged_moves), staged_worksite_type),
            int(now.timestamp() // _MATCHING_WINDOW_SECONDS), id(_eligible_card_rows), id(evaluate_candidates_for_plot),
            (offset + limit) if page_mode else None, sort if page_mode else None,
        )
        with _candidate_cache_lock:
            match_hit = _matching_cache.get(match_key)
        if match_hit is not None:
            matching = list(match_hit[0])
            excluded = dict(match_hit[1])
            pool_total = match_hit[3] if len(match_hit) > 3 else None
            rows = []
    for row in rows:
        if not _bucket_selected(row.bucket, bucket):
            continue
        if row.rental_kind == "in" and not include_rented_in:
            excluded["rented"] = excluded.get("rented", 0) + 1
            continue
        ready = bool(row.cooldown.get("ready"))
        if not ready:
            # The row was classified when the eligible list was built; a cooldown that has
            # since run out is ready now ().
            ready_at = _time(row.cooldown.get("ready_at"))
            ready = ready_at is not None and ready_at <= now
        if (cooldown_free_only or not include_cooling) and not ready:
            key = "survival_locked" if row.cooldown.get("state") == "survival_locked" else "cooling"
            excluded[key] = excluded.get(key, 0) + 1
            continue
        if not _matches(row, q, rarity, splinter, edition, min_pp, bucket, max_pp):
            continue
        if bloodline_sub_types is not None:
            candidate_sub_type = row.details.get("sub_type")
            if not candidate_sub_type or str(candidate_sub_type) not in bloodline_sub_types:
                continue
        # "Land Cards" -- any card whose details carry land abilities at its level.
        if land_only and not land_ability_row(row.card.card_detail_id, row.card.level, {row.card.card_detail_id: row.details}):
            continue
        if ability_filter:
            row_codes = set(ability_codes(land_ability_row(
                row.card.card_detail_id, row.card.level, {row.card.card_detail_id: row.details}
            ) or []))
            if "__ANY__" in ability_filter:
                if not row_codes:
                    continue
            elif not row_codes.intersection(ability_filter):
                continue
        if (group_id == "worker" or source_kind != "cards") and row.card.source_uid == plot.uid:
            excluded["same_plot"] = excluded.get("same_plot", 0) + 1
            continue
        # a card staked on another plot
        # is hidden unless the picker's 'On land' chip asks for it (the explicit `staked`
        # bucket still lists them -- that request IS the ask).
        if row.bucket == "staked" and not include_on_land and bucket != "staked" and not (source_kind != "cards" and str(row.card.asset_ref) in (released_asset_refs or set())):
            excluded["on_land"] = excluded.get("on_land", 0) + 1
            continue
        if row.card.asset_ref is not None and str(row.card.asset_ref) in reserved_asset_refs:
            continue
        to_score.append(_clone_row(row))  # shared cached row: clone before scores are written into it
    # Slot-specific eviction scoring (2026-08-24, closes the "picker recommendations are
    # scored against the wrong occupant" defect -- editor-polish removed the misleading
    # "evicts X" LABEL; this is the underlying MATH it was reporting). When the clicked
    # worker slot is full, candidates must be scored against replacing the SPECIFIC
    # occupant of that slot (`app.js:2461` already tells the user "Replacing X" -- the
    # frontend's own intent), never the best-scoring eviction across the whole roster.
    occupants: list[MovableCard] = []
    slot_specific_occupant: MovableCard | None = None
    is_worker = group_id == "worker"
    is_full = is_worker and len(plot.cards) >= plot.card_capacity
    # R-99 (2026-09-03): occupant resolution is gated on the CLICKED SLOT having an
    # occupant, never on the plot being full. A plot with a free slot elsewhere is still a
    # REPLACE when the user clicks an occupied slot -- gating this on plot fullness priced every
    # such swap as a plain add (the evicted card's Base PP was never subtracted) AND, because
    # `target_slot` below is passed regardless, put two cards in one slot, which
    # `_cap_allocation`'s duplicate-slot guard refuses -- collapsing plot_impact to `unknown`
    # for every candidate on the list. Measured: 2,765 of 2,765 on plot 41-2-27.
    real_slot = _resolve_real_slot(slot) if is_worker else None
    target_card = next(
        (c for c in plot.cards if real_slot is not None and c.get("slot") == real_slot), None
    ) if is_worker else None
    if target_card is not None:
        slot_specific_occupant = MovableCard(
            name=target_card.get("name") or "Unknown card", color=target_card.get("color") or "Gray", secondary_color=target_card.get("secondary_color"),
            base=_number(target_card.get("base")), current_harvest=_number(target_card.get("cur")),
            origin="staked", source_uid=plot.uid, card_detail_id=target_card.get("card_detail_id"),
            level=target_card.get("level"), asset_ref=target_card.get("asset_ref"), card_uid=target_card.get("card_uid"),
        )
    elif is_full:
        # `slot` did not resolve to an actual occupant on THIS plot -- missing, stale, out of
        # range, or a caller that never supplied one -- and the plot is full, so SOMETHING must
        # be evicted. Fail SAFE to the whole-roster search rather than guess which slot was
        # meant. On a NON-full plot the same condition means a genuine plain add into a free
        # slot, and no eviction search may run.
        for victim in plot.cards:
            occupants.append(MovableCard(
                name=victim.get("name") or "Unknown card", color=victim.get("color") or "Gray", secondary_color=victim.get("secondary_color"),
                base=_number(victim.get("base")), current_harvest=_number(victim.get("cur")),
                origin="staked", source_uid=plot.uid, card_detail_id=victim.get("card_detail_id"),
                level=victim.get("level"), asset_ref=victim.get("asset_ref"), card_uid=victim.get("card_uid"),
            ))
    # R-103 / : this is the same stage-zero `plot.cards` source used by the allocator's
    # breakdown. A cap can make the stage-two harvest projection unavailable, but it cannot
    # make the clicked slot's occupant unavailable; keeping one record preserves that symmetry.
    slot_evicted = None if target_card is None else {
        "asset_ref": target_card.get("asset_ref"),
        "name": target_card.get("name"),
        "cur": _number(target_card.get("cur")),
        "base": _number(target_card.get("base")),
        "construction_pp": _number(target_card.get("construction_pp")),
    }
    if page_mode and to_score:
        # order by the score-free key and keep only what the page needs. The terrain
        # penalty (a worker card whose colour the plot punishes) is known before scoring
        # `plot.terrain(colour)` is exactly what the evaluation reports as `terrain_boost`
        # so the rows the post-scoring filter would drop are dropped here, counted the same.
        if not include_terrain_penalised:
            kept: list[CandidateRow] = []
            for row in to_score:
                terrain = plot.terrain(row.card.color or "Gray", row.card.secondary_color)
                if row.stake_type_uid == "STK-LND-WKR" and terrain < 0:
                    excluded["terrain_penalty"] += 1
                else:
                    kept.append(row)
            to_score = kept
        pool_total = len(to_score)

        def _proxy(row: CandidateRow) -> tuple[Any, ...]:
            if sort == "name":
                return (row.card.name.lower(), str(row.card.asset_ref or ""))
            if sort == "boost":
                return boost_sort_key(row)
            if sort == "base_pp":
                return (-row.card.base, row.card.name.lower(), str(row.card.asset_ref or ""))
            value = row.card.base * (1 + plot.terrain(row.card.color or "Gray", row.card.secondary_color))
            return (-value, -row.card.base, row.card.name.lower(), str(row.card.asset_ref or ""))

        bound = offset + limit + _PAGE_SCORING_MARGIN
        if len(to_score) > bound:
            to_score = heapq.nsmallest(bound, to_score, key=_proxy)
    if is_full and slot_specific_occupant is None and not occupants:
        excluded["capacity"] = excluded.get("capacity", 0) + len(to_score)
    elif to_score:
        # each candidate is scored against the plot's current cards and the clicked
        # slot's occupant, independently of every other candidate (`_score_candidate_points_
        # for_plot` builds one context and evaluates each card against it), so scores memoise
        # per card under a key that names everything the score depends on. A request with a
        # different filter scores only the cards the previous requests did not.
        before_powered = power_satisfied(plot_row.get("staking_facts", {}))
        after_powered = before_powered or group_id in {"power_core", "runi"}
        occupant_key = (
            None if slot_specific_occupant is None else (slot_specific_occupant.asset_ref, slot_specific_occupant.card_uid),
            tuple(sorted(str(o.asset_ref or o.card_uid) for o in occupants)),
        )
        eval_key = (
            _snapshot_identity(report), str(deed_uid), group_id, _resolve_real_slot(slot), occupant_key,
            plot.base_cap, before_powered, after_powered,
            _staged_moves_signature(staged_moves), staged_worksite_type,
            id(evaluate_candidates_for_plot),
        )
        with _candidate_cache_lock:
            known = _evaluation_cache.get(eval_key)
            evaluation_by_card = {k: v for k, v in known.items() if k != "__pins__"} if known is not None else {}
        missing = [row for row in to_score if (row.card.asset_ref, row.card.name) not in evaluation_by_card]
        if missing:
            evaluations = evaluate_candidates_for_plot(
                plot, [row.card for row in missing], target_slot_occupant=slot_specific_occupant,
                target_slot_occupants=occupants if occupants else None,
                details_by_id=details_by_id, base_pp_ceiling=plot.base_cap,
                before_powered=before_powered,
                after_powered=after_powered,
                construction_applicable=group_id in {"worker", "runi"},
                score_candidate_base=group_id == "worker",
                target_slot=_resolve_real_slot(slot),
            )
            for evaluation in evaluations:
                evaluation_by_card[(evaluation.card.asset_ref, evaluation.card.name)] = evaluation
            with _candidate_cache_lock:
                stored = _evaluation_cache.get(eval_key)
                if stored is None:
                    stored = {}
                    _evaluation_cache[eval_key] = stored
                stored.update(evaluation_by_card)
                stored["__pins__"] = _snapshot_pins(report)
                _evict_oldest_locked(_evaluation_cache)
        for row in to_score:
            evaluation = evaluation_by_card.get((row.card.asset_ref, row.card.name))
            if evaluation is None:
                continue
            row.terrain = evaluation.terrain_boost
            row.construction_pp = evaluation.construction_pp
            if row.terrain is None and (row.card.color or "Gray") == "Gray":
                row.terrain = 0.0
                if (row.construction_pp or {}).get("reason") == "terrain_modifier_unknown":
                    value = row.card.base * (1 + row.terrain)
                    row.construction_pp = {"status": "point", "value": value, "low": value, "high": value, "reason": None}
            row.construction_pp_delta = evaluation.construction_pp_delta
            row.base_pp_delta = evaluation.base_pp_delta
            row.plot_impact = dict(evaluation.plot_impact)
            row.score = row.plot_impact.get("value") if row.plot_impact.get("status") == "point" else None
            row.breakdown = evaluation.plot_impact_breakdown if row.score is not None else None
            if row.score is not None and row.bucket == "staked" and row.card.source_uid in plots:
                donor = plots[row.card.source_uid]
                if any(card.get("color") not in donor.biome for card in donor.cards):
                    row.score = None
                    row.breakdown = None
                    row.plot_impact = {
                        "status": "unknown", "value": None, "low": None, "high": None,
                        "reason": "donor_terrain_modifier_unknown",
                    }
                else:
                    donor_result = _donor_delta(row, plots, plot_rows_by_id, details_by_id, group_id)
                    donor_delta, donor_reason = donor_result if donor_result is not None else (None, None)
                    if donor_reason is not None:
                        # The donor side is unknown, so the move's net impact is too; the
                        # destination-only figure must not be shown as the whole move's gain.
                        row.score = None
                        row.breakdown = None
                        row.plot_impact = {
                            "status": "unknown", "value": None, "low": None, "high": None,
                            "reason": donor_reason,
                        }
                    elif donor_delta is not None:
                        row.score += donor_delta
                        row.breakdown = dict(row.breakdown or {})
                        row.breakdown["donor_plot_delta"] = donor_delta
                        row.plot_impact = {
                            "status": "point", "value": row.score, "low": row.score, "high": row.score,
                            "reason": None,
                        }
            if row.breakdown and row.breakdown.get("evicted"):
                row.evicted = row.breakdown["evicted"]
            elif slot_evicted is not None:
                row.evicted = slot_evicted
            else:
                row.evicted = None
            evaluated_rows.append(row)
        terrain_penalties_fit = include_terrain_penalised
        for row in evaluated_rows:
            if _has_terrain_penalty(row) and not terrain_penalties_fit:
                excluded["terrain_penalty"] += 1
                continue
            matching.append(row)
    if match_key is not None and match_hit is None:
        with _candidate_cache_lock:
            _matching_cache[match_key] = (list(matching), dict(excluded), _snapshot_pins(report), pool_total)
            _evict_oldest_locked(_matching_cache)
    point_scores = [row.score for row in matching if row.score is not None and math.isfinite(row.score)]
    if point_scores and all(score < 0 for score in point_scores):
        excluded["negative_net_gain"] += len(point_scores)
    if sort not in {"construction_pp", "net_gain", "base_pp", "name", "boost"}:
        raise ValueError("sort must be construction_pp, net_gain, base_pp, name, or boost")
    if sort == "name":
        ordered = sorted(matching, key=lambda row: (row.card.name.lower(), str(row.card.asset_ref or "")))
    elif sort == "construction_pp":
        ordered = sorted(matching, key=construction_sort_key)
    elif sort == "boost":
        ordered = sorted(matching, key=boost_sort_key)
    else:
        key = (lambda row: (row.score if row.score is not None else float("-inf"), row.card.base, row.card.name.lower(), str(row.card.asset_ref or ""))) if sort == "net_gain" else (lambda row: (row.card.base, row.score if row.score is not None else float("-inf"), row.card.name.lower(), str(row.card.asset_ref or "")))
        ordered = heapq.nlargest(min(len(matching), offset + limit), matching, key=key)
    selected = ordered[offset:offset + limit]
    estimate_counts = {status: 0 for status in ("point", "range", "unknown", "not_applicable")}
    for row in matching:
        status = (row.plot_impact or {}).get("status", "unknown")
        estimate_counts[status if status in estimate_counts else "unknown"] += 1
    rarity_groups = {}
    item_rarities = {
        "totem": ("common", "rare", "epic", "legendary"),
        "title": ("rare", "epic", "legendary"),
    }.get(group_id)
    if item_rarities:
        for row in ordered:
            rarity = _picker_item_rarity(row)
            if rarity not in item_rarities or rarity in rarity_groups:
                continue
            rarity_groups[rarity] = {
                "count": sum(1 for candidate in matching if _picker_item_rarity(candidate) == rarity),
                "candidate": serialize_candidate(row, plot_number_by_deed),
            }
        for rarity in item_rarities:
            rarity_groups.setdefault(rarity, {"count": 0, "candidate": None})
    # one printing index from this snapshot; no extra game read.
    upgrade_counts: dict[tuple[Any, ...], int] = {}
    upgrade_eligible: set[str] = set()
    if group_id == "worker":
        printings = _upgrade_printings_cached(report)
        for row in selected:
            identity = _upgrade_identity(row.facts)
            if identity and identity not in upgrade_counts:
                copies = printings.get(identity, [])
                anchor = _upgrade_uid(copies[0]) if copies else None
                classified = upgrade_inputs(copies, anchor, account=player, details_by_id=details_by_id) if anchor else []
                upgrade_counts[identity] = sum(copy["eligible"] for copy in classified)
                upgrade_eligible.update(copy["uid"] for copy in classified if copy["eligible"])
    candidates_out = [serialize_candidate(row, plot_number_by_deed) for row in selected]
    if group_id == "worker":
        for candidate, row in zip(candidates_out, selected):
            candidate["upgrade"] = {"usable_copies": upgrade_counts.get(_upgrade_identity(row.facts), 0), "eligible": _upgrade_uid(row.facts) in upgrade_eligible}
    return {
        "group_id": group_id, "deed_uid": deed_uid, "target_slot_id": f"{group_id}:{slot}",
        "source": source_label, "pool_size": pool_total if pool_total is not None else len(matching),
        "total": pool_total if pool_total is not None else len(matching), "offset": offset,
        "scoring": "page" if page_mode else "full",
        "limit": limit, "returned": len(selected), "preset": {"id": preset_id, "label": preset_label, "applied": True},
        "candidates": candidates_out, "excluded_counts": excluded,
        "available_editions": available_editions,
        "estimate_counts": estimate_counts,
        "rarity_groups": rarity_groups,
        "notices": candidate_notices(excluded, selected, deed_uid), "notice_catalog": notice_catalog(),
    }


def serialize_candidate(row: CandidateRow, plot_number_by_deed: dict[str, str] | None = None) -> dict[str, Any]:
    detail, facts, card = row.details, row.facts, row.card
    land_abilities = land_ability_row(card.card_detail_id, card.level, {card.card_detail_id: detail}) or []
    worker_like = row.stake_type_uid in {"STK-LND-WKR", "STK-LND-RUNI"}
    if row.construction_pp is not None:
        construction_pp = _validated_estimate(row.construction_pp)
    elif worker_like and row.terrain is not None:
        value = card.base * (1 + row.terrain)
        construction_pp = {"status": "point", "value": value, "low": value, "high": value, "reason": None}
    elif worker_like:
        construction_pp = {"status": "unknown", "value": None, "low": None, "high": None, "reason": "terrain_modifier_unknown"}
    else:
        construction_pp = {"status": "not_applicable", "value": None, "low": None, "high": None, "reason": "item_pp_not_applicable"}
    if row.construction_pp_delta is not None:
        construction_pp_delta = _validated_estimate(row.construction_pp_delta)
    else:
        # No evaluation ran (defensive fallback, mirrors construction_pp above) -- without a
        # scored evaluation there is no eviction context to net against, so fall back to the
        # candidate's own construction PP unmodified (equivalent to "no occupant removed").
        construction_pp_delta = dict(construction_pp)
    if row.base_pp_delta is not None:
        base_pp_delta = _validated_estimate(row.base_pp_delta)
    else:
        base_value = card.base
        base_pp_delta = _validated_estimate({
            "status": "point", "value": base_value, "low": base_value, "high": base_value, "reason": None,
        })
    if row.plot_impact is not None:
        plot_impact = _validated_estimate(row.plot_impact)
    elif worker_like and row.score is not None and math.isfinite(row.score):
        plot_impact = {"status": "point", "value": row.score, "low": row.score, "high": row.score, "reason": None}
    elif worker_like:
        plot_impact = {"status": "unknown", "value": None, "low": None, "high": None, "reason": "plot_impact_unknown"}
    else:
        plot_impact = {"status": "not_applicable", "value": None, "low": None, "high": None, "reason": "item_scoring_not_applicable"}
    construction_pp = _validated_estimate(construction_pp)
    plot_impact = _validated_estimate(plot_impact)
    net_gain = plot_impact.get("value") if plot_impact.get("status") == "point" else None
    result = {
        "asset_ref": card.asset_ref, "card_uid": card.card_uid, "uid": card.card_uid, "name": card.name,
        "source_deed_uid": card.source_uid if row.bucket == "staked" else None,
        # The real, human-readable plot number (e.g. "65-10-88", from the source
        # plot's own `facts.plot`) -- internal deed UIDs like "I-294" are not
        # user-readable. `deed_uid` (`source_deed_uid` above) stays as the
        # internal staging/lookup key; this is display-only and must be what
        # the UI actually shows.
        "source_plot": (plot_number_by_deed or {}).get(str(card.source_uid)) if row.bucket == "staked" else None,
        "cooling_from_plot": (
            (plot_number_by_deed or {}).get(str(facts.get("stake_ref_uid")))
            if row.bucket in COOLING_BUCKETS else None
        ),
        "level": card.level, "base_pp": card.base, "base_pp_delta": base_pp_delta,
        "card_set": card.card_set or facts.get("card_set") or detail.get("card_set"),
        "skin": facts.get("skin"),
        "terrain_boost": row.terrain if worker_like else None,
        "construction_pp": construction_pp,
        # Base-PP-stage (construction) delta this candidate would cause if staged here
        # nets out the evicted occupant's own construction PP for a swap, or equals
        # `construction_pp` for a plain add. Never collapsed with `plot_impact` (the
        # harvest-stage delta) -- the internal rules's two-stage chain.
        "construction_pp_delta": construction_pp_delta,
        "card_detail_id": card.card_detail_id,
        "foil": _foil(detail, facts, card),
        "sub_type": detail.get("sub_type"),
        "land_abilities": land_abilities,
        "sole_holder_for": ability_codes(land_abilities),
        "rarity": _picker_item_rarity(row), "splinter": _splinter(detail, facts), "splinters": card_splinters(detail, facts), "edition": facts.get("edition"),
        "bucket": row.bucket, "net_gain": net_gain,
        "plot_impact": plot_impact,
        "net_gain_breakdown": row.breakdown if plot_impact.get("status") == "point" else None,
        "cooldown": row.cooldown,
        "survival_bracket": facts.get("survival_bracket"),
        "evicted": row.evicted,
        "asset_kind": "worker" if row.stake_type_uid == "STK-LND-WKR" else "runi" if row.stake_type_uid == "STK-LND-RUNI" else row.facts.get("asset_kind") or "item",
    }
    if row.stake_type_uid not in {"STK-LND-WKR", "STK-LND-RUNI"}:
        result["item_detail_id"] = row.facts.get("item_detail_id", card.card_detail_id)
        result["boost"] = row.facts.get("boost", detail.get("boost"))
        result["effect"] = row.facts.get("description") or detail.get("description")
    if row.rental_kind == "in":
        rental_notice = format_notice(
            "rental_expiry_warning",
            context={"asset_ref": card.asset_ref, "expires_at": row.rental_expiry},
            reason=f"expires at {row.rental_expiry}",
        )
        result["rental"] = {
            "direction": "in", "expires_at": row.rental_expiry,
            "warning": rental_notice["message"], "notice": rental_notice,
        }
    return result


def planned_card(report, deed_uid, plan, *, player, slot, details_by_id, staged_moves=None, staged_worksite_type=None):
    """ : read-only expected-card pricing through the ordinary plot evaluator."""
    expected = plan.get("expected") or {}
    card = plan.get("card") or {}
    ref = "planned:" + plan["id"]
    unknown = {"status": "unknown", "value": None, "low": None, "high": None, "reason": "Expected card production is unavailable."}
    result = {**card, "asset_ref": ref, "uid": ref, "card_uid": ref,
              "level": expected.get("level"), "bcx": expected.get("bcx"),
              "bcx_max": expected.get("bcx_max"), "base_pp_capped": expected.get("base_pp_capped", False),
              "base_pp": expected.get("base_pp"), "construction_pp": unknown, "plot_impact": unknown,
              "base_pp_delta": unknown, "net_gain": None}
    base = expected.get("base_pp")
    if isinstance(base, (float, int)) and not isinstance(base, bool) and math.isfinite(base) and base > 0 and isinstance(expected.get("level"), int) and card.get("card_detail_id") in details_by_id:
        facts = {**card, "uid": ref, "card_uid": ref, "asset_ref": ref, "player": player,
                 "level": expected["level"], "bcx": expected.get("bcx"), "land_base_pp": base}
        synthetic = {**report, "cards": [{"asset_ref": ref, "facts": facts, "classification": {"bucket": "unstaked_apparent"}}]}
        priced = rank_candidates(synthetic, deed_uid, "worker", player=player, slot=slot,
                                 details_by_id=details_by_id, bucket="idle", include_terrain_penalised=True,
                                 staged_moves=staged_moves, staged_worksite_type=staged_worksite_type)
        if priced["candidates"]:
            result.update(priced["candidates"][0])
    # No cooldown date is attached to the signing gate: expiry cannot release a nonexistent card.
    result.update(planned=True, chain_id=plan["id"], ready_at=plan.get("ready_at"),
                  cooldown={"ready": False, "state": "planned", "ready_at": None, "reason": "after the combine"})
    result.pop("upgrade", None)
    return result
