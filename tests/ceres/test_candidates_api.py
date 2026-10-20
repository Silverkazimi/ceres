from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

from ceres import capture as capture_mod
from ceres import routes_candidates as routes_candidates_mod
from ceres.candidates import (
    CandidateRow, _eligible_card_rows, _foil, rank_candidates, serialize_candidate, stage_impact,
)
from praetoria.land_reallocate import MovableCard
import pytest
from fastapi import HTTPException
from ceres.routes_candidates import get_candidates


NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)
BASIC_DETAILS = {
    40001: {"id": 40001, "name": "Ordinary", "color": "Red"},
    505: {"id": 505, "name": "Runi", "color": "Red"},
}


def _report(*, powered: bool = False) -> dict:
    deed_uid = "plot-api"
    return {
        "plots": [{
            "deed_uid": deed_uid,
            "facts": {"plot": "1-1-1", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
            "staking_facts": {
                "max_workers_allowed": 5 if powered else 0,
                "total_base_pp_cap": 100000,
                "is_power_core_staked": powered,
                "is_energized": False,
                "is_runi_staked": False,
                "deed_rarity_boost": 0,
                "totem_boost": 0,
                "title_boost": 0,
                "runi_boost": 0,
                "deed_status_token_boost": 0,
                "red_biome_modifier": 0,
                "blue_biome_modifier": 0,
                "white_biome_modifier": 0,
                "black_biome_modifier": 0,
                "green_biome_modifier": 0,
                "gold_biome_modifier": 0,
            },
        }],
        "workers": [{
            "name": "Resident", "card_detail_id": 40001, "land_base_pp": 1000,
            "total_harvest_pp": 0, "asset_ref": "resident", "stake_ref_uid": deed_uid,
            "asset_kind": "worker_card", "slot": 0,
        }],
        "cards": [],
        "unassigned_items": {
            "power_core": {
                "capturable": True,
                "count": 3,
                "items": [{"item_detail_id": 322, "name": "Power Core", "count": 3}],
                "uids": ["I-322-a", "I-322-b", "I-322-c"],
            },
            "totem": {
                "capturable": True,
                "count": 2,
                "items": [{"item_detail_id": 318, "name": "Totem", "count": 2}],
                "uids": ["I-318-a", "I-318-b"],
            },
            "title": {
                "capturable": True,
                "count": 1,
                "items": [{"item_detail_id": 332, "name": "The Proven", "count": 1}],
                "uids": ["I-332-a"],
            },
        },
    }


def _uncleared_capacity_report() -> dict:
    report = _report()
    report["workers"] = []
    report["unassigned_items"]["power_core"]["uids"] = ["I-322-a"]
    return report


def _idle_worker(asset_ref: str) -> dict:
    return {
        "asset_ref": asset_ref,
        "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 40001, "land_base_pp": 1000, "level": 1},
    }


def test_worker_ready_at_uses_stake_end_without_an_extra_cooldown():
    """A worker is ready at its observed stake_end_date, not 78 hours later.

 Mutation check: injected ``ready_at = ended + timedelta(hours=78)``; the exact ready_at assertion went red.
    """
    report = _report(powered=True)
    report["cards"].append({
        "asset_ref": "ended-worker",
        "classification": {"bucket": "stake_expired_or_historical"},
        "facts": {
            "uid": "CARD-END", "card_detail_id": 40001, "land_base_pp": 2000, "level": 1,
            "stake_end_date": "2026-08-23T00:00:00Z",
        },
    })
    body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=datetime(2026, 8, 23, 1, tzinfo=timezone.utc),
        bucket="idle", include_cooling=True, details_by_id=BASIC_DETAILS,
    )
    row = next(row for row in body["candidates"] if row["asset_ref"] == "ended-worker")
    assert row["cooldown"]["ready"] is True
    assert row["cooldown"]["ready_at"] == "2026-08-23T00:00:00Z"


def test_active_unconfirmed_cards_are_visible_only_in_all_as_cooling_rows():
    """An active-but-unconfirmed card is constructed for all, never idle or staked, and uses the normal cooling gate.

 Mutation check: removed ``stake_active_unconfirmed`` from the card-bucket iteration; the all-bucket visibility assertion went red.
    """
    report = _report(powered=True)
    report["cards"].append({
        "asset_ref": "active-unconfirmed",
        "classification": {"bucket": "stake_active_unconfirmed"},
        "facts": {
            "uid": "G-ACTIVE-UNCONFIRMED", "card_detail_id": 40001, "land_base_pp": 2000, "level": 1,
            "stake_ref_uid": "plot-api", "stake_start_date": "2026-08-20T00:00:00Z",
            "stake_end_date": "2026-08-25T00:00:00Z",
        },
    })
    all_rows = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        bucket="all", include_cooling=True, details_by_id=BASIC_DETAILS,
    )
    row = next(row for row in all_rows["candidates"] if row["asset_ref"] == "active-unconfirmed")
    assert row["bucket"] == "stake_active_unconfirmed"
    assert row["cooldown"]["state"] == "cooling"
    assert row["cooldown"]["ready"] is False

    gated = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        bucket="all", details_by_id=BASIC_DETAILS,
    )
    assert "active-unconfirmed" not in {row["asset_ref"] for row in gated["candidates"]}
    assert gated["excluded_counts"]["cooling"] >= 1

    idle_rows = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        bucket="idle", include_cooling=True, details_by_id=BASIC_DETAILS,
    )
    staked_rows = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        bucket="staked", include_cooling=True, details_by_id=BASIC_DETAILS,
    )
    assert "active-unconfirmed" not in {row["asset_ref"] for row in idle_rows["candidates"]}
    assert "active-unconfirmed" not in {row["asset_ref"] for row in staked_rows["candidates"]}


def test_card_uid_twin_dedupe_keeps_unconfirmed_cards_without_staked_twins():
    """Only a non-staked row with a staked card_uid twin is removed.

 The frozen snapshot contains one card emitted by both `_eligible_card_rows` loops,
 one active-unconfirmed card deliberately absent from `report["workers"]`, and two
 rows with no card_uid. Injection 1 (remove the dedupe) returns three non-null rows;
 injection 2 (dedupe by the cooling bucket) removes the twin-free active-unconfirmed
 row. Neither mutation may pass, and missing IDs remain separate rows.
    """
    report = _report(powered=True)
    report["workers"] = [{
        "name": "Staked Runi", "uid": "R-DUP", "card_detail_id": 505,
        "land_base_pp": 1500, "total_harvest_pp": 3000,
        "asset_ref": "staked-runi", "stake_ref_uid": "source-plot",
        "stake_type_uid": "STK-LND-RUNI", "asset_kind": "worker_card", "slot": 0,
    }]
    report["cards"] = [
        {
            "asset_ref": "staked-runi",
            "classification": {"bucket": "stake_active_unconfirmed"},
            "facts": {"uid": "R-DUP", "card_detail_id": 505, "land_base_pp": 1500, "level": 1},
        },
        {
            "asset_ref": "unconfirmed-only",
            "classification": {"bucket": "stake_active_unconfirmed"},
            "facts": {"uid": "R-ONLY", "card_detail_id": 505, "land_base_pp": 1500, "level": 1},
        },
        {
            "asset_ref": "missing-a",
            "classification": {"bucket": "unstaked_apparent"},
            "facts": {"card_detail_id": 505, "land_base_pp": 1500, "level": 1},
        },
        {
            "asset_ref": "missing-b",
            "classification": {"bucket": "unstaked_apparent"},
            "facts": {"card_detail_id": 505, "land_base_pp": 1500, "level": 1},
        },
    ]

    rows, _ = _eligible_card_rows(
        report, BASIC_DETAILS, "player0005", NOW, "runi", "all",
    )

    assert len(rows) == 4
    assert {(row.card.card_uid, row.bucket) for row in rows if row.card.card_uid is not None} == {
        ("R-ONLY", "stake_active_unconfirmed"), ("R-DUP", "staked"),
    }
    assert sum(row.card.card_uid is None for row in rows) == 2
    staked = next(row for row in rows if row.bucket == "staked")
    assert staked.card.source_uid == "source-plot"


@pytest.mark.parametrize("foil", [0, 1, 3, 4])
def test_real_foil_enum_is_exposed_unchanged_in_candidate_payload(foil):
    """A collection card's real foil enum is exposed without collapsing its values.

 Mutation check: replaced ``return source[\"foil\"]`` with ``return 1 if _true(source[\"foil\"]) else 0``; the enum-preservation assertion went red.
    """
    card = MovableCard("Card", "Red", 1000.0, 0.0, "idle", None, card_uid="G-CONFLICTING-UID")
    assert _foil({}, {"foil": foil}, card) == foil


def test_missing_foil_is_unknown_even_when_uid_prefix_suggests_gold():
    """A missing enum is unknown because UID-prefix inference is unsound on real data.

 Mutation check: restored the old ``G``-UID fallback; the unknown-foil assertion went red.
    """
    card = MovableCard("Card", "Red", 1000.0, 0.0, "idle", None, card_uid="G-CONFLICTING-UID")
    assert _foil({}, {}, card) is None


def test_candidate_payload_preserves_card_skin_for_picker_art():
    """A collection card's skin must survive candidate serialization for the picker."""
    card = MovableCard("Lyanna Natura", "Green", 1000.0, 0.0, "idle", None, card_uid="C-SKIN")
    row = CandidateRow(
        card, {}, {"skin": "splinterstorm", "edition": 0}, "unstaked_apparent", "STK-LND-WKR", {"ready": True},
    )

    assert serialize_candidate(row)["skin"] == "splinterstorm"


def test_item_rows_expand_uids_and_join_aggregate_metadata():
    report = _report()
    body = rank_candidates(report, "plot-api", "totem", player="player0005", now=NOW, details_by_id={})
    assert body["pool_size"] == 2
    assert {row["asset_ref"] for row in body["candidates"]} == {"I-318-a", "I-318-b"}
    assert {row["card_uid"] for row in body["candidates"]} == {"I-318-a", "I-318-b"}
    assert {row["card_detail_id"] for row in body["candidates"]} == {318}
    assert {row["level"] for row in body["candidates"]} == {1}
    assert all(row["net_gain_breakdown"] is None for row in body["candidates"])
    assert all(row["plot_impact"]["status"] == "not_applicable" for row in body["candidates"])
    assert all("blocked" not in row and "blocked_reason" not in row for row in body["candidates"])


def test_power_core_item_does_not_claim_an_unmodelled_pp_score():
    body = rank_candidates(_report(), "plot-api", "power_core", player="player0005", now=NOW, details_by_id={})
    assert body["candidates"][0]["net_gain"] is None
    assert body["candidates"][0]["plot_impact"]["status"] == "not_applicable"


def test_staked_move_includes_donor_plot_delta():
    report = _report(powered=True)
    report["workers"][0]["land_base_pp"] = 5000
    donor_uid = "donor-plot"
    report["plots"].append({
        "deed_uid": donor_uid,
        "facts": {"plot": "1-1-2", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
        "staking_facts": {
            "max_workers_allowed": 5, "total_base_pp_cap": 100000,
            "is_power_core_staked": True, "is_energized": False, "is_runi_staked": False,
        },
    })
    report["workers"].extend([
        {
            "name": "Donor", "card_detail_id": 866, "land_base_pp": 1000,
            "total_harvest_pp": 1600, "asset_ref": "donor", "stake_ref_uid": donor_uid,
            "asset_kind": "worker_card", "slot": 0,
        },
        *[
            {
                "name": f"Co-resident {index}", "card_detail_id": 40001, "land_base_pp": 1000,
                "total_harvest_pp": 1600, "asset_ref": f"co-resident-{index}", "stake_ref_uid": donor_uid,
                "asset_kind": "worker_card", "slot": index,
            }
            for index in range(1, 5)
        ],
    ])
    report["cards"].extend([
        {"asset_ref": "donor", "facts": {"card_detail_id": 866, "level": 1}},
        *[
            {"asset_ref": f"co-resident-{index}", "facts": {"card_detail_id": 40001, "level": 1}}
            for index in range(1, 5)
        ],
    ])
    body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        bucket="staked", include_cooling=True, limit=100,
        details_by_id={**BASIC_DETAILS, 866: {"id": 866, "color": "Green", "sub_type": "Human", "stats": {"land_abilities": [[ ["GRAIN", 0.6] ]]}}},
    )
    row = next(row for row in body["candidates"] if row["asset_ref"] == "donor")
    assert row["net_gain_breakdown"]["donor_plot_delta"] == -4000.0
    assert row["net_gain"] == 600.0


def test_all_negative_staked_pool_is_returned_with_advisory_count():
    report = _report(powered=True)
    report["workers"].append({
        "name": "Unproductive donor", "card_detail_id": 40001, "land_base_pp": 1000,
        "total_harvest_pp": 2000, "asset_ref": "negative-donor", "stake_ref_uid": "other-plot",
        "asset_kind": "worker_card", "slot": 0,
    })
    report["plots"].append({
        "deed_uid": "other-plot",
        "facts": {"plot": "1-1-2", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
        "staking_facts": {"max_workers_allowed": 5, "total_base_pp_cap": 100000, "is_power_core_staked": True, "deed_rarity_boost": 1},
    })
    body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        bucket="staked", include_cooling=True, limit=100, details_by_id=BASIC_DETAILS,
    )
    assert body["pool_size"] == 1
    assert body["candidates"][0]["asset_ref"] == "negative-donor"
    assert body["candidates"][0]["net_gain"] < 0
    assert body["excluded_counts"]["negative_net_gain"] == 1


def test_all_negative_default_pool_is_returned_with_advisory_count():
    report = _report(powered=True)
    report["workers"].append({
        "name": "Unproductive donor", "card_detail_id": 40001, "land_base_pp": 1000,
        "total_harvest_pp": 2000, "asset_ref": "negative-donor", "stake_ref_uid": "other-plot",
        "asset_kind": "worker_card", "slot": 0,
    })
    report["plots"].append({
        "deed_uid": "other-plot",
        "facts": {"plot": "1-1-2", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
        "staking_facts": {"max_workers_allowed": 5, "total_base_pp_cap": 100000, "is_power_core_staked": True, "deed_rarity_boost": 1},
    })
    # a card staked on another plot is hidden by default and counted as on_land ...
    hidden = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        include_cooling=True, limit=100, details_by_id=BASIC_DETAILS,
    )
    assert hidden["pool_size"] == 0
    assert hidden["excluded_counts"]["on_land"] == 1
    # ... and the 'On land' chip (include_on_land) brings it back.
    body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        include_cooling=True, include_on_land=True, limit=100, details_by_id=BASIC_DETAILS,
    )
    assert body["pool_size"] == 1
    assert body["candidates"][0]["asset_ref"] == "negative-donor"
    assert body["candidates"][0]["net_gain"] < 0
    assert body["excluded_counts"]["negative_net_gain"] == 1


def test_full_slot_returns_candidates_when_every_swap_is_negative():
    report = _report(powered=True)
    report["workers"] = [
        {
            "name": f"Resident {slot}", "card_detail_id": 40001,
            "land_base_pp": 50000 if slot == 1 else 1000,
            "total_harvest_pp": 50000 if slot == 1 else 1000,
            "asset_ref": f"resident-{slot}", "stake_ref_uid": "plot-api",
            "asset_kind": "worker_card", "slot": slot,
        }
        for slot in range(1, 6)
    ]
    report["cards"].append({
        "asset_ref": "weaker-idle", "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 40001, "land_base_pp": 100, "level": 1},
    })
    body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        bucket="idle", include_cooling=True, limit=100, slot=0, details_by_id=BASIC_DETAILS,
    )
    assert body["pool_size"] == 1
    assert body["candidates"][0]["asset_ref"] == "weaker-idle"
    assert body["candidates"][0]["evicted"]["asset_ref"] == "resident-1"
    assert body["candidates"][0]["net_gain"] < 0
    assert body["excluded_counts"]["negative_net_gain"] == 1


def test_show_everything_preset_returns_the_full_negative_pool():
    report = _report(powered=True)
    report["workers"].append({
        "name": "Unproductive donor", "card_detail_id": 40001, "land_base_pp": 1000,
        "total_harvest_pp": 2000, "asset_ref": "negative-donor", "stake_ref_uid": "other-plot",
        "asset_kind": "worker_card", "slot": 0,
    })
    report["plots"].append({
        "deed_uid": "other-plot",
        "facts": {"plot": "1-1-2", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
        "staking_facts": {"max_workers_allowed": 5, "total_base_pp_cap": 100000, "is_power_core_staked": True, "deed_rarity_boost": 1},
    })
    body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        preset="everything", include_cooling=True, include_rented_in=True, limit=100, details_by_id=BASIC_DETAILS,
    )
    assert body["preset"] == {"id": "everything", "label": "Show everything", "applied": True}
    assert body["pool_size"] == 1
    assert [row["asset_ref"] for row in body["candidates"]] == ["negative-donor"]
    assert body["excluded_counts"]["negative_net_gain"] == 1


def test_staked_runi_donor_loses_power_gate_and_runi_multiplier():
    report = _report(powered=True)
    report["plots"][0]["staking_facts"].update({
        "is_power_core_staked": False, "is_runi_staked": False, "runi_boost": 0,
    })
    donor_uid = "runi-donor"
    report["plots"].append({
        "deed_uid": donor_uid,
        "facts": {"plot": "1-1-2", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
        "staking_facts": {
            "max_workers_allowed": 5, "total_base_pp_cap": 100000,
            "is_power_core_staked": False, "is_energized": False, "is_runi_staked": True,
            "runi_boost": 1,
        },
    })
    report["workers"].append({
        "name": "Runi", "card_detail_id": 505, "land_base_pp": 1500,
        "total_harvest_pp": 3000, "asset_ref": "runi", "stake_ref_uid": donor_uid,
        "stake_type_uid": "STK-LND-RUNI", "asset_kind": "worker_card", "slot": 0,
    })
    report["workers"].append({
        "name": "Runi donor resident", "card_detail_id": 40001, "land_base_pp": 1000,
        "total_harvest_pp": 2000, "asset_ref": "runi-resident", "stake_ref_uid": donor_uid,
        "asset_kind": "worker_card", "slot": 1,
    })
    report["cards"].extend([
        {"asset_ref": "runi", "facts": {"card_detail_id": 505, "level": 1}},
        {"asset_ref": "runi-resident", "facts": {"card_detail_id": 40001, "level": 1}},
        {"asset_ref": "untracked-runi", "facts": {"card_detail_id": 505, "level": 1}},
    ])
    report["workers"].append({
        "name": "Untracked Runi", "card_detail_id": 505, "land_base_pp": 1500,
        "total_harvest_pp": 0, "asset_ref": "untracked-runi", "stake_ref_uid": "untracked-source",
        "stake_type_uid": "STK-LND-RUNI", "asset_kind": "worker_card", "slot": 0,
    })
    body = rank_candidates(
        report, "plot-api", "runi", player="player0005", now=NOW,
        bucket="staked", include_cooling=True, limit=100, details_by_id=BASIC_DETAILS,
    )
    row = next(row for row in body["candidates"] if row["asset_ref"] == "runi")
    assert row["net_gain_breakdown"]["donor_plot_delta"] == -5000.0
    assert row["net_gain"] == -4000.0


def test_runi_idle_picker_never_returns_ordinary_worker_cards():
    """ finding 2: `_eligible_card_rows`'s idle-cards loop iterated
 `report["cards"]` once for BOTH `worker` and `runi` groups and called `add(facts,
 None, bucket)` unconditionally -- since `worker is None` for every idle card, the
 `group_id == "runi" and stake_type_uid != wanted_type` filter never applied (it was
 gated on `worker is not None`), and `add()` itself defaulted the unknown
 `stake_type_uid` to whatever the CALLER asked for. So an ordinary idle monster card
 (no `card_detail_id` identifying it as the real Runi, 505) leaked into the `runi`
 picker's pool and was labelled `STK-LND-RUNI`. This fixture has ONE idle card and it
 is NOT a Runi -- the `runi` picker must return zero candidates for it, while the
 `worker` picker still returns it."""
    report = _report()
    report["cards"].append({
        "asset_ref": "idle-ordinary", "classification": {"bucket": "unstaked_apparent"},
        "facts": {
            "card_detail_id": 40001, "land_base_pp": 800, "player": "player0005",
            "market_id": None, "delegated_to": None, "rental_type": None, "unlock_date": None,
        },
    })
    runi_body = rank_candidates(
        report, "plot-api", "runi", player="player0005", now=NOW,
        bucket="idle", include_cooling=True, limit=100, details_by_id=BASIC_DETAILS,
    )
    assert not any(row["asset_ref"] == "idle-ordinary" for row in runi_body["candidates"])
    worker_body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        bucket="idle", include_cooling=True, limit=100, details_by_id=BASIC_DETAILS,
    )
    assert any(row["asset_ref"] == "idle-ordinary" for row in worker_body["candidates"])


def test_unpowered_worksite_still_returns_worker_candidates():
    report = _report()
    report["plots"][0]["facts"]["worksite_type"] = "Grain Farm"
    report["plots"][0]["worksite_facts"] = {"project_type": "worksite_grain"}
    report["workers"] = []
    report["cards"].append({
        "asset_ref": "idle-capacity", "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 40001, "land_base_pp": 1000, "level": 1},
    })
    body = rank_candidates(report, "plot-api", "worker", player="player0005", now=NOW, bucket="idle", details_by_id={})
    assert {row["asset_ref"] for row in body["candidates"]} == {"idle-capacity"}


def test_uncleared_plot_staged_worksite_and_power_core_offer_workers_at_fixed_five_capacity():
    report = _uncleared_capacity_report()
    report["cards"].append(_idle_worker("idle-capacity"))
    moves = [{
        "deed_uid": "plot-api", "action": "stake", "group_id": "power_core",
        "asset_ref": "I-322-a", "slot_index": 0,
    }]

    body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW, bucket="idle",
        details_by_id=BASIC_DETAILS, staged_moves=moves, staged_worksite_type="grain",
    )

    assert {row["asset_ref"] for row in body["candidates"]} == {"idle-capacity"}
    assert body["excluded_counts"].get("capacity", 0) == 0


def test_runi_only_slot_five_worker_contributes_zero_pp():
    report = _report()
    report["plots"][0]["facts"]["worksite_type"] = "Grain Farm"
    report["plots"][0]["worksite_facts"] = {"project_type": "worksite_grain"}
    report["plots"][0]["staking_facts"].update({
        "max_workers_allowed": 4,
        "worker_count": 5,
        "is_power_core_staked": False,
        "is_energized": False,
        "is_runi_staked": True,
    })
    report["workers"] = [
        {
            "name": f"Worker {slot}", "card_detail_id": 40001, "land_base_pp": 1000,
            "total_harvest_pp": 1000, "asset_ref": f"worker-{slot}",
            "stake_ref_uid": "plot-api", "stake_type_uid": "STK-LND-WKR",
            "asset_kind": "worker_card", "slot": slot,
        }
        for slot in range(1, 5)
    ]
    report["workers"].append({
        "name": "Runi", "card_detail_id": 505, "land_base_pp": 1500,
        "total_harvest_pp": 1500, "asset_ref": "runi", "stake_ref_uid": "plot-api",
        "stake_type_uid": "STK-LND-RUNI", "asset_kind": "runi_worker_card", "slot": 0,
    })
    report["cards"].append(_idle_worker("fifth-worker"))

    four_powered = stage_impact(report, "plot-api", [], details_by_id=BASIC_DETAILS, force_full_power=True)
    with_unpowered_fifth = stage_impact(report, "plot-api", [{
        "deed_uid": "plot-api", "group_id": "worker", "action": "stake",
        "asset_ref": "fifth-worker", "slot_index": 4,
    }], details_by_id=BASIC_DETAILS)

    assert with_unpowered_fifth["resulting_harvest"] == pytest.approx(four_powered["resulting_harvest"])


def test_omitted_and_explicit_empty_staged_moves_are_equivalent():
    report = _uncleared_capacity_report()
    report["cards"].append(_idle_worker("idle-capacity"))

    implicit = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW, bucket="idle", details_by_id=BASIC_DETAILS,
    )
    explicit = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW, bucket="idle", details_by_id=BASIC_DETAILS,
        staged_moves=[], staged_worksite_type=None,
    )

    assert explicit == implicit


@pytest.mark.parametrize("move", [
    {"deed_uid": "plot-api", "action": "stake", "group_id": "power_core", "asset_ref": "wrong-kind", "slot_index": 0},
    {"deed_uid": "plot-api", "action": "unstake", "group_id": "power_core", "asset_ref": "resident", "slot_index": 0},
])
def test_staged_asset_type_must_match_move_group(move):
    report = _report()
    report["cards"].append(_idle_worker("wrong-kind"))

    with pytest.raises(ValueError, match="has stake type STK-LND-WKR, not power_core"):
        rank_candidates(
            report, "plot-api", "worker", player="player0005", now=NOW,
            details_by_id=BASIC_DETAILS, staged_moves=[move],
        )


def test_staged_workers_are_excluded_and_occupy_projected_slots():
    report = _uncleared_capacity_report()
    report["cards"].extend([_idle_worker("staged-worker-1"), _idle_worker("staged-worker-2"), _idle_worker("candidate-worker")])
    core = {"deed_uid": "plot-api", "action": "stake", "group_id": "power_core", "asset_ref": "I-322-a", "slot_index": 0}
    worker_one = {"deed_uid": "plot-api", "action": "stake", "group_id": "worker", "asset_ref": "staged-worker-1", "slot_index": 0}
    worker_two = {"deed_uid": "plot-api", "action": "stake", "group_id": "worker", "asset_ref": "staged-worker-2", "slot_index": 1}

    two_workers = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW, bucket="idle", details_by_id=BASIC_DETAILS,
        staged_moves=[core, worker_one, worker_two], staged_worksite_type="grain", slot=0,
        reserved_asset_refs={"I-322-a"},
    )

    assert {row["asset_ref"] for row in two_workers["candidates"]} == {"candidate-worker"}
    assert {row["evicted"]["asset_ref"] for row in two_workers["candidates"]} == {"staged-worker-1"}


def test_reserved_asset_is_removed_before_candidate_pagination_and_returns_when_released():
    report = _report(powered=True)
    report["workers"] = []
    report["cards"].append({
        "asset_ref": "physical-card", "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 40001, "land_base_pp": 1000, "level": 1},
    })
    reserved = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW, bucket="idle",
        reserved_asset_refs={"physical-card"}, details_by_id={},
    )
    released = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW, bucket="idle",
        reserved_asset_refs=set(), details_by_id={},
    )
    assert reserved["candidates"] == []
    assert [row["asset_ref"] for row in released["candidates"]] == ["physical-card"]


def test_base_cap_candidate_is_point_valued_and_counted_as_point():
    report = _report(powered=True)
    report["workers"][0]["slot"] = 1
    report["plots"][0]["staking_facts"]["total_base_pp_cap"] = 500
    report["cards"].append({
        "asset_ref": "idle-base-cap", "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 40001, "land_base_pp": 1000, "level": 1},
    })
    body = rank_candidates(report, "plot-api", "worker", player="player0005", now=NOW, bucket="idle", details_by_id=BASIC_DETAILS)
    assert body["pool_size"] == 1
    assert body["excluded_counts"]["base_cap"] == 0
    assert body["estimate_counts"]["point"] == 1
    assert body["estimate_counts"]["unknown"] == 0
    assert body["candidates"][0]["construction_pp"]["status"] == "point"
    assert body["candidates"][0]["construction_pp_delta"]["status"] == "point"


def test_negative_terrain_penalty_flows_through_construction_pp_delta_signed():
    # item 40 -- a terrain mismatch is a real, SIGNED penalty (the internal rules); the
    # Base-PP-stage delta must carry that sign through, not silently drop it.
    report = _report(powered=True)
    report["plots"][0]["staking_facts"]["red_biome_modifier"] = -0.5
    report["cards"].append({
        "asset_ref": "penalized-idle", "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 40001, "land_base_pp": 2000, "level": 1},
    })
    # R-89/R-90 and / 564e1b3b: penalised workers are opt-in candidates.
    body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW, bucket="idle",
        details_by_id=BASIC_DETAILS, include_terrain_penalised=True,
    )
    row = next(r for r in body["candidates"] if r["asset_ref"] == "penalized-idle")
    assert row["terrain_boost"] == pytest.approx(-0.5)
    assert row["construction_pp"]["status"] == "point"
    assert row["construction_pp"]["value"] == pytest.approx(1000.0)
    # A plain add into an empty slot has no occupant to net against, so the delta equals
    # the candidate's own construction PP -- and it must still be positive here (a
    # terrain PENALTY reduces the card's own value; it never flips the sign of adding it).
    assert row["construction_pp_delta"]["status"] == "point"
    assert row["construction_pp_delta"]["value"] == pytest.approx(1000.0)


def test_construction_pp_delta_nets_evicted_occupant_and_stays_distinct_from_total_pp_delta():
    # item 40 -- the checkout's Base PP (stage one) and Total PP (stage two)
    # figures must never be collapsed into one number. A swap's Base-PP-stage delta nets
    # the incoming card's own construction PP against the evicted occupant's own observed
    # construction PP; the Total-PP-stage delta (net_gain, the existing figure) is a wholly
    # different quantity computed over the harvest-stage broadcast model.
    report = _report(powered=True)
    report["plots"][0]["staking_facts"]["max_workers_allowed"] = 1
    report["workers"] = [{
        "name": "Resident", "card_detail_id": 40001, "land_base_pp": 1000,
        "total_harvest_pp": 4000, "total_construction_pp": 1200,
        "asset_ref": "resident", "stake_ref_uid": "plot-api",
        "asset_kind": "worker_card", "slot": 1,
    }]
    report["cards"].append({
        "asset_ref": "idle-swap", "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 40001, "land_base_pp": 2000, "level": 1},
    })
    body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW,
        bucket="idle", slot=0, details_by_id=BASIC_DETAILS,
    )
    row = next(r for r in body["candidates"] if r["asset_ref"] == "idle-swap")
    assert row["evicted"]["asset_ref"] == "resident"
    assert row["construction_pp"]["value"] == pytest.approx(2000.0)  # candidate's own, no terrain
    # 2000 (incoming) - 1200 (evicted occupant's own observed construction PP) = 800
    assert row["construction_pp_delta"]["status"] == "point"
    assert row["construction_pp_delta"]["value"] == pytest.approx(800.0)
    # The harvest-stage net_gain is a genuinely different figure -- proves the two
    # stages were never collapsed into one number.
    assert row["net_gain"] is not None
    assert row["net_gain"] != pytest.approx(row["construction_pp_delta"]["value"])


def test_cooldown_free_preset_remains_cooldown_free_with_include_cooling():
    report = _report(powered=True)
    report["cards"].append({
        "asset_ref": "cooldown-preset", "classification": {"bucket": "stake_expired_or_historical"},
        "facts": {"card_detail_id": 40001, "land_base_pp": 1000, "level": 1, "stake_end_date": "2026-08-25T00:00:00Z"},
    })
    body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, preset="cooldown_free_only", details_by_id={},
    )
    assert "cooldown-preset" not in {row["asset_ref"] for row in body["candidates"]}
    assert body["excluded_counts"]["cooling"] == 1


def test_survival_locked_cards_have_a_distinct_exclusion_count():
    report = _report(powered=True)
    report["cards"].append({
        "asset_ref": "survival-locked", "classification": {"bucket": "unstaked_apparent"},
        "facts": {
            "uid": "SURVIVAL-1", "card_detail_id": 40001, "land_base_pp": 1000, "level": 1,
            "survival_date": "2026-08-27T00:00:00Z",
        },
    })
    body = rank_candidates(
        report, "plot-api", "worker", player="player0005", now=NOW, bucket="idle",
        details_by_id=BASIC_DETAILS,
    )
    assert "survival-locked" not in {row["asset_ref"] for row in body["candidates"]}
    assert body["excluded_counts"]["survival_locked"] == 1
    assert body["excluded_counts"]["cooling"] == 0


def test_serialize_candidate_names_the_evicted_card():
    card = MovableCard("Replacement", "Red", 1000.0, 0.0, "idle", None, asset_ref="replacement")
    row = CandidateRow(
        card, {}, {}, "unstaked_apparent", "STK-LND-WKR", {"ready": True},
        breakdown={"evicted": {"name": "Resident", "asset_ref": "resident"}},
        evicted={"name": "Resident", "asset_ref": "resident"},
    )
    assert serialize_candidate(row)["evicted"]["name"] == "Resident"


def test_idle_pool_accounts_for_zero_base_rows(report, details_by_id):
    report["cards"].append({
        "asset_ref": "missing-base",
        "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 2, "land_base_pp": "0.000", "level": 1},
    })
    deed_uid = report["plots"][0]["deed_uid"]
    body = rank_candidates(
        report, deed_uid, "worker", player="player0005", now=NOW, bucket="idle", include_cooling=True,
        limit=100, details_by_id=details_by_id,
    )
    idle_total = sum(
        row.get("classification", {}).get("bucket") in {"unstaked_apparent", "stake_expired_or_historical"}
        for row in report["cards"]
    )
    accounted = body["pool_size"] + sum(body["excluded_counts"].values())
    assert body["excluded_counts"]["missing_base"] >= 1
    assert accounted == idle_total


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(player="player0005"))))


def test_unknown_deed_is_not_upstream_failure(monkeypatch, report):
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    with pytest.raises(HTTPException) as raised:
        get_candidates("NOPE", _request(), group_id="worker")
    assert raised.value.status_code == 404


@pytest.mark.parametrize("staged", [
    {"staged_moves": "{"},
    {"staged_worksite": "grain", "staged_worksite_type": "wood"},
])
def test_unknown_deed_precedes_staged_plan_validation(monkeypatch, report, staged):
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    with pytest.raises(HTTPException) as raised:
        get_candidates("NOPE", _request(), group_id="worker", **staged)
    assert raised.value.status_code == 404


def test_route_passes_valid_staged_plan_and_plain_none_defaults(monkeypatch, report):
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    calls = []
    deed_uid = report["plots"][0]["deed_uid"]

    def fake_rank(report_arg, deed_uid, group_id, **kwargs):
        calls.append((report_arg, deed_uid, group_id, kwargs))
        return {"candidates": []}

    monkeypatch.setattr(routes_candidates_mod, "rank_candidates", fake_rank)
    get_candidates(deed_uid, _request(), group_id="worker", limit=25, offset=0)
    move = {
        "deed_uid": deed_uid, "action": "stake", "group_id": "power_core",
        "asset_ref": "I-322-a", "slot_index": 0,
    }
    other_deed_move = {
        "deed_uid": "other-plot", "action": "stake", "group_id": "worker",
        "asset_ref": "other-worker", "slot_index": 0,
    }
    other_deed_moves = [
        {**other_deed_move, "asset_ref": f"other-worker-{index}"}
        for index in range(51)
    ]
    get_candidates(
        deed_uid, _request(), group_id="worker", limit=25, offset=0,
        staged_moves=json.dumps([*other_deed_moves, move]), staged_worksite="grain",
    )

    assert calls[0][3]["staged_moves"] is None
    assert calls[0][3]["staged_worksite_type"] is None
    assert calls[1][3]["staged_moves"] == [move]
    assert calls[1][3]["staged_worksite_type"] == "grain"


@pytest.mark.parametrize("group_id", ["worker", "runi", "title", "totem"])
def test_every_candidate_picker_accepts_the_account_staged_plan(monkeypatch, group_id):
    """a picker must tolerate staged entries for the other asset groups."""
    report = _report(powered=True)
    report.pop("cards")
    if group_id in {"worker", "runi"}:
        def hydrate_card_pool(snapshot):
            hydrated = dict(snapshot)
            hydrated["cards"] = [_idle_worker("staged-worker")]
            return hydrated

        monkeypatch.setattr(capture_mod, "load_card_pool", hydrate_card_pool)
    moves = [
        {
            "deed_uid": "plot-api", "action": "stake", "group_id": "worker",
            "asset_ref": "staged-worker", "slot_index": 0,
        },
        {
            "deed_uid": "plot-api", "action": "stake", "group_id": "totem",
            "asset_ref": "I-318-a", "slot_index": 0,
        },
    ]
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    monkeypatch.setattr(routes_candidates_mod, "load_details_by_id", lambda: BASIC_DETAILS)

    response = get_candidates(
        "plot-api", _request(), group_id=group_id,
        staged_moves=json.dumps(moves), limit=25, offset=0,
    )

    assert response.status_code == 200


def test_slot_query_is_reflected_in_target_slot(monkeypatch, report):
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    response = get_candidates(report["plots"][0]["deed_uid"], _request(), group_id="worker", slot=3, limit=25, offset=0)
    assert response.status_code == 200
    assert response.body
    assert b'"target_slot_id":"worker:3"' in response.body


def test_item_candidates_resolve_catalogue_names_and_effects_from_old_snapshot():
    report = _report()
    report["unassigned_items"]["totem"]["items"] = [
        {"item_detail_id": 319, "name": "Item 319", "count": 1},
        {"item_detail_id": 318, "name": "Item 318", "count": 1},
    ]
    report["unassigned_items"]["totem"]["uids"] = ["I-319-a", "I-318-a"]
    body = rank_candidates(report, "plot-api", "totem", player="player0005", now=NOW, details_by_id={})
    by_id = {row["item_detail_id"]: row for row in body["candidates"]}
    assert by_id[318]["name"] == "Common Totem"
    assert by_id[319]["name"] == "Rare Totem"
    assert by_id[318]["boost"] == 0.1
    assert by_id[319]["boost"] == 0.25
    assert body["source"] == "Available item catalogue"
    assert by_id[318]["asset_kind"] == "item"


@pytest.mark.parametrize("secondary", [0.0, 0.1, -0.25])
@pytest.mark.parametrize("sort", ["construction_pp", "net_gain"])
@pytest.mark.parametrize("reverse", [False, True])
def test_dual_splinter_uses_best_terrain_for_filter_and_pp(secondary, sort, reverse):
    report = _report(powered=True)
    report["plots"][0]["staking_facts"].update(red_biome_modifier=-0.5, blue_biome_modifier=secondary)
    report["cards"] = [_idle_worker("dual-worker")]
    details = {**BASIC_DETAILS, 40001: {**BASIC_DETAILS[40001], "color": "Blue" if reverse else "Red", "secondary_color": "Red" if reverse else "Blue"}}
    body = rank_candidates(report, "plot-api", "worker", player="player0005", now=NOW,
                           bucket="idle", details_by_id=details, sort=sort)
    matches = [row for row in body["candidates"] if row["asset_ref"] == "dual-worker"]
    if secondary < 0:
        assert not matches
        body = rank_candidates(report, "plot-api", "worker", player="player0005", now=NOW,
                               bucket="idle", details_by_id=details, sort=sort, include_terrain_penalised=True)
        matches = [row for row in body["candidates"] if row["asset_ref"] == "dual-worker"]
    assert len(matches) == 1
    assert matches[0]["terrain_boost"] == pytest.approx(secondary)
    assert matches[0]["construction_pp"]["value"] == pytest.approx(1000 * (1 + secondary))
