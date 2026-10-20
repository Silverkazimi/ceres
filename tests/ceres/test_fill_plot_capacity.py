from types import SimpleNamespace

from ceres import fill_plot


def test_fill_plot_visits_four_powered_slots_before_unpowered_fifth(monkeypatch):
    plot = SimpleNamespace(cards=[], base_cap=100000, terrain=lambda _color, _secondary=None: 0.0)
    report = {
        "generated_at": "2026-09-15T00:00:00Z",
        "plots": [{
            "deed_uid": "plot-1",
            "facts": {"worksite_type": "Grain Farm"},
            "worksite_facts": {"project_type": "worksite_grain"},
            "staking_facts": {
                "max_workers_allowed": 4,
                "worker_count": 1,
                "is_power_core_staked": False,
                "is_energized": False,
                "is_runi_staked": True,
                "total_harvest_pp": 0,
            },
        }],
        "bonus_assets": [],
    }
    visited = []

    def ranked(_report, _deed_uid, group_id, **kwargs):
        if group_id != "worker":
            return {"total": 0, "candidates": []}
        slot = kwargs["slot"]
        visited.append(slot)
        return {
            "total": 1,
            "candidates": [{
                "asset_ref": f"worker-{slot}", "name": f"Worker {slot}",
                "base_pp": 1000, "terrain_boost": 0, "splinter": "fire",
                "cooldown": {"ready": True},
            }],
        }

    monkeypatch.setattr(fill_plot, "_plots_cached", lambda *_args, **_kwargs: {"plot-1": plot})
    monkeypatch.setattr(fill_plot, "rank_candidates", ranked)
    monkeypatch.setattr(fill_plot, "_priced", lambda _report, _deed, moves, _details, _plots: float(len(moves)))
    result = fill_plot.propose_fill(
        report,
        "plot-1",
        player="player0005",
        settings=fill_plot.clean_settings({
            "workers": True, "land_cards": False, "runis": False, "totems": False,
            "titles": False, "power_cores": False, "keep_current": True,
        }),
        planned_moves=[],
        details_by_id={},
    )

    assert visited[:5] == [0, 1, 2, 3, 4]
    assert result["worker_capacity"] == 5
    assert [move["slot_index"] for move in result["moves"]] == [0, 1, 2, 3, 4]


def _first_clear_report() -> dict:
    return {
        "generated_at": "2026-09-15T00:00:00Z",
        "plots": [{
            "deed_uid": "plot-1",
            "facts": {"plot": "1-1-1", "rarity": "common", "status": "natural", "resource": "grain", "territory": "T"},
            "staking_facts": {
                "max_workers_allowed": 0, "worker_count": 0, "is_power_core_staked": False,
                "is_energized": False, "is_runi_staked": False, "total_harvest_pp": 0,
                "total_base_pp_cap": 100000,
            },
            "diagnostics": [{"type": "no_worksite"}],
        }],
        "bonus_assets": [],
    }


def _first_clear_proposal(monkeypatch, *, planned_moves=None, free_runi=True):
    plot = SimpleNamespace(cards=[], base_cap=100000, terrain=lambda _color, _secondary=None: 0.0)
    report = _first_clear_report()

    def ranked(projected, _deed_uid, group_id, **kwargs):
        row = projected["plots"][0]
        if group_id == "runi" and not free_runi:
            return {"total": 0, "candidates": []}
        if group_id == "worker":
            assert row["worksite_facts"]["project_type"].startswith("worksite_")
            assert any(move.get("group_id") == "power_core" for move in kwargs["staged_moves"])
            slot = kwargs["slot"]
            candidate = {"asset_ref": f"worker-{slot}", "name": f"Worker {slot}", "base_pp": 1000,
                         "terrain_boost": 0, "splinter": "fire", "cooldown": {"ready": True}}
        else:
            candidate = {"asset_ref": f"item-{group_id}", "name": group_id, "boost": 0.1,
                         "cooldown": {"ready": True}}
        return {"total": 1, "candidates": [candidate]}

    monkeypatch.setattr(fill_plot, "_plots_cached", lambda *_args, **_kwargs: {"plot-1": plot})
    monkeypatch.setattr(fill_plot, "rank_candidates", ranked)
    monkeypatch.setattr(fill_plot, "_priced", lambda _report, _deed, moves, _details, _plots: float(len(moves)))
    return fill_plot.propose_fill(
        report, "plot-1", player="player0005", settings=fill_plot.clean_settings({}),
        planned_moves=planned_moves or [], details_by_id={},
    )


def test_first_clear_fill_proposes_power_five_workers_runi_totem_and_title(monkeypatch):
    result = _first_clear_proposal(monkeypatch)

    assert [move["group_id"] for move in result["moves"]] == [
        "power_core", "worker", "worker", "worker", "worker", "worker", "runi", "totem", "title",
    ]
    assert result["worker_capacity"] == 5
    assert result["worksite_prompt"] == "Choose a worksite to start the build"


def test_first_clear_fill_without_free_runi_omits_it_without_error(monkeypatch):
    result = _first_clear_proposal(monkeypatch, free_runi=False)

    assert [move["group_id"] for move in result["moves"]] == [
        "power_core", "worker", "worker", "worker", "worker", "worker", "totem", "title",
    ]
    assert result["item_available"]["runi"] == 0


def test_first_clear_fill_does_not_duplicate_a_staged_power_core(monkeypatch):
    result = _first_clear_proposal(monkeypatch, planned_moves=[{
        "deed_uid": "plot-1", "group_id": "power_core", "slot_index": 0,
        "action": "stake", "asset_ref": "staged-core", "evict_asset_ref": None,
    }])

    assert all(move["group_id"] != "power_core" for move in result["moves"])
    assert sum(move["group_id"] == "worker" for move in result["moves"]) == 5


def test_cleared_full_plot_keeps_existing_nothing_to_change_result(monkeypatch):
    cards = [{"asset_ref": f"resident-{slot}", "base": 1000, "slot": slot + 1} for slot in range(5)]
    plot = SimpleNamespace(cards=cards, base_cap=100000, terrain=lambda _color, _secondary=None: 0.0)
    report = _first_clear_report()
    report["plots"][0].update({"facts": {**report["plots"][0]["facts"], "worksite_type": "Grain Farm"},
                               "worksite_facts": {"project_type": "worksite_grain"}, "diagnostics": []})
    report["plots"][0]["staking_facts"].update({"max_workers_allowed": 5, "worker_count": 5, "is_power_core_staked": True})
    monkeypatch.setattr(fill_plot, "_plots_cached", lambda *_args, **_kwargs: {"plot-1": plot})
    monkeypatch.setattr(fill_plot, "rank_candidates", lambda *_args, **_kwargs: {"total": 0, "candidates": []})
    monkeypatch.setattr(fill_plot, "_priced", lambda _report, _deed, moves, _details, _plots: float(len(moves)))

    result = fill_plot.propose_fill(
        report, "plot-1", player="player0005",
        settings=fill_plot.clean_settings({"keep_current": True, "runis": False, "totems": False, "titles": False, "power_cores": False}),
        planned_moves=[], details_by_id={},
    )

    assert result["moves"] == []
    assert result["empty_slots_left"] == 0
    assert result["worksite_prompt"] is None
