"""Structure-derived worker capacity shared by board, candidates, and preview."""

from __future__ import annotations

from collections import defaultdict
import math
from typing import Any


MAX_STRUCTURE_WORKER_CAPACITY = 50
# every worksite has five regular worker slots, per the game's comparison table
# https://splinterlands.gitbook.io/phase-2-the-secret-of-praetoria/structures-and-resources/buildings-vs-worksites
WORKSITE_WORKER_CAPACITY = 5
RUNI_ONLY_POWERED_SLOTS = 4
NON_WORKSITE_TYPES = frozenset({"keep", "castle"})


def _capacity_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or not numeric.is_integer():
        return None
    capacity = int(numeric)
    return capacity if 0 <= capacity <= MAX_STRUCTURE_WORKER_CAPACITY else None


def _structure_identity(plot: dict[str, Any]) -> tuple[str | None, str | None]:
    worksite_facts = plot.get("worksite_facts") or {}
    facts = plot.get("facts") or {}
    project_type = str(worksite_facts.get("project_type") or "").strip().lower()
    worksite_type = str(facts.get("worksite_type") or worksite_facts.get("worksite_type") or "").strip().lower()
    if project_type:
        family = "worksite" if project_type.startswith("worksite_") else project_type.split("_", 1)[0]
        return f"project:{project_type}", family
    if worksite_type:
        if worksite_type in NON_WORKSITE_TYPES:
            return f"project:{worksite_type}_operation", worksite_type
        return f"worksite:{worksite_type}", "worksite"
    return None, None


def structure_capacity_index(report: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    exact_values: dict[str, set[int]] = defaultdict(set)
    family_values: dict[str, set[int]] = defaultdict(set)
    for plot in report.get("plots", []):
        staking = plot.get("staking_facts") or {}
        # non-worksite structures are learned only from full-power plots; a
        # Runi-only live value can understate how many regular slots the structure has.
        if not (staking.get("is_power_core_staked") is True or staking.get("is_energized") is True):
            continue
        capacity = _capacity_value(staking.get("max_workers_allowed"))
        if capacity is None or capacity == 0:
            continue
        identity, family = _structure_identity(plot)
        # replaces peer learning for every worksite; other structure families
        # retain the existing evidence-driven behavior for Keep/Castle and future buildings.
        if family == "worksite":
            continue
        if identity:
            exact_values[identity].add(capacity)
        if family:
            family_values[family].add(capacity)
    exact = {key: next(iter(values)) for key, values in exact_values.items() if len(values) == 1}
    families = {key: next(iter(values)) for key, values in family_values.items() if len(values) == 1}
    return exact, families


def potential_worker_capacity(
    plot: dict[str, Any], capacity_index: tuple[dict[str, int], dict[str, int]],
) -> int:
    """Return numbered worker slots once the current structure is powered.

    Worksites always have five regular slots. Positive live capacity remains
    authoritative for other structures. An unpowered non-worksite plot reports zero,
    so its potential is learned from powered peers with the same project identity or
    structure family. Future types are learned from snapshot evidence instead of
    guessing a level field or slot constant.
    """
    identity, family = _structure_identity(plot)
    if family == "worksite":
        return WORKSITE_WORKER_CAPACITY
    live_capacity = _capacity_value((plot.get("staking_facts") or {}).get("max_workers_allowed"))
    if live_capacity is not None and live_capacity > 0:
        return live_capacity
    exact, families = capacity_index
    if identity and identity in exact:
        return exact[identity]
    if family and family in families:
        return families[family]
    return 0


def powered_worker_capacity(
    plot: dict[str, Any], capacity_index: tuple[dict[str, int], dict[str, int]],
) -> int:
    """Return the structure's regular-worker capacity when fully powered."""
    identity, family = _structure_identity(plot)
    if family == "worksite":
        return WORKSITE_WORKER_CAPACITY
    live_capacity = _capacity_value((plot.get("staking_facts") or {}).get("max_workers_allowed"))
    if live_capacity is not None and live_capacity > 0:
        return live_capacity
    live = potential_worker_capacity(plot, capacity_index)
    exact, families = capacity_index
    peer = exact.get(identity) if identity and identity in exact else families.get(family) if family and family in families else None
    return max(live, peer or 0)


def powered_worker_slots(
    plot: dict[str, Any],
    capacity_index: tuple[dict[str, int], dict[str, int]],
    *,
    flags: dict[str, Any] | None = None,
) -> int:
    """Return regular worker slots powered in the supplied live or projected state."""
    state = flags if flags is not None else (plot.get("staking_facts") or {})
    _identity, family = _structure_identity(plot)
    if family == "worksite":
        if state.get("is_power_core_staked") is True or state.get("is_energized") is True:
            return WORKSITE_WORKER_CAPACITY
        if state.get("is_runi_staked") is True:
            return RUNI_ONLY_POWERED_SLOTS
        return 0

    live = _capacity_value(state.get("max_workers_allowed"))
    if flags is None or flags is plot.get("staking_facts"):
        return live or 0
    if state.get("is_power_core_staked") is True or state.get("is_energized") is True:
        return powered_worker_capacity(plot, capacity_index)
    if state.get("is_runi_staked") is True:
        return live or max(0, powered_worker_capacity(plot, capacity_index) - 1)
    return potential_worker_capacity(plot, capacity_index)
