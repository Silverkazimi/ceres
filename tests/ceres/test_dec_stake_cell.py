"""the DEC stake cell names what makes the stake 0 or lower, on lean rows too.

Sandbox-runnable VM guards (no browser) for format.js decStakeCell().
"""

from __future__ import annotations

import html
import json
import re

from .vm_context import run_in_module_context


_JS = r"""
const lean = {dec: {needed: 9000, needed_discount: -0.99}, capacity: {runi_staked: false}, workers: [], dec_discount_cards: ['Kinbound Druid']};
const records = {
 lowered: decStakeCell(lean),
 runi: decStakeCell({dec: {needed: 0, needed_discount: -0.5}, capacity: {runi_staked: true}, workers: [], dec_discount_cards: ['Tireless Farmhand']}),
 idle: decStakeCell({dec: {needed: 0, needed_discount: 0}, capacity: {runi_staked: false}, workers: [], dec_discount_cards: []}),
 plain: decStakeCell({dec: {needed: 331, needed_discount: 0}, capacity: {runi_staked: false}, workers: [], dec_discount_cards: []}),
 missing: decStakeCell({dec: {needed: null}, capacity: {}, workers: []}),
};
console.log('__DEC__' + JSON.stringify(records));
"""


def _run() -> dict:
    result = run_in_module_context(_JS)
    out = result.stdout.split('__DEC__', 1)
    assert len(out) == 2, result.stdout
    return json.loads(out[1].splitlines()[0])


def _label(tip: str) -> str:
    match = re.search(r'aria-label="([^"]*)"', tip)
    return html.unescape(match.group(1)) if match else ''


def test_each_cause_is_named_and_icon_follows_the_number() -> None:
    records = _run()
    assert 'Lowered 99% by Dark Discount on Kinbound Druid' in _label(records['lowered']['tip'])
    assert 'Runi removes' in _label(records['runi']['tip'])
    assert 'no workers are staked' in _label(records['idle']['tip'])
    assert '331 DEC to stake for 100% efficiency' in _label(records['plain']['tip'])
    assert records['plain']['html'].startswith('331<img class="dec-stake-icon"')
    assert 'land/dark-energy.png' in records['plain']['html']
    assert records['missing']['html'] == '—'
    assert all('{{ic:' not in _label(record['tip']) for record in records.values())
