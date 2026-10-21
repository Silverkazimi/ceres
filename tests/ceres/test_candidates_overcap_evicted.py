"""R-103 guards for over-cap candidate eviction and point projections.

The fixture was extracted from ``~/.ceres/snapshots/land-player0005-latest.json`` at
``2026-09-03T20:33:43.925765Z`` for deed ``I-295-116f9bb372e311``. The plan labels that
capture ``2026-09-03T22:33Z``; the source file's UTC ``generated_at`` is authoritative and
the discrepancy is recorded in the fixture's ``provenance`` object. It is committed because
the source snapshot churns; card names and splinters come from the committed
``card_details_sample.json`` (``build_details_by_id()``) plus the one catalogue entry it
lacks, never from a machine-local ``card_details`` cache (``load_details_by_id()``), which
CI does not have.

G1 injection: make the scored evaluator report an unknown plot impact; the point-valued
occupied-click contract must go red.
G2 injection: make the evaluator use the candidate's raw base instead of subtracting the
clicked occupant; the API symmetry assertion must go red.
G3 injection: remove ``construction_pp`` from the evaluator's scored eviction record; the
complete record-shape assertion must go red.
G4 injection: replace the whole-roster scorer's selected asset with ``guessed-occupant``;
the full-plot API must reject an eviction that is absent from the actual roster.
G5 retains the donor-terrain suppression invariant: the destination eviction remains named.
G6 retains the real-slot gap invariant: an empty click is a plain add with no eviction.
G7 injection: restore ``or resulting_over_cap`` to the evaluator's suppression branch;
the clipped point projection becomes unknown. The maintainer-gated Phase-2 ruling is now active.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import ceres.candidates as candidates_module
from ceres.candidates import rank_candidates
from tests.ceres.conftest import build_details_by_id


FIXTURE = Path(__file__).parents[1] / "fixtures" / "land" / "candidates_overcap_41_2_27.json"
DEED_UID = "I-295-116f9bb372e311"
NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)
ZINTAR_REF = "66c199db40dd569f40270aeb"


def _patch_evaluator(monkeypatch, mutation):
    original = candidates_module.evaluate_candidates_for_plot
    seen = {"hit": False}

    def injected(*args, **kwargs):
        evaluations = original(*args, **kwargs)
        result = []
        for evaluation in evaluations:
            if evaluation.card.asset_ref == ZINTAR_REF:
                seen["hit"] = True
                evaluation = mutation(evaluation)
            result.append(evaluation)
        return result

    monkeypatch.setattr(candidates_module, "evaluate_candidates_for_plot", injected)
    return seen


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _details() -> dict[int, dict]:
    # Every card in the fixture except the second idle candidate (card_detail_id 22) is in the
    # committed sample. Its catalogue identity (Alpha "Frost Giant", Water splinter) is the
    # only part of the card's detail the ranking reads here: the splinter decides the terrain
    # modifier, without which the candidate is unscored ("terrain_modifier_unknown").
    details = build_details_by_id()
    details.setdefault(22, {"id": 22, "name": "Frost Giant", "color": "Blue", "type": "Monster", "editions": "0,1"})
    return details


def _rows(report: dict, *, slot: int | None) -> list[dict]:
    return rank_candidates(
        report, DEED_UID, "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, include_terrain_penalised=True, limit=100,
        details_by_id=_details(), slot=slot,
    )["candidates"]


def _assert_g1(rows: list[dict]) -> None:
    zintar = next(row for row in rows if row["name"] == "Zintar Mortalis")
    assert zintar["plot_impact"]["status"] == "point"
    assert zintar["net_gain"] is not None
    assert zintar["base_pp_delta"]["value"] == pytest.approx(29_941.176)
    assert zintar["evicted"]["name"] == "Undying Miner"


def test_g1_scored_occupied_click_names_its_eviction() -> None:
    rows = _rows(_fixture(), slot=4)
    _assert_g1(rows)


def test_g1_scored_impact_mutation_is_red(monkeypatch) -> None:
    """Inject an unknown scored impact; the point-valued occupied-click assertion must fail."""
    def lose_impact(evaluation):
        return replace(
            evaluation,
            plot_impact={"status": "unknown", "value": None, "low": None, "high": None, "reason": "injected"},
            plot_impact_breakdown=None,
        )

    seen = _patch_evaluator(monkeypatch, lose_impact)
    with pytest.raises(AssertionError):
        _assert_g1(_rows(_fixture(), slot=4))
    assert seen["hit"]


def _assert_g2(rows: list[dict]) -> None:
    assert rows
    for row in rows:
        delta = row["base_pp_delta"]
        assert (row["evicted"] is not None) == (
            delta["status"] == "point" and delta["value"] != row["base_pp"]
        )


def test_g2_eviction_and_netted_base_delta_are_symmetric() -> None:
    rows = _rows(_fixture(), slot=4)
    _assert_g2(rows)


def test_g2_raw_base_delta_mutation_is_red(monkeypatch) -> None:
    """Inject an un-netted raw base delta; the eviction/delta symmetry assertion must fail."""
    def stop_netting(evaluation):
        value = evaluation.card.base
        return replace(
            evaluation,
            base_pp_delta={"status": "point", "value": value, "low": value, "high": value, "reason": None},
        )

    seen = _patch_evaluator(monkeypatch, stop_netting)
    with pytest.raises(AssertionError):
        _assert_g2(_rows(_fixture(), slot=4))
    assert seen["hit"]


def test_g3_scored_eviction_record_matches_across_candidates() -> None:
    rows = _rows(_fixture(), slot=4)
    zintar = next(row for row in rows if row["name"] == "Zintar Mortalis")
    frost = next(row for row in rows if row["name"] == "Frost Giant")

    assert zintar["evicted"] == frost["evicted"]
    assert list(zintar["evicted"]) == ["asset_ref", "name", "cur", "base", "construction_pp"]


def test_g3_scored_record_shape_mutation_is_red(monkeypatch) -> None:
    """Inject a scored eviction record without construction PP; the record-shape assertion must fail."""
    def drop_construction(evaluation):
        breakdown = dict(evaluation.plot_impact_breakdown or {})
        evicted = dict(breakdown["evicted"])
        evicted.pop("construction_pp")
        breakdown["evicted"] = evicted
        return replace(evaluation, plot_impact_breakdown=breakdown)

    seen = _patch_evaluator(monkeypatch, drop_construction)
    with pytest.raises(AssertionError):
        rows = _rows(_fixture(), slot=4)
        zintar = next(row for row in rows if row["name"] == "Zintar Mortalis")
        assert list(zintar["evicted"]) == ["asset_ref", "name", "cur", "base", "construction_pp"]
    assert seen["hit"]


def _assert_g4(report: dict, rows: list[dict]) -> None:
    roster_refs = {
        worker["asset_ref"] for worker in report["workers"] if worker.get("asset_kind") == "worker_card"
    }
    assert rows
    assert all(row["evicted"]["asset_ref"] in roster_refs for row in rows)


def _full_plot_report(monkeypatch) -> dict:
    """The fixture plot holds four worker cards. A worksite's picker capacity is its structural
    slot count (``ceres.capacity.WORKSITE_WORKER_CAPACITY``), not the live ``max_workers_allowed``,
    so the plot is made full by giving the structure four slots; a whole-roster (slot-less) pick
    then has to choose a real occupant to evict."""
    import ceres.capacity as capacity_module

    monkeypatch.setattr(capacity_module, "WORKSITE_WORKER_CAPACITY", 4)
    report = _fixture()
    report["plots"][0]["staking_facts"]["max_workers_allowed"] = 4
    return report


def test_g4_whole_roster_selection_never_guesses_an_eviction(monkeypatch) -> None:
    report = _full_plot_report(monkeypatch)
    rows = _rows(report, slot=None)
    _assert_g4(report, rows)


def test_g4_whole_roster_guess_mutation_is_red(monkeypatch) -> None:
    """Inject a guessed whole-roster victim; the actual-roster assertion must fail."""
    def guess_eviction(evaluation):
        breakdown = dict(evaluation.plot_impact_breakdown or {})
        evicted = dict(breakdown["evicted"])
        evicted["asset_ref"] = "guessed-occupant"
        breakdown["evicted"] = evicted
        return replace(evaluation, plot_impact_breakdown=breakdown)

    report = _full_plot_report(monkeypatch)
    seen = _patch_evaluator(monkeypatch, guess_eviction)
    with pytest.raises(AssertionError):
        _assert_g4(report, _rows(report, slot=None))
    assert seen["hit"]


def _donor_terrain_unknown_report() -> tuple[dict, dict]:
    target = {
        "deed_uid": "target",
        "facts": {"plot": "1-1-1", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
        "staking_facts": {
            "max_workers_allowed": 5, "total_base_pp_cap": 100000, "is_power_core_staked": True,
            "is_energized": False, "is_runi_staked": False,
            "red_biome_modifier": 0, "blue_biome_modifier": 0, "white_biome_modifier": 0,
            "black_biome_modifier": 0, "green_biome_modifier": 0, "gold_biome_modifier": 0,
        },
    }
    donor = copy.deepcopy(target)
    donor["deed_uid"] = "donor"
    donor["facts"]["plot"] = "1-1-2"
    return {
        "plots": [target, donor],
        "workers": [
            {"name": "Resident", "card_detail_id": 40001, "land_base_pp": 1000, "total_harvest_pp": 0, "asset_ref": "resident", "stake_ref_uid": "target", "asset_kind": "worker_card", "slot": 1},
            {"name": "Donor", "card_detail_id": 40001, "land_base_pp": 1000, "total_harvest_pp": 0, "asset_ref": "donor", "stake_ref_uid": "donor", "asset_kind": "worker_card", "slot": 1},
            {"name": "Unmapped", "card_detail_id": 49999, "land_base_pp": 1000, "total_harvest_pp": 0, "asset_ref": "unmapped", "stake_ref_uid": "donor", "asset_kind": "worker_card", "slot": 2},
        ],
        "cards": [
            {"asset_ref": "donor", "classification": {"bucket": "stake_active_unconfirmed"}, "facts": {"card_detail_id": 40001, "level": 1, "land_base_pp": 1000}},
            {"asset_ref": "unmapped", "classification": {"bucket": "stake_active_unconfirmed"}, "facts": {"card_detail_id": 49999, "level": 1, "land_base_pp": 1000}},
        ],
        "assignments": [], "unassigned_items": [],
    }, {40001: {"id": 40001, "name": "Ordinary", "color": "Red"}}


def test_g5_donor_suppression_does_not_suppress_destination_eviction() -> None:
    report, details = _donor_terrain_unknown_report()
    row = next(row for row in rank_candidates(
        report, "target", "worker", player="player0005", now=NOW, bucket="staked",
        include_cooling=True, include_terrain_penalised=True, limit=100,
        details_by_id=details, slot=0,
    )["candidates"] if row["asset_ref"] == "donor")

    assert row["plot_impact"]["reason"] == "donor_terrain_modifier_unknown"
    assert row["evicted"]["name"] == "Resident"


def test_g6_gap_slot_is_a_plain_add() -> None:
    rows = _rows(_fixture(), slot=1)

    assert rows
    for row in rows:
        assert row["evicted"] is None
        assert row["base_pp_delta"]["value"] == pytest.approx(row["base_pp"])


def test_g7_overcap_row_carries_independently_derived_clipped_point() -> None:
    report = _fixture()
    workers = [dict(worker) for worker in report["workers"] if worker.get("asset_kind") != "runi_worker_card"]
    occupant = next(worker for worker in workers if worker["slot"] == 5)
    candidate = next(card for card in report["cards"] if card["asset_ref"] == ZINTAR_REF)
    resulting = [worker for worker in workers if worker["asset_ref"] != occupant["asset_ref"]]
    resulting.append({"asset_ref": candidate["asset_ref"], "base": float(candidate["facts"]["land_base_pp"]), "slot": 5})
    excess = max(
        0.0,
        sum(float(worker["base"] if "base" in worker else worker["land_base_pp"]) for worker in resulting) - 100000.0,
    )
    expected = {}
    for worker in sorted(resulting, key=lambda row: row["slot"], reverse=True):
        base = float(worker["base"] if "base" in worker else worker["land_base_pp"])
        taken = min(base, excess)
        expected[worker["asset_ref"]] = base - taken
        excess -= taken

    rows = _rows(report, slot=4)
    zintar = next(row for row in rows if row["asset_ref"] == candidate["asset_ref"])
    assert zintar["plot_impact"]["status"] == "point"
    assert zintar["net_gain_breakdown"]["candidate_base_pp_after_cap"] == pytest.approx(expected[candidate["asset_ref"]])
