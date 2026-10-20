from __future__ import annotations

import re
from pathlib import Path

from ceres.board import project_board
from ceres.notices import NOTICE_REGISTRY, format_notice


ROOT = Path(__file__).parents[2]


def _product_notice_sources() -> list[str]:
    paths = [path for path in (ROOT / "ceres").glob("*.py") if path.name != "notices.py"]
    paths.extend((ROOT / "ceres" / "static").glob("*.js"))
    return [path.read_text(encoding="utf-8") for path in paths]


def _produced_notice_codes() -> set[str]:
    sources = _product_notice_sources()
    return {
        code
        for code in NOTICE_REGISTRY
        if any(re.search(rf"(?:['\"]){re.escape(code)}(?:['\"])", source) for source in sources)
    }


def test_phase_zero_inventory_has_a_producer_or_explicit_reachability_disposition() -> None:
    disposition = {
        "verification_waiting": "internal_only: synchronous op-verify has no progressive event",
        "verification_chain_check": "internal_only: synchronous op-verify returns its final route state",
        "board_warning": "reachable: board projection preserves report warnings",
        "rental_expiry_warning": "reachable: selected rental-in candidates carry the notice",
        "same_plot": "reachable: candidate exclusion producer retains the count",
        "base_pp_change_not_reported": "dead: no producer or quoted product use exists in ceres/",
        "base_pp_delta_cap_unknown": "dead: no producer or quoted product use exists in ceres/",
        "base_pp_stage_explainer": "dead: no producer or quoted product use exists in ceres/",
        "combine_deselect": "dead: no producer or quoted product use exists in ceres/",
        "combine_destroyed": "dead: no producer or quoted product use exists in ceres/",
        "combine_survivor": "dead: no producer or quoted product use exists in ceres/",
        "combine_uid_lookup_failed": "dead: no producer or quoted product use exists in ceres/",
        "verification_game_awaiting_verdict": "reachable: verification_notice() selects it for an on-chain operation awaiting a game verdict",
        "worksite_swap_sps_pool_share": "dead: only unquoted prose mentions exist, not a product producer",
        "checkout_cooldown_blocked": "dead: a cooling staged card is shown by the 'Waiting' chip (cooldownReasonFragment); only a comment quotes the code",
        "removal_estimate_provisional": "dead: no producer or quoted product use exists in ceres",
        "stage_plans_changed_elsewhere": "dead: another tab's staged edits are merged into this tab (stage-plans.js) instead of announced",
    }
    assert set(disposition) <= set(NOTICE_REGISTRY)
    assert all(reason for reason in disposition.values())
    covered = set(disposition) | _produced_notice_codes()
    assert set(NOTICE_REGISTRY) <= covered


def test_board_warning_producer_emits_registered_notice() -> None:
    board = project_board({"plots": [], "warnings": ["partial capture"]}, {})
    assert board["warnings"] == ["partial capture"]
    assert board["notices"] == [format_notice("board_warning", context={"reason": "partial capture", "deed_uid": None})]
