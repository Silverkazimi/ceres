"""Slot assignment for land worker-card stakes (`fides_land/slots.py`, plan §3.4).

Occupied slots per deed come from Praetoria's own `report["assignments"]`, filtered to
`asset_kind == "worker_card"` -- Runi occupies its own reserved slot (observed `slot: 0` in
live data, Phase 0 finding) and is excluded from worker-slot bookkeeping entirely. For each
destination deed this computes `occupied_after_wave1 = observed_occupied - {slots this
batch's own unstakes vacate there}`, then assigns each incoming stake the lowest free
integer in `1..max_workers_allowed`.

Phase 0 (`work/fides-phase0-findings.md`) found live `slot` values spanning `1..5` roughly
evenly, not uniformly `"1"` -- this lowest-free-integer algorithm is therefore the primary,
load-bearing behavior here, not a fallback for degenerate data.
"""

from __future__ import annotations

from typing import Any


class SlotAssignmentError(Exception):
    pass


def observed_worker_slots(assignments: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """`deed_uid -> {asset_ref: slot}` for every currently-staked worker_card. Runi
    (`asset_kind == "runi_worker_card"`) is deliberately excluded."""
    out: dict[str, dict[str, int]] = {}
    for row in assignments:
        if row.get("asset_kind") != "worker_card":
            continue
        deed = row.get("deed_uid")
        slot = row.get("slot")
        ref = row.get("asset_ref")
        if deed is None or slot is None or ref is None:
            continue
        out.setdefault(deed, {})[ref] = int(slot)
    return out


def assign_slots(
    stake_targets: dict[str, list[dict[str, Any]]],
    unstake_targets: dict[str, list[dict[str, Any]]],
    assignments: list[dict[str, Any]],
    capacity_by_deed: dict[str, int],
) -> dict[str, list[dict[str, Any]]]:
    """Mutates and returns `stake_targets`, adding an integer `slot` to every card entry.
    Each stake entry must already carry `asset_ref` (used only for this lookup, dropped
    before the chain payload is built in `ops.py`). Assignment order within a deed is by
    `asset_ref` for determinism. Raises `SlotAssignmentError` if a destination deed runs out
    of free slots or has no known capacity."""
    observed = observed_worker_slots(assignments)
    for deed, cards in stake_targets.items():
        deed_observed = dict(observed.get(deed, {}))
        for freed in unstake_targets.get(deed, []):
            deed_observed.pop(freed.get("asset_ref"), None)
        taken = set(deed_observed.values())
        capacity = capacity_by_deed.get(deed)
        if capacity is None:
            raise SlotAssignmentError(f"no known worker capacity for deed {deed!r}")
        next_slot = 1
        for card in sorted(cards, key=lambda c: str(c.get("asset_ref"))):
            while next_slot in taken:
                next_slot += 1
            if next_slot > capacity:
                raise SlotAssignmentError(
                    f"no free worker slot on deed {deed!r} (capacity {capacity}); "
                    "this batch would over-fill the plot even after its own unstakes"
                )
            card["slot"] = next_slot
            taken.add(next_slot)
            next_slot += 1
    return stake_targets
