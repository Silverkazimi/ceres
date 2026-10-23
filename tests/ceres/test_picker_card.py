""" worker picker card guards. Written, not run on the maintainer's laptop ()."""

from __future__ import annotations

import re
from pathlib import Path

from .vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
CSS = (ROOT / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")


_BODY = r"""
const row = { deed_uid: 'd1', terrain: 'Forest', worksite_type: 'Farm', workers: [] };
const base = { asset_ref: 'a', name: 'Card', level: 3, base_pp: 1000, terrain_boost: 0.1, splinter: 'Earth', rarity: 'Rare',
  card_set: 'chaos', edition: 7, construction_pp: { status: 'point', value: 1100 }, plot_impact: { status: 'point', value: 50 } };
const cards = [
  { ...base, name: 'Idle', cooldown: { state: 'ready', ready: true } },
  { ...base, name: 'Cool', cooling_from_plot: '65-9-83', cooldown: { state: 'cooling', ready: false, ready_at: '2099-01-02T03:04:05Z' } },
  { ...base, name: 'Dead', survival_bracket: 10, cooldown: { state: 'survival_locked', ready: false, ready_at: '2099-01-02T03:04:05Z' } },
  { ...base, name: 'Unknown', cooldown: { state: 'unknown', ready: false } },
];
S.CERES_PICKER = { row, deedUid: 'd1', groupId: 'worker', groupLabel: 'Workers', slotIndex: 0, mode: 'stake', currentOccupant: null,
  sort: 'construction_pp', targetTerrain: 'Forest', bloodlineSubTypes: [], offset: 0, total: 4, loading: false, error: null,
  candidates: cards, excludedCounts: { delegated: 9, terrain_penalty: 3 }, notices: [],
  filters: { q: '', rarity: '', splinter: '', edition: '', min_pp: '', max_pp: '', bucket: 'all', ready_now: false,
    include_terrain_penalised: false, bloodline_pairs: false } };
const html = renderInlineCandidatePicker(row);
if (!html.includes('class="picker-bar"')) process.exit(10);
for (const noise of ['picker-replacing', 'candidate-columns', 'picker-advisory', 'Pool:', 'candidate-first']) if (html.includes(noise)) process.exit(11);
if ((html.match(/class="candidate-row candidate-card/g) || []).length !== 4) process.exit(12);
const rows = html.split('class="candidate-row candidate-card').slice(1);
if (!rows[1].includes('data-picker-action="choose"')) process.exit(13);   // cooling stays selectable ( §9)
if (!rows[1].includes('Unstaked from') || !rows[1].includes('ch-plot-badge') || !rows[1].includes('065-09-083')) process.exit(14);   // shared plot badge
if (!rows[1].includes('is-oneline') || !rows[1].includes('data-countdown-compact')) process.exit(24);
if (!rows[1].includes('data-countdown-until')) process.exit(15);
if (rows[2].includes('data-picker-action="choose"') || !rows[2].includes('aria-disabled="true"')) process.exit(16);
if (!rows[2].includes('revives in') || !rows[2].includes('Bracket 10')) process.exit(17);
if (!rows[3].includes('data-picker-action="choose"')) process.exit(18);   // unknown keeps today's behaviour
if (!html.includes('9 unavailable') || !html.includes('3 penalised hidden')) process.exit(19);
if (!html.includes('Stake here')) process.exit(20);
if (formatCountdown(2 * 86400000 + 4 * 3600000 + 12 * 60000) !== '2d 04h 12m') process.exit(21);
if (formatCountdown(2 * 86400000 + 4 * 3600000 + 12 * 60000, { compact: true }) !== '2d 04h') process.exit(25);
if (formatCountdown(4 * 3600000 + 12 * 60000, { compact: true }) !== '4h 12m') process.exit(26);
if (formatCountdown(59 * 60000 + 5000) !== '59m 05s') process.exit(22);
if (formatCountdown(-1) !== 'now') process.exit(23);
"""


def test_worker_picker_card_shape_status_and_chips() -> None:
    run_in_module_context(_BODY)


def test_picker_css_block_has_no_raw_colour_or_border_left() -> None:
    # picker title-row dividers are outside the candidate-card CSS guard.
    start = CSS.index("/* The card row: art | identity | status")
    end = CSS.index(".slot-hazard-overlay {")
    block = CSS[start:end]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", block)
    assert "border-left" not in block
    assert "min-height: 9.4rem" in block
