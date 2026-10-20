"""Whole-module R-96 frontend guards for the Survival lock surfaces."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from .vm_context import read_entry_source, run_in_module_context


ROOT = Path(__file__).parents[2]
EDITOR_JS = ROOT / "ceres" / "static" / "editor.js"
CHECKOUT_JS = ROOT / "ceres" / "static" / "checkout.js"


def _assert_mismatch_red(body_js: str, mutated_source: str) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(body_js, source=mutated_source)


_G4_BODY = r"""
const row = { deed_uid: 'survival-plot', terrain: 'Plains', worksite_type: 'Farm' };
const locked = {
  name: 'Dead Survival Runi', asset_ref: 'dead-runi', level: 1, base_pp: 1500,
  terrain_boost: 0, splinter: 'Neutral', rarity: 'Common', card_set: 'beta', edition: 1,
  construction_pp: { status: 'point', value: 1500, low: 1500, high: 1500, reason: null },
  plot_impact: { status: 'point', value: 0, low: 0, high: 0, reason: null },
  net_gain: 0,
  cooldown: { ready: false, ready_at: '2099-01-02T03:04:05Z', class: 'worker', state: 'survival_locked' },
};
function picker(groupId, readyNow, candidates) {
  return {
    row, deedUid: row.deed_uid, groupId, groupLabel: groupId === 'worker' ? 'Workers' : 'Runi',
    slotIndex: 0, mode: 'stake', currentOccupant: null, sort: 'construction_pp', source: 'fixture',
    targetTerrain: row.terrain, bloodlineSubTypes: [], offset: 0, total: candidates.length,
    loading: false, error: null, candidates, excludedCounts: {}, notices: [],
    filters: { q: '', rarity: '', splinter: '', edition: '', min_pp: '', bucket: 'all',
      ready_now: readyNow, include_terrain_penalised: false, bloodline_pairs: false },
  };
}

const defaultUrl = candidateQuery(picker('worker', true, []), 0);
const includeUrl = candidateQuery(picker('worker', false, [locked]), 0);
const defaultParams = new URLSearchParams(defaultUrl.split('?')[1]);
const includeParams = new URLSearchParams(includeUrl.split('?')[1]);
if (defaultParams.has('include_cooling')) process.exit(40);
if (includeParams.get('include_cooling') !== 'true') process.exit(41);

S.CERES_PICKER = picker('worker', true, []);
const defaultHtml = renderInlineCandidatePicker(row);
if (defaultHtml.includes('Dead Survival Runi')) process.exit(42);

S.CERES_PICKER = picker('worker', false, [locked]);
const includedHtml = renderInlineCandidatePicker(row);
// /:  worker picker redesign (v0.9.145, d66e066f) replaced the
// old div/button row variants and their "dead — Survival" copy with one shared
// candidate-row div (renderCandidateRow) driven by candidateStatusModel(), whose
// label for this state is "Dead in Survival" (register  row: "older guards
// need updating").
if (!includedHtml.includes('Dead in Survival')) process.exit(43);
if (includedHtml.includes('data-picker-action="choose"')) process.exit(44);
if (!includedHtml.includes('aria-disabled="true"')) process.exit(45);
if (/<div[^>]*tabindex=/.test(includedHtml)) process.exit(46);

const buttonVariantHtml = renderCandidateRow(locked, 0, picker('runi', false, [locked]), false);
// The Runi picker is workerLike (not the totem/title/power_core item class), so it
// renders the same candidate-card div as the worker picker, not a <button> row.
if (!buttonVariantHtml.startsWith('<div class="candidate-row candidate-card')) process.exit(47);
if (!buttonVariantHtml.includes('Dead in Survival')) process.exit(48);
if (buttonVariantHtml.includes('data-picker-action="choose"')) process.exit(49);
if (!buttonVariantHtml.includes('aria-disabled="true"')) process.exit(50);
if (buttonVariantHtml.includes('tabindex="0"')) process.exit(51);
"""


def test_ready_now_filter_hides_survival_locked_candidates_and_include_cooling_surfaces_them() -> None:
    """G4: the default query excludes dead cards, while the opt-in query renders
    the dead tag and disables both the worker div and button row variants."""
    run_in_module_context(_G4_BODY)


def test_ready_now_filter_guard_is_red_when_include_cooling_is_always_requested() -> None:
    """G4 injection: making every worker query request include_cooling makes the
    default Ready-now query eligible to return the dead candidate."""
    source = read_entry_source()
    anchor = "if (workerLike && filters.ready_now === false) params.set('include_cooling', 'true');"
    assert source.count(anchor) == 1
    mutated = source.replace(anchor, "if (workerLike) params.set('include_cooling', 'true');", 1)
    assert mutated != source
    _assert_mismatch_red(_G4_BODY, mutated)


def test_survival_locked_candidate_disable_guard_is_red_without_row_attributes() -> None:
    """G4 injection: removing the Survival-specific disabled attributes restores
    activation for the exact worker and button markup asserted above."""
    # same redesign consolidated the old
    # `survivalLocked ? ... isWorkerGroup ...` computation into one
    # `status.selectable`-driven `rowDisabled` (status comes from
    # candidateStatusModel(), whose survival_locked branch sets `selectable: false`).
    source = read_entry_source()
    anchor = "const rowDisabled = status.selectable ? ' tabindex=\"0\"' : ' aria-disabled=\"true\"';"
    assert source.count(anchor) == 1
    mutated = source.replace(anchor, "const rowDisabled = '';", 1)
    assert mutated != source
    _assert_mismatch_red(_G4_BODY, mutated)


_G9_BODY = r"""
const status = candidateStatusModel({
  cooldown: { state: 'survival_locked', ready: false, ready_at: '2099-01-02T03:04:05Z' },
});
if (!status) process.exit(60);
if (status.label !== 'Dead in Survival') process.exit(61);
if (status.tone !== 'danger') process.exit(62);
if (!status.detail.includes('season reset')) process.exit(63);
if (status.countdownUntil !== '2099-01-02T03:04:05Z') process.exit(64);
if (status.selectable !== false) process.exit(65);
"""


def test_survival_cooldown_tag_is_nonempty_danger_copy_with_season_reset_clause() -> None:
    """G9: the new state has an explanatory status and preserves the date's
    upper-bound caveat instead of falling through to the idle default.

    /: `cooldownTagHtml()` no longer exists --  (v0.9.145)
    replaced the standalone cooldown tag with `candidateStatusModel()`/
    `candidateStatusHtml()`, which every candidate row now reads for its status
    pill (register  row: "older guards need updating")."""
    run_in_module_context(_G9_BODY)


def test_survival_cooldown_tag_guard_is_red_when_state_branch_is_removed() -> None:
    """G9 injection: disabling the Survival tag branch reproduces the old empty
    tag for a not-ready candidate."""
    source = read_entry_source()
    anchor = "if (cooldown.state === 'survival_locked')"
    assert source.count(anchor) == 2
    mutated = source.replace(anchor, "if (false && cooldown.state === 'survival_locked')")
    assert mutated != source
    _assert_mismatch_red(_G9_BODY, mutated)


_G10_BODY = r"""
const readyAt = '2099-01-02T03:04:05Z';
const expectedDate = humanizeInstant(readyAt).absolute;
const reason = cooldownReasonFragment({ state: 'survival_locked', ready: false, ready_at: readyAt });
if (!reason.includes('Survival')) process.exit(70);
if (!reason.includes(expectedDate)) process.exit(71);
if (reason.toLowerCase().includes('cooldown')) process.exit(72);
"""


def test_survival_reason_names_survival_and_date_without_generic_cooldown() -> None:
    """G10: checkout's shared reason fragment names the actual Survival cause
    and upper-bound date rather than the generic evidence fallback."""
    run_in_module_context(_G10_BODY)


def test_survival_reason_guard_is_red_when_state_branch_is_removed() -> None:
    """G10 injection: removing the Survival reason branch sends the state to the
    generic cooldown fallback and turns the assertion red."""
    source = read_entry_source()
    anchor = "if (cooldown.state === 'survival_locked')"
    assert source.count(anchor) == 2
    mutated = source.replace(anchor, "if (false && cooldown.state === 'survival_locked')")
    assert mutated != source
    _assert_mismatch_red(_G10_BODY, mutated)


# (v0.9.145) removed `pickerResultNotice()` and its
# per-code `data-notice-code` notice list entirely. A nonzero Survival exclusion
# now surfaces through `pickerHiddenSummary()`'s consolidated "not ready hidden"
# chip (shared with `cooling`), not a standalone notice (register row
# "older guards need updating").
_G11_BODY = r"""
const html = pickerHiddenSummary({ excludedCounts: { survival_locked: 1 } });
if (!html.includes('data-picker-action="show-hidden-not-ready"')) process.exit(80);
if (!html.includes('1 not ready hidden (1 dead in Survival)')) process.exit(81);
"""


def test_picker_notice_list_includes_nonzero_survival_exclusion() -> None:
    """G11: a nonzero Survival exclusion is rendered in the picker's hidden-count
    chip."""
    run_in_module_context(_G11_BODY)


def test_picker_survival_notice_guard_is_red_when_whitelist_entry_is_removed() -> None:
    """G11 injection: zeroing the Survival exclusion count makes it disappear
    from the rendered hidden-count chip."""
    source = read_entry_source()
    anchor = "const dead = Number(excluded.survival_locked || 0);"
    assert source.count(anchor) == 1
    mutated = source.replace(anchor, "const dead = 0;", 1)
    assert mutated != source
    _assert_mismatch_red(_G11_BODY, mutated)


def test_editor_and_checkout_reuse_server_cooldown_without_survival_second_sources() -> None:
    """The editor carries the server's cooldown object without deriving one from
    survival_date, and checkout blocks only incoming entries whose shared
    cooldown.ready is false. Neither module owns Survival-specific readiness.

    /: / (a8ff6735, d87b6506) added plain-English stage
    tooltips that name "Survival" so the maintainer understands why a staged move is
    blocked red -- that is UI copy, not a second readiness computation, so the
    guard now looks for the actual tell (a `survival_date` field read or a
    hand-rolled `cooldown` object) rather than banning the word outright."""
    assert "survival_date" not in EDITOR_JS.read_text(encoding="utf-8")
    assert "survival_date" not in CHECKOUT_JS.read_text(encoding="utf-8")
    run_in_module_context(
        r"""
const readyAt = '2099-01-02T03:04:05Z';
const serverCooldown = { ready: false, ready_at: readyAt, class: 'worker', state: 'survival_locked' };
const supplied = editorOccupant({ uid: 'card-1', name: 'Dead card', edition: 1, editions: '1', card_set: 'beta', cooldown: serverCooldown }, 'worker');
if (supplied.cooldown !== serverCooldown) process.exit(90);
const undelivered = editorOccupant({ uid: 'card-2', name: 'Unannotated card', edition: 1, editions: '1', card_set: 'beta', survival_date: readyAt }, 'worker');
if (undelivered.cooldown !== null) process.exit(91);

CERES_STAGE.length = 0;
CERES_STAGE.push(
  { action: 'stake', cooldown: serverCooldown },
  { action: 'stake', cooldown: { ready: true, ready_at: readyAt, state: 'survival_locked' } },
  { action: 'unstake', cooldown: serverCooldown },
);
const blocked = checkoutBlockedCooldownEntries();
if (blocked.length !== 1 || blocked[0].cooldown !== serverCooldown) process.exit(92);
""",
    )
