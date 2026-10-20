"""Static Node guards for negative Harvest all net badge styling."""
from __future__ import annotations

from pathlib import Path

from .vm_context import STATIC, run_in_module_context


def test_batch_net_badge_uses_loss_class_only_below_zero():
    run_in_module_context(
        r"""
const negative = batchBadgeHtml('GRAIN', -12.5, 'net');
if (!negative.includes('class="hm-batch-badge is-net is-loss"')) throw new Error('negative net lacks loss class: ' + negative);
if (!negative.includes('>−12.50</span>')) throw new Error('negative net sign or amount changed: ' + negative);
const positive = batchBadgeHtml('GRAIN', 12.5, 'net');
if (!positive.includes('class="hm-batch-badge is-net"')) throw new Error('positive net class changed: ' + positive);
if (positive.includes('is-loss') || !positive.includes('>+12.50</span>')) throw new Error('positive net is not a gain: ' + positive);
const zero = batchBadgeHtml('GRAIN', 0, 'net');
if (!zero.includes('class="hm-batch-badge is-net"')) throw new Error('zero net class changed: ' + zero);
if (zero.includes('is-loss') || !zero.includes('>+0.00</span>')) throw new Error('zero net is not a gain: ' + zero);
""",
        entry=STATIC / "harvest-modal.js",
    )


def test_negative_batch_net_has_danger_theme_rules():
    css = (Path(__file__).parents[2] / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")
    assert ":is(#harvest-modal-overlay, .harvest-receipt-ledger) .hm-batch-badge.is-net.is-loss { border-color:color-mix(in srgb, var(--danger) 45%, var(--border)); background:color-mix(in srgb, var(--danger) 14%, var(--bg2)); }" in css
    assert ":is(#harvest-modal-overlay, .harvest-receipt-ledger) .hm-batch-badge.is-net.is-loss .hm-batch-number { color:var(--danger); }" in css
