from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ceres import routes_candidates
from ceres.candidates import plot_removal_delta


DEED = "I-294-99ed4b7a4d170e"
KINBOUND_REF = "c0d1a943864eabe0455adbfd"


def _kinbound_fixture() -> tuple[dict, dict]:
    workers = [
        ("Lyanna Natura", "lyanna", 27, 32941.176, 36235.294, 166682.351, "STK-LND-WKR"),
        ("Silver Shield Paladin", "paladin", 40, 32941.176, 36235.294, 166682.351, "STK-LND-WKR"),
        ("Spirit of the Forest", "spirit", 33, 25000.0, 27500.0, 126500.0, "STK-LND-WKR"),
        ("Prince Rennyn", "rennyn", 112, 7500.0, 8250.0, 37950.0, "STK-LND-WKR"),
        ("Kinbound Druid", KINBOUND_REF, 867, 2200.0, 1779.413, 8185.299, "STK-LND-WKR"),
        ("Runi", "runi", 505, 1500.0, 1500.0, 6900.0, "STK-LND-RUNI"),
    ]
    report = {
        "plots": [{
            "deed_uid": DEED,
            "facts": {"plot": "41-9-56", "rarity": "legendary", "status": "occupied", "resource": "WOOD", "territory": "1"},
            "staking_facts": {
                "max_workers_allowed": 6, "total_base_pp_cap": 100000,
                "is_power_core_staked": False, "is_energized": True, "is_runi_staked": True,
                "deed_rarity_boost": 1, "totem_boost": 0.5, "title_boost": 0.1,
                "runi_boost": 1, "deed_status_token_boost": 0,
                "green_biome_modifier": 0.1,
            },
        }],
        "workers": [
            {
                "name": name, "asset_ref": ref, "card_detail_id": detail_id,
                "land_base_pp": base, "total_construction_pp": construction,
                "total_harvest_pp": harvest, "stake_ref_uid": DEED,
                "stake_type_uid": stake_type,
                "asset_kind": "runi_worker_card" if stake_type == "STK-LND-RUNI" else "worker_card",
                "slot": index,
            }
            for index, (name, ref, detail_id, base, construction, harvest, stake_type) in enumerate(workers)
        ],
        "cards": [
            {"asset_ref": ref, "facts": {"card_detail_id": detail_id, "level": 8 if detail_id == 867 else 1}}
            for _name, ref, detail_id, *_rest in workers
        ],
        "bonus_assets": [
            {"name": "Epic Totem", "asset_ref": "totem", "stake_ref_uid": DEED, "stake_type_uid": "STK-LND-TOT", "asset_kind": "totem"},
            {"name": "The Proven", "asset_ref": "title", "stake_ref_uid": DEED, "stake_type_uid": "STK-LND-TTL", "asset_kind": "title"},
        ],
    }
    details = {
        27: {"id": 27, "color": "Green", "sub_type": "Elf"},
        40: {"id": 40, "color": "Green", "sub_type": "Human"},
        33: {"id": 33, "color": "Green", "sub_type": "Elf"},
        112: {"id": 112, "color": "Green", "sub_type": "Elf"},
        505: {"id": 505, "color": "Gray", "sub_type": "Runi"},
        867: {
            "id": 867, "color": "Green", "sub_type": "Elf",
            "stats": {"land_abilities": [[] for _ in range(7)] + [[
                ["DD", -0.99], ["BLOODLINE", 0.3, "Elf"], ["WOOD", 0.7], ["ENERGIZED"], ["LL"],
            ]]},
        },
    }
    return report, details


def test_real_kinbound_i294_uses_observed_capped_pp_and_whole_plot_delta():
    report, details = _kinbound_fixture()
    result = plot_removal_delta(report, DEED, [KINBOUND_REF], details_by_id=details)

    assert result["own_harvest_pp_delta"] == pytest.approx(-8185.299)
    assert result["plot_residual_delta"] == pytest.approx(-108220.588)
    assert result["harvest_pp_delta"] == pytest.approx(-116405.887)
    assert result["cap_relief"]["status"] == "unmodeled"


def test_clear_plot_prices_workers_and_bonus_items_as_one_final_roster():
    report, details = _kinbound_fixture()
    worker_refs = [worker["asset_ref"] for worker in report["workers"] if worker["stake_type_uid"] == "STK-LND-WKR"]
    result = plot_removal_delta(report, DEED, worker_refs + ["totem", "title"], details_by_id=details)

    assert result["not_found"] == []
    assert {row["asset_ref"] for row in result["removed"]} == set(worker_refs + ["totem", "title"])
    assert result["after_mult"] == pytest.approx(3.0)
    assert result["harvest_pp_delta"] == pytest.approx(-506900.001)


def test_retained_real_shape_runi_loses_item_multiplier_but_not_worker_ability_broadcast():
    report, details = _kinbound_fixture()
    result = plot_removal_delta(report, DEED, ["totem", "title"], details_by_id=details)

    assert result["plot_ability_delta"] == 0
    assert result["plot_residual_delta"] == pytest.approx(-66900.0006)


def test_power_core_removal_projects_full_five_to_runi_only_four():
    report, details = _kinbound_fixture()
    report["plots"][0]["facts"]["worksite_type"] = "Logging Camp"
    report["plots"][0]["worksite_facts"] = {"project_type": "worksite_wood"}
    staking = report["plots"][0]["staking_facts"]
    staking.update({
        "max_workers_allowed": 5,
        "is_power_core_staked": True,
        "is_energized": False,
        "is_runi_staked": True,
    })
    numbered = [worker for worker in report["workers"] if worker["stake_type_uid"] == "STK-LND-WKR"]
    for slot, worker in enumerate(numbered, start=1):
        worker["slot"] = slot
    next(worker for worker in report["workers"] if worker["stake_type_uid"] == "STK-LND-RUNI")["slot"] = 0
    report["bonus_assets"].append({
        "name": "Power Core", "asset_ref": "core", "stake_ref_uid": DEED,
        "stake_type_uid": "STK-LND-PCR", "asset_kind": "power_core",
    })

    result = plot_removal_delta(report, DEED, ["core"], details_by_id=details)

    assert result["powered_slots_before"] == 5
    assert result["powered_slots_after"] == 4
    assert [entry["slot"] for entry in result["power_lost"]] == [5]


def test_own_construction_pp_delta_stays_exact_even_when_harvest_side_is_uncertain():
    # item 40 -- construction has no plot-wide broadcast (unlike harvest), so a
    # removal's own Base-PP-stage cost is always an exact point value, even while
    # `cap_relief` correctly reports the harvest-stage relief as unmodeled/unsolved.
    report, details = _kinbound_fixture()
    result = plot_removal_delta(report, DEED, [KINBOUND_REF], details_by_id=details)

    assert result["cap_relief"]["status"] == "unmodeled"
    assert result["own_construction_pp_delta"]["status"] == "point"
    assert result["own_construction_pp_delta"]["value"] == pytest.approx(-1779.413)
    removed = next(row for row in result["removed"] if row["asset_ref"] == KINBOUND_REF)
    assert removed["own_construction_pp"] == pytest.approx(1779.413)
    # Never the same number as the harvest-stage own delta -- the two stages apply at
    # different points on different bases (the internal rules) and must never be collapsed.
    assert result["own_construction_pp_delta"]["value"] != pytest.approx(result["own_harvest_pp_delta"])


def test_item_only_removal_has_no_base_pp_effect():
    # Totems/Titles/Power Cores shift the plot's flat multiplier, not their own
    # construction PP -- an item-only removal must report zero Base PP effect and null
    # `own_construction_pp` per item, never a fabricated number.
    report, details = _kinbound_fixture()
    result = plot_removal_delta(report, DEED, ["totem", "title"], details_by_id=details)

    assert result["own_construction_pp_delta"]["status"] == "point"
    assert result["own_construction_pp_delta"]["value"] == 0.0
    items = [row for row in result["removed"] if row["kind"] == "item"]
    assert items and all(row["own_construction_pp"] is None for row in items)


def test_unknown_requested_ref_fails_closed_instead_of_returning_partial_pricing():
    report, details = _kinbound_fixture()

    with pytest.raises(ValueError, match="stale-ref"):
        plot_removal_delta(report, DEED, [KINBOUND_REF, "stale-ref"], details_by_id=details)


def test_removal_impact_route_returns_the_same_server_authoritative_delta(monkeypatch):
    report, details = _kinbound_fixture()
    monkeypatch.setattr(routes_candidates.capture_mod, "latest_snapshot", lambda config, player: (report, "fixture"))
    monkeypatch.setattr(routes_candidates, "load_details_by_id", lambda: details)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(player="player0005"))))

    response = routes_candidates.post_removal_impact(DEED, {"asset_refs": [KINBOUND_REF]}, request)
    body = json.loads(response.body)

    assert body["harvest_pp_delta"] == pytest.approx(-116405.887)
    assert body["plot_residual_delta"] == pytest.approx(-108220.588)
