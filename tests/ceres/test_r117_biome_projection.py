"""R-117 G1/G2: regression guards for the already-landed v0.8.86 projection."""

from __future__ import annotations

import copy
import json
import types
from pathlib import Path

import pytest

from ceres.board import project_board, project_single_plot


@pytest.fixture
def mixed_biome_report(report):
    mixed = copy.deepcopy(report)
    plot = mixed["plots"][0]
    facts = plot["staking_facts"]
    for color in ("red", "blue", "green", "white", "black", "gold"):
        facts.pop(f"{color}_biome_modifier", None)
    facts.update(red_biome_modifier=0.1, blue_biome_modifier=-0.5,
                 green_biome_modifier=0.0, black_biome_modifier="malformed",
                 gold_biome_modifier=None)
    return mixed, plot["deed_uid"]


def _assert_full(project, mixed_biome_report, details_by_id):
    report, deed = mixed_biome_report
    row = project(report, deed, details_by_id)
    assert row is not None
    biome = json.loads(json.dumps(row))["biome"]
    assert biome == {"red": 0.1, "blue": -0.5, "green": 0.0,
                     "white": None, "black": None, "gold": None}
    assert isinstance(biome["green"], float)


def test_full_plot_biome_value_state(mixed_biome_report, details_by_id):
    _assert_full(project_single_plot, mixed_biome_report, details_by_id)


def test_lean_plot_biome_absence(mixed_biome_report, details_by_id):
    report, deed = mixed_biome_report
    full = project_single_plot(report, deed, details_by_id)
    assert full["biome"]
    rows = project_board(report, details_by_id, lean=True)["plots"]
    # the lean row omits the empty `biome` placeholder instead of sending `{}`.
    assert "biome" not in next(row for row in rows if row["deed_uid"] == deed)


@pytest.mark.parametrize("mutation", ["zero_fallback", "lean_leak", "discard_zero"])
def test_projection_guard_rejects_injected_regression(mutation, mixed_biome_report, details_by_id):
    path = Path(__file__).parents[2] / "ceres" / "board.py"
    source = path.read_text(encoding="utf-8")
    anchor = '"biome": {} if lean else {color: _num(sf.get(f"{color}_biome_modifier")) for color in BIOME_COLORS}'
    assert source.count(anchor) == 1
    replacement = (anchor.replace('_num(sf.get(f"{color}_biome_modifier"))',
                                  '(_num(sf.get(f"{color}_biome_modifier")) or 0.0)')
                   if mutation == "zero_fallback" else anchor.replace("{} if lean else ", ""))
    if mutation == "discard_zero":
        replacement = anchor.replace('_num(sf.get(f"{color}_biome_modifier"))',
                                     '(_num(sf.get(f"{color}_biome_modifier")) or None)')
    mutated = source.replace(anchor, replacement, 1)
    assert mutated != source
    module = types.ModuleType("ceres._r117_projection_mutation")
    module.__file__ = str(path)
    exec(compile(mutated, str(path), "exec"), module.__dict__)
    if mutation in ("zero_fallback", "discard_zero"):
        with pytest.raises(AssertionError):
            _assert_full(module.project_single_plot, mixed_biome_report, details_by_id)
    else:
        report, deed = mixed_biome_report
        rows = module.project_board(report, details_by_id, lean=True)["plots"]
        with pytest.raises(AssertionError):
            assert "biome" not in next(row for row in rows if row["deed_uid"] == deed)


def test_lean_row_drops_editor_only_keys(mixed_biome_report, details_by_id):
    """swap_eligibility / multiplier_parts ride only on the full (detail) row."""
    report, deed = mixed_biome_report
    full = project_single_plot(report, deed, details_by_id)
    assert "swap_eligibility" in full and "multiplier_parts" in full
    lean = next(row for row in project_board(report, details_by_id, lean=True)["plots"] if row["deed_uid"] == deed)
    assert "swap_eligibility" not in lean and "multiplier_parts" not in lean
