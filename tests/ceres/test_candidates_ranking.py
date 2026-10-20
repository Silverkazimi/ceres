from __future__ import annotations

from praetoria.land_reallocate import MovableCard, Plot, score_candidates_for_plot


DETAILS = {
    866: {
        "id": 866, "name": "Tireless Farmhand", "color": "Green", "sub_type": "Human",
        "stats": {"land_abilities": [[[]] for _ in range(9)] + [[["GRAIN", 0.6]]]},
    },
    40001: {"id": 40001, "name": "Ordinary", "color": "Red", "sub_type": "Unrelated"},
    40002: {"id": 40002, "name": "Blue Ordinary", "color": "Blue", "sub_type": "Unrelated"},
}


def _card(name, base, color, detail_id=40001, level=None, ref=None):
    return {
        "name": name, "color": color, "base": base, "cur": 0.0,
        "card_detail_id": detail_id, "level": level, "asset_ref": ref or name,
    }


def _plot(cards, *, capacity=5, terrain=None, resource="GRAIN"):
    biome = {color: 0.0 for color in ("Red", "Blue", "White", "Black", "Green", "Gold")}
    biome.update(terrain or {})
    return Plot("plot-rank", "1-1-1", "common", "occupied", resource, "T", capacity, 1.0, 1000000.0, biome, cards)


def _candidate(name, base, color, detail_id=40001, level=None, ref=None):
    return MovableCard(name, color, base, 0.0, "idle", None, card_detail_id=detail_id, level=level, asset_ref=ref or name)


def test_broadcast_net_gain_can_beat_raw_base_pp():
    plot = _plot([_card("Resident", 10000, "Red")])
    generic = _candidate("High raw PP", 5000, "Red")
    ability = _candidate("Lower raw PP ability", 1000, "Green", 866, 10)
    scored = score_candidates_for_plot(plot, [generic, ability], target_slot_occupant=None, details_by_id=DETAILS)
    by_name = {card.name: (score, breakdown) for card, score, breakdown in scored}
    assert by_name["Lower raw PP ability"][0] > by_name["High raw PP"][0]
    assert by_name["Lower raw PP ability"][1]["plot_residual_delta"] > 0


def test_evicting_sole_ability_holder_is_negative_for_co_residents():
    holder = _card("Tireless Farmhand", 2000, "Green", 866, 10, "holder")
    resident = _card("Resident", 30000, "Red", ref="resident")
    plot = _plot([holder, resident], capacity=2)
    replacement = _candidate("Replacement", 10000, "Red")
    holder_target = _candidate("Tireless Farmhand", 2000, "Green", 866, 10)
    holder_target.current_harvest = 0.0
    holder_target.asset_ref = "holder"
    result = score_candidates_for_plot(
        plot, [replacement], target_slot_occupant=holder_target, details_by_id=DETAILS,
    )[0]
    assert result[1] < 0
    assert result[2]["plot_residual_delta"] < 0


def test_best_eviction_victim_is_selected_by_resulting_plot_gain():
    holder = _card("Tireless Farmhand", 2000, "Green", 866, 10, "holder")
    resident = _card("Resident", 5000, "Red", ref="resident")
    plot = _plot([holder, resident], capacity=2)
    replacement = _candidate("Replacement", 10000, "Red")
    result = score_candidates_for_plot(
        plot, [replacement], target_slot_occupant=None,
        target_slot_occupants=[
            _candidate("Tireless Farmhand", 2000, "Green", 866, 10, "holder"),
            _candidate("Resident", 5000, "Red", ref="resident"),
        ], details_by_id=DETAILS,
    )[0]
    assert result[2]["evicted"]["name"] == "Resident"


def _bloodline_score(elemental_rate):
    details = {
        866: {"id": 866, "sub_type": "Djinn", "stats": {"land_abilities": [[["BLOODLINE", 0.1, "Djinn"]]]}},
        867: {"id": 867, "sub_type": "Djinn", "stats": {"land_abilities": [[['OTHER', 0.0]]]}},
        985: {"id": 985, "sub_type": "Elemental", "stats": {"land_abilities": [[['OTHER', 0.0]]]}},
        986: {"id": 986, "sub_type": "Elemental", "stats": {"land_abilities": [[["BLOODLINE", elemental_rate, "Elemental"]]]}},
    }
    plot = _plot([
        _card("Djinn holder", 1000, "Red", 866, 1),
        _card("Djinn companion", 1000, "Red", 867, 1),
        _card("Elemental companion", 1000, "Red", 985, 1),
    ])
    candidate = _candidate("Sunlit Hart", 1000, "Red", 986, 1)
    return score_candidates_for_plot(plot, [candidate], target_slot_occupant=None, details_by_id=details)[0][1]


def test_distinct_bloodlines_do_not_stack_in_candidate_result():
    # Toil and Kin does not stack: of all triggered bloodline bonuses only the single largest applies
    # (praetoria.land_reallocate.plot_bloodline_boost). The plot already runs the Djinn +10 %.
    # An equal Elemental +10 % adds nothing to the plot: the candidate's own 1000 x 1.1 only
    # (stacking would score 1500).
    assert _bloodline_score(0.1) == 1100.0
    # A stronger Elemental +20 % replaces the +10 % for all four cards: 4000 x 1.2 - 3000 x 1.1 = 1500
    # (stacking would score 1900).
    assert _bloodline_score(0.2) == 1500.0


def test_terrain_match_can_beat_a_higher_pp_mismatch():
    plot = _plot([], terrain={"Red": 0.5, "Blue": -0.5}, resource=None)
    lower = _candidate("Terrain match", 1000, "Red")
    higher = _candidate("Terrain mismatch", 2500, "Blue", 40002)
    scored = score_candidates_for_plot(plot, [lower, higher], target_slot_occupant=None, details_by_id=DETAILS)
    by_name = {card.name: score for card, score, _ in scored}
    assert by_name["Terrain match"] > by_name["Terrain mismatch"]


def test_plot_gate_scores_the_whole_harvest_in_both_directions():
    plot = _plot([_card("Resident", 10000, "Red")])
    power = _candidate("Power Core", 0, "Gray")
    satisfying = score_candidates_for_plot(
        plot, [power], target_slot_occupant=None, details_by_id=DETAILS,
        before_powered=False, after_powered=True,
    )[0][1]
    breaking = score_candidates_for_plot(
        plot, [power], target_slot_occupant=None, details_by_id=DETAILS,
        before_powered=True, after_powered=False,
    )[0][1]
    assert satisfying == 10000
    assert breaking == -10000


def test_base_cap_rejects_a_candidate_before_scoring():
    plot = _plot([_card("Resident", 900, "Red")], capacity=5)
    candidate = _candidate("Over cap", 200, "Red")
    assert score_candidates_for_plot(
        plot, [candidate], target_slot_occupant=None, details_by_id=DETAILS,
        base_pp_ceiling=1000,
    ) == []
