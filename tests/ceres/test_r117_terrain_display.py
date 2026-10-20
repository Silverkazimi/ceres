"""R-117 G3 semantic guards; DOM guards G4–G9 live in the companion module.

Only the independent test queue executes these guards and their mutations.
"""

from __future__ import annotations

import pytest

from .vm_context import read_entry_source, run_in_module_context


MODEL_GUARD = r"""
const requireAffinity = (condition, message) => { if (!condition) throw new Error(message); };
const mountain = {red: .1, blue: -.5, green: -.5, white: 0, black: .1, gold: 0};
const profile = terrainAffinityProfile({terrain: 'Mountain', biome: mountain});
requireAffinity(Object.isFrozen(TERRAIN_AFFINITY_SPLINTERS) && TERRAIN_AFFINITY_SPLINTERS.every(Object.isFrozen), 'G3 frozen six-key mapping');
const expected = [
 ['red', 'Fire', .1, '+10%'], ['blue', 'Water', -.5, '−50%'],
 ['green', 'Earth', -.5, '−50%'], ['white', 'Life', 0, '0%'],
 ['black', 'Death', .1, '+10%'], ['gold', 'Dragon', 0, '0%'],
];
requireAffinity(JSON.stringify(profile.peers.map(p => [p.rawKey, p.splinter, p.value, p.display])) === JSON.stringify(expected), 'G3 exact lowercase canonical join');
requireAffinity(JSON.stringify(profile.groups.map(g => [g.id, g.peers.map(p => p.splinter)])) === JSON.stringify([
 ['favoured', ['Fire', 'Death']], ['unchanged', ['Life', 'Dragon']], ['penalised', ['Water', 'Earth']],
]), 'G3 grouped answer order and canonical ties');
requireAffinity(profile.neutral.splinter === 'Neutral' && profile.neutral.display === 'No terrain effect', 'G3 separate Neutral');
requireAffinity(!('value' in profile.neutral) && !('percent' in profile.neutral), 'G3 Neutral nonnumeric');
requireAffinity(profile.peers[0].barRatio === .2 && profile.peers[1].barRatio === 1 && profile.peers[3].barRatio === 0, 'G3 pinned fifty-percent domain');
const unequal = terrainAffinityProfile({biome: {...mountain, red: .01, black: .4, blue: -.01, green: -.4}});
requireAffinity(JSON.stringify(unequal.groups.map(g => g.peers.map(p => p.splinter))) === JSON.stringify([['Fire','Death'],['Life','Dragon'],['Water','Earth']]), 'G3 no magnitude sorting');
for (const bad of [null, undefined, '', ' ', '0', '0.1', true, false, [], [0], {}, {value: 0}, NaN, Infinity, -Infinity]) {
 const peer = terrainAffinityProfile({biome: {...mountain, red: bad}}).peers[0];
 requireAffinity(peer.value === null && peer.percent === null && peer.state === 'unknown', 'G3 malformed input is unknown: ' + String(bad));
 requireAffinity(!peer.display.includes('0%') && peer.display.includes('not received'), 'G3 unknown is worded absence');
}
const missing = {...mountain}; delete missing.red;
for (const biome of [missing, undefined, null, {}, {Red: .1}, Object.assign(Object.create({red: .1}), {blue: 0})]) {
 requireAffinity(terrainAffinityProfile({biome}).peers[0].state === 'unknown', 'G3 lowercase own-property lookup');
}
requireAffinity(terrainAffinityProfile({}).peers.every(p => p.state === 'unknown'), 'G3 absent map');
for (const zero of [0, -0]) {
 const peer = terrainAffinityProfile({biome: {red: zero}}).peers[0];
 requireAffinity(peer.value === 0 && peer.state === 'unchanged' && peer.display === '0%', 'G3 signed zero is a received value');
}
const extras = terrainAffinityProfile({biome: {...mountain, Gray: .1, gray: 0, Neutral: -.5}});
requireAffinity(extras.peers.length === 6 && extras.neutral.splinter === 'Neutral', 'G3 no seventh peer');
requireAffinity(extras.peers.every(p => p.splinter !== 'Neutral'), 'G3 no numeric Neutral');
const overflow = terrainAffinityProfile({biome: {...mountain, red: .75}}).peers[0];
requireAffinity(overflow.overflow && overflow.barRatio === 1 && overflow.display === '+75%', 'G3 visible overflow with fixed scale');
for (const value of [.000001, -.000001]) {
 const display = terrainAffinityProfile({biome: {red: value}}).peers[0].display;
 requireAffinity(Number(display.replace('−','-').replace('%','')) === value * 100 && display !== '0%' && display !== '+0%' && display !== '−0%', 'G3 tiny known value must never round to zero');
}
const huge = terrainAffinityProfile({biome: {red: 1e304}}).peers[0];
requireAffinity(!/Infinity|NaN/.test(huge.display) && huge.overflow, 'G3 finite huge value stays finite');
for (const [value, state, display] of [[Number.MAX_VALUE, 'favoured', '+1.7976931348623157e+310%'], [-Number.MAX_VALUE, 'penalised', '−1.7976931348623157e+310%']]) {
 const peer = terrainAffinityProfile({biome: {red: value}}).peers[0];
 requireAffinity(peer.value === value && peer.state === state && peer.display === display && peer.overflow && peer.barRatio === 1, 'G3 finite maximum remains known and marked overflow');
}
"""


def test_terrain_affinity_semantic_partition():
    run_in_module_context(MODEL_GUARD)


@pytest.mark.parametrize(("anchor", "replacement"), [
    ("['red', 'Fire']", "['Red', 'Fire']"),
    ("['red', 'Fire']", "['red', 'Water']"),
    ("typeof value === 'number' && Number.isFinite(value) ? value : null",
     "Number.isFinite(Number(value)) ? Number(value) : null"),
    ("typeof value === 'number' && Number.isFinite(value) ? value : null",
     "typeof value === 'number' && Number.isFinite(value) ? value : 0"),
    ("typeof value === 'number' && Number.isFinite(value) ? value : null",
     "typeof value === 'number' && Number.isFinite(value) && value ? value : null"),
    ("Object.prototype.hasOwnProperty.call(biome, rawKey)", "rawKey in biome"),
    ("Object.freeze(['gold', 'Dragon'])", "Object.freeze(['gold', 'Dragon']), Object.freeze(['gray', 'Neutral'])"),
    ("{ id: 'favoured', label: 'Favoured', glyph: '↑', peers: peers.filter((peer) => peer.state === 'favoured') }",
     "{ id: 'favoured', label: 'Favoured', glyph: '↑', peers: peers.filter((peer) => peer.state === 'favoured').sort((a, b) => b.value - a.value) }"),
])
def test_semantic_guard_rejects_injected_regression(anchor, replacement):
    source = read_entry_source()
    assert source.count(anchor) == 1, "mutation must target the real whole module graph exactly once"
    mutated = source.replace(anchor, replacement, 1)
    assert mutated != source
    result = run_in_module_context(MODEL_GUARD, source=mutated, check=False)
    assert result.returncode != 0
    assert "G3 " in result.stderr, "a guard assertion, rather than a loader error, must reject the mutation"
