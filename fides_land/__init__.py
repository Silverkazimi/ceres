"""fides_land -- the Land worker-reallocation adapter for Fides (consumer #1 of `fides`).

Consumes `praetoria.land_reallocate`'s plan JSON, imports from Praetoria only read-only
helpers, and builds `sm_stake_change` custom_json ops, Posting-key-only, gated through the
land-specific ~3-day worker-unstake cooldown's three-wave workflow. All Splinterlands-
specific op construction, wave/cooldown logic, slot assignment, and domain re-verification
live here -- `fides` core (the package this depends on) has zero knowledge of any of it.
See docs/plans/fides-batch-signing.md and docs/fides.md.
"""

from __future__ import annotations
