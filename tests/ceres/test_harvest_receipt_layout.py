"""Static Node assertions for the // receipt layout."""
from __future__ import annotations

from pathlib import Path

from .vm_context import run_in_module_context


STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_single_plot_received_and_cost_are_side_by_side_only_for_single_plot():
    # A single plot no longer passes a `singlePlot` flag to harvestResourceColumns: it has its own
    # builder, singlePlotHarvestLedger, which keeps the received amount and its cost together in
    # one ledger (its own-resource cost netted in, any other cost as a row). The regional/receipt columns must never adopt that single-plot ledger.
    body_js = r"""
const single = singlePlotHarvestLedger({ token: 'GRAIN', net: 10, costs: { GRAIN: 2, WOOD: 3 } });
if (!single.startsWith('<div class="hm-plot-ledger">')) throw new Error('single plot lacks its own ledger: ' + single);
if (!single.includes('hm-plot-ledger-row is-gain') || !single.includes('>+8.00<') || !single.includes('received · after tax and costs')) throw new Error('single plot received row missing: ' + single);
if (!single.includes('hm-total hm-resource-debit') || !single.includes('Wood cost')) throw new Error('single plot cost is not in the same ledger: ' + single);
const regional = harvestResourceColumns({ GRAIN: 10 }, { GRAIN: 2 }, { receipt: true });
if (regional.includes('hm-plot-ledger') || !regional.includes('hm-totals hm-totals--receipt')) throw new Error('regional result incorrectly adopted single-plot layout: ' + regional);
"""
    run_in_module_context(body_js, entry=STATIC / "harvest-resources.js")
