""" (2026-09-14): Fill plot breaks a same-total, same-base-PP tie by the plot's terrain figure
for the card's splinter -- boost first, then neutral, then penalty. Base PP still outranks terrain."""

from __future__ import annotations

from ceres.fill_plot import candidate_rank_key


def _cand(name: str, base_pp: float, terrain_boost: float) -> dict:
    return {"name": name, "base_pp": base_pp, "terrain_boost": terrain_boost}


def test_same_base_pp_orders_boost_neutral_penalty() -> None:
    keys = {
        "boost": candidate_rank_key(_cand("boost", 100, 0.1), 5.0, "C-1"),
        "neutral": candidate_rank_key(_cand("neutral", 100, 0.0), 5.0, "C-2"),
        "penalty": candidate_rank_key(_cand("penalty", 100, -0.1), 5.0, "C-3"),
    }
    assert max(keys.values()) == keys["boost"]
    assert sorted(keys, key=keys.__getitem__, reverse=True) == ["boost", "neutral", "penalty"]


def test_base_pp_outranks_terrain() -> None:
    strong_penalised = candidate_rank_key(_cand("strong", 120, -0.1), 5.0, "C-1")
    weak_boosted = candidate_rank_key(_cand("weak", 100, 0.1), 5.0, "C-2")
    assert strong_penalised > weak_boosted
