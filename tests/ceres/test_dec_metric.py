"""§6.6 test_dec_metric.py -- the sortable allowlist is mechanical, not advisory.

the internal rules / plan §6.4 rule 2: no card-level DEC efficiency may ever be exposed as a
sortable ranking. This asserts the allowlist itself, independent of any one board
payload, so a future edit to board.SORTABLE cannot silently reintroduce one.
"""

from __future__ import annotations

from ceres.board import SORTABLE, project_board

CARD_LEVEL_DEC_KEYS = {
    "worker_dec_efficiency", "card_dec_efficiency", "per_card_dec_efficiency",
    "dec_efficiency", "worker_pp_per_dec", "card_pp_per_dec",
}


def test_sortable_contains_no_card_level_dec_efficiency_key():
    assert not (set(SORTABLE) & CARD_LEVEL_DEC_KEYS)
    efficiency_keys = [key for key in SORTABLE if "efficiency" in key]
    # region_efficiency is a region/plot-level field (, v0.9.346), not card-level; .
    assert sorted(efficiency_keys) == ["plot_dec_efficiency", "region_efficiency"], efficiency_keys


def test_plot_dec_efficiency_is_in_sortable():
    assert "plot_dec_efficiency" in SORTABLE


def test_board_response_carries_the_same_sortable_list(report, details_by_id):
    board = project_board(report, details_by_id)
    assert board["sortable"] == SORTABLE
    assert not (set(board["sortable"]) & CARD_LEVEL_DEC_KEYS)
