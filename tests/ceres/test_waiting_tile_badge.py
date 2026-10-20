""": staged workers that wait carry a Waiting badge with a live timer on their tile; the Changes rows count down too."""

from __future__ import annotations

from pathlib import Path

from .vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"

_BODY = r"""
const cooling = { state: 'cooling', ready: false, ready_at: '2099-01-02T03:04:05Z' };
const blocked = stageWaitBadgeHtml('blocked', cooling, 'worker');
if (!blocked.includes('slot-wait-badge') || !blocked.includes('Waiting')) process.exit(10);
if (!blocked.includes('data-countdown-until="2099-01-02T03:04:05Z"') || !blocked.includes('data-countdown-ended="due"')) process.exit(11);
const move = stageWaitBadgeHtml('elsewhere', { state: 'staked' }, 'worker');
if (!move.includes('is-elsewhere') || !move.includes('72h') || move.includes('data-countdown-until')) process.exit(12);
if (stageWaitBadgeHtml('elsewhere', { state: 'staked' }, 'totem') !== '') process.exit(13);
if (stageWaitBadgeHtml('ready', null, 'worker') !== '' || stageWaitBadgeHtml('planned', cooling, 'worker') !== '') process.exit(14);
const past = stageWaitBadgeHtml('blocked', { state: 'cooling', ready: false, ready_at: '2001-01-01T00:00:00Z' }, 'worker');
if (!past.includes('>due<')) process.exit(15);
const plain = stageWaitBadgeHtml('blocked', { state: 'cooling', ready: false }, 'worker');
if (!plain.includes('Waiting') || plain.includes('data-countdown-until')) process.exit(16);
"""


def test_waiting_badge_states_and_timer() -> None:
    run_in_module_context(_BODY)


def test_board_tile_and_changes_use_the_badge_and_timer() -> None:
    editor = (STATIC / "editor.js").read_text(encoding="utf-8")
    checkout = (STATIC / "checkout.js").read_text(encoding="utf-8")
    countdown = (STATIC / "countdown.js").read_text(encoding="utf-8")
    # the Waiting badge is one of the tile's band states; tileBand() renders at most one.
    assert "waiting: waitBadge" in editor and "${band}" in editor and "cooling && !waitBadge" in editor
    assert "stageWaitBadgeHtml" not in checkout  # the Changes tiles carry no badge; the timer sits in the box below
    assert 'ch-wait-timer"' in checkout and '<span class="spinner spinner-sm" aria-hidden="true"></span><b data-countdown-until=' in checkout
    assert "waitTimerHtml(entry.cooldown) ||" in checkout and "can be signed in <span data-countdown-until" in checkout
    assert ".slot-wait-badge [data-countdown-until]" in countdown and "document.querySelector(PERSISTENT)" in countdown



def test_stage_back_box_and_planned_card_on_the_original_tile() -> None:
    ui = (STATIC / "combine-chain-ui.js").read_text(encoding="utf-8")
    editor = (STATIC / "editor.js").read_text(encoding="utf-8")
    chain = (STATIC / "combine-chain.js").read_text(encoding="utf-8")
    candidates = (STATIC / "candidates.js").read_text(encoding="utf-8")
    assert "stageBack: true }" in ui and "data-upgrade-stage-back${view.stageBack ? ' checked' : ''}" in ui
    assert "stage_back: anchorStaked && view.stageBack !== false" in ui
    assert "changed = await stageBackPlannedCard(id, plan) || changed;" in ui and "stage_back_at" in ui
    assert "if (!plan?.origin?.stage_back || plan.origin.stage_back_at" in chain
    assert "stageBackPlanFor(row.deed_uid, index)" in editor and "chainCoolingCopy" not in editor
    assert 'class="planned-origin"' in candidates


def test_planned_badge_is_the_centred_badge() -> None:
    editor = (STATIC / "editor.js").read_text(encoding="utf-8")
    chip = editor[editor.index("export function plannedReturnChip("):][:1400]
    assert 'class="slot-wait-badge is-planned"' in chip and "after combine" in chip and "planned-return-chip" not in chip
    assert "RETURN_ICON" not in chip  # no arrow on the badge
