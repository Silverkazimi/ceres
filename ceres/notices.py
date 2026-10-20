"""Backend-owned notice vocabulary for Ceres responses and renderers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_BADGES = {
    "idle": ("Idle", "problem", "fact", "neutral", "neutral", 0, "No workers staked — produces nothing"),
    "unpowered": ("Unpowered", "problem", "warning", "caution", "critical", 0, "This plot's worker slots have no power, so staked workers earn zero. A Power Core or Energized worker powers all five slots; a Runi powers four."),
    "unpowered_worker": ("Unpowered worker", "problem", "warning", "caution", "critical", 1, "A worker in this unpowered slot earns zero. Recovery costs a 72-hour cooldown. A Power Core or an Energized worker would power the slot instead."),
    "redundant_power_core": ("Redundant Power Core", "problem", "warning", "caution", "warning", 2, "This plot already has an Energized worker, so its Power Core is doing nothing here. It could power a different plot instead."),
    "dark_discount_wasted": ("Wasted Dark Discount", "problem", "warning", "caution", "warning", 0, "Runi already removes this plot's DEC staking requirement, so Dark Discount does nothing here. It would reduce DEC elsewhere."),
    # Split from the single "incomplete" badge (2026-08-25 worksite ruling): a
    # worksite cannot simply be absent -- if there is none, it was never cleared
    # and clearing may take a very long time, whereas changing an existing
    # worksite always takes 24 hours. The two diagnostics have very different waits, so a user
    # could not tell a 24-hour change from an open-ended one under one "INC" chip.
    "no_worksite": ("Not cleared", "problem", "advisory", "caution", "warning", 1, "No worksite has ever been built here. Clearing time depends on the plot's rarity and Base PP, and can take a long time."),
    "worksite_incomplete": ("Worksite change", "problem", "advisory", "caution", "warning", 5, "A worksite here is mid-build or mid-change. A worksite change always takes 24 hours."),
    "unstable_totem": ("Unstable totem", "property", "fact", "info", "neutral", 2, "Not active yet: once Phase 2 goes live, activating this totem doubles this plot's production for 30 days. It has no effect on today's harvest."),
    "building_in_box": ("Building in a Box", "property", "fact", "info", "neutral", 1, "Not active yet: on Phase 2 release, this becomes a token that instantly builds a structure on any owned plot at no construction cost. It has no effect on today's harvest."),
    "assets_not_loaded": ("Partly loaded", "property", "advisory", "info", "neutral", 3, "Partly loaded: this plot's numbers, workers, abilities, totem and title are live from the bulk read. Slot order, each card's own PP split and the exact staked items load when you open the plot."),
}

_EXCLUSIONS = {
    "market_listed": "Candidate is market-listed and unavailable for this account.",
    "delegated": "Candidate is delegated and unavailable for this account.",
    "rented": "Candidate is rented or otherwise unavailable for this account.",
    "unlock_locked": "Candidate is lock-held until its unlock time.",
    "cooling": "Candidate is in the unstake cooldown.",
    "survival_locked": "Candidate died in Survival mode and cannot be staked until it revives.",
    "unknown_direction": "Candidate rental direction is unknown; Ceres fails closed.",
    "missing_base": "Candidate has no usable base PP.",
    "capacity": "Candidate does not fit the remaining worker capacity.",
    "base_cap": "Candidate was excluded by the plot base-PP cap model.",
    "same_plot": "Candidate is already staked on this plot.",
    "negative_net_gain": "Candidate has a negative full-plot net gain; the full ranked list remains available for inspection.",
    "terrain_penalty": "Select the penalised Splinter icons to show these cards.",
}


NOTICE_REGISTRY: dict[str, dict[str, Any]] = {
    code: {
        "code": code, "kind": semantic_kind, "severity": severity, "blocking": "none",
        "message": tooltip, "surfaces": ["board", "tile"], "required_context": ["deed_uid"],
        "aria": "polite",
    }
    for code, (_label, _legacy_kind, semantic_kind, severity, _group, _group_order, tooltip) in _BADGES.items()
}
for code, message in _EXCLUSIONS.items():
    NOTICE_REGISTRY[code] = {
        "code": code, "kind": "advisory" if code == "negative_net_gain" else "refusal",
        "severity": "caution" if code == "negative_net_gain" else "danger", "blocking": "none",
        "message": message, "surfaces": ["picker", "api"], "required_context": ["count"], "aria": "polite",
    }

NOTICE_REGISTRY.update({
    "sole_power_source_removed": {"code": "sole_power_source_removed", "kind": "hazard", "severity": "danger", "blocking": "confirm", "message": "This removes the plot's only power source. All five worker slots remain visible and staked workers remain detectable, but every worker is unpowered, inactive, and produces nothing.", "surfaces": ["slot_editor", "checkout", "hazard_dialog"], "required_context": ["deed_uid"], "aria": "assertive"},
    "energized_source_removed": {"code": "energized_source_removed", "kind": "hazard", "severity": "danger", "blocking": "confirm", "message": "This removes the plot's Energized power source. Worker slots stay in place; any slot no longer powered becomes inactive and produces 0 PP until you stake a Power Core or another Energized worker.", "surfaces": ["slot_editor", "checkout", "hazard_dialog"], "required_context": ["deed_uid"], "aria": "assertive"},
    "workers_staged_without_power": {"code": "workers_staged_without_power", "kind": "warning", "severity": "caution", "blocking": "none", "message": "This plot has workers or PP but no worker-slot power. A Power Core or Energized worker powers all five slots; a Runi powers four.", "surfaces": ["slot_editor", "checkout"], "required_context": ["deed_uid"], "aria": "polite"},
    "worker_unpowered_inactive": {"code": "worker_unpowered_inactive", "kind": "warning", "severity": "caution", "blocking": "none", "message": "{worker} is unpowered and inactive; it produces nothing. The worker remains staked and becomes active when the slot is powered.", "surfaces": ["preview", "checkout", "api"], "required_context": ["deed_uid", "worker", "slot", "powered_slots"], "aria": "polite"},
    "cooldown_refusal": {"code": "cooldown_refusal", "kind": "refusal", "severity": "danger", "blocking": "stage", "message": "{reason}", "surfaces": ["picker", "preview", "checkout"], "required_context": ["reason"], "aria": "assertive"},
    "survival_refusal": {"code": "survival_refusal", "kind": "refusal", "severity": "danger", "blocking": "stage", "message": "{reason}", "surfaces": ["picker", "preview", "checkout"], "required_context": ["reason"], "aria": "assertive"},
    "worker_count_unknown": {"code": "worker_count_unknown", "kind": "refusal", "severity": "danger", "blocking": "stage", "message": "Ceres cannot confirm this plot's worker count. Expand this plot to load it, then try again.", "surfaces": ["slot_editor", "preview", "checkout", "api"], "required_context": ["deed_uid"], "aria": "assertive"},
    "worker_count_mismatch": {"code": "worker_count_mismatch", "kind": "refusal", "severity": "danger", "blocking": "stage", "message": "Ceres received an incomplete worker roster. Expand this plot to load it, then try again.", "surfaces": ["slot_editor", "preview", "checkout", "api"], "required_context": ["deed_uid"], "aria": "assertive"},
    "worker_slot_unknown": {"code": "worker_slot_unknown", "kind": "refusal", "severity": "danger", "blocking": "stage", "message": "Ceres still does not know which worker slots are occupied. Expand this plot to load it, then try again.", "surfaces": ["slot_editor", "preview", "checkout", "api"], "required_context": ["deed_uid"], "aria": "assertive"},
    "worker_slot_taken": {"code": "worker_slot_taken", "kind": "refusal", "severity": "danger", "blocking": "stage", "message": "The worker slot you chose is already occupied. Choose a different slot.", "surfaces": ["slot_editor", "preview", "checkout", "api"], "required_context": ["deed_uid"], "aria": "assertive"},
    # Pre-broadcast checkout gate (distinct from cooldown_refusal above, which only ever
    # fires AFTER a round trip to /api/op-preview). This one renders client-side, straight
    # off the `cooldown` object candidates.py already attaches to a staged candidate
    # never a client-side re-derivation of readiness -- so the batch is flagged and
    # Prepare Transaction is disabled before a doomed preview is ever requested.
    # `{reason}` (built client-side by cooldownReasonFragment()) sometimes already ends in
    # its own parenthetical (e.g. "cooling until <time> (in 1d 16h)") -- a dash separator
    # here avoids nesting a second pair of parens around it.
    "checkout_cooldown_blocked": {"code": "checkout_cooldown_blocked", "kind": "refusal", "severity": "danger", "blocking": "stage", "message": "{name} is not ready to stake yet — {reason}. Prepare Transaction stays disabled until it clears.", "surfaces": ["checkout"], "required_context": ["name", "reason"], "aria": "assertive"},
    "removal_estimate_provisional": {"code": "removal_estimate_provisional", "kind": "advisory", "severity": "caution", "blocking": "none", "message": "This plot has both incoming and outgoing staged moves. The removal estimate is provisional; Prepare Transaction will re-price the complete batch before signing.", "surfaces": ["checkout"], "required_context": ["deed_uid"], "aria": "polite"},
    "bloodline_pair_deactivated": {"code": "bloodline_pair_deactivated", "kind": "warning", "severity": "caution", "blocking": "none", "message": "Removing this card deactivates {bloodline} ({sub_type}) for every worker on this plot ({pp_delta} PP).", "surfaces": ["slot_editor", "checkout", "api"], "required_context": ["bloodline", "sub_type", "pp_delta", "deed_uid"], "aria": "polite"},
    "plot_ability_loss": {"code": "plot_ability_loss", "kind": "warning", "severity": "caution", "blocking": "none", "message": "Plot-wide ability value changes by {pp_delta} PP.", "surfaces": ["slot_editor", "checkout", "api"], "required_context": ["pp_delta", "deed_uid"], "aria": "polite"},
    "power_source_removed_warning": {"code": "power_source_removed_warning", "kind": "warning", "severity": "caution", "blocking": "none", "message": "This removal leaves the plot without a proven power source; staging remains allowed.", "surfaces": ["slot_editor", "checkout", "api"], "required_context": ["deed_uid"], "aria": "polite"},
    "rental_expiry_warning": {"code": "rental_expiry_warning", "kind": "warning", "severity": "caution", "blocking": "none", "message": "Rental expiry force-harvests the plot, immediately unstakes this card, and starts the usual 72-hour cooldown.", "surfaces": ["picker", "api"], "required_context": ["asset_ref", "expires_at"], "aria": "polite"},
    "capture_failed": {"code": "capture_failed", "kind": "refusal", "severity": "danger", "blocking": "repeat", "message": "The last board read failed.", "surfaces": ["header", "toast"], "required_context": [], "aria": "assertive"},
    # (2026-09-14): per-plot worker-detail coverage, never "the table is incomplete".
    "assets_incomplete": {"code": "assets_incomplete", "kind": "advisory", "severity": "info", "blocking": "none", "message": "Every plot is on the board. Worker details for {failed} plot(s) could not be read{dropped_clause}; a plot loads its workers when you open it.", "surfaces": ["header"], "required_context": ["failed", "dropped_clause"], "aria": "assertive"},
    "board_warning": {"code": "board_warning", "kind": "advisory", "severity": "caution", "blocking": "none", "message": "{reason}", "surfaces": ["board", "header"], "required_context": ["reason"], "aria": "polite"},
    "verification_waiting": {"code": "verification_waiting", "kind": "verification", "severity": "info", "blocking": "repeat", "message": "Waiting for the blockchain…", "surfaces": ["verification"], "required_context": [], "aria": "polite", "phase": "pending"},
    "verification_chain_check": {"code": "verification_chain_check", "kind": "verification", "severity": "info", "blocking": "repeat", "message": "On chain — checking game state…", "surfaces": ["verification"], "required_context": [], "aria": "polite", "phase": "pending"},
    "verification_confirmed": {"code": "verification_confirmed", "kind": "verification", "severity": "success", "blocking": "none", "message": "Confirmed in game", "surfaces": ["verification", "toast"], "required_context": [], "aria": "polite", "phase": "success"},
    "verification_game_refused": {"code": "verification_game_refused", "kind": "verification", "severity": "danger", "blocking": "repeat", "message": "The game refused this operation: {reason}", "surfaces": ["verification", "checkout", "worksite_modal", "combine"], "required_context": ["reason"], "aria": "assertive", "phase": "failure"},
    "verification_game_awaiting_verdict": {"code": "verification_game_awaiting_verdict", "kind": "verification", "severity": "info", "blocking": "repeat", "message": "On chain in block {block_num}. The game has not answered yet — a verdict is normally readable about half a minute after the block.", "surfaces": ["verification", "checkout", "worksite_modal", "combine"], "required_context": ["block_num"], "aria": "polite", "phase": "pending"},
    "verification_in_flight": {"code": "verification_in_flight", "kind": "advisory", "severity": "info", "blocking": "none", "message": "Signed — waiting for the game to confirm.", "surfaces": ["checkout", "slot_editor"], "required_context": [], "aria": "polite"},
    "verification_in_flight_refusal": {"code": "verification_in_flight_refusal", "kind": "refusal", "severity": "caution", "blocking": "sign", "message": "This change is already signed and waiting for the game to confirm.", "surfaces": ["checkout"], "required_context": [], "aria": "assertive"},
    "verification_in_flight_restored": {"code": "verification_in_flight_restored", "kind": "advisory", "severity": "info", "blocking": "none", "message": "A change signed before this page was reloaded is still waiting for the game to confirm. It stays marked until the game answers.", "surfaces": ["checkout", "board"], "required_context": [], "aria": "polite"},
    "in_flight_record_expired": {"code": "in_flight_record_expired", "kind": "advisory", "severity": "caution", "blocking": "none", "message": "A signed change waited a full day without an answer from the game, so its marker has been cleared. Check the plot in the game before signing it again.", "surfaces": ["checkout", "board"], "required_context": [], "aria": "polite"},
    "in_flight_store_unavailable": {"code": "in_flight_store_unavailable", "kind": "warning", "severity": "caution", "blocking": "none", "message": "This browser could not save the signed-change marker. Do not reload this page until the game confirms the change.", "surfaces": ["checkout", "board"], "required_context": [], "aria": "assertive"},
    # phase 2: the maintainer's kept stage plans.
    "stage_plans_restored": {"code": "stage_plans_restored", "kind": "advisory", "severity": "info", "blocking": "none", "message": "Planned changes restored from your last visit: {count}.", "surfaces": ["toast"], "required_context": ["count"], "aria": "polite"},
    "stage_plans_unchecked": {"code": "stage_plans_unchecked", "kind": "warning", "severity": "caution", "blocking": "none", "message": "{count} planned changes are kept but not shown: they could not be checked against the live board. They are checked again on the next load.", "surfaces": ["toast"], "required_context": ["count"], "aria": "polite"},
    "stage_plan_dropped": {"code": "stage_plan_dropped", "kind": "advisory", "severity": "caution", "blocking": "none", "message": "{name} left your plan: {reason}.", "surfaces": ["toast"], "required_context": ["name", "reason"], "aria": "polite"},
    "stage_plans_changed_elsewhere": {"code": "stage_plans_changed_elsewhere", "kind": "warning", "severity": "caution", "blocking": "none", "message": "Your planned changes were changed in another tab. Reload to see them.", "surfaces": ["toast"], "required_context": [], "aria": "polite"},
    "stage_plans_store_unavailable": {"code": "stage_plans_store_unavailable", "kind": "warning", "severity": "caution", "blocking": "none", "message": "This browser could not save your planned changes; they will be lost on reload.", "surfaces": ["toast"], "required_context": [], "aria": "assertive"},
    "stage_plans_corrupt": {"code": "stage_plans_corrupt", "kind": "warning", "severity": "caution", "blocking": "none", "message": "Your saved planned changes could not be read. A copy was kept and the plan starts empty.", "surfaces": ["toast"], "required_context": [], "aria": "polite"},
    "verification_mismatch": {"code": "verification_mismatch", "kind": "verification", "severity": "danger", "blocking": "repeat", "message": "Game state does not match", "surfaces": ["verification"], "required_context": [], "aria": "assertive", "phase": "failure"},
    "verification_not_applied": {"code": "verification_not_applied", "kind": "verification", "severity": "danger", "blocking": "repeat", "message": "The transaction landed on chain, but the game did not apply this change. Nothing was changed in game. Prepare and sign again if you still want it.", "surfaces": ["verification", "checkout", "board"], "required_context": [], "aria": "assertive", "phase": "failure"},
    "verification_not_found": {"code": "verification_not_found", "kind": "verification", "severity": "caution", "blocking": "repeat", "message": "Not found on chain", "surfaces": ["verification"], "required_context": [], "aria": "assertive", "phase": "indeterminate"},
    # Distinct from verification_mismatch -- a stale read moments after broadcast is
    # expected propagation lag, not a failure. Never asserted from a mismatch; see
    # `fides_land/verify.py`'s `_check_worksite_change` and `routes_op_preview.py`'s
    # `op_verify` invariant comment for the full state split this notice is half of.
    # defect 3 -- this copy used to say "Checking again…", which nothing on this
    # path ever did: verifyBroadcast() set the notice and returned, and the checkout
    # surface had no retry affordance at all. The message now states only what is
    # actually true; the real re-check action is the `action` a caller attaches
    # (`verification_notice()` below always adds `retry_verification` here).
    "verification_pending": {"code": "verification_pending", "kind": "verification", "severity": "info", "blocking": "repeat", "message": "Not confirmed yet — the game may still be catching up.", "surfaces": ["verification"], "required_context": [], "aria": "polite", "phase": "pending"},
    # defect 2 -- distinct from verification_pending: this fires once
    # `pending_metadata.requires_escalation` is true (fides_land/verify.py's
    # PENDING_ESCALATION_ATTEMPTS manual re-checks have been exhausted). Pending alone
    # never resolves to confirmation on its own and previously had no bound at all, so a
    # genuinely failed operation could read "may still be propagating" forever. This is
    # not a failure verdict -- only domain-mismatch is that -- it is "did not confirm in
    # the expected window, look yourself", with the same re-check action still offered.
    "verification_pending_escalated": {"code": "verification_pending_escalated", "kind": "verification", "severity": "caution", "blocking": "repeat", "message": "Still not confirmed after several checks — this did not resolve in the expected window. It may still be propagating, or it may need attention: check the game directly, or try again.", "surfaces": ["verification"], "required_context": [], "aria": "assertive", "phase": "indeterminate"},
    # No usable evidence could be obtained at all -- genuinely unknown, never success
    # never mismatch. Distinct from verification_domain_check_failed (an unexpected
    # exception while checking) even though both render "caution" -- this one is a
    # controlled, expected outcome of a fetch that returned nothing to compare against.
    "verification_could_not_verify": {"code": "verification_could_not_verify", "kind": "verification", "severity": "caution", "blocking": "repeat", "message": "Could not check game state yet.", "surfaces": ["verification"], "required_context": [], "aria": "polite", "phase": "indeterminate"},
    "verification_lookup_failed": {"code": "verification_lookup_failed", "kind": "verification", "severity": "caution", "blocking": "repeat", "message": "Could not reach Hive to check", "surfaces": ["verification"], "required_context": [], "aria": "assertive", "phase": "indeterminate"},
    "verification_domain_check_failed": {"code": "verification_domain_check_failed", "kind": "verification", "severity": "caution", "blocking": "repeat", "message": "Could not check the game state", "surfaces": ["verification"], "required_context": [], "aria": "assertive", "phase": "indeterminate"},
    "verification_refresh_failed": {"code": "verification_refresh_failed", "kind": "advisory", "severity": "caution", "blocking": "none", "message": "The change is confirmed. This plot's figures could not be re-read.", "surfaces": ["verification", "board"], "required_context": [], "aria": "polite"},
    "verification_confirmed_stage_unmatched": {"code": "verification_confirmed_stage_unmatched", "kind": "advisory", "severity": "caution", "blocking": "none", "message": "The change is confirmed, but these staged changes could not be matched safely. They are still here; review them before trying again.", "surfaces": ["verification", "checkout"], "required_context": [], "aria": "polite"},
    # item 40 -- the checkout shows Base PP (stage one, construction) and Total
    # PP (stage two, harvest) side by side; both tooltip strings live here, not
    # hardcoded in app.js, per the single-source-of-truth rule for notice text.
    "base_pp_stage_explainer": {"code": "base_pp_stage_explainer", "kind": "advisory", "severity": "info", "blocking": "none", "message": "Base PP — The Production Points your cards bring to this plot before terrain and before the 100,000 cap. Total PP is what the plot actually harvests after every boost; the two are never the same number.", "surfaces": ["checkout"], "required_context": [], "aria": "polite"},
    "base_pp_delta_cap_unknown": {"code": "base_pp_delta_cap_unknown", "kind": "advisory", "severity": "info", "blocking": "none", "message": "Base PP change — Not calculated — one of your staged cards has no Base PP figure.", "surfaces": ["checkout"], "required_context": [], "aria": "polite"},
    "cap_derivation_failed": {"code": "cap_derivation_failed", "kind": "advisory", "severity": "caution", "blocking": "none", "message": "Base PP could not be derived for plot {plot}: {reason}. The current figure remains on screen.", "surfaces": ["slot_editor"], "required_context": ["plot", "reason"], "aria": "polite"},
    "cap_prediction_mismatch": {"code": "cap_prediction_mismatch", "kind": "advisory", "severity": "caution", "blocking": "none", "message": "Base PP differs from the figure for plot {plot}, slot {slot} ({card}): expected {predicted}, actual {allocated}. Use the figure shown.", "surfaces": ["slot_editor"], "required_context": ["plot", "slot", "card", "predicted", "allocated"], "aria": "polite"},
    "base_pp_change_not_reported": {"code": "base_pp_change_not_reported", "kind": "advisory", "severity": "info", "blocking": "none", "message": "Base PP change — Not calculated — this plot has a staged card with no Base PP figure.", "surfaces": ["slot_editor"], "required_context": [], "aria": "polite"},
    # Phase 1 -- worksite-change modal (read-only). §6 of the plan.
    "worksite_swap_refused_no_pp": {"code": "worksite_swap_refused_no_pp", "kind": "refusal", "severity": "danger", "blocking": "stage", "message": "{reason} {remedy}", "surfaces": ["worksite_modal"], "required_context": ["reason", "remedy", "deed_uid"], "aria": "assertive"},
    "worksite_swap_open": {"code": "worksite_swap_open", "kind": "fact", "severity": "info", "blocking": "none", "message": "Change worksite", "surfaces": ["worksite_scene"], "required_context": [], "aria": "polite"},
    "worksite_completion_ready": {"code": "worksite_completion_ready", "kind": "fact", "severity": "info", "blocking": "none", "message": "{worksite} is ready — feed workers with {grain_cost} Grain to complete the worksite and resume production.", "surfaces": ["worksite_modal", "checkout"], "required_context": ["worksite", "grain_cost"], "aria": "polite"},
    "worksite_swap_auto_harvest": {"code": "worksite_swap_auto_harvest", "kind": "warning", "severity": "caution", "blocking": "none", "message": "Changing a worksite will initiate a harvest for resources already produced. Unclaimed resources are lost if regional Grain or DEC is short.", "surfaces": ["worksite_modal", "checkout"], "required_context": [], "aria": "polite"},
    "worksite_first_clear_no_workers": {"code": "worksite_first_clear_no_workers", "kind": "warning", "severity": "caution", "blocking": "none", "message": "The build will not progress until workers are staked.", "surfaces": ["worksite_modal", "checkout"], "required_context": [], "aria": "polite"},
    "worksite_swap_sps_pool_share": {"code": "worksite_swap_sps_pool_share", "kind": "advisory", "severity": "info", "blocking": "none", "message": "SPS mining is a pool-share model, not a fixed rate: a plot earns a share of a monthly pool proportional to its share of ALL network PP mining SPS. No fixed per-plot rate can be shown.", "surfaces": ["worksite_modal"], "required_context": [], "aria": "polite"},
    # Fallback text only -- the tile itself renders a live-balance message built from
    # `BOARD.permit_balance` (`ceres/capture.py::_permit_balance`), this catalog
    # entry's `message` is used only when that balance was never fetched.
    "worksite_swap_aura_permit": {"code": "worksite_swap_aura_permit", "kind": "advisory", "severity": "info", "blocking": "none", "message": "Building or changing into an Aura Lab requires an account-bound Aura Lab Permit — confirm you hold one before changing.", "surfaces": ["worksite_modal"], "required_context": [], "aria": "polite"},
    "worksite_swap_tc_balance_unknown": {"code": "worksite_swap_tc_balance_unknown", "kind": "advisory", "severity": "info", "blocking": "none", "message": "Your Time Crystal balance is unavailable. The option stays enabled — verify you have enough before confirming.", "surfaces": ["worksite_modal"], "required_context": [], "aria": "polite"},
    # Explains the deed_status_token_boost 2x trap on the tile it actually
    # changes, rather than leaving a doubled/halved number unexplained (worksite
    # UX data-density pass). "info" not "advisory" severity distinction unneeded
    # both are informational, gated on target/current dstb differing.
    "worksite_swap_bonus_gained": {"code": "worksite_swap_bonus_gained", "kind": "advisory", "severity": "info", "blocking": "none", "message": "This plot is {status} — moving to {worksite} adds a +100% land-type bonus, already counted in the number above.", "surfaces": ["worksite_modal"], "required_context": ["status", "worksite"], "aria": "polite"},
    "worksite_swap_bonus_lost": {"code": "worksite_swap_bonus_lost", "kind": "advisory", "severity": "info", "blocking": "none", "message": "Leaving {current_worksite} gives up the +100% land-type bonus this {status} plot currently gets from it.", "surfaces": ["worksite_modal"], "required_context": ["status", "current_worksite"], "aria": "polite"},
    "combine_select": {"code": "combine_select", "kind": "fact", "severity": "info", "blocking": "none", "message": "Select this card for an explicit combine preview.", "surfaces": ["idle_cards"], "required_context": [], "aria": "polite"},
    "combine_deselect": {"code": "combine_deselect", "kind": "fact", "severity": "info", "blocking": "none", "message": "Remove this card from the combine selection.", "surfaces": ["idle_cards"], "required_context": [], "aria": "polite"},
    "combine_preview_loading": {"code": "combine_preview_loading", "kind": "verification", "severity": "info", "blocking": "repeat", "message": "Checking the selected cards before you combine them…", "surfaces": ["combine"], "required_context": [], "aria": "polite"},
    "combine_refusal": {"code": "combine_refusal", "kind": "refusal", "severity": "danger", "blocking": "sign", "message": "Combine refused by the game: {reason}. This decision used the state observed at {observed_at}.", "surfaces": ["combine"], "required_context": ["reason", "observed_at"], "aria": "assertive"},
    # Card-hero redesign (work/combine-modal-redesign): the plain-language sentence
    "combine_destroyed": {"code": "combine_destroyed", "kind": "hazard", "severity": "danger", "blocking": "confirm", "message": "Permanently DESTROYED", "surfaces": ["combine", "hazard_dialog"], "required_context": [], "aria": "assertive"},
    "combine_survivor": {"code": "combine_survivor", "kind": "fact", "severity": "info", "blocking": "none", "message": "Predicted survivor", "surfaces": ["combine", "hazard_dialog"], "required_context": [], "aria": "polite"},
    "combine_sign": {"code": "combine_sign", "kind": "hazard", "severity": "danger", "blocking": "confirm", "message": "Combine", "surfaces": ["combine", "hazard_dialog"], "required_context": [], "aria": "assertive"},
    "combine_confirmed": {"code": "combine_confirmed", "kind": "verification", "severity": "success", "blocking": "none", "message": "Combine confirmed in game; the card surface is updating.", "surfaces": ["combine", "toast"], "required_context": [], "aria": "polite"},
    "combine_uid_lookup_failed": {"code": "combine_uid_lookup_failed", "kind": "refusal", "severity": "danger", "blocking": "sign", "message": "This card's Card ID could not be found; it cannot be selected for a combine yet.", "surfaces": ["idle_cards", "combine"], "required_context": [], "aria": "assertive"},
    # Sign-state hard refusal (work/combine-modal-redesign state 05, v0.7.84)
    # sm_combine_cards is Posting-tier, 294/294 observed (the internal rules) -- a client-side
    # stop, not a server refusal, so its wording never says "refused by the server".
    "combine_key_tier_refused": {"code": "combine_key_tier_refused", "kind": "refusal", "severity": "danger", "blocking": "sign", "message": "Hive Keychain signed with {key}, not Posting. This must be Posting-tier — reject the {key} prompt in Keychain and try again.", "surfaces": ["combine"], "required_context": ["key"], "aria": "assertive"},
})


def validate_registry() -> None:
    required = {"problem", "fact", "advisory", "warning", "hazard", "refusal", "verification"}
    severities = {"neutral", "info", "caution", "danger", "success"}
    blocks = {"none", "confirm", "stage", "sign", "repeat"}
    for code, spec in NOTICE_REGISTRY.items():
        if spec.get("code") != code or spec.get("kind") not in required or spec.get("severity") not in severities or spec.get("blocking") not in blocks:
            raise ValueError(f"invalid notice registry entry: {code}")
        if not isinstance(spec.get("message"), str) or not isinstance(spec.get("surfaces"), list):
            raise ValueError(f"invalid notice template: {code}")


def format_notice(code: str, *, context: dict[str, Any] | None = None, reason: str | None = None,
                  evidence: dict[str, Any] | None = None, action: dict[str, Any] | None = None,
                  message: str | None = None) -> dict[str, Any]:
    spec = NOTICE_REGISTRY.get(code)
    if spec is None:
        return {"code": "unknown_notice", "kind": "refusal", "severity": "danger", "blocking": "repeat", "message": "An unknown safety notice was returned; stop before continuing.", "reason": reason or f"unknown notice code: {code}", "evidence": evidence or {}, "context": context or {}, "action": action}
    values = dict(context or {})
    if reason is not None:
        values.setdefault("reason", reason)
    template = message if message is not None else spec["message"]
    try:
        rendered = template.format(**values)
    except (KeyError, IndexError, ValueError):
        rendered = template
    result = {"code": code, "kind": spec["kind"], "severity": spec["severity"], "blocking": spec["blocking"], "message": rendered, "reason": reason, "evidence": evidence or {}, "context": context or {}}
    if action is not None:
        result["action"] = action
    if spec.get("phase"):
        result["phase"] = spec["phase"]
    return result


# Stable transport order for backend `row.badges`. 's visible column and
# filter order comes from the richer BADGE_TABLE in badges.js; this legacy payload
# order remains for non-browser API consumers.
BADGE_ORDER: list[str] = [
    "unpowered", "unpowered_worker", "over_cap", "region_dec",
    "staged_changes", "staged_cooldown",
    "dark_discount_wasted", "no_worksite", "redundant_power_core", "underfilled",
    "bloodline_solo", "worksite_incomplete",
    "bloodline_paired",
    "pending_change", "runi", "land_ability_card",
    "idle", "building_in_box", "unstable_totem", "assets_not_loaded",
]


def badge_rank(code: str) -> int:
    """Position of `code` in the canonical badge order; unknown codes sort last."""
    try:
        return BADGE_ORDER.index(code)
    except ValueError:
        return len(BADGE_ORDER)


def badge_meta_projection() -> dict[str, dict[str, Any]]:
    # Group/order feed badges.js's single runtime table; tooltip severity remains
    # independent because it describes notice treatment, not chip colour.
    return {
        code: {
            "label": values[0],
            "kind": values[1],
            "severity": values[3],
            "state_group": values[4],
            "group_order": values[5],
            "tooltip": NOTICE_REGISTRY[code]["message"],
        }
        for code, values in _BADGES.items()
    }


def notices_for_badges(badges: list[str], deed_uid: str | None = None,
                       messages: dict[str, str] | None = None) -> list[dict[str, Any]]:
    messages = messages or {}
    return [
        format_notice(
            code,
            context={"deed_uid": deed_uid} if deed_uid else {},
            message=messages.get(code),
        )
        for code in badges
        if code in NOTICE_REGISTRY
    ]


def candidate_notices(excluded_counts: dict[str, int], rental_rows: list[Any] | None = None, deed_uid: str | None = None) -> list[dict[str, Any]]:
    notices = [format_notice(code, context={"count": count}) for code, count in excluded_counts.items() if count and code in NOTICE_REGISTRY]
    for row in rental_rows or []:
        if getattr(row, "rental_kind", None) == "in" and getattr(row, "rental_expiry", None):
            notices.append(format_notice("rental_expiry_warning", context={"asset_ref": row.card.asset_ref, "expires_at": row.rental_expiry, "deed_uid": deed_uid}, reason=f"expires at {row.rental_expiry}"))
    return notices


def notice_catalog() -> dict[str, dict[str, Any]]:
    return {code: {key: deepcopy(value) for key, value in spec.items() if key != "code"} for code, spec in NOTICE_REGISTRY.items()}


RETRY_VERIFICATION_ACTION = {"id": "retry_verification", "label": "Check again"}


def verification_notice(state: str, *, chain: dict[str, Any] | None = None, domain: dict[str, Any] | None = None, game: dict[str, Any] | None = None) -> dict[str, Any]:
    chain = chain or {}
    domain = domain or {}
    game = game or {}
    if state == "domain-confirmed":
        return format_notice("verification_confirmed", evidence=domain.get("evidence") or {})
    if state == "game-refused":
        evidence = game.get("evidence") or {}
        reason = evidence.get("reason") or evidence.get("error") or "the game supplied no refusal reason"
        return format_notice("verification_game_refused", context={"reason": reason}, reason=reason, evidence=evidence)
    if state == "domain-mismatch":
        return format_notice("verification_mismatch", evidence=domain.get("evidence") or {})
    if state == "domain-not-applied":
        return format_notice("verification_not_applied", evidence=domain.get("evidence") or {})
    game_evidence = game.get("evidence") or {}
    if (
        chain.get("status") == "chain_verified"
        and game.get("status") == "game_unknown"
        and game_evidence.get("lookup_attempted", True) is not False
        and state in {"domain-pending", "domain-could-not-verify", "chain_verified"}
    ):
        block_num = (chain.get("evidence") or {}).get("block_num")
        return format_notice(
            "verification_game_awaiting_verdict",
            context={"block_num": block_num},
            evidence={**(chain.get("evidence") or {}), **game_evidence},
            action=RETRY_VERIFICATION_ACTION,
        )
    if state == "domain-pending":
        evidence = domain.get("evidence") or {}
        pending = evidence.get("pending") or []
        # defect 2 -- pending never resolves to confirmation by elapsed time
        # alone, so once the caller's own escalation threshold is crossed
        # (`pending_metadata.requires_escalation`, fides_land/verify.py's
        # PENDING_ESCALATION_ATTEMPTS) this stops reading like an ordinary in-flight
        # wait and becomes its own distinct, visible state.
        metadata = evidence.get("pending_metadata") or {}
        code = "verification_pending_escalated" if metadata.get("requires_escalation") else "verification_pending"
        return format_notice(code, evidence=evidence, reason="; ".join(pending) or None, action=RETRY_VERIFICATION_ACTION)
    if state == "domain-could-not-verify":
        unverifiable = (domain.get("evidence") or {}).get("unverifiable") or []
        return format_notice("verification_could_not_verify", evidence=domain.get("evidence") or {}, reason="; ".join(unverifiable) or None, action=RETRY_VERIFICATION_ACTION)
    if state == "chain_verified":
        return format_notice("verification_chain_check", evidence=chain.get("evidence") or {}, action=RETRY_VERIFICATION_ACTION)
    if chain.get("evidence", {}).get("reason_kind") == "lookup_failed":
        return format_notice("verification_lookup_failed", evidence=chain.get("evidence") or {}, action=RETRY_VERIFICATION_ACTION)
    if chain.get("evidence", {}).get("reason_kind") == "not_found":
        return format_notice("verification_not_found", evidence=chain.get("evidence") or {}, action=RETRY_VERIFICATION_ACTION)
    if state == "domain_check_failed":
        return format_notice("verification_domain_check_failed", evidence=domain.get("evidence") or {}, action=RETRY_VERIFICATION_ACTION)
    # Any state this function does not recognise (routes_op_preview.py's own contract
    # "a caller must treat any string it does not recognise as not confirmed yet") falls
    # through here too -- still not a failure notice, and still carries the same
    # re-check action rather than leaving the caller with nothing to do about it.
    return format_notice("verification_not_found", evidence=chain.get("evidence") or {}, action=RETRY_VERIFICATION_ACTION)


validate_registry()
