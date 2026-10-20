"""Static Node assertions for the // receipt layout."""
from __future__ import annotations

from pathlib import Path

from .vm_context import run_in_module_context


STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_single_plot_received_and_cost_are_side_by_side_only_for_single_plot():
    body_js = r"""
const single = harvestResourceColumns({ GRAIN: 10 }, { GRAIN: 2 }, { receipt: true, singlePlot: true });
if (!single.includes('hm-totals--single-plot is-side-by-side')) throw new Error('single plot lacks side-by-side hook: ' + single);
if (!single.includes('<div class="hm-resource-debits">')) throw new Error('single plot cost did not receive paired column: ' + single);
const regional = harvestResourceColumns({ GRAIN: 10 }, { GRAIN: 2 }, { receipt: true });
if (regional.includes('hm-totals--single-plot') || regional.includes('hm-resource-debits')) throw new Error('regional result incorrectly adopted single-plot layout: ' + regional);
"""
    run_in_module_context(body_js, entry=STATIC / "harvest-resources.js")
