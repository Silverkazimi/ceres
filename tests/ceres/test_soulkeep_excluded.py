from datetime import datetime, timezone
from types import SimpleNamespace

from ceres import fill_plot
from ceres.candidates import rank_candidates
from ceres.routes_combine import _collection
from .vm_context import run_in_module_context


NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)
SOULKEEP_DETAIL_ID = 40001
DETAILS = {
    SOULKEEP_DETAIL_ID: {
        "id": SOULKEEP_DETAIL_ID,
        "name": "Nightmare fixture",
        "color": "Red",
        "rarity": "common",
        "edition": 9,
    },
}


def _report():
    return {
        "generated_at": "2026-09-25T00:00:00Z",
        "plots": [{
            "deed_uid": "plot-1",
            "facts": {"plot": "1-1-1", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T", "worksite_type": "Grain Farm"},
            "worksite_facts": {"project_type": "worksite_grain"},
            "staking_facts": {
                "max_workers_allowed": 1,
                "worker_count": 0,
                "total_base_pp_cap": 100000,
                "is_power_core_staked": True,
                "is_energized": False,
                "is_runi_staked": False,
                "total_harvest_pp": 0,
            },
        }],
        "workers": [],
        "cards": [{
            "asset_ref": "soulkeep-card",
            "uid": "CARD-SOULKEEP",
            "classification": {"bucket": "unstaked_apparent"},
            "facts": {
                "uid": "CARD-SOULKEEP",
                "card_detail_id": SOULKEEP_DETAIL_ID,
                "edition": 9,
                "land_base_pp": 1000,
                "level": 1,
            },
        }],
        "coverage": {"collection": {"complete": True}},
        "bonus_assets": [],
    }


def _rank(report, group_id):
    return rank_candidates(
        report, "plot-1", group_id, player="player0005", now=NOW,
        bucket="idle", details_by_id=DETAILS,
    )


def test_nightmare_is_excluded_from_worker_and_runi_candidates_and_counted():
    report = _report()

    workers = _rank(report, "worker")
    runis = _rank(report, "runi")

    assert "soulkeep-card" not in {row["asset_ref"] for row in workers["candidates"]}
    assert "soulkeep-card" not in {row["asset_ref"] for row in runis["candidates"]}
    assert workers["excluded_counts"]["soulkeep"] == 1
    assert runis["excluded_counts"]["soulkeep"] == 0


def test_nightmare_is_not_an_auto_fill_choice(monkeypatch):
    report = _report()
    plot = SimpleNamespace(cards=[], base_cap=100000, terrain=lambda _color, _secondary=None: 0.0)
    monkeypatch.setattr(fill_plot, "_plots_cached", lambda *_args, **_kwargs: {"plot-1": plot})
    monkeypatch.setattr(fill_plot, "potential_worker_capacity", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(fill_plot, "powered_worker_slots", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(fill_plot, "_priced", lambda _report, _deed, moves, _details, _plots: float(len(moves)))

    result = fill_plot.propose_fill(
        report,
        "plot-1",
        player="player0005",
        settings=fill_plot.clean_settings({
            "workers": True, "land_cards": False, "runis": False,
            "totems": False, "titles": False, "power_cores": False,
            "keep_current": True,
        }),
        planned_moves=[],
        details_by_id=DETAILS,
    )

    assert all(move["asset_ref"] != "soulkeep-card" for move in result["moves"])


def test_soulkeep_card_remains_in_combine_collection():
    cards = _collection(_report())

    assert any(card.get("uid") == "CARD-SOULKEEP" and card.get("edition") == 9 for card in cards)


def test_picker_hidden_summary_names_soulkeep_excluded_cards():
    run_in_module_context(r"""
const html = pickerHiddenSummary({ excludedCounts: { soulkeep: 2 } });
if (!html.includes('2 unavailable')) process.exit(70);
if (!html.includes('2 Soulkeep')) process.exit(71);
""")
