""" Phase 1 server guards.

G10's mutation check was run against the completed implementation: replacing the raw
Base-PP branch with ``base_delta = dict(construction)`` made all four G10 test cases
fail, including both over-cap cases and the donor case; the removal assertion itself
remained independent of that candidate-path mutation. Restoring the branch made all
four pass. G7's mutation check was also run: returning ``0.0`` for item ``own_base_pp``
made its assertion fail, and restoring ``None`` made it pass.
"""

from __future__ import annotations

from ceres.candidates import plot_removal_delta, rank_candidates
from praetoria.land_reallocate import MovableCard, Plot, evaluate_candidates_for_plot
from tests.ceres.test_candidates_api import BASIC_DETAILS, _report
from tests.ceres.test_removal_impact import DEED, KINBOUND_REF, _kinbound_fixture


def _plot(*, cap: float, cards: list[dict]) -> Plot:
    cards = [dict(card, slot=card.get("slot", index)) for index, card in enumerate(cards, 1)]
    return Plot(
        "deed", "1-1-1", "common", "occupied", "GRAIN", "T", 5, 1.0, cap,
        {"Red": 0.1, "Blue": -0.5, "White": 0.0, "Black": -0.5, "Green": 0.0, "Gold": 0.0},
        cards,
    )


def _candidate(name: str, base: float, ref: str) -> MovableCard:
    return MovableCard(name, "Red", base, 0.0, "idle", None, asset_ref=ref)


def test_g10_removal_base_delta_is_point_on_an_over_cap_plot() -> None:
    report, details = _kinbound_fixture()
    removal = plot_removal_delta(report, DEED, [KINBOUND_REF], details_by_id=details)
    assert removal["cap_relief"] == {
        "status": "unknown", "value": None, "low": None, "high": None, "base_pp": None,
        "plot_over_cap_before": True, "plot_over_cap_after_removal": False,
        "reason": "cap_derivation_failed",
        "evidence": {"tier": "measurement-sourced", "source": "library/land-worker-abilities.md §10a", "cascade": "n=1"},
        "note": "the measured descending-slot, floored-at-zero cap cascade supplies survivor relief",
    }
    assert removal["own_base_pp_delta"] == {
        "status": "point", "value": -2200.0, "low": -2200.0, "high": -2200.0, "reason": None,
    }
    over_cap_evaluation = evaluate_candidates_for_plot(
        _plot(cap=1_000, cards=[{"name": "Resident", "color": "Red", "base": 900, "cur": 990, "asset_ref": "resident"}]),
        [_candidate("Over cap", 200, "over-cap")], target_slot_occupant=None,
        details_by_id={}, base_pp_ceiling=1_000,
    )[0]
    assert over_cap_evaluation.base_pp_delta["status"] == "point"


def test_g10_candidate_base_delta_stays_point_when_construction_is_unknown() -> None:
    evaluation = evaluate_candidates_for_plot(
        _plot(cap=1_000, cards=[{"name": "Resident", "color": "Red", "base": 900, "cur": 990, "asset_ref": "resident"}]),
        [_candidate("Over cap", 200, "over-cap")], target_slot_occupant=None,
        details_by_id={}, base_pp_ceiling=1_000,
    )[0]
    assert evaluation.construction_pp_delta["status"] == "point"
    assert evaluation.construction_pp_delta["reason"] is None
    assert evaluation.base_pp_delta == {
        "status": "point", "value": 200.0, "low": 200.0, "high": 200.0, "reason": None,
    }


def _donor_candidate_row() -> dict:
    donor_report = _report(powered=True)
    donor_report["plots"][0]["staking_facts"]["red_biome_modifier"] = -0.5
    donor_uid = "donor-plot"
    donor_report["plots"].append({
        "deed_uid": donor_uid,
        "facts": {"plot": "1-1-2", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
        "staking_facts": {
            "max_workers_allowed": 5, "total_base_pp_cap": 1_000,
            "is_power_core_staked": True, "is_energized": False, "is_runi_staked": False,
        },
    })
    donor_report["workers"] += [
        {
            "name": "Donor", "card_detail_id": 40001, "land_base_pp": 1_000,
            "total_harvest_pp": 1_600, "asset_ref": "donor", "stake_ref_uid": donor_uid,
            "asset_kind": "worker_card", "slot": 0,
        },
        *[
            {
                "name": f"Co-resident {index}", "card_detail_id": 40001, "land_base_pp": 1_000,
                "total_harvest_pp": 1_600, "asset_ref": f"co-resident-{index}", "stake_ref_uid": donor_uid,
                "asset_kind": "worker_card", "slot": index,
            }
            for index in range(1, 5)
        ],
    ]
    donor_report["cards"] += [
        {"asset_ref": "donor", "facts": {"card_detail_id": 40001, "level": 1}},
        *[
            {"asset_ref": f"co-resident-{index}", "facts": {"card_detail_id": 40001, "level": 1}}
            for index in range(1, 5)
        ],
    ]
    donor_body = rank_candidates(
        donor_report, "plot-api", "worker", player="player0005", bucket="staked", include_cooling=True,
        include_terrain_penalised=True,  # Red is -50 % on the destination; hidden by default
        limit=100, details_by_id=BASIC_DETAILS,
    )
    return next(row for row in donor_body["candidates"] if row["asset_ref"] == "donor")


def test_g10_donor_allocation_failure_does_not_change_destination_base_delta() -> None:
    donor_row = _donor_candidate_row()
    assert donor_row["plot_impact"]["reason"] == "cap_allocation_failed"
    assert donor_row["base_pp_delta"] == {
        "status": "point", "value": 1_000.0, "low": 1_000.0, "high": 1_000.0, "reason": None,
    }


def test_g10_plain_add_base_delta_is_point_on_an_over_cap_plot() -> None:
    plain_report = _report(powered=True)
    plain_report["workers"] = []
    plain_report["plots"][0]["staking_facts"]["total_base_pp_cap"] = 500
    plain_report["cards"].append({
        "asset_ref": "plain-over-cap", "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 40001, "land_base_pp": 1_000, "level": 1},
    })
    plain_body = rank_candidates(
        plain_report, "plot-api", "worker", player="player0005", bucket="idle", details_by_id=BASIC_DETAILS,
    )
    plain_row = next(row for row in plain_body["candidates"] if row["asset_ref"] == "plain-over-cap")
    assert plain_row["construction_pp_delta"]["status"] == "point"
    assert plain_row["construction_pp_delta"]["reason"] is None
    assert plain_row["base_pp_delta"] == {
        "status": "point", "value": 1_000.0, "low": 1_000.0, "high": 1_000.0, "reason": None,
    }


def test_g7_items_have_no_raw_base_pp_value() -> None:
    report, details = _kinbound_fixture()
    result = plot_removal_delta(report, DEED, ["totem", "title"], details_by_id=details)
    items = [row for row in result["removed"] if row["kind"] == "item"]
    assert items
    assert all(row["own_base_pp"] is None for row in items)
    assert all(row["own_base_pp"] != 0.0 for row in items)
