"""The staged Total Base badge frames gains and losses, and a cap-absorbed move as neither.

Sandbox-runnable VM guard (no browser); the live guard is
test_r25_stage_total_plaque.py::test_t8d_cap_loss_and_effective_frame.
"""

from __future__ import annotations

import json

from .vm_context import run_in_module_context


_JS = r"""
const total = (now, after, loss = 0) => ({estimate: {status: 'point'}, effectiveNow: now, effectiveAfter: after, loss, cap: 100000});
const badge = (stageTotal) => renderChangeBadges({status: 'unknown'}, NaN, 1, stageTotal, null, null);
console.log('__TOTALBASE__' + JSON.stringify({
  capped: badge(total(100000, 100000, 5441)),
  gain: badge(total(90000, 95000)),
  loss: badge(total(95000, 90000)),
}));
"""


def _badges() -> dict:
    result = run_in_module_context(_JS)
    out = result.stdout.split('__TOTALBASE__', 1)
    assert len(out) == 2, result.stdout
    return json.loads(out[1].splitlines()[0])


def _classes(html: str) -> set[str]:
    start = html.index('class="editor-change-badge ') + len('class="')
    return set(html[start:html.index('"', start)].split())


def test_cap_absorbed_move_has_no_gain_or_loss_frame() -> None:
    badges = _badges()
    capped = _classes(badges['capped'])
    assert 'is-positive' not in capped and 'is-negative' not in capped, badges['capped']
    assert '(0)' in badges['capped'] and 'cap <strong>−5,441</strong>' in badges['capped']
    assert 'is-positive' in _classes(badges['gain'])
    assert 'is-negative' in _classes(badges['loss'])
