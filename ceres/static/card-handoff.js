// (maintainer 2026-10-28, "Through Changes (Recommended)"): Stake and Unstake pressed in the Cards page's card menu
// arrive here as ?open=stake|unstake&copy=<opaque copy ref>. The board asks the server once which of its own copies
// that is (after the same fresh eligibility check the menu showed), then uses only its ordinary paths
// Unstake -> stagePricedRemovals (the plot's own Remove), then Changes opens; signed there with everything else.
// Stake -> a notice names the card; the next worker-slot picker the player opens is filtered to that card, and
// picking it stages through stageForSlot ( one-copy guard, preview, signing gate unchanged).
// Nothing here signs, broadcasts or builds an operation.
import { BOARD } from './board.js';
import { boardStagedPlans } from './board-staged-plans.js';
import { editorGroups } from './editor.js';
import { esc } from './format.js';
import { CERES_STAGE, stagePricedRemovals, stageToast } from './staging.js';
import { onPlansChanged, stagePlansReady } from './stage-plans.js';
import { S } from './state.js';

const COPY_REF = /^cc1_[0-9a-f]{48}$/;
const NOTICE_ID = 'card-handoff-notice';

let request = (() => {
  try {
    const params = new URLSearchParams(location.search);
    const action = params.get('open');
    const copies = params.getAll('copy').filter((ref) => COPY_REF.test(ref));
    if (!['stake', 'unstake'].includes(action) || !copies.length) return null;
    return { action, copies: action === 'stake' ? copies.slice(0, 1) : [...new Set(copies)].slice(0, 20) };
  } catch (_) { return null; }
})();

export function cardHandoffRequested() { return Boolean(request); }

function clearAddress() {
  try {
    const url = new URL(location.href);
    url.searchParams.delete('open');
    url.searchParams.delete('copy');
    history.replaceState(history.state, '', url);
  } catch (_) { /* nothing else reads it */ }
}

async function resolveCopies(player, action, copies) {
  const response = await fetch(`/api/cards/board-handoff?player=${encodeURIComponent(player)}`, {
    method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, asset_refs: copies, staged_plans: boardStagedPlans(player) }),
  });
  if (!response.ok) throw new Error('The card could not be checked');
  return (await response.json()).copies || [];
}

async function waitFor(test, ms = 15000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (test()) return true;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return false;
}

function findSlot(row, boardRef) {
  for (const group of editorGroups(row)) {
    const index = group.slots.findIndex((slot) => slot && String(slot.asset_ref) === String(boardRef));
    if (index !== -1) return { group, index, occupant: group.slots[index] };
  }
  return null;
}

async function stageUnstakes(copies) {
  let staged = 0;
  for (const copy of copies) {
    if (!copy.ok) { stageToast(copy.reason || 'This card cannot be unstaked now.', true); continue; }
    const row = BOARD?.plots.find((plot) => plot.deed_uid === copy.deed_uid);
    const spec = row ? findSlot(row, copy.board_ref) : null;
    if (!spec) { stageToast(`${copy.name || 'This card'} was not found on plot ${copy.plot || ''}. Nothing was staged.`, true); continue; }
    if (CERES_STAGE.some((entry) => String(entry.asset_ref) === String(copy.board_ref))) { staged += 1; continue; } // already in Changes
    if (spec.group.id !== 'worker') { stageToast(`Remove ${copy.name || 'this card'} on plot ${copy.plot || ''} itself: it is not an ordinary worker.`, true); continue; }
    if (await stagePricedRemovals(row, [spec], `Staged: remove ${spec.occupant.name || copy.name}.`)) staged += 1;
  }
  if (staged) document.getElementById('checkout-btn')?.click();
}

function endStakeFocus() {
  S.CERES_CARD_HANDOFF = null;
  document.getElementById(NOTICE_ID)?.remove();
}

function startStakeFocus(copy) {
  if (!copy.ok) { stageToast(copy.reason || 'This card cannot be staked now.', true); return; }
  S.CERES_CARD_HANDOFF = { boardRef: String(copy.board_ref), name: copy.name || '', level: copy.level ?? null };
  document.getElementById(NOTICE_ID)?.remove();
  const notice = document.createElement('div');
  notice.id = NOTICE_ID;
  notice.className = 'nav-include-notice';
  notice.setAttribute('role', 'status');
  const level = copy.level ? ` (Lv ${esc(copy.level)})` : '';
  notice.innerHTML = `Stake ${esc(copy.name || 'this card')}${level}: open a plot and choose an empty worker slot · <button type="button" class="ceres-btn ceres-btn--secondary card-handoff-cancel" data-control-density="compact">Cancel</button>`;
  notice.querySelector('button').addEventListener('click', endStakeFocus);
  document.body.appendChild(notice);
}

onPlansChanged(() => {
  const focus = S.CERES_CARD_HANDOFF;
  if (focus && CERES_STAGE.some((entry) => entry.action !== 'unstake' && String(entry.asset_ref) === focus.boardRef)) endStakeFocus();
});

export async function runCardHandoff(player) {
  const pending = request;
  request = null;
  clearAddress();
  if (!pending || !player) return;
  try {
    await waitFor(() => stagePlansReady() && BOARD?.plots);
    const copies = await resolveCopies(player, pending.action, pending.copies);
    if (pending.action === 'unstake') await stageUnstakes(copies);
    else if (copies[0]) startStakeFocus(copies[0]);
  } catch (error) {
    stageToast(`${error?.message || 'The card could not be checked'}. Nothing was staged.`, true);
  }
}
