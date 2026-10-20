""" Phase 1 -- the read-only worksite-change modal. Pure-logic unit tests
against `ceres/worksite_swap.py` plus `ceres/board.py` integration (`swap_targets`/
`swap_eligibility`/`pending_change` on the plot row)."""

from __future__ import annotations

from ceres.board import project_board
from ceres.worksite_swap import (
    ALL_TARGETS,
    build_pending_change,
    build_swap_targets,
    deed_status_token_boost_for,
    legal_targets,
    projected_rate,
    ready_worksite_change,
    swap_control_available,
    swap_eligibility,
)

_ALL_TERRAINS = {
    "plains": "grain", "river": "grain", "bog": "grain", "lake": "grain",
    "swamp": "wood", "forest": "wood", "tundra": "wood", "jungle": "wood",
    "desert": "stone", "canyon": "stone", "hills": "stone",
    "mountain": "iron", "badlands": "iron", "caldera": "iron",
}


def test_every_terrain_has_exactly_four_legal_targets_native_plus_three():
    assert len(_ALL_TERRAINS) == 14
    for terrain, native in _ALL_TERRAINS.items():
        targets = legal_targets(terrain)
        assert targets == [native, "research", "sps", "aura"]
        assert len(targets) == 4


def test_unrecognised_terrain_returns_none_not_a_partial_list():
    assert legal_targets("nonsense") is None
    assert legal_targets(None) is None


def test_legacy_grain_does_not_displace_terrain_native_target():
    # Invariant: legality always comes from the terrain table, including migration states.
    assert legal_targets("mountain", current_resource="GRAIN") == ["iron", "research", "sps", "aura"]
    assert legal_targets("tundra", current_resource="grain") == ["wood", "research", "sps", "aura"]
    assert legal_targets("hills", current_resource="Grain") == ["stone", "research", "sps", "aura"]


def test_current_resource_does_not_change_legal_target_set():
    # Invariant: current worksite state does not alter the legal target set.
    assert legal_targets("mountain", current_resource="research") == ["iron", "research", "sps", "aura"]
    assert legal_targets("mountain", current_resource=None) == ["iron", "research", "sps", "aura"]


def test_dstb_fires_only_for_the_three_documented_worksite_status_pairs():
    assert deed_status_token_boost_for("research", "magical") == 1.0
    assert deed_status_token_boost_for("research", "occupied") == 0.0
    assert deed_status_token_boost_for("research", "natural") == 0.0
    assert deed_status_token_boost_for("sps", "occupied") == 1.0
    assert deed_status_token_boost_for("sps", "magical") == 0.0
    assert deed_status_token_boost_for("aura", "magical") == 1.0
    assert deed_status_token_boost_for("aura", "occupied") == 1.0
    assert deed_status_token_boost_for("aura", "natural") == 0.0
    assert deed_status_token_boost_for("grain", "magical") == 0.0


def test_projected_rate_on_a_natural_plot_collapses_to_construction_pp_times_rate():
    rate = projected_rate("grain", total_construction_pp=1000.0, total_boost=0.5, deed_status_token_boost=0.0, status="natural")
    # harvest = 1000 * (1 + 0.5 - 0 + 0) = 1500; grain rate is 0.02/PP/hr.
    assert rate["kind"] == "point"
    assert rate["value"] == 1500.0 * 0.02


def test_projected_rate_dstb_2x_trap_on_a_magical_plot_moving_into_research():
    # A magical plot's CURRENT worksite is something other than Research (so its
    # current dstb is 0, total_boost carries no other component here); moving TO
    # Research gains the +100% boost, i.e. the naive (non-dstb-corrected) rate
    # ignoring that the TARGET's own dstb differs from the plot's current one
    # would be exactly HALF of the correct, dstb-corrected rate.
    rate = projected_rate("research", total_construction_pp=1000.0, total_boost=0.0, deed_status_token_boost=0.0, status="magical")
    # harvest = 1000 * (1 + 0 - 0 + 1) = 2000; research rate 0.0001/PP/hr.
    assert rate["value"] == 2000.0 * 0.0001
    naive = 1000.0 * (1 + 0.0) * 0.0001
    assert rate["value"] == naive * 2


def test_projected_rate_dstb_2x_trap_on_an_occupied_plot_leaving_sps():
    # An occupied plot currently on Shard Mine (SPS) carries dstb=1 already (the
    # ONLY component of total_boost here); moving to a non-SPS/non-Aura target
    # LOSES that boost -- the naive (non-dstb-corrected) rate would be exactly
    # DOUBLE the correct, dstb-corrected rate.
    total_boost = 1.0  # entirely the current SPS dstb
    naive = 1000.0 * (1 + total_boost) * 0.005  # wood rate, ignoring the target's own dstb
    rate = projected_rate("wood", total_construction_pp=1000.0, total_boost=total_boost, deed_status_token_boost=1.0, status="occupied")
    # harvest = 1000 * (1 + 1.0 - 1 + 0) = 1000; wood rate 0.005/PP/hr.
    assert rate["value"] == 1000.0 * 0.005
    assert rate["value"] == naive / 2


def test_sps_projected_rate_is_an_explicit_null_never_a_point_estimate():
    rate = projected_rate("sps", total_construction_pp=50000.0, total_boost=1.0, deed_status_token_boost=0.0, status="occupied")
    assert rate["kind"] == "pool_share"
    assert rate["value"] is None
    assert rate["low"] is None and rate["high"] is None


def test_swap_targets_always_four_with_exactly_one_is_current():
    for terrain, native in _ALL_TERRAINS.items():
        tiles = build_swap_targets(
            terrain=terrain, current_resource=native.upper(), status="natural",
            total_construction_pp=1000.0, total_boost=0.1, deed_status_token_boost=0.0,
            total_work_per_hour=42.0,
        )
        assert len(tiles) == 4
        current = [t for t in tiles if t["is_current"]]
        assert len(current) == 1
        assert current[0]["target"] == native
        assert tiles[0] is current[0], "current tile must be pinned first"


def test_legacy_grain_plot_offers_native_target_and_marks_grain_current():
    # Invariant: the migration current tile is retained while the native target stays selectable.
    tiles = build_swap_targets(
        terrain="mountain", current_resource="GRAIN", status="natural",
        total_construction_pp=1000.0, total_boost=0.1, deed_status_token_boost=0.0,
        total_work_per_hour=42.0,
    )
    assert [tile["target"] for tile in tiles] == ["grain", "iron", "research", "sps", "aura"]
    current = tiles[0]
    assert current["target"] == "grain"
    assert current["is_current"] is True
    assert current["tc_cost"] is None
    assert next(tile for tile in tiles if tile["target"] == "iron")["is_current"] is False


def test_current_tile_carries_no_time_or_tc_cost_and_reads_the_measured_rate():
    tiles = build_swap_targets(
        terrain="plains", current_resource="GRAIN", status="natural",
        total_construction_pp=1000.0, total_boost=0.1, deed_status_token_boost=0.0,
        total_work_per_hour=42.0,
    )
    current = next(t for t in tiles if t["is_current"])
    assert current["time_hours"] is None
    assert current["tc_cost"] is None
    assert current["rate"]["value"] == 42.0  # measured total_work_per_hour, not re-projected
    others = [t for t in tiles if not t["is_current"]]
    assert all(t["time_hours"] == 24 and t["tc_cost"] == 50 for t in others)


def test_aura_tile_requires_permit_and_all_three_higher_tier_need_all_four_inputs():
    tiles = build_swap_targets(
        terrain="plains", current_resource="GRAIN", status="natural",
        total_construction_pp=1000.0, total_boost=0.1, deed_status_token_boost=0.0,
        total_work_per_hour=42.0,
    )
    by_target = {t["target"]: t for t in tiles}
    assert by_target["aura"]["requires_permit"] is True
    assert by_target["grain"]["requires_permit"] is False
    for higher_tier in ("research", "sps", "aura"):
        assert by_target[higher_tier]["needs_inputs"] == ["grain", "wood", "stone", "iron"]
    assert by_target["grain"]["needs_inputs"] == ["grain"]


# H1 -- the zero-PP hard refusal

def test_refusal_fires_for_an_unpowered_plot_with_cause_and_remedy():
    result = swap_eligibility(is_powered=False, worker_count=3)
    assert result["eligible"] is False
    assert "power source" in result["reason"]
    assert "Power Core" in result["remedy"]


def test_refusal_fires_for_a_zero_worker_plot_with_cause_and_remedy():
    result = swap_eligibility(is_powered=True, worker_count=0)
    assert result["eligible"] is False
    assert "workers seated" in result["reason"]
    assert "Seat at least one worker" in result["remedy"]


def test_eligible_when_powered_and_staffed():
    assert swap_eligibility(is_powered=True, worker_count=1) == {"eligible": True, "reason": None, "remedy": None}


def test_refusal_clears_on_the_next_call_with_no_restart():
    # swap_eligibility is a pure function of the two fields it's given -- there is
    # no cached/stale state to restart away. A plot that becomes powered/staffed
    # between two board builds simply gets a different answer on the next call.
    assert swap_eligibility(is_powered=False, worker_count=0)["eligible"] is False
    assert swap_eligibility(is_powered=True, worker_count=1)["eligible"] is True


# swap_control_available -- suppression, not refusal

def test_control_suppressed_for_kingdom_no_worksite_and_mid_change():
    assert swap_control_available(plot_status="kingdom", diagnostics=[], project_type="castle_operation") is False
    assert swap_control_available(plot_status="natural", diagnostics=["no_worksite"], project_type=None) is False
    assert swap_control_available(plot_status="natural", diagnostics=["construction_incomplete"], project_type="worksite_grain_construction") is False
    assert swap_control_available(plot_status="natural", diagnostics=[], project_type="worksite_grain_construction") is False


def test_control_available_for_an_ordinary_settled_worksite():
    assert swap_control_available(plot_status="natural", diagnostics=[], project_type="worksite_grain") is True
    assert swap_control_available(plot_status="occupied", diagnostics=[], project_type="worksite_sps") is True


def test_ready_construction_keeps_control_and_exposes_live_grain_fee():
    wf = {
        "project_type": "worksite_stone_construction", "project_id": 397948,
        "projected_end": "2020-01-01T00:00:00Z", "hours_to_completion": 0,
        "grain_required": 500,
    }
    ready = ready_worksite_change(wf, 0.033)
    assert ready["to_label"] == "Quarry"
    assert ready["grain_cost"] == 500
    assert ready["project_id"] == 397948
    assert swap_control_available(
        plot_status="magical", diagnostics=["construction_incomplete"],
        project_type="worksite_stone_construction", ready=True,
    ) is True


def test_ready_construction_ignores_stale_positive_hours_to_completion():
    ready = ready_worksite_change({
        "project_type": "worksite_stone_construction", "project_id": 397948,
        "projected_end": "2020-01-01T00:00:00Z", "hours_to_completion": 5,
        "grain_required": 500,
    }, 0.033)
    assert ready is not None
    assert ready["grain_cost"] == 500


# pending_change -- structured shape, "stalled" never a countdown

def test_pending_change_none_when_not_mid_construction():
    assert build_pending_change("deed-1", {"project_type": "worksite_grain"}, 40.0, "GRAIN") is None
    assert build_pending_change("deed-1", {}, 40.0, "GRAIN") is None
    assert build_pending_change("deed-1", None, 40.0, "GRAIN") is None


def test_pending_change_live_in_flight_uses_moving_hours_not_event_lagging_pp():
    wf = {
        "project_type": "worksite_iron_construction", "pp_required": 50000.0, "pp_spent": 0.0,
        "hours_to_completion": 17.0, "projected_hours": 24.0,
        "start_date": "2025-04-21T07:44:48Z", "projected_end": "2025-04-22T07:44:49Z",
    }
    change = build_pending_change("deed-1", wf, 10.0, None)
    assert change["kind"] == "worksite_change"
    assert change["to"] == "iron"
    assert change["to_label"] == "Ore Mine"  # Phase 0 -- backend-owned name
    assert change["from_label"] is None  # no from_resource given
    assert change["started_at"] == "2025-04-21T07:44:48Z"
    assert change["ready_at"] == "2025-04-22T07:44:49Z"
    assert change["stalled"] is False
    assert change["pp_required"] == 50000.0
    assert change["pp_spent"] == 0.0
    assert change["hours_to_completion"] == 17.0
    assert change["projected_end"] == "2025-04-22T07:44:49Z"
    assert 0.29 < change["progress_pct"] < 0.30


def test_pending_change_from_label_reads_the_backend_name_table():
    # Phase 0 -- `from_label`/`to_label` exist so the row chip and the
    # modal's confirmed copy never need their own resource->name mirror.
    wf = {
        "project_type": "worksite_stone_construction", "pp_required": 100.0, "pp_spent": 10.0,
        "start_date": "2025-04-21T07:44:48Z", "projected_end": "2025-04-22T07:44:49Z",
    }
    change = build_pending_change("deed-1", wf, 10.0, "GRAIN")
    assert change["to_label"] == "Quarry"
    assert change["from_label"] == "Grain Farm"


def test_pending_change_stalled_renders_no_ready_at_never_the_overflowed_date():
    # player0001's two real Caldera plots (§0.5 of the plan): zero production rate
    # projected_end overflowed to year 116104. `ready_at` must be None, not that date.
    wf = {
        "project_type": "worksite_research_construction", "pp_required": 4500000.0, "pp_spent": 4938953.8,
        "start_date": "2024-01-14T20:25:27Z", "projected_end": "+116104-06-02",
    }
    change = build_pending_change("deed-1", wf, 0.0, None)
    assert change["stalled"] is True
    assert change["ready_at"] is None
    assert change["progress_pct"] is None


# board.py integration

def test_board_row_carries_the_three_new_keys(report, details_by_id):
    board = project_board(report, details_by_id)
    for row in board["plots"]:
        assert "swap_targets" in row
        assert "swap_eligibility" in row
        assert "pending_change" in row


def test_board_offers_first_clear_tiles_on_the_no_worksite_plot(report, details_by_id):
    # (his decision 2026-09-14): a never-cleared plot gets the picker for its first clear
    # (was: suppressed). Every tile is a first-clear tile with no crystal shortcut.
    board = project_board(report, details_by_id)
    row = next(r for r in board["plots"] if "no_worksite" in r["badges"])
    assert row["swap_targets"], "a never-cleared plot must offer its legal worksites"
    assert all(t.get("first_clear") and t.get("tc_cost") is None for t in row["swap_targets"])
    assert row["swap_eligibility"] is not None


def test_board_refusal_carries_a_formatted_notice(report, details_by_id):
    board = project_board(report, details_by_id)
    refused = [r for r in board["plots"] if r["swap_eligibility"] and r["swap_eligibility"]["eligible"] is False]
    assert refused, "fixture must contain at least one unpowered/idle plot"
    notice = refused[0]["swap_eligibility"]["notice"]
    assert notice["code"] == "worksite_swap_refused_no_pp"
    assert notice["blocking"] == "stage"
    assert refused[0]["swap_eligibility"]["reason"] in notice["message"]
    assert refused[0]["swap_eligibility"]["remedy"] in notice["message"]


def test_board_pending_change_uses_capture_enriched_prior_resource(report, details_by_id):
    plot = report["plots"][0]
    plot["facts"]["resource"] = None
    plot["facts"]["prior_resource"] = "GRAIN"
    plot["worksite_facts"] = {
        "project_type": "worksite_iron_construction",
        "hours_to_completion": 12.0,
        "projected_hours": 24.0,
        "start_date": "2026-08-26T00:00:00Z",
        "projected_end": "2026-08-27T00:00:00Z",
    }
    plot["staking_facts"]["total_work_per_hour"] = 10.0

    board = project_board(report, details_by_id)
    row = next(item for item in board["plots"] if item["deed_uid"] == plot["deed_uid"])
    assert row["pending_change"]["from"] == "GRAIN"
    assert row["pending_change"]["from_label"] == "Grain Farm"


def test_all_targets_constant_matches_worksite_name_keys():
    from ceres.worksite_swap import WORKSITE_NAME
    assert set(WORKSITE_NAME) == ALL_TARGETS
