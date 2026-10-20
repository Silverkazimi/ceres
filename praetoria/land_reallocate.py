"""General, parameterized Land worker reallocation planner.

This is deliberately NOT hardcoded to any specific plot set, rarity tier, or PP floor --
those are all caller-supplied criteria (see `ReallocationCriteria`). What IS fixed is the
methodology, corrected through hands-on review 2026-08-19 (see docs/land-query.md
"Reallocation methodology" and the internal rules's land-worker-placement rule):

1. Card PP should concentrate on the highest-multiplier plots that fit it (rarity boost +
   totem + title + runi + deed-status-token boosts, NOT the nominal rarity label alone --
   a totem-boosted common can out-multiply a bare epic).
2. A move must win on ITS OWN: destination gain minus any evicted occupant's harvest minus
   the moved card's own current harvest must be >= 0. Backfilling the vacated slot is a
   real, additive benefit, but it must NEVER be used to justify an otherwise-losing primary
   move (that coupling produced nonsensical recommendations during development -- e.g. a
   giant idle card "spent" as backfill fodder to subsidize evicting a productive worker for
   a near-worthless filler card).
3. Negative-terrain placements are excluded by default (a plot's per-color biome modifier
   must be >= 0 for a color to be eligible there), matching the observed data pattern that
   negative-terrain workers are pure waste.
4. Card sources: unstaked-ever cards AND cards whose stake has ENDED are both genuinely
   idle right now, even though Praetoria's own classifier puts them in two different
   buckets (`unstaked_apparent` vs `stake_expired_or_historical`); both are eligible unless
   blocked by an active market listing, delegation, rental, or unlock lock.
5. Runi occupies reserved slot 0 and is counted inside `worker_count`, never inside
   `max_workers_allowed` -- the latter counts monster-card slots only, so a plot's
   monster-card capacity is `max_workers_allowed` as-is (no Runi adjustment needed).
6. Totems/titles/Runi are NEVER moved by this planner (deferred, separate concern) -- their
   boost is read from the plot as-is and held fixed.
7. There is no hard rule against evicting a solo BLOODLINE ("Toil and Kin") holder -- no
   companion sharing its sub_type on the same plot, `plot_bloodline_boost` contributing zero
   from it -- and this planner does not carry one; it never special-cases the holder's
   asset_ref. What protects it, when it genuinely should be protected, is purely economic:
   evicting a plot-wide ability holder costs every co-occupant that ability too (the
   Land/Verico hard rule, the internal rules), and that collateral loss is priced into the move's net
   gain via `_plot_ability_delta`/`plot_bloodline_boost` recomputed on the post-removal
   roster. A removal that evicts a solo holder only wins when it genuinely wins on that true
   net -- a low-value holder with no companion to lose loses nothing extra and can be evicted
   like any other card; a holder whose removal would matter is priced accordingly.

All of the above are defaults, not hardcoded absolutes -- every one is a `ReallocationCriteria`
field so a future run can loosen or tighten them for a different question (e.g. "only look at
rare plots", "allow negative terrain", "only consider cards >= 15000 PP", "don't evict").
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Any, Callable

BIOME_FIELD_BY_COLOR = {
    "Red": "red_biome_modifier",
    "Blue": "blue_biome_modifier",
    "White": "white_biome_modifier",
    "Black": "black_biome_modifier",
    "Green": "green_biome_modifier",
    "Gold": "gold_biome_modifier",
}

IDLE_BUCKETS = ("unstaked_apparent", "stake_expired_or_historical")


def _num(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _cap_allocation(cards: list[dict[str, Any]], cap: float | None) -> dict[str, float] | None:
    """Apply the live-measurement cascade from land-worker-abilities.md §10a.

    Excess is assigned in descending worker-slot order and floored at zero; the clause is n=1.
    This is the shared implementation used by ceres.candidates.
    """
    if cap is None or not math.isfinite(cap):
        return None
    allocation: dict[str, float] = {}
    for card in cards:
        try:
            base = float(card.get("base"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(base) or card.get("asset_ref") is None:
            return None
        allocation[str(card["asset_ref"])] = base
    remaining = sum(allocation.values()) - cap
    if remaining > 0:
        ordered: list[tuple[int, dict[str, Any]]] = []
        for card in cards:
            try:
                slot = int(card.get("slot"))
            except (TypeError, ValueError):
                return None
            if slot <= 0:
                return None
            ordered.append((slot, card))
        if len({slot for slot, _card in ordered}) != len(ordered):
            return None
        for _slot, card in sorted(ordered, key=lambda item: item[0], reverse=True):
            ref = str(card["asset_ref"])
            take = min(remaining, allocation[ref])
            allocation[ref] -= take
            remaining -= take
    target = min(sum(allocation.values()), cap)
    if remaining > 0.01 or abs(sum(allocation.values()) - target) > 0.01:
        return None
    return allocation


def _scoring_allocation(cards: list[dict[str, Any]], cap: float | None) -> dict[str, float] | None:
    if cap is not None:
        return _cap_allocation(cards, cap)
    allocation: dict[str, float] = {}
    for card in cards:
        try:
            base = float(card.get("base"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(base) or card.get("asset_ref") is None:
            return None
        allocation[str(card["asset_ref"])] = base
    return allocation


def _allocation_failure_reason(cards: list[dict[str, Any]], cap: float | None) -> str:
    if cap is None or not math.isfinite(cap):
        return "cap_allocation_failed"
    bases: list[float] = []
    for card in cards:
        try:
            base = float(card.get("base"))
        except (TypeError, ValueError):
            return "cap_allocation_failed"
        if not math.isfinite(base) or card.get("asset_ref") is None:
            return "cap_allocation_failed"
        bases.append(base)
    if sum(bases) <= cap:
        return "cap_allocation_failed"
    if any(
        card.get("slot") is None and card.get("stake_type_uid") != "STK-LND-RUNI"
        for card in cards
    ):
        return "worker_slot_pending"
    return "cap_allocation_failed"


@dataclass
class Plot:
    uid: str
    plot: str
    rarity: str
    status: str
    resource: str | None
    territory: str
    card_capacity: int
    mult: float
    base_cap: float
    biome: dict[str, float]
    cards: list[dict[str, Any]] = field(default_factory=list)

    def base_filled(self) -> float:
        return sum(c["base"] for c in self.cards)

    def open_slots(self) -> int:
        return self.card_capacity - len(self.cards)

    def terrain(self, color: str, secondary_color: str | None = None) -> float:
        colors = [color] + ([secondary_color] if secondary_color else [])
        return max(self.biome.get(value, 0.0) for value in colors)


@dataclass
class MovableCard:
    name: str
    color: str
    base: float
    current_harvest: float
    origin: str  # "idle" | "staked"
    source_uid: str | None
    source_rarity: str | None = None
    bucket: str | None = None
    dec_needed: float | None = None
    bcx: int | None = None
    gold: bool | None = None
    card_set: str | None = None
    card_detail_id: int | None = None      # needed to key stats.land_abilities -- see docs/land-worker-abilities.md
    level: int | None = None               # the card's current level (read from the API, never recomputed from bcx
                                            # see docs/land-worker-abilities.md §4)
    asset_ref: str | None = None          # stable_asset_ref of the underlying card; always present when the
                                            # source report carried one (both `workers[]` and `cards[]` always do)
    card_uid: str | None = None            # real Splinterlands card uid; present ONLY when the source report was
                                            # captured with --include-asset-ids (Fides needs this to build ops
                                            # see docs/plans/fides-batch-signing.md)
    source_slot: int | str | None = None   # the slot this card currently occupies, for a "staked" origin card only
    stake_end_date: str | None = None      # for "idle" cards in the stake_expired_or_historical bucket -- when the
                                            # prior stake ended, needed by a consumer to gate on cooldown

    secondary_color: str | None = None


@dataclass
class ReallocationCriteria:
    """Every knob a caller can turn. Defaults match the corrected methodology above."""

    target_rarities: frozenset[str] | None = None       # None = any rarity is a valid destination
    target_statuses: frozenset[str] | None = None        # None = any status
    target_plots: frozenset[str] | None = None            # explicit plot-code override; takes priority
    rarity_priority: tuple[str, ...] = ("legendary", "epic", "rare", "common")
    status_priority: tuple[str, ...] = ("magical", "occupied", "natural")
    min_mult: float | None = None
    source_rarities: frozenset[str] = frozenset({"common", "rare"})  # staked tiers eligible as movers
    include_idle: bool = True
    min_card_pp: float | None = None                     # e.g. 10000.0 to require 10k+ everywhere
    require_nonnegative_terrain: bool = True
    allow_eviction: bool = True
    reserved: frozenset[tuple[str, float]] = frozenset()  # (name, base_pp) pairs already spoken for
    assume_capacity_for_unpowered: int | None = None  # if set, a plot with 0 max_workers_allowed that
    # is explicitly named in target_plots is treated as having this many card slots instead of 0
    # use when the plot is unpowered today but the caller intends to power it (never applied silently
    # to plots selected only by rarity/status/min_mult, only to explicit target_plots).


@dataclass
class ReallocationMove:
    card: MovableCard
    destination_uid: str
    destination_plot: str
    new_harvest: float
    evicted: dict[str, Any] | None
    card_abilities_boost: float = 0.0     # this card's own resource-match land ability at the destination
    card_bloodlines_boost: float = 0.0    # plot-wide Toil-and-Kin boost active on the destination after this move
    side_effects: list[dict[str, Any]] = field(default_factory=list)  # other cards on the source/destination
    # plots whose harvest changed as a side effect of this move's bloodline swing -- see docs/land-worker-abilities.md §3


@dataclass
class ReallocationPlan:
    moves: list[ReallocationMove]
    backfill: list[dict[str, Any]]

    def primary_net(self) -> float:
        return sum(m.new_harvest - m.card.current_harvest - (m.evicted["cur"] if m.evicted else 0.0) for m in self.moves)

    def backfill_gain(self) -> float:
        return sum(row["harvest"] for row in self.backfill)

    def total_net(self) -> float:
        return self.primary_net() + self.backfill_gain()


@dataclass(frozen=True)
class CandidateEvaluation:
    card: MovableCard
    terrain_boost: float | None
    construction_pp: dict[str, Any]
    # Stage-one (construction) PP delta this candidate would cause -- the incoming
    # candidate's own construction PP minus the evicted occupant's own construction PP for a
    # swap, or the candidate's own construction PP alone for a plain add (no occupant).
    # Distinct from `construction_pp` (the candidate's own value) and from `plot_impact`
    # (stage-two harvest delta) -- the internal rules: never collapse the two stages.
    construction_pp_delta: dict[str, Any]
    # Stage-ZERO (raw Base PP) delta -- the incoming candidate's raw land_base_pp minus the
    # raw land_base_pp of the occupant this move would evict (0 for a plain add). Pre-terrain
    # and pre-cap by construction, so unlike `construction_pp_delta` it is NEVER made unknown
    # by the 100,000 cap or by an unknown terrain modifier. Never collapse it into
    # `construction_pp_delta`: they are different quantities that coincide only at 0% terrain.
    base_pp_delta: dict[str, Any]
    plot_impact: dict[str, Any]
    compatibility_score: float | None
    compatibility_breakdown: dict[str, Any] | None
    plot_impact_breakdown: dict[str, Any] | None


def _level_by_asset_ref(report: dict[str, Any]) -> dict[str, int | None]:
    """`report["cards"]` (the full `/cards/collection` sweep) covers every asset_ref that also
    appears in `report["workers"]` -- staked workers carry no `level` field of their own, so this
    cross-reference is how a staked card's current level is found without reimplementing the BCX
    formula (docs/land-worker-abilities.md §4)."""
    out: dict[str, int | None] = {}
    for row in report.get("cards", []):
        ref = row.get("asset_ref")
        if ref is not None:
            out[ref] = row.get("facts", {}).get("level")
    return out


def build_plots(report: dict[str, Any], criteria: "ReallocationCriteria | None" = None) -> dict[str, Plot]:
    """Build the plot index from a Praetoria land-analysis report (live capture OR a loaded snapshot)."""
    plots: dict[str, Plot] = {}
    level_by_asset = _level_by_asset_ref(report)
    for row in report.get("plots", []):
        for key in ("facts", "staking_facts"):
            if key not in row:
                raise ValueError(
                    f"plot {row.get('deed_uid', '?')} is missing {key!r}: malformed report row"
                ) from KeyError(key)
        facts = row["facts"]
        sf = row["staking_facts"]
        mult = (
            1
            + _num(sf.get("deed_rarity_boost"))
            + _num(sf.get("totem_boost"))
            + _num(sf.get("title_boost"))
            + _num(sf.get("runi_boost"))
            + _num(sf.get("deed_status_token_boost"))
        )
        capacity = int(max(_num(sf.get("max_workers_allowed")), 0))
        if criteria is not None and criteria.assume_capacity_for_unpowered is not None and capacity == 0 \
                and criteria.target_plots is not None and facts["plot"] in criteria.target_plots:
            capacity = criteria.assume_capacity_for_unpowered
        plots[row["deed_uid"]] = Plot(
            uid=row["deed_uid"],
            plot=facts["plot"],
            rarity=facts["rarity"],
            status=facts["status"],
            resource=facts.get("resource"),
            territory=facts["territory"],
            card_capacity=capacity,
            mult=mult,
            base_cap=_num(sf.get("total_base_pp_cap")) or 100000.0,
            biome={color: _num(sf.get(field_name)) for color, field_name in BIOME_FIELD_BY_COLOR.items()},
        )
    for worker in report.get("workers", []):
        if worker.get("asset_kind") not in (None, "card", "worker_card"):
            continue
        uid = worker.get("stake_ref_uid")
        if uid not in plots:
            continue
        plots[uid].cards.append(
            {
                "name": worker.get("name"),
                "color": None,  # filled in by caller with card_details lookup
                "card_detail_id": worker.get("card_detail_id"),
                "level": level_by_asset.get(worker.get("asset_ref")),
                "base": _num(worker.get("land_base_pp")),
                "cur": _num(worker.get("total_harvest_pp")),
                "cur_known": worker.get("total_harvest_pp") not in (None, ""),
                # Stage-one (construction) PP, observed from the snapshot -- distinct from
                # `cur` (stage-two harvest PP). Needed so a removal/swap can report its own
                # Base-PP-stage effect without re-deriving it from base*(1+terrain), which
                # would silently drop any per-card cap allocation already baked into the
                # observed figure. See the internal rules's two-stage PP chain.
                "construction_pp": _num(worker.get("total_construction_pp")),
                "asset_ref": worker.get("asset_ref"),
                "card_uid": worker.get("uid"),  # only present when the report was captured with --include-asset-ids
                "stake_type_uid": worker.get("stake_type_uid"),
                "slot": worker.get("slot"),
            }
        )
    return plots


def attach_colors(plots: dict[str, Plot], details_by_id: dict[int, dict[str, Any]]) -> None:
    for plot in plots.values():
        for card in plot.cards:
            detail = details_by_id.get(card.get("card_detail_id"), {})
            card["color"] = detail.get("color", "Gray")
            card["secondary_color"] = detail.get("secondary_color")


def gather_idle_cards(report: dict[str, Any], details_by_id: dict[int, dict[str, Any]], *, now: str, min_pp: float | None, reserved: frozenset[tuple[str, float]]) -> list[MovableCard]:
    """Idle = never staked OR stake ended, and not blocked by market/rental/lock."""
    out: list[MovableCard] = []
    for card in report.get("cards", []):
        bucket = card.get("classification", {}).get("bucket")
        if bucket not in IDLE_BUCKETS:
            continue
        facts = card["facts"]
        pp = _num(facts.get("land_base_pp"))
        if pp <= 0:
            continue
        if min_pp is not None and pp < min_pp:
            continue
        if facts.get("market_id") is not None or facts.get("delegated_to") is not None or facts.get("rental_type") is not None:
            continue
        unlock = facts.get("unlock_date")
        if unlock and unlock > now:
            continue
        detail = details_by_id.get(facts.get("card_detail_id"), {})
        name = detail.get("name", "?")
        if (name, round(pp, 3)) in reserved:
            continue
        out.append(
            MovableCard(
                name=name,
                color=detail.get("color", "Gray"),
                secondary_color=detail.get("secondary_color") or facts.get("secondary_color"),
                base=pp,
                current_harvest=0.0,
                origin="idle",
                source_uid=None,
                bucket=bucket,
                dec_needed=facts.get("land_dec_stake_needed"),
                bcx=facts.get("bcx"),
                gold=facts.get("gold"),
                card_set=facts.get("card_set"),
                card_detail_id=facts.get("card_detail_id"),
                level=facts.get("level"),
                asset_ref=card.get("asset_ref"),
                card_uid=card.get("uid"),  # only present when the report was captured with --include-asset-ids
                stake_end_date=facts.get("stake_end_date"),
            )
        )
    return out


def gather_staked_candidates(plots: dict[str, Plot], *, source_rarities: frozenset[str], min_pp: float | None, reserved: frozenset[tuple[str, float]]) -> list[MovableCard]:
    out: list[MovableCard] = []
    for plot in plots.values():
        if plot.rarity not in source_rarities:
            continue
        for card in plot.cards:
            if min_pp is not None and card["base"] < min_pp:
                continue
            if (card["name"], round(card["base"], 3)) in reserved:
                continue
            out.append(
                MovableCard(
                    name=card["name"],
                    color=card["color"],
                    secondary_color=card.get("secondary_color"),
                    base=card["base"],
                    current_harvest=card["cur"],
                    origin="staked",
                    source_uid=plot.uid,
                    source_rarity=plot.rarity,
                    card_detail_id=card.get("card_detail_id"),
                    level=card.get("level"),
                    asset_ref=card.get("asset_ref"),
                    card_uid=card.get("card_uid"),
                    source_slot=card.get("slot"),
                )
            )
    return out


def select_destinations(plots: dict[str, Plot], criteria: ReallocationCriteria) -> list[str]:
    candidates = []
    for uid, plot in plots.items():
        if criteria.target_plots is not None:
            if plot.plot not in criteria.target_plots:
                continue
        else:
            if criteria.target_rarities is not None and plot.rarity not in criteria.target_rarities:
                continue
            if criteria.target_statuses is not None and plot.status not in criteria.target_statuses:
                continue
            if criteria.min_mult is not None and plot.mult < criteria.min_mult:
                continue
        candidates.append(uid)

    def sort_key(uid: str) -> tuple:
        plot = plots[uid]
        rarity_rank = criteria.rarity_priority.index(plot.rarity) if plot.rarity in criteria.rarity_priority else len(criteria.rarity_priority)
        status_rank = criteria.status_priority.index(plot.status) if plot.status in criteria.status_priority else len(criteria.status_priority)
        return (rarity_rank, status_rank, -plot.mult)

    return sorted(candidates, key=sort_key)


def _land_ability_row(card_detail_id: int | None, level: int | None, details_by_id: dict[int, dict[str, Any]]) -> list | None:
    """This card's `stats.land_abilities` row at its current level, or None if it has no
    table or its level is out of range. Carrying the table IS the test -- there is no
    separate identity check, deliberately: a hardcoded id list drifted from the data and
    silently hid two cards' abilities (). Never reimplements the BCX->level formula
    -- `level` is always the API-sourced value threaded through by the caller (see
    library/land-worker-abilities.md §4)."""
    if not level or level < 1:
        return None
    detail = details_by_id.get(card_detail_id) if card_detail_id is not None else None
    table = (detail or {}).get("stats", {}).get("land_abilities")
    if not table or level > len(table):
        return None
    return table[level - 1]


def card_abilities_boost(card_detail_id: int | None, level: int | None, resource: str | None,
                          details_by_id: dict[int, dict[str, Any]], _cache: dict | None = None) -> float:
    """This ONE card's own resource-match ability rate at its current level, from its own
    `land_abilities` table entry (GRAIN/WOOD/STONE/IRON/AURA/RESEARCH/SPS) matching `resource` --
    a pure per-card table lookup, always 0.0 for an ordinary battle card (no table to hold an entry
    in the first place). NOT what lands on this card's own harvest -- see `plot_abilities_boost`
    below for that; this is only the building block it's computed from."""
    if not resource:
        return 0.0
    cache_key = ("ability", card_detail_id, level, resource)
    if _cache is not None and cache_key in _cache:
        return _cache[cache_key]
    row = _land_ability_row(card_detail_id, level, details_by_id)
    if not row:
        if _cache is not None:
            _cache[cache_key] = 0.0
        return 0.0
    for entry in row:
        if entry[0] == resource:
            value = _num(entry[1])
            if _cache is not None:
                _cache[cache_key] = value
            return value
    if _cache is not None:
        _cache[cache_key] = 0.0
    return 0.0


def plot_abilities_boost(roster: list[dict[str, Any]], resource: str | None, details_by_id: dict[int, dict[str, Any]], _cache: dict | None = None) -> float:
    """The plot-wide resource-match ("Bountiful ...") boost actually applied to every card's
    harvest on the plot. **Correction to the original per-card model** (docs/land-worker-abilities.md
    §1/§2, and the internal rules's Land/Verico hard rule, both describe this as a private per-card
    contribution) -- re-verified live against `player0005`'s full account on 2026-08-19 during this fix:
    EVERY worker on a given plot shows the IDENTICAL `card_abilities_boost` value with zero
    exceptions across all 107 currently-staked plots, and that value always equals the MAX
    resource-matching table rate among the plot's ability-holders (the same "same ability never
    stacks, strongest instance wins" rule §3/§2a already documents for BLOODLINE and other ability
    codes generally) -- never the sum, and never scoped to only the holder itself. This function
    implements the corrected, empirically-verified mechanism; `card_abilities_boost` above is kept
    only as the per-card table-lookup building block it's computed from."""
    if not resource:
        return 0.0
    best = 0.0
    for r in roster:
        best = max(best, card_abilities_boost(r.get("card_detail_id"), r.get("level"), resource, details_by_id, _cache))
    return best


def bloodline_holder(card_detail_id: int | None, level: int | None, details_by_id: dict[int, dict[str, Any]], _cache: dict | None = None) -> tuple[float, str] | None:
    """(rate, sub_type) if this card is a BLOODLINE ("Toil and Kin") ability HOLDER at its current
    level, else None. Only a Land/Verico card can ever be a holder -- an ordinary battle card can
    still be the COMPANION that triggers someone else's bloodline (matched purely by sub_type, see
    `plot_bloodline_boost`), but never holds an ability of its own (docs/land-worker-abilities.md §3)."""
    cache_key = ("bloodline", card_detail_id, level)
    if _cache is not None and cache_key in _cache:
        return _cache[cache_key]
    row = _land_ability_row(card_detail_id, level, details_by_id)
    if not row:
        if _cache is not None:
            _cache[cache_key] = None
        return None
    for entry in row:
        if entry[0] == "BLOODLINE":
            value = (_num(entry[1]), entry[2])
            if _cache is not None:
                _cache[cache_key] = value
            return value
    if _cache is not None:
        _cache[cache_key] = None
    return None


def plot_bloodline_boost(roster: list[dict[str, Any]], details_by_id: dict[int, dict[str, Any]], _cache: dict | None = None) -> float:
    """The plot-wide "Toil and Kin" boost, applied uniformly to every card on the plot's harvest --
    NOT per-holder, NOT per-companion (docs/land-worker-abilities.md §3, validated 107/107 plots,
    0 mismatches). `roster` is the plot's card list (or dicts shaped like it: needs only
    `card_detail_id` and `level` per entry). An ability-holder's bonus triggers only if at least one
    OTHER card on the SAME plot shares its sub_type -- the companion can be any card, Land/Verico or
    an ordinary battle card, and does not itself need any land ability. Toil and Kin does NOT stack:
    of all triggered bloodline bonuses on the plot, only the single largest applies (maintainer rule
    ,  -- supersedes the earlier "distinct bloodlines stack additively" reading)."""
    sub_types = [details_by_id.get(r.get("card_detail_id"), {}).get("sub_type") for r in roster]
    triggered: dict[str, float] = {}
    for i, r in enumerate(roster):
        holder = bloodline_holder(r.get("card_detail_id"), r.get("level"), details_by_id, _cache)
        if holder is None:
            continue
        rate, sub_type = holder
        if any(j != i and sub_types[j] == sub_type for j in range(len(roster))):
            triggered[sub_type] = max(triggered.get(sub_type, 0.0), rate)
    return max(triggered.values(), default=0.0)


def _harvest(base: float, terrain: float, mult: float, abilities_boost: float, bloodline_boost: float) -> float:
    return base * (1 + terrain) * (mult + abilities_boost + bloodline_boost)


def _plot_roster(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"card_detail_id": c.get("card_detail_id"), "level": c.get("level")} for c in cards]


def _recompute_others(cards: list[dict[str, Any]], plot: Plot, abilities_boost: float, bloodline_boost: float,
                       details_by_id: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Recompute `cur` IN PLACE for every card dict remaining on a plot against freshly-settled
    plot-wide abilities and bloodline boosts -- BOTH are roster-dependent plot broadcasts (see
    `plot_abilities_boost` and `plot_bloodline_boost`), so both must be recomputed whenever the
    plot's roster changes. This is what makes a move's side effects on OTHER occupants real: adding
    an ability-holder or a bloodline companion can retroactively raise everyone else's harvest on
    the plot, and removing the sole holder/companion of either lowers it back down for everyone.
    Returns a row per card whose `cur` actually changed."""
    side_effects = []
    for c in cards:
        new_cur = _harvest(c["base"], plot.terrain(c["color"], c.get("secondary_color")), plot.mult, abilities_boost, bloodline_boost)
        if abs(new_cur - c["cur"]) > 1e-6:
            side_effects.append({"name": c["name"], "base": c["base"], "plot": plot.plot, "before": c["cur"], "after": new_cur})
        c["cur"] = new_cur
    return side_effects


def _sync_pool_current_harvest(pool: list[MovableCard], side_effects: list[dict[str, Any]]) -> None:
    """A side effect landing on a card that is itself still an un-moved candidate in `pool` must be
    reflected in that candidate's `current_harvest` -- otherwise a later iteration evaluating THAT
    card as a mover would price it against a stale, pre-side-effect baseline."""
    if not side_effects:
        return
    by_key = {(row["name"], round(row["base"], 3)): row["after"] for row in side_effects}
    for mc in pool:
        key = (mc.name, round(mc.base, 3))
        if key in by_key:
            mc.current_harvest = by_key[key]


def _score_candidate_points_for_plot(
    plot: Plot,
    candidates: list[MovableCard],
    *,
    target_slot_occupant: MovableCard | None,
    details_by_id: dict[int, dict[str, Any]],
    base_pp_ceiling: float | None = None,
    before_powered: bool = True,
    after_powered: bool = True,
    target_slot_occupants: list[MovableCard] | None = None,
    target_slot: int | None = None,
    before_mult: float | None = None,
    after_mult: float | None = None,
) -> list[tuple[MovableCard, float, dict[str, Any]]]:
    """Score candidate replacements as whole-plot harvest deltas without mutation."""
    cache: dict = {}
    before_mult = plot.mult if before_mult is None else before_mult
    after_mult = plot.mult if after_mult is None else after_mult

    def settled(cards: list[dict[str, Any]], mult: float) -> tuple[float, float, float, dict[str, float]] | None:
        allocation = _scoring_allocation(cards, base_pp_ceiling)
        if allocation is None:
            return None
        abilities = max((
            card_abilities_boost(card.get("card_detail_id"), card.get("level"), plot.resource, details_by_id, cache)
            for card in cards
        ), default=0.0)
        bloodline = plot_bloodline_boost(cards, details_by_id, cache)
        total = sum(
            _harvest(allocation[str(card["asset_ref"])], plot.terrain(card.get("color", "Gray"), card.get("secondary_color")), mult, abilities, bloodline)
            for card in cards
        )
        return total, abilities, bloodline, allocation

    current_cards = [dict(card) for card in plot.cards]
    current_settled = settled(current_cards, before_mult)
    if current_settled is None:
        return []
    current_full, _current_abilities, _current_bloodline, _current_allocation = current_settled
    current_total = current_full if before_powered else 0.0
    results: list[tuple[MovableCard, float, dict[str, Any]]] = []
    occupants = target_slot_occupants or [target_slot_occupant]
    card_rates = {
        index: card_abilities_boost(card.get("card_detail_id"), card.get("level"), plot.resource, details_by_id, cache)
        for index, card in enumerate(current_cards)
    }

    def bloodline_context(indices: list[int]) -> dict[str, Any]:
        subtype_counts: dict[str, int] = {}
        holders: dict[str, list[tuple[float, bool]]] = {}
        for index in indices:
            card = current_cards[index]
            own_subtype = details_by_id.get(card.get("card_detail_id"), {}).get("sub_type")
            if own_subtype is not None:
                subtype_counts[str(own_subtype)] = subtype_counts.get(str(own_subtype), 0) + 1
            holder = bloodline_holder(card.get("card_detail_id"), card.get("level"), details_by_id, cache)
            if holder is not None:
                rate, holder_subtype = holder
                key = str(holder_subtype)
                holders.setdefault(key, []).append((rate, str(own_subtype) == key))
        triggered = {
            subtype: max((rate for rate, own_matches in entries if subtype_counts.get(subtype, 0) - int(own_matches) >= 1), default=0.0)
            for subtype, entries in holders.items()
        }
        return {"subtype_counts": subtype_counts, "holders": holders, "triggered": triggered, "total": sum(triggered.values())}

    def candidate_bloodline(target: dict[str, Any], candidate: MovableCard) -> float:
        if candidate.base <= 0:
            return target["bloodline"]["total"]
        state = target["bloodline"]
        candidate_subtype_value = details_by_id.get(candidate.card_detail_id, {}).get("sub_type")
        candidate_subtype = str(candidate_subtype_value) if candidate_subtype_value is not None else None
        candidate_holder = bloodline_holder(candidate.card_detail_id, candidate.level, details_by_id, cache)
        affected = set()
        if candidate_subtype in state["holders"]:
            affected.add(candidate_subtype)
        if candidate_holder is not None:
            affected.add(str(candidate_holder[1]))
        total = state["total"] - sum(state["triggered"].get(subtype, 0.0) for subtype in affected)
        for subtype in affected:
            count = state["subtype_counts"].get(subtype, 0) + int(candidate_subtype == subtype)
            entries = state["holders"].get(subtype, [])
            if candidate_holder is not None and str(candidate_holder[1]) == subtype:
                entries = [*entries, (candidate_holder[0], candidate_subtype == subtype)]
            total += max((rate for rate, own_matches in entries if count - int(own_matches) >= 1), default=0.0)
        return total

    def context(occupant: MovableCard | None) -> dict[str, Any] | None:
        removed_index = None
        if occupant is not None:
            for index, card in enumerate(current_cards):
                if occupant.asset_ref and card.get("asset_ref") == occupant.asset_ref:
                    removed_index = index
                    break
                if occupant.card_uid and card.get("card_uid") == occupant.card_uid:
                    removed_index = index
                    break
            if removed_index is None:
                return None
        remaining = [index for index in range(len(current_cards)) if index != removed_index]
        remaining_cards = [current_cards[index] for index in remaining]
        remaining_allocation = _scoring_allocation(remaining_cards, base_pp_ceiling)
        if remaining_allocation is None:
            return None
        base_weight = sum(
            remaining_allocation[str(current_cards[index]["asset_ref"])]
            * (1 + plot.terrain(current_cards[index].get("color", "Gray"), current_cards[index].get("secondary_color")))
            for index in remaining
        )
        return {
            "removed_index": removed_index, "remaining": remaining, "base": base_weight,
            "base_total": sum(_num(current_cards[index].get("base")) for index in remaining),
            "ability": max((card_rates[index] for index in remaining), default=0.0),
            "bloodline": bloodline_context(remaining),
        }

    contexts = [(occupant, context(occupant)) for occupant in occupants]
    for candidate in candidates:
        candidate_results: list[tuple[MovableCard, float, dict[str, Any]]] = []
        candidate_ability = card_abilities_boost(candidate.card_detail_id, candidate.level, plot.resource, details_by_id, cache)
        for occupant, target in contexts:
            if target is None:
                continue
            evicted: dict[str, Any] | None = None
            if target["removed_index"] is not None:
                removed = current_cards[target["removed_index"]]
                evicted = {
                    "asset_ref": removed.get("asset_ref"), "name": removed.get("name"),
                    "cur": _num(removed.get("cur")), "base": _num(removed.get("base")),
                    # The evicted occupant's own observed stage-one PP -- see the
                    # `construction_pp_delta` estimate below, which nets this against the
                    # incoming candidate's own construction PP for a swap.
                    "construction_pp": _num(removed.get("construction_pp")),
                }
            abilities = max(target["ability"], candidate_ability) if candidate.base > 0 else target["ability"]
            bloodline = candidate_bloodline(target, candidate)
            resulting_cards = [current_cards[index] for index in target["remaining"]]
            candidate_after_cap = 0.0
            if candidate.base > 0:
                candidate_slot = target_slot
                if candidate_slot is None and occupant is not None:
                    candidate_slot = occupant.source_slot
                if candidate_slot is None:
                    occupied_slots: set[int] = set()
                    for card in current_cards:
                        raw_slot = card.get("slot")
                        try:
                            slot = int(raw_slot)
                        except (TypeError, ValueError):
                            continue
                        if isinstance(raw_slot, bool) or (
                            isinstance(raw_slot, float) and not raw_slot.is_integer()
                        ):
                            continue
                        occupied_slots.add(slot)
                    candidate_slot = next(slot for slot in range(1, len(current_cards) + 2) if slot not in occupied_slots)
                resulting_cards.append({
                    "base": candidate.base, "slot": candidate_slot,
                    "asset_ref": candidate.asset_ref, "card_uid": candidate.card_uid,
                    "card_detail_id": candidate.card_detail_id, "level": candidate.level,
                    "color": candidate.color, "secondary_color": candidate.secondary_color,
                })
            resulting_settled = settled(resulting_cards, after_mult)
            if resulting_settled is None:
                continue
            resulting_full, _after_abilities, _after_bloodline, resulting_allocation = resulting_settled
            if candidate.base > 0:
                candidate_after_cap = resulting_allocation.get(str(candidate.asset_ref))
                if candidate_after_cap is None:
                    continue
            resulting_total = resulting_full if after_powered else 0.0
            score = resulting_total - current_total
            own_after = 0.0
            if candidate.base > 0:
                own_after = _harvest(candidate_after_cap, plot.terrain(candidate.color, candidate.secondary_color), after_mult, abilities, bloodline)
            own_delta = own_after - candidate.current_harvest
            evicted_loss = -evicted["cur"] if evicted else 0.0
            ungated_delta = resulting_full - current_full
            breakdown = {
                "own_harvest_delta": own_delta,
                "evicted_loss": evicted_loss,
                "plot_residual_delta": ungated_delta - own_delta - evicted_loss,
                "precondition_delta": score - ungated_delta,
                "terrain_matched": plot.terrain(candidate.color, candidate.secondary_color) >= 0,
                "current_harvest": current_total,
                "resulting_harvest": resulting_total,
                "precondition": {"before": before_powered, "after": after_powered},
                "evicted": evicted,
                "candidate_base_pp_after_cap": candidate_after_cap,
            }
            candidate_results.append((candidate, score, breakdown))
        if candidate_results:
            results.append(max(candidate_results, key=lambda result: result[1]))
    return results


def _estimate(status: str, *, value: float | None = None, low: float | None = None,
              high: float | None = None, reason: str | None = None) -> dict[str, Any]:
    numbers = (value, low, high)
    if any(number is not None and not math.isfinite(number) for number in numbers):
        status, value, low, high, reason = "unknown", None, None, None, "non_finite_evaluation"
    return {"status": status, "value": value, "low": low, "high": high, "reason": reason}


def evaluate_candidates_for_plot(
    plot: Plot,
    candidates: list[MovableCard],
    *,
    target_slot_occupant: MovableCard | None,
    details_by_id: dict[int, dict[str, Any]],
    base_pp_ceiling: float | None = None,
    before_powered: bool = True,
    after_powered: bool = True,
    target_slot_occupants: list[MovableCard] | None = None,
    target_slot: int | None = None,
    before_mult: float | None = None,
    after_mult: float | None = None,
    construction_applicable: bool = True,
    score_candidate_base: bool = True,
) -> list[CandidateEvaluation]:
    """Add certainty metadata without changing the legacy point-score contract."""
    scoring_candidates = candidates if score_candidate_base else [replace(candidate, base=0.0) for candidate in candidates]
    scored = _score_candidate_points_for_plot(
        plot, scoring_candidates, target_slot_occupant=target_slot_occupant,
        target_slot_occupants=target_slot_occupants, target_slot=target_slot, details_by_id=details_by_id,
        base_pp_ceiling=base_pp_ceiling, before_powered=before_powered,
        after_powered=after_powered, before_mult=before_mult, after_mult=after_mult,
    )
    scored_by_key = {(card.asset_ref, card.name): (score, breakdown) for card, score, breakdown in scored}
    current_cards = plot.cards
    current_base = sum(_num(card.get("base")) for card in current_cards)
    occupants = target_slot_occupants or [target_slot_occupant]

    def remaining_base(occupant: MovableCard | None) -> float | None:
        if occupant is None:
            return current_base
        for card in current_cards:
            if occupant.asset_ref and card.get("asset_ref") == occupant.asset_ref:
                return current_base - _num(card.get("base"))
            if occupant.card_uid and card.get("card_uid") == occupant.card_uid:
                return current_base - _num(card.get("base"))
        return None

    remaining_bases = [base for occupant in occupants if (base := remaining_base(occupant)) is not None]
    allocation_failure_reason = None
    if construction_applicable and score_candidate_base and _scoring_allocation(plot.cards, base_pp_ceiling) is None:
        allocation_failure_reason = _allocation_failure_reason(plot.cards, base_pp_ceiling)
    target_terrain_unknown = any(
        color not in plot.biome for card in current_cards
        for color in [card.get("color")] + ([card["secondary_color"]] if card.get("secondary_color") else [])
    )
    observed_current_total = (
        sum(_num(card.get("cur")) for card in current_cards)
        if all(card.get("cur_known") is True for card in current_cards)
        else None
    )
    evaluations: list[CandidateEvaluation] = []
    for candidate in candidates:
        scored_result = scored_by_key.get((candidate.asset_ref, candidate.name))
        compatibility_score = scored_result[0] if scored_result is not None else None
        compatibility_breakdown = scored_result[1] if scored_result is not None else None
        impact_score = compatibility_score
        impact_breakdown = dict(compatibility_breakdown) if compatibility_breakdown is not None else None
        is_item = not construction_applicable
        colors = [candidate.color] + ([candidate.secondary_color] if candidate.secondary_color else [])
        terrain_boost = max(plot.biome[color] for color in colors) if all(color in plot.biome for color in colors) else None
        unresolved_target = not remaining_bases

        if is_item:
            construction = _estimate("not_applicable", reason="item_pp_not_applicable")
            impact = _estimate("not_applicable", reason="item_scoring_not_applicable")
        elif allocation_failure_reason is not None:
            construction = _estimate("unknown", reason=allocation_failure_reason)
            impact = _estimate("unknown", reason=allocation_failure_reason)
        elif terrain_boost is None:
            construction = _estimate("unknown", reason="terrain_modifier_unknown")
            impact = _estimate("unknown", reason="terrain_modifier_unknown")
        else:
            construction_value = candidate.base * (1 + terrain_boost)
            construction = _estimate(
                "point", value=construction_value, low=construction_value, high=construction_value,
            )
            if target_terrain_unknown:
                impact = _estimate("unknown", reason="target_terrain_modifier_unknown")
            elif before_powered and observed_current_total is None:
                impact = _estimate("unknown", reason="current_harvest_unknown")
            elif unresolved_target or impact_score is None:
                impact = _estimate("unknown", reason="target_roster_unknown")
            else:
                impact = _estimate(
                    "point", value=impact_score, low=impact_score, high=impact_score,
                )
        # Stage-one net delta: the candidate's own construction PP minus the evicted
        # occupant's own observed construction PP (0 when there is no occupant, i.e. a
        # plain add into an empty slot) -- construction has no plot-wide broadcast (that's
        # a harvest-stage-only mechanic), so this is always an exact figure once the
        # candidate's own construction PP is known; it inherits `construction`'s status
        # (unknown/not_applicable) whenever that isn't a point value.
        if is_item:
            construction_delta = _estimate("not_applicable", reason="item_pp_not_applicable")
        elif construction.get("status") != "point":
            construction_delta = dict(construction)
        elif impact_score is None or compatibility_breakdown is None:
            construction_delta = _estimate("unknown", reason="target_roster_unknown")
        else:
            evicted = compatibility_breakdown.get("evicted")
            own_construction_before = _num(evicted.get("construction_pp")) if evicted else 0.0
            delta_value = construction_value - own_construction_before
            construction_delta = _estimate("point", value=delta_value, low=delta_value, high=delta_value)
        if is_item or not score_candidate_base:
            base_delta = _estimate("not_applicable", reason="item_pp_not_applicable")
        elif target_slot_occupant is not None:
            value = candidate.base - _num(target_slot_occupant.base)
            base_delta = _estimate("point", value=value, low=value, high=value)
        elif target_slot_occupants and unresolved_target:
            # Whole-roster fail-safe (candidates.py:876-882) with no target resolved
            # which occupant would be evicted is genuinely not decided yet.
            base_delta = _estimate("unknown", reason="target_slot_unresolved")
        else:
            # A plain add into an empty slot: nothing is evicted, so the delta is the
            # candidate's own raw base. This is the over-cap staking case and it MUST
            # reach a point value -- it is the population the ticket exists for.
            base_delta = _estimate("point", value=candidate.base, low=candidate.base, high=candidate.base)
        evaluations.append(CandidateEvaluation(
            card=candidate, terrain_boost=terrain_boost, construction_pp=construction,
            construction_pp_delta=construction_delta,
            base_pp_delta=base_delta,
            plot_impact=impact, compatibility_score=compatibility_score,
            compatibility_breakdown=compatibility_breakdown,
            plot_impact_breakdown=impact_breakdown if impact.get("status") == "point" else None,
        ))
    return evaluations


def score_candidates_for_plot(
    plot: Plot,
    candidates: list[MovableCard],
    *,
    target_slot_occupant: MovableCard | None,
    details_by_id: dict[int, dict[str, Any]],
    base_pp_ceiling: float | None = None,
    before_powered: bool = True,
    after_powered: bool = True,
    target_slot_occupants: list[MovableCard] | None = None,
    before_mult: float | None = None,
    after_mult: float | None = None,
) -> list[tuple[MovableCard, float, dict[str, Any]]]:
    """Compatibility wrapper returning only legacy point-score tuples."""
    evaluations = evaluate_candidates_for_plot(
        plot, candidates, target_slot_occupant=target_slot_occupant,
        target_slot_occupants=target_slot_occupants, details_by_id=details_by_id,
        base_pp_ceiling=base_pp_ceiling, before_powered=before_powered,
        after_powered=after_powered, before_mult=before_mult, after_mult=after_mult,
    )
    return [
        (evaluation.card, evaluation.compatibility_score, evaluation.compatibility_breakdown or {})
        for evaluation in evaluations
        if evaluation.compatibility_score is not None
    ]


def plan_reallocation(
    report: dict[str, Any],
    details_by_id: dict[int, dict[str, Any]],
    criteria: ReallocationCriteria,
    *,
    now: str,
    progress: Callable[[str], None] | None = None,
) -> tuple[ReallocationPlan, dict[str, "Plot"]]:
    """Chain-complete reallocation: every accepted move wins on destination math alone
    (no backfill subsidy). A second, independent pass then backfills every plot a card
    was moved out of, using cards not already claimed above."""
    plots = build_plots(report, criteria)
    attach_colors(plots, details_by_id)

    idle_pool = gather_idle_cards(report, details_by_id, now=now, min_pp=criteria.min_card_pp, reserved=criteria.reserved) if criteria.include_idle else []
    staked_pool = gather_staked_candidates(plots, source_rarities=criteria.source_rarities, min_pp=criteria.min_card_pp, reserved=criteria.reserved)
    destinations = select_destinations(plots, criteria)

    used_idle: set[int] = set()
    moves: list[ReallocationMove] = []
    changed = True
    while changed:
        changed = False
        best = None
        for pool, origin in ((staked_pool, "staked"), (idle_pool, "idle")):
            for card in pool:
                if origin == "idle" and id(card) in used_idle:
                    continue
                for duid in destinations:
                    if origin == "staked" and card.source_uid == duid:
                        continue
                    dest = plots[duid]
                    t = dest.terrain(card.color, card.secondary_color)
                    if criteria.require_nonnegative_terrain and t < 0:
                        continue
                    evict = None
                    if dest.open_slots() <= 0:
                        if not criteria.allow_eviction or not dest.cards:
                            continue
                        evict = min(dest.cards, key=lambda c: c["cur"])
                        headroom = dest.base_cap - (dest.base_filled() - evict["base"])
                    else:
                        headroom = dest.base_cap - dest.base_filled()
                    if card.base > headroom:
                        continue

                    # Both boost categories are plot-wide broadcasts, roster-dependent (see
                    # `plot_abilities_boost`/`plot_bloodline_boost`) -- evaluate the destination's
                    # roster BEFORE and AFTER this hypothetical move (minus any eviction, plus the
                    # moved card) so the swing can be priced for every occupant, not just the mover
                    # (docs/land-worker-abilities.md §1-§3, corrected 2026-08-19 during this fix).
                    dest_others = [c for c in dest.cards if c is not evict]
                    dest_roster_before = _plot_roster(dest.cards)
                    dest_roster_after = _plot_roster(dest_others) + [{"card_detail_id": card.card_detail_id, "level": card.level}]
                    dest_abilities_before = plot_abilities_boost(dest_roster_before, dest.resource, details_by_id)
                    dest_bloodline_before = plot_bloodline_boost(dest_roster_before, details_by_id)
                    dest_abilities_after = plot_abilities_boost(dest_roster_after, dest.resource, details_by_id)
                    dest_bloodline_after = plot_bloodline_boost(dest_roster_after, details_by_id)
                    gain = _harvest(card.base, t, dest.mult, dest_abilities_after, dest_bloodline_after)
                    lost = evict["cur"] if evict else 0.0
                    others_dest_delta = sum(
                        c["base"] * (1 + dest.terrain(c["color"], c.get("secondary_color")))
                        * ((dest_abilities_after + dest_bloodline_after) - (dest_abilities_before + dest_bloodline_before))
                        for c in dest_others
                    )

                    # A staked mover's departure can also change the SOURCE plot's broadcasts for
                    # whoever is left behind there.
                    others_src_delta = 0.0
                    if origin == "staked":
                        src = plots[card.source_uid]
                        src_others = [c for c in src.cards if not (c["name"] == card.name and c["base"] == card.base)]
                        src_roster_before = _plot_roster(src.cards)
                        src_roster_after = _plot_roster(src_others)
                        src_abilities_before = plot_abilities_boost(src_roster_before, src.resource, details_by_id)
                        src_bloodline_before = plot_bloodline_boost(src_roster_before, details_by_id)
                        src_abilities_after = plot_abilities_boost(src_roster_after, src.resource, details_by_id)
                        src_bloodline_after = plot_bloodline_boost(src_roster_after, details_by_id)
                        others_src_delta = sum(
                            c["base"] * (1 + src.terrain(c["color"], c.get("secondary_color")))
                            * ((src_abilities_after + src_bloodline_after) - (src_abilities_before + src_bloodline_before))
                            for c in src_others
                        )

                    net = gain - lost - card.current_harvest + others_dest_delta + others_src_delta
                    if net > 1e-6 and (best is None or net > best[0]):
                        best = (net, card, duid, evict, gain, origin, dest_abilities_after)
        if best is None:
            break
        net, card, duid, evict, gain, origin, abilities_boost = best
        dest = plots[duid]
        side_effects: list[dict[str, Any]] = []
        if origin == "staked":
            src = plots[card.source_uid]
            src.cards = [c for c in src.cards if not (c["name"] == card.name and c["base"] == card.base)]
            staked_pool.remove(card)
            src_roster_after = _plot_roster(src.cards)
            src_abilities_after = plot_abilities_boost(src_roster_after, src.resource, details_by_id)
            src_bloodline_after = plot_bloodline_boost(src_roster_after, details_by_id)
            src_side_effects = _recompute_others(src.cards, src, src_abilities_after, src_bloodline_after, details_by_id)
            side_effects += src_side_effects
            _sync_pool_current_harvest(staked_pool, src_side_effects)
        else:
            used_idle.add(id(card))
        if evict:
            dest.cards.remove(evict)
        dest_roster_after = _plot_roster(dest.cards) + [{"card_detail_id": card.card_detail_id, "level": card.level}]
        dest_abilities_after = plot_abilities_boost(dest_roster_after, dest.resource, details_by_id)
        dest_bloodline_after = plot_bloodline_boost(dest_roster_after, details_by_id)
        dest_side_effects = _recompute_others(dest.cards, dest, dest_abilities_after, dest_bloodline_after, details_by_id)
        side_effects += dest_side_effects
        _sync_pool_current_harvest(staked_pool, dest_side_effects)
        dest.cards.append({
            "name": card.name, "color": card.color, "secondary_color": card.secondary_color, "base": card.base, "cur": gain,
            "card_detail_id": card.card_detail_id, "level": card.level,
            "asset_ref": card.asset_ref, "card_uid": card.card_uid, "slot": None,
        })
        moves.append(ReallocationMove(
            card=card, destination_uid=duid, destination_plot=dest.plot, new_harvest=gain, evicted=evict,
            card_abilities_boost=abilities_boost, card_bloodlines_boost=dest_bloodline_after, side_effects=side_effects,
        ))
        if progress:
            progress(f"{card.name} ({card.base:.0f} {card.color}) -> {dest.plot}: net +{net:.0f}")
        changed = True

    backfill = _plan_backfill(plots, moves, idle_pool, used_idle, criteria, details_by_id)
    return ReallocationPlan(moves=moves, backfill=backfill), plots


def _plan_backfill(plots: dict[str, Plot], moves: list[ReallocationMove], idle_pool: list[MovableCard], used_idle: set[int],
                    criteria: ReallocationCriteria, details_by_id: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    vacated: dict[str, int] = {}
    for move in moves:
        if move.card.origin == "staked":
            vacated[move.card.source_uid] = vacated.get(move.card.source_uid, 0) + 1
    remaining = sorted((c for c in idle_pool if id(c) not in used_idle), key=lambda c: -c.base)
    out = []
    for uid, n in vacated.items():
        plot = plots[uid]
        headroom = plot.base_cap - plot.base_filled()
        filled = 0
        for card in list(remaining):
            if filled >= n:
                break
            t = plot.terrain(card.color, card.secondary_color)
            if criteria.require_nonnegative_terrain and t < 0:
                continue
            if card.base > headroom:
                continue
            roster_after = _plot_roster(plot.cards) + [{"card_detail_id": card.card_detail_id, "level": card.level}]
            abilities_after = plot_abilities_boost(roster_after, plot.resource, details_by_id)
            bloodline_after = plot_bloodline_boost(roster_after, details_by_id)
            g = _harvest(card.base, t, plot.mult, abilities_after, bloodline_after)
            side_effects = _recompute_others(plot.cards, plot, abilities_after, bloodline_after, details_by_id)
            out.append({
                "plot": plot.plot, "plot_uid": uid, "card": card, "terrain": t, "harvest": g,
                "card_abilities_boost": abilities_after, "card_bloodlines_boost": bloodline_after,
                "side_effects": side_effects,
            })
            plot.cards.append({
                "name": card.name, "color": card.color, "secondary_color": card.secondary_color, "base": card.base, "cur": g,
                "card_detail_id": card.card_detail_id, "level": card.level,
                "asset_ref": card.asset_ref, "card_uid": card.card_uid, "slot": None,
            })
            headroom -= card.base
            filled += 1
            remaining.remove(card)
            used_idle.add(id(card))
    return out


def render_plan_text(plan: ReallocationPlan, plots: dict[str, Plot]) -> str:
    def display_name(name: str | None) -> str:
        return name if name is not None else "[name unavailable]"

    lines: list[str] = []
    by_dest: dict[str, list[ReallocationMove]] = {}
    for move in plan.moves:
        by_dest.setdefault(move.destination_uid, []).append(move)
    for uid, moves in by_dest.items():
        plot = plots[uid]
        fav = "/".join(c for c, v in plot.biome.items() if v > 0) or "-"
        pen = "/".join(c for c, v in plot.biome.items() if v < 0) or "-"
        lines.append("=" * 100)
        lines.append(f"{plot.plot}  {plot.rarity.upper()} / {plot.status.upper()}  mult=x{plot.mult:.2f}  favors {fav}  penalizes {pen}")
        lines.append("=" * 100)
        for move in moves:
            c = move.card
            tag = f"MOVE FROM {c.source_rarity} plot" if c.origin == "staked" else "STAKE idle"
            evict_note = f"  EVICTS {display_name(move.evicted['name'])}({move.evicted['base']:.0f}bp, was {move.evicted['cur']:.0f})" if move.evicted else ""
            was = f" was earning {c.current_harvest:.0f}" if c.origin == "staked" else ""
            lines.append(f"  {tag:<20} {display_name(c.name):<26} {c.color:<6} base={c.base:>7.0f}{was} -> new {move.new_harvest:>9.0f}{evict_note}")
        lines.append("")
    if plan.backfill:
        lines.append("=" * 100)
        lines.append("BACKFILL (vacated source plots, from remaining idle pool)")
        lines.append("=" * 100)
        for row in plan.backfill:
            c = row["card"]
            lines.append(f"  {row['plot']:<10} <- {display_name(c.name):<26} {c.color:<6} base={c.base:>7.0f} terrain{row['terrain']:+.2f} -> {row['harvest']:>9.0f}")
        lines.append("")
    lines.append(f"Primary net: +{plan.primary_net():,.0f}   Backfill: +{plan.backfill_gain():,.0f}   "
                  f"TOTAL: +{plan.total_net():,.0f}")
    return "\n".join(lines) + "\n"


def plan_to_dict(plan: ReallocationPlan) -> dict[str, Any]:
    def card_dict(c: MovableCard) -> dict[str, Any]:
        out = {
            "name": c.name, "color": c.color, "base_pp": c.base, "current_harvest": c.current_harvest,
            "origin": c.origin, "source_uid": c.source_uid, "source_rarity": c.source_rarity,
            "bucket": c.bucket, "dec_needed": c.dec_needed, "bcx": c.bcx, "gold": c.gold, "card_set": c.card_set,
            "card_detail_id": c.card_detail_id, "level": c.level,
            "asset_ref": c.asset_ref, "source_slot": c.source_slot, "stake_end_date": c.stake_end_date,
        }
        if c.card_uid is not None:  # present only when the source report carried --include-asset-ids
            out["card_uid"] = c.card_uid
        return out

    def evicted_dict(e: dict[str, Any] | None) -> dict[str, Any] | None:
        if e is None:
            return None
        out = {"name": e["name"], "base_pp": e["base"], "current_harvest": e["cur"], "asset_ref": e.get("asset_ref"), "slot": e.get("slot")}
        if e.get("card_uid") is not None:
            out["card_uid"] = e["card_uid"]
        return out

    return {
        "moves": [
            {
                "card": card_dict(m.card),
                "destination_plot": m.destination_plot,
                "destination_uid": m.destination_uid,
                "new_harvest": m.new_harvest,
                "evicted": evicted_dict(m.evicted),
                "card_abilities_boost": m.card_abilities_boost,
                "card_bloodlines_boost": m.card_bloodlines_boost,
                "side_effects": m.side_effects,
            }
            for m in plan.moves
        ],
        "backfill": [
            {
                "plot": row["plot"], "plot_uid": row["plot_uid"], "card": card_dict(row["card"]),
                "terrain": row["terrain"], "harvest": row["harvest"],
                "card_abilities_boost": row.get("card_abilities_boost", 0.0),
                "card_bloodlines_boost": row.get("card_bloodlines_boost", 0.0),
                "side_effects": row.get("side_effects", []),
            }
            for row in plan.backfill
        ],
        "primary_net": plan.primary_net(),
        "backfill_gain": plan.backfill_gain(),
        "total_net": plan.total_net(),
    }
