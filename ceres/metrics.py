"""DEC, multiplier, and ability-holder computations for the Ceres board.

These are the non-negotiable metrics rules from the plan's §6.4 -- see
``docs/land-worker-abilities.md`` for the underlying mechanism and
``work/ceres-dec-research/findings.md`` for what is and is not derivable about DEC.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Codes carried as flags only (no rate, no sub_type) in a land_abilities table row.
_FLAG_CODES = {"ENERGIZED", "LL", "ALTERATION", "GLINT_RECOVERY", "FORTUNE_SEEKER"}


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def land_ability_row(card_detail_id: int | None, level: int | None, details_by_id: dict[int, dict[str, Any]]) -> list[Any] | None:
    """This card's ``stats.land_abilities`` row at its current level, or None. Level is
    always the API-sourced value threaded through by the caller -- never recomputed from
    BCX (docs/land-worker-abilities.md §4)."""
    if not level or level < 1:
        return None
    detail = details_by_id.get(card_detail_id) if card_detail_id is not None else None
    table = (detail or {}).get("stats", {}).get("land_abilities")
    if not table or level > len(table):
        return None
    return table[level - 1]


def ability_codes(row: list[Any] | None) -> list[str]:
    if not row:
        return []
    return [entry[0] if isinstance(entry, list) else entry for entry in row]


def bloodline_companions(sub_type: str, other_workers: list[tuple[Any, str | None]], details_by_id: dict[int, dict[str, Any]]) -> list[str]:
    """Names of the OTHER workers on the same plot (excluding the BLOODLINE holder
    itself) whose card `sub_type` matches -- the set that makes a "Toil and Kin"
    entry active. `other_workers` is `[(card_detail_id, name), ...]`. A companion
    needs no land ability of its own, just the matching `sub_type`
    (docs/land-worker-abilities.md §3)."""
    out = []
    for card_detail_id, name in other_workers:
        detail = details_by_id.get(card_detail_id) if card_detail_id is not None else None
        if (detail or {}).get("sub_type") == sub_type:
            out.append(name)
    return out


def bloodline_plot_pp_impact(rate: float | None, construction_pp_values: list[float | None]) -> float | None:
    """Exact plot-wide harvest-PP value of ONE BLOODLINE entry at its own `rate` --
    the same linear broadcast term the harvest formula (`praetoria.land_reallocate._harvest`:
    ``base * (1 + terrain) * (mult + abilities_boost + bloodline_boost)``) applies to
    every worker's `total_construction_pp` (base_pp_after_cap x (1 + terrain_boost)),
    summed across every worker on the plot. Used for BOTH badge states -- PAIRED (the
    amount this bloodline is currently earning) and SOLO (the amount it would earn if
    a companion were staked) -- because the broadcast is additive: each active
    bloodline contributes `rate * construction_pp_i` to every worker `i` regardless of
    whether it happens to be active right now. PP math stays server-authoritative
    (the internal rules, ) -- the frontend never derives this figure itself. Returns
    None -- rendered as an explicit gap, never a guessed number -- if `rate` or any
    worker's construction PP is unavailable, per the internal rules's rule against point
    estimates for unmodeled values."""
    if rate is None or not construction_pp_values or any(v is None for v in construction_pp_values):
        return None
    return rate * sum(construction_pp_values)


def annotate_land_abilities(
    row: list[Any] | None, other_workers: list[tuple[Any, str | None]], details_by_id: dict[int, dict[str, Any]],
    plot_construction_pp: list[float | None] | None = None,
) -> list[Any] | None:
    """`land_ability_row`'s output, with every BLOODLINE entry's companion status
    attached as a 4th element -- `{"active": bool, "companions": [name, ...], "pp_impact": float|None}`.
    BLOODLINE requires at least one OTHER worker on the plot sharing the ability's
    sub_type; a solo holder contributes zero to the plot
    (`library/land-worker-abilities.md` §3). Every other entry passes through
    unchanged. Board-polish batch 16 (2026-08-21) -- Toil & Kin needs a
    bloodline check, whether one of the paired cards actually carries the
    required bloodline -- rendered in the Abilities column rather than a new one.

    RULING: `active: False` IS a warning -- a solo bloodline holder is a clear
    underuse of potential, regardless of
    whether the holder is a low-`base_pp` VoV/Land card kept for other abilities
    (skin drops, glint bombs) or a plain farming placement. Callers surface it as
    a missing-companion warning naming the specific Bloodline (never the bare
    `sub_type` field name in user-facing copy); still no recommendation/optimiser
    logic proposes adding a companion unsolicited -- that stays opt-in discovery,
    per the internal rules's bloodline-pair-discovery rule.

    `pp_impact` (added for the PAIRED/SOLO tooltip's exact-bonus requirement,
    2026-08-26) is `bloodline_plot_pp_impact()` computed against `plot_construction_pp`
    -- the plot's full worker roster's construction-PP values, passed in by the
    caller once per plot (`ceres.board._plot_row`) since this function only sees one
    worker's own ability row at a time. `None` when the caller omits it."""
    if not row:
        return row
    out = []
    for entry in row:
        code = entry[0] if isinstance(entry, list) else entry
        if code == "BLOODLINE" and isinstance(entry, list) and len(entry) > 2:
            sub_type = entry[2]
            companions = bloodline_companions(sub_type, other_workers, details_by_id)
            rate = entry[1] if isinstance(entry[1], (int, float)) else None
            pp_impact = bloodline_plot_pp_impact(rate, plot_construction_pp or [])
            out.append(list(entry[:3]) + [{"active": bool(companions), "companions": companions, "pp_impact": pp_impact}])
        else:
            out.append(entry)
    return out


def mark_bloodline_applied(rows: list[list[Any] | None], plot_construction_pp: list[float | None] | None = None) -> None:
    """ / : Toil and Kin does not stack -- of all ACTIVE BLOODLINE entries on
    one plot only the single largest applies. Mutates each annotated BLOODLINE status dict
    in `rows` (one annotated land_abilities row per worker on the plot) in place:
    `applied` (this entry's bonus is the one in effect -- every entry of the winning
    sub_type, since the same bloodline is one instance), `overridden` (the count of active
    entries it beats, on the applied ones), and for inactive entries `pp_impact` becomes
    what a companion would ADD now (max(0, rate - applied rate) x plot PP), with
    `not_stacking` when that is zero."""
    active = []
    statuses = []
    for row in rows:
        for entry in row or []:
            if isinstance(entry, list) and len(entry) > 3 and entry[0] == "BLOODLINE" and isinstance(entry[3], dict):
                statuses.append(entry)
                if entry[3].get("active") and isinstance(entry[1], (int, float)):
                    active.append(entry)
    best = max(active, key=lambda e: e[1], default=None)
    if best is None:
        return
    best_rate, best_sub = best[1], best[2]
    overridden = sum(1 for e in active if e[2] != best_sub)
    for entry in statuses:
        status = entry[3]
        if status.get("active"):
            status["applied"] = entry[2] == best_sub
            if status["applied"]:
                status["overridden"] = overridden
        elif isinstance(entry[1], (int, float)):
            gain = max(0.0, entry[1] - best_rate)
            status["not_stacking"] = gain <= 0
            status["gain_rate"] = gain
            status["pp_impact"] = bloodline_plot_pp_impact(gain, plot_construction_pp or [])


def sole_holder_for(codes: list[str], other_workers_codes: list[list[str]]) -> list[str]:
    """Ability codes that would leave the plot if this worker moved -- this is
    what makes 'evicting this card silently costs every co-occupant' visible before
    anyone contemplates a move (the internal rules Land/Verico hard rule)."""
    out = []
    for code in codes:
        if not any(code in other_codes for other_codes in other_workers_codes):
            out.append(code)
    return out


def plot_worker_abilities(workers: list[dict[str, Any]], details_by_id: dict[int, dict[str, Any]]) -> list[list[str]]:
    """One code-list per worker in `workers`, same order -- used to compute
    `sole_holder_for` for every worker on a plot in one pass."""
    return [
        ability_codes(land_ability_row(w.get("card_detail_id"), w.get("level"), details_by_id))
        for w in workers
    ]


def dec_for_plot(staking_facts: dict[str, Any], worker_dec: list[float]) -> dict[str, Any]:
    """PLOT-level DEC metric only -- never a card-level ranking (plan §6.4 rule 1/2).
    `dec.committed` prefers `total_dec_staked` when it is a positive number, otherwise
    falls back to the sum of the plot's workers' own `dec_stake_needed`; `dec.source`
    names which was used so the two denominators are never silently collapsed."""
    total_staked = _num(staking_facts.get("total_dec_staked"))
    if total_staked is not None and total_staked > 0:
        committed, source = total_staked, "total_dec_staked"
    else:
        committed, source = (sum(worker_dec) if worker_dec else 0.0), "worker_sum"
    needed = _num(staking_facts.get("total_dec_stake_needed"))
    in_use = _num(staking_facts.get("total_dec_stake_in_use"))
    shortfall = None
    if needed is not None:
        base = in_use if in_use is not None else committed
        shortfall = max(0.0, needed - (base or 0.0))
    return {
        "committed": committed,
        "source": source,
        "needed": needed,
        "in_use": in_use,
        "shortfall": shortfall,
        "needed_discount": _num(staking_facts.get("dec_stake_needed_discount")),
    }


def plot_dec_efficiency(
    harvest_pp: float | None, needed: float | None, needed_discount: float | None = None,
    runi_staked: bool = False,
) -> tuple[float | None, str | None]:
    """PP-per-DEC-required, denominated against `dec.needed` (`total_dec_stake_needed`
    -- the exact sum of per-card `dec_stake_needed`), never against `dec.committed`
    (`total_dec_staked`), which is not reliably per-plot: only 3 distinct values were
    observed across 130 live plots (docs/ceres.md's DEC data-quality caveat).

    Returns `(value, reason)` -- `reason` is set whenever `value` is None, so a caller
    can render *why* rather than a bare dash:
    - `runi_staked`: Runi zeroes the plot's ENTIRE DEC requirement independently of any
      Dark Discount (library/land-worker-abilities.md §8, primary-sourced) -- the ratio
      is genuinely meaningless on a Runi-staked plot, so it stays null-with-reason.
      **This, not Dark Discount, is what the three `needed == 0` live plots were** --
      the original DD-unsettled flag (below) mis-attributed them; re-checked 2026-08-21,
      all three (`41-2-27`, `41-9-56`, `65-9-83`) are Runi-staked.
    - Dark Discount is now SETTLED (library/land-worker-abilities.md §9, verified at
      individual-worker granularity against every non-Runi discount plot, 0 exceptions):
      it is multiplicative and per-card/per-level, and `total_dec_stake_needed` already
      reflects it exactly (`dec_stake_needed = land_dec_stake_needed x (1 + discount)`).
      A non-Runi plot with a nonzero `needed_discount` is therefore priced NORMALLY --
      `needed_discount` no longer withholds a price on its own.
    - zero/missing `needed` on a non-Runi plot: null-with-reason, never infinity, never
      a dropped row.
    - missing `harvest_pp`: can't compute a ratio at all.
    """
    if runi_staked:
        return None, "DEC requirement is zero (Runi-staked plot; Runi zeroes the plot's DEC requirement)"
    if not needed:
        return None, "no DEC requirement recorded for this plot"
    if harvest_pp is None:
        return None, "harvest PP unknown"
    return harvest_pp / needed, None


def rental_direction(facts: dict[str, Any], player: str | None) -> tuple[str | None, str | None, str | None]:
    """Return the rental direction, an unknown-direction reason, and expiry."""
    rental_type = facts.get("rental_type")
    if rental_type in (None, ""):
        return None, None, None
    expiry = next((facts.get(key) for key in (
        "expiration_date", "rental_expiration", "rental_end_date", "rental_expires_at",
    ) if facts.get(key)), None)
    explicit = facts.get("rental_direction") or facts.get("rental_status")
    if isinstance(explicit, str):
        value = explicit.lower().replace("-", "_").replace(" ", "_")
        if value in {"in", "rented_in", "incoming", "received", "to_player"}:
            return "in", None, expiry
        if value in {"out", "rented_out", "outgoing", "owned", "from_player"}:
            return "out", None, expiry
    recipient = facts.get("rented_to") or facts.get("rental_recipient") or facts.get("renter")
    owner = facts.get("rental_owner") or facts.get("owner") or facts.get("player")
    delegated_to = facts.get("delegated_to")
    if player and recipient == player and owner not in (None, "", player):
        return "in", None, expiry
    if player and delegated_to == player and owner != player:
        return "in", None, expiry
    if player and owner == player:
        return "out", None, expiry
    return "unknown", f"rented ({rental_type})", expiry


def classify_idle_availability(
    facts: dict[str, Any], now: datetime | None = None, player: str | None = None,
) -> tuple[bool, str | None]:
    """Mirrors the exclusion checks `land_reallocate.gather_idle_cards` applies (market
    listing, delegation, rental, future unlock) but -- unlike that Phase-2 planner
    helper, which silently skips blocked cards -- returns every card with an explicit
    `available` flag and reason, per the the internal rules hard rule that both idle buckets are
    always shown, present-but-flagged rather than silently dropped."""
    now = now or datetime.now(timezone.utc)
    # a card rented IN keeps the rental's market_id; it is the renter's to use, not a market listing.
    direction, reason, _expiry = rental_direction(facts, player)
    if direction == "in":
        return True, None
    if facts.get("market_id"):
        return False, "listed on market"
    if facts.get("rental_type"):
        if direction == "out":
            return False, f"rented ({facts['rental_type']})"
        return False, reason or f"rented ({facts['rental_type']})"
    delegated_to = facts.get("delegated_to")
    if delegated_to and delegated_to != player:
        return False, f"delegated to {delegated_to}"
    unlock = facts.get("unlock_date")
    if unlock:
        try:
            unlock_dt = datetime.fromisoformat(str(unlock).replace("Z", "+00:00")).astimezone(timezone.utc)
            if unlock_dt > now:
                return False, f"locked until {unlock}"
        except ValueError:
            pass
    return True, None
