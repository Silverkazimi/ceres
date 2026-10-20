"""§6.6 test_board_projection.py -- the PlotRow contract against real fixture data."""

from __future__ import annotations

import re

import pytest

from ceres.board import _bonus_for_plot, project_board

PLOT_RE = re.compile(r"^-?\d+-\d+-\d+$")

REQUIRED_PLOT_KEYS = {
    "deed_uid", "plot", "rarity", "plot_status", "occupied", "terrain", "resource",
    "worksite_type", "magic_type", "deed_stats", "tax_rate", "time_crystal_value",
    "territory", "region", "harvest_pp", "base_pp", "base_pp_after_cap", "base_pp_cap",
    "over_cap", "construction_pp", "work_per_hour", "boost", "multiplier",
    "multiplier_parts", "abilities_boost", "bloodlines_boost", "biome", "capacity",
    "is_powered", "is_power_core_staked", "is_energized", "has_labors_luck", "dec",
    "plot_dec_efficiency", "ability_holders", "bonus", "badges", "workers",
    "asset_fetch", "diagnostics",
}


def test_every_plot_row_key_present(report, details_by_id):
    board = project_board(report, details_by_id)
    assert board["plots"], "fixture must produce at least one plot"
    for row in board["plots"]:
        missing = REQUIRED_PLOT_KEYS - set(row)
        assert not missing, f"plot {row.get('deed_uid')} missing keys: {missing}"


def test_plot_matches_region_tract_plot_format(report, details_by_id):
    board = project_board(report, details_by_id)
    for row in board["plots"]:
        assert PLOT_RE.match(row["plot"]), row["plot"]


def test_rarity_and_status_are_separate_fields(report, details_by_id):
    board = project_board(report, details_by_id)
    for row in board["plots"]:
        assert " " not in (row["rarity"] or "")
        assert " " not in (row["plot_status"] or "")


def test_terrain_deed_stats_tax_rate_time_crystal_survive(report, details_by_id):
    board = project_board(report, details_by_id)
    row = next(r for r in board["plots"] if r["deed_uid"] == "I-296-b0dbc1ebc5ef75")
    assert row["terrain"] == "Tundra"
    assert row["deed_stats"] == {"building_in_box": True, "unstable_totem": True}
    assert row["tax_rate"] == "0.10"
    assert row["time_crystal_value"] == 1000


def test_deed_stats_flags_surface_as_badges(report, details_by_id):
    """unstable_totem/building_in_box moved from the worker sub-table's plain-text
 deed_stats note into the Badges column (board-polish batch 15) -- both are
 PROPERTIES, same badge_meta-driven system as sole_ability_holder/over_cap."""
    board = project_board(report, details_by_id)
    row = next(r for r in board["plots"] if r["deed_uid"] == "I-296-b0dbc1ebc5ef75")
    assert "unstable_totem" in row["badges"]
    assert "building_in_box" in row["badges"]
    for code in ("unstable_totem", "building_in_box"):
        assert board["badge_meta"][code]["kind"] == "property"


def test_bonus_assets_join_by_stake_ref_uid(report, details_by_id):
    board = project_board(report, details_by_id)
    row = next(r for r in board["plots"] if r["deed_uid"] == "I-294-62529e285ddcf0")
    assert row["bonus"]["totem"]["name"] == "Epic Totem"
    assert row["bonus"]["totem"]["rarity"] == "epic"
    assert row["bonus"]["totem"]["asset_ref"] == "22ea1adf761521b0a1ae16ce"
    assert row["bonus"]["totem"]["resolution"] == "resolved"
    assert row["bonus"]["title"]["name"] == "The Proven"
    assert row["bonus"]["title"]["asset_ref"] == "6e08bd863510fc9e664b50eb"
    assert row["bonus"]["title"]["resolution"] == "resolved"


def test_bonus_assets_retain_power_core_identity(report, details_by_id):
    board = project_board(report, details_by_id)
    row = next(r for r in board["plots"] if r["deed_uid"] == "I-294-8bb3ce6436e586")
    assert row["bonus"]["power_core"]["asset_ref"] == "359ea709f0f928403e4ea1ef"


def test_bonus_assets_retain_uid_and_asset_ref_for_every_item_class():
    bonus = _bonus_for_plot({"deed-1": [
        {"asset_kind": "totem", "name": "Rare Totem", "boost": "0.25", "asset_ref": "totem-ref", "uid": "totem-uid"},
        {"asset_kind": "title", "name": "The Proven", "boost": "0.10", "asset_ref": "title-ref", "uid": "title-uid"},
        {"asset_kind": "power_core", "name": "Power Core", "asset_ref": "core-ref", "uid": "core-uid"},
    ]}, "deed-1")
    assert bonus["totem"]["asset_ref"] == "totem-ref"
    assert bonus["totem"]["uid"] == "totem-uid"
    assert bonus["title"]["asset_ref"] == "title-ref"
    assert bonus["title"]["uid"] == "title-uid"
    assert bonus["power_core"]["asset_ref"] == "core-ref"
    assert bonus["power_core"]["uid"] == "core-uid"


def test_totem_survives_on_a_plot_with_zero_workers():
    """A plot with a totem but zero workers still gets its totem (§6.6)."""
    from tests.ceres.conftest import build_report

    report = build_report()
    # I-294-8bb3ce6436e586 carries a power_core with a single worker in this fixture
    # force worker_count to 0 to exercise "totem present, zero workers" without
    # depending on a specific fixture plot having both traits already.
    for row in report["plots"]:
        if row["deed_uid"] == "I-294-8bb3ce6436e586":
            row["staking_facts"]["worker_count"] = 0
    report["workers"] = [w for w in report["workers"] if w.get("stake_ref_uid") != "I-294-8bb3ce6436e586"]
    from ceres.board import project_board
    board = project_board(report, {})
    row = next(r for r in board["plots"] if r["deed_uid"] == "I-294-8bb3ce6436e586")
    assert row["capacity"]["worker_count"] == 0
    assert row["bonus"]["power_core"] is not None


def test_runi_does_not_consume_one_of_five_regular_worker_slots(report, details_by_id):
    # "5+R/5" -> "5/5+R" (corrected 2026-08-21): the "+R" moved to the capacity
    # side -- Runi occupies a real sixth slot beyond the five monster-card
    # slots, so "5/5+R" (five monsters in five slots, plus a Runi) describes
    # actual capacity rather than inflating the occupancy figure.
    source = next(r for r in report["plots"] if r["deed_uid"] == "I-294-62529e285ddcf0")
    source["staking_facts"].update({
        "max_workers_allowed": 4,
        "powered_worker_count": 5,
        "is_power_core_staked": False,
        "is_energized": False,
        "is_runi_staked": True,
    })
    for worker in report["workers"]:
        if worker.get("stake_ref_uid") == source["deed_uid"] and worker.get("stake_type_uid") == "STK-LND-WKR":
            worker["is_powered"] = worker.get("slot") <= 4
            if worker.get("slot") == 5:
                worker["total_harvest_pp"] = 0
    board = project_board(report, details_by_id)
    row = next(r for r in board["plots"] if r["deed_uid"] == "I-294-62529e285ddcf0")
    assert row["capacity"]["runi_staked"] is True
    # Raw upstream telemetry includes the Runi; every slot-facing field below does not.
    assert row["capacity"]["worker_count"] == 6
    assert row["capacity"]["card_capacity"] == 5
    assert row["capacity"]["potential_card_capacity"] == 5
    assert row["capacity"]["powered_card_capacity"] == 5
    assert row["capacity"]["powered_slots"] == 4
    assert row["capacity"]["open_slots"] == 0
    assert row["capacity"]["display"] == "5/5+R"
    warning = next(notice for notice in row["notices"] if notice["code"] == "unpowered_worker")
    assert "Worker slot 5" in warning["message"]
    assert "1 of 5 filled worker slots" in warning["message"]
    assert "unpowered and inactive; it produces nothing (0 PP)" in warning["message"]


def test_runi_only_plot_with_four_regular_workers_is_not_marked_unpowered(report, details_by_id):
    deed_uid = "I-294-62529e285ddcf0"
    source = next(r for r in report["plots"] if r["deed_uid"] == deed_uid)
    source["staking_facts"].update({
        "worker_count": 5,
        "max_workers_allowed": 4,
        "is_power_core_staked": False,
        "is_energized": False,
        "is_runi_staked": True,
    })
    report["workers"] = [
        worker for worker in report["workers"]
        if not (worker.get("stake_ref_uid") == deed_uid and worker.get("slot") == 5)
    ]

    row = next(r for r in project_board(report, details_by_id)["plots"] if r["deed_uid"] == deed_uid)
    assert row["capacity"]["powered_slots"] == 4
    assert "unpowered" not in row["badges"]
    assert "unpowered_worker" not in row["badges"]


def test_over_cap_true_on_the_above_cap_fixture(report, details_by_id):
    board = project_board(report, details_by_id)
    row = next(r for r in board["plots"] if r["deed_uid"] == "I-294-62529e285ddcf0")
    assert row["base_pp_after_cap"] > row["base_pp_cap"]
    assert row["over_cap"] is True


# Unassigned bonus assets (board polish, 2026-08-21) -- five families: totems
# titles/power cores are Praetoria's real Source A capture (report["unassigned_items"])
# Runi/Land-Verico cards are Source B, selected from idle_cards rather than fetched.


def _idle_card(asset_ref, card_detail_id, bucket="unstaked_apparent"):
    return {
        "asset_ref": asset_ref,
        "classification": {"bucket": bucket, "confidence": "confident", "reason": "test fixture"},
        "facts": {"card_detail_id": card_detail_id},
    }


def test_idle_cards_carry_skin(report, details_by_id):
    """build_idle_cards() must ship `skin` on idle-card rows the same way staked
 worker rows already do (v0.7.82) -- editorCardImage() in app.js reads
 `asset.skin` to resolve the skinned CDN art path, and without this field the
 combine-arrival dialog silently falls back to unskinned art. The fixture's
 "Spooky" skin (collection_sample.json) survives the classification pipeline
 into an idle-card row with a non-null bucket, proving the field flows through
 for a genuinely idle card, not just a staked one."""
    board = project_board(report, details_by_id)
    skinned = [c for c in board["idle_cards"] if c.get("skin")]
    assert skinned, "fixture must produce at least one skinned idle card"
    assert any(c["skin"] == "Spooky" for c in skinned)


def test_idle_card_skin_mutation_is_red(report, details_by_id):
    """Injected defect: delete the `"skin": facts.get("skin"),` line from the real
 ceres/board.py source (revert to the pre-fix shape), exec the mutated module
 under a throwaway name, and re-run the guard's own assertion against ITS
 build_idle_cards() -- the guard must fail because no idle card carries a skin
 any more. Mutating the live source (not the output dict) is what makes this
 an admissible mutation test rather than a tautology."""
    import types
    from pathlib import Path

    source = (Path(__file__).parents[2] / "ceres" / "board.py").read_text(encoding="utf-8")
    target = '            "skin": facts.get("skin"),\n'
    assert target in source, "mutation target line must exist pre-mutation"
    mutated_source = source.replace(target, "", 1)

    module = types.ModuleType("ceres._board_mutation_test")
    module.__dict__["__file__"] = str(Path(__file__).parents[2] / "ceres" / "board.py")
    exec(compile(mutated_source, "ceres/board.py (mutated)", "exec"), module.__dict__)

    idle, loaded = module.build_idle_cards(report, details_by_id)
    assert loaded
    with pytest.raises(AssertionError):
        skinned = [c for c in idle if c.get("skin")]
        assert skinned, "fixture must produce at least one skinned idle card"


def test_unassigned_items_families_render_capturable_and_uncapturable(report, details_by_id):
    report = dict(report)
    report["unassigned_items"] = {
        "totem": {"capturable": True, "count": 2, "items": [{"item_detail_id": 318, "name": "Common Totem", "count": 2}]},
        "title": {"capturable": True, "count": 0, "items": []},
        "power_core": {"capturable": True, "count": 1, "items": [{"item_detail_id": 322, "name": "Power Core", "count": 1}]},
        "runi": {"capturable": False, "count": None, "items": [], "reason": "nested upstream error P0001"},
    }
    board = project_board(report, details_by_id)
    unassigned = board["unassigned_bonus"]
    assert unassigned["totem"]["capturable"] is True
    assert unassigned["totem"]["count"] == 2
    assert unassigned["totem"]["items"][0]["name"] == "Common Totem"
    assert unassigned["title"]["capturable"] is True
    assert unassigned["title"]["count"] == 0
    assert unassigned["power_core"]["count"] == 1


def test_unassigned_runi_family_ignores_source_a_and_uses_idle_cards(report, details_by_id):
    """report["unassigned_items"]["runi"] is always uncapturable (STK-LND-RUNI's
 documented P0001 failure) -- the board's Runi family must come from idle_cards
 regardless of what Source A says, never rendered as "not captured" itself."""
    report = dict(report)
    report["unassigned_items"] = {
        "totem": {"capturable": False, "count": None, "items": [], "reason": "unused in this test"},
        "title": {"capturable": False, "count": None, "items": [], "reason": "unused in this test"},
        "power_core": {"capturable": False, "count": None, "items": [], "reason": "unused in this test"},
        "runi": {"capturable": False, "count": None, "items": [], "reason": "P0001 upstream error"},
    }
    report["cards"] = list(report["cards"]) + [
        _idle_card("test-runi-1", 505),
        _idle_card("test-land-1", 869, bucket="stake_expired_or_historical"),
    ]
    board = project_board(report, details_by_id)
    unassigned = board["unassigned_bonus"]
    assert unassigned["runi"]["capturable"] is True
    assert unassigned["runi"]["count"] == 1
    assert unassigned["runi"]["cards"][0]["asset_ref"] == "test-runi-1"
    assert unassigned["land_card"]["count"] == 1
    assert unassigned["land_card"]["cards"][0]["asset_ref"] == "test-land-1"
    # Selected from idle_cards, not duplicated as new records -- both still appear there.
    idle_refs = {c["asset_ref"] for c in board["idle_cards"]}
    assert "test-runi-1" in idle_refs
    assert "test-land-1" in idle_refs


def test_unassigned_items_missing_entirely_reports_not_captured(report, details_by_id):
    """A report with no unassigned_items key at all (e.g. a pre-2026-08-21 snapshot)
 must render every item family as explicitly unfetchable, never silently zero."""
    report = dict(report)
    report.pop("unassigned_items", None)
    board = project_board(report, details_by_id)
    unassigned = board["unassigned_bonus"]
    for family in ("totem", "title", "power_core"):
        assert unassigned[family]["capturable"] is False
        assert unassigned[family]["count"] is None
        assert unassigned[family]["reason"]
